"""
Order Book Imbalance L2 — Binance Futures depth stream
─────────────────────────────────────────────────────────────
Source    : Binance Futures WebSocket depth (déjà dans feed.py)
Exécution : Hyperliquid uniquement (aucun ordre ici)

Métriques :
  - OBI Ratio (bid_vol - ask_vol) / (bid_vol + ask_vol)
  - Depth Ratio bids/asks top 20
  - Bid/Ask Walls (niveau > 3x moyenne)
  - Absorption (gros volume + prix immobile)
  - Spoofing (ordre disparu avant impact)
  - VPIN (liquidité toxique)
"""
import logging
import time
from collections import deque
from dataclasses import dataclass, field
from typing import Optional
import numpy as np

logger = logging.getLogger(__name__)


@dataclass
class OBISignal:
    """Signal Order Book Imbalance"""
    symbol:          str
    timestamp:       int
    side:            Optional[str] = None
    score:           int           = 0
    reasons:         list          = field(default_factory=list)

    obi_ratio:       float         = 0.0
    obi_strong:      bool          = False
    depth_ratio:     float         = 0.0
    bid_wall:        bool          = False
    bid_wall_level:  Optional[float] = None
    ask_wall:        bool          = False
    ask_wall_level:  Optional[float] = None
    absorption:      bool          = False
    absorption_side: Optional[str] = None
    spoofing:        bool          = False
    vpin:            float         = 0.0
    vpin_toxic:      bool          = False


