"""
Entry point — Bot de trading quantitatif crypto
Architecture 3 layers : HTF bias → MTF confluence → LTF trigger
"""
import asyncio
import logging
import os
import signal
import sys
from pathlib import Path
from logging.handlers import RotatingFileHandler

import yaml
import time
from dotenv import load_dotenv
from pythonjsonlogger import jsonlogger

from core.exchange          import ExchangeClient
from core.risk_manager      import RiskManager
from core.portfolio         import Portfolio
from core.circuit_breaker   import CircuitBreaker
from data.feed              import DataFeed
from data.indicators        import CVDTracker
from data.scalp_orderflow import OrderFlowRegistry
from data.scalp_levels import OBIRegistry
from data.scalp_timing import VWAPRegistry
from data.scalp_liquidations import LiquidationRegistry
from data.macro_filter      import MacroFilter
from engine.paper_engine    import PaperEngine
from engine.live_engine     import LiveEngine
from engine.switcher        import ModeSwitcher
from strategies.orchestrator import Orchestrator
from core.state_manager     import StateManager
from notifications.telegram_bot import TelegramNotifier
from dashboard.cli_dashboard    import CLIDashboard


# ─── Logging ─────────────────────────────────────────────────────────────────

def setup_logging(level: str = "INFO", file_only: bool = True):
    Path("logs").mkdir(exist_ok=True)
    formatter = jsonlogger.JsonFormatter(
        "%(asctime)s %(name)s %(levelname)s %(message)s",
        datefmt="%Y-%m-%dT%H:%M:%S",
        json_ensure_ascii=False,
    )
    handlers = []

    fh = RotatingFileHandler(
        "logs/bot.log", maxBytes=10*1024*1024, backupCount=5, encoding="utf-8"
    )
    fh.setFormatter(formatter)
    handlers.append(fh)

    if not file_only:
        ch = logging.StreamHandler(sys.stdout)
        ch.setFormatter(formatter)
        handlers.append(ch)

    logging.basicConfig(
        level=getattr(logging, level.upper(), logging.INFO),
        handlers=handlers, force=True
    )
    for lib in ("telegram", "aiohttp", "websockets", "httpx"):
        logging.getLogger(lib).setLevel(logging.WARNING)


# ─── Config ──────────────────────────────────────────────────────────────────

def load_config() -> dict:
    with open("config/config.yaml", encoding="utf-8") as f:
        return yaml.safe_load(f)

def load_strategies_config() -> dict:
    with open("config/strategies.yaml", encoding="utf-8") as f:
        return yaml.safe_load(f)


# ─── Bot ─────────────────────────────────────────────────────────────────────

