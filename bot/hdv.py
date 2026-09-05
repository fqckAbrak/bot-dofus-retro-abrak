"""
Recherche de dragodindes non castrées à l'hôtel de vente (bigstore).

Flux réseau (reproduit le client Flash, cf. logs/session_20260702_120510.log) :

    C→S  ER11|0             ouvrir l'HDV (type 11 ; sur ce serveur : partout)
    S→C  ECK11|…            HDV ouvert (débloque wait_exchange_open)
    C→S  EHT97              catégorie 97 = certificats de monture
    S→C  EHL97|{gid;gid;…}  gids des certificats actuellement en vente
    C→S  EHl{gid}           demander les offres pour un certificat
    S→C  EHl{gid}|{uid;effets;prix};;;{gid}|…   une entrée par offre
    C→S  Rd{mountId}|undefined   fiche de la monture (= clic sur le certificat)
    S→C  Rd{données}        cf. retroproto — typ/commonmountdata.go
                            (github.com/kralamoure/retroproto)
    C→S  EV                 fermer l'HDV

L'id de la monture d'un certificat est porté par l'effet 995 (0x3e3), param1
en hexadécimal. Le modèle (couleur) est le champ 2 du Rd ; une monture castrée
a ``Reproductions < 0`` (dernier champ du Rd) — une non castrée en vente est
la trouvaille rare recherchée.

Le mapping gid de certificat → modèle de dragodinde n'est pas dans items.json
(noms vides) : il est appris au fil des scans via le Rd de la première offre
de chaque gid, puis mis en cache (ressources/hdv_cert_models.json) pour ne
plus re-inspecter les certificats des mauvaises couleurs.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import random
import time
from dataclasses import dataclass, field

from bot import channel as _channel
from data import mounts_db
from game.state import current

logger = logging.getLogger(__name__)

# Type d'échange « achat HDV » et type d'item « certificat de monture ».
EXCHANGE_TYPE_BIGSTORE = 11
ITEM_TYPE_MOUNT_CERT = 97

# Effet reliant un certificat à sa monture (id d'effet en hex dans le flux).
EFFECT_MOUNT_ID_HEX = "3e3"   # 995

from core.paths import RESSOURCES_DIR

_CACHE_PATH = str(RESSOURCES_DIR / "hdv_cert_models.json")

# Délais entre requêtes (mime un joueur qui parcourt l'HDV, évite le flood).
_DELAY_MIN, _DELAY_MAX = 0.15, 0.35
_RESPONSE_TIMEOUT = 8.0


@dataclass
class MountOffer:
    """Une offre de certificat à l'HDV, enrichie par la fiche Rd."""
    cert_gid: int
    cert_uid: int
    price: int
    mount_id: int
    model_id: int = -1
    model_name: str = ""
    name: str = ""
    level: int = 0
    sex: int = -1                       # 0 = mâle, 1 = femelle
    reproductions: int | None = None    # < 0 = castrée
    reproductions_max: int | None = None
    fecundable: bool = False

    @property
    def castrated(self) -> bool | None:
        """True/False si connu, None si la fiche Rd n'a pas pu être lue."""
        if self.reproductions is None:
            return None
        return self.reproductions < 0

    @property
    def sex_label(self) -> str:
        return {0: "Mâle", 1: "Femelle"}.get(self.sex, "?")


# ---------------------------------------------------------------------------
# Parsing
# ---------------------------------------------------------------------------

def _mount_id_from_effects(effects: str) -> int:
    """Extraire l'id de monture (effet 995 / 0x3e3, param1 hex) d'un certificat."""
    for eff in effects.split(","):
        parts = eff.split("#")
        if len(parts) >= 2 and parts[0].strip().lower() == EFFECT_MOUNT_ID_HEX:
            try:
                return int(parts[1], 16)
            except ValueError:
                return -1
    return -1


