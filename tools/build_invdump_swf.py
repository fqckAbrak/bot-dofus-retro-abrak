"""Génère ``core.invdump.swf`` — client patché pour dumper l'inventaire complet.

⚠️ EXPÉRIMENTAL — à valider en jeu (build + test live). Voir
``tools/inventory_dump_patch.as`` pour le détail du mécanisme (parser "ZI" →
réponse "ZO{uid~gid~qty~pos;...}", captée et droppée par le relais).

Contrairement au no-anim (patch P-code chirurgical), ce patch AJOUTE un parser
de message. On l'injecte au niveau **source** dans le script qui enregistre déjà
les autres ``addAdditionalPacketParser(...)`` (où ``_global.API`` est prêt), puis
on réimporte ce seul script via JPEXS.

Pipeline :
  1. Source vierge : ``core.swf.orig.bak`` sinon ``core.swf`` (+ backup créé).
  2. Export des scripts AS2 (source).
  3. Repère le script contenant ``addAdditionalPacketParser("NN"`` et y ajoute
     l'enregistrement du parser "ZI".
  4. Réimporte ce script → écrit ``core.invdump.swf`` à côté de ``core.swf``.

Usage :
    py -3 tools/build_invdump_swf.py [--game-path DIR] [--ffdec JAR]

Bascule on/off : copier ``core.invdump.swf`` → ``core.swf`` (après backup),
comme le fait ``bot/noanim.py`` pour la variante no-anim.
"""

from __future__ import annotations

import argparse
import os
import shutil
import sys
import tempfile

# Réutilise les helpers du build no-anim.
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import build_noanim_swf as _na  # noqa: E402
from bot import noanim  # noqa: E402

# Bloc AS2 à insérer en fin du frame 1 (timeline principale, code plain qui se
# recompile proprement). DÉFENSIF : setInterval réessaie jusqu'à ce que l'API et
# addAdditionalPacketParser soient prêts (le frame 1 tourne avant l'init API).
_REGISTRATION = r'''
// --- PATCH bot : dump d'inventaire a la demande (message ZI -> ZO) ---
_global.__invDumpInit = function()
{
   if (_global.addAdditionalPacketParser == undefined || _global.API == undefined || _global.API.network == undefined || _global.API.datacenter == undefined || _global.API.datacenter.Player == undefined || _global.API.datacenter.Player.Inventory == undefined)
   {
      return;
   }
   if (_global.__invDumpDone) { return; }
   _global.__invDumpDone = true;
   _global.addAdditionalPacketParser("ZI", function(sData)
   {
      var inv = _global.API.datacenter.Player.Inventory.clone();
      var s = "";
      var i = 0;
      while (i < inv.length)
      {
         var it = inv[i];
         s += it._nID + "~" + it._nUnicID + "~" + it._nQuantity + "~" + it._nPosition + ";";
         i = i + 1;
      }
      _global.API.network.send("ZO" + s, false, undefined, true);
   });
};
_global.__invDumpTimer = setInterval(_global.__invDumpInit, 1000);
'''


def main() -> int:
    ap = argparse.ArgumentParser(description="Build SWF patché (dump inventaire)")
    ap.add_argument("--game-path")
    ap.add_argument("--ffdec")
    ap.add_argument("--source", help="SWF de base à patcher (défaut: core.swf.orig.bak)")
    ap.add_argument("--out", help="SWF de sortie (défaut: core.invdump.swf)")
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
    out = args.out or os.path.join(modules, "core.invdump.swf")
    if not os.path.isabs(out):
        out = os.path.join(modules, out)

    ffdec = _na._find_ffdec(args.ffdec)
    if not ffdec:
        print("✗ JPEXS/FFDec introuvable (--ffdec ou FFDEC_JAR)")
        return 1

    with tempfile.TemporaryDirectory() as tmp:
        scripts_dir = os.path.join(tmp, "scripts")
        print(f"• Export des scripts depuis {src}")
        _na._run(ffdec, "-export", "script", scripts_dir, src)

        # Cible : frame 1 de la timeline principale (code plain, recompile OK).
        rel = os.path.join("scripts", "frame_1", "DoAction.as")
        target = os.path.join(scripts_dir, rel)
        if not os.path.exists(target):
            print(f"✗ {rel} introuvable")
            return 1
        print(f"• Injection dans {rel}")
        with open(target, "a", encoding="utf-8") as fh:
            fh.write("\n" + _REGISTRATION + "\n")

        # Importer UNIQUEMENT ce script (dossier isolé) pour ne pas recompiler
        # les classes obfusquées (qui ne round-trippent pas en source).
        one = os.path.join(tmp, "one")
        dst = os.path.join(one, rel)
        os.makedirs(os.path.dirname(dst), exist_ok=True)
        shutil.copy2(target, dst)

        shutil.copy2(src, out)
        print(f"• Réimport (1 script) → {out}")
        _na._run(ffdec, "-importScript", out, out, one)

    print(f"✓ Écrit {out}")
    print("  Pour activer : sauvegarde core.swf puis copie core.invdump.swf → core.swf,")
    print("  relance Abrak, et teste le bouton « ↻ Inventaire » de l'onglet Vendre.")
    return 0


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    raise SystemExit(main())
