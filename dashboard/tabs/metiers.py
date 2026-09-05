"""
Onglet Métiers — niveaux et XP des métiers du personnage.
"""

from __future__ import annotations

import tkinter as tk
from tkinter import ttk

from dashboard import bridge


class MetiersTab:
    """Onglet affichant les métiers et leurs niveaux."""

    def __init__(self, notebook: ttk.Notebook, root: tk.Tk, session) -> None:
        self._root = root
        self._session = session
        self._bridge = session.bridge
        self._frame = ttk.Frame(notebook)
        notebook.add(self._frame, text="Métiers")
        self._build()
        self._bridge.subscribe("jobs", lambda d: root.after(0, self._refresh, d))

    def _build(self) -> None:
        ttk.Label(self._frame, text="Métiers", font=("", 11, "bold")).pack(
            anchor="w", padx=12, pady=(8, 4)
        )

        cols = ("name", "level", "xp", "upper_xp", "progress")
        self._tree = ttk.Treeview(self._frame, columns=cols, show="headings", height=15)
        self._tree.heading("name",     text="Métier")
        self._tree.heading("level",    text="Niveau")
        self._tree.heading("xp",       text="XP")
        self._tree.heading("upper_xp", text="XP Palier")
        self._tree.heading("progress", text="Progression")
        self._tree.column("name",     width=150)
        self._tree.column("level",    width=60,  anchor="center")
        self._tree.column("xp",       width=90,  anchor="e")
        self._tree.column("upper_xp", width=90,  anchor="e")
        self._tree.column("progress", width=120, anchor="center")

        sb = ttk.Scrollbar(self._frame, orient="vertical", command=self._tree.yview)
        self._tree.configure(yscrollcommand=sb.set)
        self._tree.pack(side="left", fill="both", expand=True, padx=(12, 0), pady=4)
        sb.pack(side="right", fill="y", pady=4, padx=(0, 8))

    def _refresh(self, jobs: list[dict]) -> None:
        self._tree.delete(*self._tree.get_children())
        for job in sorted(jobs, key=lambda j: (-j.get("level", 0), j.get("name", ""))):
            xp = job.get("xp", 0)
            upper = job.get("upper_xp", 0)
            if upper > 0:
                pct = min(100, int(xp * 100 / upper))
                progress = f"{pct}%"
            else:
                progress = "—"
            self._tree.insert("", "end", values=(
                job.get("name", f"#{job.get('job_id', '?')}"),
                job.get("level", "?"),
                f"{xp:,}",
                f"{upper:,}" if upper else "—",
                progress,
            ))
