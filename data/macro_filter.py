"""
Filtre événements macro
─────────────────────────────────────────────────
Blackliste les entrées 30min avant et après :
- CPI, FOMC, NFP et autres événements majeurs
- Calendrier statique + override manuel possible
"""
import logging
from datetime import datetime, timedelta, timezone
from typing import Optional

logger = logging.getLogger(__name__)


# Calendrier macro UTC — à mettre à jour mensuellement
MACRO_EVENTS_UTC = [
    # Format : (année, mois, jour, heure, minute, label)
    # Exemples — remplace par les vraies dates du mois en cours
    (2026, 5, 7,  18, 30, "FOMC"),
    (2026, 5, 13, 12, 30, "CPI"),
    (2026, 5, 2,  12, 30, "NFP"),
    (2026, 6, 11, 18, 30, "FOMC"),
    (2026, 6, 11, 12, 30, "CPI"),
    (2026, 6, 6,  12, 30, "NFP"),
]


class MacroFilter:
    def __init__(self, config: dict):
        self.blackout_before = config["macro"]["blackout_minutes_before"]
        self.blackout_after  = config["macro"]["blackout_minutes_after"]
        self._manual_blackout: Optional[datetime] = None

    def is_blackout(self) -> tuple[bool, str]:
        """
        Retourne (True, label) si on est en période de blackout macro.
        Retourne (False, "") sinon.
        """
        now = datetime.now(timezone.utc).replace(tzinfo=None)

        # Override manuel
        if self._manual_blackout and now < self._manual_blackout:
            remaining = (self._manual_blackout - now).seconds // 60
            return True, f"Blackout manuel ({remaining}min restantes)"

        for (y, mo, d, h, mi, label) in MACRO_EVENTS_UTC:
            event_dt  = datetime(y, mo, d, h, mi)
            window_start = event_dt - timedelta(minutes=self.blackout_before)
            window_end   = event_dt + timedelta(minutes=self.blackout_after)

            if window_start <= now <= window_end:
                if now < event_dt:
                    mins = (event_dt - now).seconds // 60
                    return True, f"Pre-{label} ({mins}min avant)"
                else:
                    mins = (window_end - now).seconds // 60
                    return True, f"Post-{label} ({mins}min après)"

        return False, ""

    def manual_blackout(self, minutes: int):
        """Déclenche un blackout manuel (depuis Telegram)"""
        self._manual_blackout = datetime.utcnow() + timedelta(minutes=minutes)
        logger.warning(f"Blackout manuel déclenché pour {minutes}min")

    def clear_manual_blackout(self):
        self._manual_blackout = None
        logger.info("Blackout manuel levé")

    def next_event(self) -> Optional[str]:
        """Retourne le prochain événement macro"""
        now    = datetime.utcnow()
        future = []
        for (y, mo, d, h, mi, label) in MACRO_EVENTS_UTC:
            event_dt = datetime(y, mo, d, h, mi)
            if event_dt > now:
                future.append((event_dt, label))
        if not future:
            return None
        future.sort(key=lambda x: x[0])
        dt, label = future[0]
        delta = dt - now
        hours = delta.seconds // 3600
        mins  = (delta.seconds % 3600) // 60
        return f"{label} dans {delta.days}j {hours}h {mins}min"