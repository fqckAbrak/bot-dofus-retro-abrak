"""
Serveur proxy MITM asyncio pour Dofus Rétro 1.29.

Flux de connexion avec WinDivert (pydivert) :
  1. WinDivert SOCKET layer intercepte connect() de Flash vers 51.89.153.20:1303/1304
     et redirige vers 127.0.0.1:8080 (proxy_port). La destination originale est
     enregistrée dans proxy.divert._nat_table.
  2. Flash arrive directement sur notre serveur (PAS HTTP CONNECT).
  3. On retrouve la vraie destination via nat_table[(src_ip, src_port)].
  4. On ouvre la connexion vers le vrai serveur de jeu.
  5. Les deux tâches de relay sont lancées en parallèle.

Fallback : si la connexion est HTTP CONNECT (fritm legacy), on gère aussi.
"""

from __future__ import annotations

import asyncio
import logging
from asyncio import StreamReader, StreamWriter

from proxy.client import open_server_connection
from proxy.relay import relay
import bot.channel as _bot_channel
from core.session import manager as _session_manager, set_active

logger = logging.getLogger(__name__)

# Rempli par main.py depuis config.json
_default_host: str = "0.0.0.0"
_default_port: int = 443

# Port d'écoute du bot (proxy), rempli par start_proxy()
proxy_listen_host: str = "127.0.0.1"
proxy_listen_port: int = 8080

# Adresse du vrai serveur de jeu, remplie quand le relay intercepte AYK.
# La prochaine connexion entrante l'utilisera à la place de _default_host:_default_port.
_pending_game_host: str | None = None
_pending_game_port: int | None = None


def set_pending_game_server(host: str, port: int) -> None:
    """Enregistrer l'adresse du vrai serveur de jeu (intercept AYK).

    Args:
        host: IP ou hostname du serveur de jeu réel.
        port: Port TCP du serveur de jeu réel.
    """
    global _pending_game_host, _pending_game_port
    _pending_game_host = host
    _pending_game_port = port
    logger.info("[server] Pending game server enregistré : %s:%d", host, port)


def _pop_pending_game_server() -> tuple[str, int] | None:
    """Récupérer et effacer l'adresse du serveur de jeu en attente."""
    global _pending_game_host, _pending_game_port
    if _pending_game_host is not None and _pending_game_port is not None:
        result = (_pending_game_host, _pending_game_port)
        _pending_game_host = None
        _pending_game_port = None
        return result
    return None


def configure_default_target(host: str, port: int) -> None:
    """Définir l'adresse du serveur utilisée en fallback (sans HTTP CONNECT).

    Args:
        host: IP ou hostname du serveur Dofus.
        port: Port TCP du serveur.
    """
    global _default_host, _default_port
    _default_host = host
    _default_port = port


# ---------------------------------------------------------------------------
# Parsing HTTP CONNECT
# ---------------------------------------------------------------------------


def _parse_connect_request(data: bytes) -> tuple[str, int] | None:
    """Extraire host et port depuis une requête HTTP CONNECT.

    Format attendu : CONNECT <host>:<port> HTTP/1.x\\r\\n...\\r\\n\\r\\n

    Args:
        data: Données brutes reçues du client.

    Returns:
        Tuple (host, port) ou None si la requête n'est pas un CONNECT valide.
    """
    try:
        first_line = data.split(b"\r\n", 1)[0].decode("ascii")
    except (UnicodeDecodeError, IndexError):
        return None

    parts = first_line.strip().split()
    if len(parts) < 2 or parts[0].upper() != "CONNECT":
        return None

    host_port = parts[1]
    if ":" not in host_port:
        return None

    host, port_str = host_port.rsplit(":", 1)
    try:
        port = int(port_str)
    except ValueError:
        return None

    return host, port


# ---------------------------------------------------------------------------
# Handler de connexion cliente
# ---------------------------------------------------------------------------

_connection_counter: int = 0


