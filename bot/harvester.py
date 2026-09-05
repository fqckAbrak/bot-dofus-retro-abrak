"""
Boucle principale du bot de récolte.

Logique :
  - Pour chaque ressource disponible sur la carte :
    1. Se déplacer vers la cellule adjacente (GA001, géré par move_to)
    2. Envoyer GA500 {cell_id};{elem_type} pour récolter
    3. Attendre IQ (confirmation serveur) avant de passer à la ressource suivante
  - S'arrête automatiquement si la connexion est perdue

Activation :
    from bot.harvester import start_bot, stop_bot
    asyncio.get_event_loop().call_soon_threadsafe(start_bot)
"""

from __future__ import annotations

import asyncio
import logging
import random

from dashboard import bridge
from game import state as _state
from bot import actions, channel
from bot.mapdata import get_resource_job_id, load_map
from bot.pathfinding import adjacent_cells

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Configuration (modifiable à chaud)
# ---------------------------------------------------------------------------

TARGET_ELEM_TYPES: set[int] = set()
"""Types d'éléments à récolter. Vide = tous les types disponibles."""

MAP_ROUTE: list[str] = []
"""Route de changement de map (ex: ["right", "right", "bottom"]).
Vide = rester sur la map actuelle et attendre le respawn des ressources.
Les directions sont consommées en séquence, en boucle."""

HARVEST_START_TIMEOUT: float = 1.5
"""Secondes max d'attente de GA 501 (récolte démarrée) après GA500.
Si absent → la récolte a été rejetée (mauvaise position ou ressource indisponible)."""

HARVEST_TIMEOUT: float = 8.0
"""Secondes max d'attente de l'IQ si le niveau du métier est inconnu."""

LOOP_SLEEP: float = 2.0
"""Pause entre chaque itération de la boucle principale."""

INTER_HARVEST_SLEEP: float = 0.5
"""Pause entre deux récoltes consécutives."""


def _compute_iq_timeout(resource_id: int) -> float:
    """Calculer le timeout IQ selon le niveau du métier actif.

    Formule Dofus Rétro 1.29 vérifiée expérimentalement :
        harvest_time = 2.0 + 0.1 * max(0, 100 - level)

    Exemples : Nv.100 → 2.0s  Nv.97 → 2.3s  Nv.82 → 3.8s
               Nv.15  → 10.5s  Nv.10 → 11.0s
    """
    job_id = get_resource_job_id(resource_id)
    if job_id > 0:
        level = _state.get_job_level(job_id)
        harvest_time = 2.0 + 0.1 * max(0, 100 - level)
        return harvest_time + 1.0  # +1s réseau / lag
    return HARVEST_TIMEOUT


# ---------------------------------------------------------------------------
# Bot singleton
# ---------------------------------------------------------------------------

