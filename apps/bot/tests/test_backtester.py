"""Tests for backtester.py using deterministic synthetic data — no network/creds.

Requires pandas (a project dependency); skipped automatically if it is absent.
Run with:  python -m unittest tests.test_backtester
"""
import math
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

try:
    import pandas as pd

    import backtester as bt
    from risk import PortfolioGuards, RiskManager
    from strategies.base import Action, PositionState
    from strategies.macd_trend import MACDTrendStrategy
    from strategies.momentum import MomentumBreakoutStrategy
    from strategies.regime import Regime, risk_on_series
    from strategies.trend_following import TrendFollowingStrategy

    HAS_PANDAS = True
except ImportError:
    HAS_PANDAS = False


def _make_df(closes, volumes, spread=0.3):
    n = len(closes)
    idx = pd.date_range("2025-01-02 09:00", periods=n, freq="h", tz="UTC")
    return pd.DataFrame(
        {
            "open": closes,
            "high": [c + spread for c in closes],
            "low": [c - spread for c in closes],
            "close": closes,
            "volume": volumes,
        },
        index=idx,
    )


def _breakout_df():
    """Flat consolidation -> clean volume-backed breakout -> ride up -> pullback."""
    closes = []
    for i in range(200):
        if i < 160:
            c = 100.0 + 0.1 * math.sin(i)
        elif i < 172:
            c = 100.0 + (i - 159) * 2.0
        else:
            c = 124.0 - (i - 171) * 3.0
        closes.append(max(c, 80.0))
    volumes = [1000.0] * 200
    for i in range(160, 172):
        volumes[i] = 4000.0
    return _make_df(closes, volumes)


def _uptrend_df(n=60, start=100.0, step=0.5):
    """Strictly increasing closes -> trend strategies go (and stay) long."""
    return _make_df([start + step * i for i in range(n)], [1000.0] * n)


def _sim_one(symbol, strat, df, ts, capital, slippage, risk):
    """Single-asset run via the joint simulator (replaces the old simulate_asset)."""
    return bt.simulate_portfolio({symbol: (strat, df)}, ts, capital, slippage, risk)[0]


def _max_concurrent(trades):
    evs = sorted(
        [(t.entry_time, 1) for t in trades] + [(t.exit_time, -1) for t in trades],
        key=lambda e: (e[0], -e[1]),
    )
    cur = mx = 0
    for _, delta in evs:
        cur += delta
        mx = max(mx, cur)
    return mx


@unittest.skipUnless(HAS_PANDAS, "pandas not installed")
class TestHelpers(unittest.TestCase):
    def test_slippage_direction(self):
        self.assertEqual(bt.apply_slippage(100.0, True, 0.0005), 100.05)
        self.assertEqual(bt.apply_slippage(100.0, False, 0.0005), 99.95)

    def test_profit_factor(self):
        trades = [
            bt.TradeResult("X", "long", None, None, 10, 12, 1, 2.0, 0.2, "exit"),
            bt.TradeResult("X", "long", None, None, 10, 9, 1, -1.0, -0.1, "stop"),
            bt.TradeResult("X", "long", None, None, 10, 13, 1, 3.0, 0.3, "exit"),
        ]
        self.assertAlmostEqual(bt._profit_factor(trades), 5.0)
        self.assertTrue(math.isinf(bt._profit_factor(trades[:1])))
        self.assertEqual(bt._profit_factor([]), 0.0)

    def test_sharpe(self):
        growth = pd.Series(
            [100, 102, 101.5, 104, 103, 106, 108],
            index=pd.date_range("2025-01-01", periods=7, freq="D"),
            dtype=float,
        )
        self.assertGreater(bt.sharpe_from_daily(growth), 0)
        # Constant returns -> zero std -> defined as 0.
        const = pd.Series(
            [100, 101, 102.01],
            index=pd.date_range("2025-01-01", periods=3, freq="D"),
            dtype=float,
        )
        self.assertEqual(bt.sharpe_from_daily(const), 0.0)
        self.assertEqual(bt.sharpe_from_daily(growth.iloc[:1]), 0.0)

    def test_max_drawdown(self):
        s = pd.Series(
            [100, 120, 90, 110],
            index=pd.date_range("2025-01-01", periods=4, freq="D"),
            dtype=float,
        )
        self.assertAlmostEqual(bt.max_drawdown(s), -0.25)


