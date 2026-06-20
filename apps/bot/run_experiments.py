"""Run several backtest configurations against the SAME fetched data and
compare them side by side, instead of re-running `python backtester.py`
manually for each variant and diffing the output.

Usage
-----
    python run_experiments.py --years 3

Add/remove/edit the `EXPERIMENTS` list below to try other ideas (different
group limits, different groupings, dropping an asset, different
risk_per_trade, etc.) — each entry is just a name + a function that builds
a RiskManager and (optionally) filters the asset universe.
"""
from __future__ import annotations

import argparse
import logging
from datetime import datetime, timezone, timedelta
from typing import Callable

import pandas as pd

from backtester import (
    DEFAULT_CAPITAL,
    DEFAULT_SLIPPAGE,
    build_portfolio_equity,
    fetch_assets,
    portfolio_metrics,
    run_backtest,
)
from config import settings
from risk import RiskManager
from strategies import Strategy, build_strategies

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Each experiment is (name, builder). ``builder(all_assets)`` returns
# (risk_manager, assets_to_use, ignore_correlation) for that run.
# ``all_assets`` is the full fetched {symbol: (strat, df)} dict — builders
# can drop symbols from it (e.g. to test "remove SPY") without re-fetching.
# ---------------------------------------------------------------------------
def _no_filter(all_assets):
    return RiskManager(settings.risk_per_trade), dict(all_assets), True


def _current_filter(all_assets):
    return RiskManager(settings.risk_per_trade), dict(all_assets), False


def _strict_filter(all_assets):
    groups = {
        "us_equity_beta": {"symbols": frozenset({"XLK", "QQQ", "SPY", "XLF"}), "max_concurrent_long": 1},
        "metals": {"symbols": frozenset({"GLD", "SLV"}), "max_concurrent_long": 1},
    }
    return RiskManager(settings.risk_per_trade, correlation_groups=groups), dict(all_assets), False


def _loose_filter(all_assets):
    groups = {
        "us_equity_beta": {"symbols": frozenset({"XLK", "QQQ", "SPY", "XLF"}), "max_concurrent_long": 3},
        "metals": {"symbols": frozenset({"GLD", "SLV"}), "max_concurrent_long": 1},
    }
    return RiskManager(settings.risk_per_trade, correlation_groups=groups), dict(all_assets), False


def _drop_spy(all_assets):
    assets = {s: v for s, v in all_assets.items() if s != "SPY"}
    return RiskManager(settings.risk_per_trade), assets, False


def _loose_no_spy(all_assets):
    # With SPY removed, us_equity_beta only has 3 members (XLK/QQQ/XLF), so
    # max_concurrent_long=2 is the meaningful "loose-ish" setting here --
    # =3 would let all three be long at once, effectively disabling the
    # group's filter.
    groups = {
        "us_equity_beta": {"symbols": frozenset({"XLK", "QQQ", "XLF"}), "max_concurrent_long": 2},
        "metals": {"symbols": frozenset({"GLD", "SLV"}), "max_concurrent_long": 1},
    }
    assets = {s: v for s, v in all_assets.items() if s != "SPY"}
    return RiskManager(settings.risk_per_trade, correlation_groups=groups), assets, False


def _no_filter_no_spy(all_assets):
    # Isolates the pure "drop SPY" effect: no correlation filter at all on
    # what's left, so this fills in the missing cell of the
    # {with SPY, without SPY} x {filter, no filter} comparison.
    assets = {s: v for s, v in all_assets.items() if s != "SPY"}
    return RiskManager(settings.risk_per_trade), assets, True  # ignore_correlation=True


def _spy_lower_risk(all_assets):
    # Keep SPY (and the current filter) but halve its risk_per_trade, so it
    # still contributes return when it does get a slot, without the entry
    # of a single SPY trade dominating the group's risk while it's
    # structurally the slowest signal in us_equity_beta.
    risk = RiskManager(settings.risk_per_trade, symbol_risk_overrides={"SPY": settings.risk_per_trade / 2})
    return risk, dict(all_assets), False


EXPERIMENTS: list[tuple[str, Callable]] = [
    ("baseline (no filter)", _no_filter),
    ("current filter (2 / 1)", _current_filter),
    ("strict filter (1 / 1)", _strict_filter),
    ("loose filter (3 / 1)", _loose_filter),
    ("current filter, SPY removed", _drop_spy),
    ("loose filter (2 / 1), SPY removed", _loose_no_spy),
    ("no filter, SPY removed", _no_filter_no_spy),
    ("current filter, SPY risk halved", _spy_lower_risk),
]


def main() -> None:
    parser = argparse.ArgumentParser(description="Compare several backtest configurations")
    parser.add_argument("--years", type=float, default=3.0)
    parser.add_argument("--capital", type=float, default=DEFAULT_CAPITAL)
    parser.add_argument("--slippage", type=float, default=DEFAULT_SLIPPAGE)
    parser.add_argument("--feed", default=None)
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)-7s %(name)s | %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )

    strategies = build_strategies()
    now = datetime.now(timezone.utc)
    test_start = now - timedelta(days=int(365 * args.years))

    logger.info("Fetching data once for all %d configured assets ...", len(strategies))
    all_assets = fetch_assets(strategies, test_start, now, args.feed)
    if not all_assets:
        raise RuntimeError("No usable data fetched — check credentials/feed.")

    rows = []
    for name, builder in EXPERIMENTS:
        risk, assets, ignore_correlation = builder(all_assets)
        if not assets:
            logger.warning("[%s] no assets left after filtering — skipping.", name)
            continue
        results, portfolio_daily = run_backtest(
            years=args.years,
            capital=args.capital,
            slippage=args.slippage,
            risk=risk,
            assets=assets,
            ignore_correlation=ignore_correlation,
            print_report=False,
            enforce_gate=False,
        )
        m = portfolio_metrics(results, portfolio_daily, args.capital)
        rows.append(
            {
                "experiment": name,
                "assets": len(assets),
                "trades": m["num_trades"],
                "win%": m["win_rate"] * 100,
                "pf": m["profit_factor"],
                "maxdd%": m["max_drawdown"] * 100,
                "sharpe": m["sharpe"],
                "ret%": m["total_return"] * 100,
            }
        )

    print()
    print("=" * 96)
    print("  EXPERIMENT COMPARISON (portfolio-level)".ljust(96))
    print("=" * 96)
    header = f"  {'EXPERIMENT':<30}{'ASSETS':>7}{'TRADES':>8}{'WIN%':>8}{'PF':>7}{'MAXDD':>9}{'SHARPE':>9}{'RET%':>9}"
    print(header)
    print("  " + "-" * 92)
    for r in rows:
        pf = "inf" if r["pf"] == float("inf") else f"{r['pf']:.2f}"
        print(
            f"  {r['experiment']:<30}{r['assets']:>7}{r['trades']:>8}"
            f"{r['win%']:>7.1f}%{pf:>7}{r['maxdd%']:>8.1f}%{r['sharpe']:>9.2f}{r['ret%']:>8.1f}%"
        )
    print("=" * 96)


if __name__ == "__main__":
    main()