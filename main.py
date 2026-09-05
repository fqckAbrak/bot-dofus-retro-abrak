"""
Point d'entrée du proxy MITM Dofus Rétro 1.29.

Stratégie sans Proxifier (WinDivert kernel) :
  - pydivert intercepte connect() de Flash vers les ports game (1303/1304)
    au niveau SOCKET layer (avant le SYN TCP) et redirige vers 127.0.0.1:proxy_port
  - La destination originale est stockée dans proxy.divert._nat_table
  - Le proxy lit la nat_table pour savoir vers quel vrai serveur relayer
  - Nécessite des droits Administrateur (même chose que Proxifier)
"""

from __future__ import annotations

import asyncio
import ctypes
import logging
import sys
from pathlib import Path
import json

from proxy.server import configure_default_target, start_proxy
from proxy.divert import start_divert
from proxy.frida_hook import start_frida_hook
import game.state as _game_state  # noqa: F401  # type: ignore[reportUnusedImport]
import bot.auto_reply as _auto_reply  # noqa: F401  # type: ignore[reportUnusedImport]
import bot.antibot as _antibot  # noqa: F401  # type: ignore[reportUnusedImport]
import dashboard.gui as _dashboard
import bot as _bot
from core.paths import BOT_DIR

# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------
logger = logging.getLogger("main")


def _setup_logging() -> None:
    """Configurer le logging (fichier + console). Appelé une fois au démarrage,
    APRÈS l'éventuelle élévation admin, pour ne pas créer de log fantôme dans le
    process non-élevé qui se relance puis quitte."""
    log_file = BOT_DIR / "logs" / (
        f"session_{__import__('datetime').datetime.now().strftime('%Y%m%d_%H%M%S')}.log"
    )
    log_file.parent.mkdir(exist_ok=True)
    logging.basicConfig(
        level=logging.DEBUG,
        format="%(asctime)s [%(levelname)s] %(name)s — %(message)s",
        datefmt="%H:%M:%S",
        handlers=[
            h for h in (
                # stdout peut être None si la console est masquée/absente
                logging.StreamHandler(sys.stdout) if sys.stdout is not None else None,
                logging.FileHandler(log_file, encoding="utf-8"),
            ) if h is not None
        ],
    )

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------
CONFIG_PATH = BOT_DIR / "config.json"


def load_config() -> dict:
    """Charger la configuration depuis config.json."""
    if not CONFIG_PATH.exists():
        logger.error("config.json introuvable.")
        sys.exit(1)

    with CONFIG_PATH.open("r", encoding="utf-8") as fh:
        return json.load(fh)


# ---------------------------------------------------------------------------
# Point d'entrée
# ---------------------------------------------------------------------------


async def main() -> None:
    """Démarrer le proxy MITM."""
    _setup_logging()
    _bot.set_main_loop(asyncio.get_event_loop())
    if _is_admin():
        logger.info("[admin] Bot lancé avec les droits administrateur — WinDivert prioritaire.")
    else:
        logger.warning("[admin] Bot NON-admin — interception frida (élévation refusée/indisponible).")
    cfg = load_config()

    server_host: str = cfg.get("server_host", "127.0.0.1")
    server_port: int = cfg.get("server_port", 26118)
    proxy_host: str = cfg.get("proxy_host", "127.0.0.1")
    proxy_port: int = cfg.get("proxy_port", 8080)
    game_ports: list[int] = cfg.get("game_ports", [1303, 1304])

    logger.info(
        "Config chargée : serveur=%s:%d, proxy=%s:%d, game_ports=%s",
        server_host, server_port, proxy_host, proxy_port, game_ports,
    )

    configure_default_target(server_host, server_port)
    _dashboard.start()

    try:
        server = await start_proxy(proxy_host, proxy_port)
    except OSError as exc:
        # Port déjà occupé (souvent une instance précédente restée en zombie).
        logger.error(
            "Impossible d'ouvrir le proxy sur %s:%d — %s. "
            "Une autre instance du bot tourne probablement déjà (vérifie les "
            "process python.exe / le port %d).",
            proxy_host, proxy_port, exc, proxy_port,
        )
        return

    # Tentative WinDivert (kernel, requiert admin) — fallback frida si indisponible
    if _is_admin() and start_divert(proxy_port, game_ports):
        logger.info("[divert] WinDivert SOCKET actif — ports %s → 127.0.0.1:%d", game_ports, proxy_port)
    else:
        if not _is_admin():
            logger.info("[frida] Non-admin — utilisation du hook frida (ws2_32!connect)")
        else:
            logger.warning("[divert] WinDivert indisponible — fallback frida")
        start_frida_hook(proxy_port, game_ports)

    logger.info("=" * 60)
    logger.info("Proxy MITM Dofus Rétro prêt sur %s:%d", proxy_host, proxy_port)
    logger.info("Lance le client Abrak, puis connecte-toi en jeu.")
    logger.info("Les connexions game (ports %s) sont interceptées automatiquement.", game_ports)
    logger.info("Ctrl+C pour arrêter.")
    logger.info("=" * 60)

    async with server:
        await server.serve_forever()



