"""Throwaway diagnostic: fetch the universe once, run the current shared-account
config, and break down WHERE win rate / P&L come from (per asset, per exit
reason, per holding bucket). Run: python diagnose.py [years]
"""
from __future__ import annotations

import sys
from collections import defaultdict
from datetime import datetime, timedelta, timezone

from backtester import fetch_assets, fetch_regime, simulate_shared_account
from config import settings
from risk import PortfolioGuards, RiskManager
from strategies import build_strategies


def main() -> None:
    years = float(sys.argv[1]) if len(sys.argv) > 1 else 3.0
    now = datetime.now(timezone.utc)
    test_start = now - timedelta(days=int(365 * years))
    assets = fetch_assets(build_strategies(), test_start, now, None)
    risk = RiskManager(
        risk_per_trade=settings.risk_per_trade,
        max_position_fraction=settings.max_position_fraction,
        use_trailing_stop=settings.use_trailing_stop,
        trail_breakeven_atr=settings.trail_breakeven_atr,
        trail_distance_atr=settings.trail_distance_atr,
    )
    guards = PortfolioGuards(
        settings.drawdown_halt_pct, settings.drawdown_resume_pct, settings.max_daily_loss_pct
    )
    res = simulate_shared_account(
        assets, test_start, 2000.0, 0.0010, risk,
        use_stop=True, fill_timing="next_open", guards=guards,
        reentry_cooldown_bars=settings.reentry_cooldown_bars,
        adx_min=settings.adx_min, adx_period=settings.adx_period,
    )
    trades = res.trades
    print(f"\n=== {len(trades)} trades over {years}y ===")

    # By exit reason
    by_reason: dict[str, list] = defaultdict(list)
    for t in trades:
        key = t.reason.split(":")[0]
        by_reason[key].append(t)
    print("\nEXIT REASON          n    win%     avgPnL    totPnL")
    for r, ts in sorted(by_reason.items(), key=lambda kv: -sum(t.pnl for t in kv[1])):
        wins = sum(1 for t in ts if t.pnl > 0)
        tot = sum(t.pnl for t in ts)
        print(f"  {r:18} {len(ts):4} {wins/len(ts)*100:5.1f}%  {tot/len(ts):8.2f}  {tot:8.2f}")

    # Holding-time buckets (winners run longer?)
    def hours(t):
        return (t.exit_time - t.entry_time).total_seconds() / 3600.0
    print("\nHOLDING (hours)      n    win%     totPnL")
    buckets = [("<6h", 0, 6), ("6-24h", 6, 24), ("1-3d", 24, 72), ("3-10d", 72, 240), (">10d", 240, 1e9)]
    for name, lo, hi in buckets:
        ts = [t for t in trades if lo <= hours(t) < hi]
        if not ts:
            continue
        wins = sum(1 for t in ts if t.pnl > 0)
        print(f"  {name:18} {len(ts):4} {wins/len(ts)*100:5.1f}%  {sum(t.pnl for t in ts):8.2f}")

    # Winners vs losers magnitude (payoff)
    wins = [t.pnl for t in trades if t.pnl > 0]
    losses = [t.pnl for t in trades if t.pnl <= 0]
    if wins and losses:
        avg_w = sum(wins) / len(wins)
        avg_l = sum(losses) / len(losses)
        print(
            f"\nPAYOFF  avg win {avg_w:.2f} | avg loss {avg_l:.2f} | "
            f"ratio {abs(avg_w/avg_l):.2f} | win% {len(wins)/len(trades)*100:.1f}"
        )


if __name__ == "__main__":
    main()
