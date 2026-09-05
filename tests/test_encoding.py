"""
Tests unitaires pour protocol/encoding.py.

Lance avec : python -m pytest tests/ -v
"""

import pytest
from protocol.encoding import (
    DOFUS_CHARSET,
    ZIPKEY,
    decode_cell,
    encode_cell,
    decode_path,
    encode_path,
    hash_password,
    decrypt_ip,
    decrypt_port,
    parse_ayk,
)


# ---------------------------------------------------------------------------
# Base64 Dofus — encode_cell / decode_cell
# ---------------------------------------------------------------------------


class TestCellEncoding:
    """Tests de l'encodage/décodage d'IDs de cellule (Base64 Dofus)."""

    def test_decode_first_cell(self) -> None:
        """La cellule 0 est encodée 'aa'."""
        assert decode_cell("aa") == 0

    def test_decode_second_cell(self) -> None:
        """La cellule 1 est encodée 'aA'."""
        assert decode_cell("aA") == 1

    def test_decode_64(self) -> None:
        """64 = 1*64 + 0 → 'Aa'."""
        assert decode_cell("Aa") == 64

    def test_decode_max(self) -> None:
        """Cellule max = 63*64 + 63 = 4095 → '__'."""
        assert decode_cell("__") == 4095

    def test_roundtrip_zero(self) -> None:
        assert decode_cell(encode_cell(0)) == 0

    def test_roundtrip_one(self) -> None:
        assert decode_cell(encode_cell(1)) == 1

    def test_roundtrip_255(self) -> None:
        assert decode_cell(encode_cell(255)) == 255

    def test_roundtrip_4095(self) -> None:
        assert decode_cell(encode_cell(4095)) == 4095

    def test_roundtrip_all_cells(self) -> None:
        """Round-trip complet pour toutes les cellules possibles (0-4095)."""
        for cell_id in range(4096):
            assert decode_cell(encode_cell(cell_id)) == cell_id, f"Échec pour cell_id={cell_id}"

    def test_encode_cell_0(self) -> None:
        assert encode_cell(0) == "aa"

    def test_encode_cell_1(self) -> None:
        assert encode_cell(1) == "aA"

    def test_encode_cell_64(self) -> None:
        assert encode_cell(64) == "Aa"

    def test_encode_output_length(self) -> None:
        """encode_cell produit toujours 2 caractères."""
        for cell_id in range(0, 4096, 100):
            assert len(encode_cell(cell_id)) == 2


# ---------------------------------------------------------------------------
# Chemin (path) encode/decode
# ---------------------------------------------------------------------------


class TestPathEncoding:
    """Tests d'encodage/décodage de chemins de déplacement."""

    def test_empty_path(self) -> None:
        assert encode_path([]) == ""
        assert decode_path("") == []

    def test_single_cell(self) -> None:
        encoded = encode_path([42])
        assert decode_path(encoded) == [42]

    def test_multi_cell_roundtrip(self) -> None:
        path = [0, 64, 128, 255, 300, 4095]
        assert decode_path(encode_path(path)) == path

    def test_path_length_is_double(self) -> None:
        """Un chemin de N cellules produit 2N caractères."""
        path = [1, 2, 3, 4, 5]
        assert len(encode_path(path)) == 2 * len(path)


# ---------------------------------------------------------------------------
# hash_password
# ---------------------------------------------------------------------------


class TestHashPassword:
    """Tests du hachage de mot de passe (algorithme retroproto/crypto.go)."""

    def test_output_length(self) -> None:
        """Le hash fait exactement 2× la longueur du mot de passe."""
        pwd = "azerty"
        key = "abcdefghijklmnopqrstuvwxyz"
        result = hash_password(pwd, key)
        assert len(result) == 2 * len(pwd)

    def test_output_alphabet(self) -> None:
        """Chaque caractère du hash est dans ZIPKEY."""
        pwd = "password123"
        key = "abcdefghijklmnopqrstuvwxyz"
        for ch in hash_password(pwd, key):
            assert ch in ZIPKEY, f"Caractère '{ch}' hors ZIPKEY"

    def test_deterministic(self) -> None:
        """Même entrée → même sortie."""
        pwd = "test"
        key = "sel123"
        assert hash_password(pwd, key) == hash_password(pwd, key)

    def test_different_keys_produce_different_hashes(self) -> None:
        pwd = "dofus"
        assert hash_password(pwd, "aaaaaa") != hash_password(pwd, "bbbbbb")

    def test_different_passwords_produce_different_hashes(self) -> None:
        key = "mysecretkey"
        assert hash_password("password1", key) != hash_password("password2", key)

    def test_known_value_simple(self) -> None:
        """Valeur calculée manuellement avec l'algo retroproto.

        password = "a" (ord=97)  key = "a" (ord=97)
        loc8 = floor(97/16) = 6
        loc9 = 97 % 16 = 1
        loc7 = 97
        ZIPKEY[(6 + 97%64) % 64] = ZIPKEY[(6+33)%64] = ZIPKEY[39] = 'N'
        ZIPKEY[(1 + 97%64) % 64] = ZIPKEY[(1+33)%64] = ZIPKEY[34] = 'I'
        → "NI"
        """
        result = hash_password("a", "a")
        assert result == "NI"

    def test_key_cycles(self) -> None:
        """La clé est utilisée en cycle si plus courte que le mot de passe."""
        # Ne doit pas lever d'exception
        result = hash_password("longpassword", "key")
        assert len(result) == 2 * len("longpassword")