class _HarvestBot:
    def __init__(self) -> None:
        self._running: bool = False
        self._task: asyncio.Task | None = None
        self._harvest_event: asyncio.Event = asyncio.Event()
        self._harvest_started_event: asyncio.Event = asyncio.Event()
        self._subscribed: bool = False
        self._route_idx: int = 0
        self._allowed_jobs: set[int] | None = None

    # ------------------------------------------------------------------
    # Contrôle
    # ------------------------------------------------------------------

    def start(self, allowed_jobs: set[int] | None = None) -> None:
        if self._running:
            logger.info("[Bot] Déjà en cours d'exécution")
            return
        self._running = True
        self._route_idx = 0
        self._allowed_jobs = allowed_jobs if allowed_jobs else None
        self._harvest_event.clear()
        self._harvest_started_event.clear()

        if not self._subscribed:
            bridge.subscribe("harvest_done", self._on_harvest_done)
            bridge.subscribe("harvest_started", self._on_harvest_started)
            self._subscribed = True

        loop = asyncio.get_event_loop()
        self._task = loop.create_task(self._loop(), name="harvest-bot")
        bridge.add_console("Bot démarré")
        logger.info("[Bot] Démarré")

    def stop(self) -> None:
        if not self._running and (self._task is None or self._task.done()):
            return
        self._running = False
        if self._task and not self._task.done():
            self._task.cancel()
        bridge.add_console("Bot arrêté")
        logger.info("[Bot] Arrêté")

    def soft_stop(self) -> None:
        """Arrêt « doux » : ne plus engager de récolte/combat mais laisser la
        boucle finir l'action en cours (combat, récolte) avant de sortir.

        Contrairement à stop(), on N'ANNULE PAS la task : la boucle vérifie
        ``self._running`` en tête d'itération et après chaque combat géré via
        handle_combat_if_needed(), donc elle termine proprement puis s'arrête.
        Utilisé par le protocole antibot (cf. bot/antibot.py).
        """
        if self._running:
            self._running = False
            logger.info("[Bot] Soft-stop demandé (fin de l'action en cours)")

    @property
    def running(self) -> bool:
        return self._running

    # ------------------------------------------------------------------
    # Callbacks bridge
    # ------------------------------------------------------------------

    def _on_harvest_done(self, _data: dict) -> None:
        self._harvest_event.set()

    def _on_harvest_started(self, _data: dict) -> None:
        self._harvest_started_event.set()

    # ------------------------------------------------------------------
    # Boucle principale
    # ------------------------------------------------------------------

    async def _loop(self) -> None:
        logger.info("[Bot] Boucle démarrée")
        try:
            while self._running:
                await asyncio.sleep(LOOP_SLEEP)

                if not channel.is_connected():
                    logger.info("[Bot] Connexion perdue — arrêt automatique")
                    bridge.add_console("Bot : connexion perdue, arrêt automatique")
                    break

                # Gérer un combat en cours avant de reprendre la récolte
                if _state.current.in_combat:
                    from bot.combat import handle_combat_if_needed
                    await handle_combat_if_needed()
                    continue

                if _state.current.current_map is None:
                    continue

                # Vérifier que la position du personnage est connue
                # (peut être perdue après un combat, un téléport, etc.)
                char = _state.current.character
                if char is not None:
                    entity = _state.current.entities.get(char.character_id)
                    if entity is None or entity.cell_id < 0:
                        logger.info("[Bot] Position inconnue — attente")
                        await asyncio.sleep(2.0)
                        continue

                current_map_id = _state.current.current_map.map_id

                available = self._get_available()
                if not available:
                    total_elems = len(_state.current.frame_objects.elements)
                    logger.debug("[Bot] Aucune ressource disponible (%d éléments GDF au total)", total_elems)
                    if MAP_ROUTE:
                        direction = MAP_ROUTE[self._route_idx % len(MAP_ROUTE)]
                        self._route_idx += 1
                        from bot import mapnav
                        await mapnav.change_map(direction)
                    else:
                        await asyncio.sleep(5.0)
                    continue

                from bot.mapdata import get_resource_name
                names = ", ".join(
                    f"{get_resource_name(e.resource_id)} #{e.elem_id}"
                    for e in available[:5]
                )
                logger.info("[Bot] Map #%d — %d ressource(s) récoltable(s) : %s", current_map_id, len(available), names)

                for elem in available:
                    if not self._running or not channel.is_connected():
                        break

                    # Détecter un changement de map en cours de boucle
                    new_map = _state.current.current_map
                    if new_map is None or new_map.map_id != current_map_id:
                        new_id = new_map.map_id if new_map else "?"
                        logger.info(
                            "[Bot] Changement de map détecté (%d → %s) — relance de la boucle",
                            current_map_id, new_id,
                        )
                        bridge.add_console(f"Bot : nouvelle map #{new_id}, relance")
                        break

                    await self._harvest_one(elem)
                    await asyncio.sleep(INTER_HARVEST_SLEEP)

        except asyncio.CancelledError:
            logger.info("[Bot] Tâche annulée")
        except Exception as exc:
            logger.error("[Bot] Exception inattendue : %s", exc, exc_info=True)
        finally:
            self._running = False
            logger.info("[Bot] Boucle terminée")

    # ------------------------------------------------------------------
    # Récolter un élément : se déplacer puis GA500
    # ------------------------------------------------------------------

    async def _harvest_one(self, elem) -> None:
        """Se déplacer, envoyer GA500, attendre GA501 puis IQ."""
        from bot.mapdata import get_resource_name
        cell_id = elem.elem_id
        elem_type = elem.elem_type
        resource_id = elem.resource_id
        name = get_resource_name(resource_id) if resource_id else f"type#{elem_type}"

        logger.info("[Bot] → %s cell=%d type=%d", name, cell_id, elem_type)
        bridge.add_console(f"Bot : récolte {name} (cell #{cell_id})")

        self._harvest_event.clear()
        self._harvest_started_event.clear()

        ok = await actions.harvest_with_move(cell_id, elem_type)
        if not ok:
            logger.warning("[Bot] harvest_with_move échoué cell=%d", cell_id)
            return

        # Attendre GA 501 (récolte démarrée côté serveur).
        # Si absent après HARVEST_START_TIMEOUT : GA500 rejeté (mauvaise position
        # ou ressource prise par un autre joueur) → réessayer une fois immédiatement.
        started = await self._wait_harvest_started()
        if not started:
            logger.info("[Bot] Pas de GA501 — réessai GA500 pour %s (cell %d)", name, cell_id)
            self._harvest_started_event.clear()
            await actions.harvest_resource(cell_id, elem_type)
            started = await self._wait_harvest_started()
            if not started:
                logger.warning("[Bot] Toujours pas de GA501 après réessai — %s (cell %d) ignoré", name, cell_id)
                await self._unstick(cell_id)
                return

        # GA 501 reçu : récolte en cours, calculer le timeout IQ selon le niveau du métier
        iq_timeout = _compute_iq_timeout(resource_id)
        logger.debug("[Bot] GA501 reçu — attente IQ %.1f s max pour %s", iq_timeout, name)

        try:
            await asyncio.wait_for(
                asyncio.shield(self._harvest_event.wait()),
                timeout=iq_timeout,
            )
            logger.info("[Bot] IQ reçu — %s récoltée (cell %d)", name, cell_id)
            bridge.add_console(f"Bot : {name} récoltée ✓")
        except asyncio.TimeoutError:
            logger.warning("[Bot] Timeout %.1f s sans IQ pour %s (cell %d)", iq_timeout, name, cell_id)

    async def _wait_harvest_started(self) -> bool:
        """Attendre GA 501 pendant HARVEST_START_TIMEOUT secondes."""
        try:
            await asyncio.wait_for(
                asyncio.shield(self._harvest_started_event.wait()),
                timeout=HARVEST_START_TIMEOUT,
            )
            return True
        except asyncio.TimeoutError:
            return False

    # ------------------------------------------------------------------
    # Déblocage positionnel
    # ------------------------------------------------------------------

    async def _unstick(self, resource_cell: int) -> None:
        """Se déplacer sur une cellule libre éloignée de la ressource bloquante."""
        char = _state.current.character
        if char is None:
            return
        entity = _state.current.entities.get(char.character_id)
        if entity is None or entity.cell_id < 0:
            return

        cur = entity.cell_id
        info = load_map(_state.current.current_map.map_id) if _state.current.current_map else None
        if info is None:
            return

        blocked = info.blocked_cells
        resource_cells = {
            e.elem_id for e in _state.current.frame_objects.elements.values()
            if e.resource_id > 0
        }

        adj = adjacent_cells(cur, info.width)
        candidates = [c for c in adj if c not in blocked and c not in resource_cells]
        if not candidates:
            candidates = [c for c in adj if c not in blocked]
        if not candidates:
            return

        target = random.choice(candidates)
        logger.info("[Bot] Unstick : déplacement %d → %d (loin de ressource %d)", cur, target, resource_cell)
        bridge.add_console(f"Bot : repositionnement (cell #{target})")
        await actions.move_to(target, blocked=blocked, width=info.width)
        await asyncio.sleep(1.0)

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _get_available(self):
        from bot.pathfinding import cell_to_xy
        from bot.mapdata import load_map as _load_map

        elems = list(_state.current.frame_objects.elements.values())
        avail = [e for e in elems if e.available and e.resource_id > 0]
        if TARGET_ELEM_TYPES:
            avail = [e for e in avail if e.elem_type in TARGET_ELEM_TYPES]
        if self._allowed_jobs:
            avail = [e for e in avail if get_resource_job_id(e.resource_id) in self._allowed_jobs]

        if len(avail) < 2:
            return avail

        # Tri greedy nearest-neighbor depuis la position actuelle du personnage.
        # Évite les aller-retours en visitant toujours la ressource la plus proche.
        char = _state.current.character
        if char is None:
            return avail
        entity = _state.current.entities.get(char.character_id)
        if entity is None or entity.cell_id < 0:
            return avail

        map_info = None
        if _state.current.current_map:
            map_info = _load_map(_state.current.current_map.map_id)
        width = map_info.width if map_info else 15

        cur_pos = cell_to_xy(entity.cell_id, width)
        ordered: list = []
        remaining = list(avail)
        while remaining:
            nearest = min(
                remaining,
                key=lambda e: (
                    (cell_to_xy(e.elem_id, width)[0] - cur_pos[0]) ** 2
                    + (cell_to_xy(e.elem_id, width)[1] - cur_pos[1]) ** 2
                ),
            )
            ordered.append(nearest)
            remaining.remove(nearest)
            cur_pos = cell_to_xy(nearest.elem_id, width)
        return ordered