@unittest.skipUnless(HAS_PANDAS, "pandas not installed")
class TestSimulation(unittest.TestCase):
    def setUp(self):
        self.risk = RiskManager(0.01)
        self.strat = MomentumBreakoutStrategy(
            "SMH", "1Hour", lookback=120, period=20, vol_mult=1.5, exit_period=10
        )

    def test_breakout_winner_and_equity_reconciles(self):
        df = _breakout_df()
        res = _sim_one(
            "SMH", self.strat, df, df.index[100].to_pydatetime(), 100_000.0, 0.0005, self.risk
        )
        self.assertGreaterEqual(res.metrics["num_trades"], 1)
        self.assertTrue(any(t.pnl > 0 for t in res.trades))
        self.assertAlmostEqual(res.equity.iloc[0], 100_000.0, delta=1.0)
        realized = sum(t.pnl for t in res.trades)
        self.assertAlmostEqual(res.equity.iloc[-1], 100_000.0 + realized, places=4)
        self.assertGreaterEqual(
            set(res.metrics),
            {"num_trades", "win_rate", "profit_factor", "max_drawdown", "sharpe", "total_return"},
        )

    def test_hard_stop_caps_loss(self):
        # Breakout entry then an immediate crash to 95: the 1-ATR stop must cap
        # the loss far below the unmitigated ~7/share (102 -> 95) move.
        closes = []
        for i in range(200):
            if i < 160:
                c = 100.0 + 0.05 * math.sin(i)
            elif i == 160:
                c = 102.0
            else:
                c = 95.0
            closes.append(c)
        volumes = [1000.0] * 200
        volumes[160] = 4000.0
        df = _make_df(closes, volumes)
        res = _sim_one(
            "SMH", self.strat, df, df.index[100].to_pydatetime(), 100_000.0, 0.0005, self.risk
        )
        stops = [t for t in res.trades if t.reason == "stop"]
        self.assertGreaterEqual(len(stops), 1)
        loss_per_share = stops[0].exit_price - stops[0].entry_price
        self.assertTrue(-3.0 < loss_per_share < 0.0, f"loss/share={loss_per_share}")


@unittest.skipUnless(HAS_PANDAS, "pandas not installed")
class TestPortfolioAndGate(unittest.TestCase):
    def setUp(self):
        self.risk = RiskManager(0.01)
        df = _breakout_df()
        ts = df.index[100].to_pydatetime()
        strat = MomentumBreakoutStrategy("SMH", "1Hour", 120, period=20, vol_mult=1.5, exit_period=10)
        self.a = _sim_one("SMH", strat, df, ts, 100_000.0, 0.0005, self.risk)
        self.b = _sim_one(
            "GLD", TrendFollowingStrategy("GLD", "1Hour", 400, fast=20, slow=50),
            df, ts, 100_000.0, 0.0005, self.risk,
        )

    def test_portfolio_and_walk_forward(self):
        results = [self.a, self.b]
        port = bt.build_portfolio_equity(results, 100_000.0)
        self.assertGreater(len(port), 0)
        self.assertAlmostEqual(port.iloc[0], 200_000.0, delta=5_000.0)
        folds = bt.walk_forward(results, port, 4)
        self.assertEqual(len(folds), 4)
        self.assertTrue(all("SMH" in f["assets"] for f in folds))
        report = bt.format_report(results, port, 100_000.0, 0.0005, 4, 1.0)
        self.assertIsInstance(report, str)
        self.assertIn("BACKTEST REPORT", report)

    def test_sharpe_gate(self):
        self.a.metrics["sharpe"] = -0.5
        with self.assertRaises(bt.NegativeSharpeError):
            bt.enforce_sharpe_gate([self.a, self.b])
        self.a.metrics["sharpe"] = 0.5
        self.b.metrics["sharpe"] = 0.5
        bt.enforce_sharpe_gate([self.a, self.b])  # should not raise


