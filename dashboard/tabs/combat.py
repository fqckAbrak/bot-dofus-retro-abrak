"""
Onglet Combat — lancement du bot de farming + séquence de sorts + vue héros + stats session.
"""

from __future__ import annotations

import json
import logging
import os
import tkinter as tk
from tkinter import ttk

import bot as _bot
from dashboard import bridge

logger = logging.getLogger(__name__)

_SETTINGS_PATH = os.path.normpath(
    os.path.join(os.path.dirname(__file__), "..", "..", "bot_settings.json")
)


def _fmt_kamas(k: int) -> str:
    return f"{k:,}".replace(",", " ")


_TARGET_LABELS = {
    "enemy": "Ennemi",
    "self":  "Soi-même",
    "aoe2":  "AOE 2 cases",
    "aoe3":  "AOE 3 cases",
}
_TARGET_KEYS = {v: k for k, v in _TARGET_LABELS.items()}


# ---------------------------------------------------------------------------
# Persistance par compte
# ---------------------------------------------------------------------------
# Structure de bot_settings.json (multi-instance) :
#   {
#     "defaults": { <réglages combat par défaut> },
#     "accounts": { "<perso principal>": { <réglages propres au compte> } }
#   }
# La config effective d'un compte = defaults surchargés par accounts[<perso>].
# L'ancien format plat (clés combat au top-level) est migré vers "defaults".

# Bornes de délai anti-suspicion (en millisecondes) et leurs valeurs par défaut.
_DELAY_DEFAULTS = {
    "combat_spectator_delay_min_ms": 600,
    "combat_spectator_delay_max_ms": 2000,
    "combat_player_delay_min_ms": 2000,
    "combat_player_delay_max_ms": 4000,
    "combat_ready_delay_min_ms": 100,
    "combat_ready_delay_max_ms": 500,
}


def _read_settings() -> dict:
    """Lire bot_settings.json normalisé en {'defaults': {...}, 'accounts': {...}}.

    Migre l'ancien format plat (clés combat au top-level) vers 'defaults'.
    """
    try:
        with open(_SETTINGS_PATH, "r", encoding="utf-8") as f:
            data = json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return {"defaults": {}, "accounts": {}}
    if not isinstance(data, dict):
        return {"defaults": {}, "accounts": {}}
    # Ancien format plat → migration vers "defaults"
    if "accounts" not in data and "defaults" not in data:
        return {"defaults": dict(data), "accounts": {}}
    data.setdefault("defaults", {})
    data.setdefault("accounts", {})
    if not isinstance(data["accounts"], dict):
        data["accounts"] = {}
    if not isinstance(data["defaults"], dict):
        data["defaults"] = {}
    return data


def _write_settings(data: dict) -> None:
    try:
        with open(_SETTINGS_PATH, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=4, ensure_ascii=False)
    except OSError as exc:
        logger.warning("[CombatTab] Sauvegarde bot_settings.json échouée : %s", exc)


def _account_view(account: str | None) -> dict:
    """Config effective d'un compte = defaults surchargés par accounts[<perso>]."""
    data = _read_settings()
    merged = dict(data.get("defaults", {}))
    if account:
        merged.update(data.get("accounts", {}).get(account, {}))
    return merged


def _save_setting(account: str | None, key: str, value) -> None:
    """Écrire une clé scalaire dans la config du compte (ou 'defaults' si account None)."""
    data = _read_settings()
    target = data["accounts"].setdefault(account, {}) if account else data["defaults"]
    target[key] = value
    _write_settings(data)


def _sanitize_spells(raw) -> list[dict]:
    """Normaliser une liste brute de sorts en [{spell_id, count, target}]."""
    if not isinstance(raw, list):
        return []
    out: list[dict] = []
    for s in raw:
        if isinstance(s, dict) and "spell_id" in s:
            out.append({
                "spell_id": s["spell_id"],
                "count": s.get("count", 1),
                "target": s.get("target", "enemy"),
            })
    return out


def _merged_spells_by_class(account: str | None) -> dict[str, list[dict]]:
    """Séquences par classe effectives = defaults fusionnés (par classe) avec le compte.

    La fusion est faite PAR CLASSE (et non par remplacement du dict entier) : une classe
    personnalisée dans le compte ne masque pas les autres classes définies dans defaults.
    """
    data = _read_settings()
    base = data.get("defaults", {}).get("attack_spells_by_class", {})
    merged: dict[str, list[dict]] = {}
    if isinstance(base, dict):
        merged.update(base)
    if account:
        acc = data.get("accounts", {}).get(account, {}).get("attack_spells_by_class", {})
        if isinstance(acc, dict):
            merged.update(acc)
    return merged


def _load_attack_spells_for_class(account: str | None, class_id: int) -> list[dict]:
    """Charger la séquence de sorts d'une classe pour le compte."""
    return _sanitize_spells(_merged_spells_by_class(account).get(str(class_id), []))


def _save_attack_spells_for_class(account: str | None, class_id: int, spells: list[dict]) -> None:
    """Sauvegarder la séquence de sorts d'une classe pour le compte."""
    data = _read_settings()
    target = data["accounts"].setdefault(account, {}) if account else data["defaults"]
    by_class = target.get("attack_spells_by_class")
    if not isinstance(by_class, dict):
        by_class = {}
        target["attack_spells_by_class"] = by_class
    by_class[str(class_id)] = [
        {"spell_id": s["spell_id"], "count": s["count"], "target": s.get("target", "enemy")}
        for s in spells
    ]
    _write_settings(data)


def _load_combat_behavior(account: str | None) -> str:
    return _account_view(account).get("combat_behavior", "distance")


def _save_combat_behavior(account: str | None, behavior: str) -> None:
    _save_setting(account, "combat_behavior", behavior)


def _load_max_monsters(account: str | None) -> int:
    try:
        val = int(_account_view(account).get("combat_max_monsters_per_group", 8))
    except (ValueError, TypeError):
        return 8
    return max(1, min(8, val))


def _save_max_monsters(account: str | None, value: int) -> None:
    _save_setting(account, "combat_max_monsters_per_group", int(value))


def _load_slow_when_player(account: str | None) -> bool:
    return bool(_account_view(account).get("combat_slow_when_player", False))


def _save_slow_when_player(account: str | None, enabled: bool) -> None:
    _save_setting(account, "combat_slow_when_player", bool(enabled))


def _load_delay_before_ready(account: str | None) -> bool:
    return bool(_account_view(account).get("combat_delay_before_ready", False))


