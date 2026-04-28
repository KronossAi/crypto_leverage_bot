"""
Layer 2 — MTF Confluence (1H/15M)
─────────────────────────────────────────────────
Détermine sa propre direction (vote SMC + AVWAP + RSI).
Si htf_hint est passé, il est utilisé comme tie-breaker en cas d'égalité.
Nécessite 2/3 indicateurs alignés sur la direction votée.
"""
import logging
from data.indicators import calc_smc, calc_avwap, calc_rsi

logger = logging.getLogger(__name__)


class Layer2:
    def __init__(self, config: dict, strategies_cfg: dict):
        self.smc_cfg = strategies_cfg.get("smc", {})
        self.min_confluence = 2  # Min 2/3 indicateurs confirmés

    def evaluate(
        self,
        symbol: str,
        feed,
        timeframes: dict,
        htf_hint: str = "neutral",
    ) -> dict:
        """
        Détermine direction par vote majoritaire.
        Retourne {
          "valid":   bool,
          "side":    "long"|"short"|"neutral",
          "score":   int (0-3),
          "signals": dict,
          "reasons": [str]
        }
        """
        mtf     = timeframes["mtf"]
        ohlcv   = feed.get_ohlcv(symbol, mtf)
        reasons = []
        signals = {}

        if not ohlcv:
            return {
                "valid": False, "side": "neutral", "score": 0,
                "signals": {}, "reasons": ["Pas de données MTF"]
            }

        # Compteurs de votes par direction
        long_score, short_score = 0, 0
        long_reasons, short_reasons = [], []

        # ── 1. SMC ────────────────────────────────────────────────────────
        smc = calc_smc(ohlcv, self.smc_cfg)
        if smc:
            signals["smc"] = smc
            if smc["bias"] == "long":
                long_score += 1
                long_reasons.append("SMC bullish (BOS/CHoCH/OB)")
            elif smc["bias"] == "short":
                short_score += 1
                short_reasons.append("SMC bearish (BOS/CHoCH/OB)")
            logger.info(
                f"[L2 DEBUG] {symbol} SMC bias={smc['bias']} "
                f"bos_bull={smc.get('bos_bull')} bos_bear={smc.get('bos_bear')} "
                f"struct_bull={smc.get('struct_bull')} struct_bear={smc.get('struct_bear')}"
            )

        # ── 2. AVWAP — calculé sur les 2 ancres pour vote indépendant ────
        avwap_long  = calc_avwap(ohlcv, anchor_type="swing_low")
        avwap_short = calc_avwap(ohlcv, anchor_type="swing_high")

        # Vote AVWAP : on choisit l'ancre la plus pertinente selon la position du prix
        avwap_used = None
        if avwap_long and avwap_short:
            price = avwap_long["price"]
            # Long : prix au-dessus du VWAP ancré sur swing_low
            if price > avwap_long["avwap"]:
                long_score += 1
                long_reasons.append("Prix au-dessus AVWAP swing_low (bullish)")
                avwap_used = avwap_long
            # Short : prix en dessous du VWAP ancré sur swing_high
            elif price < avwap_short["avwap"]:
                short_score += 1
                short_reasons.append("Prix en dessous AVWAP swing_high (bearish)")
                avwap_used = avwap_short
            # Near : score les deux directions faiblement (équilibre)
            elif avwap_long["near_avwap"]:
                avwap_used = avwap_long
                reasons.append("Prix proche AVWAP — zone neutre")
            else:
                avwap_used = avwap_long

            signals["avwap"] = avwap_used
            logger.info(
                f"[L2 DEBUG] {symbol} AVWAP price={price:.4f} "
                f"vwap_long={avwap_long['avwap']:.4f} vwap_short={avwap_short['avwap']:.4f}"
            )

        # ── 3. RSI ────────────────────────────────────────────────────────
        rsi = calc_rsi(ohlcv)
        if rsi:
            signals["rsi"] = rsi
            # Long : oversold OU bull divergence OU RSI < 50 (zone basse)
            if rsi["oversold"] or rsi["bull_div"] or rsi["value"] < 45:
                long_score += 1
                long_reasons.append(
                    f"RSI oversold ({rsi['value']:.1f})"
                    if rsi["oversold"] else f"RSI favorable long ({rsi['value']:.1f})"
                )
            # Short : overbought OU bear divergence OU RSI > 55 (zone haute)
            elif rsi["overbought"] or rsi["bear_div"] or rsi["value"] > 55:
                short_score += 1
                short_reasons.append(
                    f"RSI overbought ({rsi['value']:.1f})"
                    if rsi["overbought"] else f"RSI favorable short ({rsi['value']:.1f})"
                )

        # ── 4. Détermination du side ──────────────────────────────────────
        if long_score > short_score:
            side  = "long"
            score = long_score
            reasons.extend(long_reasons)
        elif short_score > long_score:
            side  = "short"
            score = short_score
            reasons.extend(short_reasons)
        else:
            # Égalité → tie-breaker via htf_hint si pertinent
            if htf_hint == "long" and long_score > 0:
                side, score = "long", long_score
                reasons.extend(long_reasons)
                reasons.append("Tie-break par HTF bias (long)")
            elif htf_hint == "short" and short_score > 0:
                side, score = "short", short_score
                reasons.extend(short_reasons)
                reasons.append("Tie-break par HTF bias (short)")
            else:
                side, score = "neutral", 0

        valid = side != "neutral" and score >= self.min_confluence
        if valid:
            reasons.append(f"Layer 2 validé — {side} confluence {score}/3")
        else:
            reasons.append(
                f"Layer 2 insuffisant — long={long_score}/3 short={short_score}/3 "
                f"(min {self.min_confluence})"
            )

        logger.info(
            f"[L2 VOTE] {symbol} long={long_score}/3 short={short_score}/3 "
            f"→ side={side} score={score} valid={valid}"
        )

        return {
            "valid":   valid,
            "side":    side,
            "score":   score,
            "signals": signals,
            "reasons": reasons,
        }