"""
État de jeu global — mis à jour en temps réel par les handlers de messages.

Personnage principal : Ethera (ID 337259).
Les autres personnages (Maescaline, Etherp, etc.) sont les héros/alliés.

Ce module s'enregistre automatiquement sur le dispatcher dès son import.
Singleton : game.state.current
"""

from __future__ import annotations

import asyncio
import logging
import random
from dataclasses import dataclass, field

from protocol.parser import ParsedMessage, on_server_message, on_client_message
from protocol.messages.map import (
    MapData, FrameObjects, InteractiveElement, EntityMovement,
    parse_gm_entities, is_gm_entity_list,
)
from protocol.messages.auth import CharacterInfo
from protocol.messages.stats import CharacterStats, EntityInfo, CLASSES, ENTITY_PLAYER, ENTITY_NPC
from protocol.messages.inventory import Inventory, parse_item_add, parse_item_remove, parse_item_quantity
from protocol.messages.jobs import parse_jx
from protocol.encoding import decode_path, ZIPKEY, prepare_key
from dashboard import bridge
from bot.mapdata import load_map, MapInfo, lookup_arrival, record_arrival

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# GameState
# ---------------------------------------------------------------------------

@dataclass
class SpellEntry:
    """Sort possédé par un personnage (depuis le packet SL)."""
    spell_id: int
    spell_level: int
    position: int       # position dans la barre de sorts (-1 = non placé)


@dataclass
class GameState:
    """État courant du jeu."""

    # Notre personnage principal
    character: CharacterInfo | None = None
    stats: CharacterStats | None = None

    # Sorts du personnage principal (depuis SL)
    character_spells: list[SpellEntry] = field(default_factory=list)

    # Carte
    current_map: MapData | None = None
    frame_objects: FrameObjects = field(default_factory=FrameObjects)

    # Entités visibles sur la carte (joueurs, monstres)
    entities: dict[str, EntityInfo] = field(default_factory=dict)

    # Mouvements enregistrés
    movements: dict[str, EntityMovement] = field(default_factory=dict)

    # Poids de l'inventaire
    weight_current: int = 0
    weight_max: int = 0

    # Combat
    in_combat: bool = False

    # État des entités en combat (entity_id → cell_id)
    # Mis à jour par GTM à chaque début de tour.
    combat_entity_cells: dict[str, int] = field(default_factory=dict)

    # HP des entités en combat (entity_id → hp courant)
    # Mis à jour par GTM. Permet de détecter la mort de Sisyphyl.
    combat_entity_hp: dict[str, int] = field(default_factory=dict)

    # HP max des entités en combat (entity_id → hp max)
    # Mis à jour par GTM (field index 7). Permet de focus les monstres blessés.
    combat_entity_max_hp: dict[str, int] = field(default_factory=dict)

    # PM des entités en combat (entity_id → PM restants au début du tour)
    # Mis à jour par GTM. Utilisé pour les déplacements en combat.
    combat_entity_pm: dict[str, int] = field(default_factory=dict)

    # PM max observé pour chaque entité en combat (entity_id → max PM vu).
    # Mis à jour comme high-watermark à chaque GTM : permet d'estimer la menace
    # réelle des monstres même quand leur GTM courant reporte un PM réduit
    # (parce qu'ils ont déjà bougé ce round).
    combat_entity_max_pm: dict[str, int] = field(default_factory=dict)

    # PO bonus des entités en combat (entity_id → bonus portée total)
    # Mis à jour depuis le GM de placement au début du combat.
    combat_entity_po: dict[str, int] = field(default_factory=dict)

    # PA des entités en combat (entity_id → PA restants au début du tour)
    # Mis à jour par GTM.
    combat_entity_pa: dict[str, int] = field(default_factory=dict)

    # IDs des monstres encore vivants (mis à jour par GTL et GA;999)
    combat_live_monsters: set[str] = field(default_factory=set)

    # IDs des monstres présents AU DÉBUT du combat (1er GTL), hors invocations.
    # Les invocations (mama koalak, etc.) apparaissent en cours de combat via des
    # GA;999;…;GTL|… : leurs IDs ne sont donc PAS dans cet ensemble. Sert au
    # ciblage à ignorer les invocations et focus les monstres d'origine (quand
    # l'invocateur meurt, ses invocations meurent aussi).
    combat_initial_monsters: set[str] = field(default_factory=set)

    # Séquences de sorts d'attaque PAR CLASSE (modifiables depuis l'UI).
    # Clé : class_id (cf. CLASSES). Valeur : liste d'entrées (spell_id, cast_count, target),
    # lancées dans l'ordre. target : "enemy" (défaut), "self", "aoe2" ou "aoe3".
    # Chaque personnage/héros joue la séquence configurée pour SA classe (équipe mixte).
    attack_spell_sequences: dict[int, list[tuple[int, int, str]]] = field(default_factory=dict)

    # Classe de chaque combattant allié — entity_id → class_id (cf. CLASSES).
    # Renseigné à la connexion : perso principal via ASK, héros via Nx (entité K+).
    # Permet à combat.py de choisir la bonne séquence de sorts selon la classe du lanceur.
    entity_classes: dict[str, int] = field(default_factory=dict)

    # Sorts connus par classe — class_id → liste de SpellEntry (sort représentatif de la classe).
    # Renseigné à la connexion : perso principal via SL, héros via Nh. Sert à peupler les
    # dropdowns de sorts par classe dans l'onglet Combat et à résoudre le niveau d'un sort.
    class_spells: dict[int, list[SpellEntry]] = field(default_factory=dict)

    # Comportement en combat : "distance" (défaut) ou "rush_cac"
    combat_behavior: str = "distance"

    # Nombre maximum de monstres par groupe à attaquer (1-8).
    # 8 = pas de filtrage (les groupes Dofus 1.29 contiennent au plus 8 monstres).
    combat_max_monsters_per_group: int = 8

    # Si True : quand un autre joueur (hors nos héros) est visible sur la carte, le bot
    # attend un délai aléatoire avant de lancer chaque combat (anti-suspicion).
    # Toggle depuis l'onglet Combat. Voir bot.combat.combat_farm_loop.
    combat_slow_when_player: bool = False

    # Bornes (millisecondes) des délais aléatoires anti-suspicion, éditables dans l'UI :
    #  - spectateur : délai AVANT chaque action (sort/déplacement/fin de tour) tant qu'un
    #    joueur observe le combat en spectateur (Im 036).
    #  - joueur sur la carte : délai AVANT de lancer chaque combat quand un autre joueur
    #    est visible (seulement si combat_slow_when_player est coché).
    combat_spectator_delay_min_ms: int = 600
    combat_spectator_delay_max_ms: int = 2000
    combat_player_delay_min_ms: int = 2000
    combat_player_delay_max_ms: int = 4000

    # Si True : délai aléatoire AVANT d'envoyer GR1 (« prêt ») au lancement d'un combat,
    # pour ne pas confirmer le placement à la milliseconde (anti-suspicion). Optionnel :
    # décoché, le bot envoie GR1 immédiatement (vitesse actuelle). Toggle depuis l'onglet
    # Combat → Comportement → Discrétion. Bornes en millisecondes ci-dessous.
    combat_delay_before_ready: bool = False
    combat_ready_delay_min_ms: int = 100
    combat_ready_delay_max_ms: int = 500

    # --- Anti-détection comportementale (toujours actif, pas seulement sous témoin) ---
    # Délai minimum aléatoire AVANT chaque action de combat (sort / déplacement / fin de
    # tour), même sans spectateur. Empêche les rafales de plusieurs casts dans la même
    # seconde — signature bot n°1 (un humain ne lance pas 4 sorts/s). Cf. bot.combat.
    combat_min_action_delay_min_ms: int = 350
    combat_min_action_delay_max_ms: int = 800

    # Pauses longues régulières entre combats (imite un joueur qui fait autre chose).
    # Toutes les N combats (±jitter), le bot marque une pause de X secondes aléatoire.
    combat_pause_every_fights: int = 40      # 0 = désactivé
    combat_pause_every_jitter: int = 15      # ± sur le compteur
    combat_pause_duration_min_s: int = 90
    combat_pause_duration_max_s: int = 300

    # Plafond de session : au-delà de ce nombre de combats, le bot s'arrête tout seul.
    combat_session_max_fights: int = 0       # 0 = illimité

    # Quitter une carte contestée : si un autre joueur est sur la carte ET qu'on
    # enchaîne les échecs de lancement (groupes volés), on abandonne la carte au lieu
    # de s'acharner (comportement bot très visible). Nombre d'échecs avant abandon.
    combat_leave_contested_after: int = 4    # 0 = désactivé

    # Mode de discrétion — pilote TOUS les ralentissements comportementaux :
    #   "human"   (défaut) : agit comme un joueur — délais longs et variables entre les
    #             actions, vrais temps morts entre les combats, pauses régulières +
    #             grosses pauses « horaires », farm suspendu si un joueur inconnu est
    #             sur la carte, délai avant « Prêt ». Bornes dans bot.combat (HUMAN_*).
    #   "farming" (⚠ risque) : l'ancien comportement du bot — délais anti-rafale courts,
    #             ré-engagement quasi immédiat. Cadence ~1 combat/20 s = le pattern qui a
    #             fait repérer la team par l'admin le 09/07/2026.
    #   "speed"   (⚠ DANGER — risque de ban maximal) : aucun ralentissement, vitesse
    #             robotique d'origine.
    # Les protections qui NE COÛTENT PAS de vitesse restent actives dans tous les modes
    # (apprentissage portée/LdV, suivi PA tacle, arrêt sur déconnexion, abandon de carte
    # contestée). Sélecteur dans l'onglet Combat → Comportement → Mode de discrétion.
    combat_discretion_mode: str = "human"


    # --- État par-session déplacé depuis les ex-globals module (multi-instance) ---
    _pending_stats: CharacterStats | None = None
    _map_loaded: bool = False
    _map_info: "MapInfo | None" = None
    _last_harvested_cell: int = -1
    _pending_hp: list = field(default_factory=list)
    _pre_gdm_map_id: int | None = None
    _pre_gdm_char_cell: int = -1
    _pre_gdm_other_cells: list = field(default_factory=list)
    _arrival_context: tuple | None = None
    _gkk_event: asyncio.Event = field(default_factory=asyncio.Event)
    _move_rejected_event: asyncio.Event = field(default_factory=asyncio.Event)
    _pending_actions: int = 0
    _pending_actions_clear: asyncio.Event = field(default_factory=asyncio.Event)
    _combat_turn_event: asyncio.Event = field(default_factory=asyncio.Event)
    _combat_ended_event: asyncio.Event = field(default_factory=asyncio.Event)
    # Durée du tour (ms) annoncée par le serveur au GTS courant. Sert à borner toute
    # attente volontaire (pause anti-détection) pour ne jamais dépasser le timer et
    # laisser sauter le tour. 45000 par défaut (valeur usuelle Dofus Rétro).
    combat_turn_time_ms: int = 45000
    _companion_turn_event: asyncio.Event = field(default_factory=asyncio.Event)
    _companion_turn_id: str = ""
    _combat_started_event: asyncio.Event = field(default_factory=asyncio.Event)
    _gtf_event: asyncio.Event = field(default_factory=asyncio.Event)
    _spell_result_event: asyncio.Event = field(default_factory=asyncio.Event)
    _spell_result_caster: str = ""
    _spell_cast_confirmed: bool = False
    _combat_turn_queue: asyncio.Queue = field(default_factory=asyncio.Queue)
    _spell_los_blocked: bool = False
    _spell_cast_failed: bool = False
    _spell_pa_insufficient: bool = False
    # Params du dernier rejet de portée (Im 1171;{min}~{max}~{actual}). Le serveur y
    # révèle la vraie portée effective du sort pour ce lanceur — source de vérité pour
    # corriger notre spells.xml local (qui diverge des stats du serveur privé). Remis à
    # None par clear_spell_result. Cf. bot.combat apprentissage de portée (anti Im 1171).
    _last_range_reject: tuple[int, int, int] | None = None
    _spectator_present: bool = False
    # Gel antibot : True pendant la « lecture + saisie » du .code (bot.antibot). Toutes
    # les actions de combat attendent que le flag retombe (un humain qui tape le code
    # du popup ne pilote pas ses persos en même temps). Cf. combat._wait_antibot_freeze.
    _antibot_freeze: bool = False
    _map_ready_event: asyncio.Event = field(default_factory=asyncio.Event)
    _bank_open_event: asyncio.Event = field(default_factory=asyncio.Event)
    _bank_is_open: bool = False
    # Échange marchand (PNJ) — distinct de la banque (ECK type 5).
    _exchange_open_event: asyncio.Event = field(default_factory=asyncio.Event)
    _exchange_closed_event: asyncio.Event = field(default_factory=asyncio.Event)
    _exchange_is_open: bool = False
    _exchange_kamas: int = 0   # dernier total kamas annoncé par le PNJ (Em KG)
    # HDV (bigstore) — réponses attendues par bot/hdv.py (scan dragodindes).
    _bigstore_type_event: asyncio.Event = field(default_factory=asyncio.Event)
    _bigstore_type_id: int = -1          # type d'item de la dernière réponse EHL
    _bigstore_type_gids: list = field(default_factory=list)   # gids en vente (EHL)
    _bigstore_list_event: asyncio.Event = field(default_factory=asyncio.Event)
    _bigstore_list_payload: str = ""     # payload complet du dernier EHl S→C
    _mount_data_event: asyncio.Event = field(default_factory=asyncio.Event)
    _mount_data_payload: str = ""        # payload complet du dernier Rd S→C
    _hdv_cancel: bool = False            # posé par l'UI pour interrompre un scan
    _weight_event: asyncio.Event = field(default_factory=asyncio.Event)
    _companion_ids: set = field(default_factory=set)
    _xp_to_char: dict = field(default_factory=dict)
    _pending_xp_key: str | None = None
    _hero_fingerprint: dict = field(default_factory=dict)
    _hero_hp_cache: dict = field(default_factory=dict)
    _inventory: dict = field(default_factory=dict)
    # Contenu de la banque (message EL à l'ouverture). Sur ce serveur, l'inventaire
    # complet (OT) n'est jamais poussé : la banque est la seule source lisible.
    _bank_inventory: dict = field(default_factory=dict)
    _bank_list_event: asyncio.Event = field(default_factory=asyncio.Event)
    # Dump d'inventaire complet via le patch core.swf (message ZO).
    _full_inv_event: asyncio.Event = field(default_factory=asyncio.Event)
    _jobs: dict = field(default_factory=dict)

    def __post_init__(self) -> None:
        # _pending_actions_clear démarre à l'état set (0 action en attente).
        self._pending_actions_clear.set()

    def __str__(self) -> str:
        parts = []
        if self.character:
            parts.append(str(self.character))
        if self.current_map:
            parts.append(str(self.current_map))
        parts.append(str(self.frame_objects))
        return " | ".join(parts)


class _CurrentProxy:
    """Proxy résolvant `current` vers le GameState de la session active.

    Permet aux ~160 `current.X` internes (state.py) ET externes (`_state.current.X`)
    de cibler la bonne session sans réécrire les call-sites. Forwarde lecture,
    écriture et suppression d'attributs.
    """
    __slots__ = ()

    def _gs(self) -> "GameState":
        from core.session import active
        return active().game_state

    def __getattr__(self, name):
        return getattr(_CurrentProxy._gs(self), name)

    def __setattr__(self, name, value):
        setattr(_CurrentProxy._gs(self), name, value)

    def __delattr__(self, name):
        delattr(_CurrentProxy._gs(self), name)

    def __str__(self) -> str:
        return str(_CurrentProxy._gs(self))


current: "GameState" = _CurrentProxy()  # type: ignore[assignment]

# Logique d'attribution des messages As :
#   - Avant GDK : le serveur envoie le As de TOUS les personnages de la map.
#     L'ordre est As(autre) → JS(autre) → As(autre) → JS(autre) → … → As(notre perso) → GDK.
#     On accumule le dernier As reçu ; GDK l'applique comme stats de notre perso.
#   - Après GDK : tout As reçu est forcément pour notre perso (mises à jour HP, kamas…).

# Buffer hP : les messages hP arrivent AVANT GDM et sont perdus lors du clear.
# On les bufferise ici et on les applique après GDK.

# Position avant GDM : permet de restaurer la position post-combat (même map_id)
# Cellules des autres entités présentes AVANT GDM (hP/NL reçus avant le clear)

# Contexte pour le cache d'arrivée : enregistré au GDK, consommé par le 1er GA1.
# Permet de savoir (old_map, exit_cell, new_map) quand GA1 révèle la position réelle.

# GKK (GameActionAck) : signal que le Flash client a terminé l'animation.
# Le serveur ne valide un déplacement qu'après ce GKK.

# Mouvement rejeté par le serveur : GA;129;{controller};{controller} SANS delta PM
# (ex. après un tacle qui a vidé le PM sans que le bot le suive, ou chemin invalide).
# Le Flash n'anime alors RIEN → aucun GKK → sinon le bot attend MOVE_TIMEOUT (5s) pour
# rien. Cet event permet de réagir immédiatement (cf. wait_move_result).

# Compteur d'actions en attente d'ack Flash (GAF reçu, GKK pas encore envoyé).
# Le serveur refuse Gt tant que ce compteur > 0.

# ---------------------------------------------------------------------------
# Constantes combat — configuration du groupe héros
# ---------------------------------------------------------------------------