class TradingBot:
    def __init__(self):
        load_dotenv()
        self.config         = load_config()
        self.strat_cfg      = load_strategies_config()
        self.mode           = self.config.get("mode", "paper")
        self.paused         = False
        self._running       = False

        self.exchange:       ExchangeClient   = None
        self.feed:           DataFeed         = None
        self.risk:           RiskManager      = None
        self.portfolio:      Portfolio        = None
        self.circuit:        CircuitBreaker   = None
        self.macro:          MacroFilter      = None
        self.paper_engine:   PaperEngine      = None
        self.live_engine:    LiveEngine       = None
        self.active_engine                    = None
        self.switcher:       ModeSwitcher     = None
        self.orchestrator:   Orchestrator     = None
        self.telegram:       TelegramNotifier = None
        self.dashboard:      CLIDashboard     = None

        # CVD trackers par symbole
        self.cvd_trackers: dict[str, CVDTracker] = {
            s: CVDTracker(window=300) for s in self.config["pairs"]
        }

        self.orderflow = OrderFlowRegistry(
            pairs      = self.config["pairs"],
            tf_seconds = 60,
        )
        self.obi  = OBIRegistry(pairs=self.config["pairs"])
        self.vwap = VWAPRegistry(pairs=self.config["pairs"])
        self.liq_heatmap = LiquidationRegistry.get()
        # Funding rates en cache
        self.funding_rates: dict[str, float] = {}

        self.logger = logging.getLogger(self.__class__.__name__)

    async def start(self):
        self.logger.info(f"Demarrage bot — mode {self.mode.upper()}")

        paper_mode    = self.mode == "paper"

        # 1. Exchange
        self.exchange = ExchangeClient(paper_mode=paper_mode)
        await self.exchange.connect()

        # 2. Data feed
        self.feed = DataFeed(self.exchange, self.config)
        await self.feed.initialize()
        # Module 7 — Démarrage WebSocket liquidations
        await self.liq_heatmap.start()

        # 3. Risk + Portfolio + Circuit Breaker + Macro
        self.risk    = RiskManager(self.config)
        start_cap    = float(self.config["capital"]["initial"]) if paper_mode \
                       else await self.exchange.get_balance()
        if start_cap <= 0:
            start_cap = float(self.config["capital"]["initial"])

        self.portfolio = Portfolio(start_cap)
        self.circuit   = CircuitBreaker(self.config)
        self.macro     = MacroFilter(self.config)
        self.circuit.start_day(start_cap)
        self.logger.info(f"Capital: {start_cap:.2f} USDC")

        # 4. Engines
        self.paper_engine  = PaperEngine(self.risk, self.portfolio, self.config)
        self.live_engine   = LiveEngine(self.exchange, self.risk, self.portfolio)
        self.active_engine = self.paper_engine if paper_mode else self.live_engine

        if not paper_mode:
            await self.live_engine.start()
        # State persistence
        self.state_mgr = StateManager()
        self.state_mgr.load(self.portfolio, self.paper_engine.fsm if paper_mode else self.live_engine.fsm)
        self.paper_engine.state_mgr = self.state_mgr
        # 5. Orchestrator
        self.orchestrator = Orchestrator(
            self.config, self.strat_cfg, self.cvd_trackers
        )

        # 6. Switcher
        self.orchestrator.orderflow = self.orderflow
        self.orchestrator.obi = self.obi
        self.orchestrator.vwap = self.vwap
        self.switcher = ModeSwitcher(self)

        # 7. Telegram
        tg_token   = os.getenv("TELEGRAM_BOT_TOKEN", "")
        tg_chat_id = os.getenv("TELEGRAM_CHAT_ID", "")
        if tg_token and tg_chat_id:
            self.telegram = TelegramNotifier(tg_token, tg_chat_id, bot_ref=self)
            await self.telegram.start()
            # Injecte la référence Telegram dans le paper_engine
            if self.paper_engine:
                self.paper_engine.telegram = self.telegram
        else:
            self.logger.warning("Telegram non configure")

        # 8. Dashboard
        self.dashboard = CLIDashboard(self)
        await self.dashboard.start()

        # 9. Callbacks
        self._register_callbacks()

        # 10. WebSocket
        await self.feed.start()

        # 11. Funding rate init
        await self._refresh_funding()

        self._running = True
        self.logger.info("Bot entierement demarre")

        await self._main_loop()

    # ─── Callbacks ───────────────────────────────────────────────────────

    def _register_callbacks(self):
        async def on_tick(symbol: str, price: float):
            if self.paused:
                return
            # Met à jour CVD si aggTrade disponible (géré dans on_trade)
            if self.mode == "paper":
                await self.paper_engine.on_tick(symbol, price, self.circuit)

        async def on_bar(symbol: str, timeframe: str, bar):
            if self.paused:
                return
            if timeframe != self.config["timeframes"]["ltf"]:
                return
            await self._analyze(symbol)

        async def on_trade(symbol: str, price: float, qty: float, is_maker: bool):
            """Callback aggTrade pour CVD"""
            tracker = self.cvd_trackers.get(symbol)
            if tracker:
                tracker.on_trade(price, qty, is_maker)
            self.orderflow.on_trade(
                symbol         = symbol,
                price          = price,
                qty            = qty,
                is_buyer_maker = is_maker,
                timestamp_ms   = int(time.time() * 1000),
            )

        self.feed.on_tick(on_tick)
        self.feed.on_bar(on_bar)
        if hasattr(self.feed, "on_trade"):
            self.feed.on_trade(on_trade)
        async def on_depth(symbol, bids, asks, ts, price):
            self.obi.on_depth_update(symbol, bids, asks, ts, price)

        self.feed.on_depth(on_depth)

    # ─── Boucle principale ────────────────────────────────────────────────

    async def _main_loop(self):
        FUNDING_REFRESH  = 3600   # Toutes les heures
        SUMMARY_INTERVAL = 3600
        elapsed          = 0

        while self._running:
            await asyncio.sleep(60)
            elapsed += 60

            if self.paused:
                continue

            # Refresh funding
            if elapsed % FUNDING_REFRESH == 0:
                await self._refresh_funding()

            # Résumé Telegram
            if elapsed >= SUMMARY_INTERVAL:
                elapsed = 0
                await self._send_hourly_summary()

            # Renouvelle le circuit breaker journalier
            self.circuit.start_day(self.portfolio.capital)
            self.portfolio.log_metrics()

    # ─── Analyse ─────────────────────────────────────────────────────────

    async def _analyze(self, symbol: str):
        # Limite journalière
        daily = self.portfolio.daily_trades_count()
        max_d = self.config["limits"]["max_trades_per_day"]
        if daily >= max_d:
            return

        # Guard FSM : skip si symbole déjà actif
        active_symbols = {ctx.symbol for ctx in self.active_engine.fsm.active()}
        if symbol in active_symbols:
            return

        try:
            fsm = self.active_engine.fsm
            signal = await self.orchestrator.analyze(
                symbol        = symbol,
                feed          = self.feed,
                timeframes    = self.config["timeframes"],
                funding_rates = self.funding_rates,
                circuit_breaker = self.circuit,
                macro_filter  = self.macro,
                capital=self.portfolio.capital,
                fsm=fsm,
            )
            if signal is None:
                return

            await self.active_engine.on_signal(signal, self.circuit)
            # Note: notification Telegram déplacée dans paper_engine (1 seule notif par position réelle)

        except Exception as e:
            self.logger.error(f"Erreur analyse {symbol}: {e}")

    # ─── Funding ─────────────────────────────────────────────────────────

    async def _refresh_funding(self):
        for symbol in self.config["pairs"]:
            try:
                rate = await self.exchange.get_funding_rate(symbol)
                if rate is not None:
                    self.funding_rates[symbol] = rate
            except Exception as e:
                self.logger.warning(f"Funding {symbol}: {e}")
        self.logger.info(
            f"Funding rates: { {k: f'{v*100:.4f}%' for k,v in self.funding_rates.items()} }"
        )

    # ─── Résumé horaire ──────────────────────────────────────────────────

    async def _send_hourly_summary(self):
        if not self.telegram:
            return
        metrics   = self.active_engine.get_metrics()
        positions = self.active_engine.get_open_positions()
        cb_status = self.circuit.status()
        macro_evt = self.macro.next_event()

        await self.telegram.send_hourly_summary(metrics, positions)

        # Alerte si circuit breaker actif
        if any([cb_status["daily_limit_hit"], cb_status["volatility_kill"],
                cb_status["oi_emergency"]]):
            msg = f"CIRCUIT BREAKER ACTIF\n{cb_status}"
            await self.telegram.send_message(msg)

    # ─── Bascule ─────────────────────────────────────────────────────────

    async def restart_engines(self, mode: str):
        self.mode  = mode
        paper_mode = mode == "paper"
        await self.exchange.disconnect()
        self.exchange = ExchangeClient(paper_mode=paper_mode)
        await self.exchange.connect()
        if paper_mode:
            self.active_engine = self.paper_engine
        else:
            await self.live_engine.start()
            self.active_engine = self.live_engine
        self.logger.info(f"Engine bascule: {mode.upper()}")

    # ─── Arrêt ───────────────────────────────────────────────────────────

    async def stop(self):
        self.logger.info("Arret du bot...")
        self._running = False
        if self.dashboard:
            await self.dashboard.stop()
        if self.feed:
            await self.feed.stop()
        if self.live_engine and self.mode == "live":
            await self.live_engine.stop()
        if self.telegram:
            await self.telegram.send_message("Bot arrete proprement")
            await self.telegram.stop()
        if self.exchange:
            await self.exchange.disconnect()
        self.portfolio.log_metrics()
        self.logger.info("Bot arrete")


# ─── Entry point ──────────────────────────────────────────────────────────────

async def main():
    setup_logging(os.getenv("LOG_LEVEL", "INFO"), file_only=True)
    bot  = TradingBot()
    loop = asyncio.get_event_loop()

    def handle_signal():
        asyncio.create_task(bot.stop())

    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(sig, handle_signal)
        except NotImplementedError:
            pass

    try:
        await bot.start()
    except KeyboardInterrupt:
        await bot.stop()
    except Exception as e:
        logging.critical(f"Erreur fatale: {e}", exc_info=True)
        await bot.stop()
        sys.exit(1)


if __name__ == "__main__":
    asyncio.run(main())
