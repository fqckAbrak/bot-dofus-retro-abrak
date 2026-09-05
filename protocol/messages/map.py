"""
Dataclasses pour les messages liés à la carte et aux entités.

Messages couverts :
  GDM  — GameMapData              (S→C) : nouvelle carte chargée
  GDF  — GameFrameObject2         (S→C) : état des éléments interactifs
  GM   — GameMovement             (S→C) : déplacement d'une entité / liste d'entités
  GA   — GameActionsSendActions   (C→S) : action du joueur (mouvement, récolte…)
  GDK  — GameMapLoaded            (S→C) : carte prête (aucun payload)
  GM|- — GameMovementRemove       (S→C) : entité retirée de la carte
"""

from __future__ import annotations
from dataclasses import dataclass, field
from urllib.parse import unquote


# ---------------------------------------------------------------------------
# GDM — GameMapData
# ---------------------------------------------------------------------------


@dataclass
class MapData:
    """Données de la carte reçues via GDM.

    Format payload : |{mapId}|{date}|{key}
    (le premier champ est vide — séparateur de début)
    """

    map_id: int
    date: str
    key: str

    @classmethod
    def parse(cls, payload: str) -> "MapData":
        """Parser le payload du message GDM.

        Args:
            payload: Tout ce qui suit l'ID 'GDM', ex: '|7348|0905131019|765f...'

        Returns:
            Instance MapData.
        """
        parts = payload.split("|")
        # Format : ['', mapId, date, key]  ou  [mapId, date, key]
        parts = [p for p in parts if p]  # supprimer les vides
        if len(parts) < 3:
            raise ValueError(f"Payload GDM invalide : {payload!r}")
        return cls(
            map_id=int(parts[0]),
            date=parts[1],
            key=parts[2],
        )

    def __str__(self) -> str:
        return f"Map#{self.map_id} (date={self.date}, key={self.key[:16]}…)"


# ---------------------------------------------------------------------------
# GDF — GameFrameObject2 (éléments interactifs)
# ---------------------------------------------------------------------------


@dataclass
class InteractiveElement:
    """Un élément interactif sur la carte (ressource, porte, coffre…).

    Format d'un champ GDF : {elemId};{state};{type}
      - state 0 = cooldown (déjà récolté / en attente)
      - state 1 = disponible
    """

    elem_id: int
    state: int       # 0 = cooldown, 1 = available
    elem_type: int   # type GA500 (ex: 6 pour frêne) — issu de Recolte.txt
    resource_id: int = 0  # layer_obj2 (ex: 7500) — pour le nom affiché

    @property
    def available(self) -> bool:
        # state=5 → disponible (LeafMITM : action==5 = "Can récolte")
        # state=2,3,4 → indisponible (cooldown / en cours / inaccessible)
        # state=1 gardé comme fallback au cas où ce serveur privé diffère
        return self.state in (1, 5)

    @classmethod
    def parse(cls, field: str) -> "InteractiveElement":
        """Parser un champ ';'-séparé.

        Args:
            field: ex '327;0;1'
        """
        parts = field.split(";")
        if len(parts) < 3:
            raise ValueError(f"Champ InteractiveElement invalide : {field!r}")
        return cls(
            elem_id=int(parts[0]),
            state=int(parts[1]),
            elem_type=int(parts[2]),
        )


@dataclass
class FrameObjects:
    """Ensemble des éléments interactifs d'une carte (message GDF).

    Format payload : |{e1}|{e2}|...  où chaque {eN} = 'elemId;state;type'
    """

    elements: dict[int, InteractiveElement] = field(default_factory=dict)

    @classmethod
    def parse(cls, payload: str) -> "FrameObjects":
        """Parser le payload GDF complet.

        Args:
            payload: ex '||327;0;1|357;0;1|373;0;1'
        """
        obj = cls()
        for part in payload.split("|"):
            part = part.strip()
            if not part or ";" not in part:
                continue
            try:
                elem = InteractiveElement.parse(part)
                obj.elements[elem.elem_id] = elem
            except ValueError:
                pass
        return obj

    @property
    def available(self) -> list[InteractiveElement]:
        """Éléments disponibles à la récolte."""
        return [e for e in self.elements.values() if e.available]

    def __str__(self) -> str:
        total = len(self.elements)
        avail = len(self.available)
        return f"FrameObjects: {avail}/{total} disponibles"


