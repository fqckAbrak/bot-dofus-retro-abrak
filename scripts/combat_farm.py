"""
Script de farming combat — Map 1147.

Stratégie de groupe :
  - 1 Crâ principal (perso connecté) + 1 Crâ compagnon actif
  - 5 Enus + 1 Panda : héros passifs (passent leur tour automatiquement)
  - Toujours attaquer le groupe avec le plus de monstres
  - 2× flèche explosive (sort 179, AOE rayon 2 Chebyshev) par tour de Crâ

Usage :
  Sélectionner ce script dans l'onglet Scripts du dashboard, puis cliquer "Lancer".
  Le bot démarre automatiquement dès que la map est prête.
  Ctrl+C ou "Arrêter" pour stopper.
"""

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

# Map cible (doit correspondre à la map actuelle)
TARGET_MAP_ID: int = 1147

# Activer la boucle de farm automatique (False = seulement gérer les combats en cours)
AUTO_FARM_LOOP: bool = True


# ---------------------------------------------------------------------------
# Point d'entrée (appelé par bot/script_engine.py)
# ---------------------------------------------------------------------------

async def run(ctx: dict) -> None:
    """Point d'entrée du script.

    ctx est fourni par le script_engine et contient :
      ctx["state"]  → game.state (GameState courant)
      ctx["stop"]   → asyncio.Event signalant l'arrêt demandé
    """
    import asyncio
    import logging

    from bot.combat import combat_farm_loop, handle_combat_if_needed
    from game import state as _state
    from dashboard import bridge

    logger = logging.getLogger(__name__)
    stop_event: asyncio.Event = ctx.get("stop", asyncio.Event())

    bridge.add_console("🤖 Script combat_farm démarré")
    logger.info("[combat_farm] Script démarré — map cible #%d", TARGET_MAP_ID)

    if AUTO_FARM_LOOP:
        # Lancer la boucle de farming en tâche de fond
        farm_task = asyncio.get_event_loop().create_task(
            combat_farm_loop(target_map_id=TARGET_MAP_ID)
        )

        # Attendre le signal d'arrêt — finally garantit l'annulation même sur CancelledError
        try:
            await stop_event.wait()
        finally:
            farm_task.cancel()
            try:
                await farm_task
            except (asyncio.CancelledError, Exception):
                pass
    else:
        # Mode simple : gérer seulement les combats déjà en cours
        while not stop_event.is_set():
            await handle_combat_if_needed()
            await asyncio.sleep(1.0)

    bridge.add_console("🛑 Script combat_farm arrêté")
    logger.info("[combat_farm] Script arrêté")