def parse_ehl_offers(payload: str) -> list[MountOffer]:
    """Décoder un EHl S→C : ``{gid}|{uid;effets;prix};;;{gid}|{uid;…}…``

    Sur ce serveur les entrées sont séparées par ``;;;`` et chacune répète le
    gid devant l'uid (``7814|909;…``). Le champ prix observé est ``{prix},0``
    (certificats = lot de 1) : on prend la première valeur positive.
    """
    gid_str, _, rest = payload.partition("|")
    try:
        gid = int(gid_str.strip())
    except ValueError:
        logger.warning("[hdv] EHl : gid illisible dans %r", payload[:60])
        return []

    offers: list[MountOffer] = []
    for chunk in rest.split(";;;"):
        chunk = chunk.strip(";")
        if not chunk:
            continue
        # gid répété devant l'uid : "7814|909;effets;prix"
        head = chunk.split(";", 1)[0]
        if "|" in head:
            chunk = chunk.split("|", 1)[1]
        parts = chunk.split(";")
        if len(parts) < 3:
            continue
        uid_str = parts[0].strip()
        if not uid_str.lstrip("-").isdigit():
            continue

        price = 0
        for tok in parts[2].split(","):
            tok = tok.strip()
            if tok.isdigit() and int(tok) > 0:
                price = int(tok)
                break

        offers.append(MountOffer(
            cert_gid=gid,
            cert_uid=int(uid_str),
            price=price,
            mount_id=_mount_id_from_effects(parts[1]),
        ))
    return offers


def parse_mount_data(payload: str) -> dict | None:
    """Décoder un Rd S→C (cf. retroproto typ/commonmountdata.go).

    Champs séparés par ':' :
      0 id, 1 modèle, 2 ancêtres, 3 capacités, 4 nom, 5 sexe, 6 xp, 7 niveau,
      8 montable, 9 pods, 10 sauvage, 11 endurance, 12 maturité, 13 énergie,
      14 sérénité, 15 amour, 16 fécondation, 17 fécondable, 18 effets,
      19 fatigue, 20 « reproductions,reproductionsMax ».

    Les derniers champs sont lus depuis la FIN (f[-1]…) : le champ effets (18)
    pourrait en théorie contenir ':' — les reproductions restent alors justes.

    Sur ce serveur le Rd se termine par un ':' (champ vide final, cf. log
    session_20260702_123212) : on retire les champs vides de fin avant de lire
    les reproductions, sinon toutes les fiches paraissent incomplètes.
    """
    f = payload.split(":")
    while f and not f[-1].strip():
        f.pop()
    if len(f) < 8:
        return None

    def _int(s: str, default: int = 0) -> int:
        s = s.strip()
        return int(s) if s.lstrip("-").isdigit() else default

    data = {
        "id": abs(_int(f[0])),
        "model_id": _int(f[1], -1),
        "name": f[4] if len(f) > 4 else "",
        "sex": _int(f[5], -1) if len(f) > 5 else -1,
        "level": _int(f[7]) if len(f) > 7 else 0,
        "reproductions": None,
        "reproductions_max": None,
        "fecundable": False,
    }
    if len(f) >= 21:
        repro = f[-1].split(",")
        data["reproductions"] = _int(repro[0], None) if repro[0].strip() else None
        if len(repro) > 1:
            data["reproductions_max"] = _int(repro[1], None)
        data["fecundable"] = f[-4].strip() == "1"
    return data


# ---------------------------------------------------------------------------
# Cache gid certificat → modèle de dragodinde
# ---------------------------------------------------------------------------

# Mapping appris lors du scan du 02/07/2026 (serveur Abrak) — embarqué pour
# éviter la passe de découverte (1 EHl + 1 Rd par gid inconnu) au premier scan.
# Les gids absents (nouveaux certificats mis en vente) restent découverts et
# mémorisés dans le cache disque.
DEFAULT_CERT_MODELS: dict[int, int] = {
    7808: 3, 7810: 9, 7811: 10, 7814: 15, 7815: 16, 7816: 17, 7817: 18,
    7818: 19, 7819: 20, 7820: 21, 7821: 22, 7822: 23, 7824: 34, 7825: 35,
    7826: 36, 7828: 38, 7830: 40, 7831: 41, 7832: 42, 7833: 43, 7834: 44,
    7836: 46, 7838: 48, 7839: 49, 7840: 50, 7841: 51, 7842: 52, 7843: 53,
    7844: 54, 7845: 55, 7846: 56, 7847: 57, 7848: 58, 7849: 59, 7850: 60,
    7851: 61, 7853: 63, 7854: 64, 7855: 65, 7856: 66, 7857: 67, 7858: 68,
    7859: 69, 7862: 72, 7863: 73, 7866: 76, 7867: 77, 7868: 78, 7869: 79,
    7870: 80, 7871: 82, 7872: 83, 7874: 85, 7875: 86, 7876: 87,
    # Montures exotiques (hors élevage) croisées à l'HDV :
    9582: 88, 12776: 89, 12780: 90, 41261: 91, 121001: 99,
    43755: 111, 43757: 113, 43758: 114, 43759: 115, 43760: 116,
}


