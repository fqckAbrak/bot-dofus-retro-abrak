"""
Primitives de bas niveau pour le bot : déplacement et récolte.

Toutes les fonctions sont des coroutines asyncio à appeler depuis
le thread de l'event loop (bot/harvester.py).
"""

from __future__ import annotations

import asyncio
import logging
import math

from bot import channel
from bot.pathfinding import (
    MAP_WIDTH,
    astar,
    path_to_ga001,
    movement_duration,
    adjacent_cells,
    cell_to_xy,
)
from bot.mapdata import load_map
from game import state as _state
from protocol.messages.stats import EntityInfo
from protocol.messages.stats import CLASSES

logger = logging.getLogger(__name__)


def _current_width() -> int:
    """Retourner la largeur réelle de la map courante depuis les données XML."""
    from bot import mapdata as _mapdata
    if _state.current.current_map is not None:
        info = _mapdata.load_map(_state.current.current_map.map_id)
        if info is not None:
            return info.width
    return MAP_WIDTH


def _blocked_from_map() -> set[int]:
    """Retourner les cellules non praticables depuis les données XML.

    Inclut les cellules de sortie de map (soleils, portails) pour que l'A*
    ne route jamais à travers elles et évite les changements de map accidentels.
    """
    from bot import mapdata as _mapdata
    if _state.current.current_map is not None:
        info = _mapdata.load_map(_state.current.current_map.map_id)
        if info is not None:
            return info.blocked_cells | info.sun_magic_cells
    return set()


def _known_resource_cells() -> set[int]:
    """Retourner les cellules de ressources connues sur la map courante.

    Utilisé pour exclure les cellules voisines qui sont elles-mêmes des
    ressources non-praticables (ex : frênes adjacents).
    """
    return {
        e.elem_id
        for e in _state.current.frame_objects.elements.values()
        if e.resource_id > 0
    }


async def move_to(
    target_cell: int,
    blocked: set[int] | None = None,
    width: int | None = None,
    _retry: int = 0,
) -> bool:
    """Déplacer le personnage vers target_cell.

    Séquence :
      1. Calculer le chemin A* depuis la position courante.
      2. Envoyer GA001{path}\\n.
      3. Attendre la durée de déplacement calculée.
      4. Vérifier position : si GA;1 a corrigé et destination non atteinte, retry.

    Args:
        target_cell : Cell_id de destination.
        blocked     : Ensemble de cell_ids à éviter (ressources, murs…).
        width       : Largeur de la map (défaut 14).

    Returns:
        True si le mouvement a été envoyé, False en cas d'erreur.
    """
    if _state.current.in_combat:
        logger.debug("[actions] move_to : combat en cours — action annulée")
        return False

    if not channel.is_connected():
        logger.warning("[actions] move_to : pas de connexion")
        return False

    char = _state.current.character
    if char is None:
        logger.warning("[actions] move_to : personnage inconnu")
        return False

    real_width = width if width is not None else _current_width()

    map_blocked = _blocked_from_map()
    all_blocked = map_blocked | (blocked or set())

    entity = _state.current.entities.get(char.character_id)
    if entity is None or entity.cell_id < 0:
        logger.warning("[actions] move_to : position du personnage inconnue (cell_id=%s)",
                       entity.cell_id if entity else "None")
        return False

    start_cell = entity.cell_id
    if start_cell == target_cell:
        return True

    # Ne pas lancer l'A* si la cible est bloquée : l'A* explorerait des cellules
    # fantômes infinies (pas de borne y haute) et bloquerait l'event loop.
    if target_cell not in all_blocked:
        path = astar(all_blocked, start_cell, target_cell, real_width)
    else:
        path = None
    if path is None:
        path = astar(blocked or set(), start_cell, target_cell, real_width)
    if path is None:
        logger.warning(
            "[actions] move_to : pas de chemin %d → %d (width=%d)",
            start_cell, target_cell, real_width,
        )
        return False

    ga_path = path_to_ga001(path, real_width)
    if not ga_path:
        logger.warning("[actions] move_to : chemin vide après encodage")
        return False

    logger.info(
        "[actions] Mouvement %d → %d (%d cases, width=%d, retry=%d), payload=%r",
        start_cell, target_cell, len(path), real_width, _retry, ga_path[:30],
    )

    pre_map_id = _state.current.current_map.map_id if _state.current.current_map else None

    ok = await channel.send(f"GA001{ga_path}\n")
    if not ok:
        return False

    from game.state import wait_gkk
    duration = movement_duration(path, real_width)
    gkk_ok = await wait_gkk(timeout=duration + 3.0)

    if not gkk_ok:
        logger.warning("[actions] GKK timeout (%.1f s) — Flash n'a pas acquitté", duration + 3.0)
        await asyncio.sleep(0.5)

    # After the move, GA;1 may have corrected our position.
    # If we didn't reach the target and position was corrected, retry once
    # from the now-correct position.
    # But skip retry if the map changed (exit cell reached → don't navigate on new map).
    if _retry < 1:
        post_map_id = _state.current.current_map.map_id if _state.current.current_map else None
        map_changed = post_map_id != pre_map_id

        actual = _state.current.entities.get(char.character_id)
        actual_cell = actual.cell_id if actual else -1
        needs_retry = (
            not map_changed
            and actual_cell >= 0
            and actual_cell != target_cell
            and actual_cell != start_cell
        )

        if needs_retry:
            logger.info(
                "[actions] move_to : position corrigée par GA;1 (%d→%d), "
                "destination %d non atteinte — retry",
                start_cell, actual_cell, target_cell,
            )
            return await move_to(target_cell, blocked=blocked, width=width, _retry=_retry + 1)

    return True


