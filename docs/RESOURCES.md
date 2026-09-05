# Ressources et données de jeu

## Vue d'ensemble

Le bot utilise deux types de données :
1. **Données statiques** : fichiers XML/TXT extraits du jeu (sorts, maps, récolte).
2. **Données dynamiques** : packets réseau en temps réel (stats, positions, combat).

## Fichiers de jeu Dofus

### Chemin d'installation

```
E:\games\Abrak\Retro
```

Le client Dofus Rétro (Abrak.exe) est installé dans ce répertoire. Les SWF du jeu (`core.swf`, etc.) contiennent le code ActionScript décompilable avec FFDec.

### Code source de référence

Le fichier `core.swf` contient `dofus/aks/Aks.as` qui implémente :
- L'algorithme de chiffrement réseau (`cypherData`, `decypherData`).
- La logique `prepareSendPacket()` (quels messages sont chiffrés).
- La gestion des clés (`prepareKey`, `_aKeys`, `_nCurrentKey`).

## Données locales du bot

### `ressources/spells.xml` — Sorts

**Chemin** : `dofus-retro-bot/ressources/spells.xml`
**Taille** : ~9280 lignes, ~1100 sorts

#### Structure XML

```xml
<SPELLS>
  <SPELL ID="161">
    <NAME>Flèche Magique</NAME>
    <LEVEL LEVEL="1"
           COST_PA="4"
           RANGE_MIN="1"
           RANGE_MAX="7"
           LAUNCH_INLINE="FALSE"
           VISION_LINE="TRUE"
           EMPTY_CELL="FALSE"
           MODIFIABLE_DISTANCE="TRUE"
           LAUNCH_PER_TURN="0"
           LAUNCH_PER_TARGET="2"
           COOLDOWN="0">
      <EFFECT TYPE="100" COOLDOWN="0" TARGET="-1" RANGE="Qe" IS_CRITIC="FALSE" />
      <EFFECT TYPE="100" COOLDOWN="0" TARGET="-1" RANGE="Qe" IS_CRITIC="TRUE" />
    </LEVEL>
    <!-- LEVEL 2, 3, etc. -->
  </SPELL>
</SPELLS>
```

#### Attributs d'un `LEVEL`

| Attribut | Type | Description |
|----------|------|-------------|
| `LEVEL` | int | Niveau du sort (1-6) |
| `COST_PA` | int | Coût en Points d'Action |
| `RANGE_MIN` | int | Portée minimum (PO) |
| `RANGE_MAX` | int | Portée maximum (PO) avant bonus |
| `LAUNCH_INLINE` | bool | Doit être lancé en ligne droite |
| `VISION_LINE` | bool | Nécessite la ligne de vue |
| `EMPTY_CELL` | bool | La cellule cible doit être vide |
| `MODIFIABLE_DISTANCE` | bool | La portée est affectée par le bonus PO |
| `LAUNCH_PER_TURN` | int | Max de lancers par tour (0 = illimité) |
| `LAUNCH_PER_TARGET` | int | Max de lancers par cible par tour (0 = illimité) |
| `COOLDOWN` | int | Tours de recharge (0 = aucun) |

#### Attributs d'un `EFFECT`

| Attribut | Type | Description |
|----------|------|-------------|
| `TYPE` | int | ID du type d'effet (100=dégâts air, 101=dégâts eau, etc.) |
| `COOLDOWN` | int | Durée de l'effet en tours |
| `TARGET` | int | Type de cible (-1=ennemi) |
| `RANGE` | str | Portée de l'effet (encodé Base64 Dofus) |
| `IS_CRITIC` | bool | Effet en cas de coup critique |

#### Parser : `data/spell_data.py`

```python
from data.spell_data import get_spell_level_info, get_spell_name

info = get_spell_level_info(spell_id=161, level=1)
# SpellLevelInfo(spell_id=161, name='Flèche Magique', level=1,
#                cost_pa=4, range_min=1, range_max=7,
#                vision_line=True, modifiable_distance=True, ...)
```

Le cache est lazy : `spells.xml` est parsé au premier appel et reste en mémoire.

### Sorts importants pour les Crâs

| ID | Nom | PA | Portée | LdV | PO modifiable | Notes |
|----|-----|----|--------|-----|---------------|-------|
| 161 | Flèche Magique | 4 | 1-7 | Oui | Oui | Sort principal (Air) |
| 163 | Flèche de Recul | 3 | 2-6 | Oui | Oui | Recule la cible |
| 164 | Flèche Empoisonnée | 4 | 1-6 | Non | Oui | DoT (Neutre) |
| 165 | Flèche Glacée | 4 | 1-7 | Oui | Oui | Dégâts Eau |
| 169 | Flèche Enflammée | 4 | 2-6 | Oui | Oui | Dégâts Feu |

### `ressources/maps/` — Cartes

**Chemin** : `dofus-retro-bot/ressources/maps/{map_id}.xml`
**Nombre** : ~7770 fichiers XML

#### Structure XML

```xml
<RECORD>
  <ID>999</ID>
  <ANCHURA>15</ANCHURA>    <!-- Largeur en cellules -->
  <ALTURA>17</ALTURA>      <!-- Hauteur en rangées -->
  <X>12</X>                <!-- Coordonnée X sur la carte du monde -->
  <Y>-7</Y>                <!-- Coordonnée Y sur la carte du monde -->
  <MAPA_DATA>bhGae...abc</MAPA_DATA>  <!-- Données des cellules encodées -->
</RECORD>
```

#### Encodage MAPA_DATA (ZKARRAY)

