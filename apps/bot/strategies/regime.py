"""Market-regime filter: gate risk-ON (equity-beta) longs by a broad-market trend.

Only NEW long entries in the equity sleeve (QQQ / EWG / EEM) are gated: they are
allowed only when a broad gauge (default SPY) is above its long SMA (default
200-day). Counter-regime trend entries are where most of the small losers
cluster, so gating them tends to lift win rate / profit factor at the cost of
fewer trades. GLD / SLV / UUP are NEVER gated — UUP in particular is the
risk-OFF hedge and must be free to go long exactly when equities are weak.

The *gating policy* (which symbols, on/off) lives on the RiskManager; this module
only turns gauge bars into point-in-time risk-on flags.
"""
from __future__ import annotations

import pandas as pd

from indicators import sma


def risk_on_series(close: pd.Series, sma_period: int) -> pd.Series:
    """Per-bar risk-on flag: True where ``close > SMA(sma_period)``, False below,
    NaN during the SMA warmup so callers can treat 'unknown' as risk-on."""
    trend = sma(close, sma_period)
    return (close > trend).where(trend.notna())


class Regime:
    """Point-in-time risk-on flags for the regime gauge (e.g. SPY daily bars).

    The flags are **shifted one bar forward** so a flag derived from a daily
    bar's close only becomes available on the *next* bar — an intraday entry on
    day *d* therefore sees day *d-1*'s completed regime, never day *d*'s
    not-yet-known close. This keeps the filter free of intraday lookahead, the
    same discipline the rest of the backtester uses.

    'Unknown' (before the gauge's SMA is warm, or no data at all) is treated as
    risk-ON so the filter never blocks for lack of information (fail-open).
    """

    def __init__(self, risk_on: pd.Series) -> None:
        self._series = risk_on.sort_index()

    @classmethod
    def from_bars(cls, df: pd.DataFrame | None, sma_period: int) -> "Regime":
        if df is None or df.empty:
            return cls(pd.Series(dtype="object"))
        # shift(1): a bar's regime is only actionable on the following bar.
        return cls(risk_on_series(df["close"], sma_period).shift(1))

    def at(self, ts) -> bool:
        """Risk-on as of ``ts`` — the most recent gauge bar at or before ``ts``."""
        if self._series.empty:
            return True
        val = self._series.asof(ts)
        return True if val is None or pd.isna(val) else bool(val)

    def latest(self) -> bool:
        """Most recent completed risk-on flag (live use)."""
        if self._series.empty:
            return True
        val = self._series.iloc[-1]
        return True if pd.isna(val) else bool(val)
