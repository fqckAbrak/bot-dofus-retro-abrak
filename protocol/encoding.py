"""
Encodages propriétaires du protocole Dofus Rétro 1.29.

Référence : retroproto/crypto.go (github.com/kralamoure/retroproto)

Deux alphabets distincts coexistent dans le protocole :
- DOFUS_CHARSET : alphabet interleaved (aAbBcC…) pour encoder les IDs de cellules
  et les chemins de déplacement (Base64 Dofus).
- ZIPKEY       : alphabet alphabétique (abc…ABC…0-9-_) utilisé pour le hash
  du mot de passe et l'encodage du port dans AYK.
"""

import math

# ---------------------------------------------------------------------------
# Alphabets
# ---------------------------------------------------------------------------

DOFUS_CHARSET: str = "aAbBcCdDeEfFgGhHiIjJkKlLmMnNoOpPqQrRsStTuUvVwWxXyYzZ0123456789-_"
"""Alphabet Base64 Dofus (interleaved) — cellules, chemins."""

ZIPKEY: str = "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789-_"
"""Alphabet ZIP Dofus (alphabétique) — hash mdp, encodage port."""

# ---------------------------------------------------------------------------
# Base64 Dofus — encode/decode d'un ID de cellule (2 caractères)
# ---------------------------------------------------------------------------


def decode_cell(encoded: str) -> int:
    """Décoder 2 caractères Base64 Dofus en ID de cellule (0-4095).

    Args:
        encoded: Chaîne de 2 caractères issus de DOFUS_CHARSET.

    Returns:
        ID de cellule entier.
    """
    return DOFUS_CHARSET.index(encoded[0]) * 64 + DOFUS_CHARSET.index(encoded[1])


