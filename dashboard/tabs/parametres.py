"""
Onglet Paramètres — configuration éditable (config.json).

Permet de modifier :
- IP et port du serveur Dofus cible
- IP et port d'écoute du proxy local
- Ports game à intercepter (WinDivert / Frida)
- Chemin vers le dossier d'installation Dofus Rétro (Abrak)

Les modifications sont sauvegardées sur disque mais nécessitent un
redémarrage du bot pour prendre effet (le proxy / divert / frida sont
configurés une seule fois au démarrage).
"""

from __future__ import annotations

import json
import logging
import os
import sys
import threading
import tkinter as tk
from tkinter import filedialog, messagebox, ttk

logger = logging.getLogger(__name__)

_CONFIG_PATH = os.path.normpath(
    os.path.join(os.path.dirname(__file__), "..", "..", "config.json")
)


def _load_config() -> dict:
    try:
        with open(_CONFIG_PATH, "r", encoding="utf-8") as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return {}


def _save_config(cfg: dict) -> None:
    with open(_CONFIG_PATH, "w", encoding="utf-8") as f:
        json.dump(cfg, f, indent=4, ensure_ascii=False)


class ParametresTab:
    """Onglet Paramètres — édition du fichier config.json."""

    def __init__(self, notebook: ttk.Notebook, root: tk.Tk, session=None) -> None:
        self._root = root
        self._session = session
        self._frame = ttk.Frame(notebook)
        notebook.add(self._frame, text="Paramètres")

        self._cfg = _load_config()
        self._build()

    # ------------------------------------------------------------------
    # Construction
    # ------------------------------------------------------------------

    def _build(self) -> None:
        info = ttk.Label(
            self._frame,
            text=(
                "Configuration du bot (config.json). "
                "Les modifications prennent effet au prochain démarrage."
            ),
            font=("", 9, "italic"),
            foreground="gray",
            wraplength=900,
            justify="left",
        )
        info.pack(anchor="w", padx=12, pady=(10, 4))

        # --- Serveur Dofus ---
        srv = ttk.LabelFrame(self._frame, text="Serveur Dofus (cible)")
        srv.pack(fill="x", padx=12, pady=(8, 4))

        self._server_host_var = tk.StringVar(value=str(self._cfg.get("server_host", "51.89.153.20")))
        self._server_port_var = tk.StringVar(value=str(self._cfg.get("server_port", 1303)))

        self._row(srv, 0, "IP du serveur :", self._server_host_var, width=22)
        self._row(srv, 1, "Port du serveur :", self._server_port_var, width=10)

        # --- Proxy local ---
        prx = ttk.LabelFrame(self._frame, text="Proxy local (MITM)")
        prx.pack(fill="x", padx=12, pady=(4, 4))

        self._proxy_host_var = tk.StringVar(value=str(self._cfg.get("proxy_host", "127.0.0.1")))
        self._proxy_port_var = tk.StringVar(value=str(self._cfg.get("proxy_port", 8080)))

        self._row(prx, 0, "IP d'écoute :", self._proxy_host_var, width=22)
        self._row(prx, 1, "Port d'écoute :", self._proxy_port_var, width=10)

        # --- Ports game à intercepter ---
        gp = ttk.LabelFrame(self._frame, text="Interception réseau")
        gp.pack(fill="x", padx=12, pady=(4, 4))

        game_ports = self._cfg.get("game_ports", [1303, 1304])
        self._game_ports_var = tk.StringVar(value=",".join(str(p) for p in game_ports))

        ttk.Label(gp, text="Ports game (séparés par virgule) :",
                  font=("", 9, "bold")).grid(row=0, column=0, sticky="w", padx=10, pady=6)
        ttk.Entry(gp, textvariable=self._game_ports_var, width=22).grid(
            row=0, column=1, sticky="w", padx=4, pady=6)
        ttk.Label(gp, text="Ex: 1303,1304 — interceptés par WinDivert ou Frida.",
                  font=("", 8), foreground="gray").grid(
            row=1, column=0, columnspan=3, sticky="w", padx=10, pady=(0, 6))

        # --- Notifications ---
        notif = ttk.LabelFrame(self._frame, text="Notifications")
        notif.pack(fill="x", padx=12, pady=(4, 4))

        self._notif_var = tk.BooleanVar(value=bool(self._cfg.get("notifications_enabled", True)))
        ttk.Checkbutton(
            notif, text="Afficher les notifications (toasts) — combats, banque, kicks, etc.",
            variable=self._notif_var, command=self._on_notif_changed,
        ).grid(row=0, column=0, sticky="w", padx=10, pady=6)

        # --- Réponse auto aux MP ---
        ar = ttk.LabelFrame(self._frame, text="Réponse automatique aux MP")
        ar.pack(fill="x", padx=12, pady=(4, 4))

        self._auto_reply_var = tk.BooleanVar(value=bool(self._cfg.get("auto_reply_enabled", False)))
        ttk.Checkbutton(
            ar, text="Répondre automatiquement aux messages privés reçus (anti-suspicion bot)",
            variable=self._auto_reply_var, command=self._on_auto_reply_changed,
        ).grid(row=0, column=0, sticky="w", padx=10, pady=6)
        ttk.Label(ar, text="À laisser désactivé si tu réponds toi-même aux MP. Prend effet immédiatement.",
                  font=("", 8), foreground="gray").grid(
            row=1, column=0, columnspan=3, sticky="w", padx=10, pady=(0, 6))

        # --- Chemin Dofus ---
        gpath = ttk.LabelFrame(self._frame, text="Chemin du jeu")
        gpath.pack(fill="x", padx=12, pady=(4, 4))

        self._game_path_var = tk.StringVar(value=str(self._cfg.get("game_path", "")))

        ttk.Label(gpath, text="Dossier Dofus Rétro (Abrak) :",
                  font=("", 9, "bold")).grid(row=0, column=0, sticky="w", padx=10, pady=6)
        ttk.Entry(gpath, textvariable=self._game_path_var, width=60).grid(
            row=0, column=1, sticky="we", padx=4, pady=6)
        ttk.Button(gpath, text="Parcourir…", command=self._browse_game_path).grid(
            row=0, column=2, sticky="w", padx=4, pady=6)
        ttk.Label(gpath, text="Dossier contenant Abrak.exe et les SWF du client.",
                  font=("", 8), foreground="gray").grid(
            row=1, column=0, columnspan=3, sticky="w", padx=10, pady=(0, 6))
        gpath.columnconfigure(1, weight=1)

        # --- No-anim (déplacements instantanés) ---
        na = ttk.LabelFrame(self._frame, text="No-anim — déplacements instantanés")
        na.pack(fill="x", padx=12, pady=(4, 4))

        self._noanim_var = tk.BooleanVar(value=False)
        self._noanim_chk = ttk.Checkbutton(
            na,
            text="Déplacements instantanés (monstres et joueurs) — patche le core.swf du client",
            variable=self._noanim_var, command=self._on_noanim_changed,
        )
        self._noanim_chk.grid(row=0, column=0, columnspan=3, sticky="w", padx=10, pady=(6, 2))

        self._ffdec_path_var = tk.StringVar(value=str(self._cfg.get("ffdec_path", "")))
        ttk.Label(na, text="Chemin ffdec-cli.jar (JPEXS) :", font=("", 8)).grid(
            row=1, column=0, sticky="w", padx=10, pady=2)
        ttk.Entry(na, textvariable=self._ffdec_path_var, width=50).grid(
            row=1, column=1, sticky="we", padx=4, pady=2)
        ttk.Button(na, text="Parcourir…", command=self._browse_ffdec_path).grid(
            row=1, column=2, sticky="w", padx=4, pady=2)
        ttk.Label(
            na,
            text="Optionnel — laisse vide pour auto-détection (variable FFDEC_JAR ou "
                 "emplacements standards). Nécessaire pour générer la variante no-anim.",
            font=("", 8), foreground="gray",
        ).grid(row=2, column=0, columnspan=3, sticky="w", padx=10, pady=(0, 4))

        self._noanim_build_btn = ttk.Button(
            na, text="🛠 Patcher no-anim (générer la variante)", command=self._on_build_noanim,
        )
        self._noanim_build_btn.grid(row=3, column=0, columnspan=2, sticky="w", padx=10, pady=(2, 6))

        self._noanim_status_var = tk.StringVar(value="")
        ttk.Label(na, textvariable=self._noanim_status_var, font=("", 8),
                  foreground="gray", wraplength=860, justify="left").grid(
            row=4, column=0, columnspan=3, sticky="w", padx=10, pady=(0, 6))
        na.columnconfigure(1, weight=1)
        self._refresh_noanim()

        # --- Actions ---
        actions = ttk.Frame(self._frame)
        actions.pack(fill="x", padx=12, pady=(8, 12))

        self._status_var = tk.StringVar(value="")
        ttk.Label(actions, textvariable=self._status_var,
                  font=("", 9, "italic"), foreground="darkgreen").pack(side="left", padx=4)

        ttk.Button(actions, text="↻ Recharger", command=self._reload,
                   width=14).pack(side="right", padx=2)
        ttk.Button(actions, text="💾 Enregistrer", command=self._save,
                   width=14).pack(side="right", padx=2)
        ttk.Button(actions, text="🔄 Redémarrer le bot", command=self._restart_bot,
                   width=20).pack(side="right", padx=(2, 10))

    def _row(self, parent: ttk.Widget, row: int, label: str,
             var: tk.StringVar, width: int = 20) -> None:
        ttk.Label(parent, text=label, font=("", 9, "bold")).grid(
            row=row, column=0, sticky="w", padx=10, pady=4)
        ttk.Entry(parent, textvariable=var, width=width).grid(
            row=row, column=1, sticky="w", padx=4, pady=4)

    # ------------------------------------------------------------------
    # Actions
    # ------------------------------------------------------------------

    def _browse_game_path(self) -> None:
        initial = self._game_path_var.get() or os.path.expanduser("~")
        chosen = filedialog.askdirectory(
            parent=self._root,
            title="Sélectionner le dossier Dofus Rétro (Abrak)",
            initialdir=initial if os.path.isdir(initial) else os.path.expanduser("~"),
        )
        if chosen:
            self._game_path_var.set(os.path.normpath(chosen))

    def _browse_ffdec_path(self) -> None:
        initial = self._ffdec_path_var.get().strip()
        initial_dir = os.path.dirname(initial) if initial else os.path.expanduser("~")
        chosen = filedialog.askopenfilename(
            parent=self._root,
            title="Sélectionner ffdec-cli.jar",
            initialdir=initial_dir if os.path.isdir(initial_dir) else os.path.expanduser("~"),
            filetypes=[("FFDec JAR", "*.jar"), ("Tous les fichiers", "*.*")],
        )
        if chosen:
            self._ffdec_path_var.set(os.path.normpath(chosen))

    def _reload(self) -> None:
        self._cfg = _load_config()
        self._server_host_var.set(str(self._cfg.get("server_host", "51.89.153.20")))
        self._server_port_var.set(str(self._cfg.get("server_port", 1303)))
        self._proxy_host_var.set(str(self._cfg.get("proxy_host", "127.0.0.1")))
        self._proxy_port_var.set(str(self._cfg.get("proxy_port", 8080)))
        self._game_ports_var.set(",".join(str(p) for p in self._cfg.get("game_ports", [1303, 1304])))
        self._game_path_var.set(str(self._cfg.get("game_path", "")))
        self._ffdec_path_var.set(str(self._cfg.get("ffdec_path", "")))
        self._notif_var.set(bool(self._cfg.get("notifications_enabled", True)))
        self._auto_reply_var.set(bool(self._cfg.get("auto_reply_enabled", False)))
        self._refresh_noanim()
        self._status_var.set("Rechargé depuis le disque.")
        self._root.after(3000, lambda: self._status_var.set(""))

    # ------------------------------------------------------------------
    # No-anim (déplacements instantanés via swap du core.swf)
    # ------------------------------------------------------------------

    def _current_game_path(self) -> str:
        """Chemin du jeu effectif : l'entrée si valide, sinon la valeur sauvée."""
        entered = self._game_path_var.get().strip()
        if entered and os.path.isdir(entered):
            return entered
        return str(self._cfg.get("game_path", "") or "")

    def _refresh_noanim(self) -> None:
        from bot import noanim as _noanim

        st = _noanim.status(self._current_game_path())
        # .set() ne déclenche pas la `command` du Checkbutton (seul le clic le fait).
        self._noanim_var.set(bool(st.enabled))
        if not st.modules_dir:
            self._noanim_chk.state(["disabled"])
            self._noanim_status_var.set(
                "core.swf introuvable — vérifie le chemin du jeu ci-dessus.")
        elif not st.variant_available:
            self._noanim_chk.state(["disabled"])
            self._noanim_status_var.set(
                "Variante core.noanim.swf absente. Génère-la : "
                "python tools/build_noanim_swf.py")
        else:
            self._noanim_chk.state(["!disabled"])
            self._noanim_status_var.set(
                ("Activé." if st.enabled else "Désactivé.")
                + " Prend effet au prochain lancement du client.")

    def _on_noanim_changed(self) -> None:
        from bot import noanim as _noanim

        want = bool(self._noanim_var.get())
        ok, msg = _noanim.set_enabled(self._current_game_path(), want)
        if not ok:
            self._noanim_var.set(not want)  # revert visuel
            messagebox.showwarning("No-anim", msg, parent=self._root)
        self._noanim_status_var.set(msg)
        logger.info("[ParametresTab] no-anim %s : %s", want, msg)

    def _on_build_noanim(self) -> None:
        """Génère core.noanim.swf en arrière-plan (appelle FFDec, peut prendre 10-30s)."""
        from bot import noanim as _noanim

        game_path = self._current_game_path()
        ffdec_path = self._ffdec_path_var.get().strip() or None

        if not game_path or not os.path.isdir(game_path):
            messagebox.showwarning(
                "No-anim", "Renseigne d'abord un chemin de jeu valide ci-dessus.",
                parent=self._root,
            )
            return

        self._noanim_build_btn.state(["disabled"])
        self._noanim_chk.state(["disabled"])
        self._noanim_status_var.set("Génération en cours…")

        def _progress(msg: str) -> None:
            self._root.after(0, lambda: self._noanim_status_var.set(msg))

        def _worker() -> None:
            try:
                ok, msg = _noanim.generate_variant(game_path, ffdec_path, progress=_progress)
            except Exception as exc:  # garde-fou : ne jamais planter le thread GUI
                ok, msg = False, f"Erreur inattendue : {exc}"
                logger.exception("[ParametresTab] échec génération no-anim")
            self._root.after(0, lambda: self._on_build_noanim_done(ok, msg))

        threading.Thread(target=_worker, daemon=True, name="noanim-build").start()

    def _on_build_noanim_done(self, ok: bool, msg: str) -> None:
        self._noanim_build_btn.state(["!disabled"])
        logger.info("[ParametresTab] génération no-anim : ok=%s msg=%s", ok, msg)
        if ok:
            messagebox.showinfo(
                "No-anim", msg + "\n\nActive-la via la case à cocher ci-dessus.",
                parent=self._root,
            )
        else:
            messagebox.showwarning("No-anim — génération échouée", msg, parent=self._root)
        # Remis à jour APRÈS la messagebox (elle-même a déjà affiché le message
        # complet) : reflète l'état réel (variante dispo, activée ou non).
        self._refresh_noanim()

    def _on_notif_changed(self) -> None:
        """Sauver immédiatement la pref notifications + mettre à jour le runtime."""
        enabled = bool(self._notif_var.get())
        cfg = _load_config()
        cfg["notifications_enabled"] = enabled
        try:
            _save_config(cfg)
            self._cfg = cfg
        except OSError as exc:
            logger.warning("[ParametresTab] save notif failed: %s", exc)
            return
        try:
            from dashboard import notifications as _notif
            _notif.set_enabled(enabled)
        except Exception:
            pass

    def _on_auto_reply_changed(self) -> None:
        """Sauver immédiatement la pref réponse auto MP + mettre à jour le runtime."""
        enabled = bool(self._auto_reply_var.get())
        cfg = _load_config()
        cfg["auto_reply_enabled"] = enabled
        try:
            _save_config(cfg)
            self._cfg = cfg
        except OSError as exc:
            logger.warning("[ParametresTab] save auto_reply failed: %s", exc)
            return
        try:
            import bot.auto_reply as _auto_reply
            _auto_reply.set_enabled(enabled)
        except Exception:
            pass

    def _restart_bot(self) -> None:
        """Redémarrer complètement le processus Python (relance main.py)."""
        if not messagebox.askyesno(
            "Redémarrer le bot",
            "Redémarrer le bot maintenant ?\n"
            "Toutes les sessions de combat/récolte en cours seront interrompues.",
            parent=self._root,
        ):
            return
        logger.info("[ParametresTab] Redémarrage demandé par l'utilisateur.")
        try:
            python = sys.executable
            os.execv(python, [python] + sys.argv)
        except Exception as exc:
            messagebox.showerror(
                "Redémarrage impossible",
                f"Erreur : {exc}\nRelance manuellement `python main.py`.",
                parent=self._root,
            )

    def _save(self) -> None:
        try:
            server_port = int(self._server_port_var.get().strip())
            proxy_port = int(self._proxy_port_var.get().strip())
        except ValueError:
            messagebox.showerror(
                "Paramètres invalides",
                "Les ports doivent être des entiers.",
                parent=self._root,
            )
            return

        raw_ports = [p.strip() for p in self._game_ports_var.get().split(",") if p.strip()]
        try:
            game_ports = [int(p) for p in raw_ports]
        except ValueError:
            messagebox.showerror(
                "Paramètres invalides",
                "Les ports game doivent être une liste d'entiers (ex: 1303,1304).",
                parent=self._root,
            )
            return
        if not game_ports:
            messagebox.showerror(
                "Paramètres invalides",
                "Au moins un port game est requis.",
                parent=self._root,
            )
            return

        cfg = _load_config()
        cfg["server_host"] = self._server_host_var.get().strip() or "127.0.0.1"
        cfg["server_port"] = server_port
        cfg["proxy_host"] = self._proxy_host_var.get().strip() or "127.0.0.1"
        cfg["proxy_port"] = proxy_port
        cfg["game_ports"] = game_ports
        cfg["game_path"] = self._game_path_var.get().strip()
        cfg["ffdec_path"] = self._ffdec_path_var.get().strip()
        cfg["notifications_enabled"] = bool(self._notif_var.get())
        cfg["auto_reply_enabled"] = bool(self._auto_reply_var.get())

        try:
            _save_config(cfg)
        except OSError as exc:
            messagebox.showerror(
                "Sauvegarde impossible",
                f"Erreur d'écriture de config.json : {exc}",
                parent=self._root,
            )
            return

        self._cfg = cfg
        self._refresh_noanim()
        self._status_var.set("Enregistré — redémarrer le bot pour appliquer.")
        logger.info("[ParametresTab] config.json sauvegardé.")
        self._root.after(5000, lambda: self._status_var.set(""))