Chaque cellule est encodée en **10 caractères** de l'alphabet ZKARRAY (64 chars, 6 bits chacun).

```
ZKARRAY = "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789-_"
```

Décodage d'une cellule (10 chars → `cd[0..9]`, chaque `cd[i] = ZKARRAY.index(char)`) :

| Champ | Formule | Description |
|-------|---------|-------------|
| `isActive` | `cd[2] != 0 && cd[0] != 1 && !(cd[0]==33 && cd[2]==1)` | Cellule active |
| `lineOfSight` | `(cd[0] & 1) == 1` | Transparent pour la LdV |
| `isInteractive` | `(cd[7] & 2) >> 1 != 0` | Élément interactif |
| `movement` | `(cd[2] & 56) >> 3` | 0=bloqué, 1=passable, 4=ralenti |
| `layerObj1` | `((cd[0]&4)<<11) + ((cd[4]&1)<<12) + (cd[5]<<6) + cd[6]` | ID objet couche 1 (Sol Magique) |
| `layerObj2` | `((cd[0]&2)<<12) + ((cd[7]&1)<<12) + (cd[8]<<6) + cd[9]` | ID objet couche 2 (ressource) |

#### IDs Sol Magique

Les cellules avec `layerObj1` dans `{1029, 1030, 4088}` sont des **Soleils Magiques** (sorties de carte, téléporteurs).

#### Parser : `bot/mapdata.py`

```python
from bot.mapdata import load_map

map_info = load_map(999)
# MapInfo(map_id=999, width=15, height=17, cells=[...])

map_info.walkable_cells    # set[int] — cellules praticables
map_info.blocked_cells     # set[int] — cellules bloquées
map_info.los_blocker_cells # set[int] — cellules bloquant la LdV
map_info.sun_magic_cells   # set[int] — sorties de carte
map_info.resource_cells    # [(cell_id, layer_obj2)] — ressources
```

Le cache est permanent : une map chargée reste en mémoire.

### `ressources/Recolte.txt` — Ressources récoltables

**Chemin** : `dofus-retro-bot/ressources/Recolte.txt`

Format (pipe-séparé, une ressource par ligne) :
```
7500|Frene|Couper|6
7513|Lin|Faucher:Cueillir|50:68
```

| Champ | Description |
|-------|-------------|
| Graphic ID | ID graphique de la ressource |
| Nom | Nom de la ressource |
| Action | Action de récolte (`Couper`, `Faucher`, `Collecter`, `Cueillir`, `Pecher`) |
| Type ID | ID de type GA500 (peut être multiple séparé par `:`) |

#### Parsers

- `data/recolte.py` : mapping `type_id → nom` (utilisé par l'UI).
- `bot/mapdata.py` : mapping `layer_obj2 → (nom, ga500_type, job_action)` + `job_action → job_id`.

#### Mapping action → métier

| Action | Job ID | Métier |
|--------|--------|--------|
| Couper | 2 | Bûcheron |
| Collecter | 11 | Mineur |
| Faucher | 6 | Paysan |
| Cueillir | 14 | Alchimiste |
| Pecher | 10 | Pêcheur |

## Données dynamiques (packets)

### Stats du personnage — `As`

Le packet `As` contient toutes les stats sérialisées. Parsé par `protocol/messages/stats.py` dans `CharacterStats`.

Stats importantes : HP, PA, PM, PO, force, intelligence, agilité, chance, sagesse, vitalité, poids, kamas.

### Entités sur la carte — `GM`

Le packet `GM` liste toutes les entités présentes :
- Joueurs (ID positif)
- Monstres (ID négatif en combat, données spécifiques hors combat)
- PNJ

Parsé par `protocol/messages/stats.py` dans `EntityInfo`.

### Stats combat — `GTM`

Envoyé avant chaque tour, contient les stats actualisées de toutes les entités en combat.
Le bot extrait : `combat_entity_cells`, `combat_entity_hp`, `combat_entity_pa`, `combat_entity_pm`.

### Sorts du personnage — `SL`

Envoyé à la connexion. Format : `spell_id~level~slot;spell_id~level~slot;...`.
Le bot croise ces données avec `spells.xml` pour obtenir les propriétés complètes.

### Données de carte — `GDM`

Format : `|map_id|date|key`. Le bot utilise `map_id` pour charger le fichier XML correspondant.

### Poids — `Ow`

Format : `{current}|{max}`. Utilisé pour détecter le surpoids et déclencher un dépôt en banque.

### Métiers — `JX`

Format : `K{char_id}~{job_id};{level};{xp};{xp_next};{xp_total};|...`.
Utilisé pour afficher les stats de métier dans l'onglet Métiers.

## Points d'attention

### Toutes les données sont dans `ressources/`

`bot/mapdata.py` et `data/recolte.py` résolvent leurs chemins via
`core.paths.RESSOURCES_DIR`, donc **aucune donnée n'est attendue hors du dépôt**.
Les maps (`ressources/maps/`) et `ressources/Recolte.txt` suffisent : le bot n'a
plus besoin du dossier `_examples/`, qui ne sert que de référence de lecture.

### Fichier `ressources/hash.py` (legacy)

Code crypto incomplet/cassé, supplanté par `protocol/encoding.py`. Ne pas utiliser.

## Ajout de nouvelles données

Pour ajouter de nouvelles données statiques :

1. Extraire le fichier depuis le client Dofus.
2. Le placer dans `ressources/` (toutes les données statiques y vivent).
3. Créer un parser dans `data/` avec un cache lazy.
4. L'intégrer dans `game/state.py` (handler de packet) ou `bot/` (logique).
5. Mettre à jour le bridge et l'UI si nécessaire.
