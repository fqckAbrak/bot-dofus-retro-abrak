"""
Persistance des réglages de la vente au marchand (merchant_config.json).

Réglages globaux (partagés par toutes les teams) : niveau max, blacklist,
familles d'équipement autorisées, apparence(s) du marchand.
"""

from __future__ import annotations

import json
import logging
import os

logger = logging.getLogger(__name__)

from core.paths import BOT_DIR

_PATH = str(BOT_DIR / "merchant_config.json")

# Familles d'équipement vendues par défaut (cf. data/items_db.EQUIPMENT_CATEGORIES).
_DEFAULT = {
    "max_level": 120,
    "blacklist_names": [],          # noms exacts à ne jamais vendre
    "blacklist_gids": [],           # gids à ne jamais vendre
    "categories": [1, 2, 3, 4, 5, 7, 10, 11],
    # gfx du marchand ambulant (PNJ acheteur). 2150 = marchand observé en jeu.
    # Vide = 1er PNJ trouvé (peu fiable s'il y a plusieurs PNJ).
    "merchant_gfx": ["2150"],
}


def default() -> dict:
    return json.loads(json.dumps(_DEFAULT))


def load() -> dict:
    cfg = default()
    try:
        with open(_PATH, "r", encoding="utf-8") as f:
            data = json.load(f)
        if isinstance(data, dict):
            cfg.update(data)
    except FileNotFoundError:
        pass
    except Exception as exc:
        logger.warning("[merchant_config] Lecture impossible : %s", exc)
    return cfg


def save(cfg: dict) -> None:
    try:
        with open(_PATH, "w", encoding="utf-8") as f:
            json.dump(cfg, f, ensure_ascii=False, indent=2)
    except Exception as exc:
        logger.error("[merchant_config] Écriture impossible : %s", exc)
