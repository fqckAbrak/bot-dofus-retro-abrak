# Vente au marchand & inventaire complet

Fonctionnalité **Misc → Vendre au marchand** : vend automatiquement les
équipements en sac à un PNJ marchand ambulant, avec filtrage par famille, niveau
et blacklist. S'appuie sur la **détection de l'inventaire complet** (patch client),
qui sert aussi à l'onglet **Inventaire**.

## Vue d'ensemble

| Brique | Fichier |
|---|---|
| Logique de vente | `bot/merchant.py` |
| Réglages (niveau max, blacklist, familles, gfx) | `bot/merchant_config.py` → `merchant_config.json` |
| Base d'items (gid→type/niveau/nom) | `data/items_db.py` + `ressources/items.json` |
| Dump d'inventaire complet | `bot/inventory.py` (+ patch `core.swf`) |
| UI | `dashboard/tabs/misc.py`, `misc_vendre.py`, `inventaire.py` |

## 1. Base d'items (`ressources/items.json`)

Le bot a besoin de connaître **type** et **niveau** de chaque objet (l'inventaire
ne transporte qu'un `gid`). La base est extraite du **CDN du serveur** :

```bash
py -3 tools/extract_items.py          # télécharge items_fr_{ver}.swf, décompile, parse
```

Produit `{version, types, items}` : `items[gid] = {l:niveau, t:typeId, n:nom}` et
`types[typeId] = {name, category}`. Les **familles d'équipement** (catégorie
`I.t[].t`) : `1` amulette, `2` armes, `3` anneau, `4` ceinture, `5` bottes,
`7` bouclier, `10` coiffe, `11` cape/sac, `13` Dofus/trophée.

## 2. Détection de l'inventaire complet

⚠️ Sur ce serveur, **l'inventaire complet (`OT`) n'est jamais envoyé** : le client
le garde en mémoire. Sans patch, le bot ne voit que le loot (`OAK`) et la banque
(`EL`). Le patch `core.swf` ajoute un dump à la demande :

```
bot ──#ZI──► client patché ──ZO{uid~gid~qty~pos;…}──► relais (parse + DROP)
```

- Détail technique : [GAME_CLIENT.md §10](GAME_CLIENT.md#10-patch--dump-dinventaire-à-la-demande-zi--zo).
- Build : `tools/build_invdump_swf.py` (combiné no-anim : voir §10).
- Sans le patch, le bouton « ↻ Inventaire » timeout silencieusement et on retombe
  sur l'inventaire incrémental.

L'inventaire est **rafraîchi automatiquement** à l'ouverture des onglets
**Inventaire** et **Vendre** (throttle 4 s) et **avant chaque vente**.

## 3. Onglet Inventaire

4 sous-onglets — **Équipements / Consommables / Ressources / Autres** — alimentés
par `items_db.classify(gid)`. Objets regroupés par gid (quantités sommées), triés
par type puis nom. Jauge de poids en haut.

## 4. Vente au marchand

1. **Détection du PNJ** : `find_merchant_npc(gfx)` cherche une entité de **sprite
   type `-4`** (PNJ) dont l'apparence (gfx) correspond. Défaut `merchant_gfx = ["2150"]`
   (marchand ambulant acheteur). Vide ⇒ premier PNJ trouvé. Bouton **Détecter PNJ**
   pour lister les PNJ de la map.
2. **Filtrage** (`preview_sellable`) : objets **en sac** (`pos ∈ {-1, 63}`),
   famille d'équipement cochée, niveau ≤ max, hors blacklist (par nom).
3. **Vente** : `ER2|{npcId}` → `EMO+{uid}|{qty}…` (plaintext) → attendre `Em KG`
   (le prix apparaît) → `EK` (valider) → `EV` (fermé). Kamas lus depuis `Em KG`.

### Réglages (`merchant_config.json`)

```json
{
  "max_level": 120,
  "blacklist_names": [],
  "blacklist_gids": [],
  "categories": [1, 2, 3, 4, 5, 7, 10, 11],
  "merchant_gfx": ["2150"]
}
```

## Workflow type

1. Lancer le bot (client patché actif), se connecter en jeu.
2. Onglet **Inventaire** : les 4 familles se remplissent automatiquement.
3. Onglet **Misc → Vendre au marchand** sur la map du marchand → **Aperçu** (liste
   exacte) → **Vendre**.

> Sans le patch d'inventaire, ne sont vendables que les objets **vus arriver** par
> le bot : loot de combat (`OAK`) ou retrait de banque. Le patch lève cette limite.
