"""Entrypoint for the trading bot.

Examples:
    python main.py --init-db        # create schema (and uuidv7 fallback if needed)
    python main.py --once           # run a single evaluation cycle, then exit
    python main.py                  # run the continuous loop (DRY_RUN per .env)
    python main.py --live           # force live trading (override DRY_RUN)
    python main.py --dry-run        # force dry-run mode
"""
from __future__ import annotations

import argparse
import logging

from config import settings


def _setup_logging() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)-7s %(name)s | %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )


def main() -> None:
    parser = argparse.ArgumentParser(description="Multi-asset Alpaca trading bot")
    parser.add_argument("--init-db", action="store_true", help="Create DB schema and exit")
    parser.add_argument("--once", action="store_true", help="Run one cycle and exit")
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--live", action="store_true", help="Force live trading")
    mode.add_argument("--dry-run", action="store_true", help="Force dry-run (no orders)")
    args = parser.parse_args()

    _setup_logging()
    log = logging.getLogger("main")

    if args.init_db:
        from database.db import init_db

        init_db()
        log.info("Schema initialised. Exiting.")
        return

    dry_run = settings.dry_run
    if args.live:
        dry_run = False
    elif args.dry_run:
        dry_run = True

    # Imported here so --init-db works even without Alpaca creds configured.
    from engine import TradingEngine

    engine = TradingEngine(dry_run=dry_run)

    if args.once:
        engine.bootstrap()
        engine.run_once()
        engine.shutdown()
        log.info("Single cycle complete. Exiting.")
        return

    try:
        engine.run()
    except KeyboardInterrupt:
        log.info("Interrupted — shutting down.")
        engine.shutdown()


if __name__ == "__main__":
    main()
