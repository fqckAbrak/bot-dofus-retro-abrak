"""
Récupération de l'inventaire complet via le patch core.swf.

Sur ce serveur, le serveur ne pousse jamais l'inventaire complet (OT) : le
client Flash le garde en mémoire (datacenter). Un patch de ``core.swf`` ajoute
un parser pour le message ``ZI`` (injecté par le bot vers le client) qui répond
``ZO{uid~gid~qty~pos;...}`` (en clair). Le relais capte ce ``ZO``, le parse
(``game.state._on_full_inventory``) et le DROP (ne le transmet pas au serveur).

Sans le patch appliqué, ``request_full_inventory`` timeout simplement : le bot
retombe sur l'inventaire incrémental (loot OAK + banque EL).
"""

from __future__ import annotations

import logging

from bot import channel as _channel
from game.state import current, wait_full_inventory

logger = logging.getLogger(__name__)

# Déclencheur injecté vers le client. Le dispatch du client ne route vers les
# addAdditionalPacketParser QUE les messages préfixés "#" (cf. processAdditionalPacket
# appelé uniquement si sData.charAt(0)=="#"). Le parser patché écoute "ZI", donc on
# injecte "#ZI" (le "#" est retiré avant le matching → "ZI").
INVENTORY_REQUEST = "#ZI"


async def request_full_inventory(timeout: float = 5.0) -> bool:
    """Demander au client patché de dumper son inventaire complet.

    Returns True si l'inventaire a été reçu (``current._inventory`` à jour),
    False si timeout (patch absent ou pas de client).
    """
    if not await _channel.inject_to_client(INVENTORY_REQUEST + "\n"):
        return False
    ok = await wait_full_inventory(timeout)
    if ok:
        logger.info("[inventory] Dump complet reçu : %d objets", len(current._inventory))
    else:
        logger.info("[inventory] Pas de dump (patch core.swf absent ?) — inventaire incrémental conservé")
    return ok
