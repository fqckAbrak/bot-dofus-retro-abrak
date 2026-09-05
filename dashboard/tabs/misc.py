"""
Onglet « Misc » — fonctionnalités diverses regroupées dans un sous-notebook.

Contient pour l'instant :
  * Vendre au marchand — vente automatique des équipements en sac à un PNJ
    marchand ambulant (filtre type/niveau + blacklist).
"""

from __future__ import annotations

import tkinter as tk
from tkinter import ttk

from dashboard.tabs.misc_vendre import VendreMarchandTab
from dashboard.tabs.misc_autopilot import AutopilotTestTab


class MiscTab:
    """Onglet conteneur regroupant les fonctionnalités diverses (sous-onglets)."""

    def __init__(self, notebook: ttk.Notebook, root: tk.Tk, session) -> None:
        self._root = root
        self._session = session
        self._frame = ttk.Frame(notebook)
        notebook.add(self._frame, text="Misc")

        self._sub = ttk.Notebook(self._frame)
        self._sub.pack(fill="both", expand=True, padx=2, pady=2)

        # Sous-onglets
        self._vendre = VendreMarchandTab(self._sub, root, session)
        self._autopilot = AutopilotTestTab(self._sub, root, session)
