"""
Onglet Personnage — stats de tous les personnages (principal + héros).

Structure :
  PersonnageTab (onglet "Personnage" dans le notebook team)
    └── inner ttk.Notebook
          ├── Tab "Nom (Classe Nv.X)" → _CharPanel  (perso principal)
          └── Tab "Nom (Classe Nv.X)" → _CharPanel  (chaque héros, ajouté dynamiquement)

Chaque _CharPanel affiche :
  - Barres visuelles HP / Énergie / PA / PM
  - Grille de caractéristiques complète
  - Section auto-boost : points disponibles, radio stat, bouton "Distribuer"

La sélection de stat est persistée dans bot_settings.json :
  accounts.<pseudo>.auto_boost.<char_id> = <stat_id | null>
"""

from __future__ import annotations

import json
import logging
import os
import tkinter as tk
from tkinter import ttk

logger = logging.getLogger(__name__)

_SETTINGS_PATH = os.path.normpath(
    os.path.join(os.path.dirname(__file__), "..", "..", "bot_settings.json")
)

_BAR_W = 220
_BAR_H = 14

# (label_barre, clé_courante, clé_max, couleur)
_BARS: list[tuple[str, str, str, str]] = [
    ("HP",      "life",            "max_life",    "#3fb950"),
    ("Énergie", "energy",          "max_energy",  "#d29922"),
    ("PA",      "action_points",   "_max_pa",     "#1f6feb"),
    ("PM",      "movement_points", "_max_pm",     "#a371f7"),
]

_MAX_PA_DEFAULT = 12
_MAX_PM_DEFAULT = 6

# Stats affichées dans la grille
_STAT_FIELDS: list[tuple[str, str]] = [
    ("Pseudo",       "pseudo"),
    ("Niveau",       "level"),
    ("Classe",       "class_name"),
    ("Kamas",        "kamas"),
    ("Initiative",   "initiative"),
    ("Prospection",  "prospecting"),
    ("Portée (PO)",  "range_points"),
    ("Force",        "strength"),
    ("Vitalité",     "vitality"),
    ("Sagesse",      "wisdom"),
    ("Intelligence", "intelligence"),
    ("Chance",       "chance"),
    ("Agilité",      "agility"),
]

# Options d'auto-boost : (label, stat_id ou None)
_BOOST_OPTIONS: list[tuple[str, int | None]] = [
    ("Aucun",         None),
    ("Vitalité",      10),
    ("Sagesse",       11),
    ("Force",         12),
    ("Intelligence",  13),
    ("Chance",        14),
    ("Agilité",       15),
]


# ---------------------------------------------------------------------------
# Persistance bot_settings.json
# ---------------------------------------------------------------------------

def _read_settings() -> dict:
    try:
        with open(_SETTINGS_PATH, "r", encoding="utf-8") as f:
            data = json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return {"defaults": {}, "accounts": {}}
    if not isinstance(data, dict):
        return {"defaults": {}, "accounts": {}}
    data.setdefault("defaults", {})
    data.setdefault("accounts", {})
    return data


def _write_settings(data: dict) -> None:
    try:
        with open(_SETTINGS_PATH, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=4, ensure_ascii=False)
    except OSError as exc:
        logger.warning("[PersonnageTab] Sauvegarde bot_settings.json échouée : %s", exc)


def _load_auto_boost(account: str | None, char_id: str) -> int | None:
    """Charger la stat configurée pour auto-boost de ce personnage."""
    if not account:
        return None
    val = _read_settings().get("accounts", {}).get(account, {}).get("auto_boost", {}).get(str(char_id))
    if val is None:
        return None
    try:
        return int(val)
    except (ValueError, TypeError):
        return None


def _save_auto_boost(account: str | None, char_id: str, stat_id: int | None) -> None:
    """Enregistrer la stat auto-boost dans bot_settings.json."""
    if not account:
        return
    data = _read_settings()
    acc = data["accounts"].setdefault(account, {})
    boost = acc.setdefault("auto_boost", {})
    if stat_id is None:
        boost.pop(str(char_id), None)
    else:
        boost[str(char_id)] = stat_id
    _write_settings(data)


# ---------------------------------------------------------------------------
# Async helper (envoi AB depuis le thread UI via launch_in_session)
# ---------------------------------------------------------------------------

