"""
Autopilote inter-maps : voyage automatique vers une coordonnée (X, Y) du monde.

Stratégie (cf. mémoire project_autopilot_natif — l'autopilote natif du serveur
est verrouillé, on reconstruit le nôtre côté proxy) :

  1. PLANIFICATION (BFS) sur le graphe monde `ressources/world_graph.json`
     (généré par tools/build_world_graph.py). On raisonne en COORDONNÉES :
     depuis la map canonique d'une coord, on ne suit que les bords « ouverts ».
  2. EXÉCUTION ADAPTATIVE : on lit la coord courante (map_id → XML), on planifie,
     on fait UN saut via mapnav.change_map(direction), puis on RE-PLANIFIE depuis
     la nouvelle coord. Tout écart (atterrissage sur un intérieur, bord trompeur)
     est corrigé au tour suivant ; les arêtes qui échouent sont blacklistées pour
     forcer un re-routage.

Limites connues : pas de zaaps/téléporteurs (étape ultérieure) → seules les
destinations atteignables à pied sur le même continent fonctionnent.

Usage (depuis le contexte d'une session) :
    from bot import autopilot
    ok = await autopilot.travel_to(5, -18)
"""

from __future__ import annotations

import json
import logging
from collections import deque
from pathlib import Path

from dashboard import bridge
from game import state as _state
from bot import channel, mapnav
from bot.mapdata import load_map

logger = logging.getLogger(__name__)

from core.paths import RESSOURCES_DIR

_GRAPH_PATH = RESSOURCES_DIR / "world_graph.json"
_DEAD_EDGES_PATH = RESSOURCES_DIR / "autopilot_dead_edges.json"

MAX_MAPS: int = 250
"""Garde-fou : nombre max de changements de map pour un seul voyage."""

# Poids de planification : préférer fortement les sorties par SOLEIL aux bords
# seulement praticables (souvent de fausses sorties).
_SUN_WEIGHT: int = 1
_OPEN_WEIGHT: int = 15

Coord = tuple[int, int]


# ---------------------------------------------------------------------------
# Arêtes mortes apprises (persistées entre sessions)
# ---------------------------------------------------------------------------
# {map_id(str) → {direction}} : directions qui, depuis cette map, ne mènent à
# aucune transition (constaté au runtime). Le planificateur les exclut → le bot
# n'essaie plus ces directions « chiantes » lors des trajets suivants.
_dead_edges: dict[str, set[str]] | None = None


def _load_dead_edges() -> dict[str, set[str]]:
    global _dead_edges
    if _dead_edges is None:
        _dead_edges = {}
        if _DEAD_EDGES_PATH.exists():
            try:
                raw = json.loads(_DEAD_EDGES_PATH.read_text(encoding="utf-8"))
                _dead_edges = {k: set(v) for k, v in raw.items()}
                logger.info("[autopilot] %d maps avec arêtes mortes chargées", len(_dead_edges))
            except Exception as exc:
                logger.warning("[autopilot] lecture autopilot_dead_edges.json échouée : %s", exc)
                _dead_edges = {}
    return _dead_edges


def is_dead_edge(map_id: int, direction: str) -> bool:
    return direction in _load_dead_edges().get(str(map_id), set())


def record_dead_edge(map_id: int, direction: str) -> None:
    """Mémoriser (et persister) qu'une direction ne mène nulle part depuis une map."""
    de = _load_dead_edges()
    s = de.setdefault(str(map_id), set())
    if direction in s:
        return
    s.add(direction)
    try:
        _DEAD_EDGES_PATH.write_text(
            json.dumps({k: sorted(v) for k, v in de.items()}, separators=(",", ":")),
            encoding="utf-8",
        )
        logger.info("[autopilot] arête morte persistée : map #%d '%s'", map_id, direction)
    except Exception as exc:
        logger.warning("[autopilot] persistance arête morte échouée : %s", exc)


# ---------------------------------------------------------------------------
# Graphe monde (chargé une fois)
# ---------------------------------------------------------------------------

