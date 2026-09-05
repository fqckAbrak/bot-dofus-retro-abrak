# Documentation du bot Dofus Rétro 1.29

## Index

| Document | Description |
|----------|-------------|
| [ARCHITECTURE.md](ARCHITECTURE.md) | Structure des modules, flux de données, diagrammes |
| [GAME_CLIENT.md](GAME_CLIENT.md) | Fichiers du jeu/client (Abrak/Electron/Flash), `core.swf`, décompilation & patch P-code |
| [PROTOCOL.md](PROTOCOL.md) | Protocole réseau Dofus 1.29 : packets, chiffrement, encodages |
| [COMBAT.md](COMBAT.md) | Système de combat : IA, grille, distance PO, LdV, mouvement |
| [MERCHANT.md](MERCHANT.md) | Vente au marchand (PNJ) + détection de l'inventaire complet (patch client) |
| [HEROES.md](HEROES.md) | Mode héros : gestion multi-personnages, tours, sorts |
| [RESOURCES.md](RESOURCES.md) | Données de jeu : spells.xml, maps XML, fichiers de référence |
| [CONVENTIONS.md](CONVENTIONS.md) | Principes de design, règles de nommage, bonnes pratiques |

## Démarrage rapide

```bash
python main.py            # demande les droits admin (UAC) au lancement
python main.py --no-admin # rester non-admin (interception frida)
```

1. Au lancement, le bot **demande les droits administrateur** (une seule fois). En admin, l'interception passe par **WinDivert** (kernel) — fiable pour plusieurs clients en même temps ; sinon repli sur **frida**. L'instance élevée s'ouvre dans une nouvelle console.
2. Le proxy MITM démarre sur `127.0.0.1:8080` (configurable dans `config.json`).
3. Lancer **un ou plusieurs** clients Abrak — chaque connexion est interceptée automatiquement.
4. Se connecter en jeu : pour **chaque** client, un onglet « team » apparaît en haut du dashboard (pastille d'état + nom du perso principal). Voir [ARCHITECTURE.md](ARCHITECTURE.md#multi-instance-sessions).
5. Configurer la séquence de sorts dans l'onglet Combat → Sorts : **un sous-onglet par classe** de l'équipe (réglages **sauvegardés par compte et par classe**, cf. `bot_settings.json` ci-dessous).
6. Lancer le farming combat / récolte / scripts — indépendamment par team.

## Configuration

Le fichier `config.json` à la racine du projet (éditable depuis l'onglet **Paramètres**) :

```json
{
    "server_host": "51.89.153.20",
    "server_port": 1303,
    "proxy_host": "127.0.0.1",
    "proxy_port": 8080,
    "game_ports": [1303, 1304],
    "game_path": "E:\\games\\Abrak Launcher",
    "notifications_enabled": true
}
```

| Clé | Description |
|-----|-------------|
| `server_host` | IP du serveur Dofus (Abrak par défaut) |
| `server_port` | Port du serveur |
| `proxy_host` | IP d'écoute du proxy local |
| `proxy_port` | Port d'écoute du proxy local |
| `game_ports` | Ports game server à intercepter (WinDivert / Frida) |
| `game_path` | Dossier d'installation Dofus Rétro (Abrak Launcher) |
| `notifications_enabled` | Activer les toasts (combat, banque, kicks, surpoids) |

Les modifications via l'onglet **Paramètres** nécessitent un redémarrage du bot
pour prendre effet (bouton 🔄 *Redémarrer le bot* dans l'onglet).

## Réglages combat par compte — `bot_settings.json`

Les réglages de combat (séquences de sorts, comportement, délais…) sont stockés
**par personnage principal** (compte). Les séquences de sorts sont rangées **par classe**
sous `attack_spells_by_class` (clé = `class_id`), pour gérer les équipes mixtes :

> `bot_settings.json` contient les pseudos de tes personnages : il est **non versionné**.
> Copier `bot_settings.example.json` pour partir d'une base, ou laisser le bot le créer
> tout seul (l'onglet **Combat** l'écrit au premier réglage modifié).

```json
{
    "defaults": {
        "attack_spells_by_class": {
            "9": [ { "spell_id": 161, "count": 1, "target": "enemy" } ],
            "3": [ { "spell_id": 41,  "count": 1, "target": "enemy" } ]
        },
        "combat_behavior": "distance"
    },
    "accounts": {
        "Perso-A":  { "combat_behavior": "rush_cac" },
        "Perso-B":  { "attack_spells_by_class": { "9": [ { "spell_id": 183, "count": 1, "target": "enemy" } ] } }
    }
}
```

- La config effective d'un compte = `defaults` **surchargés** par `accounts["<perso>"]`. Pour
  `attack_spells_by_class`, la fusion est faite **par classe** (un compte ne masque que les
  classes qu'il redéfinit).
- `class_id` suit la table des classes (`9` = Crâ, `3` = Enutrof, etc.).
- Chaque classe a son **sous-onglet** dans l'onglet **Combat → Sorts** ; tous les persos d'une
  même classe jouent la même séquence.
- Un nouveau compte hérite des `defaults` tant qu'il n'a rien personnalisé ; dès qu'on modifie
  un réglage dans son onglet Combat, il est écrit dans `accounts["<perso>"]`.
- L'**ancien format plat** (clés au premier niveau) est migré automatiquement vers `defaults`
  à la première lecture. L'ancienne clé `attack_spells` (séquence unique, mono-classe) n'est
  plus utilisée : reconfigure les sorts dans le sous-onglet de chaque classe.
- Édité automatiquement par l'onglet **Combat** ; pas besoin de l'éditer à la main.
