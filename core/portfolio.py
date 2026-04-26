"""
Portfolio — capital, PnL, frais réels, métriques
"""
from dataclasses import dataclass, field
from datetime import datetime
from typing import Optional
import logging

logger = logging.getLogger(__name__)


@dataclass
class ClosedTrade:
    symbol:      str
    strategy:    str
    side:        str
    entry:       float
    close:       float
    size_usdc:   float
    leverage:    int
    pnl_gross:   float
    fees_paid:   float
    pnl_net:     float
    pnl_pct:     float
    rr_achieved: float
    reason:      str
    regime:      str
    tp1_hit:     bool
    opened_at:   datetime
    closed_at:   datetime = field(default_factory=datetime.utcnow)


class Portfolio:
    def __init__(self, initial_capital: float):
        self.initial_capital = initial_capital
        self.capital         = initial_capital
        self.trades: list[ClosedTrade] = []
        self._daily_trades   = 0
        self._day_date       = None

    def add_capital(self, amount: float):
        self.capital += amount
        logger.info(f"Apport: +{amount} USDC — Capital: {self.capital:.2f}")

    def record_trade(self, trade: ClosedTrade):
        self.capital += trade.pnl_net
        self.trades.append(trade)

        # Compteur journalier
        today = datetime.utcnow().date()
        if self._day_date != today:
            self._day_date   = today
            self._daily_trades = 0
        self._daily_trades += 1

        emoji = "✅" if trade.pnl_net >= 0 else "❌"
        tp1_tag = " [TP1]" if trade.tp1_hit else ""
        logger.info(
            f"{emoji} Trade fermé{tp1_tag} | {trade.symbol} {trade.side.upper()} | "
            f"PnL net: {trade.pnl_net:+.2f} USDC | "
            f"Frais: {trade.fees_paid:.4f} | "
            f"Raison: {trade.reason} | "
            f"Régime: {trade.regime}"
        )

    def daily_trades_count(self) -> int:
        today = datetime.utcnow().date()
        if self._day_date != today:
            return 0
        return self._daily_trades

    def metrics(self) -> dict:
        base = {
            "capital":          round(self.capital, 2),
            "total_pnl":        0.0,
            "total_fees":       0.0,
            "roi_pct":          round(
                (self.capital - self.initial_capital) / self.initial_capital * 100, 2
            ),
            "total_trades":     len(self.trades),
            "daily_trades":     self.daily_trades_count(),
            "win_rate":         0.0,
            "avg_win":          0.0,
            "avg_loss":         0.0,
            "profit_factor":    0.0,
            "max_drawdown_pct": 0.0,
            "tp1_rate":         0.0,
        }

        if not self.trades:
            return base

        wins   = [t for t in self.trades if t.pnl_net > 0]
        losses = [t for t in self.trades if t.pnl_net <= 0]
        tp1_hits = [t for t in self.trades if t.tp1_hit]

        total_pnl   = sum(t.pnl_net for t in self.trades)
        total_fees  = sum(t.fees_paid for t in self.trades)
        win_rate    = len(wins) / len(self.trades) * 100
        avg_win     = sum(t.pnl_net for t in wins) / len(wins) if wins else 0
        avg_loss    = sum(t.pnl_net for t in losses) / len(losses) if losses else 0
        gross_wins  = abs(sum(t.pnl_net for t in wins))
        gross_loss  = abs(sum(t.pnl_net for t in losses))
        pf          = gross_wins / gross_loss if gross_loss > 0 else 0.0

        peak, max_dd = self.initial_capital, 0.0
        running      = self.initial_capital
        for t in self.trades:
            running += t.pnl_net
            peak     = max(peak, running)
            dd       = (peak - running) / peak if peak > 0 else 0
            max_dd   = max(max_dd, dd)

        return {
            "capital":          round(self.capital, 2),
            "total_pnl":        round(total_pnl, 2),
            "total_fees":       round(total_fees, 4),
            "roi_pct":          round(
                (self.capital - self.initial_capital) / self.initial_capital * 100, 2
            ),
            "total_trades":     len(self.trades),
            "daily_trades":     self.daily_trades_count(),
            "win_rate":         round(win_rate, 1),
            "avg_win":          round(avg_win, 2),
            "avg_loss":         round(avg_loss, 2),
            "profit_factor":    round(pf, 2),
            "max_drawdown_pct": round(max_dd * 100, 2),
            "tp1_rate":         round(len(tp1_hits) / len(self.trades) * 100, 1),
        }

    def log_metrics(self):
        m = self.metrics()
        logger.info(
            f"Portfolio | Capital: {m['capital']} USDC | "
            f"ROI: {m['roi_pct']}% | WR: {m['win_rate']}% | "
            f"PF: {m['profit_factor']} | DD: {m['max_drawdown_pct']}% | "
            f"Frais: {m['total_fees']} USDC"
        )