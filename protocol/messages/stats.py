"""
Dataclasses pour les messages de statistiques et d'entités.

Messages couverts :
  As  — AccountStats     (S→C) : stats d'un personnage
  NL  — GameEntityCreate (S→C) : entité sur la carte (préfixe K+)
  GM  — GameMovement     (S→C) : entités visibles (joueurs, monstres, PNJ…)
"""

from __future__ import annotations
from dataclasses import dataclass, field
from typing import Literal

ENTITY_PLAYER: Literal["player"] = "player"
ENTITY_MONSTER_GROUP: Literal["monster_group"] = "monster_group"
ENTITY_NPC: Literal["npc"] = "npc"
ENTITY_TAX_COLLECTOR: Literal["tax_collector"] = "tax_collector"
ENTITY_PRISM: Literal["prism"] = "prism"
ENTITY_PARK_MOUNT: Literal["park_mount"] = "park_mount"
ENTITY_OFFLINE_CHARACTER: Literal["offline_character"] = "offline_character"
ENTITY_OTHER: Literal["other"] = "other"

ENTITY_TYPE_LABELS: dict[str, str] = {
    ENTITY_TAX_COLLECTOR:     "Percepteur",
    ENTITY_PRISM:             "Prisme",
    ENTITY_PARK_MOUNT:        "Monture",
    ENTITY_OFFLINE_CHARACTER: "Hors-ligne",
    ENTITY_OTHER:             "Autre",
}

CLASSES: dict[int, str] = {
    1: "Féca", 2: "Osamodas", 3: "Enutrof", 4: "Sram", 5: "Xélor",
    6: "Ecaflip", 7: "Eniripsa", 8: "Iop", 9: "Crâ", 10: "Sadida",
    11: "Sacrieur", 12: "Pandawa",
}


# ---------------------------------------------------------------------------
# CharacterStats — message As
# ---------------------------------------------------------------------------

@dataclass
class CharacterStats:
    """Stats d'un personnage depuis le message As.

    Format payload :
      expA,expC,expNext|kamas|lvlPct|actionPoints|conditions|hp,maxHp|
      energy,maxEnergy|initiative|prospecting|stat1_b,s1_bonus,s1_item,s1_set|...

    Les groupes de stats (champs 9+) sont dans l'ordre Dofus 1.29 :
      VIT(11), SAG(12), FOR(13), INT(14), CHA(15), AGI(16)
    """

    kamas: int = 0
    level_pct: int = 0       # % de progression dans le niveau
    action_points: int = 6   # PA — sum(parts[9])
    movement_points: int = 3  # PM — sum(parts[10])
    life: int = 0
    max_life: int = 0
    energy: int = 0
    max_energy: int = 10000
    initiative: int = 0
    prospecting: int = 0

    # Caractéristiques [base, bonus, item, set] — champs [11] à [16] du message As
    # Ordre confirmé : Force[11], Vitalité[12], Sagesse[13], Intelligence[14], Chance[15], Agilité[16]
    strength:     list[int] = field(default_factory=lambda: [0, 0, 0, 0])
    vitality:     list[int] = field(default_factory=lambda: [0, 0, 0, 0])
    wisdom:       list[int] = field(default_factory=lambda: [0, 0, 0, 0])
    intelligence: list[int] = field(default_factory=lambda: [0, 0, 0, 0])
    chance:       list[int] = field(default_factory=lambda: [0, 0, 0, 0])
    agility:      list[int] = field(default_factory=lambda: [0, 0, 0, 0])

    # Portée (PO) [base, bonus, item, set] — champ [17] du message As (après les 6 stats principales)
    range_points: list[int] = field(default_factory=lambda: [0, 0, 0, 0])

    @property
    def total_strength(self) -> int:     return sum(self.strength)
    @property
    def total_vitality(self) -> int:     return sum(self.vitality)
    @property
    def total_wisdom(self) -> int:       return sum(self.wisdom)
    @property
    def total_intelligence(self) -> int: return sum(self.intelligence)
    @property
    def total_chance(self) -> int:       return sum(self.chance)
    @property
    def total_agility(self) -> int:      return sum(self.agility)
    @property
    def total_range_points(self) -> int: return sum(self.range_points)

    @classmethod
    def parse(cls, payload: str) -> "CharacterStats":
        """
        Format As (champs séparés par '|') :
          [0]  expA,expC,expNext
          [1]  kamas
          [2]  level_pct (% dans le niveau)
          [3]  inconnu
          [4]  conditions~flags
          [5]  hp,maxHp
          [6]  energy,maxEnergy
          [7]  initiative
          [8]  prospecting
          [9]  pa_base,pa_bonus,pa_item,pa_set   → action_points = somme
          [10] pm_base,pm_bonus,pm_item,pm_set   → movement_points = somme
          [11] force (base,bonus,item,set)
          [12] vitalité
          [13] sagesse
          [14] intelligence
          [15] chance
          [16] agilité
        """
        parts = payload.split("|")
        obj = cls()

        def _int(s: str) -> int:
            s = s.strip()
            return int(s) if s.lstrip("-").isdigit() else 0

        def _stat_group(s: str) -> list[int]:
            vals = s.split(",")
            return [_int(vals[i]) if i < len(vals) else 0 for i in range(4)]

        if len(parts) > 1:
            obj.kamas = _int(parts[1])
        if len(parts) > 2:
            obj.level_pct = _int(parts[2])
        if len(parts) > 5:
            hp = parts[5].split(",")
            if len(hp) >= 2:
                obj.life, obj.max_life = _int(hp[0]), _int(hp[1])
        if len(parts) > 6:
            en = parts[6].split(",")
            if len(en) >= 2:
                obj.energy, obj.max_energy = _int(en[0]), _int(en[1])
        if len(parts) > 7:
            obj.initiative = _int(parts[7])
        if len(parts) > 8:
            obj.prospecting = _int(parts[8])
        # PA et PM : champs [9] et [10] au format base,bonus,item,set
        if len(parts) > 9:
            obj.action_points = sum(_stat_group(parts[9]))
        if len(parts) > 10:
            obj.movement_points = sum(_stat_group(parts[10]))
        # Caractéristiques : champs [11]→[16]
        # Ordre confirmé : Force[11], Vitalité[12], Sagesse[13], Chance[14], Agilité[15], Intelligence[16]
        stat_attrs = ["strength", "vitality", "wisdom", "chance", "agility", "intelligence"]
        for i, attr in enumerate(stat_attrs):
            idx = 11 + i
            if len(parts) > idx:
                setattr(obj, attr, _stat_group(parts[idx]))
        # Portée (PO) : champ [17], après les 6 stats principales
        if len(parts) > 17:
            obj.range_points = _stat_group(parts[17])

        return obj


