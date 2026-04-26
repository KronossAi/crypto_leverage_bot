"""
Détecteur de régime — obligatoire avant tout signal
HV (haute volatilité) → Momentum/Breakout
LV (basse volatilité) → Mean Reversion
"""
import logging
from data.indicators import detect_regime, get_atr_values

logger = logging.getLogger(__name__)


class RegimeDetector:
    def __init__(self, config: dict):
        self.hv_mult = config["regime"]["hv_multiplier"]
        self.lv_mult = config["regime"]["lv_multiplier"]

    def detect(self, feed, symbol: str, timeframe: str) -> str:
        ohlcv = feed.get_ohlcv(symbol, timeframe)
        if not ohlcv:
            return "normal"
        regime = detect_regime(ohlcv, self.hv_mult, self.lv_mult)
        logger.debug(f"Regime {symbol} {timeframe}: {regime}")
        return regime

    def get_atr_for_circuit(self, feed, symbol: str, timeframe: str) -> tuple:
        ohlcv = feed.get_ohlcv(symbol, timeframe)
        if not ohlcv:
            return 0.0, 0.0
        return get_atr_values(ohlcv)