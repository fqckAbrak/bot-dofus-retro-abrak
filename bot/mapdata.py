"""
Décodage des fichiers XML de map.

Source : ressources/maps/{map_id}.xml
Ces fichiers contiennent MAPA_DATA — les données statiques de chaque cellule
encodées en ZKARRAY (64-char alphabétique), 10 chars par cellule.

Fournit :
  - Largeur réelle de la map (ANCHURA)
  - Liste des cellules praticables (movement != 0)
  - Liste des cellules avec ressources récoltables (layerObject2Num dans Recolte.txt)

Le format ZKARRAY et la table des ressources récoltables suivent la même
convention que le client : cf. docs/RESOURCES.md pour le détail du décodage.
"""

from __future__ import annotations

import logging
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from pathlib import Path

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Constantes
# ---------------------------------------------------------------------------

ZKARRAY = "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789-_"
"""Alphabet ZKARRAY — chaque char → valeur 6 bits, 10 chars/cellule."""

# Répertoires sources
from core.paths import RESSOURCES_DIR

_MAPS_DIR = RESSOURCES_DIR / "maps"
_RECOLTE_FILE = RESSOURCES_DIR / "Recolte.txt"

# Cache des maps déjà décodées
_map_cache: dict[int, "MapInfo"] = {}

# ---------------------------------------------------------------------------
# Ressources récoltables (Recolte.txt)
# ---------------------------------------------------------------------------

_HARVEST_IDS: set[int] = set()
# layer_obj2 → (nom, ga500_elem_type, job_action)
_RESOURCE_DATA: dict[int, tuple[str, int, str]] = {}

# Action de récolte → job_id Dofus Rétro 1.29
# (job 2 = Bûcheron confirmé par JX post-récolte frêne)
JOB_ACTION_TO_ID: dict[str, int] = {
    "Couper":     2,   # Bûcheron
    "Collecter":  11,  # Mineur
    "Faucher":    6,   # Paysan
    "Cueillir":   14,  # Alchimiste
    "Pecher":     10,  # Pêcheur
}


def _load_harvest_ids() -> None:
    """Charger les données Recolte.txt : ID, nom, type GA500 et action métier."""
    global _HARVEST_IDS, _RESOURCE_DATA
    if _HARVEST_IDS:
        return
    if not _RECOLTE_FILE.exists():
        logger.warning("[mapdata] Recolte.txt introuvable : %s", _RECOLTE_FILE)
        return
    ids: set[int] = set()
    data: dict[int, tuple[str, int, str]] = {}
    for line in _RECOLTE_FILE.read_text(encoding="utf-8").splitlines():
        parts = line.strip().split("|")
        if len(parts) < 4 or not parts[0].isdigit():
            continue
        rid = int(parts[0])
        name = parts[1]
        job_action = parts[2].strip().split(":")[0]  # "Faucher:Cueillir" → "Faucher"
        # Le type GA500 peut être multiple (ex: "50:68") — prendre le premier
        ga_type_raw = parts[3].split(":")[0].strip()
        if not ga_type_raw.isdigit():
            continue
        ga_type = int(ga_type_raw)
        ids.add(rid)
        data[rid] = (name, ga_type, job_action)
    _HARVEST_IDS = ids
    _RESOURCE_DATA = data
    logger.info("[mapdata] %d types de ressources récoltables chargés", len(ids))


def get_resource_name(layer_obj2: int) -> str:
    """Retourner le nom d'une ressource depuis son layerObject2Num."""
    _load_harvest_ids()
    info = _RESOURCE_DATA.get(layer_obj2)
    return info[0] if info else f"#{layer_obj2}"


def get_resource_ga_type(layer_obj2: int) -> int:
    """Retourner le type GA500 d'une ressource depuis son layerObject2Num."""
    _load_harvest_ids()
    info = _RESOURCE_DATA.get(layer_obj2)
    return info[1] if info else 0


def get_resource_job_id(layer_obj2: int) -> int:
    """Retourner le job_id associé à une ressource (0 si inconnu)."""
    _load_harvest_ids()
    info = _RESOURCE_DATA.get(layer_obj2)
    if info is None:
        return 0
    return JOB_ACTION_TO_ID.get(info[2], 0)


# ---------------------------------------------------------------------------
# Décodage d'une cellule (port de LeafMITM map/cell.py)
# ---------------------------------------------------------------------------

# Identifiants des cellules "Sol Magique" (soleil) — sorties de map et téléporteurs.
# Source : LeafMITM map/contants.py SUN_MAGICS
SUN_MAGIC_IDS: frozenset[int] = frozenset({1030, 1029, 4088})