# Héros passifs : passent leur tour automatiquement.
# Vide = tous les héros sont actifs (team full Crâ).
PASSIVE_COMBAT_HEROES: set[str] = set()

# ---------------------------------------------------------------------------
# Signaux asyncio — combat et carte
# ---------------------------------------------------------------------------

# Combat : signaux asyncio pour la gestion des tours et de la fin de combat.

# Résultat d'un cast de sort : déclenché dès que le serveur a tranché — soit le GAF
# DU LANCEUR (action finie + _pending_actions incrémenté = cast accepté), soit un Im
# (rejet : LdV/portée/PA). Permet à combat.py d'attendre la confirmation réelle au lieu
# d'un sleep fixe.
#
# CRITIQUE : on déclenche sur le GAF (pas le GA;102) et UNIQUEMENT pour l'entité lanceur.
# Sinon (a) un GA;102 d'une AUTRE entité débloquait l'attente prématurément, et (b) on
# repartait AVANT que le GAF du cast n'incrémente _pending_actions → end_turn n'attendait
# pas l'ack → Gt envoyé trop tôt → tour bloqué jusqu'au timeout serveur (45 s).
# True une fois le GA;300 (confirmation du cast COURANT) reçu pour le lanceur. Sert à
# n'accepter QUE le GAF du cast courant, jamais un GAF périmé d'une action antérieure
# (qui arriverait avant le GA;102 du cast → PA lus prématurément, race "1 cast au lieu de 2").

# Queue de tours : chaque GTS d'un allié (perso principal ou héros) y place
# l'entity_id. fight_group() consomme cette queue pour jouer chaque tour.

# Sort rejeté par le serveur (Im 1174/1171/1170…) — remis à False par combat.py
# Im 1170 : PA insuffisants pour lancer le sort. Distinct des autres rejets : il est
# inutile de retenter une autre case (on n'a tout simplement pas assez de PA).

# Spectateur : True dès qu'un joueur rejoint le combat courant en mode spectateur
# (Im 036;{pseudo}). Quand actif, combat.py joue « lentement » (délais aléatoires entre
# chaque action) jusqu'à la fin du combat. Remis à False au début de chaque combat (GJ),
# donc un combat sans spectateur rejoue à pleine vitesse.

# Carte : signal quand GDK est reçu (carte prête, entités disponibles).

# Banque : signal quand ECK 5 est reçu (banque ouverte).

# Poids : signal quand Ow est reçu (poids mis à jour).


async def wait_gkk(timeout: float = 8.0) -> bool:
    """Attendre que le Flash client envoie GKK (mouvement terminé).

    Returns True si GKK reçu, False si timeout.
    """
    current._gkk_event.clear()
    try:
        await asyncio.wait_for(current._gkk_event.wait(), timeout)
        return True
    except asyncio.TimeoutError:
        return False


def clear_move_result() -> None:
    """Réarmer les events de résultat de move (à appeler AVANT d'envoyer le GA001,
    sinon une réponse arrivée pendant l'envoi serait effacée puis manquée)."""
    current._gkk_event.clear()
    current._move_rejected_event.clear()


async def wait_move_result(timeout: float = 5.0) -> str:
    """Attendre le résultat d'un déplacement combat. Retourne :

    - "ok"       : GKK reçu (Flash a animé le move = move confirmé).
    - "rejected" : GA;129 sans delta (le serveur a refusé le move, ex. PM vidé par tacle).
                   On ne perd plus MOVE_TIMEOUT secondes à attendre un GKK qui ne viendra pas.
    - "timeout"  : ni l'un ni l'autre dans le délai imparti.

    L'appelant DOIT avoir appelé clear_move_result() avant d'envoyer le GA001.
    """
    gkk_task = asyncio.ensure_future(current._gkk_event.wait())
    rej_task = asyncio.ensure_future(current._move_rejected_event.wait())
    try:
        done, _ = await asyncio.wait(
            {gkk_task, rej_task}, timeout=timeout,
            return_when=asyncio.FIRST_COMPLETED,
        )
    finally:
        for t in (gkk_task, rej_task):
            if not t.done():
                t.cancel()
    if gkk_task in done:
        return "ok"
    if rej_task in done:
        return "rejected"
    return "timeout"


async def wait_actions_clear(timeout: float = 5.0) -> bool:
    """Attendre que toutes les actions en attente soient ack par le Flash client.

    Le serveur refuse Gt tant qu'il y a des GAF non ack par GKK.
    Returns True si toutes les actions sont clear, False si timeout.
    """
    if current._pending_actions <= 0:
        return True
    try:
        await asyncio.wait_for(current._pending_actions_clear.wait(), timeout)
        return True
    except asyncio.TimeoutError:
        logger.warning(
            "[GameState] wait_actions_clear timeout (%d actions pending)",
            current._pending_actions,
        )
        return False


async def wait_combat_turn(timeout: float = 60.0) -> bool:
    """Attendre que le serveur signale que c'est notre tour de combat (GTS).

    Gère le cas où GTS est arrivé avant l'appel (pendant le sleep du harvester) :
    si l'event est déjà set, on le consomme immédiatement sans attendre.

    Se débloque immédiatement si GE arrive avant GTS (combat terminé côté serveur),
    évitant d'attendre le timeout complet de 60s en cas de lag/fin de combat brutale.

    Returns True si notre tour reçu, False si timeout ou GE reçu avant GTS.
    """
    if current._combat_turn_event.is_set():
        current._combat_turn_event.clear()
        return True

    loop = asyncio.get_event_loop()
    turn_task = loop.create_task(current._combat_turn_event.wait())
    end_task  = loop.create_task(current._combat_ended_event.wait())
    try:
        done, pending = await asyncio.wait(
            {turn_task, end_task},
            timeout=timeout,
            return_when=asyncio.FIRST_COMPLETED,
        )
    except asyncio.CancelledError:
        turn_task.cancel()
        end_task.cancel()
        raise
    for t in pending:
        t.cancel()
    if pending:
        await asyncio.gather(*pending, return_exceptions=True)

    if turn_task in done and not turn_task.cancelled():
        current._combat_turn_event.clear()
        return True
    return False  # GE reçu avant GTS, ou timeout


async def wait_combat_end(timeout: float = 120.0) -> bool:
    """Attendre la fin du combat (GE).

    Returns True si GE reçu, False si timeout.
    """
    try:
        await asyncio.wait_for(current._combat_ended_event.wait(), timeout)
        return True
    except asyncio.TimeoutError:
        return False


async def wait_companion_turn(timeout: float = 8.0) -> str | None:
    """Attendre le tour du compagnon actif (tout héros non-passif hors perso principal).

    Retourne l'ID du compagnon si son tour est arrivé.
    Retourne None si :
      - le tour du perso principal arrive avant (compagnon mort/sauté)
      - timeout

    NE PAS effacer _combat_turn_event en cas de retour None : il sera consommé
    par wait_combat_turn() lors du prochain tour du perso principal.
    """
    if current._companion_turn_event.is_set():
        cid = current._companion_turn_id
        current._companion_turn_event.clear()
        return cid or None

    # Tour du perso principal déjà en file → compagnon sauté
    if current._combat_turn_event.is_set():
        return None

    loop = asyncio.get_event_loop()
    companion_task = loop.create_task(current._companion_turn_event.wait())
    main_task      = loop.create_task(current._combat_turn_event.wait())
    try:
        done, pending = await asyncio.wait(
            {companion_task, main_task},
            timeout=timeout,
            return_when=asyncio.FIRST_COMPLETED,
        )
    except asyncio.CancelledError:
        companion_task.cancel()
        main_task.cancel()
        raise
    for t in pending:
        t.cancel()
    if pending:
        await asyncio.gather(*pending, return_exceptions=True)

    if companion_task in done and not companion_task.cancelled():
        cid = current._companion_turn_id
        current._companion_turn_event.clear()
        return cid or None
    return None


async def wait_next_ally_turn(timeout: float = 60.0) -> str | None:
    """Attendre le prochain tour d'un allié (perso principal ou héros).

    Retourne l'entity_id dont c'est le tour, ou None si timeout/combat terminé.
    Se débloque immédiatement si GE arrive (combat terminé).
    """
    loop = asyncio.get_event_loop()

    async def _get_turn() -> str:
        return await current._combat_turn_queue.get()

    turn_task = loop.create_task(_get_turn())
    end_task = loop.create_task(current._combat_ended_event.wait())
    try:
        done, pending = await asyncio.wait(
            {turn_task, end_task},
            timeout=timeout,
            return_when=asyncio.FIRST_COMPLETED,
        )
    except asyncio.CancelledError:
        turn_task.cancel()
        end_task.cancel()
        raise
    for t in pending:
        t.cancel()
    if pending:
        await asyncio.gather(*pending, return_exceptions=True)

    if turn_task in done and not turn_task.cancelled():
        try:
            return turn_task.result()
        except Exception:
            return None
    return None


async def wait_combat_start(timeout: float = 2.0, target_entity_id: str | None = None) -> bool:
    """Attendre le début du combat (GJ/GS).

    Si target_entity_id est fourni, retourne False immédiatement si le groupe
    disparaît de la carte (volé par un autre joueur) sans que le combat démarre.
    Poll toutes les 50ms — réactif sans surcharge CPU.
    """
    current._combat_started_event.clear()
    loop = asyncio.get_event_loop()
    deadline = loop.time() + timeout
    while loop.time() < deadline:
        if current.in_combat or current._combat_started_event.is_set():
            return True
        if target_entity_id is not None and target_entity_id not in current.entities:
            return False  # groupe volé / disparu
        await asyncio.sleep(0.05)
    return current.in_combat


async def wait_map_ready(timeout: float = 15.0) -> bool:
    """Attendre que la carte soit prête (GDK).

    Returns True si GDK reçu, False si timeout.
    """
    current._map_ready_event.clear()
    try:
        await asyncio.wait_for(current._map_ready_event.wait(), timeout)
        return True
    except asyncio.TimeoutError:
        return False


async def wait_bank_open(timeout: float = 10.0) -> bool:
    """Attendre que la banque soit ouverte (ECK 5).

    Returns True si banque ouverte, False si timeout.
    """
    current._bank_open_event.clear()
    try:
        await asyncio.wait_for(current._bank_open_event.wait(), timeout)
        return True
    except asyncio.TimeoutError:
        return False


async def wait_weight_update(timeout: float = 10.0) -> bool:
    """Attendre une mise à jour de poids (Ow).

    Returns True si Ow reçu, False si timeout.
    """
    current._weight_event.clear()
    try:
        await asyncio.wait_for(current._weight_event.wait(), timeout)
        return True
    except asyncio.TimeoutError:
        return False


def is_overweight() -> bool:
    """True si le personnage est en surpoids (weight_current > weight_max)."""
    return current.weight_max > 0 and current.weight_current > current.weight_max


def get_inventory_resources() -> list[dict]:
    """Retourner les ressources en sac (pos=63, qty>1) — exclut les drops équipement (toujours qty=1)."""
    return [item for item in current._inventory.values() if item.get("pos") == 63 and item.get("qty", 0) > 1]


def get_inventory_bag() -> list[dict]:
    """Retourner tous les objets EN SAC (non équipés), équipés exclus.

    « En sac » = pos 63 (OAK/EL serveur) OU pos -1 (dump ZO client). Les autres
    positions (>= 0, hors 63) sont des emplacements d'équipement porté.
    Chaque dict : {uid, gid, qty, pos, fx}.
    """
    return [item for item in current._inventory.values() if item.get("pos") in (-1, 63)]


def get_bank_inventory() -> list[dict]:
    """Retourner le contenu de la banque (dernier message EL).

    Sur ce serveur, l'inventaire complet (OT) n'est jamais poussé : la banque
    est la seule liste d'objets lisible par le bot. Ouvrir la banque (ApS)
    déclenche un EL qui remplit ce contenu.
    """
    return list(current._bank_inventory.values())


async def wait_bank_list(timeout: float = 10.0) -> bool:
    """Attendre la réception du contenu banque (EL). True si reçu."""
    current._bank_list_event.clear()
    try:
        await asyncio.wait_for(current._bank_list_event.wait(), timeout)
        return True
    except asyncio.TimeoutError:
        return False


async def wait_full_inventory(timeout: float = 5.0) -> bool:
    """Attendre le dump d'inventaire complet (ZO, patch core.swf). True si reçu."""
    current._full_inv_event.clear()
    try:
        await asyncio.wait_for(current._full_inv_event.wait(), timeout)
        return True
    except asyncio.TimeoutError:
        return False


def get_npcs() -> list[EntityInfo]:
    """Retourner les PNJ visibles sur la carte courante."""
    return [e for e in current.entities.values() if e.entity_type == ENTITY_NPC]


def find_merchant_npc(gfx_ids: list[str] | None = None) -> EntityInfo | None:
    """Trouver le PNJ marchand sur la carte.

    Args:
        gfx_ids: apparences (gfx) acceptées pour le marchand. Si fourni, on
            ne retient qu'un PNJ dont le gfx correspond. Sinon, on retourne le
            premier PNJ trouvé (utile s'il n'y a qu'un PNJ sur la map).

    Returns:
        L'EntityInfo du marchand (son ``entity_id`` négatif = cible ER), ou None.
    """
    npcs = get_npcs()
    if gfx_ids:
        wanted = {str(g) for g in gfx_ids}
        for npc in npcs:
            if str(npc.gfx_id) in wanted:
                return npc
        return None
    return npcs[0] if npcs else None


async def wait_exchange_open(timeout: float = 10.0) -> bool:
    """Attendre l'ouverture d'un échange PNJ (ECK non-banque). True si ouvert."""
    current._exchange_open_event.clear()
    try:
        await asyncio.wait_for(current._exchange_open_event.wait(), timeout)
        return True
    except asyncio.TimeoutError:
        return False


async def wait_exchange_closed(timeout: float = 10.0) -> bool:
    """Attendre la fermeture de l'échange PNJ (EV). True si fermé."""
    current._exchange_closed_event.clear()
    try:
        await asyncio.wait_for(current._exchange_closed_event.wait(), timeout)
        return True
    except asyncio.TimeoutError:
        return False


def get_entity_pa(entity_id: str) -> int:
    """Retourner les PA restants d'une entité en combat (d'après le dernier GTM)."""
    return current.combat_entity_pa.get(entity_id, 6)


def get_entity_pm(entity_id: str) -> int:
    """Retourner les PM restants d'une entité en combat (d'après le dernier GTM)."""
    return current.combat_entity_pm.get(entity_id, 0)


def get_entity_po(entity_id: str) -> int:
    """Retourner le bonus de portée (PO) d'une entité en combat."""
    return current.combat_entity_po.get(entity_id, 0)


def get_spell_los_blocked() -> bool:
    """Retourner True si un Im 1174 (LdV bloquée) a été reçu depuis le dernier clear."""
    return current._spell_los_blocked


def get_spell_cast_failed() -> bool:
    """Retourner True si le dernier sort a été rejeté par le serveur (Im 1174/1170…)."""
    return current._spell_cast_failed


def get_spell_pa_insufficient() -> bool:
    """Retourner True si le dernier rejet était un Im 1170 (PA insuffisants)."""
    return current._spell_pa_insufficient


def get_last_range_reject() -> tuple[int, int, int] | None:
    """Retourner (min, max, actual) du dernier Im 1171, ou None.

    Le serveur y expose la portée effective réelle du sort → combat.py s'en sert
    pour apprendre la vraie portée de base et ne plus émettre de casts hors portée.
    """
    return current._last_range_reject


def is_spectator_present() -> bool:
    """True si un joueur a rejoint le combat courant en mode spectateur (Im 036).

    Remis à False au début de chaque combat (GJ). combat.py s'en sert pour ralentir
    le jeu (délais aléatoires entre actions) tant qu'on est observé.
    """
    return current._spectator_present


def has_foreign_player_on_map() -> bool:
    """True si un joueur autre que notre perso (et nos héros) est visible sur la carte.

    Nos héros sont déjà exclus de current.entities : ils sont retirés via le packet Nx
    (cf. _on_nx → _companion_ids). On ne compte que les vraies entités JOUEUR — pas les
    monstres, PNJ ni percepteurs. combat.py s'en sert pour ralentir le lancement des
    combats quand quelqu'un peut nous observer farmer.
    """
    my_id = current.character.character_id if current.character else None
    for eid, ent in current.entities.items():
        if eid == my_id:
            continue
        if ent.entity_type == ENTITY_PLAYER:
            return True
    return False


def clear_spell_los_blocked() -> None:
    """Remettre à False les flags de rejet de sort (appelé par combat.py avant chaque sort)."""
    current._spell_los_blocked = False
    current._spell_cast_failed = False
    current._spell_pa_insufficient = False
    current._last_range_reject = None


def clear_spell_result(caster_id: str) -> None:
    """Réarmer l'événement de résultat de cast (appelé par combat.py avant d'envoyer GA300).

    `caster_id` : l'entité qui lance — seuls le GAF de CETTE entité (ou un Im) débloqueront
    l'attente. Doit être appelé AVANT l'envoi du sort : sinon la réponse serveur (GAF/Im)
    pourrait arriver — et set l'event — avant qu'on l'ait clear, et on raterait le signal.
    """
    current._spell_result_caster = caster_id
    current._spell_cast_confirmed = False
    current._spell_result_event.clear()


