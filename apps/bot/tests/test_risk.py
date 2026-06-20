"""Unit tests for the risk manager — pure Python, no third-party deps required.

Run with:  python -m unittest tests.test_risk
"""
import os
import sys
import unittest
from types import SimpleNamespace

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from datetime import date  # noqa: E402

from risk.guards import PortfolioGuards  # noqa: E402
from risk.risk_manager import RiskManager  # noqa: E402


class TestPositionSizing(unittest.TestCase):
    def setUp(self):
        self.rm = RiskManager(risk_per_trade=0.01)

    def test_one_atr_equals_one_percent_of_equity(self):
        equity, atr, price = 100_000.0, 2.0, 400.0
        qty = self.rm.position_size(equity, atr, price)
        # risk_dollars = 1% * 100k = $1000; qty = 1000 / 2 = 500.
        self.assertAlmostEqual(qty, 500.0)
        # A 1-ATR move on 500 shares == $1000 == exactly 1% of equity.
        self.assertAlmostEqual(self.rm.max_loss(qty, atr), 0.01 * equity, places=6)

    def test_fractional_by_default(self):
        # 1000 / 3 = 333.33...  (fractional sizing is the live default).
        qty = self.rm.position_size(100_000.0, 3.0, 400.0)
        self.assertAlmostEqual(qty, 1000.0 / 3.0, places=4)

    def test_whole_share_mode_floors(self):
        qty = self.rm.position_size(100_000.0, 3.0, 400.0, fractional=False)
        self.assertEqual(qty, 333)

    def test_buying_power_cap(self):
        # Risk target would be 1000/2 = 500 ($200k notional), but only $40k
        # buying power is available -> 40000/400 = 100.
        qty = self.rm.position_size(100_000.0, 2.0, 400.0, buying_power=40_000.0)
        self.assertAlmostEqual(qty, 100.0)

    def test_max_position_fraction_cap(self):
        capped = RiskManager(0.01, max_position_fraction=0.20)
        # Risk target = (2000*0.01)/2 = 10 shares ($4000 = 200% of equity);
        # the 20% cap = (2000*0.20)/400 = 1.0 share ($400 = 20% of equity) binds.
        self.assertAlmostEqual(capped.position_size(2000.0, 2.0, 400.0), 1.0)
        # Without a cap the full risk-target size is returned.
        self.assertAlmostEqual(self.rm.position_size(2000.0, 2.0, 400.0), 10.0)

    def test_min_notional_returns_zero(self):
        # A sized order below Alpaca's $1 minimum notional is rejected.
        self.assertEqual(self.rm.position_size(10.0, 50.0, 400.0), 0.0)

    def test_bad_inputs_return_zero(self):
        self.assertEqual(self.rm.position_size(0, 2.0, 400.0), 0)
        self.assertEqual(self.rm.position_size(100_000.0, 0, 400.0), 0)
        self.assertEqual(self.rm.position_size(100_000.0, 2.0, 0), 0)


class TestStops(unittest.TestCase):
    def setUp(self):
        self.rm = RiskManager(risk_per_trade=0.01)

    def test_long_stop_is_one_atr_below_entry(self):
        self.assertEqual(self.rm.stop_price(400.0, "long", 2.0), 398.0)

    def test_short_stop_is_one_atr_above_entry(self):
        self.assertEqual(self.rm.stop_price(400.0, "short", 2.0), 402.0)

    def test_stop_rounded_to_penny(self):
        self.assertEqual(self.rm.stop_price(400.0, "long", 1.237), 398.76)

    def test_wider_stop_keeps_dollar_risk_constant(self):
        # stop_atr_mult=2 -> stop 2 ATR below entry, HALF the size, SAME $ risk.
        wide = RiskManager(risk_per_trade=0.01, stop_atr_mult=2.0)
        equity, atr, price = 100_000.0, 2.0, 400.0
        base_qty = self.rm.position_size(equity, atr, price)      # mult 1.0
        wide_qty = wide.position_size(equity, atr, price)         # mult 2.0
        self.assertAlmostEqual(wide_qty, base_qty / 2.0, places=4)
        self.assertEqual(wide.stop_price(400.0, "long", 2.0), 396.0)  # 2 ATR below
        # Same dollar risk at the stop in both cases (== 1% of equity).
        self.assertAlmostEqual(wide.max_loss(wide_qty, atr * 2.0), 0.01 * equity, places=4)
        self.assertAlmostEqual(self.rm.max_loss(base_qty, atr), 0.01 * equity, places=4)


