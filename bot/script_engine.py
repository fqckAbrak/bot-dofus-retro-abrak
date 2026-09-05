"""
Moteur universel d'exécution de scripts de récolte.

Un script est un module Python dans scripts/ qui définit :

  ELEMENTS_TO_GATHER : set[int]   — elem_types à récolter (vide = tous)
  PRIORITY_ELEMENTS  : list[int]  — elem_types récoltés en priorité, dans l'ordre (vide = aucune priorité)
  MAX_PODS           : int        — % pods pour banquer (0 = jamais, pas encore impl.)

  def move() → list[dict]
      Route principale répétée en boucle.

  def bank() → list[dict]         (facultatif, non utilisé pour l'instant)

  async def on_map_enter(map_id: int, bot: ScriptBot) → None
      Hook facultatif appelé à chaque fois qu'on arrive sur une map.

Format d'une étape dans move() :
  {
    "map"       : int,   — map_id sur laquelle cette étape s'applique
    "changeMap" : str,   — "left" | "right" | "top" | "bottom"
                          (zaap / goto non encore implémentés)
    "gather"    : bool,  — récolter toutes les ressources disponibles (défaut False)
    "custom"    : Callable async(bot) | None   — appelé à la place de changeMap
  }
"""

from __future__ import annotations

import asyncio
import importlib.util
import logging
import random
import types
from pathlib import Path
from typing import Any

from dashboard import bridge
from game import state as _state
from bot import actions, channel
from bot.mapdata import get_resource_job_id, get_resource_name, load_map
from bot.pathfinding import adjacent_cells

logger = logging.getLogger(__name__)

Step = dict[str, Any]

_OPPOSITE: dict[str, str] = {
    "left": "right",
    "right": "left",
    "top": "bottom",
    "bottom": "top",
}

# ---------------------------------------------------------------------------
# Répertoire des scripts
# ---------------------------------------------------------------------------

from core.paths import SCRIPTS_DIR


def list_scripts() -> list[tuple[str, str]]:
    """Retourner [(name, description), ...] pour tous les scripts trouvés."""
    if not SCRIPTS_DIR.exists():
        return []
    result = []
    for path in sorted(SCRIPTS_DIR.glob("*.py")):
        if path.name.startswith("_"):
            continue
        name = path.stem
        try:
            mod = _load_module(name)
            first_line = (mod.__doc__ or "").strip().split("\n")[0]
        except Exception:
            first_line = ""
        result.append((name, first_line))
    return result


def _load_module(name: str) -> types.ModuleType:
    """Charger un script par nom (sans .py) depuis SCRIPTS_DIR."""
    path = SCRIPTS_DIR / f"{name}.py"
    spec = importlib.util.spec_from_file_location(f"scripts.{name}", path)
    if spec is None or spec.loader is None:
        raise ImportError(f"Script introuvable : {path}")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)  # type: ignore[union-attr]
    return mod


# ---------------------------------------------------------------------------
# Bot
# ---------------------------------------------------------------------------

