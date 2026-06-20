"""Event-driven backtester for the SPY/QQQ/GLD/USO/SMH strategies.

Validates the three trading strategies (mean reversion, trend following,
momentum breakout) over ~1 year of Alpaca historical data, reusing the *live*
strategy, risk, and indicator code so the test matches production behaviour.

Features
--------
* Bar-by-bar simulation with realistic **0.05% slippage** per fill.
* Intrabar hard-stop checks (the live 1-ATR / 1%-equity stop).
* ATR position sizing via the shared :class:`RiskManager`, **fractional by
  default** (matches live trading; see ``RiskManager.position_size``).
* All assets are simulated **together in true chronological order** (not
  independently), so ``RiskManager.correlation_filter_ok`` sees every other
  asset's live position state exactly as it would in production — a new long
  in a correlated group (e.g. XLK/QQQ/SPY/XLF, GLD/SLV) can be blocked because
  of a position already open in a *different* asset.
* Per-asset and combined-portfolio report: **Win rate, Profit factor,
  Max drawdown, Sharpe ratio** (Sharpe/MaxDD on a daily mark-to-market curve).
* A **simple walk-forward** breakdown: the test period is split into sequential
  out-of-sample folds and metrics are reported per fold to show temporal
  robustness. (The strategies are rule-based with fixed parameters, so there is
  no in-sample optimisation step — the walk-forward validates stability across
  rolling periods rather than re-fitting.)
* **Strict gate:** if any strategy's full-period Sharpe ratio is negative, the
  report is printed and then the script raises :class:`NegativeSharpeError` and
  halts with a non-zero exit code.

Usage
-----
    python backtester.py
    python backtester.py --years 1 --capital 100000 --slippage 0.0005 --folds 4
    python backtester.py --ignore-correlation   # A/B: disable the correlation gate
    python backtester.py --account 2000 --fill-timing same_close   # A/B: legacy fill
"""
from __future__ import annotations

import argparse
import logging
import math
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Optional

import pandas as pd

from config import settings
from indicators import adx
from risk import PortfolioGuards, RiskManager
from strategies import Action, PositionState, Strategy, build_strategies
from strategies.regime import Regime

# Note: AlpacaBroker is imported lazily inside run_backtest() so the simulation
# and metric functions can be imported and tested without the SDK installed.

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------- defaults
DEFAULT_CAPITAL = 100_000.0
DEFAULT_SLIPPAGE = 0.0010  # 0.10% per fill — realistic planning baseline for a
# small retail account using market orders on ETFs (the old 0.05% was optimistic
# and the edge proved highly cost-sensitive). Stress-test production candidates at
# --slippage 0.0015 (0.15%). This default also flows into optimize.py, so future
# optimizations now select for cost-robust (lower-turnover) parameters.
DEFAULT_FOLDS = 4
TRADING_DAYS = 252
# Approximate tradable bars per calendar day, used to size the warmup fetch.
BARS_PER_DAY = {"15Min": 26, "1Hour": 7, "4Hour": 2, "1Day": 1}

# Fill-timing models for the shared-account simulation (see simulate_shared_account):
#   "same_close" -> legacy/optimistic: signal computed on bar i is filled at
#                   bar i's own close (zero-latency assumption).
#   "next_open"  -> realistic: signal computed on bar i is queued and filled
#                   at bar i+1's open, the earliest a real order could react.
FILL_TIMINGS = ("same_close", "next_open")
DEFAULT_FILL_TIMING = "next_open"


class NegativeSharpeError(RuntimeError):
    """Raised when a strategy's full-period Sharpe ratio is negative."""


@dataclass
class TradeResult:
    symbol: str
    side: str
    entry_time: pd.Timestamp
    exit_time: pd.Timestamp
    entry_price: float
    exit_price: float
    qty: float  # fractional-capable (see RiskManager.position_size)
    pnl: float
    return_pct: float
    reason: str
    stop: float = 0.0  # initial protective stop at entry (for reporting/export)


@dataclass
class AssetResult:
    symbol: str
    strategy_name: str
    trades: list[TradeResult]
    equity: pd.Series  # mark-to-market, indexed by bar timestamp (test period)
    metrics: dict = field(default_factory=dict)


# --------------------------------------------------------------- mechanics
def apply_slippage(price: float, is_buy: bool, slippage: float) -> float:
    """Slippage always works against you: pay more buying, receive less selling."""
    return price * (1.0 + slippage) if is_buy else price * (1.0 - slippage)


def _unrealized(position: dict, price: float) -> float:
    if position["side"] == "long":
        return position["qty"] * (price - position["entry_fill"])
    return position["qty"] * (position["entry_fill"] - price)


def _trade_pnl(position: dict, exit_fill: float) -> float:
    if position["side"] == "long":
        return position["qty"] * (exit_fill - position["entry_fill"])
    return position["qty"] * (position["entry_fill"] - exit_fill)


def _to_state(symbol: str, position: Optional[dict]) -> Optional[PositionState]:
    if position is None:
        return None
    return PositionState(
        symbol=symbol,
        side=position["side"],
        qty=position["qty"],
        entry_price=position["entry_fill"],
        stop_price=position["stop"],
    )


def warmup_days(strat: Strategy) -> int:
    """Calendar days of history to fetch *before* the test window so indicators
    are fully warmed up by the time scoring begins."""
    window = strat.min_bars() + 250
    bpd = BARS_PER_DAY.get(strat.timeframe, 7)
    return int(math.ceil(window / bpd * 1.6)) + 5


def _make_trade(symbol, position, exit_fill, exit_time, reason, pnl) -> TradeResult:
    notional = position["entry_fill"] * position["qty"]
    return TradeResult(
        symbol=symbol,
        side=position["side"],
        entry_time=position["entry_time"],
        exit_time=exit_time,
        entry_price=position["entry_fill"],
        exit_price=exit_fill,
        qty=position["qty"],
        pnl=pnl,
        return_pct=(pnl / notional) if notional else 0.0,
        reason=reason,
        stop=position.get("stop_init", position.get("stop", 0.0)),
    )