class TestTrailingStop(unittest.TestCase):
    """Trailing stop: breakeven at +1 ATR, then trail 2 ATR behind the peak."""

    def setUp(self):
        # entry 100, ATR 2 -> initial 1-ATR stop at 98.
        self.rm = RiskManager(
            use_trailing_stop=True, trail_breakeven_atr=1.0, trail_distance_atr=2.0
        )
        self.entry, self.atr, self.init_stop = 100.0, 2.0, 98.0

    def trail(self, extreme, stop):
        return self.rm.trail_stop("long", self.entry, self.atr, extreme, stop)

    def test_disabled_is_a_noop(self):
        off = RiskManager(use_trailing_stop=False)
        self.assertEqual(off.trail_stop("long", 100.0, 2.0, 120.0, 98.0), 98.0)

    def test_holds_initial_stop_before_one_atr_profit(self):
        # Price up only +0.5 ATR (101) -> no breakeven lock yet, stop unchanged.
        self.assertAlmostEqual(self.trail(101.0, self.init_stop), 98.0)

    def test_locks_breakeven_at_one_atr(self):
        # Peak at +1 ATR (102) -> stop pulled up to entry (100).
        self.assertAlmostEqual(self.trail(102.0, self.init_stop), 100.0)

    def test_holds_breakeven_between_one_and_two_atr(self):
        # Peak +1.5 ATR (103): trail = 103-4 = 99 < breakeven, so stays at 100.
        self.assertAlmostEqual(self.trail(103.0, 100.0), 100.0)

    def test_trails_two_atr_behind_peak_beyond_two_atr(self):
        # Peak +3 ATR (106): trail = 106 - 2*2 = 102.
        self.assertAlmostEqual(self.trail(106.0, 100.0), 102.0)

    def test_monotonic_never_loosens(self):
        # After locking 102, a pullback (lower extreme passed in error) must not
        # lower the stop below the current 102.
        self.assertAlmostEqual(self.trail(104.0, 102.0), 102.0)

    def test_short_mirror(self):
        rm = self.rm
        # entry 100, ATR 2, initial short stop 102. Peak DOWN to 98 (=-1 ATR)
        # -> breakeven lock to 100. Down to 94 (-3 ATR) -> trail 94+4 = 98.
        self.assertAlmostEqual(rm.trail_stop("short", 100.0, 2.0, 98.0, 102.0), 100.0)
        self.assertAlmostEqual(rm.trail_stop("short", 100.0, 2.0, 94.0, 100.0), 98.0)


class TestCorrelationFilter(unittest.TestCase):
    """Default group: equity {QQQ, EWG, EEM} max 2 concurrent long; GLD/SLV/UUP ungrouped."""

    def setUp(self):
        self.rm = RiskManager()

    def _pos(self, side="long"):
        return SimpleNamespace(side=side)

    def test_blocks_third_equity_long(self):
        states = {"QQQ": self._pos(), "EWG": self._pos()}
        self.assertFalse(self.rm.correlation_filter_ok("EEM", states))

    def test_allows_second_equity_long(self):
        self.assertTrue(self.rm.correlation_filter_ok("EWG", {"QQQ": self._pos()}))

    def test_ungrouped_symbol_always_allowed(self):
        full = {"QQQ": self._pos(), "EWG": self._pos(), "EEM": self._pos()}
        self.assertTrue(self.rm.correlation_filter_ok("GLD", full))
        self.assertTrue(self.rm.correlation_filter_ok("UUP", full))

    def test_shorts_do_not_count(self):
        states = {"QQQ": self._pos("short"), "EWG": self._pos("short")}
        self.assertTrue(self.rm.correlation_filter_ok("EEM", states))

    def test_custom_groups_param(self):
        rm = RiskManager(
            correlation_groups={"g": {"symbols": frozenset({"A", "B"}), "max_concurrent_long": 1}}
        )
        self.assertFalse(rm.correlation_filter_ok("A", {"B": self._pos()}))
        self.assertTrue(rm.correlation_filter_ok("A", {"B": self._pos("short")}))