class ScriptBot:
    HARVEST_START_TIMEOUT: float = 1.5
    HARVEST_TIMEOUT: float = 8.0
    LOOP_SLEEP: float = 1.0
    INTER_HARVEST_SLEEP: float = 0.4

    def __init__(self) -> None:
        self._running: bool = False
        self._task: asyncio.Task | None = None
        self._harvest_event: asyncio.Event = asyncio.Event()
        self._harvest_started_event: asyncio.Event = asyncio.Event()
        self._subscribed: bool = False

        self._script: types.ModuleType | None = None
        self._script_name: str = ""
        self._allowed_jobs: set[int] | None = None
        self._elem_types: set[int] = set()
        self._priority_elem_types: list[int] = []

        # Dernière map connue + direction utilisée (pour retour arrière si map inconnue)
        self._prev_map_id: int | None = None
        self._prev_direction: str | None = None

        # Stop event pour les scripts custom (run)
        self._stop_event: asyncio.Event = asyncio.Event()

    # ------------------------------------------------------------------
    # Chargement
    # ------------------------------------------------------------------

    def load(self, name: str) -> None:
        """Charger un script par son nom (ex: "feudala")."""
        self._script = _load_module(name)
        self._script_name = name
        self._elem_types = set(getattr(self._script, "ELEMENTS_TO_GATHER", set()))
        self._priority_elem_types = list(getattr(self._script, "PRIORITY_ELEMENTS", []))

    # ------------------------------------------------------------------
    # Contrôle
    # ------------------------------------------------------------------

    def start(self, script_name: str | None = None, allowed_jobs: set[int] | None = None) -> None:
        if self._running:
            logger.info("[Script] Déjà en cours")
            return
        if script_name is not None:
            self.load(script_name)
        if self._script is None:
            raise RuntimeError("Aucun script chargé — appelez load() ou passez script_name")

        self._running = True
        self._allowed_jobs = allowed_jobs
        self._harvest_event.clear()
        self._harvest_started_event.clear()
        self._stop_event.clear()

        if not self._subscribed:
            bridge.subscribe("harvest_done", self._on_harvest_done)
            bridge.subscribe("harvest_started", self._on_harvest_started)
            self._subscribed = True

        loop = asyncio.get_event_loop()
        self._task = loop.create_task(self._loop(), name=f"script-{self._script_name}")
        bridge.add_console(f"Script démarré : {self._script_name}")
        logger.info("[Script] Démarré — %s", self._script_name)

    def stop(self) -> None:
        if not self._running and (self._task is None or self._task.done()):
            return
        self._running = False
        self._stop_event.set()  # signale les scripts custom (run)
        if self._task and not self._task.done():
            self._task.cancel()
        bridge.add_console("Script arrêté")
        logger.info("[Script] Arrêté")

    def soft_stop(self) -> None:
        """Arrêt « doux » : ne plus enchaîner d'étapes mais laisser l'itération
        en cours (dont un combat géré via handle_combat_if_needed) se terminer.

        On ne touche PAS à ``_stop_event`` (qui interromprait immédiatement les
        scripts custom ``run()`` en plein combat) et on n'annule pas la task :
        la boucle move() teste ``self._running`` et sort proprement. Pour les
        scripts custom pilotant combat_farm_loop, l'arrêt des nouveaux combats
        passe par combat.stop_after_combat(). Utilisé par le protocole antibot.
        """
        if self._running:
            self._running = False
            logger.info("[Script] Soft-stop demandé (fin de l'action en cours)")

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
        assert self._script is not None

        # Support des scripts "custom" avec une fonction async run(ctx).
        # Les scripts harvester classiques utilisent move() à la place.
        run_fn = getattr(self._script, "run", None)
        if run_fn is not None and asyncio.iscoroutinefunction(run_fn):
            self._stop_event = asyncio.Event()
            ctx = {"state": _state.current, "stop": self._stop_event}
            try:
                await run_fn(ctx)
            except asyncio.CancelledError:
                logger.info("[Script] Tâche custom annulée")
            except Exception as exc:
                logger.error("[Script] Exception inattendue (custom) : %s", exc, exc_info=True)
            finally:
                self._running = False
                logger.info("[Script] Boucle custom terminée")
            return

        route: list[Step] = self._script.move()
        if not route:
            logger.error("[Script] move() est vide — arrêt")
            self._running = False
            return

        step_idx = self._sync_step_idx(route)
        logger.info("[Script] Départ à l'étape %d (map #%s)", step_idx, route[step_idx]["map"])

        try:
            while self._running:
                await asyncio.sleep(self.LOOP_SLEEP)

                if not channel.is_connected():
                    bridge.add_console("Script : connexion perdue, arrêt")
                    break

                # Gérer un combat en cours avant de reprendre le script
                if _state.current.in_combat:
                    from bot.combat import handle_combat_if_needed
                    await handle_combat_if_needed()
                    continue

                if _state.current.current_map is None:
                    continue

                char = _state.current.character
                if char is not None:
                    entity = _state.current.entities.get(char.character_id)
                    if entity is None or entity.cell_id < 0:
                        await asyncio.sleep(2.0)
                        continue

                current_map_id = _state.current.current_map.map_id

                # Re-synchroniser si la map courante ne correspond pas à l'étape
                expected_map_id = int(route[step_idx]["map"])
                if current_map_id != expected_map_id:
                    new_idx = self._find_step_for_map(route, current_map_id)
                    if new_idx is None:
                        if self._prev_direction is not None:
                            opposite = _OPPOSITE.get(self._prev_direction)
                            logger.warning(
                                "[Script] Map #%d inconnue — retour via '%s' (prev map #%s, dir '%s')…",
                                current_map_id, opposite, self._prev_map_id, self._prev_direction,
                            )
                            bridge.add_console(
                                f"Script : map inconnue #{current_map_id}, retour via {opposite}…"
                            )
                            await asyncio.sleep(1.0)
                            ok = await self._navigate(opposite)
                            if not ok:
                                # Pas de sortie directionnelle (ex : intérieur de maison)
                                # → chercher n'importe quel soleil sur la map et en sortir.
                                logger.warning(
                                    "[Script] Retour via '%s' impossible — tentative sortie via soleil",
                                    opposite,
                                )
                                from bot import mapnav as _mapnav
                                ok_sun = await _mapnav.exit_via_sun()
                                if ok_sun:
                                    # Bannir la cellule qui menait à cette map indésirable
                                    # pour éviter d'y retourner au prochain changement de map.
                                    _mapnav.ban_last_exit()
                                else:
                                    logger.warning("[Script] Sortie soleil échouée — attente…")
                                    await asyncio.sleep(3.0)
                        else:
                            logger.warning(
                                "[Script] Map #%d inconnue dans le script, attente…",
                                current_map_id,
                            )
                            await asyncio.sleep(3.0)
                        continue
                    step_idx = new_idx
                    logger.info("[Script] Re-sync étape %d (map #%d)", step_idx, current_map_id)

                step = route[step_idx]

                # Hook on_map_enter
                hook = getattr(self._script, "on_map_enter", None)
                if hook is not None:
                    await hook(current_map_id, self)

                # Récolte
                if step.get("gather"):
                    await self._harvest_map(current_map_id)

                # Navigation
                custom = step.get("custom")
                if custom is not None:
                    if asyncio.iscoroutinefunction(custom):
                        await custom(self)
                    else:
                        custom(self)
                elif "changeMap" in step:
                    change_map = step["changeMap"]
                    exit_cell = step.get("exitCell")
                    self._prev_map_id = current_map_id
                    self._prev_direction = change_map
                    ok = await self._navigate(change_map, exit_cell=exit_cell)
                    if not ok:
                        logger.warning("[Script] Navigation '%s' échouée, attente…", change_map)
                        await asyncio.sleep(3.0)
                        continue
                else:
                    # Pas de navigation : rester sur place et attendre respawn
                    bridge.add_console("Script : attente respawn…")
                    await asyncio.sleep(10.0)

                step_idx = (step_idx + 1) % len(route)

        except asyncio.CancelledError:
            logger.info("[Script] Tâche annulée")
        except Exception as exc:
            logger.error("[Script] Exception inattendue : %s", exc, exc_info=True)
        finally:
            self._running = False
            logger.info("[Script] Boucle terminée")

    # ------------------------------------------------------------------
    # Synchronisation de l'index d'étape
    # ------------------------------------------------------------------

    def _sync_step_idx(self, route: list[Step]) -> int:
        """Trouver l'étape correspondant à la map courante, sinon 0."""
        current = _state.current.current_map
        if current is not None:
            idx = self._find_step_for_map(route, current.map_id)
            if idx is not None:
                return idx
        return 0

    @staticmethod
    def _find_step_for_map(route: list[Step], map_id: int) -> int | None:
        """Retourner l'index de la première étape avec map == map_id, ou None."""
        for i, step in enumerate(route):
            if int(step["map"]) == map_id:
                return i
        return None

    # ------------------------------------------------------------------
    # Navigation
    # ------------------------------------------------------------------

    async def _navigate(self, change_map: str, exit_cell: int | None = None) -> bool:
        from bot import mapnav

        if change_map in ("left", "right", "top", "bottom"):
            return await mapnav.change_map(change_map, exit_cell=exit_cell)

        if change_map.startswith("usezaap:"):
            target = change_map.split(":", 1)[1]
            bridge.add_console(f"Script : usezaap → {target} (non implémenté)")
            logger.warning("[Script] usezaap non implémenté (%s)", change_map)
            return False

        if change_map.startswith("goto:"):
            target = change_map.split(":", 1)[1].strip()
            from bot import autopilot
            # "goto:x,y" → coordonnées ; "goto:mapId" → résolution via le graphe.
            if "," in target:
                try:
                    sx, sy = target.split(",", 1)
                    coord = (int(sx), int(sy))
                except ValueError:
                    logger.warning("[Script] goto coord invalide : %s", change_map)
                    return False
            else:
                g = autopilot.get_graph()
                coord = g.coord_of_map(int(target)) if (g and target.lstrip("-").isdigit()) else None
                if coord is None:
                    logger.warning("[Script] goto map inconnue : %s", change_map)
                    return False
            return await autopilot.travel_to(coord[0], coord[1])

        logger.warning("[Script] changeMap inconnu : %s", change_map)
        return False

    # ------------------------------------------------------------------
    # Récolte sur une map
    # ------------------------------------------------------------------

    async def _harvest_map(self, expected_map_id: int) -> None:
        """Récolter toutes les ressources disponibles sur la map courante."""
        current = _state.current.current_map
        if current is None or current.map_id != expected_map_id:
            return

        available = self._get_available()
        if not available:
            logger.debug("[Script] Aucune ressource sur map #%d", expected_map_id)
            return

        names = ", ".join(
            f"{get_resource_name(e.resource_id)} #{e.elem_id}"
            for e in available[:5]
        )
        logger.info("[Script] Map #%d — %d ressource(s) : %s", expected_map_id, len(available), names)

        for elem in available:
            if not self._running or not channel.is_connected():
                break
            # Vérifier qu'on est toujours sur la bonne map
            current = _state.current.current_map
            if current is None or current.map_id != expected_map_id:
                break
            await self._harvest_one(elem)
            await asyncio.sleep(self.INTER_HARVEST_SLEEP)

    # ------------------------------------------------------------------
    # Récolter un élément
    # ------------------------------------------------------------------

    async def _harvest_one(self, elem) -> None:
        cell_id = elem.elem_id
        elem_type = elem.elem_type
        resource_id = elem.resource_id
        name = get_resource_name(resource_id) if resource_id else f"type#{elem_type}"

        logger.info("[Script] → %s cell=%d type=%d", name, cell_id, elem_type)
        bridge.add_console(f"Script : récolte {name} (cell #{cell_id})")

        self._harvest_event.clear()
        self._harvest_started_event.clear()

        ok = await actions.harvest_with_move(cell_id, elem_type)
        if not ok:
            logger.warning("[Script] harvest_with_move échoué cell=%d", cell_id)
            return

        started = await self._wait_harvest_started()
        if not started:
            # Réessai une fois
            self._harvest_started_event.clear()
            await actions.harvest_resource(cell_id, elem_type)
            started = await self._wait_harvest_started()
            if not started:
                logger.warning("[Script] Pas de GA501 pour %s (cell %d) — ignoré", name, cell_id)
                await self._unstick(cell_id)
                return

        iq_timeout = self._compute_iq_timeout(resource_id)
        logger.debug("[Script] GA501 reçu — attente IQ %.1f s max pour %s", iq_timeout, name)

        try:
            await asyncio.wait_for(
                asyncio.shield(self._harvest_event.wait()),
                timeout=iq_timeout,
            )
            logger.info("[Script] IQ reçu — %s récoltée (cell %d)", name, cell_id)
            bridge.add_console(f"Script : {name} récoltée ✓")
        except asyncio.TimeoutError:
            logger.warning("[Script] Timeout IQ pour %s (cell %d)", name, cell_id)

    async def _wait_harvest_started(self) -> bool:
        try:
            await asyncio.wait_for(
                asyncio.shield(self._harvest_started_event.wait()),
                timeout=self.HARVEST_START_TIMEOUT,
            )
            return True
        except asyncio.TimeoutError:
            return False

    def _compute_iq_timeout(self, resource_id: int) -> float:
        job_id = get_resource_job_id(resource_id)
        if job_id > 0:
            level = _state.get_job_level(job_id)
            harvest_time = 2.0 + 0.1 * max(0, 100 - level)
            return harvest_time + 1.0
        return self.HARVEST_TIMEOUT

    # ------------------------------------------------------------------
    # Déblocage positionnel
    # ------------------------------------------------------------------

    async def _unstick(self, resource_cell: int) -> None:
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
        logger.info("[Script] Unstick : %d → %d", cur, target)
        bridge.add_console(f"Script : repositionnement (cell #{target})")
        await actions.move_to(target, blocked=blocked, width=info.width)
        await asyncio.sleep(1.0)

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _get_available(self):
        from bot.pathfinding import cell_to_xy

        elems = list(_state.current.frame_objects.elements.values())
        avail = [e for e in elems if e.available and e.resource_id > 0]
        if self._elem_types:
            avail = [e for e in avail if e.elem_type in self._elem_types]
        if self._allowed_jobs:
            avail = [e for e in avail if get_resource_job_id(e.resource_id) in self._allowed_jobs]

        if len(avail) < 2:
            return avail

        char = _state.current.character
        if char is None:
            return avail
        entity = _state.current.entities.get(char.character_id)
        if entity is None or entity.cell_id < 0:
            return avail

        map_info = None
        if _state.current.current_map:
            map_info = load_map(_state.current.current_map.map_id)
        width = map_info.width if map_info else 15

        def greedy_nearest(pool: list, start_pos: tuple) -> list:
            """Trier `pool` en nearest-neighbor depuis `start_pos`. Retourne la liste triée."""
            ordered, remaining, pos = [], list(pool), start_pos
            while remaining:
                nearest = min(
                    remaining,
                    key=lambda e: (
                        (cell_to_xy(e.elem_id, width)[0] - pos[0]) ** 2
                        + (cell_to_xy(e.elem_id, width)[1] - pos[1]) ** 2
                    ),
                )
                ordered.append(nearest)
                remaining.remove(nearest)
                pos = cell_to_xy(nearest.elem_id, width)
            return ordered

        cur_pos = cell_to_xy(entity.cell_id, width)

        if not self._priority_elem_types:
            # Tri greedy nearest-neighbor depuis la position actuelle du personnage.
            return greedy_nearest(avail, cur_pos)

        # Ressources prioritaires d'abord, groupe par groupe, chaque groupe en nearest-neighbor.
        # Les ressources hors priorité sont récoltées ensuite (nearest-neighbor).
        ordered: list = []
        remaining = list(avail)
        for priority_type in self._priority_elem_types:
            group = [e for e in remaining if e.elem_type == priority_type]
            if not group:
                continue
            sorted_group = greedy_nearest(group, cur_pos)
            ordered.extend(sorted_group)
            for e in sorted_group:
                remaining.remove(e)
            cur_pos = cell_to_xy(sorted_group[-1].elem_id, width)

        ordered.extend(greedy_nearest(remaining, cur_pos))
        return ordered


