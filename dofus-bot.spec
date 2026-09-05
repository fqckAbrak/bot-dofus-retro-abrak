# -*- mode: python ; coding: utf-8 -*-
"""
Build PyInstaller du bot Dofus Rétro → dofus-bot.exe (onefile).

    python -m PyInstaller dofus-bot.spec --noconfirm

L'exe est autonome pour le CODE et les dépendances natives (frida, WinDivert).
Les DONNÉES restent EXTERNES à côté de l'exe : place dofus-bot.exe dans le
dossier dofus-retro-bot/ (avec config.json, ressources/, scripts/ et data/).
core/paths.py ancre tous les chemins sur le dossier de l'exe.
"""

from PyInstaller.utils.hooks import collect_all

datas = []
binaries = []
hiddenimports = []

# frida : module natif _frida + données.
for pkg in ("frida", "pydivert", "fritm"):
    d, b, h = collect_all(pkg)
    datas += d
    binaries += b
    hiddenimports += h

# tkinter est détecté automatiquement, mais on force les onglets/tabs chargés
# indirectement pour éviter tout oubli d'analyse statique.
hiddenimports += [
    "dashboard.tabs.personnage",
    "dashboard.tabs.carte",
    "dashboard.tabs.inventaire",
    "dashboard.tabs.metiers",
    "dashboard.tabs.scripts",
    "dashboard.tabs.misc",
    "dashboard.tabs.hdv",
    "dashboard.tabs.console",
    "dashboard.tabs.combat",
    "dashboard.tabs.parametres",
]

a = Analysis(
    ["main.py"],
    pathex=["."],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=["scapy"],  # importé seulement par des tools/ hors runtime du bot
    noarchive=False,
    optimize=0,
)

pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    name="dofus-bot",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=True,           # main.py cache la console lui-même (--show-console pour l'afficher)
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)
