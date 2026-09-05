"""
Bot de combat groupe — team générique de Crâs (mode héros).

Stratégie par Crâ (chaque tour) :
  1. Trouver l'ennemi le plus proche
  2. Si à portée + LdV : attaquer directement
  3. Sinon : se déplacer (PM) pour se rapprocher et avoir la LdV
  4. Si à portée après déplacement : attaquer
  5. Sinon : passer le tour

Le sort d'attaque est configurable depuis l'onglet Combat (dropdown).
Les propriétés du sort (PA, portée, LdV) sont lues dynamiquement depuis spells.xml.

Protocole :
  GA907{cell_id};{entity_id}  C→S  Lancer un combat contre un groupe de monstres
  GA300{spell_id};{cell_id}   C→S  Lancer un sort sur une cellule
  Gt                          C→S  Terminer son tour
  GTL   S→C  Liste des IDs en jeu (négatifs = monstres)
  GTM   S→C  État HP/PA/PM/cell de chaque entité (début de chaque tour)
  GTS   S→C  Tour d'un combattant → queue dans game/state.py
  GE    S→C  Fin du combat
"""

from __future__ import annotations

import asyncio
import logging
import random
import time

from collections import deque

from bot import channel
from bot.pathfinding import cell_to_xy, xy_to_cell, MAP_WIDTH
from game import state as _state
from game.state import (
    wait_combat_end,
    wait_combat_start,
    wait_next_ally_turn,
    wait_map_ready,
    wait_move_result,
    clear_move_result,
    wait_actions_clear,
    get_live_monster_cells,
    get_targetable_monster_cells,
    get_targetable_monster_ids,
    is_summon as _is_summon,
    get_entity_pa,
    get_entity_pm,
    get_entity_po,
    get_spell_los_blocked,
    get_spell_cast_failed,
    get_spell_pa_insufficient,
    get_last_range_reject,
    clear_spell_los_blocked,
    clear_spell_result,
    wait_spell_result,
    is_spectator_present,
    has_foreign_player_on_map,
)

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

SPELL_ID: int = 161
"""Sort par défaut (Flèche Magique). Utilisé si aucun sort sélectionné dans l'UI."""

SPELL_CAST_DELAY: float = 0.5
"""Garde-fou (secondes) après un lancer de sort : durée MAX d'attente du résultat
serveur (GA;102 accepté ou Im rejet). Le cas nominal repart dès la réponse (~1 RTT),
ce délai n'est atteint que si le serveur ne renvoie ni 102 ni Im observable."""

TURN_TIMEOUT: float = 60.0
"""Secondes max d'attente d'un tour d'allié."""

FIGHT_START_DELAY: float = 0.6
"""Délai après map_ready avant d'envoyer GA907 (laisse les GM entity list arriver)."""

MOVE_TIMEOUT: float = 5.0
"""Secondes max d'attente du GKK après un GA001 de déplacement en combat."""

# Les bornes des délais anti-suspicion (spectateur en combat / joueur sur la carte) sont
# désormais configurables dans l'onglet Combat → Comportement → Discrétion et stockées en
# millisecondes dans le GameState (combat_spectator_delay_min/max_ms,
# combat_player_delay_min/max_ms). On les lit dynamiquement au moment du délai.

# ---------------------------------------------------------------------------
# Modes de discrétion (GameState.combat_discretion_mode)
# ---------------------------------------------------------------------------
# "human" (défaut) : cadence de joueur réel — les bornes HUMAN_* ci-dessous.
# "farming"        : l'ancien comportement (délais anti-rafale courts lus dans les
#                    champs GameState, ré-engagement quasi immédiat). ⚠ risque.
# "speed"          : aucun ralentissement. ⚠ danger maximal.

DISCRETION_HUMAN = "human"
DISCRETION_FARMING = "farming"
DISCRETION_SPEED = "speed"

# --- Mode humain (calibré d'après le ban du 09/07/2026 : 1 combat/21 s, tours de
# 2-4 s et ré-engagement < 1,5 s = signature bot immédiate pour un admin). Depuis, la
# cadence de base (délai par action, ré-engagement) a été alignée sur le mode farming
# (rapide) — la protection vient maintenant d'irrégularités ponctuelles plutôt que
# d'une lenteur permanente : pauses aléatoires en combat, pauses AFK périodiques et
# actions parasites (misclick, déplacement idle). Voir combat_farm_loop() / fight_group(). ---

HUMAN_SPECTATOR_FACTOR: float = 2.0
"""Multiplie le délai spectateur configuré (0.6-2 s était trop court : tours 2-4 s)."""

HUMAN_MIDFIGHT_PAUSE_PROBA: float = 0.05
"""Probabilité, testée à chaque tour d'allié, d'une pause « distraction » en plein
combat (SMS, verre d'eau…). Plafonnée à 1 fois par combat (flag dans fight_group)."""
HUMAN_MIDFIGHT_PAUSE_S: tuple[float, float] = (1.0, 8.0)
"""Bornes de la pause « distraction » en combat : brève (1-8 s), juste une hésitation
un peu marquée. Encore re-plafonnée dynamiquement sous le timer de tour serveur
(combat_turn_time_ms) au moment du tirage : la pause consomme le budget du tour EN COURS,
donc elle doit toujours laisser le temps de jouer le tour, sinon le serveur le saute
(héros qui « ne fait rien » — bug du 11/08 avec des pauses de 41 s sur un timer de 45 s).
Les vraies absences longues (SMS, verre d'eau) sont simulées hors combat par la pause AFK."""

MIDFIGHT_PAUSE_TURN_FRACTION: float = 0.4
"""Fraction max du timer de tour qu'une pause mi-combat peut consommer (laisse ≥60 %
du tour pour jouer). Sur un timer de 45 s → pause plafonnée à 18 s."""

HUMAN_MISCLICK_PROBA: float = 0.05
"""Probabilité, testée à chaque tour d'allié, d'un « misclick » : un cast raté sur sa
propre case (hors de portée, rejeté par le serveur) avant l'action réelle du tour.
Plafonnée à 1 fois par combat."""

AFK_PAUSE_EVERY_S: tuple[float, float] = (10 * 60.0, 20 * 60.0)
AFK_PAUSE_DURATION_S: tuple[float, float] = (20.0, 165.0)
"""Pause AFK hors combat : toutes les 10-20 min, coupure de 20 s à 2 min 45 (statut
dashboard basculé sur « Pause AFK » pendant ce temps)."""

IDLE_WANDER_PROBA: float = 0.06
"""Probabilité, à chaque itération de farm hors combat (et hors pause AFK), d'un petit
aller-retour d'une case — un joueur qui patiente ne reste jamais parfaitement immobile."""

HUMAN_PLAYER_HOLD_RECHECK_S: tuple[float, float] = (4.0, 8.0)
HUMAN_PLAYER_HOLD_PAUSE_AFTER_S: float = 120.0
HUMAN_PLAYER_RESUME_DELAY_S: tuple[float, float] = (5.0, 15.0)
"""Joueur inconnu sur la carte : farm suspendu tant qu'il est là (recheck), pause longue
s'il s'installe, reprise en douceur (délai aléatoire) après son départ."""


def _discretion_mode() -> str:
    """Mode de discrétion courant, avec repli sur « human » si valeur inconnue."""
    mode = getattr(_state.current, "combat_discretion_mode", DISCRETION_HUMAN)
    if mode not in (DISCRETION_HUMAN, DISCRETION_FARMING, DISCRETION_SPEED):
        return DISCRETION_HUMAN
    return mode


async def _wait_antibot_freeze() -> None:
    """Attendre tant que la procédure antibot gèle les actions (saisie du .code).

    Un humain qui lit le popup antibot et tape le code ne joue PAS ses tours de combat
    pendant ce temps : le gel s'applique à tous les persos de la team. Posé/levé par
    bot.antibot (GameState._antibot_freeze). Le timer de tour serveur (45 s) laisse
    largement la place aux ~10-25 s de gel.
    """
    while getattr(_state.current, "_antibot_freeze", False) and channel.is_connected():
        await asyncio.sleep(0.25)

# Legacy : constantes des anciennes fonctions AOE (code mort conservé pour référence)
SPELL_MAX_RANGE: int = 8
SPELL_MAGIC_RANGE: int = 12
AOE_RADIUS: int = 1

# Portée d'attaque assumée des monstres pour le calcul de menace (en cases).
# Certains monstres tapent à 2 cases (Bouftou, Larve, etc.) — on prend cette marge
# par défaut pour éviter d'être pris au corps à corps ou en allonge.
# Menace réelle d'un monstre = PM + MONSTER_ATTACK_RANGE.
MONSTER_ATTACK_RANGE: int = 2

# Seuil de "blessure" pour focus : un monstre est considéré comme cible prioritaire
# dès qu'il a perdu au moins 10% de ses HP max. Évite de focus pour de l'érosion.
INJURED_HP_THRESHOLD: float = 0.10

# Pénalité de scoring appliquée à toute case de déplacement ADJACENTE (distance PO
# ≤ 1) à un monstre — invocations comprises. Un perso collé à un ennemi se fait
# tacler (perte de PA/PM, blocage), donc on évite le corps à corps subi : cette
# pénalité est volontairement très lourde pour ne se coller qu'en dernier recours.
CAC_PROXIMITY_PENALTY: float = 2000.0

# ---------------------------------------------------------------------------
# Cache LOS : paires (caster_cell, target_cell) rejetées par Im 1174 ce tour-ci
# ---------------------------------------------------------------------------
# Set de (caster_cell, target_cell). Vidé à chaque nouveau tour (GTS) via
# clear_turn_los_cache(). Garde les paires bloquées même après un déplacement
# du lanceur — la nouvelle case a son propre lookup distinct.
_los_blocked_pairs: set[tuple[int, int]] = set()

# Set de cell_ids de cibles/impacts rejetés par le serveur ce tour-ci, quelle que
# soit la case du lanceur (Im 1174 LdV, Im 1172 case invalide, Im 1171 portée).
# Contrairement à _los_blocked_pairs, le blocage vaut pour TOUTES les positions du
# lanceur : après un repli, on ne retente pas le même cluster lointain (qui
# rejetterait à nouveau). Vidé à chaque nouveau tour.
_rejected_target_cells: set[int] = set()

# Paires (caster_cell, target_cell) dont le serveur a rejeté la LdV (Im 1174) ALORS
# QUE notre algo la croyait dégagée = divergence d'algorithme sur la géométrie TERRAIN
# (statique par map). Contrairement à _los_blocked_pairs (par tour), celui-ci est
# PERSISTANT par map et sur disque : une fois qu'on sait que le serveur refuse
# (cell A → cell B) sur la map M, on ne réémet plus jamais ce cast. Clé : map_id →
# set de (caster_cell, target_cell). Peuplé uniquement quand notre LdV ≠ serveur.
_los_learned_blocked: dict[int, set[tuple[int, int]]] = {}


def clear_turn_los_cache() -> None:
    """Vider les caches de rejet — appelé au début du tour de chaque allié."""
    _los_blocked_pairs.clear()
    _rejected_target_cells.clear()


def mark_los_blocked(caster_cell: int, target_cell: int) -> None:
    """Marquer une paire (lanceur, cible) comme bloquée par le serveur (ce tour)."""
    _los_blocked_pairs.add((caster_cell, target_cell))


def _current_map_id() -> int | None:
    cm = _state.current.current_map
    return cm.map_id if cm else None


def is_los_blocked_pair(caster_cell: int, target_cell: int) -> bool:
    """True si le serveur a rejeté cette paire (ce tour OU appris de façon persistante).

    Le cache persistant _los_learned_blocked est consulté pour la map courante : il
    évite de réémettre un cast dont on sait déjà que le serveur refusera la LdV
    (divergence terrain), donc plus jamais de Im 1174 pour cette géométrie.
    """
    if (caster_cell, target_cell) in _los_blocked_pairs:
        return True
    mid = _current_map_id()
    if mid is not None:
        learned = _los_learned_blocked.get(mid)
        if learned is not None and (caster_cell, target_cell) in learned:
            return True
    return False


# ---------------------------------------------------------------------------
# Calcul de distance PO (portée) — coordonnées diagonales
# ---------------------------------------------------------------------------
# En Dofus 1.29, la grille de combat est un damier en losange (diamant).
# Chaque cellule a 4 voisins cardinaux dans la grille diamant.
# La distance PO = distance Manhattan dans le repère diagonal :
#   PO = |a1 - a2| + |b1 - b2|
# où (a, b) sont les coordonnées diagonales de la cellule.
#
# Formule de conversion (x, y) staggered → (a, b) diagonal :
#   k = y // 2
#   Si y pair : a = x + k,     b = x - k
#   Si y impair : a = x + k + 1, b = x - k

# Voisins diagonaux : 4 directions cardinales dans la grille diamant.
# Ce sont les seuls mouvements valides en combat (1 PM chacun).
_DIAG_ODD_NEIGHBORS  = [(0, 1), (0, -1), (1, 1), (1, -1)]
_DIAG_PAIR_NEIGHBORS = [(0, 1), (0, -1), (-1, 1), (-1, -1)]

# Voisins 6-directions (overworld, pas pour combat)
_ODD_NEIGHBORS_6  = [(0, 1), (0, -1), (1, 0), (-1, 0), (1, 1),  (1, -1)]
_PAIR_NEIGHBORS_6 = [(0, 1), (0, -1), (1, 0), (-1, 0), (-1, 1), (-1, -1)]

_ODD_NEIGHBORS_8  = [(0, 1), (0, -1), (1, 0), (-1, 0), (1, 1),  (1, -1),  (0, 2), (0, -2)]
_PAIR_NEIGHBORS_8 = [(0, 1), (0, -1), (1, 0), (-1, 0), (-1, 1), (-1, -1), (0, 2), (0, -2)]


def _cell_to_diag(cell: int, width: int = MAP_WIDTH) -> tuple[int, int]:
    """Convertir un cell_id en coordonnées diagonales (a, b)."""
    x, y = cell_to_xy(cell, width)
    k = y // 2
    if y % 2 == 0:
        return (x + k, x - k)
    else:
        return (x + k + 1, x - k)


def _po_distance(cell_a: int, cell_b: int, width: int = MAP_WIDTH) -> int:
    """Distance PO (portée) entre deux cellules.

    Utilise la distance Manhattan dans le repère diagonal, comme le serveur
    Dofus 1.29 pour les vérifications de portée de sort.
    """
    if cell_a == cell_b:
        return 0
    a1, b1 = _cell_to_diag(cell_a, width)
    a2, b2 = _cell_to_diag(cell_b, width)
    return abs(a1 - a2) + abs(b1 - b2)


def _game_distance(cell_a: int, cell_b: int, width: int = MAP_WIDTH) -> int:
    """Alias pour _po_distance (rétro-compatibilité avec les anciennes fonctions)."""
    return _po_distance(cell_a, cell_b, width)


def _cells_in_po_range(
    center_cell: int,
    max_po: int,
    width: int = MAP_WIDTH,
    min_po: int = 0,
) -> list[int]:
    """Toutes les cellules à distance PO entre min_po et max_po (inclus).

    Énumère via les coordonnées diagonales (a, b) : la distance PO étant la
    Manhattan dans ce repère, on parcourt le diamant |Δa| + |Δb| ≤ max_po.
    Plus précis que _cells_in_game_range (qui sur-approxime via BFS 8-voisins).
    """
    if max_po < 0 or max_po < min_po:
        return []
    a0, b0 = _cell_to_diag(center_cell, width)
    out: list[int] = []
    for da in range(-max_po, max_po + 1):
        max_db = max_po - abs(da)
        for db in range(-max_db, max_db + 1):
            po = abs(da) + abs(db)
            if po < min_po:
                continue
            cell = _diag_to_cell(a0 + da, b0 + db, width)
            if cell is not None:
                out.append(cell)
    return out


def _aoe_hits(
    impact_cell: int,
    monster_cells: list[int],
    radius: int,
    width: int = MAP_WIDTH,
) -> int:
    """Nombre de monstres dans la zone AOE (PO ≤ radius) autour de impact_cell."""
    if radius < 0:
        return 0
    return sum(1 for mc in monster_cells if _po_distance(impact_cell, mc, width) <= radius)


def _aoe_hits_in(
    impact_cell: int,
    cells: set[int] | list[int],
    radius: int,
    width: int = MAP_WIDTH,
) -> int:
    """Nombre de cellules de `cells` couvertes par l'AOE (PO ≤ radius) autour de impact_cell."""
    if radius < 0 or not cells:
        return 0
    return sum(1 for c in cells if _po_distance(impact_cell, c, width) <= radius)


def _cells_in_game_range(center_cell: int, radius: int, width: int = MAP_WIDTH) -> set[int]:
    """Ensemble des cellules à distance BFS ≤ radius depuis center_cell.

    Utilise le graphe 8-voisins (avec sauts (0,±2)) pour correspondre au calcul
    d'AOE du serveur. Les cellules verticalement alignées à 2 lignes d'écart sont
    incluses dans le rayon 1, ce qui est nécessaire pour la vérification friendly fire.
    """
    cx, cy = cell_to_xy(center_cell, width)
    visited: set[tuple[int, int]] = {(cx, cy)}
    queue: deque[tuple[int, int, int]] = deque([(cx, cy, 0)])
    result: set[int] = {center_cell}

    while queue:
        x, y, dist = queue.popleft()
        if dist >= radius:
            continue
        neighbors = _ODD_NEIGHBORS_8 if y % 2 != 0 else _PAIR_NEIGHBORS_8
        for dx, dy in neighbors:
            nx, ny = x + dx, y + dy
            if not (0 <= nx < width and ny >= 0):
                continue
            if (nx, ny) in visited:
                continue
            visited.add((nx, ny))
            result.add(xy_to_cell(nx, ny, width))
            queue.append((nx, ny, dist + 1))

    return result


def _aoe_hit_count(target_cell: int, monster_cells: list[int], width: int = MAP_WIDTH) -> int:
    """Nombre de monstres touchés par un cast à target_cell (AOE rayon AOE_RADIUS BFS)."""
    aoe_zone = _cells_in_game_range(target_cell, AOE_RADIUS, width)
    return sum(1 for mc in monster_cells if mc in aoe_zone)


def _diag_to_cell(a: int, b: int, width: int = MAP_WIDTH) -> int | None:
    """Inverse de _cell_to_diag. Retourne le cell_id ou None si hors grille.

    Pour y pair : a = x + y/2, b = x - y/2  →  a+b = 2x, a-b = y.
    Pour y impair : a = x + (y-1)/2 + 1, b = x - (y-1)/2  →  a+b = 2x+1, a-b = y.
    """
    s = a + b
    y = a - b
    if y < 0:
        return None
    if y % 2 == 0:
        if s % 2 != 0:
            return None
        x = s // 2
    else:
        if s % 2 == 0:
            return None
        x = (s - 1) // 2
    if not (0 <= x < width):
        return None
    return xy_to_cell(x, y, width)


