# Architecture du bot

## Vue d'ensemble

Le bot est un **proxy MITM** (Man-in-the-Middle) qui s'intercale entre le client Flash Dofus Rétro (Abrak.exe) et les serveurs de jeu. Il observe le trafic réseau, maintient un état de jeu local, et injecte ses propres commandes.

Le bot est **multi-instance** : plusieurs clients Dofus peuvent être ouverts en même temps. Chaque client = une **`Session`** indépendante (= une « team » = 1 perso principal + ses héros), avec son propre état de jeu, ses clés réseau, ses boucles bot et son onglet dans le dashboard.

```
┌──────────┐                          ┌──────────────┐
│ Abrak #1 │◄────┐              ┌─────►│  Serveur     │
│ (Flash)  │     │  ┌────────┐  │      │  Dofus 1.29  │
└──────────┘     ├─►│ Proxy  │──┤      └──────────────┘
┌──────────┐     │  │  MITM  │  │   (1 Session par connexion :
│ Abrak #2 │◄────┘  └───┬────┘  └────►  state/clés/bot isolés)
│ (Flash)  │            │
└──────────┘     ┌──────┴───────┐
                 │  Dashboard   │  TeamBar (onglets par team)
                 │  (tkinter)   │  + 1 jeu d'onglets par Session
                 └──────────────┘
```

