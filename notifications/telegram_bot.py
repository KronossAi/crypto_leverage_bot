"""
Notifications Telegram
─────────────────────────────────────────────────────────────
Commandes :
  /help      → liste des commandes
  /status    → état du bot
  /positions → positions ouvertes
  /metrics   → métriques complètes
  /pause     → suspend les trades
  /resume    → reprend le trading
  /switch    → bascule paper ↔ live
"""
import asyncio
import logging
from datetime import datetime
from typing import Optional

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    Application,
    CommandHandler,
    CallbackQueryHandler,
    ContextTypes,
)
from telegram.constants import ParseMode

logger = logging.getLogger(__name__)


class TelegramNotifier:
    def __init__(self, token: str, chat_id: str, bot_ref=None):
        self.token   = token
        self.chat_id = str(chat_id)
        self.bot_ref = bot_ref
        self.app: Optional[Application] = None
        self._paused = False

    # ─── Démarrage ────────────────────────────────────────────────────────

    async def start(self):
        self.app = (
            Application.builder()
            .token(self.token)
            .build()
        )
        self._register_handlers()
        await self.app.initialize()
        await self.app.start()
        asyncio.create_task(
            self.app.updater.start_polling(
                drop_pending_updates=True,
                allowed_updates=["message", "callback_query"],
            )
        )
        logger.info("Telegram bot demarre")
        await self.send_message(
            "Bot demarre — mode {}".format(
                self.bot_ref.mode if self.bot_ref else "inconnu"
            )
        )

    async def stop(self):
        if self.app:
            await self.app.updater.stop()
            await self.app.stop()
            await self.app.shutdown()

    # ─── Handlers ─────────────────────────────────────────────────────────

    def _register_handlers(self):
        handlers = [
            CommandHandler("start",     self._cmd_help),
            CommandHandler("help",      self._cmd_help),
            CommandHandler("status",    self._cmd_status),
            CommandHandler("positions", self._cmd_positions),
            CommandHandler("metrics",   self._cmd_metrics),
            CommandHandler("pause",     self._cmd_pause),
            CommandHandler("resume",    self._cmd_resume),
            CommandHandler("switch",    self._cmd_switch),
            CommandHandler("reset_paper", self._cmd_reset_paper),
            CallbackQueryHandler(self._on_callback),
        ]
        for h in handlers:
            self.app.add_handler(h)

    async def _cmd_help(self, update: Update, ctx: ContextTypes.DEFAULT_TYPE):
        if not self._is_authorized(update):
            return
        text = (
            "Commandes disponibles\n\n"
            "/status    — etat du bot\n"
            "/positions — positions ouvertes\n"
            "/metrics   — performance\n"
            "/pause     — suspendre les trades\n"
            "/resume    — reprendre le trading\n"
            "/switch    — basculer paper/live\n"
            "/reset    — remettre le paper trading a zero\n"
        )
        await update.message.reply_text(text)

    async def _cmd_status(self, update: Update, ctx: ContextTypes.DEFAULT_TYPE):
        if not self._is_authorized(update):
            return
        if not self.bot_ref:
            await update.message.reply_text("Bot non connecte")
            return

        mode    = self.bot_ref.mode
        capital = self.bot_ref.portfolio.capital
        n_pos   = self.bot_ref.active_engine.fsm.count_open()
        paused  = "OUI" if self._paused else "NON"
        max_pos = self.bot_ref.config["max_concurrent_positions"]

        text = (
            f"Statut du Bot\n\n"
            f"Mode      : {'LIVE' if mode == 'live' else 'PAPER'}\n"
            f"Capital   : {capital:.2f} USDC\n"
            f"Positions : {n_pos}/{max_pos}\n"
            f"Pause     : {paused}\n"
            f"Heure UTC : {datetime.utcnow().strftime('%H:%M:%S')}\n"
        )

        keyboard = InlineKeyboardMarkup([[
            InlineKeyboardButton("Positions", callback_data="positions"),
            InlineKeyboardButton("Metriques", callback_data="metrics"),
        ]])
        await update.message.reply_text(text, reply_markup=keyboard)

    async def _cmd_positions(self, update: Update, ctx: ContextTypes.DEFAULT_TYPE):
        if not self._is_authorized(update):
            return
        await self._send_positions(update.message.reply_text)

    async def _cmd_metrics(self, update: Update, ctx: ContextTypes.DEFAULT_TYPE):
        if not self._is_authorized(update):
            return
        await self._send_metrics(update.message.reply_text)

    async def _cmd_pause(self, update: Update, ctx: ContextTypes.DEFAULT_TYPE):
        if not self._is_authorized(update):
            return
        self._paused = True
        if self.bot_ref:
            self.bot_ref.paused = True
        await update.message.reply_text("Trading suspendu")
        logger.info("Trading suspendu via Telegram")

    async def _cmd_resume(self, update: Update, ctx: ContextTypes.DEFAULT_TYPE):
        if not self._is_authorized(update):
            return
        self._paused = False
        if self.bot_ref:
            self.bot_ref.paused = False
        await update.message.reply_text("Trading repris")
        logger.info("Trading repris via Telegram")

    async def _cmd_reset_paper(self, update: Update, ctx: ContextTypes.DEFAULT_TYPE):
        if not self._is_authorized(update):
            return
        if not self.bot_ref:
            return
        if self.bot_ref.mode != "paper":
            await update.message.reply_text("Reset disponible uniquement en mode PAPER")
            return
        keyboard = InlineKeyboardMarkup([[
            InlineKeyboardButton("Confirmer reset", callback_data="confirm_reset"),
            InlineKeyboardButton("Annuler", callback_data="cancel"),
        ]])
        await update.message.reply_text(
            "Reset paper trading ?\n\nCela effacera toutes les positions et trades.",
            reply_markup=keyboard,
        )

    async def _cmd_switch(self, update: Update, ctx: ContextTypes.DEFAULT_TYPE):
        if not self._is_authorized(update):
            return
        if not self.bot_ref:
            return
        current = self.bot_ref.mode
        target  = "live" if current == "paper" else "paper"
        keyboard = InlineKeyboardMarkup([[
            InlineKeyboardButton(
                f"Confirmer -> {target.upper()}",
                callback_data=f"switch_{target}"
            ),
            InlineKeyboardButton("Annuler", callback_data="cancel"),
        ]])
        await update.message.reply_text(
            f"Bascule vers {target.upper()} ?\nMode actuel : {current.upper()}",
            reply_markup=keyboard,
        )

    async def _on_callback(self, update: Update, ctx: ContextTypes.DEFAULT_TYPE):
        query = update.callback_query
        await query.answer()
        data  = query.data

        if data == "positions":
            await self._send_positions(query.edit_message_text)
        elif data == "metrics":
            await self._send_metrics(query.edit_message_text)
        elif data == "switch_live":
            await query.edit_message_text("Bascule LIVE en cours...")
            if self.bot_ref:
                ok = await self.bot_ref.switcher.switch_to_live()
                await self.send_message(
                    "MODE LIVE ACTIVE" if ok else "Bascule annulee"
                )
        elif data == "switch_paper":
            await query.edit_message_text("Bascule PAPER en cours...")
            if self.bot_ref:
                await self.bot_ref.switcher.switch_to_paper()
        elif data == "confirm_reset":
            if self.bot_ref and self.bot_ref.mode == "paper":
                initial_cap = float(self.bot_ref.config["capital"]["initial"])
                self.bot_ref.state_mgr.reset_paper(
                    self.bot_ref.portfolio,
                    self.bot_ref.paper_engine.fsm,
                    initial_cap,
                )
                self.bot_ref.circuit.start_day(initial_cap)
                await query.edit_message_text(
                    f"Paper trading remis a zero\nCapital : {initial_cap:.2f} USDC"
                )
                logger.info("Paper trading reset via Telegram")
        elif data == "switch_paper":
            await query.edit_message_text("Bascule PAPER en cours...")
            if self.bot_ref:
                await self.bot_ref.switcher.switch_to_paper()
                await self.send_message("MODE PAPER ACTIVE")
        elif data == "cancel":
            await query.edit_message_text("Operation annulee")

    # ─── Messages automatiques ────────────────────────────────────────────

    async def notify_trade_open(
        self,
        symbol: str,
        side: str,
        entry: float,
        sl: float,
        tp: float,
        size_usdc: float,
        leverage: int,
        strategy: str,
        confidence: float,
    ):
        rr = abs(tp - entry) / abs(entry - sl) if entry != sl else 0
        text = (
            f"Position ouverte\n\n"
            f"Paire     : {symbol}\n"
            f"Direction : {side.upper()}\n"
            f"Strategie : {strategy}\n"
            f"Confiance : {confidence:.0%}\n"
            f"Entree    : {entry:.4f}\n"
            f"SL        : {sl:.4f}\n"
            f"TP        : {tp:.4f}\n"
            f"R/R       : {rr:.2f}\n"
            f"Taille    : {size_usdc:.2f} USDC\n"
            f"Levier    : {leverage}x\n"
        )
        await self.send_message(text)

    async def notify_trade_close(
        self,
        symbol: str,
        side: str,
        entry: float,
        close: float,
        pnl_usdc: float,
        pnl_pct: float,
        reason: str,
        strategy: str,
        capital: float,
    ):
        result = "GAGNE" if pnl_usdc >= 0 else "PERDU"
        text = (
            f"Position fermee — {result}\n\n"
            f"Paire     : {symbol}\n"
            f"Direction : {side.upper()}\n"
            f"Raison    : {reason}\n"
            f"Entree    : {entry:.4f}\n"
            f"Sortie    : {close:.4f}\n"
            f"PnL       : {pnl_usdc:+.2f} USDC ({pnl_pct*100:+.2f}%)\n"
            f"Capital   : {capital:.2f} USDC\n"
        )
        await self.send_message(text)

    async def notify_drawdown_alert(self, drawdown_pct: float, capital: float):
        await self.send_message(
            f"ALERTE DRAWDOWN\n\n"
            f"Drawdown : {drawdown_pct:.1f}%\n"
            f"Capital  : {capital:.2f} USDC\n"
            f"Verifiez les positions immediatement"
        )

    async def notify_funding_alert(self, symbol: str, rate: float, cost: float):
        await self.send_message(
            f"Funding rate eleve\n\n"
            f"Paire  : {symbol}\n"
            f"Taux   : {rate*100:.4f}%\n"
            f"Cout   : {cost:.4f} USDC\n"
        )

    async def send_hourly_summary(self, metrics: dict, positions: list):
        n_pos   = len(positions)
        capital = metrics.get("capital", 0)
        roi     = metrics.get("roi_pct", 0)
        wr      = metrics.get("win_rate", 0)
        trades  = metrics.get("total_trades", 0)

        pos_text = ""
        for p in positions:
            pos_text += (
                f"  {p['symbol']} {p.get('side','').upper()} | "
                f"PnL: {p.get('pnl', 0):+.2f}\n"
            )

        text = (
            f"Resume horaire\n\n"
            f"Capital   : {capital:.2f} USDC\n"
            f"ROI       : {roi:+.2f}%\n"
            f"Positions : {n_pos}\n"
            f"Trades    : {trades}\n"
            f"Win Rate  : {wr:.1f}%\n"
        )
        if pos_text:
            text += f"\nPositions actives :\n{pos_text}"

        await self.send_message(text)

    # ─── Helpers ─────────────────────────────────────────────────────────

    async def _send_positions(self, reply_fn):
        if not self.bot_ref:
            return
        positions = self.bot_ref.active_engine.get_open_positions()
        if not positions:
            await reply_fn("Aucune position ouverte")
            return
        text = "Positions ouvertes\n\n"
        for p in positions:
            text += (
                f"{p.get('symbol')} — {p.get('strategy')}\n"
                f"  Direction : {str(p.get('side','')).upper()}\n"
                f"  Entree   : {p.get('entry', 0):.4f}\n"
                f"  PnL      : {p.get('pnl', 0):+.2f} USDC\n"
                f"  Etat     : {p.get('state','')}\n\n"
            )
        await reply_fn(text)

    async def _send_metrics(self, reply_fn):
        if not self.bot_ref:
            return
        m = self.bot_ref.active_engine.get_metrics()
        text = (
            f"Metriques\n\n"
            f"Capital   : {m.get('capital', 0):.2f} USDC\n"
            f"ROI       : {m.get('roi_pct', 0):+.2f}%\n"
            f"Trades    : {m.get('total_trades', 0)}\n"
            f"Win Rate  : {m.get('win_rate', 0):.1f}%\n"
            f"Moy. gain : {m.get('avg_win', 0):+.2f} USDC\n"
            f"Moy. perte: {m.get('avg_loss', 0):+.2f} USDC\n"
            f"Profit F. : {m.get('profit_factor', 0):.2f}\n"
            f"Max DD    : {m.get('max_drawdown_pct', 0):.2f}%\n"
        )
        await reply_fn(text)

    async def send_message(self, text: str):
        """Envoie un message avec retry x3"""
        if not self.app:
            return
        for attempt in range(3):
            try:
                await self.app.bot.send_message(
                    chat_id=self.chat_id,
                    text=text,
                )
                return
            except Exception as e:
                logger.warning(f"Telegram send attempt {attempt+1}: {e}")
                await asyncio.sleep(2 ** attempt)

    def _is_authorized(self, update: Update) -> bool:
        return str(update.effective_chat.id) == self.chat_id