# ---------------------------------------------------------------------------
# GM — GameMovement (entité qui se déplace)
# ---------------------------------------------------------------------------


@dataclass
class EntityMovement:
    """Déplacement d'une entité sur la carte (message GM).

    Le format GM est complexe et varie selon le type d'entité.
    Format courant : GM{entity_id};{path_encoded};...
    """

    raw: str          # payload brut — parsing complet en Phase 3
    entity_id: str
    path_encoded: str  # chemin en Base64 Dofus (à décoder avec encoding.decode_path)

    @classmethod
    def parse(cls, payload: str) -> "EntityMovement":
        """Parser le payload GM.

        Args:
            payload: Tout ce qui suit 'GM', ex '12345;aAbB...'
        """
        parts = payload.split(";", 1)
        entity_id = parts[0] if parts else ""
        path = parts[1] if len(parts) > 1 else ""
        return cls(raw=payload, entity_id=entity_id, path_encoded=path)


# ---------------------------------------------------------------------------
# GM — Liste d'entités (joueurs, monstres, PNJ) envoyée après GDK
# ---------------------------------------------------------------------------

@dataclass
class GMPlayerEntry:
    """Joueur visible sur la carte (depuis GM entity list).

    Format : +{cell};{dir};{bonus};{id};{name};{breed};{gfx}^{scale};{sex};
             {alignInfos};{c1};{c2};{c3};{accessories};{aura};{emote};
             {emoteTimer};{guildName};{emblem};{restrictions};{mount};...
    """
    cell_id: int
    direction: int
    entity_id: str
    name: str
    breed: int = 0
    gfx_id: str = ""
    scale: int = 100
    sex: int = 0
    align_side: int = 0       # 0=neutre, 1=Bonta, 2=Brâkmar, 3=Sufokia
    align_rank: int = 0
    align_honor: int = 0
    align_disgrace: int = 0
    align_extra: str = ""     # champ alignement résiduel (raw)
    color1: str = ""          # couleur skin 1 (hex)
    color2: str = ""
    color3: str = ""
    accessories: list[str] = field(default_factory=list)  # head, cloak, weapon, shield, pet
    aura: str = ""
    emote: str = ""
    emote_timer: str = ""
    guild_name: str = ""
    emblem: str = ""
    restrictions: str = ""
    mount: str = ""


@dataclass
class GMMonsterGroup:
    """Groupe de monstres visible sur la carte (depuis GM entity list).

    Format : ~{cell};{dir};{bonus};{id};{monsterIds,...};{grades};
             {gfxs,...};{levels,...};{colors,...};{accessories};...
    """
    cell_id: int
    direction: int
    entity_id: str
    monster_ids: list[int] = field(default_factory=list)
    monster_levels: list[int] = field(default_factory=list)
    monster_gfxs: list[str] = field(default_factory=list)
    bonus: int = 0

    @property
    def total_level(self) -> int:
        return sum(self.monster_levels) if self.monster_levels else 0

    @property
    def group_size(self) -> int:
        return len(self.monster_ids) if self.monster_ids else 1


@dataclass
class GMNpcEntry:
    """PNJ (marchand, banquier, donneur de quête…) visible sur la carte.

    Format : +{cell};{dir};{0};{npcId};{gfxId};{spriteType=-4};{anim}^{scale};...
    Le ``npcId`` est NÉGATIF (ex -3). C'est l'identifiant à passer à
    ``ER{type}|{npcId}`` pour lancer un échange avec ce PNJ.
    """
    cell_id: int
    direction: int
    entity_id: str        # id négatif du PNJ (= cible de l'échange ER)
    gfx_id: str = ""      # apparence du PNJ (permet d'identifier le marchand)
    scale: int = 100


