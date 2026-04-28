import time
"""
Data Feed — Binance Futures WebSocket (données) + Hyperliquid (ordres)
"""
import asyncio
import json
import logging
import time
from collections import defaultdict, deque
from typing import Callable, Optional

import aiohttp

logger = logging.getLogger(__name__)

WS_BASE   = "wss://fstream.binance.com/stream?streams="
REST_BASE = "https://fapi.binance.com/fapi/v1/klines"

HL_TO_BINANCE = {
    "BTC": "btcusdt",
    "ETH": "ethusdt",
    "SOL": "solusdt",
    "XRP": "xrpusdt",
    "XLM": "xlmusdt",
}

BINANCE_TO_HL = {v: k for k, v in HL_TO_BINANCE.items()}

TF_TO_BINANCE = {
    "1m": "1m", "5m": "5m", "15m": "15m",
    "30m": "30m", "1h": "1h", "4h": "4h", "1d": "1d",
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

        self._ohlcv: dict[str, dict[str, deque]] = defaultdict(
            lambda: defaultdict(lambda: deque(maxlen=500))
        )
        self._last_price: dict[str, float] = {}

        self._tick_callbacks:  list[Callable] = []
        self._bar_callbacks:   list[Callable] = []
        self._trade_callbacks: list[Callable] = []
        self._depth_callbacks: list[Callable] = []

        self._ws_task: Optional[asyncio.Task] = None
        self._running = False

    def on_tick(self, callback: Callable):
        self._tick_callbacks.append(callback)

    def on_bar(self, callback: Callable):
        self._bar_callbacks.append(callback)

    def on_trade(self, callback: Callable):
        self._trade_callbacks.append(callback)

    def on_depth(self, callback: Callable):
        self._depth_callbacks.append(callback)

    def get_last_price(self, symbol: str) -> Optional[float]:
        return self._last_price.get(symbol)

    def get_ohlcv(self, symbol: str, timeframe: str) -> list[list]:
        return [b.to_list() for b in self._ohlcv[symbol].get(timeframe, deque())]

    async def initialize(self):
        logger.info("Chargement OHLCV initial Binance Futures...")
        tasks = [
            self._load_ohlcv(symbol, tf)
            for symbol in self.pairs
            for tf in set(self.timeframes.values())
        ]
        await asyncio.gather(*tasks)
        logger.info(
            f"OHLCV charge — {len(self.pairs)} paires x "
            f"{len(set(self.timeframes.values()))} timeframes"
        )

    async def _load_ohlcv(self, symbol: str, timeframe: str, limit: int = 300):
        binance_symbol = HL_TO_BINANCE.get(symbol, "").upper()
        interval       = TF_TO_BINANCE.get(timeframe, "1h")
        if not binance_symbol:
            return
        try:
            params = {"symbol": binance_symbol, "interval": interval, "limit": limit}
            async with aiohttp.ClientSession() as session:
                async with session.get(REST_BASE, params=params) as resp:
                    if resp.status != 200:
                        return
                    data = await resp.json()
            bars = self._ohlcv[symbol][timeframe]
            bars.clear()
            for c in data:
                bars.append(PriceBar(ts=int(c[0]), o=c[1], h=c[2], l=c[3], c=c[4], v=c[5]))
        except Exception as e:
            logger.error(f"Erreur _load_ohlcv {symbol} {timeframe}: {e}")

    async def start(self):
        self._running = True
        self._ws_task = asyncio.create_task(self._ws_loop())
        logger.info("WebSocket Binance Futures demarre")

    async def stop(self):
        self._running = False
        if self._ws_task:
            self._ws_task.cancel()
            try:
                await self._ws_task
            except asyncio.CancelledError:
                pass
        logger.info("WebSocket arrete")

    async def _ws_loop(self):
        backoff = 1
        while self._running:
            try:
                await self._ws_connect()
                backoff = 1
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.warning(
                    f"WebSocket deconnecte: {type(e).__name__} — "
                    f"reconnexion dans {backoff}s"
                )
                await asyncio.sleep(backoff)
                backoff = min(backoff * 2, 60)

    def _build_ws_url(self) -> str:
        streams = []
        for symbol in self.pairs:
            bn = HL_TO_BINANCE.get(symbol, "")
            if not bn:
                continue
            streams.append(f"{bn}@aggTrade")
            streams.append(f"{bn}@depth@100ms")
            for tf in set(self.timeframes.values()):
                interval = TF_TO_BINANCE.get(tf, "1h")
                streams.append(f"{bn}@kline_{interval}")
        return WS_BASE + "/".join(streams)

    async def _ws_connect(self):
        url = self._build_ws_url()
        logger.info(f"Connexion WS Binance: {len(self.pairs)} paires")
        async with aiohttp.ClientSession() as session:
            async with session.ws_connect(
                url, heartbeat=30, receive_timeout=60, max_msg_size=0,
            ) as ws:
                logger.info("WS Binance connecte et abonne")
                first_msg = True
                async for msg in ws:
                    if not self._running:
                        break
                    if msg.type == aiohttp.WSMsgType.TEXT:
                        if first_msg:
                            logger.info(f"[FEED DEBUG] PREMIER MESSAGE WS reçu: {msg.data[:200]}")
                            first_msg = False
                        try:
                            await self._dispatch(json.loads(msg.data))
                        except Exception as e:
                            logger.error(f"Erreur dispatch: {e}")
                    elif msg.type == aiohttp.WSMsgType.ERROR:
                        raise ConnectionError(f"WS error: {ws.exception()}")
                    elif msg.type == aiohttp.WSMsgType.CLOSED:
                        raise ConnectionError("WS ferme par Binance")

    async def _dispatch(self, msg: dict):
        stream = msg.get("stream", "")
        data   = msg.get("data", {})
        # Compteur DEBUG (toutes les 500 messages)
        if not hasattr(self, "_msg_count"):
            self._msg_count = 0
            self._msg_types = {}
        self._msg_count += 1
        # Bucket par type
        if "@aggTrade" in stream:
            self._msg_types["trade"] = self._msg_types.get("trade", 0) + 1
        elif "@kline_" in stream:
            self._msg_types["kline"] = self._msg_types.get("kline", 0) + 1
        elif "@depth" in stream:
            self._msg_types["depth"] = self._msg_types.get("depth", 0) + 1
        if self._msg_count % 500 == 0:
            logger.info(f"[FEED DEBUG] {self._msg_count} msgs reçus — {self._msg_types}")
        if "@aggTrade" in stream:
            await self._on_trade(data)
        elif "@kline_" in stream:
            await self._on_kline(data)
        elif "@depth" in stream:
            await self._on_depth(stream, data)

    async def _on_trade(self, data: dict):
        try:
            binance_symbol = data.get("s", "").lower()
            symbol_hl      = BINANCE_TO_HL.get(binance_symbol)
            if not symbol_hl:
                return
            price = float(data.get("p", 0))
            if price > 0:
                self._last_price[symbol_hl] = price
                for cb in self._tick_callbacks:
                    await cb(symbol_hl, price)
                is_buyer_maker = data.get("m", False)
                qty            = float(data.get("q", 0))
                for cb in self._trade_callbacks:
                    await cb(symbol_hl, price, qty, is_buyer_maker)
        except Exception as e:
            logger.error(f"Erreur _on_trade: {e}")

    async def _on_kline(self, data: dict):
        try:
            k         = data.get("k", {})
            bn_symbol = k.get("s", "").lower()
            symbol_hl = BINANCE_TO_HL.get(bn_symbol)
            timeframe = k.get("i", "")
            if not symbol_hl:
                return
            bar = PriceBar(
                ts=int(k.get("t", 0)),
                o=k.get("o", 0), h=k.get("h", 0),
                l=k.get("l", 0), c=k.get("c", 0), v=k.get("v", 0),
            )
            cache = self._ohlcv[symbol_hl][timeframe]
            if cache and cache[-1].ts == bar.ts:
                cache[-1] = bar
            else:
                cache.append(bar)
            if k.get("x", False):
                for cb in self._bar_callbacks:
                    await cb(symbol_hl, timeframe, bar)
        except Exception as e:
            logger.error(f"Erreur _on_kline: {e}")

    async def _on_depth(self, stream: str, data: dict):
        try:
            bn_symbol = stream.split("@")[0]
            symbol_hl = BINANCE_TO_HL.get(bn_symbol)
            if not symbol_hl:
                return
            bids  = data.get("b", [])
            asks  = data.get("a", [])
            ts    = int(time.time() * 1000)
            price = self._last_price.get(symbol_hl, 0.0)
            for cb in self._depth_callbacks:
                await cb(symbol_hl, bids, asks, ts, price)
        except Exception as e:
            logger.error(f"Erreur _on_depth: {e}")

    async def refresh_ohlcv(self, symbol: str, timeframe: str):
        await self._load_ohlcv(symbol, timeframe)
