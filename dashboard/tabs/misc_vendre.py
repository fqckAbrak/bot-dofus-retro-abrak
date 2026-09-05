"""
Sous-onglet « Vendre au marchand ».

Vend automatiquement les équipements en sac à un PNJ marchand ambulant :
  * filtre par familles d'équipement (bottes, ceintures, anneaux, armes…) ;
  * filtre par niveau maximum (évite de vendre un objet précieux par erreur) ;
  * blacklist par nom d'item ;
  * vérifie la présence du marchand sur la carte (= bonne map) avant de vendre.

Thread tkinter : ne lit JAMAIS game.state.current (pas de session active ici).
Tout passe par self._session.game_state (aperçu/détection) ou par
launch_in_session pour la vente (qui s'exécute dans le contexte de la session).
"""

from __future__ import annotations

import tkinter as tk
from tkinter import ttk

from bot import merchant_config
from bot.merchant import SellConfig, preview_sellable, sell_equipment_to_merchant
from data import items_db


class VendreMarchandTab:
    def __init__(self, notebook: ttk.Notebook, root: tk.Tk, session) -> None:
        self._root = root
        self._session = session
        self._frame = ttk.Frame(notebook)
        notebook.add(self._frame, text="Vendre au marchand")

        self._cfg = merchant_config.load()
        self._type_vars: dict[int, tk.BooleanVar] = {}
        self._busy = False
        self._last_auto = 0.0
        self._build()
        # Fetch auto de l'inventaire quand l'onglet devient visible (throttlé).
        self._frame.bind("<Visibility>", self._on_visible)

    def _on_visible(self, _event) -> None:
        import time
        if not self._busy and time.time() - self._last_auto >= 4.0:
            self._last_auto = time.time()
            try:
                self._refresh_inventory()
            except Exception:
                pass

    # ------------------------------------------------------------------ UI
    def _build(self) -> None:
        if not items_db.is_loaded():
            ttk.Label(
                self._frame,
                text="Base d'items absente.\nLancez : py -3 tools/extract_items.py",
                foreground="#f85149", justify="center",
            ).pack(expand=True, pady=20)
            return

        # --- Réglages généraux ---
        top = ttk.LabelFrame(self._frame, text="Réglages")
        top.pack(fill="x", padx=8, pady=(8, 4))

        row = ttk.Frame(top)
        row.pack(fill="x", padx=8, pady=4)
        ttk.Label(row, text="Niveau max :").pack(side="left")
        self._max_level = tk.IntVar(value=int(self._cfg.get("max_level", 120)))
        ttk.Spinbox(row, from_=0, to=200, width=6, textvariable=self._max_level).pack(side="left", padx=(4, 16))

        ttk.Label(row, text="gfx marchand (optionnel) :").pack(side="left")
        self._gfx_var = tk.StringVar(value=",".join(str(g) for g in self._cfg.get("merchant_gfx", [])))
        ttk.Entry(row, width=14, textvariable=self._gfx_var).pack(side="left", padx=4)
        ttk.Button(row, text="Détecter PNJ", command=self._detect_npcs, width=12).pack(side="left", padx=4)

        # --- Familles d'équipement à vendre ---
        types_frame = ttk.LabelFrame(self._frame, text="Familles d'équipement à vendre")
        types_frame.pack(fill="x", padx=8, pady=4)
        grid = ttk.Frame(types_frame)
        grid.pack(fill="x", padx=8, pady=4)

        enabled_cats = set(self._cfg.get("categories", list(items_db.EQUIPMENT_CATEGORIES)))
        # Une case par FAMILLE (amulette, armes, anneau, ceinture, bottes…).
        col = 0
        rowi = 0
        for cat_id, cat_name in items_db.EQUIPMENT_CATEGORY_NAMES.items():
            var = tk.BooleanVar(value=cat_id in enabled_cats)
            self._type_vars[cat_id] = var
            ttk.Checkbutton(grid, text=cat_name, variable=var).grid(
                row=rowi, column=col, sticky="w", padx=6, pady=1
            )
            col += 1
            if col >= 4:
                col = 0
                rowi += 1

        # --- Blacklist ---
        bl_frame = ttk.LabelFrame(self._frame, text="Blacklist (ne jamais vendre)")
        bl_frame.pack(fill="both", expand=False, padx=8, pady=4)
        bl_inner = ttk.Frame(bl_frame)
        bl_inner.pack(fill="x", padx=8, pady=4)

        self._bl_list = tk.Listbox(bl_inner, height=4)
        self._bl_list.pack(side="left", fill="x", expand=True)
        for name in self._cfg.get("blacklist_names", []):
            self._bl_list.insert("end", name)
        bl_btns = ttk.Frame(bl_inner)
        bl_btns.pack(side="left", padx=6)
        self._bl_entry = ttk.Entry(bl_btns, width=22)
        self._bl_entry.pack(pady=1)
        ttk.Button(bl_btns, text="+ Ajouter", command=self._bl_add, width=18).pack(pady=1)
        ttk.Button(bl_btns, text="− Retirer sélection", command=self._bl_remove, width=18).pack(pady=1)
        ttk.Button(bl_btns, text="Blacklister l'aperçu sélectionné", command=self._bl_from_preview, width=26).pack(pady=1)

        # --- Actions ---
        act = ttk.Frame(self._frame)
        act.pack(fill="x", padx=8, pady=(4, 2))
        ttk.Button(act, text="↻ Inventaire", command=self._refresh_inventory, width=12).pack(side="left", padx=2)
        ttk.Button(act, text="Aperçu", command=self._refresh_preview, width=10).pack(side="left", padx=2)
        self._sell_btn = ttk.Button(act, text="▶ Vendre", command=self._sell, width=12)
        self._sell_btn.pack(side="left", padx=2)
        ttk.Button(act, text="💾 Enregistrer réglages", command=self._save, width=20).pack(side="right", padx=2)
        self._status = tk.StringVar(value="")
        ttk.Label(act, textvariable=self._status, font=("", 9, "italic")).pack(side="left", padx=8)

        # --- Aperçu des objets vendables ---
        prev_frame = ttk.LabelFrame(self._frame, text="Aperçu / À vendre")
        prev_frame.pack(fill="both", expand=True, padx=8, pady=4)
        cols = ("name", "level", "type", "qty")
        self._tree = ttk.Treeview(prev_frame, columns=cols, show="headings", height=7)
        for c, txt, w in (("name", "Objet", 200), ("level", "Niv.", 50),
                          ("type", "Type", 110), ("qty", "Qté", 50)):
            self._tree.heading(c, text=txt)
            self._tree.column(c, width=w, anchor="w" if c in ("name", "type") else "center")
        sb = ttk.Scrollbar(prev_frame, orient="vertical", command=self._tree.yview)
        self._tree.configure(yscrollcommand=sb.set)
        self._tree.pack(side="left", fill="both", expand=True, padx=(4, 0), pady=4)
        sb.pack(side="right", fill="y", pady=4)

        # --- Journal ---
        log_frame = ttk.LabelFrame(self._frame, text="Journal")
        log_frame.pack(fill="x", padx=8, pady=(0, 8))
        self._log = tk.Text(log_frame, height=6, wrap="word", state="disabled",
                            bg="#0d1117", fg="#c9d1d9")
        self._log.pack(fill="x", padx=4, pady=4)

    # -------------------------------------------------------------- helpers
    def _enabled_categories(self) -> set[int]:
        # self._type_vars est désormais keyé par CATÉGORIE (famille d'équipement).
        return {cat_id for cat_id, var in self._type_vars.items() if var.get()}

    def _parse_gfx(self) -> list[str]:
        return [g.strip() for g in self._gfx_var.get().split(",") if g.strip()]

    def _build_config(self) -> SellConfig:
        names = {self._bl_list.get(i).strip().lower() for i in range(self._bl_list.size())}
        return SellConfig(
            max_level=int(self._max_level.get()),
            blacklist_names=names,
            blacklist_gids=set(self._cfg.get("blacklist_gids", [])),
            categories=self._enabled_categories(),
            merchant_gfx=self._parse_gfx(),
        )

    def _bag_snapshot(self) -> list[dict]:
        """Copie du sac depuis l'état de la session (thread tkinter — pas de current)."""
        try:
            return list(self._session.game_state._inventory.values())
        except Exception:
            return []

    def _logline(self, message: str) -> None:
        self._log.configure(state="normal")
        self._log.insert("end", message + "\n")
        self._log.see("end")
        self._log.configure(state="disabled")

    # --------------------------------------------------------------- events
    def _detect_npcs(self) -> None:
        try:
            from protocol.messages.stats import ENTITY_NPC
            npcs = [e for e in self._session.game_state.entities.values()
                    if e.entity_type == ENTITY_NPC]
        except Exception:
            npcs = []
        if not npcs:
            self._logline("Aucun PNJ détecté sur la carte courante.")
            return
        self._logline(f"{len(npcs)} PNJ sur la carte :")
        for n in npcs:
            self._logline(f"  • id {n.entity_id}  gfx {n.gfx_id}  cellule {n.cell_id}")

    def _bl_add(self) -> None:
        name = self._bl_entry.get().strip()
        if name:
            self._bl_list.insert("end", name)
            self._bl_entry.delete(0, "end")

    def _bl_remove(self) -> None:
        for i in reversed(self._bl_list.curselection()):
            self._bl_list.delete(i)

    def _bl_from_preview(self) -> None:
        for iid in self._tree.selection():
            name = self._tree.item(iid, "values")[0]
            if name not in self._bl_list.get(0, "end"):
                self._bl_list.insert("end", name)

    def _refresh_inventory(self) -> None:
        """Demander au client patché (core.swf) un dump complet de l'inventaire."""
        self._status.set("Récupération de l'inventaire…")
        self._logline("Demande d'inventaire complet (patch core.swf)…")
        from core.session import launch_in_session

        async def _run() -> None:
            from bot.inventory import request_full_inventory
            ok = await request_full_inventory(timeout=5.0)
            self._root.after(0, self._on_inventory_refreshed, ok)

        launch_in_session(self._session, _run)

    def _on_inventory_refreshed(self, ok: bool) -> None:
        if ok:
            n = len(self._bag_snapshot())
            self._logline(f"Inventaire reçu : {n} objet(s).")
        else:
            self._logline("Pas de réponse (patch core.swf absent ?). Inventaire incrémental conservé.")
        self._refresh_preview()

    def _refresh_preview(self) -> list:
        cfg = self._build_config()
        items = preview_sellable(cfg, bag=self._bag_snapshot())
        self._tree.delete(*self._tree.get_children())
        for it in items:
            self._tree.insert("", "end", values=(it.name, it.level, it.type_name, it.qty))
        self._status.set(f"{len(items)} objet(s) éligible(s)")
        return items

    def _save(self) -> None:
        self._cfg["max_level"] = int(self._max_level.get())
        self._cfg["categories"] = sorted(self._enabled_categories())
        self._cfg["merchant_gfx"] = self._parse_gfx()
        self._cfg["blacklist_names"] = [self._bl_list.get(i) for i in range(self._bl_list.size())]
        merchant_config.save(self._cfg)
        self._status.set("Réglages enregistrés")

    def _sell(self) -> None:
        if self._busy:
            return
        items = self._refresh_preview()
        if not items:
            self._logline("Rien à vendre avec ces réglages.")
            return
        self._save()
        cfg = self._build_config()
        self._busy = True
        self._sell_btn.configure(state="disabled")
        self._status.set("Vente en cours…")
        self._logline(f"--- Vente démarrée ({len(items)} objet(s)) ---")

        from core.session import launch_in_session

        def _ui_log(msg: str) -> None:
            self._root.after(0, self._logline, msg)

        async def _run() -> None:
            try:
                result = await sell_equipment_to_merchant(cfg, log=_ui_log)
            except Exception as exc:  # noqa: BLE001
                result = {"ok": False, "reason": f"Erreur : {exc}", "sold": 0, "kamas": 0}
            self._root.after(0, self._on_sell_done, result)

        launch_in_session(self._session, _run)

    def _on_sell_done(self, result: dict) -> None:
        self._busy = False
        self._sell_btn.configure(state="normal")
        if result.get("ok"):
            self._status.set(f"OK — {result.get('sold', 0)} vendu(s), +{result.get('kamas', 0)} kamas")
        else:
            self._status.set(f"Échec : {result.get('reason', '')}")
        self._refresh_preview()