def _is_admin() -> bool:
    try:
        return bool(ctypes.windll.shell32.IsUserAnAdmin())
    except Exception:
        return False


def _hide_console() -> None:
    """Cacher la fenêtre console SI elle nous est dédiée.

    Le dashboard (onglet Console) et le fichier logs/session_*.log gardent la
    totalité des logs : la console externe ne fait que dupliquer stdout.

    Sécurité : si le bot est lancé depuis un terminal déjà ouvert (plusieurs
    process attachés à la console), on n'y touche pas pour ne pas masquer le
    terminal de l'utilisateur. Désactivable avec l'argument --show-console.
    """
    if "--show-console" in sys.argv:
        return
    try:
        kernel32 = ctypes.windll.kernel32
        hwnd = kernel32.GetConsoleWindow()
        if not hwnd:
            return
        # Combien de process partagent cette console ?
        buf = (ctypes.c_uint * 4)()
        count = kernel32.GetConsoleProcessList(buf, 4)
        if count <= 1:  # console dédiée (double-clic / relance admin) → on cache
            ctypes.windll.user32.ShowWindow(hwnd, 0)  # SW_HIDE
    except Exception:
        pass


def _elevate_if_needed() -> bool:
    """Relancer le bot en tant qu'administrateur via UAC, si pas déjà élevé.

    Objectif : une SEULE demande de droits admin au lancement (au lieu d'un popup
    UAC par client lors des attaches frida). En admin, l'interception passe par
    WinDivert (kernel, sans injection par-process → pas de course quand on lance
    plusieurs clients en même temps).

    Returns:
        True  si on doit poursuivre dans CE process (déjà admin, ou élévation
              refusée → repli non-admin/frida).
        False si une instance élevée a été lancée et que ce process doit quitter.

    Désactivable avec l'argument `--no-admin`.
    """
    if "--no-admin" in sys.argv:
        return True
    if _is_admin():
        return True
    try:
        # Relancer élevé. lpParameters = arguments d'origine.
        #  - .exe gelé  : sys.executable EST le bot → on ne passe que les args
        #    (sys.argv[1:]), pas argv[0] qui est le chemin de l'exe lui-même.
        #  - source     : sys.executable = python.exe → il faut lui repasser le
        #    script (argv[0]) + ses args, donc sys.argv complet.
        argv = sys.argv[1:] if getattr(sys, "frozen", False) else sys.argv
        params = " ".join(f'"{a}"' for a in argv)
        # nShowCmd : 0 = SW_HIDE (console cachée dès le départ, pas de flash),
        # 1 = SW_SHOWNORMAL si --show-console est demandé.
        n_show = 1 if "--show-console" in sys.argv else 0
        rc = ctypes.windll.shell32.ShellExecuteW(
            None, "runas", sys.executable, params, str(BOT_DIR), n_show
        )
        if rc > 32:
            # Instance élevée lancée avec succès → ce process se retire.
            return False
        # rc <= 32 : échec (souvent 5 = UAC refusé). On poursuit en non-admin.
        print(f"[admin] Élévation refusée/échouée (code {rc}) — poursuite en non-admin (frida).")
        return True
    except Exception as exc:
        print(f"[admin] Élévation impossible ({exc}) — poursuite en non-admin (frida).")
        return True


if __name__ == "__main__":
    # Demander les droits admin UNE fois, au lancement (avant tout le reste).
    if not _elevate_if_needed():
        sys.exit(0)
    # Cacher la console dédiée (logs conservés dans logs/ + onglet Console du GUI).
    _hide_console()
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        logger.info("Arrêt du proxy (Ctrl+C).")
    except Exception:
        # Console cachée : sans ça, un crash au démarrage disparaîtrait sans trace.
        logger.exception("Crash inattendu du bot — voir la stacktrace ci-dessus.")