class TestRegimeFilter(unittest.TestCase):
    """Equity sleeve {QQQ,EWG,EEM} gated by risk-on; GLD/SLV/UUP never gated."""

    def setUp(self):
        self.rm = RiskManager(use_regime_filter=True)

    def test_disabled_allows_everything(self):
        off = RiskManager(use_regime_filter=False)
        self.assertTrue(off.regime_filter_ok("QQQ", risk_on=False))

    def test_blocks_equity_long_when_risk_off(self):
        for s in ("QQQ", "EWG", "EEM"):
            self.assertFalse(self.rm.regime_filter_ok(s, risk_on=False), s)

    def test_allows_equity_long_when_risk_on(self):
        for s in ("QQQ", "EWG", "EEM"):
            self.assertTrue(self.rm.regime_filter_ok(s, risk_on=True), s)

    def test_never_gates_diversifiers(self):
        for s in ("GLD", "SLV", "UUP"):
            self.assertTrue(self.rm.regime_filter_ok(s, risk_on=False), s)

    def test_custom_gated_set(self):
        rm = RiskManager(use_regime_filter=True, regime_gated_symbols=frozenset({"GLD"}))
        self.assertFalse(rm.regime_filter_ok("GLD", risk_on=False))
        self.assertTrue(rm.regime_filter_ok("QQQ", risk_on=False))  # not in custom set


class TestDrawdownBreaker(unittest.TestCase):
    def setUp(self):
        # Halt at -10% from peak, re-arm above -6%.
        self.g = PortfolioGuards(drawdown_halt_pct=0.10, drawdown_resume_pct=0.06)

    def test_allows_within_drawdown(self):
        self.g.observe_equity(2000)
        self.assertTrue(self.g.check(2000)[0])
        self.g.observe_equity(1850)  # -7.5%, above halt
        self.assertTrue(self.g.check(1850)[0])

    def test_trips_at_threshold(self):
        self.g.observe_equity(2000)
        self.g.observe_equity(1790)  # -10.5%
        allowed, reason = self.g.check(1790)
        self.assertFalse(allowed)
        self.assertIn("drawdown breaker", reason)

    def test_hysteresis_stays_halted_until_resume(self):
        self.g.observe_equity(2000)
        self.g.observe_equity(1750)  # -12.5% -> trip
        self.assertFalse(self.g.check(1750)[0])
        self.g.observe_equity(1820)  # -9% : still below resume(-6%) -> stay halted
        self.assertFalse(self.g.check(1820)[0])
        self.g.observe_equity(1890)  # -5.5% : above resume -> re-armed
        self.assertTrue(self.g.check(1890)[0])

    def test_disabled_never_trips(self):
        g = PortfolioGuards()  # all zero
        self.assertFalse(g.enabled)
        g.observe_equity(2000)
        g.observe_equity(1000)  # -50%
        self.assertTrue(g.check(1000)[0])


class TestDailyKillSwitch(unittest.TestCase):
    def setUp(self):
        self.g = PortfolioGuards(max_daily_loss_pct=0.05)

    def test_blocks_after_daily_loss_exceeded(self):
        self.g.observe_equity(2000, date(2026, 6, 1))
        self.g.record_realized(-60)  # -3%
        self.assertTrue(self.g.check(1940)[0])
        self.g.record_realized(-50)  # cumulative -110 = -5.5% >= 5%
        self.assertFalse(self.g.check(1890)[0])

    def test_resets_on_new_day(self):
        self.g.observe_equity(2000, date(2026, 6, 1))
        self.g.record_realized(-150)  # -7.5% -> blocked
        self.assertFalse(self.g.check(1850)[0])
        self.g.observe_equity(1850, date(2026, 6, 2))  # new day resets the tally
        self.assertTrue(self.g.check(1850)[0])

    def test_profits_do_not_block(self):
        self.g.observe_equity(2000, date(2026, 6, 1))
        self.g.record_realized(+200)
        self.assertTrue(self.g.check(2200)[0])


if __name__ == "__main__":
    unittest.main()