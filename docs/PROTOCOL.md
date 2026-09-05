# Protocole Dofus Rétro 1.29

## Vue d'ensemble

Le protocole Dofus Rétro 1.29 est un protocole texte TCP. Chaque message est délimité par un octet `\x00` (null) ou un `\n` (newline). Les messages n'ont **aucun séparateur** entre l'identifiant (msgId) et le payload — il faut un matching par longueur décroissante.

Référence : [retroproto](https://github.com/kralamoure/retroproto) (implémentation Go exhaustive).

## Directions

| Notation | Direction | Signification |
|----------|-----------|---------------|
| S→C | Serveur → Client | Le serveur envoie au client Flash |
| C→S | Client → Serveur | Le client Flash (ou le bot) envoie au serveur |

## Extraction du msgId

L'ID est un préfixe de longueur variable (1 à 4 chars). L'algorithme :

1. Tester tous les IDs connus par longueur décroissante (**longest match**).
2. Si aucun match → fallback sur les 2 premiers caractères.
3. Le payload = tout ce qui suit le msgId.

### Cas spéciaux (C→S uniquement)

| Pattern | ID résolu | Raison |
|---------|-----------|--------|
| `^\d+\.\d+\.\d+` | `version` | Envoi de la version du client |
| `^[\w-@]+\n#\d` | `credential` | Envoi des identifiants |
| `G\u0406...` | `GI` | Bug cyrillique post v1.29.1 |

## Chiffrement

### Flux d'échange de clés

1. Le serveur envoie `AK{hex_keys}` après la connexion game.
2. Le bot parse les clés : `prepare_key(hex_string)` convertit les paires hex en caractères.
3. 16 clés sont stockées dans `_aKeys[0..15]` avec un index `_nCurrentKey`.

### Chiffrement C→S

Tous les messages C→S ne sont pas chiffrés. Les messages à chiffrer sont déterminés par `_should_encrypt()` :

| Préfixe | Messages chiffrés |
|---------|-------------------|
| `G` | `GA*`, `GK*`, `GM*` (actions, acks, mouvements) |
| `W`, `e`, `O`, `D`, `F`, `K`, `z`, `w`, `S`, `B` | Tous |
| `A` | `AA*`, `AD*`, `AZ*` |
| `E` | `EV`, `ER`, `Es`, `EA`, `EK`, `EP`, `ES`, `EB`, `EQ`, `Eq`, `Ew`, `EM`, `EH` |
| `N` | `NA*`, `NR*` |

### Algorithme de chiffrement

```
prepare_data(msg):
  1. new_key = (current_key + 1) % 16 (cycler de 1 à 15)
  2. checksum = HEX[sum(ord(c) % 16 for c in msg) % 16]
  3. offset = int(checksum, 16) * 2
  4. encrypted = cypher_data(msg, a_keys[new_key], offset)
  5. result = "-" + HEX[new_key] + checksum + encrypted

cypher_data(data, key, offset):
  for i, c in enumerate(data):
    xor_byte = ord(c) ^ ord(key[(i + offset) % len(key)])
    output += hex(xor_byte, 2 digits)
```

### Alphabets

| Nom | Valeur | Usage |
|-----|--------|-------|
| `DOFUS_CHARSET` | `aAbBcCdDeEfFgG...zZ0-9-_` (interleaved) | Cellules, chemins GA001 |
| `ZIPKEY` | `abcd...xyzABCD...XYZ0-9-_` (alphabétique) | Hash mot de passe, port AYK |

## Messages Serveur → Client (S→C)

### Authentification et connexion

| ID | Nom | Format | Description |
|----|-----|--------|-------------|
| `HC` | AksHelloConnect | `HC{key}` | Salutation login + clé de hash MDP |
| `HG` | AksHelloGame | `HG` | Salutation serveur de jeu |
| `AK` | AccountKey | `AK{hex_keys}` | Clés de chiffrement réseau (16 clés hex) |
| `AYK` | AccountSelectServerPlainSuccess | `AYK{8ch_ip}{3ch_port}{ticket}` | Redirection vers le game server |
| `AlK` | AccountLoginSuccess | `AlK{admin_level}` | Login réussi |
| `ALK` | AccountCharactersListSuccess | `ALK...` | Liste des personnages |
| `ASK` | AccountCharacterSelectedSuccess | `ASK\|{id}\|{name}\|{level}\|...` | Personnage sélectionné |
| `ATK` | AccountTicketResponseSuccess | `ATK` | Ticket validé |
| `As` | AccountStats | `As{stats_payload}` | Stats complètes du personnage |
| `AN` | AccountNewLevel | `AN{level}` | Nouveau niveau |

### Carte et mouvement

| ID | Nom | Format | Description |
|----|-----|--------|-------------|
| `GDM` | GameMapData | `GDM\|{map_id}\|{date}\|{key}` | ID de la carte à charger |
| `GDK` | GameMapLoaded | `GDK` | Confirmation de chargement de carte |
| `GDF` | GameFrameObject2 | `GDF\|{cell}\|{state}\|...` | État des éléments interactifs (ressources) |
| `GM` | GameMovement | `GM\|+{cell}\|{entity_data};...` | Entités sur la carte |
| `GM\|-` | GameMovementRemove | `GM\|-{entity_id}` | Entité quitte la carte |

**Discrimination des entités GM (champ index 5 = sprite type)** : dans une entrée
GM, `field[3]` est l'id et **`field[5]` le sprite type** (enum retroproto
`GameMovementSpriteType`). ⚠️ C'est **`field[5]`, et non le signe de l'id**, qui
détermine le type : percepteurs, prismes, montures en parc et PNJ ont tous un id
**négatif** comme les monstres, tandis qu'un personnage hors-ligne a un id
**positif** comme un joueur.

