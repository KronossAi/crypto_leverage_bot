"""
Data Feed Hyperliquid
─────────────────────────────────────────────────────────────
WebSocket : wss://api.hyperliquid.xyz/ws
Channels  :
  - allMids  → prix mid temps réel (tous les assets)
  - candle   → bougies par symbole/timeframe

Reconnexion : backoff exponentiel (1s → 60s)
Cache OHLCV : deque(maxlen=500) par symbole/timeframe
"""
import asyncio
import json
import logging
from collections import defaultdict, deque
from typing import Callable, Optional

import aiohttp

logger = logging.getLogger(__name__)

WS_MAINNET = "wss://api.hyperliquid.xyz/ws"
WS_TESTNET = "wss://api.hyperliquid-testnet.xyz/ws"

TF_TO_HL = {
    "1m":  "1m",  "5m":  "5m",  "15m": "15m",
    "30m": "30m", "1h":  "1h",  "4h":  "4h",  "1d": "1d",
}


class PriceBar:
    __slots__ = ["ts", "open", "high", "low", "close", "volume"]

    def __init__(self, ts, o, h, l, c, v):
        self.ts     = int(ts)
        self.open   = float(o)
        self.high   = float(h)
        self.low    = float(l)
        self.close  = float(c)
        self.volume = float(v)

    def to_list(self):
        return [self.ts, self.open, self.high, self.low, self.close, self.volume]


