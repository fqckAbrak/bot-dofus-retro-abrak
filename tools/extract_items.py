"""
Extraction de la base d'items Dofus Rétro (serveur Abrak) → ressources/items.json.

Le client Abrak télécharge ses données de langue (dont la base d'items) depuis
son CDN. La base d'items vit dans un SWF AS2 :

    https://cdn.abrak.fr/lang/swf/items_fr_{version}.swf

Une fois décompilé (JPEXS), ce SWF contient un unique script ``DoAction`` avec :

  * ``I.t[typeId] = {n:"<nom du type>", t:<catégorie>}``      → types d'objets
        ex : I.t[11] = {n:"Botte", t:5}   (catégorie 5 = bottes)
  * ``I.u[gid]    = {... l:<niveau> ... t:<typeId> ... n:"<nom>"}`` → items
        ex : I.u[100] = {... l:1, ... t:9, n:"Petit Anneau de Sagesse"}

Ce module produit ``ressources/items.json`` :

    {
      "version": 1564,
      "types":  { "<typeId>": {"name": "...", "category": <int>} },
      "items":  { "<gid>": {"l": <level>, "t": <typeId>, "n": "<name>"} }
    }

USAGE
─────
    # 1) Le plus simple : tout faire (télécharger + décompiler + parser)
    py -3 tools/extract_items.py

    # 2) À partir d'un .swf déjà téléchargé
    py -3 tools/extract_items.py --swf chemin/items_fr_1564.swf

    # 3) À partir d'un DoAction.as déjà décompilé
    py -3 tools/extract_items.py --as chemin/DoAction.as

Dépendances optionnelles : JPEXS (ffdec-cli.jar) pour décompiler un .swf.
Le chemin JPEXS par défaut est ``E:\\tools\\jpexs\\ffdec-cli.jar`` (cf. GAME_CLIENT.md).
"""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
import tempfile
import urllib.request

# --- Chemins projet -------------------------------------------------------

_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.normpath(os.path.join(_HERE, ".."))
_OUT_PATH = os.path.join(_ROOT, "ressources", "items.json")

_CDN_BASE = "https://cdn.abrak.fr"
_JPEXS_JAR = os.environ.get("JPEXS_JAR", r"E:\tools\jpexs\ffdec-cli.jar")

# --- Regex de parsing -----------------------------------------------------

# I.t[11] = {n:"Botte",t:5}        (la valeur "n" peut contenir des \' échappés)
_RE_TYPE = re.compile(r'I\.t\[(\d+)\]\s*=\s*\{n:"((?:[^"\\]|\\.)*)",t:(\d+)')

# I.u[100] = { ... };   (corps non-greedy jusqu'au premier "};")
_RE_ITEM = re.compile(r'I\.u\[(\d+)\]\s*=\s*\{(.*?)\};')
_RE_LEVEL = re.compile(r'(?:^|,)l:(\d+)')
_RE_TYPE_FIELD = re.compile(r'(?:^|,)t:(\d+)')
_RE_NAME = re.compile(r',n:"((?:[^"\\]|\\.)*)"\s*$')


def _resolve_version() -> int:
    """Lire la version courante de la base d'items depuis le CDN (versions_fr.txt)."""
    url = f"{_CDN_BASE}/lang/versions_fr.txt"
    with urllib.request.urlopen(url, timeout=30) as resp:
        txt = resp.read().decode("utf-8", "replace")
    # Format : "...&f=...|items,fr,1564|itemsets,fr,207|..."
    m = re.search(r'items,fr,(\d+)', txt)
    if not m:
        raise RuntimeError(f"Version 'items' introuvable dans {url}")
    return int(m.group(1))


def _download_swf(version: int, dest: str) -> None:
    url = f"{_CDN_BASE}/lang/swf/items_fr_{version}.swf"
    print(f"[extract_items] Téléchargement {url}")
    urllib.request.urlretrieve(url, dest)


def _decompile(swf_path: str, out_dir: str) -> str:
    """Décompiler le SWF avec JPEXS et retourner le chemin du DoAction.as."""
    if not os.path.exists(_JPEXS_JAR):
        raise RuntimeError(
            f"JPEXS introuvable ({_JPEXS_JAR}). Décompilez le SWF à la main "
            f"puis relancez avec --as <DoAction.as>, ou définissez JPEXS_JAR."
        )
    print(f"[extract_items] Décompilation {swf_path}")
    subprocess.run(
        ["java", "-jar", _JPEXS_JAR, "-export", "script", out_dir, swf_path],
        check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    )
    for root, _dirs, files in os.walk(out_dir):
        for f in files:
            if f.lower().endswith(".as"):
                return os.path.join(root, f)
    raise RuntimeError("Aucun .as produit par JPEXS")


def parse_as(src: str) -> dict:
    """Parser le source AS2 décompilé → dict {version, types, items}."""
    version_m = re.search(r'VERSION\s*=\s*(\d+)', src)
    version = int(version_m.group(1)) if version_m else 0

    types: dict[str, dict] = {}
    for m in _RE_TYPE.finditer(src):
        type_id = m.group(1)
        types[type_id] = {
            "name": m.group(2).replace("\\'", "'"),
            "category": int(m.group(3)),
        }

    items: dict[str, dict] = {}
    for m in _RE_ITEM.finditer(src):
        gid = m.group(1)
        body = m.group(2)
        lvl_m = _RE_LEVEL.search(body)
        type_m = _RE_TYPE_FIELD.search(body)
        name_m = _RE_NAME.search(body)
        items[gid] = {
            "l": int(lvl_m.group(1)) if lvl_m else 0,
            "t": int(type_m.group(1)) if type_m else 0,
            "n": (name_m.group(1).replace("\\'", "'") if name_m else ""),
        }

    return {"version": version, "types": types, "items": items}


def main() -> int:
    ap = argparse.ArgumentParser(description="Extraire la base d'items Abrak → ressources/items.json")
    ap.add_argument("--swf", help="Chemin d'un items_fr_*.swf déjà téléchargé")
    ap.add_argument("--as", dest="as_file", help="Chemin d'un DoAction.as déjà décompilé")
    ap.add_argument("--out", default=_OUT_PATH, help=f"Sortie JSON (défaut: {_OUT_PATH})")
    args = ap.parse_args()

    if args.as_file:
        src = open(args.as_file, encoding="utf-8", errors="replace").read()
    else:
        with tempfile.TemporaryDirectory() as tmp:
            swf = args.swf
            if not swf:
                version = _resolve_version()
                swf = os.path.join(tmp, f"items_fr_{version}.swf")
                _download_swf(version, swf)
            as_path = _decompile(swf, os.path.join(tmp, "dump"))
            src = open(as_path, encoding="utf-8", errors="replace").read()

    db = parse_as(src)
    n_items = len(db["items"])
    n_types = len(db["types"])
    no_type = sum(1 for v in db["items"].values() if not v["t"])
    no_level = sum(1 for v in db["items"].values() if not v["l"])
    print(f"[extract_items] version={db['version']} types={n_types} items={n_items} "
          f"(sans type={no_type}, sans niveau={no_level})")

    os.makedirs(os.path.dirname(args.out), exist_ok=True)
    with open(args.out, "w", encoding="utf-8") as f:
        json.dump(db, f, ensure_ascii=False, separators=(",", ":"))
    print(f"[extract_items] Écrit {args.out}")
    return 0


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    raise SystemExit(main())
