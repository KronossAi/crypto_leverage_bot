# data/scalp_liquidations.py
# Module 7 — Liquidation Heatmap (gratuit, sans Coinglass)
# Source 1 : Binance WebSocket forceOrder (liquidations réelles temps réel)
# Source 2 : Heatmap synthétique via OI + levier (niveaux prévisionnels)

import logging
import asyncio
import aiohttp
import websockets
import json
from datetime import datetime, timezone
from collections import defaultdict
from typing import Optional

logger = logging.getLogger(__name__)

BINANCE_FAPI = "https://fapi.binance.com"
BINANCE_WS   = "wss://fstream.binance.com/ws/!forceOrder@arr"

# Leviers typiques utilisés par les traders retail
LEVERAGE_LEVELS = [5, 10, 20, 25, 50, 100]


class LiquidationCluster:
    """Représente un cluster de liquidations à un niveau de prix."""
    def __init__(self, price: float, volume: float, side: str):
        self.price  = price
        self.volume = volume
        self.side   = side  # "long" ou "short"


class LiquidationEngine:
    """
    Reconstitue une heatmap de liquidations sans Coinglass.

    Deux sources combinées :
    1. forceOrder WebSocket → liquidations réelles accumulées par tranche de prix
    2. Heatmap synthétique → niveaux théoriques calculés depuis OI + levier

    Signaux :
    - Cluster LONG au-dessus du prix  → magnet → target short
    - Cluster SHORT en-dessous du prix → liquidity pool → target long
    - Boost/malus confidence ±5%
    """

    PROXIMITY_PCT     = 0.005   # 0.5% = "proche" d'un cluster
    CLUSTER_BUCKET    = 0.002   # regroupe les liq dans des tranches de 0.2%
    MIN_CLUSTER_VOL   = 50_000  # volume minimum en USDT pour considérer un cluster
    SCORE_CLUSTER     = 30      # score de base par cluster confluent
    SCORE_SYNTHETIC   = 20      # score heatmap synthétique
    HISTORY_SECONDS   = 3600    # garde 1h de liquidations réelles

    def __init__(self):
        # Liquidations réelles : {symbol: {price_bucket: {"vol": float, "side": str}}}
        self._real_clusters: dict[str, dict] = defaultdict(lambda: defaultdict(
            lambda: {"vol": 0.0, "side": "unknown", "ts": 0}
        ))
        # Niveaux synthétiques : {symbol: [LiquidationCluster]}
        self._synthetic: dict[str, list] = defaultdict(list)
        self._last_synthetic_fetch: dict[str, float] = {}
        self._synthetic_interval = 60  # fetch synthétique toutes les 60s
        self._ws_task: Optional[asyncio.Task] = None
        self._running = False

    # ------------------------------------------------------------------
    # WEBSOCKET — LIQUIDATIONS RÉELLES
    # ------------------------------------------------------------------

    async def start(self):
        """Démarre le WebSocket forceOrder en arrière-plan."""
        if self._running:
            return
        self._running = True
        self._ws_task = asyncio.create_task(self._ws_loop())
        logger.info("[LiqHeatmap] WebSocket forceOrder démarré")

    async def stop(self):
        """Arrête le WebSocket."""
        self._running = False
        if self._ws_task:
            self._ws_task.cancel()

    async def _ws_loop(self):
        """Boucle WebSocket avec reconnexion exponentielle."""
        delay = 1
        while self._running:
            try:
                async with websockets.connect(BINANCE_WS, ping_interval=20) as ws:
                    delay = 1  # reset délai après connexion réussie
                    logger.info("[LiqHeatmap] Connecté au stream forceOrder")
                    async for message in ws:
                        try:
                            data = json.loads(message)
                            self._on_force_order(data)
                        except Exception as e:
                            logger.warning(f"[LiqHeatmap] Erreur parsing: {e}")
            except Exception as e:
                logger.warning(f"[LiqHeatmap] WS déconnecté: {e} — retry dans {delay}s")
                await asyncio.sleep(delay)
                delay = min(delay * 2, 60)  # backoff exponentiel max 60s

    def _on_force_order(self, data: dict):
        """Traite une liquidation reçue du WebSocket."""
        try:
            order  = data.get("o", {})
            symbol = order.get("s", "")
            side   = order.get("S", "")   # BUY = liquidation short / SELL = liquidation long
            price  = float(order.get("ap", order.get("p", 0)))  # average price
            qty    = float(order.get("q", 0))
            volume = price * qty           # volume en USDT

            if not symbol or price <= 0:
                return

            # Côté liquidé : SELL = position long liquidée / BUY = position short liquidée
            liq_side = "long" if side == "SELL" else "short"

            # Bucket de prix (tranche de 0.2%)
            bucket = round(price / (price * self.CLUSTER_BUCKET)) * (price * self.CLUSTER_BUCKET)
            bucket = round(bucket, 2)

            now = datetime.now(timezone.utc).timestamp()
            cluster = self._real_clusters[symbol][bucket]
            cluster["vol"]  += volume
            cluster["side"]  = liq_side
            cluster["ts"]    = now

            logger.debug(f"[LiqHeatmap] {symbol} liq {liq_side} @ {price:.2f} vol={volume:.0f}$")

        except Exception as e:
            logger.warning(f"[LiqHeatmap] Erreur on_force_order: {e}")

    def _cleanup_old_clusters(self, symbol: str):
        """Supprime les clusters trop anciens (> 1h)."""
        now     = datetime.now(timezone.utc).timestamp()
        cutoff  = now - self.HISTORY_SECONDS
        buckets = self._real_clusters[symbol]
        old     = [b for b, c in buckets.items() if c["ts"] < cutoff]
        for b in old:
            del buckets[b]

    # ------------------------------------------------------------------
    # HEATMAP SYNTHÉTIQUE
    # ------------------------------------------------------------------

    async def _fetch_synthetic(self, symbol: str, current_price: float):
        """
        Calcule les niveaux de liquidation théoriques.
        Méthode : prix_entrée = prix_liq / (1 ± 1/levier)
        On estime les prix d'entrée probables depuis le prix actuel.
        """
        now = datetime.now(timezone.utc).timestamp()
        last = self._last_synthetic_fetch.get(symbol, 0)
        if now - last < self._synthetic_interval:
            return
        self._last_synthetic_fetch[symbol] = now

        # Fetch OI pour estimer le volume à chaque niveau
        url = f"{BINANCE_FAPI}/futures/data/openInterestHist"
        params = {"symbol": symbol, "period": "5m", "limit": 1}

        try:
            async with aiohttp.ClientSession() as session:
                async with session.get(url, params=params, timeout=aiohttp.ClientTimeout(total=5)) as resp:
                    if resp.status != 200:
                        return
                    data = await resp.json()
                    if not data:
                        return
                    total_oi = float(data[-1]["sumOpenInterestValue"])
        except Exception as e:
            logger.warning(f"[LiqHeatmap] Erreur fetch OI synthétique: {e}")
            return

        clusters = []
        # Pour chaque levier, calcule le prix de liquidation des longs et shorts
        for lev in LEVERAGE_LEVELS:
            # Longs liquidés si prix baisse de 1/levier
            liq_long  = current_price * (1 - 1 / lev)
            # Shorts liquidés si prix monte de 1/levier
            liq_short = current_price * (1 + 1 / lev)

            # Volume estimé proportionnel à l'OI (distribution uniforme simplifiée)
            vol_est = total_oi / len(LEVERAGE_LEVELS)

            clusters.append(LiquidationCluster(round(liq_long, 2),  vol_est, "long"))
            clusters.append(LiquidationCluster(round(liq_short, 2), vol_est, "short"))

        self._synthetic[symbol] = clusters
        logger.debug(f"[LiqHeatmap] {symbol} heatmap synthétique: {len(clusters)} niveaux")

    # ------------------------------------------------------------------
    # SCORING
    # ------------------------------------------------------------------

    async def analyze(self, symbol: str, current_price: float, side: str) -> tuple[int, list]:
        """
        Analyse les clusters de liquidation et retourne un score.

        Args:
            symbol        : ex. "BTCUSDT"
            current_price : prix actuel
            side          : "long" ou "short"

        Returns:
            (score 0-100, liste de raisons)
        """
        score   = 0
        reasons = []

        # Mise à jour heatmap synthétique
        await self._fetch_synthetic(symbol, current_price)

        # Nettoyage clusters anciens
        self._cleanup_old_clusters(symbol)

        def near(price: float) -> bool:
            return abs(current_price - price) / current_price <= self.PROXIMITY_PCT

        def above(price: float) -> bool:
            return price > current_price * (1 + 0.001)

        def below(price: float) -> bool:
            return price < current_price * (1 - 0.001)

        # --- Clusters réels ---
        for bucket, cluster in self._real_clusters[symbol].items():
            if cluster["vol"] < self.MIN_CLUSTER_VOL:
                continue

            liq_side = cluster["side"]
            vol      = cluster["vol"]

            # Cluster de longs au-dessus → magnet → target short
            if liq_side == "long" and above(bucket):
                if side == "short":
                    score += self.SCORE_CLUSTER
                    reasons.append(f"Cluster longs {vol/1000:.0f}k$ @ {bucket:.2f} → target short")

            # Cluster de shorts en-dessous → liquidity pool → target long
            elif liq_side == "short" and below(bucket):
                if side == "long":
                    score += self.SCORE_CLUSTER
                    reasons.append(f"Cluster shorts {vol/1000:.0f}k$ @ {bucket:.2f} → target long")

        # --- Heatmap synthétique ---
        for cluster in self._synthetic[symbol]:
            if cluster.volume < self.MIN_CLUSTER_VOL:
                continue

            # Longs synthétiques au-dessus → target short
            if cluster.side == "long" and above(cluster.price) and near(cluster.price):
                if side == "short":
                    score += self.SCORE_SYNTHETIC
                    reasons.append(f"Liq synthétique longs @ {cluster.price:.2f}")

            # Shorts synthétiques en-dessous → target long
            elif cluster.side == "short" and below(cluster.price) and near(cluster.price):
                if side == "long":
                    score += self.SCORE_SYNTHETIC
                    reasons.append(f"Liq synthétique shorts @ {cluster.price:.2f}")

        score = min(score, 100)
        return score, reasons

    def boost_confidence(self, score: int) -> float:
        """Convertit score 0-100 en boost confidence ±5%."""
        if score >= 80:
            return +0.05
        elif score >= 60:
            return +0.03
        elif score >= 40:
            return 0.0
        else:
            return -0.02


class LiquidationRegistry:
    """Singleton — accès global."""
    _instance: Optional["LiquidationEngine"] = None

    @classmethod
    def get(cls) -> "LiquidationEngine":
        if cls._instance is None:
            cls._instance = LiquidationEngine()
        return cls._instance