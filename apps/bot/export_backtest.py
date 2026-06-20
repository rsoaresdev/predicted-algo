"""Export the REAL shared-account backtest (current default config) to JSON so the
dashboard seed shows genuine strategy performance instead of synthetic data.

    python export_backtest.py [years]   # default 1
    -> writes ../dashboard/scripts/backtest_seed.json

Positions still open at the end of the test window are emitted as status="open"
(their entry is real; the dashboard marks them live against the current price).
"""
from __future__ import annotations

import json
import sys
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path

from backtester import DEFAULT_SLIPPAGE, fetch_assets, simulate_shared_account
from config import settings
from risk import PortfolioGuards, RiskManager
from strategies import build_strategies

ACCOUNT = 2000.0
OUT = (
    Path(__file__).resolve().parent.parent
    / "dashboard"
    / "scripts"
    / "backtest_seed.json"
)


def main() -> None:
    years = float(sys.argv[1]) if len(sys.argv) > 1 else 1.0
    now = datetime.now(timezone.utc)
    test_start = now - timedelta(days=int(365 * years))

    strategies = build_strategies()
    assets = fetch_assets(strategies, test_start, now, None)
    if not assets:
        raise SystemExit("No data fetched — check Alpaca credentials/feed.")

    risk = RiskManager(
        risk_per_trade=settings.risk_per_trade,
        max_position_fraction=settings.max_position_fraction,
        use_trailing_stop=settings.use_trailing_stop,
        trail_breakeven_atr=settings.trail_breakeven_atr,
        trail_distance_atr=settings.trail_distance_atr,
        stop_atr_mult=settings.stop_atr_mult,
    )
    guards = PortfolioGuards(
        settings.drawdown_halt_pct, settings.drawdown_resume_pct, settings.max_daily_loss_pct
    )
    res = simulate_shared_account(
        assets, test_start, ACCOUNT, DEFAULT_SLIPPAGE, risk,
        use_stop=True, fill_timing="next_open", guards=guards,
        reentry_cooldown_bars=settings.reentry_cooldown_bars,
        adx_min=settings.adx_min, adx_period=settings.adx_period,
    )

    strat_name = {s: strategies[s].name for s in strategies}

    def exit_reason(t) -> str:
        # Normalise the backtester's verbose signal text to the SHORT reasons the
        # live engine actually persists (and which fit exit_reason varchar(32)).
        if t.reason == "stop":
            return "stop_out"
        return f"strategy_exit:{strat_name.get(t.symbol, '')}"

    trades = []
    for t in res.trades:
        is_open = t.reason == "end_of_test"  # still open when the test ended
        trades.append({
            "asset": t.symbol,
            "direction": t.side,
            "strategy": strat_name.get(t.symbol),
            "qty": round(float(t.qty), 6),
            "entry_price": round(float(t.entry_price), 2),
            "exit_price": None if is_open else round(float(t.exit_price), 2),
            "stop_price": round(float(t.stop), 2) if t.stop else None,
            "pnl": None if is_open else round(float(t.pnl), 2),
            "status": "open" if is_open else "closed",
            "exit_reason": None if is_open else exit_reason(t),
            "opened_at": t.entry_time.isoformat(),
            "closed_at": None if is_open else t.exit_time.isoformat(),
        })

    # Daily metrics from the real mark-to-market equity curve + realized closes.
    daily_eq = res.equity.resample("1D").last().dropna()
    closed_by_day: dict = defaultdict(list)
    for t in res.trades:
        if t.reason != "end_of_test":
            closed_by_day[t.exit_time.date()].append(float(t.pnl))

    metrics = []
    prev = ACCOUNT
    for ts, end_eq in daily_eq.items():
        d = ts.date()
        pnls = closed_by_day.get(d, [])
        wins = sum(1 for p in pnls if p > 0)
        losses = sum(1 for p in pnls if p < 0)
        n = len(pnls)
        end_eq = float(end_eq)
        metrics.append({
            "date": d.isoformat(),
            "starting_equity": round(prev, 2),
            "ending_equity": round(end_eq, 2),
            "realized_pnl": round(sum(pnls), 2),
            "num_trades": n,
            "wins": wins,
            "losses": losses,
            "win_rate": round(wins / n, 4) if n else None,
            "return_pct": round((end_eq - prev) / prev * 100, 4) if prev else None,
        })
        prev = end_eq

    OUT.write_text(json.dumps({
        "generated_at": now.isoformat(),
        "years": years,
        "account": ACCOUNT,
        "trades": trades,
        "daily_metrics": metrics,
    }))
    n_open = sum(1 for t in trades if t["status"] == "open")
    final_eq = metrics[-1]["ending_equity"] if metrics else ACCOUNT
    print(
        f"Wrote {len(trades)} trades ({n_open} open) + {len(metrics)} daily_metrics "
        f"to {OUT}\n  account ${ACCOUNT:.0f} -> ${final_eq:.0f} over {years}y"
    )


if __name__ == "__main__":
    main()
