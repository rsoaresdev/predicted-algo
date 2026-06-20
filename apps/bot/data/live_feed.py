"""Real-time price feed via Alpaca's websocket trade stream.

WHY THIS EXISTS
----------------
The in-loop software stop (``engine._check_software_stop``) used to check the
*last completed bar's* low/high against the stop level. On a 4Hour timeframe
(QQQ's current config) that means a breach could go undetected for up to 4
hours after the price actually touched the stop — the bot would only "see"
it once the bar closed, by which point the price may have moved much further
against the position than the backtest's 0.05% slippage assumption.

This module keeps the *latest trade price* per symbol updated via websocket,
independent of the strategy's bar timeframe, so the software stop can react
within seconds instead of waiting for the next bar close. It does not change
how strategy signals are evaluated (those still use closed bars, as before) —
only how fast a stop breach is detected.

Runs in a background daemon thread; the alpaca-py stream client owns its own
asyncio event loop internally (via ``StockDataStream.run()``), so it cannot
share the main thread's loop.

FAILURE MODE
------------
If the websocket connection is down, reconnecting, or simply hasn't received
a trade for a symbol yet, ``get_price()`` returns ``None``. Callers MUST treat
``None`` as "no live price available" and fall back to their own bar-based
check rather than skip the stop entirely — see engine._check_software_stop.

NOTE: verify ``StockDataStream``'s exact `run()` / `stop()` method signatures
against the installed `alpaca-py` version — these have shifted slightly across
SDK releases. The behaviour assumed here: `run()` blocks (drives its own
asyncio loop) until `stop()` is called from another thread.
"""
from __future__ import annotations

import logging
import threading
from typing import Iterable

from alpaca.data.enums import DataFeed
from alpaca.data.live import StockDataStream

from config import settings

logger = logging.getLogger(__name__)


class LivePriceFeed:
    """Background websocket listener: latest trade price per symbol, in memory."""

    def __init__(self, symbols: Iterable[str], feed: str | None = None) -> None:
        self.symbols = list(symbols)
        self._prices: dict[str, float] = {}
        self._lock = threading.Lock()
        self._thread: threading.Thread | None = None
        self._started = threading.Event()

        self._stream = StockDataStream(
            api_key=settings.alpaca_key_id,
            secret_key=settings.alpaca_secret_key,
            feed=self._resolve_feed(feed),
        )
        if self.symbols:
            self._stream.subscribe_trades(self._on_trade, *self.symbols)

    @staticmethod
    def _resolve_feed(feed: str | None) -> DataFeed:
        value = (feed or settings.alpaca_data_feed or "iex").lower()
        try:
            return DataFeed(value)
        except ValueError:
            logger.warning("Unknown data feed %r — falling back to IEX.", value)
            return DataFeed.IEX

    async def _on_trade(self, trade) -> None:
        with self._lock:
            self._prices[trade.symbol] = float(trade.price)
        self._started.set()

    # --------------------------------------------------------------- access
    def get_price(self, symbol: str) -> float | None:
        """Latest known trade price for ``symbol``, or ``None`` if not seen yet
        (e.g. websocket still connecting, or no trade printed for this symbol
        since startup/reconnect)."""
        with self._lock:
            return self._prices.get(symbol)

    def is_warm(self) -> bool:
        """Whether at least one trade has been received since start()."""
        return self._started.is_set()

    # ---------------------------------------------------------- lifecycle
    def start(self) -> None:
        if self._thread is not None:
            return  # already running
        if not self.symbols:
            logger.warning("LivePriceFeed.start(): no symbols to subscribe — skipping.")
            return
        self._thread = threading.Thread(
            target=self._run_forever, name="live-price-feed", daemon=True
        )
        self._thread.start()
        logger.info("LivePriceFeed starting for %s ...", self.symbols)

    def _run_forever(self) -> None:
        try:
            self._stream.run()
        except Exception as exc:  # pragma: no cover - depends on live network
            logger.exception("LivePriceFeed stream crashed: %s", exc)

    def stop(self) -> None:
        if self._thread is None:
            return
        try:
            self._stream.stop()
        except Exception as exc:  # pragma: no cover - SDK-version dependent
            logger.warning("LivePriceFeed.stop() raised while stopping stream: %s", exc)
        self._thread.join(timeout=5)
        self._thread = None
        logger.info("LivePriceFeed stopped.")
