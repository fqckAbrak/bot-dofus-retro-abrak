"""Optimise les options de combat du client Abrak Retro (PepperFlash).

Le client Dofus Rétro tourne en PepperFlash dans Electron. Ses options sont
persistées dans un SharedObject AMF0 :

    %APPDATA%\\Abrak Retro\\Pepper Data\\Shockwave Flash\\WritableRoot\\
        #SharedObjects\\<rand>\\localhost\\...\\loader.swf\\ANKOPTIONSSO.sol

La plupart des options d'accélération sont déjà activées par l'utilisateur
(SkipFightAnimations, SkipFightPlayerAnimations, DisableDeathAnimation,
SkipLootPanel…). Ce script active les leviers restants qui réduisent le temps
de rendu d'un tour côté client — donc le délai avant que le Flash renvoie le
GKK que le bot attend ([combat.py] wait_gkk / wait_actions_clear) :

    TacticMode          False -> True   (rendu tuiles plates, pas de sprites)
    UseLightEndFightUI  False -> True   (panneau de fin de combat allégé)
    CreaturesMode       50.0  -> 0.0    (animations de créatures minimales)

IMPORTANT : le client Flash réécrit ce fichier à la fermeture. Il faut donc
fermer TOUS les clients Abrak avant de lancer ce script, sinon les modifs sont
écrasées. Le script refuse de tourner si Abrak.exe est détecté.

Patch en place (aucun changement de taille de fichier) + sauvegarde .bak.
"""
from __future__ import annotations

import glob
import os
import shutil
import struct
import subprocess
import sys

# La console Windows par défaut (cp1252) ne sait pas afficher ✓/✗/→ : forcer UTF-8.
try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

# Flags booléens à forcer à True (AMF0 type 0x01 : 1 octet de valeur).
BOOL_FLAGS_TRUE = ("TacticMode", "UseLightEndFightUI")
# Doubles à forcer à 0.0 (AMF0 type 0x00 : 8 octets big-endian).
DOUBLE_FLAGS_ZERO = {"CreaturesMode": 0.0}


def _find_sol() -> str | None:
    base = os.path.join(
        os.environ.get("APPDATA", ""),
        "Abrak Retro", "Pepper Data", "Shockwave Flash",
        "WritableRoot", "#SharedObjects",
    )
    matches = glob.glob(os.path.join(base, "**", "ANKOPTIONSSO.sol"), recursive=True)
    return matches[0] if matches else None


def _abrak_running() -> bool:
    try:
        out = subprocess.run(
            ["tasklist", "/FI", "IMAGENAME eq Abrak.exe", "/NH"],
            capture_output=True, text=True, timeout=10,
        ).stdout
        return "Abrak.exe" in out
    except Exception:
        return False  # à défaut, on ne bloque pas


def _patch_bool(data: bytearray, key: str) -> str:
    """Met une clé booléenne AMF0 à True. Clé recherchée length-prefixed pour éviter
    les faux positifs (ex. 'TacticMode' ⊂ 'PrettyTacticMode')."""
    needle = struct.pack(">H", len(key)) + key.encode()
    idx = bytes(data).find(needle)
    if idx < 0:
        return f"  {key}: introuvable (ignoré)"
    vpos = idx + len(needle)
    if data[vpos] != 0x01:  # type AMF0 boolean attendu
        return f"  {key}: type inattendu 0x{data[vpos]:02x} (ignoré)"
    old = data[vpos + 1]
    data[vpos + 1] = 0x01
    return f"  {key}: {bool(old)} -> True"


def _patch_double(data: bytearray, key: str, value: float) -> str:
    needle = struct.pack(">H", len(key)) + key.encode()
    idx = bytes(data).find(needle)
    if idx < 0:
        return f"  {key}: introuvable (ignoré)"
    vpos = idx + len(needle)
    if data[vpos] != 0x00:  # type AMF0 number attendu
        return f"  {key}: type inattendu 0x{data[vpos]:02x} (ignoré)"
    old = struct.unpack(">d", data[vpos + 1:vpos + 9])[0]
    data[vpos + 1:vpos + 9] = struct.pack(">d", value)
    return f"  {key}: {old} -> {value}"


def main() -> int:
    if _abrak_running():
        print("✗ Abrak.exe est en cours d'exécution.")
        print("  Ferme TOUS les clients avant de relancer (sinon Flash écrase les modifs).")
        return 1

    sol = _find_sol()
    if not sol:
        print("✗ ANKOPTIONSSO.sol introuvable sous %APPDATA%\\Abrak Retro.")
        return 1
    print(f"SOL : {sol}")

    data = bytearray(open(sol, "rb").read())
    backup = sol + ".bak"
    if not os.path.exists(backup):
        shutil.copy2(sol, backup)
        print(f"Sauvegarde : {backup}")
    else:
        print(f"Sauvegarde déjà présente : {backup}")

    print("Modifications :")
    for k in BOOL_FLAGS_TRUE:
        print(_patch_bool(data, k))
    for k, v in DOUBLE_FLAGS_ZERO.items():
        print(_patch_double(data, k, v))

    open(sol, "wb").write(data)
    print("✓ SOL mis à jour. Relance le client pour appliquer.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