| Sprite type | Entité | Traitement dans `parse_gm_entities` |
| --- | --- | --- |
| `-1` / `-2` | Créature / Monstre | `monster_groups` — attaquable |
| `-3` | Groupe de monstres | `monster_groups` — attaquable |
| `-4` | PNJ (marchand, banquier…) | `npcs` — ciblable via `ER{type}\|{id}` |
| `-5` | Personnage hors-ligne | `others` — non attaquable |
| `-6` | **Percepteur** | `others` — non attaquable |
| `-7` / `-8` | Mutant / Joueur muté | `others` — non attaquable |
| `-9` | Monture en parc | `others` — non attaquable |
| `-10` | Prisme | `others` — non attaquable |
| `>= 0` | Joueur (classe 1-12) | `players` |

Seuls les sprite types `-1`/`-2`/`-3` sont des cibles valides de `GA907`. Un
percepteur (`-6`) trié parmi les monstres produisait un `GA907` que le serveur
**ignore silencieusement** : le bot se déplaçait sur sa cellule puis bouclait sur
des retries jusqu'à blacklister l'entité (observé cell 331, id `-284290`, lu comme
un groupe de 2 monstres Nv.200 — son nom `39,31` étant pris pour des templateIds).

Formats des entités `others`, tous préfixés de `{cell};{dir};0;{id};` :

```
-5   {name};-5;{gfx}^{scale};{c1};{c2};{c3};{acc};{guild};{emblem};{offlineType}
-6   {firstNameId,lastNameId};-6;{gfx}^{scale};{level};{guild};{emblem}
-9   {name};-9;{gfx}^{scale};{ownerName};{level};{modelId}
-10  {templateId};-10;{gfx}^{scale};{level};{alignValue};{alignIndex}
```

### Combat — État

| ID | Nom | Format | Description |
|----|-----|--------|-------------|
| `GJ` | GameJoin | `GJ` | Entrée en combat |
| `GS` | GameStartToPlay | `GS` | Le combat commence (placement terminé) |
| `GE` | GameEnd | `GE{duration}\|{winner}\|{rewards}` | Fin de combat |
| `GP` | GamePositionStart | `GP{positions}\|{team}` | Positions de départ (phase placement) |

### Combat — Tours

