# Système de combat

## Stratégie générale

Le bot gère une team en mode héros, **monoclasse ou mixte**. Chaque personnage joue son tour individuellement en itérant sur la **séquence de sorts configurée pour sa classe** (onglet Combat → Sorts → sous-onglet de la classe ; cf. [HEROES.md § Classes mixtes](HEROES.md#classes-mixtes-équipe-hétérogène)) :

1. **Itérer** sur la séquence dans l'ordre, en sautant les sorts en cooldown ou sans PA suffisants.
2. Pour chaque sort, **résoudre la cible** selon son type (voir [Cibles de sort](#cibles-de-sort)).
3. Si pas de cible accessible et qu'on a des PM → **se déplacer** (mode distance ou rush CàC selon `combat_behavior`).
4. **Caster** le sort autant de fois que configuré (compteur `count` par entrée), gérer les rejets serveur.
5. En fin de boucle : **repli post-attaque** si une menace est détectée à proximité (mode distance).
6. **Fin de tour** (`Gt`) une fois la séquence terminée ou les PA épuisés.

### Ciblage ennemi

Les cibles ennemis sont triées par `(blessé d'abord, plus proche en PO)`. Un monstre est considéré **blessé** dès qu'il a perdu ≥ `INJURED_HP_THRESHOLD` (10 %) de ses HP max — ce qui pousse les Crâs à **focus** une cible pour l'achever plutôt que d'éroder.

Le HP max est tracké via `combat_entity_max_hp` (field 7 du `GTM`, populé au premier tour de chaque entité).

#### Filtrage des invocations

Certains monstres (mama koalak, etc.) **invoquent** des créatures en cours de combat. Comme **tuer l'invocateur tue aussi ses invocations**, le bot **ignore les invocations** dans le choix de cible (single-target ET zone AOE) et **focus les monstres d'origine** :

- Le 1er `GTL` du combat (handler `_on_turn_list`) fige les IDs des monstres d'origine dans `GameState.combat_initial_monsters`. Les invocations apparaissent **ensuite** (via `GA;999;…;GTL|…`) avec de nouveaux IDs qui ne sont donc **jamais** dans ce set.
- `get_targetable_monster_ids()` / `get_targetable_monster_cells()` renvoient les monstres vivants **intersectés** avec le set d'origine. **Fallback dur** : s'il ne reste plus aucun monstre d'origine vivant (cas rare — quand l'invocateur meurt, ses invocations meurent), on retombe sur tous les monstres vivants.
- Ces helpers remplacent `get_live_monster_cells()` **uniquement pour le ciblage et les déplacements d'approche/repositionnement d'attaque** (`_find_target_from`, `_find_best_aoe_impact`, `_find_los_correction_move`, avance). Le **calcul de menace / repli** continue de prendre en compte **tous** les monstres vivants (une invocation tape aussi), et le **blocage de mouvement + la LdV** restent calculés sur toutes les entités (`combat_entity_cells`) : une invocation bloque toujours la ligne de vue et le passage.

#### Fallback « taper les invocations » (anti-blocage)

Ignorer totalement les invocations posait un problème : quand elles **bloquent la LdV** vers les monstres d'origine ou **taclent/encerclent** nos persos, le bot pouvait rester coincé sans rien faire. Garde-fou par tour (`_play_cra_turn`) :

- En début de tour, `_primary_engageable_this_turn()` teste s'il existe une case atteignable (≤ PM, case actuelle comprise) d'où un sort d'attaque de la séquence peut toucher un monstre **d'origine** (portée + LdV terrain + LdV entités, **les invocations comptant comme bloqueurs**).
- Si **oui** → comportement normal (invocations ignorées). Si **non** → `target_summons = True` : pour ce tour, le ciblage ET les déplacements basculent sur **tous** les monstres (`get_live_monster_cells`), donc on **tape les invocations** qui nous gênent au lieu de rester bloqué.
- Même en mode fallback, `_find_target_from` trie avec `is_summon` en dernier : si un monstre d'origine redevient castable, il reste prioritaire ; on ne touche les invocations qu'en dernier recours.
- La décision est prise une fois par tour (au PM/PA plein, le plus permissif). Le tour suivant ré-évalue.

## Grille de combat Dofus 1.29

### Structure de la grille

La carte de combat est une grille en **losange** (staggered grid) :
- Largeur : `MAP_WIDTH = 15` cellules par rangée paire, 14 par rangée impaire.
- Total : environ 479 cellules (30 rangées alternées).
- Chaque cellule a un `cell_id` (entier 0 à ~479).

### Conversion cell_id ↔ coordonnées (x, y)

```python
def cell_to_xy(cell_id, width=15):
    row_len = width * 2 - 1  # 29
    pos_y = (cell_id // row_len) * 2
    if (cell_id % row_len) >= width:
        pos_y += 1
    if cell_id > (width - 1) * 2:
        pos_x = (cell_id + (pos_y // 2)) % width + 1
    else:
        pos_x = cell_id % width + 1
    return pos_x - 1, pos_y

def xy_to_cell(x, y, width=15):
    return y * width + x - (y // 2)
```

### Distance PO (portée)

**Critique** : la distance PO n'est PAS une distance BFS. C'est la **distance Manhattan en coordonnées diagonales**.

La grille de combat forme un diamant. Chaque cellule est convertie en coordonnées diagonales `(a, b)` :

```python
def _cell_to_diag(cell, width=15):
    x, y = cell_to_xy(cell, width)
    k = y // 2
    if y % 2 == 0:
        return (x + k, x - k)
    else:
        return (x + k + 1, x - k)

def _po_distance(cell_a, cell_b, width=15):
    a1, b1 = _cell_to_diag(cell_a, width)
    a2, b2 = _cell_to_diag(cell_b, width)
    return abs(a1 - a2) + abs(b1 - b2)
```

**Exemple** : Cell 263 → Cell 105
- Cell 263 = (x=2, y=18, pair) → diag (11, -7)
- Cell 105 = (x=3, y=7, impair) → diag (7, 0)
- PO = |11-7| + |-7-0| = **11**
- Le BFS 8-directions donnait incorrectement 7.

### Voisins en combat (mouvement)

En combat, seuls **4 voisins diagonaux** sont valides (1 PM chacun) :

```python
# Rangée impaire (y % 2 == 1)
_DIAG_ODD_NEIGHBORS  = [(0, 1), (0, -1), (1, 1), (1, -1)]

# Rangée paire (y % 2 == 0)
_DIAG_PAIR_NEIGHBORS = [(0, 1), (0, -1), (-1, 1), (-1, -1)]
```

Les mouvements horizontaux `(±1, 0)` et les sauts verticaux `(0, ±2)` ne sont **PAS** valides en combat (ils le sont en overworld).

**Conséquence** : la distance de mouvement en combat = la distance PO.

### Voisins overworld (hors combat)

En overworld, 8 directions sont valides :

```python
_ODD_NEIGHBORS  = [(0,1), (0,-1), (1,0), (-1,0), (1,1), (1,-1), (0,2), (0,-2)]
_PAIR_NEIGHBORS = [(0,1), (0,-1), (1,0), (-1,0), (-1,1), (-1,-1), (0,2), (0,-2)]
```

## Ligne de vue (LdV)

### Terrain

`has_line_of_sight(caster, target, los_blockers)` utilise un algorithme supercover (Amanatides-Woo) en coordonnées diagonales `(a, b)` — le repère où les 4 voisins PO=1 sont équidistants. Aux croisements de coins (a et b changent au même paramètre t), les deux cellules adjacentes au coin sont testées : c'est ce que fait aussi le serveur, et c'est ce qui catch les entités placées au croisement d'une diagonale.

Les cellules bloquantes terrain sont lues depuis `ressources/maps/{id}.xml` via `bot/mapdata.py`.

### Anti-spam Im 1174

Pour éviter de spammer le serveur avec des sorts rejetés (qui pourraient lever un flag anti-bot), le bot maintient un cache `_los_blocked_pairs` keyé par `(caster_cell, target_cell)`, peuplé à chaque `Im 1174` et vidé au début de chaque tour (`clear_turn_los_cache()`). Le `_find_target_from` filtre les paires déjà bloquées. Quota dur de **2 rejets LdV par tour** : au-delà, on arrête d'essayer (`break` sur la boucle de cast).

### Entités

En plus du terrain, les **entités** (alliés, monstres, invocations) bloquent aussi la LdV. La cellule de la **cible elle-même** n'est pas un bloqueur (on tire dessus).

```python
entity_blockers = {
    cell for eid, cell in combat_entity_cells.items()
    if eid != caster_id and cell >= 0
} - {target_cell}
```

### Rejet serveur

Si le serveur rejette un sort, il envoie un `Im`. **Tous** lèvent `_spell_cast_failed = True`, mais ils ne se traitent PAS de la même façon — la distinction est critique (un mauvais regroupement provoquait des rejets en rafale) :

| Code | Sens | Flag dédié | Réaction de `combat.py` |
|------|------|-----------|--------------------------|
| `Im 1174` | LdV bloquée (terrain ou entité) | `_spell_los_blocked` | Repositionnement LdV latéral + quota de **2** rejets LdV/tour (anti-spam) |
| `Im 1172` | Case d'impact invalide (ex. sort `EMPTY_CELL="FALSE"` sur case vide) | — | Exclut la case, **retente une autre** (quota `MAX_OTHER_REJECTIONS_PER_TURN` = 3) |
| `Im 1171;{min}~{max}~{actual}` | Hors de portée | — | Idem 1172 : exclut la case, retente une autre |
| `Im 1170;{dispo}~{requis}` | PA insuffisants | `_spell_pa_insufficient` | **Arrête le sort** sans retenter (inutile : on n'a pas les PA) |

**Pièges historiques** :
- `Im 1171` ne doit **jamais** lever `_spell_los_blocked` : sinon il est compté dans le quota LdV et déclenche un repositionnement latéral au lieu de simplement retenter une case d'impact plus proche et valide.
- `Im 1170` arrive typiquement après un **tacle** (un monstre adjacent draine des PA pendant un déplacement). Le bot ne suit pas toujours cette perte (cf. [Tacle et PA](#tacle-et-pa)) : son compteur PA local est alors faux, donc retenter une autre case ne ferait que re-rejeter. Le flag `_spell_pa_insufficient` force l'arrêt propre du sort.

Le PA n'est pas consommé côté serveur sur un rejet ; le bot resynchronise via `get_entity_pa()` après chaque tentative.

### Tacle et PA

Quand un Crâ se déplace au contact (ou s'éloigne) d'un monstre, le serveur peut le **tacler** : perte de PA/PM signalée par des `GA;101`/`GA;127;{id};{id},-N` pendant le déplacement. Le bot **ne décompte pas** ces pertes localement (le `GA;101` est parfois parasite — le décompter en double cassait le calcul de PA). Conséquence : après un tacle, le PA local du bot est surévalué, et le 2ᵉ cast peut être rejeté en `Im 1170`. Ce rejet est géré *réactivement* (arrêt du sort via `_spell_pa_insufficient`), pas préventivement. Le `GTM` resynchronise PA/PM au début de chaque round.

## Sort d'attaque

### Sélection dynamique

Le sort d'attaque est sélectionné dans l'onglet Combat (dropdown). Ses propriétés sont lues dynamiquement depuis `data/spell_data.py` qui parse `ressources/spells.xml`.

```python
def _get_attack_spell() -> tuple[spell_id, cost_pa, range_min, range_max, needs_los]:
```

### Propriétés utilisées

| Propriété | Source XML | Usage |
|-----------|-----------|-------|
| `COST_PA` | `<LEVEL ... PA_COST="4">` | Calcul du nombre de casts par tour |
| `RANGE_MIN` | `<LEVEL ... MIN_RANGE="1">` | Distance minimum pour caster |
| `RANGE_MAX` | `<LEVEL ... MAX_RANGE="7">` | Distance maximum (+ bonus PO) |
| `VISION_LINE` | `<LEVEL ... VISION_LINE="TRUE">` | Nécessite la LdV |
| `LAUNCH_PER_TURN` | `<LEVEL ... LAUNCH_PER_TURN="0">` | 0 = illimité |
| `LAUNCH_PER_TARGET` | `<LEVEL ... LAUNCH_PER_TARGET="2">` | Max casts sur même cible |

### Exemple : Flèche Magique (sort 161, niveau 1)

- Coût : 4 PA
- Portée : 1-7 (modifiable par PO)
- LdV : requise
- Lancer par tour : illimité
- Lancer par cible : 2

Avec 6 PA → 1 cast par tour (6 ÷ 4 = 1, reste 2 PA).

## Cibles de sort

Chaque entrée de la séquence (`attack_spell_sequences[class_id]` dans `GameState` — une séquence **par classe**) a un type de cible. Sauvegardé dans `bot_settings.json` sous la clé `target`. En combat, `_get_attack_spell_sequence(caster_id)` résout la classe du lanceur et joue la séquence de sa classe (cf. [HEROES.md § Classes mixtes](HEROES.md#classes-mixtes-équipe-hétérogène)).

| Clé interne | Label UI | Comportement |
|-------------|----------|--------------|
| `enemy` | Ennemi | Single-target. Trie les monstres vivants par (blessé d'abord, plus proche), retourne la 1ère cible à portée + LdV. |
| `self` | Soi-même | Cast sur `caster_cell` (buffs comme Œil de Taupe). |
| `aoe2` | AOE 2 cases | Zone de rayon PO ≤ 2 (13 cellules) autour de la case d'impact. |
| `aoe3` | AOE 3 cases | Zone de rayon PO ≤ 3 (25 cellules). |

### Algorithme AOE (`_find_best_aoe_impact`)

Pour `aoe2` / `aoe3`, on cherche la case d'impact qui touche le plus d'ennemis sans toucher d'allié :

1. Énumérer toutes les cases à PO entre `range_min` et `range_max + po_bonus` via `_cells_in_po_range` (diamant Manhattan diagonale, pas l'approximation BFS).
2. Pour chaque candidate, compter `_aoe_hits(impact, monsters, radius)`.
3. **Exclure** toute case dont l'AOE touche un allié OU le lanceur lui-même (`protected_cells = allies ∪ {caster_cell}`). Pas de friendly fire — si aucune case safe ne touche d'ennemi, le sort est skip et la séquence passe au suivant.
4. Vérifier LdV terrain + LdV entités (la case d'impact n'est pas considérée comme bloqueur).
5. Trier par `(-hits, on_monster_cell, dist_lanceur)` : max hits d'abord, puis **préférer les cases d'impact occupées par un monstre** (évite `Im 1172` pour les sorts à `EMPTY_CELL="FALSE"`), puis le plus proche.

### Repositionnement AOE (`_find_best_aoe_caster_position`)

Avant chaque cast AOE, le bot compare le nombre de hits depuis sa case actuelle vs depuis la meilleure case atteignable en ≤ PM steps. Si gain strict, il se déplace puis recast. Une case nouvellement menacée est exclue si le lanceur n'est pas déjà menacé.

**Aussi utilisé en cours de séquence** : quand `_resolve_target` renvoie `None` à l'itération 2+ d'un sort AOE (typiquement parce que le 1er cast vient de tuer la seule cible atteignable), le bot rappelle `_find_best_aoe_caster_position` plutôt que `_find_best_move_distance`. Sinon la logique mono-cible choisirait une case d'où on *voit* un monstre mais d'où aucun impact AOE valide n'existe → 2e Flèche perdue. Le mouvement n'est exécuté que si `best_hits >= 1`.

## Mode distance — menace et repli

Le `combat_behavior = "distance"` (Crâ, défaut) ajoute deux comportements clés :

### Notion de menace

Un monstre **menace** une cellule si `distance_PO ≤ pm_max + MONSTER_ATTACK_RANGE` :
- `pm_max` = high-watermark du PM observé via `combat_entity_max_pm` (et non le PM courant — un monstre qui a déjà joué peut avoir PM=0 alors qu'il retrouvera son plein au prochain tour).
- `MONSTER_ATTACK_RANGE = 2` (constante) : marge pour couvrir les monstres tapant à 2 cases (Bouftou, Larve…).

### Repli post-attaque vs avance

En fin de tour, deux comportements **mutuellement exclusifs** selon qu'on a tapé ou non :

- **Repli** (`total_attacks_cast > 0`) : si `_is_threatened_by_monsters(caster_cell)` et qu'il reste des PM, `_find_retreat_move` cherche la case la plus éloignée du monstre le plus proche tout en gardant si possible la portée d'attaque pour le tour suivant.
- **Avance** (`total_attacks_cast == 0`) : si on n'a **rien pu lancer** ce tour (cibles hors portée fiable, tirs rejetés) et qu'il reste des PM, `_find_advance_move` rapproche le Crâ du monstre le plus proche (en restant ≥ `min_keep_dist = 4`). Sans ça, un Crâ très loin restait planté/reculait et ne faisait rien si les monstres n'avançaient pas non plus.

### Choix de déplacement (`_find_best_move_distance`)

Scoring multi-critères pour chaque case atteignable, par ordre de poids :
- `cac_penalty` (`CAC_PROXIMITY_PENALTY` = −2000… pénalité **positive** de 2000) si dest est **adjacente** (PO ≤ 1) à un monstre : c'est le poids le plus lourd, il domine `attack_bonus`. But : ne jamais se coller volontairement à un ennemi pour grappiller un angle de tir, car on se ferait **tacler** (perte de PA/PM + blocage). Appliqué dans les phases attaque, latérale et approche.
- `attack_bonus` (−1000 à −1500) si on peut taper depuis dest (single-target avec LdV entités OK).
- `diagonal_bonus` (−120 à −250) si dest est en **diagonale pure** du monstre cible (les deux composantes diagonales `(a, b)` diffèrent → LdV plus robuste, contourne un allié aligné cardinalement).
- `threat_penalty` (~80 × menace_score), `los_block_penalty` (200/allié), `ally_crowd_penalty` (80/voisin), `move_cost` (3/case).

#### Anti-tacle : proximité sur TOUS les monstres

Les pénalités d'adjacence / distance de sécurité (`cac_penalty`, exclusion CàC en approche, `min_keep_dist` en avance, distance de repli) sont calculées sur **tous les monstres vivants** via `_proximity_monster_cells()` (= `get_live_monster_cells()`), **invocations comprises** — alors que le *ciblage* (`get_targetable_monster_cells`) les ignore. Une invocation tacle et bloque autant qu'un monstre d'origine : sans ça, depuis que le ciblage exclut les invocations, le bot venait se coller aux invocations pour trouver un angle de tir vers le vrai monstre. Le même garde-fou s'applique à `_find_los_correction_move`, `_find_retreat_move`, `_find_advance_move` (jamais d'avance en case adjacente) et `_find_best_aoe_caster_position` (à hits AOE égaux, on préfère une case non adjacente).

### Correction de LdV (`_find_los_correction_move`)

Après un rejet `Im 1174`, le bot se repositionne sur une case d'où il garde portée + LdV vers un monstre. Le scoring **préfère les cases plus proches** du monstre (ligne de vue plus courte = plus fiable côté serveur) avec une pénalité de menace pour ne pas foncer au CàC, **plus une pénalité `CAC_PROXIMITY_PENALTY`** si la case est adjacente à un monstre/invocation (anti-tacle). C'est l'inverse de l'ancien comportement (préférence pour les cases lointaines) qui faisait *reculer* les Crâs au lieu de débloquer un tir.

## Mouvement en combat

### Pathfinding combat

Un A* spécifique au combat (`_combat_astar`) utilise les 4 voisins diagonaux.

L'heuristique est la distance PO (admissible car distance PO = distance réelle sur ce graphe).

### Recherche de destination

`_find_best_move_for_attack()` :

1. Calcule toutes les cellules atteignables en ≤ PM pas.
2. Pour chaque cellule atteignable, vérifie si un monstre est en portée + LdV.
3. **Priorité 1** : cellule donnant un tir direct, minimisant la distance au monstre.
4. **Priorité 2** : si aucun tir possible, se rapprocher du monstre le plus proche.
5. **Garde-fou** : ne se déplace que si ça réduit la distance.

### Validation du mouvement

1. Le bot envoie `GA001{path}` (chemin encodé en ZIPKEY).
2. Le serveur confirme (`GA;1`) ou rejette (`GA;129` = PM insuffisants).
3. Le Flash client envoie `GKK` pour confirmer l'animation.
4. Le bot attend le `GKK` (`wait_gkk(timeout=5.0)`) avant de continuer.
5. Si `GKK` timeout → mouvement considéré échoué, position inchangée.

## Synchronisation fin de tour et inter-cast

Le serveur refuse `Gt` (fin de tour) **et** une nouvelle action `GA300` (cast suivant) tant que des actions sont en attente d'ack Flash :

```
Sort lancé → Serveur envoie GAF (action finish)
          → Flash traite l'animation
          → Flash envoie GKK (ack)
          → Bot peut envoyer Gt OU le cast suivant
```

Le bot utilise un compteur `_pending_actions` :
- Incrémenté sur chaque `GAF` reçu du serveur.
- Décrémenté sur chaque `GKK` envoyé par le Flash.
- `end_turn()` appelle `wait_actions_clear()` avant d'envoyer `Gt`.
- `wait_spell_result()` appelle aussi `wait_actions_clear()` **après chaque cast** : sans ça, le 2e cast d'un sort (ex. 2e Flèche Explosive) part avant le GKK du 1er → le serveur répond `GA;102;-0` (0 PA consommé, sort silencieusement jeté).

### Timeouts GKK (Flèche Explosive 2/2 sur zones chargées)

Le timeout du GAF (event `_spell_result_event`, ~1 RTT) et celui du GKK (`wait_actions_clear`) sont **dissociés**. Le GAF arrive vite, mais sur un AOE qui touche 3-5 monstres, le Flash met 0.5-1s à rendre tous les effets (animations dégâts, `GA;100` par hit, `GIe`…) avant d'envoyer les GKK :

- `wait_spell_result(timeout=0.5)` : attend le GAF (rapide), puis `wait_actions_clear(timeout=max(timeout*2.4, 1.2))` pour le GKK (plafond 1.2s).
- Avant chaque `cast_spell` dans `_play_cra_turn`, filet de sécurité supplémentaire : `await wait_actions_clear(timeout=1.2)` (no-op si `pending=0`).

Symptôme à surveiller dans les logs : `wait_actions_clear timeout (N actions pending)` suivi d'un `GA;102;{caster};{caster},-0`. Voir aussi `tools/optimize_fight_options.py` qui pousse les flags d'accélération du client Flash (`TacticMode`, `UseLightEndFightUI`, `CreaturesMode=0`, etc.) pour réduire la latence GAF→GKK à la source.

## No-anim — déplacements instantanés (patch client core.swf)

Les options natives (`SkipFightAnimations`, etc.) accélèrent les animations de
sorts/coups/mort **mais pas le déplacement** : les entités continuent de
« glisser » case par case, ce qui reste sur le chemin critique (le serveur 1.29
attend l'ack `GKK` de chaque action). Le no-anim supprime ce glissement —
**monstres comme joueurs se téléportent** à destination.

- **Deux patches, tous deux gardés par `isRunning`** :
  1. **Déplacement instantané** — `basicMove` (classe `ank.battlefield.mc[...]`)
     téléporte l'entité à destination en 1 frame au lieu de glisser.
  2. **Pas d'anim du lanceur** — dans `launchVisualEffect`
     (`ank.battlefield["\x1e\x0e\x0f"]`) on saute le `setAnim` de l'entité qui
     agit. Supprime le **petit saut des monstres en mode créature** quand ils
     tapent (anim `anim4`) — que l'option native `SkipFightPlayerAnimations`
     **n'enlève pas** (elle exclut explicitement les créatures). Les effets
     visuels du sort sur la cible restent affichés (gérés séparément).
- **Cible du patch** : `modules/core.swf` est le moteur Dofus 1.29 (AS2). La
  fonction `basicMove` de la classe sprite du champ de bataille
  (`ank.battlefield.mc[...]`) est patchée pour atteindre la case de
  destination en 1 frame **uniquement pendant un combat actif**
  (`this.api.datacenter.Game.isRunning` — `api` est l'alias de `_global.API`
  posé par le constructeur de la classe, lu exactement comme le fait `setAnim` ;
  `isRunning` est mis à `true` au *GameStartToPlay*, `false` en fin de combat) ;
  **hors combat — y compris l'agression/approche — le déplacement reste normal**
  (glissement d'origine intact). On évite `Game.isFight` qui passe à vrai trop
  tôt (dès l'agression) et faisait téléporter les déplacements d'approche. C'est
  sûr : `moveToCell` met déjà à jour la cellule / `isInMove` / la carte
  **synchroniquement** avant l'animation, donc `basicMove` n'est que du rendu.
- **Méthode — patch P-code (pas de recompilation source)** : recompiler toute la
  classe depuis le source décompilé n'est **pas fidèle** (JPEXS perd des
  déclarations `var` et restructure les closures `onEnterFrame` de `setAnim`, ce
  qui casse l'animation de marche → déplacements annulés `GKE`, voire crash
  client). On exporte donc le **P-code** de la classe, on insère le bloc de
  téléportation (gardé par `isRunning`) en tête de `basicMove`, puis on
  réassemble : le P-code est réassemblé 1:1, donc **seules les ~9 instructions
  ajoutées changent**, tout le reste du bytecode est identique à l'octet près.
- **Génération** : `python tools/build_noanim_swf.py` (exporte le P-code via
  JPEXS, injecte le bloc dans `basicMove`, réassemble) produit `core.noanim.swf`
  à côté de `core.swf` et sauvegarde l'original en `core.swf.orig.bak`. **À
  relancer après chaque mise à jour du client** (le `core.swf` change). Nécessite
  JPEXS/FFDec (`FFDEC_JAR` ou `--ffdec`).
- **Toggle** : onglet **Paramètres → No-anim**. Le checkbox échange
  `core.noanim.swf` ↔ `core.swf.orig.bak` par-dessus `core.swf`
  (`bot/noanim.py`). **Effet au prochain lancement du client.** L'état actif est
  lu depuis le disque (hash de `core.swf`), pas un flag persisté.
- ⚠️ **Risque de ban** : modifier le client est détectable. À éviter en
  arène/donjon ou sous surveillance.

## Jeu ralenti quand un spectateur observe (Im 036)

Quand un joueur rejoint le combat en mode spectateur, le serveur envoie `Im 036;{pseudo}` à tous les participants (« {pseudo} vient de rejoindre le combat en spectateur »). Pour réduire la suspicion d'automatisation tant qu'on est regardé, le bot bascule alors en **jeu lent** :

- `game/state.py` (`_on_im_message`) lève le flag `_spectator_present` sur `Im 036` (exposé via `is_spectator_present()`).
- `bot/combat.py` insère un délai aléatoire **avant chaque action** : lancement de sort (`cast_spell`), déplacement (`_move_caster_to`) et fin de tour (`end_turn`), via le helper `_spectator_slowdown()`. Les bornes (en ms) sont configurables dans l'onglet **Combat → Comportement → Discrétion** (`combat_spectator_delay_min/max_ms`, défaut **600-2000 ms**).
- Le ralenti dure **jusqu'à la fin du combat** : le flag est remis à `False` au début du combat suivant (handler `GJ`). Donc dès qu'un combat se déroule sans spectateur, le bot rejoue à pleine vitesse — et il re-ralentit automatiquement si quelqu'un rejoint à nouveau.

## Lancement ralenti quand un joueur est sur la carte (option)

Complémentaire du ralenti spectateur, mais **entre les combats** : si un autre joueur (hors nos héros) est visible sur la carte, le bot peut temporiser avant d'engager chaque combat pour ne pas farmer à cadence robotique sous les yeux d'un témoin.

- **Détection** : `game/state.py:has_foreign_player_on_map()` parcourt `current.entities` et renvoie `True` s'il existe une entité `ENTITY_PLAYER` ≠ notre perso. Nos héros sont déjà exclus de `current.entities` (retirés via `Nx` → `_companion_ids`), tout comme les monstres/PNJ/percepteurs (filtrés par `entity_type`).
- **Effet** : dans `combat_farm_loop`, juste avant `start_fight_biggest_group`, le bot attend un délai aléatoire dont les bornes (en ms) sont configurables dans l'UI (`combat_player_delay_min/max_ms`, défaut **2000-4000 ms**). La détection est fiable à ce point car les `GM` entity lists sont déjà arrivées (`FIGHT_START_DELAY`).
- **Toggle** : option `combat_slow_when_player` (défaut `False`), cochable dans l'onglet **Combat → Comportement → Discrétion**. Persistée dans `bot_settings.json` et appliquée au `GameState` au démarrage. Décochée, les combats s'enchaînent à pleine vitesse même avec des joueurs présents.

## Délai avant « Prêt » au lancement d'un combat (option)

Quand un combat démarre, le serveur envoie `GJ` (phase de placement) et le bot répond `GR1` (« prêt ») pour lancer immédiatement. Pour ne pas confirmer le placement à la milliseconde (cadence robotique), on peut insérer un délai aléatoire avant le `GR1`.

- **Effet** : dans `game/state.py:_on_game_join` (handler `GJ`), si l'option est activée, le bot attend un délai aléatoire (bornes en ms) avant d'envoyer `GR1`. L'envoi reste asynchrone (`loop.create_task`), donc le handler ne bloque pas.
- **Toggle** : option `combat_delay_before_ready` (défaut `False`), cochable dans l'onglet **Combat → Comportement → Discrétion**. Bornes `combat_ready_delay_min/max_ms` (défaut **100-500 ms**). Décochée, `GR1` part immédiatement (vitesse actuelle).
- À ne pas confondre avec le **délai entre combats** (`combat_player_delay_*`, qui temporise *avant* d'engager le combat suivant) : celui-ci temporise *pendant* la phase de placement, une fois le combat déjà engagé.

## Boucle de combat (`fight_group`)

```python
async def fight_group():
    while in_combat:
        turn_id = await wait_next_ally_turn(timeout=60)
        if turn_id is None:
            break  # timeout ou combat terminé
        await _play_cra_turn(label, caster_id=turn_id)
```

La queue `_combat_turn_queue` reçoit les IDs de chaque allié quand son GTS arrive. Les tours de monstres (IDs négatifs) sont ignorés.

## Boucle de farming (`combat_farm_loop`)

1. Attendre que la map soit prête (GDK).
2. Vérifier qu'on est sur la bonne map (sinon naviguer via sol magique).
3. Vérifier le surpoids → dépôt banque si nécessaire.
4. Trouver le groupe de monstres avec le plus d'entités, filtré par `combat_max_monsters_per_group` (option UI, 1-8, défaut 8 = pas de limite).
5. Engager le combat (`GA907{cell};{entity_id}`).
6. `fight_group()` gère le combat.
7. Recommencer.
