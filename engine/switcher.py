"""
Switcher Paper ↔ Live
─────────────────────────────────────────────────────────────
- Lit/écrit le mode dans config/config.yaml
- Vérifie les conditions avant bascule live
- Confirmation CLI obligatoire avec checklist
- Transfert du contexte FSM (positions paper non transférées)
"""
import asyncio
import logging
import yaml
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

CONFIG_PATH = Path("config/config.yaml")


class ModeSwitcher:
    def __init__(self, bot_ref):
        """bot_ref : référence au bot principal pour redémarrer les engines"""
        self.bot = bot_ref

    def current_mode(self) -> str:
        cfg = self._load_config()
        return cfg.get("mode", "paper")

    async def switch_to_live(self) -> bool:
        """
        Bascule paper → live après checklist de sécurité.
        Retourne True si la bascule a été effectuée.
        """
        if self.current_mode() == "live":
            logger.warning("Déjà en mode LIVE")
            return False

        # ── Checklist pre-live ────────────────────────────────────────────
        print("\n" + "="*60)
        print("  ⚠️  BASCULE VERS LE MODE LIVE — CAPITAL RÉEL")
        print("="*60)

        checks = await self._run_checklist()
        if not checks:
            print("\n❌ Checklist échouée — bascule annulée\n")
            return False

        # ── Confirmation finale ───────────────────────────────────────────
        metrics = self.bot.paper_engine.get_metrics()
        print(f"\n📊 Résultats Paper Trading :")
        print(f"   Trades       : {metrics.get('total_trades', 0)}")
        print(f"   Win rate     : {metrics.get('win_rate', 0)}%")
        print(f"   Profit Factor: {metrics.get('profit_factor', 0)}")
        print(f"   Max Drawdown : {metrics.get('max_drawdown_pct', 0)}%")
        print(f"   ROI          : {metrics.get('roi_pct', 0)}%")
        print()

        # Avertissement si métriques insuffisantes
        if metrics.get("total_trades", 0) < 20:
            print("⚠️  Moins de 20 trades en paper — résultats peu significatifs")
        if metrics.get("win_rate", 0) < 45:
            print("⚠️  Win rate < 45% — à surveiller")
        if metrics.get("max_drawdown_pct", 0) > 10:
            print("⚠️  Drawdown > 10% en paper — risque élevé en live")

        confirm = input(
            "\n🔴 Tapez CONFIRMER pour activer le mode LIVE (Ctrl+C pour annuler) : "
        ).strip()

        if confirm != "CONFIRMER":
            print("❌ Bascule annulée\n")
            return False

        # ── Application ───────────────────────────────────────────────────
        self._set_mode("live")
        logger.info("🔴 MODE LIVE ACTIVÉ — Trades réels en cours")
        await self.bot.restart_engines(mode="live")
        return True

    async def switch_to_paper(self) -> bool:
        """Bascule live → paper (ferme les positions live d'abord)"""
        if self.current_mode() == "paper":
            logger.warning("Déjà en mode PAPER")
            return False

        print("\n⚠️  Retour en mode PAPER — positions live en cours ?")
        active = self.bot.live_engine.get_open_positions()

        if active:
            print(f"\n🔴 {len(active)} position(s) ouverte(s) sur l'exchange :")
            for pos in active:
                print(f"   {pos['symbol']} {pos['side']} | PnL: {pos['pnl']:.2f} USDC")
            action = input(
                "\nGardez les positions ouvertes ? (o/n) : "
            ).strip().lower()
            if action != "o":
                await self._close_all_live()

        self._set_mode("paper")
        logger.info("📄 MODE PAPER réactivé")
        await self.bot.restart_engines(mode="paper")
        return True

    async def _run_checklist(self) -> bool:
        """Vérifie les conditions techniques avant live"""
        checks = {
            "Clés API Coinbase configurées"  : self._check_api_keys,
            "Connexion exchange OK"          : self._check_exchange_connection,
            "Solde USDC suffisant (> 50$)"  : self._check_balance,
            "WebSocket opérationnel"         : self._check_websocket,
            "Risk manager actif"             : self._check_risk_manager,
        }

        all_ok = True
        print("\nChecklist :")
        for label, check_fn in checks.items():
            try:
                ok = await check_fn()
                status = "✅" if ok else "❌"
                print(f"  {status} {label}")
                if not ok:
                    all_ok = False
            except Exception as e:
                print(f"  ❌ {label} — Erreur: {e}")
                all_ok = False

        return all_ok

    async def _check_api_keys(self) -> bool:
        import os
        return all([
            os.getenv("COINBASE_API_KEY"),
            os.getenv("COINBASE_API_SECRET"),
            os.getenv("COINBASE_API_PASSPHRASE"),
        ])

    async def _check_exchange_connection(self) -> bool:
        try:
            await self.bot.exchange.exchange.load_markets()
            return True
        except Exception:
            return False

    async def _check_balance(self) -> bool:
        balance = await self.bot.exchange.get_balance()
        return balance >= 50.0

    async def _check_websocket(self) -> bool:
        return (
            self.bot.feed._ws_task is not None
            and not self.bot.feed._ws_task.done()
        )

    async def _check_risk_manager(self) -> bool:
        return not self.bot.risk._trading_halted

    async def _close_all_live(self):
        """Fermeture d'urgence de toutes les positions live"""
        logger.warning("🚨 Fermeture de toutes les positions live...")
        positions = await self.bot.exchange.get_positions()
        for pos in positions:
            symbol    = pos.get("symbol")
            side      = pos.get("side", "long")
            contracts = float(pos.get("contracts", 0))
            if contracts > 0:
                await self.bot.exchange.close_position(symbol, side, contracts)
                logger.info(f"Position fermée : {symbol}")

    def _load_config(self) -> dict:
        with open(CONFIG_PATH) as f:
            return yaml.safe_load(f)

    def _set_mode(self, mode: str):
        cfg = self._load_config()
        cfg["mode"] = mode
        with open(CONFIG_PATH, "w") as f:
            yaml.dump(cfg, f, default_flow_style=False)
        logger.info(f"Config mise à jour : mode={mode}")