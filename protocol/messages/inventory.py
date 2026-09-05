"""
Dataclasses pour les messages d'inventaire Dofus Rétro 1.29.

Messages couverts :
  OT   — ItemsTool     (S→C) : contenu complet de l'inventaire (chargement map)
  OAK  — ItemsAddSuccess (S→C) : ajout d'un ou plusieurs objets
  OR   — ItemsRemove   (S→C) : suppression d'un objet (par UID)
  OQ   — ItemsQuantity (S→C) : changement de quantité

Format OT (serveur privé — défensif, loguer si inconnu) :
  OT|{obj1}|{obj2}|...
  Chaque obj : {gid}~{uid}~{qty}~{pos}~{effects}
  Note : sur serveur privé le format peut varier, les parsers sont défensifs.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field

logger = logging.getLogger(__name__)

# Compteur pour logger les payloads bruts les premières fois
_log_raw_count = 0
_LOG_RAW_MAX = 3


@dataclass
class InventoryItem:
    """Un objet de l'inventaire.

    Format d'un chunk OT : {gid}~{uid}~{qty}~{pos}~{effects}
    """

    obj_gid: int = 0      # ID du modèle d'objet
    obj_uid: str = ""     # UID unique de l'instance
    quantity: int = 1
    position: int = 0     # Slot (63 = sac, 0-15 = équipement…)
    effects: str = ""     # Effets bruts non parsés

    @classmethod
    def parse_obj(cls, chunk: str) -> "InventoryItem":
        """Parser un chunk d'objet séparé par '~'.

        Deux formats coexistent :
          - Serveur (OAK/EL) : O{uid_hex}~{gid_hex}~{qty}~{pos_hex_or_empty}~{effects}
            Le champ O-préfixé contient le uid en hexadécimal.
            Le pos vide signifie sac (63).
          - Ancien format (OT legacy) : {gid}~{uid}~{qty}~{pos}~{effects}
        """
        parts = chunk.split("~", 4)

        def _int(s: str, base: int = 10) -> int:
            s = s.strip()
            if not s:
                return 0
            try:
                return int(s, base)
            except ValueError:
                return 0

        obj = cls()

        if parts[0].startswith("O"):
            # Format serveur : O{uid_hex}~{gid_hex}~{qty}~{pos}~{effects}
            uid_hex = parts[0][1:].strip()
            obj.obj_uid = str(_int(uid_hex, 16)) if uid_hex else ""
            if len(parts) > 1:
                obj.obj_gid = _int(parts[1], 16)
            if len(parts) > 2:
                obj.quantity = _int(parts[2])
            if len(parts) > 3:
                pos_raw = parts[3].strip()
                obj.position = _int(pos_raw, 16) if pos_raw else 63
            if len(parts) > 4:
                obj.effects = parts[4].strip()
        else:
            # Format legacy : {gid}~{uid}~{qty}~{pos}~{effects}
            if len(parts) > 0:
                obj.obj_gid = _int(parts[0])
            if len(parts) > 1:
                obj.obj_uid = parts[1].strip()
            if len(parts) > 2:
                obj.quantity = _int(parts[2])
            if len(parts) > 3:
                obj.position = _int(parts[3])
            if len(parts) > 4:
                obj.effects = parts[4].strip()

        return obj


@dataclass
class Inventory:
    """Contenu complet de l'inventaire depuis le message OT."""

    items: list[InventoryItem] = field(default_factory=list)

    @classmethod
    def parse(cls, payload: str) -> "Inventory":
        """Parser le payload OT complet.

        Args:
            payload: Tout ce qui suit 'OT', ex '|{obj1}|{obj2}|...'
        """
        global _log_raw_count
        if _log_raw_count < _LOG_RAW_MAX:
            logger.info("[Inventory] OT payload brut : %r", payload[:200])
            _log_raw_count += 1

        items = []
        for chunk in payload.split("|"):
            chunk = chunk.strip()
            if not chunk or "~" not in chunk:
                continue
            try:
                items.append(InventoryItem.parse_obj(chunk))
            except Exception as exc:
                logger.debug("[Inventory] chunk ignoré %r : %s", chunk[:40], exc)
        return cls(items=items)


def parse_item_add(payload: str) -> list[InventoryItem]:
    """Parser le payload OAK (ajout d'objet(s)).

    Format serveur observé (retrait banque / loot multiple) :
        {charId}|{item1}*{item2}*{item3}...
    Les objets sont séparés par '*' (et NON '|' — le '|' ne sépare que le
    charId du premier objet). Un OAK simple peut aussi être un objet seul.
    """
    payload = payload.strip()
    # Retirer le préfixe "{charId}|" éventuel (charId = entier).
    if "|" in payload:
        head, rest = payload.split("|", 1)
        if head.strip().isdigit():
            payload = rest

    items = []
    # Objets séparés par '*'. On gère aussi '|' au cas où un ancien format
    # l'utiliserait (split combiné, sans réintroduire le charId déjà retiré).
    for chunk in payload.replace("|", "*").split("*"):
        chunk = chunk.strip()
        if not chunk or "~" not in chunk:
            continue
        try:
            items.append(InventoryItem.parse_obj(chunk))
        except Exception as exc:
            logger.debug("[Inventory] OAK chunk ignoré %r : %s", chunk[:40], exc)
    return items


def parse_item_remove(payload: str) -> str:
    """Parser le payload OR (suppression d'objet).

    Returns:
        obj_uid de l'objet à supprimer.
    """
    return payload.strip()


def parse_item_quantity(payload: str) -> list[tuple[str, int]]:
    """Parser le payload OQ (changement de quantité).

    Formats observés :
      - '{charId}|{uid},{qty}*{uid},{qty}*...'  (multi-item serveur)
      - '{charId}|{uid},{qty}'                  (single item serveur)
      - '{uid}|{qty}'                           (format legacy)
      - '{uid}~{qty}'                           (format legacy)

    Returns:
        Liste de tuples (obj_uid, new_quantity).
    """
    result: list[tuple[str, int]] = []
    payload = payload.strip()
    if "|" in payload:
        left, right = payload.split("|", 1)
        right = right.strip()
        if "," in right:
            # Format '{charId}|{uid},{qty}*{uid},{qty}*...'
            for pair in right.split("*"):
                pair = pair.strip()
                if "," not in pair:
                    continue
                uid, qty_str = pair.split(",", 1)
                qty_str = qty_str.strip()
                qty = int(qty_str) if qty_str.isdigit() else 0
                uid = uid.strip()
                if uid:
                    result.append((uid, qty))
        else:
            # Format '{uid}|{qty}'
            qty = int(right) if right.isdigit() else 0
            uid = left.strip()
            if uid:
                result.append((uid, qty))
    elif "~" in payload:
        uid, qty_str = payload.split("~", 1)
        qty_str = qty_str.strip()
        qty = int(qty_str) if qty_str.isdigit() else 0
        uid = uid.strip()
        if uid:
            result.append((uid, qty))
    return result
