"""Vectorised technical indicators built on pandas.

Each function returns a pandas Series aligned to the input index; callers
typically use ``.iloc[-1]`` for the most recent (latest closed bar) value.
"""
from __future__ import annotations

import pandas as pd


def sma(series: pd.Series, period: int) -> pd.Series:
    """Simple moving average."""
    return series.rolling(window=period, min_periods=period).mean()


def ema(series: pd.Series, period: int) -> pd.Series:
    """Exponential moving average (standard, no warmup bias correction)."""
    return series.ewm(span=period, adjust=False).mean()


def rolling_std(series: pd.Series, period: int, ddof: int = 0) -> pd.Series:
    """Rolling standard deviation.

    Defaults to population std (ddof=0), which is the convention used for
    Bollinger-band style mean-reversion bands.
    """
    return series.rolling(window=period, min_periods=period).std(ddof=ddof)


def atr(df: pd.DataFrame, period: int = 14) -> pd.Series:
    """Average True Range using Wilder's smoothing.

    Expects columns: 'high', 'low', 'close'. Returned in price units.
    """
    high = df["high"]
    low = df["low"]
    prev_close = df["close"].shift(1)

    true_range = pd.concat(
        [
            (high - low),
            (high - prev_close).abs(),
            (low - prev_close).abs(),
        ],
        axis=1,
    ).max(axis=1)

    # Wilder's smoothing == EMA with alpha = 1/period.
    return true_range.ewm(alpha=1.0 / period, adjust=False).mean()


def adx(df: pd.DataFrame, period: int = 14) -> pd.Series:
    """Average Directional Index (Wilder) — trend STRENGTH, 0..100, direction-agnostic.

    Low ADX (~<20) = choppy/rangebound; high ADX (~>25) = a strong directional
    trend. Used to gate entries away from chop. Expects 'high'/'low'/'close'.
    """
    high, low, close = df["high"], df["low"], df["close"]
    up = high.diff()
    down = -low.diff()
    plus_dm = up.where((up > down) & (up > 0), 0.0)
    minus_dm = down.where((down > up) & (down > 0), 0.0)

    prev_close = close.shift(1)
    tr = pd.concat(
        [(high - low), (high - prev_close).abs(), (low - prev_close).abs()], axis=1
    ).max(axis=1)
    alpha = 1.0 / period
    atr_ = tr.ewm(alpha=alpha, adjust=False).mean()
    plus_di = 100.0 * plus_dm.ewm(alpha=alpha, adjust=False).mean() / atr_
    minus_di = 100.0 * minus_dm.ewm(alpha=alpha, adjust=False).mean() / atr_
    denom = (plus_di + minus_di).replace(0.0, float("nan"))
    dx = 100.0 * (plus_di - minus_di).abs() / denom
    return dx.ewm(alpha=alpha, adjust=False).mean()


def rolling_high(series: pd.Series, period: int) -> pd.Series:
    """Highest value over the trailing ``period`` bars (inclusive)."""
    return series.rolling(window=period, min_periods=period).max()


def rolling_low(series: pd.Series, period: int) -> pd.Series:
    """Lowest value over the trailing ``period`` bars (inclusive)."""
    return series.rolling(window=period, min_periods=period).min()


def rolling_volume_avg(series: pd.Series, period: int) -> pd.Series:
    """Average volume over the trailing ``period`` bars."""
    return series.rolling(window=period, min_periods=period).mean()


def rsi(series: pd.Series, period: int = 14) -> pd.Series:
    """Relative Strength Index using Wilder's smoothing (alpha = 1/period).

    Returns values in [0, 100]. When avg_loss == 0 (pure up-move) RS = inf
    and RSI correctly reaches 100; when avg_gain == 0 it reaches 0.
    """
    delta = series.diff()
    avg_gain = delta.clip(lower=0).ewm(alpha=1.0 / period, adjust=False).mean()
    avg_loss = (-delta).clip(lower=0).ewm(alpha=1.0 / period, adjust=False).mean()
    rs = avg_gain / avg_loss
    return 100 - 100 / (1 + rs)
