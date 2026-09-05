"""
Réaction automatique au challenge « antibot » du serveur.

DÉTECTION
─────────
Quand le serveur soupçonne un bot, il envoie deux messages (cf. logs) :

    S→C  Im  1512;{code}
    S→C  M   150|Serveur;Afin d'indiquer que vous n'êtes pas un bot merci de
             rentrer la commande dans le chat : .code {code}

Un popup affiche le code, et il faut taper ``.code {code}`` dans le chat pour
prouver qu'on est humain. Ne pas répondre → kick / ban.

RÉACTION
────────
1. Arrêter immédiatement le bot (sans abandonner un combat en cours).
2. GELER toutes les actions de combat (GameState._antibot_freeze) le temps de
   « lire le popup et taper le code », puis envoyer ``.code {code}`` UNE seule
   fois. (L'ancien triple envoi espacé de 8 s était une signature robot : un
   humain ne retape pas 3× la même commande — et surtout il ne pilote pas ses
   persos pendant qu'il tape. Cf. ban du 09/07/2026 : les 3 .code sont partis
   pendant que les 8 persos continuaient leurs tours sous les yeux de l'admin.)
3. Terminer « doucement » le combat en cours (mode spectateur = jeu ralenti),
   puis rester ~1 min sur la map.
4. Utiliser une potion de rappel (téléport hors zone de farm).
5. Se déconnecter 2–3 min plus tard. Un challenge reçu = on est déjà surveillé :
   on ne reprend JAMAIS le farm derrière.

Le tout tourne dans une task asyncio héritant du contexte de la session
(``active()`` / ``game.state.current`` / ``bot.channel.*`` résolus dessus).
"""

from __future__ import annotations

import asyncio
import logging
import random
import re

import bot.channel as channel
from game import state as _state
from protocol.parser import ParsedMessage, on_server_message

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Réglages (avec aléa léger pour paraître humain)
# ---------------------------------------------------------------------------

# Envoi de la commande .code : UNE seule fois, après un temps de « lecture du popup
# + saisie » réaliste. Toutes les actions de combat sont gelées pendant ce temps.
CODE_FIRST_DELAY: tuple[float, float] = (8.0, 18.0)    # lecture du popup + frappe
CODE_POST_SEND_HOLD: tuple[float, float] = (2.0, 5.0)  # « je repose les mains »

# Attente sur la map après la fin du combat, avant le rappel (~1 min).
POST_COMBAT_MAP_WAIT: tuple[float, float] = (55.0, 70.0)

# Attente après le rappel, avant la déconnexion (2–3 min).
PRE_DISCONNECT_WAIT: tuple[float, float] = (120.0, 180.0)

# Plafond d'attente de fin de combat (sécurité anti-blocage).
COMBAT_FINISH_CAP: float = 300.0

# gid de la « Potion de Rappel » (base d'items Abrak). Repli : recherche par nom.
RECALL_POTION_GIDS: tuple[int, ...] = (548, 42309, 9039)

# Canal chat général (le client envoie « BM*|.code {code}| » — cf. logs relay).
_CHAT_GENERAL = "*"


# ---------------------------------------------------------------------------
# Détection — handlers de messages serveur
# ---------------------------------------------------------------------------

_CODE_RX = re.compile(r"\.code\s+(\d+)")


@on_server_message("M")
def _on_server_message_m(msg: ParsedMessage) -> None:
    """M (AksServerMessage) — repérer l'annonce antibot « ...bot... .code {code} »."""
    payload = msg.payload
    if ".code" not in payload or "bot" not in payload.lower():
        return
    m = _CODE_RX.search(payload)
    if not m:
        return
    _trigger(m.group(1))


@on_server_message("Im")
def _on_infos_message(msg: ParsedMessage) -> None:
    """Im 1512;{code} — code antibot du popup (source de repli si M change)."""
    parts = msg.payload.strip().split(";")
    if len(parts) < 2 or parts[0] != "1512":
        return
    code = parts[1].strip()
    if code.isdigit():
        _trigger(code)


# ---------------------------------------------------------------------------
# Déclenchement (idempotent par session)
# ---------------------------------------------------------------------------

