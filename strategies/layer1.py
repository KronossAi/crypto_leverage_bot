"""
Layer 1 — HTF Bias (4H/1D)
─────────────────────────────────────────────────
Valide :
  - BTC EMA50/200 trend
  - BTC.D proxy (via ratio BTC/ETH volume)
  - Funding rate global
  - Corrélation ETH/BTC
"""
import logging
from data.indicators import calc_ema_trend, calc_rsi

logger = logging.getLogger(__name__)

NEUTRAL = "neutral"
LONG    = "long"
SHORT   = "short"


class Layer1:
    def __init__(self, config: dict):
        self.config      = config
        self.funding_cfg = config["funding"]
        self.corr_cfg    = config["correlations"]

    def evaluate(
        self,
        symbol: str,
        feed,
        timeframes: dict,
        funding_rates: dict,
    ) -> dict:
        """
        Retourne {
          "bias": "long"|"short"|"neutral",
          "valid": bool,
          "reasons": [str]
        }
        """
        reasons = []
        bias    = NEUTRAL

        htf = timeframes["htf"]

        # ── BTC trend (toujours calculé comme référence) ──────────────────
        btc_ohlcv = feed.get_ohlcv("BTC", htf)
        btc_trend = calc_ema_trend(btc_ohlcv) if btc_ohlcv else None

        if btc_trend:
            if btc_trend["bullish"]:
                bias = LONG
                reasons.append("BTC EMA50>EMA200 bullish")
            elif btc_trend["bearish"]:
                bias = SHORT
                reasons.append("BTC EMA50<EMA200 bearish")
            else:
                reasons.append("BTC trend neutre")
        else:
            reasons.append("BTC HTF data insuffisante")
            return {"bias": NEUTRAL, "valid": False, "reasons": reasons}

        # ── Funding rate filter ───────────────────────────────────────────
        funding = funding_rates.get(symbol, 0.0)
        if bias == LONG and funding > self.funding_cfg["long_block_threshold"]:
            reasons.append(
                f"Funding {funding*100:.3f}% trop élevé → long bloqué"
            )
            return {"bias": NEUTRAL, "valid": False, "reasons": reasons}

        if bias == SHORT and funding < self.funding_cfg["short_block_threshold"]:
            reasons.append(
                f"Funding {funding*100:.3f}% trop négatif → short bloqué"
            )
            return {"bias": NEUTRAL, "valid": False, "reasons": reasons}

        if funding < self.funding_cfg["extreme_negative"]:
            reasons.append("Funding extrême négatif → long squeeze probable")
            if bias == SHORT:
                bias = LONG  # Override vers long counter-trend

        # ── Corrélation ETH/BTC pour alts ────────────────────────────────
        if symbol not in ("BTC",):
            eth_ohlcv = feed.get_ohlcv("ETH", htf)
            eth_trend = calc_ema_trend(eth_ohlcv) if eth_ohlcv else None
            if eth_trend:
                eth_bias = "long" if eth_trend["bullish"] else "short"
                if eth_bias != bias:
                    reasons.append(
                        f"ETH bias ({eth_bias}) diverge de BTC ({bias}) → signal affaibli"
                    )
                    # Confidence réduite mais pas bloqué

        reasons.append(f"Layer 1 validé — biais: {bias}")
        return {"bias": bias, "valid": True, "reasons": reasons}