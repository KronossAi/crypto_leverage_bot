# data/scalp_oi.py
# Module 6 — Open Interest + Long/Short Ratio
# Sources : Binance Futures API publique (pas de clé requise)
# Signaux : OI spike, OI divergence, L/S ratio contrarian, Taker B/S ratio

import logging
import asyncio
import aiohttp
from datetime import datetime, timezone
from collections import defaultdict, deque
from typing import Optional

logger = logging.getLogger(__name__)

BINANCE_FAPI = "https://fapi.binance.com"


class OIEngine:
    """
    Récupère et analyse l'Open Interest + ratios Long/Short Binance.

    Détecte :
    - OI Spike         : hausse OI > seuil → continuation
    - OI Divergence    : OI monte + prix baisse → reversal baissier
                         OI baisse + prix monte → reversal haussier
    - L/S Ratio        : >75% longs → contrarian short / <25% longs → contrarian long
    - Taker B/S Ratio  : >1.3 → acheteurs agressifs / <0.7 → vendeurs agressifs
    """

    # Seuils
    OI_SPIKE_PCT       = 0.02    # +2% OI en 5m = spike
    OI_DROP_PCT        = -0.02   # -2% OI en 5m = drop
    LS_LONG_EXTREME    = 0.75    # >75% longs → contrarian short
    LS_SHORT_EXTREME   = 0.25    # <25% longs → contrarian long
    TAKER_BUY_STRONG   = 1.3     # ratio acheteurs agressifs
    TAKER_SELL_STRONG  = 0.7     # ratio vendeurs agressifs
    HISTORY_SIZE       = 20      # nombre de points OI gardés en mémoire

    # Scores
    SCORE_OI_SPIKE         = 30
    SCORE_OI_DIVERGENCE    = 35
    SCORE_LS_CONTRARIAN    = 25
    SCORE_TAKER_RATIO      = 20

    def __init__(self):
        # Historique OI par symbol : deque de (timestamp, oi_value)
        self._oi_history: dict[str, deque] = defaultdict(
            lambda: deque(maxlen=self.HISTORY_SIZE)
        )
        self._last_fetch: dict[str, float] = {}
        self._fetch_interval = 30  # secondes minimum entre deux fetches

    # ------------------------------------------------------------------
    # FETCH DONNÉES BINANCE
    # ------------------------------------------------------------------

    async def _fetch_oi_history(self, symbol: str) -> list:
        """Récupère l'historique OI 5m depuis Binance."""
        url = f"{BINANCE_FAPI}/futures/data/openInterestHist"
        params = {
            "symbol": symbol,
            "period": "5m",
            "limit": 10,
        }
        try:
            async with aiohttp.ClientSession() as session:
                async with session.get(url, params=params, timeout=aiohttp.ClientTimeout(total=5)) as resp:
                    if resp.status == 200:
                        return await resp.json()
                    else:
                        logger.warning(f"[OI] HTTP {resp.status} pour {symbol}")
                        return []
        except Exception as e:
            logger.warning(f"[OI] Erreur fetch OI {symbol}: {e}")
            return []

    async def _fetch_ls_ratio(self, symbol: str) -> Optional[dict]:
        """Récupère le Global Long/Short Account Ratio."""
        url = f"{BINANCE_FAPI}/futures/data/globalLongShortAccountRatio"
        params = {
            "symbol": symbol,
            "period": "5m",
            "limit": 1,
        }
        try:
            async with aiohttp.ClientSession() as session:
                async with session.get(url, params=params, timeout=aiohttp.ClientTimeout(total=5)) as resp:
                    if resp.status == 200:
                        data = await resp.json()
                        return data[0] if data else None
                    return None
        except Exception as e:
            logger.warning(f"[OI] Erreur fetch L/S ratio {symbol}: {e}")
            return None

    async def _fetch_taker_ratio(self, symbol: str) -> Optional[dict]:
        """Récupère le Taker Buy/Sell Volume Ratio."""
        url = f"{BINANCE_FAPI}/futures/data/takerlongshortRatio"
        params = {
            "symbol": symbol,
            "period": "5m",
            "limit": 1,
        }
        try:
            async with aiohttp.ClientSession() as session:
                async with session.get(url, params=params, timeout=aiohttp.ClientTimeout(total=5)) as resp:
                    if resp.status == 200:
                        data = await resp.json()
                        return data[0] if data else None
                    return None
        except Exception as e:
            logger.warning(f"[OI] Erreur fetch Taker ratio {symbol}: {e}")
            return None

    # ------------------------------------------------------------------
    # ANALYSE PRINCIPALE
    # ------------------------------------------------------------------

    async def analyze(self, symbol: str, current_price: float, side: str) -> tuple[int, list]:
        """
        Analyse OI + ratios et retourne un score de confluence.

        Args:
            symbol        : ex. "BTCUSDT"
            current_price : prix actuel
            side          : "long" ou "short"

        Returns:
            (score 0-100, liste de raisons)
        """
        # Rate limiting — pas plus d'un fetch toutes les 30s
        now = datetime.now(timezone.utc).timestamp()
        last = self._last_fetch.get(symbol, 0)
        if now - last < self._fetch_interval:
            return 0, []
        self._last_fetch[symbol] = now

        # Fetch en parallèle
        oi_data, ls_data, taker_data = await asyncio.gather(
            self._fetch_oi_history(symbol),
            self._fetch_ls_ratio(symbol),
            self._fetch_taker_ratio(symbol),
            return_exceptions=True
        )

        score   = 0
        reasons = []

        # --- OI Spike / Divergence ---
        if isinstance(oi_data, list) and len(oi_data) >= 2:
            oi_current  = float(oi_data[-1]["sumOpenInterest"])
            oi_previous = float(oi_data[-2]["sumOpenInterest"])
            oi_change   = (oi_current - oi_previous) / oi_previous if oi_previous else 0

            # Mise à jour historique
            self._oi_history[symbol].append((now, oi_current))

            # OI Spike + prix monte → continuation haussière
            if oi_change >= self.OI_SPIKE_PCT:
                if side == "long":
                    score += self.SCORE_OI_SPIKE
                    reasons.append(f"OI spike +{oi_change:.1%} → continuation long")
                else:
                    score -= self.SCORE_OI_SPIKE // 2
                    reasons.append(f"OI spike +{oi_change:.1%} → défavorable short")

            # OI Drop + prix monte → reversal baissier probable
            elif oi_change <= self.OI_DROP_PCT:
                if side == "short":
                    score += self.SCORE_OI_DIVERGENCE
                    reasons.append(f"OI drop {oi_change:.1%} → reversal baissier")
                else:
                    score -= self.SCORE_OI_DIVERGENCE // 2
                    reasons.append(f"OI drop {oi_change:.1%} → défavorable long")

            logger.debug(f"[OI] {symbol} OI change={oi_change:.2%}")

        # --- Long/Short Ratio contrarian ---
        if isinstance(ls_data, dict) and ls_data:
            try:
                long_ratio = float(ls_data["longAccount"])
                if long_ratio >= self.LS_LONG_EXTREME:
                    # Trop de longs → contrarian short
                    if side == "short":
                        score += self.SCORE_LS_CONTRARIAN
                        reasons.append(f"L/S ratio {long_ratio:.0%} longs → contrarian short")
                    else:
                        score -= self.SCORE_LS_CONTRARIAN // 2
                        reasons.append(f"L/S ratio {long_ratio:.0%} longs → foule long (risque)")

                elif long_ratio <= self.LS_SHORT_EXTREME:
                    # Trop de shorts → contrarian long
                    if side == "long":
                        score += self.SCORE_LS_CONTRARIAN
                        reasons.append(f"L/S ratio {long_ratio:.0%} longs → contrarian long")
                    else:
                        score -= self.SCORE_LS_CONTRARIAN // 2
                        reasons.append(f"L/S ratio {long_ratio:.0%} longs → foule short (risque)")

                logger.debug(f"[OI] {symbol} L/S long={long_ratio:.2%}")
            except (KeyError, ValueError) as e:
                logger.warning(f"[OI] Erreur parsing L/S ratio: {e}")

        # --- Taker Buy/Sell Ratio ---
        if isinstance(taker_data, dict) and taker_data:
            try:
                taker_ratio = float(taker_data["buySellRatio"])
                if taker_ratio >= self.TAKER_BUY_STRONG:
                    if side == "long":
                        score += self.SCORE_TAKER_RATIO
                        reasons.append(f"Taker B/S {taker_ratio:.2f} → acheteurs agressifs")
                    else:
                        score -= self.SCORE_TAKER_RATIO // 2
                        reasons.append(f"Taker B/S {taker_ratio:.2f} → défavorable short")

                elif taker_ratio <= self.TAKER_SELL_STRONG:
                    if side == "short":
                        score += self.SCORE_TAKER_RATIO
                        reasons.append(f"Taker B/S {taker_ratio:.2f} → vendeurs agressifs")
                    else:
                        score -= self.SCORE_TAKER_RATIO // 2
                        reasons.append(f"Taker B/S {taker_ratio:.2f} → défavorable long")

                logger.debug(f"[OI] {symbol} Taker ratio={taker_ratio:.2f}")
            except (KeyError, ValueError) as e:
                logger.warning(f"[OI] Erreur parsing Taker ratio: {e}")

        score = max(0, min(score, 100))
        return score, reasons

    def boost_confidence(self, score: int) -> float:
        """Convertit score 0-100 en boost confidence ±8%."""
        if score >= 80:
            return +0.08
        elif score >= 60:
            return +0.04
        elif score >= 40:
            return 0.0
        else:
            return -0.04


class OIRegistry:
    """Singleton — accès global."""
    _instance: Optional["OIEngine"] = None

    @classmethod
    def get(cls) -> "OIEngine":
        if cls._instance is None:
            cls._instance = OIEngine()
        return cls._instance