> Détails du modèle multi-instance : voir [§ Multi-instance (Sessions)](#multi-instance-sessions) plus bas.

## Arborescence des fichiers

```
dofus-retro-bot/
├── main.py                    # Point d'entrée : élévation admin + lance proxy + GUI
├── config.json                # Configuration (ports, serveur cible) — non versionné
├── bot_settings.json          # Réglages combat PAR COMPTE ({defaults, accounts}) — non versionné
├── bot_settings.example.json  # Modèle versionné de bot_settings.json
│
├── core/                      # Modèle multi-instance
│   ├── __init__.py
│   └── session.py             # Session, ChannelState, NavState, SessionManager,
│                              #   contextvar `_active`, launch_in_session/call_in_session
│
├── proxy/                     # Couche réseau MITM
│   ├── server.py              # Serveur TCP asyncio (accepte Flash)
│   ├── client.py              # Connexion TCP vers le serveur Dofus
│   ├── relay.py               # Relais bidirectionnel Flash ↔ Serveur
│   ├── divert.py              # WinDivert : redirection kernel (admin)
│   └── frida_hook.py          # Frida : hook ws2_32!connect (non-admin)
│
├── protocol/                  # Parsing et encodage du protocole Dofus
│   ├── constants.py           # Dictionnaires SERVER_MESSAGES / CLIENT_MESSAGES
│   ├── parser.py              # Extraction msg_id, dispatch handlers
│   ├── encoding.py            # Chiffrement/déchiffrement, échange de clés
│   └── messages/              # Parsers de messages spécifiques
│       ├── stats.py           # CharacterStats, EntityInfo, CLASSES
│       ├── map.py             # MapData (GDM)
│       ├── jobs.py            # JobStats (JX, Jx)
│       ├── inventory.py       # Parsing inventaire (OA, OQ)
│       └── auth.py            # Authentification (AYK)
│
├── game/                      # État de jeu et handlers
│   └── state.py               # GameState (1 par Session) + handlers S→C/C→S ;
│                              #   `current` = proxy vers la Session active
│
├── bot/                       # Logique de bot
│   ├── __init__.py            # Référence à la boucle asyncio principale
│   ├── channel.py             # Injection C→S (chiffrée) — opère sur la Session active
│   ├── combat.py              # IA de combat (stratégie Crâ générique) — état par-Session
│   ├── pathfinding.py         # A* overworld, encodage GA001, ZIPKEY
│   ├── mapdata.py             # Chargement maps XML (cellules bloquées, LdV)
│   ├── mapnav.py              # Navigation inter-maps (état dans Session.nav)
│   ├── actions.py             # Primitives : déplacement, récolte, interaction
│   ├── harvester.py           # Boucle de récolte (instance par-Session)
│   ├── bank.py                # Dépôt en banque automatique
│   ├── merchant.py            # Vente d'équipements à un PNJ marchand (ER→EMO→EK→EV)
│   ├── merchant_config.py     # Réglages vente (niveau max, blacklist, familles)
│   ├── inventory.py           # Dump d'inventaire complet via patch client (#ZI→ZO)
│   ├── noanim.py              # Bascule no-anim (swap core.swf)
│   ├── script_engine.py       # Moteur de scripts (instance par-Session)
│   └── auto_reply.py          # Réponse auto aux MP (cooldown par-Session)
│
├── dashboard/                 # Interface graphique tkinter (multi-team)
│   ├── gui.py                 # Fenêtre + TeamBar (onglets team) + TeamView par Session
│   ├── bridge.py              # Bridge pub/sub PAR Session + bus lifecycle global
│   ├── statusbar.py           # Barre de status d'une team (Flash/Perso/Map/Combat/Bot)
│   ├── notifications.py       # Toasts non-modal globaux (préfixés du perso émetteur)
│   └── tabs/                  # Onglets d'une team (chaque onglet reçoit sa `session`)
│       ├── personnage.py      # Stats personnage + barres HP/PA/PM/Énergie
│       ├── carte.py           # Visualisation carte (grille isométrique + entités)
│       ├── combat.py          # Contrôle combat + sous-onglets Sorts PAR CLASSE (persist. par compte)
│       ├── inventaire.py      # Inventaire complet en 4 familles (équip/conso/ressource/autre) + poids
│       ├── metiers.py         # Métiers et niveaux
│       ├── scripts.py         # Gestion des scripts
│       ├── misc.py            # Onglet « Misc » (sous-notebook)
│       ├── misc_vendre.py     # Sous-onglet « Vendre au marchand »
│       ├── parametres.py      # Édition config.json (IP/ports, path Dofus, notifs, restart)
│       └── console.py         # Console de logs (événements de cette team)
│
├── data/                      # Parsers de données statiques
│   ├── spell_data.py          # Parser spells.xml (SpellInfo, SpellLevelInfo)
│   ├── items_db.py            # Base d'items (gid→type/niveau/nom), classify(), is_equipment()
│   └── recolte.py             # Données de récolte (ressources par métier)
│
├── ressources/                # Données de jeu extraites
│   ├── spells.xml             # Tous les sorts (9280 lignes, ~1100 sorts)
│   ├── items.json             # Base d'items extraite du CDN (gid→{l,t,n}) — cf. tools/extract_items.py
│   ├── Items/                 # Icônes PNG par gid
│   └── maps/                  # ~7770 fichiers XML de maps
│       └── {map_id}.xml       # Données d'une map (cellules, obstacles, LdV)
│
├── scripts/                   # Scripts de bot prédéfinis
│   ├── combat_farm.py         # Farming combat en boucle
│   ├── feudala_loop.py        # Trajet Feudala (récolte)
│   ├── feudala_full.py        # Trajet Feudala complet
│   └── mine_brakmar.py        # Trajet mine Brakmar
│
├── tools/                     # Outils de diagnostic / build
│   ├── sniff_server.py        # Sniffer TCP brut
│   ├── log_anticheat.py       # Logging anti-cheat
│   ├── hook.py                # Tests hooks Frida
│   ├── find_server_ip.py      # Trouver l'IP du serveur
│   ├── find_config_files.py   # Trouver les fichiers de config du jeu
│   ├── diagnose_connections.py # Diagnostic connexions réseau
│   ├── extract_items.py       # Génère ressources/items.json depuis le CDN (cdn.abrak.fr)
│   ├── build_noanim_swf.py    # Build core.noanim.swf (patch P-code déplacement)
│   ├── build_invdump_swf.py   # Build patch dump d'inventaire (#ZI→ZO)
│   └── inventory_dump_patch.as # Référence AS2 du patch dump d'inventaire
│
├── tests/                     # Tests unitaires (python -m pytest tests/)
│   ├── test_encoding.py       # Chiffrement/déchiffrement, Base64 Dofus, AYK
│   ├── test_gm_entities.py    # Classement des entités GM (percepteur, prisme, PNJ…)
│   └── test_combat_movement.py # Mouvement refusé (tacle) + positionnement distance
│
└── logs/                      # Logs de session (générés automatiquement)
    └── session_YYYYMMDD_HHMMSS.log
```

## Flux de démarrage

0. **Élévation admin** : `main.py:_elevate_if_needed()` relance le bot via UAC s'il n'est pas déjà administrateur (une seule demande au lancement ; `--no-admin` pour sauter). But : éviter un popup UAC par client et privilégier WinDivert.
1. `main.py` charge `config.json` (ports, serveur cible).
2. Configure le serveur proxy TCP (`proxy/server.py`).
3. Démarre le dashboard tkinter dans un thread séparé.
4. Active l'interception réseau :
   - **Admin** → WinDivert SOCKET layer (`proxy/divert.py`) — kernel, multi-client sans course d'injection (recommandé).
   - **Non-admin** → Hook Frida ws2_32!connect (`proxy/frida_hook.py`).
5. Le proxy écoute sur `proxy_host:proxy_port` (défaut 127.0.0.1:8080).
6. Quand un client Flash se connecte, le proxy **crée une `Session`** (`SessionManager.create`), pose le contextvar (`set_active`), puis ouvre une connexion vers le vrai serveur.
7. Le relais (`proxy/relay.py`) copie les octets dans les deux sens (avec la Session passée explicitement).
8. Chaque message est parsé et dispatché aux handlers (`protocol/parser.py`), dans le contexte de la Session.

## Flux d'un message

```
Flash envoie "GA300161;105\n"
        │
        ▼
proxy/relay.py (relay_client_to_server)
        │
        ├── protocol/parser.py:parse_message() → ParsedMessage(id="GA300", ...)
        ├── protocol/parser.py:log_message() → affiche dans les logs
        ├── protocol/parser.py:dispatch() → appelle @on_client_message("GA300")
        │
        └── forward vers le serveur Dofus

Serveur répond "GA;300;403035;161,105,..."
        │
        ▼
proxy/relay.py (relay_server_to_client)
        │
        ├── protocol/parser.py:parse_message() → ParsedMessage(id="GA", ...)
        ├── protocol/parser.py:dispatch() → appelle @on_server_message("GA")
        │       └── game/state.py:_on_game_action() → met à jour GameState
        │
        └── forward vers Flash
```

## Injection de commandes par le bot

Le bot n'intercepte ni ne modifie les messages existants. Il **injecte** ses propres messages via `bot/channel.py` :

```python
await channel.send("GA300161;105\n")  # Lancer un sort
await channel.send("Gt\n")            # Fin de tour
await channel.send("GA001abcdef\n")   # Déplacement
```

`channel.send()` :
1. Chiffre le message avec les clés AK de la **Session active** (si connexion game).
2. Ajoute le délimiteur `\x00`.
3. Écrit dans le `StreamWriter` de la Session vers le serveur.

## Multi-instance (Sessions)

Tout l'état autrefois global est encapsulé dans une **`Session`** (`core/session.py`), créée par le proxy à chaque connexion TCP entrante. Une Session porte :
- `channel` (`ChannelState`) : `StreamWriter` serveur + clés de chiffrement (`a_keys`, `current_key`).
- `game_state` (`GameState`) : l'état de jeu dédié.
- `bridge` (`Bridge`) : le bus pub/sub dédié à l'UI de cette team.
- l'état d'exécution des boucles bot (combat / récolte / scripts) et `nav` (`NavState`) pour la navigation.
- `main_character`, `status`, `ui_registered`.

### Routage côté asyncio — `contextvars`

Un `contextvars.ContextVar` (`core.session._active`) désigne la Session courante. `proxy/server.handle_client` fait `set_active(session)` **dans la task de la connexion** ; les tasks filles (relays, handlers dispatchés, coroutines bot lancées depuis ce contexte) **héritent** automatiquement de la Session. Ainsi :
- `game.state.current` est un **proxy** (`_CurrentProxy`) qui forwarde lecture/écriture/suppression d'attributs vers `active().game_state`. Les ~160 `current.X` (internes à state.py **et** externes `_state.current.X`) ciblent la bonne Session **sans réécriture des call-sites**.
- `bot.channel.*` opère sur `active().channel`.

> ⚠️ Conséquence : tout accès à un ex-global d'état depuis un **autre module** doit passer par le proxy (`_state.current._map_loaded`, pas `_state._map_loaded`) — sinon `AttributeError`. Idem pour les clés (`channel.get_a_keys()`, pas `channel._a_keys`).

### Hot-path chiffrement — passage explicite

`proxy/relay.py` reçoit la `session` **explicitement** (jamais via le contextvar) pour lire/écrire `session.channel.a_keys` / `.current_key`. C'est le point le plus sensible : un compteur de clés partagé entre 2 connexions = désync = kick serveur.

### Routage côté UI (thread tkinter)

Le thread tkinter n'a **pas** de Session active : chaque onglet/`StatusBar` détient une référence **explicite** à sa `Session` et lit `session.game_state` / `session.bridge`. Pour lancer une coroutine bot ciblant une team précise, l'UI utilise `core.session.launch_in_session()` / `call_in_session()` (qui posent le contextvar dans un contexte copié avant de créer la task).

### Cycle de vie d'une team (UI)

`dashboard/bridge.py` expose un **bus lifecycle global** (`subscribe_lifecycle` / `emit_lifecycle`). L'onglet team n'est créé **qu'à la sélection du personnage** (`Session.set_connected`, au packet `ASK`) → les connexions d'authentification (sans perso) ne créent jamais d'onglet fantôme. Topics : `session_added`, `session_status` (pastille verte/rouge/orange + nom), `session_removed`, `notification`.

## Modules principaux

### `game/state.py` — Le cœur du bot

Contient le `GameState` (dataclass, **une instance par Session**) qui centralise tout l'état du jeu :
- Personnage principal (pseudo, classe, niveau, stats)
- Carte courante (map_id, map_data)
- Entités visibles (joueurs, monstres, PNJ)
- État combat (positions, HP, PA, PM, PO de chaque entité)
- Sorts du personnage (depuis packet SL)
- Sort d'attaque sélectionné
- Les ex-globals module (events asyncio, queues de tours, `_pending_*`, inventaire, métiers…) sont désormais des **champs de `GameState`**.

`current` n'est plus un singleton mais un **proxy** vers `active().game_state` (cf. § Multi-instance). Contient aussi **tous les handlers de packets** via `@on_server_message` / `@on_client_message` ; le dispatch s'exécute dans le contexte de la Session de la connexion.

### `bot/combat.py` — L'IA de combat

Gère un combat complet :
- `fight_group()` : boucle principale, consomme la queue de tours.
- `_play_cra_turn()` : stratégie par Crâ (trouver cible, vérifier portée/LdV, bouger, attaquer).
- `combat_farm_loop()` : boucle de farming (chercher monstres, combattre, recommencer).

Voir [COMBAT.md](COMBAT.md) pour les détails.

### `dashboard/bridge.py` — Communication inter-threads (par Session)

Classe **`Bridge`** (pub/sub thread-safe), **une instance par Session** :
- `update_*(data)` : met à jour l'état + notifie les abonnés (méthodes d'instance).
- `subscribe(topic, callback)` : s'abonner aux mises à jour de cette team.
- Topics : `"character"`, `"map"`, `"combat_stats"`, `"spells"`, `"console"`, `"entity_set"`, etc.