# Modes de discrétion (cf. GameState.combat_discretion_mode / bot.combat) :
# libellé radio + description (affichée sous les radios) + couleur du texte.
_DISCRETION_MODES: dict[str, tuple[str, str, str]] = {
    "human": (
        "🧍 Humain (recommandé)",
        "Même cadence que le mode Farming (délais anti-rafale courts, ré-engagement "
        "quasi immédiat) — la protection vient d'irrégularités ponctuelles plutôt que "
        "d'une lenteur permanente : pause AFK toutes les 10-20 min (quelques secondes "
        "à quelques minutes, statut « Pause AFK » affiché), au plus 1 pause "
        "« distraction » par combat (10-60 s), misclick occasionnel, petits "
        "déplacements idle hors combat, farm suspendu dès qu'un joueur inconnu "
        "apparaît sur la carte. Mode par défaut.",
        "#27ae60",
    ),
    "farming": (
        "🌾 Farming",
        "⚠ RISQUE DE DÉTECTION. Délais anti-rafale courts (350-800 ms), ré-engagement "
        "quasi immédiat après chaque combat, pauses espacées configurables mais AUCUNE "
        "des irrégularités du mode Humain (pas de pause AFK, pas de distraction en "
        "combat, pas de farm suspendu si un joueur apparaît). Cadence très élevée "
        "(≈1 combat/20 s) sans aucun camouflage : c'est exactement le pattern repéré "
        "par un admin lors du ban du 09/07. À réserver aux sessions courtes et "
        "surveillées — préférer le mode Humain sinon (même vitesse, mais plus discret).",
        "#e67e22",
    ),
    "speed": (
        "⚡ Speed",
        "⚠ DANGER — RISQUE DE BAN MAXIMAL. Aucun ralentissement : casts en rafale, "
        "tours < 1 s, aucune pause. Les protections sans coût de vitesse restent "
        "actives (apprentissage portée/LdV, suivi PA tacle, arrêt sur déconnexion). "
        "À n'utiliser qu'en connaissance de cause.",
        "#c0392b",
    ),
}


def _load_discretion_mode(account: str | None) -> str:
    view = _account_view(account)
    mode = view.get("combat_discretion_mode")
    if mode in _DISCRETION_MODES:
        return mode
    # Migration : ancien booléen combat_speed_mode (avant l'arrivée des 3 modes).
    if bool(view.get("combat_speed_mode", False)):
        return "speed"
    return "human"


def _load_delay_settings(account: str | None) -> dict:
    """Charger les bornes de délai (ms) du compte (avec valeurs défaut)."""
    view = _account_view(account)
    out = {}
    for key, default in _DELAY_DEFAULTS.items():
        try:
            out[key] = max(0, int(view.get(key, default)))
        except (ValueError, TypeError):
            out[key] = default
    return out


def _config_to_sequence(config: list[dict]) -> list[tuple[int, int, str]]:
    """Convertir une config sauvegardée en séquence (spell_id, count, target) pour GameState."""
    return [
        (s["spell_id"], s.get("count", 1), s.get("target", "enemy"))
        for s in config if "spell_id" in s
    ]