# ------------------------------------------------------- portfolio simulation
def simulate_portfolio(
    assets: dict[str, tuple[Strategy, pd.DataFrame]],
    test_start: datetime,
    capital: float,
    slippage: float,
    risk: RiskManager,
    ignore_correlation: bool = False,
    regime: "Regime | None" = None,
) -> list[AssetResult]:
    """Run every asset together, in true chronological (cross-asset) order.

    Each asset still funds its own independent capital sub-account, exactly
    as before (``equity_now = capital + realized`` *for that asset only*) —
    that part of the live bot's behaviour (per-strategy book) is unchanged.
    What's new is a single shared ``position_states`` map, updated as bars
    are processed in real time order across *all* assets, so
    ``RiskManager.correlation_filter_ok`` can block a new long in one asset
    because a correlated asset elsewhere already has one open — the same
    check the live bot makes before submitting an order.

    Mixed timeframes (1Hour/4Hour) are handled naturally: bars are merged
    into one global event queue sorted by timestamp, so a 4Hour bar and the
    1Hour bars inside its window interleave in the order they actually
    happened.
    """
    state: dict[str, dict] = {}
    for symbol, (strat, df) in assets.items():
        n = len(df)
        min_bars = strat.min_bars()
        state[symbol] = {
            "strat": strat,
            "df": df,
            "closes": df["close"].to_numpy(),
            "highs": df["high"].to_numpy(),
            "lows": df["low"].to_numpy(),
            "index": df.index,
            "n": n,
            "min_bars": min_bars,
            "window_len": min_bars + 250,
            "realized": 0.0,
            "position": None,
            "trades": [],
            "eq_times": [],
            "eq_values": [],
        }

    # Global chronological event queue across every asset: (ts, symbol, bar_index).
    # Symbol is a tie-breaker for determinism when two assets share a timestamp.
    events: list[tuple[pd.Timestamp, str, int]] = [
        (st["index"][i], symbol, i)
        for symbol, st in state.items()
        for i in range(st["min_bars"], st["n"])
    ]
    events.sort(key=lambda e: (e[0], e[1]))

    # Shared across all assets: who is currently long/short, right now.
    position_states: dict[str, PositionState] = {}

    for ts, symbol, i in events:
        st = state[symbol]
        position = st["position"]
        bar_close = float(st["closes"][i])
        in_test = ts >= test_start

        # 1) Hard-stop check on an open position (never on its entry bar).
        if position is not None and i > position["entry_index"]:
            stopped = False
            exit_fill = 0.0
            if position["side"] == "long" and float(st["lows"][i]) <= position["stop"]:
                exit_fill = apply_slippage(position["stop"], is_buy=False, slippage=slippage)
                stopped = True
            elif position["side"] == "short" and float(st["highs"][i]) >= position["stop"]:
                exit_fill = apply_slippage(position["stop"], is_buy=True, slippage=slippage)
                stopped = True
            if stopped:
                pnl = _trade_pnl(position, exit_fill)
                st["realized"] += pnl
                st["trades"].append(_make_trade(symbol, position, exit_fill, ts, "stop", pnl))
                position = None
                st["position"] = None
                position_states.pop(symbol, None)

        # 2) Strategy evaluation on a trailing window (matches live evaluate()).
        lo = max(0, i - st["window_len"] + 1)
        window = st["df"].iloc[lo : i + 1]
        signal = st["strat"].evaluate(window, _to_state(symbol, position))

        if position is not None:
            if signal.action == Action.CLOSE:
                exit_fill = apply_slippage(
                    bar_close, is_buy=(position["side"] == "short"), slippage=slippage
                )
                pnl = _trade_pnl(position, exit_fill)
                st["realized"] += pnl
                st["trades"].append(
                    _make_trade(symbol, position, exit_fill, ts, signal.reason or "exit", pnl)
                )
                position = None
                st["position"] = None
                position_states.pop(symbol, None)
        elif in_test and signal.action in (Action.BUY, Action.SELL):
            side = "long" if signal.action == Action.BUY else "short"
            if side == "short" and not st["strat"].allow_short:
                pass
            elif side == "long" and not ignore_correlation and not risk.correlation_filter_ok(
                symbol, position_states
            ):
                pass  # blocked: too many correlated longs already open elsewhere
            elif side == "long" and not risk.regime_filter_ok(
                symbol, regime.at(ts) if regime is not None else True
            ):
                pass  # blocked: risk-OFF regime for a gated equity name
            else:
                equity_now = capital + st["realized"]
                qty = risk.position_size(
                    equity_now, signal.atr, bar_close, buying_power=equity_now, symbol=symbol
                )
                if qty > 0 and signal.atr > 0:
                    entry_fill = apply_slippage(
                        bar_close, is_buy=(side == "long"), slippage=slippage
                    )
                    stop = risk.stop_price(entry_fill, side, signal.atr)
                    position = {
                        "side": side,
                        "qty": qty,
                        "entry_fill": entry_fill,
                        "stop": stop,
                        "stop_init": stop,
                        "atr": signal.atr,
                        "extreme": entry_fill,
                        "entry_time": ts,
                        "entry_index": i,
                    }
                    st["position"] = position
                    position_states[symbol] = _to_state(symbol, position)

        # 2b) Trail the protective stop. This bar's extreme is folded in only
        #     AFTER the stop check in (1), so there is no intrabar lookahead.
        #     No-op when trailing is disabled on the RiskManager.
        pos = st["position"]
        if pos is not None:
            if pos["side"] == "long":
                pos["extreme"] = max(pos["extreme"], float(st["highs"][i]))
            else:
                pos["extreme"] = min(pos["extreme"], float(st["lows"][i]))
            pos["stop"] = risk.trail_stop(
                pos["side"], pos["entry_fill"], pos["atr"], pos["extreme"], pos["stop"]
            )

        # 3) Mark-to-market equity, recorded only within the test window
        #    (and only at this asset's own bar timestamps, as before).
        if in_test:
            eq = capital + st["realized"]
            if st["position"] is not None:
                eq += _unrealized(st["position"], bar_close)
            st["eq_times"].append(ts)
            st["eq_values"].append(eq)

    # Force-close any position still open at the end of the test data.
    results: list[AssetResult] = []
    for symbol, st in state.items():
        if st["position"] is not None and st["eq_values"]:
            last_ts = st["index"][st["n"] - 1]
            last_close = float(st["closes"][st["n"] - 1])
            exit_fill = apply_slippage(
                last_close, is_buy=(st["position"]["side"] == "short"), slippage=slippage
            )
            pnl = _trade_pnl(st["position"], exit_fill)
            st["realized"] += pnl
            st["trades"].append(
                _make_trade(symbol, st["position"], exit_fill, last_ts, "end_of_test", pnl)
            )
            st["eq_values"][-1] = capital + st["realized"]

        equity = pd.Series(st["eq_values"], index=pd.DatetimeIndex(st["eq_times"]), dtype="float64")
        metrics = compute_metrics(st["trades"], equity, capital)
        results.append(AssetResult(symbol, st["strat"].name, st["trades"], equity, metrics))

    return results


# ----------------------------------------------- shared single-account model
@dataclass
class SharedResult:
    """Outcome of a single shared-cash-pool backtest (see simulate_shared_account)."""

    equity: pd.Series                 # one portfolio mark-to-market curve
    trades: list[TradeResult]         # every trade, all assets
    per_asset: dict[str, dict]        # symbol -> trade attribution (no sub-equity)
    account_capital: float
    fill_timing: str = DEFAULT_FILL_TIMING


