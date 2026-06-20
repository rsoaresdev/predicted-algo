"""SQLAlchemy ORM models for the trading bot.

The ``trades`` primary key uses PostgreSQL's native ``uuidv7()`` (PostgreSQL 18+).
For older servers, database/db.py installs a tested plpgsql ``uuidv7()`` fallback
before creating the schema, so this default works regardless of server version.
"""
from __future__ import annotations

from sqlalchemy import (
    Date,
    DateTime,
    Float,
    Index,
    Integer,
    String,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    pass


class Trade(Base):
    """One row per round-trip trade.

    Inserted as ``status='open'`` on entry, then updated to ``status='closed'``
    with exit_price / pnl / closed_at when the position is closed.
    """

    __tablename__ = "trades"

    id: Mapped[str] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        server_default=text("uuidv7()"),
    )

    asset: Mapped[str] = mapped_column(String(16), nullable=False)
    direction: Mapped[str] = mapped_column(String(8), nullable=False)  # 'long' | 'short'
    strategy: Mapped[str] = mapped_column(String(32), nullable=True)

    # Position size in shares (the requirement's "position size").
    qty: Mapped[float] = mapped_column(Float, nullable=False)

    entry_price: Mapped[float] = mapped_column(Float, nullable=False)
    exit_price: Mapped[float] = mapped_column(Float, nullable=True)
    stop_price: Mapped[float] = mapped_column(Float, nullable=True)

    pnl: Mapped[float] = mapped_column(Float, nullable=True)

    status: Mapped[str] = mapped_column(String(8), nullable=False, default="open")
    exit_reason: Mapped[str] = mapped_column(String(32), nullable=True)

    # opened_at is the trade timestamp; closed_at is set on exit.
    opened_at: Mapped["DateTime"] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    closed_at: Mapped["DateTime"] = mapped_column(DateTime(timezone=True), nullable=True)

    __table_args__ = (
        Index("ix_trades_asset", "asset"),
        Index("ix_trades_status", "status"),
        Index("ix_trades_opened_at", "opened_at"),
    )

    def __repr__(self) -> str:  # pragma: no cover - debug helper
        return (
            f"<Trade {self.asset} {self.direction} qty={self.qty} "
            f"status={self.status} pnl={self.pnl}>"
        )


class DailyMetric(Base):
    """Aggregated performance metrics, one row per trading day."""

    __tablename__ = "daily_metrics"

    id: Mapped[str] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        server_default=text("uuidv7()"),
    )

    date: Mapped["Date"] = mapped_column(Date, nullable=False, unique=True)

    starting_equity: Mapped[float] = mapped_column(Float, nullable=True)
    ending_equity: Mapped[float] = mapped_column(Float, nullable=True)
    realized_pnl: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)

    num_trades: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    wins: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    losses: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    win_rate: Mapped[float] = mapped_column(Float, nullable=True)
    return_pct: Mapped[float] = mapped_column(Float, nullable=True)

    created_at: Mapped["DateTime"] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    def __repr__(self) -> str:  # pragma: no cover - debug helper
        return f"<DailyMetric {self.date} pnl={self.realized_pnl} trades={self.num_trades}>"