def has_line_of_sight(
    caster_cell: int,
    target_cell: int,
    los_blockers: set[int],
    width: int = MAP_WIDTH,
) -> bool:
    """Vérifier si la ligne de vue est dégagée entre deux cellules.

    Supercover ÉPAIS (Amanatides-Woo) en coordonnées diagonales (a, b). À CHAQUE
    pas, les DEUX cellules candidates `(a+sgn_a, b)` et `(a, b+sgn_b)` sont testées,
    pas seulement aux croisements de coin. Le serveur Dofus 1.29 bloque la LdV dès
    qu'un obstacle « effleure » la ligne ; le supercover fin d'origine ne traversait
    que la cellule centrale et laissait passer des tirs que le serveur refuse
    (Im 1174). Mesuré sur des rejets réels : la version épaisse rattrape ~40 % des
    divergences terrain (le fin : 0 %), pour ~6 % de tirs en trop écartés (prudent =
    on rate un tir plutôt que de se faire rejeter). Le reste est couvert par le cache
    LdV appris (is_los_blocked_pair) et les entités passées en blockers.

    los_blockers : set de cell_ids obstruant la LdV (obstacles terrain non
    praticables + cellules des entités intermédiaires).
    """
    if caster_cell == target_cell:
        return True

    a0, b0 = _cell_to_diag(caster_cell, width)
    a1, b1 = _cell_to_diag(target_cell, width)
    da = a1 - a0
    db = b1 - b0
    if da == 0 and db == 0:
        return True

    sgn_a = 1 if da > 0 else (-1 if da < 0 else 0)
    sgn_b = 1 if db > 0 else (-1 if db < 0 else 0)
    nx, ny = abs(da), abs(db)
    inf = float("inf")
    t_delta_a = (1.0 / nx) if nx else inf
    t_delta_b = (1.0 / ny) if ny else inf
    t_max_a = t_delta_a
    t_max_b = t_delta_b

    a, b = a0, b0
    EPS = 1e-9

    def _blocks(cell_id: int | None) -> bool:
        return (cell_id is not None
                and cell_id != caster_cell and cell_id != target_cell
                and cell_id in los_blockers)

    while not (a == a1 and b == b1):
        # Épais : tester les deux cellules candidates à CHAQUE pas.
        if _blocks(_diag_to_cell(a + sgn_a, b, width)) or _blocks(_diag_to_cell(a, b + sgn_b, width)):
            return False

        if abs(t_max_a - t_max_b) < EPS and nx and ny:
            a += sgn_a
            b += sgn_b
            t_max_a += t_delta_a
            t_max_b += t_delta_b
        elif t_max_a < t_max_b:
            a += sgn_a
            t_max_a += t_delta_a
        else:
            b += sgn_b
            t_max_b += t_delta_b

        if (a, b) != (a1, b1) and _blocks(_diag_to_cell(a, b, width)):
            return False

    return True


def _ranked_aoe_candidates(
    alive_monster_cells: list[int],
    caster_cell: int | None = None,
    protected_cells: list[int] | None = None,
    blocked_cells: set[int] | None = None,
    los_blockers: set[int] | None = None,
    width: int = MAP_WIDTH,
    spell_range: int = SPELL_MAX_RANGE,
) -> list[int]:
    """Retourne toutes les cellules candidates triées par (AOE DESC, dist_lanceur ASC).

    Tri secondaire par distance au lanceur : les cellules plus proches ont une
    ligne de vue plus directe et sont généralement moins obstruées par le terrain.

    Si le filtre LOS n'élimine aucun candidat viable, on conserve tous les
    candidats en portée et on laisse le serveur trancher via Im 1174 (notre
    algo LOS peut ne pas être parfait sur toutes les maps).

    protected_cells : cells des alliés à NE PAS toucher (friendly fire).
    blocked_cells   : obstacles/murs non ciblables + cellules déjà Im 1174 ce tour.
    los_blockers    : cellules bloquant la ligne de vue (MapInfo.los_blocker_cells).
    """
    if not alive_monster_cells:
        return []

    _blocked = blocked_cells or set()

    if caster_cell is not None:
        caster_range = _cells_in_game_range(caster_cell, spell_range, width)
        candidate_set = {
            c for c in caster_range
            if c not in _blocked
            and _aoe_hit_count(c, alive_monster_cells, width) > 0
        }
        if not candidate_set:
            logger.debug(
                "[combat] Aucun monstre à portée %d BFS depuis cell %d (monstres: %s)",
                spell_range, caster_cell, alive_monster_cells,
            )
            return []

        # Filtrer par ligne de vue (best-effort : notre algo peut être imparfait)
        if los_blockers is not None:
            los_candidates = {
                c for c in candidate_set
                if has_line_of_sight(caster_cell, c, los_blockers, width)
            }
            if los_candidates:
                candidate_set = los_candidates
            # else : garder tous les candidats — le serveur tranchera via Im 1174
    else:
        candidate_set = set()
        for mc in alive_monster_cells:
            candidate_set |= _cells_in_game_range(mc, AOE_RADIUS, width)

    # Filtrer les cibles dont l'AOE toucherait un allié
    if protected_cells:
        safe_candidates = {
            c for c in candidate_set
            if not any(
                pc in _cells_in_game_range(c, AOE_RADIUS, width)
                for pc in protected_cells
            )
        }
        if safe_candidates:
            candidate_set = safe_candidates
        # else : ignorer la contrainte friendly fire si aucune cible safe

    # Tri : max AOE en premier, puis plus proche du lanceur (meilleure LdV probable)
    def _score(c: int) -> tuple[int, int]:
        aoe = _aoe_hit_count(c, alive_monster_cells, width)
        dist = _game_distance(caster_cell, c, width) if caster_cell is not None else 0
        return (-aoe, dist)

    return sorted(candidate_set, key=_score)


def _best_aoe_target(
    alive_monster_cells: list[int],
    caster_cell: int | None = None,
    protected_cells: list[int] | None = None,
    blocked_cells: set[int] | None = None,
    los_blockers: set[int] | None = None,
    width: int = MAP_WIDTH,
) -> int | None:
    """Meilleur candidat AOE — wrapper autour de _ranked_aoe_candidates."""
    candidates = _ranked_aoe_candidates(
        alive_monster_cells,
        caster_cell=caster_cell,
        protected_cells=protected_cells,
        blocked_cells=blocked_cells,
        los_blockers=los_blockers,
        width=width,
    )
    return candidates[0] if candidates else None


# ---------------------------------------------------------------------------
# Déplacement en combat
# ---------------------------------------------------------------------------

def _movement_reachable_cells(
    caster_cell: int,
    pm: int,
    move_blocked: set[int],
    width: int = MAP_WIDTH,
) -> set[int]:
    """Cellules atteignables depuis caster_cell en au plus pm pas (obstacles respectés).

    move_blocked : terrain bloqué + positions des monstres + positions des alliés.
    Retourne les cellules sur lesquelles le Crâ peut S'ARRÊTER (pas celles traversées).
    """
    cx, cy = cell_to_xy(caster_cell, width)
    visited: dict[tuple[int, int], int] = {(cx, cy): 0}
    queue: deque[tuple[int, int, int]] = deque([(cx, cy, 0)])
    result: set[int] = set()

    while queue:
        x, y, steps = queue.popleft()
        # 4 voisins diagonaux uniquement (mouvement combat Dofus 1.29)
        neighbors = _DIAG_ODD_NEIGHBORS if y % 2 != 0 else _DIAG_PAIR_NEIGHBORS
        for dx, dy in neighbors:
            nx, ny = x + dx, y + dy
            if not (0 <= nx < width and ny >= 0):
                continue
            cell_id = xy_to_cell(nx, ny, width)
            if cell_id in move_blocked:
                continue
            new_steps = steps + 1
            if new_steps > pm:
                continue
            if (nx, ny) in visited and visited[(nx, ny)] <= new_steps:
                continue
            visited[(nx, ny)] = new_steps
            result.add(cell_id)
            queue.append((nx, ny, new_steps))

    return result


def _find_move_destination(
    caster_cell: int,
    pm: int,
    monster_cells: list[int],
    protected_cells: list[int],
    blocked_cells: set[int],
    los_blockers: set[int] | None,
    all_ally_cells: set[int] | None = None,
    width: int = MAP_WIDTH,
    spell_range: int = SPELL_MAX_RANGE,
    magic_range: int = SPELL_MAGIC_RANGE,
) -> int | None:
    """Trouver la meilleure case vers laquelle se déplacer en combat.

    Phase 1 : case depuis laquelle flèche explosive atteint le plus de monstres.
    Phase 2 : si rien trouvé, case la plus proche d'où flèche magique peut atteindre.

    all_ally_cells : toutes les cases occupées par des alliés (y compris héros passifs)
                     utilisé pour bloquer le pathfinding — évite les chemins à travers
                     des héros que le serveur refuserait.

    Retourne None si aucune case avantageuse n'est trouvable.
    """
    # Tous les alliés (y compris passifs) bloquent physiquement les cases
    _ally_block = all_ally_cells if all_ally_cells is not None else set(protected_cells)
    move_blocked = blocked_cells | set(monster_cells) | _ally_block
    reachable = _movement_reachable_cells(caster_cell, pm, move_blocked, width)

    if not reachable:
        return None

    best_dest: int | None = None
    best_hits: int = 0
    best_dist: int = 9999

    # Phase 1 : meilleure case pour flèche explosive
    for dest in reachable:
        candidates = _ranked_aoe_candidates(
            monster_cells,
            caster_cell=dest,
            protected_cells=protected_cells,
            blocked_cells=blocked_cells,
            los_blockers=los_blockers,
            width=width,
            spell_range=spell_range,
        )
        if not candidates:
            continue
        top_hits = _aoe_hit_count(candidates[0], monster_cells, width)
        if top_hits == 0:
            continue
        dist = _game_distance(caster_cell, dest, width)
        if top_hits > best_hits or (top_hits == best_hits and dist < best_dist):
            best_hits = top_hits
            best_dest = dest
            best_dist = dist

    if best_dest is not None:
        return best_dest

    # Phase 2 : fallback flèche magique — destination la plus proche d'où un
    # monstre est atteignable en portée magic_range avec ligne de vue.
    best_dist = 9999
    for dest in reachable:
        magic_ok = any(
            mc not in blocked_cells
            and _game_distance(dest, mc, width) <= magic_range
            and (los_blockers is None or has_line_of_sight(dest, mc, los_blockers, width))
            for mc in monster_cells
        )
        if not magic_ok:
            continue
        dist = _game_distance(caster_cell, dest, width)
        if dist < best_dist:
            best_dist = dist
            best_dest = dest

    return best_dest


