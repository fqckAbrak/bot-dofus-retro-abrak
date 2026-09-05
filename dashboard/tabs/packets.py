"""
Sous-onglet Packets — inspecteur réseau façon sniffer.

Affiche les packets reçus (RECV = S→C) et envoyés (SENT = C→S) au fil de
l'eau, avec colonnes DIR / TYPE / PID / NAME / LEN / TIME / CONTENT.
Alimenté par le topic "packets" du bridge de la session (cf. proxy/relay.py).
"""

from __future__ import annotations

import tkinter as tk
from tkinter import ttk


class PacketsTab:
    """Tableau temps réel des packets réseau d'une team."""

    MAX_ROWS = 500

    def __init__(self, notebook: ttk.Notebook, root: tk.Tk, session) -> None:
        self._root = root
        self._session = session
        self._bridge = session.bridge
        self._paused = False
        self._filter = ""
        self._count = 0

        self._frame = ttk.Frame(notebook)
        notebook.add(self._frame, text="Packets")
        self._build()
        self._bridge.subscribe("packets", lambda d: root.after(0, self._on_packet, d))

    # -- construction UI ---------------------------------------------------
    def _build(self) -> None:
        header = ttk.Frame(self._frame)
        header.pack(fill="x", padx=8, pady=(8, 2))

        ttk.Label(header, text="Packets", font=("", 11, "bold")).pack(side="left")

        self._count_var = tk.StringVar(value="0 packet(s)")
        ttk.Label(header, textvariable=self._count_var,
                  font=("", 9, "italic")).pack(side="left", padx=(8, 0))

        ttk.Button(header, text="Effacer", command=self._clear).pack(side="right", padx=4)

        self._pause_var = tk.BooleanVar(value=False)
        ttk.Checkbutton(header, text="Pause", variable=self._pause_var,
                        command=self._toggle_pause).pack(side="right", padx=4)

        # Barre de filtre
        filter_row = ttk.Frame(self._frame)
        filter_row.pack(fill="x", padx=8, pady=(0, 4))
        ttk.Label(filter_row, text="Filtre :").pack(side="left")
        self._filter_var = tk.StringVar()
        entry = ttk.Entry(filter_row, textvariable=self._filter_var)
        entry.pack(side="left", fill="x", expand=True, padx=(4, 4))
        entry.bind("<KeyRelease>", lambda _e: self._apply_filter())
        ttk.Label(filter_row, text="(type / nom / contenu)",
                  foreground="#8b949e", font=("", 8, "italic")).pack(side="left")

        # Tableau
        table_frame = ttk.Frame(self._frame)
        table_frame.pack(fill="both", expand=True, padx=8, pady=4)

        cols = ("dir", "type", "pid", "name", "len", "time", "content")
        self._tree = ttk.Treeview(table_frame, columns=cols, show="headings")
        self._tree.heading("dir",     text="DIR")
        self._tree.heading("type",    text="TYPE")
        self._tree.heading("pid",     text="PID")
        self._tree.heading("name",    text="NAME")
        self._tree.heading("len",     text="LEN")
        self._tree.heading("time",    text="TIME")
        self._tree.heading("content", text="CONTENT")
        self._tree.column("dir",     width=55,  anchor="center", stretch=False)
        self._tree.column("type",    width=55,  anchor="center", stretch=False)
        self._tree.column("pid",     width=45,  anchor="center", stretch=False)
        self._tree.column("name",    width=170, stretch=False)
        self._tree.column("len",     width=50,  anchor="e",      stretch=False)
        self._tree.column("time",    width=75,  anchor="center", stretch=False)
        self._tree.column("content", width=420, stretch=True)

        sb = ttk.Scrollbar(table_frame, orient="vertical", command=self._tree.yview)
        self._tree.configure(yscrollcommand=sb.set)
        self._tree.pack(side="left", fill="both", expand=True)
        sb.pack(side="right", fill="y")

        # Couleurs par direction (RECV vert, SENT orange)
        self._tree.tag_configure("recv", foreground="#3fb950")
        self._tree.tag_configure("sent", foreground="#d29922")

        # Police monospace pour le contenu lisible
        self._tree.configure(style="Packets.Treeview")
        style = ttk.Style(self._root)
        style.configure("Packets.Treeview", font=("Consolas", 9), rowheight=20)

    # -- évènements --------------------------------------------------------
    def _on_packet(self, entry: dict) -> None:
        self._count += 1
        self._count_var.set(f"{self._count} packet(s)")
        if self._paused or not self._matches(entry):
            return
        self._insert(entry)

    def _insert(self, entry: dict) -> None:
        direction = entry.get("direction", "")
        tag = "recv" if direction == "RECV" else "sent"
        self._tree.insert(
            "", 0,
            values=(
                direction,
                entry.get("msg_id", ""),
                entry.get("pid", ""),
                entry.get("msg_name", ""),
                entry.get("length", ""),
                entry.get("ts", ""),
                entry.get("content", ""),
            ),
            tags=(tag,),
        )
        children = self._tree.get_children()
        if len(children) > self.MAX_ROWS:
            self._tree.delete(*children[self.MAX_ROWS:])

    def _matches(self, entry: dict) -> bool:
        if not self._filter:
            return True
        f = self._filter.lower()
        return (
            f in str(entry.get("msg_id", "")).lower()
            or f in str(entry.get("msg_name", "")).lower()
            or f in str(entry.get("content", "")).lower()
        )

    # -- contrôles ---------------------------------------------------------
    def _toggle_pause(self) -> None:
        self._paused = self._pause_var.get()

    def _apply_filter(self) -> None:
        self._filter = self._filter_var.get().strip()
        self._tree.delete(*self._tree.get_children())
        for record in reversed(self._bridge.get_state().get("packets", [])):
            if self._matches(record):
                self._insert(record)

    def _clear(self) -> None:
        self._tree.delete(*self._tree.get_children())
        self._count = 0
        self._count_var.set("0 packet(s)")
