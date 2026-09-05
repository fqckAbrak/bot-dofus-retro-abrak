# Ligne de vue (LdV) — algorithme réel du serveur/client Dofus Retro

Extrait du client Abrak (`modules/core.swf` + `loader.swf`) via JPEXS/FFDec.

## Découverte clé : la LdV est 3D (par hauteurs)

Le client ne fait **pas** un simple tracé 2D. `checkView(from, to)` (voir
[checkView.as](checkView.as)) trace une **ligne de visée en hauteur** :

- Chaque cellule a une hauteur `z = getCellHeight(cell) + 1.5*(sprite présent) + 1.5*(sprite porté)`.
- La ligne de visée va de `z_from` à `z_to`, interpolée linéairement le long du trajet.
- Une cellule **bloque** si sa hauteur **dépasse** la hauteur de la ligne à ce point,
  en tenant compte de `lineOfSight` (opaque), `active`, et des sprites (effet 150 = ne bloque pas).

## Pourquoi notre bot se faisait rejeter (Im 1174)

Notre `has_line_of_sight` était **purement 2D** et ignorait la hauteur. Or :

- Le bit `lineOfSight` des maps Abrak est quasi vide (toutes les cellules « transparentes »).
- La hauteur de sol `ground_level` (= `cd[1] & 15`) est **uniforme (7)** sur les maps testées.
- Les blocages viennent donc des **décorations** (`layerObject1/2` : arbres, rochers…) :
  `getCellHeight` mappe certains graphismes de décoration à une hauteur qui dépasse la ligne.

`getCellHeight` est défini dans une classe **fortement obfusquée** de `loader.swf`
(`__Packages/ank/battlefield/%0B%05`) — la table « graphisme de décoration → hauteur »
n'a pas encore été extraite.

## Prochaine étape : porter l'algo exact

1. **Capture ground-truth** (déjà en place) : `bot/combat.py:_capture_los` écrit
   `logs/los_capture.jsonl` à chaque tir single-target à LdV, avec `blocked` (1=refusé, 0=accepté),
   les positions de toutes les entités, et les cellules. Croisé avec les décorations des maps
   (`ressources/maps/{id}.xml`), ça permet d'identifier quelles décorations donnent de la hauteur.
2. Reconstituer la table décoration→hauteur (ou déobfusquer `getCellHeight`).
3. Porter `checkView`/`checkCellView` en Python (attention : la source décompilée réutilise des
   variables — se référer au P-code pour lever les ambiguïtés).

Fichiers de référence : [checkView.as](checkView.as) (algo LdV), [cell_class.as](cell_class.as)
(champs de cellule : `lineOfSight`, `movement`, `groundLevel`, `nPermanentLevel`, `groundSlope`).
