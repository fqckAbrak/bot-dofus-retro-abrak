"""
Construit le graphe du monde (offline) pour l'autopilote inter-maps.

Parcourt toutes les maps XML (cf. bot.mapdata._MAPS_DIR), et pour chacune extrait :
  - ses coordonnées monde (x, y) et sa largeur,
  - les directions de SORTIE disponibles (left/right/top/bottom), détectées via
    les cellules Sol Magique (soleils) sur chaque bord — avec repli sur les
    cellules praticables de bord (même logique que bot.mapnav.get_exit_cells).

Produit `ressources/world_graph.json` :
  {
    "deltas": {"left": [-1,0], "right": [1,0], "top": [0,-1], "bottom": [0,1]},
    "maps": { "<map_id>": {"x":27,"y":-50,"w":15,"sun":["top"],"open":["top","right"]} },
    "coord_index": { "27,-50": [8343, ...] }
  }

`sun`  = bords ayant au moins un soleil (sortie « officielle », forte confiance).
`open` = bords ayant au moins une cellule praticable (surensemble, repli).

Le planificateur (bot/autopilot.py) BFS sur coord_index en ne suivant que les
bords présents dans `open` (ou `sun`), puis l'exécuteur valide chaque saut au
runtime via mapnav.change_map (et apprend les vraies arêtes).

Usage :
    py -3 tools/build_world_graph.py                 # construit le JSON
    py -3 tools/build_world_graph.py --inspect 27 -50  # affiche la zone autour de (27,-50)
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from bot.mapdata import _MAPS_DIR, load_map  # noqa: E402
from bot.pathfinding import cell_to_xy        # noqa: E402

# Déplacement monde par direction (validé sur le trajet Feudala) :
#   top = nord = y-1 ; bottom = sud = y+1 ; left = ouest = x-1 ; right = est = x+1
DELTAS: dict[str, tuple[int, int]] = {
    "left": (-1, 0), "right": (1, 0), "top": (0, -1), "bottom": (0, 1),
}

OUT_PATH = Path(__file__).resolve().parent.parent / "ressources" / "world_graph.json"


def _border_dirs(info) -> tuple[list[str], list[str]]:
    """Retourner (sun_dirs, open_dirs) pour une MapInfo.

    Réplique la détection de bord de bot.mapnav.get_exit_cells :
      left = x==0 ; right = x>=w-2 ; top = y<=1 ; bottom = y>=max_y-1
    """
    width = info.width
    walkable = info.walkable_cells
    if not walkable:
        return [], []
    sun = info.sun_magic_cells

    xy = {c: cell_to_xy(c, width) for c in walkable}
    max_y = max(y for _x, y in xy.values())

    def on_border(c: int, direction: str) -> bool:
        x, y = xy[c]
        if direction == "left":   return x == 0
        if direction == "right":  return x >= width - 2
        if direction == "top":    return y <= 1
        if direction == "bottom": return y >= max_y - 1
        return False

    sun_dirs, open_dirs = [], []
    for d in ("left", "right", "top", "bottom"):
        border = [c for c in walkable if on_border(c, d)]
        if not border:
            continue
        open_dirs.append(d)
        if any(c in sun for c in border):
            sun_dirs.append(d)
    return sun_dirs, open_dirs


def build() -> dict:
    files = sorted(_MAPS_DIR.glob("*.xml"))
    total = len(files)
    print(f"[world_graph] {total} maps dans {_MAPS_DIR}")

    maps: dict[str, dict] = {}
    coord_index: dict[str, list[int]] = {}
    t0 = time.time()
    skipped = 0

    for i, f in enumerate(files):
        if not f.stem.lstrip("-").isdigit():
            continue
        map_id = int(f.stem)
        info = load_map(map_id)
        if info is None:
            skipped += 1
            continue
        sun_dirs, open_dirs = _border_dirs(info)
        maps[str(map_id)] = {
            "x": info.x, "y": info.y, "w": info.width,
            "sun": sun_dirs, "open": open_dirs,
        }
        coord_index.setdefault(f"{info.x},{info.y}", []).append(map_id)

        if (i + 1) % 1000 == 0:
            print(f"  … {i + 1}/{total} ({time.time() - t0:.1f}s)")

    print(f"[world_graph] {len(maps)} maps indexées, {skipped} ignorées, "
          f"{len(coord_index)} coordonnées uniques ({time.time() - t0:.1f}s)")
    return {"deltas": {k: list(v) for k, v in DELTAS.items()},
            "maps": maps, "coord_index": coord_index}


def inspect(graph: dict, cx: int, cy: int, radius: int = 2) -> None:
    """Afficher la connectivité autour d'une coordonnée (debug visuel)."""
    maps = graph["maps"]
    idx = graph["coord_index"]
    print(f"\n=== Zone autour de ({cx},{cy}) rayon {radius} ===")
    for dy in range(-radius, radius + 1):
        for dx in range(-radius, radius + 1):
            x, y = cx + dx, cy + dy
            ids = idx.get(f"{x},{y}", [])
            if not ids:
                print(f"  ({x:>4},{y:>4}) : —")
                continue
            for mid in ids:
                m = maps[str(mid)]
                print(f"  ({x:>4},{y:>4}) : map #{mid:<10} sorties={m['open']}  soleils={m['sun']}")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--inspect", nargs=2, type=int, metavar=("X", "Y"),
                    help="afficher la zone autour de (X,Y) au lieu de reconstruire")
    ap.add_argument("--radius", type=int, default=2)
    args = ap.parse_args()

    if args.inspect is not None and OUT_PATH.exists():
        graph = json.loads(OUT_PATH.read_text(encoding="utf-8"))
        inspect(graph, args.inspect[0], args.inspect[1], args.radius)
        return

    graph = build()
    OUT_PATH.write_text(json.dumps(graph, separators=(",", ":")), encoding="utf-8")
    size_mb = OUT_PATH.stat().st_size / 1e6
    print(f"[world_graph] écrit : {OUT_PATH} ({size_mb:.1f} Mo)")

    if args.inspect is not None:
        inspect(graph, args.inspect[0], args.inspect[1], args.radius)


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    main()
