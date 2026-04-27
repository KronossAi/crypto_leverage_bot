"""
State Manager — persistance de l'etat entre redemarrages
"""
import json
import logging
import os
from datetime import datetime
from typing import Optional

from core.portfolio import Portfolio, ClosedTrade
from core.fsm       import TradeFSM, TradeContext, TradeState

logger = logging.getLogger(__name__)

STATE_FILE = os.path.join(
    os.path.dirname(__file__), "..", "data", "state.json"
)


class StateManager:
    def __init__(self, state_file: str = STATE_FILE):
        self.path = os.path.abspath(state_file)
        os.makedirs(os.path.dirname(self.path), exist_ok=True)

    def save(self, portfolio: Portfolio, fsm: TradeFSM):
        try:
            state = {
                "saved_at":        datetime.utcnow().isoformat(),
                "capital":         portfolio.capital,
                "initial_capital": portfolio.initial_capital,
                "trades":          [self._trade_to_dict(t) for t in portfolio.trades],
                "positions":       [self._ctx_to_dict(c) for c in fsm.active()],
            }
            with open(self.path, "w") as f:
                json.dump(state, f, indent=2)
            logger.debug(f"Etat sauvegarde -> {self.path}")
        except Exception as e:
            logger.error(f"Erreur sauvegarde etat: {e}")

    def load(self, portfolio: Portfolio, fsm: TradeFSM) -> bool:
        if not os.path.exists(self.path):
            logger.info("Pas d'etat sauvegarde — demarrage frais")
            return False
        try:
            with open(self.path) as f:
                state = json.load(f)
            portfolio.capital         = state["capital"]
            portfolio.initial_capital = state["initial_capital"]
            portfolio.trades          = [
                self._dict_to_trade(t) for t in state.get("trades", [])
            ]
            for pos in state.get("positions", []):
                ctx = fsm.get(pos["symbol"], pos["strategy"])
                ctx.side        = pos["side"]
                ctx.entry_price = pos["entry_price"]
                ctx.sl          = pos["sl"]
                ctx.tp1         = pos["tp1"]
                ctx.tp2         = pos["tp2"]
                ctx.size_usdc   = pos["size_usdc"]
                ctx.leverage    = pos["leverage"]
                ctx.tp1_hit     = pos["tp1_hit"]
                ctx.tp1_pnl     = pos.get("tp1_pnl", 0.0)
                ctx.regime      = pos["regime"]
                ctx.open_time   = (
                    datetime.fromisoformat(pos["open_time"])
                    if pos.get("open_time") else datetime.utcnow()
                )
                ctx.state = TradeState[pos["state"]]
            logger.info(
                f"Etat restaure | Capital: {portfolio.capital:.2f} USDC | "
                f"Positions: {len(state.get('positions', []))} | "
                f"Trades: {len(state.get('trades', []))}"
            )
            return True
        except Exception as e:
            logger.error(f"Erreur chargement etat: {e}")
            return False

    def reset_paper(self, portfolio: Portfolio, fsm: TradeFSM, initial_capital: float):
        portfolio.capital         = initial_capital
        portfolio.initial_capital = initial_capital
        portfolio.trades          = []
        portfolio._daily_trades   = 0
        portfolio._day_date       = None
        for ctx in list(fsm.active()):
            ctx.reset()
        if os.path.exists(self.path):
            os.remove(self.path)
        logger.info(f"Paper trading reset — Capital: {initial_capital:.2f} USDC")

    def _trade_to_dict(self, t: ClosedTrade) -> dict:
        return {
            "symbol":      t.symbol,
            "strategy":    t.strategy,
            "side":        t.side,
            "entry":       t.entry,
            "close":       t.close,
            "size_usdc":   t.size_usdc,
            "leverage":    t.leverage,
            "pnl_gross":   t.pnl_gross,
            "fees_paid":   t.fees_paid,
            "pnl_net":     t.pnl_net,
            "pnl_pct":     t.pnl_pct,
            "rr_achieved": t.rr_achieved,
            "reason":      t.reason,
            "regime":      t.regime,
            "tp1_hit":     t.tp1_hit,
            "opened_at":   t.opened_at.isoformat() if t.opened_at else None,
            "closed_at":   t.closed_at.isoformat() if t.closed_at else None,
        }

    def _dict_to_trade(self, d: dict) -> ClosedTrade:
        return ClosedTrade(
            symbol      = d["symbol"],
            strategy    = d["strategy"],
            side        = d["side"],
            entry       = d["entry"],
            close       = d["close"],
            size_usdc   = d["size_usdc"],
            leverage    = d["leverage"],
            pnl_gross   = d["pnl_gross"],
            fees_paid   = d["fees_paid"],
            pnl_net     = d["pnl_net"],
            pnl_pct     = d["pnl_pct"],
            rr_achieved = d["rr_achieved"],
            reason      = d["reason"],
            regime      = d["regime"],
            tp1_hit     = d["tp1_hit"],
            opened_at   = (
                datetime.fromisoformat(d["opened_at"])
                if d.get("opened_at") else datetime.utcnow()
            ),
            closed_at   = (
                datetime.fromisoformat(d["closed_at"])
                if d.get("closed_at") else datetime.utcnow()
            ),
        )

    def _ctx_to_dict(self, ctx: TradeContext) -> dict:
        return {
            "symbol":      ctx.symbol,
            "strategy":    ctx.strategy,
            "state":       ctx.state.name,
            "side":        ctx.side,
            "entry_price": ctx.entry_price,
            "sl":          ctx.sl,
            "tp1":         ctx.tp1,
            "tp2":         ctx.tp2,
            "size_usdc":   ctx.size_usdc,
            "leverage":    ctx.leverage,
            "tp1_hit":     ctx.tp1_hit,
            "tp1_pnl":     ctx.tp1_pnl,
            "regime":      ctx.regime,
            "open_time":   ctx.open_time.isoformat() if ctx.open_time else None,
        }
