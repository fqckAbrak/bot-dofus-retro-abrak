"""
Hook frida pour intercepter ws2_32.dll!connect dans Abrak.exe.

Le script fritm original échoue sur Windows car il cherche 'connect' dans null
(tous les modules). Sur Windows, connect() est dans ws2_32.dll et doit être
recherché explicitement.

On surveille l'apparition d'Abrak.exe et on injecte le script dès que possible.
"""

from __future__ import annotations

import asyncio
import logging
import threading
from typing import Set

logger = logging.getLogger(__name__)

_HOOK_JS_TEMPLATE = r"""
(function() {
    var proxy_port = PROXY_PORT;
    var game_ports = GAME_PORTS_ARRAY;

    // Frida 16+ : API instance via Process.getModuleByName (plus de méthodes statiques sur Module)
    var ws2;
    try {
        ws2 = Process.getModuleByName('ws2_32.dll');
    } catch(e) {
        // ws2_32.dll pas encore chargé dans ce process — pas de socket
        return;
    }

    var connect_p = ws2.findExportByName('connect');
    var send_p    = ws2.findExportByName('send');
    var recv_p    = ws2.findExportByName('recv');

    if (!connect_p) {
        console.log('[hook] ERREUR: ws2_32!connect introuvable dans ws2_32.dll');
        return;
    }
    console.log('[hook] ws2_32!connect @ ' + connect_p + ' — hook actif');

    var socket_send = new NativeFunction(send_p, 'int', ['int', 'pointer', 'int', 'int']);
    var socket_recv = new NativeFunction(recv_p, 'int', ['int', 'pointer', 'int', 'int']);

    Interceptor.attach(connect_p, {
        onEnter: function(args) {
            var sockaddr_p = args[1];
            // Windows sockaddr_in: sa_family(u16 LE) + port(u16 BE) + addr(4 bytes)
            var sa_family = sockaddr_p.readU16();
            if (sa_family !== 2) {   // AF_INET = 2
                this.hook = false;
                return;
            }

            // Port en network byte order (big endian)
            var port = sockaddr_p.add(2).readU8() * 256 + sockaddr_p.add(3).readU8();
            var ip = sockaddr_p.add(4).readU8() + '.' +
                     sockaddr_p.add(5).readU8() + '.' +
                     sockaddr_p.add(6).readU8() + '.' +
                     sockaddr_p.add(7).readU8();

            if (game_ports.indexOf(port) === -1) {
                this.hook = false;
                return;
            }

            this.hook    = true;
            this.sockfd  = args[0].toInt32();
            this.orig_ip   = ip;
            this.orig_port = port;

            // Réécrire destination → 127.0.0.1:proxy_port
            sockaddr_p.add(2).writeU8(Math.floor(proxy_port / 256));
            sockaddr_p.add(3).writeU8(proxy_port % 256);
            sockaddr_p.add(4).writeByteArray([127, 0, 0, 1]);

            console.log('[hook] connect intercepté : ' + ip + ':' + port + ' → 127.0.0.1:' + proxy_port);
        },

        onLeave: function(retval) {
            if (!this.hook) return;

            // Envoyer HTTP CONNECT pour que le bot sache la vraie destination
            var req = 'CONNECT ' + this.orig_ip + ':' + this.orig_port + ' HTTP/1.0\r\n\r\n';
            var buf_send = Memory.allocUtf8String(req);
            socket_send(this.sockfd, buf_send, req.length, 0);

            // Lire octet par octet jusqu'à \r\n\r\n (fin des headers HTTP).
            // CRITIQUE : ne PAS lire au-delà — les octets suivants sont du trafic
            // game (ex: HG\0) que Flash doit recevoir lui-même.
            // On utilise une fenêtre glissante d'entiers (pas de strings JS) pour
            // éviter les bugs QuickJS avec \0 dans les chaînes.
            var byte_buf = Memory.alloc(1);
            var w0 = 0, w1 = 0, w2 = 0, w3 = 0;  // sliding window: derniers 4 bytes
            var limit = 500;
            while (limit-- > 0) {
                var n = socket_recv(this.sockfd, byte_buf, 1, 0);
                if (n <= 0) {
                    Thread.sleep(0.01);
                    limit++;
                    continue;
                }
                var b = byte_buf.readU8();
                w0 = w1; w1 = w2; w2 = w3; w3 = b;
                // \r\n\r\n = 13, 10, 13, 10
                if (w0 === 13 && w1 === 10 && w2 === 13 && w3 === 10) break;
            }
            console.log('[hook] 200 OK reçu — relay actif');
        }
    });
})();
"""


def _build_script(proxy_port: int, game_ports: list[int]) -> str:
    ports_js = "[" + ", ".join(str(p) for p in game_ports) + "]"
    return (
        _HOOK_JS_TEMPLATE
        .replace("PROXY_PORT", str(proxy_port))
        .replace("GAME_PORTS_ARRAY", ports_js)
    )


def _inject(pid: int, proxy_port: int, game_ports: list[int]) -> bool:
    """Injecter le hook frida dans un PID. Retourne True si succès."""
    try:
        import frida
        session = frida.attach(pid)
        script = session.create_script(_build_script(proxy_port, game_ports))
        script.on("message", lambda msg, data: logger.debug("[frida:%d] %s", pid, msg))
        script.load()
        logger.info("[frida] Hook injecté dans PID=%d", pid)
        return True
    except Exception as exc:
        logger.warning("[frida] Échec injection PID=%d : %s", pid, exc)
        return False


def start_frida_hook(proxy_port: int, game_ports: list[int] | None = None) -> None:
    """Démarrer la surveillance Abrak.exe et injecter le hook frida."""
    if game_ports is None:
        game_ports = [1303, 1304]

    try:
        import frida  # noqa: F401
        import psutil  # noqa: F401
    except ImportError as e:
        logger.error("[frida] Dépendance manquante : %s", e)
        return

    hooked: Set[int] = set()

    def _watch() -> None:
        import psutil
        logger.info("[frida] Surveillance Abrak.exe démarrée (proxy_port=%d)", proxy_port)
        while True:
            try:
                import psutil as _ps
                for proc in _ps.process_iter(["pid", "name"]):
                    try:
                        pid: int = proc.info["pid"]
                        name: str = (proc.info["name"] or "").lower()
                        if name == "abrak.exe" and pid not in hooked:
                            hooked.add(pid)
                            if _inject(pid, proxy_port, game_ports):
                                pass
                            else:
                                hooked.discard(pid)
                    except (_ps.NoSuchProcess, _ps.AccessDenied):
                        pass

                # Purger les PIDs morts
                dead = {p for p in hooked if not psutil.pid_exists(p)}
                hooked.difference_update(dead)
                if dead:
                    logger.debug("[frida] PIDs expirés : %s", dead)

            except Exception as exc:
                logger.debug("[frida] Erreur boucle : %s", exc)

            import time
            time.sleep(0.5)

    t = threading.Thread(target=_watch, daemon=True, name="frida-watch")
    t.start()
