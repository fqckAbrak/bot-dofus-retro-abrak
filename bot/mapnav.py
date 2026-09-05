"""
Navigation entre maps : détection des cellules de sortie et changement de map.

Pour changer de map dans Dofus Rétro, il suffit de se déplacer sur une cellule
de bordure praticable. Le serveur détecte automatiquement le changement de zone.

Directions : "left", "right", "top", "bottom"

Usage :
    from bot import mapnav
    success = await mapnav.change_map("left")
"""

from __future__ import annotations

import asyncio
import logging

from dashboard import bridge
from game import state as _state
from bot import actions, channel
from bot.mapdata import load_map
from bot.pathfinding import cell_to_xy, xy_to_cell, astar, adjacent_cells, _PAIR_NEIGHBORS, _ODD_NEIGHBORS

logger = logging.getLogger(__name__)

def _nav():
    """État de navigation de la session active (core.session.NavState)."""
    from core.session import active
    return active().nav


def _resource_cells() -> set[int]:
    """Cases des ressources visibles (arbres/minerais) sur la map courante.

    Le serveur les traite comme des obstacles infranchissables ; on les ajoute au
    blocked du pathfinding de navigation pour éviter les chemins refusés.
    """
    try:
        return {
            e.elem_id
            for e in _state.current.frame_objects.elements.values()
            if e.resource_id > 0
        }
    except Exception:
        return set()


MAP_CHANGE_TIMEOUT: float = 20.0
"""Secondes max d'attente d'un GDK sur une nouvelle map après déplacement vers une sortie."""

MAP_CHANGE_SETTLE: float = 2.0
"""Secondes d'attente après GDK pour l'inférence de position."""

# ---------------------------------------------------------------------------
# Abonnement bridge (singleton) + verrou anti-doublon
# ---------------------------------------------------------------------------


# Cellule de sortie intentée par le bot (set avant GA001, clear après GDK).
# Utilisée par state.py à la place de entity.cell_id pour éviter que les
# messages GA1 de Flash ne corrompent _pre_gdm_char_cell.

# Cellules dynamiquement détectées comme mortes sur la map courante.
# Persistant entre les cycles change_map sur la même map, vidé au GDK (changement de map).
# Permet au pathfinding d'éviter les cellules où le serveur refuse systématiquement le mouvement.

# Dernière sortie réussie : (source_map_id, exit_cell_id).
# Mémorisée à chaque changement de map réussi pour pouvoir bannir la cellule
# si elle mène à une map inconnue (ex : maison accidentelle).

# Cellules de sortie bannies par map : {map_id → {cell_id}}.
# Exclues de get_exit_cells() pour éviter de retomber sur la même map indésirable.
# Peuplé par ban_last_exit() après sortie d'urgence via exit_via_sun().


def get_pending_exit() -> tuple[int, int] | None:
    """Retourner (exit_cell, old_map_id) si un changement de map est en cours, sinon None."""
    if _nav()._pending_exit_cell >= 0 and _nav()._pending_exit_map_id is not None:
        return _nav()._pending_exit_cell, _nav()._pending_exit_map_id
    return None


def clear_pending_exit() -> None:
    """Effacer la cellule de sortie en attente (appelé après GDK traité)."""
    _nav()._pending_exit_cell = -1
    _nav()._pending_exit_map_id = None


def ban_last_exit() -> None:
    """Bannir la dernière cellule de sortie utilisée.

    À appeler quand on vient de constater que cette cellule menait à une map
    indésirable (ex : maison) et qu'on vient d'en sortir via exit_via_sun().
    La cellule sera exclue de get_exit_cells() sur sa map d'origine.
    """
    if _nav()._last_successful_exit is None:
        return
    src_map_id, exit_cell = _nav()._last_successful_exit
    if src_map_id not in _nav()._banned_exit_cells:
        _nav()._banned_exit_cells[src_map_id] = set()
    _nav()._banned_exit_cells[src_map_id].add(exit_cell)
    logger.warning(
        "[mapnav] Sortie bannie : cell=%d map #%d (menait à une map indésirable)",
        exit_cell, src_map_id,
    )
    bridge.add_console(f"Bot : sortie #{exit_cell} bannie sur map #{src_map_id}")


def _ensure_subscribed() -> None:
    if not _nav()._subscribed:
        bridge.subscribe("map_ready", _on_map_ready)
        _nav()._subscribed = True


def _get_lock() -> asyncio.Lock:
    if _nav()._lock is None:
        _nav()._lock = asyncio.Lock()
    return _nav()._lock


def _on_map_ready(data: dict) -> None:
    if _nav()._map_changed_event is not None:
        _nav()._map_changed_event.set()
    if _nav()._dead_cells:
        logger.debug("[mapnav] Nouvelle map — reset des %d cellule(s) mortes", len(_nav()._dead_cells))
    _nav()._dead_cells = set()