async def wait_spell_result(timeout: float = 0.5) -> bool:
    """Attendre que le serveur ait tranché sur le dernier cast (GAF du lanceur, ou Im rejet).

    Remplace le sleep fixe : on repart dès que le serveur a répondu (~1 RTT) au lieu
    d'attendre une durée arbitraire. Le timeout n'est qu'un garde-fou (sort sans GAF
    ni Im observable, perte réseau…). Returns True si un résultat est arrivé, False si timeout.

    CRITIQUE — attente du GKK avant de rendre la main : le GAF signale que le serveur a
    *traité* le cast, mais le serveur REFUSE une nouvelle action GA300 tant que la
    précédente n'est pas acquittée par le Flash (GKK). Si on enchaîne le cast suivant dès
    le GAF (pending encore > 0), le serveur le jette silencieusement (GA;102;-0, aucun
    GA;300) → sort perdu. On attend donc aussi wait_actions_clear (pending→0). Sur un rejet
    Im (pas de GAF), pending est déjà 0 → retour immédiat.

    Le timeout GKK est plus large que celui du GAF : sur les zones à 3-5 monstres touchés
    en AOE, le Flash met 0.5-1s à traiter tous les effets (animations dégâts, GA;100,
    GIe…) avant d'envoyer ses GKK. Avec 0.5s on timeoute → bot envoie cast 2 → rejet -0.
    Plafond à 1.2s : couvre les pires GKK observés sans pénaliser le chemin rapide
    (qui repart dès que l'event est set, en général <200 ms).
    """
    try:
        await asyncio.wait_for(current._spell_result_event.wait(), timeout)
    except asyncio.TimeoutError:
        return False
    await wait_actions_clear(timeout=max(timeout * 2.4, 1.2))
    return True


def get_live_monster_cells() -> list[int]:
    """Retourner les cell_id de tous les monstres vivants en combat.

    Combine les infos de combat_live_monsters et combat_entity_cells.
    """
    result = []
    for eid in current.combat_live_monsters:
        cell = current.combat_entity_cells.get(eid)
        if cell is not None and cell >= 0:
            result.append(cell)
    return result


def is_summon(entity_id: str) -> bool:
    """True si l'entité est une invocation (apparue après le début du combat).

    Une invocation est un monstre vivant dont l'ID n'est PAS dans le set figé au
    1er GTL (``combat_initial_monsters``). Si ce set est vide (jamais renseigné),
    on ne peut pas distinguer → on considère que ce n'en est pas une.
    """
    return bool(
        current.combat_initial_monsters
        and entity_id not in current.combat_initial_monsters
    )


def get_targetable_monster_ids() -> set[str]:
    """Retourner les IDs des monstres à cibler en priorité (hors invocations).

    Les invocations (ex. mama koalak) apparaissent en cours de combat et ne sont
    donc pas dans ``combat_initial_monsters``. Comme tuer l'invocateur tue aussi
    ses invocations, on focus les monstres d'origine encore vivants.

    Fallback : si plus aucun monstre d'origine n'est vivant (ou si le set initial
    n'a pas été renseigné), on retombe sur l'ensemble des monstres vivants — ainsi
    le bot finit toujours le combat même s'il ne reste que des invocations.
    """
    live = current.combat_live_monsters
    if current.combat_initial_monsters:
        primary = live & current.combat_initial_monsters
        if primary:
            return primary
    return set(live)


def get_targetable_monster_cells() -> list[int]:
    """cell_id des monstres ciblables en priorité (hors invocations).

    Pendant générique de ``get_live_monster_cells`` mais filtré sur les monstres
    d'origine (cf. ``get_targetable_monster_ids``). Utilisé pour la sélection de
    cible (single-target et zone AOE), pas pour le calcul de menace.
    """
    result = []
    for eid in get_targetable_monster_ids():
        cell = current.combat_entity_cells.get(eid)
        if cell is not None and cell >= 0:
            result.append(cell)
    return result


# ---------------------------------------------------------------------------
# Push vers le dashboard
# ---------------------------------------------------------------------------

def _push() -> None:
    """Sérialiser l'état courant vers le dashboard bridge."""
    c = current

    # Personnage
    if c.character:
        char_data: dict = {
            "character_id": c.character.character_id,
            "pseudo": c.character.pseudo,
            "level": c.character.level,
            "class_id": c.character.class_id,
            "class_name": CLASSES.get(c.character.class_id, ""),
        }
        if c.stats:
            char_data.update({
                "life":            c.stats.life,
                "max_life":        c.stats.max_life,
                "energy":          c.stats.energy,
                "max_energy":      c.stats.max_energy,
                "initiative":      c.stats.initiative,
                "prospecting":     c.stats.prospecting,
                "kamas":           c.stats.kamas,
                "action_points":   c.stats.action_points,
                "movement_points": c.stats.movement_points,
                "strength":        c.stats.strength,
                "vitality":        c.stats.vitality,
                "wisdom":          c.stats.wisdom,
                "intelligence":    c.stats.intelligence,
                "chance":          c.stats.chance,
                "agility":         c.stats.agility,
                "range_points":    c.stats.range_points,
                "stat_points":     c.stats.level_pct,
            })
        bridge.update_character(char_data)

    # Carte
    if c.current_map:
        bridge.update_map({"map_id": c.current_map.map_id, "date": c.current_map.date})

    # Ressources (GDF)
    bridge.update_resources([
        {"cell_id": e.elem_id, "available": e.available, "elem_type": e.elem_type}
        for e in c.frame_objects.elements.values()
    ])

    # Entités
    for entity in c.entities.values():
        bridge.set_entity({
            "entity_id":  entity.entity_id,
            "name":       entity.display_name,
            "level":      entity.level,
            "class_id":   entity.class_id,
            "class_name": entity.class_name,
            "sex":        entity.sex,
            "cell_id":    entity.cell_id,
            "is_monster": entity.is_monster,
        })


# ---------------------------------------------------------------------------
# Handlers — authentification / personnage
# ---------------------------------------------------------------------------

@on_server_message("ASK")
def _on_character_selected(msg: ParsedMessage) -> None:
    """ASK — personnage sélectionné (notre perso principal)."""
    try:
        current.character = CharacterInfo.parse(msg.payload)
        logger.info("[GameState] Personnage : %s", current.character)
        # Enregistrer la classe du perso principal (équipe mixte : ciblage de la séquence
        # de sorts par classe dans combat.py + sous-onglet Sorts par classe dans l'UI).
        current.entity_classes[current.character.character_id] = current.character.class_id
        _push_team_classes()
        bridge.add_console(f"Personnage {current.character.pseudo} Lv.{current.character.level} sélectionné")
        # Renseigner la team (onglet) avec le nom du perso principal + statut connecté.
        try:
            from core.session import active_or_none
            _sess = active_or_none()
            if _sess is not None:
                _sess.set_connected(current.character.pseudo, current.character.level)
        except Exception:
            pass
        _push()
    except Exception as exc:
        logger.warning("[GameState] ASK parse error : %s | payload=%r", exc, msg.payload[:80])


@on_server_message("AK")
def _on_account_key(msg: ParsedMessage) -> None:
    """AK — AccountKey : le serveur envoie les clés de chiffrement réseau.

    Format payload : {hex_idx}{hex_key}|{hex_idx}{hex_key}|...
    Chaque entrée : premier char = index hex (0-F), reste = clé en hex.

    Après réception, les clés sont transmises au module channel pour
    chiffrer les messages C→S envoyés par le bot (GA001, GA500, GKK0…).
    """
    from bot import channel as _channel
    try:
        a_keys: list[str | None] = [None] * 16
        parts = msg.payload.split("|")
        for part in parts:
            part = part.strip()
            if not part:
                continue
            try:
                idx = int(part[0], 16)  # premier char = index hex 0-F
                raw_key = part[1:]
                if raw_key:
                    a_keys[idx] = prepare_key(raw_key)
            except (ValueError, IndexError):
                continue
        n_keys = sum(1 for k in a_keys if k)
        logger.info("[GameState] AK : %d clés reçues", n_keys)
        # Ne pas encore activer le chiffrement — attendre AYK (startUsingKey)
        _channel.set_encryption_keys(a_keys, 0)
    except Exception as exc:
        logger.warning("[GameState] AK parse error : %s | payload=%r", exc, msg.payload[:80])


@on_server_message("ATK")
def _on_account_ticket_response(msg: ParsedMessage) -> None:
    """ATK — AccountTicketResponseSuccess : active le chiffrement réseau.

    Format payload : {hex_idx}{key_data}...
    Le premier char = index de départ pour _nCurrentKey (startUsingKey).
    Reproduit Account.onTicketResponse() de Aks.as.
    """
    from bot import channel as _channel
    try:
        if not msg.payload:
            return
        start_key_idx = int(msg.payload[0], 16)
        if start_key_idx > 0:
            current_keys = _channel.get_a_keys()
            _channel.set_encryption_keys(current_keys, start_key_idx)
            logger.info("[GameState] ATK : chiffrement activé, startKey=%d", start_key_idx)
        else:
            logger.info("[GameState] ATK : pas de chiffrement (start_key=0)")
    except Exception as exc:
        logger.warning("[GameState] ATK parse error : %s | payload=%r", exc, msg.payload[:20])


@on_server_message("SL")
def _on_spell_list(msg: ParsedMessage) -> None:
    """SL — SpellsList : liste des sorts possédés par le personnage actif.

    Format payload : {spellId}~{spellLevel}~{position};...
    Position = place dans la barre de sorts (-1 = non placé).
    Envoyé après ASK, après un level-up, ou après SLo (changement de perso héros).
    """
    try:
        spells: list[SpellEntry] = []
        for part in msg.payload.strip().rstrip(";").split(";"):
            part = part.strip()
            if not part:
                continue
            fields = part.split("~")
            if len(fields) < 3:
                continue
            spell_id = int(fields[0])
            spell_level = int(fields[1])
            position = int(fields[2])
            spells.append(SpellEntry(spell_id=spell_id, spell_level=spell_level, position=position))

        current.character_spells = spells

        # Sorts de la classe du perso principal (SL ne concerne que le perso principal).
        # Les héros ont leurs sorts via Nh (cf. _on_hero_spells).
        if current.character is not None:
            current.class_spells[current.character.class_id] = spells

        from data.spell_data import get_spell_name
        spell_names = [f"{get_spell_name(s.spell_id)} Nv.{s.spell_level}" for s in spells]
        logger.info("[GameState] SL : %d sorts — %s", len(spells), ", ".join(spell_names))

        bridge.update_spells(_spells_to_bridge_list(spells))
        _push_team_classes()
    except Exception as exc:
        logger.warning("[GameState] SL parse error : %s | payload=%r", exc, msg.payload[:80])


def _spells_to_bridge_list(spells: list[SpellEntry]) -> list[dict]:
    """Convertir les sorts en données pour le bridge/UI."""
    from data.spell_data import get_spell_level_info
    result = []
    for s in spells:
        level_info = get_spell_level_info(s.spell_id, s.spell_level)
        entry: dict = {
            "spell_id": s.spell_id,
            "spell_level": s.spell_level,
            "position": s.position,
            "name": level_info.name if level_info else f"Sort #{s.spell_id}",
        }
        if level_info:
            entry.update({
                "cost_pa": level_info.cost_pa,
                "range_min": level_info.range_min,
                "range_max": level_info.range_max,
                "launch_inline": level_info.launch_inline,
                "vision_line": level_info.vision_line,
                "modifiable_distance": level_info.modifiable_distance,
                "launch_per_turn": level_info.launch_per_turn,
                "launch_per_target": level_info.launch_per_target,
                "cooldown": level_info.cooldown,
            })
        result.append(entry)
    return result


def _push_team_classes() -> None:
    """Émettre vers l'UI la liste des classes présentes dans l'équipe + leurs sorts.

    Une classe apparaît dès qu'un allié (perso principal ou héros) de cette classe est
    détecté (entity_classes). Les sorts proviennent de class_spells (SL pour le perso
    principal, Nh pour les héros) — éventuellement vides tant que le Nh/SL n'est pas arrivé.
    L'onglet Combat crée un sous-onglet Sorts par classe à partir de cet événement.
    """
    classes: list[dict] = []
    seen: set[int] = set()
    for class_id in current.entity_classes.values():
        if class_id in seen or not class_id:
            continue
        seen.add(class_id)
        spells = current.class_spells.get(class_id, [])
        classes.append({
            "class_id":   class_id,
            "class_name": CLASSES.get(class_id, f"#{class_id}"),
            "spells":     _spells_to_bridge_list(spells),
        })
    classes.sort(key=lambda c: c["class_name"])
    bridge.update_team_classes(classes)


def get_entity_class_id(entity_id: str) -> int | None:
    """Classe d'un combattant allié (perso principal ou héros), ou None si inconnue."""
    cid = current.entity_classes.get(entity_id)
    if cid is not None:
        return cid
    if current.character is not None and entity_id == current.character.character_id:
        return current.character.class_id
    return None


def get_class_spell_level(class_id: int | None, spell_id: int) -> int:
    """Niveau connu d'un sort pour une classe (défaut 1 si inconnu).

    Cherche dans class_spells[class_id], puis dans les sorts du perso principal,
    sinon retourne 1 (niveau minimal, propriétés de base depuis spells.xml).
    """
    if class_id is not None:
        for s in current.class_spells.get(class_id, []):
            if s.spell_id == spell_id:
                return s.spell_level
    for s in current.character_spells:
        if s.spell_id == spell_id:
            return s.spell_level
    return 1


@on_server_message("HG")
def _on_hello_game(_msg: ParsedMessage) -> None:
    """HG — premier message du game server. Autorise le bot à envoyer des messages."""
    from bot import channel as _channel
    _channel.mark_game_connection()


@on_server_message("GCK")
def _on_game_character_create(msg: ParsedMessage) -> None:
    """GCK — notre personnage vient d'entrer en jeu."""
    parts = msg.payload.split("|")
    name = parts[2].strip() if len(parts) > 2 else "?"
    logger.info("[GameState] GCK : %s en jeu", name)
    bridge.add_console(f"Session démarrée — {name} en jeu")

    # Reprise auto du bot combat après une reconnexion : GCK refire à chaque
    # ré-entrée en jeu. on_game_ready() est idempotent (ne relance que si
    # l'utilisateur avait lancé le bot et qu'il ne tourne pas déjà).
    try:
        from bot import combat as _combat
        _combat.on_game_ready()
    except Exception:
        logger.debug("[GameState] GCK : on_game_ready indisponible", exc_info=True)


@on_server_message("As")
def _on_account_stats(msg: ParsedMessage) -> None:
    """As — stats d'un personnage.

    Avant GDK : le serveur envoie les As de tous les persos de la map.
    Le dernier As avant GDK appartient à notre perso → on accumule.
    Après GDK : les As arrivent pour tous les persos (notre perso + héros).
    On les route grâce à l'empreinte XP (field[0]) corrélée avec les Ow pré-GDK.
    """

    try:
        stats = CharacterStats.parse(msg.payload)
        # Empreinte XP = premier champ du payload (expA,expC,expNext)
        xp_key = msg.payload.split("|", 1)[0]

        if current._map_loaded:
            # Routage : XP-key d'abord, fingerprint (kamas, max_life) en fallback.
            char_id = current._xp_to_char.get(xp_key)
            if char_id is None:
                # XP-key périmée (XP gagnée depuis la corrélation pré-GDK)
                # → empreinte stable comme fallback
                char_id = current._hero_fingerprint.get((stats.kamas, stats.max_life))
                if char_id is not None:
                    # Self-heal : mémoriser la nouvelle XP-key pour ce héros
                    current._xp_to_char[xp_key] = char_id
                    logger.debug("[GameState] As : XP-key mise à jour héros %s", char_id)

            if char_id is not None and current.character and char_id != current.character.character_id:
                # As d'un héros → mettre à jour toutes ses stats dans le bridge
                bridge.update_hero(char_id, {
                    "life":             stats.life,
                    "max_life":         stats.max_life,
                    "kamas":            stats.kamas,
                    "stat_points":      stats.level_pct,
                    "energy":           stats.energy,
                    "max_energy":       stats.max_energy,
                    "action_points":    stats.action_points,
                    "movement_points":  stats.movement_points,
                    "initiative":       stats.initiative,
                    "prospecting":      stats.prospecting,
                    "strength":         stats.strength,
                    "vitality":         stats.vitality,
                    "wisdom":           stats.wisdom,
                    "intelligence":     stats.intelligence,
                    "chance":           stats.chance,
                    "agility":          stats.agility,
                    "range_points":     stats.range_points,
                })
                current._hero_hp_cache[char_id] = (stats.life, stats.max_life)
                if current.in_combat and stats.total_range_points >= 0:
                    current.combat_entity_po[char_id] = stats.total_range_points
                logger.debug("[GameState] As héros %s : HP=%d/%d PO=%d", char_id, stats.life, stats.max_life, stats.total_range_points)
                if stats.level_pct > 0:
                    _schedule_auto_boost(char_id, stats.level_pct)
            else:
                # As du perso principal → mise à jour normale
                current.stats = stats
                if current.in_combat and current.character:
                    current.combat_entity_po[current.character.character_id] = stats.total_range_points
                logger.debug("[GameState] Stats (update) : HP=%d/%d kamas=%d",
                             stats.life, stats.max_life, stats.kamas)
                _push()
                if stats.level_pct > 0 and current.character:
                    _schedule_auto_boost(current.character.character_id, stats.level_pct)
        else:
            # Pré-GDK : on garde le dernier (= notre perso, envoyé juste avant GDK)
            current._pending_stats = stats
            current._pending_xp_key = xp_key
    except Exception as exc:
        logger.warning("[GameState] As parse error : %s", exc)


