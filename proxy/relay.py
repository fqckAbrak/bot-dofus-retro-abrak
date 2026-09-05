"""
Relay bidirectionnel pour le proxy MITM Dofus Rétro.

GESTION DES CLÉS DE CHIFFREMENT C→S
─────────────────────────────────────
Flash et le bot partagent la même connexion TCP vers le serveur.
Le serveur valide la séquence de clés C→S : il attend la clé N+1 après
avoir reçu la clé N.

Problème : quand le bot injecte GA001+GA500 (clés #K, #K+1), Flash ne le
sait pas et continue avec sa propre séquence. Son prochain message utilise
une clé déjà vue ou en avance → serveur kick immédiatement.

Solution (re-chiffrement transparent) :
  - Pour chaque message C→S chiffré de Flash, le relay le déchiffre
    et le RE-chiffre avec la prochaine clé du compteur centralisé
    (_current_key dans bot.channel).
  - Bot et Flash partagent ainsi le même compteur ; le serveur voit
    une séquence continue.
  - Règle critique : _current_key ne doit être mis à jour QUE par
    _reencrypt_c2s (C→S) et channel.send() (bot). JAMAIS depuis les
    messages S→C dont le compteur est indépendant.

Pass-through : si on ne peut pas re-chiffrer (clé absente ou checksum KO),
le message Flash part au serveur tel quel — donc avec la clé Flash key_idx.
On DOIT alors aligner _current_key = key_idx, sinon la prochaine
re-chiffrement réussie utilisera une clé périmée et le serveur kickera.
Sans cet alignement, banque/NPC/chat provoquent des décos sporadiques
quand un message validable suit une rafale de pass-through.
"""

from __future__ import annotations

import asyncio
import logging
from asyncio import StreamReader, StreamWriter

import protocol.parser as _parser
from protocol.encoding import (
    decypher_data, cypher_data, _checksum, HEX_CHARS,
)

logger = logging.getLogger(__name__)


def _try_decrypt_s2c(raw: str, session) -> str:
    """Déchiffrer un message S→C pour le parsing/logging uniquement.

    Ne modifie PAS current_key (compteur S→C indépendant du C→S).

    Le serveur utilise une variante de checksum légèrement différente du
    client (décalage constant de +2 pour les messages courts, variable pour
    les longs). Le déchiffrement XOR est cependant correct. On valide donc
    le résultat en vérifiant que le texte déchiffré est du contenu ASCII
    imprimable (pas du binaire) plutôt que par checksum strict.
    """
    if not raw.startswith("-") or len(raw) < 4:
        return raw
    try:
        key_idx = int(raw[1], 16)
        a_keys = session.channel.a_keys
        if key_idx >= len(a_keys) or not a_keys[key_idx]:
            return raw
        key = a_keys[key_idx]
        chk = raw[2].upper()
        offset = int(chk, 16) * 2
        hex_data = raw[3:]
        decrypted = decypher_data(hex_data, key, offset)
        if not decrypted:
            return raw
        # Validation souple : le texte déchiffré doit être du contenu
        # protocole lisible (pas du binaire/garbage). Les messages Dofus
        # utilisent du latin-1 (accents dans noms de guilde, etc.).
        # On vérifie les 20 premiers chars : pas de contrôle 0x00-0x1F sauf \n\r\t.
        sample = decrypted[:20]
        if sample and all(
            ord(c) >= 32 or c in "\r\n\t" for c in sample
        ):
            return decrypted
        logger.debug(
            "[decrypt_s2c] -%s rejeté (binaire) sample=%r",
            HEX_CHARS[key_idx], sample,
        )
    except Exception as exc:
        logger.debug("[decrypt_s2c] exception: %s", exc)
    return raw


