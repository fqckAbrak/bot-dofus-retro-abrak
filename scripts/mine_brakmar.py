"""Récolte Mine — 3 maps en aller-retour (10228 ↔ 10227 ↔ 10229)"""

# Laisser vide pour récolter tous les types disponibles.
# Remplir pour filtrer : ex. {7555} pour la Dolomite uniquement.
ELEMENTS_TO_GATHER: set[int] = set()

# Laisser vide pour désactiver la priorité.
# Remplir avec les elem_types dans l'ordre de priorité : ex. [7555, 7554] récolte
# d'abord toutes les Dolomite, puis toutes les 7554, puis les ressources restantes.
PRIORITY_ELEMENTS: list[int] = [7524]  # Manganèse en priorité

MAX_PODS: int = 0  # Pas de retour banque automatique (non implémenté)


def move() -> list[dict]:
    """
    Cycle mine aller-retour : 10228 → 10227 → 10229 → 10227 → retour 10228.

    Maps :
        10228  ←→  10227  ←→  10229
    """
    return [
        {"map": 10228, "changeMap": "right", "gather": True},   # 10228 → 10227
        {"map": 10227, "changeMap": "right", "gather": True},   # 10227 → 10229
        {"map": 10229, "changeMap": "left",  "gather": True},   # 10229 → 10227
        {"map": 10227, "changeMap": "left",  "gather": True},   # 10227 → 10228
    ]
