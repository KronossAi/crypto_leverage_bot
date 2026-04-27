# data/scalp_levels_key.py
# Module 5 — Niveaux clés automatiques
# PDH/PDL, PWH/PWL, Round Numbers, Initial Balance, Overnight Range, Monthly Open

import logging
from datetime import datetime, timezone, timedelta
from collections import defaultdict
from typing import Optional

logger = logging.getLogger(__name__)


class KeyLevelsEngine:
    """
    Calcule et maintient les niveaux clés de prix pour le scalping.
    Sources : OHLCV Binance via feed.get_ohlcv()

    Niveaux calculés :
    - PDH/PDL  : Previous Day High/Low
    - PWH/PWL  : Previous Week High/Low
    - Round Numbers : niveaux 00 et 50
    - IB       : Initial Balance (range première heure UTC)
    - ONR      : Overnight Range H/L (session Asia 00h-07h UTC)
    - PSC      : Previous Session Close
    - MO       : Monthly Open
    """

    SCORE_TOUCH_PDH_PDL = 30
    SCORE_IB_BREAKOUT   = 35
    SCORE_ROUND_NUMBER  = 20
    SCORE_PWH_PWL       = 25
    SCORE_ONR           = 20
    SCORE_MONTHLY_OPEN  = 25
    PROXIMITY_PCT       = 0.0015  # 0.15% = "en contact" avec le niveau

    def __init__(self):
        self._levels: dict = defaultdict(lambda: {
            "pdh": None, "pdl": None,
            "pwh": None, "pwl": None,
            "ib_high": None, "ib_low": None, "ib_closed": False,
            "onr_high": None, "onr_low": None,
            "psc": None,
            "monthly_open": None,
            "last_update": None,
        })

    # ------------------------------------------------------------------
    # MISE À JOUR PRINCIPALE
    # ------------------------------------------------------------------

    def update(self, symbol: str, ohlcv_1h: list, ohlcv_1d: list, ohlcv_1w: list) -> None:
        """
        Met à jour tous les niveaux clés.
        Appelé depuis orchestrator.py à chaque analyse.
        """
        lvl = self._levels[symbol]
        now_utc = datetime.now(timezone.utc)

        # --- PDH / PDL / PSC (bougie J-1) ---
        if ohlcv_1d and len(ohlcv_1d) >= 2:
            prev_day      = ohlcv_1d[-2]
            lvl["pdh"]    = float(prev_day[2])
            lvl["pdl"]    = float(prev_day[3])
            lvl["psc"]    = float(prev_day[4])

        # --- PWH / PWL (semaine précédente) ---
        if ohlcv_1w and len(ohlcv_1w) >= 2:
            prev_week     = ohlcv_1w[-2]
            lvl["pwh"]    = float(prev_week[2])
            lvl["pwl"]    = float(prev_week[3])

        # --- Monthly Open ---
        if ohlcv_1d:
            for candle in reversed(ohlcv_1d):
                ts = datetime.fromtimestamp(candle[0] / 1000, tz=timezone.utc)
                if ts.month == now_utc.month and ts.year == now_utc.year:
                    lvl["monthly_open"] = float(candle[1])
                else:
                    break

        # --- Initial Balance (00h-01h UTC) ---
        if ohlcv_1h:
            self._update_initial_balance(symbol, ohlcv_1h, now_utc)
            self._update_overnight_range(symbol, ohlcv_1h, now_utc)

        lvl["last_update"] = now_utc

        logger.debug(
            f"[KeyLevels] {symbol} | "
            f"PDH={lvl['pdh']} PDL={lvl['pdl']} | "
            f"IB=[{lvl['ib_low']},{lvl['ib_high']}] | "
            f"MO={lvl['monthly_open']}"
        )

    def _update_initial_balance(self, symbol: str, ohlcv_1h: list, now_utc: datetime) -> None:
        """IB = range de la première heure de session (00:00-01:00 UTC)."""
        lvl         = self._levels[symbol]
        today_start = now_utc.replace(hour=0, minute=0, second=0, microsecond=0)
        ib_end      = today_start + timedelta(hours=1)

        ib_candles = [
            c for c in ohlcv_1h
            if today_start <= datetime.fromtimestamp(c[0] / 1000, tz=timezone.utc) < ib_end
        ]

        if ib_candles:
            lvl["ib_high"]   = max(float(c[2]) for c in ib_candles)
            lvl["ib_low"]    = min(float(c[3]) for c in ib_candles)
            lvl["ib_closed"] = now_utc >= ib_end
        else:
            lvl["ib_high"]   = None
            lvl["ib_low"]    = None
            lvl["ib_closed"] = False

    def _update_overnight_range(self, symbol: str, ohlcv_1h: list, now_utc: datetime) -> None:
        """ONR = range session Asia (00h-07h UTC)."""
        lvl         = self._levels[symbol]
        today_start = now_utc.replace(hour=0, minute=0, second=0, microsecond=0)
        asia_end    = today_start + timedelta(hours=7)

        asia_candles = [
            c for c in ohlcv_1h
            if today_start <= datetime.fromtimestamp(c[0] / 1000, tz=timezone.utc) < asia_end
        ]

        if asia_candles:
            lvl["onr_high"] = max(float(c[2]) for c in asia_candles)
            lvl["onr_low"]  = min(float(c[3]) for c in asia_candles)

    # ------------------------------------------------------------------
    # SCORING
    # ------------------------------------------------------------------

    def get_score(self, symbol: str, current_price: float, bias: str) -> tuple[int, list]:
        """
        Calcule le score de confluence avec les niveaux clés.

        Args:
            symbol        : ex. "BTCUSDT"
            current_price : prix actuel
            bias          : "long" ou "short"

        Returns:
            (score 0-100, liste de raisons)
        """
        lvl = self._levels[symbol]
        if lvl["last_update"] is None:
            return 0, []

        score   = 0
        reasons = []
        price   = current_price

        def near(level: Optional[float]) -> bool:
            if level is None:
                return False
            return abs(price - level) / level <= self.PROXIMITY_PCT

        # PDH / PDL
        if bias == "long" and near(lvl["pdl"]):
            score += self.SCORE_TOUCH_PDH_PDL
            reasons.append(f"PDL touch @ {lvl['pdl']:.4f}")
        elif bias == "short" and near(lvl["pdh"]):
            score += self.SCORE_TOUCH_PDH_PDL
            reasons.append(f"PDH touch @ {lvl['pdh']:.4f}")

        # PWH / PWL
        if bias == "long" and near(lvl["pwl"]):
            score += self.SCORE_PWH_PWL
            reasons.append(f"PWL touch @ {lvl['pwl']:.4f}")
        elif bias == "short" and near(lvl["pwh"]):
            score += self.SCORE_PWH_PWL
            reasons.append(f"PWH touch @ {lvl['pwh']:.4f}")

        # IB Breakout / Support (seulement si IB fermé)
        if lvl["ib_closed"] and lvl["ib_high"] and lvl["ib_low"]:
            if bias == "long":
                if price > lvl["ib_high"] and near(lvl["ib_high"]):
                    score += self.SCORE_IB_BREAKOUT
                    reasons.append(f"IB breakout haussier @ {lvl['ib_high']:.4f}")
                elif near(lvl["ib_low"]):
                    score += self.SCORE_IB_BREAKOUT
                    reasons.append(f"IB support @ {lvl['ib_low']:.4f}")
            elif bias == "short":
                if price < lvl["ib_low"] and near(lvl["ib_low"]):
                    score += self.SCORE_IB_BREAKOUT
                    reasons.append(f"IB breakout baissier @ {lvl['ib_low']:.4f}")
                elif near(lvl["ib_high"]):
                    score += self.SCORE_IB_BREAKOUT
                    reasons.append(f"IB resistance @ {lvl['ib_high']:.4f}")

        # Round Numbers
        round_level = self._nearest_round(price)
        if near(round_level):
            score += self.SCORE_ROUND_NUMBER
            reasons.append(f"Round number @ {round_level:.4f}")

        # Overnight Range
        if bias == "long" and near(lvl["onr_low"]):
            score += self.SCORE_ONR
            reasons.append(f"ONR support @ {lvl['onr_low']:.4f}")
        elif bias == "short" and near(lvl["onr_high"]):
            score += self.SCORE_ONR
            reasons.append(f"ONR resistance @ {lvl['onr_high']:.4f}")

        # Monthly Open
        if near(lvl["monthly_open"]):
            score += self.SCORE_MONTHLY_OPEN
            reasons.append(f"Monthly Open @ {lvl['monthly_open']:.4f}")

        return min(score, 100), reasons

    def _nearest_round(self, price: float) -> float:
        """Retourne le niveau rond (x00 ou x50) le plus proche."""
        if price >= 10000:
            step = 500
        elif price >= 1000:
            step = 50
        elif price >= 100:
            step = 5
        elif price >= 10:
            step = 0.5
        else:
            step = 0.05
        return round(round(price / step) * step, 4)

    def boost_confidence(self, score: int) -> float:
        """Convertit score 0-100 en boost confidence ±6%."""
        if score >= 80:
            return +0.06
        elif score >= 60:
            return +0.03
        elif score >= 40:
            return 0.0
        else:
            return -0.03

    def get_levels(self, symbol: str) -> dict:
        """Retourne tous les niveaux pour affichage dashboard."""
        return dict(self._levels[symbol])


class KeyLevelsRegistry:
    """Singleton — accès global depuis orchestrateur."""
    _instance: Optional["KeyLevelsEngine"] = None

    @classmethod
    def get(cls) -> "KeyLevelsEngine":
        if cls._instance is None:
            cls._instance = KeyLevelsEngine()
        return cls._instance