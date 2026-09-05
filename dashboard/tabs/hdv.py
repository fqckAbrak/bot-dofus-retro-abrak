"""
Onglet « HDV » — outils liés à l'hôtel de vente, dans un sous-notebook.

Sous-onglets :
  * Achat/Revente — placeholder (fonctionnalité à venir).
  * Recherche dragodinde — scanne les certificats de monture de l'HDV à la
    recherche de dragodindes NON castrées (mises en vente par erreur : les
    éleveurs castrent normalement avant de vendre). Choix de la couleur via
    dropdown, rapport (nombre + prix) dans un tableau + journal.

Thread tkinter : ne lit jamais game.state.current — le scan s'exécute via
launch_in_session dans le contexte de la session ; les retours UI passent par
root.after (cf. misc_vendre.py pour le même motif).
"""

from __future__ import annotations

import tkinter as tk
from tkinter import ttk

from data import mounts_db

_ALL_LABEL = "— Toutes les dragodindes —"


class HdvTab:
    """Onglet conteneur des fonctionnalités HDV (sous-onglets)."""

    def __init__(self, notebook: ttk.Notebook, root: tk.Tk, session) -> None:
        self._root = root
        self._session = session
        self._frame = ttk.Frame(notebook)
        notebook.add(self._frame, text="HDV")

        self._sub = ttk.Notebook(self._frame)
        self._sub.pack(fill="both", expand=True, padx=2, pady=2)

        # Sous-onglets
        self._achat_revente = AchatReventeTab(self._sub, root, session)
        self._recherche = RechercheDragodindeTab(self._sub, root, session)


class AchatReventeTab:
    """Sous-onglet « Achat/Revente » — vide pour l'instant (à venir)."""

    def __init__(self, notebook: ttk.Notebook, root: tk.Tk, session) -> None:
        self._frame = ttk.Frame(notebook)
        notebook.add(self._frame, text="Achat/Revente")
        ttk.Label(
            self._frame,
            text="Achat/Revente automatique — à venir.",
            foreground="#8b949e", anchor="center", justify="center",
        ).pack(expand=True, pady=40)


