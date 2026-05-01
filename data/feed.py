import time
last_msg_ts = time.time()
"""
Data Feed — Bybit V5 WebSocket (données) + Hyperliquid (ordres)
─────────────────────────────────────────────────────────────────
Bybit V5 public WebSocket :
  - Trades : publicTrade.{SYMBOL}
  - Klines : kline.{INTERVAL}.{SYMBOL}
  - Orderbook : orderbook.50.{SYMBOL}

Endpoint WS : wss://stream.bybit.com/v5/public/linear
Endpoint REST : https://api.bybit.com/v5/market/kline
"""
import asyncio
import json
import logging
import time
from collections import defaultdict, deque
from typing import Callable, Optional

import aiohttp

logger = logging.getLogger(__name__)

WS_URL    = "wss://stream.bybit.com/v5/public/linear"
REST_BASE = "https://api.bybit.com/v5/market/kline"

# Mapping Hyperliquid → Bybit (USDT perp)
HL_TO_BYBIT = {
    "BTC": "BTCUSDT",
    "ETH": "ETHUSDT",
    "SOL": "SOLUSDT",
    "XRP": "XRPUSDT",
    "XLM": "XLMUSDT",
}

BYBIT_TO_HL = {v: k for k, v in HL_TO_BYBIT.items()}

