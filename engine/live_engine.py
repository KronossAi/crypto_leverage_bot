"""
Live Engine — Exécution réelle sur Coinbase International
─────────────────────────────────────────────────────────────
- Ordres market via ExchangeClient
- SL/TP : ordres conditionnels sur l'exchange
- Sync positions au démarrage (reprend les positions ouvertes)
- Vérification périodique état positions (toutes les 30s)
- Gestion funding rate réel via API
"""
import asyncio
import logging
from datetime import datetime
from typing import Optional

from core.exchange     import ExchangeClient
from core.fsm          import TradeFSM, TradeState
from core.risk_manager import RiskManager, TradeSignal, TradeOrder
from core.portfolio    import Portfolio, ClosedTrade

logger = logging.getLogger(__name__)

POSITION_CHECK_INTERVAL = 30   # secondes
FUNDING_CHECK_INTERVAL  = 3600 # 1h


class LiveEngine:
    def __init__(
        self,
        exchange:     ExchangeClient,
        risk_manager: RiskManager,
        portfolio:    Portfolio,
    ):
        self.exchange = exchange
        self.risk     = risk_manager
        self.port     = portfolio
        self.fsm      = TradeFSM()
        self._monitor_task: Optional[asyncio.Task] = None
        self._running = False

    # ─── Démarrage ────────────────────────────────────────────────────────

    async def start(self):
        """Démarre le monitoring des positions"""
        await self._sync_existing_positions()
        self._running     = True
        self._monitor_task = asyncio.create_task(self._monitor_loop())
        logger.info("🟢 Live Engine démarré")

    async def stop(self):
        self._running = False
        if self._monitor_task:
            self._monitor_task.cancel()
            try:
                await self._monitor_task
            except asyncio.CancelledError:
                pass
        logger.info("🔴 Live Engine arrêté")

    # ─── Signal → Ordre ───────────────────────────────────────────────────

    async def on_signal(self, signal: TradeSignal):
        """Reçoit un signal et exécute un ordre réel"""
        ctx = self.fsm.get(signal.symbol, signal.strategy)

        if ctx.state != TradeState.IDLE:
            logger.debug(f"[LIVE] {signal.symbol}/{signal.strategy} déjà actif")
            return

        # Solde réel
        balance = await self.exchange.get_balance()
        if balance <= 0:
            logger.error("[LIVE] Solde USDC nul ou inaccessible")
            return

        order: Optional[TradeOrder] = self.risk.calculate_position(
            signal, balance, self.fsm.count_open()
        )
        if order is None:
            return

        # ── Configuration levier ──────────────────────────────────────────
        await self.exchange.set_leverage(order.symbol, order.leverage)

        # ── Placement de l'ordre ──────────────────────────────────────────
        exec_side = "buy" if order.side == "long" else "sell"

        result = await self.exchange.place_order(
            symbol     = order.symbol,
            side       = exec_side,
            amount     = order.size_contracts,
            order_type = "market",
            sl         = order.sl,
            tp         = order.tp,
        )

        if result is None:
            logger.error(f"[LIVE] ❌ Ordre échoué pour {order.symbol}")
            return

        order_id   = result.get("id", "unknown")
        exec_price = float(result.get("average", order.entry) or order.entry)

        ctx.on_signal(order.side, exec_price, order.sl, order.tp)
        ctx.on_open(order.size_usdc, order.leverage, order_id=order_id)

        logger.info(
            f"[LIVE] ✅ Ordre exécuté | {order.symbol} {order.side.upper()} | "
            f"Entry: {exec_price:.4f} | SL: {order.sl:.4f} | TP: {order.tp:.4f} | "
            f"Size: {order.size_usdc:.2f} USDC | Levier: {order.leverage}x | "
            f"ID: {order_id}"
        )

    # ─── Monitoring ───────────────────────────────────────────────────────

    async def _monitor_loop(self):
        """
        Boucle de monitoring :
        - Vérifie l'état des positions toutes les 30s
        - Détecte les SL/TP touchés côté exchange
        - Met à jour trailing stop si nécessaire
        """
        check_count = 0
        while self._running:
            try:
                await self._check_positions()
                check_count += 1

                # Vérif funding toutes les heures
                if check_count % (FUNDING_CHECK_INTERVAL // POSITION_CHECK_INTERVAL) == 0:
                    await self._log_funding_rates()

            except Exception as e:
                logger.error(f"[LIVE] Erreur monitoring: {e}")

            await asyncio.sleep(POSITION_CHECK_INTERVAL)

    async def _check_positions(self):
        """Réconcilie les positions FSM avec l'exchange"""
        exchange_positions = await self.exchange.get_positions()
        exchange_symbols   = {p["symbol"] for p in exchange_positions}

        for ctx in self.fsm.active():
            if ctx.symbol not in exchange_symbols:
                # Position fermée côté exchange (SL/TP touché)
                ticker = await self.exchange.get_ticker(ctx.symbol)
                close_price = float(ticker["last"]) if ticker else ctx.entry_price

                # Détermine la raison de fermeture
                if ctx.side == "long":
                    reason = "TP" if close_price >= ctx.tp * 0.99 else "SL"
                else:
                    reason = "TP" if close_price <= ctx.tp * 1.01 else "SL"

                await self._record_close(ctx, close_price, reason)
            else:
                # Position toujours ouverte → trailing stop
                ticker = await self.exchange.get_ticker(ctx.symbol)
                if ticker:
                    price = float(ticker["last"])
                    await self._update_trailing_live(ctx, price)

    async def _update_trailing_live(self, ctx, price: float):
        """
        Trailing stop live : modifie le SL sur l'exchange si besoin.
        """
        initial_risk = abs(ctx.entry_price - ctx.sl)

        if ctx.side == "long":
            profit = price - ctx.entry_price
            if profit >= initial_risk:
                new_sl = price - initial_risk * 0.8
                if new_sl > ctx.sl:
                    # Annule et remplace le SL sur l'exchange
                    await self._replace_sl(ctx, new_sl)
                    ctx.on_manage(new_sl=new_sl)
        else:
            profit = ctx.entry_price - price
            if profit >= initial_risk:
                new_sl = price + initial_risk * 0.8
                if new_sl < ctx.sl:
                    await self._replace_sl(ctx, new_sl)
                    ctx.on_manage(new_sl=new_sl)

    async def _replace_sl(self, ctx, new_sl: float):
        """Remplace l'ordre SL sur l'exchange"""
        try:
            close_side = "sell" if ctx.side == "long" else "buy"
            await self.exchange.place_order(
                symbol     = ctx.symbol,
                side       = close_side,
                amount     = ctx.size_usdc / ctx.entry_price * ctx.leverage,
                order_type = "stop_market",
                price      = new_sl,
            )
            logger.info(
                f"[LIVE] Trailing SL mis à jour {ctx.symbol}: {new_sl:.4f}"
            )
        except Exception as e:
            logger.error(f"[LIVE] Erreur replace_sl {ctx.symbol}: {e}")

    async def _record_close(self, ctx, close_price: float, reason: str):
        """Enregistre la fermeture dans le portfolio"""
        ctx.on_close(close_price, reason)

        balance      = await self.exchange.get_balance()
        self.port.capital = balance  # Sync solde réel

        trade = ClosedTrade(
            symbol      = ctx.symbol,
            strategy    = ctx.strategy,
            side        = ctx.side,
            entry       = ctx.entry_price,
            close       = close_price,
            size_usdc   = ctx.size_usdc,
            leverage    = ctx.leverage,
            pnl_usdc    = ctx.pnl_usdc,
            pnl_pct     = ctx.pnl_pct,
            rr_achieved = 0.0,
            reason      = reason,
            opened_at   = ctx.open_time,
        )
        self.port.record_trade(trade)
        self.risk.update_peak(self.port.capital)

        emoji = "✅" if ctx.pnl_usdc >= 0 else "❌"
        logger.info(
            f"[LIVE] {emoji} Clôture détectée | {ctx.symbol} [{reason}] | "
            f"PnL: {ctx.pnl_usdc:+.2f} USDC"
        )
        ctx.reset()

    async def _sync_existing_positions(self):
        """
        Au démarrage du live engine, récupère les positions
        déjà ouvertes sur l'exchange pour les suivre.
        """
        positions = await self.exchange.get_positions()
        for pos in positions:
            symbol   = pos.get("symbol")
            side_raw = pos.get("side", "long")
            side     = "long" if side_raw == "long" else "short"
            entry    = float(pos.get("entryPrice", 0))
            size     = float(pos.get("notional", 0))
            leverage = int(pos.get("leverage", 1))

            if not symbol or entry == 0:
                continue

            ctx = self.fsm.get(symbol, "recovered")
            ctx.on_signal(side, entry, entry * 0.97, entry * 1.03)  # SL/TP par défaut
            ctx.on_open(size, leverage, order_id="recovered")
            logger.warning(
                f"[LIVE] ⚠️ Position récupérée: {symbol} {side.upper()} "
                f"@ {entry} — SL/TP par défaut appliqués, vérifiez manuellement"
            )

    async def _log_funding_rates(self):
        """Log les funding rates actuels pour info"""
        for ctx in self.fsm.active():
            rate = await self.exchange.get_funding_rate(ctx.symbol)
            if rate is not None:
                cost_h = ctx.size_usdc * ctx.leverage * abs(rate)
                logger.info(
                    f"[LIVE] Funding {ctx.symbol}: {rate*100:.4f}% | "
                    f"Coût estimé: {cost_h:.4f} USDC/période"
                )

    def get_open_positions(self) -> list[dict]:
        return self.fsm.summary()

    def get_metrics(self) -> dict:
        return self.port.metrics()