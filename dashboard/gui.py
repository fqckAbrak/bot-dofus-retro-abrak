"""
Interface tkinter — fenêtre principale du bot.

Layout :
  ┌─────────────────────────────────────────┐
  │ StatusBar (Flash | Perso | Map | Combat)│
  ├─────────────────────────────────────────┤
  │ Notebook                                │
  │  ├─ Personnage / Carte / Inventaire …  │
  │  └─ …                                   │
  └─────────────────────────────────────────┘
  + Toasts en bas-droite (Toplevel)

Les onglets s'abonnent au bridge pour leurs mises à jour ; les notifications
toast écoutent le topic "notification".
"""

from __future__ import annotations

import json
import logging
import os
import threading
import tkinter as tk
from tkinter import ttk

from dashboard import bridge, notifications
from dashboard.statusbar import StatusBar
from dashboard.tabs.personnage import PersonnageTab
from dashboard.tabs.carte import CarteTab
from dashboard.tabs.inventaire import InventaireTab
from dashboard.tabs.metiers import MetiersTab
from dashboard.tabs.scripts import ScriptsTab
from dashboard.tabs.misc import MiscTab
from dashboard.tabs.hdv import HdvTab
from dashboard.tabs.console import ConsoleTab
from dashboard.tabs.combat import CombatTab
from dashboard.tabs.parametres import ParametresTab

logger = logging.getLogger("gui")

_CONFIG_PATH = os.path.normpath(
    os.path.join(os.path.dirname(__file__), "..", "config.json")
)


def _load_notif_enabled() -> bool:
    try:
        with open(_CONFIG_PATH, "r", encoding="utf-8") as f:
            return bool(json.load(f).get("notifications_enabled", True))
    except (FileNotFoundError, json.JSONDecodeError):
        return True


# ---------------------------------------------------------------------------
# Thème sombre
# ---------------------------------------------------------------------------

_BG          = "#0d1117"
_BG_PANEL    = "#161b22"
_BG_INPUT    = "#21262d"
_BG_HOVER    = "#1f6feb"
_FG          = "#c9d1d9"
_FG_DIM      = "#8b949e"
_ACCENT      = "#1f6feb"
_BORDER      = "#30363d"


