"""
Onglet Carte — grille isométrique de la map avec entités en temps réel.

Affiche chaque cellule sous forme de losange (diamant) coloré selon son type
(bloqué, praticable, sortie, ressource dispo/CD) et superpose des marqueurs
pour les entités (joueurs, monstres, notre personnage).
"""

from __future__ import annotations

import tkinter as tk
from tkinter import ttk
from typing import TYPE_CHECKING

from dashboard import bridge
from data.recolte import get_name as _res_name

if TYPE_CHECKING:
    from bot.mapdata import MapInfo

# ---------------------------------------------------------------------------
# Couleurs (thème sombre)
# ---------------------------------------------------------------------------

_BG       = "#0e1117"
_BLOCKED  = "#161b22"
_WALK     = "#21262d"
_EXIT     = "#3d3d19"
_RES_OK   = "#1a4d1a"
_RES_CD   = "#4d2a1a"
_OUTLINE  = "#0b0e13"
_SELF_C   = "#4CAF50"
_PLAYER_C = "#42A5F5"
_MONSTER_C = "#EF5350"


class CarteTab:
    """Onglet affichant la carte en grille isométrique avec entités."""

    _PAD = 8
    _MIN_CW = 6

    def __init__(self, notebook: ttk.Notebook, root: tk.Tk, session) -> None:
        self._root = root
        self._session = session
        self._bridge = session.bridge
        self._frame = ttk.Frame(notebook)
        notebook.add(self._frame, text="Carte")

        self._entities: dict[str, dict] = {}
        self._map_info: MapInfo | None = None
        self._current_map_id: int | None = None
        self._resources: dict[int, bool] = {}
        self._resource_types: dict[int, int] = {}

        # Canvas item tracking
        self._cell_items: dict[int, int] = {}
        self._cell_centers: dict[int, tuple[float, float]] = {}
        self._cell_base: dict[int, str] = {}
        self._entity_items: dict[str, list[int]] = {}

        self._cell_w: float = 20.0
        self._cell_h: float = 14.0
        self._hover_cell: int = -1
        self._hover_eids: tuple[str, ...] = ()
        self._tip_win: tk.Toplevel | None = None
        self._tip_label: tk.Label | None = None
        self._resize_after: str | None = None

        self._build()

        self._bridge.subscribe("map",            lambda d: root.after(0, self._refresh_map, d))
        self._bridge.subscribe("resources",      lambda d: root.after(0, self._refresh_resources, d))
        self._bridge.subscribe("entity_set",     lambda d: root.after(0, self._on_entity_set, d))
        self._bridge.subscribe("entity_remove",  lambda d: root.after(0, self._on_entity_remove, d))
        self._bridge.subscribe("entities_clear", lambda _: root.after(0, self._on_entities_clear))

    # ------------------------------------------------------------------ build

    def _build(self) -> None:
        top = ttk.Frame(self._frame)
        top.pack(fill="x", padx=8, pady=(8, 2))

        ttk.Label(top, text="Map :").pack(side="left")
        self._map_id_var = tk.StringVar(value="—")
        ttk.Label(top, textvariable=self._map_id_var,
                  font=("", 10, "bold")).pack(side="left", padx=(4, 12))

        self._dim_var = tk.StringVar(value="")
        ttk.Label(top, textvariable=self._dim_var,
                  foreground="gray").pack(side="left")

        # Légende
        legend = ttk.Frame(top)
        legend.pack(side="right")
        for label, color in [
            ("Moi", _SELF_C), ("Joueur", _PLAYER_C), ("Monstre", _MONSTER_C),
            ("Dispo", _RES_OK), ("CD", _RES_CD), ("Sortie", _EXIT),
        ]:
            sw = tk.Frame(legend, bg=color, width=10, height=10)
            sw.pack(side="left", padx=(8, 2))
            sw.pack_propagate(False)
            ttk.Label(legend, text=label, font=("", 7)).pack(side="left")

        # --- vertical pane : canvas | treeviews ---
        vpane = ttk.PanedWindow(self._frame, orient="vertical")
        vpane.pack(fill="both", expand=True, padx=8, pady=4)

        canvas_frame = ttk.Frame(vpane)
        vpane.add(canvas_frame, weight=3)

        self._canvas = tk.Canvas(canvas_frame, bg=_BG, highlightthickness=0)
        self._canvas.pack(fill="both", expand=True)
        self._canvas.bind("<Configure>", self._on_resize)
        self._canvas.bind("<Motion>", self._on_hover)
        self._canvas.bind("<Leave>", lambda _e: self._hide_tooltip())

        self._tip_var = tk.StringVar(value="")
        ttk.Label(canvas_frame, textvariable=self._tip_var,
                  font=("Consolas", 8), foreground="#888").pack(anchor="w", padx=4)

        # --- bottom pane : ressources | entités ---
        hpane = ttk.PanedWindow(vpane, orient="horizontal")
        vpane.add(hpane, weight=1)

        self._build_res_tree(hpane)
        self._build_ent_tree(hpane)

    def _build_res_tree(self, parent: ttk.PanedWindow) -> None:
        fr = ttk.LabelFrame(parent, text="Ressources")
        parent.add(fr, weight=1)

        cols = ("cell", "nom", "état")
        self._res_tree = ttk.Treeview(fr, columns=cols, show="headings", height=6)
        self._res_tree.heading("cell", text="Cell")
        self._res_tree.heading("nom",  text="Nom")
        self._res_tree.heading("état", text="État")
        self._res_tree.column("cell", width=50,  anchor="center")
        self._res_tree.column("nom",  width=120, anchor="w")
        self._res_tree.column("état", width=70,  anchor="center")
        sb = ttk.Scrollbar(fr, orient="vertical", command=self._res_tree.yview)
        self._res_tree.configure(yscrollcommand=sb.set)
        self._res_tree.pack(side="left", fill="both", expand=True)
        sb.pack(side="right", fill="y")
        self._res_tree.tag_configure("dispo",    foreground="#4CAF50")
        self._res_tree.tag_configure("cooldown", foreground="#888")

    def _build_ent_tree(self, parent: ttk.PanedWindow) -> None:
        fr = ttk.LabelFrame(parent, text="Entités")
        parent.add(fr, weight=1)

        cols = ("ico", "name", "lvl", "cls", "cell")
        self._ent_tree = ttk.Treeview(fr, columns=cols, show="headings", height=6)
        self._ent_tree.heading("ico",  text="")
        self._ent_tree.heading("name", text="Nom")
        self._ent_tree.heading("lvl",  text="Nv.")
        self._ent_tree.heading("cls",  text="Classe")
        self._ent_tree.heading("cell", text="Cell")
        self._ent_tree.column("ico",  width=24,  anchor="center")
        self._ent_tree.column("name", width=110)
        self._ent_tree.column("lvl",  width=36,  anchor="center")
        self._ent_tree.column("cls",  width=70)
        self._ent_tree.column("cell", width=50,  anchor="center")
        sb = ttk.Scrollbar(fr, orient="vertical", command=self._ent_tree.yview)
        self._ent_tree.configure(yscrollcommand=sb.set)
        self._ent_tree.pack(side="left", fill="both", expand=True)
        sb.pack(side="right", fill="y")
        self._ent_tree.tag_configure("player",  foreground="#42A5F5")
        self._ent_tree.tag_configure("monster", foreground="#EF5350")
        self._ent_tree.tag_configure("self",    foreground="#4CAF50")

    # ----------------------------------------------------------- map refresh

    def _refresh_map(self, data: dict) -> None:
        map_id = data.get("map_id")
        self._map_id_var.set(str(map_id or "—"))

        if map_id is not None and map_id == self._current_map_id:
            return

        self._current_map_id = map_id
        self._resources.clear()
        self._resource_types.clear()

        if map_id is not None:
            from bot.mapdata import load_map
            self._map_info = load_map(map_id)
        else:
            self._map_info = None

        if self._map_info:
            w, h = self._map_info.width, self._map_info.height
            self._dim_var.set(f"{w}×{h} — {len(self._map_info.cells)} cellules")
        else:
            self._dim_var.set("(XML manquant)" if map_id else "")

        self._draw_grid()

    # -------------------------------------------------------------- grid draw

    def _draw_grid(self) -> None:
        self._canvas.delete("all")
        self._cell_items.clear()
        self._cell_centers.clear()
        self._cell_base.clear()
        self._entity_items.clear()

        cw_cv = self._canvas.winfo_width()
        ch_cv = self._canvas.winfo_height()

        if cw_cv < 50 or ch_cv < 50:
            self._root.after(100, self._draw_grid)
            return

        info = self._map_info
        if not info or not info.cells:
            msg = ("En attente d'une carte…"
                   if self._current_map_id is None
                   else f"XML manquant — map #{self._current_map_id}")
            self._canvas.create_text(
                cw_cv // 2, ch_cv // 2, text=msg,
                fill="#555566", font=("", 12),
            )
            return

        from bot.pathfinding import cell_to_xy
        width = info.width

        positions: dict[int, tuple[int, int]] = {}
        for c in info.cells:
            positions[c.cell_id] = cell_to_xy(c.cell_id, width)

        max_gx = max(gx for gx, _ in positions.values())
        max_gy = max(gy for _, gy in positions.values())

        # Auto-fit cell dimensions to available canvas space
        n_cols = max_gx + 1.5
        n_rows = max_gy * 0.5 + 1.0
        pad = self._PAD
        avail_w = cw_cv - 2 * pad
        avail_h = ch_cv - 2 * pad

        cw = avail_w / n_cols
        ch = avail_h / n_rows
        if ch * 1.5 < cw:
            cw = ch * 1.5
        else:
            ch = cw / 1.5
        cw = max(cw, self._MIN_CW)
        ch = max(ch, self._MIN_CW / 1.5)
        self._cell_w = cw
        self._cell_h = ch
        rh = ch * 0.5

        # Center grid
        grid_w = n_cols * cw
        grid_h = n_rows * ch
        off_x = pad + (avail_w - grid_w) * 0.5
        off_y = pad + (avail_h - grid_h) * 0.5

        walkable = info.walkable_cells
        exits = info.sun_magic_cells
        res_ids = {cid for cid, _ in info.resource_cells}
        show_id = cw > 28
        draw_outline = cw > 8

        for cell in info.cells:
            cid = cell.cell_id
            gx, gy = positions[cid]

            offset = cw * 0.5 if gy % 2 == 1 else 0.0
            cx = off_x + gx * cw + offset + cw * 0.5
            cy = off_y + gy * rh + ch * 0.5
            self._cell_centers[cid] = (cx, cy)

            if cid in exits:
                base, color = "exit", _EXIT
            elif cid in res_ids:
                base, color = "resource", _RES_OK
            elif cid in walkable:
                base, color = "walk", _WALK
            else:
                base, color = "block", _BLOCKED
            self._cell_base[cid] = base

            hw, hh = cw * 0.5, ch * 0.5
            pts = [cx, cy - hh, cx + hw, cy, cx, cy + hh, cx - hw, cy]
            item = self._canvas.create_polygon(
                pts, fill=color,
                outline=_OUTLINE if draw_outline else "",
                width=1,
                tags=(f"c{cid}", "cell"),
            )
            self._cell_items[cid] = item

            if show_id:
                self._canvas.create_text(
                    cx, cy, text=str(cid),
                    fill="#555", font=("", max(int(ch * 0.35), 6)),
                    tags=("cellid",),
                )

        self._apply_resource_colors()
        self._redraw_all_markers()

    # -------------------------------------------------------- resource colors

    def _apply_resource_colors(self) -> None:
        for cid, avail in self._resources.items():
            item = self._cell_items.get(cid)
            if item is not None:
                self._canvas.itemconfig(
                    item, fill=_RES_OK if avail else _RES_CD,
                )

    def _refresh_resources(self, resources: list[dict]) -> None:
        self._res_tree.delete(*self._res_tree.get_children())
        self._resources.clear()
        self._resource_types.clear()

        for r in resources:
            cid = r.get("cell_id", -1)
            avail = r.get("available", False)
            etype = r.get("elem_type", "")
            self._resources[cid] = avail
            self._resource_types[cid] = etype

            tag = "dispo" if avail else "cooldown"
            self._res_tree.insert("", "end", values=(
                cid,
                _res_name(etype),
                "✓ Dispo" if avail else "○ CD",
            ), tags=(tag,))

        self._apply_resource_colors()

    # ------------------------------------------------------------- entities

    def _on_entity_set(self, entity: dict) -> None:
        eid = entity.get("entity_id", "")
        self._entities[eid] = entity
        self._draw_marker(eid, entity)
        if eid in self._hover_eids:
            ents = [self._entities[e] for e in self._hover_eids if e in self._entities]
            if ents:
                self._update_tooltip_text(ents)
        self._rebuild_ent_tree()

    def _on_entity_remove(self, entity_id: str) -> None:
        self._entities.pop(entity_id, None)
        self._del_marker(entity_id)
        if entity_id in self._hover_eids:
            self._hide_tooltip()
        self._rebuild_ent_tree()

    def _on_entities_clear(self) -> None:
        self._entities.clear()
        for ids in self._entity_items.values():
            for i in ids:
                self._canvas.delete(i)
        self._entity_items.clear()
        self._hide_tooltip()
        self._ent_tree.delete(*self._ent_tree.get_children())

    # -------------------------------------------------------- entity markers

    def _draw_marker(self, eid: str, entity: dict) -> None:
        self._del_marker(eid)

        cell_id = entity.get("cell_id", -1)
        if cell_id < 0 or cell_id not in self._cell_centers:
            return

        cx, cy = self._cell_centers[cell_id]
        is_monster = entity.get("is_monster", False)
        name = entity.get("name", "")
        is_self = not is_monster and name == self._self_name

        if is_self:
            color, r = _SELF_C, max(self._cell_w * 0.32, 5)
        elif is_monster:
            color, r = _MONSTER_C, max(self._cell_w * 0.24, 3)
        else:
            color, r = _PLAYER_C, max(self._cell_w * 0.24, 3)

        ids: list[int] = []

        # Halo pour notre perso
        if is_self and self._cell_w > 12:
            rg = r * 1.6
            ids.append(self._canvas.create_oval(
                cx - rg, cy - rg, cx + rg, cy + rg,
                fill="", outline=_SELF_C, width=1, dash=(3, 3),
                tags=(f"e{eid}", "ent"),
            ))

        ids.append(self._canvas.create_oval(
            cx - r, cy - r, cx + r, cy + r,
            fill=color, outline="white", width=1,
            tags=(f"e{eid}", "ent"),
        ))

        if self._cell_w > 18 and name:
            ids.append(self._canvas.create_text(
                cx, cy - self._cell_h * 0.55 - 5,
                text=name[:12], fill=color,
                font=("", max(int(self._cell_w * 0.28), 7), "bold"),
                tags=(f"e{eid}", "entlbl"),
            ))

        self._entity_items[eid] = ids

        if eid in self._hover_eids:
            self._set_markers_hover((eid,), True)

    def _del_marker(self, eid: str) -> None:
        for i in self._entity_items.pop(eid, []):
            self._canvas.delete(i)

    def _redraw_all_markers(self) -> None:
        for ids in self._entity_items.values():
            for i in ids:
                self._canvas.delete(i)
        self._entity_items.clear()
        for eid, ent in self._entities.items():
            self._draw_marker(eid, ent)

    # --------------------------------------------------------------- resize

    def _on_resize(self, _event: tk.Event) -> None:
        if self._resize_after is not None:
            self._root.after_cancel(self._resize_after)
        self._resize_after = self._root.after(150, self._do_resize)

    def _do_resize(self) -> None:
        self._resize_after = None
        if self._map_info:
            self._draw_grid()

    # ---------------------------------------------------------------- hover

    def _on_hover(self, event: tk.Event) -> None:
        cid = self._cell_at(event.x, event.y)
        ents_here = [
            e for e in self._entities.values() if e.get("cell_id") == cid
        ] if cid >= 0 else []

        new_eids = tuple(e.get("entity_id", "") for e in ents_here)
        if new_eids != self._hover_eids:
            self._set_markers_hover(self._hover_eids, False)
            self._set_markers_hover(new_eids, True)
            self._hover_eids = new_eids
            if ents_here:
                self._update_tooltip_text(ents_here)

        if ents_here:
            self._move_tooltip(event.x_root, event.y_root)
        else:
            self._hide_tooltip()

        if cid == self._hover_cell:
            return
        self._hover_cell = cid

        if cid < 0:
            self._tip_var.set("")
            return

        parts: list[str] = [f"Cell #{cid}"]

        base = self._cell_base.get(cid, "?")
        if base == "exit":
            parts.append("Sortie")
        elif base == "resource":
            et = self._resource_types.get(cid)
            rname = _res_name(et) if et else "Ressource"
            avail = self._resources.get(cid)
            if avail is not None:
                rname += " ✓" if avail else " (CD)"
            parts.append(rname)
        elif base == "walk":
            parts.append("Praticable")
        else:
            parts.append("Bloqué")

        for ent in ents_here:
            if ent.get("is_monster"):
                kind = "Monstre"
            else:
                # Décor (percepteur, prisme, monture…) : le type porte le libellé,
                # sinon c'est un joueur.
                kind = ent.get("class_name", "") if ent.get("entity_type") else "Joueur"
            parts.append(f"{kind}: {ent.get('name', '?')}")

        self._tip_var.set("  ·  ".join(parts))

    # ---------------------------------------------------- entity tooltip

    def _ensure_tooltip(self) -> None:
        if self._tip_win is not None:
            return
        win = tk.Toplevel(self._root)
        win.withdraw()
        win.overrideredirect(True)
        try:
            win.attributes("-topmost", True)
        except tk.TclError:
            pass
        frame = tk.Frame(win, bg="#1c1f26",
                         highlightbackground="#3d4250", highlightthickness=1)
        frame.pack()
        self._tip_label = tk.Label(
            frame, text="", justify="left",
            bg="#1c1f26", fg="#e6e6e6",
            font=("Consolas", 9), padx=8, pady=6,
        )
        self._tip_label.pack()
        self._tip_win = win

    def _update_tooltip_text(self, entities: list[dict]) -> None:
        self._ensure_tooltip()
        lines: list[str] = []
        for i, ent in enumerate(entities):
            if i > 0:
                lines.append("─" * 28)
            if ent.get("is_monster"):
                lines.extend(self._fmt_monster_lines(ent))
            else:
                lines.extend(self._fmt_player_lines(ent))
        assert self._tip_label is not None
        self._tip_label.config(text="\n".join(lines))

    _ALIGN_NAMES = {0: "Neutre", 1: "Bonta", 2: "Brâkmar", 3: "Sufokia"}
    _DIR_ARROWS = {0: "→ E", 1: "↘ SE", 2: "↓ S", 3: "↙ SW",
                   4: "← W", 5: "↖ NW", 6: "↑ N", 7: "↗ NE"}
    _ACC_SLOTS = ("Coiffe", "Cape", "Arme", "Bouclier", "Familier")

    def _fmt_player_lines(self, ent: dict) -> list[str]:
        name = ent.get("name", "?")
        is_self = name == self._self_name
        sex = ent.get("sex", 0)
        sex_ico = "♀" if sex == 1 else "♂"
        head = "★ " if is_self else "● "
        lines = [f"{head}{name}  {sex_ico}"]

        lvl = ent.get("level", 0)
        cls = ent.get("class_name", "") or ""
        meta = []
        if lvl:
            meta.append(f"Nv.{lvl}")
        if cls:
            meta.append(cls)
        if meta:
            lines.append("   " + "  ·  ".join(meta))

        guild = ent.get("guild_name", "")
        if guild:
            emblem = ent.get("emblem", "")
            line = f"   ⚔ <{guild}>"
            if emblem:
                line += f"  [{emblem}]"
            lines.append(line)

        side = ent.get("align_side", 0)
        if side:
            align_str = self._ALIGN_NAMES.get(side, f"#{side}")
            rank = ent.get("align_rank", 0)
            if rank:
                align_str += f" rang {rank}"
            honor = ent.get("align_honor", 0)
            if honor:
                align_str += f"  · {honor} hr"
            disg = ent.get("align_disgrace", 0)
            if disg:
                align_str += f"  · {disg} dh"
            lines.append(f"   ⚑ {align_str}")

        cell = ent.get("cell_id", -1)
        direction = ent.get("direction", -1)
        cell_str = f"Cell #{cell}" if cell >= 0 else ""
        if direction >= 0:
            cell_str += f"   {self._DIR_ARROWS.get(direction, str(direction))}"
        if cell_str.strip():
            lines.append(f"   {cell_str}")

        gfx = ent.get("gfx_id", "")
        scale = ent.get("scale", 0)
        if gfx:
            skin = f"gfx {gfx}"
            if scale and scale != 100:
                skin += f" @{scale}%"
            lines.append(f"   ◈ {skin}")

        c1 = ent.get("color1", "")
        c2 = ent.get("color2", "")
        c3 = ent.get("color3", "")
        if c1 or c2 or c3:
            def _c(v: str) -> str:
                if not v or v in ("0", "-1"):
                    return "—"
                return v
            lines.append(f"   ◧ couleurs : {_c(c1)} / {_c(c2)} / {_c(c3)}")

        accs = ent.get("accessories") or []
        if any(a for a in accs):
            lines.append("   ▤ Équipement :")
            for slot, raw in zip(self._ACC_SLOTS, accs):
                if not raw:
                    continue
                # format brut "id" ou "id~qty~slot"
                acc_id = raw.split("~", 1)[0]
                lines.append(f"        {slot:<9} #{acc_id}")
            extras = accs[len(self._ACC_SLOTS):]
            for raw in extras:
                if raw:
                    lines.append(f"        ?         {raw}")

        aura = ent.get("aura", "")
        if aura and aura not in ("0", ""):
            lines.append(f"   ✦ Aura {aura}")

        mount = ent.get("mount", "")
        if mount:
            lines.append(f"   🐎 Monture : {mount}")

        restr = ent.get("restrictions", "")
        if restr:
            lines.append(f"   ⛔ Restrictions : {restr}")

        eid = ent.get("entity_id", "")
        if eid:
            lines.append(f"   id {eid}")
        return lines

    def _fmt_monster_lines(self, ent: dict) -> list[str]:
        ids = list(ent.get("monster_ids") or [])
        levels = list(ent.get("monster_levels") or [])
        n = max(len(ids), len(levels), 1)
        bonus = ent.get("bonus", 0)
        total = sum(levels) if levels else ent.get("level", 0)
        star = f"  ★{bonus}%" if bonus else ""
        lines = [f"▼ Groupe de {n} monstre(s){star}"]
        lines.append(f"   Total Nv.{total}")
        cell = ent.get("cell_id", -1)
        if cell >= 0:
            lines.append(f"   Cell #{cell}")
        lines.append("")
        for i in range(n):
            mid = ids[i] if i < len(ids) else "?"
            lvl = levels[i] if i < len(levels) else "?"
            lines.append(f"   • #{mid:<5}  Nv.{lvl}")
        eid = ent.get("entity_id", "")
        if eid:
            lines.append(f"   id {eid}")
        return lines

    def _move_tooltip(self, x_root: int, y_root: int) -> None:
        if self._tip_win is None:
            return
        x, y = x_root + 14, y_root + 14
        self._tip_win.geometry(f"+{x}+{y}")
        if self._tip_win.state() != "normal":
            self._tip_win.deiconify()
        try:
            self._tip_win.lift()
        except tk.TclError:
            pass

    def _hide_tooltip(self) -> None:
        if self._tip_win is not None:
            self._tip_win.withdraw()
        if self._hover_eids:
            self._set_markers_hover(self._hover_eids, False)
            self._hover_eids = ()

    def _set_markers_hover(self, eids: tuple[str, ...], on: bool) -> None:
        outline = "#FFD54F" if on else "white"
        width = 2 if on else 1
        for eid in eids:
            if not eid:
                continue
            for item in self._canvas.find_withtag(f"e{eid}"):
                try:
                    self._canvas.itemconfig(item, outline=outline, width=width)
                except tk.TclError:
                    pass

    def _cell_at(self, px: float, py: float) -> int:
        best, best_d = -1, float("inf")
        max_d = (self._cell_w * 0.5) ** 2 + (self._cell_h * 0.5) ** 2
        for cid, (cx, cy) in self._cell_centers.items():
            d = (px - cx) ** 2 + (py - cy) ** 2
            if d < best_d:
                best_d = d
                best = cid
        return best if best_d <= max_d else -1

    # -------------------------------------------------------- entity treeview

    def _rebuild_ent_tree(self) -> None:
        self._ent_tree.delete(*self._ent_tree.get_children())
        sn = self._self_name

        def _key(e: dict) -> tuple:
            m = e.get("is_monster", False)
            n = e.get("name", "")
            return (
                0 if not m and n == sn else 1,
                1 if not m else 2,
                n.lower(),
            )

        for e in sorted(self._entities.values(), key=_key):
            cell = e.get("cell_id", -1)
            is_m = e.get("is_monster", False)
            name = e.get("name", "")

            if is_m:
                tag, ico = "monster", "M"
            elif name == sn:
                tag, ico = "self", "★"
            else:
                tag, ico = "player", "J"

            self._ent_tree.insert("", "end", values=(
                ico, name, e.get("level", ""),
                e.get("class_name", ""),
                cell if cell >= 0 else "?",
            ), tags=(tag,))

    # ----------------------------------------------------------------- utils

    @property
    def _self_name(self) -> str:
        try:
            char = self._session.game_state.character
            if char:
                return char.pseudo
        except Exception:
            pass
        return ""