def _trigger(code: str) -> None:
    """Lancer la procédure antibot une seule fois par session."""
    from core.session import active_or_none

    session = active_or_none()
    if session is None:
        logger.error("[antibot] Challenge reçu (code %s) hors session — ignoré", code)
        return

    if getattr(session, "_antibot_active", False):
        logger.info("[antibot] Challenge déjà en cours de traitement (code %s)", code)
        return
    session._antibot_active = True

    logger.warning("[antibot] ⚠ CHALLENGE ANTIBOT DÉTECTÉ — code %s", code)
    try:
        from dashboard import bridge
        bridge.notify(f"⚠ ANTIBOT détecté — code {code} — procédure d'évasion", level="error")
        bridge.add_console(f"🚨 Antibot : code {code} — arrêt du bot et évasion")
    except Exception:
        pass

    try:
        loop = asyncio.get_event_loop()
        loop.create_task(_run_procedure(session, code), name="antibot-procedure")
    except RuntimeError:
        logger.error("[antibot] Pas de boucle asyncio — impossible de lancer la procédure")


# ---------------------------------------------------------------------------
# Procédure principale
# ---------------------------------------------------------------------------

async def _run_procedure(session, code: str) -> None:
    """Orchestration : stop → .code → fin de combat → attente → rappel → déco."""
    try:
        from dashboard import bridge
    except Exception:
        bridge = None

    def _console(text: str) -> None:
        logger.info("[antibot] %s", text)
        if bridge is not None:
            try:
                bridge.add_console(text)
            except Exception:
                pass

    # 1) Arrêter le bot (sans casser un combat en cours) + jeu ralenti « spectateur »
    _soft_stop_bots(session)
    try:
        _state.current._spectator_present = True  # force le mode combat lent
    except Exception:
        pass
    _console("Bot arrêté — combat en cours terminé en mode discret")

    # 2) Envoyer .code une fois, actions gelées pendant la « saisie » (task concurrente :
    # le combat en cours reste piloté par sa boucle, qui attend la levée du gel)
    code_task = asyncio.get_event_loop().create_task(
        _send_code(code, _console), name="antibot-code"
    )

    # 3) Terminer le combat en cours doucement
    await _finish_combat(session, _console)

    # S'assurer que les .code sont partis avant de continuer
    try:
        await code_task
    except Exception as exc:
        logger.warning("[antibot] Envoi .code : %s", exc)

    # 4) Rester ~1 min sur la map
    wait_map = random.uniform(*POST_COMBAT_MAP_WAIT)
    _console(f"Attente {wait_map:.0f}s sur la map avant rappel…")
    await asyncio.sleep(wait_map)

    # Le serveur a pu nous kicker pendant l'attente (cf. ban du 09/07) : dans ce cas
    # il n'y a plus rien à faire — ne pas dérouler rappel/déco sur une session morte.
    if not channel.is_connected():
        _console("Session déjà fermée par le serveur — fin de la procédure antibot")
        logger.warning("[antibot] Procédure interrompue : connexion déjà fermée")
        return

    # 5) Potion de rappel
    used = await _use_recall_potion(session, _console)
    if used:
        # Laisser le téléport + chargement de map se faire
        await asyncio.sleep(random.uniform(3.0, 6.0))

    # 6) Déconnexion 2–3 min plus tard
    wait_deco = random.uniform(*PRE_DISCONNECT_WAIT)
    if used:
        _console(f"Rappel effectué — déconnexion dans {wait_deco:.0f}s")
    else:
        _console(f"Pas de rappel possible — déconnexion dans {wait_deco:.0f}s")
    await asyncio.sleep(wait_deco)

    _console("Déconnexion (antibot)")
    await _disconnect(session)
    logger.warning("[antibot] Procédure antibot terminée (déconnecté)")


# ---------------------------------------------------------------------------
# Étapes
# ---------------------------------------------------------------------------

def _soft_stop_bots(session) -> None:
    """Ne plus engager de nouvelle action, mais laisser finir le combat en cours.

    - combat_farm_loop : stop_after_combat() → termine le combat courant puis rend la main.
    - récolte / scripts move() : soft_stop() → sortent en fin d'itération.
    """
    try:
        from bot import combat
        if combat.is_running(session):
            combat.stop_after_combat()
    except Exception as exc:
        logger.debug("[antibot] stop combat : %s", exc)
    try:
        from bot import harvester
        harvester.soft_stop_bot(session)
    except Exception as exc:
        logger.debug("[antibot] soft-stop harvester : %s", exc)
    try:
        from bot import script_engine
        script_engine.soft_stop_bot(session)
    except Exception as exc:
        logger.debug("[antibot] soft-stop script : %s", exc)


