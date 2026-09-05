"""
Modèle de session multi-instance.

Chaque client Dofus connecté = une `Session` indépendante qui encapsule TOUT
ce qui était auparavant un singleton global :
  - `channel`     : writer serveur + clés de chiffrement (ex bot/channel.py)
  - `game_state`  : l'état de jeu dédié (ex game.state.current)
  - `bridge`      : le bus pub/sub dédié à l'UI de cette team (ex dashboard/bridge)
  - état d'exécution des boucles bot (combat / récolte / scripts)

Routage côté asyncio
────────────────────
Un `contextvars.ContextVar` (`_active`) pointe sur la session courante. Chaque
connexion lance ses tasks relay dans son propre contexte de task (cf.
proxy/server.handle_client) ; les tasks filles (handlers, combat) **héritent**
de ce contexte à leur création. Ainsi `game.state.current` et `bot.channel.*`
se résolvent automatiquement sur la bonne session sans réécrire les call-sites.

Routage côté UI (thread tkinter)
─────────────────────────────────
Le thread tkinter n'a pas de session active : chaque onglet détient une
référence **explicite** à sa `Session` et lit `session.game_state` /
`session.bridge`. Pour lancer une coroutine bot ciblant une team précise depuis
l'UI, utiliser `launch_in_session()` qui pose le contextvar dans un contexte
copié avant de créer la task.
"""

from __future__ import annotations

import asyncio
import contextvars
import logging
from asyncio import StreamWriter
from dataclasses import dataclass, field
from typing import Callable, Coroutine, Any, TYPE_CHECKING

if TYPE_CHECKING:
    from game.state import GameState
    from dashboard.bridge import Bridge

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# État réseau (ex-globals de bot/channel.py)
# ---------------------------------------------------------------------------

@dataclass
class ChannelState:
    """Canal réseau d'une session : writer + clés de chiffrement rotatives."""
    writer: StreamWriter | None = None
    is_game_connection: bool = False         # True après réception de HG
    a_keys: list[str | None] = field(default_factory=lambda: [None] * 16)
    current_key: int = 0
    # Writer vers le client Flash (S→C), pour injecter des messages au client
    # (ex. déclencheur de dump d'inventaire du patch core.swf).
    client_writer: StreamWriter | None = None


@dataclass
class NavState:
    """État de navigation overworld d'une session (ex-globals de bot/mapnav.py)."""
    _map_changed_event: "asyncio.Event | None" = None
    _subscribed: bool = False
    _lock: "asyncio.Lock | None" = None
    _pending_exit_cell: int = -1
    _pending_exit_map_id: int | None = None
    _dead_cells: set = field(default_factory=set)
    _last_successful_exit: tuple | None = None
    _banned_exit_cells: dict = field(default_factory=dict)


# ---------------------------------------------------------------------------
# Session
# ---------------------------------------------------------------------------

class Session:
    """Tout l'état d'un client Dofus connecté (une team)."""

    def __init__(self, session_id: int) -> None:
        # Imports différés pour éviter les cycles d'import au chargement du module.
        from game.state import GameState
        from dashboard.bridge import Bridge

        self.session_id: int = session_id
        self.channel: ChannelState = ChannelState()
        self.game_state: "GameState" = GameState()
        self.bridge: "Bridge" = Bridge()

        # Identité / statut (alimentés par le handler ASK et la (dé)connexion)
        self.main_character: str | None = None
        self.character_level: int | None = None
        self.status: str = "connecting"   # connecting | connected | disconnected
        # True une fois qu'un onglet UI a été créé pour cette session (à l'ASK).
        # Les connexions d'auth (sans perso) ne déclenchent jamais d'onglet.
        self.ui_registered: bool = False

        # État d'exécution des boucles bot (déplacé depuis les modules bot/*)
        self.combat_task: asyncio.Task | None = None
        self.combat_stop_after: bool = False
        self.harvester_bot = None   # instance bot.harvester._HarvestBot (lazy)
        self.script_bot = None      # instance bot.script_engine.ScriptBot (lazy)

        # État de navigation overworld (ex-globals de bot/mapnav.py)
        self.nav: NavState = NavState()

        # Cooldown anti-spam des réponses auto aux MP (ex-global bot/auto_reply.py)
        self.auto_reply_cooldown: dict[str, float] = {}

    def __repr__(self) -> str:
        return f"<Session #{self.session_id} {self.main_character or '…'} ({self.status})>"

    def emit_status(self) -> None:
        """Émettre l'état courant de la team sur le bus lifecycle (pour l'UI)."""
        try:
            from dashboard import bridge
            bridge.emit_lifecycle("session_status", {
                "session_id": self.session_id,
                "status": self.status,
                "character": self.main_character,
                "level": self.character_level,
            })
        except Exception as exc:
            logger.debug("[session] emit_status échoué : %s", exc)

    def set_connected(self, character: str, level: int | None = None) -> None:
        """Marquer la team connectée et renseigner son perso principal (handler ASK).

        C'est ICI (et seulement ici) qu'un onglet team est créé : les connexions
        d'authentification (sans perso) ne génèrent jamais d'onglet fantôme.
        """
        self.main_character = character
        self.character_level = level
        self.status = "connected"
        if not self.ui_registered:
            self.ui_registered = True
            self._emit("session_added", {"session_id": self.session_id})
        self.emit_status()

    def set_disconnected(self) -> None:
        """Marquer la team déconnectée (fin de relay).

        N'émet un statut que si un onglet existe (vraie team) — une connexion
        d'auth qui se ferme ne doit rien afficher.
        """
        self.status = "disconnected"
        if self.ui_registered:
            self.emit_status()

    @staticmethod
    def _emit(topic: str, data: dict) -> None:
        try:
            from dashboard import bridge
            bridge.emit_lifecycle(topic, data)
        except Exception as exc:
            logger.debug("[session] emit %s échoué : %s", topic, exc)


