# Fichiers du jeu / client (Dofus Rétro 1.29 — Abrak)

> Synthèse pour comprendre **comment le client est construit** et **comment le
> modifier proprement** (décompilation SWF, patch P-code). Destinée à un futur
> intervenant (humain ou LLM) qui ne connaît pas l'arborescence du jeu.
>
> Le bot lui-même n'a **pas besoin** de modifier le client pour fonctionner (il
> est purement MITM réseau, cf. [ARCHITECTURE.md](ARCHITECTURE.md)). Ce document
> couvre uniquement les modifications **côté client** (ex. no-anim, cf.
> [COMBAT.md](COMBAT.md#no-anim--déplacements-instantanés-patch-client-coreswf)).

## 1. Ce qu'est le client en une phrase

Le client Dofus Rétro « Abrak » est une **app Electron** (Chromium + Node) qui
embarque le plugin **PepperFlash**, lequel exécute le **moteur de jeu Dofus 1.29
écrit en ActionScript 2 (AS2)** contenu dans `core.swf`. Autrement dit :

```
Abrak.exe (Electron)
  └── PepperFlash (runtime Flash intégré, pepperflash/)
        └── core.swf  ← TOUT le jeu Dofus 1.29 (AS2) vit ici
```

`core.swf` est l'équivalent Rétro du `DofusInvoker.swf` des clients récents : il
contient le réseau, le datacenter, l'UI, le combat, l'animation des sprites…

## 2. Où est installé le client

Chemin d'install (configurable, cf. `game_path` dans `config.json` — voir aussi
`bot/noanim.py:find_modules_dir` qui sait le retrouver) :

```
E:\games\Abrak\                       # racine choisie à l'install
└── Abrak\Retro\                      # build "retro" / release "main"
    ├── Abrak.exe                     # exécutable Electron (le launcher/jeu)
    ├── main\                         # ressources Electron
    ├── locales\ swiftshader\         # Chromium (i18n, rendu logiciel GL)
    ├── *.dll  *.pak  *.bin  icudtl.dat   # binaires Chromium/Electron
    ├── .release.hashes.json          # manifeste SHA1 de TOUS les fichiers (cf. §6)
    ├── .release.infos.json           # {"gameUid":"retro","release":"main"}
    ├── zaap.yml  manifest.json       # méta launcher (Ankama Launcher / Zaap)
    └── resources\app\                # ← l'application Electron proprement dite
        ├── main.js  main.jsc  package.json   # entrée Electron (Node)
        ├── preloader.js  mms.cfg              # bootstrap + config Flash
        ├── flashsettingslocalhost.sol        # réglages Flash globaux
        ├── pepperflash\                       # le runtime Flash embarqué
        ├── node_modules\                      # dont node-forge/.../SocketPool.swf
        └── retroclient\              # ← LE CLIENT DE JEU
            ├── modules\
            │   ├── core.swf          # ★ moteur de jeu AS2 (~6.7 Mo)
            │   ├── soma.swf          # petit module annexe (~4 Ko)
            │   ├── core.swf.orig.bak # (créé par nous) sauvegarde vierge — cf. no-anim
            │   └── core.noanim.swf   # (créé par nous) variante patchée — cf. no-anim
            ├── loader.swf  preloader.swf      # chargeurs Flash
            ├── data\                 # docs, maps, tutorials (données de jeu)
            ├── clips\                # SWF d'assets : items, spells, gfx, maps, emotes…
            ├── audio\ fonts\ svg\ ui\ css\ styles\ js\
            ├── config.xml            # config du client
            └── *.html                # D1Chat / D1Console / D1ElectronLauncher
```

Points d'attention :

- **`modules/core.swf` est la cible** de toute modification du moteur. C'est le
  seul fichier qui contient la logique de jeu décompilable.
- `clips\` contient des **centaines de petits SWF d'assets** (graphismes,
  animations de sorts, maps). Ce ne sont **pas** du code — inutile pour modder la
  logique.
- `data\maps\` contient les données de cartes (cf. [RESOURCES.md](RESOURCES.md)
  pour les données exploitées côté bot).

## 3. Anatomie de `core.swf`

- **Format** : SWF **non compressé**, ActionScript **2** (AS1/2, pas AS3/ABC).
  ~**8095 scripts** AS2 au total (timeline + classes + `DoInitAction` de sprites).
- **Obfusqué** : les noms de packages/classes sont brouillés avec des chaînes de
  caractères de contrôle. Exemples réels rencontrés :
  - `ank.battlefield.mc["\x1e\x0e\x12"]` — la **classe sprite du champ de
    bataille** (rendu + déplacement des entités).
  - `dofus["\r\x13"].gapi.ui.*` — sous-systèmes UI.
  - Les noms lisibles (`initialize`, `setAnim`, `moveToCell`, `basicMove`,
    `WALK_SPEEDS`, `isRunning`…) restent en clair car ce sont des membres
    standards / non renommés.
- **Convention `__Packages`** : comme tout AS2 compilé, les classes sont rangées
  sous un dossier virtuel `__Packages\…` et instanciées via `#initclip` dans des
  `DefineSprite`. JPEXS recrée cette arborescence à l'export.

### Classes / fonctions notables (repères pour modder)

| Élément | Rôle | Utilisé pour |
|---|---|---|
| `dofus/aks/Aks.as` | Chiffrement réseau (`cypherData`, `decypherData`, `prepareKey`, `prepareSendPacket`) | Comprendre le protocole, cf. [PROTOCOL.md](PROTOCOL.md) |
| `…datacenter…Game` | État global de jeu. Champs clés : `isFight` (vrai **dès l'agression**), `isRunning` (vrai **uniquement en combat actif**, du *GameStartToPlay* à la fin) | Détecter le combat côté client |
| `ank.battlefield.mc["\x1e\x0e\x12"]` | Sprite d'entité sur le champ de bataille. Méthodes : `moveToCell` (met à jour cellule / `isInMove` / carte **puis** lance l'anim), `setAnim` (attache l'anim de marche via `onEnterFrame`), **`basicMove`** (avance le sprite frame par frame), `basicMoveEnd` | Patch **no-anim déplacement** (cf. §7 et COMBAT.md) |
| `ank.battlefield["\x1e\x0e\x0f"]` | « spriteHandler » : `launchVisualEffect` orchestre l'anim d'une action (déplacement vers la cible, `setAnim` du lanceur, effet visuel) via le `sequencer`. La branche *string* joue `setAnim(anim)` sur l'entité qui agit (p.ex. `anim4` = saut des créatures) | Patch **no-anim lanceur** : skip du `setAnim` si `isRunning` |
| `…OptionsManager` | Options client (`SkipFightAnimations`, `SkipFightPlayerAnimations`, `DisableDeathAnimation`…). ⚠️ **Aucune** option ne masque l'anim des **créatures** | Comprendre ce que les `.sol` couvrent (et leurs limites) |
| `api` (membre d'instance) | Alias de `_global.API` posé par le constructeur — donne accès à `api.datacenter.Game.isRunning` depuis une instance sprite | Lire l'état combat dans `basicMove` |

> ⚠️ **`isFight` vs `isRunning`** : piège classique. `isFight` passe à vrai **dès
> la tentative d'agression** (avant le placement/combat). Pour « combat
> seulement », utiliser **`isRunning`**.

## 4. Le runtime : Electron + PepperFlash

- `Abrak.exe` = Electron. `resources\app\main.js` est le point d'entrée Node ; il
  charge une page HTML qui embarque l'objet Flash via `pepperflash\`.
- Conséquence pratique : le SWF est chargé **une seule fois au démarrage** du
  client. **Toute modification de `core.swf` ne prend effet qu'au prochain
  lancement** d'Abrak (pas de hot-reload).
- Electron permet à Ankama d'ajouter des vérifications d'intégrité côté Node
  (d'où l'intérêt historique du MITM réseau plutôt que de patcher le client).

## 5. Données persistées côté Flash — SharedObjects (`.sol`)

Les options client (modes de combat, raccourcis…) sont stockées dans des
**SharedObjects** Flash (`.sol`), sous le profil utilisateur :

```
%APPDATA%\Abrak Retro\...\WritableRoot\#SharedObjects\<rand>\localhost\...\loader.swf\ANKOPTIONSSO.sol
```

- `tools/optimize_fight_options.py` édite `ANKOPTIONSSO.sol` pour forcer des
  options d'accélération de combat (mode tactique, `CreaturesMode=0`, etc.).
- ⚠️ Ces options accélèrent les anims de **sorts/coups/mort** mais **pas le
  déplacement** des entités — d'où le besoin du patch no-anim sur `basicMove`.

## 6. Intégrité : `.release.hashes.json`

- Le manifeste liste un **SHA1 attendu** pour chaque fichier (dont
  `resources/app/retroclient/modules/core.swf`).
- **Constat important** : sur l'install analysée, le `core.swf` **vierge** ne
  correspond **déjà pas** au hash du manifeste, et le jeu tourne sans souci. Le
  manifeste sert donc à la **vérification de téléchargement par le launcher**, et
  n'est **pas** appliqué comme anti-tamper bloquant au runtime du jeu.
- ⇒ Modifier `core.swf` **ne déclenche pas** de blocage d'intégrité observé.
  (Le risque restant est un **ban serveur** si la triche réseau est détectée —
  cf. avertissement no-anim.)

## 7. Modifier le moteur : outillage et méthode

### Outil — JPEXS Free Flash Decompiler (FFDec)

- CLI : `E:\tools\jpexs\ffdec-cli.jar` (testé avec **Java 8** — `java -jar … --help`).
- Invocation type : `java -jar E:\tools\jpexs\ffdec-cli.jar <commande> …`.

Commandes utiles :

```bash
# Décompiler en AS2 (lecture / compréhension)
java -jar ffdec-cli.jar -export script  <outdir> core.swf

# Exporter le P-CODE (pour patch chirurgical — voir plus bas)
java -jar ffdec-cli.jar -format script:pcode -export script <outdir> core.swf

# Lister les scripts AS2 (retrouver un nom de classe)
java -jar ffdec-cli.jar -dumpAS2 core.swf

# Réassembler un dossier de scripts (.as OU .pcode) dans un nouveau SWF
java -jar ffdec-cli.jar -importScript <in.swf> <out.swf> <scriptsfolder>
```

### Règle d'or : **patcher en P-code, pas en recompilant le source**

C'est la leçon clé de l'implémentation no-anim :

- ❌ **Recompiler depuis le source décompilé (`.as`) n'est PAS fidèle.** Sur la
  classe battlefield, JPEXS perdait des déclarations `var` et **restructurait les
  closures `onEnterFrame` de `setAnim`** → l'animation de marche se cassait → le
  client **annulait les déplacements** (`GKE`) et **crashait**, y compris hors
  combat. Vérifié par diff : la recompilation modifiait plusieurs méthodes.
- ✅ **Éditer le P-code et réassembler est fidèle 1:1.** Le P-code (désassemblage)
  se réassemble en bytecode identique. En insérant seulement quelques
  instructions dans **une** fonction, **tout le reste du bytecode reste identique
  à l'octet près** (vérifié : seules les ~9 instructions ajoutées diffèrent).

### Recette du patch chirurgical (généralisable)

1. Exporter le P-code : `-format script:pcode -export script <dir> source.swf`.
   Le fichier de la classe est p.ex.
   `…\scripts\ank\battlefield\mc[%22%5Cx1e%5Cx0e%5Cx12%22].pcode`.
2. Localiser la fonction cible dans le `.pcode` : repérer la ligne
   `Push "<nom>"` suivie de `DefineFunction2 … {`. Les **registres** des
   paramètres sont indiqués sur la ligne `DefineFunction2` (`register1` = `this`).
3. Insérer ses instructions **en tête du corps** de la fonction, en sautant
   l'original via un label (`If monLabel`) quand on veut conserver le
   comportement d'origine dans certains cas. Réutiliser les **constantes déjà
   présentes** dans le `ConstantPool` (pas besoin d'en ajouter).
4. Réassembler : copier **uniquement** le `.pcode` patché dans un dossier à
   l'arborescence identique, puis `-importScript source.swf out.swf <dossier>`.
   `-importScript` ne remplace que les scripts présents dans le dossier.
5. Vérifier : re-décompiler `out.swf` et **diff** la classe contre l'original —
   seules les lignes voulues doivent différer.

> Implémentation concrète et automatisée : `tools/build_noanim_swf.py`
> (régénère `core.noanim.swf` après chaque MAJ du client). Bascule on/off :
> `bot/noanim.py` (+ onglet **Paramètres → No-anim**), qui échange
> `core.noanim.swf` ↔ `core.swf.orig.bak` par-dessus `core.swf`.

## 8. Pièges pratiques (Windows / PowerShell / fichiers verrouillés)

- **Crochets dans les chemins** : les noms exportés contiennent `[` `]`
  (`mc[%22%5Cx1e%5Cx0e%5Cx12%22].pcode`). PowerShell les traite comme des
  **wildcards** → toujours utiliser **`-LiteralPath`** avec
  `Get-Content`/`Set-Content`/`Copy-Item`, sinon « fichier introuvable ».
- **SWF verrouillé** : si un client Abrak est ouvert, `core.swf` peut être
  verrouillé en écriture → fermer Abrak avant de basculer/écrire.
- **Effet différé** : après modification, **relancer Abrak** (le SWF n'est lu
  qu'au démarrage).
- **Python sous PowerShell** : utiliser `py -3` (le `python` stub peut
  interférer) et `sys.stdout.reconfigure(encoding="utf-8")` pour les caractères
  non-cp1252 dans les logs.
- **Toujours travailler depuis une sauvegarde vierge** (`core.swf.orig.bak`),
  jamais en repatchant un SWF déjà patché.

## 9. Pour aller plus vite (résumé exécutif)

- Le jeu = `…\retroclient\modules\core.swf` (AS2, obfusqué, ~8095 scripts).
- Combat actif = `api.datacenter.Game.isRunning` (pas `isFight`).
- Animation de déplacement = `basicMove` dans `ank.battlefield.mc[...]`.
- Modifier = **patch P-code via JPEXS**, jamais recompilation source.
- Effet = **au prochain lancement** du client ; pas de blocage d'intégrité connu,
  mais **risque de ban** côté serveur.

## 10. Patch — dump d'inventaire à la demande (`#ZI` → `ZO`)

> Implémentation : `tools/build_invdump_swf.py` (+ `tools/inventory_dump_patch.as`
> pour la référence AS2). Côté bot : `bot/inventory.py`, handler `ZO` dans
> `game/state.py`, injection/drop dans `proxy/relay.py` + `bot/channel.py`.

### Problème résolu

Sur ce serveur, **l'inventaire complet (`OT`) n'est JAMAIS envoyé** au client (ni
au login, ni à l'ouverture du sac — vérifié sur de nombreux logs). Le client Flash
le tient en mémoire dans `api.datacenter.Player.Inventory`. Le proxy MITM ne peut
donc pas le connaître passivement : sans patch, le bot ne « voit » que ce qui
**bouge** (loot `OAK`, retrait banque `EL`).

### Mécanisme

1. Le patch enregistre un parser pour un message custom **`ZI`** :
   ```actionscript
   _global.addAdditionalPacketParser("ZI", function(sData) {
      var inv = _global.API.datacenter.Player.Inventory.clone();   // tout l'inventaire
      var s = "";
      for (var i = 0; i < inv.length; i++) {
         var it = inv[i];
         s += it._nID + "~" + it._nUnicID + "~" + it._nQuantity + "~" + it._nPosition + ";";
      }
      _global.API.network.send("ZO" + s, false, undefined, true);   // dump en clair
   });
   ```
2. Le **bot injecte `#ZI` vers le client** (S→C, via `channel.inject_to_client` →
   `session.channel.client_writer`).
3. Le client patché répond **`ZO{uid~gid~qty~pos;...}`** (C→S, **en clair**).
4. Le **relais capte `ZO`, le parse** (`_on_full_inventory` reconstruit
   `current._inventory`) **et le DROP** : il ne le transmet pas au serveur (qui ne
   connaît pas ce message). En clair ⇒ aucun compteur de clé avancé ⇒ pas de désync.

### Deux pièges (durement appris)

- **Préfixe `#` obligatoire.** Le dispatch du client n'appelle
  `processAdditionalPacket` (donc les `addAdditionalPacketParser`) **que** si le
  message reçu commence par `#` (`if (sData.charAt(0) == "#") ...`). Il faut donc
  injecter **`#ZI`**, pas `ZI`. Le `#` est retiré avant le matching du parser.
- **Position en sac = `-1` côté client.** L'item (`dofus.datacenter["\f\x0b"]`)
  stocke `_nPosition = -1` pour un objet **non équipé** (`isEquiped` ⇔ `position > -1`),
  alors que le serveur utilise `63` (champ vide `OAK`/`EL`). Le filtrage « en sac »
  doit donc accepter `pos ∈ {-1, 63}` (cf. `get_inventory_bag`, `preview_sellable`).
- Champs de l'item : `_nID` = uid, **`_nUnicID` = GID** (modèle, pour type/niveau),
  `_nQuantity`, `_nPosition`. Le client définit aussi
  `ENHANCEABLE_SUPER_TYPE = [1,2,3,4,5,10,11]` (= familles d'équipement).
- `send(msg, false, undefined, true)` : `false` = pas de spinner d'attente,
  `true` (5ᵉ arg) = **ne pas tronquer** (l'inventaire peut être long). Préfixe `Z`
  **non chiffré** ⇒ le relais lit le `ZO` en clair et le drop sans désync.

### Build — injection dans un **frame plain**, pas une classe obfusquée

`-importScript` recompile le source. Recompiler une **classe obfusquée**
(le dispatcher `dofus.aks["\x11\x0b"]`) **échoue** (le source décompilé ne
re-parse pas). La parade : injecter la registration en fin de
**`scripts/frame_1/DoAction.as`** — un script de timeline en clair (assignations
`_global.ascalion…`) qui se recompile sans souci — et **n'importer que ce script**
(dossier isolé), pour ne toucher à aucune classe. Le bloc injecté est défensif
(`setInterval` qui réessaie jusqu'à ce que `_global.API` et
`addAdditionalPacketParser` soient prêts, puis se désarme).

### ⚠️ Interaction avec le no-anim

La bascule no-anim (`bot/noanim.py`) **écrase `core.swf`** en copiant
`core.noanim.swf` (ON) ou `core.swf.orig.bak` (OFF). Pour que l'invdump survive
aux deux états, on l'injecte dans **les deux** variantes :

Le **déverrouillage du double-clic carte du monde** (autopilote in-game, patch
P-code de `MapExplorer`) est désormais **intégré à `build_noanim_swf.py`** (3ᵉ
patcher de `_PATCHERS`) : il est donc bundlé avec le no-anim. Pour la variante
no-anim **OFF** (`core.swf.orig.bak`), on l'ajoute via `build_autopilot_swf.py`.

| Fichier | Contenu | Rôle |
|---|---|---|
| `core.swf` | no-anim **+** worldmap **+** invdump | actif |
| `core.noanim.swf` | no-anim **+** worldmap **+** invdump | no-anim ON |
| `core.swf.orig.bak` | vanilla **+** worldmap **+** invdump | no-anim OFF |
| `core.swf.vanilla.bak` | vrai vanilla | sauvegarde pristine |

Régénérer le combo après une MAJ du client :
```bash
py -3 tools/build_noanim_swf.py                                                 # core.noanim.swf = no-anim + worldmap
py -3 tools/build_invdump_swf.py   --source core.noanim.swf      --out core.combo.swf    # + invdump
py -3 tools/build_invdump_swf.py   --source core.swf.vanilla.bak --out core.vaninv0.swf  # vanilla + invdump
py -3 tools/build_autopilot_swf.py --source core.vaninv0.swf     --out core.vaninv.swf   # + worldmap (no-anim OFF)
#   puis : core.combo.swf → core.swf & core.noanim.swf ; core.vaninv.swf → core.swf.orig.bak
```
