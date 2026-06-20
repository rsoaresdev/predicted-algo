"""Strategy abstraction shared by all assets."""
from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from enum import Enum
from typing import Optional

import pandas as pd


class Action(str, Enum):
    BUY = "buy"      # open / desire a long
    SELL = "sell"    # open / desire a short
    CLOSE = "close"  # exit an existing position
    HOLD = "hold"    # do nothing


@dataclass
class PositionState:
    """Lightweight view of a currently open position held by the engine."""

    symbol: str
    side: str          # 'long' | 'short'
    qty: float
    entry_price: float
    stop_price: float | None = None
    # Entry ATR and the best price seen since entry — used to ratchet a
    # trailing stop. ``extreme`` is the highest price for a long / lowest for a
    # short; both are None for positions restored from the DB on restart (the
    # trail then re-arms from the current bar, never loosening the saved stop).
    atr: float | None = None
    extreme: float | None = None


@dataclass
class Signal:
    symbol: str
    action: Action
    price: float          # latest close used for sizing / logging
    atr: float            # latest ATR in price units
    reason: str = ""
    side: Optional[str] = None  # 'long' | 'short' for entry actions


class Strategy(ABC):
    """Base class. Subclasses implement :meth:`evaluate`."""

    name = "base"

    def __init__(
        self,
        symbol: str,
        timeframe: str,
        lookback: int,
        atr_period: int = 14,
        allow_short: bool = True,
    ) -> None:
        self.symbol = symbol
        self.timeframe = timeframe
        self.lookback = lookback
        self.atr_period = atr_period
        self.allow_short = allow_short

    def min_bars(self) -> int:
        """Minimum number of bars required before the strategy can evaluate."""
        return self.atr_period + 2

    @abstractmethod
    def evaluate(
        self, df: pd.DataFrame, position: Optional[PositionState]
    ) -> Signal:
        """Return a Signal given the latest bars and current position (or None)."""
        raise NotImplementedError
