"""
Onglet Console — conteneur de deux sous-onglets :
  - « Console » : log scrollant des événements de jeu + navigation map.
  - « Packets » : inspecteur réseau (packets reçus / envoyés).
"""

from __future__ import annotations

import tkinter as tk
from tkinter import ttk
import bot as _bot

from dashboard import bridge
from dashboard.tabs.packets import PacketsTab


class ConsoleTab:
    """Onglet « Console » regroupant les sous-onglets Console et Packets."""

    MAX_LINES = 200

    def __init__(self, notebook: ttk.Notebook, root: tk.Tk, session) -> None:
        self._root = root
        self._session = session
        self._bridge = session.bridge

        # Onglet principal -> sous-notebook
        self._frame = ttk.Frame(notebook)
        notebook.add(self._frame, text="Console")

        self._inner = ttk.Notebook(self._frame)
        self._inner.pack(fill="both", expand=True, padx=2, pady=2)

        # Sous-onglet « Console »
        self._console_frame = ttk.Frame(self._inner)
        self._inner.add(self._console_frame, text="Console")
        self._build(self._console_frame)
        self._bridge.subscribe("console", lambda d: root.after(0, self._on_entry, d))

        # Sous-onglet « Packets »
        PacketsTab(self._inner, root, session)

    def _build(self, parent: ttk.Frame) -> None:
        header = ttk.Frame(parent)
        header.pack(fill="x", padx=8, pady=(8, 2))
        ttk.Label(header, text="Console", font=("", 11, "bold")).pack(side="left")

        btn_clear = ttk.Button(header, text="Effacer", command=self._clear)
        btn_clear.pack(side="right", padx=4)

        # --- Navigation map ---
        nav_frame = ttk.LabelFrame(parent, text="Navigation map")
        nav_frame.pack(fill="x", padx=8, pady=(0, 4))

        # Grille 3×3 centrée : boutons aux positions (0,1) haut, (1,0) gauche, (1,2) droite, (2,1) bas
        grid = ttk.Frame(nav_frame)
        grid.pack(pady=4)

        ttk.Button(grid, text="↑ Haut",   command=lambda: self._change_map("top"),    width=10).grid(row=0, column=1, padx=2, pady=2)
        ttk.Button(grid, text="← Gauche", command=lambda: self._change_map("left"),   width=10).grid(row=1, column=0, padx=2, pady=2)
        ttk.Button(grid, text="→ Droite", command=lambda: self._change_map("right"),  width=10).grid(row=1, column=2, padx=2, pady=2)
        ttk.Button(grid, text="↓ Bas",    command=lambda: self._change_map("bottom"), width=10).grid(row=2, column=1, padx=2, pady=2)

        text_frame = ttk.Frame(parent)
        text_frame.pack(fill="both", expand=True, padx=8, pady=4)

        self._text = tk.Text(
            text_frame,
            state="disabled",
            wrap="word",
            font=("Consolas", 9),
            bg="#1e1e1e",
            fg="#d4d4d4",
            insertbackground="white",
        )
        sb = ttk.Scrollbar(text_frame, orient="vertical", command=self._text.yview)
        self._text.configure(yscrollcommand=sb.set)
        self._text.pack(side="left", fill="both", expand=True)
        sb.pack(side="right", fill="y")

        # Tags couleur pour les niveaux de log
        self._text.tag_configure("ts",   foreground="#569cd6")
        self._text.tag_configure("msg",  foreground="#d4d4d4")

    def _on_entry(self, entry: dict) -> None:
        """Ajouter une entrée en haut du log."""
        ts = entry.get("ts", "")
        msg = entry.get("msg", "")
        self._text.configure(state="normal")
        self._text.insert("1.0", msg + "\n", "msg")
        self._text.insert("1.0", f"[{ts}] ", "ts")
        # Supprimer les lignes en excès
        line_count = int(self._text.index("end-1c").split(".")[0])
        if line_count > self.MAX_LINES:
            self._text.delete(f"{self.MAX_LINES + 1}.0", "end")
        self._text.configure(state="disabled")

    def _clear(self) -> None:
        self._text.configure(state="normal")
        self._text.delete("1.0", "end")
        self._text.configure(state="disabled")

    def _change_map(self, direction: str) -> None:
        """Déclencher un changement de map dans la direction donnée (cette team)."""
        from bot import mapnav
        from core.session import launch_in_session

        try:
            launch_in_session(self._session, lambda: mapnav.change_map(direction))
        except Exception as exc:
            self._bridge.add_console(f"Nav : erreur ({exc})")
