"""
Status bar globale en haut de la fenêtre principale.

Affiche en permanence un résumé : connexion proxy, état Flash, serveur cible,
map courante, perso actif, statut combat (en combat / hors combat / bot actif).
S'abonne aux topics du bridge pour rester à jour.
"""

from __future__ import annotations

import tkinter as tk
from tkinter import ttk

from dashboard import bridge


_DOT = "●"

_COLORS = {
    "ok":      "#3fb950",
    "warn":    "#d29922",
    "ko":      "#f85149",
    "neutral": "#8b949e",
}


class StatusBar:
    """Barre de status compacte au-dessus du notebook."""

    def __init__(self, parent: tk.Misc, root: tk.Tk, session) -> None:
        self._root = root
        self._session = session

        self._frame = tk.Frame(parent, bg="#161b22", height=28)
        self._frame.pack(fill="x", side="top")
        self._frame.pack_propagate(False)

        # --- Connexion proxy / Flash ---
        self._conn_var = tk.StringVar(value="● Flash : déconnecté")
        self._conn_lbl = tk.Label(
            self._frame, textvariable=self._conn_var,
            bg="#161b22", fg=_COLORS["neutral"],
            font=("", 9), padx=10,
        )
        self._conn_lbl.pack(side="left")

        self._sep1 = tk.Label(self._frame, text="│", bg="#161b22", fg="#30363d")
        self._sep1.pack(side="left")

        # --- Perso actif ---
        self._char_var = tk.StringVar(value="Perso : —")
        tk.Label(
            self._frame, textvariable=self._char_var,
            bg="#161b22", fg="#c9d1d9", font=("", 9), padx=10,
        ).pack(side="left")

        self._sep2 = tk.Label(self._frame, text="│", bg="#161b22", fg="#30363d")
        self._sep2.pack(side="left")

        # --- Map courante ---
        self._map_var = tk.StringVar(value="Map : —")
        tk.Label(
            self._frame, textvariable=self._map_var,
            bg="#161b22", fg="#c9d1d9", font=("", 9), padx=10,
        ).pack(side="left")

        # --- Combat / bot (droite) ---
        self._combat_var = tk.StringVar(value="● Hors combat")
        self._combat_lbl = tk.Label(
            self._frame, textvariable=self._combat_var,
            bg="#161b22", fg=_COLORS["neutral"], font=("", 9), padx=10,
        )
        self._combat_lbl.pack(side="right")

        self._sep3 = tk.Label(self._frame, text="│", bg="#161b22", fg="#30363d")
        self._sep3.pack(side="right")

        self._bot_var = tk.StringVar(value="● Bot inactif")
        self._bot_lbl = tk.Label(
            self._frame, textvariable=self._bot_var,
            bg="#161b22", fg=_COLORS["neutral"], font=("", 9), padx=10,
        )
        self._bot_lbl.pack(side="right")

        self._subscribe()
        self._root.after(1000, self._poll_state)

    def _subscribe(self) -> None:
        self._session.bridge.subscribe("character", lambda d: self._root.after(0, self._on_character, d))
        self._session.bridge.subscribe("map",       lambda d: self._root.after(0, self._on_map, d))

    def _on_character(self, data: dict) -> None:
        pseudo = data.get("pseudo")
        level = data.get("level")
        if pseudo:
            self._char_var.set(f"Perso : {pseudo} (Nv.{level})")
            self._set_conn(connected=True)

    def _on_map(self, data: dict) -> None:
        map_id = data.get("map_id")
        if map_id is not None:
            self._map_var.set(f"Map : #{map_id}")

    def _set_conn(self, connected: bool) -> None:
        if connected:
            self._conn_var.set("● Flash : connecté")
            self._conn_lbl.configure(fg=_COLORS["ok"])
        else:
            self._conn_var.set("● Flash : déconnecté")
            self._conn_lbl.configure(fg=_COLORS["ko"])

    def _poll_state(self) -> None:
        """Poll le GameState et le bot de CETTE session pour le statut combat/bot."""
        s = self._session
        try:
            in_combat = bool(s.game_state.in_combat)
        except Exception:
            in_combat = False

        if in_combat:
            self._combat_var.set("● En combat")
            self._combat_lbl.configure(fg=_COLORS["warn"])
        else:
            self._combat_var.set("● Hors combat")
            self._combat_lbl.configure(fg=_COLORS["neutral"])

        # Bot actif ? (boucles de cette session)
        bot_active = False
        try:
            from bot.combat import is_running as _combat_running
            bot_active = bool(_combat_running(s))
        except Exception:
            pass
        if not bot_active:
            try:
                from bot.harvester import is_running as _harv_running
                bot_active = bool(_harv_running(s))
            except Exception:
                pass
        if not bot_active:
            try:
                from bot.script_engine import is_running as _scr_running
                bot_active = bool(_scr_running(s))
            except Exception:
                pass

        if bot_active:
            self._bot_var.set("● Bot actif")
            self._bot_lbl.configure(fg=_COLORS["ok"])
        else:
            self._bot_var.set("● Bot inactif")
            self._bot_lbl.configure(fg=_COLORS["neutral"])

        # Connexion : état du writer de la session
        try:
            ch = s.channel
            connected = (ch.writer is not None and not ch.writer.is_closing()
                         and ch.is_game_connection)
            if not connected:
                self._set_conn(False)
        except Exception:
            pass

        self._root.after(1500, self._poll_state)