async def handle_client(
    client_reader: StreamReader,
    client_writer: StreamWriter,
) -> None:
    """Gérer une nouvelle connexion du client Flash.

    Étapes :
      1. Lire les premiers octets pour détecter HTTP CONNECT.
      2. Si CONNECT → extraire la destination réelle et répondre 200 OK.
      3. Sinon → utiliser la destination configurée dans config.json.
      4. Ouvrir la connexion vers le serveur réel.
      5. Lancer les deux relays en parallèle avec asyncio.gather.

    Args:
        client_reader: Stream en lecture depuis le client Flash.
        client_writer: Stream en écriture vers le client Flash.
    """
    global _connection_counter
    _connection_counter += 1
    conn_id = _connection_counter
    peer = client_writer.get_extra_info("peername")
    logger.info("[server] [#%d] Nouvelle connexion depuis %s:%d", conn_id, *peer)

    # --- Session dédiée à cette connexion (multi-instance) ---
    # set_active dans le contexte de cette task : les tasks filles (relays,
    # handlers dispatchés, coroutines bot lancées depuis ce contexte) héritent
    # de la session. Chaque connexion = une task handle_client = un contexte isolé.
    session = _session_manager.create(conn_id)
    set_active(session)

    # --- 1. Résoudre la destination cible ---
    # Priorité : WinDivert nat_table > pending game server (AYK legacy) > default config
    from proxy.divert import get_original_dest
    orig = get_original_dest(peer[0], peer[1])
    if orig:
        target_host, target_port = orig
        logger.info(
            "[server] [#%d] WinDivert NAT %s:%d → vrai dest %s:%d",
            conn_id, peer[0], peer[1], target_host, target_port,
        )
    else:
        pending = _pop_pending_game_server()
        target_host = pending[0] if pending else _default_host
        target_port = pending[1] if pending else _default_port
        if pending:
            logger.info("[server] [#%d] Connexion game server (AYK) → %s:%d", conn_id, target_host, target_port)
    label = f"#{conn_id}"

    try:
        initial = await asyncio.wait_for(client_reader.read(4096), timeout=5.0)
    except asyncio.TimeoutError:
        logger.warning("[server] [%s] Timeout lecture initiale", label)
        client_writer.close()
        return

    if not initial:
        client_writer.close()
        return

    connect_result = _parse_connect_request(initial)
    if connect_result is not None:
        target_host, target_port = connect_result
        label = f"#{conn_id}:{target_host}:{target_port}"
        logger.info(
            "[server] [%s] HTTP CONNECT → %s:%d", label, target_host, target_port
        )
        # Répondre 200 OK (format HTTP/1.0 attendu par fritm)
        client_writer.write(b"HTTP/1.0 200 Connection established\r\n\r\n")
        await client_writer.drain()
        # Pas de données Dofus dans 'initial' → le flux commence après le 200 OK
        buffered_initial: bytes = b""
    else:
        # Connexion directe sans fritm : les premières données sont déjà du Dofus
        logger.info(
            "[server] [%s] Connexion directe → %s:%d (config.json)",
            label, target_host, target_port,
        )
        buffered_initial = initial

    # --- 2. Connexion vers le vrai serveur ---
    try:
        server_reader, server_writer = await open_server_connection(
            target_host, target_port
        )
    except Exception as exc:
        logger.error(
            "[server] [%s] Impossible de joindre %s:%d : %s",
            label, target_host, target_port, exc,
        )
        client_writer.close()
        return

    # Si connexion directe : injecter les données buffées dans le relay C→S
    # en les « rejouant » dans le reader via un hack de préfixage
    if buffered_initial:
        # On réinjecte en écrivant directement vers le serveur et en logguant
        try:
            server_writer.write(buffered_initial)
            await server_writer.drain()
        except Exception:
            pass
        # Logger le fragment initial comme paquet C→S
        _log_raw_fragment(buffered_initial, is_server=False, label=label, session=session)

    # --- 3. Relay bidirectionnel ---
    logger.info("[server] [%s] Relay démarré.", label)
    _bot_channel.set_writer(server_writer)
    session.channel.client_writer = client_writer  # pour injection S→C (dump inventaire)
    try:
        await asyncio.gather(
            relay(client_reader, server_writer, is_server=False, label=label, session=session),
            relay(server_reader, client_writer, is_server=True, label=label, session=session),
        )
    except Exception as exc:
        logger.error("[server] [%s] asyncio.gather a levé : %s", label, exc)
    finally:
        _bot_channel.set_writer(None)
        session.set_disconnected()
        for w in (client_writer, server_writer):
            try:
                w.close()
                await w.wait_closed()
            except Exception:
                pass
        logger.info("[server] [%s] Session terminée.", label)


def _log_raw_fragment(data: bytes, is_server: bool, label: str, session=None) -> None:
    """Logger les messages contenus dans un fragment brut initial.

    Utilisé uniquement pour la connexion directe (sans HTTP CONNECT),
    pour logguer les données reçues avant que le relay soit démarré.
    """
    from protocol.parser import parse_message, log_message, dispatch
    from proxy.relay import _emit_packet

    buffer = data
    while b"\x00" in buffer:
        raw_bytes, buffer = buffer.split(b"\x00", 1)
        try:
            msg = parse_message(raw_bytes.decode("latin-1"), is_server)
            log_message(msg)
            _emit_packet(msg, session)
            dispatch(msg)
        except Exception:
            pass


# ---------------------------------------------------------------------------
# Démarrage du serveur
# ---------------------------------------------------------------------------


async def start_proxy(host: str, port: int) -> asyncio.Server:
    """Créer et démarrer le serveur proxy MITM.

    Args:
        host: Interface d'écoute (ex : "127.0.0.1").
        port: Port d'écoute (ex : 8080).

    Returns:
        Instance asyncio.Server en cours d'exécution.
    """
    global proxy_listen_host, proxy_listen_port
    proxy_listen_host = host
    proxy_listen_port = port
    server = await asyncio.start_server(handle_client, host, port)
    addrs = ", ".join(str(sock.getsockname()) for sock in server.sockets)
    logger.info("[server] Proxy MITM en écoute sur %s", addrs)
    return server
