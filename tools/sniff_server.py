"""
Sniffer de connexions Dofus Rétro — détecte l'IP du serveur de test.

Deux approches en parallèle :
  Approche 1 (psutil) : scanner les connexions TCP actives du processus Dofus
                         toutes les 2 secondes → la plus simple et fiable.
  Approche 2 (scapy)  : sniffer les paquets TCP SYN sortants sur les ports
                         443 et 5555 → fallback si le process est sandboxé.

Usage :
    python tools/sniff_server.py           # détecte une fois et sauvegarde
    python tools/sniff_server.py --watch   # continue à monitorer
"""

from __future__ import annotations

import argparse
import json
import sys
import time
import threading
from pathlib import Path

# ---------------------------------------------------------------------------
# Ports Dofus connus
# ---------------------------------------------------------------------------
DOFUS_PORTS: set[int] = {443, 1303, 5555, 5556, 5001, 6555}
DOFUS_PROCESS_NAMES: list[str] = ["Dofus", "Dofus.exe", "Dofus Retro", "flash", "Abrak", "Abrak.exe"]

CONFIG_PATH = Path(__file__).parent.parent / "config.json"

# Résultat partagé entre les deux approches
_found_host: str | None = None
_found_port: int | None = None
_lock = threading.Lock()


# ---------------------------------------------------------------------------
# Utilitaires
# ---------------------------------------------------------------------------


def _is_dofus_process(name: str) -> bool:
    """Vérifier si un nom de processus correspond à Dofus."""
    name_lower = name.lower()
    return any(d.lower() in name_lower for d in DOFUS_PROCESS_NAMES)


def _save_config(host: str, port: int) -> None:
    """Sauvegarder l'IP et le port dans config.json."""
    cfg: dict = {}
    if CONFIG_PATH.exists():
        try:
            with CONFIG_PATH.open("r", encoding="utf-8") as fh:
                cfg = json.load(fh)
        except Exception:
            pass

    cfg["server_host"] = host
    cfg["server_port"] = port
    cfg.setdefault("proxy_host", "127.0.0.1")
    cfg.setdefault("proxy_port", 8080)

    with CONFIG_PATH.open("w", encoding="utf-8") as fh:
        json.dump(cfg, fh, indent=4)

    print(f"\n✓  config.json mis à jour : server_host={host}, server_port={port}")


def _report(host: str, port: int, source: str) -> None:
    """Afficher la découverte et mettre à jour la variable partagée."""
    global _found_host, _found_port
    with _lock:
        if _found_host is not None:
            return  # déjà trouvé
        _found_host = host
        _found_port = port

    print(f"\n[DOFUS] [{source}] Connexion détectée → IP: {host}  PORT: {port}")
    _save_config(host, port)


# ---------------------------------------------------------------------------
# Approche 1 — psutil
# ---------------------------------------------------------------------------


def scan_with_psutil(watch: bool) -> None:
    """Scanner les connexions TCP actives via psutil.

    Parcourt toutes les connexions TCP du système, filtre celles dont
    le PID correspond à un processus Dofus, et affiche les IP distantes.

    Args:
        watch: Si True, continue à monitorer même après la première détection.
    """
    try:
        import psutil
    except ImportError:
        print("[psutil] Non installé — approche ignorée. pip install psutil")
        return

    seen: set[tuple[str, int]] = set()
    print("[psutil] Monitoring des connexions TCP… (lance Dofus et connecte-toi)")

    while True:
        try:
            # Collecter tous les PIDs Dofus
            dofus_pids: set[int] = set()
            for proc in psutil.process_iter(["pid", "name"]):
                try:
                    if _is_dofus_process(proc.info["name"] or ""):
                        dofus_pids.add(proc.info["pid"])
                except (psutil.NoSuchProcess, psutil.AccessDenied):
                    pass

            if not dofus_pids:
                print("[psutil] Aucun processus Dofus détecté — attente…", end="\r")
            else:
                print(f"[psutil] Processus Dofus trouvés : PIDs {dofus_pids}    ", end="\r")

            # Parcourir les connexions
            for conn in psutil.net_connections(kind="tcp"):
                if conn.pid not in dofus_pids:
                    continue
                if conn.status not in ("ESTABLISHED", "SYN_SENT"):
                    continue
                if not conn.raddr:
                    continue

                rhost, rport = conn.raddr.ip, conn.raddr.port
                # Ignorer localhost et IP privées courantes qui ne sont pas Dofus
                if rhost.startswith("127.") or rhost == "::1":
                    continue
                if (rhost, rport) in seen:
                    continue

                seen.add((rhost, rport))
                _report(rhost, rport, "psutil")

                if not watch and _found_host is not None:
                    return

        except (psutil.AccessDenied, PermissionError) as exc:
            print(f"\n[psutil] Accès refusé : {exc}")
            print("         Lance le script en administrateur pour accéder aux connexions.")
            return
        except Exception as exc:
            print(f"[psutil] Erreur : {exc}")

        time.sleep(2)

        if not watch and _found_host is not None:
            return