| ID | Nom | Format | Description |
|----|-----|--------|-------------|
| `GTS` | GameTurnStart | `GTS {id}\|{time_ms}\|{turn_nb}` | Début du tour d'une entité |
| `GTF` | GameTurnFinish | `GTF{id}` | Fin du tour d'une entité |
| `GTM` | GameTurnMiddle | `GTM \|{id};{?};{hp};{pa};{pm};{cell};;{max_hp}\|...` | Stats actualisées de toutes les entités |
| `GTL` | GameTurnList | `GTL \|{id1}\|{id2}\|...` | Ordre d'initiative |
| `GTR` | GameTurnReady | `GTR {id}` | Entité prête |

### Combat — Actions

| ID | Nom | Format | Description |
|----|-----|--------|-------------|
| `GA` | GameActions | `GA;{action_id};{caster_id};{payload}` | Action de jeu (mouvement, sort, mort...) |
| `GAF` | GameActionsFinish | `GAF{action_id}\|{entity_id}` | Action terminée, attente d'ack client |
| `GAS` | GameActionsStart | `GAS{id}` | Début de séquence d'actions |

**Sous-types de GA** (champ `action_id`) :

| action_id | Signification | Payload |
|-----------|---------------|---------|
| 1 | Mouvement | `{cell_list_base64}` |
| 300 | Lancer de sort | `{spell_id},{target_cell},...` |
| 999 | Mort d'entité | `{entity_id}` |
| 102 | Dégâts | `{target},{dmg}` |
| 129 | Mouvement rejeté | `` |

### Sorts

| ID | Nom | Format | Description |
|----|-----|--------|-------------|
| `SL` | SpellsList | `SL{spell_id}~{level}~{position};...` | Liste des sorts du personnage principal |
| `SUK` | SpellsUpgradeSpellSuccess | `SUK{spell_id}~{level}` | Sort amélioré |

### Infos

| ID | Nom | Format | Description |
|----|-----|--------|-------------|
| `Im` | InfosMessage | `Im{code}[;{params}]` | Message d'information |
| `IM` | InfosInfoMaps | `IM{x}\|{y}` | Coordonnées de la carte |

**Codes Im importants** :

| Code | Signification |
|------|---------------|
| `1171` | Sort hors portée : `Im 1171;{min}~{max}~{actual}` |
| `1174` | Ligne de vue bloquée |
| `176` | Sort en cooldown |

### Chat

| ID | Nom | Format | Description |
|----|-----|--------|-------------|
| `cMK` | ChatMessageSuccess | `cMK{canal}\|{sender_id}\|{sender_name}\|{message}` | Message reçu |

Canaux : `F` = MP (From), `*` = général, `%` = groupe, `#` = commerce, etc.

### Inventaire

| ID | Nom | Format | Description |
|----|-----|--------|-------------|
| `OAK` | ItemsAddSuccess | `OAK{charId}\|{obj1}*{obj2}*...` | Objet(s) ajouté(s) — **séparés par `*`** |
| `OQ` | ItemsQuantity | `OQ{id}\|{qty}` | Quantité modifiée |
| `OR` | ItemsRemove | `OR{charId}\|{uid1}*{uid2}*...` | Objet(s) supprimé(s) |
| `Ow` | ItemsWeight | `Ow{current}\|{max}` | Poids actuel/max |
| `Or` | InventoryShortcut | `OrA{char};{slot};{gid}` | Raccourci de barre (≠ équipement) |

Format d'un objet : `O{uid_hex}~{gid_hex}~{qty}~{pos}~{effects}`. **`pos` vide ⇒ sac
(`63`).** ⚠️ `OAK`/`OR` séparent les objets par **`*`** (le `|` ne sépare que le
`charId`) — un parseur qui splitte sur `|` ne capte qu'1 objet sur N.

