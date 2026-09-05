# Mode héros

## Concept

Le mode héros de Dofus Rétro permet de contrôler jusqu'à 3 personnages par compte (1 principal + 2 compagnons). Avec plusieurs comptes connectés simultanément via le même proxy, le bot gère jusqu'à **8 personnages** dans un seul client. L'équipe peut être **monoclasse** (ex. 8 Crâs) ou **mixte** (ex. 2 Crâs + 6 Enutrofs) — voir [§ Classes mixtes](#classes-mixtes-équipe-hétérogène).

> **Team vs multi-instance.** Ce document décrit les héros au sein d'**un** client (= une « team » = 1 connexion = 1 `Session`, jusqu'à 8 persos partageant la même connexion réseau). Lancer **plusieurs clients Dofus** crée **plusieurs Sessions/teams** indépendantes, chacune avec son propre onglet — voir [ARCHITECTURE.md § Multi-instance](ARCHITECTURE.md#multi-instance-sessions). Les héros ci-dessous concernent l'intérieur d'une seule team.

## Identification des personnages

### Personnage principal

- Identifié à la connexion via le packet `ASK` (AccountCharacterList).
- Stocké dans `GameState.character` (pseudo, classe, niveau, `character_id`).
- Son `character_id` est la référence pour toute la logique.

### Héros (compagnons)

- Les héros partagent la même connexion réseau que le personnage principal.
- Ils n'ont **pas** de packet `ASK` individuel — ils sont détectés via :
  - `GM` (GameMap) : entités sur la carte avec des IDs positifs.
  - `GTS X` : informations de sorts envoyées au début de chaque combat.
  - `As` (AccountStats) : stats héros identifiées par `entity_id` dans le payload.

## Packets héros en combat

### GTS (GameTurnStart) — Deux formats

**Format réel (tour de jeu)** :
```
GTS {entity_id}|{time_ms}|{turn_number}
```
Exemples :
- `GTS 403035|45000|1` → Tour du personnage principal, tour n°1, 45s de timer.
- `GTS -3|45000|1` → Tour du monstre -3.

**Format info héros (pas un vrai tour)** :
```
GTS X{main_id};{hero_id};0;1;0;{spells_list}
```
Exemple :
```
GTS X403035;403042;0;1;0;161~1~1,163~1~-1,164~1~2,169~1~3
```
- Envoyé au début du combat pour chaque héros.
- Contient la liste des sorts du héros : `spell_id~spell_level~slot_position`.
- Le bot ignore ces messages (préfixe `X` détecté dans le handler GTS).

### GTM (GameTurnMiddle) — Stats des entités

Envoyé avant chaque GTS réel. Format :
```
GTM |{id};{?};{hp};{PA};{PM};{cell};;{max_hp}|...
```
Exemple :
```
|403035;0;85;6;3;259;;85|403037;0;85;6;3;353;;85|-3;0;100;6;4;368;;100
```

Le bot extrait pour chaque entité :
- `combat_entity_cells[id]` : position (cell_id)
- `combat_entity_hp[id]` : HP courants
- `combat_entity_pa[id]` : PA disponibles
- `combat_entity_pm[id]` : PM disponibles

### GTL (GameTurnList) — Ordre d'initiative

```
GTL |{id1}|{id2}|...
```
Exemple : `GTL |-3|403035|-2|403037|-1|403036|403039|403038|403041|403040|403042`

- IDs négatifs = monstres.
- IDs positifs = alliés (perso principal + héros).
- Mis à jour quand un monstre meurt (GA;999).

## Gestion des tours

### Queue de tours

Au lieu d'attendre un seul personnage (l'ancien système), le bot utilise une **queue asyncio** :

```python
_combat_turn_queue: asyncio.Queue[str] = asyncio.Queue()
```

Le handler GTS place chaque ID d'allié dans la queue :
```python
# Allié actif → dans la queue
_combat_turn_queue.put_nowait(turn_id)
```

`fight_group()` consomme la queue séquentiellement :
```python
turn_id = await wait_next_ally_turn(timeout=60)
await _play_cra_turn(label, caster_id=turn_id)
```

### Héros passifs

Le set `PASSIVE_COMBAT_HEROES` permet de définir des héros qui passent leur tour automatiquement (envoi de `Gt` immédiat). Actuellement **vide** car tous les Crâs sont actifs.

```python
PASSIVE_COMBAT_HEROES: set[str] = set()
```

Si un ID est dans ce set, le handler GTS envoie `Gt` automatiquement au lieu de mettre l'ID dans la queue.

### Flux d'un round complet (8 Crâs + 3 monstres)

