"""
Layer 3 — LTF Trigger (5M/1M)
─────────────────────────────────────────────────
Trigger final — 3 confirmations :
  - EMA 9/21 cross dans le sens du biais
  - CVD flip (acheteurs/vendeurs prennent le dessus)
  - Breakout candle (bougie de momentum avec volume)
"""
import logging
from data.indicators import calc_ema_cross, calc_volume_spike

logger = logging.getLogger(__name__)


class Layer3:
    def __init__(self, config: dict, strategies_cfg: dict, cvd_trackers: dict):
        self.momentum_cfg = strategies_cfg.get("momentum", {})
        self.cvd_trackers = cvd_trackers  # {symbol: CVDTracker}

    def evaluate(
        self,
        symbol: str,
        side: str,
        feed,
        timeframes: dict,
    ) -> dict:
        """
        Retourne {
          "triggered": bool,
          "entry_price": float,
          "reasons": [str]
        }
        """
        ltf     = timeframes["ltf"]
        ohlcv   = feed.get_ohlcv(symbol, ltf)
        reasons = []
        score   = 0

        if not ohlcv:
            return {"triggered": False, "entry_price": 0, "reasons": ["Pas de données LTF"]}

        price = float(ohlcv[-1][4]) if ohlcv else 0.0

        # ── 1. EMA 9/21 cross ─────────────────────────────────────────────
        ema = calc_ema_cross(ohlcv, fast=9, slow=21)
        if ema:
            if side == "long" and (ema["cross_up"] or ema["above"]):
                score += 1
                reasons.append("EMA 9/21 cross up")
            elif side == "short" and (ema["cross_down"] or not ema["above"]):
                score += 1
                reasons.append("EMA 9/21 cross down")

        # ── 2. CVD flip ───────────────────────────────────────────────────
        cvd_tracker = self.cvd_trackers.get(symbol)
        if cvd_tracker:
            cvd = cvd_tracker.get()
            if side == "long" and (cvd["cvd_flip"] and cvd["bullish"]):
                score += 1
                reasons.append(f"CVD flip bullish ({cvd['cvd']:+.2f})")
            elif side == "long" and cvd["bullish"]:
                score += 1
                reasons.append(f"CVD positif ({cvd['cvd']:+.2f})")
            elif side == "short" and (cvd["cvd_flip"] and cvd["bearish"]):
                score += 1
                reasons.append(f"CVD flip bearish ({cvd['cvd']:+.2f})")
            elif side == "short" and cvd["bearish"]:
                score += 1
                reasons.append(f"CVD négatif ({cvd['cvd']:+.2f})")

        # ── 3. Volume spike ───────────────────────────────────────────────
        vol = calc_volume_spike(
            ohlcv,
            mult=self.momentum_cfg.get("volume_spike_mult", 2.0)
        )
        if vol and vol["spike"]:
            score += 1
            reasons.append(f"Volume spike {vol['vol_ratio']:.1f}x")

        triggered = score >= 2  # Min 2/3
        if triggered:
            reasons.append(f"Layer 3 déclenché — {score}/3")
        else:
            reasons.append(f"Layer 3 non déclenché — {score}/3 (min 2)")

        return {
            "triggered":   triggered,
            "entry_price": price,
            "reasons":     reasons,
        }