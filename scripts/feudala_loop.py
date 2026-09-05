"""Récolte Feudala — 4 maps en boucle (27,-50 / 27,-51 / 28,-51 / 28,-50)"""

# Laisser vide pour récolter tous les types disponibles.
# Remplir pour filtrer : ex. {7555} pour la Dolomite uniquement.
ELEMENTS_TO_GATHER: set[int] = set()

# Laisser vide pour désactiver la priorité.
# Remplir avec les elem_types dans l'ordre de priorité : ex. [7555, 7554] récolte
# d'abord toutes les Dolomite, puis toutes les 7554, puis les ressources restantes.
PRIORITY_ELEMENTS: list[int] = [7555]  # Dolomite en priorité

MAX_PODS: int = 0  # Pas de retour banque automatique (non implémenté)


def move() -> list[dict]:
    """
    Cycle Feudala sens horaire : 8343 → 8353 → 8360 → 8341 → retour 8343.

    Maps :
        8343  (27,-50)  ←→  8341  (28,-50)
           ↕                    ↕
        8353  (27,-51)  ←→  8360  (28,-51)
    """
    return [
        {"map": 8343, "changeMap": "top",    "gather": True},   # 27,-50 → 27,-51
        {"map": 8353, "changeMap": "right",  "gather": True},   # 27,-51 → 28,-51
        {"map": 8360, "changeMap": "bottom", "gather": True},   # 28,-51 → 28,-50
        {"map": 8341, "changeMap": "left",   "gather": True},   # 28,-50 → 27,-50
    ]