def _load_cert_cache() -> dict[int, int]:
    cache = dict(DEFAULT_CERT_MODELS)
    try:
        with open(_CACHE_PATH, "r", encoding="utf-8") as fh:
            cache.update({int(k): int(v) for k, v in json.load(fh).items()})
    except (FileNotFoundError, json.JSONDecodeError, ValueError):
        pass
    return cache


def _save_cert_cache(cache: dict[int, int]) -> None:
    try:
        with open(_CACHE_PATH, "w", encoding="utf-8") as fh:
            json.dump({str(k): v for k, v in cache.items()}, fh, indent=1)
    except OSError as exc:
        logger.warning("[hdv] Impossible d'écrire le cache %s : %s", _CACHE_PATH, exc)


# ---------------------------------------------------------------------------
# Primitives réseau : envoyer une requête et attendre sa réponse
# ---------------------------------------------------------------------------

async def _request(event: asyncio.Event, msg: str, timeout: float = _RESPONSE_TIMEOUT) -> bool:
    """Clear l'event, envoyer le message, attendre la réponse. False si timeout.

    L'event est réarmé AVANT l'envoi pour ne pas perdre une réponse rapide.
    """
    event.clear()
    if not await _channel.send(msg):
        return False
    try:
        await asyncio.wait_for(event.wait(), timeout)
        return True
    except asyncio.TimeoutError:
        return False


async def _fetch_mount(mount_id: int) -> dict | None:
    """Demander la fiche Rd d'une monture et la décoder (None si timeout)."""
    if not await _request(current._mount_data_event, f"Rd{mount_id}|undefined\n"):
        return None
    data = parse_mount_data(current._mount_data_payload)
    if data and data["id"] != mount_id:
        logger.warning("[hdv] Rd inattendu : demandé %d, reçu %d", mount_id, data["id"])
    return data


async def _pace() -> None:
    await asyncio.sleep(random.uniform(_DELAY_MIN, _DELAY_MAX))


# ---------------------------------------------------------------------------
# Scan principal
# ---------------------------------------------------------------------------