# ---------------------------------------------------------------------------
# Handlers — carte
# ---------------------------------------------------------------------------

@on_server_message("GDM")
def _on_map_data(msg: ParsedMessage) -> None:
    """GDM — nouvelle carte chargée."""
    current._companion_ids.clear()
    current._pending_xp_key = None
    # Reset état combat si un changement de map intervient (fin de combat → retour map)
    if current.in_combat:
        current.in_combat = False
        current._combat_ended_event.set()
    try:
        # Sauvegarder la position du perso avant de vider les entités.
        # Utile après un combat : même map_id → restaurer position au GDK.
        current._pre_gdm_map_id = current.current_map.map_id if current.current_map else None

        # Priorité 1 : sortie intentée par le bot (immune aux overwrites de Flash)
        try:
            from bot import mapnav as _mapnav
            _bot_exit = _mapnav.get_pending_exit()
        except Exception:
            _bot_exit = None

        if _bot_exit is not None:
            bot_exit_cell, bot_exit_map_id = _bot_exit
            # Ne l'utiliser que si la map source correspond
            if bot_exit_map_id == current._pre_gdm_map_id:
                current._pre_gdm_char_cell = bot_exit_cell
                logger.info(
                    "[GameState] GDM : exit cell depuis mapnav = %d (map #%s)",
                    bot_exit_cell, current._pre_gdm_map_id,
                )
            else:
                # Map source différente — utiliser entity.cell_id comme d'habitude
                if current.character:
                    ent = current.entities.get(current.character.character_id)
                    current._pre_gdm_char_cell = ent.cell_id if ent and ent.cell_id >= 0 else -1
                else:
                    current._pre_gdm_char_cell = -1
        else:
            if current.character:
                ent = current.entities.get(current.character.character_id)
                current._pre_gdm_char_cell = ent.cell_id if ent and ent.cell_id >= 0 else -1
            else:
                current._pre_gdm_char_cell = -1

        # Sauvegarder les cellules des autres entités présentes (hP avant GDM).
        # Permet d'inférer la position sur la map initiale (connexion) quand
        # _pre_gdm_char_cell est inconnu.
        char_id = current.character.character_id if current.character else None
        current._pre_gdm_other_cells = [
            e.cell_id for eid, e in current.entities.items()
            if e.cell_id >= 0 and eid != char_id
        ]

        current._map_loaded = False
        current._pending_stats = None
        current._map_ready_event.clear()
        current.current_map = MapData.parse(msg.payload)
        current.frame_objects = FrameObjects()
        current.entities.clear()
        current.movements.clear()
        bridge.clear_entities()

        # Charger les données statiques de la map (XML LeafMITM)
        current._map_info = load_map(current.current_map.map_id)
        if current._map_info is not None:
            _populate_resources_from_map(current._map_info)
        else:
            bridge.add_console(f"⚠ XML manquant pour map #{current.current_map.map_id} — ressources non identifiables")

        logger.info("[GameState] Carte : %s", current.current_map)
        bridge.add_console(f"Carte #{current.current_map.map_id} chargée")
        _push()
    except Exception as exc:
        logger.warning("[GameState] GDM parse error : %s", exc)


def _populate_resources_from_map(info: MapInfo) -> None:
    """Pré-remplir frame_objects avec les ressources statiques de la map XML.

    Les ressources GDF mettront à jour l'état (available) au fur et à mesure.
    On initialise toutes les ressources en état 'disponible' (state=5) car
    on ne connaît pas leur état réel avant un GDF.
    """
    from bot.mapdata import get_resource_ga_type
    for cell_id, layer_obj2 in info.resource_cells:
        ga_type = get_resource_ga_type(layer_obj2)
        elem = InteractiveElement(elem_id=cell_id, state=5, elem_type=ga_type, resource_id=layer_obj2)
        current.frame_objects.elements[cell_id] = elem
    if info.resource_cells:
        logger.info(
            "[GameState] %d ressources chargées depuis XML (map #%d)",
            len(info.resource_cells), info.map_id,
        )


@on_server_message("GDF")
def _on_frame_objects(msg: ParsedMessage) -> None:
    """GDF — éléments interactifs (ressources, portes…).

    GDF peut être partiel (un seul elem qui change d'état) ou complet.
    On fusionne les éléments reçus dans la liste existante plutôt que
    de la remplacer, pour ne pas perdre les ressources non modifiées.
    """
    try:
        logger.info("[GameState] GDF payload brut : %r", msg.payload[:120])
        partial = FrameObjects.parse(msg.payload)

        # Détecter les éléments qui passent de disponible à indisponible
        # Les ressources sont pré-initialisées comme disponibles (depuis XML ou état=5).
        # Tout GDF montrant un passage available→unavailable = récolte en cours.
        for elem_id, elem in partial.elements.items():
            prev = current.frame_objects.elements.get(elem_id)
            if prev is not None and prev.available and not elem.available:
                current._last_harvested_cell = elem_id
                logger.info("[GameState] GDF : cellule %d → cooldown (récolte détectée)", elem_id)

        # Fusion : mettre à jour uniquement les éléments reçus
        # Préserver resource_id et elem_type (issus de Recolte.txt) car le
        # troisième champ GDF n'est PAS le type GA500 (c'est 0 ou 1 sur ce serveur).
        for elem_id, elem in partial.elements.items():
            prev = current.frame_objects.elements.get(elem_id)
            if prev is not None and prev.resource_id:
                elem.resource_id = prev.resource_id
                elem.elem_type = prev.elem_type
            current.frame_objects.elements[elem_id] = elem
        avail = current.frame_objects.available
        logger.info("[GameState] GDF (merge) : %s — elem_ids=%s",
                    current.frame_objects,
                    [e.elem_id for e in list(partial.elements.values())[:5]])
        bridge.add_console(f"Ressources : {len(avail)}/{len(current.frame_objects.elements)} disponibles")
        _push()
    except Exception as exc:
        logger.warning("[GameState] GDF parse error : %s", exc)


def _infer_arrival_from_exit(exit_cell: int, old_map_id: int, new_map_id: int) -> int | None:
    """Inférer la cellule d'arrivée sur la nouvelle map depuis la cellule de sortie.

    Priorité 1 : cache auto-apprenant (arrivées précédemment confirmées par GA1).
    Priorité 2 : heuristique basée sur les SunMagic cells de MAPA_DATA.
    """
    # --- Priorité 1 : cache d'arrivée ---
    cached = lookup_arrival(old_map_id, exit_cell, new_map_id)
    if cached is not None:
        logger.info(
            "[GameState] Arrivée depuis cache : map %d exit %d → map %d cell %d",
            old_map_id, exit_cell, new_map_id, cached,
        )
        return cached

    from bot.pathfinding import cell_to_xy

    old_info = load_map(old_map_id)
    new_info = load_map(new_map_id)
    if old_info is None or new_info is None:
        return None

    old_width = old_info.width
    new_width = new_info.width

    old_walkable = old_info.walkable_cells
    if not old_walkable:
        return None
    old_max_y = max(cell_to_xy(c, old_width)[1] for c in old_walkable)

    new_walkable = new_info.walkable_cells
    if not new_walkable:
        return None
    new_max_y = max(cell_to_xy(c, new_width)[1] for c in new_walkable)

    exit_x, exit_y = cell_to_xy(exit_cell, old_width)

    # Déterminer la bordure d'arrivée (opposée à la direction de sortie)
    # Note : x >= width-2 couvre les rangées impaires dont le max x = width-2
    if exit_x == 0:
        arrival_border = "right"
    elif exit_x >= old_width - 2:
        arrival_border = "left"
    elif exit_y <= 1:
        arrival_border = "bottom"
    elif exit_y >= old_max_y - 1:
        arrival_border = "top"
    else:
        # Cellule intérieure (téléport magique) — inférence impossible
        return None

    def _on_arrival_border(cell_id: int) -> bool:
        x, y = cell_to_xy(cell_id, new_width)
        if arrival_border == "left":   return x == 0
        if arrival_border == "right":  return x >= new_width - 2
        if arrival_border == "top":    return y <= 1
        if arrival_border == "bottom": return y >= new_max_y - 1
        return False

    # Priorité 1 : SUN_MAGIC sur la bordure d'arrivée → adjacent inward
    # Le joueur n'apparaît PAS sur le sun_magic mais sur sa cellule voisine
    # qui est un pas vers l'intérieur de la map (hors bordure).
    sun_on_border = [c for c in new_info.sun_magic_cells if _on_arrival_border(c)]

    from bot.pathfinding import xy_to_cell, _PAIR_NEIGHBORS, _ODD_NEIGHBORS

    def _inward_neighbor(sun_cell: int) -> int | None:
        """Trouver le voisin walkable du sun_magic qui pointe vers l'intérieur."""
        sx, sy = cell_to_xy(sun_cell, new_width)
        neighbors = _PAIR_NEIGHBORS if sy % 2 == 0 else _ODD_NEIGHBORS
        candidates: list[tuple[int, int, int]] = []
        for di, dj in neighbors[:6]:  # 6 premiers = voisins immédiats
            nx, ny = sx + di, sy + dj
            if nx < 0 or nx >= new_width or ny < 0:
                continue
            nc = xy_to_cell(nx, ny, new_width)
            if nc not in new_walkable:
                continue
            candidates.append((nc, nx, ny))
        if not candidates:
            return None
        if len(candidates) == 1:
            return candidates[0][0]
        # Choisir le voisin qui s'éloigne le plus de la bordure d'arrivée
        def _inward_score(t: tuple[int, int, int]) -> float:
            _, nx, ny = t
            if arrival_border == "left":   return nx        # max x = plus à droite
            if arrival_border == "right":  return -nx       # min x = plus à gauche
            if arrival_border == "top":    return ny        # max y = plus en bas
            if arrival_border == "bottom": return -ny       # min y = plus en haut
            return 0
        return max(candidates, key=_inward_score)[0]


    if sun_on_border:
        if len(sun_on_border) == 1:
            inward = _inward_neighbor(sun_on_border[0])
            return inward if inward is not None else sun_on_border[0]
        # Plusieurs SUN_MAGIC → prendre le plus proche de la position de sortie
        if arrival_border in ("left", "right"):
            best_sun = min(sun_on_border, key=lambda c: abs(cell_to_xy(c, new_width)[1] - exit_y))
        else:
            best_sun = min(sun_on_border, key=lambda c: abs(cell_to_xy(c, new_width)[0] - exit_x))
        inward = _inward_neighbor(best_sun)
        return inward if inward is not None else best_sun

    # Priorité 2 : toute cellule praticable sur la bordure d'arrivée → son voisin inward
    border_walkable = [c for c in new_walkable if _on_arrival_border(c)]
    if border_walkable:
        if arrival_border in ("left", "right"):
            best = min(border_walkable, key=lambda c: abs(cell_to_xy(c, new_width)[1] - exit_y))
        else:
            best = min(border_walkable, key=lambda c: abs(cell_to_xy(c, new_width)[0] - exit_x))
        inward = _inward_neighbor(best)
        return inward if inward is not None else best

    # Dernier recours : cellule walkable la plus proche
    return min(
        new_walkable,
        key=lambda c: (cell_to_xy(c, new_width)[0] - exit_x) ** 2
                    + (cell_to_xy(c, new_width)[1] - exit_y) ** 2,
    )


@on_server_message("GDK")
def _on_map_loaded(_msg: ParsedMessage) -> None:
    """GDK — carte prête côté client. Le dernier As reçu = stats de notre perso."""
    current._last_harvested_cell = -1  # reset à chaque nouvelle map
    current._arrival_context = None    # reset le contexte d'arrivée
    current._map_loaded = True
    if current._pending_stats is not None:
        current.stats = current._pending_stats
        logger.info("[GameState] Stats appliquées : HP=%d/%d PA=%d PM=%d Init=%d Prosp=%d",
                    current.stats.life, current.stats.max_life,
                    current.stats.action_points, current.stats.movement_points,
                    current.stats.initiative, current.stats.prospecting)
        current._pending_stats = None

    # Injecter notre propre perso dans les entités s'il n'y est pas encore
    if current.character:
        cid = current.character.character_id
        if cid not in current.entities:
            inferred_cell = -1
            new_map_id = current.current_map.map_id if current.current_map else None

            # 1) Retour de combat = même map_id que pré-GDM → restaurer position
            if (new_map_id is not None
                    and new_map_id == current._pre_gdm_map_id
                    and current._pre_gdm_char_cell >= 0):
                inferred_cell = current._pre_gdm_char_cell
                logger.info(
                    "[GameState] GDK : position restaurée (retour combat) cell=%d",
                    inferred_cell,
                )

            # 2) Changement de map via bordure → inférer l'arrivée depuis la sortie
            elif (new_map_id is not None
                  and current._pre_gdm_map_id is not None
                  and new_map_id != current._pre_gdm_map_id
                  and current._pre_gdm_char_cell >= 0):
                # Sauvegarder le contexte pour le cache d'arrivée (GA1 confirmera)
                current._arrival_context = (current._pre_gdm_map_id, current._pre_gdm_char_cell, new_map_id)
                inferred = _infer_arrival_from_exit(
                    current._pre_gdm_char_cell, current._pre_gdm_map_id, new_map_id
                )
                if inferred is not None:
                    inferred_cell = inferred
                    logger.info(
                        "[GameState] GDK : position inférée (exit cell=%d map %d→%d) → cell=%d",
                        current._pre_gdm_char_cell, current._pre_gdm_map_id, new_map_id, inferred_cell,
                    )
                else:
                    logger.warning(
                        "[GameState] GDK : inférence impossible (exit=%d map %d→%d) — position inconnue",
                        current._pre_gdm_char_cell, current._pre_gdm_map_id, new_map_id,
                    )

            # 3) Sinon, tenter d'inférer depuis les entités courantes ou pré-GDM
            else:
                # Entités ajoutées entre GDM et GDK (hP reçus après GDM)
                cells = [e.cell_id for e in current.entities.values() if e.cell_id >= 0]
                # Fallback : entités sauvegardées avant GDM (hP reçus AVANT GDM — connexion initiale)
                if not cells:
                    cells = current._pre_gdm_other_cells
                if cells:
                    from collections import Counter
                    most_common_cell, count = Counter(cells).most_common(1)[0]
                    inferred_cell = most_common_cell
                    logger.info(
                        "[GameState] GDK : position inférée depuis entités = cell %d "
                        "(%d entité(s))", inferred_cell, count,
                    )

            self_entity = EntityInfo(
                entity_id=cid,
                name=current.character.pseudo,
                level=current.character.level,
                class_id=current.character.class_id,
                cell_id=inferred_cell,
            )
            current.entities[cid] = self_entity
            logger.info("[GameState] Entité self injectée : %s (cell=%d)", cid, inferred_cell)

        else:
            # Notre perso est déjà dans entities (NL reçu avant GDK).
            # Si sa position est inconnue, tenter l'inférence depuis la sortie.
            entity = current.entities[cid]
            if entity.cell_id < 0:
                new_map_id = current.current_map.map_id if current.current_map else None
                if (new_map_id is not None
                        and current._pre_gdm_map_id is not None
                        and new_map_id != current._pre_gdm_map_id
                        and current._pre_gdm_char_cell >= 0):
                    current._arrival_context = (current._pre_gdm_map_id, current._pre_gdm_char_cell, new_map_id)
                    inferred = _infer_arrival_from_exit(
                        current._pre_gdm_char_cell, current._pre_gdm_map_id, new_map_id
                    )
                    if inferred is not None:
                        entity.cell_id = inferred
                        logger.info(
                            "[GameState] GDK : position corrigée (entité existante) cell=%d",
                            inferred,
                        )

    # Effacer la sortie en attente du bot (inférence terminée)
    try:
        from bot import mapnav as _mapnav
        _mapnav.clear_pending_exit()
    except Exception:
        pass

    # Appliquer les hP bufferisés (joueurs/marchands reçus avant GDM)
    if current._pending_hp:
        for entity in current._pending_hp:
            if entity.entity_id not in current.entities:
                current.entities[entity.entity_id] = entity
                bridge.set_entity({
                    "entity_id": entity.entity_id,
                    "name":      entity.name,
                    "level":     0,
                    "class_id":  0,
                    "class_name": "",
                    "sex":       entity.sex,
                    "cell_id":   entity.cell_id,
                    "is_monster": False,
                    "guild_name": entity.guild_name,
                })
        logger.info(
            "[GameState] GDK : %d entité(s) hP restaurée(s)", len(current._pending_hp),
        )
        current._pending_hp.clear()

    map_id = current.current_map.map_id if current.current_map else "?"
    logger.info("[GameState] Carte #%s prête.", map_id)

    bridge.add_console(f"Carte #{map_id} prête — en jeu")
    if current.current_map:
        bridge.notify_map_ready(current.current_map.map_id)
    current._map_ready_event.set()
    _push()


