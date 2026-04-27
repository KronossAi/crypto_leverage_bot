"""
Circuit Breakers — protections anti-mort du bot
─────────────────────────────────────────────────
- Daily loss limit (-5% capital journalier)
- Consecutive loss limit (3 pertes → pause 2h)
- ATR volatility kill switch (spike >300%)
- OI drop emergency close (>15% en 5min)
"""
import logging
from datetime import datetime, timedelta
from typing import Optional

logger = logging.getLogger(__name__)


class CircuitBreaker:
    def __init__(self, config: dict):
        cfg                         = config["limits"]
        cap                         = config["capital"]

        self.daily_loss_pct         = cap["daily_loss_limit_pct"] / 100
        self.max_consec_losses      = cfg["max_consecutive_losses"]
        self.pause_hours            = cfg["consecutive_loss_pause_h"]
        self.atr_spike_mult         = config["regime"]["atr_spike_multiplier"]
        self.oi_drop_threshold      = config["macro"]["oi_drop_threshold_pct"] / 100

        # État journalier
        self._day_start_capital: float        = 0.0
        self._day_reset_date: Optional[datetime] = None
        self._consecutive_losses: int         = 0
        self._pause_until: Optional[datetime] = None

        # Flags
        self.daily_limit_hit        = False
        self.volatility_kill        = False
        self.oi_emergency           = False

    # ─── Init journalier ─────────────────────────────────────────────────

    def start_day(self, capital: float):
        today = datetime.utcnow().date()
        if self._day_reset_date != today:
            self._day_start_capital = capital
            self._day_reset_date    = today
            self.daily_limit_hit    = False
            self._consecutive_losses = 0
            self._pause_until        = None
            logger.info(
                f"Nouveau jour — Capital de référence : {capital:.2f} USDC"
            )

    # ─── Vérifications ───────────────────────────────────────────────────

    def can_trade(self, capital: float) -> tuple[bool, str]:
        """
        Retourne (True, "") si le trading est autorisé.
        Retourne (False, raison) si bloqué.
        """
        # 1. Daily loss limit
        if self._day_start_capital > 0:
            daily_loss = (self._day_start_capital - capital) / self._day_start_capital
            if daily_loss >= self.daily_loss_pct:
                self.daily_limit_hit = True
                return False, f"Daily loss limit atteint ({daily_loss*100:.1f}%)"

        # 2. Pause consécutive
        if self._pause_until and datetime.utcnow() < self._pause_until:
            delta     = self._pause_until - datetime.utcnow()
            remaining = max(0, int(delta.total_seconds() // 60))
            return False, f"Pause consécutive — reprend dans {remaining}min"

        # 3. Volatility kill switch
        if self.volatility_kill:
            return False, "Volatility kill switch actif (ATR spike)"

        # 4. OI emergency
        if self.oi_emergency:
            return False, "OI emergency — fermeture positions en cours"

        return True, ""

    def on_trade_result(self, pnl_usdc: float):
        """Appelé après chaque clôture de trade"""
        if pnl_usdc < 0:
            self._consecutive_losses += 1
            logger.warning(
                f"Perte consecutive #{self._consecutive_losses}"
            )
            if self._consecutive_losses >= self.max_consec_losses:
                self._pause_until = datetime.utcnow() + timedelta(
                    hours=self.pause_hours
                )
                logger.warning(
                    f"PAUSE FORCEE {self.pause_hours}h — "
                    f"{self._consecutive_losses} pertes consécutives"
                )
        else:
            # Reset si gain
            self._consecutive_losses = 0

    def on_atr_spike(self, atr_current: float, atr_avg: float):
        """Appelé à chaque calcul ATR"""
        if atr_avg > 0 and atr_current > atr_avg * self.atr_spike_mult:
            if not self.volatility_kill:
                self.volatility_kill = True
                logger.critical(
                    f"VOLATILITY KILL SWITCH — ATR: {atr_current:.4f} "
                    f"({atr_current/atr_avg:.1f}x moyenne)"
                )
        elif self.volatility_kill:
            # Reset si ATR revient à la normale
            if atr_current < atr_avg * 1.5:
                self.volatility_kill = False
                logger.info("Volatility kill switch désactivé")

    def on_oi_drop(self, drop_pct: float) -> bool:
        """
        Appelé si OI chute brutalement.
        Retourne True si fermeture d'urgence déclenchée.
        """
        if drop_pct >= self.oi_drop_threshold:
            self.oi_emergency = True
            logger.critical(
                f"OI DROP URGENCE — chute de {drop_pct*100:.1f}% "
                f"— fermeture toutes positions"
            )
            return True
        return False

    def reset_oi_emergency(self):
        self.oi_emergency = False
        logger.info("OI emergency reset")

    def resume_volatility(self):
        self.volatility_kill = False
        logger.info("Volatility kill switch reset manuel")

    def status(self) -> dict:
        return {
            "daily_limit_hit":     self.daily_limit_hit,
            "volatility_kill":     self.volatility_kill,
            "oi_emergency":        self.oi_emergency,
            "consecutive_losses":  self._consecutive_losses,
            "pause_until":         self._pause_until.isoformat() if self._pause_until else None,
        }
