-- ============================================================================
--  trading-bot schema — mirrors apps/bot/database/models.py (SQLAlchemy).
--
--  The BOT owns and creates this schema in production (database/db.py ->
--  Base.metadata.create_all). This file exists so the dashboard's local DEV
--  database (and CI) can be created without running the bot, and as the
--  authoritative reference for the read-only data layer.
--
--  Run as the database owner:  psql -d <db> -f 02_schema.sql
-- ============================================================================

-- ── uuidv7() ────────────────────────────────────────────────────────────────
-- PostgreSQL 18+ ships uuidv7() natively. On older servers, install the same
-- community fallback the bot uses (database/db.py). Guarded so we never shadow
-- the native function on PG 18+.
DO $$
BEGIN
    IF current_setting('server_version_num')::int < 180000 THEN
        CREATE EXTENSION IF NOT EXISTS pgcrypto;
        EXECUTE $fn$
            CREATE OR REPLACE FUNCTION uuidv7() RETURNS uuid AS $body$
            BEGIN
                RETURN encode(
                    set_bit(
                        set_bit(
                            overlay(
                                uuid_send(gen_random_uuid())
                                PLACING substring(
                                    int8send(floor(extract(epoch FROM clock_timestamp()) * 1000)::bigint)
                                    FROM 3
                                )
                                FROM 1 FOR 6
                            ),
                            52, 1
                        ),
                        53, 1
                    ),
                    'hex'
                )::uuid;
            END;
            $body$ LANGUAGE plpgsql VOLATILE;
        $fn$;
    END IF;
END
$$;

-- ── trades ──────────────────────────────────────────────────────────────────
-- One row per round-trip. Inserted status='open' on entry; updated to
-- status='closed' with exit_price / pnl / closed_at on exit.
CREATE TABLE IF NOT EXISTS trades (
    id            uuid PRIMARY KEY DEFAULT uuidv7(),
    asset         varchar(16) NOT NULL,
    direction     varchar(8)  NOT NULL,            -- 'long' | 'short'
    strategy      varchar(32),                     -- nullable
    qty           double precision NOT NULL,       -- position size (supports fractional)
    entry_price   double precision NOT NULL,
    exit_price    double precision,                -- NULL while open
    stop_price    double precision,
    pnl           double precision,                -- pre-computed by the bot on close
    status        varchar(8)  NOT NULL DEFAULT 'open',  -- 'open' | 'closed'
    exit_reason   varchar(32),                     -- 'stop_out' | 'software_stop:*' | 'strategy_exit:*'
    opened_at     timestamptz NOT NULL DEFAULT now(),
    closed_at     timestamptz                      -- NULL while open
);

CREATE INDEX IF NOT EXISTS ix_trades_asset     ON trades (asset);
CREATE INDEX IF NOT EXISTS ix_trades_status    ON trades (status);
CREATE INDEX IF NOT EXISTS ix_trades_opened_at ON trades (opened_at);

-- ── daily_metrics ───────────────────────────────────────────────────────────
-- One row per trading day (upsert keyed on date).
CREATE TABLE IF NOT EXISTS daily_metrics (
    id               uuid PRIMARY KEY DEFAULT uuidv7(),
    date             date NOT NULL UNIQUE,
    starting_equity  double precision,
    ending_equity    double precision,
    realized_pnl     double precision NOT NULL DEFAULT 0.0,
    num_trades       integer NOT NULL DEFAULT 0,
    wins             integer NOT NULL DEFAULT 0,
    losses           integer NOT NULL DEFAULT 0,
    win_rate         double precision,
    return_pct       double precision,
    created_at       timestamptz NOT NULL DEFAULT now()
);
