"""Volatility Expansion Breakout."""
from __future__ import annotations
from typing import Optional
import pandas as pd

from indicators import atr, sma, rolling_std
from strategies.base import Action, PositionState, Signal, Strategy

class VolatilityBreakoutStrategy(Strategy):
    name = "volatility_breakout"

    def __init__(
        self, symbol: str, timeframe: str, lookback: int,
        bb_period: int = 20, bb_std: float = 2.0, exit_bars: int = 10,
        atr_period: int = 14, allow_short: bool = False,
    ) -> None:
        super().__init__(symbol, timeframe, lookback, atr_period, allow_short)
        self.bb_period = bb_period
        self.bb_std = bb_std
        self.exit_bars = exit_bars  # Simple time-based or SMA reversion exit
        # A simple state to track bars since entry would require engine support, 
        # so we'll use an SMA crossover exit for simplicity here.

    def min_bars(self) -> int:
        return max(self.bb_period, self.atr_period) + 2

    def evaluate(self, df: pd.DataFrame, position: Optional[PositionState]) -> Signal:
        close = df["close"]
        price = float(close.iloc[-1])
        atr_val = float(atr(df, self.atr_period).iloc[-1])

        mid = sma(close, self.bb_period)
        std = rolling_std(close, self.bb_period)
        
        # Current and previous bands
        upper = float((mid + self.bb_std * std).iloc[-1])
        lower = float((mid - self.bb_std * std).iloc[-1])
        prior_upper = float((mid + self.bb_std * std).iloc[-2])
        prior_lower = float((mid - self.bb_std * std).iloc[-2])
        prior_close = float(close.iloc[-2])

        def signal(action: Action, side: Optional[str], reason: str) -> Signal:
            return Signal(symbol=self.symbol, action=action, price=price, atr=atr_val, reason=reason, side=side)

        if position is None:
            # Breakout: Previous close was inside bands, current price breaks upper band
            if prior_close < prior_upper and price > upper:
                return signal(Action.BUY, "long", f"Price {price:.2f} broke upper BB {upper:.2f}")
            if prior_close > prior_lower and price < lower and self.allow_short:
                return signal(Action.SELL, "short", f"Price {price:.2f} broke lower BB {lower:.2f}")
            return signal(Action.HOLD, None, "Inside bands")

        # Exit: Price reverts back to the middle moving average
        mid_val = float(mid.iloc[-1])
        if position.side == "long" and price < mid_val:
            return signal(Action.CLOSE, None, f"Reverted to BB mid {mid_val:.2f}")
        if position.side == "short" and price > mid_val:
            return signal(Action.CLOSE, None, f"Reverted to BB mid {mid_val:.2f}")
            
        return signal(Action.HOLD, None, "Riding breakout")