def _apply_dark_theme(root: tk.Tk) -> None:
    """Configurer un thème sombre cohérent (GitHub dark inspiré)."""
    root.configure(bg=_BG)

    style = ttk.Style(root)
    try:
        style.theme_use("clam")
    except tk.TclError:
        pass

    # Frame / Label
    style.configure("TFrame", background=_BG)
    style.configure("TLabel", background=_BG, foreground=_FG)
    style.configure("TLabelframe", background=_BG, foreground=_FG, bordercolor=_BORDER)
    style.configure("TLabelframe.Label", background=_BG, foreground=_FG)

    # Notebook
    style.configure("TNotebook", background=_BG, borderwidth=0, tabmargins=[2, 4, 2, 0])
    style.configure(
        "TNotebook.Tab",
        background=_BG_PANEL, foreground=_FG_DIM,
        padding=[12, 5], borderwidth=0,
    )
    style.map(
        "TNotebook.Tab",
        background=[("selected", _BG)],
        foreground=[("selected", _FG)],
    )

    # Buttons
    style.configure(
        "TButton",
        background=_BG_INPUT, foreground=_FG,
        bordercolor=_BORDER, borderwidth=1, padding=4,
    )
    style.map(
        "TButton",
        background=[("active", _BG_HOVER), ("pressed", _ACCENT)],
        foreground=[("active", "#ffffff")],
    )

    # Entry / Combobox
    style.configure(
        "TEntry",
        fieldbackground=_BG_INPUT, foreground=_FG,
        insertcolor=_FG, bordercolor=_BORDER,
    )
    style.configure(
        "TCombobox",
        fieldbackground=_BG_INPUT, background=_BG_INPUT,
        foreground=_FG, bordercolor=_BORDER, arrowcolor=_FG,
    )
    style.map(
        "TCombobox",
        fieldbackground=[("readonly", _BG_INPUT)],
        foreground=[("readonly", _FG)],
    )
    root.option_add("*TCombobox*Listbox.background", _BG_INPUT)
    root.option_add("*TCombobox*Listbox.foreground", _FG)
    root.option_add("*TCombobox*Listbox.selectBackground", _ACCENT)

    # Checkbutton
    style.configure(
        "TCheckbutton",
        background=_BG, foreground=_FG,
        indicatorcolor=_BG_INPUT,
    )
    style.map(
        "TCheckbutton",
        background=[("active", _BG)],
        indicatorcolor=[("selected", _ACCENT)],
    )

    # Treeview
    style.configure(
        "Treeview",
        background=_BG_PANEL, foreground=_FG,
        fieldbackground=_BG_PANEL, bordercolor=_BORDER,
        rowheight=22,
    )
    style.configure(
        "Treeview.Heading",
        background=_BG_INPUT, foreground=_FG,
        bordercolor=_BORDER, relief="flat",
    )
    style.map(
        "Treeview",
        background=[("selected", _ACCENT)],
        foreground=[("selected", "#ffffff")],
    )
    style.map(
        "Treeview.Heading",
        background=[("active", _BG_HOVER)],
    )

    # Scrollbar
    style.configure(
        "TScrollbar",
        background=_BG_PANEL, troughcolor=_BG,
        bordercolor=_BORDER, arrowcolor=_FG,
    )

    # Progressbar
    style.configure(
        "TProgressbar",
        background=_ACCENT, troughcolor=_BG_INPUT,
        bordercolor=_BORDER,
    )

    # PanedWindow sashes
    style.configure("TPanedwindow", background=_BG)

    # tk natives (toplevels, menus) — pour les messageboxes & menus contextuels
    root.option_add("*Background", _BG)
    root.option_add("*Foreground", _FG)


# ---------------------------------------------------------------------------
# Démarrage
# ---------------------------------------------------------------------------

def start() -> None:
    """Lancer la GUI dans un thread daemon."""
    t = threading.Thread(target=_run, daemon=True, name="gui")
    t.start()
    logger.info("GUI tkinter démarrée (thread daemon)")


# ---------------------------------------------------------------------------
# Vue d'une team (un client Dofus) : StatusBar + Notebook complet, lié à sa Session
# ---------------------------------------------------------------------------

# Couleurs des pastilles de team (reprend la palette de la status bar)
_PILL_COLORS = {
    "connected":    "#3fb950",   # vert
    "combat":       "#d29922",   # orange (connecté + en combat)
    "disconnected": "#f85149",   # rouge
    "connecting":   "#8b949e",   # gris
}
_DOT = "●"


class TeamView:
    """Conteneur d'onglets dédié à une session (une team)."""

    def __init__(self, parent: tk.Misc, root: tk.Tk, session) -> None:
        self.session = session
        self._frame = ttk.Frame(parent)

        # Status bar propre à la team
        StatusBar(self._frame, root, session)

        notebook = ttk.Notebook(self._frame)
        notebook.pack(fill="both", expand=True, padx=4, pady=4)

        # Onglets — chacun s'abonne au bridge de la session
        PersonnageTab(notebook, root, session)
        CarteTab(notebook, root, session)
        CombatTab(notebook, root, session)
        InventaireTab(notebook, root, session)
        MetiersTab(notebook, root, session)
        ScriptsTab(notebook, root, session)
        MiscTab(notebook, root, session)
        HdvTab(notebook, root, session)
        ParametresTab(notebook, root, session)
        ConsoleTab(notebook, root, session)

    def show(self) -> None:
        self._frame.pack(fill="both", expand=True)

    def hide(self) -> None:
        self._frame.pack_forget()

    def destroy(self) -> None:
        self._frame.destroy()


