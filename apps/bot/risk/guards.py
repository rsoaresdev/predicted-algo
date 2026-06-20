"""Portfolio-level safety guards — a drawdown circuit-breaker and a daily
max-loss kill-switch (STRATEGY_IMPROVEMENTS #1 / #2).

Both guards only ever **block new entries**; open positions keep being managed
by their (trailing) stops. Pure Python so it is trivially unit-testable, and
shared by the live engine *and* the backtester so a backtest reflects exactly
the same braking the live bot would apply.

Usage each cycle/bar:

    guards.observe_equity(equity, day)   # update high-water + roll the day
    ...on every trade close: guards.record_realized(pnl)
    allow, reason = guards.check(equity) # gate new entries
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from typing import Optional


@dataclass
class GuardState:
    peak_equity: float = 0.0
    halted: bool = False  # drawdown breaker latched (hysteresis)
    day: Optional[date] = None
    day_start_equity: float = 0.0
    day_realized: float = 0.0  # realized P&L accumulated today (< 0 = loss)


class PortfolioGuards:
    def __init__(
        self,
        drawdown_halt_pct: float = 0.0,
        drawdown_resume_pct: float = 0.0,
        max_daily_loss_pct: float = 0.0,
    ) -> None:
        self.dd_halt = max(0.0, drawdown_halt_pct)
        self.dd_resume = max(0.0, drawdown_resume_pct)
        self.max_daily_loss = max(0.0, max_daily_loss_pct)
        self.s = GuardState()

    @property
    def enabled(self) -> bool:
        return self.dd_halt > 0 or self.max_daily_loss > 0

    def observe_equity(self, equity: float, day: Optional[date] = None) -> None:
        """Update the high-water mark and, when the calendar day rolls, reset the
        daily counters (snapshotting the day's starting equity)."""
        if equity > self.s.peak_equity:
            self.s.peak_equity = equity
        if day is not None and day != self.s.day:
            self.s.day = day
            self.s.day_start_equity = equity
            self.s.day_realized = 0.0

    def record_realized(self, pnl: float) -> None:
        """Accumulate a closed trade's realized P&L into today's tally."""
        self.s.day_realized += pnl

    def drawdown(self, equity: float) -> float:
        """Current drawdown from the high-water mark (<= 0)."""
        if self.s.peak_equity <= 0:
            return 0.0
        return (equity - self.s.peak_equity) / self.s.peak_equity

    def daily_loss_frac(self) -> float:
        """Today's realized loss as a positive fraction of the day's start equity."""
        if self.s.day_start_equity <= 0 or self.s.day_realized >= 0:
            return 0.0
        return -self.s.day_realized / self.s.day_start_equity

    def check(self, equity: float) -> tuple[bool, str]:
        """Whether new entries are allowed right now; ``(False, reason)`` if not.

        Has a side effect: latches/unlatches the drawdown breaker with
        hysteresis (trips at ``dd_halt``, only re-arms once recovered above
        ``dd_resume``). Call :meth:`observe_equity` first so the high-water mark
        and day counters are current.
        """
        if self.dd_halt > 0:
            dd = self.drawdown(equity)
            if self.s.halted:
                if dd >= -self.dd_resume:
                    self.s.halted = False
                else:
                    return (
                        False,
                        f"drawdown breaker: {dd * 100:.1f}% (halted until > -{self.dd_resume * 100:.0f}%)",
                    )
            elif dd <= -self.dd_halt:
                self.s.halted = True
                return (
                    False,
                    f"drawdown breaker tripped: {dd * 100:.1f}% <= -{self.dd_halt * 100:.0f}%",
                )

        if self.max_daily_loss > 0:
            loss = self.daily_loss_frac()
            if loss >= self.max_daily_loss:
                return (
                    False,
                    f"daily loss kill-switch: -{loss * 100:.1f}% >= -{self.max_daily_loss * 100:.0f}%",
                )

        return True, ""

    def status(self, equity: float) -> str:
        """One-line state for the heartbeat log."""
        return (
            f"dd={self.drawdown(equity) * 100:.1f}% peak=${self.s.peak_equity:,.0f} "
            f"dayPnL=${self.s.day_realized:,.0f} halted={self.s.halted}"
        )
