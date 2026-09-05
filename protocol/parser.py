"""
Parser de messages Dofus Rétro 1.29.

Stratégie d'extraction du msgId :
  Le protocole n'a aucun séparateur entre l'ID et le payload.
  On teste les préfixes dans l'ordre longueur décroissante (longest match)
  en s'appuyant sur les tables de constants.py.
  Si aucun préfixe connu ne correspond, on renvoie les 2 premiers chars
  comme fallback (la majorité des IDs font 2 chars).

Cas spéciaux gérés (cf. retroproto/msgcli.go) :
  - AccountVersion : regex ^\\d+\\.\\d+\\.\\d+
  - AccountCredential : regex ^[\\w-@]+\\n#\\d
  - GI cyrillique -> GameGetExtraInformations
"""

from __future__ import annotations

import logging
import re
import time
from dataclasses import dataclass
from typing import Callable, Coroutine, Any

from protocol.constants import (
    SERVER_MESSAGES,
    CLIENT_MESSAGES,
    _SERVER_IDS_SORTED,
    _CLIENT_IDS_SORTED,
)
from protocol.encoding import parse_ayk

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Regex pour les IDs spéciaux côté client
# ---------------------------------------------------------------------------
_VERSION_RX = re.compile(r"^\d+\.\d+\.\d+(\.\d+)?s?e?$")
_CREDENTIAL_RX = re.compile(r"^[\w\d\-@]{1,20}\n#\d")

# ---------------------------------------------------------------------------
# Structure d'un message parsé
# ---------------------------------------------------------------------------


@dataclass
class ParsedMessage:
    """Représentation d'un message Dofus Rétro parsé."""

    direction: str       # "C→S" ou "S→C"
    msg_id: str          # ID extrait (ex: "HC", "GA", "AYK")
    msg_name: str        # Nom lisible (ex: "AksHelloConnect") ou "Unknown"
    payload: str         # Payload brut (tout ce qui suit l'ID)
    raw: str             # Message complet avant découpage
    timestamp: float     # time.time()


# ---------------------------------------------------------------------------
# Extraction de l'ID de message
# ---------------------------------------------------------------------------


def extract_msg_id(msg: str, is_server: bool) -> tuple[str, str]:
    """Extraire l'ID de message et son nom depuis un message brut.

    Teste les préfixes par longueur décroissante (longest match).
    Fallback sur 2 chars si rien n'est trouvé.

    Args:
        msg      : Message brut (sans le délimiteur \\0).
        is_server: True si le message vient du serveur (S→C).

    Returns:
        Tuple (msg_id, msg_name).
    """
    if not msg:
        return ("", "Empty")

    ids_sorted = _SERVER_IDS_SORTED if is_server else _CLIENT_IDS_SORTED
    names_map = SERVER_MESSAGES if is_server else CLIENT_MESSAGES

    # Cas spéciaux client uniquement
    if not is_server:
        # Cyrillique І (bug Dofus Retro post v1.29.1)
        if msg.startswith("G\u0406"):
            return ("GI", "GameGetExtraInformations")
        if _VERSION_RX.match(msg):
            return ("version", "AccountVersion")
        if _CREDENTIAL_RX.match(msg):
            return ("credential", "AccountCredential")

    for candidate_id in ids_sorted:
        if msg.startswith(candidate_id):
            return (candidate_id, names_map[candidate_id])

    # Fallback : 2 premiers chars
    fallback = msg[:2]
    return (fallback, "Unknown")


# ---------------------------------------------------------------------------
# Parsing d'un message complet
# ---------------------------------------------------------------------------


def parse_message(raw: str, is_server: bool) -> ParsedMessage:
    """Parser un message Dofus brut (sans le \\0 terminal).

    Args:
        raw      : Contenu du message (str, encodage latin-1 / ASCII).
        is_server: True si le message vient du serveur.

    Returns:
        ParsedMessage avec id, name, payload et timestamp.
    """
    direction = "S→C" if is_server else "C→S"
    msg_id, msg_name = extract_msg_id(raw, is_server)
    payload = raw[len(msg_id):]
    return ParsedMessage(
        direction=direction,
        msg_id=msg_id,
        msg_name=msg_name,
        payload=payload,
        raw=raw,
        timestamp=time.time(),
    )


# ---------------------------------------------------------------------------
# Logger de messages
# ---------------------------------------------------------------------------


def log_message(msg: ParsedMessage, max_payload: int = 120) -> None:
    """Afficher un message parsé dans les logs.

    Args:
        msg        : Message parsé.
        max_payload: Nombre max de chars du payload à afficher.
    """
    ts = time.strftime("%H:%M:%S", time.localtime(msg.timestamp))
    payload_preview = msg.payload[:max_payload]
    if len(msg.payload) > max_payload:
        payload_preview += "…"

    logger.info(
        "[%s] [%s] %s (%s) | %s",
        ts,
        msg.direction,
        msg.msg_id,
        msg.msg_name,
        payload_preview,
    )

    # Log spécial AK : afficher la clé complète pour analyse anti-cheat
    if msg.msg_id == "AK":
        logger.info("[AK] Clé complète (len=%d) : %s", len(msg.payload), msg.payload)

    # Log spécial AYK : décoder l'adresse du game server
    if msg.msg_id == "AYK" and len(msg.payload) >= 11:
        try:
            host, port, ticket = parse_ayk(msg.payload)
            logger.info(
                "  ↳ [AYK] Game server → %s:%d | ticket: %s…",
                host,
                port,
                ticket[:16],
            )
        except Exception as exc:
            logger.warning("  ↳ [AYK] Décodage échoué : %s", exc)


# ---------------------------------------------------------------------------
# Dispatcher (extensible pour la Phase 2)
# ---------------------------------------------------------------------------

# Signature d'un handler : (msg: ParsedMessage) → None (ou coroutine)
HandlerFn = Callable[[ParsedMessage], None]

_server_handlers: dict[str, list[HandlerFn]] = {}
_client_handlers: dict[str, list[HandlerFn]] = {}


def on_server_message(msg_id: str) -> Callable[[HandlerFn], HandlerFn]:
    """Décorateur pour enregistrer un handler sur un ID de message serveur.

    Usage ::

        @on_server_message("GDM")
        def handle_map_data(msg: ParsedMessage) -> None:
            ...
    """
    def decorator(fn: HandlerFn) -> HandlerFn:
        _server_handlers.setdefault(msg_id, []).append(fn)
        return fn
    return decorator


def on_client_message(msg_id: str) -> Callable[[HandlerFn], HandlerFn]:
    """Décorateur pour enregistrer un handler sur un ID de message client."""
    def decorator(fn: HandlerFn) -> HandlerFn:
        _client_handlers.setdefault(msg_id, []).append(fn)
        return fn
    return decorator


def dispatch(msg: ParsedMessage) -> None:
    """Appeler les handlers enregistrés pour ce message.

    Args:
        msg: Message parsé à dispatcher.
    """
    handlers = (
        _server_handlers if msg.direction == "S→C" else _client_handlers
    )
    for handler in handlers.get(msg.msg_id, []):
        try:
            handler(msg)
        except Exception as exc:
            logger.error("Handler %s sur %s a levé : %s", handler, msg.msg_id, exc)
