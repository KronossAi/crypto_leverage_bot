"""
VWAP Sessions + Bands — Binance Futures OHLCV
─────────────────────────────────────────────────────────────
Source    : OHLCV Binance (deja dans feed.py)
Exécution : Hyperliquid uniquement

Sessions :
  - Asian  : 00h-08h UTC
  - London : 07h-10h UTC
  - NY     : 13h-16h UTC

Métriques :
  - VWAP ancré session open
  - Bands ±1σ ±2σ ±3σ
  - Rolling VWAP N bougies
  - Signal reversion / continuation
"""
import logging
import time
from datetime import datetime, timezone
from dataclasses import dataclass, field
from typing import Optional
import numpy as np

logger = logging.getLogger(__name__)


# ─── Sessions UTC ────────────────────────────────────────────────────────────

SESSIONS = {
    "asian":  (0,  8),   # 00h-08h UTC
    "london": (7,  10),  # 07h-10h UTC
    "ny":     (13, 16),  # 13h-16h UTC
}


def get_current_session() -> Optional[str]:
    """Retourne la session active ou None si dead zone"""
    hour = datetime.now(timezone.utc).hour
    for name, (start, end) in SESSIONS.items():
        if start <= hour < end:
            return name
    return None


# ─── VWAP Engine ─────────────────────────────────────────────────────────────

@dataclass
class VWAPSignal:
    symbol:           str
    timestamp:        int
    session:          Optional[str]  = None
    side:             Optional[str]  = None
    score:            int            = 0
    reasons:          list           = field(default_factory=list)

    vwap:             float          = 0.0
    band_1s_upper:    float          = 0.0
    band_1s_lower:    float          = 0.0
    band_2s_upper:    float          = 0.0
    band_2s_lower:    float          = 0.0
    band_3s_upper:    float          = 0.0
    band_3s_lower:    float          = 0.0
    rolling_vwap:     float          = 0.0

    price_vs_vwap:    str            = "above"  # "above" | "below" | "at"
    touch_1s:         bool           = False
    touch_2s:         bool           = False
    touch_3s:         bool           = False
    above_vwap:       bool           = False
    extension_3s:     bool           = False


