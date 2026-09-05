"""
Bridge thread-safe entre le loop asyncio (game state) et la GUI tkinter.

MULTI-INSTANCE
──────────────
Chaque `Session` possède son propre `Bridge` (état + abonnés), de sorte que
chaque team alimente ses propres onglets. Les handlers asyncio appellent les
**fonctions module** `update_*()` qui se résolvent automatiquement sur le
`Bridge` de la session active (via le contextvar de core.session). L'UI, elle,
s'abonne explicitement au `Bridge` de la team qu'elle affiche
(`session.bridge.subscribe(...)`).

Un **bus lifecycle global** (indépendant des sessions) sert aux évènements
de cycle de vie des teams (`session_added` / `session_status` /
`session_removed`) et aux notifications toast globales.
"""

from __future__ import annotations

import copy
import threading
import time
from typing import Callable


def _initial_state() -> dict:
    return {
        "character": None,
        "map": None,
        "resources": [],
        "entities": [],
        "console": [],
        "packets": [],
        "inventory": [],
        "jobs": [],
        "harvest_log": [],
        "heroes": {},
        "spells": [],
        "team_classes": [],
        "weight": {"current": 0, "max": 0},
        "combat_stats": {
            "fights_completed": 0,
            "bank_openings": 0,
            "kamas_start": None,
            "kamas_current": 0,
        },
        "activity": {"status": "idle", "detail": ""},
    }


