"""
Notifications toast (popup non-modal en bas-droite de la fenêtre).

Empile plusieurs toasts qui glissent automatiquement vers le haut quand un
nouveau apparaît. Auto-dismiss après 5 secondes. Désactivable globalement
depuis l'onglet Paramètres (`notifications_enabled` dans config.json).

Usage (depuis n'importe quel thread) :
    from dashboard import notifications
    notifications.show("Combat gagné", level="success")
    notifications.show("Surpoids — banque", level="warning")
    notifications.show("Kick serveur", level="error")
"""

from __future__ import annotations

import logging
import threading
import tkinter as tk
from typing import Literal

logger = logging.getLogger(__name__)

# Couleurs par niveau (fond, texte)
_COLORS: dict[str, tuple[str, str]] = {
    "info":    ("#1f6feb", "#ffffff"),
    "success": ("#238636", "#ffffff"),
    "warning": ("#d29922", "#000000"),
    "error":   ("#da3633", "#ffffff"),
}

_TOAST_W = 320
_TOAST_H = 56
_TOAST_PAD = 8
_TOAST_TTL_MS = 5000

# Globaux protégés par lock
_lock = threading.Lock()
_root: tk.Tk | None = None
_enabled: bool = True
_active_toasts: list["_Toast"] = []


def init(root: tk.Tk, enabled: bool = True) -> None:
    """Initialiser le système de notifications. Appelé une fois depuis gui.py."""
    global _root, _enabled
    with _lock:
        _root = root
        _enabled = enabled


def set_enabled(enabled: bool) -> None:
    """Activer/désactiver les notifications à chaud."""
    global _enabled
    with _lock:
        _enabled = bool(enabled)


def is_enabled() -> bool:
    with _lock:
        return _enabled


def show(message: str, level: Literal["info", "success", "warning", "error"] = "info") -> None:
    """Afficher une notification toast (thread-safe).

    Si les notifications sont désactivées, ne fait rien.
    """
    with _lock:
        if not _enabled or _root is None:
            return
        root = _root
    try:
        root.after(0, lambda m=message, lv=level: _spawn(m, lv))
    except Exception as exc:
        logger.debug("[notifications] show failed: %s", exc)


class _Toast:
    """Une bulle de notification individuelle."""

    def __init__(self, parent: tk.Tk, message: str, level: str) -> None:
        bg, fg = _COLORS.get(level, _COLORS["info"])
        self.win = tk.Toplevel(parent)
        self.win.overrideredirect(True)
        self.win.attributes("-topmost", True)
        try:
            self.win.attributes("-alpha", 0.95)
        except tk.TclError:
            pass
        self.win.configure(bg=bg)

        frame = tk.Frame(self.win, bg=bg, padx=12, pady=8)
        frame.pack(fill="both", expand=True)

        icon = {"info": "ℹ", "success": "✓", "warning": "⚠", "error": "✕"}.get(level, "ℹ")
        tk.Label(
            frame, text=icon, bg=bg, fg=fg,
            font=("", 14, "bold"),
        ).pack(side="left", padx=(0, 8))

        tk.Label(
            frame, text=message, bg=bg, fg=fg,
            font=("", 9), wraplength=_TOAST_W - 60, justify="left",
            anchor="w",
        ).pack(side="left", fill="x", expand=True)

        # Click pour fermer
        for w in (self.win, frame):
            w.bind("<Button-1>", lambda _e: self.close())

        self._closed = False

    def place_at(self, x: int, y: int) -> None:
        self.win.geometry(f"{_TOAST_W}x{_TOAST_H}+{x}+{y}")

    def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        try:
            self.win.destroy()
        except tk.TclError:
            pass
        _remove(self)


def _spawn(message: str, level: str) -> None:
    """Créer un nouveau toast et le positionner. Doit tourner sur le thread Tk."""
    global _root
    if _root is None:
        return
    toast = _Toast(_root, message, level)
    _active_toasts.append(toast)
    _reposition_all()
    _root.after(_TOAST_TTL_MS, toast.close)


def _remove(toast: _Toast) -> None:
    if toast in _active_toasts:
        _active_toasts.remove(toast)
        _reposition_all()


def _reposition_all() -> None:
    """Repositionner tous les toasts actifs (du plus récent en bas)."""
    global _root
    if _root is None or not _active_toasts:
        return
    try:
        screen_w = _root.winfo_screenwidth()
        screen_h = _root.winfo_screenheight()
    except tk.TclError:
        return

    base_x = screen_w - _TOAST_W - 20
    base_y = screen_h - _TOAST_H - 60  # marge taskbar

    for i, t in enumerate(reversed(_active_toasts)):
        y = base_y - i * (_TOAST_H + _TOAST_PAD)
        try:
            t.place_at(base_x, y)
        except tk.TclError:
            pass