# ---------------------------------------------------------------------------
# decrypt_ip
# ---------------------------------------------------------------------------


class TestDecryptIp:
    """Tests du décodage d'IP depuis AYK."""

    def test_known_ip(self) -> None:
        """IP 34.251.172.139 encodée manuellement.

        Algorithme : chaque octet → 2 chars, chaque char = nibble + 48
        octet=34  → nibble_hi=2, nibble_lo=2 → chars chr(50)chr(50) = '22'
        octet=251 → nibble_hi=15, nibble_lo=11 → chars chr(63)chr(59) = '?;'
        octet=172 → nibble_hi=10, nibble_lo=12 → chars chr(58)chr(60) = ':<'
        octet=139 → nibble_hi=8,  nibble_lo=11 → chars chr(56)chr(59) = '8;'
        → '22?;<:8;'  → decode → 34.251.172.139

        Ré-encodage des nibbles : hi = (octet >> 4) + 48, lo = (octet & 15) + 48
        """
        def encode_ip(ip: str) -> str:
            """Helper : encoder une IP en 8 chars pour le test inverse."""
            parts = [int(x) for x in ip.split(".")]
            encoded = ""
            for b in parts:
                hi = (b >> 4) + 48
                lo = (b & 15) + 48
                encoded += chr(hi) + chr(lo)
            return encoded

        ip = "34.251.172.139"
        encoded = encode_ip(ip)
        assert decrypt_ip(encoded) == ip

    def test_roundtrip_loopback(self) -> None:
        """127.0.0.1 encodé/décodé."""
        def encode_ip(ip: str) -> str:
            parts = [int(x) for x in ip.split(".")]
            return "".join(chr((b >> 4) + 48) + chr((b & 15) + 48) for b in parts)

        ip = "127.0.0.1"
        assert decrypt_ip(encode_ip(ip)) == ip

    def test_too_short_raises(self) -> None:
        with pytest.raises(ValueError):
            decrypt_ip("short")


# ---------------------------------------------------------------------------
# decrypt_port
# ---------------------------------------------------------------------------


class TestDecryptPort:
    """Tests du décodage de port depuis AYK."""

    def test_port_443(self) -> None:
        """Port 443 encodé en Base64 ZIP (3 chars).

        443 = 0b000_000110_111011
        n1 = 0b000000 = 0  → ZIPKEY[0] = 'a'
        n2 = 0b000110 = 6  → ZIPKEY[6] = 'g'
        n3 = 0b111011 = 59 → ZIPKEY[59] = '7'
        → 'ag7'
        """
        # Vérification de la formule inverse
        from protocol.encoding import ZIPKEY
        port = 443
        n3 = port & 63
        n2 = (port >> 6) & 63
        n1 = (port >> 12) & 63
        encoded = ZIPKEY[n1] + ZIPKEY[n2] + ZIPKEY[n3]
        assert decrypt_port(encoded) == port

    def test_port_5555(self) -> None:
        from protocol.encoding import ZIPKEY
        port = 5555
        n3 = port & 63
        n2 = (port >> 6) & 63
        n1 = (port >> 12) & 63
        encoded = ZIPKEY[n1] + ZIPKEY[n2] + ZIPKEY[n3]
        assert decrypt_port(encoded) == port

    def test_port_roundtrip(self) -> None:
        """Round-trip pour plusieurs ports."""
        from protocol.encoding import ZIPKEY
        for port in [80, 443, 5555, 8080, 65535]:
            n3 = port & 63
            n2 = (port >> 6) & 63
            n1 = (port >> 12) & 63
            encoded = ZIPKEY[n1] + ZIPKEY[n2] + ZIPKEY[n3]
            assert decrypt_port(encoded) == port, f"Échec port={port}"

    def test_too_short_raises(self) -> None:
        with pytest.raises(ValueError):
            decrypt_port("ab")


# ---------------------------------------------------------------------------
# parse_ayk
# ---------------------------------------------------------------------------


class TestParseAyk:
    """Tests du parsing complet du payload AYK."""

    def _build_ayk_payload(self, ip: str, port: int, ticket: str) -> str:
        """Construire un payload AYK valide pour les tests."""
        # Encoder l'IP
        parts = [int(x) for x in ip.split(".")]
        encoded_ip = "".join(chr((b >> 4) + 48) + chr((b & 15) + 48) for b in parts)
        # Encoder le port
        n3 = port & 63
        n2 = (port >> 6) & 63
        n1 = (port >> 12) & 63
        encoded_port = ZIPKEY[n1] + ZIPKEY[n2] + ZIPKEY[n3]
        return encoded_ip + encoded_port + ticket

    def test_parse_ayk_correct(self) -> None:
        ip = "192.168.1.50"
        port = 5555
        ticket = "abc123xyz"
        payload = self._build_ayk_payload(ip, port, ticket)
        h, p, t = parse_ayk(payload)
        assert h == ip
        assert p == port
        assert t == ticket

    def test_parse_ayk_short_raises(self) -> None:
        with pytest.raises(ValueError):
            parse_ayk("short")

    def test_parse_ayk_ticket_can_be_empty(self) -> None:
        ip = "10.0.0.1"
        port = 443
        payload = self._build_ayk_payload(ip, port, "")
        h, p, t = parse_ayk(payload)
        assert h == ip
        assert p == port
        assert t == ""
