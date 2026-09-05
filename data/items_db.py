"""
Base de données d'items Dofus Rétro (serveur Abrak).

Charge ``ressources/items.json`` (généré par ``tools/extract_items.py``) et
expose des requêtes simples : niveau, type, nom, et surtout *est-ce un
équipement ?* — utilisé par la vente au marchand pour ne vendre QUE les
équipements (bottes, ceintures, anneaux…) et filtrer par niveau.

Structure du JSON :
    {
      "version": 1564,
      "types": { "<typeId>": {"name": "Botte", "category": <int>} },
      "items": { "<gid>":    {"l": <level>, "t": <typeId>, "n": "<name>"} }
    }

Le champ ``category`` (= ``I.t[].t`` du client) regroupe les types par grande
famille. Les familles d'ÉQUIPEMENT (porté sur le personnage) :

    1  = Amulette
    2  = Armes (Arc, Épée, Dague, Bâton, Marteau, Hache, Pelle, Pioche, Faux,
              Arbalète, Baguette, Outil…)
    3  = Anneau
    4  = Ceinture
    5  = Botte
    7  = Bouclier
    10 = Chapeau (coiffe)
    11 = Cape / Sac à dos

Familles EXCLUES (non-équipement) : 6 (consommables/parchemins), 8 (pierres
d'âme), 9 (ressources), 13 (Dofus/Trophée — précieux, exclu par sécurité),
23/25 (runes), 14-22/24 (quêtes, montures, nourriture, mutations…).
"""

from __future__ import annotations

import json
import logging
import os

logger = logging.getLogger(__name__)

from core.paths import RESSOURCES_DIR

_DB_PATH = str(RESSOURCES_DIR / "items.json")

# Familles d'équipement vendables par défaut (cf. docstring).
EQUIPMENT_CATEGORIES: frozenset[int] = frozenset({1, 2, 3, 4, 5, 7, 10, 11})

# Noms lisibles des familles d'équipement (pour l'UI).
EQUIPMENT_CATEGORY_NAMES: dict[int, str] = {
    1:  "Amulette",
    2:  "Armes",
    3:  "Anneau",
    4:  "Ceinture",
    5:  "Bottes",
    7:  "Bouclier",
    10: "Coiffe",
    11: "Cape / Sac à dos",
}

_db: dict | None = None


def _load() -> dict:
    global _db
    if _db is None:
        try:
            with open(_DB_PATH, "r", encoding="utf-8") as f:
                _db = json.load(f)
            logger.info("[items_db] %d items, %d types (version %s)",
                        len(_db.get("items", {})), len(_db.get("types", {})),
                        _db.get("version"))
        except FileNotFoundError:
            logger.warning("[items_db] %s introuvable — lancez tools/extract_items.py", _DB_PATH)
            _db = {"version": 0, "types": {}, "items": {}}
        except Exception as exc:
            logger.error("[items_db] Erreur de chargement : %s", exc)
            _db = {"version": 0, "types": {}, "items": {}}
    return _db


def is_loaded() -> bool:
    """True si la base contient au moins un item."""
    return bool(_load().get("items"))


def get_item(gid: int | str) -> dict | None:
    """Retourner {'l': level, 't': typeId, 'n': name} pour un gid, ou None."""
    if gid is None:
        return None
    return _load().get("items", {}).get(str(gid))


def get_level(gid: int | str) -> int:
    item = get_item(gid)
    return int(item["l"]) if item else 0


def get_type_id(gid: int | str) -> int:
    item = get_item(gid)
    return int(item["t"]) if item else 0


def get_name(gid: int | str) -> str:
    item = get_item(gid)
    return item["n"] if item else ""


def get_type_info(type_id: int | str) -> dict | None:
    """{'name': ..., 'category': ...} pour un typeId, ou None."""
    return _load().get("types", {}).get(str(type_id))


def get_type_name(gid: int | str) -> str:
    info = get_type_info(get_type_id(gid))
    return info["name"] if info else ""


def get_category(gid: int | str) -> int:
    """Grande famille (I.t category) de l'item, 0 si inconnu."""
    info = get_type_info(get_type_id(gid))
    return int(info["category"]) if info else 0


# Classification large pour l'onglet Inventaire (4 familles).
_DISPLAY_EQUIPMENT = frozenset({1, 2, 3, 4, 5, 7, 10, 11, 13})  # + Dofus/Trophée
_DISPLAY_CONSUMABLE = frozenset({6, 16, 17, 18, 19})            # parchemins, potions, food…
_DISPLAY_RESOURCE = frozenset({9})                              # ressources de récolte/craft


def classify(gid: int | str) -> str:
    """Classer un item pour l'onglet Inventaire : 'equipement', 'consommable',
    'ressource' ou 'autre'."""
    cat = get_category(gid)
    if cat in _DISPLAY_EQUIPMENT:
        return "equipement"
    if cat in _DISPLAY_CONSUMABLE:
        return "consommable"
    if cat in _DISPLAY_RESOURCE:
        return "ressource"
    return "autre"


def is_equipment(gid: int | str, categories: frozenset[int] | set[int] | None = None) -> bool:
    """True si l'item appartient à une famille d'équipement.

    Args:
        gid: identifiant du modèle d'objet (obj_gid de l'inventaire).
        categories: familles autorisées (défaut : EQUIPMENT_CATEGORIES).
    """
    cats = EQUIPMENT_CATEGORIES if categories is None else categories
    return get_category(gid) in cats


def equipment_types(categories: frozenset[int] | set[int] | None = None) -> list[dict]:
    """Catalogue des types d'équipement pour l'UI.

    Returns une liste triée de {'type_id', 'name', 'category'} pour tous les
    types dont la famille est dans *categories*.
    """
    cats = EQUIPMENT_CATEGORIES if categories is None else categories
    out = []
    for tid, info in _load().get("types", {}).items():
        if info.get("category") in cats:
            out.append({
                "type_id": int(tid),
                "name": info.get("name", f"#{tid}"),
                "category": int(info.get("category", 0)),
            })
    out.sort(key=lambda d: (d["category"], d["name"]))
    return out