async def _send_code(code: str, console) -> None:
    """Envoyer « .code {code} » dans le chat général, UNE seule fois, comme un humain.

    Pendant toute la « lecture du popup + saisie » (CODE_FIRST_DELAY) puis un court
    instant après l'envoi (CODE_POST_SEND_HOLD), le gel antibot est posé : toutes les
    actions de combat de la team attendent (cf. combat._wait_antibot_freeze) — un
    humain qui tape le code ne pilote pas ses persos en même temps.
    """
    _state.current._antibot_freeze = True
    try:
        console("Saisie du code antibot — actions de combat gelées…")
        await asyncio.sleep(random.uniform(*CODE_FIRST_DELAY))
        if not channel.is_connected():
            logger.warning("[antibot] Déconnecté — envoi .code interrompu")
            return
        ok = await channel.send(f"BM{_CHAT_GENERAL}|.code {code}|\n")
        console(f".code {code} envoyé" if ok else "⚠ Échec d'envoi du .code")
        await asyncio.sleep(random.uniform(*CODE_POST_SEND_HOLD))
    finally:
        _state.current._antibot_freeze = False


async def _finish_combat(session, console) -> None:
    """Attendre la fin du combat en cours (le laisser se jouer en mode lent).

    Si aucune boucle bot ne pilote le combat (combat manuel), on le joue nous-mêmes
    via fight_group(). Le flag spectateur force les délais anti-suspicion.
    """
    from game.state import wait_combat_end

    if not _state.current.in_combat:
        return

    console("Combat en cours — finalisation discrète…")
    from bot import combat, harvester, script_engine

    loop_drives = (
        combat.is_running(session)
        or harvester.is_running(session)
        or script_engine.is_running(session)
    )

    elapsed = 0.0
    while _state.current.in_combat and elapsed < COMBAT_FINISH_CAP:
        if not channel.is_connected():
            return
        if loop_drives:
            # Une boucle joue déjà le combat : on attend juste sa fin (GE).
            await wait_combat_end(timeout=30.0)
            elapsed += 30.0
        else:
            # Personne ne pilote : on joue le combat nous-mêmes.
            try:
                await combat.fight_group()
            except Exception as exc:
                logger.warning("[antibot] fight_group : %s", exc)
                return
    if _state.current.in_combat:
        logger.warning("[antibot] Combat toujours en cours après %.0fs — on continue", elapsed)
    else:
        console("Combat terminé")


def _find_recall_potion_uid() -> int | None:
    """Chercher l'uid d'une potion de rappel dans l'inventaire (gid connu ou nom)."""
    inv = getattr(_state.current, "_inventory", {}) or {}
    # 1) gid connu
    for item in inv.values():
        try:
            if int(item.get("gid") or -1) in RECALL_POTION_GIDS and int(item.get("qty", 0)) > 0:
                return int(item.get("uid"))
        except (TypeError, ValueError):
            continue
    # 2) repli : nom contenant « rappel »
    try:
        from data import items_db
        for item in inv.values():
            gid = item.get("gid")
            if gid is None or int(item.get("qty", 0)) <= 0:
                continue
            if "rappel" in items_db.get_name(gid).lower():
                return int(item.get("uid"))
    except Exception as exc:
        logger.debug("[antibot] lookup nom potion : %s", exc)
    return None


async def _use_recall_potion(session, console) -> bool:
    """Utiliser une potion de rappel : OU{uid}|{char_id}|1| (cf. logs client)."""
    if _state.current.in_combat:
        console("Toujours en combat — rappel annulé")
        return False

    char = _state.current.character
    if char is None:
        console("Personnage inconnu — rappel impossible")
        return False

    uid = _find_recall_potion_uid()
    if uid is None:
        console("⚠ Aucune potion de rappel en inventaire — rappel impossible")
        logger.warning("[antibot] Pas de potion de rappel trouvée (inventaire non tracké ?)")
        return False

    payload = f"OU{uid}|{char.character_id}|1|"
    ok = await channel.send(payload + "\n")
    if ok:
        console("🧪 Potion de rappel utilisée")
    else:
        console("⚠ Échec d'utilisation de la potion de rappel")
    return ok


async def _disconnect(session) -> None:
    """Couper la connexion (serveur + client Flash) pour déconnecter le personnage."""
    ch = session.channel
    for w in (ch.writer, ch.client_writer):
        try:
            if w is not None and not w.is_closing():
                w.close()
        except Exception as exc:
            logger.debug("[antibot] close writer : %s", exc)
