"""
Parsing du fichier Recolte.txt — mapping type_id → nom de ressource.

Format du fichier (pipe-séparé, une ressource par ligne) :
    graphic_id|nom|action|type_id[:type_id...]

Un préfixe « index<TAB> » en début de ligne est toléré (ancien format).
"""

from __future__ import annotations

from core.paths import RESSOURCES_DIR

_RECOLTE_PATH = RESSOURCES_DIR / "Recolte.txt"

# {type_id: nom}  — ex. {6: "Frene", 39: "Chataignier"}
_TYPE_NAMES: dict[int, str] = {}


def _load() -> None:
    if not _RECOLTE_PATH.exists():
        return
    with _RECOLTE_PATH.open("r", encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            # Format : "7500|Frene|Couper|6" (un préfixe "1\t" est toléré)
            fields = line.rsplit("\t", 1)[-1].split("|")
            if len(fields) < 4:
                continue
            # Peut y avoir plusieurs type_id séparés par ":" dans fields[3]
            # Ex. "7513|Lin|Faucher:Cueillir|50:68"
            name = fields[1].replace("_", " ")
            for tid in fields[3].split(":"):
                try:
                    _TYPE_NAMES[int(tid)] = name
                except ValueError:
                    pass


_load()


def get_name(type_id: int | str, default: str | None = None) -> str:
    """Retourne le nom de la ressource pour un type_id donné.

    Si inconnu, retourne *default* (ou la valeur brute stringifiée).
    """
    try:
        tid = int(type_id)
    except (TypeError, ValueError):
        return str(type_id) if default is None else default
    if default is None:
        default = str(type_id)
    return _TYPE_NAMES.get(tid, default)
