"""
Vente automatique d'équipements à un marchand ambulant (PNJ).

Flux réseau (reproduit le client Flash, cf. logs/session_20260623_184108.log) :

    C→S  ER2{npcId}          ouvrir l'échange avec le PNJ (id négatif, ex -3)
    S→C  ECK2|{npcId}        échange ouvert
    C→S  EMO+{uid}|{qty}+…   déposer les objets à vendre (plaintext, comme banque)
    S→C  EM KO+{uid}|{qty}   confirmation par objet
    S→C  Em KG{kamas}        total kamas offert (cumulatif)
    C→S  EK                  (vide) valider la vente  (chiffré)
    S→C  OR… / As… / EV a    objets retirés, kamas crédités, échange fermé

Filtrage (config) :
  * uniquement les ÉQUIPEMENTS (familles items_db.EQUIPMENT_CATEGORIES) ;
  * niveau ≤ max_level ;
  * gid/uid hors blacklist.

Sécurité : on ne vend QUE les objets EN SAC (pos=63), jamais l'équipement porté.
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field

from bot import channel as _channel
from data import items_db
from game.state import (
    current,
    get_inventory_bag,
    find_merchant_npc,
    wait_exchange_open,
    wait_exchange_closed,
)

logger = logging.getLogger(__name__)


# Type d'échange "achat PNJ" (le PNJ rachète nos objets) — observé dans les logs.
EXCHANGE_TYPE_NPC = 2

# Nombre d'objets par paquet EMO (le client en groupe une dizaine ; on borne
# pour éviter un paquet trop long sur de gros inventaires).
EMO_BATCH_SIZE = 40


@dataclass
class SellConfig:
    """Réglages de la vente au marchand."""
    max_level: int = 120
    blacklist_gids: set[int] = field(default_factory=set)
    blacklist_uids: set[str] = field(default_factory=set)
    # Noms d'items à ne jamais vendre (comparaison exacte, insensible à la casse).
    blacklist_names: set[str] = field(default_factory=set)
    # Familles d'items considérées comme « équipement » (défaut : items_db).
    categories: set[int] = field(default_factory=lambda: set(items_db.EQUIPMENT_CATEGORIES))
    # Apparences (gfx) acceptées pour identifier le marchand. Vide = 1er PNJ trouvé.
    merchant_gfx: list[str] = field(default_factory=list)


@dataclass
class SellableItem:
    uid: str
    gid: int
    qty: int
    name: str
    level: int
    type_name: str


def preview_sellable(cfg: SellConfig, bag: list[dict] | None = None) -> list[SellableItem]:
    """Lister les objets du sac qui seraient vendus avec cette config.

    Ne touche pas au réseau — sert à l'aperçu UI et au filtrage de la vente.

    Args:
        cfg: réglages de filtrage.
        bag: liste d'objets d'inventaire à filtrer. Si None, lit le sac de la
            session active (``get_inventory_bag``). L'UI (thread tkinter, sans
            session active) doit passer ``session.game_state._inventory.values()``.
    """
    if bag is None:
        bag = get_inventory_bag()
    out: list[SellableItem] = []
    for item in bag:
        # "En sac" (non équipé) : 63 côté serveur (OAK/EL), -1 côté client (dump ZO).
        # Les positions >= 0 (hors 63) sont des emplacements d'équipement portés.
        if item.get("pos") not in (-1, 63):
            continue
        uid = str(item.get("uid", ""))
        gid = item.get("gid")
        if gid is None:
            continue
        try:
            gid = int(gid)
        except (ValueError, TypeError):
            continue

        if gid in cfg.blacklist_gids or uid in cfg.blacklist_uids:
            continue
        if not items_db.is_equipment(gid, cfg.categories):
            continue
        level = items_db.get_level(gid)
        if cfg.max_level and level > cfg.max_level:
            continue
        name = items_db.get_name(gid) or f"#{gid}"
        if name.strip().lower() in cfg.blacklist_names:
            continue

        out.append(SellableItem(
            uid=uid,
            gid=gid,
            qty=int(item.get("qty", 1)),
            name=name,
            level=level,
            type_name=items_db.get_type_name(gid),
        ))
    return out


async def sell_equipment_to_merchant(
    cfg: SellConfig,
    *,
    timeout_open: float = 10.0,
    timeout_close: float = 15.0,
    settle_delay: float = 0.8,
    log=None,
) -> dict:
    """Vendre tous les équipements éligibles au marchand présent sur la carte.

    Args:
        cfg: réglages de filtrage.
        log: callback optionnel ``log(str)`` pour le retour live dans l'UI.

    Returns:
        dict de résultat : {ok, reason, sold, kamas, items}.
    """
    def _emit(message: str) -> None:
        logger.info("[merchant] %s", message)
        if log:
            try:
                log(message)
            except Exception:
                pass

    if not _channel.is_connected():
        return {"ok": False, "reason": "Pas connecté au serveur", "sold": 0, "kamas": 0}

    if not items_db.is_loaded():
        return {"ok": False, "reason": "Base d'items absente (lancer tools/extract_items.py)",
                "sold": 0, "kamas": 0}

    # 0. Tenter de récupérer l'inventaire complet via le patch core.swf (si présent).
    #    Sinon on garde l'inventaire incrémental (loot OAK + banque).
    try:
        from bot.inventory import request_full_inventory
        if await request_full_inventory(timeout=4.0):
            _emit("Inventaire complet rafraîchi (patch core.swf)")
    except Exception:
        pass

    # 1. Vérifier la présence du marchand sur la carte (= bonne map).
    merchant = find_merchant_npc(cfg.merchant_gfx or None)
    if merchant is None:
        if cfg.merchant_gfx:
            reason = ("Marchand introuvable sur cette carte "
                      f"(gfx attendu : {', '.join(cfg.merchant_gfx)})")
        else:
            reason = "Aucun PNJ marchand sur cette carte"
        _emit(reason)
        return {"ok": False, "reason": reason, "sold": 0, "kamas": 0}

    npc_id = merchant.entity_id
    _emit(f"Marchand détecté (id {npc_id}, gfx {merchant.gfx_id}, cellule {merchant.cell_id})")

    # 2. Construire la liste à vendre.
    sellable = preview_sellable(cfg)
    if not sellable:
        _emit("Aucun équipement éligible à vendre (filtre type/niveau/blacklist)")
        return {"ok": True, "reason": "Rien à vendre", "sold": 0, "kamas": 0, "items": []}

    _emit(f"{len(sellable)} équipement(s) à vendre (niveau ≤ {cfg.max_level})")

    # 3. Ouvrir l'échange avec le PNJ.  Format : ER{type}|{npcId}  (ex "ER2|-3")
    await _channel.send(f"ER{EXCHANGE_TYPE_NPC}|{npc_id}\n")
    if not await wait_exchange_open(timeout_open):
        reason = "Le marchand n'a pas ouvert l'échange (timeout)"
        _emit(reason)
        return {"ok": False, "reason": reason, "sold": 0, "kamas": 0}

    # 4. Déposer les objets par lots (EMO plaintext, comme la banque).
    for start in range(0, len(sellable), EMO_BATCH_SIZE):
        batch = sellable[start:start + EMO_BATCH_SIZE]
        parts = "+".join(f"{it.uid}|{it.qty}" for it in batch)
        await _channel.send_plain(f"EMO+{parts}\n")
        await asyncio.sleep(0.2)

    # Attendre que le PNJ ait chiffré les items (Em KG → kamas > 0), comme on
    # attend que « le prix apparaisse » en jeu avant de valider. Poll borné.
    await asyncio.sleep(settle_delay)
    waited = 0.0
    while current._exchange_kamas <= 0 and waited < 5.0:
        await asyncio.sleep(0.2)
        waited += 0.2
    offered = current._exchange_kamas
    if offered:
        _emit(f"Offre du marchand : {offered} kamas")
    else:
        _emit("Avertissement : le marchand n'a pas affiché de prix (Em KG) — validation quand même")

    # 5. Valider la vente (EK vide, chiffré).
    await _channel.send("EK\n")

    # 6. Attendre la fermeture (EV) = vente finalisée.
    if not await wait_exchange_closed(timeout_close):
        _emit("Avertissement : fermeture d'échange (EV) non reçue — vente peut-être incomplète")

    kamas = current._exchange_kamas or offered
    _emit(f"Vente terminée : {len(sellable)} objet(s), +{kamas} kamas")
    return {
        "ok": True,
        "reason": "Vente effectuée",
        "sold": len(sellable),
        "kamas": kamas,
        "items": [it.name for it in sellable],
    }
