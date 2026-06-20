"""Database engine, session factory, and schema initialisation.

``uuidv7()`` is native to PostgreSQL 18+. On older servers we install a small,
well-tested plpgsql implementation so the ``server_default=uuidv7()`` columns
in models.py work everywhere.
"""
from __future__ import annotations

import logging

from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker

from config import settings
from database.models import Base

logger = logging.getLogger(__name__)

engine = create_engine(settings.database_url, pool_pre_ping=True, future=True)
SessionLocal = sessionmaker(bind=engine, expire_on_commit=False, future=True)


# A community-standard UUIDv7 generator for PostgreSQL < 18.
# It reuses gen_random_uuid() (built in since PG 13, else pgcrypto) for the
# random bits, overlays the first 48 bits with the current Unix time in ms,
# and flips bits 52/53 to turn the v4 version nibble (0100) into v7 (0111).
_UUIDV7_FALLBACK_SQL = """
CREATE EXTENSION IF NOT EXISTS pgcrypto;

CREATE OR REPLACE FUNCTION uuidv7() RETURNS uuid AS $$
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
$$ LANGUAGE plpgsql VOLATILE;
"""


def _ensure_uuidv7(conn) -> None:
    """Install the uuidv7() fallback when the server predates PostgreSQL 18."""
    version_num = int(conn.execute(text("SHOW server_version_num")).scalar())
    if version_num < 180000:
        logger.info(
            "PostgreSQL %s < 18 detected — installing uuidv7() fallback function.",
            version_num,
        )
        conn.execute(text(_UUIDV7_FALLBACK_SQL))
    else:
        logger.info("PostgreSQL %s provides native uuidv7().", version_num)


def init_db() -> None:
    """Create the schema (and uuidv7 fallback if needed). Idempotent."""
    with engine.begin() as conn:
        _ensure_uuidv7(conn)
        Base.metadata.create_all(conn)
    logger.info("Database schema is ready.")
