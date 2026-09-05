"""
Onglet Scripts — sélection et lancement de scripts automatisés.

Les scripts dans le dossier scripts/ sont découverts automatiquement.
Les modes "single map" et "multi-map" (exploration) restent accessibles
en tant qu'entrées spéciales.
"""

from __future__ import annotations

import tkinter as tk
from tkinter import ttk

import bot as _bot
from dashboard import bridge


# job_id → label affiché dans l'UI
_JOBS: list[tuple[int, str]] = [
    (2,  "Bûcheron"),
    (11, "Mineur"),
    (6,  "Paysan"),
    (14, "Alchimiste"),
    (10, "Pêcheur"),
]

# Entrées "built-in" (non issues du dossier scripts/)
_BUILTIN_SCRIPTS: list[tuple[str, str, str]] = [
    # (key, label, description)
    ("single", "Récolte single map",   "Récolte la map courante, attend le respawn"),
]


def _load_script_entries() -> list[tuple[str, str, str]]:
    """Retourner [(key, label, description), ...] en combinant builtins + scripts/."""
    entries = list(_BUILTIN_SCRIPTS)
    try:
        from bot.script_engine import list_scripts
        for name, desc in list_scripts():
            label = name.replace("_", " ").title()
            entries.append((f"script:{name}", label, desc or f"Script {name}"))
    except Exception:
        pass
    return entries


class ScriptsTab:
    """Onglet de gestion des scripts automatisés."""

    def __init__(self, notebook: ttk.Notebook, root: tk.Tk, session) -> None:
        self._root = root
        self._session = session
        self._bridge = session.bridge
        self._frame = ttk.Frame(notebook)
        notebook.add(self._frame, text="Scripts")

        self._active_key: str | None = None
        self._entries: list[tuple[str, str, str]] = _load_script_entries()
        self._build()

    # ------------------------------------------------------------------
    # Construction de l'interface
    # ------------------------------------------------------------------

    def _build(self) -> None:
        header = ttk.Frame(self._frame)
        header.pack(fill="x", padx=8, pady=(8, 2))
        ttk.Label(header, text="Scripts", font=("", 11, "bold")).pack(side="left")

        # Sélection du script
        script_frame = ttk.LabelFrame(self._frame, text="Script")
        script_frame.pack(fill="x", padx=8, pady=(4, 4))

        labels = [label for _, label, _ in self._entries]
        self._script_combo = ttk.Combobox(
            script_frame,
            values=labels,
            state="readonly",
            width=40,
        )
        self._script_combo.current(0)
        self._script_combo.pack(anchor="w", padx=8, pady=(6, 2))
        self._script_combo.bind("<<ComboboxSelected>>", self._on_script_changed)

        first_desc = self._entries[0][2] if self._entries else ""
        self._desc_var = tk.StringVar(value=first_desc)
        ttk.Label(
            script_frame, textvariable=self._desc_var, font=("", 8, "italic"),
            foreground="gray",
        ).pack(anchor="w", padx=12, pady=(0, 6))

        # Filtre métiers
        jobs_frame = ttk.LabelFrame(self._frame, text="Métiers à récolter")
        jobs_frame.pack(fill="x", padx=8, pady=(0, 4))

        self._job_vars: dict[int, tk.BooleanVar] = {}
        for job_id, label in _JOBS:
            var = tk.BooleanVar(value=True)
            self._job_vars[job_id] = var
            ttk.Checkbutton(jobs_frame, text=label, variable=var).pack(
                anchor="w", padx=8, pady=1
            )

        # Contrôles
        ctrl_frame = ttk.Frame(self._frame)
        ctrl_frame.pack(fill="x", padx=8, pady=(0, 4))

        self._status_var = tk.StringVar(value="Inactif")
        ttk.Label(ctrl_frame, textvariable=self._status_var, font=("", 9, "italic")).pack(
            side="left", padx=8, pady=4
        )

        ttk.Button(ctrl_frame, text="▶ Démarrer", command=self._start, width=12).pack(
            side="left", padx=4, pady=4
        )
        ttk.Button(ctrl_frame, text="■ Arrêter", command=self._stop, width=12).pack(
            side="left", padx=2, pady=4
        )

        # Bouton pour recharger la liste (si on ajoute un script à chaud)
        ttk.Button(ctrl_frame, text="↺", command=self._reload_scripts, width=3).pack(
            side="right", padx=4, pady=4
        )

    # ------------------------------------------------------------------
    # Events
    # ------------------------------------------------------------------

    def _on_script_changed(self, _event) -> None:
        idx = self._script_combo.current()
        if 0 <= idx < len(self._entries):
            self._desc_var.set(self._entries[idx][2])

    def _reload_scripts(self) -> None:
        """Recharger la liste des scripts depuis le dossier scripts/."""
        self._entries = _load_script_entries()
        labels = [label for _, label, _ in self._entries]
        self._script_combo["values"] = labels
        self._script_combo.current(0)
        self._desc_var.set(self._entries[0][2] if self._entries else "")

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _get_selected_key(self) -> str:
        idx = self._script_combo.current()
        if 0 <= idx < len(self._entries):
            return self._entries[idx][0]
        return "single"

    def _get_allowed_jobs(self) -> set[int] | None:
        selected = {jid for jid, var in self._job_vars.items() if var.get()}
        if len(selected) == len(self._job_vars):
            return None
        return selected if selected else None

    # ------------------------------------------------------------------
    # Démarrer / Arrêter
    # ------------------------------------------------------------------

    def _start(self) -> None:
        key = self._get_selected_key()
        allowed_jobs = self._get_allowed_jobs()
        from core.session import call_in_session

        try:
            if key == "single":
                from bot.harvester import start_bot, is_running
                if is_running(self._session):
                    return
                call_in_session(self._session, lambda: start_bot(allowed_jobs))
                self._active_key = key
                self._status_var.set("Récolte single map : actif")

            elif key == "multi":
                from bot.harvester_multimap import start_bot, is_running
                if is_running(self._session):
                    return
                call_in_session(self._session, lambda: start_bot(allowed_jobs))
                self._active_key = key
                self._status_var.set("Récolte multi-map : actif")

            elif key.startswith("script:"):
                script_name = key[len("script:"):]
                from bot.script_engine import start_bot, is_running
                if is_running(self._session):
                    return
                call_in_session(
                    self._session, lambda: start_bot(script_name, allowed_jobs)
                )
                self._active_key = key
                label = next(
                    (lbl for k, lbl, _ in self._entries if k == key), script_name
                )
                self._status_var.set(f"{label} : actif")

        except Exception as exc:
            self._status_var.set(f"Erreur : {exc}")

    def _stop(self) -> None:
        from core.session import call_in_session
        try:
            if self._active_key == "single":
                from bot.harvester import stop_bot
                call_in_session(self._session, stop_bot)

            elif self._active_key == "multi":
                from bot.harvester_multimap import stop_bot
                call_in_session(self._session, stop_bot)

            elif self._active_key is not None and self._active_key.startswith("script:"):
                from bot.script_engine import stop_bot
                call_in_session(self._session, stop_bot)

            else:
                # Arrêt global en cas de doute (cette team)
                from bot.harvester import stop_bot as s1
                from bot.script_engine import stop_bot as s3
                call_in_session(self._session, s1)
                call_in_session(self._session, s3)

            self._active_key = None
            self._status_var.set("Inactif")

        except Exception as exc:
            self._status_var.set(f"Erreur : {exc}")