@on_server_message("GM")
def _on_entity_movement(msg: ParsedMessage) -> None:
    """GM — liste d'entités OU déplacement d'une entité.

    Après GDK, le serveur envoie un GM contenant toutes les entités de la carte
    (joueurs, groupes de monstres, PNJ). Le payload est pipe-delimited avec
    préfixe ``+``/``%2B`` (ajout) ou ``~`` (déjà présent).

    Le reste du temps, GM contient un simple déplacement (entity_id;path).
    """
    if is_gm_entity_list(msg.payload):
        _handle_gm_entity_list(msg.payload)
        return

    try:
        movement = EntityMovement.parse(msg.payload)
        current.movements[movement.entity_id] = movement

        if movement.path_encoded and len(movement.path_encoded) >= 2:
            cells = decode_path(movement.path_encoded)
            if cells:
                dest_cell = cells[-1]
                eid = movement.entity_id
                if eid in current.entities:
                    current.entities[eid].cell_id = dest_cell
                    logger.debug("[GameState] GM : %s → cell %d", eid, dest_cell)
                elif current.character and eid == current.character.character_id:
                    self_entity = EntityInfo(
                        entity_id=eid,
                        name=current.character.pseudo,
                        level=current.character.level,
                        class_id=current.character.class_id,
                        cell_id=dest_cell,
                    )
                    current.entities[eid] = self_entity
                    logger.debug("[GameState] GM self : %s → cell %d", eid, dest_cell)
                _push()
    except Exception as exc:
        logger.debug("[GameState] GM parse error : %s", exc)


def _extract_combat_entity_po(fields: list[str], entity_id: str) -> int:
    """Extraire le bonus PO depuis les champs d'un placement GM en combat.

    Le format des champs de placement est :
      cell;dir;bonus;entity_id;name;class;gfx^scale;sex;[guild;emblem;]level;acc;c1;c2;c3;HP;PA;PM;PO;...
    Le PO se trouve 3 champs après HP. HP est détecté comme le premier entier ≥ 100
    à partir de l'index 14, ou comme max_life connu pour ce personnage.
    """
    def _to_int(s: str) -> int:
        s = s.strip()
        return int(s) if s.lstrip("-").isdigit() else -1

    known_hp = 0
    if current.character and entity_id == current.character.character_id:
        known_hp = current.stats.max_life if current.stats else 0
    else:
        cached = current._hero_hp_cache.get(entity_id)
        if cached:
            known_hp = cached[1]

    for hp_pos in range(14, min(len(fields), 26)):
        v = _to_int(fields[hp_pos])
        if v < 0:
            continue
        if v >= 100 or (known_hp > 0 and v == known_hp):
            po_pos = hp_pos + 3
            if po_pos < len(fields):
                po = _to_int(fields[po_pos])
                return max(0, po)
            return 0
    return 0


def _handle_gm_entity_list(payload: str) -> None:
    """Parser et enregistrer la liste d'entités reçue via GM après GDK."""
    try:
        players, monster_groups, npcs, others = parse_gm_entities(payload)

        # NOTE : on n'extrait PLUS le bonus PO depuis le GM. L'ancienne heuristique
        # (_extract_combat_entity_po, qui devinait la position du HP puis lisait le PO
        # 3 champs plus loin) renvoyait des valeurs fausses (ex : 10 au lieu de 3),
        # gonflant effective_max = range_max + po et provoquant des rejets de portée
        # serveur en rafale (Im 1171 "vous visez à 16"). Le PO fiable vient uniquement
        # du packet As (CharacterStats.total_range_points), renseigné par
        # _on_account_stats. Tant qu'aucun As n'a renseigné un allié, get_entity_po()
        # renvoie 0 (défaut prudent : on sous-estime la portée, ce qui ne déclenche
        # jamais de rejet serveur), et le premier As le corrige à sa vraie valeur.

        my_id = current.character.character_id if current.character else None

        for p in players:
            if p.entity_id == my_id:
                # Mettre à jour notre propre cellule depuis GM (position réelle après combat)
                if my_id in current.entities and p.cell_id >= 0:
                    current.entities[my_id].cell_id = p.cell_id
                    logger.debug("[GameState] GM : self → cell %d", p.cell_id)
                continue
            if p.entity_id in current._companion_ids:
                continue
            entity = EntityInfo.from_gm_player(p)
            current.entities[entity.entity_id] = entity

            bridge.set_entity({
                "entity_id":  entity.entity_id,
                "name":       entity.name,
                "level":      entity.level,
                "class_id":   entity.class_id,
                "class_name": entity.class_name,
                "sex":        entity.sex,
                "cell_id":    entity.cell_id,
                "direction":  entity.direction,
                "is_monster": False,
                "guild_name": entity.guild_name,
                "gfx_id":     entity.gfx_id,
                "scale":      entity.scale,
                "align_side": entity.align_side,
                "align_rank": entity.align_rank,
                "align_honor": entity.align_honor,
                "align_disgrace": entity.align_disgrace,
                "align_extra": entity.align_extra,
                "color1":     entity.color1,
                "color2":     entity.color2,
                "color3":     entity.color3,
                "accessories": entity.accessories,
                "aura":       entity.aura,
                "emblem":     entity.emblem,
                "restrictions": entity.restrictions,
                "mount":      entity.mount,
            })

        for mg in monster_groups:
            entity = EntityInfo.from_gm_monster(mg)
            current.entities[entity.entity_id] = entity

            bridge.set_entity({
                "entity_id":  entity.entity_id,
                "name":       entity.display_name,
                "level":      entity.level,
                "class_id":   0,
                "class_name": entity.class_name,
                "sex":        0,
                "cell_id":    entity.cell_id,
                "is_monster": True,
                "bonus":      entity.bonus,
                "monster_ids": entity.monster_ids,
                "monster_levels": entity.monster_levels,
            })

        for npc in npcs:
            entity = EntityInfo.from_gm_npc(npc)
            current.entities[entity.entity_id] = entity

            bridge.set_entity({
                "entity_id":  entity.entity_id,
                "name":       entity.name,
                "level":      0,
                "class_id":   0,
                "class_name": "PNJ",
                "sex":        0,
                "cell_id":    entity.cell_id,
                "is_monster": False,
                "is_npc":     True,
                "gfx_id":     entity.gfx_id,
            })

        # Décor à id négatif : percepteur, prisme, monture en parc, perso hors-ligne.
        # Enregistrés pour l'affichage carte, mais is_monster=False → jamais ciblés
        # par un GA907, que le serveur ignorerait en laissant le bot boucler.
        other_labels: list[str] = []
        for other in others:
            entity = EntityInfo.from_gm_other(other)
            current.entities[entity.entity_id] = entity
            other_labels.append(entity.class_name)

            bridge.set_entity({
                "entity_id":  entity.entity_id,
                "name":       entity.display_name,
                "level":      entity.level,
                "class_id":   0,
                "class_name": entity.class_name,
                "entity_type": entity.entity_type,
                "sex":        0,
                "cell_id":    entity.cell_id,
                "direction":  entity.direction,
                "is_monster": False,
                "guild_name": entity.guild_name,
                "gfx_id":     entity.gfx_id,
                "scale":      entity.scale,
            })

        n_players = len(players) - (1 if any(p.entity_id == my_id for p in players) else 0)
        n_monsters = len(monster_groups)
        n_npcs = len(npcs)
        parts = []
        if n_players:
            parts.append(f"{n_players} joueur(s)")
        if n_monsters:
            parts.append(f"{n_monsters} groupe(s) de monstres")
        if n_npcs:
            parts.append(f"{n_npcs} PNJ")
        for label in sorted(set(other_labels)):
            parts.append(f"{other_labels.count(label)} {label.lower()}")
        if parts:
            bridge.add_console(f"Entités : {', '.join(parts)}")
            logger.info("[GameState] GM entités : %s", ", ".join(parts))
        else:
            logger.debug("[GameState] GM entités : aucune entité parsée")

    except Exception as exc:
        logger.warning("[GameState] GM entity list parse error : %s", exc)


@on_server_message("GM|-")
def _on_entity_remove(msg: ParsedMessage) -> None:
    """GM|- — une entité quitte la carte (joueur, monstre, PNJ).

    Format payload : {entity_id}
    """
    eid = msg.payload.strip()
    if not eid:
        return

    my_id = current.character.character_id if current.character else None
    if eid == my_id:
        return

    if eid in current.entities:
        entity = current.entities.pop(eid)
        bridge.remove_entity(eid)
        logger.debug("[GameState] GM|- : %s (%s) retiré", entity.name or eid, eid)
    else:
        logger.debug("[GameState] GM|- : %s (inconnu, ignoré)", eid)


def _track_other_entity_movement(entity_id: str, path_data: str) -> None:
    """Tracker le mouvement d'une autre entité (joueur/monstre) via GA;1."""
    try:
        path_data = path_data.rstrip("\n").rstrip()
        if len(path_data) < 3:
            return
        last_wp = path_data[-3:]
        hi = ZIPKEY.find(last_wp[1])
        lo = ZIPKEY.find(last_wp[2])
        if hi < 0 or lo < 0:
            return
        dest_cell = hi * 64 + lo

        if entity_id in current.entities:
            current.entities[entity_id].cell_id = dest_cell
            # Synchroniser combat_entity_cells si en combat (sinon get_live_monster_cells() est obsolète)
            if current.in_combat and entity_id in current.combat_entity_cells:
                current.combat_entity_cells[entity_id] = dest_cell
            entity = current.entities[entity_id]
            bridge.set_entity({
                "entity_id":  entity.entity_id,
                "name":       entity.display_name,
                "level":      entity.level,
                "class_id":   entity.class_id,
                "class_name": entity.class_name,
                "sex":        entity.sex,
                "cell_id":    dest_cell,
                "is_monster": entity.is_monster,
            })
            logger.debug("[GameState] GA1 other : %s → cell %d", entity_id, dest_cell)
    except Exception as exc:
        logger.debug("[GameState] GA1 other parse error : %s", exc)


@on_server_message("GA")
def _on_game_action(msg: ParsedMessage) -> None:
    """GA S→C — confirmation d'une action de jeu.

    Format payload : {seq};{action_id};{entity_id};{data}
    Pour action_id=1 (mouvement), data = chemin ZIPKEY (3 chars/waypoint).
    On décode le dernier waypoint pour obtenir la position finale confirmée
    par le serveur — source la plus fiable (gère téléports, map changes, combat).
    """
    if current.character is None:
        return
    try:
        parts = msg.payload.split(";", 3)
        if len(parts) < 3:
            return
        action_id = parts[1]
        entity_id = parts[2]
        is_self = entity_id == current.character.character_id

        # --- Combat : confirmation du cast courant (GA;300 du lanceur) ---
        # Marque que le GAF qui suivra (et qui arrive APRÈS le GA;102 de coût) est bien
        # celui du cast courant → wait_spell_result repartira avec des PA à jour.
        if action_id == "300" and entity_id == current._spell_result_caster:
            current._spell_cast_confirmed = True
            return

        # --- Combat : mise à jour PA/PM en cours de tour ---
        # 102 = perte de PA (coût d'un sort), 129 = perte de PM (déplacement),
        # 101 = perte de PA due à un TACLE (drain quand on entre/sort du contact
        # d'un monstre pendant un déplacement).
        #
        # HISTORIQUE : on ignorait 101 (crainte de double-compter le coût d'un sort).
        # Vérifié sur les logs : GA;101 n'arrive JAMAIS après un cast (GA300), TOUJOURS
        # après un déplacement (GA001), et sa valeur correspond exactement au drain PA
        # confirmé par le serveur (ex. Im 1170;3~4 après un GA;101;…,-3). Le coût des
        # sorts passe lui par 102 → aucun chevauchement, aucun double-comptage. Ne PAS
        # le compter laissait le PA local trop haut → le bot castait un sort que le
        # serveur refusait (Im 1170 « il vous faut X PA ») = signature bot. On le
        # compte donc : le PA local reflète le tacle et le bot saute proprement le sort.
        if action_id in ("101", "102", "129") and len(parts) >= 4:
            data = parts[3]
            # GA;129 sans delta (ex. "403035;403035") ou avec un delta NUL
            # (ex. "403035,-0") = mouvement refusé par le serveur, typiquement un
            # tacle : un déplacement réel consomme toujours au moins 1 PM.
            # Ne JAMAIS parser le cas sans delta comme une perte de PM (l'ancien
            # int(data) ajoutait 403035 au PM !). On lève l'event de rejet pour
            # débloquer wait_move_result, sinon le tour reste gelé.
            if action_id == "129":
                _, _, pm_raw = data.partition(",")
                try:
                    pm_lost = int(pm_raw)
                except ValueError:
                    pm_lost = 0          # pas de delta du tout
                if pm_lost == 0:
                    current._move_rejected_event.set()
                    logger.debug(
                        "[GameState] GA;129 sans perte de PM : mouvement refusé (%s)", data,
                    )
                    return
            if entity_id in current.combat_entity_pa or entity_id in current.combat_entity_cells:
                try:
                    delta = int(data.split(",")[1]) if "," in data else int(data)
                    if action_id in ("101", "102"):
                        current.combat_entity_pa[entity_id] = (
                            current.combat_entity_pa.get(entity_id, 0) + delta
                        )
                        if action_id == "101" and delta != 0:
                            logger.debug(
                                "[GameState] GA;101 tacle : %s perd %d PA → %d",
                                entity_id, -delta, current.combat_entity_pa[entity_id],
                            )
                    else:
                        current.combat_entity_pm[entity_id] = (
                            current.combat_entity_pm.get(entity_id, 0) + delta
                        )
                except (ValueError, IndexError):
                    pass
            return

        # --- Combat : mort d'une entité ---
        if action_id == "999" and len(parts) >= 4:
            dead_id = entity_id
            data = parts[3]
            if dead_id.lstrip("-").isdigit() and int(dead_id) < 0:
                current.combat_live_monsters.discard(dead_id)
                logger.debug("[GameState] GA999 : monstre %s mort", dead_id)
            if data.startswith("GTL|"):
                gtl_ids = data[4:].split("|")
                current.combat_live_monsters = {
                    p.strip()
                    for p in gtl_ids
                    if p.strip().lstrip("-").isdigit() and int(p.strip()) < 0
                }
            return

        # --- Combat : entité retirée ---
        if action_id == "1000":
            removed_id = entity_id
            if removed_id:
                current.combat_entity_cells.pop(removed_id, None)
                current.combat_live_monsters.discard(removed_id)
            return

        # --- Hors-combat : seulement GA1 et GA501 ---
        if action_id not in ("1", "501"):
            return

        # Tracker le mouvement des autres entités (joueurs/monstres)
        if not is_self and action_id == "1" and len(parts) >= 4:
            _track_other_entity_movement(entity_id, parts[3])
            return

        if not is_self:
            return

        if action_id == "1" and len(parts) >= 4:
            path_data = parts[3].rstrip("\n").rstrip()
            if len(path_data) < 3:
                return
            last_wp = path_data[-3:]
            hi = ZIPKEY.find(last_wp[1])
            lo = ZIPKEY.find(last_wp[2])
            if hi < 0 or lo < 0:
                return
            dest_cell = hi * 64 + lo

            first_wp = path_data[:3]
            s_hi = ZIPKEY.find(first_wp[1])
            s_lo = ZIPKEY.find(first_wp[2])
            start_cell = s_hi * 64 + s_lo if s_hi >= 0 and s_lo >= 0 else -1

            cid = current.character.character_id
            prev_cell = current.entities[cid].cell_id if cid in current.entities else -1

            # Corriger la position : le serveur indique start_cell = position
            # réelle AVANT ce mouvement. Si ≠ notre croyance, on corrige.
            if start_cell >= 0 and start_cell != prev_cell and prev_cell >= 0:
                logger.info(
                    "[GameState] GA1 correction : serveur=%d, croyance=%d → corrigé",
                    start_cell, prev_cell,
                )
                if cid in current.entities:
                    current.entities[cid].cell_id = start_cell
                    bridge.set_entity({
                        "entity_id":  cid,
                        "name":       current.character.pseudo,
                        "level":      current.character.level,
                        "class_id":   current.character.class_id,
                        "class_name": CLASSES.get(current.character.class_id, ""),
                        "sex":        0,
                        "cell_id":    start_cell,
                        "is_monster": False,
                    })

            # Enregistrer la position réelle dans le cache d'arrivée
            # (1er GA1 après changement de map = position d'arrivée confirmée)
            if start_cell >= 0 and current._arrival_context is not None:
                old_map, exit_cell, new_map = current._arrival_context
                record_arrival(old_map, exit_cell, new_map, start_cell)
                current._arrival_context = None

            if cid in current.entities:
                current.entities[cid].cell_id = dest_cell
            else:
                current.entities[cid] = EntityInfo(
                    entity_id=cid,
                    name=current.character.pseudo,
                    level=current.character.level,
                    class_id=current.character.class_id,
                    cell_id=dest_cell,
                )
            logger.debug("[GameState] GA1 : %s start=%d → dest=%d (prev=%d)", entity_id, start_cell, dest_cell, prev_cell)

            bridge.set_entity({
                "entity_id":  cid,
                "name":       current.character.pseudo,
                "level":      current.character.level,
                "class_id":   current.character.class_id,
                "class_name": CLASSES.get(current.character.class_id, ""),
                "sex":        0,
                "cell_id":    dest_cell,
                "is_monster": False,
            })
        elif action_id == "501" and len(parts) >= 4:
            # Action récolte démarrée — notifier le bot pour éviter d'attendre
            # le timeout complet si la récolte a bien commencé
            data = parts[3].rstrip("\n").rstrip()
            cell_str = data.split(",")[0]
            if cell_str.lstrip("-").isdigit():
                harvested_cell = int(cell_str)
                logger.debug("[GameState] GA501 : %s récolte cell=%d", entity_id, harvested_cell)
                if entity_id == current.character.character_id:
                    bridge.notify_harvest_started(harvested_cell, entity_id)

    except Exception as exc:
        logger.debug("[GameState] GA parse error : %s", exc)