def _combat_astar(
    blocked: set[int],
    start_cell: int,
    goal_cell: int,
    width: int = MAP_WIDTH,
) -> list[int] | None:
    """A* spécifique au combat : utilise les 4 voisins diagonaux uniquement.

    Retourne le chemin (liste de cell_ids du start au goal inclus), ou None.
    """
    import heapq

    sx, sy = cell_to_xy(start_cell, width)
    gx, gy = cell_to_xy(goal_cell, width)

    open_set: list[tuple[float, int, int]] = [(0.0, sx, sy)]
    came_from: dict[tuple[int, int], tuple[int, int]] = {}
    g_score: dict[tuple[int, int], float] = {(sx, sy): 0.0}

    def _h(x: int, y: int) -> float:
        a1, b1 = (x + y // 2 + (1 if y % 2 else 0), x - y // 2)
        a2, b2 = (gx + gy // 2 + (1 if gy % 2 else 0), gx - gy // 2)
        return abs(a1 - a2) + abs(b1 - b2)

    while open_set:
        _, cx, cy = heapq.heappop(open_set)
        if cx == gx and cy == gy:
            # Reconstruire le chemin
            path_xy = [(gx, gy)]
            cur = (gx, gy)
            while cur in came_from:
                cur = came_from[cur]
                path_xy.append(cur)
            path_xy.reverse()
            return [xy_to_cell(x, y, width) for x, y in path_xy]

        current_g = g_score.get((cx, cy), float("inf"))
        neighbors = _DIAG_ODD_NEIGHBORS if cy % 2 != 0 else _DIAG_PAIR_NEIGHBORS
        for dx, dy in neighbors:
            nx, ny = cx + dx, cy + dy
            if not (0 <= nx < width and ny >= 0):
                continue
            ncell = xy_to_cell(nx, ny, width)
            if ncell in blocked and ncell != goal_cell:
                continue
            tentative_g = current_g + 1.0
            if tentative_g < g_score.get((nx, ny), float("inf")):
                came_from[(nx, ny)] = (cx, cy)
                g_score[(nx, ny)] = tentative_g
                heapq.heappush(open_set, (tentative_g + _h(nx, ny), nx, ny))

    return None


async def _move_caster_to(
    caster_label: str,
    caster_id: str,
    dest_cell: int,
    move_blocked: set[int],
    width: int = MAP_WIDTH,
) -> bool:
    """Déplacer un Crâ vers dest_cell en combat (GA001).

    Utilise le A* combat (4 voisins diagonaux), envoie GA001, attend GKK.
    Met à jour combat_entity_cells avec la nouvelle position.

    Returns True si le déplacement a été envoyé et confirmé.
    """
    from bot.pathfinding import path_to_ga001

    caster_cell = _state.current.combat_entity_cells.get(caster_id)
    if caster_cell is None or caster_cell == dest_cell:
        return False

    path = _combat_astar(move_blocked, caster_cell, dest_cell, width)
    if not path or len(path) < 2:
        logger.warning(
            "[combat] %s — pas de chemin vers cell %d depuis %d",
            caster_label, dest_cell, caster_cell,
        )
        return False

    ga001_data = path_to_ga001(path, width)
    if not ga001_data:
        return False

    logger.info(
        "[combat] %s — déplacement %d→%d (%d étape(s))",
        caster_label, caster_cell, dest_cell, len(path) - 1,
    )
    await _spectator_slowdown()
    # Réarmer les events de résultat AVANT l'envoi (anti-race : un rejet arrivé
    # pendant channel.send ne doit pas être effacé puis manqué).
    clear_move_result()
    ok = await channel.send(f"GA001{ga001_data}\n")
    if not ok:
        logger.warning("[combat] %s — envoi GA001 échoué (connexion ?)", caster_label)
        return False

    # Attendre le résultat du déplacement :
    #  - "ok"       : GKK Flash reçu → move confirmé.
    #  - "rejected" : GA;129 sans delta → le serveur a refusé le move (souvent PM vidé
    #                 par un tacle non suivi localement). Inutile d'attendre le GKK : il
    #                 ne viendra pas. On force le PM local à 0 pour que les stratégies de
    #                 déplacement suivantes (correction LdV, avance, repli) soient skippées
    #                 ce tour au lieu de re-tenter des moves que le serveur re-rejettera.
    #  - "timeout"  : ni GKK ni rejet → on considère le move échoué, position inchangée.
    result = await wait_move_result(timeout=MOVE_TIMEOUT)
    if result == "rejected":
        logger.info(
            "[combat] %s — mouvement refusé par le serveur (PM épuisé ? tacle) — PM local mis à 0",
            caster_label,
        )
        _state.current.combat_entity_pm[caster_id] = 0
        return False
    if result == "timeout":
        logger.warning(
            "[combat] %s — résultat de mouvement non reçu (timeout %.0fs) — position inchangée",
            caster_label, MOVE_TIMEOUT,
        )
        return False

    # Mettre à jour la position locale (GTM ne vient qu'au prochain tour)
    _state.current.combat_entity_cells[caster_id] = dest_cell
    return True


# ---------------------------------------------------------------------------
# Récupération des propriétés du sort d'attaque
# ---------------------------------------------------------------------------

# ---------------------------------------------------------------------------
# Portée apprise du serveur (anti Im 1171)
# ---------------------------------------------------------------------------
# Le serveur privé Abrak a des portées de sort qui DIVERGENT de notre spells.xml
# local (ex : Flèche Explosive 179 = portée 1-4 côté serveur, 1-8 dans le XML). Le
# vrai client Flash utilise les données du serveur, donc ne vise jamais hors portée ;
# nous, avec le XML gonflé, on émettait des GA300 rejetés (Im 1171) = signature bot.
#
# Correctif : quand le serveur rejette un cast avec "Im 1171;{min}~{max}~{actual}", il
# révèle la portée EFFECTIVE (base + PO du lanceur à cet instant). On en déduit la portée
# de base réelle (base = max_serveur − po_du_lanceur) et on la mémorise par sort. Ensuite
# _get_spell_info renvoie cette base corrigée → plus aucun cast hors portée après la 1ʳᵉ
# observation. Persisté sur disque : appris une fois, jamais réémis.
#
# Clé : spell_id → (base_range_min, base_range_max). On garde la valeur la PLUS BASSE
# observée (prudent : ne jamais surestimer la portée).
_observed_spell_range: dict[int, tuple[int, int]] = {}

import os as _os
import json as _json
from core.paths import DATA_DIR

_SPELL_RANGE_OVERRIDE_PATH = str(DATA_DIR / "spell_range_overrides.json")


def _load_spell_range_overrides() -> None:
    """Charger le cache de portées apprises depuis le disque (best-effort)."""
    try:
        with open(_SPELL_RANGE_OVERRIDE_PATH, encoding="utf-8") as fh:
            data = _json.load(fh)
        for k, v in data.items():
            _observed_spell_range[int(k)] = (int(v[0]), int(v[1]))
        if _observed_spell_range:
            logger.info("[combat] %d portée(s) de sort apprises chargées", len(_observed_spell_range))
    except (FileNotFoundError, ValueError, KeyError, TypeError):
        pass


def _save_spell_range_overrides() -> None:
    """Persister le cache de portées apprises (best-effort, ne jamais planter le combat)."""
    try:
        _os.makedirs(_os.path.dirname(_SPELL_RANGE_OVERRIDE_PATH), exist_ok=True)
        with open(_SPELL_RANGE_OVERRIDE_PATH, "w", encoding="utf-8") as fh:
            _json.dump({str(k): list(v) for k, v in _observed_spell_range.items()}, fh)
    except OSError:
        pass


def _learn_spell_range(spell_id: int, server_min: int, server_max: int, caster_po: int) -> None:
    """Mémoriser la portée de base réelle d'un sort d'après un rejet Im 1171.

    server_max inclut le bonus PO du lanceur à l'instant du cast → base = server_max − po.
    On conserve la borne max la plus BASSE jamais vue (prudence anti-rejet).
    """
    base_max = max(0, server_max - max(0, caster_po))
    base_min = max(0, server_min)  # le PO n'affecte pas la portée min
    prev = _observed_spell_range.get(spell_id)
    if prev is None or base_max < prev[1]:
        _observed_spell_range[spell_id] = (base_min, base_max)
        logger.info(
            "[combat] Portée serveur apprise pour sort %d : base %d-%d "
            "(rejet %d~%d, PO lanceur=%d) → spells.xml local corrigé",
            spell_id, base_min, base_max, server_min, server_max, caster_po,
        )
        _save_spell_range_overrides()


_load_spell_range_overrides()


# ---------------------------------------------------------------------------
# Plafond de portée appris par ÉTAT (anti Im 1171, robuste au PO/buff faux)
# ---------------------------------------------------------------------------
# Le modèle base+PO est fragile : la portée de base diverge du serveur ET le bonus
# PO (Tir Eloigné, PO des héros non remonté par As) est mal estimé, donc parfois
# la portée effective calculée (range_max + po_bonus) DÉPASSE ce que le serveur
# autorise → Im 1171 en boucle sans converger.
#
# Solution robuste : on n'essaie PLUS de deviner base ni PO. On mémorise directement
# le mapping (spell_id, portée_effective_calculée_par_le_bot) → max_serveur_réel. Peu
# importe que notre calcul soit faux : il est DÉTERMINISTE pour un état donné (même
# sort, même PO estimé → même effectif calculé). Après 1 rejet dans cet état, on
# clampe le PO pour ne jamais dépasser le max serveur. Converge en 1 rejet par état.
# Clé JSON : "spell_id:effectif_calculé" → max_serveur. On garde le plus BAS observé.
_range_cap: dict[tuple[int, int], int] = {}

_RANGE_CAP_PATH = str(DATA_DIR / "spell_range_caps.json")


def _load_range_caps() -> None:
    try:
        with open(_RANGE_CAP_PATH, encoding="utf-8") as fh:
            data = _json.load(fh)
        for k, v in data.items():
            sid, eff = k.split(":")
            _range_cap[(int(sid), int(eff))] = int(v)
        if _range_cap:
            logger.info("[combat] %d plafond(s) de portée appris chargés", len(_range_cap))
    except (FileNotFoundError, ValueError, KeyError, TypeError):
        pass


def _save_range_caps() -> None:
    try:
        _os.makedirs(_os.path.dirname(_RANGE_CAP_PATH), exist_ok=True)
        data = {f"{sid}:{eff}": v for (sid, eff), v in _range_cap.items()}
        with open(_RANGE_CAP_PATH, "w", encoding="utf-8") as fh:
            _json.dump(data, fh)
    except OSError:
        pass


def _learn_range_cap(spell_id: int, computed_effective: int, server_max: int) -> None:
    """Mémoriser : pour ce (sort, portée effective que le bot avait calculée), le
    serveur n'autorise en réalité que server_max. On garde le plus bas (prudence)."""
    key = (spell_id, computed_effective)
    prev = _range_cap.get(key)
    if prev is None or server_max < prev:
        _range_cap[key] = server_max
        logger.info(
            "[combat] Plafond portée appris : sort %d, effectif calculé %d → "
            "max serveur %d (clamp PO)", spell_id, computed_effective, server_max,
        )
        _save_range_caps()


def _capped_po_bonus(spell_id: int, range_max: int, po_bonus: int) -> int:
    """Réduire po_bonus pour que range_max+po_bonus ne dépasse pas le plafond serveur
    appris pour cet état. No-op si aucun plafond connu."""
    computed = range_max + po_bonus
    cap = _range_cap.get((spell_id, computed))
    if cap is not None and cap < computed:
        return max(0, cap - range_max)
    return po_bonus


_load_range_caps()


# --- Cache LdV terrain appris (anti Im 1174) ---
_LOS_BLOCKED_PATH = str(DATA_DIR / "los_blocked_pairs.json")


def _load_los_blocked_overrides() -> None:
    """Charger les paires LdV bloquées apprises (par map) depuis le disque."""
    try:
        with open(_LOS_BLOCKED_PATH, encoding="utf-8") as fh:
            data = _json.load(fh)
        total = 0
        for mid, pairs in data.items():
            s = {(int(p[0]), int(p[1])) for p in pairs}
            _los_learned_blocked[int(mid)] = s
            total += len(s)
        if total:
            logger.info("[combat] %d paire(s) LdV bloquées apprises chargées", total)
    except (FileNotFoundError, ValueError, KeyError, TypeError):
        pass


def _save_los_blocked_overrides() -> None:
    """Persister les paires LdV bloquées apprises (best-effort)."""
    try:
        _os.makedirs(_os.path.dirname(_LOS_BLOCKED_PATH), exist_ok=True)
        data = {
            str(mid): [list(p) for p in sorted(pairs)]
            for mid, pairs in _los_learned_blocked.items()
        }
        with open(_LOS_BLOCKED_PATH, "w", encoding="utf-8") as fh:
            _json.dump(data, fh)
    except OSError:
        pass


def _learn_los_blocked(caster_cell: int, target_cell: int) -> None:
    """Mémoriser (map, lanceur, cible) comme LdV refusée par le serveur, et persister.

    À n'appeler QUE quand notre propre calcul de LdV disait « dégagée » : c'est alors
    une divergence d'algorithme sur la géométrie terrain (statique), donc valable pour
    toutes les parties futures sur cette map. On ne pollue pas le cache avec des
    blocages dynamiques d'entités (que notre algo détecte déjà).
    """
    mid = _current_map_id()
    if mid is None:
        return
    s = _los_learned_blocked.setdefault(mid, set())
    if (caster_cell, target_cell) not in s:
        s.add((caster_cell, target_cell))
        logger.info(
            "[combat] LdV serveur apprise (map %d) : %d→%d refusée → mémorisée "
            "(divergence terrain, plus de réémission)",
            mid, caster_cell, target_cell,
        )
        _save_los_blocked_overrides()


_load_los_blocked_overrides()


# --- Capture ground-truth des rejets LdV (pour reverse-engineer l'algo serveur 3D) ---
# Le client Dofus calcule la LdV en 3D (docs/reference_client_los/checkView.as) :
# ligne de visée en hauteur, une cellule bloque si sa hauteur (getCellHeight, +1.5 par
# sprite/décoration) dépasse la ligne. Notre algo est 2D → divergence. Pour porter l'algo
# exact il faut des cas réels complets. À chaque Im 1174, on dumpe TOUT l'état dans
# logs/los_capture.jsonl : map, lanceur, cible, positions + hauteurs de toutes les
# entités, et hauteurs de sol des cellules. Analysable hors-ligne pour valider un port.
_LOS_CAPTURE_PATH = _os.path.join(
    _os.path.dirname(_os.path.dirname(_os.path.abspath(__file__))),
    "logs", "los_capture.jsonl",
)


def _capture_los(caster_cell: int, target_cell: int, spell_id: int, blocked: bool) -> None:
    """Écrire une ligne JSON avec l'état complet d'un tir single-target à LdV.

    blocked=True : le serveur a refusé (Im 1174). blocked=False : accepté (LdV dégagée).
    Les deux sont nécessaires pour porter l'algo 3D : contraster les lignes bloquées et
    dégagées permet d'identifier quelles décorations (layerObject) donnent de la hauteur.
    """
    try:
        mid = _current_map_id()
        entities = {
            eid: cell
            for eid, cell in _state.current.combat_entity_cells.items()
            if cell is not None and cell >= 0
        }
        heights = None
        try:
            from bot.mapdata import load_map as _load_map
            mi = _load_map(mid) if mid is not None else None
            if mi is not None:
                # hauteur de sol + praticabilité des cellules occupées + caster/cible
                cells_of_interest = set(entities.values()) | {caster_cell, target_cell}
                heights = {
                    str(cc): [mi.get_cell_height(cc),
                              1 if cc in mi.blocked_cells else 0,
                              0 if (0 <= cc < len(mi.cells) and mi.cells[cc].line_of_sight) else 1]
                    for cc in cells_of_interest
                }
        except Exception:
            pass
        rec = {
            "map": mid,
            "caster": caster_cell,
            "target": target_cell,
            "spell": spell_id,
            "blocked": 1 if blocked else 0,
            "entities": entities,   # eid -> cell (bloqueurs potentiels)
            "cells": heights,       # cell -> [ground_level, blocked(mvt0), opaque(los=0)]
        }
        _os.makedirs(_os.path.dirname(_LOS_CAPTURE_PATH), exist_ok=True)
        with open(_LOS_CAPTURE_PATH, "a", encoding="utf-8") as fh:
            fh.write(_json.dumps(rec, ensure_ascii=False) + "\n")
    except Exception:
        pass


def _get_spell_info(spell_id: int, class_id: int | None = None) -> tuple[int, int, int, bool, int, bool]:
    """Retourner (cost_pa, range_min, range_max, needs_los, cooldown, empty_cell) d'un sort.

    empty_cell : True si le sort PEUT être lancé sur une case vide. False (ex:
    Flèche Explosive) → la case d'impact doit être occupée par une entité, sinon
    le serveur rejette avec Im 1172 (case d'impact invalide).

    class_id : classe du lanceur — le niveau du sort est résolu pour cette classe
    (équipe mixte). None → repli sur les sorts du perso principal.

    La portée est corrigée par la valeur apprise du serveur si disponible
    (_observed_spell_range) : le spells.xml local diverge des stats du serveur privé.
    """
    from data.spell_data import get_spell_level_info

    spell_level = _state.get_class_spell_level(class_id, spell_id)

    info = get_spell_level_info(spell_id, spell_level)
    if info is not None:
        # Portée XML brute : la correction serveur se fait désormais par le plafond
        # appris (_range_cap / _capped_po_bonus), robuste au PO/buff faux — cf. loop.
        return (info.cost_pa, info.range_min, info.range_max,
                info.vision_line, info.cooldown, info.empty_cell)

    return (4, 1, 7, True, 0, False)


# Cooldown tracking par entité par combat.
# Clé externe : entity_id. Clé interne : spell_id → tours restants.
# Décrémenté de 1 à chaque début de tour ; mis à `cooldown` après un lancer.
# Reset complet au début de chaque combat (dans fight_group).
_spell_cooldowns: dict[str, dict[int, int]] = {}


# Buffs PO actifs (sorts comme Tir Eloigné qui ajoutent temporairement de la portée).
# Clé : entity_id. Valeur : dict spell_id → (bonus_po, tours_restants).
# Décrémenté à chaque début de tour de l'entité ; supprimé à expiration.
# Reset complet au début de chaque combat.
_active_po_buffs: dict[str, dict[int, tuple[int, int]]] = {}

# Baseline PO observée au premier tour de chaque entité dans le combat.
# Utilisée pour distinguer le bonus reflété côté serveur (As) du bonus local
# (non remonté pour les héros). Reset au début de chaque combat.
_po_baselines: dict[str, int] = {}

# Sorts auto-buff PO connus (donnent +PO à soi-même pour N tours).
# spell_id → (bonus_po, durée_tours).
# Tir Eloigné (172) : +6 PO pour 3 tours, cooldown 5.
PO_BUFF_SPELLS: dict[int, tuple[int, int]] = {
    172: (6, 3),
}


def _reset_cooldowns() -> None:
    """Réinitialiser tous les cooldowns (début d'un nouveau combat)."""
    _spell_cooldowns.clear()
    _active_po_buffs.clear()
    _po_baselines.clear()


def _tick_cooldowns(entity_id: str) -> None:
    """Décrémenter les cooldowns d'une entité de 1 (début de son tour)."""
    cds = _spell_cooldowns.get(entity_id)
    if cds:
        expired = []
        for sid in cds:
            cds[sid] = max(0, cds[sid] - 1)
            if cds[sid] <= 0:
                expired.append(sid)
        for sid in expired:
            del cds[sid]

    # Tick des buffs PO actifs pour cette entité (même cadence que les cooldowns).
    buffs = _active_po_buffs.get(entity_id)
    if buffs:
        expired_b = []
        for sid, (bonus, turns) in buffs.items():
            turns -= 1
            if turns <= 0:
                expired_b.append(sid)
            else:
                buffs[sid] = (bonus, turns)
        for sid in expired_b:
            del buffs[sid]


def _set_cooldown(entity_id: str, spell_id: int, cooldown: int) -> None:
    """Enregistrer le cooldown d'un sort après l'avoir lancé."""
    if cooldown <= 0:
        return
    _spell_cooldowns.setdefault(entity_id, {})[spell_id] = cooldown


def _clear_cooldown(entity_id: str, spell_id: int) -> None:
    """Retirer le cooldown d'un sort (ex: cast annulé par le serveur)."""
    cds = _spell_cooldowns.get(entity_id)
    if cds and spell_id in cds:
        del cds[spell_id]


def _is_on_cooldown(entity_id: str, spell_id: int) -> bool:
    """True si le sort est encore en cooldown pour cette entité."""
    return _spell_cooldowns.get(entity_id, {}).get(spell_id, 0) > 0


def _register_po_buff(entity_id: str, spell_id: int) -> None:
    """Enregistrer un buff PO local après un cast réussi (si le sort en accorde un)."""
    info = PO_BUFF_SPELLS.get(spell_id)
    if info is None:
        return
    bonus, duration = info
    # Le tick de début de tour décrémente à 0 pour expirer : avec duration=3,
    # le buff reste actif sur le tour de cast + 2 tours suivants (idem cooldown).
    _active_po_buffs.setdefault(entity_id, {})[spell_id] = (bonus, duration)


def _get_local_po_buff(entity_id: str) -> int:
    """Somme des bonus PO locaux actifs pour cette entité."""
    buffs = _active_po_buffs.get(entity_id)
    if not buffs:
        return 0
    return sum(bonus for bonus, _ in buffs.values())


def _get_effective_po_bonus(entity_id: str) -> int:
    """Retourner le bonus PO effectif pour le ciblage.

    Combine la valeur serveur (As, qui inclut le buff pour le perso principal)
    et nos buffs locaux (Tir Eloigné). Pour les héros, les As de mid-combat ne
    sont pas remontés → le buff serveur reste à la baseline, donc on applique
    le buff local. Pour le perso principal, le serveur reflète déjà le buff →
    on prend le max pour éviter le double comptage.
    """
    server_po = get_entity_po(entity_id)
    baseline = _po_baselines.setdefault(entity_id, server_po)
    server_buff = max(0, server_po - baseline)
    local_buff = _get_local_po_buff(entity_id)
    return baseline + max(server_buff, local_buff)


def _get_attack_spell_sequence(caster_id: str) -> list[tuple[int, int, str, int, int, int, bool, int, bool]]:
    """Retourner la séquence de sorts d'attaque configurée pour la CLASSE du lanceur.

    Chaque élément :
        (spell_id, cast_count, target, cost_pa, range_min, range_max, needs_los, cooldown, empty_cell)
    target : "enemy", "self", "aoe2" ou "aoe3".

    Équipe mixte : chaque combattant joue la séquence de sa classe (attack_spell_sequences).
    Si aucune séquence n'est configurée pour la classe du lanceur, retourne une liste vide
    (le lanceur passe son tour) — il faut configurer le sous-onglet Sorts de cette classe.
    """
    class_id = _state.get_entity_class_id(caster_id)
    sequence = _state.current.attack_spell_sequences.get(class_id, []) if class_id is not None else []

    result = []
    for spell_id, count, target in sequence:
        cost_pa, rmin, rmax, los, cd, empty = _get_spell_info(spell_id, class_id)
        result.append((spell_id, count, target, cost_pa, rmin, rmax, los, cd, empty))

    return result


# ---------------------------------------------------------------------------
# Actions de combat primitives
# ---------------------------------------------------------------------------

async def _spectator_slowdown() -> None:
    """Délai humain aléatoire AVANT chaque action de combat (sort/déplacement/fin de tour).

    Régimes, dans l'ordre :
      - Gel antibot (saisie du .code) → on attend la levée du gel avant toute action.
      - Spectateur présent (Im 036) → délai LONG (combat_spectator_delay_*), amplifié
        en mode humain (HUMAN_SPECTATOR_FACTOR), jusqu'à la fin du combat observé.
      - Mode humain / farming → même délai anti-rafale COURT (combat_min_action_delay_*).
        La cadence de base est désormais identique entre les deux modes ; la protection
        du mode humain vient des irrégularités ponctuelles (pauses, misclick, AFK) gérées
        ailleurs (fight_group / combat_farm_loop), pas d'une lenteur systématique.
      - Mode speed → aucun délai (⚠ risque de ban).
    """
    await _wait_antibot_freeze()
    mode = _discretion_mode()

    if is_spectator_present():
        lo = max(0, _state.current.combat_spectator_delay_min_ms)
        hi = max(lo, _state.current.combat_spectator_delay_max_ms)
        factor = HUMAN_SPECTATOR_FACTOR if mode == DISCRETION_HUMAN else 1.0
        delay = random.uniform(lo, hi) * factor / 1000.0
        logger.info("[combat] Spectateur présent → délai %.1fs avant l'action", delay)
        await asyncio.sleep(delay)
        return

    if mode == DISCRETION_SPEED:
        return

    lo = max(0, _state.current.combat_min_action_delay_min_ms)
    hi = max(lo, _state.current.combat_min_action_delay_max_ms)
    if hi > 0:
        await asyncio.sleep(random.uniform(lo, hi) / 1000.0)


async def cast_spell(spell_id: int, target_cell: int) -> bool:
    """Lancer un sort sur target_cell (GA300, chiffré automatiquement)."""
    await _spectator_slowdown()
    logger.info("[combat] Sort %d → cell %d", spell_id, target_cell)
    return await channel.send(f"GA300{spell_id};{target_cell}\n")


async def end_turn() -> bool:
    """Terminer notre tour (Gt).

    Attend que toutes les actions en attente (GAF) soient ack par le Flash client
    (GKK) avant d'envoyer Gt. Le serveur refuse Gt si des actions sont pendantes.
    """
    await _spectator_slowdown()
    await wait_actions_clear(timeout=5.0)
    logger.info("[combat] Fin du tour (Gt)")
    return await channel.send("Gt\n")


# ---------------------------------------------------------------------------
# Gestion d'un tour de combat (pour un Crâ générique)
# ---------------------------------------------------------------------------

def _get_occupied_cells(exclude_id: str) -> set[int]:
    """Cellules occupées par toutes les entités sauf exclude_id (alliés + monstres)."""
    return {
        cell for eid, cell in _state.current.combat_entity_cells.items()
        if eid != exclude_id and cell >= 0
    }


def _find_closest_monster(
    caster_cell: int,
    monster_cells: list[int],
) -> int | None:
    """Trouver le monstre vivant le plus proche (distance BFS)."""
    if not monster_cells:
        return None
    return min(monster_cells, key=lambda mc: _game_distance(caster_cell, mc))


def _can_cast_on(
    caster_cell: int,
    target_cell: int,
    spell_range_min: int,
    spell_range_max: int,
    po_bonus: int,
    needs_los: bool,
    los_blockers: set[int] | None,
    entity_cells: set[int] | None = None,
) -> bool:
    """Vérifier si le lanceur peut atteindre la cible avec le sort."""
    dist = _game_distance(caster_cell, target_cell)
    effective_max = spell_range_max + po_bonus
    if dist < spell_range_min or dist > effective_max:
        return False
    if needs_los:
        # LdV bloquée par le terrain
        if los_blockers and not has_line_of_sight(caster_cell, target_cell, los_blockers):
            return False
        # LdV bloquée par les entités (joueurs, monstres, invocations)
        if entity_cells and not has_line_of_sight(caster_cell, target_cell, entity_cells):
            return False
    return True


def _find_best_move_for_attack(
    caster_cell: int,
    pm: int,
    monster_cells: list[int],
    spell_range_min: int,
    spell_range_max: int,
    po_bonus: int,
    needs_los: bool,
    blocked_cells: set[int],
    los_blockers: set[int] | None,
    occupied_cells: set[int],
) -> int | None:
    """Trouver la meilleure case de déplacement (mode rush CàC).

    Se rapprocher au maximum du monstre le plus proche pour attaquer.
    Fallback : coller le monstre au corps à corps.
    """
    move_blocked = blocked_cells | occupied_cells
    reachable = _movement_reachable_cells(caster_cell, pm, move_blocked)

    if not reachable:
        return None

    effective_max = spell_range_max + po_bonus

    entity_blockers = occupied_cells - set(monster_cells)

    best_dest: int | None = None
    best_monster_dist: int = 9999
    best_move_dist: int = 9999

    for dest in reachable:
        for mc in monster_cells:
            dist_to_monster = _game_distance(dest, mc)
            if dist_to_monster < spell_range_min or dist_to_monster > effective_max:
                continue
            if needs_los and los_blockers and not has_line_of_sight(dest, mc, los_blockers):
                continue
            if needs_los and entity_blockers and not has_line_of_sight(dest, mc, entity_blockers):
                continue

            move_dist = _game_distance(caster_cell, dest)
            if (dist_to_monster < best_monster_dist
                    or (dist_to_monster == best_monster_dist and move_dist < best_move_dist)):
                best_monster_dist = dist_to_monster
                best_move_dist = move_dist
                best_dest = dest

    if best_dest is not None:
        return best_dest

    closest_monster = _find_closest_monster(caster_cell, monster_cells)
    if closest_monster is None:
        return None

    best_approach: int | None = None
    best_approach_dist: int = 9999
    for dest in reachable:
        dist = _game_distance(dest, closest_monster)
        if dist < best_approach_dist:
            best_approach_dist = dist
            best_approach = dest

    current_dist = _game_distance(caster_cell, closest_monster)
    if best_approach is not None and best_approach_dist < current_dist:
        return best_approach

    return None


# ---------------------------------------------------------------------------
# Déplacement en mode distance (pour classes distance comme Crâ)
# ---------------------------------------------------------------------------

def _get_ally_cells(caster_id: str) -> set[int]:
    """Cellules des alliés (excluant le caster et les monstres)."""
    return {
        cell for eid, cell in _state.current.combat_entity_cells.items()
        if eid != caster_id
        and cell >= 0
        and not eid.startswith("-")
    }


def _proximity_monster_cells() -> list[int]:
    """Cellules de TOUS les monstres vivants (invocations incluses).

    Le ciblage ignore les invocations (cf. ``get_targetable_monster_cells``), mais
    pour la PROXIMITÉ / l'anti-tacle il faut les considérer : une invocation tacle
    et bloque autant qu'un monstre d'origine. Sert aux pénalités d'adjacence et aux
    distances de sécurité du scoring de déplacement, indépendamment de la cible.
    """
    return get_live_monster_cells()


def _find_best_aoe_impact(
    caster_cell: int,
    caster_id: str,
    monster_cells: list[int],
    aoe_radius: int,
    spell_range_min: int,
    spell_range_max: int,
    po_bonus: int,
    needs_los: bool,
    los_blockers: set[int] | None,
    width: int = MAP_WIDTH,
    empty_cell: bool = False,
    excluded_cells: set[int] | None = None,
    blocked_cells: set[int] | None = None,
    prefer_close: bool = False,
) -> int | None:
    """Trouver la case d'impact AOE optimale (max monstres touchés, zéro friendly fire).

    prefer_close : si True, privilégie l'impact le PLUS PROCHE (puis le plus de hits)
    au lieu du plus de hits. Activé après un rejet LdV (Im 1174) du serveur ce tour :
    c'est le signe que notre calcul de LdV longue portée est peu fiable sur cette
    géométrie, donc on retombe sur des tirs courts (LdV beaucoup plus sûre).

    Énumère toutes les cases à portée du sort (PO entre min et max+po_bonus),
    rejette celles dont la zone AOE contient un allié (y compris le lanceur),
    puis retourne la case qui touche le plus de monstres. Égalité : préférer
    la case la plus proche du lanceur (LdV plus stable).

    Vérifie la LdV terrain et la LdV entités. La cellule d'impact elle-même
    n'est pas considérée comme bloqueur (on peut tirer sur la case d'une entité).

    Une case d'impact peut être VIDE : pour un sort AOE (Flèche Explosive), viser
    une case vide entre plusieurs monstres maximise souvent les touches.

    blocked_cells : cases d'obstacle/mur du terrain — NON ciblables (le serveur
    rejette Im 1172 « case d'impact invalide »). Exclues des candidats.
    empty_cell : sémantique XML EMPTY_CELL — si True (invocations, glyphes,
    pièges, téléportation), la case d'impact DOIT être libre de toute entité.
    Pour les sorts d'attaque (EMPTY_CELL=FALSE) ce filtre ne s'applique pas.
    excluded_cells : cases déjà rejetées ce tour (LdV/portée/invalide) à ne pas
    reproposer, même depuis une autre position de lanceur.
    """
    if not monster_cells or aoe_radius < 0:
        return None

    effective_max = spell_range_max + po_bonus
    if effective_max < spell_range_min:
        return None

    # Toutes les cases protégées du friendly fire : alliés + le lanceur lui-même.
    protected_cells = _get_ally_cells(caster_id) | {caster_cell}

    # Bloqueurs entités : alliés + monstres (sauf le lanceur).
    entity_blockers = {
        c for eid, c in _state.current.combat_entity_cells.items()
        if eid != caster_id and c >= 0
    }

    impacts = _cells_in_po_range(
        caster_cell, effective_max, width, min_po=max(0, spell_range_min),
    )

    monster_set = set(monster_cells)
    # (−hits, occupé-par-monstre, dist, cell) — préférer + de hits, puis cible
    # occupée (évite Im 1172 quand EMPTY_CELL=FALSE), puis proche du lanceur.
    best: tuple[int, int, int, int] | None = None

    for impact in impacts:
        # Case déjà rejetée par le serveur ce tour (LdV/portée/invalide)
        if excluded_cells and impact in excluded_cells:
            continue

        # Case d'obstacle/mur : non ciblable → Im 1172. (La case d'un monstre
        # n'est pas dans blocked_cells, on peut donc toujours tirer dessus.)
        if blocked_cells and impact in blocked_cells:
            continue

        # Sorts EMPTY_CELL=TRUE (invocation/glyphe/piège/téléport) : la case
        # d'impact doit être libre de toute entité. Sans effet sur l'attaque AOE.
        if empty_cell and impact in entity_blockers:
            continue

        hits = _aoe_hits(impact, monster_cells, aoe_radius, width)
        if hits == 0:
            continue

        # Friendly fire : exclure toute case dont l'AOE touche un allié (ou nous-mêmes)
        if _aoe_hits_in(impact, protected_cells, aoe_radius, width) > 0:
            continue

        # LdV terrain
        if needs_los and los_blockers and not has_line_of_sight(
            caster_cell, impact, los_blockers, width
        ):
            continue

        # LdV entités (la case d'impact peut être occupée — pas un bloqueur)
        if needs_los and entity_blockers:
            blockers = entity_blockers - {impact}
            if blockers and not has_line_of_sight(caster_cell, impact, blockers, width):
                continue

        on_monster = 0 if impact in monster_set else 1
        dist = _po_distance(caster_cell, impact, width)
        # Tri normal : max hits, puis case occupée, puis proche. En mode prefer_close
        # (après un Im 1174) : proche d'abord (tir fiable), puis max hits.
        if prefer_close:
            key = (dist, -hits, on_monster, impact)
        else:
            key = (-hits, on_monster, dist, impact)
        if best is None or key < best:
            best = key

    return best[3] if best is not None else None


def _best_aoe_hits_from(
    caster_cell: int,
    caster_id: str,
    monster_cells: list[int],
    aoe_radius: int,
    spell_range_min: int,
    spell_range_max: int,
    po_bonus: int,
    needs_los: bool,
    los_blockers: set[int] | None,
    width: int = MAP_WIDTH,
    empty_cell: bool = False,
    excluded_cells: set[int] | None = None,
    blocked_cells: set[int] | None = None,
) -> int:
    """Nombre maximum de monstres touchés par l'AOE depuis caster_cell (0 si impossible)."""
    impact = _find_best_aoe_impact(
        caster_cell, caster_id, monster_cells, aoe_radius,
        spell_range_min, spell_range_max, po_bonus, needs_los,
        los_blockers, width, empty_cell, excluded_cells, blocked_cells,
    )
    if impact is None:
        return 0
    return _aoe_hits(impact, monster_cells, aoe_radius, width)


def _find_best_aoe_caster_position(
    caster_cell: int,
    caster_id: str,
    pm: int,
    monster_cells: list[int],
    aoe_radius: int,
    spell_range_min: int,
    spell_range_max: int,
    po_bonus: int,
    needs_los: bool,
    blocked_cells: set[int],
    los_blockers: set[int] | None,
    occupied_cells: set[int],
    width: int = MAP_WIDTH,
    empty_cell: bool = False,
    excluded_cells: set[int] | None = None,
) -> tuple[int, int] | None:
    """Trouver la case d'où on peut taper le MAXIMUM de monstres en AOE.

    Parcourt toutes les cases atteignables (≤ PM) + la case actuelle,
    calcule pour chacune le meilleur impact AOE, et retourne (dest, hits).

    Sécurité : on n'envisage pas de se déplacer dans une case nouvellement
    menacée (à portée PM + attaque d'un monstre) si on ne l'est pas déjà —
    SAUF si on ne touche aucun monstre depuis notre case actuelle : dans ce cas
    il faut bien s'avancer dans la mêlée pour pouvoir taper (ne pas rester planté).

    Returns (dest_cell, hits) ou None si aucune position ne touche d'ennemi.
    """
    if aoe_radius < 0 or not monster_cells:
        return None

    move_blocked = blocked_cells | occupied_cells
    reachable = _movement_reachable_cells(caster_cell, pm, move_blocked, width)
    reachable.add(caster_cell)  # rester sur place est aussi une option

    currently_threatened = _is_threatened_by_monsters(caster_cell, width)
    # Proximité/anti-tacle : tous les monstres vivants (invocations comprises).
    prox_cells = _proximity_monster_cells()

    # Hits depuis la case actuelle : si 0, on s'autorise à entrer en zone menacée
    # pour pouvoir attaquer (sinon le Crâ reste planté hors de portée).
    hits_here = _best_aoe_hits_from(
        caster_cell, caster_id, monster_cells, aoe_radius,
        spell_range_min, spell_range_max, po_bonus, needs_los,
        los_blockers, width, empty_cell, excluded_cells, blocked_cells,
    )
    allow_threatened = currently_threatened or hits_here == 0

    # (-hits, adjacent?, threat_penalty, move_dist, cell)
    best: tuple[int, int, int, int, int] | None = None

    for dest in reachable:
        # Si on n'est pas menacé (et qu'on touche déjà des monstres), ne pas se
        # déplacer dans une case qui le devient.
        dest_threatened = _is_threatened_by_monsters(dest, width)
        if dest != caster_cell and not allow_threatened and dest_threatened:
            continue

        hits = _best_aoe_hits_from(
            dest, caster_id, monster_cells, aoe_radius,
            spell_range_min, spell_range_max, po_bonus, needs_los,
            los_blockers, width, empty_cell, excluded_cells, blocked_cells,
        )
        if hits == 0:
            continue

        # À hits égaux : éviter le corps à corps (adjacence = tacle), puis préférer
        # la case la plus sûre, puis la plus proche.
        is_adj = 1 if _min_monster_distance(dest, prox_cells) <= 1 else 0
        threat_score = int(_max_monster_threat_at(dest, width))
        move_dist = _po_distance(caster_cell, dest, width)
        key = (-hits, is_adj, threat_score, move_dist, dest)
        if best is None or key < best:
            best = key

    if best is None:
        return None
    return (best[4], -best[0])


def _get_monster_threat_range(monster_id: str) -> int:
    """Portée de menace d'un monstre = max(PM observé) + MONSTER_ATTACK_RANGE.

    On utilise le PM MAX observé (high-watermark via combat_entity_max_pm) plutôt
    que le PM courant : un monstre qui a joué son tour avant nous peut avoir
    `combat_entity_pm` à 0, mais il récupérera son plein PM au prochain tour.
    Ignorer cela mène à sous-estimer la menace et à finir le tour en zone rouge.

    Un monstre avec PM max=4 et attaque à 2 cases menace toute entité à
    distance ≤ 4+2 = 6 : il peut marcher 4 puis frapper à 2.
    Fallback : PM courant si jamais vu de max, sinon 4 (valeur conservative).
    """
    max_pm = _state.current.combat_entity_max_pm.get(monster_id, 0)
    if max_pm <= 0:
        max_pm = _state.current.combat_entity_pm.get(monster_id, 4)
    return max_pm + MONSTER_ATTACK_RANGE


def _is_threatened_by_monsters(
    cell: int,
    width: int = MAP_WIDTH,
) -> bool:
    """True si la cellule est dans la zone de menace d'au moins un monstre.

    Zone de menace = distance <= PM_monstre + 1 (le monstre peut marcher puis taper).
    """
    for mid in _state.current.combat_live_monsters:
        mc = _state.current.combat_entity_cells.get(mid)
        if mc is None or mc < 0:
            continue
        threat_range = _get_monster_threat_range(mid)
        if _po_distance(cell, mc, width) <= threat_range:
            return True
    return False


def _max_monster_threat_at(
    cell: int,
    width: int = MAP_WIDTH,
) -> float:
    """Indice de menace : plus la valeur est haute, plus la case est dangereuse.

    Pour chaque monstre, menace = max(0, threat_range - distance).
    Un monstre à 4 PM et dist 3 → menace = (4+1) - 3 = 2.
    """
    total = 0.0
    for mid in _state.current.combat_live_monsters:
        mc = _state.current.combat_entity_cells.get(mid)
        if mc is None or mc < 0:
            continue
        threat_range = _get_monster_threat_range(mid)
        dist = _po_distance(cell, mc, width)
        if dist <= threat_range:
            total += (threat_range - dist + 1)
    return total


def _is_adjacent(cell_a: int, cell_b: int, width: int = MAP_WIDTH) -> bool:
    """True si les deux cellules sont adjacentes (distance PO = 1)."""
    return _po_distance(cell_a, cell_b, width) == 1


def _min_monster_distance(cell: int, monster_cells: list[int]) -> int:
    """Distance PO au monstre le plus proche."""
    if not monster_cells:
        return 9999
    return min(_po_distance(cell, mc) for mc in monster_cells)


def _is_diagonal_angle(cell: int, target_cell: int, width: int = MAP_WIDTH) -> bool:
    """True si cell est en diagonale 'pure' de target_cell.

    En coordonnées diagonales (a, b), les cellules sur le même axe cardinal
    qu'une cible (a1==a2 ou b1==b2) sont vulnérables aux entités intermédiaires :
    un allié collé au monstre bloque facilement la LdV des tireurs alignés.
    Une diagonale pure (les deux composantes diffèrent) a une LdV plus robuste —
    le supercover passe par un coin et accepte plus largement.
    """
    if cell == target_cell:
        return False
    a1, b1 = _cell_to_diag(cell, width)
    a2, b2 = _cell_to_diag(target_cell, width)
    return a1 != a2 and b1 != b2


def _is_monster_injured(monster_id: str) -> bool:
    """True si le monstre a perdu ≥ INJURED_HP_THRESHOLD (10%) de ses HP max.

    On focus en priorité les monstres blessés pour les achever plus vite plutôt
    que de répartir les dégâts. Le HP max provient du field 7 de GTM.
    """
    max_hp = _state.current.combat_entity_max_hp.get(monster_id, 0)
    if max_hp <= 0:
        return False
    cur_hp = _state.current.combat_entity_hp.get(monster_id, max_hp)
    return cur_hp <= max_hp * (1.0 - INJURED_HP_THRESHOLD)


def _can_attack_from(
    dest: int,
    monster_cells: list[int],
    spell_range_min: int,
    spell_range_max: int,
    po_bonus: int,
    needs_los: bool,
    los_blockers: set[int] | None,
    entity_blockers: set[int],
    width: int = MAP_WIDTH,
) -> bool:
    """True si depuis dest on peut taper au moins un monstre (portée + LdV complète).

    Vérifie la portée, la LdV terrain et la LdV entités (alliés + autres monstres).
    Pour chaque monstre testé, son propre cell est retiré de entity_blockers
    (on tire dessus, il ne se bloque pas lui-même).
    """
    effective_max = spell_range_max + po_bonus
    for mc in monster_cells:
        d = _po_distance(dest, mc, width)
        if d < spell_range_min or d > effective_max:
            continue
        if needs_los and los_blockers and not has_line_of_sight(dest, mc, los_blockers, width):
            continue
        if needs_los and entity_blockers:
            blockers_no_target = entity_blockers - {mc}
            if blockers_no_target and not has_line_of_sight(dest, mc, blockers_no_target, width):
                continue
        return True
    return False


def _primary_engageable_this_turn(
    caster_id: str,
    caster_cell: int,
    pm: int,
    po_bonus: int,
    spell_sequence: list,
    blocked_cells: set[int],
    los_blockers: set[int] | None,
    width: int = MAP_WIDTH,
) -> bool:
    """True si un monstre d'ORIGINE (hors invocations) est jouable ce tour.

    « Jouable » = il existe une case atteignable (≤ pm, case actuelle comprise)
    d'où un sort d'attaque de la séquence peut le toucher (portée + LdV terrain
    + LdV entités, les invocations comptant comme bloqueurs). Sert à décider s'il
    faut basculer le ciblage sur les invocations : si les invocations bloquent
    totalement l'accès aux monstres d'origine (LdV/tacle), on doit pouvoir les
    taper plutôt que rester coincé.

    Renvoie True (= pas de bascule) dès qu'aucune invocation n'est présente ou
    qu'il ne reste plus aucun monstre d'origine vivant (le ciblage gère déjà ces
    cas via get_targetable_*).
    """
    primary = get_targetable_monster_cells()
    if not primary:
        return True  # plus de primaire → get_targetable_* fait déjà le fallback
    if len(get_live_monster_cells()) == len(primary):
        return True  # aucune invocation en jeu

    occupied = _get_occupied_cells(caster_id)
    move_blocked = blocked_cells | occupied
    reachable = _movement_reachable_cells(caster_cell, pm, move_blocked, width)
    reachable.add(caster_cell)
    # Les invocations (et alliés) bloquent la LdV vers un monstre d'origine.
    entity_blockers = occupied - set(primary)

    for entry in spell_sequence:
        target = entry[2]
        if target not in ("enemy", "aoe2", "aoe3"):
            continue
        range_min, range_max, needs_los = entry[4], entry[5], entry[6]
        for dest in reachable:
            if _can_attack_from(
                dest, primary, range_min, range_max, po_bonus,
                needs_los, los_blockers, entity_blockers, width,
            ):
                return True
    return False


def _count_ally_los_blocked(
    dest: int,
    ally_cells: set[int],
    monster_cells: list[int],
    los_blockers: set[int] | None,
    width: int = MAP_WIDTH,
) -> int:
    """Nombre d'alliés dont la LdV vers un monstre serait bloquée par dest."""
    blocked_count = 0
    blockers_with_dest = (los_blockers or set()) | {dest}
    for ally_cell in ally_cells:
        if ally_cell == dest:
            continue
        for mc in monster_cells:
            if not has_line_of_sight(ally_cell, mc, blockers_with_dest, width):
                if los_blockers is None or has_line_of_sight(ally_cell, mc, los_blockers, width):
                    blocked_count += 1
                    break
    return blocked_count


def _find_best_move_distance(
    caster_cell: int,
    caster_id: str,
    pm: int,
    monster_cells: list[int],
    spell_range_min: int,
    spell_range_max: int,
    po_bonus: int,
    needs_los: bool,
    blocked_cells: set[int],
    los_blockers: set[int] | None,
    occupied_cells: set[int],
    width: int = MAP_WIDTH,
    is_aoe: bool = False,
) -> int | None:
    """Trouver la meilleure case de déplacement en mode distance.

    Priorités :
      1. Trouver une case d'où on peut attaquer un monstre
      2. Maintenir une distance de sécurité (éviter le CàC)
      3. Ne pas bloquer les LdV des alliés
      4. Si déjà à portée mais trop près, reculer
      5. Si pas à portée, se rapprocher du minimum nécessaire
    """
    move_blocked = blocked_cells | occupied_cells
    reachable = _movement_reachable_cells(caster_cell, pm, move_blocked, width)

    if not reachable:
        return None

    effective_max = spell_range_max + po_bonus
    entity_blockers = occupied_cells - set(monster_cells)
    ally_cells = _get_ally_cells(caster_id)
    # Proximité/anti-tacle : TOUS les monstres vivants (invocations comprises),
    # alors que monster_cells (ciblage) peut exclure les invocations.
    prox_cells = _proximity_monster_cells()

    closest_monster = _find_closest_monster(caster_cell, monster_cells)
    if closest_monster is None:
        return None

    current_dist_to_closest = _po_distance(caster_cell, closest_monster, width)

    # Distance de sécurité : rester à au moins 4 cases des monstres idéalement
    # (hors de portée des sorts CàC et mêlée à 1-3 cases des monstres)
    safe_min_dist = max(4, spell_range_min + 1)

    # --- Phase 1 : cases d'où on peut attaquer, avec scoring intelligent ---
    attack_candidates: list[tuple[float, int]] = []

    for dest in reachable:
        can_attack_any = False
        for mc in monster_cells:
            dist_to_monster = _po_distance(dest, mc, width)
            if dist_to_monster < spell_range_min or dist_to_monster > effective_max:
                continue
            if needs_los and los_blockers and not has_line_of_sight(dest, mc, los_blockers, width):
                continue
            if needs_los and entity_blockers and not has_line_of_sight(dest, mc, entity_blockers, width):
                continue
            can_attack_any = True
            break

        if not can_attack_any:
            continue

        min_m_dist = _min_monster_distance(dest, prox_cells)

        # Pénalité très forte si adjacent à un monstre (dist=1, CàC) — invocations
        # comprises, pour ne pas se faire tacler en cherchant un angle de tir.
        is_cac = min_m_dist <= 1
        cac_penalty = CAC_PROXIMITY_PENALTY if is_cac else 0.0

        # Pénalité basée sur la menace réelle des monstres (PM + portée CàC)
        threat = _max_monster_threat_at(dest, width)
        threat_penalty = threat * 80.0

        # Pénalité progressive si en dessous de la distance de sécurité
        proximity_penalty = max(0, safe_min_dist - min_m_dist) * 100.0

        # Bonus pour maximiser la distance (préférer rester loin)
        distance_bonus = -min_m_dist * 15.0

        los_block_penalty = _count_ally_los_blocked(
            dest, ally_cells, monster_cells, los_blockers, width
        ) * 200.0

        ally_crowd_penalty = 0.0
        for ac in ally_cells:
            if _po_distance(dest, ac, width) <= 1:
                ally_crowd_penalty += 80.0

        move_cost = _po_distance(caster_cell, dest, width) * 3.0

        score = (cac_penalty + threat_penalty + proximity_penalty
                 + distance_bonus + los_block_penalty + ally_crowd_penalty + move_cost)

        attack_candidates.append((score, dest))

    if attack_candidates:
        attack_candidates.sort(key=lambda x: x[0])
        best_score, best_dest = attack_candidates[0]
        logger.debug(
            "[combat][distance] Meilleure case d'attaque : cell %d (score=%.0f)",
            best_dest, best_score,
        )
        return best_dest

    # --- Phase 2 : pas de case d'attaque → se rapprocher intelligemment ---

    dist_deficit = current_dist_to_closest - effective_max
    # Pour un sort AOE, le positionnement « dans la portée mais sans LdV » est déjà
    # géré par _find_best_aoe_caster_position (qui raisonne sur les cases d'impact,
    # pas la LdV directe vers le monstre). Le déplacement latéral mono-cible ci-dessous
    # ne ferait que gaspiller du PM sans débloquer de tir AOE — on le saute.
    if dist_deficit <= 0 and not is_aoe:
        # On est à portée mais pas de LdV → déplacement latéral (chercher angle de tir)
        lateral_candidates: list[tuple[float, int]] = []
        for dest in reachable:
            min_m_dist = _min_monster_distance(dest, prox_cells)

            can_atk = _can_attack_from(
                dest, monster_cells, spell_range_min, spell_range_max,
                po_bonus, needs_los, los_blockers, entity_blockers, width,
            )
            is_threatened = _is_threatened_by_monsters(dest, width)

            # Si menacé et qu'on ne peut pas attaquer depuis dest, autant rester safe :
            # on saute. Mais si on peut attaquer, l'attaque vaut plus que la sécurité.
            if is_threatened and not can_atk:
                continue

            range_ok = spell_range_min <= min_m_dist <= effective_max
            range_bonus = -100.0 if range_ok else 0.0

            # Pénalité très forte si adjacent à un monstre (CàC subi = tacle).
            # Sans ça, chercher un angle de tir collait le perso à un ennemi/invoc.
            cac_penalty = CAC_PROXIMITY_PENALTY if min_m_dist <= 1 else 0.0

            # Bonus massif si on peut attaquer depuis dest (LdV entités OK)
            attack_bonus = -1000.0 if can_atk else 0.0

            # Bonus diagonale pure vers le monstre le plus proche (LdV plus robuste)
            diag_bonus = -120.0 if _is_diagonal_angle(dest, closest_monster, width) else 0.0

            distance_bonus = -min_m_dist * 10.0

            threat_penalty = _max_monster_threat_at(dest, width) * 80.0 if is_threatened else 0.0

            los_block_penalty = _count_ally_los_blocked(
                dest, ally_cells, monster_cells, los_blockers, width
            ) * 200.0

            ally_crowd_penalty = 0.0
            for ac in ally_cells:
                if _po_distance(dest, ac, width) <= 1:
                    ally_crowd_penalty += 80.0

            move_cost = _po_distance(caster_cell, dest, width) * 3.0

            score = (attack_bonus + range_bonus + diag_bonus + distance_bonus
                     + cac_penalty + threat_penalty + los_block_penalty
                     + ally_crowd_penalty + move_cost)

            lateral_candidates.append((score, dest))

        if lateral_candidates:
            lateral_candidates.sort(key=lambda x: x[0])
            best_score, best_dest = lateral_candidates[0]
            logger.debug(
                "[combat][distance] Déplacement latéral → cell %d (score=%.0f)",
                best_dest, best_score,
            )
            return best_dest

    # Phase 2 : trop loin OU latéral n'a rien donné → approche.
    # Préfère les cases d'où on pourra taper (LdV entités OK) ou en diagonale
    # du monstre le plus proche, pour casser un alignement bloqué par un allié.
    approach_candidates: list[tuple[float, int]] = []
    for dest in reachable:
        dist_to_closest_m = _po_distance(dest, closest_monster, width)
        min_m_dist = _min_monster_distance(dest, prox_cells)

        # Exclure les cases CàC (on reste à distance) — invocations comprises.
        if min_m_dist <= 1:
            continue

        can_atk = _can_attack_from(
            dest, monster_cells, spell_range_min, spell_range_max,
            po_bonus, needs_los, los_blockers, entity_blockers, width,
        )
        is_diag = _is_diagonal_angle(dest, closest_monster, width)

        # Bonus massif si on peut attaquer depuis dest — c'est le but du déplacement
        attack_bonus = -1500.0 if can_atk else 0.0

        # Bonus si dest est en diagonale du monstre cible : LdV plus robuste,
        # contourne les alliés alignés cardinalement avec le monstre.
        diagonal_bonus = -250.0 if is_diag else 0.0

        remaining_deficit = max(0, dist_to_closest_m - effective_max)

        # Pénalité basée sur la menace réelle (PM monstres + portée d'attaque)
        threat = _max_monster_threat_at(dest, width)
        threat_penalty = threat * 80.0

        los_block_penalty = _count_ally_los_blocked(
            dest, ally_cells, monster_cells, los_blockers, width
        ) * 200.0

        ally_crowd_penalty = 0.0
        for ac in ally_cells:
            if _po_distance(dest, ac, width) <= 1:
                ally_crowd_penalty += 80.0

        score = (attack_bonus + diagonal_bonus + remaining_deficit * 100.0
                 + threat_penalty + los_block_penalty + ally_crowd_penalty)

        approach_candidates.append((score, dest, can_atk))

    if approach_candidates:
        approach_candidates.sort(key=lambda x: x[0])
        best_score, best_dest, best_can_atk = approach_candidates[0]
        new_dist = _po_distance(best_dest, closest_monster, width)
        # Autoriser le déplacement s'il rapproche OU s'il débloque une attaque
        # (un repositionnement latéral peut ne pas rapprocher mais donner LdV).
        if new_dist < current_dist_to_closest or best_can_atk:
            logger.debug(
                "[combat][distance] Approche → cell %d (score=%.0f, dist %d→%d, attaque=%s)",
                best_dest, best_score, current_dist_to_closest, new_dist,
                "oui" if best_can_atk else "non",
            )
            return best_dest

    return None


def _find_los_correction_move(
    caster_cell: int,
    caster_id: str,
    pm: int,
    monster_cells: list[int],
    spell_range_min: int,
    spell_range_max: int,
    po_bonus: int,
    needs_los: bool,
    blocked_cells: set[int],
    los_blockers: set[int] | None,
    occupied_cells: set[int],
    width: int = MAP_WIDTH,
) -> int | None:
    """Trouver une case accessible avec LdV dégagée vers au moins un monstre.

    Utilisé après un rejet Im 1174 pour se repositionner latéralement.
    """
    if pm <= 0:
        return None

    move_blocked = blocked_cells | occupied_cells
    reachable = _movement_reachable_cells(caster_cell, pm, move_blocked, width)
    entity_blockers = occupied_cells - set(monster_cells)
    ally_cells = _get_ally_cells(caster_id)
    effective_max = spell_range_max + po_bonus
    # Proximité/anti-tacle : tous les monstres vivants (invocations comprises).
    prox_cells = _proximity_monster_cells()

    closest_monster = _find_closest_monster(caster_cell, monster_cells)

    candidates: list[tuple[float, int]] = []
    for dest in reachable:
        if dest == caster_cell:
            continue
        target_mc: int | None = None
        for mc in monster_cells:
            dist = _po_distance(dest, mc, width)
            if dist < spell_range_min or dist > effective_max:
                continue
            if needs_los and los_blockers and not has_line_of_sight(dest, mc, los_blockers, width):
                continue
            if needs_los and entity_blockers and not has_line_of_sight(dest, mc, entity_blockers, width):
                continue
            target_mc = mc
            break
        if target_mc is None:
            continue

        # Pénalité très forte si la correction de LdV nous colle à un ennemi/invoc
        # (tacle subi) : on préfère un autre angle, même un peu plus loin.
        cac_penalty = (
            CAC_PROXIMITY_PENALTY
            if _min_monster_distance(dest, prox_cells) <= 1 else 0.0
        )

        move_cost = _po_distance(caster_cell, dest, width) * 5.0
        los_penalty = _count_ally_los_blocked(
            dest, ally_cells, monster_cells, los_blockers, width
        ) * 100.0
        # Avancer plutôt que reculer : préférer les cases plus PROCHES du monstre
        # (ligne de vue plus courte = plus fiable, le serveur rejette moins).
        # L'ancienne préférence pour les cases lointaines (dist_bonus négatif sur la
        # distance) faisait RECULER les Crâs loin au lieu de se rapprocher pour
        # débloquer un tir — c'est ce qui les laissait inutiles tout le combat.
        dist_penalty = (
            _po_distance(dest, closest_monster, width) * 5.0
            if closest_monster is not None else 0.0
        )
        # Mais rester à distance de sécurité : pénaliser les cases trop exposées
        # pour ne pas foncer au corps à corps en corrigeant la LdV.
        threat_penalty = _max_monster_threat_at(dest, width) * 60.0
        # Bonus diagonale : la LdV reste robuste si un allié vient s'aligner
        diag_bonus = 0.0
        if closest_monster is not None and _is_diagonal_angle(dest, closest_monster, width):
            diag_bonus = -80.0
        candidates.append(
            (move_cost + los_penalty + dist_penalty + threat_penalty
             + diag_bonus + cac_penalty, dest)
        )

    if not candidates:
        return None
    candidates.sort(key=lambda x: x[0])
    return candidates[0][1]


def _find_retreat_move(
    caster_cell: int,
    caster_id: str,
    pm: int,
    monster_cells: list[int],
    spell_range_min: int,
    spell_range_max: int,
    po_bonus: int,
    needs_los: bool,
    blocked_cells: set[int],
    los_blockers: set[int] | None,
    occupied_cells: set[int],
    width: int = MAP_WIDTH,
) -> int | None:
    """Trouver une case de repli quand un monstre est trop proche (mode distance).

    Recule tout en essayant de rester à portée de tir.
    """
    move_blocked = blocked_cells | occupied_cells
    reachable = _movement_reachable_cells(caster_cell, pm, move_blocked, width)

    if not reachable:
        return None

    effective_max = spell_range_max + po_bonus
    entity_blockers = occupied_cells - set(monster_cells)
    ally_cells = _get_ally_cells(caster_id)
    # Repli : s'éloigner de TOUS les monstres vivants (invocations comprises).
    prox_cells = _proximity_monster_cells()
    current_min_dist = _min_monster_distance(caster_cell, prox_cells)

    retreat_candidates: list[tuple[float, int]] = []

    for dest in reachable:
        min_m_dist = _min_monster_distance(dest, prox_cells)

        # Exclure les cases qui ne nous éloignent pas
        if min_m_dist <= current_min_dist:
            continue

        # Peut-on attaquer depuis cette case ?
        can_attack = False
        for mc in monster_cells:
            d = _po_distance(dest, mc, width)
            if d < spell_range_min or d > effective_max:
                continue
            if needs_los and los_blockers and not has_line_of_sight(dest, mc, los_blockers, width):
                continue
            if needs_los and entity_blockers and not has_line_of_sight(dest, mc, entity_blockers, width):
                continue
            can_attack = True
            break

        # Priorité absolue : s'éloigner le plus possible
        distance_bonus = -min_m_dist * 30.0

        # Bonus fort si on peut encore attaquer depuis la case de repli
        attack_bonus = -300.0 if can_attack else 0.0

        # Pénalité basée sur la menace réelle (PM des monstres)
        threat = _max_monster_threat_at(dest, width)
        threat_penalty = threat * 100.0

        los_block_penalty = _count_ally_los_blocked(
            dest, ally_cells, monster_cells, los_blockers, width
        ) * 100.0

        score = distance_bonus + attack_bonus + threat_penalty + los_block_penalty

        retreat_candidates.append((score, dest))

    if retreat_candidates:
        retreat_candidates.sort(key=lambda x: x[0])
        logger.debug(
            "[combat][distance] Repli → cell %d (score=%.0f)",
            retreat_candidates[0][1], retreat_candidates[0][0],
        )
        return retreat_candidates[0][1]

    return None


def _find_advance_move(
    caster_cell: int,
    caster_id: str,
    pm: int,
    monster_cells: list[int],
    blocked_cells: set[int],
    occupied_cells: set[int],
    width: int = MAP_WIDTH,
    min_keep_dist: int = 4,
) -> int | None:
    """Case atteignable qui RAPPROCHE le plus du monstre le plus proche.

    Dernier recours pour un Crâ distance qui n'a rien pu lancer ce tour (typiquement
    très loin avec un gros bonus PO : ses tirs longue portée sont rejetés en LdV/portée
    et il ne se rapproche jamais). Il avance pour raccourcir la ligne et être à portée
    fiable au tour suivant — au lieu de rester planté ou de reculer.

    Ne descend pas sous `min_keep_dist` du monstre le plus proche (reste à distance,
    évite de finir au corps à corps). Si aucune case « sûre » ne rapproche, autorise
    quand même la meilleure case qui rapproche pour ne pas rester planté.
    """
    move_blocked = blocked_cells | occupied_cells
    reachable = _movement_reachable_cells(caster_cell, pm, move_blocked, width)
    if not reachable:
        return None

    closest = _find_closest_monster(caster_cell, monster_cells)
    if closest is None:
        return None
    current = _po_distance(caster_cell, closest, width)
    # Proximité/anti-tacle : tous les monstres vivants (invocations comprises).
    prox_cells = _proximity_monster_cells()

    # (dist_au_plus_proche, menace, cout_de_deplacement) — on minimise : la case la
    # plus avancée d'abord (dist la plus faible), puis la moins exposée, puis la plus
    # proche en PM. Deux listes : cases respectant min_keep_dist, et toutes les autres.
    best_safe_key: tuple[int, float, int] | None = None
    best_safe_cell: int | None = None
    best_any_key: tuple[int, float, int] | None = None
    best_any_cell: int | None = None

    for dest in reachable:
        dist_to_closest = _po_distance(dest, closest, width)
        if dist_to_closest >= current:
            continue  # ne garder que ce qui rapproche réellement
        # Ne jamais avancer en case adjacente à un monstre (invoc comprise) : se
        # coller volontairement = se faire tacler/bloquer au tour suivant.
        min_prox = _min_monster_distance(dest, prox_cells)
        if min_prox <= 1:
            continue
        threat = _max_monster_threat_at(dest, width)
        move_cost = _po_distance(caster_cell, dest, width)
        key = (dist_to_closest, threat, move_cost)

        if best_any_key is None or key < best_any_key:
            best_any_key = key
            best_any_cell = dest

        if min_prox >= min_keep_dist:
            if best_safe_key is None or key < best_safe_key:
                best_safe_key = key
                best_safe_cell = dest

    return best_safe_cell if best_safe_cell is not None else best_any_cell


async def _play_cra_turn(caster_label: str, caster_id: str) -> bool:
    """Jouer un tour de combat générique.

    Dispatch selon le mode de comportement (distance / rush_cac).

    Itère sur la séquence de sorts configurée (dans l'ordre) :
      1. Pour chaque sort, tenter de le lancer cast_count fois
      2. Si aucune cible directe et pas encore bougé → se déplacer
      3. Passer au sort suivant quand le sort courant ne peut plus être lancé
      4. Fin de tour quand PA épuisés ou séquence terminée

    Returns True si le tour s'est déroulé normalement, False si connexion perdue.
    """
    if not _state.current.in_combat:
        return True

    # Anti-déterminisme : petite hésitation occasionnelle en début de tour. Casse la
    # régularité métronomique des tours. Même réglage humain/farming (cadence unifiée) ;
    # jamais en mode speed, ni sous spectateur (déjà lent).
    _mode = _discretion_mode()
    if not is_spectator_present() and _mode != DISCRETION_SPEED:
        hesit_proba, hesit_bounds = 0.10, (1.0, 3.0)
        if _state.current.combat_min_action_delay_max_ms <= 0:
            hesit_proba = 0.0
        if random.random() < hesit_proba:
            hesitation = random.uniform(*hesit_bounds)
            logger.debug("[combat] %s — hésitation %.1fs (anti-déterminisme)", caster_label, hesitation)
            await asyncio.sleep(hesitation)

    behavior = _state.current.combat_behavior
    spell_sequence = _get_attack_spell_sequence(caster_id)

    caster_cell = _state.current.combat_entity_cells.get(caster_id)
    if caster_cell is None:
        logger.warning("[combat] %s — position inconnue, passage du tour", caster_label)
        return await end_turn() if _state.current.in_combat else True

    if not spell_sequence:
        cls = _state.get_entity_class_id(caster_id)
        logger.info(
            "[combat] %s — aucune séquence de sorts configurée pour la classe %s, tour passé",
            caster_label, cls,
        )
        return await end_turn() if _state.current.in_combat else True

    remaining_pa = get_entity_pa(caster_id)
    pm = get_entity_pm(caster_id)
    po_bonus = _get_effective_po_bonus(caster_id)

    def _target_label(t: str) -> str:
        return {"self": "self", "aoe2": "aoe2", "aoe3": "aoe3"}.get(t, "ennemi")

    seq_summary = ", ".join(
        f"{sid}x{cnt}({_target_label(tgt)})"
        for sid, cnt, tgt, *_ in spell_sequence
    )
    logger.info(
        "[combat] %s — tour [%s] (cell=%d PA=%d PM=%d PO+%d, séquence=[%s])",
        caster_label, behavior, caster_cell, remaining_pa, pm, po_bonus, seq_summary,
    )

    # Décrémenter les cooldowns de ce caster (nouveau tour)
    _tick_cooldowns(caster_id)

    # Réinitialiser le cache LoS pour ce nouveau tour
    clear_turn_los_cache()
    los_rejections_this_turn = 0
    MAX_LOS_REJECTIONS_PER_TURN = 2
    # Rejets non-LdV récupérables (Im 1172 case invalide, Im 1171 portée) : on
    # réessaie avec une autre cible plutôt que d'abandonner le sort, dans la limite
    # de ce quota (évite de gâcher le tour ET de spammer le serveur).
    other_rejections_this_turn = 0
    MAX_OTHER_REJECTIONS_PER_TURN = 3

    blocked_cells: set[int] = set()
    los_blockers: set[int] | None = None
    try:
        from bot.mapdata import load_map as _load_map
        if _state.current.current_map is not None:
            _map_info = _load_map(_state.current.current_map.map_id)
            if _map_info is not None:
                blocked_cells = _map_info.blocked_cells
                # Le bit LdV des maps de ce serveur est vide (toutes les cellules
                # marquées transparentes) → on ne peut PAS s'y fier. Les vrais
                # obstacles (murs, arbres, rochers) sont les cellules NON PRATICABLES.
                # On les prend donc comme bloqueurs terrain de la LdV, en union avec
                # le rare bit opaque. C'est ce qui redonne au bot une notion de LdV
                # terrain (sinon il croit voir à travers toute la map).
                los_blockers = _map_info.los_blocker_cells | _map_info.blocked_cells
    except Exception:
        pass

    monster_cells = get_live_monster_cells()
    if not monster_cells:
        logger.info("[combat] %s — aucun monstre vivant", caster_label)
        if _state.current.in_combat:
            return await end_turn()
        return True

    # Ciblage des invocations en dernier recours : par défaut on ignore les
    # invocations (tuer l'invocateur les tue), mais si AUCUN monstre d'origine
    # n'est jouable ce tour (LdV bloquée par des invocations, tacle), on bascule
    # le ciblage ET les déplacements sur TOUS les monstres pour ne pas rester
    # coincé — on tape alors les invocations qui nous gênent.
    target_summons = not _primary_engageable_this_turn(
        caster_id, caster_cell, pm, po_bonus, spell_sequence,
        blocked_cells, los_blockers,
    )
    if target_summons:
        logger.info(
            "[combat] %s — aucun monstre d'origine jouable (invocations bloquantes), "
            "ciblage des invocations ce tour",
            caster_label,
        )

    def _turn_target_cells() -> list[int]:
        """Cibles du tour : invocations incluses uniquement en mode fallback."""
        return get_live_monster_cells() if target_summons else get_targetable_monster_cells()

    def _turn_target_ids() -> set[str]:
        return (
            set(_state.current.combat_live_monsters)
            if target_summons else get_targetable_monster_ids()
        )

    moved_this_turn = False
    total_attacks_cast = 0  # nb de sorts d'attaque (ennemi/AOE) réellement lancés ce tour

    for spell_id, desired_count, target, cost_pa, range_min, range_max, needs_los, cooldown, empty_cell in spell_sequence:

        # Skip si en cooldown
        if _is_on_cooldown(caster_id, spell_id):
            cd_left = _spell_cooldowns.get(caster_id, {}).get(spell_id, 0)
            logger.info(
                "[combat] %s — sort %d : en cooldown (%d tour(s)), ignoré",
                caster_label, spell_id, cd_left,
            )
            continue

        casts_this_spell = 0

        while casts_this_spell < desired_count and remaining_pa >= cost_pa:
            if not _state.current.in_combat:
                return True

            caster_cell = _state.current.combat_entity_cells.get(caster_id)
            if caster_cell is None:
                break

            # Resynchroniser PA/PM avec le serveur (effets, debuffs en début de tour…)
            remaining_pa = get_entity_pa(caster_id)
            pm = get_entity_pm(caster_id)
            po_bonus = _get_effective_po_bonus(caster_id)
            # Portée effective que le bot calcule (clé du plafond appris). On clampe
            # ensuite po_bonus pour ne jamais viser au-delà du max serveur connu pour
            # cet état → plus de Im 1171 après le 1er rejet, même si notre PO est faux.
            raw_effective_range = range_max + po_bonus
            po_bonus = _capped_po_bonus(spell_id, range_max, po_bonus)
            if remaining_pa < cost_pa:
                logger.info(
                    "[combat] %s — sort %d : PA insuffisants (%d<%d), sort suivant",
                    caster_label, spell_id, remaining_pa, cost_pa,
                )
                break

            # --- Ciblage sur soi-même (buffs) ---
            if target == "self":
                logger.info(
                    "[combat] %s — sort %d → soi-même (cell %d, cast %d/%d, PA restants=%d)",
                    caster_label, spell_id, caster_cell,
                    casts_this_spell + 1, desired_count, remaining_pa,
                )
                clear_spell_los_blocked()
                clear_spell_result(caster_id)
                ok = await cast_spell(spell_id, caster_cell)
                if not ok:
                    logger.warning("[combat] %s — envoi sort échoué (connexion ?)", caster_label)
                    return False
                remaining_pa -= cost_pa
                casts_this_spell += 1
                if cooldown > 0:
                    _set_cooldown(caster_id, spell_id, cooldown)
                await wait_spell_result(timeout=SPELL_CAST_DELAY)

                remaining_pa = get_entity_pa(caster_id)
                if get_spell_cast_failed():
                    remaining_pa = get_entity_pa(caster_id)
                    casts_this_spell -= 1
                    if cooldown > 0:
                        _clear_cooldown(caster_id, spell_id)
                    logger.warning("[combat] %s — sort %d rejeté, sort suivant", caster_label, spell_id)
                    break
                # Cast réussi : enregistrer le buff PO local si le sort en accorde un
                # (ex. Tir Eloigné). Les As de mid-combat ne remontent pas pour les
                # héros, donc on suit le buff localement pour étendre leur portée.
                _register_po_buff(caster_id, spell_id)
                po_bonus = _get_effective_po_bonus(caster_id)
                continue

            # --- Ciblage ennemi (single-target ou AOE) ---
            # On cible en priorité les monstres d'origine (hors invocations) :
            # tuer l'invocateur tue ses invocations. _turn_target_* bascule sur
            # tous les monstres (invocations comprises) si aucun monstre d'origine
            # n'est jouable ce tour (cf. target_summons).
            monster_cells = _turn_target_cells()
            if not monster_cells:
                break

            aoe_radius = 2 if target == "aoe2" else (3 if target == "aoe3" else 0)
            is_aoe = aoe_radius > 0

            def _find_target_from(cell: int) -> int | None:
                """Cible single-target : monstre d'origine d'abord, puis blessé, puis proche.

                Le tri place les invocations en dernier (`is_summon`) : même en mode
                fallback, on tape un monstre d'origine s'il redevient jouable, et on
                ne touche les invocations que si rien de mieux n'est castable.
                """
                eb = {
                    c for eid, c in _state.current.combat_entity_cells.items()
                    if eid != caster_id and c >= 0
                }
                live: list[tuple[str, int]] = []
                for mid in _turn_target_ids():
                    mc = _state.current.combat_entity_cells.get(mid, -1)
                    if mc < 0:
                        continue
                    live.append((mid, mc))
                live.sort(key=lambda item: (
                    1 if _is_summon(item[0]) else 0,
                    0 if _is_monster_injured(item[0]) else 1,
                    _game_distance(cell, item[1]),
                ))
                for mid, mc in live:
                    if is_los_blocked_pair(cell, mc) or mc in _rejected_target_cells:
                        continue
                    blockers = eb - {mc}
                    if _can_cast_on(cell, mc, range_min, range_max, po_bonus,
                                    needs_los, los_blockers, blockers):
                        return mc
                return None

            def _find_aoe_from(cell: int) -> int | None:
                """Cible AOE : case d'impact maximisant les hits, zéro friendly fire."""
                impact = _find_best_aoe_impact(
                    cell, caster_id, monster_cells, aoe_radius,
                    range_min, range_max, po_bonus, needs_los,
                    los_blockers, MAP_WIDTH, empty_cell, _rejected_target_cells,
                    blocked_cells, prefer_close=(los_rejections_this_turn > 0),
                )
                if impact is not None and is_los_blocked_pair(cell, impact):
                    return None
                return impact

            def _resolve_target(cell: int) -> int | None:
                return _find_aoe_from(cell) if is_aoe else _find_target_from(cell)

            target_cell = _resolve_target(caster_cell)

            # AOE : essayer de bouger pour toucher plus d'ennemis dans la zone.
            # On compare les hits depuis la case courante vs la meilleure case
            # atteignable, et on bouge uniquement si gain strict + case safe.
            if is_aoe and pm > 0 and not moved_this_turn:
                current_hits = (
                    _aoe_hits(target_cell, monster_cells, aoe_radius)
                    if target_cell is not None else 0
                )
                occupied = _get_occupied_cells(caster_id)
                better = _find_best_aoe_caster_position(
                    caster_cell, caster_id, pm, monster_cells, aoe_radius,
                    range_min, range_max, po_bonus, needs_los,
                    blocked_cells, los_blockers, occupied,
                    MAP_WIDTH, empty_cell, _rejected_target_cells,
                )
                if better is not None:
                    best_dest, best_hits = better
                    if best_dest != caster_cell and best_hits > current_hits:
                        logger.info(
                            "[combat][aoe] %s — reposition %d→%d (hits %d→%d, PM=%d)",
                            caster_label, caster_cell, best_dest,
                            current_hits, best_hits, pm,
                        )
                        move_blocked = blocked_cells | occupied
                        moved = await _move_caster_to(
                            caster_label, caster_id, best_dest, move_blocked,
                        )
                        if moved:
                            moved_this_turn = True
                            caster_cell = _state.current.combat_entity_cells.get(
                                caster_id, best_dest,
                            )
                            pm = get_entity_pm(caster_id)
                            target_cell = _resolve_target(caster_cell)

            # Si aucune cible directe et qu'on a des PM → se déplacer
            if target_cell is None and pm > 0:
                occupied = _get_occupied_cells(caster_id)

                # Sort AOE : la recherche mono-cible (_find_best_move_distance)
                # cherche une case d'où on VOIT un monstre — pas une case d'où on
                # peut placer un IMPACT AOE valide (hits ≥ 1, sans friendly fire).
                # Du coup, après un 1er cast qui tue la seule cible, le bot
                # bougeait vers une case mono-cible inutile puis « aucune cible
                # AOE atteignable » → 2e Flèche perdue.
                # On utilise donc la repositionneuse AOE-aware aussi ici.
                move_dest = None
                if is_aoe:
                    better = _find_best_aoe_caster_position(
                        caster_cell, caster_id, pm, monster_cells, aoe_radius,
                        range_min, range_max, po_bonus, needs_los,
                        blocked_cells, los_blockers, occupied,
                        MAP_WIDTH, empty_cell, _rejected_target_cells,
                    )
                    if better is not None:
                        best_dest, best_hits = better
                        if best_dest != caster_cell and best_hits >= 1:
                            move_dest = best_dest
                elif behavior == "distance":
                    move_dest = _find_best_move_distance(
                        caster_cell, caster_id, pm, monster_cells,
                        range_min, range_max, po_bonus, needs_los,
                        blocked_cells, los_blockers, occupied,
                        is_aoe=is_aoe,
                    )
                else:
                    move_dest = _find_best_move_for_attack(
                        caster_cell, pm, monster_cells,
                        range_min, range_max, po_bonus, needs_los,
                        blocked_cells, los_blockers, occupied,
                    )

                if move_dest is not None and move_dest != caster_cell:
                    logger.info(
                        "[combat][%s] %s — déplacement %d→%d (PM=%d)",
                        behavior, caster_label, caster_cell, move_dest, pm,
                    )
                    move_blocked = blocked_cells | occupied
                    moved = await _move_caster_to(caster_label, caster_id, move_dest, move_blocked)
                    if moved:
                        moved_this_turn = True
                        caster_cell = _state.current.combat_entity_cells.get(caster_id, move_dest)
                        pm = get_entity_pm(caster_id)
                        target_cell = _resolve_target(caster_cell)
                elif move_dest is None:
                    logger.info("[combat] %s — aucune case utile accessible (PM=%d)", caster_label, pm)

            if target_cell is None:
                logger.info(
                    "[combat] %s — sort %d : aucune cible%s atteignable, sort suivant",
                    caster_label, spell_id,
                    f" AOE r={aoe_radius}" if is_aoe else "",
                )
                break

            dist = _game_distance(caster_cell, target_cell)
            if is_aoe:
                hits = _aoe_hits(target_cell, monster_cells, aoe_radius)
                logger.info(
                    "[combat] %s — sort %d AOE r=%d → cell %d (dist=%d, hits=%d, cast %d/%d, PA restants=%d)",
                    caster_label, spell_id, aoe_radius, target_cell, dist, hits,
                    casts_this_spell + 1, desired_count, remaining_pa,
                )
            else:
                logger.info(
                    "[combat] %s — sort %d → cell %d (dist=%d, cast %d/%d, PA restants=%d)",
                    caster_label, spell_id, target_cell, dist,
                    casts_this_spell + 1, desired_count, remaining_pa,
                )
            clear_spell_los_blocked()
            clear_spell_result(caster_id)
            # Filet de sécurité : si le GKK du cast précédent n'est pas encore arrivé
            # (zone à beaucoup d'ennemis → Flash lent à acquitter), patienter encore.
            # Sinon le serveur rejette silencieusement ce GA300 (réponse GA;102;-0).
            # No-op si pending=0 (retour immédiat) ; plafond aligné sur wait_spell_result.
            await wait_actions_clear(timeout=1.2)
            ok = await cast_spell(spell_id, target_cell)
            if not ok:
                logger.warning("[combat] %s — envoi sort échoué (connexion ?)", caster_label)
                return False
            remaining_pa -= cost_pa
            casts_this_spell += 1
            total_attacks_cast += 1
            if cooldown > 0:
                _set_cooldown(caster_id, spell_id, cooldown)
            await wait_spell_result(timeout=SPELL_CAST_DELAY)

            remaining_pa = get_entity_pa(caster_id)
            pm = get_entity_pm(caster_id)

            # Capture ground-truth LdV (accepté OU bloqué) pour porter l'algo serveur 3D.
            if needs_los and not is_aoe:
                if get_spell_los_blocked():
                    _capture_los(caster_cell, target_cell, spell_id, blocked=True)
                elif not get_spell_cast_failed():
                    _capture_los(caster_cell, target_cell, spell_id, blocked=False)

            if get_spell_cast_failed():
                # Resynchroniser PA (le serveur n'a pas consommé le sort rejeté)
                remaining_pa = get_entity_pa(caster_id)
                casts_this_spell -= 1
                total_attacks_cast -= 1
                if cooldown > 0:
                    _clear_cooldown(caster_id, spell_id)

                if get_spell_los_blocked():
                    mark_los_blocked(caster_cell, target_cell)
                    # Divergence d'algorithme : le serveur refuse la LdV alors que NOTRE
                    # calcul la croyait dégagée (sinon on n'aurait pas casté). C'est de la
                    # géométrie terrain (statique) → on l'apprend de façon persistante pour
                    # ne PLUS JAMAIS réémettre ce cast sur cette map. Single-target
                    # uniquement (la cible = une case monstre stable ; les impacts AOE
                    # varient trop pour être mis en cache sans risque de sur-prudence).
                    if not is_aoe:
                        _learn_los_blocked(caster_cell, target_cell)
                    # Bloquer cette cible pour TOUT le tour : après un repli, on ne
                    # retentera pas le même cluster lointain (qui rejetterait encore).
                    _rejected_target_cells.add(target_cell)
                    los_rejections_this_turn += 1
                    logger.warning(
                        "[combat] %s — sort %d rejeté (LdV bloquée sur cell %d)",
                        caster_label, spell_id, target_cell,
                    )

                    # Quota de rejets atteint → on n'essaie plus, pour éviter de spammer
                    # le serveur avec des Im 1174 (qui pourraient lever un flag anti-bot).
                    if los_rejections_this_turn >= MAX_LOS_REJECTIONS_PER_TURN:
                        logger.warning(
                            "[combat] %s — quota LdV atteint (%d/%d), arrêt des tentatives ce tour",
                            caster_label, los_rejections_this_turn, MAX_LOS_REJECTIONS_PER_TURN,
                        )
                        break

                    # Ne pas retenter depuis la même case : repositionnement LdV
                    if pm > 0:
                        occupied = _get_occupied_cells(caster_id)
                        mc_list = _turn_target_cells()
                        move_dest = _find_los_correction_move(
                            caster_cell, caster_id, pm, mc_list,
                            range_min, range_max, po_bonus, needs_los,
                            blocked_cells, los_blockers, occupied,
                        )
                        if move_dest is not None and move_dest != caster_cell:
                            logger.info(
                                "[combat] %s — LdV bloquée, repositionnement %d→%d",
                                caster_label, caster_cell, move_dest,
                            )
                            move_blocked = blocked_cells | occupied
                            moved = await _move_caster_to(
                                caster_label, caster_id, move_dest, move_blocked,
                            )
                            if moved:
                                moved_this_turn = True
                                caster_cell = _state.current.combat_entity_cells.get(
                                    caster_id, move_dest
                                )
                                pm = get_entity_pm(caster_id)
                                # Pas de clear : les paires (ancien_cell, target) restent
                                # bloquées, mais le nouveau caster_cell a son propre lookup.
                                continue

                    # Pas de repositionnement possible (PM épuisé après déplacement,
                    # ou aucune case utile) : on réessaie un AUTRE impact depuis la
                    # case actuelle — la cible rejetée vient d'être exclue. C'est ce
                    # qui permet le 2e cast après un déplacement quand le 1er tir
                    # (souvent lointain) est rejeté en LdV. Le quota LdV ci-dessus
                    # borne le nombre total de tentatives (anti-spam Im 1174).
                    logger.info(
                        "[combat] %s — LdV bloquée, nouvel impact depuis la case actuelle (PM=%d)",
                        caster_label, pm,
                    )
                    continue
                elif get_spell_pa_insufficient():
                    # Im 1170 : le serveur dit qu'on n'a pas assez de PA (typiquement
                    # après un tacle qui a drainé des PA sans que le bot le suive). Notre
                    # compteur local de PA est donc faux ; inutile de retenter une autre
                    # case (ça rejetterait pareil). On force remaining_pa sous le coût
                    # pour sortir proprement et on passe au sort suivant.
                    remaining_pa = cost_pa - 1
                    logger.warning(
                        "[combat] %s — sort %d rejeté (PA réellement insuffisants, tacle ?), "
                        "arrêt du sort",
                        caster_label, spell_id,
                    )
                else:
                    # Rejet non-LdV. Si on a encore de quoi lancer le sort, c'est une
                    # case invalide (Im 1172, EMPTY_CELL) ou hors portée (Im 1171) :
                    # on exclut cette cible et on réessaie avec une autre (au lieu
                    # d'abandonner le sort — c'est ce qui causait le "1 seul cast").
                    #
                    # Si c'est un Im 1171 (hors portée), le serveur nous a donné sa
                    # portée effective réelle → on l'apprend pour ne plus jamais viser
                    # hors portée avec ce sort (spells.xml local diverge du serveur privé).
                    range_reject = get_last_range_reject()
                    if range_reject is not None:
                        _srv_min, srv_max, _actual = range_reject
                        # Mémoriser : pour cet effectif calculé, le serveur plafonne à
                        # srv_max → le prochain cast dans le même état clampera dessus.
                        _learn_range_cap(spell_id, raw_effective_range, srv_max)
                    if remaining_pa >= cost_pa and other_rejections_this_turn < MAX_OTHER_REJECTIONS_PER_TURN:
                        _rejected_target_cells.add(target_cell)
                        other_rejections_this_turn += 1
                        logger.warning(
                            "[combat] %s — sort %d rejeté (case %d invalide/hors portée), "
                            "nouvelle cible (%d/%d)",
                            caster_label, spell_id, target_cell,
                            other_rejections_this_turn, MAX_OTHER_REJECTIONS_PER_TURN,
                        )
                        continue

                    logger.warning(
                        "[combat] %s — sort %d rejeté (PA insuffisants ou quota, restants=%d)",
                        caster_label, spell_id, remaining_pa,
                    )

                logger.info(
                    "[combat] %s — sort %d : échec serveur, sort suivant",
                    caster_label, spell_id,
                )
                break

        if not get_live_monster_cells():
            break

    # --- Mode distance : après avoir tapé, reculer si un monstre menace notre position ---
    # On ne recule QUE si on a réellement attaqué ce tour. Sinon (aucune cible / sort
    # rejeté), reculer reviendrait à fuir avec des PA intacts au lieu de s'avancer
    # pour taper — c'est le comportement "recule au lieu de s'avancer et taper".
    pm = get_entity_pm(caster_id)
    if behavior == "distance" and pm > 0 and total_attacks_cast > 0:
        caster_cell = _state.current.combat_entity_cells.get(caster_id)
        if caster_cell is not None:
            monster_cells = get_live_monster_cells()
            if monster_cells:
                closest_monster = _find_closest_monster(caster_cell, monster_cells)
                if closest_monster is not None:
                    dist_to_closest = _po_distance(caster_cell, closest_monster)
                    # Sort de référence pour le repli : premier sort visant les ennemis
                    # (single-target ou AOE) — détermine la portée à préserver.
                    first_enemy = next(
                        (s for s in spell_sequence if s[2] in ("enemy", "aoe2", "aoe3")), None
                    )
                    effective_max = (first_enemy[5] + po_bonus) if first_enemy else 7

                    # Vérifier si un monstre peut nous atteindre au prochain tour
                    threatened = _is_threatened_by_monsters(caster_cell)
                    # Reculer si menacé et qu'on a assez de PO pour taper en reculant d'1 case
                    should_retreat = (
                        threatened
                        and effective_max >= dist_to_closest + 1
                    )
                    if should_retreat:
                        closest_mid = None
                        for mid in _state.current.combat_live_monsters:
                            mc = _state.current.combat_entity_cells.get(mid)
                            if mc == closest_monster:
                                closest_mid = mid
                                break
                        monster_pm = _state.current.combat_entity_pm.get(closest_mid, 0) if closest_mid else 0
                        logger.info(
                            "[combat][distance] %s — menacé (dist=%d, monstre PM=%d, PO max=%d), repli post-attaque",
                            caster_label, dist_to_closest, monster_pm, effective_max,
                        )
                        occupied = _get_occupied_cells(caster_id)
                        retreat_dest = _find_retreat_move(
                            caster_cell, caster_id, pm, monster_cells,
                            first_enemy[4] if first_enemy else 1,
                            first_enemy[5] if first_enemy else 7,
                            po_bonus, first_enemy[6] if first_enemy else True,
                            blocked_cells, los_blockers, occupied,
                        )
                        if retreat_dest is not None:
                            logger.info(
                                "[combat][distance] %s — repli %d→%d (dist monstre %d→%d)",
                                caster_label, caster_cell, retreat_dest,
                                dist_to_closest, _po_distance(retreat_dest, closest_monster),
                            )
                            move_blocked = blocked_cells | occupied
                            await _move_caster_to(
                                caster_label, caster_id, retreat_dest, move_blocked,
                            )

    # --- Mode distance : aucun tir abouti ce tour → AVANCER vers les monstres ---
    # Si on n'a lancé AUCUN sort d'attaque ce tour (cibles hors de portée fiable,
    # tirs longue portée rejetés en LdV/portée) et qu'il reste des PM, on se rapproche
    # du monstre le plus proche. Sans ça, un Crâ très loin (gros bonus PO) reste planté
    # ou recule indéfiniment et ne fait rien si les monstres n'avancent pas non plus.
    # Exclusif avec le repli ci-dessus (qui exige total_attacks_cast > 0).
    pm = get_entity_pm(caster_id)
    if behavior == "distance" and pm > 0 and total_attacks_cast == 0:
        caster_cell = _state.current.combat_entity_cells.get(caster_id)
        # On avance vers les monstres d'origine (hors invocations) : c'est eux
        # qu'on veut mettre à portée pour les focus. En mode fallback (invocations
        # bloquantes), on avance vers les invocations à taper.
        monster_cells = _turn_target_cells()
        if caster_cell is not None and monster_cells:
            occupied = _get_occupied_cells(caster_id)
            advance_dest = _find_advance_move(
                caster_cell, caster_id, pm, monster_cells,
                blocked_cells, occupied,
            )
            if advance_dest is not None and advance_dest != caster_cell:
                closest_monster = _find_closest_monster(caster_cell, monster_cells)
                logger.info(
                    "[combat][distance] %s — aucun tir ce tour, avance %d→%d "
                    "(PM=%d, dist monstre %d→%d)",
                    caster_label, caster_cell, advance_dest, pm,
                    _po_distance(caster_cell, closest_monster) if closest_monster else -1,
                    _po_distance(advance_dest, closest_monster) if closest_monster else -1,
                )
                move_blocked = blocked_cells | occupied
                await _move_caster_to(
                    caster_label, caster_id, advance_dest, move_blocked,
                )

    if not _state.current.in_combat:
        return True

    ok = await end_turn()
    if not ok:
        logger.warning("[combat] %s — envoi Gt échoué", caster_label)
        return False
    return True


# ---------------------------------------------------------------------------
# Boucle principale de combat
# ---------------------------------------------------------------------------

async def fight_group() -> bool:
    """Gérer un combat complet pour tous les alliés (perso principal + héros).

    Utilise une queue de tours : chaque GTS d'un allié y place son entity_id.
    Le bot consomme les tours un par un et joue chaque Crâ.

    Returns True si le combat s'est terminé normalement (GE reçu).
    """
    from dashboard import bridge

    _reset_cooldowns()
    logger.info("[combat] fight_group() — combat en cours (cooldowns reset)")
    bridge.update_activity("combat")

    turns_played = 0
    max_turns = 300  # sécurité : 8 cras × ~30 rounds max
    mode = _discretion_mode()
    # Anti-détection (mode humain) : au plus UNE pause « distraction » et UN misclick
    # par combat (pas à chaque tour, sinon ça devient un pattern reconnaissable).
    midfight_pause_done = mode != DISCRETION_HUMAN
    misclick_done = mode != DISCRETION_HUMAN

    try:
        while turns_played < max_turns:
            if not _state.current.in_combat:
                logger.info("[combat] Combat terminé (GE reçu)")
                return True

            # Connexion perdue en plein combat : sortir TOUT DE SUITE au lieu de rester
            # figé jusqu'au timeout de tour (60 s). La boucle de farm gèrera l'arrêt /
            # la reprise auto à la reconnexion. Sans ça, le bot semble planté ~1 min.
            if not channel.is_connected():
                logger.warning("[combat] Connexion perdue pendant le combat — sortie immédiate")
                return False

            # Attendre le prochain tour d'allié
            turn_id = await wait_next_ally_turn(timeout=TURN_TIMEOUT)

            if turn_id is None:
                if not _state.current.in_combat:
                    logger.info("[combat] Combat terminé pendant l'attente")
                    return True
                if not channel.is_connected():
                    logger.warning("[combat] Aucun tour reçu — connexion perdue")
                    return False
                logger.warning("[combat] Timeout : aucun tour reçu en %.0fs", TURN_TIMEOUT)
                return False

            if not _state.current.in_combat:
                return True

            # Identifier le personnage
            char = _state.current.character
            if char and turn_id == char.character_id:
                label = char.pseudo
            else:
                label = f"Héros-{turn_id}"

            # Misclick occasionnel : un cast raté sur sa propre case (hors de portée,
            # rejeté par le serveur) avant l'action réelle — comme un vrai misclick.
            if (not misclick_done and not is_spectator_present()
                    and random.random() < HUMAN_MISCLICK_PROBA):
                misclick_done = True
                caster_cell = _state.current.combat_entity_cells.get(turn_id)
                if caster_cell is not None:
                    logger.debug("[combat] %s — misclick simulé (cast hors de portée)", label)
                    await cast_spell(SPELL_ID, caster_cell)
                    await asyncio.sleep(random.uniform(0.5, 1.5))

            # Pause « distraction » occasionnelle en plein combat (SMS, verre d'eau…).
            # La pause consomme le budget du tour EN COURS : on la re-plafonne sous le
            # timer serveur (combat_turn_time_ms) pour TOUJOURS garder le temps de jouer
            # le tour — sinon le serveur le saute et le héros ne fait rien (bug du 11/08).
            if (not midfight_pause_done and not is_spectator_present()
                    and random.random() < HUMAN_MIDFIGHT_PAUSE_PROBA):
                midfight_pause_done = True
                turn_budget_s = max(1.0, _state.current.combat_turn_time_ms / 1000.0)
                cap = turn_budget_s * MIDFIGHT_PAUSE_TURN_FRACTION
                dur = min(random.uniform(*HUMAN_MIDFIGHT_PAUSE_S), cap)
                logger.info("[combat] %s — pause en combat %.0fs (anti-détection, cap %.0fs)",
                            label, dur, cap)
                bridge.add_console(f"📱 Petite pause en combat — {dur:.0f}s")
                bridge.update_activity("pause_combat", f"{dur:.0f}s")
                await asyncio.sleep(dur)
                bridge.update_activity("combat")

            # Jouer le tour
            ok = await _play_cra_turn(label, caster_id=turn_id)
            if not ok:
                return False
            turns_played += 1

            # Courte pause pour laisser le serveur processer (et détecter un GE éventuel)
            await asyncio.sleep(0.3)

        logger.warning("[combat] %d tours joués sans GE — abandon", max_turns)
        return False
    finally:
        bridge.update_activity("farming")


# ---------------------------------------------------------------------------
# Lancement du combat — sélection du groupe le plus grand
# ---------------------------------------------------------------------------

async def start_fight_biggest_group(skip_entity_ids: set[str] | None = None) -> str | None:
    """Trouver le groupe de monstres avec le plus de membres et lancer le combat.

    Le Flash client envoie toujours GA001 (déplacement vers la cellule du monstre)
    ET GA907 simultanément — le serveur l'exige pour accepter l'attaque.

    skip_entity_ids : entités à ignorer (ex : groupes déjà en combat, inaccessibles).
    Returns l'entity_id ciblé si GA907 envoyé, None si aucun groupe trouvé.
    """
    from bot.pathfinding import astar, path_to_ga001, MAP_WIDTH
    from bot.mapdata import load_map as _load_map
    from dashboard import bridge

    # Collecter les groupes de monstres depuis les entités sur la carte.
    # `monster_ids` non vide est exigé en plus de `is_monster` : une entité sans
    # aucun monstre identifié n'est pas un groupe attaquable (le serveur ignore le
    # GA907 en silence et le bot boucle sur des retries), c'est le signe d'un
    # sprite type mal classé — percepteur, prisme, monture en parc…
    groups = [
        (e.entity_id, e.cell_id, len(e.monster_ids))
        for e in _state.current.entities.values()
        if e.is_monster and e.cell_id >= 0 and e.monster_ids
    ]

    if not groups:
        logger.warning("[combat] start_fight: aucun groupe de monstres visible")
        return None

    # Filtrer les entités à éviter (ex : déjà en combat ou inaccessibles)
    if skip_entity_ids:
        available = [(eid, cid, cnt) for eid, cid, cnt in groups if eid not in skip_entity_ids]
        if not available:
            logger.warning("[combat] start_fight: tous les groupes sont blacklistés — reset")
            return None
        groups = available

    # Filtrer par taille maximale (option UI). 8 = pas de filtrage.
    max_monsters = _state.current.combat_max_monsters_per_group
    if max_monsters < 8:
        filtered = [(eid, cid, cnt) for eid, cid, cnt in groups if cnt <= max_monsters]
        if not filtered:
            logger.warning(
                "[combat] start_fight: aucun groupe ≤ %d monstres (groupes disponibles: %s)",
                max_monsters, sorted({cnt for _, _, cnt in groups}, reverse=True),
            )
            return None
        groups = filtered

    # Trier par taille décroissante
    groups.sort(key=lambda x: x[2], reverse=True)

    # Position de départ du personnage (pour vérifier l'accessibilité des groupes).
    char = _state.current.character
    start_cell = -1
    if char is not None:
        self_entity = _state.current.entities.get(char.character_id)
        if self_entity is not None:
            start_cell = self_entity.cell_id

    map_info = _load_map(_state.current.current_map.map_id) if _state.current.current_map else None
    blocked: set[int] = map_info.blocked_cells if map_info else set()
    width = map_info.width if map_info else MAP_WIDTH
    sun_magic: set[int] = map_info.sun_magic_cells if map_info else set()

    # Sélectionner le plus grand groupe ATTEIGNABLE. Le GA001 (déplacement) doit
    # accompagner le GA907, sinon le serveur ignore silencieusement l'attaque — et
    # le bot reste bloqué à spammer un GA907 voué à l'échec (cf. monstre fantôme
    # cell 440 map 1669).
    #
    # La cellule du monstre est passée à l'A* comme destination VALIDE même si elle
    # est marquée bloquée dans le XML : dans cette version, un monstre peut
    # stationner sur une case « non-marchable » (trou/décor). Le serveur tronque
    # de toute façon le déplacement à la case adjacente (comportement identique au
    # cas normal, où le chemin GA001 se termine déjà sur la cellule du monstre).
    entity_id: str | None = None
    cell_id = -1
    count = 0
    ga001_path: list[int] | None = None
    for eid, cid, cnt in groups:
        if start_cell < 0 or start_cell == cid:
            # Position inconnue ou déjà sur la cellule : on tente directement (sans GA001).
            entity_id, cell_id, count, ga001_path = eid, cid, cnt, None
            break

        # Bloquer les soleils dans l'A* (jamais les traverser ni s'y arrêter), sauf
        # le monstre lui-même. `- {cid}` rend la cellule-cible marchable comme
        # destination, qu'elle soit un obstacle statique ou occupée par le monstre.
        blocked_no_sun = (blocked | (sun_magic - {cid})) - {cid}
        path = astar(blocked_no_sun, start_cell, cid, width)
        if not path or len(path) < 2:
            logger.debug("[combat] start_fight: groupe %s (cell %d) inaccessible — skip", eid, cid)
            continue

        # Si le monstre est sur un soleil, s'arrêter à la cellule précédente.
        if cid in sun_magic:
            if len(path) >= 3:
                path = path[:-1]
                logger.debug("[combat] GA001 : cible=%d est soleil → arrêt à %d", cid, path[-1])
            else:
                # Monstre adjacent au soleil : impossible de s'arrêter avant — on skip.
                logger.debug("[combat] start_fight: groupe %s sur soleil trop proche — skip", eid)
                continue

        entity_id, cell_id, count, ga001_path = eid, cid, cnt, path
        break

    if entity_id is None:
        logger.warning(
            "[combat] start_fight: aucun groupe accessible (%d groupe(s) hors d'atteinte)",
            len(groups),
        )
        return None

    logger.info("[combat] Lancement combat → cell %d, entity %s (%d monstres)",
                cell_id, entity_id, count)
    bridge.add_console(f"⚔ Attaque → {count} monstres (cell {cell_id})")

    # Construire et envoyer GA001 (chemin vers le monstre) avant GA907,
    # comme le fait le Flash client quand on clique sur un groupe.
    if ga001_path:
        ga001_data = path_to_ga001(ga001_path, width)
        if ga001_data:
            await channel.send(f"GA001{ga001_data}\n")
            logger.debug("[combat] GA001 envoyé (path %d→%d)", start_cell, ga001_path[-1])

    sent = await channel.send(f"GA907{cell_id};{entity_id}\n")
    return entity_id if sent else None


# ---------------------------------------------------------------------------
# Navigation de récupération — retour sur la bonne map via sol magique
# ---------------------------------------------------------------------------

async def _navigate_via_sun() -> bool:
    """Marcher vers la cellule sol magique la plus proche pour changer de map.

    Utilisé pour revenir sur la map cible quand le bot s'est retrouvé ailleurs.
    Envoie GA001 vers le sol magique (la traversée déclenche le changement de map).
    Attend le GDK (nouvelle map chargée) jusqu'à 15 s.

    Returns True si le GA001 a été envoyé (changement de map en cours).
    """
    from bot.pathfinding import astar, path_to_ga001
    from bot.mapdata import load_map as _load_map

    current_map = _state.current.current_map
    if current_map is None:
        logger.warning("[combat] _navigate_via_sun: pas de map courante")
        return False

    map_info = _load_map(current_map.map_id)
    if not map_info:
        logger.warning("[combat] _navigate_via_sun: données map %d introuvables", current_map.map_id)
        return False

    sun_cells = list(map_info.sun_magic_cells)
    if not sun_cells:
        logger.warning("[combat] _navigate_via_sun: aucun sol magique sur map %d", current_map.map_id)
        return False

    char = _state.current.character
    if char is None:
        return False
    entity = _state.current.entities.get(char.character_id)
    start_cell = entity.cell_id if entity is not None else -1
    if start_cell < 0:
        logger.warning("[combat] _navigate_via_sun: cellule du personnage inconnue")
        return False

    # Déjà sur un sol magique
    if start_cell in sun_cells:
        logger.info("[combat] _navigate_via_sun: déjà sur sol magique (cell %d)", start_cell)
        return True

    blocked = map_info.blocked_cells
    width = map_info.width

    # Trouver le sol magique le plus proche (chemin le plus court)
    best_path: list[int] | None = None
    best_sun: int | None = None
    for sun_cell in sun_cells:
        path = astar(blocked, start_cell, sun_cell, width)
        if path and (best_path is None or len(path) < len(best_path)):
            best_path = path
            best_sun = sun_cell

    if not best_path or best_sun is None:
        logger.warning(
            "[combat] _navigate_via_sun: aucun chemin vers sol magique depuis cell %d", start_cell
        )
        return False

    logger.info(
        "[combat] _navigate_via_sun: map %d → sol magique cell %d (depuis %d, %d étapes)",
        current_map.map_id, best_sun, start_cell, len(best_path) - 1,
    )

    ga001_data = path_to_ga001(best_path, width)
    if ga001_data:
        await channel.send(f"GA001{ga001_data}\n")
        return True

    return False


async def _idle_wander() -> None:
    """Petit aller-retour d'une case, hors combat (anti-détection, mode humain).

    Un joueur qui attend le prochain groupe de monstres ne reste jamais parfaitement
    immobile. Se déplace vers une case adjacente valide puis revient — mouvement
    volontairement minimal pour ne jamais interférer avec le farming (pathing A*
    court, aucune traversée de sol magique possible sur 1 case).
    """
    from bot import actions as _actions
    from bot.mapdata import load_map as _load_map
    from bot.pathfinding import adjacent_cells, MAP_WIDTH

    if _state.current.in_combat:
        return
    char = _state.current.character
    if char is None:
        return
    entity = _state.current.entities.get(char.character_id)
    if entity is None or entity.cell_id < 0:
        return
    origin = entity.cell_id

    width = MAP_WIDTH
    if _state.current.current_map is not None:
        info = _load_map(_state.current.current_map.map_id)
        if info is not None:
            width = info.width

    neighbors = [c for c in adjacent_cells(origin, width) if c >= 0]
    if not neighbors:
        return
    step = random.choice(neighbors)

    logger.debug("[combat] Farm: déplacement idle %d→%d (anti-statisme)", origin, step)
    if await _actions.move_to(step):
        await asyncio.sleep(random.uniform(1.5, 4.0))
        if not _state.current.in_combat:
            await _actions.move_to(origin)


# ---------------------------------------------------------------------------
# Boucle de farming continue
# ---------------------------------------------------------------------------

async def combat_farm_loop(target_map_id: int | None = None) -> None:
    """Boucle de farming infinie : attendre map → lancer combat → combattre → répéter.

    À appeler depuis un script ou une tâche asyncio.
    La boucle tourne jusqu'à ce que la tâche soit annulée.

    target_map_id : si fourni, vérifie qu'on est sur la bonne map après chaque GDK.
                    Si la map est incorrecte, attend sans attaquer (sécurité anti-drift).
    """
    global _combat_bot_wanted
    from dashboard import bridge

    logger.info("[combat] combat_farm_loop() démarré (map cible: %s)", target_map_id)
    bridge.add_console("🤖 Bot combat démarré — farming en boucle")
    bridge.update_activity("farming")

    consecutive_failures = 0
    # Entités dont GA907 a échoué consécutivement — on les skip jusqu'à reset.
    _skipped_entities: set[str] = set()
    # Compteur d'échecs par entité pour décider quand la blacklister.
    _entity_fail_count: dict[str, int] = {}

    # --- Anti-détection : cadence de farm humaine ---
    fights_done = 0
    st = _state.current

    def _compute_next_pause() -> int:
        """Nombre de combats avant la prochaine pause longue (avec jitter).

        Mode humain : désactivé (0) — remplacé par la pause AFK périodique basée sur
        le temps (next_afk_pause_at ci-dessous), plus réaliste qu'un compteur de combats.
        """
        if _discretion_mode() == DISCRETION_HUMAN:
            return 0
        base = st.combat_pause_every_fights
        if base <= 0:
            return 0  # désactivé
        jit = max(0, st.combat_pause_every_jitter)
        return max(1, base + random.randint(-jit, jit))

    next_pause_at = _compute_next_pause()

    async def _long_pause(reason: str, bounds: tuple[float, float] | None = None) -> None:
        if bounds is not None:
            lo, hi = bounds
        else:
            lo = max(1, st.combat_pause_duration_min_s)
            hi = max(lo, st.combat_pause_duration_max_s)
        dur = random.uniform(lo, hi)
        logger.info("[combat] Farm: pause longue %.0fs (%s)", dur, reason)
        bridge.add_console(f"⏸ Pause {dur:.0f}s ({reason})")
        await asyncio.sleep(dur)

    # Mode humain : pause AFK périodique (10-20 min, quelques secondes à quelques
    # minutes) + suivi du joueur inconnu présent sur la carte (farm suspendu tant
    # qu'il est là).
    next_afk_pause_at = time.monotonic() + random.uniform(*AFK_PAUSE_EVERY_S)
    player_hold_since: float | None = None

    while True:
        try:
            # Garde déconnexion : si la session serveur est tombée, ne SURTOUT pas
            # continuer à spammer GA907/GA300 dans le vide (78 envois « sans connexion
            # active » observés le jour du ban). On stoppe la boucle proprement.
            if not channel.is_connected():
                logger.warning("[combat] Farm: connexion serveur perdue — arrêt de la boucle")
                bridge.add_console("🔌 Connexion perdue — bot combat arrêté")
                return

            # Vérifier si un arrêt post-combat a été demandé
            if is_stopping_after_combat() and not _state.current.in_combat:
                logger.info("[combat] Farm: arrêt demandé après combat — stop")
                bridge.add_console("🛑 Bot combat arrêté (fin du combat)")
                _combat_bot_wanted = False  # arrêt volontaire : pas de reprise auto
                return

            # Si déjà en combat (ex: combat déclenché manuellement), gérer ce combat d'abord
            if _state.current.in_combat:
                logger.info("[combat] Farm: combat déjà en cours — fight_group()")
                bridge.add_console("⚔ Combat en cours détecté — prise en charge")
                await fight_group()
                await asyncio.sleep(0.5)
                consecutive_failures = 0

                # Arrêt demandé après ce combat ?
                if is_stopping_after_combat():
                    logger.info("[combat] Farm: arrêt post-combat effectif")
                    bridge.add_console("🛑 Bot combat arrêté (fin du combat)")
                    _combat_bot_wanted = False  # arrêt volontaire : pas de reprise auto
                    return
                continue

            # Anti-détection (mode humain), hors combat : pause AFK périodique (statut
            # dashboard basculé sur "Pause AFK" le temps de la pause, pour que l'absence
            # d'activité soit lisible plutôt qu'inquiétante) + petit déplacement idle
            # occasionnel — un joueur qui patiente ne reste jamais parfaitement immobile.
            if _discretion_mode() == DISCRETION_HUMAN:
                if time.monotonic() >= next_afk_pause_at:
                    dur = random.uniform(*AFK_PAUSE_DURATION_S)
                    logger.info("[combat] Farm: pause AFK %.0fs", dur)
                    bridge.add_console(f"💤 Pause AFK — {dur:.0f}s")
                    bridge.update_activity("pause_afk", f"{dur:.0f}s")
                    await asyncio.sleep(dur)
                    bridge.update_activity("farming")
                    next_afk_pause_at = time.monotonic() + random.uniform(*AFK_PAUSE_EVERY_S)
                    continue
                elif random.random() < IDLE_WANDER_PROBA:
                    await _idle_wander()

            # Attendre que la carte soit prête (après retour de combat = GDK)
            if not _state.current._map_loaded:
                logger.info("[combat] Farm: attente GDK…")
                ok = await wait_map_ready(timeout=20.0)
                if not ok:
                    logger.warning("[combat] Farm: timeout GDK — retry")
                    consecutive_failures += 1
                    await asyncio.sleep(2.0)
                    continue

            # Vérifier qu'on est sur la bonne map
            if target_map_id is not None:
                current_map = _state.current.current_map
                current_map_id = current_map.map_id if current_map else None
                if current_map_id != target_map_id:
                    logger.warning(
                        "[combat] Farm: mauvaise map %s (attendu %d) — tentative retour via sol magique",
                        current_map_id, target_map_id,
                    )
                    bridge.add_console(
                        f"⚠ Mauvaise map ({current_map_id}) — retour vers {target_map_id} via sol magique…"
                    )
                    # Tenter de naviguer vers le sol magique (téléporteur)
                    navigated = await _navigate_via_sun()
                    if navigated:
                        # Attendre le chargement de la nouvelle map (GDK)
                        _state.current._map_loaded = False
                        ok = await wait_map_ready(timeout=15.0)
                        if not ok:
                            logger.warning("[combat] Farm: timeout GDK après sol magique")
                            await asyncio.sleep(3.0)
                    else:
                        # Pas de sol magique trouvé — attendre intervention manuelle
                        bridge.add_console(
                            f"⚠ Pas de sol magique sur map {current_map_id} — retourne sur {target_map_id} manuellement"
                        )
                        _state.current._map_loaded = False
                        await asyncio.sleep(10.0)
                    continue

            # Déposer les ressources en banque si surpoids
            from game.state import is_overweight, get_inventory_resources
            from bot.bank import bank_deposit_resources
            if is_overweight():
                logger.info("[combat] Farm: surpoids (%d/%d) — dépôt banque",
                            _state.current.weight_current, _state.current.weight_max)
                resources = get_inventory_resources()
                if not resources:
                    # Items en sac non trackés (session précédente sans OAK/OQ)
                    # On ne peut pas déposer — on continue quand même le farming
                    logger.warning(
                        "[combat] Farm: surpoids mais 0 ressource trackée — farming sans dépôt"
                    )
                    bridge.add_console(
                        f"⚠ Surpoids ({_state.current.weight_current}/{_state.current.weight_max})"
                        " mais inventaire non tracké — combat quand même"
                    )
                else:
                    bridge.add_console(
                        f"⚖ Surpoids ({_state.current.weight_current}/{_state.current.weight_max}) "
                        "— dépôt banque en cours…"
                    )
                    bridge.notify(
                        f"Surpoids — dépôt banque "
                        f"({_state.current.weight_current}/{_state.current.weight_max})",
                        level="warning",
                    )
                    deposited = await bank_deposit_resources()
                    if deposited:
                        bridge.add_console("✓ Dépôt banque terminé — reprise du farming")
                    else:
                        bridge.add_console("⚠ Dépôt banque échoué — pause 10s")
                        await asyncio.sleep(10.0)
                    continue

            # Laisser le temps aux GM entity lists d'arriver
            await asyncio.sleep(FIGHT_START_DELAY)

            # Vérifier si un combat s'est lancé entre-temps (Flash client)
            if _state.current.in_combat:
                continue

            # Joueur inconnu sur la carte (hors nos héros). Les GM entity lists sont déjà
            # arrivées (FIGHT_START_DELAY ci-dessus) → détection fiable.
            #  - Mode humain : on SUSPEND le farm tant qu'il est là. Le 09/07 le bot a
            #    lancé 4 combats sous les yeux de l'admin arrivé sur la map (2 s après
            #    son arrivée !) → MP « Bot interdit » + ban. S'il s'installe, pause
            #    longue ; on ne reprend qu'un délai aléatoire après son départ.
            #  - Modes farming/speed : simple délai optionnel avant d'engager
            #    (comportement historique, si combat_slow_when_player est coché).
            mode = _discretion_mode()
            if mode == DISCRETION_HUMAN and has_foreign_player_on_map():
                now = time.monotonic()
                if player_hold_since is None:
                    player_hold_since = now
                    logger.info("[combat] Farm: joueur inconnu sur la carte — farm suspendu")
                    bridge.add_console("👤 Joueur inconnu sur la carte — farm suspendu")
                if now - player_hold_since >= HUMAN_PLAYER_HOLD_PAUSE_AFTER_S:
                    await _long_pause("joueur toujours présent — on laisse la place")
                    player_hold_since = time.monotonic()  # ré-évaluer après la pause
                else:
                    await asyncio.sleep(random.uniform(*HUMAN_PLAYER_HOLD_RECHECK_S))
                continue
            if player_hold_since is not None:
                # Le joueur est parti : reprise en douceur, pas à la seconde près.
                player_hold_since = None
                delay = random.uniform(*HUMAN_PLAYER_RESUME_DELAY_S)
                logger.info("[combat] Farm: joueur parti — reprise dans %.0fs", delay)
                bridge.add_console(f"👤 Joueur parti — reprise du farm dans {delay:.0f}s")
                await asyncio.sleep(delay)
                if _state.current.in_combat:
                    continue
            if (mode != DISCRETION_HUMAN
                    and _state.current.combat_slow_when_player
                    and has_foreign_player_on_map()):
                lo = max(0, _state.current.combat_player_delay_min_ms)
                hi = max(lo, _state.current.combat_player_delay_max_ms)
                delay = random.uniform(lo, hi) / 1000.0
                logger.info("[combat] Farm: joueur sur la carte → délai %.1fs avant combat", delay)
                bridge.add_console(f"👤 Joueur sur la carte — lancement du combat dans {delay:.1f}s")
                await asyncio.sleep(delay)
                # Un combat a pu démarrer (Flash client) ou un combat manuel pendant l'attente
                if _state.current.in_combat:
                    continue

            # Lancer le combat contre le plus grand groupe (en évitant les blacklistés)
            targeted_entity = await start_fight_biggest_group(skip_entity_ids=_skipped_entities)
            if targeted_entity is None:
                if _skipped_entities:
                    # Tous les groupes étaient blacklistés : reset et réessayer
                    logger.info("[combat] Farm: reset blacklist (%d entité(s))", len(_skipped_entities))
                    bridge.add_console(f"↩ Reset blacklist ({len(_skipped_entities)} groupe(s) inaccessibles)")
                    _skipped_entities.clear()
                    _entity_fail_count.clear()
                    await asyncio.sleep(2.0)
                else:
                    logger.warning("[combat] Farm: pas de groupe disponible — attente 1s")
                    await asyncio.sleep(1.0)
                    consecutive_failures += 1
                    if consecutive_failures > 10:
                        logger.error("[combat] Farm: trop d'échecs consécutifs — pause 30s")
                        await asyncio.sleep(30.0)
                        consecutive_failures = 0
                continue

            # Attendre que le combat commence.
            # Le GJ arrive au tick suivant du timer ILS (~2s) → timeout à 4.5s pour absorber
            # tout délai réseau/serveur sans envoyer un 2e GA907 trop tôt.
            combat_started = await wait_combat_start(timeout=4.5, target_entity_id=targeted_entity)
            if not combat_started:
                if _state.current.in_combat:
                    pass  # GJ reçu mais pas GS encore, c'est OK
                else:
                    if targeted_entity not in _state.current.entities:
                        logger.warning("[combat] Farm: groupe %s volé — retry immédiat", targeted_entity)
                        bridge.add_console("⚠ Groupe volé — retry immédiat")
                    else:
                        logger.warning("[combat] Farm: combat non démarré après GA907 — retry")
                        _entity_fail_count[targeted_entity] = _entity_fail_count.get(targeted_entity, 0) + 1
                        if _entity_fail_count[targeted_entity] >= 3:
                            _skipped_entities.add(targeted_entity)
                            logger.warning(
                                "[combat] Farm: groupe %s blacklisté après %d échecs",
                                targeted_entity, _entity_fail_count[targeted_entity],
                            )
                            bridge.add_console(f"⚠ Groupe {targeted_entity} inaccessible — skip")
                    consecutive_failures += 1

                    # Carte contestée : si un autre joueur est présent ET qu'on enchaîne
                    # les échecs (groupes volés/pris sous notre nez), s'acharner à retenter
                    # GA907 en boucle est une signature bot très visible (c'est ce qui a
                    # précédé le ban). On lâche la carte : longue pause + reset blacklist.
                    if (st.combat_leave_contested_after > 0
                            and consecutive_failures >= st.combat_leave_contested_after
                            and has_foreign_player_on_map()):
                        await _long_pause("carte contestée par un joueur — on lâche")
                        _skipped_entities.clear()
                        _entity_fail_count.clear()
                        consecutive_failures = 0
                        continue

                    # Laisser le serveur finaliser le rollback avant de renvoyer GA907.
                    await asyncio.sleep(2.0)
                    continue

            # Combattre !
            success = await fight_group()

            if success:
                logger.info("[combat] Farm: combat terminé ✓")
                consecutive_failures = 0
                _skipped_entities.clear()
                _entity_fail_count.clear()
                fights_done += 1

                # Arrêt demandé après ce combat ?
                if is_stopping_after_combat():
                    logger.info("[combat] Farm: arrêt post-combat effectif")
                    bridge.add_console("🛑 Bot combat arrêté (fin du combat)")
                    _combat_bot_wanted = False  # arrêt volontaire : pas de reprise auto
                    return

                # Plafond de session : s'arrêter tout seul au-delà du quota (anti-marathon).
                if st.combat_session_max_fights > 0 and fights_done >= st.combat_session_max_fights:
                    logger.info(
                        "[combat] Farm: plafond de session atteint (%d combats) — arrêt",
                        fights_done,
                    )
                    bridge.add_console(f"🏁 Plafond de session atteint ({fights_done} combats) — arrêt")
                    _combat_bot_wanted = False  # quota atteint : arrêt définitif voulu
                    return

                # Attendre la fin effective du combat AVANT une éventuelle pause longue
                # (sinon on pause alors que le retour de carte n'est pas terminé).
                if _state.current.in_combat:
                    await wait_combat_end(timeout=30.0)

                # Pause longue régulière : casse la cadence robotique (un humain
                # s'interrompt régulièrement). Recalcule le prochain seuil avec jitter.
                # Désactivée en mode speed (vitesse d'avant).
                if (next_pause_at and fights_done >= next_pause_at
                        and _discretion_mode() != DISCRETION_SPEED):
                    await _long_pause(f"pause régulière après {fights_done} combats")
                    next_pause_at = fights_done + _compute_next_pause()

                # Note : la pause AFK périodique (mode humain, 10-20 min) est gérée en
                # début de boucle, hors combat — cf. next_afk_pause_at ci-dessus.

                # Ré-engagement rapide (même cadence que le mode farming) : la protection
                # anti-détection vient maintenant des pauses ponctuelles (AFK, mi-combat,
                # misclick, déplacement idle), pas d'un délai systématique ici.
                post_delay = random.uniform(0.5, 1.2)
                bridge.add_console("✓ Combat terminé — relance dans %.1fs" % post_delay)
                await asyncio.sleep(post_delay)
            else:
                logger.warning("[combat] Farm: combat échoué (timeout/connexion)")
                bridge.add_console("⚠ Erreur combat — retry")
                consecutive_failures += 1
                await asyncio.sleep(2.0)

            # Attendre la fin effective du combat si encore en cours
            if _state.current.in_combat:
                await wait_combat_end(timeout=30.0)

        except asyncio.CancelledError:
            logger.info("[combat] combat_farm_loop() annulé")
            bridge.add_console("🛑 Bot combat arrêté")
            raise
        except Exception as exc:
            logger.exception("[combat] Erreur inattendue dans farm_loop : %s", exc)
            bridge.add_console(f"⚠ Erreur bot combat : {exc}")
            await asyncio.sleep(3.0)
            consecutive_failures += 1


# ---------------------------------------------------------------------------
# Helpers pour compatibilité avec les harvesters existants
# ---------------------------------------------------------------------------

# ---------------------------------------------------------------------------
# API publique — start / stop / is_running
# ---------------------------------------------------------------------------

def _session_active():
    from core.session import active
    return active()


# --- Reprise automatique après reconnexion (cross-session) --------------------
# La boucle de farm appartient à une Session : quand la connexion serveur tombe,
# sa task se termine et la Session est détruite. Ces globals (indépendants de la
# Session) mémorisent que l'utilisateur VEUT que le bot tourne, pour le relancer
# automatiquement sur la NOUVELLE session dès que le personnage ré-entre en jeu
# (hook on_game_ready appelé depuis le handler GCK). Sûr : la reprise n'agit
# jamais sur une socket morte — elle attend une connexion game confirmée (GCK).
_combat_bot_wanted: bool = False
_combat_target_map_id: int | None = None


def start_bot(target_map_id: int | None = None) -> None:
    """Démarrer la boucle de farming combat (dans le contexte d'une session)."""
    global _combat_bot_wanted, _combat_target_map_id
    _combat_bot_wanted = True
    _combat_target_map_id = target_map_id
    s = _session_active()
    if s.combat_task is not None and not s.combat_task.done():
        return
    s.combat_stop_after = False
    loop = asyncio.get_event_loop()
    s.combat_task = loop.create_task(combat_farm_loop(target_map_id), name="combat-bot")


def stop_bot() -> None:
    """Arrêter la boucle de farming combat immédiatement (et désactiver la reprise)."""
    global _combat_bot_wanted
    _combat_bot_wanted = False  # arrêt explicite : pas de reprise auto
    s = _session_active()
    s.combat_stop_after = False
    if s.combat_task and not s.combat_task.done():
        s.combat_task.cancel()


def on_game_ready() -> None:
    """Relancer le bot combat après une reconnexion, si l'utilisateur l'avait lancé.

    Appelé depuis le handler GCK (« personnage en jeu »), qui refire à chaque
    reconnexion. Idempotent : ne relance que si (a) l'utilisateur veut le bot,
    (b) il n'est pas déjà en train de tourner sur la session courante. Après une
    déconnexion, la boucle précédente s'est arrêtée mais _combat_bot_wanted est
    resté True → on repart sur la nouvelle connexion.
    """
    if not _combat_bot_wanted:
        return
    if is_running():
        return
    logger.info("[combat] Reconnexion détectée — reprise automatique du bot combat")
    try:
        from dashboard import bridge
        bridge.add_console("🔄 Reconnexion — reprise automatique du farm")
    except Exception:
        pass
    start_bot(_combat_target_map_id)


def stop_after_combat() -> None:
    """Demander l'arrêt après la fin du combat en cours."""
    _session_active().combat_stop_after = True
    logger.info("[combat] Arrêt demandé après le combat en cours")


def is_running(session=None) -> bool:
    """True si la boucle combat tourne pour la session donnée (ou la session active)."""
    from core.session import active_or_none
    s = session or active_or_none()
    if s is None:
        return False
    return s.combat_task is not None and not s.combat_task.done()


def is_stopping_after_combat() -> bool:
    return _session_active().combat_stop_after


async def handle_combat_if_needed() -> None:
    """Gérer un combat en cours si in_combat=True. À appeler en début de boucle harvester."""
    if not _state.current.in_combat:
        return

    from dashboard import bridge
    logger.info("[combat] Combat détecté — fight_group()")
    bridge.add_console("⚔ Combat en cours, gestion automatique…")

    success = await fight_group()

    if success:
        logger.info("[combat] Combat terminé avec succès — reprise récolte")
        bridge.add_console("✓ Combat terminé — reprise de la récolte")
    else:
        logger.warning("[combat] Combat timeout/erreur — reprise quand même")
        bridge.add_console("⚠ Combat terminé (timeout) — reprise de la récolte")

    await asyncio.sleep(1.5)