def simulate_shared_account(
    assets: dict[str, tuple[Strategy, pd.DataFrame]],
    test_start: datetime,
    account_capital: float,
    slippage: float,
    risk: RiskManager,
    ignore_correlation: bool = False,
    use_stop: bool = True,
    fill_timing: str = DEFAULT_FILL_TIMING,
    regime: "Regime | None" = None,
    guards: "PortfolioGuards | None" = None,
    reentry_cooldown_bars: int = 0,
    adx_min: float = 0.0,
    adx_period: int = 14,
    scale_out_frac: float = 0.0,
    scale_out_atr: float = 3.0,
) -> SharedResult:
    """Realistic single-account backtest: ONE shared cash pool for all assets.

    Unlike :func:`simulate_portfolio` (which gives every asset its own
    independent sub-account), here all assets draw from and return to a single
    cash balance — exactly like the live bot trading one Alpaca account.
    Opening a long *spends* cash; positions compete for the same buying power;
    each is sized against total account equity with the RiskManager's
    ``max_position_fraction`` cap. This is the honest model for "what would €X
    actually do" on a small account. Long-only (the configured universe); any
    short signal is skipped.

    ``fill_timing`` controls execution latency, the main lever for how
    optimistic the simulation is:

    * ``"same_close"`` (legacy): the signal computed on bar i's window
      (which includes bar i itself) is filled immediately at bar i's own
      close. This is a zero-latency assumption — equivalent to placing and
      filling an order at a price you only know *because* the bar already
      closed. It overstates edge, especially for fast strategies with many
      trades.
    * ``"next_open"`` (default, realistic): a BUY/CLOSE signal computed on
      bar i is queued and only filled at bar i+1's open — the earliest price
      a real order reacting to that bar's close could actually get. This is
      the standard "no lookahead" convention for close-triggered signals.
      The 1-ATR stop is unaffected either way: it represents a resting stop
      order that can fill intrabar on the day the price is touched, which is
      realistic in both modes.

    Run both and compare (see ``run_shared_backtest(fill_timing=...)``) to
    see how much of the backtested edge depends on zero-latency execution.
    """
    if fill_timing not in FILL_TIMINGS:
        raise ValueError(f"fill_timing must be one of {FILL_TIMINGS}, got {fill_timing!r}")

    state: dict[str, dict] = {}
    for symbol, (strat, df) in assets.items():
        state[symbol] = {
            "strat": strat,
            "df": df,
            "opens": df["open"].to_numpy(),
            "closes": df["close"].to_numpy(),
            "highs": df["high"].to_numpy(),
            "lows": df["low"].to_numpy(),
            "adx": adx(df, adx_period).to_numpy() if adx_min > 0 else None,
            "index": df.index,
            "n": len(df),
            "min_bars": strat.min_bars(),
            "window_len": strat.min_bars() + 250,
        }

    events = sorted(
        (
            (state[s]["index"][i], s, i)
            for s in state
            for i in range(state[s]["min_bars"], state[s]["n"])
        ),
        key=lambda e: (e[0], e[1]),
    )

    cash = account_capital
    open_positions: dict[str, dict] = {}
    position_states: dict[str, PositionState] = {}
    last_price: dict[str, float] = {}
    trades: list[TradeResult] = []
    eq_times: list = []
    eq_values: list = []

    # Orders queued by a signal on bar i, to be filled at bar i+1's open.
    # Only used when fill_timing == "next_open"; dropped (never filled) if no
    # further bar for that symbol arrives before the data ends, exactly like
    # a real pending order that never gets its next tick.
    pending_entry: dict[str, dict] = {}   # symbol -> {"atr": float}
    pending_exit: dict[str, dict] = {}    # symbol -> {"reason": str}
    # Bar index of the most recent stop-out per symbol, for the re-entry cooldown.
    last_stop_index: dict[str, int] = {}

    def equity_now() -> float:
        eq = cash
        for sym, p in open_positions.items():
            eq += p["qty"] * last_price.get(sym, p["entry_fill"])
        return eq

    for ts, symbol, i in events:
        st = state[symbol]
        bar_open = float(st["opens"][i])
        bar_close = float(st["closes"][i])
        last_price[symbol] = bar_close
        in_test = ts >= test_start
        pos = open_positions.get(symbol)

        # Portfolio safety guards: update high-water / daily counters, then decide
        # once whether NEW entries are allowed this event. Open positions and
        # their stops are unaffected — exactly like the live engine.
        if guards is not None:
            guards.observe_equity(equity_now(), ts.date())
        guard_block = guards is not None and not guards.check(equity_now())[0]
        # Re-entry cooldown: block a new entry within N bars of this symbol's
        # last stop-out (skips the immediate whipsaw re-entry).
        cooldown_block = reentry_cooldown_bars > 0 and (
            i - last_stop_index.get(symbol, -(10**9)) < reentry_cooldown_bars
        )
        # ADX trend-strength gate: skip entries in chop (low/NaN ADX).
        adx_block = False
        if adx_min > 0 and st["adx"] is not None:
            av = st["adx"][i]
            adx_block = (av != av) or av < adx_min  # av != av -> NaN

        # 0) Execute any order queued by the *previous* bar's signal for this
        #    symbol, filled at THIS bar's open. This is what makes
        #    "next_open" realistic: by the time we can act on bar i-1's
        #    close, the earliest tradable price is bar i's open.
        if fill_timing == "next_open":
            if symbol in pending_exit:
                reason = pending_exit.pop(symbol)["reason"]
                if pos is not None:
                    exit_fill = apply_slippage(bar_open, is_buy=False, slippage=slippage)
                    cash += pos["qty"] * exit_fill
                    pnl = pos["qty"] * (exit_fill - pos["entry_fill"])
                    trades.append(_make_trade(symbol, pos, exit_fill, ts, reason, pnl))
                    if guards is not None:
                        guards.record_realized(pnl)
                    del open_positions[symbol]
                    position_states.pop(symbol, None)
                    pos = None
                # else: position already closed (e.g. stopped out before the
                # exit signal could be filled) — stale order, just drop it.

            if symbol in pending_entry:
                pend = pending_entry.pop(symbol)
                if pos is None:
                    blocked = guard_block or cooldown_block or adx_block or (
                        not ignore_correlation
                        and not risk.correlation_filter_ok(symbol, position_states)
                    ) or not risk.regime_filter_ok(
                        symbol, regime.at(ts) if regime is not None else True
                    )
                    if not blocked and cash > 0:
                        entry_fill = apply_slippage(bar_open, is_buy=True, slippage=slippage)
                        qty = risk.position_size(
                            equity_now(), pend["atr"], entry_fill, buying_power=cash, symbol=symbol
                        )
                        cost = qty * entry_fill
                        if qty > 0 and cost <= cash + 1e-4:
                            cash -= cost
                            stop = risk.stop_price(entry_fill, "long", pend["atr"])
                            pos = {
                                "side": "long",
                                "qty": qty,
                                "entry_fill": entry_fill,
                                "stop": stop,
                                "stop_init": stop,
                                "atr": pend["atr"],
                                "extreme": entry_fill,
                                "entry_time": ts,
                                "entry_index": i,
                            }
                            open_positions[symbol] = pos
                            position_states[symbol] = _to_state(symbol, pos)
                # else: somehow already in a position — stale order, drop it.

        # 1) Hard-stop check (long-only; never on the entry bar). Skipped when
        #    use_stop is False — then positions exit only on the strategy signal.
        #    Unaffected by fill_timing: a resting stop order can be touched
        #    and filled intrabar on the day price actually hits it, in both modes.
        if use_stop and pos is not None and i > pos["entry_index"] and float(st["lows"][i]) <= pos["stop"]:
            exit_fill = apply_slippage(pos["stop"], is_buy=False, slippage=slippage)
            cash += pos["qty"] * exit_fill
            pnl = pos["qty"] * (exit_fill - pos["entry_fill"])
            trades.append(_make_trade(symbol, pos, exit_fill, ts, "stop", pnl))
            if guards is not None:
                guards.record_realized(pnl)
            last_stop_index[symbol] = i  # arm the re-entry cooldown
            del open_positions[symbol]
            position_states.pop(symbol, None)
            pos = None

        # 1b) Scale-out: once (and only once) a trade trades +scale_out_atr ATR in
        #     its favour, sell scale_out_frac of it at that target — a resting limit
        #     touched intrabar — booking a realized partial WIN; the remainder rides
        #     on under the trailing stop. No-op when disabled or already scaled.
        if (
            scale_out_frac > 0
            and pos is not None
            and not pos.get("scaled")
            and i > pos["entry_index"]
        ):
            target = pos["entry_fill"] + scale_out_atr * pos["atr"]
            if float(st["highs"][i]) >= target:
                fill = apply_slippage(target, is_buy=False, slippage=slippage)
                sell_qty = round(pos["qty"] * scale_out_frac, 6)
                if sell_qty > 0:
                    cash += sell_qty * fill
                    pnl = sell_qty * (fill - pos["entry_fill"])
                    partial = {**pos, "qty": sell_qty}
                    trades.append(_make_trade(symbol, partial, fill, ts, "scale_out", pnl))
                    if guards is not None:
                        guards.record_realized(pnl)
                    pos["qty"] -= sell_qty
                    pos["scaled"] = True
                    open_positions[symbol] = pos
                    position_states[symbol] = _to_state(symbol, pos)

        # 2) Strategy evaluation on a trailing window (matches live evaluate()).
        lo = max(0, i - st["window_len"] + 1)
        signal = st["strat"].evaluate(st["df"].iloc[lo : i + 1], _to_state(symbol, pos))

        if pos is not None:
            if signal.action == Action.CLOSE:
                if fill_timing == "same_close":
                    exit_fill = apply_slippage(bar_close, is_buy=False, slippage=slippage)
                    cash += pos["qty"] * exit_fill
                    pnl = pos["qty"] * (exit_fill - pos["entry_fill"])
                    trades.append(_make_trade(symbol, pos, exit_fill, ts, signal.reason or "exit", pnl))
                    if guards is not None:
                        guards.record_realized(pnl)
                    del open_positions[symbol]
                    position_states.pop(symbol, None)
                else:
                    pending_exit[symbol] = {"reason": signal.reason or "exit"}
        elif in_test and signal.action == Action.BUY and signal.atr > 0 and cash > 0:
            if fill_timing == "same_close":
                blocked = guard_block or cooldown_block or adx_block or (
                    not ignore_correlation
                    and not risk.correlation_filter_ok(symbol, position_states)
                ) or not risk.regime_filter_ok(
                    symbol, regime.at(ts) if regime is not None else True
                )
                if not blocked:
                    entry_fill = apply_slippage(bar_close, is_buy=True, slippage=slippage)
                    qty = risk.position_size(
                        equity_now(), signal.atr, entry_fill, buying_power=cash, symbol=symbol
                    )
                    cost = qty * entry_fill
                    if qty > 0 and cost <= cash + 1e-4:
                        cash -= cost
                        stop = risk.stop_price(entry_fill, "long", signal.atr)
                        pos = {
                            "side": "long",
                            "qty": qty,
                            "entry_fill": entry_fill,
                            "stop": stop,
                            "stop_init": stop,
                            "atr": signal.atr,
                            "extreme": entry_fill,
                            "entry_time": ts,
                            "entry_index": i,
                        }
                        open_positions[symbol] = pos
                        position_states[symbol] = _to_state(symbol, pos)
            else:
                pending_entry[symbol] = {"atr": signal.atr}

        # 2b) Trail the protective stop in the trade's favour. This bar's high is
        #     folded into the high-water mark only AFTER the stop check in (1)
        #     above, so the stop can never react to a high it hasn't seen yet
        #     (no intrabar lookahead). No-op when trailing is disabled on the
        #     RiskManager; only meaningful when there is a stop to trail.
        pos = open_positions.get(symbol)
        if use_stop and pos is not None:
            pos["extreme"] = max(pos["extreme"], float(st["highs"][i]))
            pos["stop"] = risk.trail_stop(
                "long", pos["entry_fill"], pos["atr"], pos["extreme"], pos["stop"]
            )

        # 3) Mark-to-market portfolio equity (only inside the test window).
        if in_test:
            eq_times.append(ts)
            eq_values.append(equity_now())

    # Force-close everything still open at the end of data. Any still-pending
    # orders simply never get filled (no further bar exists) — dropped, like
    # a real order outliving the data.
    for symbol, pos in list(open_positions.items()):
        st = state[symbol]
        last_close = float(st["closes"][st["n"] - 1])
        last_ts = st["index"][st["n"] - 1]
        exit_fill = apply_slippage(last_close, is_buy=False, slippage=slippage)
        cash += pos["qty"] * exit_fill
        pnl = pos["qty"] * (exit_fill - pos["entry_fill"])
        trades.append(_make_trade(symbol, pos, exit_fill, last_ts, "end_of_test", pnl))
        del open_positions[symbol]
    if eq_values:
        eq_values[-1] = cash  # everything liquidated -> equity == cash

    equity = pd.Series(eq_values, index=pd.DatetimeIndex(eq_times), dtype="float64")
    if not equity.empty:
        equity = equity[~equity.index.duplicated(keep="last")].sort_index()

    per_asset: dict[str, dict] = {}
    for symbol in assets:
        ats = [t for t in trades if t.symbol == symbol]
        wins = sum(1 for t in ats if t.pnl > 0)
        per_asset[symbol] = {
            "strategy": assets[symbol][0].name,
            "num_trades": len(ats),
            "win_rate": (wins / len(ats)) if ats else 0.0,
            "profit_factor": _profit_factor(ats),
            "pnl": sum(t.pnl for t in ats),
        }

    return SharedResult(equity, trades, per_asset, account_capital, fill_timing=fill_timing)