# ---------------------------------------------------------------------------
# Détection des sorties de map
# ---------------------------------------------------------------------------

def get_exit_cells(direction: str, *, sun_magic_only: bool = False) -> list[int]:
    """Retourner les cellules de sortie pour une direction donnée.

    Priorité 1 : cellules Sol Magique (SUN_MAGIC) sur la bordure correspondante.
    Priorité 2 : toutes les cellules praticables sur la bordure (fallback).

    Les cellules Sol Magique sont les sorties officielles dans Dofus Rétro
    (identifiées par leur layerObject1Num ∈ {1030, 1029, 4088}).

    Args:
        direction: "left", "right", "top" ou "bottom"
        sun_magic_only: si True, ne retourne que les SUN_MAGIC (pas de fallback).
            Utile pour le bot multi-map : évite de foncer sur une bordure
            qui n'a pas de vraie sortie.

    Returns:
        Liste de cell_ids (SUN_MAGIC d'abord, puis les autres si aucun SUN_MAGIC).
    """
    if _state.current.current_map is None:
        return []

    map_id = _state.current.current_map.map_id
    info = load_map(map_id)
    if info is None:
        return []

    width = info.width
    walkable = info.walkable_cells
    sun_magic = info.sun_magic_cells
    if not walkable:
        return []

    ys = [cell_to_xy(c, width)[1] for c in walkable]
    max_y = max(ys)

    def _is_border(cell_id: int) -> bool:
        x, y = cell_to_xy(cell_id, width)
        if direction == "left":   return x == 0
        if direction == "right":  return x >= width - 2
        if direction == "top":    return y <= 1
        if direction == "bottom": return y >= max_y - 1
        return False

    border_cells = [c for c in walkable if _is_border(c)]
    if not border_cells:
        return []

    # Exclure les cellules de sortie connues comme indésirables (menant à une maison, etc.)
    banned = _nav()._banned_exit_cells.get(map_id, set())
    if banned:
        border_cells_filtered = [c for c in border_cells if c not in banned]
        if border_cells_filtered:
            border_cells = border_cells_filtered
            logger.debug("[mapnav] %d cellule(s) bannies exclues pour '%s'", len(banned), direction)

    # Préférer les SUN_MAGIC sur la bordure
    sun_exits = [c for c in border_cells if c in sun_magic]
    if sun_exits:
        logger.debug("[mapnav] %d cellule(s) SUN_MAGIC pour '%s'", len(sun_exits), direction)
        return sun_exits

    if sun_magic_only:
        return []

    # Fallback : toutes les cellules de bordure
    return border_cells


# ---------------------------------------------------------------------------
# Recherche d'une position valide pour naviguer
# ---------------------------------------------------------------------------

def _find_valid_nav_cell(
    cur_cell: int,
    target_cell: int,
    width: int,
    eff_blocked: set[int],
    tried_cells: set[int],
    max_depth: int = 10,
) -> int | None:
    """Trouver la cellule la plus proche de target_cell depuis laquelle l'A* peut l'atteindre.

    Algorithme :
    1. BFS depuis cur_cell pour lister toutes les cases accessibles (max_depth hops).
    2. Filtrer : pas dans tried_cells, pas dans eff_blocked.
    3. Trier par distance euclidienne à target_cell (on veut s'approcher).
    4. Prendre le premier candidat pour lequel astar(candidate→target) réussit.

    Cela évite le BFS aveugle qui choisit des cases arbitraires sans vérifier
    que la sortie est réellement atteignable depuis là.
    """
    from bot.mapdata import load_map as _load_map
    info = _load_map(_state.current.current_map.map_id) if _state.current.current_map else None
    map_blocked = info.blocked_cells if info is not None else set()

    all_cells = _bfs_cells(cur_cell, width, hard_blocked=map_blocked, max_depth=max_depth)
    candidates = [c for c in all_cells if c not in tried_cells and c not in eff_blocked and c != cur_cell]

    if not candidates:
        return None

    tx, ty = cell_to_xy(target_cell, width)
    candidates.sort(
        key=lambda c: (cell_to_xy(c, width)[0] - tx) ** 2 + (cell_to_xy(c, width)[1] - ty) ** 2
    )

    # Vérifier au plus 50 candidats pour éviter de bloquer l'event loop
    for candidate in candidates[:50]:
        if astar(eff_blocked, candidate, target_cell, width) is not None:
            return candidate

    return None


# ---------------------------------------------------------------------------
# Déblocage de case buggée — helpers
# ---------------------------------------------------------------------------

