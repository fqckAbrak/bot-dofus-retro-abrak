"""Lance ce script PENDANT que le client Abrak est ouvert et connecté en jeu."""
import psutil

print(f"{'PID':<8} {'NOM PROCESS':<30} {'IP DISTANTE':<22} {'PORT'}")
print("-" * 70)
for conn in psutil.net_connections("tcp"):
    if conn.status == "ESTABLISHED" and conn.raddr:
        try:
            name = psutil.Process(conn.pid).name() if conn.pid else "?"
        except Exception:
            name = "?"
        print(f"{conn.pid:<8} {name:<30} {conn.raddr.ip:<22} {conn.raddr.port}")