# ----------------------------------------------------------------- metrics
def sharpe_from_daily(daily: pd.Series, periods_per_year: int = TRADING_DAYS) -> float:
    rets = daily.pct_change().dropna()
    if len(rets) < 2:
        return 0.0
    sd = rets.std(ddof=1)
    if sd == 0 or math.isnan(sd):
        return 0.0
    return float(rets.mean() / sd * math.sqrt(periods_per_year))


def sharpe_ratio(equity: pd.Series) -> float:
    """Annualised Sharpe from a mark-to-market curve (resampled to daily)."""
    if equity.empty:
        return 0.0
    daily = equity.resample("1D").last().dropna()
    return sharpe_from_daily(daily)


def max_drawdown(equity: pd.Series) -> float:
    """Largest peak-to-trough decline, as a negative fraction (e.g. -0.12)."""
    if equity.empty:
        return 0.0
    running_max = equity.cummax()
    drawdown = equity / running_max - 1.0
    return float(drawdown.min())


def _profit_factor(trades: list[TradeResult]) -> float:
    gross_profit = sum(t.pnl for t in trades if t.pnl > 0)
    gross_loss = -sum(t.pnl for t in trades if t.pnl < 0)
    if gross_loss == 0:
        return math.inf if gross_profit > 0 else 0.0
    return gross_profit / gross_loss


def compute_metrics(trades: list[TradeResult], equity: pd.Series, capital: float) -> dict:
    num = len(trades)
    wins = sum(1 for t in trades if t.pnl > 0)
    total_return = (
        (float(equity.iloc[-1]) - capital) / capital if not equity.empty else 0.0
    )
    return {
        "num_trades": num,
        "win_rate": (wins / num) if num else 0.0,
        "profit_factor": _profit_factor(trades),
        "max_drawdown": max_drawdown(equity),
        "sharpe": sharpe_ratio(equity),
        "total_return": total_return,
    }


