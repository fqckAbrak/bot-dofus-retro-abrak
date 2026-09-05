"""
Redirection transparente de connexions TCP via WinDivert SOCKET layer.

Nécessite des droits Administrateur (comme Proxifier).

WinDivert SOCKET layer intercepte l'appel connect() AVANT l'envoi du SYN TCP.
On modifie l'adresse destination vers le bot local, et le kernel établit
la connexion vers ce nouveau dest de façon transparente pour le processus.

SOCKET layer vs NETWORK layer :
  - NETWORK : paquet IP brut → packet.dst_addr / packet.dst_port (champs IP header)
  - SOCKET  : événement socket → packet.socket.RemoteAddr / RemotePort (struct ctypes)
    Les champs RemoteAddr sont 4×uint32 en network byte order (IPv4 dans [0]).
    Les champs RemotePort sont uint16 en network byte order.

On enregistre la destination originale dans _nat_table, indexée par (src_ip, src_port),
pour que le bot sache vers quel vrai serveur relayer.
"""

from __future__ import annotations

import logging
import socket as _socket
import struct
import threading
from typing import Dict, Tuple

logger = logging.getLogger(__name__)

# nat_table[(src_ip, src_port)] = (original_dst_ip, original_dst_port)
_nat_table: Dict[Tuple[str, int], Tuple[str, int]] = {}
_divert_active: bool = False


def _addr_from_sock(addr_field) -> str:
    """Convertir un champ RemoteAddr/LocalAddr (c_uint32*4, network order) en IP string."""
    return _socket.inet_ntoa(struct.pack("<I", addr_field[0]))


def _addr_to_sock(ip: str, addr_field) -> None:
    """Écrire une IP string dans un champ RemoteAddr/LocalAddr."""
    addr_field[0] = struct.unpack("<I", _socket.inet_aton(ip))[0]
    for i in range(1, 4):
        addr_field[i] = 0


def get_original_dest(src_ip: str, src_port: int) -> Tuple[str, int] | None:
    """Retourner la destination originale pour une connexion détournée."""
    return _nat_table.get((src_ip, src_port))


def start_divert(proxy_port: int, game_ports: list[int] | None = None) -> bool:
    """Démarrer WinDivert SOCKET layer pour rediriger les connexions game vers le bot.

    Args:
        proxy_port: Port local du bot (connexions redirigées vers 127.0.0.1:proxy_port).
        game_ports: Ports à intercepter (défaut : [1303, 1304]).

    Returns:
        True si démarré avec succès, False sinon (droits insuffisants, etc.).
    """
    global _divert_active

    if game_ports is None:
        game_ports = [1303, 1304]

    try:
        import pydivert
    except ImportError:
        logger.error("[divert] pydivert non installé. pip install pydivert")
        return False

    # SOCKET layer : remotePort (pas tcp.DstPort qui est réservé au NETWORK layer)
    port_conditions = " or ".join(f"remotePort == {p}" for p in game_ports)
    filt = f"outbound and tcp and ({port_conditions})"

    def _try_open() -> "pydivert.WinDivert":
        handle = pydivert.WinDivert(filt, layer=pydivert.Layer.SOCKET)
        handle.open()
        return handle

    try:
        w = _try_open()
    except PermissionError as exc:
        logger.warning(
            "[divert] Droits insuffisants pour WinDivert (%s). "
            "Relancer main.py en tant qu'Administrateur pour intercepter le game traffic.",
            exc,
        )
        return False
    except OSError as exc:
        if getattr(exc, "winerror", None) == 87:
            # ERROR_INVALID_PARAMETER : version du driver en kernel ≠ DLL.
            # Supprimer le service pour forcer pydivert à en créer un propre.
            logger.warning("[divert] WinError 87 — suppression/recréation du service WinDivert…")
            import subprocess, time
            subprocess.run(["sc", "stop", "WinDivert"], capture_output=True)
            time.sleep(0.5)
            subprocess.run(["sc", "delete", "WinDivert"], capture_output=True)
            time.sleep(1.5)
            try:
                w = _try_open()
            except PermissionError as exc2:
                logger.warning("[divert] Droits insuffisants après recréation : %s", exc2)
                return False
            except Exception as exc2:
                logger.error("[divert] Échec après recréation service : %s", exc2)
                return False
        else:
            logger.error("[divert] Impossible d'ouvrir WinDivert : %s", exc)
            return False

    _divert_active = True
    logger.info(
        "[divert] WinDivert SOCKET actif — ports %s interceptés → 127.0.0.1:%d",
        game_ports, proxy_port,
    )

    def _loop() -> None:
        try:
            with w:
                for packet in w:
                    try:
                        sock = packet.socket
                        if sock is None:
                            w.send(packet)
                            continue

                        # Lire src/dst depuis la struct SOCKET (pas les champs IP header)
                        orig_dst_ip = _addr_from_sock(sock.RemoteAddr)
                        orig_dst_port = _socket.ntohs(sock.RemotePort)
                        src_ip = _addr_from_sock(sock.LocalAddr)
                        src_port = _socket.ntohs(sock.LocalPort)

                        # Enregistrer la destination originale pour le relay
                        _nat_table[(src_ip, src_port)] = (orig_dst_ip, orig_dst_port)
                        logger.debug(
                            "[divert] %s:%d → %s:%d intercepté → 127.0.0.1:%d",
                            src_ip, src_port, orig_dst_ip, orig_dst_port, proxy_port,
                        )

                        # Rediriger vers le bot
                        _addr_to_sock("127.0.0.1", sock.RemoteAddr)
                        sock.RemotePort = _socket.htons(proxy_port)

                        w.send(packet)
                    except Exception as exc:
                        logger.debug("[divert] Erreur paquet : %s", exc)
                        try:
                            w.send(packet)
                        except Exception:
                            pass
        except Exception as exc:
            logger.error("[divert] Boucle WinDivert arrêtée : %s", exc)

    t = threading.Thread(target=_loop, daemon=True, name="windivert")
    t.start()
    return True