class WorldGraph:
    def __init__(self, data: dict) -> None:
        self.maps: dict[str, dict] = data["maps"]
        self.coord_index: dict[str, list[int]] = data["coord_index"]
        self.deltas: dict[str, tuple[int, int]] = {k: tuple(v) for k, v in data["deltas"].items()}

    # -- résolution coord ↔ map ------------------------------------------
    def maps_at(self, x: int, y: int) -> list[int]:
        return self.coord_index.get(f"{x},{y}", [])

    def canonical(self, x: int, y: int) -> int | None:
        """Map « overworld » canonique d'une coord (la plus connectée).

        Plusieurs maps partagent une coord (intérieurs, sous-zones). On retient
        celle qui a le plus de soleils, puis le plus de sorties, puis le plus
        petit id — heuristique qui sélectionne la map de plein air traversable.
        """
        ids = self.maps_at(x, y)
        if not ids:
            return None
        return max(ids, key=lambda m: (
            len(self.maps[str(m)]["sun"]),
            len(self.maps[str(m)]["open"]),
            -m,
        ))

    def weighted_exits(self, x: int, y: int) -> list[tuple[str, int]]:
        """[(direction, poids)] sortants d'une coord.

        Les SOLEILS (vraies sorties, = ce qu'affiche l'onglet Carte) ont un poids
        faible ; les bords seulement praticables (sans soleil) — souvent de
        fausses sorties — un poids élevé, donc évités sauf si aucune route soleil
        n'existe. Les arêtes apprises comme mortes (persistées) sont exclues.
        """
        mid = self.canonical(x, y)
        if mid is None:
            return []
        m = self.maps[str(mid)]
        sun = set(m["sun"])
        out: list[tuple[str, int]] = []
        for d in m["open"]:
            if is_dead_edge(mid, d):
                continue
            out.append((d, _SUN_WEIGHT if d in sun else _OPEN_WEIGHT))
        return out

    def coord_of_map(self, map_id: int) -> Coord | None:
        m = self.maps.get(str(map_id))
        if m is not None:
            return (m["x"], m["y"])
        # Pas dans le graphe (map récente/absente) : repli sur le XML.
        info = load_map(map_id)
        return (info.x, info.y) if info is not None else None

    # -- planification ----------------------------------------------------
    def plan(
        self,
        start: Coord,
        goal: Coord,
        blocked_edges: set[tuple[Coord, str]] | None = None,
    ) -> list[str] | None:
        """Dijkstra : liste de directions de `start` à `goal`, ou None si injoignable.

        Préfère les routes par SOLEILS (poids faible) ; n'emprunte un bord
        praticable sans soleil qu'à défaut (poids élevé). Exclut les arêtes
        bloquées (session) et mortes (persistées, via weighted_exits).
        """
        if start == goal:
            return []
        blocked_edges = blocked_edges or set()
        import heapq
        counter = 0
        pq: list[tuple[int, int, Coord, list[str]]] = [(0, 0, start, [])]
        best: dict[Coord, int] = {start: 0}
        while pq:
            cost, _, (x, y), path = heapq.heappop(pq)
            if (x, y) == goal:
                return path
            if cost > best.get((x, y), 1 << 30):
                continue
            for d, w in self.weighted_exits(x, y):
                if ((x, y), d) in blocked_edges:
                    continue
                dx, dy = self.deltas[d]
                nxt = (x + dx, y + dy)
                if self.canonical(*nxt) is None:
                    continue
                ncost = cost + w
                if ncost < best.get(nxt, 1 << 30):
                    best[nxt] = ncost
                    counter += 1
                    heapq.heappush(pq, (ncost, counter, nxt, path + [d]))
        return None


_graph: WorldGraph | None = None


def get_graph() -> WorldGraph | None:
    global _graph
    if _graph is None:
        if not _GRAPH_PATH.exists():
            logger.error("[autopilot] world_graph.json absent — lancer tools/build_world_graph.py")
            return None
        try:
            _graph = WorldGraph(json.loads(_GRAPH_PATH.read_text(encoding="utf-8")))
            logger.info("[autopilot] graphe monde chargé (%d maps, %d coords)",
                        len(_graph.maps), len(_graph.coord_index))
        except Exception as exc:
            logger.error("[autopilot] échec chargement graphe : %s", exc)
            return None
    return _graph


def preview_route(start: Coord, goal: Coord) -> list[str] | None:
    """Aperçu du trajet (directions) sans rien exécuter — pour l'UI."""
    g = get_graph()
    return g.plan(start, goal) if g is not None else None


# ---------------------------------------------------------------------------
# Contrôle stop/running par session (coopératif)
# ---------------------------------------------------------------------------
# Clé = session_id ; True si un voyage est en cours / un stop est demandé.
_running: dict[int, bool] = {}
_stop: dict[int, bool] = {}


def is_running(session=None) -> bool:
    from core.session import active_or_none
    s = session or active_or_none()
    return bool(s and _running.get(s.session_id))


def request_stop() -> None:
    """Demander l'arrêt du voyage de la session active (appelé via call_in_session)."""
    from core.session import active_or_none
    s = active_or_none()
    if s is not None:
        _stop[s.session_id] = True


def _should_stop() -> bool:
    from core.session import active_or_none
    s = active_or_none()
    return bool(s and _stop.get(s.session_id))


# ---------------------------------------------------------------------------
# Exécuteur
# ---------------------------------------------------------------------------

def _current_coord() -> Coord | None:
    cm = _state.current.current_map
    if cm is None:
        return None
    g = get_graph()
    if g is not None:
        c = g.coord_of_map(cm.map_id)
        if c is not None:
            return c
    info = load_map(cm.map_id)
    return (info.x, info.y) if info is not None else None


def _current_map_id() -> int | None:
    cm = _state.current.current_map
    return cm.map_id if cm is not None else None


# Settle court pour l'autopilote : la position d'arrivée est déjà inférée au GDK,
# inutile d'attendre les 2 s par défaut de change_map (gain ~1.5 s par saut).
_TRAVEL_SETTLE: float = 0.5


