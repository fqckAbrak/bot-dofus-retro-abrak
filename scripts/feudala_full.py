"""Récolte Feudala Full — 8 maps en boucle avec 2 impasses (8471, 7891)"""

# Laisser vide pour récolter tous les types disponibles.
# Remplir pour filtrer : ex. {161} pour la Dolomite uniquement.
ELEMENTS_TO_GATHER: set[int] = set()

# Laisser vide pour désactiver la priorité.
# Remplir avec les elem_types dans l'ordre de priorité.
PRIORITY_ELEMENTS: list[int] = [161, 162]  # Dolomite en priorité, puis Silicate

MAX_PODS: int = 0  # Pas de retour banque automatique (non implémenté)


def move() -> list[dict]:
    """
    Cycle Feudala Full :
      7899 → 8341 → 8471 (impasse) → 8341 → 8343 → 8353 → 8352 → 8346 → 7891 (impasse) → 8346 → 8343 → 8341 → 7899
    """
    return [
        {"map": 7899, "changeMap": "top",    "gather": True},   # 7899 → 8341
        {"map": 8341, "changeMap": "right",  "gather": True},   # 8341 → 8471
        {"map": 8471, "changeMap": "left",   "gather": True},   # 8471 → 8341 (impasse)
        {"map": 8341, "changeMap": "left",   "gather": True},   # 8341 → 8343
        {"map": 8343, "changeMap": "top",    "gather": True},   # 8343 → 8353
        {"map": 8353, "changeMap": "left",   "gather": True, "exitCell": 232},  # 8353 → 8352 (soleil cell 232, pas 218)
        {"map": 8352, "changeMap": "bottom", "gather": True, "exitCell": 455},  # 8352 → 8346 (soleil cell 455, pas 443)
        {"map": 8346, "changeMap": "bottom", "gather": True},   # 8346 → 7891
        {"map": 7891, "changeMap": "top",    "gather": True},   # 7891 → 8346 (impasse)
        {"map": 8346, "changeMap": "right",  "gather": True},   # 8346 → 8343
        {"map": 8343, "changeMap": "right",  "gather": True},   # 8343 → 8341
        {"map": 8341, "changeMap": "bottom", "gather": True},   # 8341 → 7899
    ]
