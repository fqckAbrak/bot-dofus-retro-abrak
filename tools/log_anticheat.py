"""
Outil d'analyse des messages anti-cheat `-X` d'Abrak Rétro.

Lance ce script À LA PLACE de main.py pour capturer UNIQUEMENT
les messages -X avec leurs payloads complets et des horodatages précis.

Usage :
    python tools/log_anticheat.py

Sortie fichier : logs/anticheat_<timestamp>.log
"""

from __future__ import annotations

import asyncio
import json
import logging
import sys
import time
from pathlib import Path

# Ajouter la racine du projet au path
ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT))

from proxy.server import configure_default_target, start_proxy
from protocol.parser import on_server_message, on_client_message, ParsedMessage

# ---------------------------------------------------------------------------
# Logging double : console + fichier
# ---------------------------------------------------------------------------
LOG_DIR = ROOT / "logs"
LOG_DIR.mkdir(exist_ok=True)
log_filename = LOG_DIR / f"anticheat_{int(time.time())}.log"

file_handler = logging.FileHandler(log_filename, encoding="utf-8")
file_handler.setLevel(logging.DEBUG)

console_handler = logging.StreamHandler(sys.stdout)
console_handler.setLevel(logging.DEBUG)

fmt = logging.Formatter("%(asctime)s.%(msecs)03d %(message)s", datefmt="%H:%M:%S")
file_handler.setFormatter(fmt)
console_handler.setFormatter(fmt)

root_logger = logging.getLogger()
root_logger.setLevel(logging.DEBUG)
root_logger.addHandler(file_handler)
root_logger.addHandler(console_handler)

# Désactiver les loggers parasites
for noisy in ("proxy.relay", "proxy.server", "proxy.client", "protocol.parser"):
    logging.getLogger(noisy).setLevel(logging.WARNING)

logger = logging.getLogger("anticheat")

# ---------------------------------------------------------------------------
# État de tracking
# ---------------------------------------------------------------------------
_start_time: float = time.time()
_counter_s = 0   # messages -X serveur→client
_counter_c = 0   # messages -X client→serveur
_sequence: list[dict] = []   # pour dump JSON à la fin


def _record(direction: str, msg: ParsedMessage) -> None:
    global _counter_s, _counter_c
    elapsed = msg.timestamp - _start_time

    if direction == "S→C":
        _counter_s += 1
        idx = _counter_s
    else:
        _counter_c += 1
        idx = _counter_c

    entry = {
        "t": round(elapsed, 3),
        "dir": direction,
        "id": msg.msg_id,
        "payload": msg.payload,
        "payload_len": len(msg.payload),
        "payload_hex": msg.payload.encode("latin-1").hex() if msg.payload else "",
    }
    _sequence.append(entry)

    logger.info(
        "[%s] #%03d %-4s | len=%-4d | payload=%s",
        direction,
        idx,
        msg.msg_id,
        len(msg.payload),
        msg.payload if len(msg.payload) <= 80 else msg.payload[:80] + "…",
    )


# ---------------------------------------------------------------------------
# Handlers : intercepter TOUS les messages -X
# ---------------------------------------------------------------------------
# Les IDs -X sont de la forme "-C", "-D", "-1", "-2", etc.
# On enregistre un handler générique via le fallback "Unknown" en monkeypatching
# le dispatcher — ou plus proprement via un hook dans relay.

# Approche : on surcharge log_message pour intercepter les -X
import protocol.parser as _parser_mod

_original_log = _parser_mod.log_message


def _patched_log(msg: ParsedMessage, max_payload: int = 120) -> None:
    """Intercepte tous les messages -X avant le log normal."""
    if msg.msg_id.startswith("-"):
        _record(msg.direction, msg)
    # Sinon, log normal très court
    # (on supprime le bruit pour ne pas remplir la console)


_parser_mod.log_message = _patched_log


# ---------------------------------------------------------------------------
# Dump JSON à la fin
# ---------------------------------------------------------------------------
def _dump_sequence() -> None:
    if not _sequence:
        logger.info("Aucun message -X capturé.")
        return

    json_path = log_filename.with_suffix(".json")
    with json_path.open("w", encoding="utf-8") as fh:
        json.dump(_sequence, fh, indent=2, ensure_ascii=False)

    logger.info("=" * 60)
    logger.info("RÉSUMÉ ANTI-CHEAT")
    logger.info("  Total S→C : %d", _counter_s)
    logger.info("  Total C→S : %d", _counter_c)
    logger.info("  Durée     : %.1fs", time.time() - _start_time)
    logger.info("  Dump JSON : %s", json_path)
    logger.info("=" * 60)

    # Analyse basique des patterns
    _analyze()


def _analyze() -> None:
    """Analyse statistique rapide des messages capturés."""
    from collections import Counter

    ids_s = [e["id"] for e in _sequence if e["dir"] == "S→C"]
    ids_c = [e["id"] for e in _sequence if e["dir"] == "C→S"]

    logger.info("IDs S→C : %s", dict(Counter(ids_s).most_common(20)))
    logger.info("IDs C→S : %s", dict(Counter(ids_c).most_common(20)))

    # Intervalles entre messages consécutifs
    if len(_sequence) > 1:
        intervals = [
            round(_sequence[i]["t"] - _sequence[i-1]["t"], 3)
            for i in range(1, len(_sequence))
        ]
        avg = sum(intervals) / len(intervals)
        logger.info("Intervalle moyen entre -X : %.3fs", avg)

    # Longueurs de payload par ID
    from collections import defaultdict
    lengths: dict[str, list[int]] = defaultdict(list)
    for e in _sequence:
        lengths[f"{e['dir']} {e['id']}"].append(e["payload_len"])
    for k, lens in sorted(lengths.items()):
        logger.info("  %-12s : lens=%s", k, sorted(set(lens)))


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------
CONFIG_PATH = ROOT / "config.json"


async def main() -> None:
    if not CONFIG_PATH.exists():
        logger.error("config.json introuvable.")
        sys.exit(1)

    cfg = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
    server_host: str = cfg.get("server_host", "0.0.0.0")
    server_port: int = cfg.get("server_port", 443)
    proxy_host: str = cfg.get("proxy_host", "127.0.0.1")
    proxy_port: int = cfg.get("proxy_port", 8080)

    configure_default_target(server_host, server_port)
    server = await start_proxy(proxy_host, proxy_port)

    logger.info("=" * 60)
    logger.info("log_anticheat.py actif — seuls les messages -X sont loggués")
    logger.info("Fichier : %s", log_filename)
    logger.info("Ctrl+C pour arrêter et afficher l'analyse.")
    logger.info("=" * 60)

    try:
        async with server:
            await server.serve_forever()
    except asyncio.CancelledError:
        pass
    finally:
        _dump_sequence()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        pass