@dataclass
class GMOtherEntry:
    """Entité de décor NON attaquable au corps à corps : percepteur, prisme,
    monture en parc, personnage hors-ligne (marchand).

    Ces entités ont un id NÉGATIF comme les monstres et étaient donc parsées comme
    des « groupes de monstres » : le bot leur envoyait un ``GA907`` que le serveur
    ignore silencieusement, puis restait bloqué à retenter (cf. percepteur cell 331).

    Formats (cf. retroproto ``msgsvr.GameMovement``), tous préfixés par
    ``{cell};{dir};0;{id};`` puis, en champ 5, le sprite type :

      -5  hors-ligne  {name};-5;{gfx}^{scale};{c1};{c2};{c3};{acc};{guild};{emblem};{type}
      -6  percepteur  {firstName,lastName};-6;{gfx}^{scale};{level};{guild};{emblem}
      -9  monture     {name};-9;{gfx}^{scale};{ownerName};{level};{modelId}
      -10 prisme      {templateId};-10;{gfx}^{scale};{level};{alignValue};{alignIndex}
    """
    cell_id: int
    direction: int
    entity_id: str
    sprite_type: int
    name: str = ""        # brut ; pour un percepteur = "{firstNameId},{lastNameId}"
    gfx_id: str = ""
    scale: int = 100
    level: int = 0
    guild_name: str = ""
    owner_name: str = ""  # monture en parc : propriétaire


def _safe_int(s: str) -> int:
    s = s.strip()
    return int(s) if s.lstrip("-").isdigit() else 0


# Sprite types (cf. retroproto enum.GameMovementSpriteType) — champ index 5
# d'une entrée GM. Négatif = entité non-joueur ; pour un joueur ce champ porte
# la classe (1-12), éventuellement suffixée du titre ("10,5*param").
SPRITE_TYPE_CREATURE = -1
SPRITE_TYPE_MONSTER = -2
SPRITE_TYPE_MONSTER_GROUP = -3
SPRITE_TYPE_NPC = -4
SPRITE_TYPE_OFFLINE_CHARACTER = -5
SPRITE_TYPE_TAX_COLLECTOR = -6
SPRITE_TYPE_MUTANT = -7
SPRITE_TYPE_MUTANT_PLAYER = -8
SPRITE_TYPE_PARK_MOUNT = -9
SPRITE_TYPE_PRISM = -10

# Sprite types réellement attaquables via GA907 : un monstre solo/créature en
# placement de combat, ou un groupe de monstres sur la carte.
_ATTACKABLE_SPRITE_TYPES = frozenset({
    SPRITE_TYPE_CREATURE, SPRITE_TYPE_MONSTER, SPRITE_TYPE_MONSTER_GROUP,
})

# Sprite types de décor : présents sur la carte, jamais une cible de combat.
_OTHER_SPRITE_TYPES = frozenset({
    SPRITE_TYPE_OFFLINE_CHARACTER, SPRITE_TYPE_TAX_COLLECTOR,
    SPRITE_TYPE_MUTANT, SPRITE_TYPE_MUTANT_PLAYER,
    SPRITE_TYPE_PARK_MOUNT, SPRITE_TYPE_PRISM,
})


def _parse_other_entry(
    fields: list[str],
    cell_id: int,
    direction: int,
    entity_id: int,
    sprite_type: int,
) -> GMOtherEntry:
    """Construire un GMOtherEntry en lisant les champs propres à son sprite type."""
    gfx_raw = fields[6] if len(fields) > 6 else ""
    if "^" in gfx_raw:
        gfx, scale_str = gfx_raw.split("^", 1)
        scale = _safe_int(scale_str.rstrip("*")) or 100
    else:
        gfx, scale = gfx_raw, 100

    entry = GMOtherEntry(
        cell_id=cell_id,
        direction=direction,
        entity_id=str(entity_id),
        sprite_type=sprite_type,
        name=fields[4] if len(fields) > 4 else "",
        gfx_id=gfx,
        scale=scale,
    )

    if sprite_type == SPRITE_TYPE_TAX_COLLECTOR:
        # {level};{guildName};{guildEmblem}
        entry.level = _safe_int(fields[7]) if len(fields) > 7 else 0
        entry.guild_name = fields[8] if len(fields) > 8 else ""
    elif sprite_type == SPRITE_TYPE_PARK_MOUNT:
        # {ownerName};{level};{modelId}
        entry.owner_name = fields[7] if len(fields) > 7 else ""
        entry.level = _safe_int(fields[8]) if len(fields) > 8 else 0
    elif sprite_type == SPRITE_TYPE_PRISM:
        # {level};{alignmentValue};{alignmentIndex}
        entry.level = _safe_int(fields[7]) if len(fields) > 7 else 0
    elif sprite_type == SPRITE_TYPE_OFFLINE_CHARACTER:
        # {c1};{c2};{c3};{accessories};{guildName};{guildEmblem};{offlineType}
        entry.guild_name = fields[11] if len(fields) > 11 else ""

    return entry