# ---------------------------------------------------------------------------
# EntityInfo — message NL (S→C, préfixe K+)
# ---------------------------------------------------------------------------

@dataclass
class EntityInfo:
    """Entité sur la carte (joueur, monstre, PNJ, percepteur).

    Créée depuis NL (K+), GM (entity list), ou hP (HerePlayer).
    """

    entity_id: str
    name: str = ""
    level: int = 0
    class_id: int = 0
    sex: int = 0        # 0=masculin, 1=féminin
    cell_id: int = -1   # -1 = inconnu
    direction: int = 0

    entity_type: str = ENTITY_PLAYER  # cf. constantes ENTITY_* ci-dessus
    sprite_type: int = 0    # champ 5 du GM (négatif = entité non-joueur)
    monster_ids: list[int] = field(default_factory=list)
    monster_levels: list[int] = field(default_factory=list)
    bonus: int = 0          # bonus étoile des groupes de monstres
    guild_name: str = ""

    # Champs additionnels parsés depuis GM (joueurs)
    gfx_id: str = ""
    scale: int = 100
    align_side: int = 0
    align_rank: int = 0
    align_honor: int = 0
    align_disgrace: int = 0
    align_extra: str = ""
    color1: str = ""
    color2: str = ""
    color3: str = ""
    accessories: list[str] = field(default_factory=list)
    aura: str = ""
    emblem: str = ""
    restrictions: str = ""
    mount: str = ""

    @property
    def class_name(self) -> str:
        if self.entity_type == ENTITY_MONSTER_GROUP:
            n = len(self.monster_ids) if self.monster_ids else 1
            return f"Groupe ({n})"
        label = ENTITY_TYPE_LABELS.get(self.entity_type)
        if label:
            return label
        return CLASSES.get(self.class_id, f"#{self.class_id}")

    @property
    def is_monster(self) -> bool:
        return self.entity_type == ENTITY_MONSTER_GROUP

    @property
    def display_name(self) -> str:
        if self.entity_type == ENTITY_MONSTER_GROUP:
            n = len(self.monster_ids) if self.monster_ids else 1
            lvl = sum(self.monster_levels) if self.monster_levels else 0
            star = f" ({self.bonus}%)" if self.bonus else ""
            return f"{n} monstres Nv.{lvl}{star}"
        if self.entity_type == ENTITY_TAX_COLLECTOR:
            guild = f" [{self.guild_name}]" if self.guild_name else ""
            return f"Percepteur{guild} Nv.{self.level}"
        return self.name

    @classmethod
    def parse_server(cls, payload: str) -> "EntityInfo | None":
        """Parser un message NL côté serveur (préfixe K+)."""
        if not payload.startswith("K+"):
            return None
        parts = payload[2:].split(";")
        if len(parts) < 2:
            return None

        def _int(s: str) -> int:
            s = s.strip()
            return int(s) if s.lstrip("-").isdigit() else 0

        obj = cls(entity_id=parts[0])
        if len(parts) > 1:  obj.name     = parts[1]
        if len(parts) > 2:  obj.level    = _int(parts[2])
        if len(parts) > 4:  obj.class_id = _int(parts[4])
        if len(parts) > 9:  obj.sex      = _int(parts[9])
        if len(parts) > 10: obj.cell_id  = _int(parts[10])
        if len(parts) > 11: obj.direction = _int(parts[11])
        return obj

    @classmethod
    def from_gm_player(cls, entry: "GMPlayerEntry") -> "EntityInfo":
        """Construire depuis un GMPlayerEntry parsé du message GM."""
        from protocol.messages.map import GMPlayerEntry  # noqa: F811
        return cls(
            entity_id=entry.entity_id,
            name=entry.name,
            class_id=entry.breed,
            sex=entry.sex,
            cell_id=entry.cell_id,
            direction=entry.direction,
            entity_type=ENTITY_PLAYER,
            guild_name=entry.guild_name,
            gfx_id=entry.gfx_id,
            scale=entry.scale,
            align_side=entry.align_side,
            align_rank=entry.align_rank,
            align_honor=entry.align_honor,
            align_disgrace=entry.align_disgrace,
            align_extra=entry.align_extra,
            color1=entry.color1,
            color2=entry.color2,
            color3=entry.color3,
            accessories=list(entry.accessories),
            aura=entry.aura,
            emblem=entry.emblem,
            restrictions=entry.restrictions,
            mount=entry.mount,
        )

    @classmethod
    def from_gm_npc(cls, npc: "GMNpcEntry") -> "EntityInfo":
        """Construire depuis un GMNpcEntry parsé du message GM (PNJ)."""
        from protocol.messages.map import GMNpcEntry  # noqa: F811
        return cls(
            entity_id=npc.entity_id,
            name=f"PNJ {npc.gfx_id}",
            cell_id=npc.cell_id,
            direction=npc.direction,
            entity_type=ENTITY_NPC,
            gfx_id=npc.gfx_id,
            scale=npc.scale,
        )

    @classmethod
    def from_gm_other(cls, other: "GMOtherEntry") -> "EntityInfo":
        """Construire depuis un GMOtherEntry (percepteur, prisme, monture, hors-ligne).

        Ces entités occupent une cellule mais ne sont **jamais** une cible de combat :
        ``is_monster`` reste False, donc ``start_fight_biggest_group`` les ignore.
        """
        from protocol.messages.map import (  # noqa: F811
            GMOtherEntry,
            SPRITE_TYPE_OFFLINE_CHARACTER,
            SPRITE_TYPE_PARK_MOUNT,
            SPRITE_TYPE_PRISM,
            SPRITE_TYPE_TAX_COLLECTOR,
        )

        entity_type = {
            SPRITE_TYPE_TAX_COLLECTOR:     ENTITY_TAX_COLLECTOR,
            SPRITE_TYPE_PRISM:             ENTITY_PRISM,
            SPRITE_TYPE_PARK_MOUNT:        ENTITY_PARK_MOUNT,
            SPRITE_TYPE_OFFLINE_CHARACTER: ENTITY_OFFLINE_CHARACTER,
        }.get(other.sprite_type, ENTITY_OTHER)

        # Nom lisible : le champ brut du GM n'est exploitable que pour un joueur
        # hors-ligne ou une monture. Un percepteur porte deux ids de noms
        # ("39,31") résolus par les fichiers lang du client, et un prisme son
        # templateId — dans ces cas on retombe sur le libellé du type.
        if other.sprite_type in (SPRITE_TYPE_OFFLINE_CHARACTER, SPRITE_TYPE_PARK_MOUNT):
            name = other.name
        else:
            name = ENTITY_TYPE_LABELS.get(entity_type, "Entité")

        return cls(
            entity_id=other.entity_id,
            name=name,
            level=other.level,
            cell_id=other.cell_id,
            direction=other.direction,
            entity_type=entity_type,
            sprite_type=other.sprite_type,
            guild_name=other.guild_name,
            gfx_id=other.gfx_id,
            scale=other.scale,
        )

    @classmethod
    def from_gm_monster(cls, group: "GMMonsterGroup") -> "EntityInfo":
        """Construire depuis un GMMonsterGroup parsé du message GM."""
        from protocol.messages.map import GMMonsterGroup  # noqa: F811
        total_level = sum(group.monster_levels) if group.monster_levels else 0
        return cls(
            entity_id=group.entity_id,
            name=f"{group.group_size} monstres",
            level=total_level,
            cell_id=group.cell_id,
            direction=group.direction,
            entity_type=ENTITY_MONSTER_GROUP,
            monster_ids=list(group.monster_ids),
            monster_levels=list(group.monster_levels),
            bonus=group.bonus,
        )
