"""
Connexion sortante vers le vrai serveur Dofus (login ou game).
"""

from __future__ import annotations

import asyncio
import logging
from asyncio import StreamReader, StreamWriter

logger = logging.getLogger(__name__)


async def open_server_connection(
    host: str,
    port: int,
    timeout: float = 10.0,
) -> tuple[StreamReader, StreamWriter]:
    """Ouvrir une connexion TCP vers le serveur Ankama.

    Args:
        host   : Adresse IP du serveur (ex : "34.251.172.139").
        port   : Port TCP (ex : 443 ou 5555).
        timeout: Délai max en secondes avant TimeoutError.

    Returns:
        Tuple (reader, writer) pour la connexion vers le serveur.

    Raises:
        OSError     : Si la connexion est refusée.
        TimeoutError: Si le délai est dépassé.
    """
    logger.info("[client] Connexion vers %s:%d…", host, port)
    reader, writer = await asyncio.wait_for(
        asyncio.open_connection(host, port),
        timeout=timeout,
    )
    peer = writer.get_extra_info("peername")
    logger.info("[client] Connecté à %s:%d", *peer)
    return reader, writer