Des **fonctions module** `update_*()` délèguent au `Bridge` de la Session active (appels côté asyncio inchangés). Un **bus lifecycle global** (`subscribe_lifecycle`/`emit_lifecycle`) gère le cycle de vie des teams et les notifications toast.

### `bot/channel.py` — Injection réseau

Opère sur le `ChannelState` de la **Session active** : `StreamWriter` serveur + clés rotatives (AK protocol). Accesseur `get_a_keys()` pour les modules externes (jamais `._a_keys`). `inject_to_client()` écrit vers le **client Flash** (S→C, via `ChannelState.client_writer`) — sert au dump d'inventaire (`#ZI`).

### Vente au marchand & inventaire complet

- **`bot/merchant.py`** : vend les équipements en sac à un PNJ marchand. Détecte le PNJ via `find_merchant_npc(gfx)` (entités GM de sprite type `-4`), filtre par famille/niveau/blacklist (`data/items_db.py`), puis joue `ER2|{npcId}` → `EMO` → attend `Em KG` → `EK` → `EV`. Réglages persistés par `bot/merchant_config.py` (`merchant_config.json`).
- **`data/items_db.py`** : charge `ressources/items.json` (généré du CDN par `tools/extract_items.py`). Expose `get_level/get_type_name`, `is_equipment`, `classify` (équipement/consommable/ressource/autre).
- **Inventaire complet** (sans `OT`) : `bot/inventory.request_full_inventory()` injecte `#ZI` au client patché, qui renvoie `ZO{...}` ; `proxy/relay.py` capte ce `ZO`, le dispatche (`_on_full_inventory` → `current._inventory`) et le **drop** (pas vers le serveur). Voir [GAME_CLIENT.md §10](GAME_CLIENT.md#10-patch--dump-dinventaire-à-la-demande-zi--zo). Les onglets Inventaire/Vendre le rafraîchissent à l'ouverture (throttle).

### `proxy/relay.py` — Re-chiffrement C→S

Point critique : le client Flash et le bot partagent un compteur de clé par Session (`session.channel.current_key`). Le relay reçoit la `session` **explicitement** (pas via le contextvar — hot-path) et :
1. **Déchiffre** les messages C→S du Flash (pour les parser/loguer).
2. **Re-chiffre** avec le compteur de la Session.
3. Les messages injectés par le bot (même Session) utilisent le même compteur.
4. **Drop des `ZO`** : le message custom du patch client (dump d'inventaire, en clair) est parsé puis **non transmis** au serveur (qui ne le connaît pas). En clair ⇒ pas de compteur de clé avancé ⇒ pas de désync.

Sans isolation par Session, deux connexions simultanées corromperaient mutuellement la séquence de clés (kick serveur immédiat).

## Points d'attention

1. **Chemins ressources** : toutes les données statiques (maps, `Recolte.txt`, sorts, items) vivent dans `ressources/` et sont résolues via `core.paths.RESSOURCES_DIR`. Aucune donnée n'est attendue hors du dépôt. Voir [RESOURCES.md](RESOURCES.md).
2. **Deux moteurs de bot** : `harvester.py` (récolte single-map) et `script_engine.py` (scripts multi-maps ou custom `async run()`).
3. **Position joueur** : la source de vérité est toujours la confirmation serveur `GA;1` (S→C), jamais le `GA001` (C→S) envoyé localement.
4. **Dépendances d'interception** : `pydivert` (admin/WinDivert) et `frida` (non-admin) sont importés conditionnellement, tous deux listés dans `requirements.txt`. Sans `pydivert`, même en admin l'interception retombe sur frida.
5. **Multi-instance — pièges** :
   - Tout accès à l'état de jeu **depuis un autre module** passe par le proxy : `_state.current.X` (jamais `_state.X` pour un ex-global déplacé) ; clés via `channel.get_a_keys()` (jamais `channel._a_keys`).
   - Le code asyncio doit s'exécuter **dans le contexte d'une Session** (relay/handlers : automatique ; lancements UI : via `launch_in_session`/`call_in_session`). Hors contexte, `current` / `channel.*` lèvent `RuntimeError("Aucune session active")`.
   - Les onglets/`StatusBar`/bots reçoivent une `session` explicite ; ne jamais lire `game.state.current` depuis le thread tkinter.
6. **Dette mineure** : `proxy/server.py:_pending_game_server` (chemin AYK legacy) reste global ; `bot/harvester_multimap` est importé par l'onglet Scripts mais n'existe pas.
