"""Strategy registry and factory."""
from __future__ import annotations

from config import ASSET_CONFIGS, settings
from strategies.base import Action, PositionState, Signal, Strategy
from strategies.mean_reversion import MeanReversionStrategy
from strategies.momentum import MomentumBreakoutStrategy
from strategies.trend_following import TrendFollowingStrategy
from strategies.macd_trend import MACDTrendStrategy
from strategies.volatility_breakout import VolatilityBreakoutStrategy

_REGISTRY: dict[str, type[Strategy]] = {
    "mean_reversion": MeanReversionStrategy,
    "trend_following": TrendFollowingStrategy,
    "momentum": MomentumBreakoutStrategy,
    "macd_trend": MACDTrendStrategy,
    "volatility_breakout": VolatilityBreakoutStrategy,
}


def build_strategies(atr_period: int | None = None) -> dict[str, Strategy]:
    """Instantiate one strategy per configured asset."""
    atr_period = atr_period or settings.atr_period
    strategies: dict[str, Strategy] = {}
    for symbol, cfg in ASSET_CONFIGS.items():
        cls = _REGISTRY[cfg["strategy"]]
        strategies[symbol] = cls(
            symbol=symbol,
            timeframe=cfg["timeframe"],
            lookback=cfg["lookback"],
            atr_period=atr_period,
            allow_short=cfg.get("allow_short", False),
            **cfg["params"],
        )
    return strategies


__all__ = [
    "Action",
    "PositionState",
    "Signal",
    "Strategy",
    "build_strategies",
]