> **L'inventaire complet (`OT`) n'est jamais envoyé** sur ce serveur. Pour
> l'obtenir, le bot patche le client (message custom `#ZI` → `ZO`) — voir
> [GAME_CLIENT.md §10](GAME_CLIENT.md#10-patch--dump-dinventaire-à-la-demande-zi--zo).

### Échange / Marchand (vente PNJ)

| ID | Dir | Format | Description |
|----|-----|--------|-------------|
| `ER` | C→S | `ER{type}\|{npcId}` | Ouvrir un échange (type `2` = PNJ ; `npcId` négatif, cf. GM sprite type) |
| `ECK` | S→C | `ECK{type}\|{npcId}` ou `ECK5` | Échange ouvert (`5` = banque) |
| `EMO` | C→S | `EMO+{uid}\|{qty}+...` | Déposer des objets dans l'échange (**plaintext**, `+`/`%2B` add, `-` retrait) |
| `EM` | S→C | `EM KO+{uid}\|{qty}` | Objet placé (confirmation) |
| `Em` | S→C | `Em KG{total}` | **Kamas cumulés** offerts par le PNJ |
| `EK` | C→S | `EK` (vide) | **Valider** l'échange (ExchangeRequestReady) |
| `EK` | S→C | `EK 1{charId}` / `EK 1-{npc}` | Prêt (joueur / PNJ) |
| `EV` | S→C | `EV a` | Échange fermé (vente finalisée) |
| `EL` | S→C | `EL{obj1};{obj2};...` | Contenu banque/coffre à l'ouverture (objets `;`-séparés) |

Flux de vente : `ER2\|{npcId}` → `ECK` → `EMO+{uid}\|{qty}...` → attendre `Em KG`
(le prix apparaît) → `EK` (valider) → `OR`/`Ow`/`As`/`EV` (objets retirés, kamas
crédités). Cf. `bot/merchant.py`.

### Messages custom (patch client)

| ID | Dir | Format | Description |
|----|-----|--------|-------------|
| `#ZI` | S→C (injecté par le bot) | `#ZI` | Demander au client patché de dumper son inventaire |
| `ZO` | C→S (client patché) | `ZO{uid}~{gid}~{qty}~{pos};...` | Inventaire complet (capté + **droppé** par le relais, en clair) |

⚠️ Côté client, `pos = -1` pour un objet **en sac** (équipé = `pos ≥ 0`), alors que
`OAK`/`EL` utilisent `63`. Filtre « en sac » : `pos ∈ {-1, 63}`.

### Métiers

| ID | Nom | Format | Description |
|----|-----|--------|-------------|
| `JX` | JobXP | `JX K{char_id}~{job_id};{lvl};{xp};...` | Stats métier |
| `JS` | JobSkills | `JS K{char_id}_{job_id};{skills}` | Compétences métier |

### Entités (serveur privé Abrak)

| ID | Nom | Format | Description |
|----|-----|--------|-------------|
| `NL` | GameEntityCreate | `NL K+{id};{name};...` | Nouvelle entité sur la carte |
| `Nx` | GameEntityCompanion | `Nx{id}` | Entité marquée comme héros |

## Messages Client → Serveur (C→S)

### Actions de jeu

| ID | Nom | Format | Description |
|----|-----|--------|-------------|
| `GA001` | GameActionMove | `GA001{path_base64}` | Déplacement (chemin encodé) |
| `GA300` | GameActionCastSpell | `GA300{spell_id};{cell_id}` | Lancer un sort |
| `GA500` | GameActionHarvest | `GA500{cell};{type}` | Récolter une ressource |
| `GA900` | GameActionAggress | `GA900;{cell}` | Attaquer un groupe de monstres |
| `GKK` | GameActionAck | `GKK{action_id}` | Acquitter une action serveur |
| `GKE` | GameActionCancel | `GKE{action_id}` | Annuler une action |
| `Gt` | GameTurnEnd | `Gt` | Finir son tour |
| `GR` | GameRequestReady | `GR1` | Prêt à combattre |
| `GD` | GameGetMapData | `GD` | Demander les données de la carte |

### Chat

| ID | Nom | Format | Description |
|----|-----|--------|-------------|
| `BM` | ChatSend | `BM{dest}\|{message}\|` | Envoyer un MP |

### Navigation

| ID | Nom | Format | Description |
|----|-----|--------|-------------|
| `WU` | WaypointsUse | `WU{zaap_id}` | Utiliser un zaap |

## Encodage des cellules (Base64 Dofus)

Un ID de cellule (0-479) est encodé en 2 caractères de `DOFUS_CHARSET` :

```
cell_id = CHARSET.index(char1) * 64 + CHARSET.index(char2)
char1 = CHARSET[cell_id // 64]
char2 = CHARSET[cell_id % 64]
```

### Encodage des chemins (GA001)

Un chemin GA001 est composé de paires `{direction}{cellule}` :

- 1 caractère de direction (encodé dans les bits hauts du premier char).
- 2 caractères d'ID de cellule.

Les 4 directions diagonales en combat :
- 0 = droite-bas (SE)
- 2 = droite-haut (NE)
- 4 = gauche-haut (NW)
- 6 = gauche-bas (SW)

En overworld, les 8 directions sont disponibles (ajout de 1, 3, 5, 7 pour E, N, W, S).

## Encodage AYK (redirection game server)

Le payload `AYK` encode l'adresse du serveur de jeu :

```
AYK{8ch_ip}{3ch_port}{ticket}

IP (8 chars) :
  Pour chaque octet : chr(high_nibble + 48) + chr(low_nibble + 48)
  Exemple : 192 → chr(12+48) + chr(0+48) = "<0"

Port (3 chars ZIPKEY) :
  n1 = (port >> 12) & 63
  n2 = (port >> 6)  & 63
  n3 = port & 63
  Exemple : 5555 → ZIPKEY[1] + ZIPKEY[22] + ZIPKEY[51]
```

## Flux complet d'une connexion

```
1. Flash → Login Server
   C→S: version (ex: "1.29.1")
   S→C: HC{key}              ← clé pour hasher le MDP
   C→S: credential            ← pseudo + hash MDP
   S→C: AlK{level}            ← login OK
   C→S: Ax                    ← demander liste serveurs
   S→C: AxK{servers}          ← liste des serveurs
   C→S: AX{server_id}         ← choix du serveur
   S→C: AYK{encoded_ip_port_ticket}  ← redirection vers game server

2. Flash → Game Server
   S→C: HG                    ← hello game
   C→S: AT{ticket}            ← envoyer le ticket reçu dans AYK
   S→C: ATK                   ← ticket validé
   C→S: AL                    ← demander liste personnages
   S→C: ALK{characters}       ← liste des personnages
   C→S: AS{char_id}           ← choisir un personnage
   S→C: ASK|{id}|{name}|...   ← personnage sélectionné
   S→C: AK{hex_keys}          ← clés de chiffrement (à partir d'ici, certains C→S sont chiffrés)
   S→C: As{stats}             ← stats du personnage
   S→C: SL{spells}            ← sorts du personnage
   S→C: GDM|{map_id}|...      ← carte à charger
   C→S: GD                    ← demander données carte
   S→C: GDK                   ← carte chargée

3. Combat
   C→S: GA900;{cell}          ← engager un combat (ou monstre agresse)
   S→C: GJ                    ← on rejoint le combat
   S→C: GP{positions}         ← positions de départ
   C→S: GR1                   ← prêt
   S→C: GS                    ← le combat commence
   S→C: GTL|{ids}             ← ordre d'initiative
   S→C: GTM|{stats}           ← stats de toutes les entités
   S→C: GTS {id}|{time}|{n}   ← tour de l'entité {id}
   C→S: GA001{path}           ← déplacement (si besoin)
   S→C: GA;1;{id};{path}      ← mouvement confirmé
   S→C: GAF{action_id}|{id}   ← action terminée
   C→S: GKK{action_id}        ← acquitter l'action
   C→S: GA300{spell};{cell}   ← lancer un sort
   S→C: GA;300;{id};{details} ← sort confirmé + effets
   S→C: GAF{action_id}|{id}   ← action terminée
   C→S: GKK{action_id}        ← acquitter
   C→S: Gt                    ← fin de mon tour
   S→C: GTF{id}               ← tour terminé confirmé
   ... (tours des autres entités) ...
   S→C: GE{duration}|...      ← fin du combat
```
