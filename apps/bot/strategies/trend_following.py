"""Trend-following strategy for GLD and USO (daily bars).

Uses the 50/200 EMA relationship on the daily timeframe — the natural home for a
200-period trend (on 4h bars, 200 periods is only ~5 weeks and lags badly).
State-based rather than event-based so the
bot aligns with the prevailing trend even if it starts mid-trend:

  * fast EMA above slow EMA  -> bullish  -> want long
  * fast EMA below slow EMA  -> bearish  -> want short (if allow_short) / flat

Exits happen when the relationship flips against the open position (the
crossover). The engine opens the opposite side on the following cycle.
"""
from __future__ import annotations

from typing import Optional

import pandas as pd

from indicators import atr, ema
from strategies.base import Action, PositionState, Signal, Strategy


class TrendFollowingStrategy(Strategy):
    name = "trend_following"

    def __init__(
        self,
        symbol: str,
        timeframe: str,
        lookback: int,
        fast: int = 50,
        slow: int = 200,
        atr_period: int = 14,
        allow_short: bool = True,
    ) -> None:
        super().__init__(symbol, timeframe, lookback, atr_period, allow_short)
        self.fast = fast
        self.slow = slow

    def min_bars(self) -> int:
        return self.slow + 2

    def evaluate(
        self, df: pd.DataFrame, position: Optional[PositionState]
    ) -> Signal:
        close = df["close"]
        price = float(close.iloc[-1])
        fast_val = float(ema(close, self.fast).iloc[-1])
        slow_val = float(ema(close, self.slow).iloc[-1])
        atr_val = float(atr(df, self.atr_period).iloc[-1])

        bullish = fast_val > slow_val

        def signal(action: Action, side: Optional[str], reason: str) -> Signal:
            return Signal(
                symbol=self.symbol,
                action=action,
                price=price,
                atr=atr_val,
                reason=reason,
                side=side,
            )

        trend = (
            f"EMA{self.fast} {fast_val:.2f} "
            f"{'>' if bullish else '<'} EMA{self.slow} {slow_val:.2f}"
        )

        if position is None:
            if bullish:
                return signal(Action.BUY, "long", f"bullish: {trend}")
            if self.allow_short:
                return signal(Action.SELL, "short", f"bearish: {trend}")
            return signal(Action.HOLD, None, f"bearish, shorts disabled: {trend}")

        # Exit when the trend flips against the position.
        if position.side == "long" and not bullish:
            return signal(Action.CLOSE, None, f"trend flipped bearish: {trend}")
        if position.side == "short" and bullish:
            return signal(Action.CLOSE, None, f"trend flipped bullish: {trend}")
        return signal(Action.HOLD, None, f"trend intact: {trend}")
