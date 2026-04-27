"""
Indicateurs techniques complets
─────────────────────────────────────────────────
- EMA, MACD, RSI, BB, ATR (ta lib)
- CVD (Cumulative Volume Delta) — calculé sur aggTrades
- AVWAP (Anchored VWAP) — ancré sur swing high/low
- Regime detector (HV/LV)
- BTC.D proxy + Funding filter
"""
import logging
from typing import Optional
import numpy as np
import pandas as pd
from ta.trend      import EMAIndicator, MACD
from ta.momentum   import RSIIndicator
from ta.volatility import BollingerBands, AverageTrueRange

logger = logging.getLogger(__name__)


def _to_df(ohlcv: list) -> Optional[pd.DataFrame]:
    if not ohlcv or len(ohlcv) < 50:
        return None
    df = pd.DataFrame(
        ohlcv, columns=["ts", "open", "high", "low", "close", "volume"]
    )
    return df.astype({
        "open": float, "high": float, "low": float,
        "close": float, "volume": float,
    })


# ─── REGIME DETECTOR ─────────────────────────────────────────────────────────

def detect_regime(ohlcv: list, hv_mult: float = 1.5, lv_mult: float = 0.8) -> str:
    """
    Retourne "hv" (haute volatilité), "lv" (basse vol) ou "normal".
    ATR14 > ATR50 × hv_mult → HV
    ATR14 < ATR50 × lv_mult → LV
    """
    df = _to_df(ohlcv)
    if df is None:
        return "normal"
    try:
        h, l, c = df["high"], df["low"], df["close"]
        atr14   = float(AverageTrueRange(h, l, c, window=14).average_true_range().iloc[-1])
        atr50   = float(AverageTrueRange(h, l, c, window=50).average_true_range().iloc[-1])

        if atr50 == 0:
            return "normal"
        if atr14 > atr50 * hv_mult:
            return "hv"
        if atr14 < atr50 * lv_mult:
            return "lv"
        return "normal"
    except Exception as e:
        logger.error(f"detect_regime error: {e}")
        return "normal"


def get_atr_values(ohlcv: list) -> tuple[float, float]:
    """Retourne (atr14, atr50) pour le circuit breaker"""
    df = _to_df(ohlcv)
    if df is None:
        return 0.0, 0.0
    try:
        h, l, c = df["high"], df["low"], df["close"]
        atr14   = float(AverageTrueRange(h, l, c, window=14).average_true_range().iloc[-1])
        atr50   = float(AverageTrueRange(h, l, c, window=50).average_true_range().iloc[-1])
        return atr14, atr50
    except Exception as e:
        logger.error(f"get_atr_values error: {e}")
        return 0.0, 0.0


# ─── ATR utilitaire ───────────────────────────────────────────────────────────

def get_atr(ohlcv: list, period: int = 14) -> Optional[float]:
    df = _to_df(ohlcv)
    if df is None:
        return None
    try:
        return float(
            AverageTrueRange(
                df["high"], df["low"], df["close"], window=period
            ).average_true_range().iloc[-1]
        )
    except Exception as e:
        logger.error(f"get_atr error: {e}")
        return None


# ─── EMA CROSS ────────────────────────────────────────────────────────────────

def calc_ema_cross(ohlcv: list, fast: int = 9, slow: int = 21) -> Optional[dict]:
    df = _to_df(ohlcv)
    if df is None:
        return None
    try:
        close      = df["close"]
        ema_fast   = EMAIndicator(close=close, window=fast).ema_indicator()
        ema_slow   = EMAIndicator(close=close, window=slow).ema_indicator()
        price      = float(close.iloc[-1])
        ef_now     = float(ema_fast.iloc[-1])
        es_now     = float(ema_slow.iloc[-1])
        ef_prev    = float(ema_fast.iloc[-2])
        es_prev    = float(ema_slow.iloc[-2])

        cross_up   = ef_prev <= es_prev and ef_now > es_now
        cross_down = ef_prev >= es_prev and ef_now < es_now

        return {
            "price":      price,
            "ema_fast":   round(ef_now, 6),
            "ema_slow":   round(es_now, 6),
            "cross_up":   cross_up,
            "cross_down": cross_down,
            "above":      ef_now > es_now,
        }
    except Exception as e:
        logger.error(f"calc_ema_cross error: {e}")
        return None


