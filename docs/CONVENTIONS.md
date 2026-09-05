# Conventions et principes de design

## Philosophie

Le bot est conçu pour être **générique** : il ne doit pas dépendre de noms de personnages, d'IDs hardcodés, ni d'une composition d'équipe spécifique. Toute la configuration doit être dynamique (lue depuis le serveur, les fichiers de jeu, ou l'UI).

## Règles fondamentales

### 1. Pas de valeurs hardcodées spécifiques à un personnage

- **Interdit** : `if character_id == "403035":`, `ETHERA_ID = "337259"`
- **Correct** : `if turn_id == current.character.character_id:`
- Les IDs de personnages, pseudos, niveaux changent à chaque reroll de team.

### 2. Les données de jeu viennent du serveur ou des fichiers

- Les sorts disponibles arrivent via le packet `SL` (SpellsList).
- Les stats (PA, PM, PO) arrivent via `GTM` (GameTurnMiddle) à chaque tour.
- Les propriétés des sorts (coût PA, portée, LdV) sont dans `ressources/spells.xml`.
- Les données de map (cellules bloquées, LdV terrain) sont dans `ressources/maps/{id}.xml`.

### 3. L'UI reflète l'état, l'état ne dépend pas de l'UI

- Le `GameState` est la source de vérité (une instance **par Session**).
- L'UI (dashboard tkinter) s'abonne au `Bridge` de **sa** Session et affiche l'état.
- Les choix utilisateur (ex: sort d'attaque) sont stockés dans le `GameState` de la Session ; les réglages combat persistants sont écrits par compte dans `bot_settings.json`.

### 4. Thread-safety : asyncio + tkinter

- Le bot tourne dans une boucle `asyncio` (thread principal).
- Le dashboard tkinter tourne dans un thread séparé.
- Communication via `dashboard/bridge.py` (pub/sub thread-safe avec `threading.Lock`).
- Les callbacks tkinter utilisent `root.after(0, callback)` pour poster sur le thread GUI.

### 5. Protocole : le proxy est passif, le bot est actif

- Le proxy (MITM) relaye les messages entre Flash ↔ Serveur sans les modifier.
- Le bot **injecte** ses propres messages C→S via `bot/channel.py`.
- Le bot **observe** les messages S→C via les handlers `@on_server_message`.
- Le bot **observe** les messages C→S via les handlers `@on_client_message`.

### 6. Synchronisation avec le client Flash

- Certaines actions serveur (GAF) nécessitent un ack du client Flash (GKK).
- Le bot doit **attendre** que le Flash ait envoyé ses GKK avant d'envoyer `Gt` (fin de tour) **ou la prochaine `GA300`** (cast suivant) — sinon le serveur rejette silencieusement avec `GA;102;-0`.
- `wait_spell_result()` enchaîne GAF puis `wait_actions_clear()` ; un filet de sécurité `await wait_actions_clear(timeout=1.2)` précède aussi chaque `cast_spell` dans `_play_cra_turn`. `end_turn()` appelle `wait_actions_clear()` avant `Gt`.

### 7. Distances en combat

- **Distance PO (portée)** : distance Manhattan en coordonnées diagonales (grille diamant).
- **Distance mouvement** : identique à la distance PO (4 voisins diagonaux en combat).
- **Ne pas confondre** avec la distance BFS 8-directions utilisée pour le déplacement overworld.

### 8. Nommage

| Contexte | Convention | Exemple |
|----------|-----------|---------|
| Fichiers Python | snake_case | `spell_data.py` |
| Classes | PascalCase | `GameState`, `SpellEntry` |
| Constantes | UPPER_SNAKE | `SPELL_CAST_DELAY`, `MAP_WIDTH` |
| Fonctions privées | _snake_case | `_po_distance()`, `_play_cra_turn()` |
| Handlers protocol | `_on_{msg_id_lower}` | `_on_gts()`, `_on_gkk()` |
| Variables d'état globales | `_snake_case` | `_combat_turn_queue` |

### 9. Logging

- `logger = logging.getLogger(__name__)` dans chaque module.
- Préfixer les messages avec le module : `[combat]`, `[GameState]`, `[channel]`.
- Niveaux :
  - `DEBUG` : détails internes (positions, calculs de distance).
  - `INFO` : actions importantes (tour de combat, sort lancé, déplacement).
  - `WARNING` : problèmes récupérables (GKK timeout, sort rejeté).
  - `ERROR` : problèmes graves (connexion perdue, parse impossible).

### 10. Gestion des erreurs

- Les handlers de packets sont wrappés dans `try/except` avec un `logger.warning`.
- Les fonctions de combat retournent `bool` (True = succès, False = erreur connexion).
- Les timeouts utilisent `asyncio.wait_for()` avec des constantes configurables.

### 11. Isolation par session (multi-instance)

Plusieurs clients Dofus = plusieurs `Session` indépendantes. Voir [ARCHITECTURE.md § Multi-instance](ARCHITECTURE.md#multi-instance-sessions).

- **Pas de nouvel état global mutable** spécifique à une connexion (writer, clés, événements, queues, flags de bot). Tout état par-connexion vit dans `Session` (`core/session.py`) — au besoin dans `GameState`, `ChannelState` ou `NavState`.
- **Côté asyncio** : accéder à l'état via `game.state.current` (proxy vers la Session active) et `bot.channel.*`. Ce code doit tourner dans le contexte d'une Session (relay/handlers : automatique).
- **Depuis un autre module** : toujours passer par le proxy — `_state.current._mon_flag`, jamais `_state._mon_flag` ; clés via `channel.get_a_keys()`, jamais `channel._a_keys`.
- **Côté UI (tkinter)** : pas de Session active dans ce thread. Chaque widget reçoit une `session` explicite et lit `session.game_state` / `session.bridge`. Lancer une coroutine bot ciblée via `core.session.launch_in_session()` / `call_in_session()`.
- **Hot-path chiffrement** : passer la `session` **explicitement** (jamais via contextvar) — cf. `proxy/relay.py`.
