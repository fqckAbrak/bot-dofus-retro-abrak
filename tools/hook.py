"""
Hook fritm sur le process Abrak.exe.
Lance ce script EN ADMINISTRATEUR pendant que main.py tourne.
"""
import time
import psutil

PROXY_PORT = 8080
PROCESS_NAME = "Abrak.exe"


def find_abrak_pid() -> int | None:
    for proc in psutil.process_iter(["pid", "name"]):
        if proc.info["name"] == PROCESS_NAME:
            return proc.info["pid"]
    return None


def main() -> None:
    import fritm

    pid = find_abrak_pid()
    if pid is None:
        print(f"Process '{PROCESS_NAME}' introuvable. Lance le client d'abord.")
        return

    print(f"Abrak.exe trouvé → PID {pid}")
    print(f"Hook vers 127.0.0.1:{PROXY_PORT}...")

    try:
        fritm.hook_pid(pid, PROXY_PORT)
        print("Hook actif ! Reconnecte-toi en jeu — les paquets apparaissent dans main.py.")
        print("Ctrl+C pour arrêter.")
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        print("Hook arrêté.")
    except Exception as e:
        print(f"Erreur fritm : {e}")
        print()
        print("→ Essaie la Solution 2 : modifier le fichier de config du client Abrak.")


if __name__ == "__main__":
    main()