def parse_gm_entities(
    payload: str,
) -> tuple[list[GMPlayerEntry], list[GMMonsterGroup], list[GMNpcEntry], list[GMOtherEntry]]:
    """Parser le payload GM contenant la liste d'entités d'une carte.

    Le serveur envoie ce message après GDK avec toutes les entités visibles.
    Le payload est pipe-delimited, chaque entrée préfixée par ``+``/``%2B``
    (entité ajoutée) ou ``~`` (entité déjà présente).

    Discrimination : c'est le **sprite type** (champ 5) qui détermine le type
    d'entité, PAS le signe de l'id (champ 3). Percepteurs, prismes, montures en
    parc et PNJ ont tous un id négatif comme les monstres.

      * ``-1`` créature, ``-2`` monstre, ``-3`` groupe  → cible de combat valide
      * ``-4`` PNJ                                      → échange ``ER{type}|{id}``
      * ``-5`` hors-ligne, ``-6`` percepteur, ``-7``/``-8`` mutant,
        ``-9`` monture en parc, ``-10`` prisme          → décor, non attaquable
      * ``>= 0`` (classe 1-12, ou 0 si suffixé du titre) → joueur

    ⚠️ Trier sur le signe de l'id classait percepteurs, prismes et montures parmi
    les « groupes de monstres » : le bot lançait un ``GA907`` que le serveur ignore
    en silence, puis bouclait sur des retries (cf. percepteur cell 331).

    Returns:
        Tuple (players, monster_groups, npcs, others).
    """
    players: list[GMPlayerEntry] = []
    monsters: list[GMMonsterGroup] = []
    npcs: list[GMNpcEntry] = []
    others: list[GMOtherEntry] = []

    decoded = unquote(payload)

    for part in decoded.split("|"):
        part = part.strip()
        if not part:
            continue

        # Retirer le préfixe +/~
        if part[0] in ("+", "~"):
            part = part[1:]
        elif part[0].isdigit() or part[0] == "-":
            pass  # pas de préfixe, juste des données
        else:
            continue

        fields = part.split(";")
        if len(fields) < 5:
            continue

        try:
            cell_id = int(fields[0])
        except ValueError:
            continue

        direction = _safe_int(fields[1]) if len(fields) > 1 else 0

        try:
            entity_id_int = int(fields[3])
        except (ValueError, IndexError):
            continue

        # Champ 5 = sprite type. Pour un joueur il porte la classe (1-12), suivie
        # du titre quand il en a un ("10,5*param") — d'où le split sur ",".
        sprite_type = _safe_int(fields[5].split(",", 1)[0]) if len(fields) > 5 else 0

        if sprite_type == SPRITE_TYPE_NPC:
            # --- PNJ (marchand, banquier, donneur de quête…) ---
            gfx_raw = fields[4] if len(fields) > 4 else ""
            anim_scale = fields[6] if len(fields) > 6 else ""
            scale = 100
            if "^" in anim_scale:
                scale = _safe_int(anim_scale.split("^", 1)[1]) or 100
            npcs.append(GMNpcEntry(
                cell_id=cell_id,
                direction=direction,
                entity_id=str(entity_id_int),
                gfx_id=gfx_raw,
                scale=scale,
            ))
        elif sprite_type in _OTHER_SPRITE_TYPES:
            # --- Décor : percepteur, prisme, monture en parc, hors-ligne ---
            others.append(_parse_other_entry(
                fields, cell_id, direction, entity_id_int, sprite_type,
            ))
        elif sprite_type in _ATTACKABLE_SPRITE_TYPES:
            # --- Groupe de monstres ---
            bonus = _safe_int(fields[2]) if len(fields) > 2 else 0

            mid_strs = fields[4].split(",") if len(fields) > 4 and fields[4] else []
            monster_ids = [int(m) for m in mid_strs if m.lstrip("-").isdigit()]

            gfx_strs = fields[6].split(",") if len(fields) > 6 and fields[6] else []

            lvl_strs = fields[7].split(",") if len(fields) > 7 and fields[7] else []
            levels = [int(lv) for lv in lvl_strs if lv.lstrip("-").isdigit()]

            monsters.append(GMMonsterGroup(
                cell_id=cell_id,
                direction=direction,
                entity_id=str(entity_id_int),
                monster_ids=monster_ids,
                monster_levels=levels,
                monster_gfxs=gfx_strs,
                bonus=bonus,
            ))
        elif sprite_type < 0:
            # Sprite type non-joueur inconnu : le ranger dans « autres » plutôt que
            # de le confondre avec un monstre attaquable ou un joueur.
            others.append(_parse_other_entry(
                fields, cell_id, direction, entity_id_int, sprite_type,
            ))
        else:
            # --- Joueur ---
            name = fields[4] if len(fields) > 4 else ""
            breed = sprite_type

            gfx_raw = fields[6] if len(fields) > 6 else ""
            if "^" in gfx_raw:
                gfx, scale_str = gfx_raw.split("^", 1)
                scale = _safe_int(scale_str) or 100
            else:
                gfx, scale = gfx_raw, 100

            sex = _safe_int(fields[7]) if len(fields) > 7 else 0

            # alignInfos : align_side, align_value, align_grade, honor, disgrace[, ...]
            align_side = 0
            align_rank = 0
            align_honor = 0
            align_disgrace = 0
            align_extra = ""
            if len(fields) > 8 and fields[8]:
                ai = fields[8].split(",")
                if len(ai) > 0: align_side     = _safe_int(ai[0])
                if len(ai) > 2: align_rank     = _safe_int(ai[2])
                if len(ai) > 3: align_honor    = _safe_int(ai[3])
                if len(ai) > 4: align_disgrace = _safe_int(ai[4])
                if len(ai) > 5: align_extra    = ",".join(ai[5:])

            color1 = fields[9]  if len(fields) > 9  else ""
            color2 = fields[10] if len(fields) > 10 else ""
            color3 = fields[11] if len(fields) > 11 else ""

            accessories: list[str] = []
            if len(fields) > 12 and fields[12]:
                accessories = [a.strip() for a in fields[12].split(",")]

            aura         = fields[13] if len(fields) > 13 else ""
            emote        = fields[14] if len(fields) > 14 else ""
            emote_timer  = fields[15] if len(fields) > 15 else ""
            guild_name   = fields[16] if len(fields) > 16 else ""
            emblem       = fields[17] if len(fields) > 17 else ""
            restrictions = fields[18] if len(fields) > 18 else ""
            mount        = fields[19] if len(fields) > 19 else ""

            players.append(GMPlayerEntry(
                cell_id=cell_id,
                direction=direction,
                entity_id=str(entity_id_int),
                name=name,
                breed=breed,
                gfx_id=gfx,
                scale=scale,
                sex=sex,
                align_side=align_side,
                align_rank=align_rank,
                align_honor=align_honor,
                align_disgrace=align_disgrace,
                align_extra=align_extra,
                color1=color1,
                color2=color2,
                color3=color3,
                accessories=accessories,
                aura=aura,
                emote=emote,
                emote_timer=emote_timer,
                guild_name=guild_name,
                emblem=emblem,
                restrictions=restrictions,
                mount=mount,
            ))

    return players, monsters, npcs, others


def is_gm_entity_list(payload: str) -> bool:
    """Détecter si un payload GM est une liste d'entités (vs un simple mouvement).

    Les listes contiennent ``|`` et au moins un préfixe ``~``, ``+`` ou ``%2B``.
    """
    return ("|" in payload
            and ("~" in payload
                 or "%2B" in payload
                 or payload.lstrip("|")[:1] == "+"))