def calc_ema_trend(ohlcv: list, fast: int = 50, slow: int = 200) -> Optional[dict]:
    """EMA 50/200 pour le biais HTF"""
    df = _to_df(ohlcv)
    if df is None:
        return None
    try:
        close     = df["close"]
        ema50     = float(EMAIndicator(close=close, window=fast).ema_indicator().iloc[-1])
        ema200    = float(EMAIndicator(close=close, window=slow).ema_indicator().iloc[-1])
        price     = float(close.iloc[-1])
        return {
            "price":    price,
            "ema50":    round(ema50, 6),
            "ema200":   round(ema200, 6),
            "bullish":  price > ema50 > ema200,
            "bearish":  price < ema50 < ema200,
            "bias":     "long" if price > ema200 else "short",
        }
    except Exception as e:
        logger.error(f"calc_ema_trend error: {e}")
        return None


# ─── MACD ────────────────────────────────────────────────────────────────────

def calc_macd(ohlcv: list, fast=12, slow=26, signal=9) -> Optional[dict]:
    df = _to_df(ohlcv)
    if df is None:
        return None
    try:
        close    = df["close"]
        macd_obj = MACD(close=close, window_fast=fast, window_slow=slow, window_sign=signal)
        line     = float(macd_obj.macd().iloc[-1])
        sig      = float(macd_obj.macd_signal().iloc[-1])
        hist     = float(macd_obj.macd_diff().iloc[-1])
        prev_hist = float(macd_obj.macd_diff().iloc[-2])
        return {
            "line":      round(line, 6),
            "signal":    round(sig, 6),
            "hist":      round(hist, 6),
            "cross_up":  hist > 0 and prev_hist <= 0,
            "cross_down": hist < 0 and prev_hist >= 0,
            "bullish":   line > sig,
        }
    except Exception as e:
        logger.error(f"calc_macd error: {e}")
        return None


# ─── RSI ─────────────────────────────────────────────────────────────────────

def calc_rsi(ohlcv: list, period: int = 14) -> Optional[dict]:
    df = _to_df(ohlcv)
    if df is None:
        return None
    try:
        close  = df["close"]
        rsi    = RSIIndicator(close=close, window=period).rsi()
        val    = float(rsi.iloc[-1])
        prev   = float(rsi.iloc[-2])
        price  = float(close.iloc[-1])
        p_prev = float(close.iloc[-2])

        # Divergence haussière : prix fait LL, RSI fait HL
        bull_div = price < p_prev * 0.999 and val > prev + 1.0
        # Divergence baissière : prix fait HH, RSI fait LH
        bear_div = price > p_prev * 1.001 and val < prev - 1.0

        return {
            "value":      round(val, 2),
            "oversold":   val <= 30,
            "overbought": val >= 70,
            "extreme_os": val <= 20,
            "extreme_ob": val >= 80,
            "bull_div":   bull_div,
            "bear_div":   bear_div,
        }
    except Exception as e:
        logger.error(f"calc_rsi error: {e}")
        return None


# ─── BOLLINGER BANDS ─────────────────────────────────────────────────────────

def calc_bb(ohlcv: list, period: int = 20, std: float = 2.0) -> Optional[dict]:
    df = _to_df(ohlcv)
    if df is None:
        return None
    try:
        close  = df["close"]
        bb     = BollingerBands(close=close, window=period, window_dev=std)
        upper  = float(bb.bollinger_hband().iloc[-1])
        middle = float(bb.bollinger_mavg().iloc[-1])
        lower  = float(bb.bollinger_lband().iloc[-1])
        price  = float(close.iloc[-1])
        width  = (upper - lower) / middle if middle else 0
        pct_b  = (price - lower) / (upper - lower) if (upper - lower) > 0 else 0.5
        return {
            "upper":      round(upper, 6),
            "middle":     round(middle, 6),
            "lower":      round(lower, 6),
            "width":      round(width, 4),
            "pct_b":      round(pct_b, 4),
            "price":      price,
            "near_upper": price >= upper * 0.998,
            "near_lower": price <= lower * 1.002,
            "is_ranging": width < 0.04,
        }
    except Exception as e:
        logger.error(f"calc_bb error: {e}")
        return None


# ─── VOLUME SPIKE ────────────────────────────────────────────────────────────