def _reencrypt_c2s(raw_bytes: bytes, session) -> tuple[bytes, str | None]:
    """Re-chiffrer un message C→S de Flash avec le compteur de la session.

    Returns:
        (bytes_à_envoyer_au_serveur, texte_déchiffré_pour_parsing_ou_None)

    Si le message n'est pas chiffré (pas de '-'), retourne (raw_bytes, None).
    Si le déchiffrement échoue, retourne (raw_bytes, None) — pass-through sûr.
    Si le re-chiffrement réussit, retourne (re_chiffré, plaintext) ET met à
    jour session.channel.current_key.
    """
    try:
        raw_str = raw_bytes.decode("latin-1")
    except Exception:
        return raw_bytes, None

    if not raw_str.startswith("-") or len(raw_str) < 4:
        return raw_bytes, None  # Message en clair, pas touché

    try:
        ch = session.channel
        a_keys = ch.a_keys
        if not any(a_keys):
            return raw_bytes, None  # Clés pas encore disponibles

        # --- Déchiffrement du message Flash ---
        key_idx = int(raw_str[1], 16)
        if key_idx >= len(a_keys) or not a_keys[key_idx]:
            # Pass-through : aligner current_key sur la clé Flash pour que
            # les prochaines re-chiffrements restent en phase avec le serveur.
            ch.current_key = key_idx
            return raw_bytes, None
        key = a_keys[key_idx]
        chk = raw_str[2].upper()
        offset = int(chk, 16) * 2
        decrypted = decypher_data(raw_str[3:], key, offset)
        if not decrypted:
            # Décryption a échoué (clé absente, hex invalide). Pass-through
            # avec alignement du compteur.
            ch.current_key = key_idx
            return raw_bytes, None

        # NOTE: on ne valide PAS le checksum ici. Le serveur a sa propre
        # variante de checksum (cf. _try_decrypt_s2c) et certains messages
        # Flash (EM*/EH* longs : banque, NPC, coffre des héros) ont un
        # checksum qui ne matche pas notre formule alors que la décryption
        # XOR est correcte. Rejeter ces messages = pass-through = désync
        # serveur immédiate. On fait confiance à la décryption XOR.
        if _checksum(decrypted) != chk:
            logger.debug(
                "[relay] C→S Flash#%d checksum mismatch (got=%s our=%s) plain=%r — re-chiffrement quand même",
                key_idx, chk, _checksum(decrypted), decrypted[:60],
            )

        # --- Re-chiffrement avec la prochaine clé du compteur de session ---
        new_key = ch.current_key + 1
        if new_key > len(a_keys) - 1:
            new_key = 1
        if not a_keys[new_key]:
            # Clé indisponible : passer en clair serait pire — garder original
            return raw_bytes, decrypted

        new_key_str = a_keys[new_key]
        # Réutiliser le chk d'origine de Flash plutôt que de le recalculer.
        # Raison : si notre formule _checksum diffère subtilement de celle de
        # Flash pour certains messages (cas observé sur EM*/EH* longs), la
        # recalculer produirait un chk_out qui ne matcherait pas la formule
        # du serveur → kick. Réutiliser le chk Flash garantit que le serveur
        # voit la même valeur de checksum que Flash a calculée, et déchiffre
        # avec le bon offset (= chk * 2).
        chk_out = chk
        offset_out = int(chk_out, 16) * 2
        encrypted_out = cypher_data(decrypted, new_key_str, offset_out)
        reencrypted = "-" + HEX_CHARS[new_key] + chk_out + encrypted_out

        ch.current_key = new_key  # Mettre à jour UNIQUEMENT ici pour C→S Flash

        # Préserver le \n terminal si le message original Flash le contenait.
        # Flash envoie message\n\0 (XMLSocket). La re-chiffrement ne doit pas
        # supprimer ce \n sinon le serveur reçoit un message mal formaté.
        suffix = b"\n" if raw_bytes.endswith(b"\n") else b""
        logger.debug(
            "[relay] C→S re-chiffré Flash#%d→srv#%d plain=%r suffix=%r",
            key_idx, new_key, decrypted[:40], suffix,
        )
        return reencrypted.encode("latin-1") + suffix, decrypted

    except Exception as exc:
        logger.debug("[relay] _reencrypt_c2s échec : %s", exc)
        return raw_bytes, None


def _emit_packet(msg, session) -> None:
    """Pousser un message parsé vers l'onglet Packets de la session.

    Le contenu affiché est le message *déchiffré* (msg_id + payload), pas la
    trame chiffrée. RECV = S→C (reçu par le client), SENT = C→S (envoyé).
    """
    if session is None:
        return
    try:
        content = f"{msg.msg_id}{msg.payload}"
        session.bridge.add_packet({
            "direction": "RECV" if msg.direction == "S→C" else "SENT",
            "msg_id": msg.msg_id,
            "msg_name": msg.msg_name,
            "length": len(content),
            "content": content,
            "pid": session.session_id,
        })
    except Exception:
        pass


