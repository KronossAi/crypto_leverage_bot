"""
Risk Manager — position sizing avec TP1/TP2
─────────────────────────────────────────────────
- Sizing dynamique (% risk)
- TP1 : ferme 50% à R/R 1:1.5
- TP2 : ferme le reste à R/R 1:3
- Trailing stop si momentum fort
"""
import logging
from dataclasses import dataclass, field
from typing import Optional

logger = logging.getLogger(__name__)


@dataclass
class TradeSignal:
    symbol:     str
    side:       str
    entry:      float
    sl:         float
    tp:         float
    tp1:        float = 0.0
    tp2:        float = 0.0
    strategy:   str   = "orchestrator"
    confidence: float = 0.5
    regime:     str   = "normal"


@dataclass
class TradeOrder:
    symbol:         str
    side:           str
    size_usdc:      float
    size_contracts: float
    leverage:       int
    entry:          float
    sl:             float
    tp1:            float
    tp2:            float
    risk_usdc:      float
    rr_ratio:       float
    tp1_size:       float = 0.0  # Taille à fermer au TP1 (50%)
    tp2_size:       float = 0.0  # Taille à fermer au TP2 (50%)
    fee_estimate:   float = 0.0


class RiskManager:
    def __init__(self, config: dict):
        self.risk_pct      = config["capital"]["risk_per_trade_pct"] / 100
        self.max_dd_pct    = config["capital"]["max_drawdown_pct"] / 100
        self.max_positions = config["max_concurrent_positions"]
        self.lev_max       = config["leverage"]["max"]
        self.lev_default   = config["leverage"]["default"]
        self.initial_cap   = config["capital"]["initial"]
        self.peak_capital  = self.initial_cap
        self._halted       = False

        # Frais Hyperliquid
        self.fee_rate      = 0.0002  # 0.02% maker

    def calculate_position(
        self,
        signal:        TradeSignal,
        capital:       float,
        open_positions: int,
    ) -> Optional[TradeOrder]:

        if self._halted:
            logger.critical("Trading suspendu — drawdown max atteint")
            return None

        if open_positions >= self.max_positions:
            logger.warning(f"Max positions atteint ({self.max_positions})")
            return None

        if not self._check_drawdown(capital):
            return None

        # R/R
        if signal.side == "long":
            risk_unit = signal.entry - signal.sl
            rr_ratio  = (signal.tp2 - signal.entry) / risk_unit if risk_unit > 0 else 0
        else:
            risk_unit = signal.sl - signal.entry
            rr_ratio  = (signal.entry - signal.tp2) / risk_unit if risk_unit > 0 else 0

        if risk_unit <= 0 or rr_ratio < 2.0:
            logger.warning(f"R/R invalide {rr_ratio:.2f}")
            return None

        # Leverage fixe (maker orders → moins de risque slippage)
        leverage = self.lev_default

        # Sizing
        risk_usdc     = capital * self.risk_pct * signal.confidence
        sl_pct        = risk_unit / signal.entry
        size_usdc     = (risk_usdc / sl_pct) / leverage
        size_usdc     = min(size_usdc, capital * 0.25)

        size_contracts = (size_usdc * leverage) / signal.entry

        # Frais estimés aller-retour
        fee_estimate  = size_usdc * leverage * self.fee_rate * 2

        # Split TP1/TP2 (50%/50%)
        tp1_size = size_contracts * 0.5
        tp2_size = size_contracts * 0.5

        logger.info(
            f"Position | {signal.symbol} {signal.side.upper()} | "
            f"Size: {size_usdc:.2f} USDC | Lev: {leverage}x | "
            f"Risk: {risk_usdc:.2f} | R/R: {rr_ratio:.2f} | "
            f"Frais est.: {fee_estimate:.4f} USDC"
        )

        return TradeOrder(
            symbol=signal.symbol,
            side=signal.side,
            size_usdc=size_usdc,
            size_contracts=size_contracts,
            leverage=leverage,
            entry=signal.entry,
            sl=signal.sl,
            tp1=signal.tp1,
            tp2=signal.tp2,
            risk_usdc=risk_usdc,
            rr_ratio=rr_ratio,
            tp1_size=tp1_size,
            tp2_size=tp2_size,
            fee_estimate=fee_estimate,
        )

    def _check_drawdown(self, capital: float) -> bool:
        self.peak_capital = max(self.peak_capital, capital)
        dd = (self.peak_capital - capital) / self.peak_capital
        if dd >= self.max_dd_pct:
            self._halted = True
            logger.critical(
                f"DRAWDOWN MAX: {dd*100:.1f}% — TRADING SUSPENDU"
            )
            return False
        return True

    def update_peak(self, capital: float):
        self.peak_capital = max(self.peak_capital, capital)

    def resume(self):
        self._halted = False
        logger.info("Risk manager réactivé")