def calc_volume_spike(ohlcv: list, mult: float = 2.0, period: int = 20) -> Optional[dict]:
    df = _to_df(ohlcv)
    if df is None:
        return None
    try:
        vol        = df["volume"]
        vol_now    = float(vol.iloc[-1])
        vol_avg    = float(vol.iloc[-period-1:-1].mean())
        vol_ratio  = vol_now / vol_avg if vol_avg > 0 else 0
        return {
            "vol_now":   round(vol_now, 2),
            "vol_avg":   round(vol_avg, 2),
            "vol_ratio": round(vol_ratio, 2),
            "spike":     vol_ratio >= mult,
        }
    except Exception as e:
        logger.error(f"calc_volume_spike error: {e}")
        return None


# ─── CVD (Cumulative Volume Delta) ───────────────────────────────────────────

class CVDTracker:
    """
    Calcule le CVD en temps réel depuis les aggTrades Binance.
    CVD = somme(buy_vol - sell_vol) sur une fenêtre glissante.
    """
    def __init__(self, window: int = 200):
        self.window   = window
        self._deltas: list[float] = []  # + = buy, - = sell

    def on_trade(self, price: float, qty: float, is_buyer_maker: bool):
        """
        is_buyer_maker=True → vente agressive (seller prend la liquidité)
        is_buyer_maker=False → achat agressif (buyer prend la liquidité)
        """
        delta = -qty if is_buyer_maker else qty
        self._deltas.append(delta)
        if len(self._deltas) > self.window:
            self._deltas.pop(0)

    def get(self) -> dict:
        if not self._deltas:
            return {"cvd": 0.0, "cvd_flip": False, "bullish": False}
        cvd       = sum(self._deltas)
        prev_cvd  = sum(self._deltas[:-10]) if len(self._deltas) > 10 else 0
        cvd_flip  = (prev_cvd < 0 and cvd > 0) or (prev_cvd > 0 and cvd < 0)
        return {
            "cvd":      round(cvd, 4),
            "cvd_flip": cvd_flip,
            "bullish":  cvd > 0,
            "bearish":  cvd < 0,
        }

    def reset(self):
        self._deltas = []


# ─── AVWAP (Anchored VWAP) ───────────────────────────────────────────────────

def calc_avwap(ohlcv: list, anchor_type: str = "swing_low") -> Optional[dict]:
    """
    AVWAP ancré sur le dernier swing high ou swing low.
    anchor_type : "swing_low" ou "swing_high"
    """
    df = _to_df(ohlcv)
    if df is None:
        return None
    try:
        close  = df["close"]
        high   = df["high"]
        low    = df["low"]
        vol    = df["volume"]
        price  = float(close.iloc[-1])

        # Trouve le point d'ancrage (swing sur 10 bougies)
        anchor_idx = _find_swing(high, low, anchor_type, lookback=10)
        if anchor_idx is None:
            return None

        # Calcul VWAP depuis l'ancre
        df_since  = df.iloc[anchor_idx:]
        tp        = (df_since["high"] + df_since["low"] + df_since["close"]) / 3
        cum_tpvol = (tp * df_since["volume"]).cumsum()
        cum_vol   = df_since["volume"].cumsum()
        vwap_series = cum_tpvol / cum_vol
        avwap     = float(vwap_series.iloc[-1])

        # Bands (1 stddev)
        deviation = float(((tp - vwap_series) ** 2 * df_since["volume"]).cumsum().iloc[-1])
        stddev    = (deviation / float(cum_vol.iloc[-1])) ** 0.5 if float(cum_vol.iloc[-1]) > 0 else 0

        return {
            "avwap":        round(avwap, 6),
            "upper_band":   round(avwap + stddev, 6),
            "lower_band":   round(avwap - stddev, 6),
            "price":        price,
            "above":        price > avwap,
            "near_avwap":   abs(price - avwap) / avwap < 0.002 if avwap > 0 else False,
            "anchor_type":  anchor_type,
        }
    except Exception as e:
        logger.error(f"calc_avwap error: {e}")
        return None


def _find_swing(
    high: pd.Series, low: pd.Series,
    swing_type: str, lookback: int = 10
) -> Optional[int]:
    """Trouve l'index du dernier swing high ou low"""
    series = low if swing_type == "swing_low" else high
    best_val = None
    best_idx = None
    for i in range(lookback, len(series) - lookback):
        window = series.iloc[i - lookback: i + lookback + 1]
        val    = series.iloc[i]
        if swing_type == "swing_low" and val == window.min():
            if best_val is None or val < best_val:
                best_val = val
                best_idx = i
        elif swing_type == "swing_high" and val == window.max():
            if best_val is None or val > best_val:
                best_val = val
                best_idx = i
    return best_idx