async def travel_to(x: int, y: int, *, max_maps: int = MAX_MAPS) -> bool:
    """Voyager automatiquement jusqu'à la coordonnée (x, y).

    Returns:
        True si arrivé à destination, False sinon (injoignable, combat persistant,
        déconnexion, ou garde-fou atteint).
    """
    goal: Coord = (x, y)
    g = get_graph()
    if g is None:
        bridge.add_console("Autopilote : graphe monde absent (tools/build_world_graph.py)")
        return False

    if not channel.is_connected():
        bridge.add_console("Autopilote : pas de connexion")
        return False

    if g.canonical(x, y) is None:
        bridge.add_console(f"Autopilote : destination ({x},{y}) inconnue dans le graphe")
        return False

    from core.session import active_or_none
    _sess = active_or_none()
    _sid = _sess.session_id if _sess is not None else -1
    if _running.get(_sid):
        bridge.add_console("Autopilote : déjà un voyage en cours")
        return False
    _running[_sid] = True
    _stop[_sid] = False

    blocked_edges: set[tuple[Coord, str]] = set()
    interior_attempts: dict[Coord, int] = {}  # sorties d'intérieur (maison) par coord
    bridge.add_console(f"🧭 Autopilote : départ vers ({x},{y})")
    logger.info("[autopilot] travel_to (%d,%d)", x, y)

    import asyncio

    try:
        for step in range(max_maps):
            if _should_stop():
                bridge.add_console("Autopilote : arrêt demandé")
                return False
            if not channel.is_connected():
                bridge.add_console("Autopilote : connexion perdue — arrêt")
                return False

            # Laisser le combat se résoudre avant de poursuivre.
            if _state.current.in_combat:
                from bot.combat import handle_combat_if_needed
                await handle_combat_if_needed()
                await asyncio.sleep(1.0)
                continue

            cur = _current_coord()
            if cur is None:
                await asyncio.sleep(1.0)
                continue

            if cur == goal:
                bridge.add_console(f"✅ Autopilote : arrivé à ({x},{y}) en {step} map(s)")
                logger.info("[autopilot] arrivé à (%d,%d)", x, y)
                return True

            dirs = g.plan(cur, goal, blocked_edges)
            if dirs is None:
                bridge.add_console(f"Autopilote : aucun chemin de ({cur[0]},{cur[1]}) vers "
                                   f"({x},{y}) (île/zaap requis ?)")
                logger.warning("[autopilot] pas de chemin %s → %s (blocked=%d)",
                               cur, goal, len(blocked_edges))
                return False

            d = dirs[0]
            dx, dy = g.deltas[d]
            expected = (cur[0] + dx, cur[1] + dy)
            bridge.add_console(f"Autopilote : ({cur[0]},{cur[1]}) → {d}  "
                               f"({len(dirs)} sauts restants)")

            pre_map_id = _current_map_id()
            ok = await mapnav.change_map(d, settle=_TRAVEL_SETTLE)
            new = _current_coord()
            new_map_id = _current_map_id()

            # --- Cas nominal : on a atteint la coord attendue ---
            if ok and new == expected:
                logger.info("[autopilot] %s → %s via '%s'", cur, new, d)
                continue

            # --- Anomalie A : on a changé de MAP mais pas atteint la coord attendue.
            #     ⇒ entré dans un intérieur (maison, sous-zone, donjon) partageant ou
            #     non la coord. Il faut RESSORTIR (sinon on route depuis l'intérieur).
            if new_map_id is not None and new_map_id != pre_map_id:
                interior_attempts[cur] = interior_attempts.get(cur, 0) + 1
                bridge.add_console(f"Autopilote : intérieur détecté (map #{new_map_id}, "
                                   f"coord {new}) — sortie…")
                logger.warning("[autopilot] intérieur : %s '%s' → map #%s coord %s — exit_via_sun",
                               cur, d, new_map_id, new)
                exited = await mapnav.exit_via_sun()
                if exited:
                    mapnav.ban_last_exit()  # bannir l'entrée qui mène à cet intérieur
                if not exited or interior_attempts[cur] >= 3:
                    # Impossible de ressortir, ou intérieur récurrent : renoncer à cette
                    # direction depuis cur et re-router.
                    blocked_edges.add((cur, d))
                await asyncio.sleep(0.3)
                continue

            # --- Anomalie B : la map n'a pas changé (aucune sortie exploitable
            #     dans cette direction). ⇒ bannir l'arête (session) ET la PERSISTER
            #     comme morte (le bot ne réessaiera plus cette direction ici), puis
            #     re-router.
            blocked_edges.add((cur, d))
            if pre_map_id is not None:
                record_dead_edge(pre_map_id, d)
            logger.warning("[autopilot] saut %s '%s' sans changement de map — arête morte persistée", cur, d)
            bridge.add_console(f"Autopilote : '{d}' sans issue ici — appris, re-routage")
            await asyncio.sleep(0.3)
            continue

        bridge.add_console(f"Autopilote : garde-fou atteint ({max_maps} maps) — arrêt")
        logger.warning("[autopilot] max_maps atteint sans arriver à %s", goal)
        return False
    finally:
        _running[_sid] = False
        _stop[_sid] = False