def build_portfolio_equity(results: list[AssetResult], capital: float) -> pd.Series:
    """Sum per-asset daily mark-to-market curves into one portfolio curve.

    Each asset funds an equal, independent sub-account of ``capital``; the
    portfolio starts at ``capital * n_assets``.
    """
    daily = []
    for r in results:
        if r.equity.empty:
            continue
        daily.append(r.equity.resample("1D").last().rename(r.symbol))
    if not daily:
        return pd.Series(dtype="float64")
    frame = pd.concat(daily, axis=1, sort=False).sort_index()
    # Internal gaps -> forward fill; leading gaps -> the sub-account's starting capital.
    frame = frame.ffill().fillna(capital)
    return frame.sum(axis=1)


# ------------------------------------------------------------ walk-forward
def _slice(series: pd.Series, lo, hi, inclusive_end: bool) -> pd.Series:
    idx = series.index
    mask = (idx >= lo) & ((idx <= hi) if inclusive_end else (idx < hi))
    return series[mask]


def walk_forward(results: list[AssetResult], portfolio_daily: pd.Series, folds: int) -> list[dict]:
    if portfolio_daily.empty or folds < 1:
        return []
    start, end = portfolio_daily.index.min(), portfolio_daily.index.max()
    span = end - start
    asset_daily = {
        r.symbol: r.equity.resample("1D").last().dropna()
        for r in results
        if not r.equity.empty
    }
    out = []
    for k in range(folds):
        lo = start + span * (k / folds)
        hi = start + span * ((k + 1) / folds)
        inclusive = k == folds - 1
        port_slice = _slice(portfolio_daily, lo, hi, inclusive)
        out.append(
            {
                "idx": k + 1,
                "start": lo,
                "end": hi,
                "port_sharpe": sharpe_from_daily(port_slice),
                "port_return": _series_return(port_slice),
                "assets": {
                    sym: sharpe_from_daily(_slice(d, lo, hi, inclusive))
                    for sym, d in asset_daily.items()
                },
            }
        )
    return out


def _series_return(series: pd.Series) -> float:
    if len(series) < 2 or series.iloc[0] == 0:
        return 0.0
    return float(series.iloc[-1] / series.iloc[0] - 1.0)


def portfolio_metrics(
    results: list[AssetResult], portfolio_daily: pd.Series, capital: float
) -> dict:
    all_trades = [t for r in results for t in r.trades]
    num = len(all_trades)
    wins = sum(1 for t in all_trades if t.pnl > 0)
    base = capital * len(results)
    total_return = (
        (float(portfolio_daily.iloc[-1]) - base) / base if not portfolio_daily.empty else 0.0
    )
    return {
        "num_trades": num,
        "win_rate": (wins / num) if num else 0.0,
        "profit_factor": _profit_factor(all_trades),
        "max_drawdown": max_drawdown(portfolio_daily),
        "sharpe": sharpe_from_daily(portfolio_daily),
        "total_return": total_return,
    }


# ------------------------------------------------------------------ report
def _pf(x: float) -> str:
    return "inf" if math.isinf(x) else f"{x:.2f}"


def format_report(
    results: list[AssetResult],
    portfolio_daily: pd.Series,
    capital: float,
    slippage: float,
    folds: int,
    years: float,
    correlation_filter_applied: bool = True,
) -> str:
    port = portfolio_metrics(results, portfolio_daily, capital)
    lines: list[str] = []
    sep = "=" * 86

    lines.append(sep)
    lines.append("  BACKTEST REPORT".ljust(86))
    lines.append(
        f"  period: {years:g}y   capital/asset: ${capital:,.0f}   "
        f"slippage: {slippage * 100:.3f}%/fill   folds: {folds}"
    )
    lines.append(sep)

    # --- Per-asset / per-strategy table ---
    header = f"  {'ASSET':<6}{'STRATEGY':<17}{'TRADES':>7}{'WIN%':>8}{'PF':>8}{'MAXDD':>9}{'SHARPE':>9}{'RET%':>9}"
    lines.append(header)
    lines.append("  " + "-" * 82)
    for r in results:
        m = r.metrics
        lines.append(
            f"  {r.symbol:<6}{r.strategy_name:<17}{m['num_trades']:>7}"
            f"{m['win_rate'] * 100:>7.1f}%{_pf(m['profit_factor']):>8}"
            f"{m['max_drawdown'] * 100:>8.1f}%{m['sharpe']:>9.2f}{m['total_return'] * 100:>8.1f}%"
        )
    lines.append("  " + "-" * 82)
    lines.append(
        f"  {'PORT':<6}{'(combined)':<17}{port['num_trades']:>7}"
        f"{port['win_rate'] * 100:>7.1f}%{_pf(port['profit_factor']):>8}"
        f"{port['max_drawdown'] * 100:>8.1f}%{port['sharpe']:>9.2f}{port['total_return'] * 100:>8.1f}%"
    )
    lines.append(sep)

    # --- Walk-forward folds ---
    folds_data = walk_forward(results, portfolio_daily, folds)
    if folds_data:
        lines.append("  WALK-FORWARD (out-of-sample Sharpe per fold)")
        syms = [r.symbol for r in results]
        head = f"  {'FOLD':<5}{'PERIOD':<26}" + "".join(f"{s:>7}" for s in syms) + f"{'PORT':>8}{'RET%':>9}"
        lines.append(head)
        lines.append("  " + "-" * (31 + 7 * len(syms) + 17))
        for fd in folds_data:
            period = f"{fd['start'].date()}..{fd['end'].date()}"
            row = f"  {fd['idx']:<5}{period:<26}"
            row += "".join(f"{fd['assets'].get(s, 0.0):>7.2f}" for s in syms)
            row += f"{fd['port_sharpe']:>8.2f}{fd['port_return'] * 100:>8.1f}%"
            lines.append(row)
        lines.append(sep)

    lines.append("  Metrics: Sharpe & MaxDD on daily mark-to-market equity (rf=0, ann.=252).")
    if correlation_filter_applied:
        lines.append(
            "  Note: assets simulated jointly in chronological order; correlation filter"
            " (XLK/QQQ/SPY/XLF, GLD/SLV) IS applied to new long entries."
        )
    else:
        lines.append(
            "  Note: assets simulated jointly in chronological order; correlation filter"
            " DISABLED for this run (--ignore-correlation)."
        )
    lines.append(sep)
    return "\n".join(lines)