BUFFER_SIZE: int = 8192


async def relay(
    reader: StreamReader,
    writer: StreamWriter,
    is_server: bool,
    label: str = "",
    session=None,
) -> None:
    """Relay bidirectionnel.

    S→C : bytes bruts transmis immédiatement (latence minimale).
         Déchiffrement pour parsing/logging uniquement, sans sync _current_key.
    C→S : re-chiffrement transparent avec compteur centralisé avant envoi.
         Le plaintext retourné par _reencrypt_c2s est utilisé directement
         pour le parsing — on n'appelle PAS _try_decrypt qui réinitialiserait
         _current_key avec la clé de Flash.
    """
    direction = "S→C" if is_server else "C→S"
    buffer: bytes = b""

    try:
        while True:
            try:
                data = await reader.read(BUFFER_SIZE)
            except (ConnectionResetError, asyncio.IncompleteReadError):
                break

            if not data:
                break

            buffer += data

            while b"\x00" in buffer:
                raw_bytes, buffer = buffer.split(b"\x00", 1)

                if is_server:
                    # S→C : relayer directement (fritm intercepte la connexion game
                    # au niveau TCP via spawn gating — pas besoin de réécrire AYK)
                    try:
                        writer.write(raw_bytes + b"\x00")
                        await writer.drain()
                    except (ConnectionResetError, BrokenPipeError):
                        logger.warning("[relay:%s] Écriture échouée", label)
                        return

                    try:
                        raw_str = raw_bytes.decode("latin-1")
                        parsed_str = _try_decrypt_s2c(raw_str, session)
                        msg = _parser.parse_message(parsed_str, is_server=True)
                        if parsed_str != raw_str:
                            msg.raw = raw_str
                        _parser.log_message(msg)
                        _emit_packet(msg, session)
                        _parser.dispatch(msg)
                    except Exception as exc:
                        logger.debug("[relay:%s] S→C parse échoué : %s", label, exc)

                else:
                    # C→S : re-chiffrer avec compteur de session avant d'envoyer
                    bytes_to_send, decrypted_plain = _reencrypt_c2s(raw_bytes, session)

                    # Message custom du patch core.swf : dump d'inventaire "ZO...".
                    # Envoyé EN CLAIR par le client patché (préfixe Z non chiffré),
                    # le serveur ne le connaît pas → on le PARSE puis on le DROP
                    # (ne pas forwarder). En clair = pas de compteur de clé avancé.
                    plain_peek = decrypted_plain if decrypted_plain is not None else raw_bytes.decode("latin-1", "replace")
                    if plain_peek.startswith("ZO"):
                        try:
                            msg = _parser.parse_message(plain_peek, is_server=False)
                            _parser.dispatch(msg)
                        except Exception as exc:
                            logger.debug("[relay:%s] ZO parse échoué : %s", label, exc)
                        continue  # NE PAS forwarder au serveur

                    try:
                        writer.write(bytes_to_send + b"\x00")
                        await writer.drain()
                    except (ConnectionResetError, BrokenPipeError):
                        logger.warning("[relay:%s] Écriture échouée", label)
                        return

                    # Parsing : utiliser le plaintext si disponible (évite _try_decrypt
                    # qui réinitialiserait _current_key avec la clé Flash)
                    try:
                        raw_str = raw_bytes.decode("latin-1")
                        parse_str = decrypted_plain if decrypted_plain is not None else raw_str
                        msg = _parser.parse_message(parse_str, is_server=False)
                        if decrypted_plain is not None and decrypted_plain != raw_str:
                            msg.raw = raw_str
                        _parser.log_message(msg)
                        _emit_packet(msg, session)
                        _parser.dispatch(msg)
                    except Exception as exc:
                        logger.debug("[relay:%s] C→S parse échoué : %s", label, exc)

    except Exception as exc:
        logger.error("[relay:%s] %s Exception : %s", label, direction, exc)
    finally:
        try:
            writer.close()
            await writer.wait_closed()
        except Exception:
            pass
        logger.info("[relay:%s] %s Connexion fermée.", label, direction)