class Bridge:
    """Bus pub/sub thread-safe d'une session (une team)."""

    # Nombre max de packets conservés dans le buffer (anneau).
    MAX_PACKETS = 500

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._state: dict = _initial_state()
        self._callbacks: dict[str, list[Callable]] = {}

    # -- pub/sub -----------------------------------------------------------
    def subscribe(self, topic: str, fn: Callable) -> None:
        self._callbacks.setdefault(topic, []).append(fn)

    def _notify(self, topic: str, data) -> None:
        for fn in self._callbacks.get(topic, []):
            try:
                fn(data)
            except Exception:
                pass

    # -- écriture (depuis le thread asyncio) -------------------------------
    def update_character(self, data: dict) -> None:
        stats_snapshot = None
        with self._lock:
            if self._state["character"] is None:
                self._state["character"] = {}
            self._state["character"].update(data)
            snapshot = dict(self._state["character"])
            if "kamas" in data:
                kamas = data["kamas"]
                if self._state["combat_stats"]["kamas_start"] is None:
                    self._state["combat_stats"]["kamas_start"] = kamas
                self._state["combat_stats"]["kamas_current"] = kamas
                stats_snapshot = dict(self._state["combat_stats"])
        self._notify("character", snapshot)
        if stats_snapshot is not None:
            self._notify("combat_stats", stats_snapshot)

    def update_map(self, data: dict) -> None:
        with self._lock:
            self._state["map"] = data
            snapshot = dict(data)
        self._notify("map", snapshot)

    def update_resources(self, resources: list[dict]) -> None:
        with self._lock:
            self._state["resources"] = resources
            snapshot = list(resources)
        self._notify("resources", snapshot)

    def set_entity(self, entity: dict) -> None:
        with self._lock:
            eid = entity.get("entity_id")
            self._state["entities"] = [e for e in self._state["entities"] if e.get("entity_id") != eid]
            self._state["entities"].append(entity)
            snapshot = dict(entity)
        self._notify("entity_set", snapshot)

    def remove_entity(self, entity_id: str) -> None:
        with self._lock:
            self._state["entities"] = [e for e in self._state["entities"] if e.get("entity_id") != entity_id]
        self._notify("entity_remove", entity_id)

    def clear_entities(self) -> None:
        with self._lock:
            self._state["entities"] = []
        self._notify("entities_clear", None)

    def update_hero(self, hero_id: str, data: dict) -> None:
        with self._lock:
            if hero_id not in self._state["heroes"]:
                self._state["heroes"][hero_id] = {"hero_id": hero_id}
            self._state["heroes"][hero_id].update(data)
            snapshot = dict(self._state["heroes"][hero_id])
        self._notify("hero_update", snapshot)

    def add_console(self, msg: str) -> None:
        entry = {"ts": time.strftime("%H:%M:%S"), "msg": msg}
        with self._lock:
            self._state["console"].insert(0, entry)
            self._state["console"] = self._state["console"][:60]
        self._notify("console", entry)

    def add_packet(self, entry: dict) -> None:
        """Enregistrer un packet réseau (S→C / C→S) pour l'onglet Packets.

        `entry` attend les clés : direction ("RECV"/"SENT"), msg_id, msg_name,
        length, content. Le timestamp est ajouté ici.
        """
        record = {"ts": time.strftime("%H:%M:%S"), **entry}
        with self._lock:
            self._state["packets"].insert(0, record)
            self._state["packets"] = self._state["packets"][: self.MAX_PACKETS]
        self._notify("packets", record)

    def add_harvest(self, entry: dict) -> None:
        record = {"ts": time.strftime("%H:%M:%S"), **entry}
        with self._lock:
            self._state["harvest_log"].insert(0, record)
            self._state["harvest_log"] = self._state["harvest_log"][:200]
            snapshot = list(self._state["harvest_log"])
        self._notify("harvest_log", snapshot)
        self._notify("harvest_done", record)

    def update_inventory(self, items: list[dict]) -> None:
        with self._lock:
            self._state["inventory"] = items
            snapshot = list(items)
        self._notify("inventory", snapshot)

    def update_bank_inventory(self, items: list[dict]) -> None:
        with self._lock:
            self._state["bank_inventory"] = items
            snapshot = list(items)
        self._notify("bank_inventory", snapshot)

    def update_weight(self, current_w: int, max_w: int) -> None:
        snapshot = {"current": current_w, "max": max_w}
        with self._lock:
            self._state["weight"] = snapshot
        self._notify("weight", snapshot)

    def update_jobs(self, jobs: list[dict]) -> None:
        with self._lock:
            self._state["jobs"] = jobs
            snapshot = list(jobs)
        self._notify("jobs", snapshot)

    def update_spells(self, spells: list[dict]) -> None:
        with self._lock:
            self._state["spells"] = spells
            snapshot = list(spells)
        self._notify("spells", snapshot)

    def update_team_classes(self, classes: list[dict]) -> None:
        """Classes présentes dans l'équipe (+ leurs sorts), pour les sous-onglets Sorts."""
        with self._lock:
            self._state["team_classes"] = classes
            snapshot = [dict(c) for c in classes]
        self._notify("team_classes", snapshot)

    def increment_fight_count(self) -> None:
        with self._lock:
            self._state["combat_stats"]["fights_completed"] += 1
            snapshot = dict(self._state["combat_stats"])
        self._notify("combat_stats", snapshot)

    def increment_bank_count(self) -> None:
        with self._lock:
            self._state["combat_stats"]["bank_openings"] += 1
            snapshot = dict(self._state["combat_stats"])
        self._notify("combat_stats", snapshot)

    def notify_harvest_started(self, cell_id: int, entity_id: str) -> None:
        self._notify("harvest_started", {"cell_id": cell_id, "entity_id": entity_id})

    def notify_map_ready(self, map_id: int) -> None:
        self._notify("map_ready", {"map_id": map_id})

    def notify_travel(self, data: dict) -> None:
        """Publier le trajet d'autopilote reçu du serveur (topic 'travel_path')."""
        self._notify("travel_path", data)

    def update_activity(self, status: str, detail: str = "") -> None:
        """Publier l'activité courante du bot combat (topic 'activity').

        Statuts : "idle", "farming", "combat", "pause_afk", "pause_combat".
        Consommé par l'onglet Combat pour que le statut affiché reflète l'état réel
        (notamment les pauses volontaires) plutôt que de laisser l'utilisateur croire
        que le bot est planté quand il ne fait "rien" pendant une pause AFK.
        """
        snapshot = {"status": status, "detail": detail}
        with self._lock:
            self._state["activity"] = snapshot
        self._notify("activity", snapshot)

    def get_state(self) -> dict:
        with self._lock:
            return copy.deepcopy(self._state)


# ---------------------------------------------------------------------------
# Bus lifecycle GLOBAL (indépendant des sessions)
# ---------------------------------------------------------------------------

_lifecycle_callbacks: dict[str, list[Callable]] = {}
_lifecycle_lock = threading.Lock()