@unittest.skipUnless(HAS_PANDAS, "pandas not installed")
class TestSharedAccount(unittest.TestCase):
    """The single shared-cash-pool model used for a realistic small account."""

    def _assets(self):
        return {
            s: (
                TrendFollowingStrategy(s, "1Hour", 300, fast=3, slow=5, allow_short=False),
                _uptrend_df(),
            )
            for s in ("AAA", "BBB", "CCC")
        }

    def _run(self, max_position_fraction):
        risk = RiskManager(
            0.01, correlation_groups={}, max_position_fraction=max_position_fraction
        )
        ts = _uptrend_df().index[7].to_pydatetime()
        return bt.simulate_shared_account(self._assets(), ts, 2000.0, 0.0005, risk)

    def test_cash_is_conserved(self):
        res = self._run(0.20)
        realized = sum(t.pnl for t in res.trades)
        self.assertAlmostEqual(float(res.equity.iloc[-1]), 2000.0 + realized, places=4)

    def test_cap_enables_concurrent_positions(self):
        # 20% cap -> three €400 positions fit in the €2k account at once.
        self.assertGreaterEqual(_max_concurrent(self._run(0.20).trades), 2)

    def test_without_cap_one_trade_eats_the_account(self):
        # No cap -> the first long's notional swallows all buying power.
        self.assertEqual(_max_concurrent(self._run(None).trades), 1)

    def test_use_stop_toggle(self):
        # Rise (open long) then a sharp one-bar crash that blows through the
        # 1-ATR stop before the EMA strategy can react.
        closes = [100 + 0.5 * i for i in range(20)] + [95.0] * 20
        df = _make_df(closes, [1000.0] * 40)
        assets = {
            "AAA": (TrendFollowingStrategy("AAA", "1Hour", 300, fast=3, slow=5, allow_short=False), df)
        }
        ts = df.index[7].to_pydatetime()
        risk = RiskManager(0.01, correlation_groups={}, max_position_fraction=0.20)
        with_stop = bt.simulate_shared_account(assets, ts, 2000.0, 0.0005, risk, use_stop=True)
        no_stop = bt.simulate_shared_account(assets, ts, 2000.0, 0.0005, risk, use_stop=False)
        self.assertTrue(any(t.reason == "stop" for t in with_stop.trades))
        self.assertFalse(any(t.reason == "stop" for t in no_stop.trades))

    def test_scale_out_books_partial_win(self):
        # A long that runs well past +3 ATR should book a partial "scale_out" win
        # for half the size, then close the remainder later — two trades, one entry.
        closes = [100.0 + 1.0 * i for i in range(40)]
        df = _make_df(closes, [1000.0] * len(closes))
        assets = {
            "AAA": (TrendFollowingStrategy("AAA", "1Hour", 300, fast=3, slow=5, allow_short=False), df)
        }
        ts = df.index[7].to_pydatetime()
        risk = RiskManager(0.01, correlation_groups={}, max_position_fraction=0.20)
        res = bt.simulate_shared_account(
            assets, ts, 2000.0, 0.0005, risk, use_stop=True,
            scale_out_frac=0.5, scale_out_atr=3.0,
        )
        scale = [t for t in res.trades if t.reason == "scale_out"]
        self.assertTrue(scale, "expected a scale_out partial trade")
        self.assertGreater(scale[0].pnl, 0)  # booked in profit
        # The remainder of that entry also produced a separate trade.
        self.assertGreaterEqual(len(res.trades), 2)

    def test_trailing_stop_locks_in_profit_on_reversal(self):
        # Long, steady ~30-bar run-up (>2 ATR of profit) then a sharp reversal.
        # A trailing stop set 2 ATR behind the peak catches the reversal long
        # before the slow EMA flip can — so it exits HIGHER (locks profit).
        closes = [100.0 + 1.0 * i for i in range(30)] + [
            129.0 - 3.0 * i for i in range(1, 12)
        ]
        df = _make_df(closes, [1000.0] * len(closes))
        assets = {
            "AAA": (TrendFollowingStrategy("AAA", "1Hour", 300, fast=3, slow=5, allow_short=False), df)
        }
        ts = df.index[7].to_pydatetime()

        def run(use_trailing):
            risk = RiskManager(
                0.01, correlation_groups={}, max_position_fraction=0.20,
                use_trailing_stop=use_trailing,
            )
            return bt.simulate_shared_account(assets, ts, 2000.0, 0.0005, risk, use_stop=True)

        trailed = run(True)
        fixed = run(False)

        # Trailing exits via a stop that fired ABOVE entry (profit locked).
        trail_stops = [t for t in trailed.trades if t.reason == "stop"]
        self.assertTrue(trail_stops, "expected a trailing-stop exit")
        self.assertGreater(trail_stops[0].exit_price, trail_stops[0].entry_price)
        # And it beats the fixed-stop book, which gives back more before exiting.
        self.assertGreater(
            sum(t.pnl for t in trailed.trades), sum(t.pnl for t in fixed.trades)
        )