def encode_cell(cell_id: int) -> str:
    """Encoder un ID de cellule (0-4095) en 2 caractères Base64 Dofus.

    Args:
        cell_id: Identifiant de cellule (0 ≤ cell_id < 4096).

    Returns:
        Chaîne de 2 caractères.
    """
    return DOFUS_CHARSET[cell_id // 64] + DOFUS_CHARSET[cell_id % 64]


def decode_path(encoded: str) -> list[int]:
    """Décoder un chemin encodé en Base64 Dofus (paires de chars) en liste de cellIds.

    Args:
        encoded: Chaîne de longueur paire issue du payload GA001.

    Returns:
        Liste d'IDs de cellule.
    """
    return [
        decode_cell(encoded[i : i + 2])
        for i in range(0, len(encoded) - 1, 2)
    ]


def encode_path(path: list[int]) -> str:
    """Encoder une liste de cellIds en chaîne Base64 Dofus pour GA001.

    Args:
        path: Liste d'IDs de cellule.

    Returns:
        Chaîne de longueur paire.
    """
    return "".join(encode_cell(cell_id) for cell_id in path)


# ---------------------------------------------------------------------------
# Hash du mot de passe (algorithme retroproto / crypto.go)
# ---------------------------------------------------------------------------


def hash_password(password: str, key: str) -> str:
    """Chiffrer le mot de passe avec la clé fournie par HC.

    Algorithme (retroproto/crypto.go — EncryptPassword) :
        Pour chaque caractère du mot de passe :
        - loc8 = floor(ord(char) / 16)   ← nibble haut
        - loc9 = ord(char) % 16          ← nibble bas
        - output += ZIPKEY[(loc8 + ord(key[i]) % 64) % 64]
        - output += ZIPKEY[(loc9 + ord(key[i]) % 64) % 64]

    Args:
        password: Mot de passe en clair.
        key     : Sel aléatoire reçu dans le paquet HC (sans le préfixe "HC").

    Returns:
        Hash encodé en alphabet ZIPKEY (longueur = 2 × len(password)).
    """
    result: list[str] = []
    key_len = len(ZIPKEY)
    for i, char in enumerate(password):
        loc6 = ord(char)
        loc7 = ord(key[i % len(key)])
        loc8 = int(math.floor(loc6 / 16))
        loc9 = loc6 % 16
        result.append(ZIPKEY[(loc8 + loc7 % key_len) % key_len])
        result.append(ZIPKEY[(loc9 + loc7 % key_len) % key_len])
    return "".join(result)


# ---------------------------------------------------------------------------
# Décodage de l'adresse du game server (paquet AYK)
# ---------------------------------------------------------------------------
# Format du payload AYK : {8 chars IP chiffrée}{3 chars port encodé}{ticket}
# Référence : retroproto/crypto.go — SplitEncodedHostPortTicket


def _decode64_zip(ch: str) -> int:
    """Décoder un caractère de l'alphabet ZIPKEY en entier.

    Args:
        ch: Un caractère de ZIPKEY.

    Returns:
        Index (0-63) du caractère dans ZIPKEY.

    Raises:
        ValueError: Si le caractère n'est pas dans ZIPKEY.
    """
    idx = ZIPKEY.find(ch)
    if idx < 0:
        raise ValueError(f"Caractère '{ch}' absent de ZIPKEY")
    return idx


def decrypt_ip(encrypted: str) -> str:
    """Décoder les 8 premiers caractères du payload AYK en adresse IPv4.

    Algorithme :
        Pour chaque paire de chars (c1, c2) :
        - tmp1 = ord(c1) - 48
        - tmp2 = ord(c2) - 48
        - octet = (tmp1 & 0x0F) << 4 | (tmp2 & 0x0F)

    Args:
        encrypted: Chaîne de 8 caractères (les 8 premiers chars du payload AYK).

    Returns:
        Adresse IPv4 en notation pointée, ex : "192.168.1.1".
    """
    if len(encrypted) < 8:
        raise ValueError(f"Chaîne trop courte pour décoder l'IP : {len(encrypted)} chars")
    octets: list[str] = []
    for i in range(0, 8, 2):
        tmp1 = ord(encrypted[i]) - 48
        tmp2 = ord(encrypted[i + 1]) - 48
        octets.append(str((tmp1 & 0x0F) << 4 | tmp2 & 0x0F))
    return ".".join(octets)


def decrypt_port(encoded: str) -> int:
    """Décoder les 3 caractères de port du payload AYK en entier.

    Algorithme (ZIPKEY base-64) :
        port = (n1 & 63) << 12 | (n2 & 63) << 6 | (n3 & 63)

    Args:
        encoded: Chaîne de 3 caractères (chars 8-10 du payload AYK).

    Returns:
        Numéro de port (entier).
    """
    if len(encoded) < 3:
        raise ValueError(f"Chaîne trop courte pour décoder le port : {len(encoded)} chars")
    n1 = _decode64_zip(encoded[0])
    n2 = _decode64_zip(encoded[1])
    n3 = _decode64_zip(encoded[2])
    return (n1 & 63) << 12 | (n2 & 63) << 6 | n3 & 63


# ---------------------------------------------------------------------------
# Chiffrement réseau Dofus Rétro (AK / cypherData / decypherData)
# ---------------------------------------------------------------------------
# Source : core.swf → dofus/aks/Aks.as (décompilé avec FFDec)
# Algorithme : XOR par clé cyclique, données encodées en hex.
#
# prepareData(msg):
#   1. prepareSendPacket(msg) → bool (certains messages sont chiffrés)
#   2. key = _aKeys[_nCurrentKey] (clé préparée)
#   3. checksum = HEX_CHARS[sum(ord(c)%16 for c in msg) % 16]
#   4. output = "-" + HEX_CHARS[_nCurrentKey] + checksum + cypherData(msg, key, offset)
#      où offset = int(checksum, 16) * 2
#
# cypherData(data, key, offset):
#   for i, c in enumerate(data):
#       xb = ord(c) ^ ord(key[(i + offset) % len(key)])
#       output += HEX_CHARS[xb >> 4] + HEX_CHARS[xb & 0xF]
#
# decypherData(hex_data, key, offset):
#   for i in range(0, len(hex_data), 2):
#       byte = int(hex_data[i:i+2], 16)
#       output += chr(byte ^ ord(key[(i//2 + offset) % len(key)]))
#
# prepareKey(hex_string) → convert hex pairs to chars (same as map decryption)

HEX_CHARS: str = "0123456789ABCDEF"


def _checksum(data: str) -> str:
    """Calculer le checksum Dofus (1 char hex) d'une chaîne."""
    total = sum(ord(c) % 16 for c in data)
    return HEX_CHARS[total % 16]


def prepare_key(hex_key: str) -> str:
    """Convertir une clé hex (reçue dans AK) en chaîne de chars.

    Exemple : "48656c6c6f" → "Hello"
    """
    result = []
    for i in range(0, len(hex_key) - 1, 2):
        try:
            result.append(chr(int(hex_key[i:i+2], 16)))
        except ValueError:
            pass
    return "".join(result)


def cypher_data(data: str, key: str, offset: int) -> str:
    """Chiffrer un message (XOR + encodage hex).

    Args:
        data  : Texte en clair à chiffrer.
        key   : Clé (chaîne de chars, résultat de prepare_key).
        offset: Décalage initial dans la clé (= int(checksum, 16) * 2).

    Returns:
        Chaîne hexadécimale représentant le texte chiffré.
    """
    if not key:
        return data
    key_len = len(key)
    result = []
    for i, c in enumerate(data):
        xb = ord(c) ^ ord(key[(i + offset) % key_len])
        result.append(HEX_CHARS[xb >> 4])
        result.append(HEX_CHARS[xb & 0x0F])
    return "".join(result)


def decypher_data(hex_data: str, key: str, offset: int) -> str:
    """Déchiffrer un message (décodage hex + XOR).

    Args:
        hex_data: Texte chiffré (chaîne hexadécimale).
        key     : Clé (chaîne de chars).
        offset  : Décalage initial.

    Returns:
        Texte en clair.
    """
    if not key:
        return hex_data
    key_len = len(key)
    result = []
    for i in range(0, len(hex_data) - 1, 2):
        try:
            byte = int(hex_data[i:i+2], 16)
            result.append(chr(byte ^ ord(key[(i // 2 + offset) % key_len])))
        except ValueError:
            pass
    return "".join(result)


def _should_encrypt(msg: str) -> bool:
    """Vérifie si un message C→S doit être chiffré.

    Reproduit prepareSendPacket() de Aks.as :
    Les messages GA*, GK*, GM doivent être chiffrés.
    """
    if len(msg) < 2:
        return False
    c0, c1 = msg[0], msg[1]
    if c0 == "G":
        return c1 in ("A", "K", "M")
    if c0 in ("W", "e", "O", "D", "F", "K", "z", "w", "S", "B"):
        return True
    if c0 == "A":
        return c1 in ("A", "D", "Z")
    if c0 == "N":
        return c1 in ("A", "R")
    if c0 == "E":
        return c1 in ("V", "R", "s", "A", "K", "P", "S", "B", "Q", "q", "w", "M", "H")
    return False


def prepare_data(msg: str, a_keys: list[str | None], current_key: int) -> tuple[str, int]:
    """Chiffrer un message C→S comme le ferait le client Flash.

    Reproduit prepareData() de Aks.as.

    Args:
        msg        : Message en clair (sans le \\n terminal).
        a_keys     : Liste des clés (_aKeys), indexée 0-15.
        current_key: Index de clé courant (_nCurrentKey).

    Returns:
        Tuple (message_chiffré, nouveau_current_key).
        Si le chiffrement n'est pas applicable, retourne (msg, current_key).
    """
    if not _should_encrypt(msg):
        return msg, current_key

    # Incrémenter et cycler l'index de clé
    new_key = current_key + 1
    if new_key > len(a_keys) - 1:
        new_key = 1
    if new_key <= 0 or new_key >= len(a_keys) or a_keys[new_key] is None:
        return msg, current_key

    key = a_keys[new_key]
    if not key:
        return msg, current_key

    chk = _checksum(msg)
    offset = int(chk, 16) * 2
    encrypted = cypher_data(msg, key, offset)
    result = "-" + HEX_CHARS[new_key] + chk + encrypted
    return result, new_key


def encrypt_ip(ip: str) -> str:
    """Encoder une adresse IPv4 en 8 chars (format AYK).

    Inverse de decrypt_ip : octet → (chr(high+48), chr(low+48)).

    Args:
        ip: Adresse IPv4 en notation pointée, ex : "127.0.0.1".

    Returns:
        Chaîne de 8 caractères.
    """
    octets = [int(x) for x in ip.split(".")]
    result = []
    for octet in octets:
        high = (octet >> 4) & 0x0F
        low = octet & 0x0F
        result.append(chr(high + 48))
        result.append(chr(low + 48))
    return "".join(result)


def encrypt_port(port: int) -> str:
    """Encoder un numéro de port en 3 chars ZIPKEY (format AYK).

    Inverse de decrypt_port.

    Args:
        port: Numéro de port (0 ≤ port < 262144).

    Returns:
        Chaîne de 3 caractères ZIPKEY.
    """
    n1 = (port >> 12) & 63
    n2 = (port >> 6) & 63
    n3 = port & 63
    return ZIPKEY[n1] + ZIPKEY[n2] + ZIPKEY[n3]


def parse_ayk(payload: str) -> tuple[str, int, str]:
    """Parser le payload complet du message AYK.

    Format : {8 chars IP}{3 chars port}{ticket}

    Args:
        payload: Contenu brut après l'ID "AYK".

    Returns:
        Tuple (host, port, ticket).
    """
    if len(payload) < 11:
        raise ValueError(f"Payload AYK trop court : {len(payload)} chars")
    host = decrypt_ip(payload[:8])
    port = decrypt_port(payload[8:11])
    ticket = payload[11:]
    return host, port, ticket