class OrderBookAnalyzer:
    """
    Analyse le carnet d'ordres Binance Futures en temps réel.
    Reçoit les updates depth via feed.py
    """

    def __init__(
        self,
        symbol:           str,
        depth_levels:     int   = 20,
        obi_threshold:    float = 0.3,
        wall_mult:        float = 3.0,
        vpin_threshold:   float = 0.8,
        spoof_window_s:   int   = 30,
        absorption_pct:   float = 0.05,
    ):
        self.symbol         = symbol
        self.depth_levels   = depth_levels
        self.obi_threshold  = obi_threshold
        self.wall_mult      = wall_mult
        self.vpin_threshold = vpin_threshold
        self.spoof_window_s = spoof_window_s
        self.absorption_pct = absorption_pct

        # Carnet d'ordres actuel
        self._bids: dict[float, float] = {}  # prix → volume
        self._asks: dict[float, float] = {}

        # Historique OBI pour VPIN
        self._obi_history: deque[float] = deque(maxlen=50)

        # Historique ordres pour spoofing
        self._order_snapshots: deque[dict] = deque(maxlen=10)

        # Prix précédent pour absorption
        self._last_price:     float = 0.0
        self._last_update_ts: int   = 0

    # ─── Mise à jour carnet ──────────────────────────────────────────────

    def on_depth_update(
        self,
        bids:         list[list],  # [[prix, volume], ...]
        asks:         list[list],
        timestamp_ms: int,
        current_price: float,
    ):
        """
        Appelé à chaque update depth Binance.
        bids/asks : liste de [prix, volume] — volume=0 = suppression
        """
        try:
            # Mise à jour bids
            for price_str, vol_str in bids:
                price = float(price_str)
                vol   = float(vol_str)
                if vol == 0:
                    self._bids.pop(price, None)
                else:
                    self._bids[price] = vol

            # Mise à jour asks
            for price_str, vol_str in asks:
                price = float(price_str)
                vol   = float(vol_str)
                if vol == 0:
                    self._asks.pop(price, None)
                else:
                    self._asks[price] = vol

            self._last_price     = current_price
            self._last_update_ts = timestamp_ms

            # Snapshot pour spoofing
            self._order_snapshots.append({
                "ts":   timestamp_ms,
                "bids": dict(self._bids),
                "asks": dict(self._asks),
            })

        except Exception as e:
            logger.error(f"OrderBookAnalyzer.on_depth_update {self.symbol}: {e}")

    # ─── Analyse ─────────────────────────────────────────────────────────

    def analyze(self, current_price: float) -> OBISignal:
        signal = OBISignal(
            symbol    = self.symbol,
            timestamp = int(time.time() * 1000),
        )

        if not self._bids or not self._asks:
            return signal

        try:
            self._calc_obi(signal)
            self._calc_walls(signal)
            self._detect_spoofing(signal)
            self._calc_vpin(signal)
            self._compute_score(signal)
        except Exception as e:
            logger.error(f"OrderBookAnalyzer.analyze {self.symbol}: {e}")

        return signal

    def _calc_obi(self, signal: OBISignal):
        """
        OBI Ratio = (bid_vol - ask_vol) / (bid_vol + ask_vol)
        > +0.3 = pression achat | < -0.3 = pression vente
        Depth Ratio = bids_depth / asks_depth top 20
        """
        try:
            top_bids = sorted(self._bids.items(), reverse=True)[:self.depth_levels]
            top_asks = sorted(self._asks.items())[:self.depth_levels]

            bid_vol = sum(v for _, v in top_bids)
            ask_vol = sum(v for _, v in top_asks)
            total   = bid_vol + ask_vol

            if total == 0:
                return

            obi = (bid_vol - ask_vol) / total
            signal.obi_ratio = round(obi, 4)
            self._obi_history.append(obi)

            if abs(obi) >= self.obi_threshold:
                signal.obi_strong = True
                direction = "achat" if obi > 0 else "vente"
                signal.reasons.append(
                    f"OBI fort ({direction}): {obi:.3f} "
                    f"(seuil={self.obi_threshold})"
                )

            # Depth ratio
            if ask_vol > 0:
                signal.depth_ratio = round(bid_vol / ask_vol, 2)
                if signal.depth_ratio > 2.0:
                    signal.reasons.append(
                        f"Depth Ratio élevé: {signal.depth_ratio:.2f} "
                        f"(accumulation institutionnelle)"
                    )

        except Exception as e:
            logger.error(f"_calc_obi {self.symbol}: {e}")

    def _calc_walls(self, signal: OBISignal):
        """
        Bid/Ask Walls : niveau avec volume > 3x moyenne
        → support/résistance fort
        """
        try:
            if not self._bids or not self._asks:
                return

            # Moyenne volumes bids/asks
            bid_vols  = list(self._bids.values())
            ask_vols  = list(self._asks.values())
            avg_bid   = np.mean(bid_vols) if bid_vols else 1.0
            avg_ask   = np.mean(ask_vols) if ask_vols else 1.0

            # Bid wall
            for price, vol in sorted(self._bids.items(), reverse=True)[:self.depth_levels]:
                if vol > avg_bid * self.wall_mult:
                    signal.bid_wall       = True
                    signal.bid_wall_level = price
                    signal.reasons.append(
                        f"Bid Wall @ {price:.4f}: "
                        f"vol={vol:.2f} ({vol/avg_bid:.1f}x avg)"
                    )
                    break

            # Ask wall
            for price, vol in sorted(self._asks.items())[:self.depth_levels]:
                if vol > avg_ask * self.wall_mult:
                    signal.ask_wall       = True
                    signal.ask_wall_level = price
                    signal.reasons.append(
                        f"Ask Wall @ {price:.4f}: "
                        f"vol={vol:.2f} ({vol/avg_ask:.1f}x avg)"
                    )
                    break

        except Exception as e:
            logger.error(f"_calc_walls {self.symbol}: {e}")

    def _detect_spoofing(self, signal: OBISignal):
        """
        Spoofing : gros ordre présent dans snapshot précédent
        mais disparu dans le snapshot actuel sans impact prix
        → signal invalide → ignorer
        """
        try:
            if len(self._order_snapshots) < 2:
                return

            now_snap  = self._order_snapshots[-1]
            prev_snap = self._order_snapshots[-2]

            # Délai entre snapshots
            dt = (now_snap["ts"] - prev_snap["ts"]) / 1000
            if dt > self.spoof_window_s:
                return

            prev_bids = prev_snap["bids"]
            curr_bids = now_snap["bids"]

            # Cherche ordres disparus > 3x moyenne
            if prev_bids:
                avg_vol = np.mean(list(prev_bids.values()))
                for price, vol in prev_bids.items():
                    if vol > avg_vol * self.wall_mult and price not in curr_bids:
                        signal.spoofing = True
                        signal.reasons.append(
                            f"Spoofing détecté: bid {price:.4f} "
                            f"disparu (vol={vol:.2f})"
                        )
                        break

        except Exception as e:
            logger.error(f"_detect_spoofing {self.symbol}: {e}")

    def _calc_vpin(self, signal: OBISignal):
        """
        VPIN simplifié : variance de l'OBI sur les dernières périodes
        > 0.8 = liquidité toxique → skip le trade
        """
        try:
            if len(self._obi_history) < 10:
                return

            obi_arr = np.array(list(self._obi_history))
            vpin    = float(np.std(obi_arr))
            signal.vpin = round(vpin, 4)

            if vpin > self.vpin_threshold:
                signal.vpin_toxic = True
                signal.reasons.append(
                    f"VPIN toxique: {vpin:.4f} > {self.vpin_threshold} "
                    f"→ skip trade"
                )

        except Exception as e:
            logger.error(f"_calc_vpin {self.symbol}: {e}")

    def _compute_score(self, signal: OBISignal):
        """
        Score OBI (0-100) — contribue au scoring gate unifié.

        Barème :
          OBI fort          : +30
          Depth ratio > 2   : +15
          Bid/Ask Wall      : +20
          Pas de spoofing   : +15 (bonus si absent)
          VPIN non toxique  : +10
          Absorption        : +10
        """
        score      = 0
        bull_votes = bear_votes = 0

        # OBI
        if signal.obi_strong:
            score += 30
            if signal.obi_ratio > 0: bull_votes += 1
            else:                    bear_votes += 1

        # Depth ratio
        if signal.depth_ratio > 2.0:
            score += 15
            bull_votes += 1

        # Walls
        if signal.bid_wall:
            score += 20
            bull_votes += 1
        if signal.ask_wall:
            score += 20
            bear_votes += 1

        # Spoofing — pénalise le score
        if signal.spoofing:
            score -= 20
            signal.reasons.append("Spoofing détecté → score -20")

        # VPIN toxique — bloque le trade
        if signal.vpin_toxic:
            score = 0
            signal.side = None
            logger.warning(
                f"[OBI] {signal.symbol} VPIN toxique → trade bloqué"
            )
            return

        # Bonus si pas de spoofing
        if not signal.spoofing:
            score += 15

        # VPIN sain
        if not signal.vpin_toxic:
            score += 10

        score = max(0, min(score, 100))

        if bull_votes > bear_votes:   signal.side = "long"
        elif bear_votes > bull_votes: signal.side = "short"
        else:                         signal.side = None

        signal.score = score

        if score > 0:
            logger.info(
                f"[OBI] {signal.symbol} | Score: {score} | "
                f"Side: {signal.side} | OBI: {signal.obi_ratio:.3f} | "
                f"{' | '.join(signal.reasons[:2])}"
            )


class OBIRegistry:
    """Gestionnaire d'OrderBookAnalyzer par symbole"""

    def __init__(self, pairs: list[str]):
        self._analyzers: dict[str, OrderBookAnalyzer] = {
            s: OrderBookAnalyzer(s) for s in pairs
        }

    def on_depth_update(
        self,
        symbol:        str,
        bids:          list[list],
        asks:          list[list],
        timestamp_ms:  int,
        current_price: float,
    ):
        a = self._analyzers.get(symbol)
        if a:
            a.on_depth_update(bids, asks, timestamp_ms, current_price)

    def analyze(self, symbol: str, current_price: float) -> OBISignal:
        a = self._analyzers.get(symbol)
        if not a:
            return OBISignal(symbol=symbol, timestamp=int(time.time()*1000))
        return a.analyze(current_price)
