"""
Orchestrateur — combine les 3 layers
─────────────────────────────────────────────────
Layer 1 (HTF) → biais directionnel
Layer 2 (MTF) → confluence min 2/3
Layer 3 (LTF) → trigger final

Entrée uniquement si L1 + L2 + L3 validés.
Calcule SL/TP1/TP2 selon la config.
"""
import logging
from typing import Optional

from core.risk_manager   import TradeSignal
from data.indicators     import get_atr, calc_smc
from data.scalp_levels_key import KeyLevelsRegistry
from data.scalp_oi import OIRegistry
from data.scalp_liquidations import LiquidationRegistry
from strategies.layer1   import Layer1
from strategies.layer2   import Layer2
from strategies.layer3   import Layer3
from strategies.regime_detector import RegimeDetector

logger = logging.getLogger(__name__)


class Orchestrator:
    def __init__(
        self,
        config: dict,
        strategies_cfg: dict,
        cvd_trackers: dict,
    ):
        self.config         = config
        self.strategies_cfg = strategies_cfg
        self.regime         = RegimeDetector(config)
        self.layer1         = Layer1(config)
        self.layer2         = Layer2(config, strategies_cfg)
        self.layer3         = Layer3(config, strategies_cfg, cvd_trackers)

        # Frais Hyperliquid (0.02% maker + 0.05% taker)
        self.fee_rt         = 0.001  # aller-retour conservateur
        self.orderflow = None
        self.obi = None
        self.vwap = None
        self.key_levels = KeyLevelsRegistry.get()
        self.oi_engine = OIRegistry.get()
        self.liq_engine = LiquidationRegistry.get()

    async def analyze(
        self,
        symbol:        str,
        feed,
        timeframes:    dict,
        funding_rates: dict,
        circuit_breaker,
        macro_filter,
        capital:       float = 0.0,
        fsm=None,
    ) -> Optional[TradeSignal]:

        # ── 0. Circuit breaker ────────────────────────────────────────────
        can, reason = circuit_breaker.can_trade(capital)  # capital passé par le bot
        if not can:
            logger.debug(f"[{symbol}] Circuit breaker: {reason}")
            return None

        # ── 0b. Macro filter ──────────────────────────────────────────────
        blackout, bl_reason = macro_filter.is_blackout()
        if blackout:
            logger.debug(f"[{symbol}] Macro blackout: {bl_reason}")
            return None
        
        # ── 0c. Check max positions ───────────────────────────────────────
        if fsm and len(fsm.active()) >= self.config["max_concurrent_positions"]:
            logger.debug(f"[{symbol}] Max positions atteint ({len(fsm.active())})")
            return None

        # ── 1. Régime ─────────────────────────────────────────────────────
        regime = self.regime.detect(feed, symbol, timeframes["mtf"])
        logger.debug(f"[{symbol}] Régime: {regime}")

        # Circuit breaker ATR
        atr14, atr50 = self.regime.get_atr_for_circuit(
            feed, symbol, timeframes["ltf"]
        )
        circuit_breaker.on_atr_spike(atr14, atr50)
        if circuit_breaker.volatility_kill:
            return None

        # ── 2. Layer 1 — HTF bias (informatif, pas dictateur) ────────────
        l1 = self.layer1.evaluate(symbol, feed, timeframes, funding_rates)
        # On NE retourne PAS si L1 invalid — c'est juste un hint pour L2
        htf_hint = l1["bias"] if l1.get("valid") else "neutral"
        l1_funding_block = not l1.get("valid") and "Funding" in l1["reasons"][-1]
        if l1_funding_block:
            # Funding extrême = vrai blocage, on respecte
            logger.info(f"[{symbol}] L1 funding block: {l1['reasons'][-1]}")
            return None

        # ── 3. Layer 2 — MTF vote sa propre direction ─────────────────────
        l2 = self.layer2.evaluate(symbol, feed, timeframes, htf_hint=htf_hint)
        if not l2["valid"]:
            logger.info(f"[{symbol}] L2 invalid: {l2['reasons'][-1]}")
            return None

        # ── 3a. Check position déjà ouverte ──────────────────────────────────
        if fsm:
            open_pos = next(
                (c for c in fsm.active() 
                 if c.symbol == symbol and c.state.name not in ["CLOSED", "IDLE"]),
                None
            )
            if open_pos:
                logger.debug(
                    f"[{symbol}] Position {open_pos.side.upper()} déjà OPEN "
                    f"(état: {open_pos.state.name}, depuis {open_pos.open_time})"
                )
                return None

        side = l2["side"]  # "long" ou "short" — déterminé par L2

        # Vérifie compatibilité régime
        if not self._regime_allows(regime, side):
            logger.info(f"[{symbol}] Régime {regime} incompatible")
            return None

        # ── 3b. Validation L1 vs L2 — détection contre-tendance brutale ──
        l1_aligned = (htf_hint == side)
        l1_strict_against = False
        btc_ohlcv = feed.get_ohlcv("BTC", timeframes["htf"])
        from data.indicators import calc_ema_trend
        btc_trend = calc_ema_trend(btc_ohlcv) if btc_ohlcv else None
        if btc_trend:
            # Si L2 dit long mais HTF strictement bearish → BLOQUE
            if side == "long" and btc_trend.get("strict_bearish"):
                logger.debug(f"[{symbol}] BLOCK — L2 long contre HTF strict_bearish")
                return None
            # Si L2 dit short mais HTF strictement bullish → BLOQUE
            if side == "short" and btc_trend.get("strict_bullish"):
                logger.debug(f"[{symbol}] BLOCK — L2 short contre HTF strict_bullish")
                return None

        # ── 4. Layer 3 — LTF trigger ──────────────────────────────────────
        l3 = self.layer3.evaluate(symbol, side, feed, timeframes)
        if not l3["triggered"]:
            logger.info(f"[{symbol}] L3 non déclenché")
            return None

        entry = l3["entry_price"]
        if entry <= 0:
            return None

        # ── 5. SL / TP1 / TP2 ────────────────────────────────────────────
        atr     = get_atr(feed.get_ohlcv(symbol, timeframes["ltf"]), period=14)
        if not atr:
            return None

        sl, tp1, tp2 = self._calc_sl_tp(
            side, entry, atr, l2["signals"], symbol, timeframes, feed
        )
        if not sl or not tp1 or not tp2:
            return None

        # Vérifie R/R minimum (1:2)
        risk   = abs(entry - sl)
        reward = abs(tp2 - entry)
        if risk == 0 or reward / risk < 2.0:
            logger.debug(
                f"[{symbol}] R/R insuffisant: {reward/risk:.2f} < 2.0"
            )
            return None

        # Vérifie breakeven avec frais
        breakeven = entry * (1 + self.fee_rt) if side == "long" \
                    else entry * (1 - self.fee_rt)
        if side == "long" and tp1 < breakeven:
            logger.debug(f"[{symbol}] TP1 sous breakeven frais")
            return None

        # ── 6. Confidence score ───────────────────────────────────────────
        confidence = 0.5
        confidence += l2["score"] * 0.1      # +0.1 par signal L2
        if l3["triggered"]:
            confidence += 0.15
        if regime == "hv":
            confidence += 0.05
        # Bonus si L1 aligné avec L2 (confluence HTF)
        if l1_aligned:
            confidence += 0.10
            logger.info(f"[{symbol}] L1↔L2 aligned → confidence +10%")
        else:
            # Counter-trend permis mais malus
            confidence -= 0.05
            logger.info(f"[{symbol}] L1↔L2 divergent → confidence -5% (counter-trend)")
        confidence = max(0.10, min(confidence, 0.95))
        if self.orderflow:
            of = self.orderflow.analyze(symbol, entry)
            if of.score >= 60:
                if of.side == side:
                    confidence = min(confidence + 0.10, 0.95)
                elif of.side and of.side != side:
                    confidence = max(confidence - 0.10, 0.10)

        if self.orderflow:
            tape = self.orderflow.analyze_tape(symbol)
            if tape.get("score", 0) >= 50:
                if tape.get("side") == side:
                    confidence = min(confidence + 0.05, 0.95)
                elif tape.get("side") and tape.get("side") != side:
                    confidence = max(confidence - 0.05, 0.10)

        if self.obi:
            obi = self.obi.analyze(symbol, entry)
            if obi.vpin_toxic:
                logger.debug(f"[OBI] {symbol} VPIN toxique — trade bloque")
                return None
            if obi.score >= 50:
                if obi.side == side:
                    confidence = min(confidence + 0.08, 0.95)
                elif obi.side and obi.side != side:
                    confidence = max(confidence - 0.08, 0.10)

        if self.vwap:
            ohlcv  = feed.get_ohlcv(symbol, timeframes["mtf"])
            vsig   = self.vwap.update(symbol, ohlcv)
            if vsig and vsig.score >= 40:
                if vsig.side == side:
                    confidence = min(confidence + 0.07, 0.95)
                elif vsig.side and vsig.side != side:
                    confidence = max(confidence - 0.07, 0.10)
                    # --- Module 5 — Niveaux clés ---
        ohlcv_1h = feed.get_ohlcv(symbol, timeframes["ltf"])
        ohlcv_1d = feed.get_ohlcv(symbol, "1d")
        ohlcv_1w = feed.get_ohlcv(symbol, "1w")
        self.key_levels.update(symbol, ohlcv_1h, ohlcv_1d, ohlcv_1w)
        kl_score, kl_reasons = self.key_levels.get_score(symbol, entry, side)
        kl_boost = self.key_levels.boost_confidence(kl_score)
        confidence = max(0.10, min(confidence + kl_boost, 0.95))
        if kl_reasons:
            logger.info(f"[KeyLevels] {symbol} score={kl_score} boost={kl_boost:+.2f} — {kl_reasons}")
            # --- Module 6 — Open Interest + Long/Short Ratio ---
        oi_score, oi_reasons = await self.oi_engine.analyze(symbol, entry, side)
        oi_boost = self.oi_engine.boost_confidence(oi_score)
        confidence = max(0.10, min(confidence + oi_boost, 0.95))
        if oi_reasons:
            logger.info(f"[OI] {symbol} score={oi_score} boost={oi_boost:+.2f} — {oi_reasons}")
            # --- Module 7 — Liquidation Heatmap ---
        liq_score, liq_reasons = await self.liq_engine.analyze(symbol, entry, side)
        liq_boost = self.liq_engine.boost_confidence(liq_score)
        confidence = max(0.10, min(confidence + liq_boost, 0.95))
        if liq_reasons:
            logger.info(f"[LiqHeatmap] {symbol} score={liq_score} boost={liq_boost:+.2f} — {liq_reasons}")

        logger.info(
            f"SIGNAL | {symbol} {side.upper()} | "
            f"Régime: {regime} | L2: {l2['score']}/3 | "
            f"R/R: {reward/risk:.2f} | Conf: {confidence:.0%}\n"
            f"  Entry: {entry:.4f} | SL: {sl:.4f} | "
            f"TP1: {tp1:.4f} | TP2: {tp2:.4f}"
        )

        return TradeSignal(
            symbol=symbol, side=side,
            entry=entry, sl=sl, tp=tp2,  # tp = TP2 (full target)
            tp1=tp1, tp2=tp2,
            strategy="orchestrator",
            confidence=confidence,
            regime=regime,
        )

    def _calc_sl_tp(
        self, side, entry, atr, l2_signals, symbol, timeframes, feed
    ):
        """Calcule SL sous/sur OB ou ATR × 1.5 puis TP1/TP2"""
        sl_dist = atr * 1.5

        # Préfère SL sur Order Block si disponible
        smc = l2_signals.get("smc", {})
        ob  = smc.get("order_block") if smc else None
        if ob:
            if side == "long" and ob["type"] == "bullish":
                ob_dist = entry - ob["bottom"]
                if ob_dist > 0:
                    sl_dist = max(ob_dist, sl_dist)
            elif side == "short" and ob["type"] == "bearish":
                ob_dist = ob["top"] - entry
                if ob_dist > 0:
                    sl_dist = max(ob_dist, sl_dist)

        if side == "long":
            sl  = entry - sl_dist
            tp1 = entry + sl_dist * 1.5
            tp2 = entry + sl_dist * 3.0
        else:
            sl  = entry + sl_dist
            tp1 = entry - sl_dist * 1.5
            tp2 = entry - sl_dist * 3.0

        return sl, tp1, tp2

    def _regime_allows(self, regime: str, side: str) -> bool:
        """
        En HV → momentum/breakout actif (all sides ok)
        En LV → mean reversion (on prend les deux sens)
        Normal → tout actif
        """
        return True  # Les layers filtrent déjà — on laisse passer en normal
