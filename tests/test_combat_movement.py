"""
Tests du déplacement en combat : détection du mouvement refusé (tacle) et
positionnement en mode distance.

Lance avec : python -m pytest tests/ -v
"""

import pytest

from core.session import Session, set_active
from game import state as _state
from game.state import _on_game_action
from protocol.parser import ParsedMessage
from bot import combat as c


class _Char:
    character_id = "100"


@pytest.fixture
def gs():
    """État de jeu neuf lié à une session active, en combat."""
    sess = Session(1)
    set_active(sess)
    g = _state.current
    g.character = _Char()
    g.in_combat = True
    g.combat_entity_cells.clear()
    g.combat_live_monsters.clear()
    g.combat_entity_pm.clear()
    g.entity_classes.clear()
    g._move_rejected_event.clear()
    return g


def _fire_ga(payload: str) -> None:
    msg = ParsedMessage(
        direction="S→C", msg_id="GA", msg_name="GameActions",
        payload=payload, raw="GA" + payload, timestamp=0.0,
    )
    _on_game_action(msg)


# ---------------------------------------------------------------------------
# Détection du mouvement refusé (GA;129) — corrige le tour gelé sur tacle
# ---------------------------------------------------------------------------

class TestMoveRejection:
    def test_zero_delta_with_comma_is_rejected(self, gs) -> None:
        """GA;129 avec delta NUL (',-0') = mouvement refusé (cas tacle du log)."""
        gs.combat_entity_pm["100"] = 3
        _fire_ga(";129;100;100,-0")
        assert gs._move_rejected_event.is_set()
        # Le PM ne doit PAS être modifié (ni +0, ni +100…).
        assert gs.combat_entity_pm["100"] == 3

    def test_no_comma_is_rejected(self, gs) -> None:
        """GA;129 sans delta ('100;100') = mouvement refusé."""
        _fire_ga(";129;100;100")
        assert gs._move_rejected_event.is_set()

    def test_real_move_not_rejected(self, gs) -> None:
        """GA;129 avec delta réel ('-2') = vrai déplacement, PM décrémenté."""
        gs.combat_entity_cells["100"] = 200  # requis pour la MAJ PM en combat
        gs.combat_entity_pm["100"] = 3
        _fire_ga(";129;100;100,-2")
        assert not gs._move_rejected_event.is_set()
        assert gs.combat_entity_pm["100"] == 1


# ---------------------------------------------------------------------------
# Déplacement mono-cible mode distance — le corps à corps est un dernier recours
# ---------------------------------------------------------------------------

class TestMoveDistancePrefersRange:
    """`_find_best_move_distance` pénalise très fortement les cases adjacentes à un
    monstre, mais ne les interdit pas : rester planté sans tirer est pire que
    tirer au contact (cf. la pénalité CàC dans la phase 1 du scoring)."""

    def test_takes_adjacent_cell_as_last_resort(self, gs, monkeypatch) -> None:
        """Seule une case adjacente permet de tirer → on y va quand même."""
        m = 280
        cc = next(x for x in range(560) if c._po_distance(x, m) == 10)
        adj = next(x for x in range(560) if c._po_distance(x, m) == 1)
        gs.combat_entity_cells["100"] = cc
        gs.combat_entity_cells["-1"] = m
        gs.combat_live_monsters.add("-1")
        gs.combat_entity_max_pm["-1"] = 3
        monkeypatch.setattr(c, "_movement_reachable_cells",
                            lambda cell, pm, blocked, width=c.MAP_WIDTH: {cc, adj})
        # range 1-9 sans LdV : cc (dist 10) ne peut pas tirer, adj (dist 1) oui
        res = c._find_best_move_distance(cc, "100", 6, [m], 1, 9, 0, False, set(), None, set())
        assert res == adj          # faute de mieux, on accepte le CàC pour tirer

    def test_prefers_non_adjacent_attack_cell(self, gs, monkeypatch) -> None:
        """Une case non adjacente permet de tirer → on la choisit (pas l'adjacente)."""
        m = 280
        cc = next(x for x in range(560) if c._po_distance(x, m) == 10)
        adj = next(x for x in range(560) if c._po_distance(x, m) == 1)
        far = next(x for x in range(560) if c._po_distance(x, m) == 4)
        gs.combat_entity_cells["100"] = cc
        gs.combat_entity_cells["-1"] = m
        gs.combat_live_monsters.add("-1")
        gs.combat_entity_max_pm["-1"] = 3
        monkeypatch.setattr(c, "_movement_reachable_cells",
                            lambda cell, pm, blocked, width=c.MAP_WIDTH: {cc, adj, far})
        res = c._find_best_move_distance(cc, "100", 6, [m], 1, 9, 0, False, set(), None, set())
        assert res == far
        assert c._po_distance(res, m) > 1
