// ============================================================================
//  PATCH core.swf — DUMP D'INVENTAIRE À LA DEMANDE  (ActionScript 2)
// ============================================================================
//
//  PROBLÈME
//  --------
//  Sur ce serveur, l'inventaire complet (message OT) n'est JAMAIS envoyé au
//  client : le client Flash le tient en mémoire (api.datacenter.Player.Inventory).
//  Le proxy MITM ne peut donc pas le connaître passivement.
//
//  SOLUTION
//  --------
//  On enregistre un parser pour un message custom "ZI" (injecté par le BOT vers
//  le client, S→C). À sa réception, le client parcourt Player.Inventory et
//  renvoie un message "ZO{uid~gid~qty~pos;...}" (C→S, EN CLAIR car préfixe "Z"
//  non chiffré). Le relais capte ce "ZO", le parse, et le DROP (ne le transmet
//  pas au serveur qui ne le connaît pas). En clair ⇒ aucun compteur de clé
//  avancé ⇒ pas de désync.
//
//  Champs de l'item d'inventaire (classe dofus.datacenter["\f\x0b"]) :
//      _nID      = identifiant unique de l'instance (uid)
//      _nUnicID  = GID (modèle/template d'objet)        ← pour le type/niveau
//      _nQuantity, _nPosition
//  ``send(msg, false, undefined, true)`` : false = pas de spinner d'attente,
//  true (5e arg) = ne pas tronquer le message (inventaire long).
//
//  POSE DU PATCH (JPEXS / FFDec)
//  ----------------------------
//  Ce bloc doit s'exécuter UNE FOIS au démarrage, après init de _global.API.
//  Le setInterval ci-dessous est défensif : il réessaie jusqu'à ce que l'API et
//  addAdditionalPacketParser soient prêts, puis se désarme.
//  L'ajouter en P-code dans le script qui contient déjà les autres
//  ``addAdditionalPacketParser(...)`` (classe dofus.aks["\x11\x0b"]), OU comme
//  nouveau DoAction de frame. NE PAS recompiler les classes obfusquées fragiles.
// ============================================================================

_global.__invDumpInit = function()
{
   if (_global.addAdditionalPacketParser == undefined
       || _global.API == undefined
       || _global.API.network == undefined
       || _global.API.datacenter == undefined
       || _global.API.datacenter.Player == undefined
       || _global.API.datacenter.Player.Inventory == undefined)
   {
      return; // pas encore prêt — on réessaiera
   }
   if (_global.__invDumpDone) { return; }
   _global.__invDumpDone = true;

   _global.addAdditionalPacketParser("ZI", function(sData)
   {
      var inv = _global.API.datacenter.Player.Inventory.clone();
      var s = "";
      var i = 0;
      while (i < inv.length)
      {
         var it = inv[i];
         s += it._nID + "~" + it._nUnicID + "~" + it._nQuantity + "~" + it._nPosition + ";";
         i = i + 1;
      }
      _global.API.network.send("ZO" + s, false, undefined, true);
   });
};

_global.__invDumpTimer = setInterval(_global.__invDumpInit, 1000);
