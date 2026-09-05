"""Génère la variante no-anim du client (``core.noanim.swf``) — wrapper CLI.

La logique de patch (P-code AS2 via FFDec/JPEXS) vit dans ``bot.noanim`` —
voir ``bot/noanim.py:generate_variant`` pour le détail de la méthode et son
justificatif. Ce script n'est plus qu'une façade en ligne de commande ; le
même code est appelé depuis le dashboard (onglet Paramètres → bouton
« Patcher no-anim »).

À relancer après chaque mise à jour du client (le ``core.swf`` change).

Usage :

    python tools/build_noanim_swf.py [--game-path DIR] [--ffdec CHEMIN_JAR]

Par défaut le chemin du jeu est lu dans ``config.json`` et FFDec est cherché
dans ``E:\\tools\\jpexs\\ffdec-cli.jar``, la variable d'env ``FFDEC_JAR`` ou
les emplacements d'installation standards (voir ``bot.noanim._find_ffdec``).
"""
from __future__ import annotations

import argparse
import json
import os
import sys

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

from bot import noanim  # noqa: E402

# Alias de compatibilité : build_invdump_swf.py et build_autopilot_swf.py
# importent ce module (`import build_noanim_swf as _na`) et utilisent ces
# noms directement — la logique vit maintenant dans bot.noanim.
_find_ffdec = noanim._find_ffdec
_run = noanim._run
_patch_worldmap_doubleclick_pcode = noanim._patch_worldmap_doubleclick_pcode


def _load_config() -> dict:
    cfg_path = os.path.join(_ROOT, "config.json")
    try:
        with open(cfg_path, "r", encoding="utf-8") as f:
            return json.load(f)
    except (OSError, json.JSONDecodeError):
        return {}


def _load_game_path() -> str | None:
    return _load_config().get("game_path")


def main() -> int:
    ap = argparse.ArgumentParser(description="Génère core.noanim.swf (no-anim déplacements).")
    ap.add_argument("--game-path", default=None, help="Dossier du jeu (défaut: config.json).")
    ap.add_argument("--ffdec", default=None, help="Chemin vers ffdec-cli.jar (défaut: config.json).")
    args = ap.parse_args()

    cfg = _load_config()
    game_path = args.game_path or cfg.get("game_path")
    ffdec_path = args.ffdec or cfg.get("ffdec_path")
    ok, msg = noanim.generate_variant(game_path, ffdec_path, progress=print)
    print(("✓ " if ok else "✗ ") + msg)
    if ok:
        print("  Active-la via l'onglet Paramètres → No-anim (effet au prochain "
              "lancement du client).")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