# Bybit utilise des minutes pour les intervalles : "1", "5", "15", "60", "240", "D"
TF_TO_BYBIT = {
    "1m": "1", "5m": "5", "15m": "15",
    "30m": "30", "1h": "60", "4h": "240", "1d": "D",
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
        self._last_bar_ts = defaultdict(dict)

        self._ws_task: Optional[asyncio.Task] = None
        self._running = False

    # ─── Enregistrement callbacks ──────────────────────────────────────────
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

    # ─── Initialisation REST ───────────────────────────────────────────────
    async def initialize(self):
        logger.info("Chargement OHLCV initial Bybit V5...")
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
        bybit_symbol = HL_TO_BYBIT.get(symbol)
        interval     = TF_TO_BYBIT.get(timeframe, "60")
        if not bybit_symbol:
            return
        try:
            params = {
                "category": "linear",
                "symbol":   bybit_symbol,
                "interval": interval,
                "limit":    limit,
            }
            async with aiohttp.ClientSession() as session:
                async with session.get(REST_BASE, params=params) as resp:
                    if resp.status != 200:
                        logger.warning(
                            f"REST kline {symbol} {timeframe}: HTTP {resp.status}"
                        )
                        return
                    data = await resp.json()
            # Bybit retourne data["result"]["list"] dans l'ordre desc → on inverse
            klines = data.get("result", {}).get("list", [])
            klines.reverse()
            bars = self._ohlcv[symbol][timeframe]
            bars.clear()
            for c in klines:
                # c = [start_ts, open, high, low, close, volume, turnover]
                bars.append(
                    PriceBar(ts=int(c[0]), o=c[1], h=c[2], l=c[3], c=c[4], v=c[5])
                )
        except Exception as e:
            logger.error(f"Erreur _load_ohlcv {symbol} {timeframe}: {e}")

    # ─── WebSocket ─────────────────────────────────────────────────────────
    async def start(self):
        self._running = True
        self._ws_task = asyncio.create_task(self._ws_loop())
        logger.info("WebSocket Bybit V5 demarre")

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

    def _build_subscribe_msg(self) -> dict:
        """Construit le message d'abonnement Bybit V5."""
        args = []
        for symbol in self.pairs:
            bb = HL_TO_BYBIT.get(symbol)
            if not bb:
                continue
            args.append(f"publicTrade.{bb}")
            args.append(f"orderbook.50.{bb}")
            for tf in set(self.timeframes.values()):
                interval = TF_TO_BYBIT.get(tf, "60")
                args.append(f"kline.{interval}.{bb}")
        return {"op": "subscribe", "args": args}

    async def _ws_connect(self):
        sub_msg = self._build_subscribe_msg()
        logger.info(
            f"Connexion WS Bybit: {len(self.pairs)} paires "
            f"({len(sub_msg['args'])} streams)"
        )
        async with aiohttp.ClientSession() as session:
            async with session.ws_connect(
                WS_URL, heartbeat=20, receive_timeout=60, max_msg_size=0,
            ) as ws:
                # Envoi du message de souscription
                await ws.send_json(sub_msg)
                logger.info("WS Bybit connecte et abonne")

                # Compteur DEBUG
                if not hasattr(self, "_msg_count"):
                    self._msg_count = 0
                    self._msg_types = {}
                first_msg = True

                # Tâche ping pour maintenir la connexion (Bybit recommande ping/30s)
                ping_task = asyncio.create_task(self._ping_loop(ws))

                try:
                    async for msg in ws:
                        if not self._running:
                            break
                        if msg.type == aiohttp.WSMsgType.TEXT:
                            if first_msg:
                                logger.debug(
                                    f"[FEED DEBUG] PREMIER MSG WS: {msg.data[:200]}"
                                )
                                first_msg = False
                            try:
                                await self._dispatch(json.loads(msg.data))
                            except Exception as e:
                                logger.error(f"Erreur dispatch: {e}")
                        elif msg.type == aiohttp.WSMsgType.ERROR:
                            raise ConnectionError(f"WS error: {ws.exception()}")
                        elif msg.type == aiohttp.WSMsgType.CLOSED:
                            raise ConnectionError("WS ferme par Bybit")
                finally:
                    ping_task.cancel()

    async def _ping_loop(self, ws):
        """Bybit V5 attend un ping JSON toutes les ~20-30 sec."""
        try:
            while self._running:
                await asyncio.sleep(20)
                try:
                    await ws.send_json({"op": "ping"})
                except Exception:
                    return
        except asyncio.CancelledError:
            return

    # ─── Dispatch des messages ─────────────────────────────────────────────
    async def _dispatch(self, msg: dict):
        # Réponses de souscription / pong
        if "op" in msg or "success" in msg:
            if msg.get("op") in ("subscribe", "pong"):
                return
            if msg.get("success") is False:
                logger.warning(f"WS Bybit erreur subscribe: {msg}")
            return

        topic = msg.get("topic", "")
        data  = msg.get("data", {})

        if not topic:
            return

        # Compteur DEBUG
        self._msg_count += 1
        if topic.startswith("publicTrade."):
            self._msg_types["trade"] = self._msg_types.get("trade", 0) + 1
        elif topic.startswith("kline."):
            self._msg_types["kline"] = self._msg_types.get("kline", 0) + 1
        elif topic.startswith("orderbook."):
            self._msg_types["depth"] = self._msg_types.get("depth", 0) + 1
        if self._msg_count % 500 == 0:
            logger.debug(
                f"[FEED DEBUG] {self._msg_count} msgs reçus — {self._msg_types}"
            )

        # Routing
        if topic.startswith("publicTrade."):
            await self._on_trade(topic, data)
        elif topic.startswith("kline."):
            await self._on_kline(topic, data)
        elif topic.startswith("orderbook."):
            await self._on_depth(topic, data)

    async def _on_trade(self, topic: str, trades: list):
        """
        Bybit publicTrade format:
        data = [
            {"T": 1672304486868, "s": "BTCUSDT", "S": "Buy"|"Sell",
             "v": "0.001", "p": "16578.50", "L": "PlusTick",
             "i": "trade_id", "BT": False}
        ]
        """
        try:
            if not isinstance(trades, list):
                return
            bybit_symbol = topic.split(".", 1)[1]
            symbol_hl    = BYBIT_TO_HL.get(bybit_symbol)
            if not symbol_hl:
                return
            for t in trades:
                price = float(t.get("p", 0))
                qty   = float(t.get("v", 0))
                # Bybit "S" : Buy = acheteur agressif (taker buy)
                #            Sell = vendeur agressif (taker sell)
                # Pour cohérence avec ancien code Binance :
                #   is_buyer_maker = True  → vendeur agressif (Sell)
                #   is_buyer_maker = False → acheteur agressif (Buy)
                is_buyer_maker = (t.get("S") == "Sell")
                if price > 0:
                    self._last_price[symbol_hl] = price
                    for cb in self._tick_callbacks:
                        await cb(symbol_hl, price)
                    for cb in self._trade_callbacks:
                        await cb(symbol_hl, price, qty, is_buyer_maker)
        except Exception as e:
            logger.error(f"Erreur _on_trade: {e}")

    async def _on_kline(self, topic: str, klines: list):
        """
        Bybit kline format:
        data = [
            {"start": 1672304400000, "end": 1672304459999, "interval": "1",
             "open": "16649.5", "close": "16677.0", "high": "16677.0",
             "low": "16649.5", "volume": "2.081", "turnover": "...",
             "confirm": False, "timestamp": 1672304486868}
        ]
        """
        try:
            if not isinstance(klines, list):
                return
            parts = topic.split(".", 2)
            if len(parts) != 3:
                return
            interval, bybit_symbol = parts[1], parts[2]
            symbol_hl = BYBIT_TO_HL.get(bybit_symbol)
            if not symbol_hl:
                return
            # Convertir l'intervalle Bybit en format interne
            tf = next(
                (k for k, v in TF_TO_BYBIT.items() if v == interval),
                None
            )
            if not tf:
                return
            for k in klines:
                bar = PriceBar(
                    ts=int(k.get("start", 0)),
                    o=k.get("open", 0), h=k.get("high", 0),
                    l=k.get("low", 0), c=k.get("close", 0),
                    v=k.get("volume", 0),
                )
                cache = self._ohlcv[symbol_hl][tf]
                if cache and cache[-1].ts == bar.ts:
                    cache[-1] = bar
                else:
                    cache.append(bar)
                # Si bougie confirmée (close)
                prev = self._last_bar_ts[symbol_hl].get(tf)

                if prev is None:
                    self._last_bar_ts[symbol_hl][tf] = bar.ts

                elif bar.ts != prev:
                    for cb in self._bar_callbacks:
                        await cb(symbol_hl, tf, cache[-1])
                    self._last_bar_ts[symbol_hl][tf] = bar.ts
        except Exception as e:
            logger.error(f"Erreur _on_kline: {e}")

    async def _on_depth(self, topic: str, data: dict):
        """
        Bybit orderbook format:
        data = {
            "s": "BTCUSDT",
            "b": [["16649.5", "0.532"], ...],   # bids
            "a": [["16650.0", "1.123"], ...],   # asks
            "u": 18352, "seq": 7961638724
        }
        """
        try:
            parts = topic.split(".", 2)
            if len(parts) < 3:
                return
            bybit_symbol = parts[2]
            symbol_hl    = BYBIT_TO_HL.get(bybit_symbol)
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

if time.time() - last_msg_ts > 30:
    logger.error("WS_FREEZE_DETECTED")

