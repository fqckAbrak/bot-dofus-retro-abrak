"""
Base des modèles de montures Dofus Rétro (lang ``rides_fr`` du CDN Abrak).

Table statique extraite de https://cdn.abrak.fr/lang/swf/rides_fr_145.swf
(décompilé JPEXS : ``RI[modelId] = {n:"<nom>", g:"<gfx>", ...}``).

Le modèle (``ModelId``) est le 2e champ du message ``Rd`` (MountData) — c'est
lui qui détermine la couleur d'une dragodinde. Utilisé par bot/hdv.py pour la
recherche de dragodindes non castrées à l'hôtel de vente.
"""

from __future__ import annotations

MOUNT_MODELS: dict[int, str] = {
    1:   "Dragodinde Amande Sauvage",
    3:   "Dragodinde Ebène",
    6:   "Dragodinde Rousse Sauvage",
    9:   "Dragodinde Ebène et Ivoire",
    10:  "Dragodinde Rousse",
    11:  "Dragodinde Ivoire et Rousse",
    12:  "Dragodinde Ebène et Rousse",
    15:  "Dragodinde Turquoise",
    16:  "Dragodinde Ivoire",
    17:  "Dragodinde Indigo",
    18:  "Dragodinde Dorée",
    19:  "Dragodinde Pourpre",
    20:  "Dragodinde Amande",
    21:  "Dragodinde Emeraude",
    22:  "Dragodinde Orchidée",
    23:  "Dragodinde Prune",
    33:  "Dragodinde Amande et Dorée",
    34:  "Dragodinde Amande et Ebène",
    35:  "Dragodinde Amande et Emeraude",
    36:  "Dragodinde Amande et Indigo",
    37:  "Dragodinde Amande et Ivoire",
    38:  "Dragodinde Amande et Rousse",
    39:  "Dragodinde Amande et Turquoise",
    40:  "Dragodinde Amande et Orchidée",
    41:  "Dragodinde Amande et Pourpre",
    42:  "Dragodinde Dorée et Ebène",
    43:  "Dragodinde Dorée et Emeraude",
    44:  "Dragodinde Dorée et Indigo",
    45:  "Dragodinde Dorée et Ivoire",
    46:  "Dragodinde Dorée et Rousse",
    47:  "Dragodinde Dorée et Turquoise",
    48:  "Dragodinde Dorée et Orchidée",
    49:  "Dragodinde Dorée et Pourpre",
    50:  "Dragodinde Ebène et Emeraude",
    51:  "Dragodinde Ebène et Indigo",
    52:  "Dragodinde Ebène et Turquoise",
    53:  "Dragodinde Ebène et Orchidée",
    54:  "Dragodinde Ebène et Pourpre",
    55:  "Dragodinde Emeraude et Indigo",
    56:  "Dragodinde Emeraude et Ivoire",
    57:  "Dragodinde Emeraude et Rousse",
    58:  "Dragodinde Emeraude et Turquoise",
    59:  "Dragodinde Emeraude et Orchidée",
    60:  "Dragodinde Emeraude et Pourpre",
    61:  "Dragodinde Indigo et Ivoire",
    62:  "Dragodinde Indigo et Rousse",
    63:  "Dragodinde Indigo et Turquoise",
    64:  "Dragodinde Indigo et Orchidée",
    65:  "Dragodinde Indigo et Pourpre",
    66:  "Dragodinde Ivoire et Turquoise",
    67:  "Dragodinde Ivoire et Orchidée",
    68:  "Dragodinde Ivoire et Pourpre",
    69:  "Dragodinde Turquoise et Rousse",
    70:  "Dragodinde Orchidée et Rousse",
    71:  "Dragodinde Pourpre et Rousse",
    72:  "Dragodinde Turquoise et Orchidée",
    73:  "Dragodinde Turquoise et Pourpre",
    74:  "Dragodinde Dorée Sauvage",
    75:  "Dragodinde Squelette",
    76:  "Dragodinde Orchidée et Pourpre",
    77:  "Dragodinde Prune et Amande",
    78:  "Dragodinde Prune et Dorée",
    79:  "Dragodinde Prune et Ebène",
    80:  "Dragodinde Prune et Emeraude",
    82:  "Dragodinde Prune et Indigo",
    83:  "Dragodinde Prune et Ivoire",
    84:  "Dragodinde Prune et Rousse",
    85:  "Dragodinde Prune et Turquoise",
    86:  "Dragodinde Prune et Orchidée",
    87:  "Dragodinde Prune et Pourpre",
    88:  "Dragodinde en armure",
    89:  "Dragodinde du Paladin",
    90:  "Tabi",
    91:  "Karnage",
    92:  "Dragodinde Ascensionnel",
    93:  "Grougalorasalar",
    94:  "Dardondakal",
    95:  "Aerafal",
    96:  "Aguabrial",
    97:  "Terrakourial",
    98:  "Ignemikhal",
    99:  "Hurledent",
    133: "Dragodinde Squelette du Magma Noir",
    145: "Dragodinde Gonflable",
}

# Modèles non élevables (sauvages, squelettes, skins boutique) — exclus du
# dropdown de recherche HDV même si leur nom commence par « Dragodinde ».
_NON_ELEVAGE = frozenset({1, 6, 74, 75, 88, 89, 92, 133, 145})

# Dragodindes d'élevage (celles qui existent en certificat à l'HDV).
DRAGODINDE_MODELS: dict[int, str] = {
    mid: name for mid, name in MOUNT_MODELS.items()
    if name.startswith("Dragodinde") and mid not in _NON_ELEVAGE
}


def get_model_name(model_id: int) -> str:
    """Nom lisible d'un modèle de monture (fallback ``Monture #id``)."""
    return MOUNT_MODELS.get(model_id, f"Monture #{model_id}")