@on_server_message("GIC")
def _on_players_coordinates(msg: ParsedMessage) -> None:
    """GIC — GamePlayersCoordinates : positions exactes des joueurs de la carte.

    Envoyé par le serveur après que le client envoie Gp (GameSetPlayerPosition).
    Format payload : |{entity_id};{cell_id};{direction}|...
    Source de position la plus fiable pour les joueurs déjà sur la carte.
    """
    try:
        for part in msg.payload.split("|"):
            part = part.strip()
            if not part:
                continue
            fields = part.split(";")
            if len(fields) < 2:
                continue
            eid = fields[0]
            cell_str = fields[1]
            if not cell_str.lstrip("-").isdigit():
                continue
            cell_id = int(cell_str)
            if cell_id < 0:
                continue

            if eid in current.entities:
                current.entities[eid].cell_id = cell_id
            elif current.character and eid == current.character.character_id:
                entity = EntityInfo(
                    entity_id=eid,
                    name=current.character.pseudo,
                    level=current.character.level,
                    class_id=current.character.class_id,
                    cell_id=cell_id,
                )
                current.entities[eid] = entity
            else:
                continue

            logger.info("[GameState] GIC : %s → cell %d", eid, cell_id)
            if current.character and eid == current.character.character_id:
                bridge.set_entity({
                    "entity_id":  eid,
                    "name":       current.character.pseudo,
                    "level":      current.character.level,
                    "class_id":   current.character.class_id,
                    "class_name": CLASSES.get(current.character.class_id, ""),
                    "sex":        0,
                    "cell_id":    cell_id,
                    "is_monster": False,
                })
    except Exception as exc:
        logger.debug("[GameState] GIC parse error : %s", exc)


@on_server_message("IP")
def _on_travel_path(msg: ParsedMessage) -> None:
    """IP — InfosTravelPath : trajet calculé par le serveur (autopilote natif).

    Réponse au C→S ``IP{x},{y},{subAreaId}`` (Account.onMoveToPosition côté client).
    Format payload : ``{x};{y}|{x};{y}|...`` — liste de coordonnées de maps à
    traverser. La présence de ce message prouve que le serveur a ACCEPTÉ la
    demande de voyage auto (sinon il renvoie un Im d'erreur, pas d'IP).

    HARNAIS DE TEST : on log le trajet et on le pousse à l'UI (topic
    ``travel_path``) pour valider que l'autopilote natif est exploitable par
    injection. Cf. docs autopilote / mémoire project_autopilot_natif.
    """
    payload = msg.payload.strip()
    coords: list[tuple[str, str]] = []
    for part in payload.split("|"):
        part = part.strip()
        if not part or ";" not in part:
            continue
        x, _, y = part.partition(";")
        coords.append((x.strip(), y.strip()))

    logger.info("[GameState] IP (trajet serveur) : %d map(s) → %s", len(coords), coords[:12])
    if coords:
        pretty = " → ".join(f"({x},{y})" for x, y in coords)
        bridge.add_console(f"🧭 Autopilote : trajet serveur reçu ({len(coords)} maps) : {pretty}")
    else:
        # Payload vide = le serveur efface le trajet (arrivée atteinte ou annulation).
        bridge.add_console("🧭 Autopilote : trajet serveur vide (arrivée / annulation)")
    try:
        bridge.notify_travel({"coords": coords, "raw": payload})
    except Exception:
        pass


@on_client_message("BaM")
def _on_worldmap_click(msg: ParsedMessage) -> None:
    """BaM — double-clic sur la carte du monde (patch client : gate retiré).

    Format : ``{x},{y},{subAreaId}``. Le serveur ignore ce packet pour les
    non-admins (répond BN), mais on l'utilise comme DÉCLENCHEUR de NOTRE
    autopilote : on extrait (x,y) et on lance bot.autopilot.travel_to.
    Cf. mémoire project_autopilot_natif (intégration in-game).
    """
    try:
        parts = msg.payload.split(",")
        x, y = int(parts[0]), int(parts[1])
    except (ValueError, IndexError):
        logger.debug("[GameState] BaM payload inattendu : %r", msg.payload)
        return

    from bot import autopilot
    if autopilot.is_running():
        bridge.add_console("🧭 Autopilote déjà en cours — clic ignoré")
        return

    bridge.add_console(f"🧭 Autopilote (clic carte) : destination ({x},{y})")
    logger.info("[GameState] BaM worldmap-click → autopilot.travel_to(%d,%d)", x, y)

    from core.session import active_or_none, launch_in_session
    s = active_or_none()
    if s is not None:
        launch_in_session(s, lambda: autopilot.travel_to(x, y))


@on_server_message("GTM")
def _on_turn_middle(msg: ParsedMessage) -> None:
    """GTM — GameTurnMiddle : état de toutes les entités au début d'un tour de combat.

    Format payload : |{id};?;hp;PA;PM;cell;;maxhp|...
    Tracker toutes les entités (position en combat) et notre perso en particulier
    pour que _pre_gdm_char_cell soit à jour quand le combat se termine.
    """
    if current.character is None:
        return
    cid = current.character.character_id
    try:
        for part in msg.payload.split("|"):
            part = part.strip()
            if not part:
                continue
            fields = part.split(";")
            if len(fields) < 6:
                continue
            eid = fields[0]
            cell_str = fields[5]
            if not cell_str.lstrip("-").isdigit():
                continue
            cell_id = int(cell_str)
            if cell_id < 0:
                continue

            # Tracker la position de toutes les entités en combat
            current.combat_entity_cells[eid] = cell_id

            # Tracker les HP (format: id;?;hp;PA;PM;cell;;maxhp)
            if len(fields) >= 3 and fields[2].lstrip("-").isdigit():
                current.combat_entity_hp[eid] = int(fields[2])

            # Tracker HP max (field index 7) — utile pour focus les monstres blessés
            if len(fields) >= 8 and fields[7].lstrip("-").isdigit():
                max_hp = int(fields[7])
                if max_hp > 0:
                    current.combat_entity_max_hp[eid] = max_hp

            # Tracker les PA (field index 3)
            if len(fields) >= 4 and fields[3].lstrip("-").isdigit():
                current.combat_entity_pa[eid] = int(fields[3])

            # Tracker les PM (field index 4) + high-watermark pour la menace réelle
            if len(fields) >= 5 and fields[4].lstrip("-").isdigit():
                pm = int(fields[4])
                current.combat_entity_pm[eid] = pm
                if pm > current.combat_entity_max_pm.get(eid, 0):
                    current.combat_entity_max_pm[eid] = pm

            # Pour notre perso : mettre à jour la position dans entities aussi
            if eid == cid:
                if cid in current.entities:
                    current.entities[cid].cell_id = cell_id
                logger.debug("[GameState] GTM : %s → cell %d (en combat)", cid, cell_id)
    except Exception as exc:
        logger.debug("[GameState] GTM parse error : %s", exc)


# ---------------------------------------------------------------------------
# Handlers — combat
# ---------------------------------------------------------------------------
# Protocole réel (d'après analyse des logs) :
#
#   GJ      S→C  Phase de placement démarrée → bot envoie GR1 SI une automatisation est active
#   GS      S→C  Combat officiellement lancé (après que tous aient envoyé GR)
#   GTL     S→C  Ordre des tours (info seulement)
#   GTM     S→C  État de toutes les entités au début d'un tour
#   GTS id  S→C  Tour de l'entité id — si notre id : _combat_turn_event
#   GTF id  S→C  Fin du tour de l'entité id (info seulement)
#   GTR id  S→C  Serveur demande GT en retour pour valider le passage au tour suivant
#   C→S GT       Envoyé par le bot en réponse à GTR (obligatoire sinon le tour ne passe pas)
#   GE      S→C  Fin du combat → in_combat=False
# ---------------------------------------------------------------------------

@on_server_message("GTL")
def _on_turn_list(msg: ParsedMessage) -> None:
    """GTL — GameTurnList : ordre des tours au début du combat.

    Format payload : |{id1}|{id2}|...
    IDs négatifs = monstres. On initialise combat_live_monsters.
    """
    try:
        current.combat_live_monsters = {
            part.strip()
            for part in msg.payload.split("|")
            if part.strip().lstrip("-").isdigit() and int(part.strip()) < 0
        }
        # Mémoriser les monstres d'origine au 1er GTL du combat (les invocations,
        # ajoutées ensuite via GA;999;…;GTL|…, n'y figureront jamais). Le set est
        # vidé au GJ : on ne le peuple donc qu'une fois, au début du combat.
        if not current.combat_initial_monsters:
            current.combat_initial_monsters = set(current.combat_live_monsters)
        logger.info("[GameState] GTL : %d monstres vivants", len(current.combat_live_monsters))
    except Exception as exc:
        logger.debug("[GameState] GTL parse error : %s", exc)


def _is_automation_running() -> bool:
    """True si une automatisation est active (bot combat, récolte ou script).

    Sert à décider si le bot doit envoyer GR1 (« prêt ») automatiquement en
    phase de placement. Hors automatisation, on laisse le joueur placer ses
    personnages manuellement. Mirroir de la détection « Bot actif » de la
    status bar (dashboard/statusbar.py).
    """
    try:
        from bot.combat import is_running as _combat_running
        if _combat_running():
            return True
    except Exception:
        pass
    try:
        from bot.harvester import is_running as _harv_running
        if _harv_running():
            return True
    except Exception:
        pass
    try:
        from bot.script_engine import is_running as _scr_running
        if _scr_running():
            return True
    except Exception:
        pass
    return False


@on_server_message("GJ")
def _on_game_join(msg: ParsedMessage) -> None:
    """GJ — phase de placement du combat démarrée.

    Le bot envoie GR1 (« prêt », pas de repositionnement) UNIQUEMENT si une
    automatisation est active (bot combat / récolte / script). En combat
    manuel, on laisse la phase de préparation ouverte pour que le joueur
    place ses personnages lui-même.
    On marque in_combat=True dès maintenant pour bloquer le harvester.
    """
    from bot import channel as _channel
    current.in_combat = True
    current.combat_entity_cells.clear()
    current.combat_entity_hp.clear()
    current.combat_entity_max_hp.clear()
    current.combat_entity_max_pm.clear()
    current.combat_entity_pa.clear()
    current.combat_entity_pm.clear()
    current.combat_entity_po.clear()
    current.combat_live_monsters.clear()
    current.combat_initial_monsters.clear()
    current._combat_turn_event.clear()
    current._combat_ended_event.clear()
    current._companion_turn_event.clear()
    current._combat_started_event.clear()
    current._gtf_event.clear()
    current._spell_result_event.clear()
    current._move_rejected_event.clear()
    current._pending_actions = 0
    current._pending_actions_clear.set()
    # Nouveau combat : on repart à pleine vitesse tant qu'aucun spectateur ne rejoint
    # (le flag est ré-armé par Im 036 si quelqu'un nous observe — cf. _on_im_message).
    current._spectator_present = False
    # Vider la queue de tours (résidus d'un combat précédent)
    while not current._combat_turn_queue.empty():
        try:
            current._combat_turn_queue.get_nowait()
        except asyncio.QueueEmpty:
            break
    current._companion_turn_id = ""

    # Le GR1 automatique (« prêt ») ne doit partir QUE si une automatisation
    # est active (bot combat, récolte ou script). Sinon on laisse la main au
    # joueur pour qu'il puisse placer ses personnages pendant la préparation.
    if not _is_automation_running():
        logger.info("[GameState] GJ : placement — aucune automatisation active, GR1 non envoyé")
        bridge.add_console("⚔ Combat : placement (manuel — bot inactif, GR1 non envoyé)")
        return

    logger.info("[GameState] GJ : phase de placement — envoi GR1")
    bridge.add_console("⚔ Combat : placement en cours…")

    # Délai optionnel avant « prêt » (anti-suspicion) : si activé, on temporise un délai
    # aléatoire avant d'envoyer GR1 pour ne pas confirmer le placement à la milliseconde.
    # En mode discrétion « humain », le délai s'applique TOUJOURS (un joueur ne confirme
    # jamais son placement à la milliseconde), même si l'option n'est pas cochée.
    ready_delay = 0.0
    if current.combat_delay_before_ready:
        lo = max(0, current.combat_ready_delay_min_ms)
        hi = max(lo, current.combat_ready_delay_max_ms)
        ready_delay = random.uniform(lo, hi) / 1000.0
    elif current.combat_discretion_mode == "human":
        ready_delay = random.uniform(0.9, 3.0)

    async def _send_ready() -> None:
        if ready_delay > 0:
            await asyncio.sleep(ready_delay)
        await _channel.send("GR1\n")

    # Envoyer GR1 de manière asynchrone (pas de repositionnement)
    try:
        loop = asyncio.get_event_loop()
        loop.create_task(_send_ready())
    except RuntimeError:
        pass  # Pas de loop en cours (ne devrait pas arriver)


@on_server_message("GS")
def _on_combat_start(msg: ParsedMessage) -> None:
    """GS — combat officiellement lancé (tous les joueurs ont confirmé GR).

    in_combat est déjà True depuis GJ, on log juste la confirmation.
    """
    current.in_combat = True  # sécurité si GJ manqué
    current._combat_started_event.set()
    logger.info("[GameState] GS : combat commencé !")
    bridge.add_console("⚔ Combat commencé !")


@on_server_message("GTF")
def _on_turn_finish(msg: ParsedMessage) -> None:
    """GTF — fin du tour d'un combattant. Signal pour confirmer le Gt passif."""
    current._gtf_event.set()


@on_server_message("GTR")
def _on_turn_ready(msg: ParsedMessage) -> None:
    """GTR — le serveur demande GT pour valider le passage au prochain tour.

    Format payload : {entity_id}
    OBLIGATOIRE : sans GT en réponse, le combat se bloque (le tour ne passe jamais).
    On envoie GT pour TOUT GTR reçu (que ce soit notre tour ou celui de l'ennemi).
    """
    from bot import channel as _channel
    try:
        loop = asyncio.get_event_loop()
        loop.create_task(_channel.send("GT\n"))
        logger.debug("[GameState] GTR %r → GT envoyé", msg.payload.strip())
    except RuntimeError:
        pass


@on_server_message("GTS")
def _on_combat_turn_start(msg: ParsedMessage) -> None:
    """GTS — début du tour d'un combattant.

    Deux formats :
      - Réel     : "{entity_id}|{time_ms}|{flag}"   → tour de jeu effectif
      - Info héros : "X{main_id};{hero_id};..."      → mise à jour spells/cooldowns (ignorer)

    Logique :
      - Tout allié (perso principal ou héros actif) → _combat_turn_queue
      - Héros passifs (si définis)                  → Gt envoyé automatiquement
      - Monstres (ID négatif)                       → ignoré
    """
    if current.character is None:
        return
    try:
        payload = msg.payload.strip()
        if "|" not in payload or payload.startswith("X"):
            logger.debug("[GameState] GTS info-héros ignoré : %r", payload[:40])
            return

        parts = payload.split("|")
        turn_id = parts[0].strip()

        if turn_id.startswith("-"):
            logger.debug("[GameState] GTS : tour du monstre %s (ignoré)", turn_id)
            return

        # Mémoriser la durée de tour annoncée (borne les pauses anti-détection).
        if len(parts) >= 2:
            try:
                t = int(parts[1].strip())
                if t > 0:
                    current.combat_turn_time_ms = t
            except ValueError:
                pass

        # Héros passifs : passer le tour automatiquement
        if turn_id in PASSIVE_COMBAT_HEROES:
            from bot import channel as _channel

            async def _pass_passive(tid: str = turn_id) -> None:
                for attempt in range(3):
                    current._gtf_event.clear()
                    logger.debug("[GameState] GTS : %s (passif) → Gt auto (%d/3)", tid, attempt + 1)
                    ok = await _channel.send("Gt\n")
                    if not ok or not current.in_combat:
                        return
                    lp = asyncio.get_event_loop()
                    gtf_task = lp.create_task(current._gtf_event.wait())
                    end_task = lp.create_task(current._combat_ended_event.wait())
                    try:
                        done, pending = await asyncio.wait(
                            {gtf_task, end_task}, timeout=3.0,
                            return_when=asyncio.FIRST_COMPLETED,
                        )
                    except asyncio.CancelledError:
                        gtf_task.cancel()
                        end_task.cancel()
                        raise
                    for t in pending:
                        t.cancel()
                    if pending:
                        await asyncio.gather(*pending, return_exceptions=True)
                    if not current.in_combat or end_task in done:
                        return
                    if gtf_task in done and not gtf_task.cancelled():
                        return
                logger.error("[GameState] GTS %s — Gt ignoré 3 fois", tid)

            try:
                asyncio.get_event_loop().create_task(_pass_passive())
            except RuntimeError:
                pass
            return

        # Allié actif (perso principal ou héros) → dans la queue de tours
        current._combat_turn_queue.put_nowait(turn_id)

        # Rétro-compatibilité : signaler aussi les anciens events
        if turn_id == current.character.character_id:
            current._combat_turn_event.set()
            logger.info("[GameState] GTS : tour de %s (principal)", turn_id)
        else:
            current._companion_turn_id = turn_id
            current._companion_turn_event.set()
            logger.info("[GameState] GTS : tour de %s (héros)", turn_id)

    except Exception as exc:
        logger.debug("[GameState] GTS parse error : %s", exc)


