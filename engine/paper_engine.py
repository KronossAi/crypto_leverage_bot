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

FUNDING_INTERVAL_H   = 1
FUNDING_RATE_DEFAULT = 0.0001


class PaperEngine:
    def __init__(self, risk_manager: RiskManager, portfolio: Portfolio, config: dict, telegram=None):
        self.risk     = risk_manager
        self.port     = portfolio
        self.config   = config
        self.fsm      = TradeFSM()
        self._funding: dict[str, datetime] = {}
        self.max_daily_trades = config["limits"]["max_trades_per_day"]
        self.state_mgr = None
        self.telegram  = telegram  # injecté pour notifications

    async def on_signal(self, signal: TradeSignal, circuit_breaker=None):
        logger.info(
            f"[PAPER] Signal recu | {signal.symbol} {signal.side.upper()} | "
            f"Conf: {signal.confidence}%"
        )
        if self.port.daily_trades_count() >= self.max_daily_trades:
            logger.warning("Limite journaliere atteinte — signal ignore")
            return
        ctx = self.fsm.get(signal.symbol, signal.strategy)
        if ctx.state != TradeState.IDLE:
            return
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
        slip       = order.entry * 0.0005
        exec_price = order.entry + slip if signal.side == "long" else order.entry - slip
        ctx.on_signal(order.side, exec_price, order.sl, order.tp1, order.tp2)
        ctx.on_open(order.size_usdc, order.size_contracts, order.leverage, "paper")
        ctx.regime = signal.regime
        self._funding[f"{signal.symbol}|{signal.strategy}"] = (
            datetime.utcnow() + timedelta(hours=FUNDING_INTERVAL_H)
        )
        logger.info(
            f"[PAPER] Position ouverte | {signal.symbol} {signal.side.upper()} | "
            f"Entry: {exec_price:.4f} | SL: {order.sl:.4f} | "
            f"TP1: {order.tp1:.4f} | TP2: {order.tp2:.4f} | "
            f"Size: {order.size_usdc:.2f} USDC | Lev: {order.leverage}x"
        )
        # Notification Telegram d'ouverture (1x par position réelle)
        if self.telegram:
            try:
                await self.telegram.notify_trade_open(
                    symbol=signal.symbol, side=signal.side,
                    entry=exec_price, sl=order.sl,
                    tp=order.tp2, size_usdc=order.size_usdc,
                    leverage=order.leverage, strategy=signal.strategy,
                    confidence=signal.confidence,
                )
            except Exception as e:
                logger.error(f"[Telegram] Erreur notify_trade_open: {e}")
                # Persiste l'état du FSM
        if self.state_mgr:
            self.state_mgr.save(self.port, self.fsm)

    async def on_tick(self, symbol: str, price: float, circuit_breaker=None):
        logger.debug(f"[PAPER] Tick | {symbol} @ {price:.4f}")
        for ctx in list(self.fsm.active()):
            if ctx.symbol != symbol:
                continue
            key = f"{symbol}|{ctx.strategy}"
            if not ctx.tp1_hit and ctx.tp1:
                tp1_hit = (
                    (ctx.side == "long"  and price >= ctx.tp1) or
                    (ctx.side == "short" and price <= ctx.tp1)
                )
                if tp1_hit:
                    fees        = ctx.size_usdc * 0.5 * self.risk.fee_rate
                    partial_pnl = (
                        ctx.size_usdc * 0.5
                        * abs(price - ctx.entry_price) / ctx.entry_price
                        * ctx.leverage
                    ) - fees
                    self.port.capital += partial_pnl
                    ctx.on_tp1_hit(price, fees, partial_pnl)
                    logger.info(
                        f"[PAPER] TP1 touche {symbol} @ {price:.4f} | "
                        f"PnL partiel net: +{partial_pnl:.2f} USDC"
                    )
                    # Notification Telegram TP1 hit
                    if self.telegram:
                        try:
                            await self.telegram.send_message(
                                f"TP1 touche\n\n"
                                f"Paire    : {symbol}\n"
                                f"Direction: {ctx.side.upper()}\n"
                                f"Prix TP1 : {price:.4f}\n"
                                f"PnL part.: +{partial_pnl:.2f} USDC\n"
                                f"SL deplace a breakeven\n"
                                f"Capital  : {self.port.capital:.2f} USDC"
                            )
                        except Exception as e:
                            logger.error(f"[Telegram] Erreur notif TP1: {e}")
                    continue
            sl_hit = (
                (ctx.side == "long"  and price <= ctx.sl) or
                (ctx.side == "short" and price >= ctx.sl)
            )
            if sl_hit:
                await self._close(ctx, price, "SL", circuit_breaker)
                continue
            if ctx.tp2:
                tp2_hit = (
                    (ctx.side == "long"  and price >= ctx.tp2) or
                    (ctx.side == "short" and price <= ctx.tp2)
                )
                if tp2_hit:
                    await self._close(ctx, price, "TP2", circuit_breaker)
                    continue
            if ctx.tp1_hit:
                await self._trailing(ctx, price)
            await self._funding_cost(ctx, key)

    async def _close(self, ctx, price: float, reason: str, circuit_breaker=None):
        fees = ctx.size_usdc * 0.5 * self.risk.fee_rate if ctx.tp1_hit \
               else ctx.size_usdc * self.risk.fee_rate
        ctx.on_close(price, reason, fees)
        remaining_pnl = (
            ctx.size_usdc * 0.5 * ctx.pnl_pct * ctx.leverage
        ) if ctx.tp1_hit else (
            ctx.size_usdc * ctx.pnl_pct * ctx.leverage
        )
        net_remaining = remaining_pnl - fees
        self.port.capital += net_remaining
        if circuit_breaker:
            circuit_breaker.on_trade_result(ctx.pnl_usdc)
        trade = ClosedTrade(
            symbol      = ctx.symbol,
            strategy    = ctx.strategy,
            side        = ctx.side,
            entry       = ctx.entry_price,
            close       = price,
            size_usdc   = ctx.size_usdc,
            leverage    = ctx.leverage,
            pnl_gross   = ctx.pnl_usdc + ctx.fees_paid,
            fees_paid   = ctx.fees_paid,
            pnl_net     = ctx.pnl_usdc,
            pnl_pct     = ctx.pnl_pct,
            rr_achieved = (
                abs(ctx.pnl_pct * ctx.leverage) /
                (abs(ctx.entry_price - ctx.sl) / ctx.entry_price * ctx.leverage)
            ) if ctx.sl and ctx.entry_price else 0.0,
            reason      = reason,
            regime      = ctx.regime,
            tp1_hit     = ctx.tp1_hit,
            opened_at   = ctx.open_time,
        )
        self.port.record_trade(trade)
        if self.state_mgr:
            self.state_mgr.save(self.port, self.fsm)
        self.risk.update_peak(self.port.capital)
        emoji = "✅" if ctx.pnl_usdc >= 0 else "❌"
        logger.info(
            f"[PAPER] {emoji} {ctx.symbol} [{reason}] | "
            f"PnL net total: {ctx.pnl_usdc:+.2f} USDC | "
            f"Capital: {self.port.capital:.2f} USDC"
        )
        # Notification Telegram de fermeture
        if self.telegram:
            try:
                await self.telegram.notify_trade_close(
                    symbol=ctx.symbol, side=ctx.side,
                    entry=ctx.entry_price, close=price,
                    pnl_usdc=ctx.pnl_usdc, pnl_pct=ctx.pnl_pct,
                    reason=reason, strategy=ctx.strategy,
                    capital=self.port.capital,
                )
            except Exception as e:
                logger.error(f"[Telegram] Erreur notify_trade_close: {e}")
        ctx.reset()

    async def _trailing(self, ctx, price: float):
        if ctx.state not in (TradeState.TP1_HIT, TradeState.MANAGE):
            return
        initial_risk = abs(ctx.entry_price - ctx.tp1) / 1.5
        if ctx.side == "long":
            new_sl = price - initial_risk
            if new_sl > ctx.sl:
                ctx.on_manage(new_sl)
                logger.info(f"[PAPER] Trailing SL {ctx.symbol} -> {new_sl:.4f}")
        else:
            new_sl = price + initial_risk
            if new_sl < ctx.sl:
                ctx.on_manage(new_sl)
                logger.info(f"[PAPER] Trailing SL {ctx.symbol} -> {new_sl:.4f}")

    async def _funding_cost(self, ctx, key: str):
        next_f = self._funding.get(key)
        if next_f and datetime.utcnow() >= next_f:
            cost = ctx.size_usdc * ctx.leverage * FUNDING_RATE_DEFAULT
            self.port.capital -= cost
            self._funding[key] = datetime.utcnow() + timedelta(hours=FUNDING_INTERVAL_H)

    def get_open_positions(self) -> list[dict]:
        return self.fsm.summary()

    def get_metrics(self) -> dict:
        return self.port.metrics()
