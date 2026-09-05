"""
Dataclasses pour les messages d'authentification et de sélection de personnage.

Messages couverts :
  ASK  — AccountCharacterSelectedSuccess (S→C) : stats du perso sélectionné
  AlK  — AccountLoginSuccess             (S→C)
  AH   — AccountHosts                   (S→C) : liste des serveurs
  AxK  — AccountServersListSuccess       (S→C)
"""

from __future__ import annotations
from dataclasses import dataclass, field


# ---------------------------------------------------------------------------
# ASK — AccountCharacterSelectedSuccess
# ---------------------------------------------------------------------------


@dataclass
class CharacterInfo:
    """Informations de base du personnage sélectionné (message ASK).

    Format payload (d'après LeafMITM) :
      ASK{id}|{pseudo}|{level}|{class_id}|{sex}|{gfx}|...
    """

    character_id: str
    pseudo: str
    level: int
    class_id: int
    sex: int      # 0 = masculin, 1 = féminin
    gfx: int      # sprite GFX id

    @classmethod
    def parse(cls, payload: str) -> "CharacterInfo":
        """Parser le payload ASK.

        Args:
            payload: Tout ce qui suit 'ASK', ex '12345|MonPerso|130|1|0|100|...'
        """
        parts = payload.split("|")
        # Le payload commence par '|' → parts[0] est vide, on le saute
        if parts and parts[0] == "":
            parts = parts[1:]
        if len(parts) < 6:
            raise ValueError(f"Payload ASK invalide ({len(parts)} champs) : {payload!r}")
        return cls(
            character_id=parts[0],
            pseudo=parts[1],
            level=int(parts[2]) if parts[2].isdigit() else 0,
            class_id=int(parts[3]) if parts[3].isdigit() else 0,
            sex=int(parts[4]) if parts[4].isdigit() else 0,
            gfx=int(parts[5]) if parts[5].isdigit() else 0,
        )

    def __str__(self) -> str:
        return f"{self.pseudo} Lv{self.level} (class={self.class_id}, id={self.character_id})"


# ---------------------------------------------------------------------------
# AH — AccountHosts (liste des serveurs disponibles)
# ---------------------------------------------------------------------------


@dataclass
class ServerInfo:
    """Un serveur dans la liste AH.

    Format d'un champ : {id};{online};{capacity};{chars_count};{status}
    """

    server_id: int
    online: bool
    capacity: int
    chars_count: int
    status: str

    @classmethod
    def parse(cls, field: str) -> "ServerInfo":
        parts = field.split(";")
        return cls(
            server_id=int(parts[0]) if parts[0].isdigit() else 0,
            online=parts[1] == "1" if len(parts) > 1 else False,
            capacity=int(parts[2]) if len(parts) > 2 and parts[2].isdigit() else 0,
            chars_count=int(parts[3]) if len(parts) > 3 and parts[3].isdigit() else 0,
            status=parts[4] if len(parts) > 4 else "",
        )


@dataclass
class ServerList:
    """Liste des serveurs reçue via AH.

    Format payload : {s1}|{s2}|...
    """

    servers: list[ServerInfo] = field(default_factory=list)

    @classmethod
    def parse(cls, payload: str) -> "ServerList":
        obj = cls()
        for part in payload.split("|"):
            if not part or not part[0].isdigit():
                continue
            try:
                obj.servers.append(ServerInfo.parse(part))
            except (ValueError, IndexError):
                pass
        return obj

    def online_servers(self) -> list[ServerInfo]:
        return [s for s in self.servers if s.online]
