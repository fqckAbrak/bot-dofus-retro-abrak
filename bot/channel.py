"""
Canal d'injection de messages C→S vers le serveur Dofus.

MULTI-INSTANCE
──────────────
Chaque connexion possède son propre `ChannelState` (writer + clés de
chiffrement) porté par sa `Session` (cf. core/session.py). Les fonctions de ce
module opèrent sur la session **active** (résolue via le contextvar) ; les
call-sites (`channel.send(...)`, `channel.is_connected()`, …) restent inchangés
tant qu'ils tournent dans le contexte d'une connexion.

Le relay (proxy/relay.py) accède aux clés via `session.channel` **explicitement**
(hot-path crypto : pas de dépendance au contextvar).

Usage :
    # Dans proxy/server.py, après open_server_connection (contexte session posé) :
    bot.channel.set_writer(server_writer)
    # Dans le finally :
    bot.channel.set_writer(None)

    # Depuis une coroutine bot :
    await bot.channel.send("GA001ab\n")
"""

from __future__ import annotations

import logging
from asyncio import StreamWriter

from protocol.encoding import prepare_data
from core.session import active, active_or_none, ChannelState

logger = logging.getLogger(__name__)


def _chan() -> ChannelState:
    """ChannelState de la session active (lève hors contexte de connexion)."""
    return active().channel


def set_encryption_keys(a_keys: list[str | None], current_key: int) -> None:
    """Mettre à jour les clés de chiffrement (appelé par le handler AK/AYK)."""
    ch = _chan()
    ch.a_keys = a_keys[:]
    ch.current_key = current_key
    logger.info("[channel] Clés de chiffrement mises à jour (key_idx=%d, %d clés)",
                current_key, sum(1 for k in a_keys if k))


def reset_encryption_keys() -> None:
    """Réinitialiser les clés (appelé à la déconnexion)."""
    ch = _chan()
    ch.a_keys = [None] * 16
    ch.current_key = 0
    logger.debug("[channel] Clés de chiffrement réinitialisées")


def get_a_keys() -> list[str | None]:
    """Retourner une copie des clés de chiffrement de la session active."""
    return list(_chan().a_keys)


def set_writer(w: StreamWriter | None) -> None:
    """Enregistrer (ou effacer) le writer actif vers le serveur de la session.

    Appelé par proxy/server.py à chaque connexion/déconnexion. Le writer est
    stocké mais marqué "non-jeu" jusqu'à confirmation via mark_game_connection().
    """
    ch = _chan()
    ch.writer = w
    ch.is_game_connection = False  # reset : on ne sait pas encore si c'est un game server
    if w is not None:
        logger.info("[channel] Writer serveur enregistré (en attente de HG)")
    else:
        logger.info("[channel] Writer serveur effacé")
        try:
            from dashboard import bridge as _bridge
            _bridge.notify("Déconnexion du serveur", level="error")
        except Exception:
            pass
        # Notifier les bots actifs de la déconnexion
        try:
            from bot.harvester import on_disconnect
            on_disconnect()
        except Exception:
            pass
        try:
            from bot.script_engine import on_disconnect as on_disconnect_script
            on_disconnect_script()
        except Exception:
            pass


def mark_game_connection() -> None:
    """Marquer la connexion courante comme un game server (après réception de HG)."""
    _chan().is_game_connection = True
    logger.info("[channel] Connexion game server confirmée (HG reçu)")


def is_connected() -> bool:
    """Retourner True si connecté à un game server actif."""
    s = active_or_none()
    if s is None:
        return False
    ch = s.channel
    return ch.writer is not None and not ch.writer.is_closing() and ch.is_game_connection


async def send(msg: str) -> bool:
    """Envoyer un message au serveur (C→S), avec chiffrement si nécessaire.

    Les messages GA*, GK*, GM* sont chiffrés automatiquement avec les clés
    reçues dans le message AK (comme le ferait le client Flash).

    Args:
        msg: Message en clair sans le \\x00 terminal (ex: "GA001ab\\n").

    Returns:
        True si le message a été envoyé, False sinon.
    """
    ch = _chan()
    writer = ch.writer
    if writer is None or writer.is_closing():
        logger.warning("[channel] Tentative d'envoi sans connexion active : %r", msg[:60])
        return False
    try:
        # Chiffrer si les clés sont disponibles
        plain_msg = msg.rstrip("\n")
        if ch.current_key > 0 and any(ch.a_keys):
            encrypted, ch.current_key = prepare_data(plain_msg, ch.a_keys, ch.current_key)
            wire_msg = encrypted + "\n"
        else:
            wire_msg = msg if msg.endswith("\n") else msg + "\n"

        writer.write(wire_msg.encode("latin-1") + b"\x00")
        await writer.drain()
        if wire_msg != msg:
            logger.debug("[channel] → (chiffré) clé#%d plain=%r", ch.current_key, plain_msg[:60])
        else:
            logger.debug("[channel] → %r", wire_msg[:80])
        return True
    except (ConnectionResetError, BrokenPipeError) as exc:
        logger.warning("[channel] Connexion perdue lors de l'envoi : %s", exc)
        return False
    except Exception as exc:
        logger.error("[channel] Erreur inattendue lors de l'envoi : %s", exc)
        return False


async def inject_to_client(msg: str) -> bool:
    """Injecter un message S→C vers le client Flash (comme si le serveur l'envoyait).

    Sert à déclencher le dump d'inventaire du patch core.swf : le bot envoie
    ``ZI`` au client, le client patché répond ``ZO{inventaire}`` (capté et droppé
    par le relais). Les messages S→C ne sont pas chiffrés sur ce serveur, donc on
    écrit en clair + délimiteur ``\\x00``.
    """
    ch = _chan()
    w = ch.client_writer
    if w is None or w.is_closing():
        logger.warning("[channel] inject_to_client : pas de client connecté : %r", msg[:40])
        return False
    try:
        # Les messages S→C du serveur sont délimités par \x00 sans \n ajouté :
        # on mime ce format (on retire un \n terminal éventuel).
        wire = msg.rstrip("\n")
        w.write(wire.encode("latin-1") + b"\x00")
        await w.drain()
        logger.debug("[channel] → (client) %r", wire[:60])
        return True
    except (ConnectionResetError, BrokenPipeError) as exc:
        logger.warning("[channel] inject_to_client : connexion perdue : %s", exc)
        return False
    except Exception as exc:
        logger.error("[channel] inject_to_client : erreur : %s", exc)
        return False


async def send_plain(msg: str) -> bool:
    """Envoyer un message en clair (sans chiffrement) — pour ApS et EMO banque.

    Certains messages comme ApS et EMO sont envoyés en plaintext par le client
    Flash même quand le chiffrement est actif. Cette méthode court-circuite
    le chiffrement.

    Args:
        msg: Message en clair (ex: "ApS\\n" ou "EMO+uid|qty\\n").

    Returns:
        True si envoyé, False sinon.
    """
    ch = _chan()
    writer = ch.writer
    if writer is None or writer.is_closing():
        logger.warning("[channel] send_plain : pas de connexion active : %r", msg[:60])
        return False
    try:
        wire_msg = msg if msg.endswith("\n") else msg + "\n"
        writer.write(wire_msg.encode("latin-1") + b"\x00")
        await writer.drain()
        logger.debug("[channel] → (plain) %r", wire_msg[:80])
        return True
    except (ConnectionResetError, BrokenPipeError) as exc:
        logger.warning("[channel] send_plain : connexion perdue : %s", exc)
        return False
    except Exception as exc:
        logger.error("[channel] send_plain : erreur inattendue : %s", exc)
        return False
