"""Momentum breakout strategy for SMH (1-hour bars), long only.

Entry: price breaks above the prior 20-period high AND current volume is at
least ``vol_mult`` (1.5x) the 20-period average volume.

Exit: a **Donchian channel trailing exit** — close the long when price closes
below the lowest low of the prior ``exit_period`` bars. This is the standard
breakout-system companion to a channel entry: it lets winners run (unlike a
fixed SMA exit) while ratcheting the give-back as the trade trends. The hard
1-ATR stop still caps the initial downside.
"""
from __future__ import annotations

from typing import Optional

import pandas as pd

from indicators import atr, rolling_high, rolling_low, rolling_volume_avg
from strategies.base import Action, PositionState, Signal, Strategy


class MomentumBreakoutStrategy(Strategy):
    name = "momentum"

    def __init__(
        self,
        symbol: str,
        timeframe: str,
        lookback: int,
        period: int = 20,
        vol_mult: float = 1.5,
        exit_period: int = 10,
        atr_period: int = 14,
        allow_short: bool = False,
    ) -> None:
        super().__init__(symbol, timeframe, lookback, atr_period, allow_short)
        self.period = period
        self.vol_mult = vol_mult
        self.exit_period = exit_period

    def min_bars(self) -> int:
        return max(self.period, self.exit_period, self.atr_period) + 2

    def evaluate(
        self, df: pd.DataFrame, position: Optional[PositionState]
    ) -> Signal:
        close = df["close"]
        price = float(close.iloc[-1])
        atr_val = float(atr(df, self.atr_period).iloc[-1])

        # Prior-period channel highs/lows exclude the current bar via shift(1).
        prior_high = float(rolling_high(df["high"], self.period).shift(1).iloc[-1])
        prior_low = float(rolling_low(df["low"], self.exit_period).shift(1).iloc[-1])
        vol = float(df["volume"].iloc[-1])
        vol_avg = float(rolling_volume_avg(df["volume"], self.period).iloc[-1])

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
            breakout = price > prior_high
            volume_ok = vol >= self.vol_mult * vol_avg
            if breakout and volume_ok:
                return signal(
                    Action.BUY, "long",
                    f"breakout {price:.2f} > {self.period}-high {prior_high:.2f}, "
                    f"vol {vol:.0f} >= {self.vol_mult}x avg {vol_avg:.0f}",
                )
            return signal(Action.HOLD, None, "no qualified breakout")

        # Long-only Donchian trailing exit.
        if position.side == "long" and price < prior_low:
            return signal(
                Action.CLOSE, None,
                f"close {price:.2f} < {self.exit_period}-period low {prior_low:.2f}",
            )
        return signal(Action.HOLD, None, "momentum intact")