def _bfs_cells(
    start_cell: int,
    width: int,
    hard_blocked: set[int],
    max_depth: int,
) -> list[int]:
    """BFS depuis start_cell, retourne toutes les cells atteignables à ≤ max_depth hops.

    Utilise les 8 voisins (cardinaux + diagonaux) pour une couverture maximale.
    Ne traverse pas hard_blocked (murs map).
    """
    from collections import deque
    visited: set[int] = {start_cell}
    queue: deque[tuple[int, int]] = deque([(start_cell, 0)])
    result: list[int] = []
    while queue:
        cell, depth = queue.popleft()
        if depth >= max_depth:
            continue
        x, y = cell_to_xy(cell, width)
        neighbors_delta = _PAIR_NEIGHBORS if y % 2 == 0 else _ODD_NEIGHBORS
        for di, dj in neighbors_delta:
            nx, ny = x + di, y + dj
            if not (0 <= nx < width and ny >= 0):
                continue
            nc = xy_to_cell(nx, ny, width)
            if nc in visited or nc in hard_blocked:
                continue
            visited.add(nc)
            result.append(nc)
            queue.append((nc, depth + 1))
    return result


def _pick_diverse_candidates(
    cur_cell: int,
    candidates: list[int],
    width: int,
    n: int,
) -> list[int]:
    """Choisir n candidats répartis dans des quadrants différents (NE/NW/SE/SW).

    Maximise la dispersion spatiale pour éviter de tourner en rond.
    """
    if len(candidates) <= n:
        return candidates[:]
    cx, cy = cell_to_xy(cur_cell, width)
    quadrants: list[list[int]] = [[], [], [], []]
    for c in candidates:
        x, y = cell_to_xy(c, width)
        qi = (0 if x >= cx else 1) + (0 if y <= cy else 2)
        quadrants[qi].append(c)
    result: list[int] = []
    for q in quadrants:
        if not q or len(result) >= n:
            continue
        # Préférer le plus éloigné dans chaque quadrant
        q.sort(key=lambda c: -((cell_to_xy(c, width)[0] - cx) ** 2 + (cell_to_xy(c, width)[1] - cy) ** 2))
        result.append(q[0])
    # Compléter si < n
    remaining = [c for c in candidates if c not in result]
    result.extend(remaining[: n - len(result)])
    return result[:n]


# ---------------------------------------------------------------------------
# Déblocage de case buggée
# ---------------------------------------------------------------------------

async def _unstick(
    cur_cell: int,
    exits_set: set[int],
    blocked: set[int],
    width: int,
    *,
    tried_cells: set[int] | None = None,
    attempt: int = 0,
) -> None:
    """Tenter de débloquer le personnage coincé sur une case buggée.

    Stratégie progressive :
    - attempt 0 : cases adjacentes directes (1 hop) — comportement original.
    - attempt 1 : cases à 2-3 hops, diversifiées par quadrant (NE/NW/SE/SW).
    - attempt 2+ : cases à 4-6 hops, diversifiées — fuite large pour sortir de la zone morte.

    tried_cells (partagé par l'appelant) accumule toutes les cells déjà testées
    pour éviter de boucler sur les mêmes positions.

    On évite les cases de sortie de map pour ne pas déclencher de changement de
    map accidentel pendant le déblocage.
    """
    from bot.mapdata import load_map as _load_map
    info = _load_map(_state.current.current_map.map_id) if _state.current.current_map else None
    map_blocked = info.blocked_cells if info is not None else set()

    if tried_cells is None:
        tried_cells = set()
    tried_cells.add(cur_cell)

    # Cases strictement interdites (murées ou sorties de map)
    hard_forbidden = exits_set | map_blocked
    # Cases déjà essayées (exclues en plus pour éviter les boucles)
    soft_forbidden = hard_forbidden | blocked | tried_cells

    if attempt == 0:
        # Cas simple : 4 voisins cardinaux adjacents
        adj = adjacent_cells(cur_cell, width)
        candidates = [c for c in adj if c not in soft_forbidden]
        if not candidates:
            candidates = [c for c in adj if c not in hard_forbidden]
    else:
        # Cas progressif : BFS sur 2+(attempt*2) hops, cases pas encore tentées
        max_depth = 2 + attempt * 2          # attempt1→4, attempt2→6…
        max_depth = min(max_depth, 8)
        all_reachable = _bfs_cells(cur_cell, width, hard_blocked=map_blocked, max_depth=max_depth)
        candidates = [c for c in all_reachable if c not in soft_forbidden]
        if not candidates:
            # Dernier recours : ignorer tried_cells mais garder les sorties interdites
            candidates = [c for c in all_reachable if c not in hard_forbidden]
        # Diversifier : 1 candidat par quadrant
        candidates = _pick_diverse_candidates(cur_cell, candidates, width, n=4)

    if not candidates:
        logger.warning(
            "[mapnav] _unstick attempt=%d : aucune case libre depuis %d", attempt, cur_cell
        )
        return

    logger.info(
        "[mapnav] _unstick attempt=%d : déblocage depuis cell=%d → cases=%s",
        attempt, cur_cell, candidates[:4],
    )
    bridge.add_console(f"Bot : déblocage case buggée #{cur_cell} (attempt {attempt + 1})…")

    for step_cell in candidates[:3]:
        tried_cells.add(step_cell)
        ok = await actions.move_to(step_cell, blocked=blocked, width=width)
        await asyncio.sleep(0.2)
        if ok:
            cur_ent = (
                _state.current.entities.get(_state.current.character.character_id)
                if _state.current.character
                else None
            )
            new_cell = cur_ent.cell_id if cur_ent else -1
            logger.info("[mapnav] _unstick : après move cell=%d → %d", cur_cell, new_cell)
            if new_cell >= 0 and new_cell != cur_cell:
                return  # déblocage réussi