class _ClassSpellEditor:
    """Éditeur de séquence de sorts pour UNE classe (un sous-onglet de « Sorts »).

    Encapsule la liste des lignes de sorts, le drag & drop de réordonnancement, et la
    persistance par compte/classe (attack_spells_by_class). Écrit la séquence dans
    GameState.attack_spell_sequences[class_id] que combat.py lit pour le lanceur de cette classe.
    """

    def __init__(self, notebook: ttk.Notebook, root: tk.Tk, session,
                 account: str | None, class_id: int, class_name: str) -> None:
        self._root = root
        self._session = session
        self._account = account
        self._class_id = class_id
        self._class_name = class_name

        # Sorts disponibles pour la classe (SL pour le perso principal, Nh pour les héros)
        self._spells: list[dict] = []
        # Lignes de sorts d'attaque configurées (UI dynamique)
        self._spell_rows: list[dict] = []
        # Ligne en cours de glissement (drag & drop), ou None
        self._drag_row: dict | None = None

        # Config sauvegardée pour cette classe (restaurée quand les sorts arrivent)
        self._saved_config: list[dict] = _load_attack_spells_for_class(account, class_id)
        # Pré-renseigner GameState dès maintenant (combat peut démarrer avant la MAJ des sorts)
        self._session.game_state.attack_spell_sequences[class_id] = _config_to_sequence(self._saved_config)

        self._frame = ttk.Frame(notebook)
        notebook.add(self._frame, text=class_name)
        self._build()

    # ------------------------------------------------------------------
    # Construction
    # ------------------------------------------------------------------

    def _build(self) -> None:
        spell_frame = ttk.LabelFrame(self._frame, text="Sorts d'attaque (ordre de lancement)")
        spell_frame.pack(fill="both", expand=True, padx=8, pady=(8, 4))

        self._spell_rows_container = ttk.Frame(spell_frame)
        self._spell_rows_container.pack(fill="x", padx=4, pady=(4, 0))

        btn_frame = ttk.Frame(spell_frame)
        btn_frame.pack(fill="x", padx=4, pady=(2, 4))
        ttk.Button(btn_frame, text="+ Ajouter un sort",
                   command=self._add_spell_row, width=20).pack(side="left")

        self._spell_detail_var = tk.StringVar(value="")
        ttk.Label(spell_frame, textvariable=self._spell_detail_var,
                  font=("", 8, "italic"), foreground="gray").pack(
            anchor="w", padx=8, pady=(0, 4))

        self._empty_var = tk.StringVar(value="")
        ttk.Label(self._frame, textvariable=self._empty_var, font=("", 8),
                  foreground="gray", wraplength=420, justify="left").pack(
            anchor="w", padx=12, pady=(0, 6))
        self._update_empty_note()

    def _update_empty_note(self) -> None:
        if not self._spells:
            self._empty_var.set(
                f"Sorts de la classe {self._class_name} pas encore reçus du serveur "
                "(arrivent à la connexion via SL/Nh). Reconnecte-toi si la liste reste vide."
            )
        else:
            self._empty_var.set("")

    # ------------------------------------------------------------------
    # Liste dynamique des lignes de sorts
    # ------------------------------------------------------------------

    def _build_spell_display_values(self) -> list[str]:
        values = []
        for s in self._spells:
            name = s.get("name", f"Sort #{s['spell_id']}")
            lvl = s.get("spell_level", 1)
            pa = s.get("cost_pa")
            pa_str = f"  [{pa} PA]" if pa is not None else ""
            values.append(f"{name} (Nv.{lvl}){pa_str}")
        return values

    def _spell_index_by_id(self, spell_id: int) -> int | None:
        for i, s in enumerate(self._spells):
            if s["spell_id"] == spell_id:
                return i
        return None

    def _add_spell_row(self, spell_id: int | None = None, count: int = 1,
                       target: str = "enemy") -> None:
        row_frame = ttk.Frame(self._spell_rows_container)
        row_frame.pack(fill="x", pady=1)

        grip = ttk.Label(row_frame, text="\u283f", width=2, anchor="center",
                         font=("", 11), cursor="fleur")
        grip.pack(side="left", padx=(0, 0))

        order_var = tk.StringVar(value=str(len(self._spell_rows) + 1))
        order_label = ttk.Label(row_frame, textvariable=order_var, width=3,
                                font=("", 9, "bold"), anchor="center")
        order_label.pack(side="left", padx=(0, 4))

        display_values = self._build_spell_display_values()
        spell_var = tk.StringVar(value="\u2014 choisir \u2014")
        spell_combo = ttk.Combobox(row_frame, textvariable=spell_var,
                                   state="readonly", width=30, values=display_values)
        spell_combo.pack(side="left", padx=2, fill="x", expand=True)

        target_label = _TARGET_LABELS.get(target, "Ennemi")
        target_var = tk.StringVar(value=target_label)
        target_combo = ttk.Combobox(row_frame, textvariable=target_var,
                                    state="readonly", width=13,
                                    values=list(_TARGET_LABELS.values()))
        target_combo.pack(side="left", padx=2)

        count_values = [f"x{i}" for i in range(1, 11)]
        count_var = tk.StringVar(value=f"x{count}")
        count_combo = ttk.Combobox(row_frame, textvariable=count_var,
                                   state="readonly", width=4, values=count_values)
        count_combo.pack(side="left", padx=2)

        row_data: dict = {}

        def _on_delete() -> None:
            self._remove_spell_row(row_data)

        del_btn = ttk.Button(row_frame, text="\u2212", width=3, command=_on_delete)
        del_btn.pack(side="left", padx=(2, 0))

        row_data.update({
            "frame": row_frame, "grip": grip,
            "spell_var": spell_var, "spell_combo": spell_combo,
            "target_var": target_var, "target_combo": target_combo,
            "count_var": count_var, "count_combo": count_combo,
            "order_var": order_var, "order_label": order_label, "del_btn": del_btn,
        })
        self._spell_rows.append(row_data)

        if spell_id is not None and self._spells:
            idx = self._spell_index_by_id(spell_id)
            if idx is not None:
                spell_combo.current(idx)

        spell_combo.bind("<<ComboboxSelected>>", lambda _e: self._on_row_changed(row_data))
        target_combo.bind("<<ComboboxSelected>>", lambda _e: self._on_row_changed(row_data))
        count_combo.bind("<<ComboboxSelected>>", lambda _e: self._on_row_changed(row_data))

        for handle in (grip, order_label):
            handle.bind("<ButtonPress-1>", lambda _e, rd=row_data: self._on_drag_start(rd))
            handle.bind("<B1-Motion>", self._on_drag_motion)
            handle.bind("<ButtonRelease-1>", self._on_drag_release)

    def _remove_spell_row(self, row_data: dict) -> None:
        if row_data in self._spell_rows:
            self._spell_rows.remove(row_data)
            row_data["frame"].destroy()
            self._renumber_rows()
            self._sync_to_state()
            self._save_config()

    def _renumber_rows(self) -> None:
        for i, row in enumerate(self._spell_rows):
            row["order_var"].set(str(i + 1))

    def _repack_rows(self) -> None:
        for row in self._spell_rows:
            row["frame"].pack_forget()
        for row in self._spell_rows:
            row["frame"].pack(fill="x", pady=1)

    def _on_drag_start(self, row_data: dict) -> None:
        self._drag_row = row_data
        row_data["frame"].configure(relief="ridge", borderwidth=1)

    def _on_drag_motion(self, _event: tk.Event) -> None:
        if self._drag_row is None or self._drag_row not in self._spell_rows:
            return
        container = self._spell_rows_container
        y = container.winfo_pointery() - container.winfo_rooty()
        new_idx = 0
        for row in self._spell_rows:
            if row is self._drag_row:
                continue
            frame = row["frame"]
            mid = frame.winfo_y() + frame.winfo_height() / 2
            if y > mid:
                new_idx += 1
        cur_idx = self._spell_rows.index(self._drag_row)
        if new_idx != cur_idx:
            self._spell_rows.pop(cur_idx)
            self._spell_rows.insert(new_idx, self._drag_row)
            self._repack_rows()
            self._renumber_rows()

    def _on_drag_release(self, _event: tk.Event) -> None:
        if self._drag_row is None:
            return
        self._drag_row["frame"].configure(relief="flat", borderwidth=0)
        self._drag_row = None
        self._sync_to_state()
        self._save_config()

    def _on_row_changed(self, row_data: dict) -> None:
        idx = row_data["spell_combo"].current()
        if 0 <= idx < len(self._spells):
            self._show_spell_details(self._spells[idx])
        self._sync_to_state()
        self._save_config()

    def _get_row_spell_id(self, row_data: dict) -> int | None:
        idx = row_data["spell_combo"].current()
        if 0 <= idx < len(self._spells):
            return self._spells[idx]["spell_id"]
        return None

    def _get_row_count(self, row_data: dict) -> int:
        val = row_data["count_var"].get()
        try:
            return int(val.lstrip("x"))
        except (ValueError, AttributeError):
            return 1

    def _get_row_target(self, row_data: dict) -> str:
        label = row_data["target_var"].get()
        return _TARGET_KEYS.get(label, "enemy")

    def _show_spell_details(self, spell: dict) -> None:
        parts = []
        pa = spell.get("cost_pa")
        if pa is not None:
            parts.append(f"PA:{pa}")
        rmin = spell.get("range_min", 0)
        rmax = spell.get("range_max", 0)
        if rmax > 0:
            parts.append(f"PO:{rmin}-{rmax}")
        if spell.get("modifiable_distance"):
            parts.append("+PO")
        if spell.get("vision_line"):
            parts.append("LdV")
        if spell.get("launch_inline"):
            parts.append("Ligne")
        per_turn = spell.get("launch_per_turn", 0)
        per_target = spell.get("launch_per_target", 0)
        if per_turn > 0:
            parts.append(f"Max {per_turn}/tour")
        if per_target > 0:
            parts.append(f"Max {per_target}/cible")
        cooldown = spell.get("cooldown", 0)
        if cooldown > 0:
            parts.append(f"CD:{cooldown}")
        self._spell_detail_var.set("  |  ".join(parts) if parts else "")

    # ------------------------------------------------------------------
    # Synchronisation state + persistance
    # ------------------------------------------------------------------

    def _sync_to_state(self) -> None:
        sequence: list[tuple[int, int, str]] = []
        for row in self._spell_rows:
            spell_id = self._get_row_spell_id(row)
            if spell_id is not None:
                sequence.append((spell_id, self._get_row_count(row), self._get_row_target(row)))
        self._session.game_state.attack_spell_sequences[self._class_id] = sequence

    def _save_config(self) -> None:
        spells: list[dict] = []
        for row in self._spell_rows:
            spell_id = self._get_row_spell_id(row)
            if spell_id is not None:
                spells.append({
                    "spell_id": spell_id,
                    "count": self._get_row_count(row),
                    "target": self._get_row_target(row),
                })
        _save_attack_spells_for_class(self._account, self._class_id, spells)

    def _restore_saved_config(self) -> None:
        if not self._saved_config:
            return
        for row in list(self._spell_rows):
            row["frame"].destroy()
        self._spell_rows.clear()
        for entry in self._saved_config:
            spell_id = entry.get("spell_id")
            if self._spell_index_by_id(spell_id) is not None:
                self._add_spell_row(spell_id=spell_id,
                                    count=entry.get("count", 1),
                                    target=entry.get("target", "enemy"))
        self._renumber_rows()
        self._sync_to_state()

    # ------------------------------------------------------------------
    # MAJ des sorts disponibles (depuis team_classes)
    # ------------------------------------------------------------------

    def set_spells(self, spells: list[dict]) -> None:
        """Mettre à jour la liste des sorts disponibles pour cette classe."""
        self._spells = spells or []
        self._update_empty_note()
        display_values = self._build_spell_display_values()

        for row in self._spell_rows:
            current_spell_id = self._get_row_spell_id(row)
            row["spell_combo"]["values"] = display_values
            if current_spell_id is not None:
                idx = self._spell_index_by_id(current_spell_id)
                if idx is not None:
                    row["spell_combo"].current(idx)

        # Première arrivée des sorts → restaurer la config sauvegardée
        if not self._spell_rows and self._saved_config and self._spells:
            self._restore_saved_config()