def _best_adjacent(resource_cell: int, start_cell: int, map_blocked: set[int], width: int) -> int | None:
    """Trouver la meilleure cellule adjacente à resource_cell pour se placer avant de récolter.

    Les cellules de ressources sont non-praticables (obstacles dans la map XML).
    Le joueur doit se placer sur une case adjacente pour pouvoir récolter.

    Returns:
        cell_id de la case adjacente la plus proche, ou None si aucune trouvée.
    """
    adj = adjacent_cells(resource_cell, width)
    walkable = [c for c in adj if c not in map_blocked]
    if not walkable:
        return None
    # Si déjà adjacent, rester sur place
    if start_cell in walkable:
        return start_cell
    # Sinon, prendre la case adjacente la plus proche de la position courante
    sx, sy = cell_to_xy(start_cell, width)
    return min(
        walkable,
        key=lambda c: math.sqrt(
            (cell_to_xy(c, width)[0] - sx) ** 2 + (cell_to_xy(c, width)[1] - sy) ** 2
        ),
    )


async def harvest_with_move(cell_id: int, elem_type: int) -> bool:
    """Se déplacer vers la ressource (ou une case adjacente si non-praticable) puis GA500.

    - Si cell_id est praticable (frênes sur ce serveur) : on va SUR la cellule.
    - Si cell_id est bloquée (obstacle XML) : on va sur une case adjacente.
    GA500 est envoyé APRÈS la fin du déplacement pour éviter que le serveur
    ne le reçoive avant GKK de Flash (rejet silencieux).

    Returns:
        True si les deux messages ont été envoyés.
    """
    if _state.current.in_combat:
        logger.debug("[actions] harvest_with_move : combat en cours — action annulée")
        return False

    if not channel.is_connected():
        return False

    char = _state.current.character
    if char is None:
        return False

    real_width = _current_width()
    map_blocked = _blocked_from_map()

    entity = _state.current.entities.get(char.character_id)
    if entity is None or entity.cell_id < 0:
        logger.warning("[actions] harvest_with_move : position inconnue")
        return False

    start_cell = entity.cell_id

    # Les cellules de ressources actives (frênes, minerais…) sont des obstacles
    # physiques : le serveur refuse tout chemin qui passe À TRAVERS elles, même si
    # l'XML les marque comme praticables. On les ajoute au blocked set pour l'A*,
    # sauf la ressource cible elle-même (qu'on "vise" — le serveur nous redirige
    # vers une case adjacente si nécessaire).
    resource_cells_all = _known_resource_cells()
    path_blocked = map_blocked | (resource_cells_all - {cell_id})

    # Destination : la cellule ressource elle-même si praticable,
    # sinon la meilleure case adjacente praticable.
    if cell_id in map_blocked:
        destination = _best_adjacent(cell_id, start_cell, path_blocked, real_width)
        if destination is None:
            destination = _best_adjacent(cell_id, start_cell, map_blocked, real_width)
        if destination is None:
            logger.warning(
                "[actions] harvest_with_move : aucune case adjacente praticable cell=%d", cell_id
            )
            return False
        logger.debug("[actions] harvest_with_move cell=%d non-praticable → adjacent=%d", cell_id, destination)
    else:
        destination = cell_id

    # Pour les repositionnements, on bloque TOUTES les ressources (y compris cell_id)
    # car on vise une case adjacente, pas la ressource elle-même.
    path_blocked_strict = map_blocked | resource_cells_all

    # Pré-calculer les cases adjacentes à la ressource (utile pour les checks d'adjacence).
    adj_of_resource = set(adjacent_cells(cell_id, real_width))

    # Mémoriser la map avant tout mouvement : si la map change (exit cell), on abandonne.
    pre_move_map_id = _state.current.current_map.map_id if _state.current.current_map else None

    from game.state import wait_gkk

    async def _do_move(frm: int, to: int, blocked_set: set, label: str) -> tuple[bool, int]:
        """Envoyer GA001, attendre GKK, retourner (map_ok, actual_cell_after)."""
        p = astar(blocked_set, frm, to, real_width)
        if p is None:
            p = astar(map_blocked, frm, to, real_width)
        if p is None:
            p = astar(set(), frm, to, real_width)
        if p is None:
            logger.warning("[actions] harvest_with_move : pas de chemin %d→%d (%s)", frm, to, label)
            return True, frm  # map_ok=True, position inchangée
        ga = path_to_ga001(p, real_width)
        logger.info("[actions] harvest_with_move %d → %d path=%r (%s)", frm, to, ga[:20], label)
        if not await channel.send(f"GA001{ga}\n"):
            return False, frm
        dur = movement_duration(p, real_width)
        logger.debug("[actions] Attente GKK %.2f s max (%s)", dur, label)
        gkk_ok = await wait_gkk(timeout=dur + 3.0)
        if not gkk_ok:
            logger.warning("[actions] harvest_with_move : GKK timeout %.1f s (%s)", dur + 3.0, label)
            await asyncio.sleep(0.3)
        else:
            await asyncio.sleep(0.05)
        # Vérifier changement de map (exit cell déclenchée)
        cur_map = _state.current.current_map
        if cur_map is None or cur_map.map_id != pre_move_map_id:
            logger.warning("[actions] harvest_with_move : changement de map pendant %s — abandon", label)
            return False, -1
        ent = _state.current.entities.get(char.character_id)
        pos = ent.cell_id if ent else -1
        return True, pos

    if start_cell != destination:
        map_ok, actual_cell = await _do_move(start_cell, destination, path_blocked, "move 1")
        if not map_ok:
            return False
        if actual_cell < 0:
            logger.warning("[actions] harvest_with_move : entité introuvable après move 1 — on tente GA500")
            actual_cell = destination

        if actual_cell != destination:
            logger.info(
                "[actions] harvest_with_move : GA1 redirect %d→%d (voulu %d)",
                start_cell, actual_cell, destination,
            )
            # Si le serveur nous a déjà placés sur une case adjacente à la ressource,
            # pas besoin de repositionnement — GA500 direct (évite le double-move visible).
            if actual_cell in adj_of_resource:
                logger.debug("[actions] harvest_with_move : déjà adjacent à cell=%d — GA500 direct", cell_id)
            else:
                # Repositionnement : chercher la meilleure case adjacente libre.
                # path_blocked_strict bloque TOUTES les ressources pour éviter de router à travers.
                adj_cell = _best_adjacent(cell_id, actual_cell, path_blocked_strict, real_width)
                if adj_cell is None:
                    adj_cell = _best_adjacent(cell_id, actual_cell, map_blocked, real_width)
                if adj_cell is None:
                    adj_cell = _best_adjacent(cell_id, actual_cell, set(), real_width)
                if adj_cell is None:
                    logger.warning("[actions] harvest_with_move : aucune case adjacente à cell=%d", cell_id)
                    return False

                if actual_cell != adj_cell:
                    map_ok2, actual_cell2 = await _do_move(actual_cell, adj_cell, path_blocked_strict, "repositionnement")
                    if not map_ok2:
                        return False

                    if actual_cell2 < 0:
                        actual_cell2 = adj_cell

                    # Vérifier si le repositionnement a réussi ou si on est adjacent
                    if actual_cell2 == adj_cell or actual_cell2 in adj_of_resource:
                        pass  # en position, GA500 direct
                    else:
                        logger.info(
                            "[actions] harvest_with_move : repositionnement redirigé %d→%d (voulu %d) — 2e tentative",
                            actual_cell, actual_cell2, adj_cell,
                        )
                        path_blocked3 = path_blocked_strict | {adj_cell}
                        adj_cell2 = _best_adjacent(cell_id, actual_cell2, path_blocked3, real_width)
                        if adj_cell2 is None:
                            adj_cell2 = _best_adjacent(cell_id, actual_cell2, map_blocked, real_width)
                        if adj_cell2 is not None and actual_cell2 != adj_cell2:
                            map_ok3, _ = await _do_move(actual_cell2, adj_cell2, path_blocked3, "2e repositionnement")
                            if not map_ok3:
                                return False

    logger.info("[actions] harvest_with_move GA500 cell=%d type=%d", cell_id, elem_type)
    return await channel.send(f"GA500{cell_id};{elem_type}\n")


async def harvest_resource(cell_id: int, elem_type: int) -> bool:
    """Envoyer l'action de récolte GA500.

    Format : GA500{cell_id};{elem_type}\\n

    Args:
        cell_id  : Cell_id de la ressource (= elem_id du GDF).
        elem_type: Type d'élément interactif (champ 'type' du GDF).

    Returns:
        True si le message a été envoyé.
    """
    logger.info("[actions] Récolte cell=%d type=%d", cell_id, elem_type)
    return await channel.send(f"GA500{cell_id};{elem_type}\n")
