"""MACD Crossover with Trend Filter."""
from __future__ import annotations
from typing import Optional
import pandas as pd

from config import settings
from indicators import atr, ema, sma
from strategies.base import Action, PositionState, Signal, Strategy

class MACDTrendStrategy(Strategy):
    name = "macd_trend"

    def __init__(
        self, symbol: str, timeframe: str, lookback: int,
        fast_macd: int = 12, slow_macd: int = 26, signal_macd: int = 9,
        trend_filter: int = 200, atr_period: int = 14, allow_short: bool = False,
        exit_requires_trend: Optional[bool] = None,
    ) -> None:
        super().__init__(symbol, timeframe, lookback, atr_period, allow_short)
        self.fast_macd = fast_macd
        self.slow_macd = slow_macd
        self.signal_macd = signal_macd
        self.trend_filter = trend_filter
        # When True, only exit on an opposite MACD cross that is ALSO confirmed by
        # the trend filter (price the wrong side of the SMA), instead of on every
        # cross — so a winner isn't cut on noise while the trend holds (the
        # trailing stop still protects it). Defaults to the global config toggle.
        self.exit_requires_trend = (
            settings.macd_exit_requires_trend
            if exit_requires_trend is None
            else exit_requires_trend
        )

    def min_bars(self) -> int:
        return max(self.slow_macd + self.signal_macd, self.trend_filter, self.atr_period) + 2

    def evaluate(self, df: pd.DataFrame, position: Optional[PositionState]) -> Signal:
        close = df["close"]
        price = float(close.iloc[-1])
        atr_val = float(atr(df, self.atr_period).iloc[-1])

        # Calc MACD
        ema_fast = ema(close, self.fast_macd)
        ema_slow = ema(close, self.slow_macd)
        macd_line = ema_fast - ema_slow
        signal_line = ema(macd_line, self.signal_macd)
        
        macd_val = float(macd_line.iloc[-1])
        sig_val = float(signal_line.iloc[-1])
        
        # MACD cruzou acima da linha de sinal recentemente
        bullish_cross = macd_val > sig_val and float(macd_line.iloc[-2]) <= float(signal_line.iloc[-2])
        bearish_cross = macd_val < sig_val and float(macd_line.iloc[-2]) >= float(signal_line.iloc[-2])

        trend_sma = float(sma(close, self.trend_filter).iloc[-1])
        trend_up = price > trend_sma
        trend_down = price < trend_sma

        def signal(action: Action, side: Optional[str], reason: str) -> Signal:
            return Signal(symbol=self.symbol, action=action, price=price, atr=atr_val, reason=reason, side=side)

        if position is None:
            if bullish_cross and trend_up:
                return signal(Action.BUY, "long", f"MACD bull cross + price > SMA{self.trend_filter}")
            if bearish_cross and trend_down and self.allow_short:
                return signal(Action.SELL, "short", f"MACD bear cross + price < SMA{self.trend_filter}")
            return signal(Action.HOLD, None, "No MACD setup")

        # Exits based on opposite cross. With exit_requires_trend, the cross must
        # ALSO be confirmed by the trend filter (price the wrong side of the SMA);
        # otherwise the position is held (the trailing stop manages the downside),
        # which stops winners being cut on a transient cross while the trend holds.
        if self.exit_requires_trend:
            if position.side == "long" and macd_val < sig_val and trend_down:
                return signal(Action.CLOSE, None, f"MACD down + price < SMA{self.trend_filter}")
            if position.side == "short" and macd_val > sig_val and trend_up:
                return signal(Action.CLOSE, None, f"MACD up + price > SMA{self.trend_filter}")
            return signal(Action.HOLD, None, "Holding MACD trend (trend intact)")

        if position.side == "long" and macd_val < sig_val:
            return signal(Action.CLOSE, None, "MACD crossed down")
        if position.side == "short" and macd_val > sig_val:
            return signal(Action.CLOSE, None, "MACD crossed up")

        return signal(Action.HOLD, None, "Holding MACD trend")