def format_shared_report(
    res: SharedResult,
    slippage: float,
    years: float,
    max_position_fraction: float,
    ignore_correlation: bool,
    folds: int = DEFAULT_FOLDS,
    use_stop: bool = True,
) -> str:
    eq = res.equity
    cap = res.account_capital
    port = compute_metrics(res.trades, eq, cap)
    final_eq = float(eq.iloc[-1]) if not eq.empty else cap
    cagr = ((final_eq / cap) ** (1.0 / years) - 1.0) if final_eq > 0 and years > 0 else 0.0
    sep = "=" * 86
    lines = [
        sep,
        "  SHARED-ACCOUNT BACKTEST (one cash pool — realistic for a single account)".ljust(86),
        f"  period: {years:g}y   account: ${cap:,.0f}   slippage: {slippage * 100:.3f}%/fill"
        f"   max/position: {max_position_fraction * 100:.0f}% of equity"
        f"   fill: {res.fill_timing}",
        sep,
        f"  {'ASSET':<6}{'STRATEGY':<16}{'TRADES':>7}{'WIN%':>8}{'PF':>8}{'PnL $':>12}{'PnL %acct':>12}",
        "  " + "-" * 82,
    ]
    for sym, m in res.per_asset.items():
        lines.append(
            f"  {sym:<6}{m['strategy']:<16}{m['num_trades']:>7}{m['win_rate'] * 100:>7.1f}%"
            f"{_pf(m['profit_factor']):>8}{m['pnl']:>12,.0f}{m['pnl'] / cap * 100:>11.1f}%"
        )
    lines.append("  " + "-" * 82)
    lines.append(
        f"  PORTFOLIO  trades={port['num_trades']}  win={port['win_rate'] * 100:.1f}%"
        f"  PF={_pf(port['profit_factor'])}  Sharpe={port['sharpe']:.2f}"
        f"  MaxDD={port['max_drawdown'] * 100:.1f}%"
    )
    lines.append(
        f"  Account ${cap:,.0f} -> ${final_eq:,.0f}   total {port['total_return'] * 100:+.1f}%"
        f"   CAGR {cagr * 100:+.1f}%/yr"
    )
    lines.append(sep)

    daily = eq.resample("1D").last().dropna() if not eq.empty else eq
    if not daily.empty and folds >= 1:
        start, end = daily.index.min(), daily.index.max()
        span = end - start
        lines.append("  WALK-FORWARD (portfolio Sharpe / return per fold)")
        lines.append(f"  {'FOLD':<5}{'PERIOD':<28}{'SHARPE':>9}{'RET%':>9}")
        for k in range(folds):
            lo = start + span * (k / folds)
            hi = start + span * ((k + 1) / folds)
            sl = _slice(daily, lo, hi, k == folds - 1)
            lines.append(
                f"  {k + 1:<5}{f'{lo.date()}..{hi.date()}':<28}"
                f"{sharpe_from_daily(sl):>9.2f}{_series_return(sl) * 100:>8.1f}%"
            )
        lines.append(sep)

    lines.append(
        "  Single shared cash pool; positions compete for buying power; each capped at"
        f" {max_position_fraction * 100:.0f}% of equity."
    )
    lines.append(
        "  Protective stop: "
        + ("ON (1-ATR — broker OTO if whole-share, else in-loop software stop)." if use_stop
           else "OFF — exits via strategy signal only (no stop).")
    )
    from risk.risk_manager import CORRELATED_GROUPS
    groups_desc = ", ".join(
        f"{name}<={g['max_concurrent_long']}" for name, g in CORRELATED_GROUPS.items()
    ) or "no groups"
    lines.append(
        "  Correlation filter "
        + ("OFF" if ignore_correlation else f"ON ({groups_desc}).")
    )
    lines.append(
        "  Fill timing: "
        + (
            "next_open (realistic — signal on bar i filled at bar i+1's open)."
            if res.fill_timing == "next_open"
            else "same_close (legacy/optimistic — signal on bar i filled at bar i's own close)."
        )
    )
    lines.append(sep)
    return "\n".join(lines)


# --------------------------------------------------------------------- run
def enforce_sharpe_gate(results: list[AssetResult]) -> None:
    """Strict validation gate: raise if any strategy has a negative Sharpe ratio."""
    negatives = [r for r in results if r.metrics["sharpe"] < 0]
    if negatives:
        detail = ", ".join(f"{r.symbol} ({r.metrics['sharpe']:+.2f})" for r in negatives)
        raise NegativeSharpeError(
            f"Negative Sharpe ratio over the backtest period: {detail}. "
            "Failing the validation and halting."
        )


def fetch_assets(
    strategies: dict[str, Strategy],
    test_start: datetime,
    now: datetime,
    feed: Optional[str] = None,
) -> dict[str, tuple[Strategy, pd.DataFrame]]:
    """Fetch historical bars for every strategy's asset.

    Pulled out of ``run_backtest`` so an experiment harness can fetch the
    data ONCE and reuse it across many backtest configurations (different
    correlation groups, asset subsets, etc.) without hitting Alpaca repeatedly.
    """
    from data import AlpacaBroker  # lazy: keeps the SDK out of import-time deps

    broker = AlpacaBroker()
    assets: dict[str, tuple[Strategy, pd.DataFrame]] = {}
    for symbol, strat in strategies.items():
        fetch_start = test_start - timedelta(days=warmup_days(strat))
        logger.info(
            "Fetching %s (%s) from %s ...", symbol, strat.timeframe, fetch_start.date()
        )
        try:
            df = broker.get_bars_range(symbol, strat.timeframe, fetch_start, now, feed)
        except Exception as exc:
            logger.warning("[%s] data fetch failed: %s", symbol, exc)
            continue
        if df is None or df.empty or len(df) < strat.min_bars():
            logger.warning(
                "[%s] insufficient data (%s bars) — skipping.",
                symbol, 0 if df is None else len(df),
            )
            continue
        logger.info("[%s] %d bars fetched, queued for joint simulation ...", symbol, len(df))
        assets[symbol] = (strat, df)
    return assets


def fetch_regime(
    symbol: str,
    sma_period: int,
    test_start: datetime,
    now: datetime,
    feed: Optional[str] = None,
) -> Regime:
    """Fetch the broad-gauge daily bars and build a point-in-time :class:`Regime`.

    Pulls enough history *before* ``test_start`` that the SMA is already warm
    when scoring begins (~``sma_period`` trading days ≈ ``sma_period*1.7`` calendar
    days, plus a buffer). Returns an empty (always risk-on) Regime if the fetch
    fails, so a data hiccup degrades to "no gating" rather than blocking trading.
    """
    from data import AlpacaBroker  # lazy: keep the SDK out of import-time deps

    fetch_start = test_start - timedelta(days=int(sma_period * 1.7) + 60)
    logger.info("Fetching regime gauge %s (1Day) from %s ...", symbol, fetch_start.date())
    try:
        df = AlpacaBroker().get_bars_range(symbol, "1Day", fetch_start, now, feed)
    except Exception as exc:
        logger.warning("Regime gauge %s fetch failed (%s) — gating disabled.", symbol, exc)
        return Regime.from_bars(None, sma_period)
    return Regime.from_bars(df, sma_period)