@unittest.skipUnless(HAS_PANDAS, "pandas not installed")
class TestAdx(unittest.TestCase):
    def test_strong_trend_high_chop_low(self):
        from indicators import adx
        n = 120
        up = pd.DataFrame({
            "high": [100 + i + 0.3 for i in range(n)],
            "low": [100 + i - 0.3 for i in range(n)],
            "close": [100 + i for i in range(n)],
        })
        chop = pd.DataFrame({
            "high": [100 + (i % 2) + 0.3 for i in range(n)],
            "low": [100 + (i % 2) - 0.3 for i in range(n)],
            "close": [100 + (i % 2) for i in range(n)],
        })
        self.assertGreater(float(adx(up).iloc[-1]), 40)
        self.assertLess(float(adx(chop).iloc[-1]), 20)


@unittest.skipUnless(HAS_PANDAS, "pandas not installed")
class TestMacdExitGating(unittest.TestCase):
    """With exit_requires_trend, a MACD cross-down while price is still above the
    trend SMA does NOT close the long (trailing stop manages it); the legacy
    behaviour closes on any cross."""

    def _scenario(self):
        # Strong rise (price well above SMA10), then a one-bar dip that crosses
        # MACD down while the close stays above the SMA.
        closes = [100 + 2 * i for i in range(14)] + [120.0]
        df = _make_df(closes, [1000.0] * len(closes))
        pos = PositionState(symbol="EWG", side="long", qty=1.0, entry_price=100.0)
        return df, pos

    def _strat(self, gated):
        return MACDTrendStrategy(
            "EWG", "1Hour", 300, fast_macd=3, slow_macd=5, signal_macd=2,
            trend_filter=10, atr_period=5, exit_requires_trend=gated,
        )

    def test_legacy_closes_on_cross(self):
        df, pos = self._scenario()
        sig = self._strat(gated=False).evaluate(df, pos)
        self.assertEqual(sig.action, Action.CLOSE)

    def test_gated_holds_while_trend_intact(self):
        df, pos = self._scenario()
        sig = self._strat(gated=True).evaluate(df, pos)
        # Price is still above SMA10, so the gated exit holds instead of closing.
        self.assertEqual(sig.action, Action.HOLD)


