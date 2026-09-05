"""
Référence à la boucle asyncio principale.
Stockée dans main.py avant le démarrage de la GUI tkinter,
utilisée par la GUI pour call_soon_threadsafe.
"""

from __future__ import annotations
import asyncio

_main_loop: asyncio.AbstractEventLoop | None = None


def set_main_loop(loop: asyncio.AbstractEventLoop) -> None:
    global _main_loop
    _main_loop = loop


def get_main_loop() -> asyncio.AbstractEventLoop:
    if _main_loop is None:
        raise RuntimeError("La boucle asyncio principale n'a pas été enregistrée.")
    return _main_loop