@on_server_message("GE")
def _on_combat_end(msg: ParsedMessage) -> None:
    """GE — fin du combat (victoire, défaite ou fuite).

    Réinitialise l'état in_combat et signale la fin pour débloquer fight_simple().
    """
    current.in_combat = False
    current._combat_ended_event.set()
    bridge.increment_fight_count()
    logger.info("[GameState] GE : combat terminé — payload=%r", msg.payload[:60])
    bridge.add_console("✓ Combat terminé")
    bridge.notify("Combat terminé", level="success")


# ---------------------------------------------------------------------------
# Handler C→S GA001 — tracking de position du joueur
# ---------------------------------------------------------------------------

@on_client_message("GA500")
def _on_client_harvest(msg: ParsedMessage) -> None:
    """GA500 C→S — récolte manuelle. On déduit la position approx. du joueur.

    Quand le joueur récolte manuellement, il est forcément adjacent à la
    ressource. Si sa position est inconnue (cell_id=-1), on pose une cellule
    adjacente libre comme approximation.
    """
    if current.character is None:
        return
    cid = current.character.character_id
    entity = current.entities.get(cid)
    if entity is None or entity.cell_id >= 0:
        return  # position déjà connue

    try:
        # Format : GA500{cell_id};{elem_type}
        parts = msg.payload.split(";")
        if not parts[0].lstrip("-").isdigit():
            return
        resource_cell = int(parts[0])

        from bot.pathfinding import adjacent_cells, MAP_WIDTH
        from bot.mapdata import load_map as _load_map

        width = MAP_WIDTH
        blocked: set[int] = set()
        if current.current_map:
            info = _load_map(current.current_map.map_id)
            if info:
                width = info.width
                blocked = info.blocked_cells

        for c in adjacent_cells(resource_cell, width):
            if c >= 0 and c not in blocked and c != resource_cell:
                entity.cell_id = c
                logger.info(
                    "[GameState] GA500 (manuel) : position approx. cell=%d "
                    "(adjacent à resource %d)", c, resource_cell,
                )
                bridge.set_entity({
                    "entity_id":  cid,
                    "name":       current.character.pseudo,
                    "level":      current.character.level,
                    "class_id":   current.character.class_id,
                    "class_name": CLASSES.get(current.character.class_id, ""),
                    "sex":        0,
                    "cell_id":    c,
                    "is_monster": False,
                })
                break
    except Exception as exc:
        logger.debug("[GameState] GA500 C→S parse error : %s", exc)


@on_client_message("GA001")
def _on_client_move(msg: ParsedMessage) -> None:
    """GA001 C→S — le joueur (ou le bot) envoie un déplacement.

    On NE met PAS à jour entity.cell_id ici : seule la confirmation
    serveur (GA;1 S→C) fait foi, car elle contient le start_cell réel
    du serveur.  Mettre à jour ici écrasait la position avant que GA
    puisse comparer start_cell vs notre croyance.
    """
    try:
        payload = msg.payload.rstrip("\n").rstrip()
        if len(payload) < 3:
            return
        last_wp = payload[-3:]
        hi = ZIPKEY.find(last_wp[1])
        lo = ZIPKEY.find(last_wp[2])
        if hi < 0 or lo < 0:
            return
        dest_cell = hi * 64 + lo
        logger.debug("[GameState] GA001 → dest_cell=%d (position inchangée)", dest_cell)
    except Exception as exc:
        logger.debug("[GameState] GA001 parse error : %s", exc)


@on_client_message("Gp")
def _on_client_set_position(msg: ParsedMessage) -> None:
    """Gp C→S — GameSetPlayerPosition : le client envoie sa position exacte.

    Envoyé par le client Flash après un changement de map pour signaler au
    serveur la cellule d'arrivée. C'est la source la plus fiable de position.
    Format payload : {cell_id}  (entier décimal, éventuellement suivi de \\n)
    """
    if current.character is None:
        return
    try:
        cell_id_str = msg.payload.strip()
        if not cell_id_str.lstrip("-").isdigit():
            return
        cell_id = int(cell_id_str)
        cid = current.character.character_id
        if cid in current.entities:
            current.entities[cid].cell_id = cell_id
        else:
            entity = EntityInfo(
                entity_id=cid,
                name=current.character.pseudo,
                level=current.character.level,
                class_id=current.character.class_id,
                cell_id=cell_id,
            )
            current.entities[cid] = entity
        logger.info("[GameState] Gp : position exacte cell=%d", cell_id)
        bridge.set_entity({
            "entity_id":  cid,
            "name":       current.character.pseudo,
            "level":      current.character.level,
            "class_id":   current.character.class_id,
            "class_name": CLASSES.get(current.character.class_id, ""),
            "sex":        0,
            "cell_id":    cell_id,
            "is_monster": False,
        })
    except Exception as exc:
        logger.debug("[GameState] Gp parse error : %s", exc)


@on_server_message("GAF")
def _on_gaf(msg: ParsedMessage) -> None:
    """GAF S→C — le serveur signale la fin d'un groupe d'actions.

    Le Flash client doit répondre par GKK pour chaque GAF.
    Tant que des GAF ne sont pas ack, le serveur refuse Gt.
    """
    current._pending_actions += 1
    current._pending_actions_clear.clear()
    logger.debug("[GameState] GAF reçu → %d action(s) en attente d'ack", current._pending_actions)

    # Déblocage de wait_spell_result : le GAF du LANCEUR signale que le serveur a fini
    # de traiter SON cast (PA déjà décomptés par le GA;102 qui précède le GAF), ET il
    # vient d'incrémenter _pending_actions → end_turn attendra bien l'ack avant le Gt.
    # Conditions cumulées :
    #  - entité = lanceur attendu (payload "action_id|entity_id"),
    #  - _spell_cast_confirmed : le GA;300 du cast COURANT a déjà été vu → ce GAF est
    #    bien celui du cast (et non un GAF périmé d'une action antérieure qui, arrivant
    #    avant le GA;102, ferait relire les PA trop tôt → "1 cast au lieu de 2").
    if current._spell_result_caster and current._spell_cast_confirmed:
        parts = msg.payload.split("|")
        if len(parts) >= 2 and parts[1].strip() == current._spell_result_caster:
            current._spell_result_event.set()


@on_client_message("GKK")
def _on_gkk(msg: ParsedMessage) -> None:
    """GKK C→S — le Flash client confirme qu'une animation est terminée.

    Décrémente le compteur d'actions en attente et signale quand tout est clear.
    """
    current._gkk_event.set()
    if current._pending_actions > 0:
        current._pending_actions -= 1
    if current._pending_actions <= 0:
        current._pending_actions = 0
        current._pending_actions_clear.set()
    logger.debug("[GameState] GKK reçu → %d action(s) restante(s)", current._pending_actions)


# ---------------------------------------------------------------------------
# Handlers — entités sur la carte
# ---------------------------------------------------------------------------

@on_server_message("hP")
def _on_here_player(msg: ParsedMessage) -> None:
    """hP — joueur/marchand déjà présent sur la carte.

    Toujours envoyé AVANT GDM lors d'un changement de carte. À ce moment,
    ``_map_loaded`` est encore True (de la carte précédente), donc si on
    ajoutait directement à ``current.entities``, GDM les effacerait.

    On bufferise systématiquement dans ``_pending_hp`` ; le handler GDK
    les applique après le clear.

    Format payload : {cell_id}|{name};{sex};{guilde};{accessories}|
    """
    try:
        parts = msg.payload.split("|")
        if len(parts) < 2:
            return
        cell_id_str = parts[0].strip()
        cell_id = int(cell_id_str) if cell_id_str.lstrip("-").isdigit() else -1
        player_fields = parts[1].split(";")
        name = player_fields[0].strip() if player_fields else "?"
        sex = int(player_fields[1]) if len(player_fields) > 1 and player_fields[1].strip().isdigit() else 0
        guild_name = player_fields[2].strip() if len(player_fields) > 2 else ""
        if not name:
            return

        eid = f"hp_{name}"
        entity = EntityInfo(
            entity_id=eid, name=name, cell_id=cell_id,
            sex=sex, guild_name=guild_name,
        )
        current._pending_hp.append(entity)
        logger.debug("[GameState] hP (buffered) : %s cell=%d guild=%s", name, cell_id, guild_name or "-")
    except Exception as exc:
        logger.warning("[GameState] hP parse error : %s | payload=%r", exc, msg.payload[:60])


# IDs des entités à ignorer (héros du joueur — marqués par Nx juste après leur NL)

# Mapping empreinte XP (field[0] du message As) → character_id.
# Construit pré-GDK en corrélant chaque As avec le Ow qui le suit immédiatement
# (le Ow contient le character_id explicitement).
# Permet de router les As post-GDK vers le bon personnage.

# Empreinte stable (kamas, max_life) → character_id.
# Fallback quand _xp_to_char est périmé (XP gagnée entre deux combats).
# max_life et kamas ne changent pas pendant une session de farm → très stables.

# Cache HP des héros (character_id → (hp, max_hp)) mis à jour par les As héros


@on_server_message("Nx")
def _on_nx(msg: ParsedMessage) -> None:
    """Nx — marque une entité comme héros/compagnon (envoyé juste après NL pour les héros).

    On l'ajoute à _companion_ids pour l'exclure des entités "joueurs" et
    on la retire de current.entities si elle y était déjà.
    On sauvegarde ses infos (nom, niveau, classe) dans le bridge avant suppression.
    """
    eid = msg.payload.strip()
    if eid:
        current._companion_ids.add(eid)
        if eid in current.entities:
            entity = current.entities[eid]
            # Mémoriser la classe du héros (équipe mixte) : combat.py l'utilise pour
            # choisir la séquence de sorts de sa classe ; l'UI pour ses sous-onglets.
            if entity.class_id:
                current.entity_classes[eid] = entity.class_id
                _push_team_classes()
            bridge.update_hero(eid, {
                "hero_id":    eid,
                "name":       entity.name,
                "pseudo":     entity.name,   # alias pour l'UI (même clé que le perso principal)
                "level":      entity.level,
                "class_id":   entity.class_id,
                "class_name": entity.class_name,
            })
            del current.entities[eid]
            logger.debug("[GameState] Nx : %s retiré des entités (héros)", eid)


@on_server_message("Nh")
def _on_hero_spells(msg: ParsedMessage) -> None:
    """Nh — liste des sorts d'un héros (HeroSpells), envoyée à la connexion.

    Format payload : {hero_id}|{spell_id}~{spell_level}~{position};...
    On route les sorts vers la classe du héros (connue via Nx) pour peupler les
    sous-onglets Sorts par classe et résoudre le niveau des sorts en combat.
    """
    try:
        parts = msg.payload.split("|", 1)
        if len(parts) < 2:
            return
        hero_id = parts[0].strip()
        spells: list[SpellEntry] = []
        for chunk in parts[1].strip().rstrip(";").split(";"):
            chunk = chunk.strip()
            if not chunk:
                continue
            fields = chunk.split("~")
            if len(fields) < 3:
                continue
            try:
                spells.append(SpellEntry(
                    spell_id=int(fields[0]),
                    spell_level=int(fields[1]),
                    position=int(fields[2]) if fields[2].lstrip("-").isdigit() else -1,
                ))
            except ValueError:
                continue

        class_id = current.entity_classes.get(hero_id)
        if class_id is None or not spells:
            logger.debug("[GameState] Nh : héros %s classe inconnue ou sans sort (ignoré)", hero_id)
            return

        current.class_spells[class_id] = spells
        logger.debug("[GameState] Nh : %d sorts pour classe %s (héros %s)",
                     len(spells), CLASSES.get(class_id, class_id), hero_id)
        _push_team_classes()
    except Exception as exc:
        logger.warning("[GameState] Nh parse error : %s | payload=%r", exc, msg.payload[:80])


@on_server_message("NL")
def _on_entity_create(msg: ParsedMessage) -> None:
    """NL S→C avec préfixe K+ — entité (joueur) apparaît sur la carte."""
    if not msg.payload.startswith("K+"):
        logger.debug("[GameState] NL ignoré (préfixe inconnu) : %r", msg.payload[:60])
        return
    try:
        entity = EntityInfo.parse_server(msg.payload)
        if entity is None:
            return
        # Les héros sont signalés par Nx juste après → on les ignore
        if entity.entity_id in current._companion_ids:
            logger.debug("[GameState] NL ignoré (héros connu) : %s", entity.entity_id)
            return
        current.entities[entity.entity_id] = entity
        logger.debug("[GameState] Entité : %s Lv.%d (cell=%d)", entity.name, entity.level, entity.cell_id)
        bridge.set_entity({
            "entity_id":  entity.entity_id,
            "name":       entity.name,
            "level":      entity.level,
            "class_id":   entity.class_id,
            "class_name": entity.class_name,
            "sex":        entity.sex,
            "cell_id":    entity.cell_id,
            "is_monster": False,
        })
        bridge.add_console(f"{entity.name} Lv.{entity.level} ({entity.class_name}) visible — cell #{entity.cell_id}")
    except Exception as exc:
        logger.warning("[GameState] NL parse error : %s | payload=%r", exc, msg.payload[:60])


# ---------------------------------------------------------------------------
# État inventaire et métiers (module-level, mis à jour par les handlers)
# ---------------------------------------------------------------------------



def _inventory_to_list() -> list[dict]:
    return list(current._inventory.values())


def _jobs_to_list() -> list[dict]:
    return list(current._jobs.values())


def get_job_level(job_id: int) -> int:
    """Retourner le niveau d'un métier (1 si inconnu)."""
    return current._jobs.get(job_id, {}).get("level", 1)


# ---------------------------------------------------------------------------
# Handlers — inventaire
# ---------------------------------------------------------------------------

@on_server_message("OT")
def _on_inventory_full(msg: ParsedMessage) -> None:
    """OT — contenu complet de l'inventaire (envoyé au chargement de la map)."""
    try:
        inv = Inventory.parse(msg.payload)
        current._inventory = {
            item.obj_uid: {
                "uid":  item.obj_uid,
                "gid":  item.obj_gid,
                "qty":  item.quantity,
                "pos":  item.position,
                "fx":   item.effects,
            }
            for item in inv.items
            if item.obj_uid  # ignorer les entrées sans UID
        }
        bridge.update_inventory(_inventory_to_list())
        bridge.add_console(f"Inventaire : {len(current._inventory)} objets")
        logger.info("[GameState] OT : %d objets", len(current._inventory))
    except Exception as exc:
        logger.warning("[GameState] OT parse error : %s | payload=%r", exc, msg.payload[:80])


@on_server_message("OAK")
def _on_item_add(msg: ParsedMessage) -> None:
    """OAK — ajout d'un ou plusieurs objets dans l'inventaire."""
    try:
        items = parse_item_add(msg.payload)
        for item in items:
            if item.obj_uid:
                current._inventory[item.obj_uid] = {
                    "uid": item.obj_uid,
                    "gid": item.obj_gid,
                    "qty": item.quantity,
                    "pos": item.position,
                    "fx":  item.effects,
                }
        if items:
            bridge.update_inventory(_inventory_to_list())
            logger.debug("[GameState] OAK : +%d objet(s)", len(items))
    except Exception as exc:
        logger.warning("[GameState] OAK parse error : %s", exc)


@on_server_message("OR")
def _on_item_remove(msg: ParsedMessage) -> None:
    """OR — suppression d'un ou plusieurs objets de l'inventaire.

    Deux formats :
      - Simple (drop/vente) : "{uid}"
      - Banque (dépôt)      : "{charId}|{uid1}*{uid2}*..."
    """
    try:
        payload = msg.payload.strip()
        if "|" in payload:
            # Format banque : charId|uid1*uid2*...
            _, uids_part = payload.split("|", 1)
            uids = [u.strip() for u in uids_part.split("*") if u.strip()]
        else:
            uids = [parse_item_remove(payload)]

        removed = 0
        for uid in uids:
            if uid in current._inventory:
                del current._inventory[uid]
                removed += 1
        if removed:
            bridge.update_inventory(_inventory_to_list())
            logger.debug("[GameState] OR : supprimé %d objet(s)", removed)
    except Exception as exc:
        logger.warning("[GameState] OR parse error : %s", exc)


@on_server_message("OQ")
def _on_item_quantity(msg: ParsedMessage) -> None:
    """OQ — changement de quantité d'un objet.

    Si l'objet est inconnu (non tracké via OAK), on l'ajoute en pos=63 (sac).
    Cela couvre les items déjà en sac depuis une session précédente dont la
    quantité change (dépôt/retrait banque, stack loot).
    """
    try:
        pairs = parse_item_quantity(msg.payload)
        if not pairs:
            return
        for uid, qty in pairs:
            if not uid:
                continue
            if uid in current._inventory:
                current._inventory[uid]["qty"] = qty
            else:
                current._inventory[uid] = {"uid": uid, "gid": None, "qty": qty, "pos": 63, "fx": ""}
                logger.debug("[GameState] OQ : item inconnu %s ajouté (qty=%d, pos=63 supposé)", uid, qty)
            logger.debug("[GameState] OQ : %s → qty=%d", uid, qty)
        bridge.update_inventory(_inventory_to_list())
    except Exception as exc:
        logger.warning("[GameState] OQ parse error : %s", exc)


