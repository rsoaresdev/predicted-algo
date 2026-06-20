"""Vectorised, drop-in-compatible replacement for backtester.simulate_asset,
used only by optimize.py's grid search.

WHY THIS EXISTS
----------------
The original `simulate_asset` calls `strat.evaluate(window, position)` once
per bar, and each strategy's `evaluate()` recomputes its indicators (SMA,
EMA, RSI, ATR, rolling high/low...) from scratch over the trailing window
it's given. That's fine for *live* trading (one bar per cycle) but it makes
backtesting/optimizing O(n * window) per candidate: a 2-year 15Min series
(~35k bars) with a ~450-bar window means ~15M+ rolling-window evaluations
per candidate, and pandas' per-call overhead dominates at that scale.

This module precomputes every indicator **once**, vectorised over the full
series, for each strategy family. The bar-by-bar loop then becomes simple
array lookups instead of slicing + recomputation. The trading logic mirrors
each strategy's `evaluate()` exactly (same conditions, same exit rules), so
results are numerically identical to the slow path — just much faster to
produce. The 0.05% slippage, 1-ATR hard stop, and ATR position sizing are
unchanged (still applied by the same simulate_asset-style mechanics).

This file does NOT replace strategies/*.py or backtester.py — it's an
additional fast path used only inside optimize.py's grid search. Live
trading and backtester.py continue to use the original bar-by-bar engine,
so production behaviour is untouched.

If you add a new strategy family, add a matching `_precompute_<name>` and
`_step_<name>` pair here, or fall back to the slow path for that family.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

import numpy as np
import pandas as pd

from backtester import AssetResult, TradeResult, apply_slippage, compute_metrics
from risk import RiskManager
from strategies.mean_reversion import MeanReversionStrategy
from strategies.momentum import MomentumBreakoutStrategy
from strategies.trend_following import TrendFollowingStrategy
from strategies.macd_trend import MACDTrendStrategy
from strategies.volatility_breakout import VolatilityBreakoutStrategy

# Families this fast path supports. Anything else should use the slow path.
SUPPORTED = {"mean_reversion", "trend_following", "momentum", "macd_trend", "volatility_breakout"}


# --------------------------------------------------------------- indicators
def _ema_np(values: np.ndarray, period: int) -> np.ndarray:
    """Same recursion as pandas .ewm(span=period, adjust=False).mean()."""
    alpha = 2.0 / (period + 1.0)
    out = np.empty_like(values, dtype="float64")
    out[0] = values[0]
    for i in range(1, len(values)):
        out[i] = alpha * values[i] + (1 - alpha) * out[i - 1]
    return out


def _wilder_np(values: np.ndarray, period: int) -> np.ndarray:
    """Same recursion as pandas .ewm(alpha=1/period, adjust=False).mean()."""
    alpha = 1.0 / period
    out = np.empty_like(values, dtype="float64")
    out[0] = values[0]
    for i in range(1, len(values)):
        out[i] = alpha * values[i] + (1 - alpha) * out[i - 1]
    return out


def _precompute_common(df: pd.DataFrame, atr_period: int) -> dict:
    close = df["close"].to_numpy(dtype="float64")
    high = df["high"].to_numpy(dtype="float64")
    low = df["low"].to_numpy(dtype="float64")
    prev_close = np.empty_like(close)
    prev_close[0] = close[0]
    prev_close[1:] = close[:-1]
    tr = np.maximum.reduce(
        [high - low, np.abs(high - prev_close), np.abs(low - prev_close)]
    )
    atr_vals = _wilder_np(tr, atr_period)
    return {"close": close, "high": high, "low": low, "atr": atr_vals}


# ----------------------------------------------------------- mean reversion
def _precompute_mean_reversion(df: pd.DataFrame, strat: MeanReversionStrategy) -> dict:
    base = _precompute_common(df, strat.atr_period)
    close_s = df["close"]

    mid = close_s.rolling(strat.period, min_periods=strat.period).mean().to_numpy()
    std = close_s.rolling(strat.period, min_periods=strat.period).std(ddof=0).to_numpy()

    delta = close_s.diff().to_numpy()
    delta[0] = 0.0
    gain = np.clip(delta, 0, None)
    loss = np.clip(-delta, 0, None)
    avg_gain = _wilder_np(gain, strat.rsi_period)
    avg_loss = _wilder_np(loss, strat.rsi_period)
    with np.errstate(divide="ignore", invalid="ignore"):
        rs = avg_gain / avg_loss
    rsi_vals = 100 - 100 / (1 + rs)
    rsi_vals = np.where(avg_loss == 0, 100.0, rsi_vals)
    rsi_vals = np.where((avg_gain == 0) & (avg_loss == 0), 50.0, rsi_vals)

    trend_sma = None
    if strat.trend_filter:
        trend_sma = (
            close_s.rolling(strat.trend_filter, min_periods=strat.trend_filter)
            .mean()
            .to_numpy()
        )

    base.update({"mid": mid, "std": std, "rsi": rsi_vals, "trend_sma": trend_sma})
    return base


def _step_mean_reversion(strat: MeanReversionStrategy, pre: dict, i: int, side: Optional[str]):
    """Returns (action, side, atr) for bar i. action in {'buy','sell','close','hold'}."""
    price = pre["close"][i]
    mid = pre["mid"][i]
    std = pre["std"][i]
    rsi_val = pre["rsi"][i]
    atr_val = pre["atr"][i]

    if np.isnan(mid) or np.isnan(std) or np.isnan(rsi_val):
        return "hold", None, atr_val

    upper = mid + strat.num_std * std
    lower = mid - strat.num_std * std

    trend_up = trend_down = True
    if strat.trend_filter:
        tsma = pre["trend_sma"][i]
        if np.isnan(tsma):
            return "hold", None, atr_val
        trend_up = price > tsma
        trend_down = price < tsma

    if side is None:
        if price <= lower and rsi_val < strat.rsi_oversold and trend_up:
            return "buy", "long", atr_val
        if price >= upper and rsi_val > strat.rsi_overbought and trend_down:
            return "sell", "short", atr_val
        return "hold", None, atr_val

    if side == "long":
        if price >= mid or rsi_val >= 55:
            return "close", None, atr_val
    if side == "short":
        if price <= mid or rsi_val <= 45:
            return "close", None, atr_val
    return "hold", None, atr_val


# ----------------------------------------------------------- trend following
def _precompute_trend_following(df: pd.DataFrame, strat: TrendFollowingStrategy) -> dict:
    base = _precompute_common(df, strat.atr_period)
    close = base["close"]
    fast = _ema_np(close, strat.fast)
    slow = _ema_np(close, strat.slow)
    base.update({"fast": fast, "slow": slow})
    return base


def _step_trend_following(strat: TrendFollowingStrategy, pre: dict, i: int, side: Optional[str]):
    atr_val = pre["atr"][i]
    bullish = pre["fast"][i] > pre["slow"][i]

    if side is None:
        if bullish:
            return "buy", "long", atr_val
        if strat.allow_short:
            return "sell", "short", atr_val
        return "hold", None, atr_val

    if side == "long" and not bullish:
        return "close", None, atr_val
    if side == "short" and bullish:
        return "close", None, atr_val
    return "hold", None, atr_val


# ----------------------------------------------------------- momentum breakout
def _precompute_momentum(df: pd.DataFrame, strat: MomentumBreakoutStrategy) -> dict:
    base = _precompute_common(df, strat.atr_period)
    high_s = df["high"]
    low_s = df["low"]
    vol_s = df["volume"]

    prior_high = (
        high_s.rolling(strat.period, min_periods=strat.period).max().shift(1).to_numpy()
    )
    prior_low = (
        low_s.rolling(strat.exit_period, min_periods=strat.exit_period).min().shift(1).to_numpy()
    )
    vol_avg = (
        vol_s.rolling(strat.period, min_periods=strat.period).mean().to_numpy()
    )
    vol = vol_s.to_numpy(dtype="float64")

    base.update(
        {"prior_high": prior_high, "prior_low": prior_low, "vol": vol, "vol_avg": vol_avg}
    )
    return base


def _step_momentum(strat: MomentumBreakoutStrategy, pre: dict, i: int, side: Optional[str]):
    price = pre["close"][i]
    atr_val = pre["atr"][i]

    if side is None:
        prior_high = pre["prior_high"][i]
        if np.isnan(prior_high):
            return "hold", None, atr_val
        breakout = price > prior_high
        volume_ok = pre["vol"][i] >= strat.vol_mult * pre["vol_avg"][i]
        if breakout and volume_ok:
            return "buy", "long", atr_val
        return "hold", None, atr_val

    # side == "long" (momentum is long-only)
    prior_low = pre["prior_low"][i]
    if not np.isnan(prior_low) and price < prior_low:
        return "close", None, atr_val
    return "hold", None, atr_val


# ----------------------------------------------------------- MACD Trend (NOVO)
def _precompute_macd_trend(df: pd.DataFrame, strat: MACDTrendStrategy) -> dict:
    base = _precompute_common(df, strat.atr_period)
    close_s = df["close"]
    close = base["close"]
    
    ema_fast = _ema_np(close, strat.fast_macd)
    ema_slow = _ema_np(close, strat.slow_macd)
    macd_line = ema_fast - ema_slow
    signal_line = _ema_np(macd_line, strat.signal_macd)
    
    trend_sma = None
    if strat.trend_filter:
        trend_sma = (
            close_s.rolling(strat.trend_filter, min_periods=strat.trend_filter)
            .mean()
            .to_numpy()
        )
        
    base.update({
        "macd_line": macd_line,
        "signal_line": signal_line,
        "trend_sma": trend_sma
    })
    return base

def _step_macd_trend(strat: MACDTrendStrategy, pre: dict, i: int, side: Optional[str]):
    price = pre["close"][i]
    atr_val = pre["atr"][i]
    macd_val = pre["macd_line"][i]
    sig_val = pre["signal_line"][i]
    
    # Needs previous bar for cross detection
    prev_macd = pre["macd_line"][i-1] if i > 0 else macd_val
    prev_sig = pre["signal_line"][i-1] if i > 0 else sig_val
    
    bullish_cross = (macd_val > sig_val) and (prev_macd <= prev_sig)
    bearish_cross = (macd_val < sig_val) and (prev_macd >= prev_sig)
    
    trend_up = trend_down = True
    if strat.trend_filter:
        tsma = pre["trend_sma"][i]
        if np.isnan(tsma):
            return "hold", None, atr_val
        trend_up = price > tsma
        trend_down = price < tsma

    if side is None:
        if bullish_cross and trend_up:
            return "buy", "long", atr_val
        if bearish_cross and trend_down and strat.allow_short:
            return "sell", "short", atr_val
        return "hold", None, atr_val

    if side == "long" and macd_val < sig_val:
        return "close", None, atr_val
    if side == "short" and macd_val > sig_val:
        return "close", None, atr_val
        
    return "hold", None, atr_val


# ----------------------------------------------------------- Volatility Breakout (NOVO)
def _precompute_volatility_breakout(df: pd.DataFrame, strat: VolatilityBreakoutStrategy) -> dict:
    base = _precompute_common(df, strat.atr_period)
    close_s = df["close"]
    
    mid = close_s.rolling(strat.bb_period, min_periods=strat.bb_period).mean().to_numpy()
    std = close_s.rolling(strat.bb_period, min_periods=strat.bb_period).std(ddof=0).to_numpy()
    
    base.update({
        "mid": mid,
        "std": std
    })
    return base

def _step_volatility_breakout(strat: VolatilityBreakoutStrategy, pre: dict, i: int, side: Optional[str]):
    price = pre["close"][i]
    atr_val = pre["atr"][i]
    mid = pre["mid"][i]
    std = pre["std"][i]
    
    if np.isnan(mid) or np.isnan(std) or i == 0:
        return "hold", None, atr_val
        
    upper = mid + strat.bb_std * std
    lower = mid - strat.bb_std * std
    
    prev_close = pre["close"][i-1]
    prev_mid = pre["mid"][i-1]
    prev_std = pre["std"][i-1]
    
    if np.isnan(prev_mid) or np.isnan(prev_std):
        return "hold", None, atr_val
        
    prev_upper = prev_mid + strat.bb_std * prev_std
    prev_lower = prev_mid - strat.bb_std * prev_std
    
    if side is None:
        if prev_close < prev_upper and price > upper:
            return "buy", "long", atr_val
        if prev_close > prev_lower and price < lower and strat.allow_short:
            return "sell", "short", atr_val
        return "hold", None, atr_val
        
    if side == "long" and price < mid:
        return "close", None, atr_val
    if side == "short" and price > mid:
        return "close", None, atr_val
        
    return "hold", None, atr_val


# ------------------------------------------------------------------ engine maps
_PRECOMPUTE = {
    "mean_reversion": _precompute_mean_reversion,
    "trend_following": _precompute_trend_following,
    "momentum": _precompute_momentum,
    "macd_trend": _precompute_macd_trend,
    "volatility_breakout": _precompute_volatility_breakout,
}
_STEP = {
    "mean_reversion": _step_mean_reversion,
    "trend_following": _step_trend_following,
    "momentum": _step_momentum,
    "macd_trend": _step_macd_trend,
    "volatility_breakout": _step_volatility_breakout,
}


# ------------------------------------------------------------------ engine
def _trade_pnl(position: dict, exit_fill: float) -> float:
    if position["side"] == "long":
        return position["qty"] * (exit_fill - position["entry_fill"])
    return position["qty"] * (position["entry_fill"] - exit_fill)


def _unrealized(position: dict, price: float) -> float:
    if position["side"] == "long":
        return position["qty"] * (price - position["entry_fill"])
    return position["qty"] * (position["entry_fill"] - price)


def _make_trade(symbol, position, exit_fill, exit_time, reason, pnl) -> TradeResult:
    notional = position["entry_fill"] * position["qty"]
    return TradeResult(
        symbol=symbol,
        side=position["side"],
        entry_time=position["entry_time"],
        exit_time=exit_time,
        entry_price=position["entry_fill"],
        exit_price=exit_fill,
        qty=position["qty"],
        pnl=pnl,
        return_pct=(pnl / notional) if notional else 0.0,
        reason=reason,
    )


def fast_simulate_asset(
    symbol: str,
    strat,
    df: pd.DataFrame,
    test_start,
    capital: float,
    slippage: float,
    risk: RiskManager,
) -> AssetResult:
    """Vectorised-precompute version of backtester.simulate_asset."""
    family = strat.name
    if family not in SUPPORTED:
        raise ValueError(f"fast_simulate_asset: unsupported family '{family}'")

    pre = _PRECOMPUTE[family](df, strat)
    step = _STEP[family]

    closes = pre["close"]
    highs = pre["high"]
    lows = pre["low"]
    index = df.index
    n = len(df)
    min_bars = strat.min_bars()

    test_start_ts = pd.Timestamp(test_start)
    if index.tz is not None and test_start_ts.tzinfo is None:
        test_start_ts = test_start_ts.tz_localize(index.tz)
    elif index.tz is None and test_start_ts.tzinfo is not None:
        test_start_ts = test_start_ts.tz_localize(None)

    realized = 0.0
    position: Optional[dict] = None
    trades: list[TradeResult] = []
    eq_times: list = []
    eq_values: list = []

    for i in range(min_bars, n):
        ts = index[i]
        bar_close = float(closes[i])
        in_test = ts >= test_start_ts

        # 1) Hard-stop check (never on entry bar) — identical to slow path.
        if position is not None and i > position["entry_index"]:
            stopped = False
            exit_fill = 0.0
            if position["side"] == "long" and float(lows[i]) <= position["stop"]:
                exit_fill = apply_slippage(position["stop"], is_buy=False, slippage=slippage)
                stopped = True
            elif position["side"] == "short" and float(highs[i]) >= position["stop"]:
                exit_fill = apply_slippage(position["stop"], is_buy=True, slippage=slippage)
                stopped = True
            if stopped:
                pnl = _trade_pnl(position, exit_fill)
                realized += pnl
                trades.append(_make_trade(symbol, position, exit_fill, ts, "stop", pnl))
                position = None

        # 2) Strategy step (array lookup, not a window recompute).
        side = position["side"] if position is not None else None
        action, new_side, atr_val = step(strat, pre, i, side)

        if position is not None:
            if action == "close":
                exit_fill = apply_slippage(
                    bar_close, is_buy=(position["side"] == "short"), slippage=slippage
                )
                pnl = _trade_pnl(position, exit_fill)
                realized += pnl
                trades.append(_make_trade(symbol, position, exit_fill, ts, "exit", pnl))
                position = None
        elif in_test and action in ("buy", "sell"):
            entry_side = "long" if action == "buy" else "short"
            if entry_side == "short" and not strat.allow_short:
                pass
            else:
                equity_now = capital + realized
                qty = risk.position_size(
                    equity_now, atr_val, bar_close, buying_power=equity_now, symbol=symbol
                )
                if qty > 0 and atr_val > 0:
                    entry_fill = apply_slippage(
                        bar_close, is_buy=(entry_side == "long"), slippage=slippage
                    )
                    stop = risk.stop_price(entry_fill, entry_side, atr_val)
                    position = {
                        "side": entry_side,
                        "qty": qty,
                        "entry_fill": entry_fill,
                        "stop": stop,
                        "entry_time": ts,
                        "entry_index": i,
                    }

        if in_test:
            eq = capital + realized
            if position is not None:
                eq += _unrealized(position, bar_close)
            eq_times.append(ts)
            eq_values.append(eq)

    if position is not None and eq_values:
        last_ts = index[n - 1]
        last_close = float(closes[n - 1])
        exit_fill = apply_slippage(
            last_close, is_buy=(position["side"] == "short"), slippage=slippage
        )
        pnl = _trade_pnl(position, exit_fill)
        realized += pnl
        trades.append(_make_trade(symbol, position, exit_fill, last_ts, "end_of_test", pnl))
        eq_values[-1] = capital + realized

    equity = pd.Series(eq_values, index=pd.DatetimeIndex(eq_times), dtype="float64")
    metrics = compute_metrics(trades, equity, capital)
    return AssetResult(symbol, strat.name, trades, equity, metrics)