async def scan_dragodindes(
    model_ids: set[int] | None = None,
    *,
    log=None,
) -> dict:
    """Scanner l'HDV à la recherche de dragodindes non castrées.

    Args:
        model_ids: modèles de dragodinde recherchés (cf. data/mounts_db.py).
            None ou vide = toutes les couleurs.
        log: callback optionnel ``log(str)`` pour le journal live de l'UI.

    Returns:
        dict : {ok, reason, gids_total, gids_scanned, mounts_scanned,
                found: [MountOffer…], duration}.
    """
    def _emit(message: str) -> None:
        logger.info("[hdv] %s", message)
        if log:
            try:
                log(message)
            except Exception:
                pass

    def _fail(reason: str) -> dict:
        _emit(reason)
        return {"ok": False, "reason": reason, "gids_total": 0, "gids_scanned": 0,
                "mounts_scanned": 0, "found": [], "duration": 0.0}

    if not _channel.is_connected():
        return _fail("Pas connecté au serveur")

    wanted = set(model_ids) if model_ids else None
    current._hdv_cancel = False
    started = time.monotonic()

    # 1. Ouvrir l'HDV (ER 11|0). L'event est réarmé avant l'envoi (anti-race).
    if not current._exchange_is_open:
        current._exchange_open_event.clear()
        await _channel.send(f"ER{EXCHANGE_TYPE_BIGSTORE}|0\n")
        try:
            await asyncio.wait_for(current._exchange_open_event.wait(), _RESPONSE_TIMEOUT)
        except asyncio.TimeoutError:
            return _fail("L'HDV ne s'est pas ouvert (timeout ECK)")
    _emit("HDV ouvert")

    found: list[MountOffer] = []
    gids_scanned = 0
    mounts_scanned = 0
    cache = _load_cert_cache()

    try:
        # 2. Lister les certificats de monture en vente (EHT 97 → EHL).
        if not await _request(current._bigstore_type_event,
                              f"EHT{ITEM_TYPE_MOUNT_CERT}\n"):
            return _fail("Pas de réponse EHL à la demande de catégorie (timeout)")
        if current._bigstore_type_id != ITEM_TYPE_MOUNT_CERT:
            _emit(f"Avertissement : EHL reçu pour le type {current._bigstore_type_id} "
                  f"(attendu {ITEM_TYPE_MOUNT_CERT})")
        gids = list(current._bigstore_type_gids)
        _emit(f"{len(gids)} certificat(s) de monture en vente à l'HDV")

        # 3. Parcourir chaque gid de certificat.
        for i, gid in enumerate(gids, 1):
            if current._hdv_cancel:
                _emit("Scan interrompu par l'utilisateur")
                break

            cached_model = cache.get(gid)
            if wanted and cached_model is not None and cached_model not in wanted:
                continue    # certificat d'une autre couleur (connu du cache)

            await _pace()
            if not await _request(current._bigstore_list_event, f"EHl{gid}\n"):
                _emit(f"[{i}/{len(gids)}] gid {gid} : pas de réponse EHl (timeout) — ignoré")
                continue
            offers = parse_ehl_offers(current._bigstore_list_payload)
            gids_scanned += 1

            label = mounts_db.get_model_name(cached_model) if cached_model else f"gid {gid}"
            _emit(f"[{i}/{len(gids)}] {label} : {len(offers)} offre(s)")

            # 4. Inspecter chaque monture (Rd) — la 1re révèle le modèle du gid.
            for offer in offers:
                if current._hdv_cancel:
                    break
                if offer.mount_id <= 0:
                    logger.warning("[hdv] Offre sans id de monture : uid %d (gid %d)",
                                   offer.cert_uid, gid)
                    continue

                await _pace()
                data = await _fetch_mount(offer.mount_id)
                if data is None:
                    _emit(f"    monture {offer.mount_id} : pas de réponse Rd — ignorée")
                    continue
                mounts_scanned += 1

                offer.model_id = data["model_id"]
                offer.model_name = mounts_db.get_model_name(data["model_id"])
                offer.name = data["name"]
                offer.level = data["level"]
                offer.sex = data["sex"]
                offer.reproductions = data["reproductions"]
                offer.reproductions_max = data["reproductions_max"]
                offer.fecundable = data["fecundable"]

                if data["model_id"] > 0 and cache.get(gid) != data["model_id"]:
                    cache[gid] = data["model_id"]

                # Mauvaise couleur → inutile d'inspecter les autres offres du gid
                # (un gid de certificat = un seul modèle de dragodinde).
                if wanted and data["model_id"] not in wanted:
                    _emit(f"    → {offer.model_name} : hors filtre, gid ignoré")
                    break

                if offer.castrated is False:
                    found.append(offer)
                    _emit(f"    ★ NON CASTRÉE : {offer.model_name} « {offer.name} » "
                          f"niv.{offer.level} {offer.sex_label} — {offer.price:,} kamas "
                          f"(repro {offer.reproductions}/{offer.reproductions_max})".replace(",", " "))
                elif offer.castrated is None:
                    _emit(f"    ? fiche incomplète pour {offer.model_name} "
                          f"« {offer.name} » (Rd : {current._mount_data_payload[:80]}…)")
    finally:
        _save_cert_cache(cache)
        # 5. Fermer l'HDV proprement (EV), même en cas d'erreur/interruption.
        current._exchange_closed_event.clear()
        await _channel.send("EV\n")
        try:
            await asyncio.wait_for(current._exchange_closed_event.wait(), 5.0)
        except asyncio.TimeoutError:
            _emit("Avertissement : fermeture HDV (EV) non confirmée")

    duration = time.monotonic() - started
    _emit(f"Scan terminé en {duration:.0f}s : {len(found)} dragodinde(s) non castrée(s) "
          f"({mounts_scanned} monture(s) inspectée(s), {gids_scanned} certificat(s))")
    return {
        "ok": True,
        "reason": "",
        "gids_total": len(gids),
        "gids_scanned": gids_scanned,
        "mounts_scanned": mounts_scanned,
        "found": found,
        "duration": duration,
    }


def cancel_scan(session) -> None:
    """Demander l'arrêt du scan en cours (appelé depuis le thread tkinter)."""
    try:
        session.game_state._hdv_cancel = True
    except Exception:
        pass