async def _send_boost(char_id: str, stat_id: int, points: int) -> None:
    """Envoyer AB{char_id};{stat_id};{points} sur la connexion active."""
    from bot import channel as _channel
    await _channel.send(f"AB{char_id};{stat_id};{points}\n")
    logger.info("[PersonnageTab] Boost manuel : %s stat=%d points=%d", char_id, stat_id, points)


# ---------------------------------------------------------------------------
# _CharPanel — panneau de stats + auto-boost pour un personnage
# ---------------------------------------------------------------------------

class _CharPanel:
    """Panneau affichant les stats et l'auto-boost d'un personnage (perso ou héros)."""

    def __init__(
        self,
        notebook: ttk.Notebook,
        tab_label: str,
        root: tk.Tk,
        session,
        char_id: str,
        account_getter,   # callable → str | None (renvoie le pseudo du perso principal)
    ) -> None:
        self._root = root
        self._session = session
        self._char_id = char_id
        self._get_account = account_getter

        self._frame = ttk.Frame(notebook)
        notebook.add(self._frame, text=tab_label)

        self._vars: dict[str, tk.StringVar] = {}
        self._bars: dict[str, dict] = {}
        self._title_var = tk.StringVar(value="—")
        self._points_var = tk.StringVar(value="Points disponibles : —")
        self._boost_var = tk.StringVar(value="None")   # "None" ou str(stat_id)

        self._build()

    # ------------------------------------------------------------------
    # Construction de l'UI
    # ------------------------------------------------------------------

    def _build(self) -> None:
        # Rendre le panneau scrollable verticalement
        outer = ttk.Frame(self._frame)
        outer.pack(fill="both", expand=True)

        canvas = tk.Canvas(outer, highlightthickness=0)
        sb = ttk.Scrollbar(outer, orient="vertical", command=canvas.yview)
        canvas.configure(yscrollcommand=sb.set)
        sb.pack(side="right", fill="y")
        canvas.pack(side="left", fill="both", expand=True)

        inner = ttk.Frame(canvas)
        win_id = canvas.create_window((0, 0), window=inner, anchor="nw")

        def _on_configure(event):
            canvas.configure(scrollregion=canvas.bbox("all"))
        def _on_canvas_resize(event):
            canvas.itemconfig(win_id, width=event.width)

        inner.bind("<Configure>", _on_configure)
        canvas.bind("<Configure>", _on_canvas_resize)

        # Scroll molette
        def _on_mousewheel(event):
            canvas.yview_scroll(int(-1 * (event.delta / 120)), "units")
        canvas.bind_all("<MouseWheel>", _on_mousewheel)

        self._build_content(inner)

    def _build_content(self, frame: ttk.Frame) -> None:
        # --- En-tête ---
        header = ttk.Frame(frame)
        header.pack(fill="x", padx=12, pady=(12, 4))
        ttk.Label(header, textvariable=self._title_var,
                  font=("", 13, "bold")).pack(side="left")

        # --- Barres HP / Énergie / PA / PM ---
        bars_frame = ttk.LabelFrame(frame, text="Vitalité & ressources")
        bars_frame.pack(fill="x", padx=12, pady=(4, 4))

        for label, key_c, key_m, color in _BARS:
            row = ttk.Frame(bars_frame)
            row.pack(fill="x", padx=8, pady=3)
            ttk.Label(row, text=label, width=8,
                      font=("", 9, "bold")).pack(side="left")
            cnv = tk.Canvas(row, width=_BAR_W, height=_BAR_H,
                            bg="#21262d", highlightthickness=1,
                            highlightbackground="#30363d")
            cnv.pack(side="left", padx=(4, 8))
            rect = cnv.create_rectangle(0, 0, 0, _BAR_H, fill=color, outline="")
            val_var = tk.StringVar(value="—")
            ttk.Label(row, textvariable=val_var, width=14,
                      font=("Consolas", 9)).pack(side="left")
            self._bars[label] = {
                "canvas": cnv, "rect": rect, "var": val_var,
                "key_c": key_c, "key_m": key_m, "color": color,
            }

        # --- Caractéristiques ---
        stats_frame = ttk.LabelFrame(frame, text="Caractéristiques")
        stats_frame.pack(fill="x", padx=12, pady=(4, 4))

        cols = 2
        for i, (label, key) in enumerate(_STAT_FIELDS):
            col_pair = (i % cols) * 2
            row = i // cols
            ttk.Label(stats_frame, text=label + " :", anchor="e",
                      width=14, font=("", 9, "bold")).grid(
                row=row, column=col_pair, padx=(12, 4), pady=2, sticky="e")
            var = tk.StringVar(value="—")
            self._vars[key] = var
            ttk.Label(stats_frame, textvariable=var,
                      anchor="w", width=22).grid(
                row=row, column=col_pair + 1, padx=(0, 18), pady=2, sticky="w")
        stats_frame.columnconfigure(1, weight=1)
        stats_frame.columnconfigure(3, weight=1)

        # --- Section auto-boost ---
        boost_frame = ttk.LabelFrame(frame, text="Auto-boost caractéristiques")
        boost_frame.pack(fill="x", padx=12, pady=(4, 12))

        # Points disponibles
        pts_row = ttk.Frame(boost_frame)
        pts_row.pack(fill="x", padx=8, pady=(6, 2))
        ttk.Label(pts_row, textvariable=self._points_var,
                  font=("", 10, "bold"), foreground="#d29922").pack(side="left")

        # Radiobuttons
        radios_frame = ttk.Frame(boost_frame)
        radios_frame.pack(fill="x", padx=8, pady=(2, 4))

        for col, (lbl, stat_id) in enumerate(_BOOST_OPTIONS):
            val = "None" if stat_id is None else str(stat_id)
            ttk.Radiobutton(
                radios_frame,
                text=lbl,
                variable=self._boost_var,
                value=val,
                command=self._on_radio_change,
            ).grid(row=0, column=col, padx=6, pady=2, sticky="w")

        # Bouton "Distribuer maintenant"
        btn_row = ttk.Frame(boost_frame)
        btn_row.pack(fill="x", padx=8, pady=(2, 8))
        ttk.Button(btn_row, text="Distribuer maintenant",
                   command=self._on_distribute).pack(side="left")
        self._pts_label_var = tk.StringVar(value="")
        ttk.Label(btn_row, textvariable=self._pts_label_var,
                  foreground="#888").pack(side="left", padx=8)

        # Charger la config persistée
        self._reload_boost_setting()

    # ------------------------------------------------------------------
    # Rafraîchissement
    # ------------------------------------------------------------------

    def refresh(self, data: dict) -> None:
        """Mettre à jour l'affichage depuis un dict de stats."""
        # Titre
        pseudo    = data.get("pseudo") or data.get("name", "")
        klass     = data.get("class_name", "")
        level     = data.get("level", "")
        if pseudo:
            self._title_var.set(f"{pseudo} — {klass} (Nv.{level})")

        # Points de caractéristiques disponibles
        stat_pts = data.get("stat_points")
        if stat_pts is not None:
            self._points_var.set(f"Points disponibles : {stat_pts}")
        else:
            self._points_var.set("Points disponibles : —")

        # Champs texte
        for key, var in self._vars.items():
            if key.startswith("_"):
                continue
            if key in data:
                val = data[key]
                if isinstance(val, list):
                    total = sum(val)
                    base  = val[0] if val else 0
                    var.set(f"{total}  (base {base})")
                else:
                    var.set(str(val))

        # Compléter max PA/PM (non envoyés par As)
        if "_max_pa" not in data:
            data = dict(data)
            data["_max_pa"] = _MAX_PA_DEFAULT
            data["_max_pm"] = _MAX_PM_DEFAULT

        # Barres
        for label, b in self._bars.items():
            cur = data.get(b["key_c"])
            mx  = data.get(b["key_m"])
            if isinstance(cur, list): cur = sum(cur)
            if isinstance(mx,  list): mx  = sum(mx)
            if cur is None or mx is None or mx == 0:
                b["var"].set("—")
                b["canvas"].coords(b["rect"], 0, 0, 0, _BAR_H)
                continue
            pct   = max(0.0, min(1.0, cur / mx))
            width = int(_BAR_W * pct)
            b["canvas"].coords(b["rect"], 0, 0, width, _BAR_H)
            b["var"].set(f"{cur} / {mx}")

    def update_tab_label(self, notebook: ttk.Notebook, label: str) -> None:
        """Mettre à jour le texte de l'onglet."""
        try:
            idx = notebook.index(self._frame)
            notebook.tab(idx, text=label)
        except Exception:
            pass

    # ------------------------------------------------------------------
    # Auto-boost
    # ------------------------------------------------------------------

    def _reload_boost_setting(self) -> None:
        """Charger depuis bot_settings.json et cocher le bon radio."""
        stat_id = _load_auto_boost(self._get_account(), self._char_id)
        self._boost_var.set("None" if stat_id is None else str(stat_id))

    def _on_radio_change(self) -> None:
        """Sauvegarder la sélection du radio dans bot_settings.json."""
        val = self._boost_var.get()
        stat_id = None if val == "None" else int(val)
        _save_auto_boost(self._get_account(), self._char_id, stat_id)

    def _on_distribute(self) -> None:
        """Distribuer manuellement les points disponibles."""
        val = self._boost_var.get()
        if val == "None":
            self._pts_label_var.set("⚠ Aucune stat sélectionnée")
            return

        # Lire le nombre de points depuis l'affichage en cours
        pts_text = self._points_var.get()
        try:
            points = int(pts_text.split(":")[-1].strip())
        except (ValueError, IndexError):
            self._pts_label_var.set("⚠ Points inconnus")
            return

        if points <= 0:
            self._pts_label_var.set("⚠ Aucun point disponible")
            return

        stat_id = int(val)
        try:
            from core.session import launch_in_session
            launch_in_session(
                self._session,
                lambda: _send_boost(self._char_id, stat_id, points),
            )
            self._pts_label_var.set(f"✓ Envoyé : +{points} pts → stat {stat_id}")
        except Exception as exc:
            self._pts_label_var.set(f"Erreur : {exc}")
            logger.warning("[PersonnageTab] Boost erreur : %s", exc)


