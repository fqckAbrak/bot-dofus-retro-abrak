"""Génère ``core.autopilot.swf`` — déverrouille le double-clic « voyage » sur la
carte du monde, pour piloter NOTRE autopilote en jeu.

Contexte (cf. mémoire project_autopilot_natif) : le voyage natif du serveur est
verrouillé. Mais l'UI de la carte du monde envoie déjà, au double-clic,
``BaM{x},{y},{subAreaId}`` (``Basics.autorisedMoveCommand``) — sauf que le
listener double-clic n'est ajouté QUE pour les admins
(``MapExplorer.addListeners`` : ``if (Player.isAuthorized) addEventListener(
"doubleClick", ...)``).

Ce patch retire ce gate → le double-clic carte du monde envoie ``BaM`` pour tout
le monde. Le bot l'intercepte (handler ``@on_client_message("BaM")`` dans
game/state.py) et lance ``autopilot.travel_to(x, y)``. Le serveur, lui, ignore le
``BaM`` (répond ``BN``) : aucun effet côté serveur, juste notre déclencheur.

Méthode : patch **P-code** chirurgical (comme no-anim — pas de recompilation
source d'une classe obfusquée). On remplace, dans ``addListeners`` :

    Push "isAuthorized" / GetMember / Not / If <label>     (saute si non-admin)
par
    Push "isAuthorized" / GetMember / Pop                   (toujours ajouter)

→ le ``addEventListener("doubleClick", ...)`` s'exécute toujours ; tout le reste
du bytecode est identique à l'octet près.

Pipeline (identique à build_noanim_swf) :
  1. Source vierge : ``core.swf.orig.bak`` sinon ``core.swf``.
  2. Export du P-code AS2.
  3. Patch le ``.pcode`` de MapExplorer.
  4. Réimporte ce seul script → ``core.autopilot.swf``.

Usage :
    py -3 tools/build_autopilot_swf.py [--source SWF] [--out SWF] [--ffdec JAR]

Bascule on/off : copier ``core.autopilot.swf`` → ``core.swf`` (après backup),
comme bot/noanim.py. Pour cumuler avec no-anim + invdump, chaîner ce build sur la
variante combo (``--source core.combo.swf --out core.combo.swf``).
"""

from __future__ import annotations

import argparse
import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import build_noanim_swf as _na  # noqa: E402
from bot import noanim  # noqa: E402

# Le patch lui-même vit dans build_noanim_swf (réutilisé par le combo / _PATCHERS).
_patch_worldmap_doubleclick_pcode = _na._patch_worldmap_doubleclick_pcode


def main() -> int:
    ap = argparse.ArgumentParser(description="Build SWF patché (déverrouille double-clic carte du monde)")
    ap.add_argument("--game-path")
    ap.add_argument("--ffdec")
    ap.add_argument("--source", help="SWF de base (défaut: core.swf.orig.bak sinon core.swf)")
    ap.add_argument("--out", help="SWF de sortie (défaut: core.autopilot.swf)")
    args = ap.parse_args()

    game_path = args.game_path or _na._load_game_path()
    modules = noanim.find_modules_dir(game_path)
    if not modules:
        print(f"✗ modules/core.swf introuvable sous : {game_path!r}")
        return 1
    core = os.path.join(modules, "core.swf")
    orig = os.path.join(modules, "core.swf.orig.bak")
    src = args.source or (orig if os.path.exists(orig) else core)
    if not os.path.isabs(src):
        src = os.path.join(modules, src)
    out = args.out or os.path.join(modules, "core.autopilot.swf")
    if not os.path.isabs(out):
        out = os.path.join(modules, out)

    ffdec = _na._find_ffdec(args.ffdec)
    if not ffdec:
        print("✗ JPEXS/FFDec introuvable (--ffdec ou FFDEC_JAR)")
        return 1
    print(f"Source : {src}")
    print(f"FFDec  : {ffdec}")

    with tempfile.TemporaryDirectory(prefix="autopilot_") as tmp:
        export_dir = os.path.join(tmp, "export")
        print("Export du P-code AS2…")
        _na._run(ffdec, "-format", "script:pcode", "-export", "script", export_dir, src)

        patch_root = os.path.join(tmp, "patch")
        applied = False
        for root, _dirs, files in os.walk(export_dir):
            for name in files:
                if not name.endswith(".pcode"):
                    continue
                p = os.path.join(root, name)
                try:
                    txt = open(p, "r", encoding="utf-8", errors="replace").read()
                except OSError:
                    continue
                res = _patch_worldmap_doubleclick_pcode(txt)
                if res is not None:
                    dst = os.path.join(patch_root, os.path.relpath(p, export_dir))
                    os.makedirs(os.path.dirname(dst), exist_ok=True)
                    open(dst, "w", encoding="utf-8").write(res)
                    print(f"Patch  : double-clic carte du monde déverrouillé  →  "
                          f"{os.path.relpath(p, export_dir)}")
                    applied = True
                    break
            if applied:
                break

        if not applied:
            print("✗ Motif isAuthorized/doubleClick introuvable dans le P-code "
                  "(structure du client modifiée ?) — à revoir.")
            return 1

        print("Réassemblage du P-code patché…")
        _na._run(ffdec, "-importScript", src, out, patch_root)

    print(f"✓ Écrit {out}")
    print("  Pour activer : sauvegarde core.swf puis copie core.autopilot.swf → core.swf,")
    print("  relance Abrak, ouvre la carte du monde et DOUBLE-CLIQUE une map.")
    return 0


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    raise SystemExit(main())
