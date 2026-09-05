"""Résolution des chemins projet — compatible mode source ET exécutable PyInstaller.

En mode source, la racine du bot (``dofus-retro-bot/``) est le parent de ce
package ``core``.

En mode gelé (.exe PyInstaller), le code Python est extrait dans un dossier
temporaire (``sys._MEIPASS``), donc ``__file__`` ne pointe plus vers l'arbre du
projet. En revanche les DONNÉES (``ressources/`` — maps incluses —, ``scripts/``,
``config.json``, ``bot_settings.json``) restent EXTERNES, à côté de
l'exécutable. On ancre donc tous les chemins sur le dossier de l'exe.

Placement attendu de l'exe : dans ``dofus-retro-bot/`` (à côté de config.json),
exactement là où vivait ``main.py``.
"""

from __future__ import annotations

import sys
from pathlib import Path


def _bot_dir() -> Path:
    """Dossier racine du bot (contient config.json, ressources/, scripts/…)."""
    if getattr(sys, "frozen", False):
        # .exe : dossier de l'exécutable.
        return Path(sys.executable).resolve().parent
    # source : .../dofus-retro-bot (parent du package core/).
    return Path(__file__).resolve().parent.parent


BOT_DIR: Path = _bot_dir()                 # .../dofus-retro-bot
RESSOURCES_DIR: Path = BOT_DIR / "ressources"
DATA_DIR: Path = BOT_DIR / "data"
SCRIPTS_DIR: Path = BOT_DIR / "scripts"