# ---------------------------------------------------------------------------
# Sortie d'urgence via un soleil (map inconnue / maison accidentelle)
# ---------------------------------------------------------------------------

async def exit_via_sun() -> bool:
    """Se déplacer vers n'importe quel soleil (sun_magic) de la map courante et attendre GDK.

    Utilisé comme fallback quand le bot se retrouve sur une map inconnue (ex : intérieur
    de maison) où il n'y a pas de sortie dans la direction souhaitée.
    Il suffit de marcher sur le seul sol magique disponible pour ressortir.

    Returns:
        True si le changement de map a bien eu lieu.
    """
    _ensure_subscribed()
    lock = _get_lock()

    if lock.locked():
        logger.debug("[mapnav] exit_via_sun : change_map déjà en cours, ignoré")
        return False

    await lock.acquire()
    try:
        result = await _do_exit_via_sun()
    finally:
        lock.release()

    if not result:
        return False

    await asyncio.sleep(MAP_CHANGE_SETTLE)

    char = _state.current.character
    new_map_id = _state.current.current_map.map_id if _state.current.current_map else "?"
    final_entity = _state.current.entities.get(char.character_id) if char else None
    final_cell = final_entity.cell_id if final_entity else -1
    if final_cell >= 0:
        logger.info("[mapnav] exit_via_sun — Position après sortie : cell=%d (map #%s)", final_cell, new_map_id)
        bridge.add_console(f"Bot : sorti de map inconnue → map #{new_map_id}, cell #{final_cell}")
    else:
        logger.warning("[mapnav] exit_via_sun — Position inconnue après sortie (map #%s)", new_map_id)

    return True


async def _do_exit_via_sun() -> bool:
    """Corps de exit_via_sun (sous verrou)."""
    if not channel.is_connected():
        return False

    char = _state.current.character
    if char is None:
        return False

    if _state.current.current_map is None:
        return False

    old_map_id = _state.current.current_map.map_id
    info = load_map(old_map_id)
    if info is None:
        logger.warning("[mapnav] exit_via_sun : XML manquant pour map #%d", old_map_id)
        return False

    entity = _state.current.entities.get(char.character_id)
    if entity is None or entity.cell_id < 0:
        logger.warning("[mapnav] exit_via_sun : position inconnue")
        return False

    width = info.width
    start_cell = entity.cell_id
    sx, sy = cell_to_xy(start_cell, width)

    sun_cells = list(info.sun_magic_cells)
    if not sun_cells:
        # Maison ou intérieur sans soleil dans le XML.
        # Dans Dofus Rétro, le personnage spawn directement SUR le portail de sortie.
        # La position courante de l'entité est donc la cellule de sortie à activer.
        logger.info(
            "[mapnav] exit_via_sun : aucun soleil XML sur map #%d — utilise spawn cell=%d",
            old_map_id, start_cell,
        )
        sun_cells = [start_cell]

    # Trier les soleils par distance euclidienne depuis la position courante
    sun_cells.sort(
        key=lambda c: (cell_to_xy(c, width)[0] - sx) ** 2 + (cell_to_xy(c, width)[1] - sy) ** 2
    )
    target_sun = sun_cells[0]

    logger.info(
        "[mapnav] exit_via_sun : map #%d, cell %d → soleil %d (total %d soleils)",
        old_map_id, start_cell, target_sun, len(sun_cells),
    )
    bridge.add_console(f"Bot : sortie d'urgence via soleil #{target_sun} (map #{old_map_id})")

    _nav()._map_changed_event = asyncio.Event()
    _nav()._pending_exit_cell = target_sun
    _nav()._pending_exit_map_id = old_map_id

    if start_cell == target_sun:
        # Déjà sur le portail : move_to retournerait True sans rien envoyer.
        # Il faut d'abord s'éloigner d'une case, puis revenir pour activer le portail.
        from bot.pathfinding import adjacent_cells as _adj
        adj = [c for c in _adj(target_sun, width) if c not in info.blocked_cells]
        if adj:
            logger.debug("[mapnav] exit_via_sun : déjà sur soleil %d — recul vers %d", target_sun, adj[0])
            await actions.move_to(adj[0], blocked=info.blocked_cells, width=width)
            await asyncio.sleep(0.3)
            # Relire la position après le recul
            ent2 = _state.current.entities.get(char.character_id)
            if ent2 and ent2.cell_id >= 0:
                start_cell = ent2.cell_id
        else:
            logger.warning("[mapnav] exit_via_sun : aucune case adjacente pour reculer depuis %d", target_sun)

    # move_to avec blocked=info.blocked_cells (murs seulement, pas les soleils).
    # Le fallback interne de move_to utilise astar(blocked, ...) ce qui permet
    # de router jusqu'au soleil sans le considérer comme obstacle.
    ok = await actions.move_to(target_sun, blocked=info.blocked_cells, width=width)
    if not ok:
        logger.warning("[mapnav] exit_via_sun : impossible de naviguer vers soleil %d", target_sun)
        _nav()._pending_exit_cell = -1
        _nav()._pending_exit_map_id = None
        return False

    # Attendre GDK
    deadline = asyncio.get_event_loop().time() + MAP_CHANGE_TIMEOUT
    while asyncio.get_event_loop().time() < deadline:
        remaining = deadline - asyncio.get_event_loop().time()
        if remaining <= 0:
            break
        try:
            await asyncio.wait_for(_nav()._map_changed_event.wait(), timeout=min(remaining, 1.0))
        except asyncio.TimeoutError:
            pass
        if _state.current.current_map is not None and _state.current.current_map.map_id != old_map_id:
            return True
        _nav()._map_changed_event.clear()

    logger.warning("[mapnav] exit_via_sun : timeout — map #%d toujours active", old_map_id)
    _nav()._pending_exit_cell = -1
    _nav()._pending_exit_map_id = None
    return False