@dataclass
class CellData:
    cell_id: int
    is_active: bool
    is_interactive: bool
    movement: int        # 0=impassable, 1=passable, 4=lent, etc.
    layer_obj1: int      # layerObject1Num — Sol Magique si in SUN_MAGIC_IDS
    layer_obj2: int      # layerObject2Num — type de ressource si récoltable
    line_of_sight: bool = False  # True = TRANSPARENTE (LdV passe) ; False = opaque/bloque (bit 0 de cd[0], source LeafMITM cell.py)
    ground_level: int = 0        # hauteur du sol (cd[1] & 15) — CRITIQUE pour la LdV : le
                                 # client Dofus calcule la ligne de vue en 3D (getCellHeight),
                                 # une cellule bloque si sa hauteur dépasse la ligne de visée.
    ground_slope: int = 0        # pente du sol (cd[1] & 48) >> 4

    @property
    def is_sun_magic(self) -> bool:
        """True si c'est une cellule Sol Magique (sortie de map / téléporteur)."""
        return self.layer_obj1 in SUN_MAGIC_IDS


def _decode_cell(raw10: str, cell_id: int) -> CellData:
    """Décoder 10 caractères ZKARRAY en données de cellule.

    Port de LeafMITM map/cell.py Cell.__init__() :
      cd = [ZKARRAY.index(c) for c in raw10]  # 10 valeurs 6-bit
      isActive      = cd[2] != 0 and cd[0] != 1 and not (cd[0]==33 and cd[2]==1)
      lineOfSight   = (cd[0] & 1) == 1          ← bloque la LdV
      isInteractive = (cd[7] & 2) >> 1 != 0
      movement      = (cd[2] & 56) >> 3
      layerObj1     = ((cd[0]&4)<<11) + ((cd[4]&1)<<12) + (cd[5]<<6) + cd[6]
      layerObj2     = ((cd[0]&2)<<12) + ((cd[7]&1)<<12) + (cd[8]<<6) + cd[9]
    """
    try:
        cd = [ZKARRAY.index(c) for c in raw10]
    except ValueError:
        # Cellule indécodable : conservateur — on la traite comme un blocker LdV
        # (line_of_sight=False = opaque) en plus d'être non praticable.
        return CellData(cell_id=cell_id, is_active=False, is_interactive=False,
                        movement=0, layer_obj1=0, layer_obj2=0, line_of_sight=False)

    if cd[2] == 0 or cd[0] == 1 or (cd[0] == 33 and cd[2] == 1):
        is_active = False
    else:
        is_active = (cd[0] & 32) != 0

    line_of_sight = (cd[0] & 1) == 1
    is_interactive = ((cd[7] & 2) >> 1) != 0
    movement   = (cd[2] & 56) >> 3
    layer_obj1 = ((cd[0] & 4) << 11) + ((cd[4] & 1) << 12) + (cd[5] << 6) + cd[6]
    layer_obj2 = ((cd[0] & 2) << 12) + ((cd[7] & 1) << 12) + (cd[8] << 6) + cd[9]
    ground_level = cd[1] & 15          # LeafMITM cell.py : self.groundLevel = cd[1] & 15
    ground_slope = (cd[1] & 48) >> 4   # LeafMITM : layerGroundRot = cd[1] & 48 >> 4

    return CellData(
        cell_id=cell_id,
        is_active=is_active,
        is_interactive=is_interactive,
        movement=movement,
        layer_obj1=layer_obj1,
        layer_obj2=layer_obj2,
        line_of_sight=line_of_sight,
        ground_level=ground_level,
        ground_slope=ground_slope,
    )


# ---------------------------------------------------------------------------
# Données complètes d'une map
# ---------------------------------------------------------------------------

