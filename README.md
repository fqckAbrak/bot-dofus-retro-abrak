# Dofus Rétro Bot — Proxy MITM 1.29

Bot d'automatisation pour **Dofus Rétro 1.29** basé sur une architecture proxy MITM (Man-in-the-Middle).  
Le client Flash communique normalement avec le serveur — le bot intercepte, lit et injecte des messages protocole sans modifier le client.

---

## Comment ça marche

```
Flash (Abrak) ──► WinDivert ──► Proxy MITM (8080) ──► Serveur Dofus
                                       │
                               Parsing + Injection
                                       │
                                Bot / Dashboard
```

1. **WinDivert** (kernel Windows) intercepte les connexions TCP sortantes de Flash vers les ports game (1303/1304) et les redirige vers le proxy local — sans aucune configuration du client.
2. Le **proxy asyncio** relaie les messages dans les deux sens en parsant chaque paquet Dofus.
3. Les messages sont **déchiffrés à la volée** (XOR + Base64 Dofus) pour extraire l'état du jeu en temps réel.
4. Le bot **injecte ses propres messages** (déplacements GA001, récoltes GA500, actions combat…) en partageant le compteur de clés avec Flash pour ne pas désynchroniser le protocole.
5. Un **dashboard tkinter** affiche l'état du personnage, de la carte, de l'inventaire, des métiers et des combats.

**Fallback Frida** : si le bot est lancé sans droits administrateur, il injecte un hook JavaScript dans `Abrak.exe` via Frida pour intercepter `ws2_32.dll!connect()`.

---

## Prérequis