# ─── SMC ─────────────────────────────────────────────────────────────────────

def calc_smc(ohlcv: list, cfg: dict) -> Optional[dict]:
    df = _to_df(ohlcv)
    if df is None:
        return None
    try:
        close = df["close"]
        high  = df["high"]
        low   = df["low"]
        price = float(close.iloc[-1])

        swing_highs = _swing_points(high, left=5, right=5, is_high=True)
        swing_lows  = _swing_points(low,  left=5, right=5, is_high=False)

        last_sh = swing_highs[-1] if swing_highs else None
        last_sl = swing_lows[-1]  if swing_lows  else None

        bos_bull   = bool(last_sh and price > last_sh)
        bos_bear   = bool(last_sl and price < last_sl)
        choch_bull, choch_bear = _detect_choch(swing_highs, swing_lows, price)

        fvg = _detect_fvg(df, cfg.get("fvg_min_size_pct", 0.15))
        ob  = _detect_order_block(df, cfg.get("ob_lookback", 50))

        bias = "long" if (bos_bull or choch_bull) else (
               "short" if (bos_bear or choch_bear) else "neutral"
        )

        return {
            "price":       price,
            "bias":        bias,
            "bos_bull":    bos_bull,
            "bos_bear":    bos_bear,
            "choch_bull":  choch_bull,
            "choch_bear":  choch_bear,
            "fvg":         fvg,
            "order_block": ob,
            "last_sh":     last_sh,
            "last_sl":     last_sl,
        }
    except Exception as e:
        logger.error(f"calc_smc error: {e}")
        return None


def _swing_points(series, left, right, is_high):
    result = []
    for i in range(left, len(series) - right):
        window = series.iloc[i - left: i + right + 1]
        val    = series.iloc[i]
        if is_high and val == window.max():
            result.append(float(val))
        elif not is_high and val == window.min():
            result.append(float(val))
    return result


def _detect_choch(swing_highs, swing_lows, price):
    choch_bull = choch_bear = False
    if len(swing_highs) >= 2:
        if swing_highs[-2] > swing_highs[-1] and price > swing_highs[-1]:
            choch_bull = True
    if len(swing_lows) >= 2:
        if swing_lows[-2] < swing_lows[-1] and price < swing_lows[-1]:
            choch_bear = True
    return choch_bull, choch_bear


def _detect_fvg(df, min_size_pct):
    for i in range(len(df) - 1, 1, -1):
        high_prev2 = float(df["high"].iloc[i - 2])
        low_curr   = float(df["low"].iloc[i])
        low_prev2  = float(df["low"].iloc[i - 2])
        high_curr  = float(df["high"].iloc[i])
        if low_curr > high_prev2:
            size_pct = (low_curr - high_prev2) / high_prev2
            if size_pct >= min_size_pct / 100:
                return {"type": "bullish", "top": low_curr,
                        "bottom": high_prev2, "size_pct": round(size_pct * 100, 3)}
        if high_curr < low_prev2:
            size_pct = (low_prev2 - high_curr) / low_prev2
            if size_pct >= min_size_pct / 100:
                return {"type": "bearish", "top": low_prev2,
                        "bottom": high_curr, "size_pct": round(size_pct * 100, 3)}
    return None


def _detect_order_block(df, lookback):
    df_slice = df.iloc[-lookback:]
    closes   = df_slice["close"].values
    opens    = df_slice["open"].values
    highs    = df_slice["high"].values
    lows     = df_slice["low"].values
    for i in range(len(df_slice) - 3, 0, -1):
        if closes[i] < opens[i]:
            move = (closes[i + 1] - opens[i + 1]) / opens[i + 1]
            if move > 0.005:
                return {"type": "bullish", "top": float(highs[i]),
                        "bottom": float(lows[i]),
                        "midpoint": float((highs[i] + lows[i]) / 2)}
        if closes[i] > opens[i]:
            move = (opens[i + 1] - closes[i + 1]) / opens[i + 1]
            if move > 0.005:
                return {"type": "bearish", "top": float(highs[i]),
                        "bottom": float(lows[i]),
                        "midpoint": float((highs[i] + lows[i]) / 2)}
    return None
