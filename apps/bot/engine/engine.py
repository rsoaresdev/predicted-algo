"""The orchestration loop: pull bars, evaluate strategies, manage risk, persist.

Per cycle, for each asset:
  1. Reconcile in-memory state against the broker (detect stop-outs).
  2. Pull the latest bars for the asset's timeframe.
  3. Skip if we've already acted on the most recent closed bar.
  4. Ask the strategy for a Signal.
  5. Open / close positions, applying ATR sizing, the hard stop, and the
     correlation filter, then record the trade in PostgreSQL.

Set ``dry_run=True`` to compute and log everything without submitting orders.
"""
from __future__ import annotations

import logging
import time
from datetime import datetime, timedelta, timezone

import pandas as pd

from config import settings
from data import AlpacaBroker, LivePriceFeed
from database.repository import (
    close_trade,
    create_open_trade,
    load_open_trades,
    record_daily_metrics,
    update_trade_stop,
)
from notify import Notifier
from risk import PortfolioGuards, RiskManager
from strategies import Action, PositionState, Signal, Strategy, build_strategies
from strategies.regime import Regime

logger = logging.getLogger(__name__)


class TradingEngine:
    def __init__(self, broker: AlpacaBroker | None = None, dry_run: bool | None = None) -> None:
        self.dry_run = settings.dry_run if dry_run is None else dry_run
        self.broker = broker or AlpacaBroker()
        self.strategies: dict[str, Strategy] = build_strategies()
        self.risk = RiskManager(
            risk_per_trade=settings.risk_per_trade,
            max_position_fraction=settings.max_position_fraction,
            use_trailing_stop=settings.use_trailing_stop,
            trail_breakeven_atr=settings.trail_breakeven_atr,
            trail_distance_atr=settings.trail_distance_atr,
            use_regime_filter=settings.use_regime_filter,
            stop_atr_mult=settings.stop_atr_mult,
        )
        # Broad-market risk-on flag, refreshed once per cycle when the regime
        # filter is enabled. Defaults to True (fail-open) so a missing/late
        # gauge fetch never blocks trading.
        self._regime_on = True

        # Portfolio safety guards (drawdown breaker + daily kill-switch) and the
        # alert channel. The guards block only NEW entries; open positions keep
        # being protected by their stops.
        self.guards = PortfolioGuards(
            drawdown_halt_pct=settings.drawdown_halt_pct,
            drawdown_resume_pct=settings.drawdown_resume_pct,
            max_daily_loss_pct=settings.max_daily_loss_pct,
        )
        self.notify = Notifier()
        self._entries_allowed = True
        self._entry_block_reason = ""
        self._was_blocked = False
        self._summary_day = None

        self.poll_seconds = settings.poll_seconds

        # Real-time trade-price feed (websocket), used by _check_software_stop
        # so stop detection doesn't wait for the next bar close (see
        # data/live_feed.py for why this matters, especially on QQQ's 4Hour
        # timeframe). Falls back to the old bar-based check if no live price
        # is available yet (e.g. just started, or websocket reconnecting).
        self.price_feed = LivePriceFeed(self.strategies.keys())

        # symbol -> PositionState for positions we believe are open.
        self.position_states: dict[str, PositionState] = {}
        # symbol -> timestamp of the last bar we acted on (dedupe per bar).
        self._last_bar: dict[str, object] = {}

    # ------------------------------------------------------------------ setup
    def bootstrap(self) -> None:
        """Rebuild in-memory position state from open DB rows (live mode only)."""
        if self.dry_run:
            return
        try:
            for row in load_open_trades():
                self.position_states[row["asset"]] = PositionState(
                    symbol=row["asset"],
                    side=row["direction"],
                    qty=row["qty"],
                    entry_price=row["entry_price"],
                    stop_price=row["stop_price"],
                )
            if self.position_states:
                logger.info("Restored open positions: %s", list(self.position_states))
        except Exception as exc:  # pragma: no cover - depends on live DB
            logger.warning("Could not load open trades from DB: %s", exc)

    # ------------------------------------------------------------------- loop
    def run(self) -> None:
        logger.info(
            "Engine starting | dry_run=%s | assets=%s",
            self.dry_run,
            list(self.strategies),
        )
        self.bootstrap()
        self.price_feed.start()
        try:
            while True:
                try:
                    if not self.broker.is_market_open():
                        logger.info("Market closed — sleeping %ss.", self.poll_seconds)
                    else:
                        self.run_once()
                        equity, _ = self.broker.get_account_snapshot()
                        record_daily_metrics(equity)
                except Exception as exc:
                    logger.exception("Cycle error: %s", exc)
                time.sleep(self.poll_seconds)
        finally:
            self.shutdown()

    def shutdown(self) -> None:
        """Stop background resources (the websocket price feed). Call this on
        exit so the bot doesn't leave a dangling connection/thread behind."""
        self.price_feed.stop()

    def run_once(self) -> None:
        """Evaluate every asset exactly once."""
        equity, buying_power = self.broker.get_account_snapshot()

        broker_positions = self.broker.get_positions()
        self._reconcile(broker_positions)
        self._refresh_regime()

        # Portfolio safety guards: roll the daily counter / high-water mark, then
        # decide once per cycle whether NEW entries are allowed. Open positions
        # are unaffected (their stops still run below).
        today = datetime.now(timezone.utc).date()
        self._maybe_daily_summary(today, equity)
        self.guards.observe_equity(equity, today)
        self._entries_allowed, self._entry_block_reason = self.guards.check(equity)
        if not self._entries_allowed and not self._was_blocked:
            self.notify.send(
                f"New entries halted — {self._entry_block_reason}", level="warning"
            )
        self._was_blocked = not self._entries_allowed
        self._heartbeat(equity)

        for symbol, strat in self.strategies.items():
            try:
                df = self.broker.get_bars(symbol, strat.timeframe, strat.lookback)
            except Exception as exc:
                logger.warning("[%s] bar fetch failed: %s", symbol, exc)
                continue

            if df is None or len(df) < strat.min_bars():
                logger.debug("[%s] not enough bars (%s).", symbol, 0 if df is None else len(df))
                continue

            # Data-quality guard: never act on a stale or NaN latest bar.
            if not self._bars_fresh(symbol, strat, df):
                continue

            # Ratchet the trailing stop up (in the trade's favour) BEFORE the
            # stop check, so a tightened level is enforced this same cycle.
            self._update_trailing_stop(symbol, df)

            # In-loop protective stop (when broker-side OTO stops are off, e.g.
            # fractional sizing). Runs every poll, independent of the per-bar
            # dedupe below, so a breach is caught as soon as the bar shows it.
            if self._check_software_stop(symbol, df):
                continue

            last_ts = df.index[-1]
            if self._last_bar.get(symbol) == last_ts:
                continue  # already acted on this bar
            self._last_bar[symbol] = last_ts

            position = self.position_states.get(symbol)
            signal = strat.evaluate(df, position)
            self._handle_signal(signal, strat, equity, buying_power)

    # -------------------------------------------------------------- internals
    def _reconcile(self, broker_positions: dict) -> None:
        """Detect positions that vanished at the broker (stop-outs) and record them."""
        if self.dry_run:
            return
        for symbol in list(self.position_states):
            if symbol not in broker_positions:
                state = self.position_states[symbol]
                exit_price = state.stop_price or state.entry_price
                logger.info(
                    "[%s] no broker position — treating as stop-out @ %.2f.",
                    symbol, exit_price,
                )
                pnl = close_trade(asset=symbol, exit_price=exit_price, exit_reason="stop_out")
                if pnl is not None:
                    self.guards.record_realized(pnl)
                self.notify.send(
                    f"Stop-out {symbol} @ {exit_price:.2f}"
                    + (f" (P&L ${pnl:,.2f})" if pnl is not None else ""),
                    level="warning",
                )
                self.position_states.pop(symbol, None)

    _TF_MINUTES = {"15Min": 15, "1Hour": 60, "4Hour": 240, "1Day": 1440}

    def _bars_fresh(self, symbol: str, strat: Strategy, df) -> bool:
        """Reject acting on a NaN or stale latest bar (data-quality guard #19).

        A bad/late feed should never trigger a trade. NaN OHLC is always
        rejected; a bar older than ~3 timeframes + 90 min is treated as stale
        (a generous bound so normal gaps and market opens don't false-positive).
        """
        last = df.iloc[-1]
        if any(pd.isna(last.get(c)) for c in ("open", "high", "low", "close")):
            logger.warning("[%s] latest bar has NaN OHLC — skipping this cycle.", symbol)
            return False
        tf_min = self._TF_MINUTES.get(strat.timeframe, 60)
        max_age = timedelta(minutes=tf_min * 3 + 90)
        try:
            age = datetime.now(timezone.utc) - df.index[-1].to_pydatetime()
        except Exception:
            return True  # can't determine age — don't block on it
        if age > max_age:
            logger.warning(
                "[%s] latest bar is stale (%s old > %s) — skipping this cycle.",
                symbol, age, max_age,
            )
            return False
        return True

    def _heartbeat(self, equity: float) -> None:
        """One structured line per cycle for liveness/observability (#18)."""
        logger.info(
            "HEARTBEAT | equity=$%.2f | open=%d %s | entries=%s | regime=%s | %s",
            equity,
            len(self.position_states),
            list(self.position_states),
            "ON" if self._entries_allowed else f"HALTED ({self._entry_block_reason})",
            self._regime_on if self.risk.use_regime_filter else "n/a",
            self.guards.status(equity) if self.guards.enabled else "guards off",
        )

    def _maybe_daily_summary(self, today, equity: float) -> None:
        """On the first cycle of a new UTC day, push a summary of the day that
        just ended (reads the guard's still-unreset daily realized P&L)."""
        if self._summary_day is None:
            self._summary_day = today
            return
        if today == self._summary_day:
            return
        self.notify.send(
            f"Daily summary {self._summary_day}: realized "
            f"${self.guards.s.day_realized:,.2f} | equity ${equity:,.2f} | "
            f"open {len(self.position_states)} "
            f"({', '.join(self.position_states) or 'none'})"
        )
        self._summary_day = today

    def _refresh_regime(self) -> None:
        """Refresh the broad-market risk-on flag once per cycle (regime filter).

        Pulls the gauge's (e.g. SPY) daily bars and reads the most recent
        *completed* risk-on flag. On any failure it keeps the previous value
        (which defaults to True at startup), so a data hiccup degrades to "don't
        gate" rather than blocking every equity long.
        """
        if not self.risk.use_regime_filter:
            return
        try:
            limit = max(settings.regime_sma + 30, 60)
            df = self.broker.get_bars(settings.regime_symbol, "1Day", limit)
            if df is not None and not df.empty:
                on = Regime.from_bars(df, settings.regime_sma).latest()
                if on != self._regime_on:
                    logger.info(
                        "Regime: %s now risk-%s (%s>SMA%d).",
                        settings.regime_symbol, "ON" if on else "OFF",
                        settings.regime_symbol, settings.regime_sma,
                    )
                self._regime_on = on
        except Exception as exc:  # pragma: no cover - depends on live data feed
            logger.warning(
                "Regime refresh failed (%s) — keeping risk_on=%s.", exc, self._regime_on
            )

    def _update_trailing_stop(self, symbol: str, df) -> None:
        """Move ``symbol``'s protective stop in the trade's favour as price runs.

        Folds the latest price into the position's high-/low-water mark (live
        websocket price preferred, last completed bar's high/low as a floor) and
        asks the RiskManager for the new trailing level. When the stop tightens
        we update it in memory, persist it (so a restart resumes at the trailed
        level, not the entry stop), and — when the broker holds an OTO stop
        (whole-share mode) — amend the resting order. No-op when trailing is
        disabled, or for DB-restored positions until they re-arm an ``extreme``.
        """
        if not self.risk.use_trailing_stop:
            return
        pos = self.position_states.get(symbol)
        if pos is None or pos.stop_price is None or pos.atr is None:
            return

        live = self.price_feed.get_price(symbol)
        if pos.side == "long":
            bar_extreme = float(df["high"].iloc[-1])
            candidate = max(bar_extreme, live) if live is not None else bar_extreme
            pos.extreme = candidate if pos.extreme is None else max(pos.extreme, candidate)
        else:
            bar_extreme = float(df["low"].iloc[-1])
            candidate = min(bar_extreme, live) if live is not None else bar_extreme
            pos.extreme = candidate if pos.extreme is None else min(pos.extreme, candidate)

        new_stop = self.risk.trail_stop(
            pos.side, pos.entry_price, pos.atr, pos.extreme, pos.stop_price
        )
        if new_stop == pos.stop_price:
            return

        logger.info(
            "[%s] TRAIL stop %.2f -> %.2f (extreme=%.2f, entry=%.2f).",
            symbol, pos.stop_price, new_stop, pos.extreme, pos.entry_price,
        )
        pos.stop_price = new_stop
        if not self.dry_run:
            try:
                update_trade_stop(asset=symbol, stop_price=new_stop)
            except Exception as exc:  # pragma: no cover - depends on live DB
                logger.warning("[%s] could not persist trailing stop: %s", symbol, exc)
            if settings.use_protective_stop:
                try:
                    self.broker.replace_protective_stop(symbol, new_stop)
                except Exception as exc:  # pragma: no cover - depends on live broker
                    logger.warning("[%s] could not amend broker stop: %s", symbol, exc)

    def _check_software_stop(self, symbol: str, df) -> bool:
        """Close a position whose stop has been breached.

        The software equivalent of the broker OTO stop, used when
        ``settings.use_protective_stop`` is False (fractional accounts, where
        Alpaca won't attach a stop to the entry). Runs every poll, independent
        of the per-bar dedupe, so a breach is caught as soon as possible.

        Detection is two-tiered:
          1. PREFERRED: the websocket ``LivePriceFeed`` latest trade price —
             reacts within seconds of the stop being touched, regardless of
             the strategy's bar timeframe (critical for QQQ's 4Hour bars,
             where the old bar-close check could lag up to 4 hours behind).
          2. FALLBACK: if no live price is available yet (feed just started,
             reconnecting, or no trade printed since startup), falls back to
             the last completed bar's low/high — the original behaviour. This
             is strictly better than skipping the check, even though it's
             slower to react.

        Returns True if it closed the position this cycle.
        """
        if settings.use_protective_stop:
            return False  # the broker holds the stop; nothing to do in-loop
        position = self.position_states.get(symbol)
        if position is None or position.stop_price is None:
            return False

        live_price = self.price_feed.get_price(symbol)
        if live_price is not None:
            breached = (
                (position.side == "long" and live_price <= position.stop_price)
                or (position.side == "short" and live_price >= position.stop_price)
            )
            if not breached:
                return False
            exit_price = live_price
            detection = "live"
        else:
            # Fallback: websocket has no price for this symbol yet.
            low = float(df["low"].iloc[-1])
            high = float(df["high"].iloc[-1])
            breached = (
                (position.side == "long" and low <= position.stop_price)
                or (position.side == "short" and high >= position.stop_price)
            )
            if not breached:
                return False
            exit_price = float(df["close"].iloc[-1])
            detection = "bar_fallback"
            logger.warning(
                "[%s] software stop triggered via BAR FALLBACK — no live price yet "
                "(websocket cold/disconnected). Detection latency may be up to one "
                "%s bar.", symbol, self.strategies[symbol].timeframe,
            )

        logger.info(
            "[%s] SOFTWARE STOP (%s) @ stop=%.2f exit~%.2f — closing.",
            symbol, detection, position.stop_price, exit_price,
        )
        self._close(
            Signal(
                symbol=symbol, action=Action.CLOSE, price=exit_price, atr=0.0,
                reason=f"software_stop:{detection}",
            ),
            reason="software_stop",
        )
        return True

    def _handle_signal(
        self, signal: Signal, strat: Strategy, equity: float, buying_power: float
    ) -> None:
        position = self.position_states.get(signal.symbol)

        if signal.action == Action.HOLD:
            logger.debug("[%s] HOLD — %s", signal.symbol, signal.reason)
            return

        if signal.action in (Action.BUY, Action.SELL) and position is None:
            side = "long" if signal.action == Action.BUY else "short"
            if side == "short" and not strat.allow_short:
                logger.debug("[%s] short signal ignored (shorts disabled).", signal.symbol)
                return
            self._open(signal, side, strat, equity, buying_power)

        elif signal.action == Action.CLOSE and position is not None:
            self._close(signal, reason=f"strategy_exit:{strat.name}")

    def _open(
        self, signal: Signal, side: str, strat: Strategy, equity: float, buying_power: float
    ) -> None:
        # Portfolio safety guards (drawdown breaker / daily kill-switch): block
        # ALL new entries while tripped. Decided once per cycle in run_once.
        if not self._entries_allowed:
            logger.info(
                "[%s] entry blocked — %s.", signal.symbol, self._entry_block_reason
            )
            return

        # Correlation filter: block a new long if too many correlated peers
        # are already long (instance method — reads this RiskManager's groups).
        if side == "long" and not self.risk.correlation_filter_ok(
            signal.symbol, self.position_states
        ):
            return

        # Regime filter: block a new equity-sleeve long while the broad gauge is
        # risk-OFF (no-op when the filter is disabled or the symbol is ungated).
        if side == "long" and not self.risk.regime_filter_ok(
            signal.symbol, self._regime_on
        ):
            return

        qty = self.risk.position_size(
            equity, signal.atr, signal.price, buying_power, symbol=signal.symbol
        )
        if qty <= 0:
            logger.info(
                "[%s] computed qty=0 (atr=%.4f price=%.2f) — skipping entry.",
                signal.symbol, signal.atr, signal.price,
            )
            return

        # Reference 1-ATR stop level — submitted to the broker only when
        # protective stops are enabled (whole-share sizing). On a fractional
        # account it is logged for reference but exits come from the strategy.
        stop = self.risk.stop_price(signal.price, side, signal.atr)
        broker_stop = stop if settings.use_protective_stop else None
        max_loss = self.risk.max_loss(qty, signal.atr)

        logger.info(
            "[%s] OPEN %s qty=%.4f @~%.2f stop=%.2f%s maxloss=$%.2f (%.2f%% equity) | %s",
            signal.symbol, side, qty, signal.price, stop,
            "" if settings.use_protective_stop else " (ref only, no broker stop)",
            max_loss, (max_loss / equity * 100.0) if equity else 0.0, signal.reason,
        )

        if not self.dry_run:
            self.broker.submit_entry(signal.symbol, qty, side, broker_stop)
            create_open_trade(
                asset=signal.symbol,
                direction=side,
                strategy=strat.name,
                qty=qty,
                entry_price=signal.price,
                stop_price=stop,
            )

        self.position_states[signal.symbol] = PositionState(
            symbol=signal.symbol,
            side=side,
            qty=qty,
            entry_price=signal.price,
            stop_price=stop,
            atr=signal.atr,
            extreme=signal.price,
        )

    def _close(self, signal: Signal, reason: str) -> None:
        symbol = signal.symbol
        logger.info("[%s] CLOSE @~%.2f | %s | %s", symbol, signal.price, reason, signal.reason)

        if not self.dry_run:
            # Cancel the resting protective stop before flattening to avoid an orphan.
            try:
                self.broker.cancel_open_orders(symbol)
            except Exception as exc:
                logger.warning("[%s] could not cancel open orders: %s", symbol, exc)
            self.broker.close_position(symbol)
            pnl = close_trade(asset=symbol, exit_price=signal.price, exit_reason=reason)
            if pnl is not None:
                self.guards.record_realized(pnl)
            if reason == "software_stop":
                self.notify.send(
                    f"Software stop {symbol} @ ~{signal.price:.2f}"
                    + (f" (P&L ${pnl:,.2f})" if pnl is not None else ""),
                    level="warning",
                )

        self.position_states.pop(symbol, None)
