"""
Sous-onglet « Autopilote » — voyage automatique vers une coordonnée (X, Y).

S'appuie sur bot/autopilot.py (planification BFS sur le graphe monde +
exécution adaptative via mapnav.change_map). Le voyage natif du serveur étant
verrouillé (cf. mémoire project_autopilot_natif), on utilise notre propre
moteur, 100 % côté proxy.

Thread tkinter : ne lit JAMAIS game.state.current. Lit self._session.game_state
pour l'affichage ; lance/arrête via launch_in_session / call_in_session.
"""

from __future__ import annotations

import time
import tkinter as tk
from tkinter import ttk


class AutopilotTestTab:
    MAX_LINES = 150

    def __init__(self, notebook: ttk.Notebook, root: tk.Tk, session) -> None:
        self._root = root
        self._session = session
        self._bridge = session.bridge
        self._frame = ttk.Frame(notebook)
        notebook.add(self._frame, text="Autopilote")

        self._build()
        # Afficher en direct les lignes d'autopilote (publiées via bridge.add_console).
        self._bridge.subscribe("console", lambda d: root.after(0, self._on_console, d))
        self._frame.bind("<Visibility>", lambda _e: self._refresh_position())

    # ------------------------------------------------------------------ UI
    def _build(self) -> None:
        info = ttk.LabelFrame(self._frame, text="Position courante")
        info.pack(fill="x", padx=8, pady=(8, 4))
        self._pos_var = tk.StringVar(value="—")
        ttk.Label(info, textvariable=self._pos_var, font=("Consolas", 10)).pack(side="left", padx=8, pady=4)
        ttk.Button(info, text="↻ Rafraîchir", command=self._refresh_position, width=12).pack(side="right", padx=8, pady=4)

        cfg = ttk.LabelFrame(self._frame, text="Destination (coordonnées du monde)")
        cfg.pack(fill="x", padx=8, pady=4)
        row = ttk.Frame(cfg)
        row.pack(fill="x", padx=8, pady=6)
        ttk.Label(row, text="X :").pack(side="left")
        self._x_var = tk.IntVar(value=0)
        ttk.Spinbox(row, from_=-200, to=200, width=6, textvariable=self._x_var).pack(side="left", padx=(2, 12))
        ttk.Label(row, text="Y :").pack(side="left")
        self._y_var = tk.IntVar(value=0)
        ttk.Spinbox(row, from_=-200, to=200, width=6, textvariable=self._y_var).pack(side="left", padx=(2, 12))
        ttk.Button(row, text="⟲ = position", command=self._target_here, width=12).pack(side="left", padx=4)

        act = ttk.Frame(self._frame)
        act.pack(fill="x", padx=8, pady=(2, 6))
        ttk.Button(act, text="🔎 Aperçu trajet", command=self._preview, width=16).pack(side="left", padx=2)
        ttk.Button(act, text="▶ Voyager", command=self._travel, width=12).pack(side="left", padx=2)
        ttk.Button(act, text="⏹ Stop", command=self._stop, width=8).pack(side="left", padx=2)

        log_frame = ttk.LabelFrame(self._frame, text="Journal")
        log_frame.pack(fill="both", expand=True, padx=8, pady=4)
        self._text = tk.Text(
            log_frame, state="disabled", wrap="word", height=12,
            font=("Consolas", 9), bg="#1e1e1e", fg="#d4d4d4",
        )
        sb = ttk.Scrollbar(log_frame, orient="vertical", command=self._text.yview)
        self._text.configure(yscrollcommand=sb.set)
        self._text.pack(side="left", fill="both", expand=True)
        sb.pack(side="right", fill="y")
        self._text.tag_configure("ts", foreground="#569cd6")
        self._text.tag_configure("ok", foreground="#4CAF50")
        self._text.tag_configure("err", foreground="#f85149")

    # ------------------------------------------------------------------ Helpers
    def _current_coord(self):
        """(map_id, x, y) de la session, ou (None, None, None). Sûr depuis le thread UI."""
        gs = self._session.game_state
        cm = getattr(gs, "current_map", None)
        if cm is None:
            return None, None, None
        from bot.mapdata import load_map
        mi = load_map(cm.map_id)
        if mi is None:
            return cm.map_id, None, None
        return cm.map_id, mi.x, mi.y

    def _refresh_position(self) -> None:
        map_id, x, y = self._current_coord()
        if map_id is None:
            self._pos_var.set("Pas connecté / pas de carte chargée")
        elif x is None:
            self._pos_var.set(f"Map #{map_id} (XML absent — coords inconnues)")
        else:
            self._pos_var.set(f"Map #{map_id}  →  ({x}, {y})")

    def _target_here(self) -> None:
        _map_id, x, y = self._current_coord()
        if x is not None:
            self._x_var.set(x)
            self._y_var.set(y)

    def _log(self, msg: str, tag: str = "") -> None:
        ts = time.strftime("%H:%M:%S")
        self._text.configure(state="normal")
        self._text.insert("1.0", msg + "\n", tag or "ts")
        self._text.insert("1.0", f"[{ts}] ", "ts")
        if int(self._text.index("end-1c").split(".")[0]) > self.MAX_LINES:
            self._text.delete(f"{self.MAX_LINES + 1}.0", "end")
        self._text.configure(state="disabled")

    def _on_console(self, entry: dict) -> None:
        """Relayer dans le journal les lignes liées à l'autopilote."""
        msg = entry.get("msg", "")
        if "utopilote" in msg or "🧭" in msg:
            tag = "ok" if ("✅" in msg or "arrivé" in msg) else ("err" if "échec" in msg or "aucun chemin" in msg else "")
            self._log(msg, tag)

    # ------------------------------------------------------------------ Actions
    def _preview(self) -> None:
        map_id, x, y = self._current_coord()
        if x is None:
            self._log("Aperçu impossible : position inconnue.", "err")
            return
        from bot import autopilot
        route = autopilot.preview_route((x, y), (self._x_var.get(), self._y_var.get()))
        if route is None:
            self._log(f"Aucun chemin de ({x},{y}) vers ({self._x_var.get()},{self._y_var.get()}) "
                      f"— île ou zaap requis.", "err")
        elif not route:
            self._log("Déjà sur la destination.", "ok")
        else:
            self._log(f"Trajet : {len(route)} sauts → {route}", "ok")

    def _travel(self) -> None:
        x, y = self._x_var.get(), self._y_var.get()
        self._log(f"▶ Voyage vers ({x},{y})…")
        from core.session import launch_in_session
        from bot import autopilot

        async def _run() -> None:
            await autopilot.travel_to(x, y)

        try:
            launch_in_session(self._session, _run)
        except Exception as exc:
            self._log(f"Erreur launch : {exc}", "err")

    def _stop(self) -> None:
        from core.session import call_in_session
        from bot import autopilot
        try:
            call_in_session(self._session, autopilot.request_stop)
            self._log("⏹ Arrêt demandé.")
        except Exception as exc:
            self._log(f"Erreur stop : {exc}", "err")
