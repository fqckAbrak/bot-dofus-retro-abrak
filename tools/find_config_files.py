"""Cherche les fichiers de config réseau du client Abrak (connexion.xml, etc.)"""
import os

ABRAK_DIR = r"E:\games\Abrak"
EXTENSIONS = {".xml", ".json", ".ini", ".cfg", ".properties", ".txt"}
KEYWORDS = {"connexion", "connection", "server", "host", "ip", "port", "login", "config"}

print(f"Scan de {ABRAK_DIR}\n")
for root, dirs, files in os.walk(ABRAK_DIR):
    # Ignorer les dossiers inutiles
    dirs[:] = [d for d in dirs if d.lower() not in {"node_modules", "gfx", "maps", "sounds", "fonts", "__pycache__"}]
    for fname in files:
        name_lower = fname.lower()
        ext = os.path.splitext(fname)[1].lower()
        if ext in EXTENSIONS and any(kw in name_lower for kw in KEYWORDS):
            full = os.path.join(root, fname)
            print(f"  {full}")
