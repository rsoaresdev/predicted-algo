"""Mean-reversion strategy for SPY and QQQ (15-minute bars).

Entry (contrarian): price stretches ``num_std`` standard deviations away from the
20-period SMA **and** RSI is in an extreme zone. A two-layer confirmation —
band breach alone fires too many false signals in trending markets.

Exit: price reverts to the SMA **or** RSI crosses back to neutral (55 for longs,
45 for shorts), whichever comes first.

Regime filter: longs only while price > trend_filter SMA; shorts only below.
With ``allow_short=False`` (default for indices) the strategy becomes a
long-only "buy extreme dips in an uptrend" system. Set ``trend_filter=None``
to disable the regime gate entirely.
"""
from __future__ import annotations

from typing import Optional

import pandas as pd

from indicators import atr, rolling_std, rsi, sma
from strategies.base import Action, PositionState, Signal, Strategy


class MeanReversionStrategy(Strategy):
    name = "mean_reversion"

    def __init__(
        self,
        symbol: str,
        timeframe: str,
        lookback: int,
        period: int = 20,
        num_std: float = 2.0,
        trend_filter: Optional[int] = 200,
        rsi_period: int = 14,
        rsi_oversold: int = 35,
        rsi_overbought: int = 65,
        atr_period: int = 14,
        allow_short: bool = False,
    ) -> None:
        super().__init__(symbol, timeframe, lookback, atr_period, allow_short)
        self.period = period
        self.num_std = num_std
        self.trend_filter = trend_filter
        self.rsi_period = rsi_period
        self.rsi_oversold = rsi_oversold
        self.rsi_overbought = rsi_overbought

    def min_bars(self) -> int:
        needed = max(self.period, self.atr_period, self.rsi_period)
        if self.trend_filter:
            needed = max(needed, self.trend_filter)
        return needed + 2

    def evaluate(
        self, df: pd.DataFrame, position: Optional[PositionState]
    ) -> Signal:
        close = df["close"]
        price = float(close.iloc[-1])
        mid = float(sma(close, self.period).iloc[-1])
        std = float(rolling_std(close, self.period).iloc[-1])
        atr_val = float(atr(df, self.atr_period).iloc[-1])
        rsi_val = float(rsi(close, self.rsi_period).iloc[-1])

        upper = mid + self.num_std * std
        lower = mid - self.num_std * std

        # Regime filter: which sides are allowed given the longer-term trend.
        trend_up = trend_down = True
        trend_note = ""
        if self.trend_filter:
            trend_sma = float(sma(close, self.trend_filter).iloc[-1])
            trend_up = price > trend_sma
            trend_down = price < trend_sma
            trend_note = f" [SMA{self.trend_filter}={trend_sma:.2f}]"

        def signal(action: Action, side: Optional[str], reason: str) -> Signal:
            return Signal(
                symbol=self.symbol,
                action=action,
                price=price,
                atr=atr_val,
                reason=reason,
                side=side,
            )

        if position is None:
            # Long entry: band breach + RSI oversold + uptrend.
            if price <= lower and rsi_val < self.rsi_oversold and trend_up:
                return signal(
                    Action.BUY, "long",
                    f"dip {price:.2f} <= lower {lower:.2f} ({self.num_std}σ)"
                    f" RSI={rsi_val:.1f}<{self.rsi_oversold}{trend_note}",
                )
            # Short entry: band breach + RSI overbought + downtrend.
            if price >= upper and rsi_val > self.rsi_overbought and trend_down:
                return signal(
                    Action.SELL, "short",
                    f"spike {price:.2f} >= upper {upper:.2f} ({self.num_std}σ)"
                    f" RSI={rsi_val:.1f}>{self.rsi_overbought}{trend_note}",
                )
            return signal(Action.HOLD, None, "no qualified reversion setup")

        # In a position: exit on SMA reversion OR RSI returning to neutral.
        if position.side == "long":
            if price >= mid:
                return signal(Action.CLOSE, None, f"reverted to SMA {mid:.2f}")
            if rsi_val >= 55:
                return signal(Action.CLOSE, None, f"RSI {rsi_val:.1f} neutral — momentum gone")
        if position.side == "short":
            if price <= mid:
                return signal(Action.CLOSE, None, f"reverted to SMA {mid:.2f}")
            if rsi_val <= 45:
                return signal(Action.CLOSE, None, f"RSI {rsi_val:.1f} neutral — momentum gone")

        return signal(Action.HOLD, None, "holding, not yet reverted")
