"""
Réponse automatique aux messages privés (anti-suspicion bot).

Détecte les MP reçus (cMK canal F) et envoie une réponse générique
après un délai aléatoire proportionnel à la longueur de la réponse.

DÉSACTIVÉ par défaut : activable depuis l'onglet Paramètres
(`auto_reply_enabled` dans config.json), togglable à chaud via `set_enabled()`.

Délai :
  - réponse courte  (≤ 12 chars)  → 6 – 18 s
  - réponse moyenne (≤ 40 chars)  → 14 – 30 s
  - réponse longue  (> 40 chars)  → 28 – 45 s
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import random
import re
import time

import bot.channel as channel
from protocol.parser import ParsedMessage, on_server_message

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Activation (opt-in) — désactivé par défaut
# ---------------------------------------------------------------------------
_CONFIG_PATH = os.path.normpath(
    os.path.join(os.path.dirname(__file__), "..", "config.json")
)


def _load_enabled_from_config() -> bool:
    try:
        with open(_CONFIG_PATH, "r", encoding="utf-8") as f:
            return bool(json.load(f).get("auto_reply_enabled", False))
    except (OSError, json.JSONDecodeError):
        return False


_enabled: bool = _load_enabled_from_config()


def set_enabled(enabled: bool) -> None:
    """Activer/désactiver la réponse auto à chaud (depuis l'onglet Paramètres)."""
    global _enabled
    _enabled = bool(enabled)
    logger.info("[auto_reply] Réponse auto aux MP %s", "activée" if _enabled else "désactivée")


def is_enabled() -> bool:
    return _enabled

# ---------------------------------------------------------------------------
# Anti-spam : ne pas répondre deux fois de suite au même expéditeur
# en moins de 60 s
# ---------------------------------------------------------------------------
_GLOBAL_LAST_REPLY: dict[str, float] = {}   # repli hors session (pseudo_lower → ts)
_COOLDOWN = 60.0  # secondes


# ---------------------------------------------------------------------------
# Base de réponses : (patterns_regex, [réponses])
# L'ordre des règles compte : premier match gagne.
# ---------------------------------------------------------------------------

_RULES: list[tuple[list[str], list[str]]] = [

    # ── Accusation directe / soupçon de bot ──────────────────────────────
    (
        [r"\bbot\b", r"\bmacro\b", r"\bauto\b", r"\bscript\b",
         r"\bprogramme\b", r"\blogiciel\b", r"\bcheat\b", r"\btricheur\b",
         r"\bafk\b", r"\btu\s+es\s+l[aà]\b", r"\bt[']es\s+l[aà]\b"],
        [
            "lol non je farm juste^^",
            "Haha nn, je suis juste concentré sur ce que je fais",
            "je suis là, je farme tranquille c'est tout :)",
            "Non non, humain 100% garanti xD",
            "J'allais justement faire une pause, qu'est-ce qu'il y a ?",
            "Je suis là, j'écoutais de la musique en farmant",
            "Pas du tout, je jouais juste sans regarder le chat ^^",
            "Lol je fais juste farm, je réponds pas toujours vite",
        ]
    ),

    # ── Salutations ───────────────────────────────────────────────────────
    (
        [r"\bsalut\b", r"\bcoucou\b", r"\bhello\b", r"\bcc\b",
         r"\bohai\b", r"\byo\b", r"\bhey\b", r"\bbjr\b", r"\bbonjour\b"],
        [
            "Salut !",
            "Coucou :)",
            "Hey !",
            "Salut, ça va ?",
            "Yo !",
            "Cc ^^",
        ]
    ),

    # ── Comment ça va ─────────────────────────────────────────────────────
    (
        [r"\bça va\b", r"\bcomment\s+tu\s+vas\b", r"\bca\s+va\b",
         r"\btu\s+vas\s+bien\b"],
        [
            "Ouais ça va et toi ?",
            "Bien et toi :) ?",
            "Tranquille, en train de farm, toi ?",
            "Nickel, occupe à farm ^^",
        ]
    ),

    # ── Vente / achat / échange ───────────────────────────────────────────
    (
        [r"\bvend\b", r"\bvente\b", r"\bachète\b", r"\bachat\b",
         r"\béchange\b", r"\btroc\b", r"\bprix\b", r"\bkamas?\b",
         r"\bcombo\b", r"\bitem\b", r"\bobjet\b"],
        [
            "Désolé, je suis pas là pour acheter/vendre là",
            "Pas intéressé pour l'instant, merci quand même",
            "J'ai pas le temps là, désolé !",
            "Pas pour moi, merci",
            "Je gère pas les échanges là, désolé",
        ]
    ),

    # ── Guilde / alliance ─────────────────────────────────────────────────
    (
        [r"\bguilde\b", r"\balliance\b", r"\brecrut\b", r"\brejoin\b",
         r"\bintégrer\b"],
        [
            "Merci mais je suis déjà dans une guilde ^^",
            "J'ai déjà ma guilde, merci !",
            "Pas pour moi, j'ai déjà ma guilde",
            "Sympa la prop mais non merci :)",
        ]
    ),

    # ── Groupe / donjon ───────────────────────────────────────────────────
    (
        [r"\bgroupe\b", r"\bdonjon\b", r"\bteam\b", r"\bparty\b",
         r"\bon\s+joue\b", r"\bon\s+farm\b"],
        [
            "Pas dispo là, je farm solo",
            "Désolé, j'ai pas le temps là",
            "Je peux pas là, désolé",
            "Je farm solo pour l'instant, merci",
        ]
    ),

    # ── Classe / niveau / perso ───────────────────────────────────────────
    (
        [r"\bclasse\b", r"\bniveau\b", r"\blvl\b", r"\bperso\b",
         r"\bt[']es\s+quoi\b", r"\bc[']est\s+quoi\b", r"\btu\s+joues\s+quoi\b"],
        [
            "Haha c'est une surprise ^^",
            "Tu verras ;)",
            "Un perso classique, rien de fou",
            "Je préfère garder le mystère :p",
        ]
    ),

    # ── Où es-tu / map ────────────────────────────────────────────────────
    (
        [r"\bo[uù]\s+tu\s+es\b", r"\bt[']es\s+o[uù]\b", r"\bmap\b",
         r"\bzone\b", r"\bcoord\b"],
        [
            "Je suis en train de farm, endroit tranquille ^^",
            "Dans le coin, je farm",
            "Quelque part sur la map, haha",
            "Pas loin, je farm",
        ]
    ),

    # ── Aide / besoin d'aide ──────────────────────────────────────────────
    (
        [r"\baide\b", r"\baider\b", r"\bhelp\b", r"\bbesoin\b",
         r"\bpeux-tu\b", r"\bpeux\s+tu\b", r"\btu\s+peux\b"],
        [
            "Désolé, j'ai pas vraiment le temps là",
            "Je peux pas t'aider là, désolé",
            "Pas possible pour l'instant, désolé",
            "Je suis occupé là, désolé !",
        ]
    ),

    # ── Serveur / meta ────────────────────────────────────────────────────
    (
        [r"\bserveur\b", r"\bméta\b", r"\bmeta\b", r"\bpatch\b",
         r"\bmaj\b", r"\bupdate\b"],
        [
            "Je suis pas trop au courant des actus là",
            "Je farm, je suis pas trop les news ^^",
            "Aucune idée désolé",
        ]
    ),

    # ── Réponse par défaut ────────────────────────────────────────────────
    (
        [],   # vide = fallback, toujours matché en dernier
        [
            "^^",
            ":)",
            "Lol",
            "Haha ok",
            "Ouais ouais",
            "Je suis là, juste occupé ^^",
            "Hm ?",
            "Ok ok",
            "Ah ouais ?",
            "Intéressant ^^",
        ]
    ),
]


# ---------------------------------------------------------------------------
# Logique de sélection
# ---------------------------------------------------------------------------

def _pick_response(message: str) -> str:
    """Choisir une réponse en fonction des mots-clés du message."""
    msg_lower = message.lower()

    for patterns, responses in _RULES:
        if not patterns:
            # fallback
            return random.choice(responses)
        for pat in patterns:
            if re.search(pat, msg_lower):
                return random.choice(responses)

    return random.choice(_RULES[-1][1])


def _compute_delay(response: str) -> float:
    """Délai aléatoire proportionnel à la longueur de la réponse."""
    n = len(response)
    if n <= 12:
        return random.uniform(6, 18)
    elif n <= 40:
        return random.uniform(14, 30)
    else:
        return random.uniform(28, 45)


# ---------------------------------------------------------------------------
# Coroutine de réponse
# ---------------------------------------------------------------------------

async def _delayed_reply(sender: str, response: str) -> None:
    delay = _compute_delay(response)
    logger.info("[auto_reply] Réponse à %s dans %.1fs : %r", sender, delay, response)
    await asyncio.sleep(delay)

    if not _enabled:
        logger.info("[auto_reply] Désactivé entre-temps, réponse à %s annulée", sender)
        return

    if not channel.is_connected():
        logger.warning("[auto_reply] Plus connecté, réponse annulée")
        return

    msg = f"BM{sender}|{response}|"
    await channel.send(msg)
    logger.info("[auto_reply] → MP envoyé à %s : %r", sender, response)


# ---------------------------------------------------------------------------
# Handler cMK
# ---------------------------------------------------------------------------

@on_server_message("cMK")
def _on_chat_message(msg: ParsedMessage) -> None:
    """Intercepter les messages privés reçus (canal F = From)."""
    if not _enabled:
        return

    payload = msg.payload

    # Format attendu : F|{sender_id}|{sender_name}|{message}|
    if not payload.startswith("F|"):
        return

    parts = payload.split("|")
    # parts[0] = "F", parts[1] = sender_id, parts[2] = sender_name, parts[3] = message
    if len(parts) < 4:
        return

    sender_name = parts[2]
    message_text = parts[3]

    if not sender_name or not message_text:
        return

    # Anti-spam (cooldown par-session)
    now = time.time()
    key = sender_name.lower()
    try:
        from core.session import active
        _last_reply = active().auto_reply_cooldown
    except Exception:
        _last_reply = _GLOBAL_LAST_REPLY
    if now - _last_reply.get(key, 0) < _COOLDOWN:
        logger.debug("[auto_reply] Cooldown actif pour %s, MP ignoré", sender_name)
        return
    _last_reply[key] = now

    logger.info("[auto_reply] MP reçu de %s : %r", sender_name, message_text)

    response = _pick_response(message_text)

    # Planifier la réponse sur la boucle asyncio principale
    try:
        loop = asyncio.get_event_loop()
        loop.create_task(_delayed_reply(sender_name, response))
    except RuntimeError:
        # Pas de boucle courante (ex: tests), ignorer
        pass