# ---------------------------------------------------------------------------
# PersonnageTab — onglet principal
# ---------------------------------------------------------------------------

class PersonnageTab:
    """Onglet 'Personnage' du dashboard : sub-tabs par personnage."""

    def __init__(self, notebook: ttk.Notebook, root: tk.Tk, session) -> None:
        self._root      = root
        self._session   = session
        self._bridge    = session.bridge
        self._account: str | None = None   # pseudo du perso principal (connu après "character")

        # Onglet externe
        self._outer_frame = ttk.Frame(notebook)
        notebook.add(self._outer_frame, text="Personnage")

        # Notebook interne (un sub-tab par personnage)
        self._inner_nb = ttk.Notebook(self._outer_frame)
        self._inner_nb.pack(fill="both", expand=True)

        # Panels par char_id
        self._panels: dict[str, _CharPanel] = {}
        self._main_char_id: str | None = None

        # Abonnements bridge
        self._bridge.subscribe(
            "character",
            lambda d: root.after(0, self._on_character, d),
        )
        self._bridge.subscribe(
            "hero_update",
            lambda d: root.after(0, self._on_hero_update, d),
        )

    # ------------------------------------------------------------------
    # Callbacks bridge
    # ------------------------------------------------------------------

    def _on_character(self, data: dict) -> None:
        """Mise à jour du personnage principal."""
        char_id = data.get("character_id")
        if not char_id:
            return

        pseudo = data.get("pseudo", "?")
        self._account     = pseudo
        self._main_char_id = str(char_id)

        panel = self._get_or_create_panel(
            char_id=str(char_id),
            tab_label=self._make_label(data),
            is_hero=False,
        )
        panel.refresh(data)
        # Mettre à jour le label de l'onglet
        panel.update_tab_label(self._inner_nb, self._make_label(data))

    def _on_hero_update(self, data: dict) -> None:
        """Mise à jour d'un héros."""
        hero_id = str(data.get("hero_id", ""))
        if not hero_id:
            return

        panel = self._get_or_create_panel(
            char_id=hero_id,
            tab_label=self._make_label(data),
            is_hero=True,
        )
        panel.refresh(data)
        panel.update_tab_label(self._inner_nb, self._make_label(data))

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _make_label(self, data: dict) -> str:
        name  = data.get("pseudo") or data.get("name", "?")
        klass = data.get("class_name", "")
        level = data.get("level", "")
        if klass and level:
            return f"{name} ({klass} {level})"
        if klass:
            return f"{name} ({klass})"
        return name

    def _get_or_create_panel(
        self,
        char_id: str,
        tab_label: str,
        is_hero: bool,
    ) -> _CharPanel:
        if char_id in self._panels:
            return self._panels[char_id]

        panel = _CharPanel(
            notebook=self._inner_nb,
            tab_label=tab_label,
            root=self._root,
            session=self._session,
            char_id=char_id,
            account_getter=lambda: self._account,
        )
        self._panels[char_id] = panel
        return panel