class CombatTab:
    """Onglet de contrôle du bot de combat groupe."""

    def __init__(self, notebook: ttk.Notebook, root: tk.Tk, session) -> None:
        self._root = root
        self._session = session
        self._bridge = session.bridge
        self._frame = ttk.Frame(notebook)
        notebook.add(self._frame, text="Combat")

        self._hero_iids: dict[str, str] = {}

        # Éditeurs de sorts par classe (un sous-onglet « Sorts » par classe de l'équipe).
        # Clé : class_id. Créés dynamiquement à la réception de l'événement team_classes.
        self._class_editors: dict[int, _ClassSpellEditor] = {}

        # Compte (perso principal) : clé de persistance par-compte dans bot_settings.json
        self._account: str | None = session.main_character

        # Comportement combat (distance par défaut)
        self._saved_behavior: str = _load_combat_behavior(self._account)

        # Nombre max de monstres par groupe (8 = pas de filtrage)
        self._saved_max_monsters: int = _load_max_monsters(self._account)

        # Ralentir le lancement des combats si un joueur est sur la carte
        self._saved_slow_when_player: bool = _load_slow_when_player(self._account)

        # Délai optionnel avant « prêt » (GR1) au lancement d'un combat
        self._saved_delay_before_ready: bool = _load_delay_before_ready(self._account)
        self._saved_discretion_mode: str = _load_discretion_mode(self._account)

        # Bornes de délai anti-suspicion (ms)
        self._saved_delays: dict = _load_delay_settings(self._account)

        self._build()
        self._subscribe()
        self._apply_saved_behavior()

    # ------------------------------------------------------------------
    # Construction
    # ------------------------------------------------------------------

    def _build(self) -> None:
        # Sous-notebook imbriqué dans l'onglet Combat
        sub_notebook = ttk.Notebook(self._frame)
        sub_notebook.pack(fill="both", expand=True, padx=4, pady=4)

        # --- Sous-onglet Infos ---
        info_tab = ttk.Frame(sub_notebook)
        sub_notebook.add(info_tab, text="Infos")
        self._build_info_tab(info_tab)

        # --- Sous-onglet Sorts ---
        spells_tab = ttk.Frame(sub_notebook)
        sub_notebook.add(spells_tab, text="Sorts")
        self._build_spells_tab(spells_tab)

        # --- Sous-onglet Comportement ---
        behavior_tab = ttk.Frame(sub_notebook)
        sub_notebook.add(behavior_tab, text="Comportement")
        self._build_behavior_tab(behavior_tab)

    def _build_info_tab(self, parent: ttk.Frame) -> None:
        """Construire le sous-onglet Infos : contrôle, héros, session."""

        # Contrôles start / stop
        ctrl_frame = ttk.LabelFrame(parent, text="Contrôle")
        ctrl_frame.pack(fill="x", padx=8, pady=(8, 4))

        self._status_var = tk.StringVar(value="Inactif")
        ttk.Label(ctrl_frame, textvariable=self._status_var,
                  font=("", 9, "italic")).pack(side="left", padx=8, pady=4)

        ttk.Button(ctrl_frame, text="▶ Démarrer",
                   command=self._start, width=13).pack(side="left", padx=4, pady=4)
        ttk.Button(ctrl_frame, text="■ Arrêter",
                   command=self._stop, width=13).pack(side="left", padx=2, pady=4)
        ttk.Button(ctrl_frame, text="⏹ Après combat",
                   command=self._stop_after_combat, width=15).pack(side="left", padx=2, pady=4)

        # Liste des héros
        heroes_frame = ttk.LabelFrame(parent, text="Héros")
        heroes_frame.pack(fill="both", expand=True, padx=8, pady=(0, 4))

        cols = ("name", "level", "hp", "kamas")
        self._tree = ttk.Treeview(
            heroes_frame, columns=cols, show="headings", height=10, selectmode="none"
        )
        self._tree.heading("name",  text="Nom")
        self._tree.heading("level", text="Niveau")
        self._tree.heading("hp",    text="Points de vie")
        self._tree.heading("kamas", text="Kamas")
        self._tree.column("name",  width=140, anchor="w")
        self._tree.column("level", width=60,  anchor="center")
        self._tree.column("hp",    width=130, anchor="center")
        self._tree.column("kamas", width=120, anchor="e")

        sb = ttk.Scrollbar(heroes_frame, orient="vertical", command=self._tree.yview)
        self._tree.configure(yscrollcommand=sb.set)
        self._tree.pack(side="left", fill="both", expand=True)
        sb.pack(side="right", fill="y")

        # Statistiques de session
        stats_frame = ttk.LabelFrame(parent, text="Session")
        stats_frame.pack(fill="x", padx=8, pady=(0, 8))

        self._fights_var = tk.StringVar(value="0")
        self._banks_var  = tk.StringVar(value="0")
        self._kamas_var  = tk.StringVar(value="—")

        grid = [
            ("Combats terminés :",  self._fights_var),
            ("Ouvertures banque :", self._banks_var),
            ("Kamas générés :",     self._kamas_var),
        ]
        for row, (label, var) in enumerate(grid):
            ttk.Label(stats_frame, text=label,
                      font=("", 9, "bold")).grid(row=row, column=0, sticky="w",
                                                  padx=10, pady=2)
            ttk.Label(stats_frame, textvariable=var,
                      font=("", 9)).grid(row=row, column=1, sticky="w",
                                         padx=4, pady=2)

    def _build_behavior_tab(self, parent: ttk.Frame) -> None:
        """Construire le sous-onglet Comportement : choix du mode de combat."""

        mode_frame = ttk.LabelFrame(parent, text="Mode de combat")
        mode_frame.pack(fill="x", padx=8, pady=(8, 4))

        ttk.Label(mode_frame, text="Comportement :",
                  font=("", 9, "bold")).grid(row=0, column=0, sticky="w",
                                              padx=10, pady=6)

        _BEHAVIOR_LABELS = {
            "distance": "Distance (Crâ / classe distance)",
            "rush_cac": "Rush Corps à Corps",
        }
        self._behavior_var = tk.StringVar(value=_BEHAVIOR_LABELS.get(self._saved_behavior, "Distance (Crâ / classe distance)"))
        self._behavior_labels = _BEHAVIOR_LABELS
        self._behavior_keys = list(_BEHAVIOR_LABELS.keys())

        behavior_combo = ttk.Combobox(
            mode_frame, textvariable=self._behavior_var,
            state="readonly", width=36,
            values=list(_BEHAVIOR_LABELS.values()),
        )
        behavior_combo.grid(row=0, column=1, sticky="w", padx=4, pady=6)
        behavior_combo.bind("<<ComboboxSelected>>", self._on_behavior_changed)

        # Description du mode sélectionné
        self._behavior_desc_var = tk.StringVar(value="")
        desc_label = ttk.Label(mode_frame, textvariable=self._behavior_desc_var,
                               font=("", 8), foreground="gray", wraplength=400,
                               justify="left")
        desc_label.grid(row=1, column=0, columnspan=2, sticky="w", padx=10, pady=(0, 8))
        self._update_behavior_description()

        # Sélection de la cible (taille des groupes)
        target_frame = ttk.LabelFrame(parent, text="Sélection des groupes")
        target_frame.pack(fill="x", padx=8, pady=(4, 4))

        ttk.Label(target_frame, text="Max monstres par groupe :",
                  font=("", 9, "bold")).grid(row=0, column=0, sticky="w",
                                              padx=10, pady=6)

        self._max_monsters_var = tk.StringVar(value=str(self._saved_max_monsters))
        max_monsters_combo = ttk.Combobox(
            target_frame, textvariable=self._max_monsters_var,
            state="readonly", width=4,
            values=[str(i) for i in range(1, 9)],
        )
        max_monsters_combo.grid(row=0, column=1, sticky="w", padx=4, pady=6)
        max_monsters_combo.bind("<<ComboboxSelected>>", self._on_max_monsters_changed)

        ttk.Label(target_frame,
                  text="Le bot attaque le plus gros groupe ≤ cette valeur. "
                       "8 = pas de limite (un groupe Dofus contient au plus 8 monstres).",
                  font=("", 8), foreground="gray", wraplength=400,
                  justify="left").grid(row=1, column=0, columnspan=2, sticky="w",
                                       padx=10, pady=(0, 8))

        # Mode de discrétion : humain (défaut) / farming (⚠) / speed (⚠⚠).
        discretion_mode_frame = ttk.LabelFrame(parent, text="🕵 Mode de discrétion")
        discretion_mode_frame.pack(fill="x", padx=8, pady=(4, 4))

        self._discretion_mode_var = tk.StringVar(value=self._saved_discretion_mode)
        for col, (mode_key, (label, _desc, _color)) in enumerate(_DISCRETION_MODES.items()):
            ttk.Radiobutton(
                discretion_mode_frame,
                text=label,
                value=mode_key,
                variable=self._discretion_mode_var,
                command=self._on_discretion_mode_changed,
            ).grid(row=0, column=col, sticky="w", padx=10, pady=(6, 2))

        self._discretion_desc_var = tk.StringVar(value="")
        self._discretion_desc_label = ttk.Label(
            discretion_mode_frame, textvariable=self._discretion_desc_var,
            font=("", 8), wraplength=420, justify="left",
        )
        self._discretion_desc_label.grid(row=1, column=0, columnspan=3, sticky="w",
                                         padx=10, pady=(0, 8))
        self._update_discretion_description()

        # Discrétion : ralentissements anti-suspicion + bornes de délai éditables (ms)
        discretion_frame = ttk.LabelFrame(parent, text="Discrétion")
        discretion_frame.pack(fill="x", padx=8, pady=(4, 4))

        # --- Joueur sur la carte : toggle + bornes du délai entre combats ---
        self._slow_when_player_var = tk.BooleanVar(value=self._saved_slow_when_player)
        ttk.Checkbutton(
            discretion_frame,
            text="Ralentir les combats si un joueur est sur la carte",
            variable=self._slow_when_player_var,
            command=self._on_slow_when_player_changed,
        ).grid(row=0, column=0, columnspan=4, sticky="w", padx=10, pady=(6, 2))

        self._play_min_var = tk.StringVar(value=str(self._saved_delays["combat_player_delay_min_ms"]))
        self._play_max_var = tk.StringVar(value=str(self._saved_delays["combat_player_delay_max_ms"]))
        ttk.Label(discretion_frame, text="Délai entre combats (ms) :",
                  font=("", 9)).grid(row=1, column=0, sticky="w", padx=(24, 4), pady=4)
        ttk.Spinbox(discretion_frame, from_=0, to=60000, increment=100, width=8,
                    textvariable=self._play_min_var,
                    command=self._on_delays_changed).grid(row=1, column=1, sticky="w", pady=4)
        ttk.Label(discretion_frame, text="à").grid(row=1, column=2, padx=4, pady=4)
        ttk.Spinbox(discretion_frame, from_=0, to=60000, increment=100, width=8,
                    textvariable=self._play_max_var,
                    command=self._on_delays_changed).grid(row=1, column=3, sticky="w", pady=4)

        # --- Spectateur en combat : bornes du délai entre actions ---
        self._spec_min_var = tk.StringVar(value=str(self._saved_delays["combat_spectator_delay_min_ms"]))
        self._spec_max_var = tk.StringVar(value=str(self._saved_delays["combat_spectator_delay_max_ms"]))
        ttk.Label(discretion_frame, text="Délai entre actions si spectateur (ms) :",
                  font=("", 9)).grid(row=2, column=0, sticky="w", padx=(10, 4), pady=4)
        ttk.Spinbox(discretion_frame, from_=0, to=60000, increment=100, width=8,
                    textvariable=self._spec_min_var,
                    command=self._on_delays_changed).grid(row=2, column=1, sticky="w", pady=4)
        ttk.Label(discretion_frame, text="à").grid(row=2, column=2, padx=4, pady=4)
        ttk.Spinbox(discretion_frame, from_=0, to=60000, increment=100, width=8,
                    textvariable=self._spec_max_var,
                    command=self._on_delays_changed).grid(row=2, column=3, sticky="w", pady=4)

        # --- Délai avant « prêt » au lancement d'un combat : toggle + bornes ---
        self._delay_before_ready_var = tk.BooleanVar(value=self._saved_delay_before_ready)
        ttk.Checkbutton(
            discretion_frame,
            text="Délai avant « Prêt » au lancement d'un combat",
            variable=self._delay_before_ready_var,
            command=self._on_delay_before_ready_changed,
        ).grid(row=3, column=0, columnspan=4, sticky="w", padx=10, pady=(6, 2))

        self._ready_min_var = tk.StringVar(value=str(self._saved_delays["combat_ready_delay_min_ms"]))
        self._ready_max_var = tk.StringVar(value=str(self._saved_delays["combat_ready_delay_max_ms"]))
        ttk.Label(discretion_frame, text="Délai avant prêt (ms) :",
                  font=("", 9)).grid(row=4, column=0, sticky="w", padx=(24, 4), pady=4)
        ttk.Spinbox(discretion_frame, from_=0, to=60000, increment=100, width=8,
                    textvariable=self._ready_min_var,
                    command=self._on_delays_changed).grid(row=4, column=1, sticky="w", pady=4)
        ttk.Label(discretion_frame, text="à").grid(row=4, column=2, padx=4, pady=4)
        ttk.Spinbox(discretion_frame, from_=0, to=60000, increment=100, width=8,
                    textvariable=self._ready_max_var,
                    command=self._on_delays_changed).grid(row=4, column=3, sticky="w", pady=4)

        # Valider aussi les valeurs tapées au clavier (Spinbox.command ne fire que sur flèches)
        for widget in discretion_frame.winfo_children():
            if isinstance(widget, ttk.Spinbox):
                widget.bind("<FocusOut>", self._on_delays_changed)
                widget.bind("<Return>", self._on_delays_changed)

        ttk.Label(discretion_frame,
                  text="Spectateur (Im 036) : délai aléatoire avant chaque sort/déplacement/fin "
                       "de tour, tant qu'on est observé, jusqu'à la fin du combat (doublé en "
                       "mode Humain). Joueur sur la carte : délai aléatoire avant d'engager "
                       "chaque combat (si la case est cochée — modes Farming/Speed uniquement : "
                       "en mode Humain le farm est automatiquement SUSPENDU tant qu'un joueur "
                       "inconnu est présent). Délai avant « Prêt » : délai aléatoire avant de "
                       "confirmer le placement (GR1) au début de chaque combat (si la case est "
                       "cochée — toujours actif en mode Humain). Bornes en millisecondes.",
                  font=("", 8), foreground="gray", wraplength=420,
                  justify="left").grid(row=5, column=0, columnspan=4, sticky="w",
                                       padx=10, pady=(2, 8))

    def _on_slow_when_player_changed(self) -> None:
        """Callback quand l'utilisateur (dé)coche le ralentissement si joueur présent."""
        enabled = bool(self._slow_when_player_var.get())
        _game_state = self._session.game_state
        _game_state.combat_slow_when_player = enabled
        _save_slow_when_player(self._account, enabled)
        logger.info("[CombatTab] Ralentir si joueur sur la carte → %s", enabled)

    def _on_delay_before_ready_changed(self) -> None:
        """Callback quand l'utilisateur (dé)coche le délai avant « prêt »."""
        enabled = bool(self._delay_before_ready_var.get())
        _game_state = self._session.game_state
        _game_state.combat_delay_before_ready = enabled
        _save_setting(self._account, "combat_delay_before_ready", enabled)
        logger.info("[CombatTab] Délai avant prêt → %s", enabled)

    def _on_discretion_mode_changed(self) -> None:
        """Callback quand l'utilisateur change le mode de discrétion."""
        mode = self._discretion_mode_var.get()
        if mode not in _DISCRETION_MODES:
            mode = "human"
        _game_state = self._session.game_state
        _game_state.combat_discretion_mode = mode
        _save_setting(self._account, "combat_discretion_mode", mode)
        self._update_discretion_description()
        if mode == "human":
            logger.info("[CombatTab] Mode de discrétion → human")
        else:
            logger.warning("[CombatTab] Mode de discrétion → %s (risque de détection)", mode)

    def _update_discretion_description(self) -> None:
        """Afficher la description (et sa couleur d'alerte) du mode sélectionné."""
        mode = self._discretion_mode_var.get()
        _label, desc, color = _DISCRETION_MODES.get(mode, _DISCRETION_MODES["human"])
        self._discretion_desc_var.set(desc)
        self._discretion_desc_label.configure(foreground=color)

    def _on_delays_changed(self, _event: object = None) -> None:
        """Callback quand une borne de délai (ms) change. Valide, applique et sauvegarde."""
        def _read(var: tk.StringVar, default: int) -> int:
            try:
                return max(0, int(float(var.get())))
            except (ValueError, TypeError):
                return default

        spec_min = _read(self._spec_min_var, _DELAY_DEFAULTS["combat_spectator_delay_min_ms"])
        spec_max = max(spec_min, _read(self._spec_max_var, _DELAY_DEFAULTS["combat_spectator_delay_max_ms"]))
        play_min = _read(self._play_min_var, _DELAY_DEFAULTS["combat_player_delay_min_ms"])
        play_max = max(play_min, _read(self._play_max_var, _DELAY_DEFAULTS["combat_player_delay_max_ms"]))
        ready_min = _read(self._ready_min_var, _DELAY_DEFAULTS["combat_ready_delay_min_ms"])
        ready_max = max(ready_min, _read(self._ready_max_var, _DELAY_DEFAULTS["combat_ready_delay_max_ms"]))

        # Refléter les valeurs normalisées (min ≤ max, entiers ≥ 0) dans l'UI
        self._spec_min_var.set(str(spec_min))
        self._spec_max_var.set(str(spec_max))
        self._play_min_var.set(str(play_min))
        self._play_max_var.set(str(play_max))
        self._ready_min_var.set(str(ready_min))
        self._ready_max_var.set(str(ready_max))

        _game_state = self._session.game_state
        _game_state.combat_spectator_delay_min_ms = spec_min
        _game_state.combat_spectator_delay_max_ms = spec_max
        _game_state.combat_player_delay_min_ms = play_min
        _game_state.combat_player_delay_max_ms = play_max
        _game_state.combat_ready_delay_min_ms = ready_min
        _game_state.combat_ready_delay_max_ms = ready_max

        _save_setting(self._account, "combat_spectator_delay_min_ms", spec_min)
        _save_setting(self._account, "combat_spectator_delay_max_ms", spec_max)
        _save_setting(self._account, "combat_player_delay_min_ms", play_min)
        _save_setting(self._account, "combat_player_delay_max_ms", play_max)
        _save_setting(self._account, "combat_ready_delay_min_ms", ready_min)
        _save_setting(self._account, "combat_ready_delay_max_ms", ready_max)
        logger.info(
            "[CombatTab] Délais MAJ : spectateur %d-%d ms, joueur %d-%d ms, prêt %d-%d ms",
            spec_min, spec_max, play_min, play_max, ready_min, ready_max,
        )

    def _on_max_monsters_changed(self, _event: object = None) -> None:
        """Callback quand l'utilisateur change le max de monstres par groupe."""
        try:
            value = int(self._max_monsters_var.get())
        except ValueError:
            value = 8
        value = max(1, min(8, value))

        _game_state = self._session.game_state
        _game_state.combat_max_monsters_per_group = value
        _save_max_monsters(self._account, value)
        logger.info("[CombatTab] Max monstres par groupe → %d", value)

    def _on_behavior_changed(self, _event: object = None) -> None:
        """Callback quand l'utilisateur change le mode de combat."""
        selected_label = self._behavior_var.get()
        behavior_key = "distance"
        for key, label in self._behavior_labels.items():
            if label == selected_label:
                behavior_key = key
                break

        _game_state = self._session.game_state
        _game_state.combat_behavior = behavior_key
        _save_combat_behavior(self._account, behavior_key)
        self._update_behavior_description()
        logger.info("[CombatTab] Comportement combat → %s", behavior_key)

    def _update_behavior_description(self) -> None:
        """Mettre à jour la description du mode sélectionné."""
        selected_label = self._behavior_var.get()
        behavior_key = "distance"
        for key, label in self._behavior_labels.items():
            if label == selected_label:
                behavior_key = key
                break

        descriptions = {
            "distance": (
                "Maintient la distance avec les monstres. Se rapproche uniquement du nombre "
                "de cases nécessaires pour être à portée. Évite le corps à corps et recule "
                "si un monstre s'approche trop. Optimise le placement pour ne pas bloquer "
                "les lignes de vue des alliés."
            ),
            "rush_cac": (
                "Se rapproche au maximum du monstre le plus proche pour le coller au corps "
                "à corps. Tape dès que possible."
            ),
        }
        self._behavior_desc_var.set(descriptions.get(behavior_key, ""))

    def _apply_saved_behavior(self) -> None:
        """Appliquer le comportement sauvegardé au GameState au démarrage."""
        _game_state = self._session.game_state
        _game_state.combat_behavior = self._saved_behavior
        _game_state.combat_max_monsters_per_group = self._saved_max_monsters
        _game_state.combat_slow_when_player = self._saved_slow_when_player
        _game_state.combat_delay_before_ready = self._saved_delay_before_ready
        _game_state.combat_discretion_mode = self._saved_discretion_mode
        _game_state.combat_spectator_delay_min_ms = self._saved_delays["combat_spectator_delay_min_ms"]
        _game_state.combat_spectator_delay_max_ms = self._saved_delays["combat_spectator_delay_max_ms"]
        _game_state.combat_player_delay_min_ms = self._saved_delays["combat_player_delay_min_ms"]
        _game_state.combat_player_delay_max_ms = self._saved_delays["combat_player_delay_max_ms"]
        _game_state.combat_ready_delay_min_ms = self._saved_delays["combat_ready_delay_min_ms"]
        _game_state.combat_ready_delay_max_ms = self._saved_delays["combat_ready_delay_max_ms"]

    def _build_spells_tab(self, parent: ttk.Frame) -> None:
        """Construire le sous-onglet Sorts : un sous-onglet par classe de l'équipe.

        Les sous-onglets (un par classe) sont créés dynamiquement à la réception de
        l'événement team_classes (perso principal + héros détectés à la connexion).
        """
        ttk.Label(
            parent,
            text="Chaque classe de l'équipe a sa propre séquence de sorts. "
                 "Tous les persos d'une même classe jouent la même séquence.",
            font=("", 8), foreground="gray", wraplength=440, justify="left",
        ).pack(anchor="w", padx=10, pady=(8, 2))

        self._spells_notebook = ttk.Notebook(parent)
        self._spells_notebook.pack(fill="both", expand=True, padx=4, pady=(0, 4))

        self._spells_placeholder = ttk.Label(
            parent, text="En attente de la détection des classes de l'équipe…",
            font=("", 9, "italic"), foreground="gray",
        )
        self._spells_placeholder.pack(anchor="w", padx=10, pady=4)

        # Réafficher les classes déjà connues (si l'onglet est créé après les packets).
        try:
            snapshot = self._bridge.get_state().get("team_classes", [])
        except Exception:
            snapshot = []
        if snapshot:
            self._refresh_team_classes(snapshot)

    def _ensure_class_tab(self, class_id: int, class_name: str) -> _ClassSpellEditor:
        """Créer (si besoin) le sous-onglet Sorts d'une classe et le retourner."""
        editor = self._class_editors.get(class_id)
        if editor is None:
            editor = _ClassSpellEditor(
                self._spells_notebook, self._root, self._session,
                self._account, class_id, class_name,
            )
            self._class_editors[class_id] = editor
            if self._spells_placeholder is not None:
                self._spells_placeholder.destroy()
                self._spells_placeholder = None
        return editor

    # ------------------------------------------------------------------
    # Bridge subscriptions
    # ------------------------------------------------------------------

    def _subscribe(self) -> None:
        self._bridge.subscribe("character",     self._on_character)
        self._bridge.subscribe("hero_update",   self._on_hero_update)
        self._bridge.subscribe("combat_stats",  self._on_combat_stats)
        self._bridge.subscribe("team_classes",  self._on_team_classes)
        self._bridge.subscribe("activity",      self._on_activity)

    # Statuts publiés par bot.combat (bridge.update_activity) → libellé affiché.
    _ACTIVITY_LABELS: dict[str, str] = {
        "farming": "🎯 Farming en cours",
        "combat": "⚔ En combat",
        "pause_afk": "💤 Pause AFK ({detail}) — comportement normal, pas un bug",
        "pause_combat": "📱 Petite pause en combat ({detail})",
    }

    def _on_activity(self, data: dict) -> None:
        self._root.after(0, lambda d=data: self._refresh_activity(d))

    def _refresh_activity(self, data: dict) -> None:
        status = data.get("status", "")
        detail = data.get("detail", "")
        label = self._ACTIVITY_LABELS.get(status)
        if label:
            self._status_var.set(label.format(detail=detail))

    def _on_character(self, data: dict) -> None:
        self._root.after(0, lambda d=data: self._refresh_main_row(d))

    def _on_hero_update(self, data: dict) -> None:
        self._root.after(0, lambda d=data: self._refresh_hero_row(d))

    def _on_combat_stats(self, data: dict) -> None:
        self._root.after(0, lambda d=data: self._refresh_stats(d))

    def _on_team_classes(self, data: list[dict]) -> None:
        self._root.after(0, lambda d=data: self._refresh_team_classes(d))

    # ------------------------------------------------------------------
    # Mise à jour des sous-onglets de sorts par classe
    # ------------------------------------------------------------------

    def _refresh_team_classes(self, classes: list[dict]) -> None:
        """Créer/mettre à jour un sous-onglet Sorts pour chaque classe de l'équipe."""
        for entry in classes:
            class_id = entry.get("class_id")
            if class_id is None:
                continue
            class_name = entry.get("class_name", f"#{class_id}")
            editor = self._ensure_class_tab(class_id, class_name)
            editor.set_spells(entry.get("spells", []))

    # ------------------------------------------------------------------
    # Mise à jour Treeview — perso principal
    # ------------------------------------------------------------------

    def _refresh_main_row(self, data: dict) -> None:
        cid   = data.get("character_id", "")
        name  = data.get("pseudo", "?")
        level = data.get("level", "?")
        life      = data.get("life")
        max_life  = data.get("max_life")
        kamas     = data.get("kamas")

        hp_str    = f"{life}/{max_life}" if life is not None and max_life else "—"
        kamas_str = _fmt_kamas(kamas) if kamas is not None else "—"
        iid = f"main_{cid}"

        if self._tree.exists(iid):
            self._tree.item(iid, values=(name, level, hp_str, kamas_str))
        else:
            self._tree.insert("", 0, iid=iid,
                              values=(name, level, hp_str, kamas_str))

    # ------------------------------------------------------------------
    # Mise à jour Treeview — héros (persistants, from Nx + As)
    # ------------------------------------------------------------------

    def _refresh_hero_row(self, data: dict) -> None:
        hero_id = data.get("hero_id", "")
        if not hero_id:
            return

        name    = data.get("name", "?")
        level   = data.get("level", "?")
        life     = data.get("life")
        max_life = data.get("max_life")
        kamas    = data.get("kamas")

        iid = f"hero_{hero_id}"

        # Conserver les valeurs existantes si les nouvelles données sont absentes
        if self._tree.exists(iid):
            cur = self._tree.item(iid, "values")
            if not name or name == "?":
                name = cur[0] if cur else "?"
            if not level or level == "?":
                level = cur[1] if len(cur) > 1 else "?"
            hp_str    = f"{life}/{max_life}" if life is not None else (cur[2] if len(cur) > 2 else "—")
            kamas_str = _fmt_kamas(kamas) if kamas is not None else (cur[3] if len(cur) > 3 else "—")
            self._tree.item(iid, values=(name, level, hp_str, kamas_str))
        else:
            hp_str    = f"{life}/{max_life}" if life is not None else "—"
            kamas_str = _fmt_kamas(kamas) if kamas is not None else "—"
            self._tree.insert("", "end", iid=iid,
                              values=(name, level, hp_str, kamas_str))
            self._hero_iids[hero_id] = iid

    # ------------------------------------------------------------------
    # Statistiques de session
    # ------------------------------------------------------------------

    def _refresh_stats(self, data: dict) -> None:
        self._fights_var.set(str(data.get("fights_completed", 0)))
        self._banks_var.set(str(data.get("bank_openings", 0)))

        start   = data.get("kamas_start")
        current = data.get("kamas_current", 0)
        if start is not None:
            diff = current - start
            sign = "+" if diff >= 0 else ""
            self._kamas_var.set(f"{sign}{_fmt_kamas(diff)}")
        else:
            self._kamas_var.set("—")

    # ------------------------------------------------------------------
    # Start / Stop
    # ------------------------------------------------------------------

    def _start(self) -> None:
        try:
            from bot.combat import is_running, start_bot
            if is_running(self._session):
                return
            from core.session import call_in_session
            call_in_session(self._session, start_bot)
            self._status_var.set("Bot combat : actif")
        except Exception as exc:
            self._status_var.set(f"Erreur : {exc}")

    def _stop(self) -> None:
        try:
            from bot.combat import stop_bot
            from core.session import call_in_session
            call_in_session(self._session, stop_bot)
            self._status_var.set("Inactif")
        except Exception as exc:
            self._status_var.set(f"Erreur : {exc}")

    def _stop_after_combat(self) -> None:
        try:
            from bot.combat import is_running, stop_after_combat
            if not is_running(self._session):
                return
            from core.session import call_in_session
            call_in_session(self._session, stop_after_combat)
            self._status_var.set("Arrêt après combat…")
        except Exception as exc:
            self._status_var.set(f"Erreur : {exc}")
