"""
Dépôt automatique en banque.

Flux :
  1. Envoyer ApS  (ouvrir interface banque)
  2. Attendre ECK 5  (banque prête)
  3. Construire le paquet EMO à partir des ressources en sac (pos=63)
  4. Envoyer EMO  (déposer)
  5. Attendre Ow  (poids mis à jour = dépôt confirmé)
  6. Envoyer EV   (fermer banque)
"""

from __future__ import annotations

import asyncio
import logging

from bot import channel as _channel
from game.state import (
    current,
    get_inventory_resources,
    is_overweight,
    wait_bank_open,
    wait_weight_update,
)

logger = logging.getLogger(__name__)


BANK_PRE_OPEN_DELAY: float = 2.0
"""Délai (secondes) avant d'ouvrir la banque (laisse le serveur envoyer les OAK de fin de combat)."""


async def bank_deposit_resources(
    timeout_open: float = 10.0,
    timeout_deposit: float = 15.0,
) -> bool:
    """Ouvrir la banque, déposer toutes les ressources (pos=63), fermer.

    Returns True si le dépôt s'est bien terminé, False en cas d'échec.
    """
    resources = get_inventory_resources()
    if not resources:
        logger.info("[bank] Aucune ressource à déposer (inventaire vide ou déjà propre)")
        return True

    logger.info("[bank] %d ressource(s) à déposer — attente %.1fs puis ouverture banque",
                len(resources), BANK_PRE_OPEN_DELAY)
    await asyncio.sleep(BANK_PRE_OPEN_DELAY)

    # 1. Ouvrir la banque (bouton interface — plaintext)
    await _channel.send_plain("ApS\n")

    # 2. Attendre ECK 5
    if not await wait_bank_open(timeout_open):
        logger.warning("[bank] Timeout : banque non ouverte après %.1fs", timeout_open)
        return False

    # 3. Construire EMO+{uid}|{qty}+{uid}|{qty}...
    parts = "+".join(f"{item['uid']}|{item['qty']}" for item in resources)
    emo_packet = f"EMO+{parts}\n"
    logger.info("[bank] EMO : %d objet(s) → %s", len(resources), emo_packet[:80])

    # 4. Envoyer le dépôt (plaintext — même format que le client Flash)
    await _channel.send_plain(emo_packet)

    # 5. Attendre Ow (confirmation poids)
    if not await wait_weight_update(timeout_deposit):
        logger.warning("[bank] Timeout : Ow non reçu après dépôt (%.1fs)", timeout_deposit)
        # On ferme quand même la banque
    else:
        logger.info(
            "[bank] Dépôt confirmé : %d/%d pods",
            current.weight_current, current.weight_max,
        )

    # 6. Fermer la banque (encrypted — même que le client Flash)
    await _channel.send("EV\n")
    logger.info("[bank] Banque fermée")
    return True
