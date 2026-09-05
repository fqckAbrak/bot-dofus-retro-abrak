"""
Table des IDs de messages du protocole Dofus Rétro 1.29.

Extraite de retroproto (github.com/kralamoure/retroproto) — référence exhaustive en Go.
Organisée en deux dicts : SERVER_MESSAGES (S→C) et CLIENT_MESSAGES (C→S).
"""

# ---------------------------------------------------------------------------
# Messages Serveur → Client
# ---------------------------------------------------------------------------
SERVER_MESSAGES: dict[str, str] = {
    # Handshake
    "HC":   "AksHelloConnect",
    "HG":   "AksHelloGame",
    "p":    "AksPong",
    "q":    "AksQuickPong",
    "rping": "AksRPing",
    "M":    "AksServerMessage",
    "k":    "AksServerWillDisconnect",

    # Basics
    "BN":   "BasicsNothing",
    "BAE":  "BasicsAuthorizedCommandError",
    "BAT":  "BasicsAuthorizedCommandSuccess",
    "BAL":  "BasicsAuthorizedLine",
    "BAP":  "BasicsAuthorizedCommandPrompt",
    "BAC":  "BasicsAuthorizedCommandClear",
    "BAIO": "BasicsAuthorizedInterfaceOpen",
    "BAIC": "BasicsAuthorizedInterfaceClose",
    "BT":   "BasicsTime",
    "BD":   "BasicsDate",
    "BWE":  "BasicsWhoIsError",
    "BWK":  "BasicsWhoIsSuccess",
    "BP+":  "BasicsSubscriberRestrictionAdd",
    "BP-":  "BasicsSubscriberRestrictionRemove",
    "BC":   "BasicsFileCheck",
    "Bp":   "BasicsAveragePing",

    # Account
    "Ac":   "AccountCommunity",
    "Ad":   "AccountPseudo",
    "AlK":  "AccountLoginSuccess",
    "AlE":  "AccountLoginError",
    "ALE":  "AccountCharactersListError",
    "ALK":  "AccountCharactersListSuccess",
    "AxE":  "AccountServersListError",
    "AxK":  "AccountServersListSuccess",
    "AAE":  "AccountCharacterAddError",
    "AAK":  "AccountCharacterAddSuccess",
    "ATE":  "AccountTicketResponseError",
    "ATK":  "AccountTicketResponseSuccess",
    "AXE":  "AccountSelectServerError",
    "AXK":  "AccountSelectServerSuccess",
    "AYK":  "AccountSelectServerPlainSuccess",  # IP encodée + port + ticket
    "ASE":  "AccountCharacterSelectedError",
    "ASK":  "AccountCharacterSelectedSuccess",
    "As":   "AccountStats",
    "AN":   "AccountNewLevel",
    "AR":   "AccountRestrictions",
    "AH":   "AccountHosts",
    "Ar":   "AccountRescue",
    "Ag":   "AccountGiftsList",
    "AGE":  "AccountGiftStoredError",
    "AGK":  "AccountGiftStoredSuccess",
    "Aq":   "AccountQueue",
    "Af":   "AccountNewQueue",
    "AV":   "AccountRegionalVersion",
    "APE":  "AccountCharacterNameGeneratedError",
    "APK":  "AccountCharacterNameGeneratedSuccess",
    "AK":   "AccountKey",
    "AQ":   "AccountSecretQuestion",
    "ADE":  "AccountCharacterDeleteError",
    "ADK":  "AccountCharacterDeleteSuccess",
    "AM?":  "AccountCharacterMigrationAskConfirm",
    "AME":  "AccountCharacterMigrationError",
    "AMK":  "AccountCharacterMigrationSuccess",
    "AF":   "AccountFriendServerList",
    "Am":   "AccountMiniClipInfo",

    # Game — création et états
    "GCE":  "GameCreateError",
    "GCK":  "GameCreateSuccess",
    "GJ":   "GameJoin",
    "GP":   "GamePositionStart",
    "GR":   "GameReady",
    "GS":   "GameStartToPlay",
    "GE":   "GameEnd",
    "GM|-": "GameMovementRemove",
    "GM":   "GameMovement",
    "Gc":   "GameChallenge",
    "Gt":   "GameTeam",
    "GV":   "GameLeave",
    "Gf":   "GameFlag",

    # Game — infos de combat
    "GIC":  "GamePlayersCoordinates",
    "GIE":  "GameEffect",
    "GIe":  "GameClearAllEffect",
    "GIP":  "GamePVP",

    # Game — données de carte
    "GDM":  "GameMapData",         # mapId|date|key
    "GDK":  "GameMapLoaded",
    "GDC":  "GameCellData",
    "GDZ":  "GameZoneData",
    "GDO":  "GameCellObject",
    "GDF":  "GameFrameObject2",    # état des éléments interactifs
    "GDE":  "GameFrameObjectExternal",

    # Game — combat (défis)
    "Gd":   "GameFightChallenge",
    "GdO":  "GameFightChallengeUpdateError",
    "GdK":  "GameFightChallengeUpdateSuccess",
    "Gdi":  "GameShowFightChallengeTarget",

    # Game — tours
    "GTS":  "GameTurnStart",
    "GTF":  "GameTurnFinish",
    "GTL":  "GameTurnList",
    "GTM":  "GameTurnMiddle",
    "GTR":  "GameTurnReady",

    # Game — divers
    "GX":   "GameExtraClip",
    "Go":   "GameFightOption",
    "GO":   "GameGameOver",

    # Game — actions
    "GA":   "GameActions",
    "GAS":  "GameActionsStart",
    "GAF":  "GameActionsFinish",

    # Chat
    "cME":  "ChatMessageError",
    "cMK":  "ChatMessageSuccess",
    "cs":   "ChatServerMessage",
    "cS":   "ChatSmiley",
    "cC+":  "ChatSubscribeChannelAdd",
    "cC-":  "ChatSubscribeChannelRemove",

    # Dialog
    "DA":   "DialogCustomAction",
    "DCE":  "DialogCreateError",
    "DCK":  "DialogCreateSuccess",
    "DQ":   "DialogQuestion",
    "DV":   "DialogLeave",
    "DP":   "DialogPause",

    # Infos
    "IM":   "InfosInfoMaps",
    "IC":   "InfosCompass",
    "IH":   "InfosInfoCoordinatesPHighlight",
    "Im":   "InfosMessage",
    "IP":   "InfosTravelPath",   # IP{x;y|x;y|...} — trajet auto calculé par le serveur (autopilote natif)
    "IQ":   "InfosQuantity",
    "ILS":  "InfosLifeRestoreTimerStart",
    "ILF":  "InfosLifeRestoreTimerFinish",

    # Entités sur la carte (serveur privé)
    "NL":   "GameEntityCreate",   # Joueur qui arrive sur la carte (K+{id};{name};...)
    "Nx":   "GameEntityCompanion", # Marque une entité comme héros/compagnon (envoyé après NL)
    "Nm":   "GameEntityGroup",    # Groupe de personnages / team (Nm {groupId};{count};{name};{memberIds})
    "hP":   "HerePlayer",         # Joueur déjà sur la carte au chargement ({cell_id}|{name};{sex};...)

    # Métiers (serveur privé)
    "JX":   "JobXP",          # Stats complètes des métiers : K{charId}~{jobId};{lvl};{xp};{xpNext};{xpTotal};|...
    "JS":   "JobSkills",       # Compétences des métiers : K{charId}_{jobId};{skillId}~...

    # Sorts
    "SL":   "SpellsList",
    "SLo":  "SpellsChangeOption",
    "SUE":  "SpellsUpgradeSpellError",
    "SUK":  "SpellsUpgradeSpellSuccess",
    "SB":   "SpellsSpellBoost",
    "SF+":  "SpellsSpellForgetShow",
    "SF-":  "SpellsSpellForgetClose",

    # Objets / inventaire
    "Oa":   "ItemsAccessories",
    "ODE":  "ItemsDropError",
    "ODK":  "ItemsDropSuccess",
    "OAE":  "ItemsAddError",
    "OAK":  "ItemsAddSuccess",
    "OC":   "ItemsChange",
    "OR":   "ItemsRemove",
    "OQ":   "ItemsQuantity",
    "OM":   "ItemsMovement",
    "OT":   "ItemsTool",
    "Ow":   "ItemsWeight",
    "OS+":  "ItemsItemSetAdd",
    "OS-":  "ItemsItemSetRemove",
    "OK":   "ItemsItemUseCondition",
    "OF":   "ItemsItemFound",

    # Amis
    "FAE":  "FriendsAddFriendError",
    "FAK":  "FriendsAddFriendSuccess",
    "FDE":  "FriendsRemoveFriendError",
    "FDK":  "FriendsRemoveFriendSuccess",
    "FL":   "FriendsFriendsList",
    "FS":   "FriendsSpouse",
    "FO":   "FriendsNotifyChange",

    # Ennemis
    "iAE":  "EnemiesAddEnemyError",
    "iAK":  "EnemiesAddEnemySuccess",
    "iDE":  "EnemiesRemoveEnemyError",
    "iDK":  "EnemiesRemoveEnemySuccess",
    "iL":   "EnemiesEnemiesList",

    # Échanges
    "EV":   "ExchangeLeave",
    "ECE":  "ExchangeCreateError",
    "ECK":  "ExchangeCreateSuccess",
    "EE":   "ExchangeError",
    "EK":   "ExchangeReady",
    "EW":   "ExchangeSetPublicMode",
    "ESK":  "ExchangeMovementSellSuccess",
    "EBK":  "ExchangeMovementBuySuccess",
    "EMO":  "ExchangeMovementItemsSuccess",
    "EMG":  "ExchangeMovementKamasSuccess",
    "EHT":  "ExchangeBigStoreType",
    "EHl":  "ExchangeBigStoreItemList",
    "EHL":  "ExchangeBigStoreTypeItemsList",
    "EHP":  "ExchangeBigStoreItemMiddlePrice",

    # Guilde
    "GCE":  "GuildCreateError",  # attention : conflit avec GameCreateError → priorité longest match
    "gIGK": "GuildGetInfosGeneralSuccess",
    "gIMK": "GuildGetInfosMembersSuccess",

    # Maison
    "hK":   "HousesKickSuccess",
    "hS":   "HousesSellSuccess",

    # Monture
    "Rd":   "MountData",
    "Rp":   "MountParkData",

    # Conquête
    "CIK":  "ConquestPrismInfosSuccess",
    "CFK":  "ConquestPrismFightSuccess",

    # Quête
    "QlK":  "QuestListSuccess",
}