def run_backtest(
    years: float = 1.0,
    capital: float = DEFAULT_CAPITAL,
    slippage: float = DEFAULT_SLIPPAGE,
    folds: int = DEFAULT_FOLDS,
    feed: Optional[str] = None,
    ignore_correlation: bool = False,
    risk: Optional[RiskManager] = None,
    assets: Optional[dict[str, tuple[Strategy, pd.DataFrame]]] = None,
    print_report: bool = True,
    enforce_gate: bool = True,
) -> tuple[list[AssetResult], pd.Series]:
    """Run one full backtest.

    ``risk`` and ``assets`` can be injected (e.g. by an experiment harness
    that already fetched the data and wants to try several RiskManager
    configs against it) — when omitted, both are built/fetched exactly as
    before so plain CLI usage (``python backtester.py``) is unchanged.
    """
    risk = risk or RiskManager(
        risk_per_trade=settings.risk_per_trade,
        use_trailing_stop=settings.use_trailing_stop,
        trail_breakeven_atr=settings.trail_breakeven_atr,
        trail_distance_atr=settings.trail_distance_atr,
        use_regime_filter=settings.use_regime_filter,
        stop_atr_mult=settings.stop_atr_mult,
    )
    strategies = build_strategies()

    now = datetime.now(timezone.utc)
    test_start = now - timedelta(days=int(365 * years))

    if assets is None:
        assets = fetch_assets(strategies, test_start, now, feed)

    if not assets:
        raise RuntimeError("No usable data fetched for any asset — check credentials/feed.")

    regime = (
        fetch_regime(settings.regime_symbol, settings.regime_sma, test_start, now, feed)
        if risk.use_regime_filter
        else None
    )

    logger.info(
        "Simulating %d assets jointly (%s) ...",
        len(assets),
        "correlation filter ON" if not ignore_correlation else "correlation filter OFF",
    )
    results = simulate_portfolio(
        assets, test_start, capital, slippage, risk,
        ignore_correlation=ignore_correlation, regime=regime,
    )

    portfolio_daily = build_portfolio_equity(results, capital)
    if print_report:
        print(
            format_report(
                results, portfolio_daily, capital, slippage, folds, years,
                correlation_filter_applied=not ignore_correlation,
            )
        )

    # --- Strict gate: any negative full-period Sharpe halts execution. ---
    if enforce_gate:
        enforce_sharpe_gate(results)
        logger.info("All strategies passed the non-negative Sharpe gate.")
    return results, portfolio_daily


def run_shared_backtest(
    years: float = 3.0,
    account: float = 2000.0,
    slippage: float = DEFAULT_SLIPPAGE,
    folds: int = DEFAULT_FOLDS,
    feed: Optional[str] = None,
    ignore_correlation: bool = False,
    risk: Optional[RiskManager] = None,
    assets: Optional[dict[str, tuple[Strategy, pd.DataFrame]]] = None,
    print_report: bool = True,
    use_stop: Optional[bool] = None,
    fill_timing: str = DEFAULT_FILL_TIMING,
    symbols: Optional[list[str]] = None,
    trailing: Optional[bool] = None,
    regime_filter: Optional[bool] = None,
    guards_enabled: Optional[bool] = None,
) -> SharedResult:
    """Run a realistic single shared-account backtest (one cash pool of ``account``).

    Sizes positions against total equity with ``max_position_fraction`` so the
    account can hold several positions at once — the honest model for a small
    live account, unlike the per-asset sub-account model of ``run_backtest``.
    ``use_stop`` defaults to True: the live engine always enforces the 1-ATR stop
    — as a broker OTO order when ``use_protective_stop`` is on (whole shares), or
    as an in-loop software stop otherwise (fractional). Pass ``use_stop=False``
    only to A/B the no-stop scenario.

    ``fill_timing`` defaults to ``"next_open"`` (realistic, no same-bar
    lookahead). Pass ``"same_close"`` to reproduce the legacy zero-latency
    behaviour for an A/B comparison — see ``simulate_shared_account`` for the
    full explanation of what each mode assumes.

    ``symbols`` restricts the run to a subset of the configured universe (e.g.
    ``["GLD", "VGK", "EWJ"]`` to test the trend-following sleeve alone vs the
    full 6-asset book). Sizing still uses the SAME shared ``account`` cash pool
    and ``RiskManager`` as the full run, so this is a fair apples-to-apples
    comparison of "what if only these assets were trading this account" —
    not a re-normalized or rescaled result.
    """
    if risk is None:
        use_trailing = settings.use_trailing_stop if trailing is None else trailing
        use_regime = settings.use_regime_filter if regime_filter is None else regime_filter
        risk = RiskManager(
            risk_per_trade=settings.risk_per_trade,
            max_position_fraction=settings.max_position_fraction,
            use_trailing_stop=use_trailing,
            trail_breakeven_atr=settings.trail_breakeven_atr,
            trail_distance_atr=settings.trail_distance_atr,
            use_regime_filter=use_regime,
            stop_atr_mult=settings.stop_atr_mult,
        )
    if use_stop is None:
        use_stop = True
    now = datetime.now(timezone.utc)
    test_start = now - timedelta(days=int(365 * years))

    if assets is None:
        strategies = build_strategies()
        if symbols is not None:
            missing = [s for s in symbols if s not in strategies]
            if missing:
                raise ValueError(f"Unknown symbol(s) not in the configured universe: {missing}")
            strategies = {s: strategies[s] for s in symbols}
        assets = fetch_assets(strategies, test_start, now, feed)
    elif symbols is not None:
        missing = [s for s in symbols if s not in assets]
        if missing:
            raise ValueError(f"Unknown symbol(s) not in the provided assets: {missing}")
        assets = {s: assets[s] for s in symbols}
    if not assets:
        raise RuntimeError("No usable data fetched for any asset — check credentials/feed.")

    regime = (
        fetch_regime(settings.regime_symbol, settings.regime_sma, test_start, now, feed)
        if risk.use_regime_filter
        else None
    )

    default_guards_on = settings.drawdown_halt_pct > 0 or settings.max_daily_loss_pct > 0
    use_guards = default_guards_on if guards_enabled is None else guards_enabled
    guards = (
        PortfolioGuards(
            drawdown_halt_pct=settings.drawdown_halt_pct,
            drawdown_resume_pct=settings.drawdown_resume_pct,
            max_daily_loss_pct=settings.max_daily_loss_pct,
        )
        if use_guards
        else None
    )

    logger.info(
        "Shared-account simulation: $%.0f across %d assets %s(correlation %s, stop %s, trailing %s, regime %s, guards %s, fill %s) ...",
        account, len(assets),
        f"({', '.join(sorted(assets))}) " if symbols is not None else "",
        "OFF" if ignore_correlation else "ON",
        "ON" if use_stop else "OFF",
        "ON" if (use_stop and risk.use_trailing_stop) else "OFF",
        f"ON({settings.regime_symbol}>SMA{settings.regime_sma})" if risk.use_regime_filter else "OFF",
        f"ON(dd-{settings.drawdown_halt_pct*100:.0f}%/day-{settings.max_daily_loss_pct*100:.0f}%)" if guards is not None else "OFF",
        fill_timing,
    )
    res = simulate_shared_account(
        assets, test_start, account, slippage, risk, ignore_correlation,
        use_stop=use_stop, fill_timing=fill_timing, regime=regime, guards=guards,
        reentry_cooldown_bars=settings.reentry_cooldown_bars,
        adx_min=settings.adx_min, adx_period=settings.adx_period,
        scale_out_frac=settings.scale_out_frac, scale_out_atr=settings.scale_out_atr,
    )
    if print_report:
        print(
            format_shared_report(
                res, slippage, years, risk.max_position_fraction or 1.0,
                ignore_correlation, folds, use_stop=use_stop,
            )
        )
    return res


TREND_FOLLOWING_SLEEVE = ["GLD", "VGK", "EWJ"]  # the sleeve that survived next_open best