@dataclass
class MapInfo:
    map_id: int
    width: int
    height: int
    cells: list[CellData] = field(default_factory=list)
    x: int = 0  # coordonnée X sur la carte du monde (balise <X> du XML)
    y: int = 0  # coordonnée Y sur la carte du monde (balise <Y> du XML)

    @property
    def walkable_cells(self) -> set[int]:
        """Cellules praticables (movement != 0 et isActive)."""
        return {c.cell_id for c in self.cells if c.is_active and c.movement != 0}

    @property
    def blocked_cells(self) -> set[int]:
        """Cellules non praticables."""
        return {c.cell_id for c in self.cells if not c.is_active or c.movement == 0}

    @property
    def los_blocker_cells(self) -> set[int]:
        """Cellules qui bloquent la ligne de vue.

        line_of_sight=True (bit 0 de cd[0]) → cellule TRANSPARENTE (la LdV passe).
        line_of_sight=False → un objet (arbre, mur, rocher) bloque la vue.

        Important : la transparence est INDÉPENDANTE de la praticabilité. Les
        "trous" (cases noires hors-sol) sont non praticables (movement==0 ou
        is_active==False) MAIS line_of_sight==True : ils laissent passer la
        LdV, contrairement aux vrais obstacles. Ne filtrer QUE sur
        line_of_sight évite que le bot refuse de tirer par-dessus un trou.

        Source : LeafMITM map/cell.py — self.lineOfSight = (cd[0] & 1) == 1.
        """
        return {c.cell_id for c in self.cells if not c.line_of_sight}

    @property
    def cell_heights(self) -> dict[int, int]:
        """cell_id → hauteur de sol (ground_level). Pour la LdV 3D façon client."""
        return {c.cell_id: c.ground_level for c in self.cells}

    def get_cell_height(self, cell_id: int) -> int:
        """Hauteur de sol d'une cellule (0 si hors map)."""
        if 0 <= cell_id < len(self.cells):
            return self.cells[cell_id].ground_level
        return 0

    @property
    def sun_magic_cells(self) -> set[int]:
        """Cellules Sol Magique praticables (sorties de map, téléporteurs)."""
        return {c.cell_id for c in self.cells if c.is_active and c.movement != 0 and c.is_sun_magic}

    @property
    def resource_cells(self) -> list[tuple[int, int]]:
        """Retourner [(cell_id, layer_obj2)] pour les ressources récoltables."""
        _load_harvest_ids()
        return [
            (c.cell_id, c.layer_obj2)
            for c in self.cells
            if c.layer_obj2 in _HARVEST_IDS
        ]


def load_map(map_id: int) -> MapInfo | None:
    """Charger et décoder la map depuis le fichier XML LeafMITM.

    Args:
        map_id: Identifiant numérique de la map.

    Returns:
        MapInfo ou None si le fichier est absent.
    """
    if map_id in _map_cache:
        return _map_cache[map_id]

    xml_path = _MAPS_DIR / f"{map_id}.xml"
    if not xml_path.exists():
        logger.warning("[mapdata] Pas de fichier XML pour map #%d : %s", map_id, xml_path)
        return None

    try:
        tree = ET.parse(xml_path)
        root = tree.getroot()
        width  = int(root.findtext("ANCHURA") or 15)
        height = int(root.findtext("ALTURA")  or 17)
        x      = int(root.findtext("X") or 0)
        y      = int(root.findtext("Y") or 0)
        raw = root.findtext("MAPA_DATA") or ""
    except Exception as exc:
        logger.error("[mapdata] Erreur lecture XML map #%d : %s", map_id, exc)
        return None

    # Découper en chunks de 10 chars
    chunks = [raw[i:i+10] for i in range(0, len(raw), 10)]
    cells = [_decode_cell(chunk, idx) for idx, chunk in enumerate(chunks)]

    info = MapInfo(map_id=map_id, width=width, height=height, cells=cells, x=x, y=y)
    _map_cache[map_id] = info

    resources = info.resource_cells
    logger.info(
        "[mapdata] Map #%d chargée : %dx%d, %d cellules, %d ressources",
        map_id, width, height, len(cells), len(resources),
    )
    return info


# ---------------------------------------------------------------------------
# Cache auto-apprenant : cellules d'arrivée confirmées par GA;1
# ---------------------------------------------------------------------------
# Clé : (old_map_id, exit_cell, new_map_id) → arrival_cell
_arrival_cache: dict[tuple[int, int, int], int] = {}


def lookup_arrival(old_map: int, exit_cell: int, new_map: int) -> int | None:
    """Chercher une arrivée précédemment confirmée."""
    return _arrival_cache.get((old_map, exit_cell, new_map))


def record_arrival(old_map: int, exit_cell: int, new_map: int, arrival_cell: int) -> None:
    """Enregistrer une arrivée confirmée par le serveur (GA;1 start_cell)."""
    key = (old_map, exit_cell, new_map)
    prev = _arrival_cache.get(key)
    if prev != arrival_cell:
        _arrival_cache[key] = arrival_cell
        logger.info(
            "[mapdata] Cache arrivée : map %d exit %d → map %d = cell %d%s",
            old_map, exit_cell, new_map, arrival_cell,
            f" (remplace {prev})" if prev is not None else "",
        )