@on_server_message("IQ")
def _on_info_quantity(msg: ParsedMessage) -> None:
    """IQ — InfosQuantity : quantité de ressources récoltées.

    Format : {charId}|{quantity}
    Envoyé après une récolte réussie. Corrélé avec le dernier GDF reçu.
    """
    try:
        parts = msg.payload.split("|", 1)
        if len(parts) < 2:
            return
        char_id = parts[0].strip()
        qty_str = parts[1].strip()
        qty = int(qty_str) if qty_str.isdigit() else 0

        # Ignorer si pas notre perso
        if current.character and char_id != str(current.character.character_id):
            return

        # Elem récemment passé en cooldown (mis à jour par GDF handler)
        harvested_cell = current._last_harvested_cell
        last_elem = current.frame_objects.elements.get(harvested_cell) if harvested_cell >= 0 else None

        map_id = current.current_map.map_id if current.current_map else "?"
        elem_info = f"elem#{last_elem.elem_id} type{last_elem.elem_type}" if last_elem else f"elem#{harvested_cell}"

        entry = {
            "qty":     qty,
            "elem":    elem_info,
            "map_id":  map_id,
        }
        bridge.add_harvest(entry)
        bridge.add_console(f"Récolte : +{qty} ({elem_info} sur map #{map_id})")
        logger.info("[GameState] IQ : +%d %s", qty, elem_info)

        # Inférer la position du joueur si inconnue (adjacent à la cellule récoltée)
        if harvested_cell >= 0 and current.character:
            cid = current.character.character_id
            entity = current.entities.get(cid)
            if entity is None or entity.cell_id < 0:
                from bot.pathfinding import adjacent_cells, MAP_WIDTH
                width = MAP_WIDTH
                blocked: set[int] = set()
                if current.current_map:
                    info = load_map(current.current_map.map_id)
                    if info:
                        width = info.width
                        blocked = info.blocked_cells
                resource_cells = {e.elem_id for e in current.frame_objects.elements.values()}
                for adj in adjacent_cells(harvested_cell, width):
                    if adj >= 0 and adj not in blocked and adj not in resource_cells:
                        if entity is None:
                            from protocol.messages.stats import EntityInfo, CLASSES
                            entity = EntityInfo(
                                entity_id=cid,
                                name=current.character.pseudo,
                                level=current.character.level,
                                class_id=current.character.class_id,
                                cell_id=adj,
                            )
                            current.entities[cid] = entity
                        else:
                            entity.cell_id = adj
                        logger.info(
                            "[GameState] IQ : position inférée cell=%d (adjacent à récolte %d)",
                            adj, harvested_cell,
                        )
                        bridge.set_entity({
                            "entity_id":  cid,
                            "name":       current.character.pseudo,
                            "level":      current.character.level,
                            "class_id":   current.character.class_id,
                            "class_name": CLASSES.get(current.character.class_id, ""),
                            "sex":        0,
                            "cell_id":    adj,
                            "is_monster": False,
                        })
                        break
    except Exception as exc:
        logger.warning("[GameState] IQ parse error : %s", exc)


# ---------------------------------------------------------------------------
# Handlers — poids et banque
# ---------------------------------------------------------------------------

@on_server_message("Ow")
def _on_items_weight(msg: ParsedMessage) -> None:
    """Ow — ItemsWeight : poids courant de l'inventaire.

    Format : "{charId};{currentWeight}|{maxWeight}"
    """
    # Corrélation XP-key → char_id pré-GDK : le Ow suit immédiatement le As du héros.
    if not current._map_loaded and current._pending_xp_key is not None:
        payload_stripped = msg.payload.strip()
        if ";" in payload_stripped:
            ow_char_id = payload_stripped.split(";", 1)[0].strip()
            if ow_char_id and (current.character is None or ow_char_id != current.character.character_id):
                current._xp_to_char[current._pending_xp_key] = ow_char_id
                # Empreinte stable (kamas, max_life) → char_id (fallback quand l'XP change)
                if current._pending_stats is not None:
                    current._hero_fingerprint[(current._pending_stats.kamas, current._pending_stats.max_life)] = ow_char_id
                logger.debug("[GameState] Ow corrélation : %s → %s (kamas=%d max_life=%d)",
                             current._pending_xp_key[:20], ow_char_id,
                             current._pending_stats.kamas if current._pending_stats else -1,
                             current._pending_stats.max_life if current._pending_stats else -1)
        current._pending_xp_key = None

    if current.character is None:
        return
    try:
        payload = msg.payload.strip()
        if ";" in payload:
            char_id, weight_part = payload.split(";", 1)
            if char_id.strip() != current.character.character_id:
                return
        else:
            weight_part = payload
        if "|" in weight_part:
            curr_str, max_str = weight_part.split("|", 1)
            current.weight_current = int(curr_str.strip())
            current.weight_max = int(max_str.strip())
            current._weight_event.set()
            logger.info("[GameState] Ow : %d/%d pods", current.weight_current, current.weight_max)
            bridge.update_weight(current.weight_current, current.weight_max)
    except Exception as exc:
        logger.warning("[GameState] Ow parse error : %s", exc)


@on_server_message("ECK")
def _on_exchange_create(msg: ParsedMessage) -> None:
    """ECK — ExchangeCreateSuccess : échange/banque ouvert.

    Type 5 = banque. On set _bank_open_event pour débloquer wait_bank_open().
    """
    try:
        exchange_type = msg.payload.strip().split("|")[0].strip()
        if exchange_type == "5":
            current._bank_is_open = True
            current._bank_open_event.set()
            bridge.increment_bank_count()
            logger.info("[GameState] ECK 5 : banque ouverte")
            bridge.notify("Banque ouverte", level="info")
        else:
            # Échange PNJ (marchand : type 2). Débloque wait_exchange_open().
            current._exchange_is_open = True
            current._exchange_kamas = 0
            current._exchange_closed_event.clear()
            current._exchange_open_event.set()
            logger.info("[GameState] ECK %s : échange PNJ ouvert", exchange_type)
    except Exception as exc:
        logger.warning("[GameState] ECK parse error : %s", exc)


@on_server_message("EV")
def _on_exchange_leave(msg: ParsedMessage) -> None:
    """EV S→C — ExchangeLeave : échange/banque fermé."""
    if current._bank_is_open:
        current._bank_is_open = False
        current._bank_open_event.clear()
        logger.info("[GameState] EV : banque fermée")
    if current._exchange_is_open:
        current._exchange_is_open = False
        current._exchange_open_event.clear()
        current._exchange_closed_event.set()
        logger.info("[GameState] EV : échange PNJ fermé")


@on_server_message("EL")
def _on_exchange_list(msg: ParsedMessage) -> None:
    """EL — ExchangeList : contenu d'un coffre/banque à l'ouverture.

    Sur ce serveur privé, l'inventaire complet (OT) n'est jamais poussé : la
    banque est la seule source lisible de la liste d'objets. Format :
        EL{item1};{item2};...   chaque item = O{uid_hex}~{gid_hex}~{qty}~{pos}~{fx}
    """
    try:
        from protocol.messages.inventory import InventoryItem
        items: dict = {}
        pos_dist: dict = {}
        for chunk in msg.payload.split(";"):
            chunk = chunk.strip()
            if "~" not in chunk:
                continue
            try:
                it = InventoryItem.parse_obj(chunk)
            except Exception:
                continue
            if it.obj_uid:
                items[it.obj_uid] = {
                    "uid": it.obj_uid, "gid": it.obj_gid, "qty": it.quantity,
                    "pos": it.position, "fx": it.effects,
                }
                pos_dist[it.position] = pos_dist.get(it.position, 0) + 1
        current._bank_inventory = items
        current._bank_list_event.set()
        bridge.update_bank_inventory(list(items.values()))
        logger.info("[GameState] EL : %d objets en banque (positions: %s)",
                    len(items), pos_dist)
    except Exception as exc:
        logger.warning("[GameState] EL parse error : %s", exc)


@on_client_message("ZO")
def _on_full_inventory(msg: ParsedMessage) -> None:
    """ZO — dump complet de l'inventaire injecté par le patch core.swf.

    Format : ZO{uid}~{gid}~{qty}~{pos};{uid}~{gid}~{qty}~{pos};...
    (uid/gid/qty/pos en décimal). Reconstruit entièrement current._inventory.
    """
    try:
        items: dict = {}
        for chunk in msg.payload.split(";"):
            chunk = chunk.strip()
            if not chunk or "~" not in chunk:
                continue
            parts = chunk.split("~")
            if len(parts) < 4:
                continue
            uid = parts[0].strip()
            if not uid:
                continue

            def _int(s: str) -> int:
                s = s.strip()
                return int(s) if s.lstrip("-").isdigit() else 0

            items[uid] = {
                "uid": uid,
                "gid": _int(parts[1]),
                "qty": _int(parts[2]) or 1,
                "pos": _int(parts[3]),
                "fx": "",
            }
        current._inventory = items
        current._full_inv_event.set()
        bridge.update_inventory(list(items.values()))
        bridge.add_console(f"Inventaire complet : {len(items)} objets")
        logger.info("[GameState] ZO : inventaire complet reçu (%d objets)", len(items))
    except Exception as exc:
        logger.warning("[GameState] ZO parse error : %s", exc)


@on_server_message("Em")
def _on_exchange_kamas(msg: ParsedMessage) -> None:
    """Em S→C — montant kamas de l'échange PNJ.

    Pendant une vente au marchand, le serveur envoie ``Em KG{total}`` à mesure
    que les objets sont déposés : ``{total}`` = kamas cumulés offerts par le PNJ.
    On mémorise le dernier total pour le reporter après la vente.
    """
    payload = msg.payload.strip()
    if payload.startswith("KG"):
        amount = payload[2:].strip()
        if amount.lstrip("-").isdigit():
            current._exchange_kamas = int(amount)


# ---------------------------------------------------------------------------
# Handlers — HDV (bigstore), consommés par bot/hdv.py
# ---------------------------------------------------------------------------

@on_server_message("EHL")
def _on_bigstore_type_items(msg: ParsedMessage) -> None:
    """EHL S→C — gids des items en vente pour un type d'item.

    Réponse au ``EHT{type}`` du client. Format : ``{type}|{gid};{gid};…``
    """
    try:
        type_str, _, gids_str = msg.payload.strip().partition("|")
        current._bigstore_type_id = int(type_str)
        current._bigstore_type_gids = [
            int(g) for g in gids_str.split(";") if g.strip().lstrip("-").isdigit()
        ]
        current._bigstore_type_event.set()
        logger.info("[GameState] EHL type %s : %d item(s) en vente",
                    type_str, len(current._bigstore_type_gids))
    except Exception as exc:
        logger.warning("[GameState] EHL parse error : %s", exc)


@on_server_message("EHl")
def _on_bigstore_item_list(msg: ParsedMessage) -> None:
    """EHl S→C — offres HDV pour un gid (réponse au EHl C→S).

    On stocke le payload brut complet (le log du parser le tronque à 120
    chars) ; le décodage est fait par bot/hdv.py.
    """
    current._bigstore_list_payload = msg.payload
    current._bigstore_list_event.set()
    logger.debug("[GameState] EHl (offres) : %s", msg.payload)


@on_server_message("Rd")
def _on_mount_data(msg: ParsedMessage) -> None:
    """Rd S→C — fiche complète d'une monture (MountData).

    Format retroproto typ/commonmountdata.go — 21 champs séparés par ':'.
    Stocké brut, décodé par bot/hdv.py (recherche de dragodindes non castrées).
    """
    current._mount_data_payload = msg.payload
    current._mount_data_event.set()
    logger.debug("[GameState] Rd (monture) : %s", msg.payload)


# ---------------------------------------------------------------------------
# Handlers — métiers
# ---------------------------------------------------------------------------

@on_server_message("JX")
def _on_job_xp(msg: ParsedMessage) -> None:
    """JX — stats complètes de tous les métiers du personnage.

    Format : K{charId}~{jobId};{level};{xp};{xpNext};{xpTotal};|...
    Seul le JX de notre personnage principal est traité (filtré par charId).
    """
    if current.character is None:
        return
    try:
        jobs = parse_jx(msg.payload, current.character.character_id)
        if not jobs:
            return  # Pas notre perso
        for job in jobs:
            current._jobs[job.job_id] = {
                "job_id":   job.job_id,
                "name":     job.name,
                "level":    job.level,
                "xp":       job.xp,
                "upper_xp": job.xp_next,
            }
        bridge.update_jobs(_jobs_to_list())
        unlocked = [j for j in jobs if j.level > 1]
        logger.info("[GameState] JX : %d métiers (dont %d Nv>1)", len(jobs), len(unlocked))
        if unlocked:
            bridge.add_console(
                "Métiers : " + ", ".join(f"{j.name} Nv.{j.level}" for j in unlocked[:5])
            )
    except Exception as exc:
        logger.warning("[GameState] JX parse error : %s | payload=%r", exc, msg.payload[:80])


# ---------------------------------------------------------------------------
# Im — Messages d'information serveur (erreurs de sort, etc.)
# ---------------------------------------------------------------------------

@on_server_message("Im")
def _on_im_message(msg: ParsedMessage) -> None:
    """Im — InfoMessage : message d'information du serveur.

    Im 036  = "{pseudo} vient de rejoindre le combat en spectateur" → jeu ralenti.
    Im 1174 = "Un obstacle gêne votre vue" (LOS bloquée).
    Im 1172 = "Cette case n'est pas une cible valide" (ex: EMPTY_CELL=FALSE → cible vide refusée).
    Im 1171 = "Cet ennemi est hors de portée" (sort hors portée).
    Im 1170 = PA insuffisants (ex. "1170;1~3").
    """
    code = msg.payload.strip().split(";")[0]
    if code == "036":
        # "{pseudo} vient de rejoindre le combat en spectateur." Un joueur nous observe :
        # on bascule en jeu lent (délais aléatoires entre actions dans combat.py) jusqu'à
        # la fin du combat pour réduire la suspicion d'automatisation.
        current._spectator_present = True
        parts = msg.payload.strip().split(";", 1)
        spectator = parts[1] if len(parts) > 1 else "?"
        logger.info("[GameState] Im 036 : %s observe en spectateur → jeu ralenti", spectator)
        bridge.add_console(f"👁 {spectator} observe en spectateur — jeu ralenti")
        return
    if code == "1174":
        # Obstacle / ligne de vue bloquée : déclenche le repositionnement LdV et
        # compte dans le quota LdV par tour côté combat.py.
        current._spell_los_blocked = True
        current._spell_cast_failed = True
        current._spell_result_event.set()
        logger.debug("[GameState] Im 1174 : sort rejeté (LdV bloquée, flag levé)")
    elif code in ("1171", "1172"):
        # 1171 = hors de portée, 1172 = case d'impact invalide. Ce ne sont PAS des
        # rejets de LdV : il ne faut PAS lever _spell_los_blocked (sinon combat.py
        # les compte dans le quota LdV et fait du repositionnement latéral au lieu de
        # simplement exclure la case et retenter une case d'impact plus proche/valide).
        current._spell_cast_failed = True
        if code == "1171":
            # Le payload "1171;{min}~{max}~{actual}" donne la portée effective réelle
            # côté serveur → combat.py l'apprend pour ne plus jamais retirer hors portée.
            try:
                params = msg.payload.strip().split(";", 1)[1]
                rmin, rmax, ractual = (int(x) for x in params.split("~")[:3])
                current._last_range_reject = (rmin, rmax, ractual)
            except (IndexError, ValueError):
                current._last_range_reject = None
        current._spell_result_event.set()
        logger.debug("[GameState] Im %s : sort rejeté (hors portée / case invalide)", code)
    elif code.startswith("1170"):
        # PA insuffisants (souvent après un tacle qui a drainé des PA sans que le bot
        # le suive). Inutile de retenter une autre case : on lève un flag dédié pour
        # que combat.py arrête le sort au lieu de spammer le serveur.
        current._spell_cast_failed = True
        current._spell_pa_insufficient = True
        current._spell_result_event.set()
        logger.debug("[GameState] Im %s : sort rejeté (PA insuffisants)", code)


# ---------------------------------------------------------------------------
# Auto-boost des caractéristiques
# ---------------------------------------------------------------------------

def _schedule_auto_boost(char_id: str, points: int) -> None:
    """Planifier un AB automatique si une stat est configurée pour ce personnage.

    Lit bot_settings.json → accounts[pseudo].auto_boost[char_id].
    Si une stat_id est trouvée et que points > 0, crée une task asyncio
    pour envoyer AB dans la foulée.
    """
    import json
    import os

    account = current.character.pseudo if current.character else None
    if not account:
        return

    from core.paths import BOT_DIR
    settings_path = str(BOT_DIR / "bot_settings.json")
    try:
        with open(settings_path, "r", encoding="utf-8") as f:
            data = json.load(f)
    except Exception:
        return

    auto_boost = data.get("accounts", {}).get(account, {}).get("auto_boost", {})
    stat_id_raw = auto_boost.get(str(char_id))
    if stat_id_raw is None:
        return

    try:
        stat_id = int(stat_id_raw)
    except (ValueError, TypeError):
        return

    loop = asyncio.get_event_loop()
    loop.create_task(_do_auto_boost(char_id, stat_id, points))


async def _do_auto_boost(char_id: str, stat_id: int, points: int) -> None:
    """Envoyer le packet AB pour distribuer automatiquement des points de carac."""
    from bot import channel as _channel
    try:
        await asyncio.sleep(0.4)
        await _channel.send(f"AB{char_id};{stat_id};{points}\n")
        logger.info("[GameState] Auto-boost %s : stat=%d points=%d", char_id, stat_id, points)
        bridge.add_console(f"Auto-boost {char_id} : +{points} pts → stat {stat_id}")
    except Exception as exc:
        logger.warning("[GameState] Auto-boost error : %s", exc)
