"""
Onglet Inventaire — vue complète de l'inventaire en 4 familles.

L'inventaire complet est récupéré via le patch core.swf (message ZI→ZO, cf.
bot/inventory.py) : le bot le demande automatiquement quand on ouvre l'onglet
(throttlé) et avant une vente. Les objets sont classés par data.items_db en
Équipements / Consommables / Ressources / Autres.

Le poids (Ow) reste affiché en haut.
"""

from __future__ import annotations

import time
import tkinter as tk
from tkinter import ttk

from data import items_db

_CATEGORIES = [
    ("equipement",  "Équipements"),
    ("consommable", "Consommables"),
    ("ressource",   "Ressources"),
    ("autre",       "Autres"),
]
_AUTO_THROTTLE = 4.0  # secondes mini entre deux fetch auto


class InventaireTab:
    def __init__(self, notebook: ttk.Notebook, root: tk.Tk, session) -> None:
        self._root = root
        self._session = session
        self._bridge = session.bridge
        self._frame = ttk.Frame(notebook)
        notebook.add(self._frame, text="Inventaire")

        self._trees: dict[str, ttk.Treeview] = {}
        self._tab_frames: dict[str, ttk.Frame] = {}
        self._last_auto = 0.0
        self._build()

        self._bridge.subscribe("inventory", lambda d: root.after(0, self._refresh, d))
        self._bridge.subscribe("weight",    lambda d: root.after(0, self._refresh_weight, d))
        # Fetch auto quand l'onglet devient visible (throttlé).
        self._frame.bind("<Visibility>", self._on_visible)

    # ------------------------------------------------------------------ UI
    def _build(self) -> None:
        # --- Barre du haut : poids + bouton refresh ---
        top = ttk.Frame(self._frame)
        top.pack(fill="x", padx=8, pady=(8, 4))

        self._weight_var = tk.StringVar(value="Poids : — / —")
        ttk.Label(top, textvariable=self._weight_var, font=("Consolas", 10, "bold")).pack(side="left")
        self._weight_canvas = tk.Canvas(top, width=200, height=14, bg="#21262d",
                                        highlightthickness=1, highlightbackground="#30363d")
        self._weight_canvas.pack(side="left", padx=8)
        self._weight_rect = self._weight_canvas.create_rectangle(0, 0, 0, 14, fill="#3fb950", outline="")

        ttk.Button(top, text="↻ Rafraîchir", command=self._manual_refresh, width=12).pack(side="right")
        self._total_var = tk.StringVar(value="")
        ttk.Label(top, textvariable=self._total_var, font=("", 9, "italic")).pack(side="right", padx=8)

        if not items_db.is_loaded():
            ttk.Label(self._frame, text="Base d'items absente — lancez tools/extract_items.py",
                      foreground="#f85149").pack(pady=20)
            return

        # --- Notebook 4 familles ---
        nb = ttk.Notebook(self._frame)
        nb.pack(fill="both", expand=True, padx=8, pady=4)
        for key, label in _CATEGORIES:
            frame = ttk.Frame(nb)
            nb.add(frame, text=label)
            self._tab_frames[key] = frame
            tree = ttk.Treeview(frame, columns=("name", "level", "type", "qty"),
                                show="headings")
            for col, txt, w, anchor in (("name", "Objet", 230, "w"), ("level", "Niv.", 50, "center"),
                                        ("type", "Type", 130, "w"), ("qty", "Qté", 60, "center")):
                tree.heading(col, text=txt)
                tree.column(col, width=w, anchor=anchor)
            sb = ttk.Scrollbar(frame, orient="vertical", command=tree.yview)
            tree.configure(yscrollcommand=sb.set)
            tree.pack(side="left", fill="both", expand=True, padx=(4, 0), pady=4)
            sb.pack(side="right", fill="y", pady=4)
            self._trees[key] = tree

    # -------------------------------------------------------------- refresh
    def _refresh(self, items: list[dict]) -> None:
        if not self._trees:
            return
        # Regrouper par gid (somme des quantités) pour une vue lisible.
        groups: dict[str, dict] = {cat: {} for cat, _ in _CATEGORIES}
        for it in items:
            gid = it.get("gid")
            if gid is None:
                continue
            cat = items_db.classify(gid)
            g = groups[cat].setdefault(gid, {"qty": 0})
            g["qty"] += int(it.get("qty", 1) or 1)

        total = 0
        for cat, _label in _CATEGORIES:
            tree = self._trees[cat]
            tree.delete(*tree.get_children())
            rows = []
            for gid, g in groups[cat].items():
                rows.append((
                    items_db.get_name(gid) or f"#{gid}",
                    items_db.get_level(gid),
                    items_db.get_type_name(gid) or "?",
                    g["qty"],
                ))
            rows.sort(key=lambda r: (r[2], r[0]))  # par type puis nom
            for r in rows:
                tree.insert("", "end", values=r)
            total += len(rows)
            # nb d'objets distincts par famille dans le titre de l'onglet
        self._total_var.set(f"{total} type(s) d'objet, {len(items)} objet(s)")

    def _refresh_weight(self, data: dict) -> None:
        cur, mx = data.get("current", 0), data.get("max", 0)
        if mx <= 0:
            self._weight_var.set("Poids : — / —")
            self._weight_canvas.coords(self._weight_rect, 0, 0, 0, 14)
            return
        pct = cur / mx
        self._weight_var.set(f"Poids : {cur} / {mx} ({int(pct*100)}%)")
        color = "#f85149" if pct >= 1 else "#d29922" if pct >= 0.8 else "#3fb950"
        self._weight_canvas.itemconfig(self._weight_rect, fill=color)
        self._weight_canvas.coords(self._weight_rect, 0, 0, int(200 * min(pct, 1.0)), 14)

    # ----------------------------------------------------------- auto-fetch
    def _on_visible(self, _event) -> None:
        if time.time() - self._last_auto >= _AUTO_THROTTLE:
            self._auto_fetch()

    def _manual_refresh(self) -> None:
        self._auto_fetch(force=True)

    def _auto_fetch(self, force: bool = False) -> None:
        self._last_auto = time.time()
        try:
            from core.session import launch_in_session
            from bot.inventory import request_full_inventory
            launch_in_session(self._session, lambda: request_full_inventory(timeout=5.0))
        except Exception:
            pass