# ---------------------------------------------------------------------------
# API publique
# ---------------------------------------------------------------------------

def _get_bot() -> "_HarvestBot":
    """Instance de récolte de la session active (créée à la demande)."""
    from core.session import active
    s = active()
    if s.harvester_bot is None:
        s.harvester_bot = _HarvestBot()
    return s.harvester_bot


def start_bot(allowed_jobs: set[int] | None = None) -> None:
    """Démarrer le bot de récolte (dans le contexte d'une session)."""
    _get_bot().start(allowed_jobs=allowed_jobs)


def stop_bot() -> None:
    """Arrêter le bot de récolte."""
    _get_bot().stop()


def soft_stop_bot(session=None) -> None:
    """Arrêt doux du bot de récolte de la session donnée (laisse finir l'action)."""
    from core.session import active_or_none
    s = session or active_or_none()
    if s is not None and s.harvester_bot is not None:
        s.harvester_bot.soft_stop()


def is_running(session=None) -> bool:
    from core.session import active_or_none
    s = session or active_or_none()
    if s is None or s.harvester_bot is None:
        return False
    return s.harvester_bot.running


def on_disconnect() -> None:
    """Appelé par channel.set_writer(None) pour stopper le bot proprement."""
    from core.session import active_or_none
    s = active_or_none()
    if s is not None and s.harvester_bot is not None and s.harvester_bot.running:
        logger.info("[Bot] Déconnexion détectée — arrêt automatique")
        s.harvester_bot.stop()