def subscribe_lifecycle(topic: str, fn: Callable) -> None:
    """S'abonner aux évènements globaux de cycle de vie des teams.

    Topics : "session_added", "session_status", "session_removed", "notification".
    """
    with _lifecycle_lock:
        _lifecycle_callbacks.setdefault(topic, []).append(fn)


def emit_lifecycle(topic: str, data) -> None:
    """Émettre un évènement global (cycle de vie / notification)."""
    with _lifecycle_lock:
        callbacks = list(_lifecycle_callbacks.get(topic, []))
    for fn in callbacks:
        try:
            fn(data)
        except Exception:
            pass


# ---------------------------------------------------------------------------
# Fonctions module — délèguent au Bridge de la session active (côté asyncio)
# ---------------------------------------------------------------------------

# Bridge de repli pour le code asyncio s'exécutant hors contexte de session
# (rare : démarrage, logs systèmes). Ses notifications ne sont vues par personne.
_fallback_bridge = Bridge()


def _active_bridge() -> Bridge:
    try:
        from core.session import active_or_none
        s = active_or_none()
        if s is not None:
            return s.bridge
    except Exception:
        pass
    return _fallback_bridge


def update_character(data: dict) -> None:
    _active_bridge().update_character(data)


def update_map(data: dict) -> None:
    _active_bridge().update_map(data)


def update_resources(resources: list[dict]) -> None:
    _active_bridge().update_resources(resources)


def set_entity(entity: dict) -> None:
    _active_bridge().set_entity(entity)


def remove_entity(entity_id: str) -> None:
    _active_bridge().remove_entity(entity_id)


def clear_entities() -> None:
    _active_bridge().clear_entities()


def update_hero(hero_id: str, data: dict) -> None:
    _active_bridge().update_hero(hero_id, data)


def add_console(msg: str) -> None:
    _active_bridge().add_console(msg)


def update_activity(status: str, detail: str = "") -> None:
    _active_bridge().update_activity(status, detail)


def add_packet(entry: dict) -> None:
    _active_bridge().add_packet(entry)


def add_harvest(entry: dict) -> None:
    _active_bridge().add_harvest(entry)


def update_inventory(items: list[dict]) -> None:
    _active_bridge().update_inventory(items)


def update_bank_inventory(items: list[dict]) -> None:
    _active_bridge().update_bank_inventory(items)


def update_weight(current_w: int, max_w: int) -> None:
    _active_bridge().update_weight(current_w, max_w)


def update_jobs(jobs: list[dict]) -> None:
    _active_bridge().update_jobs(jobs)


def update_spells(spells: list[dict]) -> None:
    _active_bridge().update_spells(spells)


def update_team_classes(classes: list[dict]) -> None:
    _active_bridge().update_team_classes(classes)


def increment_fight_count() -> None:
    _active_bridge().increment_fight_count()


def increment_bank_count() -> None:
    _active_bridge().increment_bank_count()


def notify_harvest_started(cell_id: int, entity_id: str) -> None:
    _active_bridge().notify_harvest_started(cell_id, entity_id)


def notify_map_ready(map_id: int) -> None:
    _active_bridge().notify_map_ready(map_id)


def notify_travel(data: dict) -> None:
    _active_bridge().notify_travel(data)


def notify(message: str, level: str = "info") -> None:
    """Émettre une notification toast GLOBALE (préfixée du perso de la team émettrice)."""
    prefix = ""
    try:
        from core.session import active_or_none
        s = active_or_none()
        if s is not None and s.main_character:
            prefix = f"[{s.main_character}] "
    except Exception:
        pass
    emit_lifecycle("notification", {"message": prefix + message, "level": level})


def subscribe(topic: str, fn: Callable) -> None:
    """S'abonner au bridge de la session active (usage asyncio : mapnav, bots).

    L'abonnement étant posé dans le contexte d'une connexion (au démarrage d'un
    bot ou d'une navigation), il cible le bon Bridge de session. Les onglets UI
    utilisent directement `session.bridge.subscribe(...)`.
    """
    _active_bridge().subscribe(topic, fn)


def get_state() -> dict:
    """Lecture de repli (debug). Préfère session.bridge.get_state()."""
    return _active_bridge().get_state()