# ---------------------------------------------------------------------------
# Messages Client → Serveur
# ---------------------------------------------------------------------------
CLIENT_MESSAGES: dict[str, str] = {
    # Handshake / ping
    "ping":   "AksPing",
    "qping":  "AksQuickPing",
    "rpong":  "AksRPong",

    # Basics
    "BA":   "BasicsAuthorizedCommand",
    "BaM":  "BasicsAuthorizedMoveCommand",
    "BaK":  "BasicsAuthorizedKickCommand",
    "BW":   "BasicsWhoIs",
    "BQ":   "BasicsKick",
    "BYA":  "BasicsAway",
    "BYI":  "BasicsInvisible",
    "BD":   "BasicsGetDate",
    "BC":   "BasicsFileCheckAnswer",
    "BK":   "BasicsSanctionMe",
    "Bp":   "BasicsRequestAveragePing",

    # Account
    "version":    "AccountVersion",
    "credential": "AccountCredential",
    "nickname":   "AccountSetNickname",
    "AL":   "AccountGetCharacters",
    "ALf":  "AccountGetCharactersForced",
    "Ax":   "AccountGetServersList",
    "AX":   "AccountSetServer",
    "AF":   "AccountSearchForFriend",
    "AS":   "AccountSetCharacter",
    "AA":   "AccountAddCharacter",
    "AD":   "AccountDeleteCharacter",
    "AR":   "AccountResetCharacter",
    "AB":   "AccountBoost",
    "AT":   "AccountSendTicket",
    "Ar":   "AccountRequestRescue",
    "Ag":   "AccountGetGifts",
    "AG":   "AccountAttributeGiftToCharacter",
    "Af":   "AccountQueuePosition",
    "AP":   "AccountGetRandomCharacterName",
    "Ak":   "AccountUseKey",
    "AV":   "AccountRequestRegionalVersion",
    "Ai":   "AccountSendIdentity",
    "AM":   "AccountValidCharacterMigration",
    "AM-":  "AccountDeleteCharacterMigration",
    "AM?":  "AccountAskCharacterMigration",
    "Ap":   "AccountConfiguredPort",

    # Game
    "GC":   "GameCreate",
    "GQ":   "GameRequestLeave",
    "Gp":   "GameSetPlayerPosition",
    "GR":   "GameRequestReady",
    "GD":   "GameGetMapData",
    "GI":   "GameGetExtraInformations",
    "Gt":   "GameTurnEnd",
    "GT":   "GameTurnOk",
    "GP*":  "GameAskDisablePVPMode",
    "GP":   "GameEnabledPVPMode",
    "GF":   "GameFreeMySoul",
    "Gf":   "GameSetFlag",
    "Gdi":  "GameShowFightChallengeTarget",

    # Game actions (GA + action_id numérique dans le payload)
    "GA001": "GameActionMove",        # GA001{path}\n — déplacement (C→S)
    "GA300": "GameActionCastSpell",   # GA300{spell_id};{cell}\n — lancer un sort
    "GA303": "GameActionMoveCombat",  # GA303;{cell}\n — déplacement en combat
    "GA500": "GameActionHarvest",     # GA500{cell};{type}\n — récolte (C→S)
    "GA900": "GameActionAggress",     # GA900;{cell}\n — agression manuelle d'un monstre
    "GA":   "GameActionsSendActions",
    "GKK":  "GameActionAck",
    "GKE":  "GameActionCancel",

    # Chat
    "BaM":  "BasicsAutorisedMove",   # BaM{x},{y},{subAreaId} — voyage auto (double-clic worldmap/minimap, admin/GM)
    "BM":   "ChatSend",
    "BR":   "ChatReportMessage",
    "cC+":  "ChatRequestSubscribeChannelAdd",
    "cC-":  "ChatRequestSubscribeChannelRemove",
    "BS":   "ChatUseSmiley",

    # Dialog
    "DB":   "DialogBeginning",
    "DC":   "DialogCreate",
    "DV":   "DialogRequestLeave",
    "DR":   "DialogResponse",

    # Infos
    "IM":   "InfosGetMaps",
    "IP":   "AccountMoveToPosition",  # IP{x},{y},{subAreaId} — autopilote natif (plaintext, gate item slot 28 côté client)
    "Ir":   "InfosSendScreenInfo",

    # Sorts
    "SM":   "SpellsMoveToUsed",
    "SB":   "SpellsBoost",
    "SF":   "SpellsForget",

    # Objets
    "OM":   "ItemsRequestMovement",
    "OD":   "ItemsDrop",
    "Od":   "ItemsDestroy",
    "Ou":   "ItemsUseConfirm",
    "OU":   "ItemsUseNoConfirm",
    "Ox":   "ItemsDissociate",
    "Os":   "ItemsSetSkin",
    "Of":   "ItemsFeed",

    # Amis
    "FL":   "FriendsGetFriendsList",
    "FA":   "FriendsAddFriend",
    "FD":   "FriendsRemoveFriend",
    "FJ":   "FriendsJoin",
    "FJF":  "FriendsJoinFriend",
    "FJC":  "FriendsCompass",
    "FO":   "FriendsSetNotifyWhenConnect",

    # Ennemis
    "iL":   "EnemiesGetEnemiesList",
    "iA":   "EnemiesAddEnemy",
    "iD":   "EnemiesRemoveEnemy",

    # Clés
    "KV":   "KeyRequestLeave",
    "KK":   "KeySendKey",

    # Jobs
    "JO":   "JobChangeJobStats",

    # Échanges
    "EV":   "ExchangeLeave",
    "ER":   "ExchangeRequest",
    "Es":   "ExchangeShop",
    "EA":   "ExchangeAccept",
    "EK":   "ExchangeRequestReady",
    "EMO":  "ExchangeMovementItems",
    "EP":   "ExchangeMovementPay",
    "EMG":  "ExchangeMovementKamas",
    "ES":   "ExchangeMovementSell",
    "EB":   "ExchangeMovementBuy",
    "EQ":   "ExchangeOfflineExchange",
    "Eq":   "ExchangeRequestAskOfflineExchange",
    "EHT":  "ExchangeBigStoreType",
    "EHl":  "ExchangeBigStoreItemList",
    "EHB":  "ExchangeBigStoreBuy",
    "EHS":  "ExchangeBigStoreSearch",
    "EW":   "ExchangeSetPublicMode",
    "EJF":  "ExchangeGetCrafterForJob",
    "Erp":  "ExchangePutInShedFromInventory",
    "Erg":  "ExchangePutInInventoryFromShed",
    "Erc":  "ExchangePutInCertificateFromShed",
    "ErC":  "ExchangePutInShedFromCertificate",
    "Efp":  "ExchangePutInMountParkFromShed",
    "Efg":  "ExchangePutInShedFromMountPark",
    "Eff":  "ExchangeKillMountInPark",
    "Erf":  "ExchangeKillMount",
    "EHP":  "ExchangeGetItemMiddlePriceInBigStore",
    "EL":   "ExchangeReplayCraft",
    "EMR":  "ExchangeRepeatCraft",
    "EMr":  "ExchangeStopRepeatCraft",

    # Maison
    "hQ":   "HousesKick",
    "hV":   "HousesRequestLeave",
    "hS":   "HousesSell",
    "hB":   "HousesBuy",
    "hG":   "HousesState",
    "hG+":  "HousesShare",
    "hG-":  "HousesUnShare",

    # Émotes
    "eU":   "EmotesUseEmote",
    "eD":   "EmotesSetDirection",

    # Documents
    "dV":   "DocumentsRequestLeave",

    # Guilde
    "gC":   "GuildCreate",
    "gV":   "GuildRequestLeave",
    "gITV": "GuildLeaveTaxInterface",
    "gJR":  "GuildInvite",
    "gJK":  "GuildAcceptInvitation",
    "gJE":  "GuildRefuseInvitation",
    "gIG":  "GuildGetInfosGeneral",
    "gIM":  "GuildGetInfosMembers",
    "gIB":  "GuildGetInfosBoosts",
    "gIT":  "GuildGetInfosTaxCollector",
    "gIF":  "GuildGetInfosMountPark",
    "gIH":  "GuildGetInfosGuildHouses",
    "gK":   "GuildBan",
    "gP":   "GuildChangeMemberProfile",
    "gB":   "GuildBoostCharacteristic",
    "gb":   "GuildBoostSpell",
    "gH":   "GuildHireTaxCollector",
    "gTJ":  "GuildJoinTaxCollector",
    "gTV":  "GuildLeaveTaxCollector",
    "gF":   "GuildRemoveTaxCollector",
    "gh":   "GuildTeleportToGuildHouse",
    "gf":   "GuildTeleportToGuildFarm",

    # Waypoints
    "WV":   "WaypointsRequestLeave",
    "WU":   "WaypointsUse",

    # Métro
    "Wv":   "SubwayRequestLeave",
    "Wu":   "SubwayUse",
    "Ww":   "SubwayRequestPrismLeave",
    "Wp":   "SubwayPrismUse",

    # Conquête
    "CB":   "ConquestGetAlignedBonus",
    "CIJ":  "ConquestPrismInfosJoin",
    "CIV":  "ConquestPrismInfosLeave",
    "CFJ":  "ConquestPrismFightJoin",
    "CFV":  "ConquestPrismFightLeave",
    "CWJ":  "ConquestWorldInfosJoin",
    "CWV":  "ConquestWorldInfosLeave",
    "CFS":  "ConquestSwitchPlaces",
    "Cb":   "ConquestRequestBalance",

    # Combats (liste)
    "fL":   "FightsGetList",
    "fD":   "FightsGetDetails",
    "fS":   "FightsBlockSpectators",
    "fP":   "FightsBlockJoinerExceptParty",
    "fN":   "FightsBlockJoiner",
    "fH":   "FightsNeedHelp",

    # Tutoriel
    "TV":   "TutorialEnd",

    # Quête
    "QL":   "QuestGetList",
    "QS":   "QuestGetStep",

    # Groupe
    "PI":   "PartyInvite",
    "PR":   "PartyRefuseInvitation",
    "PA":   "PartyAcceptInvitation",
    "PV":   "PartyRequestLeave",
    "PF":   "PartyRequestFollow",
    "PW":   "PartyWhere",
    "PG":   "PartyFollowAll",

    # Monture
    "Rn":   "MountRename",
    "Rf":   "MountFree",
    "Rx":   "MountSetXP",
    "Rr":   "MountRide",
    "Rd":   "MountRequestData",
    "Rp":   "MountParkMountData",
    "Ro":   "MountRemoveObjectInPark",
    "Rs":   "MountMountParkSell",
    "Rb":   "MountRequestMountParkBuy",
    "Rv":   "MountRequestLeave",
    "Rc":   "MountCastrate",
}

# ---------------------------------------------------------------------------
# Index pré-calculé : listes d'IDs triées par longueur décroissante
# pour un matching longest-prefix correct.
# ---------------------------------------------------------------------------
_SERVER_IDS_SORTED: list[str] = sorted(SERVER_MESSAGES.keys(), key=len, reverse=True)
_CLIENT_IDS_SORTED: list[str] = sorted(CLIENT_MESSAGES.keys(), key=len, reverse=True)
