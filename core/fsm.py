"""
FSM — cycle de vie avec TP1/TP2
IDLE → SIGNAL → OPEN → TP1_HIT → MANAGE → CLOSE
"""
from enum import Enum, auto
from dataclasses import dataclass, field
from typing import Optional
from datetime import datetime
import logging

logger = logging.getLogger(__name__)


class TradeState(Enum):
    IDLE    = auto()
    SIGNAL  = auto()
    OPEN    = auto()
    TP1_HIT = auto()   # 50% fermé, trailing sur le reste
    MANAGE  = auto()   # Trailing stop actif
    CLOSE   = auto()


@dataclass
class TradeContext:
    symbol:        str
    strategy:      str
    state:         TradeState = TradeState.IDLE
    side:          Optional[str]   = None
    entry_price:   Optional[float] = None
    sl:            Optional[float] = None
    tp1:           Optional[float] = None
    tp2:           Optional[float] = None
    size_usdc:     float = 0.0
    size_contracts: float = 0.0
    leverage:      int   = 1
    open_time:     Optional[datetime] = None
    close_price:   Optional[float] = None
    pnl_usdc:      float = 0.0
    pnl_pct:       float = 0.0
    fees_paid:     float = 0.0
    close_reason:  Optional[str] = None
    order_id:      Optional[str] = None
    tp1_order_id:  Optional[str] = None
    tp2_order_id:  Optional[str] = None
    tp1_hit:       bool  = False
    regime:        str   = "normal"
    history:       list  = field(default_factory=list)

    def transition(self, new_state: TradeState, **meta):
        old        = self.state
        self.state = new_state
        self.history.append({
            "from": old.name, "to": new_state.name,
            "ts": datetime.utcnow().isoformat(), **meta
        })
        logger.info(f"[{self.symbol}] {old.name} → {new_state.name}")

    def on_signal(self, side, entry, sl, tp1, tp2):
        self.side        = side
        self.entry_price = entry
        self.sl          = sl
        self.tp1         = tp1
        self.tp2         = tp2
        self.transition(TradeState.SIGNAL)

    def on_open(self, size_usdc, size_contracts, leverage, order_id=None):
        self.size_usdc      = size_usdc
        self.size_contracts = size_contracts
        self.leverage       = leverage
        self.order_id       = order_id
        self.open_time      = datetime.utcnow()
        self.transition(TradeState.OPEN)

    def on_tp1_hit(self, price: float, fees: float = 0.0):
        """50% de la position fermée"""
        self.tp1_hit   = True
        self.fees_paid += fees
        # Déplace le SL au breakeven
        self.sl        = self.entry_price
        self.transition(TradeState.TP1_HIT, tp1_price=price)

    def on_manage(self, new_sl: float):
        self.sl = new_sl
        self.transition(TradeState.MANAGE, sl=new_sl)

    def on_close(self, close_price: float, reason: str, fees: float = 0.0):
        self.close_price   = close_price
        self.close_reason  = reason
        self.fees_paid    += fees
        if self.entry_price and self.side:
            if self.side == "long":
                self.pnl_pct = (close_price - self.entry_price) / self.entry_price
            else:
                self.pnl_pct = (self.entry_price - close_price) / self.entry_price
            gross_pnl    = self.size_usdc * self.pnl_pct * self.leverage
            self.pnl_usdc = gross_pnl - self.fees_paid
        self.transition(TradeState.CLOSE, reason=reason, pnl=round(self.pnl_usdc, 4))

    def reset(self):
        self.side = self.entry_price = self.sl = self.tp1 = self.tp2 = None
        self.size_usdc = self.pnl_usdc = self.pnl_pct = self.fees_paid = 0.0
        self.size_contracts = 0.0
        self.open_time = self.close_price = self.order_id = None
        self.tp1_hit   = False
        self.transition(TradeState.IDLE)


class TradeFSM:
    def __init__(self):
        self._ctx: dict[str, TradeContext] = {}

    def get(self, symbol: str, strategy: str) -> TradeContext:
        key = f"{symbol}|{strategy}"
        if key not in self._ctx:
            self._ctx[key] = TradeContext(symbol=symbol, strategy=strategy)
        return self._ctx[key]

    def active(self) -> list[TradeContext]:
        return [
            c for c in self._ctx.values()
            if c.state not in (TradeState.IDLE, TradeState.CLOSE)
        ]

    def count_open(self) -> int:
        return len(self.active())

    def summary(self) -> list[dict]:
        return [
            {
                "symbol":   c.symbol,
                "strategy": c.strategy,
                "state":    c.state.name,
                "side":     c.side,
                "entry":    c.entry_price,
                "sl":       c.sl,
                "tp1":      c.tp1,
                "tp2":      c.tp2,
                "pnl":      round(c.pnl_usdc, 4),
                "tp1_hit":  c.tp1_hit,
                "regime":   c.regime,
            }
            for c in self._ctx.values()
            if c.state != TradeState.IDLE
        ]