class _DashboardApp:
    """Contrôleur principal : barre de teams (haut) + vue de la team sélectionnée."""

    def __init__(self, root: tk.Tk) -> None:
        self._root = root
        self._views: dict[int, TeamView] = {}
        self._buttons: dict[int, dict] = {}   # session_id → {frame, dot, name}
        self._selected: int | None = None

        # --- Barre de teams (tout en haut, au-dessus des onglets) ---
        self._bar = tk.Frame(root, bg=_BG_PANEL, height=34)
        self._bar.pack(fill="x", side="top")
        self._bar.pack_propagate(False)
        tk.Label(self._bar, text="Teams :", bg=_BG_PANEL, fg=_FG_DIM,
                 font=("", 9, "bold"), padx=8).pack(side="left")

        # --- Zone des vues de team ---
        self._container = ttk.Frame(root)
        self._container.pack(fill="both", expand=True)

        self._placeholder = ttk.Label(
            self._container,
            text="En attente de connexion d'un client Dofus…\n"
                 "Lance un ou plusieurs clients : une team apparaîtra ici par client.",
            anchor="center", justify="center", font=("", 11),
        )
        self._placeholder.pack(expand=True)

        # Abonnements lifecycle (évènements émis depuis le thread asyncio)
        bridge.subscribe_lifecycle(
            "session_added", lambda d: root.after(0, self._on_session_added, d))
        bridge.subscribe_lifecycle(
            "session_status", lambda d: root.after(0, self._on_session_status, d))
        bridge.subscribe_lifecycle(
            "session_removed", lambda d: root.after(0, self._on_session_removed, d))

        # Réconcilier les sessions déjà créées avant l'abonnement (course au démarrage)
        root.after(200, self._reconcile_existing)
        # Rafraîchissement périodique des pastilles (état combat)
        root.after(1000, self._poll_pills)

    # -- réconciliation initiale ------------------------------------------
    def _reconcile_existing(self) -> None:
        try:
            from core.session import manager
            for s in manager.all():
                # Seules les sessions ayant un perso (onglet) sont affichées :
                # les connexions d'auth (sans perso) sont ignorées.
                if s.ui_registered and s.session_id not in self._views:
                    self._on_session_added({"session_id": s.session_id})
                    self._on_session_status({
                        "session_id": s.session_id, "status": s.status,
                        "character": s.main_character, "level": s.character_level,
                    })
        except Exception:
            pass

    # -- lifecycle ---------------------------------------------------------
    def _on_session_added(self, data: dict) -> None:
        sid = data.get("session_id")
        if sid is None or sid in self._views:
            return
        from core.session import manager
        session = manager.get(sid)
        if session is None:
            return

        self._placeholder.pack_forget()
        self._views[sid] = TeamView(self._container, self._root, session)
        self._add_team_button(sid, label="Connexion…", color="connecting")
        if self._selected is None:
            self._select(sid)

    def _on_session_status(self, data: dict) -> None:
        sid = data.get("session_id")
        if sid not in self._buttons:
            return
        status = data.get("status", "connecting")
        character = data.get("character")
        level = data.get("level")

        # Réconciliation reconnect : si une AUTRE team déconnectée porte le même
        # perso, la retirer (évite les doublons quand un client se reconnecte).
        if status == "connected" and character:
            for other_id, view in list(self._views.items()):
                if other_id != sid and view.session.status == "disconnected" \
                        and view.session.main_character == character:
                    self._remove_team(other_id)

        label = character or ("Déconnecté" if status == "disconnected" else "Connexion…")
        if character and level is not None:
            label = f"{character} (Nv.{level})"
        color = {"connected": "connected", "disconnected": "disconnected"}.get(status, "connecting")
        self._update_team_button(sid, label=label, color=color)

    def _on_session_removed(self, data: dict) -> None:
        self._remove_team(data.get("session_id"))

    # -- barre de teams ----------------------------------------------------
    def _add_team_button(self, sid: int, label: str, color: str) -> None:
        f = tk.Frame(self._bar, bg=_BG_INPUT, padx=2)
        f.pack(side="left", padx=3, pady=3)

        dot = tk.Label(f, text=_DOT, bg=_BG_INPUT, fg=_PILL_COLORS[color], font=("", 10))
        dot.pack(side="left", padx=(6, 2))

        name = tk.Label(f, text=label, bg=_BG_INPUT, fg=_FG, font=("", 9),
                        padx=2, cursor="hand2")
        name.pack(side="left")

        close = tk.Label(f, text="✕", bg=_BG_INPUT, fg=_FG_DIM, font=("", 9),
                         padx=6, cursor="hand2")
        close.pack(side="left")

        for w in (f, dot, name):
            w.bind("<Button-1>", lambda _e, s=sid: self._select(s))
        close.bind("<Button-1>", lambda _e, s=sid: self._close_team(s))

        self._buttons[sid] = {"frame": f, "dot": dot, "name": name, "color": color}

    def _update_team_button(self, sid: int, label: str, color: str) -> None:
        b = self._buttons.get(sid)
        if not b:
            return
        b["name"].configure(text=label)
        b["dot"].configure(fg=_PILL_COLORS[color])
        b["color"] = color

    def _select(self, sid: int) -> None:
        if sid not in self._views:
            return
        for other, view in self._views.items():
            if other != sid:
                view.hide()
                self._highlight(other, selected=False)
        self._views[sid].show()
        self._highlight(sid, selected=True)
        self._selected = sid

    def _highlight(self, sid: int, selected: bool) -> None:
        b = self._buttons.get(sid)
        if not b:
            return
        bg = _ACCENT if selected else _BG_INPUT
        b["frame"].configure(bg=bg)
        for key in ("dot", "name"):
            b[key].configure(bg=bg)

    def _close_team(self, sid: int) -> None:
        """Fermer manuellement une team (depuis le ✕). Retire la session du registre."""
        try:
            from core.session import manager
            manager.remove(sid)   # émet session_removed → _remove_team
        except Exception:
            self._remove_team(sid)

    def _remove_team(self, sid: int) -> None:
        if sid is None:
            return
        view = self._views.pop(sid, None)
        if view is not None:
            view.destroy()
        b = self._buttons.pop(sid, None)
        if b is not None:
            b["frame"].destroy()
        if self._selected == sid:
            self._selected = None
            if self._views:
                self._select(next(iter(self._views)))
            else:
                self._placeholder.pack(expand=True)

    # -- pastilles : refléter l'état combat en plus de la connexion -------
    def _poll_pills(self) -> None:
        for sid, b in self._buttons.items():
            view = self._views.get(sid)
            if view is None:
                continue
            s = view.session
            if s.status == "disconnected":
                color = "disconnected"
            elif s.status == "connected":
                in_combat = False
                try:
                    in_combat = bool(s.game_state.in_combat)
                except Exception:
                    pass
                color = "combat" if in_combat else "connected"
            else:
                color = "connecting"
            if color != b.get("color"):
                b["dot"].configure(fg=_PILL_COLORS[color])
                b["color"] = color
        self._root.after(1000, self._poll_pills)


def _run() -> None:
    root = tk.Tk()
    root.title("Dofus Rétro Bot — Multi-team")
    root.geometry("1100x760")
    root.minsize(900, 600)

    _apply_dark_theme(root)

    # Notifications globales (toast) — bus lifecycle global
    notifications.init(root, enabled=_load_notif_enabled())
    bridge.subscribe_lifecycle(
        "notification",
        lambda d: root.after(0, lambda: notifications.show(d.get("message", ""), d.get("level", "info"))),
    )

    _DashboardApp(root)

    logger.info("Fenêtre GUI prête")
    root.mainloop()
    logger.info("GUI fermée — arrêt du bot")
    # La GUI tourne dans un thread daemon ; le thread principal est bloqué dans
    # serve_forever(). Sans ça, fermer la fenêtre laisserait le process tourner
    # en zombie (port 8080 occupé). os._exit termine tout le process.
    os._exit(0)
