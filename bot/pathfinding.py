"""
Pathfinding A* et encodage du chemin pour GA001.

Référence : LeafMITM — character/path_finding.py (github.com/Azzary/LeafMITM)
Port sans numpy — dict-based A*.

Coordonnées isométriques Dofus Rétro :
  - cell_id → (x, y) via cell_to_xy()
  - (x, y)  → cell_id via xy_to_cell()
  - Grille alternée pair/impair (odd rows décalées)

Encoding GA001 (C→S) :
  Chaque waypoint = 3 chars : {direction_char}{cell_char1}{cell_char2}
  en alphabet ZIPKEY (alphabétique : abc...ABC...01-_).
  On n'émet un waypoint qu'aux changements de direction (compression).
"""

from __future__ import annotations

import heapq
import math
import logging

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Constantes
# ---------------------------------------------------------------------------

MAP_WIDTH: int = 15  # confirmé dans les logs : "15x17, 479 cellules"
"""Largeur par défaut des maps overworld Dofus Rétro 1.29."""

ZIPKEY: str = "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789-_"
"""Alphabet pour l'encodage GA001 (direction + cellule)."""

# Indices des directions (matching LeafMITM)
_DIRS: dict[str, int] = {
    "e": 0, "se": 1, "s": 2, "sw": 3,
    "w": 4, "nw": 5, "n": 6, "ne": 7,
}

# Timings de déplacement (secondes par case)
_TIMING_RUN: dict[str, float] = {"horizontal": 0.3, "vertical": 0.2, "diagonal": 0.2}
_TIMING_WALK: dict[str, float] = {"horizontal": 0.75, "vertical": 0.5, "diagonal": 0.5}

# ---------------------------------------------------------------------------
# Conversions cell_id ↔ (x, y)
# ---------------------------------------------------------------------------