def compare_sleeves(
    years: float = 5.0,
    account: float = 2000.0,
    slippage: float = DEFAULT_SLIPPAGE,
    folds: int = DEFAULT_FOLDS,
    feed: Optional[str] = None,
    ignore_correlation: bool = False,
    use_stop: bool = True,
    fill_timing: str = DEFAULT_FILL_TIMING,
    sleeve_symbols: Optional[list[str]] = None,
) -> tuple[SharedResult, SharedResult]:
    """Run the full universe and a sleeve subset (default: trend_following only,
    GLD/VGK/EWJ) back to back on the SAME shared account, fetching the data once.

    Answers "does dropping the momentum names (QQQ/XLF/EEM) help or hurt the
    realistic, next_open-filled result?" — fair because both runs share the
    exact same account size, RiskManager, slippage, and fill timing; only the
    traded universe differs.
    """
    sleeve_symbols = sleeve_symbols or TREND_FOLLOWING_SLEEVE
    risk = RiskManager(
        risk_per_trade=settings.risk_per_trade,
        max_position_fraction=settings.max_position_fraction,
        use_trailing_stop=settings.use_trailing_stop,
        trail_breakeven_atr=settings.trail_breakeven_atr,
        trail_distance_atr=settings.trail_distance_atr,
        stop_atr_mult=settings.stop_atr_mult,
    )
    strategies = build_strategies()
    now = datetime.now(timezone.utc)
    test_start = now - timedelta(days=int(365 * years))
    # Fetch the full universe ONCE; the sleeve run reuses the same cached bars.
    all_assets = fetch_assets(strategies, test_start, now, feed)
    if not all_assets:
        raise RuntimeError("No usable data fetched for any asset — check credentials/feed.")

    print("\n########## FULL UNIVERSE ##########")
    full_res = run_shared_backtest(
        years=years, account=account, slippage=slippage, folds=folds, feed=feed,
        ignore_correlation=ignore_correlation, risk=risk, assets=all_assets,
        print_report=True, use_stop=use_stop, fill_timing=fill_timing, symbols=None,
    )

    missing = [s for s in sleeve_symbols if s not in all_assets]
    if missing:
        logger.warning("Sleeve symbols missing from fetched data, skipping them: %s", missing)
    sleeve_symbols = [s for s in sleeve_symbols if s in all_assets]

    print(f"\n########## SLEEVE ONLY ({', '.join(sleeve_symbols)}) ##########")
    sleeve_res = run_shared_backtest(
        years=years, account=account, slippage=slippage, folds=folds, feed=feed,
        ignore_correlation=ignore_correlation, risk=risk, assets=all_assets,
        print_report=True, use_stop=use_stop, fill_timing=fill_timing, symbols=sleeve_symbols,
    )

    full_m = compute_metrics(full_res.trades, full_res.equity, account)
    sleeve_m = compute_metrics(sleeve_res.trades, sleeve_res.equity, account)
    print("\n########## DELTA: sleeve-only minus full universe ##########")
    print(
        f"  PF      {_pf(full_m['profit_factor']):>6} -> {_pf(sleeve_m['profit_factor']):>6}\n"
        f"  Sharpe  {full_m['sharpe']:>6.2f} -> {sleeve_m['sharpe']:>6.2f}\n"
        f"  MaxDD   {full_m['max_drawdown']*100:>5.1f}% -> {sleeve_m['max_drawdown']*100:>5.1f}%\n"
        f"  Return  {full_m['total_return']*100:>5.1f}% -> {sleeve_m['total_return']*100:>5.1f}%\n"
    )
    return full_res, sleeve_res


def main() -> None:
    parser = argparse.ArgumentParser(description="Backtest the trading strategies")
    parser.add_argument("--years", type=float, default=1.0, help="Length of the test window (default 1)")
    parser.add_argument("--capital", type=float, default=DEFAULT_CAPITAL, help="Starting capital per asset")
    parser.add_argument("--slippage", type=float, default=DEFAULT_SLIPPAGE, help="Slippage per fill (0.0005 = 0.05%%)")
    parser.add_argument("--folds", type=int, default=DEFAULT_FOLDS, help="Walk-forward folds")
    parser.add_argument("--feed", default=None, help="Override data feed (iex/sip)")
    parser.add_argument(
        "--account",
        type=float,
        default=None,
        help="Run the REALISTIC single shared-account model with this much total capital "
        "(e.g. --account 2000). Omit for the legacy per-asset sub-account model.",
    )
    parser.add_argument(
        "--ignore-correlation",
        action="store_true",
        help="Disable the correlation filter for this run (A/B comparison)",
    )
    parser.add_argument(
        "--no-stop",
        action="store_true",
        help="Shared-account mode only: A/B the scenario with NO 1-ATR stop.",
    )
    parser.add_argument(
        "--no-trailing",
        action="store_true",
        help="Shared-account mode only: disable the trailing stop for this run "
        "(A/B against the default trailing behaviour from config.py).",
    )
    parser.add_argument(
        "--regime",
        action="store_true",
        help="Enable the market-regime filter for this run (gate equity-sleeve "
        "longs by REGIME_SYMBOL>SMA). A/B against the default (off).",
    )
    parser.add_argument(
        "--no-regime",
        action="store_true",
        help="Force the market-regime filter OFF (overrides config.py if you set "
        "USE_REGIME_FILTER=true there).",
    )
    parser.add_argument(
        "--no-guards",
        action="store_true",
        help="Shared-account mode only: disable the portfolio safety guards "
        "(drawdown breaker + daily kill-switch) for this run, to measure their "
        "cost. At the default levels they should be a near no-op.",
    )
    parser.add_argument(
        "--fill-timing",
        choices=FILL_TIMINGS,
        default=DEFAULT_FILL_TIMING,
        help="Shared-account mode only: 'next_open' (default, realistic — fills at the "
        "next bar's open) vs 'same_close' (legacy/optimistic — fills at the same bar's "
        "close that produced the signal, a zero-latency assumption). A/B these to see how "
        "much edge depends on execution latency.",
    )
    parser.add_argument(
        "--symbols",
        default=None,
        help="Shared-account mode only: comma-separated subset of the universe to trade "
        "against the SAME shared account (e.g. --symbols GLD,VGK,EWJ to test the "
        "trend-following sleeve alone). Omit for the full configured universe.",
    )
    parser.add_argument(
        "--compare-sleeves",
        action="store_true",
        help="Shared-account mode only: run the full universe AND the trend_following-only "
        "subset (GLD,VGK,EWJ) back to back on the same $%(default)s-style account, printing "
        "both reports plus a one-line delta summary. Overrides --symbols.",
    )
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)-7s %(name)s | %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )
    if args.account is not None:
        if args.compare_sleeves:
            compare_sleeves(
                years=args.years,
                account=args.account,
                slippage=args.slippage,
                folds=args.folds,
                feed=args.feed,
                ignore_correlation=args.ignore_correlation,
                use_stop=not args.no_stop,
                fill_timing=args.fill_timing,
            )
            return
        symbols = [s.strip() for s in args.symbols.split(",")] if args.symbols else None
        run_shared_backtest(
            years=args.years,
            account=args.account,
            slippage=args.slippage,
            folds=args.folds,
            feed=args.feed,
            ignore_correlation=args.ignore_correlation,
            use_stop=not args.no_stop,
            fill_timing=args.fill_timing,
            symbols=symbols,
            trailing=(False if args.no_trailing else None),
            regime_filter=(True if args.regime else False if args.no_regime else None),
            guards_enabled=(False if args.no_guards else None),
        )
        return
    run_backtest(
        years=args.years,
        capital=args.capital,
        slippage=args.slippage,
        folds=args.folds,
        feed=args.feed,
        ignore_correlation=args.ignore_correlation,
    )


if __name__ == "__main__":
    main()