class DataFeed:
    def __init__(self, exchange_client, config: dict):
        self.client     = exchange_client
        self.pairs      = config["pairs"]
        self.timeframes = config["timeframes"]
        self.testnet    = config["exchange"].get("testnet", True)
        self.ws_url     = WS_TESTNET if self.testnet else WS_MAINNET

        # Cache OHLCV : {symbol: {timeframe: deque[PriceBar]}}
        self._ohlcv: dict[str, dict[str, deque]] = defaultdict(
            lambda: defaultdict(lambda: deque(maxlen=500))
        )

        # Prix mid par symbole
        self._last_price: dict[str, float] = {}

        # Callbacks
        self._tick_callbacks: list[Callable] = []
        self._bar_callbacks:  list[Callable] = []

        self._ws_task: Optional[asyncio.Task] = None
        self._running = False

    # ─── Abonnements ─────────────────────────────────────────────────────

    def on_tick(self, callback: Callable):
        """Enregistre un callback appelé à chaque tick prix"""
        self._tick_callbacks.append(callback)

    def on_bar(self, callback: Callable):
        """Enregistre un callback appelé à chaque nouvelle bougie"""
        self._bar_callbacks.append(callback)

    # ─── Accesseurs ──────────────────────────────────────────────────────

    def get_last_price(self, symbol: str) -> Optional[float]:
        return self._last_price.get(symbol)

    def get_ohlcv(self, symbol: str, timeframe: str) -> list[list]:
        return [b.to_list() for b in self._ohlcv[symbol].get(timeframe, deque())]

    # ─── Initialisation REST ─────────────────────────────────────────────

    async def initialize(self):
        """Charge l'historique OHLCV initial via REST avant le WebSocket"""
        logger.info("📥 Chargement OHLCV initial Hyperliquid...")
        tasks = [
            self._load_ohlcv(symbol, tf)
            for symbol in self.pairs
            for tf in set(self.timeframes.values())
        ]
        await asyncio.gather(*tasks)
        logger.info(
            f"✅ OHLCV chargé — {len(self.pairs)} paires × "
            f"{len(set(self.timeframes.values()))} timeframes"
        )

    async def _load_ohlcv(self, symbol: str, timeframe: str, limit: int = 300):
        try:
            raw  = await self.client.get_ohlcv(symbol, timeframe, limit=limit)
            bars = self._ohlcv[symbol][timeframe]
            bars.clear()
            for row in raw:
                bars.append(PriceBar(*row))
            logger.debug(f"  {symbol} {timeframe}: {len(raw)} bougies")
        except Exception as e:
            logger.error(f"Erreur _load_ohlcv {symbol} {timeframe}: {e}")

    # ─── WebSocket ───────────────────────────────────────────────────────

    async def start(self):
        """Démarre le WebSocket avec reconnexion auto"""
        self._running = True
        self._ws_task = asyncio.create_task(self._ws_loop())
        logger.info(f"🔌 WebSocket Hyperliquid démarré ({self.ws_url})")

    async def stop(self):
        self._running = False
        if self._ws_task:
            self._ws_task.cancel()
            try:
                await self._ws_task
            except asyncio.CancelledError:
                pass
        logger.info("🔌 WebSocket arrêté")

    async def _ws_loop(self):
        """Reconnexion automatique avec backoff exponentiel"""
        backoff = 1
        while self._running:
            try:
                await self._ws_connect()
                backoff = 1
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.warning(
                    f"WebSocket HL déconnecté: {type(e).__name__} — "
                    f"reconnexion dans {backoff}s"
                )
                await asyncio.sleep(backoff)
                backoff = min(backoff * 2, 60)

    async def _ws_connect(self):
        """Connexion et abonnement WebSocket Hyperliquid via aiohttp"""
        async with aiohttp.ClientSession() as session:
            async with session.ws_connect(
                self.ws_url,
                heartbeat=20,
                receive_timeout=30,
            ) as ws:
                # Abonnement allMids — prix temps réel tous assets
                await ws.send_str(json.dumps({
                    "method": "subscribe",
                    "subscription": {"type": "allMids"},
                }))

                # Abonnement candles par paire + timeframe
                for symbol in self.pairs:
                    for tf_val in set(self.timeframes.values()):
                        hl_interval = TF_TO_HL.get(tf_val, "1h")
                        await ws.send_str(json.dumps({
                            "method": "subscribe",
                            "subscription": {
                                "type":     "candle",
                                "coin":     symbol,
                                "interval": hl_interval,
                            },
                        }))

                logger.info(
                    f"✅ WS abonné — {len(self.pairs)} paires, "
                    f"{len(set(self.timeframes.values()))} timeframes"
                )

                # Boucle de réception
                async for msg in ws:
                    if not self._running:
                        break
                    if msg.type == aiohttp.WSMsgType.TEXT:
                        try:
                            await self._dispatch(json.loads(msg.data))
                        except Exception as e:
                            logger.error(f"Erreur dispatch WS: {e}")
                    elif msg.type == aiohttp.WSMsgType.ERROR:
                        raise ConnectionError(f"WS error: {ws.exception()}")
                    elif msg.type == aiohttp.WSMsgType.CLOSED:
                        raise ConnectionError("WS fermé par le serveur")

    # ─── Dispatch ────────────────────────────────────────────────────────

    async def _dispatch(self, msg: dict):
        """Route les messages vers les handlers appropriés"""
        channel = msg.get("channel", "")
        data    = msg.get("data", {})

        if channel == "allMids":
            await self._on_all_mids(data)
        elif channel == "candle":
            await self._on_candle(data)

    async def _on_all_mids(self, data: dict):
        """
        Prix mid temps réel pour tous les assets.
        data = {"BTC": "67500.0", "ETH": "3500.5", ...}
        """
        for symbol, price_str in data.items():
            if symbol not in self.pairs:
                continue
            try:
                price = float(price_str)
                if price > 0:
                    self._last_price[symbol] = price
                    for cb in self._tick_callbacks:
                        await cb(symbol, price)
            except (ValueError, TypeError):
                pass

    async def _on_candle(self, data: dict):
        """
        Mise à jour OHLCV depuis le WS candle Hyperliquid.
        data = {
          "t": 1700000000000, "s": "BTC", "i": "1h",
          "o": "67000", "h": "67500", "l": "66800",
          "c": "67300", "v": "123.45"
        }
        """
        try:
            symbol    = data.get("s", "")
            interval  = data.get("i", "")
            timeframe = self._hl_to_tf(interval)

            if symbol not in self.pairs:
                return

            bar = PriceBar(
                ts=int(data.get("t", 0)),
                o=data.get("o", 0),
                h=data.get("h", 0),
                l=data.get("l", 0),
                c=data.get("c", 0),
                v=data.get("v", 0),
            )

            cache = self._ohlcv[symbol][timeframe]
            if cache and cache[-1].ts == bar.ts:
                cache[-1] = bar      # Mise à jour bougie en cours
            else:
                cache.append(bar)    # Nouvelle bougie

            for cb in self._bar_callbacks:
                await cb(symbol, timeframe, bar)

        except Exception as e:
            logger.error(f"Erreur _on_candle: {e}")

    # ─── Helpers ─────────────────────────────────────────────────────────

    @staticmethod
    def _hl_to_tf(interval: str) -> str:
        """Interval Hyperliquid → timeframe standard"""
        return {
            "1m": "1m", "5m": "5m", "15m": "15m",
            "30m": "30m", "1h": "1h", "4h": "4h", "1d": "1d",
        }.get(interval, interval)

    async def refresh_ohlcv(self, symbol: str, timeframe: str):
        """Rechargement manuel d'une paire/timeframe"""
        await self._load_ohlcv(symbol, timeframe)