# ---------------------------------------------------------------------------
# Changement de map
# ---------------------------------------------------------------------------

async def change_map(direction: str, exit_cell: int | None = None,
                     settle: float = MAP_CHANGE_SETTLE) -> bool:
    """Se déplacer vers une sortie dans la direction donnée et attendre le changement de map.

    Séquence :
      1. Trouver les cellules de sortie (bordure) dans la direction.
      2. Calculer la plus proche accessible (A*).
      3. Si déjà sur la case de sortie : reculer d'une case d'abord.
      4. Se déplacer vers la sortie (GA001 via actions.move_to).
      5. Attendre GDK sur une nouvelle map (event bridge "map_ready").
      6. Relâcher le verrou → nouvelles demandes peuvent être acceptées.
      7. Attendre `settle` secondes pour que l'inférence de position s'applique.

    Args:
        direction: "left", "right", "top" ou "bottom"
        exit_cell: si fourni, forcer cette cellule de sortie au lieu de la détecter
                   automatiquement (utile quand le SUN_MAGIC n'est pas dans le XML).
        settle:    secondes d'attente après GDK pour laisser l'inférence de position
                   s'appliquer. Défaut MAP_CHANGE_SETTLE (2 s, scripts récolte) ;
                   l'autopilote passe une valeur courte (la position d'arrivée est
                   déjà inférée au GDK, inutile d'attendre autant).

    Returns:
        True si le changement de map a bien eu lieu, False sinon.
    """
    _ensure_subscribed()
    lock = _get_lock()

    # Un seul changement de map à la fois (ignore les clics multiples)
    if lock.locked():
        logger.debug("[mapnav] change_map déjà en cours, ignoré")
        return False

    await lock.acquire()
    try:
        result, new_map_id, char = await _do_change_map(direction, exit_cell=exit_cell)
    finally:
        lock.release()

    if not result:
        return False

    # Attendre que state.py ait fini de traiter GDK (inférence de position).
    # Verrou déjà relâché : d'autres demandes peuvent être acceptées ici.
    if settle > 0:
        await asyncio.sleep(settle)

    final_entity = _state.current.entities.get(char.character_id) if char else None
    final_cell = final_entity.cell_id if final_entity else -1
    if final_cell >= 0:
        logger.info("[mapnav] Position après changement : cell=%d (map #%s)", final_cell, new_map_id)
        bridge.add_console(f"Bot : map #{new_map_id}, position #{final_cell} OK")
    else:
        logger.warning("[mapnav] Position toujours inconnue après changement de map #%s", new_map_id)
        bridge.add_console(f"Bot : map #{new_map_id} — position inconnue !")

    return True