# ---------------------------------------------------------------------------
# Contexte actif (côté asyncio)
# ---------------------------------------------------------------------------

_active: contextvars.ContextVar["Session | None"] = contextvars.ContextVar(
    "active_session", default=None
)


def set_active(session: "Session | None") -> None:
    """Définir la session active dans le contexte courant."""
    _active.set(session)


def active() -> "Session":
    """Retourner la session active ou lever si aucune (appel hors contexte)."""
    s = _active.get()
    if s is None:
        raise RuntimeError(
            "Aucune session active dans ce contexte (code asyncio hors connexion ?)"
        )
    return s


def active_or_none() -> "Session | None":
    """Retourner la session active ou None (sans lever)."""
    return _active.get()


# ---------------------------------------------------------------------------
# Registre global des sessions
# ---------------------------------------------------------------------------

class SessionManager:
    """Registre des sessions actives, indexées par leur id de connexion."""

    def __init__(self) -> None:
        self._sessions: dict[int, Session] = {}

    def create(self, session_id: int) -> Session:
        session = Session(session_id)
        self._sessions[session_id] = session
        logger.info("[session] Création %r", session)
        # NB : pas d'évènement UI ici. L'onglet team n'est créé qu'à l'ASK
        # (Session.set_connected), pour ne pas afficher les connexions d'auth.
        return session

    def remove(self, session_id: int) -> None:
        session = self._sessions.pop(session_id, None)
        if session is not None:
            logger.info("[session] Suppression %r", session)
            self._emit("session_removed", {"session_id": session_id})

    def get(self, session_id: int) -> Session | None:
        return self._sessions.get(session_id)

    def all(self) -> list[Session]:
        return list(self._sessions.values())

    def find_disconnected_by_character(self, character: str) -> Session | None:
        """Trouver une session déconnectée portant ce perso (réconciliation reconnect)."""
        for s in self._sessions.values():
            if s.status == "disconnected" and s.main_character == character:
                return s
        return None

    @staticmethod
    def _emit(topic: str, data: dict) -> None:
        # Import différé : le bus lifecycle vit dans dashboard.bridge.
        try:
            from dashboard import bridge
            bridge.emit_lifecycle(topic, data)
        except Exception as exc:
            logger.debug("[session] emit %s échoué : %s", topic, exc)


manager = SessionManager()


# ---------------------------------------------------------------------------
# Lancement de coroutine bot ciblant une session précise (depuis l'UI)
# ---------------------------------------------------------------------------

def launch_in_session(
    session: Session,
    coro_factory: Callable[[], Coroutine[Any, Any, Any]],
) -> None:
    """Planifier une coroutine bot sur l'event loop, dans le contexte de `session`.

    Pose `_active = session` dans un **contexte copié** (pour ne pas polluer le
    contexte racine du loop) puis crée la task dans ce contexte. La coroutine
    voit alors la bonne session via `active()` / `game.state.current` /
    `bot.channel.*`.
    """
    import bot as _bot

    loop = _bot.get_main_loop()

    def _run() -> None:
        ctx = contextvars.copy_context()
        ctx.run(_active.set, session)
        loop.create_task(coro_factory(), context=ctx)

    loop.call_soon_threadsafe(_run)


def call_in_session(session: Session, fn: Callable[[], Any]) -> None:
    """Exécuter une fonction synchrone (ex: start_bot/stop_bot) côté loop,
    dans le contexte de `session`. La fonction crée généralement elle-même une
    task ; on copie le contexte pour que cette task hérite de la session.
    """
    import bot as _bot

    loop = _bot.get_main_loop()

    def _run() -> None:
        ctx = contextvars.copy_context()
        ctx.run(_active.set, session)
        ctx.run(fn)

    loop.call_soon_threadsafe(_run)
