"""Thin persistence helpers so the engine never touches the ORM directly."""
from __future__ import annotations

import logging
from datetime import date, datetime, time, timezone

from sqlalchemy import select

from database.db import SessionLocal
from database.models import DailyMetric, Trade

logger = logging.getLogger(__name__)


def create_open_trade(
    *,
    asset: str,
    direction: str,
    strategy: str,
    qty: float,
    entry_price: float,
    stop_price: float,
) -> str:
    """Insert a new open trade; returns its uuid as a string."""
    with SessionLocal() as session:
        trade = Trade(
            asset=asset,
            direction=direction,
            strategy=strategy,
            qty=qty,
            entry_price=entry_price,
            stop_price=stop_price,
            status="open",
        )
        session.add(trade)
        session.commit()
        session.refresh(trade)
        return str(trade.id)


def close_trade(*, asset: str, exit_price: float, exit_reason: str) -> float | None:
    """Close the most recent open trade for ``asset`` and compute realised P&L."""
    with SessionLocal() as session:
        trade = session.scalars(
            select(Trade)
            .where(Trade.asset == asset, Trade.status == "open")
            .order_by(Trade.opened_at.desc())
        ).first()
        if trade is None:
            logger.warning("close_trade: no open trade found for %s", asset)
            return None

        sign = 1.0 if trade.direction == "long" else -1.0
        trade.exit_price = exit_price
        trade.pnl = (exit_price - trade.entry_price) * trade.qty * sign
        trade.status = "closed"
        trade.exit_reason = exit_reason
        trade.closed_at = datetime.now(timezone.utc)
        session.commit()
        return trade.pnl


def update_trade_stop(*, asset: str, stop_price: float) -> None:
    """Persist a moved (trailing) stop on the most recent open trade for ``asset``.

    Keeps the DB's ``stop_price`` in sync with the engine's in-memory trailing
    stop so a restart re-bootstraps the *current* protective level, not the
    original entry stop.
    """
    with SessionLocal() as session:
        trade = session.scalars(
            select(Trade)
            .where(Trade.asset == asset, Trade.status == "open")
            .order_by(Trade.opened_at.desc())
        ).first()
        if trade is None:
            return
        trade.stop_price = stop_price
        session.commit()


def load_open_trades() -> list[dict]:
    """Return open trades as plain dicts (used to rebuild in-memory state)."""
    with SessionLocal() as session:
        rows = session.scalars(select(Trade).where(Trade.status == "open")).all()
        return [
            {
                "asset": r.asset,
                "direction": r.direction,
                "qty": r.qty,
                "entry_price": r.entry_price,
                "stop_price": r.stop_price,
            }
            for r in rows
        ]


def record_daily_metrics(equity: float) -> None:
    """Upsert today's metrics row from closed trades and current equity."""
    today = date.today()
    day_start = datetime.combine(today, time.min, tzinfo=timezone.utc)

    with SessionLocal() as session:
        metric = session.scalar(select(DailyMetric).where(DailyMetric.date == today))
        if metric is None:
            metric = DailyMetric(date=today, starting_equity=equity)
            session.add(metric)

        closed_today = session.scalars(
            select(Trade).where(
                Trade.status == "closed", Trade.closed_at >= day_start
            )
        ).all()

        wins = sum(1 for t in closed_today if (t.pnl or 0) > 0)
        losses = sum(1 for t in closed_today if (t.pnl or 0) < 0)
        num = len(closed_today)

        metric.ending_equity = equity
        metric.realized_pnl = sum((t.pnl or 0.0) for t in closed_today)
        metric.num_trades = num
        metric.wins = wins
        metric.losses = losses
        metric.win_rate = (wins / num) if num else None
        if metric.starting_equity:
            metric.return_pct = (
                (equity - metric.starting_equity) / metric.starting_equity * 100.0
            )
        session.commit()
