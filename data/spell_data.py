"""
Chargement et accès aux données de sorts depuis spells.xml.

Fournit un cache en mémoire des informations statiques de chaque sort
(nom, coût PA, portée, LdV, etc.) indexé par (spell_id, spell_level).
"""

from __future__ import annotations

import logging
import os
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field

logger = logging.getLogger(__name__)

from core.paths import RESSOURCES_DIR

_SPELLS_XML = str(RESSOURCES_DIR / "spells.xml")


@dataclass(frozen=True)
class SpellLevelInfo:
    """Données statiques d'un niveau de sort."""
    spell_id: int
    name: str
    level: int
    cost_pa: int
    range_min: int
    range_max: int
    launch_inline: bool
    vision_line: bool
    empty_cell: bool
    modifiable_distance: bool
    launch_per_turn: int
    launch_per_target: int
    cooldown: int


@dataclass
class SpellInfo:
    """Données statiques d'un sort (toutes les niveaux)."""
    spell_id: int
    name: str
    levels: dict[int, SpellLevelInfo] = field(default_factory=dict)


# Cache global : spell_id → SpellInfo
_spell_cache: dict[int, SpellInfo] = {}
_loaded: bool = False


def _load_spells_xml() -> None:
    """Parser spells.xml et remplir le cache."""
    global _loaded
    if _loaded:
        return

    xml_path = os.path.normpath(_SPELLS_XML)
    if not os.path.isfile(xml_path):
        logger.warning("[spell_data] spells.xml introuvable : %s", xml_path)
        _loaded = True
        return

    try:
        tree = ET.parse(xml_path)
        root = tree.getroot()

        for spell_elem in root.findall("SPELL"):
            spell_id = int(spell_elem.get("ID", "0"))
            name_elem = spell_elem.find("NAME")
            name = name_elem.text.strip() if name_elem is not None and name_elem.text else f"Sort #{spell_id}"

            info = SpellInfo(spell_id=spell_id, name=name)

            for level_elem in spell_elem.findall("LEVEL"):
                lvl = int(level_elem.get("LEVEL", "1"))
                level_info = SpellLevelInfo(
                    spell_id=spell_id,
                    name=name,
                    level=lvl,
                    cost_pa=int(level_elem.get("COST_PA", "0")),
                    range_min=int(level_elem.get("RANGE_MIN", "0")),
                    range_max=int(level_elem.get("RANGE_MAX", "0")),
                    launch_inline=level_elem.get("LAUNCH_INLINE", "FALSE") == "TRUE",
                    vision_line=level_elem.get("VISION_LINE", "FALSE") == "TRUE",
                    empty_cell=level_elem.get("EMPTY_CELL", "FALSE") == "TRUE",
                    modifiable_distance=level_elem.get("MODIFIABLE_DISTANCE", "FALSE") == "TRUE",
                    launch_per_turn=int(level_elem.get("LAUNCH_PER_TURN", "0")),
                    launch_per_target=int(level_elem.get("LAUNCH_PER_TARGET", "0")),
                    cooldown=int(level_elem.get("COOLDOWN", "0")),
                )
                info.levels[lvl] = level_info

            _spell_cache[spell_id] = info

        logger.info("[spell_data] %d sorts chargés depuis spells.xml", len(_spell_cache))
    except Exception as exc:
        logger.error("[spell_data] Erreur de parsing spells.xml : %s", exc)

    _loaded = True


def get_spell_info(spell_id: int) -> SpellInfo | None:
    """Retourner les données d'un sort par son ID."""
    _load_spells_xml()
    return _spell_cache.get(spell_id)


def get_spell_name(spell_id: int) -> str:
    """Retourner le nom d'un sort (ou 'Sort #{id}' si inconnu)."""
    info = get_spell_info(spell_id)
    return info.name if info else f"Sort #{spell_id}"


def get_spell_level_info(spell_id: int, level: int) -> SpellLevelInfo | None:
    """Retourner les données d'un sort à un niveau donné."""
    info = get_spell_info(spell_id)
    if info is None:
        return None
    return info.levels.get(level)


def get_all_spells() -> dict[int, SpellInfo]:
    """Retourner tout le cache de sorts."""
    _load_spells_xml()
    return dict(_spell_cache)