# ---------------------------------------------------------------------------
# Approche 2 — scapy (sniffer passif TCP SYN)
# ---------------------------------------------------------------------------


def scan_with_scapy(watch: bool) -> None:
    """Sniffer les paquets TCP SYN sortants via scapy.

    Filtre les SYN sortants vers les ports 443 et 5555 (ports Dofus).
    Affiche l'IP de destination dès qu'un SYN est capturé.

    Args:
        watch: Si True, continue à monitorer même après la première détection.
    """
    try:
        from scapy.all import sniff, TCP, IP  # type: ignore
    except ImportError:
        print("[scapy] Non installé — approche ignorée. pip install scapy")
        return
    except Exception as exc:
        print(f"[scapy] Erreur import : {exc}")
        return

    seen: set[tuple[str, int]] = set()
    print("[scapy] Sniff TCP SYN sur ports Dofus (443, 5555)… (Ctrl+C pour arrêter)")

    def packet_callback(pkt) -> None:  # type: ignore
        if not (pkt.haslayer(IP) and pkt.haslayer(TCP)):
            return
        tcp = pkt[TCP]
        ip = pkt[IP]

        # SYN uniquement (flags == 0x02)
        if tcp.flags != 0x02:
            return
        if tcp.dport not in DOFUS_PORTS:
            return

        dst = ip.dst
        dport = tcp.dport

        if dst.startswith("127.") or dst == "::1":
            return
        if (dst, dport) in seen:
            return

        seen.add((dst, dport))
        _report(dst, dport, "scapy")

    try:
        sniff(
            filter=f"tcp and ({' or '.join(f'port {p}' for p in DOFUS_PORTS)})",
            prn=packet_callback,
            store=False,
            stop_filter=lambda _: (not watch and _found_host is not None),
        )
    except PermissionError:
        print("[scapy] Accès refusé — lance le script en administrateur (sniff raw socket).")
    except Exception as exc:
        print(f"[scapy] Erreur sniff : {exc}")


# ---------------------------------------------------------------------------
# Point d'entrée
# ---------------------------------------------------------------------------


def main() -> None:
    """Lancer les deux approches en parallèle."""
    parser = argparse.ArgumentParser(
        description="Détecte l'IP du serveur Dofus Rétro auquel le client se connecte."
    )
    parser.add_argument(
        "--watch",
        action="store_true",
        help="Continuer à monitorer toutes les connexions (ne s'arrête pas après la première).",
    )
    args = parser.parse_args()

    print("=" * 60)
    print("Dofus Rétro — Sniffer d'IP serveur")
    print("=" * 60)
    print(f"Cible : {CONFIG_PATH}")
    print("Lance le client Dofus et connecte-toi à un serveur.")
    print("=" * 60)

    # Lancer psutil dans le thread principal, scapy dans un thread secondaire
    # (scapy bloque avec sniff(), donc il part en daemon)
    scapy_thread = threading.Thread(
        target=scan_with_scapy,
        args=(args.watch,),
        daemon=True,
        name="scapy-sniffer",
    )
    scapy_thread.start()

    try:
        scan_with_psutil(args.watch)
    except KeyboardInterrupt:
        pass

    # Attendre que scapy termine si on est en mode --watch
    if args.watch:
        try:
            scapy_thread.join()
        except KeyboardInterrupt:
            pass

    if _found_host:
        print(f"\nRésultat final → IP: {_found_host}  PORT: {_found_port}")
        print("Lance maintenant : python main.py")
    else:
        print("\nAucune connexion Dofus détectée.")
        print("Vérifie que le client Dofus est ouvert et connecté.")
        sys.exit(1)


if __name__ == "__main__":
    main()
