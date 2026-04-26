"""
Client Hyperliquid — SDK officiel hyperliquid
─────────────────────────────────────────────────────────────
Auth       : clé privée ETH (wallet)
Settlement : USDC
Testnet    : dispo (HYPERLIQUID_TESTNET=true)
Paires     : "BTC", "ETH", "SOL", etc. (sans /USD)
"""
import time
import asyncio
import logging
from typing import Optional
from os import getenv

from eth_account import Account
from hyperliquid.info     import Info
from hyperliquid.exchange import Exchange
from hyperliquid.utils    import constants

logger = logging.getLogger(__name__)

HL_MAINNET = constants.MAINNET_API_URL
HL_TESTNET = constants.TESTNET_API_URL


class ExchangeClient:
    def __init__(self, paper_mode: bool = True):
        self.paper_mode = paper_mode
        self.testnet    = getenv("HYPERLIQUID_TESTNET", "true").lower() == "true"
        self.base_url   = HL_TESTNET if self.testnet else HL_MAINNET

        self._info:           Optional[Info]     = None
        self._exchange:       Optional[Exchange] = None
        self._wallet_address: str                = ""

    # ─── Connexion ────────────────────────────────────────────────────────

    async def connect(self):
        private_key          = getenv("PRIVATE_KEY", "")
        self._wallet_address = getenv("WALLET_ADDRESS", "")

        if not private_key and not self.paper_mode:
            raise ValueError("PRIVATE_KEY requis pour le mode live")

        self._info = Info(self.base_url, skip_ws=True)

        if private_key:
            account              = Account.from_key(private_key)
            self._wallet_address = self._wallet_address or account.address
            self._exchange       = Exchange(
                account,
                self.base_url,
                account_address=self._wallet_address,
            )

        mode_str = (
            f"{'TESTNET' if self.testnet else 'MAINNET'} | "
            f"{'PAPER' if self.paper_mode else 'LIVE'}"
        )
        logger.info(
            f"✅ Hyperliquid connecté — {mode_str} | "
            f"Wallet: {self._wallet_address[:10]}..."
        )

    async def disconnect(self):
        logger.info("Hyperliquid client déconnecté")

    # ─── Solde ────────────────────────────────────────────────────────────

    async def get_balance(self) -> float:
        try:
            state     = self._info.user_state(self._wallet_address)
            usdc_free = float(
                state.get("withdrawable", 0) or
                state.get("marginSummary", {}).get("accountValue", 0)
            )
            return usdc_free
        except Exception as e:
            logger.error(f"Erreur get_balance: {e}")
            return 0.0

    # ─── Levier ───────────────────────────────────────────────────────────

    async def set_leverage(self, symbol: str, leverage: int):
        if self.paper_mode or not self._exchange:
            logger.debug(f"[PAPER] Levier {leverage}x simulé pour {symbol}")
            return
        try:
            resp = self._exchange.update_leverage(
                leverage, symbol, is_cross=False
            )
            if resp.get("status") == "ok":
                logger.info(f"Levier {leverage}x isolated configuré — {symbol}")
            else:
                logger.warning(f"set_leverage {symbol}: {resp}")
        except Exception as e:
            logger.error(f"Erreur set_leverage {symbol}: {e}")

    # ─── Ordres ───────────────────────────────────────────────────────────

    async def place_order(
        self,
        symbol:     str,
        side:       str,
        amount:     float,
        order_type: str = "market",
        price:      Optional[float] = None,
        sl:         Optional[float] = None,
        tp:         Optional[float] = None,
    ) -> Optional[dict]:
        if self.paper_mode:
            logger.info(f"[PAPER] Ordre simulé: {side} {amount:.4f} {symbol}")
            return {"status": "ok", "id": "paper", "side": side, "amount": amount}

        if not self._exchange:
            logger.error("Exchange non initialisé — clé privée manquante")
            return None

        try:
            is_buy = side == "buy"

            if order_type == "market" and price is None:
                ticker = await self.get_ticker(symbol)
                if ticker:
                    raw_price = float(ticker["last"])
                    price     = raw_price * 1.001 if is_buy else raw_price * 0.999

            price  = round(price, 6) if price else None
            amount = round(amount, 6)

            resp = self._exchange.order(
                symbol,
                is_buy,
                amount,
                price,
                {"limit": {"tif": "Ioc"}} if order_type == "market"
                else {"limit": {"tif": "Gtc"}},
            )

            if resp.get("status") != "ok":
                logger.error(f"Ordre refusé {symbol}: {resp}")
                return None

            order_id = (
                resp.get("response", {})
                    .get("data", {})
                    .get("statuses", [{}])[0]
                    .get("resting", {})
                    .get("oid", "unknown")
            )

            logger.info(
                f"✅ Ordre exécuté: {side.upper()} {amount:.4f} {symbol} "
                f"@ {price} | ID: {order_id}"
            )

            if sl:
                await self._place_sl(symbol, is_buy, amount, sl)
            if tp:
                await self._place_tp(symbol, is_buy, amount, tp)

            return {"status": "ok", "id": order_id, "price": price}

        except Exception as e:
            logger.error(f"Erreur place_order {symbol}: {e}")
            return None

    async def _place_sl(
        self, symbol: str, is_buy: bool, amount: float, sl_price: float
    ):
        try:
            self._exchange.order(
                symbol,
                not is_buy,
                amount,
                sl_price,
                {
                    "trigger": {
                        "triggerPx": sl_price,
                        "isMarket":  True,
                        "tpsl":      "sl",
                    }
                },
                reduce_only=True,
            )
            logger.info(f"SL placé {symbol} @ {sl_price}")
        except Exception as e:
            logger.error(f"Erreur _place_sl {symbol}: {e}")

    async def _place_tp(
        self, symbol: str, is_buy: bool, amount: float, tp_price: float
    ):
        try:
            self._exchange.order(
                symbol,
                not is_buy,
                amount,
                tp_price,
                {
                    "trigger": {
                        "triggerPx": tp_price,
                        "isMarket":  False,
                        "tpsl":      "tp",
                    }
                },
                reduce_only=True,
            )
            logger.info(f"TP placé {symbol} @ {tp_price}")
        except Exception as e:
            logger.error(f"Erreur _place_tp {symbol}: {e}")

    # ─── Positions ────────────────────────────────────────────────────────

    async def get_positions(self) -> list:
        try:
            state     = self._info.user_state(self._wallet_address)
            positions = state.get("assetPositions", [])
            active    = []
            for p in positions:
                pos  = p.get("position", {})
                size = float(pos.get("szi", 0))
                if size != 0:
                    active.append({
                        "symbol":     pos.get("coin"),
                        "side":       "long" if size > 0 else "short",
                        "contracts":  abs(size),
                        "entryPrice": float(pos.get("entryPx", 0)),
                        "leverage":   float(
                            pos.get("leverage", {}).get("value", 1)
                        ),
                        "notional":   abs(size) * float(pos.get("entryPx", 0)),
                        "pnl":        float(pos.get("unrealizedPnl", 0)),
                    })
            return active
        except Exception as e:
            logger.error(f"Erreur get_positions: {e}")
            return []

    async def close_position(
        self, symbol: str, side: str, amount: float
    ) -> Optional[dict]:
        close_side = "sell" if side == "long" else "buy"
        ticker     = await self.get_ticker(symbol)
        price      = float(ticker["last"]) if ticker else None
        return await self.place_order(
            symbol, close_side, amount, order_type="market", price=price
        )

    # ─── Market data ─────────────────────────────────────────────────────

    async def get_ticker(self, symbol: str) -> Optional[dict]:
        try:
            mids  = self._info.all_mids()
            price = float(mids.get(symbol, 0))
            if price == 0:
                return None
            return {
                "last": price,
                "bid":  price * 0.9999,
                "ask":  price * 1.0001,
            }
        except Exception as e:
            logger.error(f"Erreur get_ticker {symbol}: {e}")
            return None

    async def get_ohlcv(
        self,
        symbol:    str,
        timeframe: str,
        limit:     int = 300,
    ) -> list:
        """OHLCV via l'endpoint candles Hyperliquid"""
        try:
            tf_map = {
                "1m": "1m", "5m": "5m", "15m": "15m",
                "30m": "30m", "1h": "1h", "2h": "2h",
                "4h": "4h", "1d": "1d",
            }
            interval = tf_map.get(timeframe, "1h")

            # Calcul startTime / endTime en millisecondes
            tf_seconds = {
                "1m": 60, "5m": 300, "15m": 900,
                "30m": 1800, "1h": 3600, "2h": 7200,
                "4h": 14400, "1d": 86400,
            }
            now_ms      = int(time.time() * 1000)
            interval_ms = tf_seconds.get(timeframe, 3600) * 1000
            start_ms    = now_ms - (interval_ms * limit)

            candles = self._info.candles_snapshot(
                coin      = symbol,
                interval  = interval,
                startTime = start_ms,
                endTime   = now_ms,
            )

            return [
                [
                    int(c["t"]),
                    float(c["o"]),
                    float(c["h"]),
                    float(c["l"]),
                    float(c["c"]),
                    float(c["v"]),
                ]
                for c in candles
            ]
        except Exception as e:
            logger.error(f"Erreur get_ohlcv {symbol} {timeframe}: {e}")
            return []

    async def get_funding_rate(self, symbol: str) -> Optional[float]:
        try:
            meta     = self._info.meta_and_asset_ctxs()
            universe = meta[0].get("universe", [])
            ctxs     = meta[1]
            for i, asset in enumerate(universe):
                if asset.get("name") == symbol and i < len(ctxs):
                    rate = float(ctxs[i].get("funding", 0))
                    logger.debug(f"Funding {symbol}: {rate:.6f}")
                    return rate
            return None
        except Exception as e:
            logger.warning(f"Funding rate {symbol} indisponible: {e}")
            return None

    async def cancel_all_orders(self, symbol: str):
        if self.paper_mode or not self._exchange:
            return
        try:
            open_orders = self._info.open_orders(self._wallet_address)
            to_cancel   = [
                {"coin": o["coin"], "oid": o["oid"]}
                for o in open_orders
                if o.get("coin") == symbol
            ]
            if to_cancel:
                self._exchange.cancel_by_cloid(symbol, to_cancel)
                logger.info(f"{len(to_cancel)} ordre(s) annulé(s) pour {symbol}")
        except Exception as e:
            logger.error(f"Erreur cancel_all_orders {symbol}: {e}")