class RechercheDragodindeTab:
    """Sous-onglet « Recherche dragodinde » — scan des certificats HDV."""

    def __init__(self, notebook: ttk.Notebook, root: tk.Tk, session) -> None:
        self._root = root
        self._session = session
        self._frame = ttk.Frame(notebook)
        notebook.add(self._frame, text="Recherche dragodinde")

        self._busy = False
        # label affiché → model_id (trié par nom pour le dropdown)
        self._models_by_label = {
            name: mid for mid, name in sorted(
                mounts_db.DRAGODINDE_MODELS.items(), key=lambda kv: kv[1])
        }
        self._build()

    # ------------------------------------------------------------------ UI
    def _build(self) -> None:
        top = ttk.LabelFrame(self._frame, text="Recherche")
        top.pack(fill="x", padx=8, pady=(8, 4))

        row = ttk.Frame(top)
        row.pack(fill="x", padx=8, pady=6)
        ttk.Label(row, text="Dragodinde :").pack(side="left")
        self._model_var = tk.StringVar(value=_ALL_LABEL)
        self._combo = ttk.Combobox(
            row, textvariable=self._model_var, state="readonly", width=36,
            values=[_ALL_LABEL] + list(self._models_by_label),
        )
        self._combo.pack(side="left", padx=(4, 12))

        self._scan_btn = ttk.Button(row, text="▶ Scanner l'HDV", command=self._scan, width=16)
        self._scan_btn.pack(side="left", padx=2)
        self._turquoise_btn = ttk.Button(
            row, text="▶ Scanner les turquoises", command=self._scan_turquoise, width=22)
        self._turquoise_btn.pack(side="left", padx=2)
        self._stop_btn = ttk.Button(row, text="■ Stop", command=self._stop, width=8,
                                    state="disabled")
        self._stop_btn.pack(side="left", padx=2)

        self._status = tk.StringVar(value="")
        ttk.Label(row, textvariable=self._status, font=("", 9, "italic")).pack(
            side="left", padx=8)

        # --- Résultats : dragodindes non castrées trouvées ---
        res_frame = ttk.LabelFrame(self._frame, text="Dragodindes non castrées trouvées")
        res_frame.pack(fill="both", expand=True, padx=8, pady=4)
        cols = ("model", "name", "level", "sex", "repro", "price")
        self._tree = ttk.Treeview(res_frame, columns=cols, show="headings", height=8)
        for c, txt, w, anchor in (
            ("model", "Dragodinde", 220, "w"),
            ("name", "Nom", 120, "w"),
            ("level", "Niv.", 45, "center"),
            ("sex", "Sexe", 70, "center"),
            ("repro", "Repro", 70, "center"),
            ("price", "Prix (kamas)", 110, "e"),
        ):
            self._tree.heading(c, text=txt)
            self._tree.column(c, width=w, anchor=anchor)
        sb = ttk.Scrollbar(res_frame, orient="vertical", command=self._tree.yview)
        self._tree.configure(yscrollcommand=sb.set)
        self._tree.pack(side="left", fill="both", expand=True, padx=(4, 0), pady=4)
        sb.pack(side="right", fill="y", pady=4)

        # --- Journal ---
        log_frame = ttk.LabelFrame(self._frame, text="Journal")
        log_frame.pack(fill="x", padx=8, pady=(0, 8))
        self._log = tk.Text(log_frame, height=8, wrap="word", state="disabled",
                            bg="#0d1117", fg="#c9d1d9")
        self._log.pack(fill="x", padx=4, pady=4)

    def _logline(self, message: str) -> None:
        self._log.configure(state="normal")
        self._log.insert("end", message + "\n")
        self._log.see("end")
        self._log.configure(state="disabled")

    # --------------------------------------------------------------- events
    def _stop(self) -> None:
        from bot.hdv import cancel_scan
        cancel_scan(self._session)
        self._logline("Arrêt demandé — fin du scan en cours…")

    def _scan(self) -> None:
        label = self._model_var.get()
        if label == _ALL_LABEL:
            # Toutes les couleurs d'élevage — mais pas les montures exotiques
            # (Tabi, Karnage…) dont les certificats traînent aussi à l'HDV.
            model_ids = set(mounts_db.DRAGODINDE_MODELS)
        else:
            model_ids = {self._models_by_label[label]}
        self._start_scan(model_ids, label)

    def _scan_turquoise(self) -> None:
        """Scanner la Turquoise pure + toutes ses variantes bicolores."""
        model_ids = {mid for mid, name in mounts_db.DRAGODINDE_MODELS.items()
                     if "Turquoise" in name}
        self._start_scan(model_ids, f"Turquoise et variantes ({len(model_ids)} couleurs)")

    def _start_scan(self, model_ids: set[int], label: str) -> None:
        if self._busy:
            return
        self._busy = True
        self._scan_btn.configure(state="disabled")
        self._turquoise_btn.configure(state="disabled")
        self._stop_btn.configure(state="normal")
        self._tree.delete(*self._tree.get_children())
        self._status.set("Scan en cours…")
        self._logline(f"--- Scan HDV : {label} ---")

        from core.session import launch_in_session

        def _ui_log(msg: str) -> None:
            self._root.after(0, self._logline, msg)

        async def _run() -> None:
            from bot.hdv import scan_dragodindes
            try:
                result = await scan_dragodindes(model_ids, log=_ui_log)
            except Exception as exc:  # noqa: BLE001
                result = {"ok": False, "reason": f"Erreur : {exc}", "found": [],
                          "gids_scanned": 0, "mounts_scanned": 0}
            self._root.after(0, self._on_scan_done, result)

        launch_in_session(self._session, _run)

    def _on_scan_done(self, result: dict) -> None:
        self._busy = False
        self._scan_btn.configure(state="normal")
        self._turquoise_btn.configure(state="normal")
        self._stop_btn.configure(state="disabled")

        found = result.get("found", [])
        for offer in found:
            repro = "?"
            if offer.reproductions is not None:
                repro = str(offer.reproductions)
                if offer.reproductions_max is not None:
                    repro += f"/{offer.reproductions_max}"
            self._tree.insert("", "end", values=(
                offer.model_name,
                offer.name or "SansNom",
                offer.level,
                offer.sex_label,
                repro,
                f"{offer.price:,}".replace(",", " "),
            ))

        if result.get("ok"):
            self._status.set(
                f"{len(found)} non castrée(s) — "
                f"{result.get('mounts_scanned', 0)} monture(s) inspectée(s)")
        else:
            self._status.set(f"Échec : {result.get('reason', '')}")
