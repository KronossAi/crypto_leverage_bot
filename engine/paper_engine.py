"""
Paper Engine — simulation avec TP1/TP2 + circuit breakers
"""
import asyncio
import logging
from datetime import datetime, timedelta
from typing import Optional

from core.fsm          import TradeFSM, TradeState
from core.risk_manager import RiskManager, TradeSignal, TradeOrder
from core.portfolio    import Portfolio, ClosedTrade

logger = logging.getLogger(__name__)

FUNDING_INTERVAL_H   = 1      # Hyperliquid : funding toutes les heures
FUNDING_RATE_DEFAULT = 0.0001


class PaperEngine:
    def __init__(self, risk_manager: RiskManager, portfolio: Portfolio, config: dict):
        self.risk     = risk_manager
        self.port     = portfolio
        self.config   = config
        self.fsm      = TradeFSM()
        self._funding: dict[str, datetime] = {}

        self.max_daily_trades = config["limits"]["max_trades_per_day"]
        self.state_mgr = None  # Branché depuis bot.py

    async def on_signal(self, signal: TradeSignal, circuit_breaker=None):
        # Limite journalière
        if self.port.daily_trades_count() >= self.max_daily_trades:
            logger.warning("Limite journalière atteinte — signal ignoré")
            return

        ctx = self.fsm.get(signal.symbol, signal.strategy)
        if ctx.state != TradeState.IDLE:
            return

        # Mise à jour circuit breaker avec capital actuel
        if circuit_breaker:
            circuit_breaker.start_day(self.port.capital)
            can, reason = circuit_breaker.can_trade(self.port.capital)
            if not can:
                logger.warning(f"Circuit breaker: {reason}")
                return

        order: Optional[TradeOrder] = self.risk.calculate_position(
            signal, self.port.capital, self.fsm.count_open()
        )
        if order is None:
            return

        # Slippage simulé 0.05%
        slip        = order.entry * 0.0005
        exec_price  = order.entry + slip if signal.side == "long" else order.entry - slip

        ctx.on_signal(order.side, exec_price, order.sl, order.tp1, order.tp2)
        ctx.on_open(order.size_usdc, order.size_contracts, order.leverage, "paper")
        ctx.regime  = signal.regime

        self._funding[f"{signal.symbol}|{signal.strategy}"] = \
            datetime.utcnow() + timedelta(hours=FUNDING_INTERVAL_H)

        logger.info(
            f"[PAPER] Position ouverte | {signal.symbol} {signal.side.upper()} | "
            f"Entry: {exec_price:.4f} | SL: {order.sl:.4f} | "
            f"TP1: {order.tp1:.4f} | TP2: {order.tp2:.4f} | "
            f"Size: {order.size_usdc:.2f} USDC | Lev: {order.leverage}x | "
            f"Frais est.: {order.fee_estimate:.4f}"
        )

    async def on_tick(self, symbol: str, price: float, circuit_breaker=None):
        for ctx in self.fsm.active():
            if ctx.symbol != symbol:
                continue

            key = f"{symbol}|{ctx.strategy}"

            # ── TP1 ───────────────────────────────────────────────────────
            if not ctx.tp1_hit and ctx.tp1:
                tp1_hit = (
                    (ctx.side == "long"  and price >= ctx.tp1) or
                    (ctx.side == "short" and price <= ctx.tp1)
                )
                if tp1_hit:
                    fees = ctx.size_usdc * 0.5 * self.risk.fee_rate
                    ctx.on_tp1_hit(price, fees)
                    partial_pnl = ctx.size_usdc * 0.5 * abs(price - ctx.entry_price) / ctx.entry_price * ctx.leverage
                    self.port.capital += partial_pnl - fees
                    logger.info(
                        f"[PAPER] TP1 touche {symbol} @ {price:.4f} | "
                        f"PnL partiel: +{partial_pnl:.2f} USDC"
                    )
                    continue

            # ── SL ────────────────────────────────────────────────────────
            sl_hit = (
                (ctx.side == "long"  and price <= ctx.sl) or
                (ctx.side == "short" and price >= ctx.sl)
            )
            if sl_hit:
                await self._close(ctx, price, "SL", circuit_breaker)
                continue

            # ── TP2 ───────────────────────────────────────────────────────
            if ctx.tp2:
                tp2_hit = (
                    (ctx.side == "long"  and price >= ctx.tp2) or
                    (ctx.side == "short" and price <= ctx.tp2)
                )
                if tp2_hit:
                    await self._close(ctx, price, "TP2", circuit_breaker)
                    continue

            # ── Trailing stop (après TP1) ─────────────────────────────────
            if ctx.tp1_hit:
                await self._trailing(ctx, price)

            # ── Funding ───────────────────────────────────────────────────
            await self._funding_cost(ctx, price, key)

    async def _close(self, ctx, price: float, reason: str, circuit_breaker=None):
        fees = ctx.size_usdc * self.risk.fee_rate
        ctx.on_close(price, reason, fees)

        # Notifie circuit breaker
        if circuit_breaker:
            circuit_breaker.on_trade_result(ctx.pnl_usdc)

        trade = ClosedTrade(
            symbol=ctx.symbol, strategy=ctx.strategy,
            side=ctx.side, entry=ctx.entry_price, close=price,
            size_usdc=ctx.size_usdc, leverage=ctx.leverage,
            pnl_gross=ctx.pnl_usdc + ctx.fees_paid,
            fees_paid=ctx.fees_paid,
            pnl_net=ctx.pnl_usdc,
            pnl_pct=ctx.pnl_pct,
            rr_achieved=abs(ctx.pnl_pct * ctx.leverage) / (
                abs(ctx.entry_price - ctx.sl) / ctx.entry_price * ctx.leverage
            ) if ctx.sl and ctx.entry_price else 0.0,
            reason=reason,
            regime=ctx.regime,
            tp1_hit=ctx.tp1_hit,
            opened_at=ctx.open_time,
        )
        self.port.record_trade(trade)
        if self.state_mgr:
            self.state_mgr.save(self.port, self.fsm)
        self.risk.update_peak(self.port.capital)

        emoji = "✅" if ctx.pnl_usdc >= 0 else "❌"
        logger.info(
            f"[PAPER] {emoji} {ctx.symbol} [{reason}] | "
            f"PnL net: {ctx.pnl_usdc:+.2f} USDC | "
            f"Capital: {self.port.capital:.2f} USDC"
        )
        ctx.reset()

    async def _trailing(self, ctx, price: float):
        """Trailing stop après TP1 — protège les gains restants"""
        if ctx.state not in (TradeState.TP1_HIT, TradeState.MANAGE):
            return
        initial_risk = abs(ctx.entry_price - ctx.tp1) / 1.5
        if ctx.side == "long":
            new_sl = price - initial_risk
            if new_sl > ctx.sl:
                ctx.on_manage(new_sl)
        else:
            new_sl = price + initial_risk
            if new_sl < ctx.sl:
                ctx.on_manage(new_sl)

    async def _funding_cost(self, ctx, price: float, key: str):
        next_f = self._funding.get(key)
        if next_f and datetime.utcnow() >= next_f:
            cost = ctx.size_usdc * ctx.leverage * FUNDING_RATE_DEFAULT
            self.port.capital -= cost
            self._funding[key] = datetime.utcnow() + timedelta(hours=FUNDING_INTERVAL_H)

    def get_open_positions(self) -> list[dict]:
        return self.fsm.summary()

    def get_metrics(self) -> dict:
        return self.port.metrics()