# ---------------------------------------------------------------------------
# API publique (singleton)
# ---------------------------------------------------------------------------

def _get_bot() -> "ScriptBot":
    """Instance de moteur de scripts de la session active (créée à la demande)."""
    from core.session import active
    s = active()
    if s.script_bot is None:
        s.script_bot = ScriptBot()
    return s.script_bot


def start_bot(script_name: str, allowed_jobs: set[int] | None = None) -> None:
    """Démarrer le moteur avec le script donné (dans le contexte d'une session)."""
    _get_bot().start(script_name=script_name, allowed_jobs=allowed_jobs)


def stop_bot() -> None:
    _get_bot().stop()


def soft_stop_bot(session=None) -> None:
    """Arrêt doux du moteur de scripts de la session (laisse finir l'action)."""
    from core.session import active_or_none
    s = session or active_or_none()
    if s is not None and s.script_bot is not None:
        s.script_bot.soft_stop()


def is_running(session=None) -> bool:
    from core.session import active_or_none
    s = session or active_or_none()
    if s is None or s.script_bot is None:
        return False
    return s.script_bot.running


def on_disconnect() -> None:
    from core.session import active_or_none
    s = active_or_none()
    if s is not None and s.script_bot is not None and s.script_bot.running:
        logger.info("[Script] Déconnexion — arrêt automatique")
        s.script_bot.stop()
