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
        ltf     = timeframes["ltf"]
        ohlcv   = feed.get_ohlcv(symbol, ltf)
        reasons = []
        score   = 0
        cvd     = None
        ema     = None
        vol     = None

        if not ohlcv:
            result = {
                "triggered": False,
                "entry_price": 0,
                "reasons": ["no_ohlcv"]
            }
            logger.debug(f"[L3 DEBUG] {symbol} result={result}")
            return result

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

        # Log debug détaillé
        logger.info(
            f"[L3 DEBUG] {symbol} side={side} score={score}/3 — "
            f"EMA: cross_up={ema['cross_up'] if ema else None} "
            f"cross_down={ema['cross_down'] if ema else None} "
            f"above={ema['above'] if ema else None} | "
            f"CVD: cvd={cvd['cvd'] if cvd else 'None'} "
            f"flip={cvd['cvd_flip'] if cvd else None} "
            f"bullish={cvd['bullish'] if cvd else None} "
            f"bearish={cvd['bearish'] if cvd else None} | "
            f"VOL: spike={vol['spike'] if vol else None} "
            f"ratio={vol['vol_ratio'] if vol else None}"
        )

        triggered = score >= 2  # Min 2/3

        if triggered:
            reasons.append(f"L3 OK {score}/3")
        else:
            reasons.append(f"L3 FAIL {score}/3")

        result = {
            "triggered": triggered,
            "entry_price": price,
            "reasons": reasons,
            "score": score
        }

        logger.debug(f"[L3 DEBUG] {symbol} result={result}")

        return result