def cell_to_xy(cell_id: int, width: int = MAP_WIDTH) -> tuple[int, int]:
    """Convertir un cell_id en coordonnées (x, y) isométriques.

    Port de LeafMITM from_cell_id_to_x_y_pos().
    """
    row_len = width * 2 - 1
    pos_y = (cell_id // row_len) * 2
    if (cell_id % row_len) >= width:
        pos_y += 1
    if cell_id > (width - 1) * 2:
        pos_x = (cell_id + (pos_y // 2)) % width + 1
    else:
        pos_x = cell_id % width + 1
    return pos_x - 1, pos_y


def xy_to_cell(x: int, y: int, width: int = MAP_WIDTH) -> int:
    """Convertir des coordonnées (x, y) en cell_id.

    Port de LeafMITM from_pos_x_y_to_cell_id().
    """
    return y * width + x - (y // 2)


# ---------------------------------------------------------------------------
# A* pathfinding
# ---------------------------------------------------------------------------

def _heuristic(a: tuple[int, int], b: tuple[int, int]) -> float:
    return math.sqrt((b[0] - a[0]) ** 2 + (b[1] - a[1]) ** 2)


# Voisins pour pair et impair (non-combat, port LeafMITM)
_ODD_NEIGHBORS  = [(0, 1), (0, -1), (1, 0), (-1, 0), (1, 1),  (1, -1),  (0, 2), (0, -2)]
_PAIR_NEIGHBORS = [(0, 1), (0, -1), (1, 0), (-1, 0), (-1, 1), (-1, -1), (0, 2), (0, -2)]


def astar(
    blocked: set[int],
    start_cell: int,
    goal_cell: int,
    width: int = MAP_WIDTH,
) -> list[int] | None:
    """Trouver le chemin optimal entre deux cell_ids.

    Args:
        blocked   : Ensemble de cell_ids non praticables.
        start_cell: Cell de départ.
        goal_cell : Cell d'arrivée.
        width     : Largeur de la map.

    Returns:
        Liste de cell_ids (start inclus, goal inclus), ou None si impossible.
    """
    start = cell_to_xy(start_cell, width)
    goal  = cell_to_xy(goal_cell,  width)

    # Borne supérieure sur y pour éviter l'exploration de cellules fantômes
    # au-delà de la carte (la grille isométrique n'a pas de limite haute naturelle).
    _max_y = max(start[1], goal[1]) + width * 4

    close_set: set[tuple[int, int]] = set()
    came_from: dict[tuple[int, int], tuple[int, int]] = {}
    gscore: dict[tuple[int, int], float] = {start: 0.0}
    heap: list[tuple[float, tuple[int, int]]] = []
    heapq.heappush(heap, (0.0, start))

    while heap:
        _, current = heapq.heappop(heap)

        # Entrée obsolète dans le heap (déjà traitée avec un meilleur coût)
        if current in close_set:
            continue

        if current == goal:
            # Reconstruire le chemin
            path_xy: list[tuple[int, int]] = []
            while current in came_from:
                path_xy.append(current)
                current = came_from[current]
            path_xy.append(start)
            path_xy.reverse()
            return [xy_to_cell(x, y, width) for x, y in path_xy]

        close_set.add(current)

        neighbors = _PAIR_NEIGHBORS if current[1] % 2 == 0 else _ODD_NEIGHBORS

        for di, dj in neighbors:
            neighbor = (current[0] + di, current[1] + dj)

            # Vérifier les bornes
            if not (0 <= neighbor[0] < width):
                continue
            if not (0 <= neighbor[1] <= _max_y):
                continue

            if neighbor in close_set:
                continue

            neighbor_cell = xy_to_cell(neighbor[0], neighbor[1], width)

            # Cellule bloquée
            if neighbor_cell in blocked:
                continue

            # Coût de déplacement (multiplier selon type de mouvement)
            if abs(di) == 1 and dj == 0:
                mult = 1.2   # horizontal pur
            elif abs(di + dj) == 2:
                mult = 0.7   # diagonal rapide
            else:
                mult = 1.0

            tentative_g = gscore[current] + _heuristic(current, neighbor) * mult

            if tentative_g < gscore.get(neighbor, float("inf")):
                came_from[neighbor] = current
                gscore[neighbor] = tentative_g
                f = tentative_g + _heuristic(neighbor, goal)
                heapq.heappush(heap, (f, neighbor))

    return None


# ---------------------------------------------------------------------------
# Direction entre deux cellules adjacentes
# ---------------------------------------------------------------------------

def _find_direction(start_xy: tuple[int, int], end_xy: tuple[int, int]) -> int:
    """Calculer la direction (0-7) entre deux cases adjacentes.

    Port de LeafMITM find_direction().
    """
    sx, sy = start_xy
    ex, ey = end_xy
    t = ""

    if sy - ey > 0:
        t += "n"
    elif sy - ey < 0:
        t += "s"

    if t == "":
        if sx - ex < 0:
            t += "e"
        elif sx - ex > 0:
            t += "w"
    else:
        start_pair = (sy % 2 == 0)
        end_pair   = (ey % 2 == 0)
        if start_pair and not end_pair:
            if sx - ex == 0:
                t += "e"
            elif sx - ex > 0:
                t += "w"
        elif not start_pair and end_pair:
            if sx - ex == 0:
                t += "w"
            elif sx - ex < 0:
                t += "e"

    return _DIRS.get(t, 0)


# ---------------------------------------------------------------------------
# Encoding GA001
# ---------------------------------------------------------------------------

def _encode_waypoint(cell_id: int, direction: int) -> str:
    """Encoder un waypoint en 3 chars ZIPKEY : {dir}{cell_hi}{cell_lo}."""
    d  = ZIPKEY[direction]
    c1 = ZIPKEY[cell_id // 64]
    c2 = ZIPKEY[cell_id % 64]
    return d + c1 + c2


def path_to_ga001(cell_path: list[int], width: int = MAP_WIDTH) -> str:
    """Convertir une liste de cell_ids en payload GA001.

    N'émet qu'un waypoint aux changements de direction (compression LeafMITM).
    Format : {dir}{cell_hi}{cell_lo} répété pour chaque waypoint.

    Args:
        cell_path: Liste de cell_ids (retourné par astar()).
        width    : Largeur de la map.

    Returns:
        Chaîne de longueur multiple de 3 (payload GA001 sans préfixe ni \\n).
    """
    if len(cell_path) < 2:
        return ""

    xys = [cell_to_xy(c, width) for c in cell_path]
    result = ""
    last_dir = None

    for i in range(1, len(xys)):
        direction = _find_direction(xys[i - 1], xys[i])
        if i == 1:
            last_dir = direction
        if direction != last_dir:
            # Changement de direction : émettre le waypoint précédent
            result += _encode_waypoint(cell_path[i - 1], last_dir)
            last_dir = direction

    # Toujours émettre la cellule finale
    result += _encode_waypoint(cell_path[-1], last_dir)
    return result


# ---------------------------------------------------------------------------
# Calcul de la durée du déplacement
# ---------------------------------------------------------------------------

def movement_duration(cell_path: list[int], width: int = MAP_WIDTH) -> float:
    """Calculer le temps de déplacement estimé (secondes).

    Port de LeafMITM timing() — run si > 2 cases, sinon walk.
    Ajoute 0.4 s de marge pour le lag réseau.

    Args:
        cell_path: Liste de cell_ids du chemin.
        width    : Largeur de la map.

    Returns:
        Durée en secondes.
    """
    if len(cell_path) < 2:
        return 0.0

    timing = _TIMING_RUN if len(cell_path) > 2 else _TIMING_WALK
    xys = [cell_to_xy(c, width) for c in cell_path]
    total = 0.0

    for i in range(len(xys) - 1):
        d = _find_direction(xys[i], xys[i + 1])
        if d in (2, 6):          # s ou n → vertical
            total += timing["vertical"]
        elif d in (0, 4):        # e ou w → horizontal
            total += timing["horizontal"]
        else:                    # diagonal
            total += timing["diagonal"]

    return total + 0.4


# ---------------------------------------------------------------------------
# Utilitaires
# ---------------------------------------------------------------------------

def adjacent_cells(cell_id: int, width: int = MAP_WIDTH) -> list[int]:
    """Retourner les cell_ids directement adjacents (4 directions cardinales).

    Utilisé pour trouver une cellule où se placer à côté d'une ressource.
    """
    x, y = cell_to_xy(cell_id, width)
    neighbors = _PAIR_NEIGHBORS if y % 2 == 0 else _ODD_NEIGHBORS
    result = []
    for di, dj in neighbors[:4]:   # 4 premiers = cardinaux
        nx, ny = x + di, y + dj
        if 0 <= nx < width and ny >= 0:
            result.append(xy_to_cell(nx, ny, width))
    return result