async def _do_change_map(direction: str, exit_cell: int | None = None) -> tuple[bool, object, object]:
    """Effectuer le changement de map (partie sous verrou).

    Args:
        exit_cell: si fourni, utiliser cette cellule de sortie au lieu de la détecter.

    Returns:
        (success, new_map_id, char) — new_map_id et char sont None si success=False.
    """
    if not channel.is_connected():
        logger.warning("[mapnav] change_map : pas de connexion")
        return False, None, None

    char = _state.current.character
    if char is None:
        return False, None, None

    entity = _state.current.entities.get(char.character_id)
    if entity is None or entity.cell_id < 0:
        logger.warning("[mapnav] change_map : position inconnue")
        return False, None, None

    if _state.current.current_map is None:
        return False, None, None

    old_map_id = _state.current.current_map.map_id
    info = load_map(old_map_id)
    if info is None:
        logger.warning("[mapnav] change_map : XML manquant pour map #%d", old_map_id)
        return False, None, None

    width = info.width
    # Bloquer aussi les cases-ressources visibles (arbres, minerais…) : le serveur
    # les traite comme des obstacles, mais elles ne sont PAS dans les murs du XML.
    # Sans ça, l'A* trace un chemin à travers un arbre → le serveur refuse le
    # mouvement (« serveur immobile ») → tâtonnement près des ressources.
    # On n'exclut jamais les soleils (sorties) du passage.
    resource_cells = _resource_cells() - info.sun_magic_cells
    blocked = info.blocked_cells | resource_cells
    start_cell = entity.cell_id

    if exit_cell is not None:
        # Cellule de sortie forcée par le script
        logger.info("[mapnav] change_map '%s' : cellule forcée %d (map #%d)", direction, exit_cell, old_map_id)
        exits_set = {exit_cell}
        target_cell = exit_cell
    else:
        exits = get_exit_cells(direction)
        if not exits:
            logger.warning("[mapnav] change_map : aucune sortie '%s' sur map #%d", direction, old_map_id)
            bridge.add_console(f"Bot : aucune sortie '{direction}' sur map #{old_map_id}")
            return False, None, None

        exits_set = set(exits)

        # Trier par distance euclidienne depuis notre position
        sx, sy = cell_to_xy(start_cell, width)
        exits_sorted = sorted(
            exits,
            key=lambda c: (cell_to_xy(c, width)[0] - sx) ** 2 + (cell_to_xy(c, width)[1] - sy) ** 2,
        )

        # Trouver la première sortie accessible par A*
        # Ne pas court-circuiter sur start_cell == candidate : on doit toujours envoyer GA001
        target_cell = None
        for candidate in exits_sorted[:8]:
            if candidate == start_cell:
                target_cell = candidate
                break
            path = astar(blocked, start_cell, candidate, width)
            if path is not None:
                target_cell = candidate
                break

        if target_cell is None:
            logger.warning("[mapnav] change_map : pas de chemin vers sortie '%s'", direction)
            bridge.add_console(f"Bot : pas de chemin vers sortie '{direction}'")
            return False, None, None

    # Sorties détectées « mortes » (atteintes mais sans transition serveur) durant
    # CE change_map. Bannies aussi de façon persistante (_banned_exit_cells) pour
    # ne pas y retourner. Permet de basculer sur une autre cellule de sortie.
    forced_exit = exit_cell is not None
    dead_exits: set[int] = set()

    def _select_target(from_cell: int) -> int | None:
        """Choisir la meilleure cellule de sortie atteignable, hors sorties mortes."""
        if forced_exit:
            return exit_cell if exit_cell not in dead_exits else None
        cands = [c for c in get_exit_cells(direction) if c not in dead_exits]
        if not cands:
            return None
        fx, fy = cell_to_xy(from_cell, width)
        cands.sort(key=lambda c: (cell_to_xy(c, width)[0] - fx) ** 2
                                 + (cell_to_xy(c, width)[1] - fy) ** 2)
        for cand in cands[:8]:
            if cand == from_cell or astar(blocked, from_cell, cand, width) is not None:
                return cand
        return cands[0]

    logger.info(
        "[mapnav] Changement map '%s' : cell %d → %d (map #%d)",
        direction, start_cell, target_cell, old_map_id,
    )
    bridge.add_console(f"Bot : changement map → {direction} (cell #{target_cell})")

    # Préparer l'event AVANT de bouger pour ne pas rater le GDK
    _nav()._map_changed_event = asyncio.Event()

    # Enregistrer la sortie intentée : state.py l'utilisera au GDK pour
    # inférer la position d'arrivée, même si Flash écrase entity.cell_id entre-temps.
    _nav()._pending_exit_cell = target_cell
    _nav()._pending_exit_map_id = old_map_id

    # --- Cas spécial : déjà sur la case de sortie ---
    current_cell = entity.cell_id
    if current_cell == target_cell:
        adj = adjacent_cells(target_cell, width)
        step_away = next(
            (c for c in adj if c not in blocked and c not in exits_set),
            next((c for c in adj if c not in blocked), None),
        )
        if step_away is not None:
            logger.debug("[mapnav] Déjà sur sortie — recul vers cell %d", step_away)
            ok_back = await actions.move_to(step_away, blocked=blocked, width=width)
            if not ok_back:
                logger.warning("[mapnav] Recul impossible vers cell %d", step_away)
                return False, None, None
        else:
            logger.warning("[mapnav] Aucune case adjacente libre pour reculer depuis %d", target_cell)
            return False, None, None

    # ---------------------------------------------------------------------------
    # Boucle de navigation vers la cellule de sortie
    # ---------------------------------------------------------------------------
    # Stratégie :
    #  1. move_to(target_cell) avec pathfinding évitant les cellules mortes connues.
    #  2. Attente GDK (2 s) — succès si la map change.
    #  3. Échec → détecter immédiatement si la case courante est morte (pré == post),
    #     mettre à jour dead_cells (persistant entre cycles), faire _unstick.
    #  4. Si l'unstick nous déplace vers une nouvelle case → tentative immédiate
    #     ("bonus") depuis cette case avec le pathfinding mis à jour, SANS consommer
    #     d'attempt supplémentaire.
    #  5. Jusqu'à MAX_NAV_ATTEMPTS cycles complets.
    # ---------------------------------------------------------------------------

    MAX_NAV_ATTEMPTS = 5
    MAX_DEAD_EXIT_SWITCHES = 3   # éviter de tester toute la colonne d'un bord sans soleil
    _dead_switches = 0
    _unstick_tried: set[int] = set()
    map_changed = False

    async def _wait_gdk(timeout: float = 2.0) -> bool:
        """Attendre le GDK de changement de map. Retourne True si la map a changé."""
        deadline = asyncio.get_event_loop().time() + timeout
        while asyncio.get_event_loop().time() < deadline:
            remaining = deadline - asyncio.get_event_loop().time()
            if remaining <= 0:
                break
            try:
                await asyncio.wait_for(_nav()._map_changed_event.wait(), timeout=min(remaining, 1.0))
            except asyncio.TimeoutError:
                pass
            if _state.current.current_map is not None and _state.current.current_map.map_id != old_map_id:
                return True
            _nav()._map_changed_event.clear()
        return False

    def _cur_cell() -> int:
        ent = _state.current.entities.get(char.character_id)
        return ent.cell_id if ent else -1

    def _mark_dead(cell: int) -> None:
        """Ajouter cell à _dead_cells si elle n'y est pas encore."""
        if cell >= 0 and cell not in _nav()._dead_cells and cell != target_cell:
            _nav()._dead_cells.add(cell)
            logger.warning(
                "[mapnav] Cellule morte : %d — exclue du pathfinding (total: %s)",
                cell, _nav()._dead_cells,
            )
            bridge.add_console(f"Bot : cellule morte #{cell} — pathfinding mis à jour")

    async def _try_nav_and_gdk(from_cell: int, label: str) -> bool:
        """move_to(target_cell) puis attente GDK. Retourne True si map changée.

        Détecte aussi si la case d'arrivée est morte (pré == post) et la marque.
        """
        eff = blocked | _nav()._dead_cells
        ok = await actions.move_to(target_cell, blocked=eff, width=width)
        if not ok:
            logger.warning("[mapnav] %s : pas de chemin %d→%d (dead_cells=%s)",
                           label, from_cell, target_cell, _nav()._dead_cells)
            return False
        if await _wait_gdk():
            return True
        # GDK non reçu
        after = _cur_cell()
        if after == from_cell and from_cell != target_cell:
            _mark_dead(from_cell)   # immédiatement : pré == post → cellule morte
        return False

    for attempt in range(MAX_NAV_ATTEMPTS):
        cur = _cur_cell()
        if attempt > 0:
            logger.warning(
                "[mapnav] Retry navigation (attempt %d/%d), position=%d",
                attempt + 1, MAX_NAV_ATTEMPTS, cur,
            )
            bridge.add_console(f"Bot : tentative navigation sortie {attempt + 1}/{MAX_NAV_ATTEMPTS}")

        # --- Tentative principale ---
        map_changed = await _try_nav_and_gdk(cur, f"attempt {attempt + 1}")
        if map_changed:
            break

        # --- Sortie morte : on est SUR la cellule de sortie mais aucune transition.
        #     La cellule de sortie elle-même ne mène nulle part (soleil décoratif /
        #     sortie bloquée). On la bannit et on bascule sur une autre sortie au
        #     lieu de s'acharner (cf. bug : 21 s perdues sur une fausse sortie).
        if _cur_cell() == target_cell:
            dead_exits.add(target_cell)
            _nav()._banned_exit_cells.setdefault(old_map_id, set()).add(target_cell)
            _dead_switches += 1
            nxt = _select_target(_cur_cell()) if _dead_switches <= MAX_DEAD_EXIT_SWITCHES else None
            if nxt is not None and nxt != target_cell:
                logger.warning("[mapnav] Sortie morte %d (map #%d) — bascule sur sortie %d",
                               target_cell, old_map_id, nxt)
                bridge.add_console(f"Bot : sortie #{target_cell} morte → essai sortie #{nxt}")
                target_cell = nxt
                exits_set.add(target_cell)
                _nav()._pending_exit_cell = target_cell
                continue  # retry immédiat avec la nouvelle sortie
            logger.warning("[mapnav] Plus aucune sortie '%s' exploitable sur map #%d "
                           "(sorties mortes: %s)", direction, old_map_id, dead_exits)
            break

        if attempt >= MAX_NAV_ATTEMPTS - 1:
            break  # dernière tentative échouée, pas de repositionnement inutile

        # --- Repositionnement intelligent ---
        # Chercher la case la plus proche de la sortie depuis laquelle l'A* peut l'atteindre.
        # Priorité sur l'unstick aveugle : on sait à l'avance que la case choisie a un chemin.
        cur = _cur_cell()
        if cur < 0:
            continue

        eff_blocked = blocked | _nav()._dead_cells
        best = _find_valid_nav_cell(cur, target_cell, width, eff_blocked, _unstick_tried)

        if best is not None:
            _unstick_tried.add(best)
            logger.info(
                "[mapnav] Repositionnement vers %d (chemin valide vers sortie %d, dead_cells=%s)",
                best, target_cell, _nav()._dead_cells,
            )
            bridge.add_console(f"Bot : repositionnement #{best} → sortie #{target_cell}")
            ok_move = await actions.move_to(best, blocked=eff_blocked, width=width)
            if ok_move:
                new_pos = _cur_cell()
                if new_pos >= 0 and new_pos != cur:
                    map_changed = await _try_nav_and_gdk(new_pos, "post-reposition")
                    if map_changed:
                        break
                else:
                    # Serveur refuse tout mouvement depuis cur (cellule "super morte").
                    # _find_valid_nav_cell a trouvé des candidats mais aucun n'est
                    # atteignable physiquement → fallback unstick avec BFS progressif.
                    logger.warning(
                        "[mapnav] Serveur immobile depuis %d (best=%d ignoré) — fallback unstick",
                        cur, best,
                    )
                    bridge.add_console(f"Bot : cellule #{cur} totalement bloquée — unstick forcé")
                    await _unstick(cur, exits_set, eff_blocked, width,
                                   tried_cells=_unstick_tried, attempt=attempt)
                    new_pos = _cur_cell()
                    if new_pos >= 0 and new_pos != cur:
                        logger.info(
                            "[mapnav] Unstick réussi (%d→%d) : tentative immédiate",
                            cur, new_pos,
                        )
                        map_changed = await _try_nav_and_gdk(new_pos, "post-unstick-superdead")
                        if map_changed:
                            break
                    else:
                        # Aucun mouvement possible depuis cur — arrêt immédiat des tentatives
                        logger.warning(
                            "[mapnav] Bot totalement immobile sur %d — abandon navigation", cur,
                        )
                        bridge.add_console(f"Bot : impossible de bouger depuis #{cur} — abandon")
                        break
        else:
            # Fallback : aucune case valide trouvée (zone isolée) → BFS aléatoire
            logger.warning(
                "[mapnav] Aucune case avec chemin vers %d (dead_cells=%s) — fallback unstick",
                target_cell, _nav()._dead_cells,
            )
            await _unstick(cur, exits_set, eff_blocked, width,
                           tried_cells=_unstick_tried, attempt=attempt)
            new_pos = _cur_cell()
            if new_pos >= 0 and new_pos != cur:
                logger.info(
                    "[mapnav] Post-unstick (%d→%d) : tentative immédiate (dead_cells=%s)",
                    cur, new_pos, _nav()._dead_cells,
                )
                map_changed = await _try_nav_and_gdk(new_pos, "post-unstick")
                if map_changed:
                    break

    if not map_changed:
        logger.warning("[mapnav] change_map : échec après %d tentatives — map #%d toujours active",
                       MAX_NAV_ATTEMPTS, old_map_id)
        bridge.add_console(f"Bot : échec changement map '{direction}' après {MAX_NAV_ATTEMPTS} tentatives")
        _nav()._pending_exit_cell = -1
        _nav()._pending_exit_map_id = None
        return False, None, None

    new_map_id = _state.current.current_map.map_id if _state.current.current_map else "?"
    logger.info("[mapnav] Nouvelle map #%s détectée", new_map_id)
    _nav()._last_successful_exit = (old_map_id, target_cell)
    return True, new_map_id, char
