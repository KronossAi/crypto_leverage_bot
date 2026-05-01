"""
State Manager — persistance de l'etat entre redemarrages
"""
import json
import logging
import os
from datetime import datetime

from core.portfolio import Portfolio, ClosedTrade
from core.fsm import TradeFSM

logger = logging.getLogger(__name__)

STATE_FILE = os.path.join(
    os.path.dirname(__file__), "..", "data", "state.json"
)


class StateManager:
    def __init__(self, state_file: str = STATE_FILE):
        self.path = os.path.abspath(state_file)
        os.makedirs(os.path.dirname(self.path), exist_ok=True)

    def save(self, portfolio: Portfolio, fsm: TradeFSM):
        try:
            state = {
                "saved_at": datetime.utcnow().isoformat(),
                "capital": portfolio.capital,
                "initial_capital": portfolio.initial_capital,
                "trades": [],
                "positions": []
            }

            with open(self.path, "w") as f:
                json.dump(state, f, indent=2)

            logger.info("Etat sauvegarde")

        except Exception as e:
            logger.error(f"Erreur sauvegarde etat: {e}")

    def load(self, portfolio: Portfolio, fsm: TradeFSM) -> bool:
        if not os.path.exists(self.path):
            return False

        try:
            with open(self.path) as f:
                state = json.load(f)

            portfolio.capital = state["capital"]
            portfolio.initial_capital = state["initial_capital"]

            for ctx in list(fsm.active()):
                ctx.reset()

            logger.info("Etat restaure")
            return True

        except Exception as e:
            logger.error(f"Erreur chargement etat: {e}")
            return False
