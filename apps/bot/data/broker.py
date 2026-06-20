"""Alpaca broker + market-data access via the modern ``alpaca-py`` SDK.

alpaca-py splits responsibilities across two clients:
  * ``TradingClient``            -> account, positions, orders, market clock
  * ``StockHistoricalDataClient``-> historical bars

This module wraps both behind a single :class:`AlpacaBroker` so the rest of the
engine never imports the SDK directly. Swapping providers (or stubbing for
tests) means reimplementing this one class.
"""
from __future__ import annotations

import logging

import pandas as pd
from alpaca.data.enums import Adjustment, DataFeed
from alpaca.data.historical import StockHistoricalDataClient
from alpaca.data.requests import StockBarsRequest
from alpaca.data.timeframe import TimeFrame, TimeFrameUnit
from alpaca.trading.client import TradingClient
from alpaca.trading.enums import OrderClass, OrderSide, QueryOrderStatus, TimeInForce
from alpaca.trading.requests import (
    GetOrdersRequest,
    MarketOrderRequest,
    ReplaceOrderRequest,
    StopLossRequest,
)

from config import settings

logger = logging.getLogger(__name__)


# String timeframe -> alpaca-py TimeFrame factory.
_TIMEFRAMES = {
    "15Min": lambda: TimeFrame(15, TimeFrameUnit.Minute),
    "1Hour": lambda: TimeFrame(1, TimeFrameUnit.Hour),
    "4Hour": lambda: TimeFrame(4, TimeFrameUnit.Hour),
    "1Day": lambda: TimeFrame(1, TimeFrameUnit.Day),
}


def resolve_timeframe(timeframe: str) -> TimeFrame:
    if timeframe not in _TIMEFRAMES:
        raise ValueError(f"Unsupported timeframe: {timeframe!r}")
    return _TIMEFRAMES[timeframe]()


def _resolve_feed(feed: str | None) -> DataFeed:
    value = (feed or settings.alpaca_data_feed or "iex").lower()
    try:
        return DataFeed(value)
    except ValueError:
        logger.warning("Unknown data feed %r — falling back to IEX.", value)
        return DataFeed.IEX


class AlpacaBroker:
    """Thin, engine-facing wrapper over the alpaca-py trading + data clients."""

    def __init__(self) -> None:
        settings.validate()
        self.trading = TradingClient(
            api_key=settings.alpaca_key_id,
            secret_key=settings.alpaca_secret_key,
            paper=settings.alpaca_paper,
        )
        self.data = StockHistoricalDataClient(
            api_key=settings.alpaca_key_id,
            secret_key=settings.alpaca_secret_key,
        )

    # ------------------------------------------------------------- account
    def get_account_snapshot(self) -> tuple[float, float]:
        """Return ``(equity, buying_power)``."""
        account = self.trading.get_account()
        return float(account.equity), float(account.buying_power)

    def is_market_open(self) -> bool:
        return bool(self.trading.get_clock().is_open)

    def get_positions(self) -> dict:
        """Map of ``symbol -> Position`` for all open broker positions."""
        return {p.symbol: p for p in self.trading.get_all_positions()}

    # ---------------------------------------------------------------- data
    def get_bars(
        self, symbol: str, timeframe: str, limit: int, feed: str | None = None
    ) -> pd.DataFrame:
        """Most recent ``limit`` bars for ``symbol`` as a timestamp-indexed frame.

        alpaca-py returns a MultiIndex (symbol, timestamp); we drop the symbol
        level so downstream indicators see a plain timestamp index. Columns:
        open, high, low, close, volume, trade_count, vwap.
        """
        request = StockBarsRequest(
            symbol_or_symbols=symbol,
            timeframe=resolve_timeframe(timeframe),
            limit=limit,
            feed=_resolve_feed(feed),
            adjustment=Adjustment.RAW,
        )
        df = self.data.get_stock_bars(request).df
        if df is None or df.empty:
            return df
        if isinstance(df.index, pd.MultiIndex):
            df = df.droplevel(0)
        return df

    def get_bars_range(
        self,
        symbol: str,
        timeframe: str,
        start,
        end=None,
        feed: str | None = None,
    ) -> pd.DataFrame:
        """All bars for ``symbol`` between ``start`` and ``end`` (datetimes).

        Used by the backtester. The SDK paginates large ranges automatically.
        Same column/index shape as :meth:`get_bars`.
        """
        request = StockBarsRequest(
            symbol_or_symbols=symbol,
            timeframe=resolve_timeframe(timeframe),
            start=start,
            end=end,
            feed=_resolve_feed(feed),
            adjustment=Adjustment.RAW,
        )
        df = self.data.get_stock_bars(request).df
        if df is None or df.empty:
            return df
        if isinstance(df.index, pd.MultiIndex):
            df = df.droplevel(0)
        return df

    # -------------------------------------------------------------- orders
    def submit_entry(self, symbol: str, qty: float, side: str, stop_price: float | None = None):
        """Market entry.

        If ``stop_price`` is given, the entry carries an attached protective
        stop (OTO order, GTC) — broker-side protection that survives a restart,
        but this requires **whole-share** quantities (Alpaca rejects stops on
        fractional orders).

        If ``stop_price`` is ``None``, a plain market order is submitted (DAY
        TIF, fractional-safe) and exits are left to the strategy's own CLOSE
        signal. This is the small-fractional-account path.
        """
        order_side = OrderSide.BUY if side == "long" else OrderSide.SELL
        if stop_price is None:
            order = MarketOrderRequest(
                symbol=symbol,
                qty=qty,
                side=order_side,
                time_in_force=TimeInForce.DAY,  # fractional orders must be DAY
            )
        else:
            order = MarketOrderRequest(
                symbol=symbol,
                qty=qty,
                side=order_side,
                time_in_force=TimeInForce.GTC,
                order_class=OrderClass.OTO,
                stop_loss=StopLossRequest(stop_price=stop_price),
            )
        return self.trading.submit_order(order_data=order)

    def cancel_open_orders(self, symbol: str) -> None:
        """Cancel any resting orders for ``symbol`` (e.g. the protective stop)."""
        request = GetOrdersRequest(status=QueryOrderStatus.OPEN, symbols=[symbol])
        for order in self.trading.get_orders(filter=request):
            self.trading.cancel_order_by_id(order.id)

    def replace_protective_stop(self, symbol: str, new_stop_price: float) -> None:
        """Amend the stop price of ``symbol``'s resting protective stop order.

        Only used in whole-share mode (``use_protective_stop=True``), where the
        broker holds an OTO stop that the trailing logic ratchets up. Finds the
        open ``stop``/``stop_limit`` order for the symbol and replaces its stop
        price; safe to call when no such order exists (no-op).
        """
        request = GetOrdersRequest(status=QueryOrderStatus.OPEN, symbols=[symbol])
        for order in self.trading.get_orders(filter=request):
            if getattr(order, "stop_price", None) is None:
                continue  # not a stop order (e.g. the filled/working entry leg)
            self.trading.replace_order_by_id(
                order.id, ReplaceOrderRequest(stop_price=round(new_stop_price, 2))
            )
            return

    def close_position(self, symbol: str) -> None:
        self.trading.close_position(symbol)