class VWAPEngine:
    """
    Calcule le VWAP ancré à chaque session + bands.
    """

    def __init__(
        self,
        symbol:         str,
        rolling_window: int   = 20,
        touch_tolerance: float = 0.001,  # 0.1% autour des bands
    ):
        self.symbol          = symbol
        self.rolling_window  = rolling_window
        self.touch_tolerance = touch_tolerance

        # Données session en cours
        self._session_name:    Optional[str]  = None
        self._session_tpvol:   float          = 0.0
        self._session_vol:     float          = 0.0
        self._session_tpvol2:  float          = 0.0  # Pour stddev

        # Rolling VWAP
        self._rolling_tp:  list[float] = []
        self._rolling_vol: list[float] = []

    def update(self, ohlcv: list[list]) -> Optional[VWAPSignal]:
        """
        Met à jour le VWAP depuis les données OHLCV.
        Appelé à chaque nouvelle bougie.
        ohlcv : [[ts, open, high, low, close, volume], ...]
        """
        if not ohlcv or len(ohlcv) < 2:
            return None

        try:
            current_session = get_current_session()
            signal          = VWAPSignal(
                symbol    = self.symbol,
                timestamp = int(time.time() * 1000),
                session   = current_session,
            )

            # Reset si nouvelle session
            if current_session != self._session_name:
                self._session_name   = current_session
                self._session_tpvol  = 0.0
                self._session_vol    = 0.0
                self._session_tpvol2 = 0.0
                self._rolling_tp     = []
                self._rolling_vol    = []
                logger.debug(
                    f"[VWAP] {self.symbol} nouvelle session: {current_session}"
                )

            # Calcul VWAP sur toutes les bougies de la session
            # (simplification : on utilise les N dernières bougies)
            bars = ohlcv[-100:]  # Max 100 bougies pour la session

            cum_tpvol = 0.0
            cum_vol   = 0.0
            cum_tp2vol = 0.0

            for bar in bars:
                _, o, h, l, c, v = bar
                tp       = (float(h) + float(l) + float(c)) / 3
                vol      = float(v)
                cum_tpvol  += tp * vol
                cum_vol    += vol
                cum_tp2vol += (tp ** 2) * vol

            if cum_vol == 0:
                return None

            vwap = cum_tpvol / cum_vol

            # Variance et stddev pour les bands
            variance = (cum_tp2vol / cum_vol) - (vwap ** 2)
            stddev   = max(variance, 0) ** 0.5

            signal.vwap         = round(vwap, 6)
            signal.band_1s_upper = round(vwap + stddev, 6)
            signal.band_1s_lower = round(vwap - stddev, 6)
            signal.band_2s_upper = round(vwap + 2 * stddev, 6)
            signal.band_2s_lower = round(vwap - 2 * stddev, 6)
            signal.band_3s_upper = round(vwap + 3 * stddev, 6)
            signal.band_3s_lower = round(vwap - 3 * stddev, 6)

            # Rolling VWAP sur N dernières bougies
            last_bars = ohlcv[-self.rolling_window:]
            r_tpvol = sum(
                ((float(b[2]) + float(b[3]) + float(b[4])) / 3) * float(b[5])
                for b in last_bars
            )
            r_vol = sum(float(b[5]) for b in last_bars)
            signal.rolling_vwap = round(r_tpvol / r_vol, 6) if r_vol > 0 else vwap

            # Prix actuel
            price = float(ohlcv[-1][4])

            # Position vs VWAP
            tol = vwap * self.touch_tolerance
            if abs(price - vwap) <= tol:
                signal.price_vs_vwap = "at"
            elif price > vwap:
                signal.price_vs_vwap = "above"
                signal.above_vwap    = True
            else:
                signal.price_vs_vwap = "below"

            # Touch bands
            signal.touch_1s = (
                abs(price - signal.band_1s_upper) <= tol or
                abs(price - signal.band_1s_lower) <= tol
            )
            signal.touch_2s = (
                abs(price - signal.band_2s_upper) <= tol or
                abs(price - signal.band_2s_lower) <= tol
            )
            signal.touch_3s = (
                abs(price - signal.band_3s_upper) <= tol or
                abs(price - signal.band_3s_lower) <= tol
            )
            signal.extension_3s = (
                price > signal.band_3s_upper or
                price < signal.band_3s_lower
            )

            # Score et side
            self._compute_score(signal, price)

            return signal

        except Exception as e:
            logger.error(f"VWAPEngine.update {self.symbol}: {e}")
            return None

    def _compute_score(self, signal: VWAPSignal, price: float):
        """
        Score VWAP (0-100)

        Barème :
          Touch ±1σ + reversal    : +30
          Touch ±2σ               : +40
          Extension ±3σ (fade)    : +50
          Prix sur VWAP (bounce)  : +25
          Rolling VWAP aligné     : +10
          Session killzone active : +10
        """
        score = 0

        # Extension ±3σ → fade extrême
        if signal.extension_3s:
            score += 50
            if price > signal.band_3s_upper:
                signal.side = "short"
                signal.reasons.append(
                    f"Extension +3σ ({price:.4f} > {signal.band_3s_upper:.4f}) → fade short"
                )
            else:
                signal.side = "long"
                signal.reasons.append(
                    f"Extension -3σ ({price:.4f} < {signal.band_3s_lower:.4f}) → fade long"
                )

        # Touch ±2σ
        elif signal.touch_2s:
            score += 40
            if price >= signal.band_2s_upper:
                signal.side = "short"
                signal.reasons.append(
                    f"Touch +2σ ({price:.4f} ~ {signal.band_2s_upper:.4f}) → reversion short"
                )
            else:
                signal.side = "long"
                signal.reasons.append(
                    f"Touch -2σ ({price:.4f} ~ {signal.band_2s_lower:.4f}) → reversion long"
                )

        # Touch ±1σ
        elif signal.touch_1s:
            score += 30
            if price >= signal.band_1s_upper:
                signal.side = "short"
                signal.reasons.append(f"Touch +1σ → reversion short")
            else:
                signal.side = "long"
                signal.reasons.append(f"Touch -1σ → reversion long")

        # Prix sur VWAP → bounce
        elif signal.price_vs_vwap == "at":
            score += 25
            signal.reasons.append("Prix sur VWAP → bounce attendu")

        # Continuation : prix au-dessus VWAP + bias haussier
        if signal.above_vwap and signal.side != "short":
            score += 15
            signal.side = signal.side or "long"
            signal.reasons.append("Prix au-dessus VWAP → bias long")
        elif not signal.above_vwap and signal.side != "long":
            score += 15
            signal.side = signal.side or "short"
            signal.reasons.append("Prix en dessous VWAP → bias short")

        # Rolling VWAP aligné
        if signal.rolling_vwap > 0 and signal.vwap > 0:
            if signal.side == "long" and signal.rolling_vwap > signal.vwap:
                score += 10
                signal.reasons.append("Rolling VWAP > VWAP → momentum haussier")
            elif signal.side == "short" and signal.rolling_vwap < signal.vwap:
                score += 10
                signal.reasons.append("Rolling VWAP < VWAP → momentum baissier")

        # Session killzone active
        if signal.session in ("london", "ny"):
            score += 10
            signal.reasons.append(f"Killzone active: {signal.session}")

        signal.score = min(score, 100)

        if score > 0:
            logger.info(
                f"[VWAP] {signal.symbol} | Score: {score} | "
                f"Side: {signal.side} | VWAP: {signal.vwap:.4f} | "
                f"{' | '.join(signal.reasons[:2])}"
            )


class VWAPRegistry:
    """Gestionnaire de VWAPEngine par symbole"""

    def __init__(self, pairs: list[str], rolling_window: int = 20):
        self._engines: dict[str, VWAPEngine] = {
            s: VWAPEngine(s, rolling_window) for s in pairs
        }

    def update(self, symbol: str, ohlcv: list[list]) -> Optional[VWAPSignal]:
        e = self._engines.get(symbol)
        if not e:
            return None
        return e.update(ohlcv)

    def get_session(self) -> Optional[str]:
        return get_current_session()
