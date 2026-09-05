"""Cherche l'IP du serveur de jeu dans les fichiers binaires du client Abrak."""
import os

ABRAK_DIR = r"E:\games\Abrak"
TARGET_IP = b"51.89.153.20"
# Aussi chercher d'autres IPs potentielles
EXTRA_TERMS = [b"1303", b"abrak.fr", b"server", b"gameserver"]

SKIP_DIRS = {"node_modules", "__pycache__", "gfx", "sounds", "fonts"}
SKIP_EXTS = {".png", ".jpg", ".jpeg", ".gif", ".mp3", ".ogg", ".wav", ".ttf", ".woff"}

print(f"Recherche de {TARGET_IP.decode()} dans {ABRAK_DIR}\n")

found_files = []
for root, dirs, files in os.walk(ABRAK_DIR):
    dirs[:] = [d for d in dirs if d not in SKIP_DIRS]
    for fname in files:
        if os.path.splitext(fname)[1].lower() in SKIP_EXTS:
            continue
        fpath = os.path.join(root, fname)
        try:
            with open(fpath, "rb") as f:
                data = f.read()
            if TARGET_IP in data:
                found_files.append(fpath)
                print(f"[IP TROUVÉE] {fpath}")
                # Afficher le contexte autour de l'IP
                idx = data.find(TARGET_IP)
                ctx = data[max(0, idx-60):idx+80]
                print(f"  Contexte: {ctx}\n")
        except (PermissionError, OSError):
            pass

if not found_files:
    print(f"IP {TARGET_IP.decode()} non trouvée dans les fichiers.")
    print("\nRecherche de 'abrak.fr' et du port 1303...\n")
    for root, dirs, files in os.walk(ABRAK_DIR):
        dirs[:] = [d for d in dirs if d not in SKIP_DIRS]
        for fname in files:
            if os.path.splitext(fname)[1].lower() in SKIP_EXTS:
                continue
            fpath = os.path.join(root, fname)
            try:
                with open(fpath, "rb") as f:
                    data = f.read()
                for term in EXTRA_TERMS:
                    if term in data:
                        print(f"[{term.decode()}] {fpath}")
                        break
            except (PermissionError, OSError):
                pass
