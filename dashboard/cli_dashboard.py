"""
Dashboard CLI temps réel — Bibliothèque Rich
─────────────────────────────────────────────────────────────
Affichage :
  - Header : mode, capital, ROI, drawdown
  - Tableau positions ouvertes (PnL live coloré)
  - Tableau métriques par stratégie
  - Log des derniers trades
  - Barre de progression drawdown
  - Rafraîchissement : 2 secondes
"""
import asyncio
import logging
from datetime import datetime
from typing import Optional

from rich.console    import Console
from rich.layout     import Layout
from rich.live       import Live
from rich.panel      import Panel
from rich.table      import Table
from rich.text       import Text
from rich.progress   import Progress, BarColumn, TextColumn
from rich.columns    import Columns
from rich import box

logger = logging.getLogger(__name__)


class CLIDashboard:
    def __init__(self, bot_ref, refresh_rate: float = 2.0):
        self.bot          = bot_ref
        self.refresh_rate = refresh_rate
        self.console      = Console()
        self._task: Optional[asyncio.Task] = None
        self._running     = False

    async def start(self):
        self._running = True
        self._task    = asyncio.create_task(self._run())

    async def stop(self):
        self._running = False
        if self._task:
            self._task.cancel()

    # ─── Boucle principale ───────────────────────────────────────────────

    async def _run(self):
        with Live(
            self._build_layout(),
            console      = self.console,
            refresh_per_second = 1 / self.refresh_rate,
            screen       = True,
        ) as live:
            while self._running:
                try:
                    live.update(self._build_layout())
                except Exception as e:
                    logger.error(f"Dashboard refresh error: {e}")
                await asyncio.sleep(self.refresh_rate)

    # ─── Layout principal ────────────────────────────────────────────────

    def _build_layout(self):
        layout = Layout()
        layout.split_column(
            Layout(self._header(),      name="header",   size=7),
            Layout(name="middle",                        ratio=1),
            Layout(self._recent_trades(), name="trades", size=12),
            Layout(self._footer(),       name="footer",  size=3),
        )
        layout["middle"].split_row(
            Layout(self._positions_panel(), name="positions", ratio=3),
            Layout(self._metrics_panel(),   name="metrics",   ratio=2),
        )
        return layout

    # ─── Header ──────────────────────────────────────────────────────────

    def _header(self):
        metrics  = self._get_metrics()
        mode     = self.bot.mode if self.bot else "paper"
        capital  = metrics.get("capital", 0)
        roi      = metrics.get("roi_pct", 0)
        dd       = metrics.get("max_drawdown_pct", 0)
        paused   = getattr(self.bot, "paused", False)

        mode_color = "red" if mode == "live" else "cyan"
        roi_color  = "green" if roi >= 0 else "red"
        dd_color   = "red" if dd > 10 else ("yellow" if dd > 5 else "green")

        # Barre drawdown
        dd_bar = self._make_dd_bar(dd)

        header_text = Text()
        header_text.append("  🤖 CRYPTO LEVERAGE BOT  ", style="bold white on dark_blue")
        header_text.append(f"  [{mode.upper()}]", style=f"bold {mode_color}")
        if paused:
            header_text.append("  ⏸ PAUSÉ", style="bold yellow")

        info_text = (
            f"  Capital: [bold green]{capital:.2f} USDC[/]  │  "
            f"ROI: [{roi_color}]{roi:+.2f}%[/]  │  "
            f"Max DD: [{dd_color}]{dd:.2f}%[/]  │  "
            f"UTC: [dim]{datetime.utcnow().strftime('%H:%M:%S')}[/]"
        )

        return Panel(
            f"[bold]{header_text}[/]\n{info_text}\n{dd_bar}",
            box=box.HEAVY,
            border_style=mode_color,
        )

    def _make_dd_bar(self, dd_pct: float, max_dd: float = 15.0) -> str:
        """Barre de progression du drawdown"""
        fill   = min(int((dd_pct / max_dd) * 30), 30)
        empty  = 30 - fill
        color  = "red" if dd_pct > 10 else ("yellow" if dd_pct > 5 else "green")
        bar    = f"[{color}]{'█' * fill}[/][dim]{'░' * empty}[/]"
        return f"  Drawdown [{bar}] {dd_pct:.1f}% / {max_dd:.0f}%"

    # ─── Positions ───────────────────────────────────────────────────────

    def _positions_panel(self):
        positions = self._get_positions()
        table     = Table(
            show_header=True,
            header_style="bold cyan",
            box=box.SIMPLE_HEAVY,
            expand=True,
        )
        table.add_column("Paire",      style="bold white", width=14)
        table.add_column("Stratégie",  style="cyan",       width=16)
        table.add_column("Dir.",        width=6)
        table.add_column("Entrée",      justify="right",   width=10)
        table.add_column("PnL USDC",    justify="right",   width=10)
        table.add_column("État",        width=8)

        if not positions:
            table.add_row(
                "[dim]—[/]", "[dim]Aucune position[/]",
                "", "", "", ""
            )
        else:
            for p in positions:
                side  = p.get("side", "")
                pnl   = p.get("pnl", 0.0)
                state = p.get("state", "")

                dir_text  = Text("▲ LONG" if side == "long" else "▼ SHORT")
                dir_text.stylize("green" if side == "long" else "red")

                pnl_text  = Text(f"{pnl:+.2f}")
                pnl_text.stylize("green bold" if pnl >= 0 else "red bold")

                state_color = {
                    "OPEN":   "green",
                    "MANAGE": "yellow",
                    "SIGNAL": "cyan",
                }.get(state, "white")

                table.add_row(
                    p.get("symbol", ""),
                    p.get("strategy", ""),
                    dir_text,
                    f"{p.get('entry', 0):.4f}",
                    pnl_text,
                    f"[{state_color}]{state}[/]",
                )

        n   = len(positions)
        max_pos = self.bot.config["max_concurrent_positions"] if self.bot else 3
        return Panel(
            table,
            title=f"[bold cyan]📊 Positions ouvertes ({n}/{max_pos})[/]",
            border_style="cyan",
            box=box.ROUNDED,
        )

    # ─── Métriques ───────────────────────────────────────────────────────

    def _metrics_panel(self):
        m     = self._get_metrics()
        table = Table(
            show_header=False,
            box=box.SIMPLE,
            expand=True,
        )
        table.add_column("Label", style="dim",        width=14)
        table.add_column("Value", style="bold white", width=14)

        wrt   = m.get("win_rate", 0)
        wr_c  = "green" if wrt >= 55 else ("yellow" if wrt >= 45 else "red")
        pf    = m.get("profit_factor", 0)
        pf_c  = "green" if pf >= 1.5 else ("yellow" if pf >= 1.0 else "red")
        roi   = m.get("roi_pct", 0)
        roi_c = "green" if roi >= 0 else "red"

        rows = [
            ("Trades",      str(m.get("total_trades", 0))),
            ("Win Rate",    f"[{wr_c}]{wrt:.1f}%[/]"),
            ("Profit F.",   f"[{pf_c}]{pf:.2f}[/]"),
            ("Moy. gain",   f"[green]{m.get('avg_win', 0):+.2f} USDC[/]"),
            ("Moy. perte",  f"[red]{m.get('avg_loss', 0):+.2f} USDC[/]"),
            ("ROI total",   f"[{roi_c}]{roi:+.2f}%[/]"),
            ("Max DD",      f"{m.get('max_drawdown_pct', 0):.2f}%"),
        ]
        for label, val in rows:
            table.add_row(label, val)

        return Panel(
            table,
            title="[bold magenta]📈 Métriques[/]",
            border_style="magenta",
            box=box.ROUNDED,
        )

    # ─── Trades récents ───────────────────────────────────────────────────

    def _recent_trades(self):
        trades = []
        if self.bot and self.bot.portfolio:
            trades = self.bot.portfolio.trades[-8:]  # 8 derniers

        table = Table(
            show_header=True,
            header_style="bold white",
            box=box.SIMPLE,
            expand=True,
        )
        table.add_column("Heure",      width=10)
        table.add_column("Paire",      width=14)
        table.add_column("Stratégie",  width=16)
        table.add_column("Dir.",        width=8)
        table.add_column("Raison",      width=10)
        table.add_column("PnL USDC",   justify="right", width=12)
        table.add_column("PnL %",      justify="right", width=8)

        if not trades:
            table.add_row("[dim]—[/]", "[dim]Aucun trade[/]", "", "", "", "", "")
        else:
            for t in reversed(trades):
                pnl_c  = "green bold" if t.pnl_usdc >= 0 else "red bold"
                dir_c  = "green" if t.side == "long" else "red"
                reason_icons = {
                    "TP": "🎯", "SL": "🛑",
                    "manual": "👋", "trailing": "🔄"
                }
                reason_icon = reason_icons.get(t.reason, t.reason)
                closed_at = t.closed_at.strftime("%H:%M:%S") \
                    if hasattr(t.closed_at, "strftime") else str(t.closed_at)

                table.add_row(
                    f"[dim]{closed_at}[/]",
                    t.symbol,
                    t.strategy,
                    f"[{dir_c}]{'▲' if t.side == 'long' else '▼'} {t.side.upper()}[/]",
                    reason_icon,
                    f"[{pnl_c}]{t.pnl_usdc:+.2f}[/]",
                    f"[{pnl_c}]{t.pnl_pct*100:+.2f}%[/]",
                )

        return Panel(
            table,
            title="[bold white]📋 Trades récents[/]",
            border_style="white",
            box=box.ROUNDED,
        )

    # ─── Footer ──────────────────────────────────────────────────────────

    def _footer(self):
        cmds = (
            "[dim]Telegram:[/] /status  /positions  /metrics  "
            "/pause  /resume  /switch  │  "
            f"[dim]Refresh: {self.refresh_rate}s[/]"
        )
        return Panel(cmds, box=box.SIMPLE, border_style="dim")

    # ─── Helpers ─────────────────────────────────────────────────────────

    def _get_metrics(self) -> dict:
        try:
            return self.bot.active_engine.get_metrics() if self.bot else {}
        except Exception:
            return {}

    def _get_positions(self) -> list:
        try:
            return self.bot.active_engine.get_open_positions() if self.bot else []
        except Exception:
            return []