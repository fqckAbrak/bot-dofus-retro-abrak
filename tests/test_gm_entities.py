"""
Tests unitaires pour le parsing de la liste d'entités GM (protocol/messages/map.py).

Tous les payloads ci-dessous sont des captures réelles du proxy (dossier logs/).

Lance avec : python -m pytest tests/ -v
"""

import pytest

from protocol.messages.map import (
    SPRITE_TYPE_OFFLINE_CHARACTER,
    SPRITE_TYPE_PARK_MOUNT,
    SPRITE_TYPE_PRISM,
    SPRITE_TYPE_TAX_COLLECTOR,
    is_gm_entity_list,
    parse_gm_entities,
)
from protocol.messages.stats import (
    ENTITY_OFFLINE_CHARACTER,
    ENTITY_PARK_MOUNT,
    ENTITY_PRISM,
    ENTITY_TAX_COLLECTOR,
    EntityInfo,
)

# Percepteur de la guilde « Guilde-Test », cellule 331 — celui sur lequel le bot
# s'acharnait en le prenant pour un groupe de 2 monstres de niveau 200.
PERCEPTEUR = "|%2B331;3;0;-284290;39,31;-6;6000^100;200;Guilde-Test;8,2lvp2,8,9zldr"

PRISME = "|%2B414;3;0;-170087;1111;-10;8101^100;1;3;1"
MONTURE_PARC = "|%2B370;1;0;-1628860;SansNom;-9;7002^50;Proprio-Test;1;15"
HORS_LIGNE = "|%2B281;1;0;379697;Marchand-Test;-5;110^100;1f78;f03d14;b5db;,,,,;;;0;"
PNJ = "|%2B220;1;0;-6;857;-4;30^100;0;6363b3;ffe926;d1cdad;0,1b4c,0,0,0;;9089"
JOUEUR = (
    "|%2B118;5;0;410896;Joueur-Test;10;101^100;1;2,0,0,411042,0;3e3e3e;d0d0d;3e3e3e;"
    "197b,4aff~16~-1,2118,,;1;;;Dragodinde noire;"
)
GROUPE_MONSTRES = "|~430;1;0;-3058;297,297,48;-3;1070^103,1070^103,1569^102;44,44,10;-1,-1,-1;,,,,"


class TestTaxCollector:
    """Un percepteur (sprite type -6) ne doit jamais être pris pour un monstre."""

    def test_not_parsed_as_monster(self) -> None:
        _, monsters, _, _ = parse_gm_entities(PERCEPTEUR)
        assert monsters == []

    def test_not_parsed_as_player(self) -> None:
        players, _, _, _ = parse_gm_entities(PERCEPTEUR)
        assert players == []

    def test_parsed_as_other(self) -> None:
        _, _, _, others = parse_gm_entities(PERCEPTEUR)
        assert len(others) == 1
        assert others[0].sprite_type == SPRITE_TYPE_TAX_COLLECTOR

    def test_fields(self) -> None:
        _, _, _, others = parse_gm_entities(PERCEPTEUR)
        perco = others[0]
        assert perco.cell_id == 331
        assert perco.entity_id == "-284290"
        assert perco.level == 200
        assert perco.guild_name == "Guilde-Test"
        assert perco.gfx_id == "6000"

    def test_entity_is_not_targetable(self) -> None:
        """is_monster=False → start_fight_biggest_group ne le retiendra pas."""
        _, _, _, others = parse_gm_entities(PERCEPTEUR)
        entity = EntityInfo.from_gm_other(others[0])
        assert entity.entity_type == ENTITY_TAX_COLLECTOR
        assert entity.is_monster is False
        assert entity.monster_ids == []

    def test_display_name(self) -> None:
        _, _, _, others = parse_gm_entities(PERCEPTEUR)
        entity = EntityInfo.from_gm_other(others[0])
        assert entity.display_name == "Percepteur [Guilde-Test] Nv.200"


class TestOtherDecorEntities:
    """Prismes, montures en parc et persos hors-ligne : id négatif mais non attaquables."""

    @pytest.mark.parametrize("payload", [PRISME, MONTURE_PARC, HORS_LIGNE])
    def test_never_a_monster(self, payload: str) -> None:
        _, monsters, _, others = parse_gm_entities(payload)
        assert monsters == []
        assert len(others) == 1

    def test_prism(self) -> None:
        _, _, _, others = parse_gm_entities(PRISME)
        assert others[0].sprite_type == SPRITE_TYPE_PRISM
        assert others[0].cell_id == 414
        assert EntityInfo.from_gm_other(others[0]).entity_type == ENTITY_PRISM

    def test_park_mount(self) -> None:
        _, _, _, others = parse_gm_entities(MONTURE_PARC)
        mount = others[0]
        assert mount.sprite_type == SPRITE_TYPE_PARK_MOUNT
        assert mount.owner_name == "Proprio-Test"
        assert mount.level == 1
        assert EntityInfo.from_gm_other(mount).entity_type == ENTITY_PARK_MOUNT

    def test_offline_character_not_a_player(self) -> None:
        """Un marchand hors-ligne a un id POSITIF : il ne doit pas compter comme joueur
        (sinon `has_foreign_player_on_map()` ralentit le farm pour rien)."""
        players, _, _, others = parse_gm_entities(HORS_LIGNE)
        assert players == []
        assert others[0].sprite_type == SPRITE_TYPE_OFFLINE_CHARACTER
        assert others[0].name == "Marchand-Test"
        assert EntityInfo.from_gm_other(others[0]).entity_type == ENTITY_OFFLINE_CHARACTER


class TestNoRegression:
    """Les types déjà gérés doivent continuer à être classés à l'identique."""

    def test_monster_group(self) -> None:
        _, monsters, _, others = parse_gm_entities(GROUPE_MONSTRES)
        assert others == []
        assert len(monsters) == 1
        group = monsters[0]
        assert group.cell_id == 430
        assert group.entity_id == "-3058"
        assert group.monster_ids == [297, 297, 48]
        assert group.monster_levels == [44, 44, 10]
        assert group.group_size == 3

    def test_npc(self) -> None:
        _, monsters, npcs, others = parse_gm_entities(PNJ)
        assert monsters == [] and others == []
        assert len(npcs) == 1
        assert npcs[0].entity_id == "-6"
        assert npcs[0].cell_id == 220

    def test_player(self) -> None:
        players, monsters, npcs, others = parse_gm_entities(JOUEUR)
        assert monsters == [] and npcs == [] and others == []
        assert len(players) == 1
        assert players[0].name == "Joueur-Test"
        assert players[0].breed == 10
        assert players[0].cell_id == 118

    def test_mixed_payload(self) -> None:
        """Un GM réel mélange les types dans un même message pipe-delimited."""
        payload = PERCEPTEUR + GROUPE_MONSTRES + PNJ + JOUEUR
        players, monsters, npcs, others = parse_gm_entities(payload)
        assert len(players) == 1
        assert len(monsters) == 1
        assert len(npcs) == 1
        assert len(others) == 1

    @pytest.mark.parametrize(
        "payload",
        [PERCEPTEUR, PRISME, MONTURE_PARC, HORS_LIGNE, PNJ, JOUEUR, GROUPE_MONSTRES],
    )
    def test_recognised_as_entity_list(self, payload: str) -> None:
        assert is_gm_entity_list(payload)