@unittest.skipUnless(HAS_PANDAS, "pandas not installed")
class TestGuardsInSim(unittest.TestCase):
    """Portfolio guards can only ever block entries — never add trades."""

    def test_guards_only_reduce_entries(self):
        closes = [100 + 0.5 * i for i in range(20)] + [95.0] * 20
        df = _make_df(closes, [1000.0] * 40)
        assets = {
            "AAA": (TrendFollowingStrategy("AAA", "1Hour", 300, fast=3, slow=5, allow_short=False), df)
        }
        ts = df.index[7].to_pydatetime()
        risk = RiskManager(0.01, correlation_groups={}, max_position_fraction=0.20)
        base = bt.simulate_shared_account(assets, ts, 2000.0, 0.0005, risk, use_stop=True)
        guarded = bt.simulate_shared_account(
            assets, ts, 2000.0, 0.0005, risk, use_stop=True,
            guards=PortfolioGuards(drawdown_halt_pct=0.005, drawdown_resume_pct=0.002,
                                   max_daily_loss_pct=0.005),
        )
        self.assertLessEqual(len(guarded.trades), len(base.trades))


@unittest.skipUnless(HAS_PANDAS, "pandas not installed")
class TestRegime(unittest.TestCase):
    def _daily(self, closes):
        idx = pd.date_range("2025-01-01", periods=len(closes), freq="D", tz="UTC")
        return pd.DataFrame({"close": closes, "high": closes, "low": closes,
                             "open": closes, "volume": [1.0] * len(closes)}, index=idx)

    def test_risk_on_series_warmup_is_nan(self):
        s = risk_on_series(pd.Series([10.0, 11, 12, 13]), sma_period=3)
        self.assertTrue(pd.isna(s.iloc[0]))  # SMA(3) undefined on bar 0
        self.assertIn(s.iloc[-1], (True, False))

    def test_at_is_lookahead_free(self):
        # Rising series: SMA turns risk-on partway. The flag for a given day must
        # only be visible the NEXT day (shift by one bar).
        closes = [100.0] * 5 + [100 + i for i in range(1, 20)]
        reg = Regime.from_bars(self._daily(closes), sma_period=5)
        idx = self._daily(closes).index
        # At the very first day, regime is unknown -> fail-open (risk-on/allow).
        self.assertTrue(reg.at(idx[0]))
        # Querying as-of an intraday ts on day d returns day d-1's flag, never a
        # value derived from day d's own (not-yet-known) close.
        intraday = idx[15] + pd.Timedelta(hours=14)
        self.assertEqual(reg.at(intraday), reg.at(idx[15]))

    def test_empty_is_risk_on(self):
        self.assertTrue(Regime.from_bars(None, 200).latest())
        self.assertTrue(Regime.from_bars(pd.DataFrame(), 200).at(pd.Timestamp("2025-01-01", tz="UTC")))


@unittest.skipUnless(HAS_PANDAS, "pandas not installed")
class TestRegimeGateInSim(unittest.TestCase):
    """The regime gate, wired into the shared-account simulator, blocks equity
    longs while risk-OFF and lets the same book trade freely when risk-ON."""

    def _assets(self):
        # QQQ is gated (equity sleeve); steady uptrend so it always wants to be long.
        return {"QQQ": (TrendFollowingStrategy("QQQ", "1Hour", 300, fast=3, slow=5,
                                               allow_short=False), _uptrend_df(n=80))}

    def _run(self, risk_on_flag):
        risk = RiskManager(0.01, correlation_groups={}, max_position_fraction=0.20,
                           use_regime_filter=True)
        df = _uptrend_df(n=80)
        ts = df.index[7].to_pydatetime()
        # Constant-regime gauge over the whole window.
        gauge = pd.Series([1.0 if risk_on_flag else 0.0] * len(df), index=df.index)
        # Bypass the SMA/shift machinery: feed the flag directly.
        regime = Regime(gauge.map(bool))
        return bt.simulate_shared_account(self._assets(), ts, 2000.0, 0.0005, risk,
                                          regime=regime)

    def test_risk_off_blocks_equity_entries(self):
        self.assertEqual(len(self._run(risk_on_flag=False).trades), 0)

    def test_risk_on_allows_equity_entries(self):
        self.assertGreaterEqual(len(self._run(risk_on_flag=True).trades), 1)


if __name__ == "__main__":
    unittest.main()