```
GTS -3|45000|1          → Monstre -3 joue (ignoré par le bot)
GTS 403035|45000|1      → Queue ← 403035 → bot joue Perso-A (perso principal)
GTS -2|45000|1          → Monstre -2 joue (ignoré)
GTS 403037|45000|1      → Queue ← 403037 → bot joue Héros-403037
GTS -1|45000|1          → Monstre -1 joue (ignoré)
GTS 403036|45000|1      → Queue ← 403036 → bot joue Héros-403036
GTS 403039|45000|1      → Queue ← 403039 → bot joue
GTS 403038|45000|1      → Queue ← 403038 → bot joue
GTS 403041|45000|1      → Queue ← 403041 → bot joue
GTS 403040|45000|1      → Queue ← 403040 → bot joue
GTS 403042|45000|1      → Queue ← 403042 → bot joue
... (tour n°2, n°3, etc.)
```

## Sorts des héros

### Packet SL (SpellsList) — Perso principal uniquement

```
SL {spell_id}~{spell_level}~{position};...
```
Envoyé à la connexion. Contient tous les sorts du personnage principal.
Parsé dans `game/state.py` → `GameState.character_spells`.

### Packet GTS X — Sorts des héros en combat

Les sorts de chaque héros sont aussi envoyés via le format `GTS X` au début du combat.
Le bot ignore ces messages (préfixe `X`) : il dispose déjà des sorts par classe via `Nh`.

### Packet Nh (HeroSpells) — Sorts par héros (exploité)

```
Nh {hero_id}|{spell_id}~{level}~{position};...
```
Envoyé **à la connexion** pour chaque héros. Le handler `_on_hero_spells` (`game/state.py`)
le parse et range les sorts dans `GameState.class_spells[class_id]`, en résolvant la classe
du héros via `entity_classes` (renseigné par `Nx`). C'est ce qui alimente les sous-onglets
**Sorts** par classe de l'onglet Combat et la résolution du niveau des sorts en combat.

## Bonus PO des héros

Le bonus PO (Portée) de chaque héros provient **uniquement** du packet `As` (AccountStats), via `CharacterStats.total_range_points` :
```python
combat_entity_po[entity_id] = po_bonus   # As, source autoritaire
```

Ce bonus s'ajoute à la portée max du sort :
```python
effective_max_range = spell_range_max + po_bonus
```

> ⚠️ **Ne PAS extraire le PO du packet `GM`.** Une ancienne heuristique (`_extract_combat_entity_po`, qui devinait la position du HP dans les champs de placement puis lisait le PO 3 champs plus loin) renvoyait des valeurs fausses (ex. 10 au lieu de 3). Elle gonflait `effective_max` et provoquait des rejets de portée serveur en rafale (`Im 1171` « vous visez à 16 »). Cette extraction GM a été **supprimée**.

**Défaut prudent** : tant qu'aucun `As` n'a renseigné un allié, `get_entity_po()` renvoie `0`. On sous-estime alors la portée (jamais de rejet serveur), et le premier `As` la corrige. Le `As` peut arriver tardivement pour un héros (parfois en cours de combat) — c'est volontairement toléré, l'inverse (sur-estimer) étant pire.

## Classes mixtes (équipe hétérogène)

Le bot gère les équipes **multi-classes** (ex. 2 Crâs + 6 Enutrofs). À la connexion :

- **Détection des classes** : le perso principal via `ASK` (`class_id`), chaque héros via `Nx`
  (l'entité `NL`/`K+` porte le `class_id` en champ 4). Le tout est mémorisé dans
  `GameState.entity_classes` (`entity_id → class_id`).
- **Sorts par classe** : `SL` (perso principal) et `Nh` (héros) remplissent
  `GameState.class_spells[class_id]`.
- **Séquences de sorts par classe** : `GameState.attack_spell_sequences[class_id]` — une
  séquence configurable **par classe** (onglet Combat → Sorts → un sous-onglet par classe).
  En combat, `bot/combat.py:_get_attack_spell_sequence(caster_id)` résout la classe du lanceur
  (`get_entity_class_id`) et joue la séquence de **sa** classe. Tous les persos d'une même
  classe jouent donc la même séquence.
- **Persistance** : par compte **et par classe**, sous `attack_spells_by_class` dans
  `bot_settings.json` (cf. [README](README.md#réglages-combat-par-compte--bot_settingsjson)).

> Si aucune séquence n'est configurée pour la classe d'un lanceur, celui-ci **passe son tour**
> (un message le signale dans les logs). Il faut renseigner le sous-onglet Sorts de cette classe.

## Évolutions futures

1. **Stratégie de tour par classe** : adapter le déplacement/positionnement selon la classe
   (les Enutrofs/Crâs partagent pour l'instant la même logique « distance »).
2. **Positionnement tactique** : coordonner les positions des alliés pour maximiser la couverture.
3. **Soins** : ajouter une logique de soin si un héros est un Eniripsa.