- **Windows 10/11** (WinDivert est Windows-only)
- **Python 3.10+**
- **[Abrak Launcher](https://www.dofus-retro.org/)** — client Dofus Rétro officiel
- **Droits Administrateur** pour le mode WinDivert (recommandé)

---

## Installation

```bash
# 1. Cloner le dépôt
git clone <url-du-repo>
cd dofus-retro-bot

# 2. Créer un environnement virtuel (recommandé)
python -m venv .venv
.venv\Scripts\activate

# 3. Installer les dépendances
pip install -r requirements.txt
```

### Dépendances

| Package    | Rôle                                        |
| ---------- | ------------------------------------------- |
| `pydivert` | Interception TCP kernel via WinDivert       |
| `psutil`   | Détection du processus Abrak                |
| `scapy`    | Analyse réseau (outil de détection serveur) |
| `frida`    | Hook `ws2_32!connect` (fallback sans admin) |

---

## Configuration

Créer un fichier `config.json` à la racine du projet :

```json
{
  "server_host": "51.89.153.20",
  "server_port": 26118,
  "proxy_host": "127.0.0.1",
  "proxy_port": 8080,
  "game_ports": [1303, 1304]
}
```

| Clé           | Description                                                                    |
| ------------- | ------------------------------------------------------------------------------ |
| `server_host` | IP du serveur Dofus Rétro (utilisée en fallback si WinDivert est indisponible) |
| `server_port` | Port du serveur                                                                |
| `proxy_host`  | Interface d'écoute du proxy (laisser `127.0.0.1`)                              |
| `proxy_port`  | Port local du proxy (laisser `8080`)                                           |
| `game_ports`  | Ports interceptés par WinDivert (ne pas modifier)                              |

> **Tip** : pour détecter automatiquement l'IP du serveur, lancer `python tools/sniff_server.py` pendant qu'Abrak est ouvert.

---

## Lancement

```bash
# Lancer en tant qu'Administrateur (mode WinDivert — recommandé)
python main.py
```

Puis **lancer Abrak Launcher** et se connecter normalement en jeu.  
Le proxy intercepte la connexion automatiquement.

Sans droits admin, le bot bascule automatiquement sur le hook Frida — Abrak doit déjà être lancé.

Le **dashboard tkinter** s'ouvre dans une fenêtre séparée au démarrage.

---

## Structure du projet

```
dofus-retro-bot/
├── main.py                  # Point d'entrée
├── config.json              # Configuration locale (non versionné)
├── requirements.txt
│
├── proxy/                   # Couche interception réseau
│   ├── server.py            # Serveur asyncio — accepte les connexions Flash
│   ├── relay.py             # Relay bidirectionnel + re-chiffrement C→S transparent
│   ├── client.py            # Connexion vers le vrai serveur Dofus
│   ├── divert.py            # WinDivert SOCKET layer — redirection kernel
│   └── frida_hook.py        # Hook Frida ws2_32!connect (fallback sans admin)
│
├── protocol/                # Parsing du protocole Dofus Rétro
│   ├── constants.py         # Tables des IDs de messages (200+ messages S→C et C→S)
│   ├── encoding.py          # Crypto : Base64 Dofus, XOR réseau, hash mdp, AYK
│   ├── parser.py            # Extraction des IDs + dispatcher @on_server/client_message
│   └── messages/            # Dataclasses des messages parsés
│       ├── auth.py          # CharacterInfo, ServerInfo
│       ├── stats.py         # CharacterStats, EntityInfo
│       ├── map.py           # MapData, FrameObjects, EntityMovement
│       ├── inventory.py     # Inventaire et objets
│       └── jobs.py          # Métiers et niveaux XP
│
├── game/
│   └── state.py             # GameState singleton — mis à jour par les handlers en temps réel
│
├── bot/                     # Logique d'automatisation
│   ├── channel.py           # Injection de messages C→S avec gestion du chiffrement
│   ├── actions.py           # Primitives : move_to(), harvest_with_move()
│   ├── harvester.py         # Boucle de récolte principale (greedy nearest-neighbor)
│   ├── mapnav.py            # Navigation entre cartes (changement de map)
│   ├── pathfinding.py       # A* + encodage chemin GA001
│   ├── mapdata.py           # Chargement des cartes XML et données de ressources
│   ├── combat.py            # Automatisation des combats tour par tour
│   ├── bank.py              # Dépôt en banque automatique
│   ├── auto_reply.py        # Réponses automatiques aux MPs (anti-détection)
│   └── script_engine.py     # Exécution dynamique de scripts utilisateur
│
├── dashboard/               # Interface graphique tkinter
│   ├── gui.py               # Fenêtre principale (7 onglets)
│   ├── bridge.py            # Bus d'événements thread-safe asyncio ↔ tkinter
│   └── tabs/
│       ├── personnage.py    # Stats du personnage (vie, PA/PM, caractéristiques)
│       ├── carte.py         # Vue de la carte et ressources
│       ├── inventaire.py    # Contenu de l'inventaire
│       ├── metiers.py       # Niveaux des métiers
│       ├── combat.py        # Suivi des combats et statistiques de session
│       ├── scripts.py       # Lancement / arrêt des scripts
│       └── console.py       # Journal des événements en temps réel
│
├── data/
│   └── recolte.py           # Données statiques des ressources (type → métier)
│
├── scripts/                 # Scripts d'automatisation prêts à l'emploi
│   ├── feudala_loop.py      # Boucle de récolte sur les Féodalas
│   ├── feudala_full.py      # Route complète Féodalas (multi-maps)
│   ├── mine_brakmar.py      # Mine de Brakmar
│   └── combat_farm.py       # Farm de monstres
│
├── tools/                   # Utilitaires de diagnostic
│   ├── sniff_server.py      # Détection automatique de l'IP serveur (psutil + scapy)
│   ├── log_anticheat.py     # Analyse des paquets anti-cheat
│   └── diagnose_connections.py
│
├── ressources/
│   ├── maps/                # Cartes XML Dofus Rétro (données de cellules et de blocage)
│   └── gfx/                 # Sprites items et éléments
│
└── tests/
    └── test_encoding.py     # Tests unitaires du module d'encodage
```

---

## Écrire un script

Les scripts dans `scripts/` sont chargés dynamiquement et peuvent être lancés depuis l'onglet **Scripts** du dashboard.

```python
# scripts/mon_script.py
from bot.harvester import start_bot
from bot import harvester

async def run():
    # Route de changement de map (direction : "right", "left", "top", "bottom")
    harvester.MAP_ROUTE = ["right", "right", "bottom", "left"]

    # Restreindre aux métiers souhaités (None = tous)
    # IDs métiers : 2=Bûcheron, 26=Mineur, 28=Paysan, 24=Alchimiste...
    start_bot(allowed_jobs={2, 26})
```

---

## Tests

```bash
pytest tests/ -v
```

Les tests couvrent l'intégralité de `protocol/encoding` : Base64 Dofus, hash mot de passe, décodage AYK (IP + port), chiffrement/déchiffrement réseau.

---

## Notes techniques

### Re-chiffrement C→S transparent

Flash et le bot partagent le même compteur de clés `_current_key` (dans `bot/channel.py`).  
Pour chaque message chiffré envoyé par Flash, le relay le **déchiffre** avec la clé Flash, puis le **re-chiffre** avec la prochaine clé du compteur centralisé avant de l'envoyer au serveur.  
Cela permet au bot d'injecter ses propres messages sans désynchroniser la séquence côté serveur.

### Protocole Dofus Rétro 1.29

- Messages délimités par `\x00` (XMLSocket Flash)
- Messages C→S chiffrés : `GA*`, `GK*`, `GM*`, et quelques autres (`W`, `e`, `O`…)
- Format d'un message chiffré : `-{key_idx}{checksum}{hex_data}`
- Deux alphabets Base64 : `DOFUS_CHARSET` (cellules, chemins) et `ZIPKEY` (hash mdp, port AYK)
- Référence implémentation : [retroproto/crypto.go](https://github.com/kralamoure/retroproto)
