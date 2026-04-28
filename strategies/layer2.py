"""
Layer 2 — MTF Confluence (1H/15M)
─────────────────────────────────────────────────
Nécessite 2/3 parmi :
  - SMC (BOS/CHoCH/FVG/OB)
  - AVWAP (prix vs VWAP ancré)
  - RSI (zones + divergence)
"""
import logging
from data.indicators import calc_smc, calc_avwap, calc_rsi

logger = logging.getLogger(__name__)


class Layer2:
    def __init__(self, config: dict, strategies_cfg: dict):
        self.smc_cfg  = strategies_cfg.get("smc", {})
        self.min_confluence = 2  # Min 2/3 indicateurs confirmés

    def evaluate(
        self,
        symbol: str,
        side: str,
        feed,
        timeframes: dict,
    ) -> dict:
        """
        Retourne {
          "valid": bool,
          "score": int (0-3),
          "signals": dict,
          "reasons": [str]
        }
        """
        mtf     = timeframes["mtf"]
        ohlcv   = feed.get_ohlcv(symbol, mtf)
        reasons = []
        score   = 0
        signals = {}

        if not ohlcv:
            return {"valid": False, "score": 0, "signals": {}, "reasons": ["Pas de données MTF"]}

        # ── 1. SMC ────────────────────────────────────────────────────────
        smc = calc_smc(ohlcv, self.smc_cfg)
        if smc:
            signals["smc"] = smc
            if side == "long" and smc["bias"] == "long":
                score += 1
                reasons.append("SMC bullish (BOS/CHoCH/OB)")
            elif side == "short" and smc["bias"] == "short":
                score += 1
                reasons.append("SMC bearish (BOS/CHoCH/OB)")
            else:
                reasons.append(f"SMC neutre ou opposé ({smc['bias']})")

        # ── 2. AVWAP ──────────────────────────────────────────────────────
        anchor = "swing_low" if side == "long" else "swing_high"
        avwap  = calc_avwap(ohlcv, anchor_type=anchor)
        if avwap:
            signals["avwap"] = avwap
            price            = avwap["price"]
            if side == "long":
                if price > avwap["avwap"]:
                    score += 1
                    reasons.append("Prix au-dessus AVWAP (bullish)")
                elif avwap["near_avwap"]:
                    score += 1
                    reasons.append("Prix sur AVWAP (support potentiel)")
            else:
                if price < avwap["avwap"]:
                    score += 1
                    reasons.append("Prix en dessous AVWAP (bearish)")
                elif avwap["near_avwap"]:
                    score += 1
                    reasons.append("Prix sur AVWAP (résistance potentielle)")

        # ── 3. RSI ────────────────────────────────────────────────────────
        rsi = calc_rsi(ohlcv)
        if rsi:
            signals["rsi"] = rsi
            if side == "long":
                if rsi["oversold"] or rsi["bull_div"] or rsi["value"] < 50:
                    score += 1
                    reasons.append(
                        f"RSI oversold ({rsi['value']:.1f})"
                        if rsi["oversold"] else f"RSI favorable long ({rsi['value']:.1f})"
                    )
            else:
                if rsi["overbought"] or rsi["bear_div"] or rsi["value"] > 50:
                    score += 1
                    reasons.append(
                        f"RSI overbought ({rsi['value']:.1f})"
                        if rsi["overbought"] else f"RSI favorable short ({rsi['value']:.1f})"
                    )

        valid = score >= self.min_confluence
        if valid:
            reasons.append(f"Layer 2 validé — confluence {score}/3")
        else:
            reasons.append(f"Layer 2 insuffisant — {score}/3 (min {self.min_confluence})")

        return {
            "valid":   valid,
            "score":   score,
            "signals": signals,
            "reasons": reasons,
        }