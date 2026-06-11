"""Standalone BingX paper-trading daemon.

Runs the full Scan → Filter → Risk → Execute pipeline on a configurable
schedule with ``dry_run=True``. Never sends live orders — the BingXClient
intercepts every placement.

Usage:

    python backend/scripts/bingx_paper_daemon.py
    python backend/scripts/bingx_paper_daemon.py --cycle-interval 300
    python backend/scripts/bingx_paper_daemon.py --no-market-hours --persist ./data/audit.duckdb
    python backend/scripts/bingx_paper_daemon.py --symbols BTC-USDT AAPL-USDT

Exit codes:
    0  — stopped normally (Ctrl+C or SIGTERM)
    1  — startup error
"""

from __future__ import annotations

import argparse
import asyncio
import os
import sys
from pathlib import Path

if __package__ in {None, ""}:  # pragma: no cover — script execution shim
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

try:  # pragma: no cover
    from dotenv import load_dotenv

    load_dotenv()
except ImportError:  # pragma: no cover
    pass

from backend.config.logger_setup import get_logger
from backend.layer_1_data.datos.bingx_client import (
    BINGX_REST_BASE,
    BINGX_REST_VST_BASE,
    BingXClient,
)
from backend.services.bingx_audit_store import BingXAuditStore
from backend.services.bingx_bot_service import DEFAULT_UNIVERSE, BingXBotService
from backend.tasks.bingx_bot_scheduler import BingXBotScheduler, SchedulerConfig

logger = get_logger(__name__)


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="BingX paper-trading daemon — dry-run cycles on a schedule.",
    )
    parser.add_argument(
        "--cycle-interval",
        type=int,
        default=300,
        metavar="SECONDS",
        help="Seconds between full Scan → Execute cycles (default: 300 = 5 min).",
    )
    parser.add_argument(
        "--universe-interval",
        type=int,
        default=1800,
        metavar="SECONDS",
        help="Seconds between universe rebuilds (default: 1800 = 30 min).",
    )
    parser.add_argument(
        "--no-market-hours",
        action="store_true",
        help="Disable market-hours gate — run 24/7 regardless of ET session.",
    )
    parser.add_argument(
        "--no-healthcheck-gate",
        action="store_true",
        help="Disable provider healthcheck gate (cycles run even when providers degrade).",
    )
    parser.add_argument(
        "--persist",
        metavar="DB_PATH",
        default=None,
        help="Persist cycle audit records to a DuckDB file at DB_PATH.",
    )
    parser.add_argument(
        "--symbols",
        nargs="*",
        default=None,
        help="Override the default universe (e.g. --symbols BTC-USDT AAPL-USDT).",
    )
    parser.add_argument(
        "--vst-demo",
        action="store_true",
        help="Submit eligible orders to BingX Demo/VST instead of local dry-run.",
    )
    parser.add_argument(
        "--timeout",
        type=float,
        default=15.0,
        help="HTTP timeout for BingX client requests in seconds (default: 15).",
    )
    return parser.parse_args(argv)


async def _run(args: argparse.Namespace) -> int:
    client = BingXClient(
        api_key=os.getenv("BINGX_API_KEY"),
        secret_key=os.getenv("BINGX_SECRET"),
        base_url=BINGX_REST_VST_BASE if args.vst_demo else BINGX_REST_BASE,
        dry_run=not bool(args.vst_demo),
        allow_env_dry_run_override=not bool(args.vst_demo),
        timeout_seconds=float(args.timeout),
    )
    service = BingXBotService(
        client=client,
        universe=tuple(args.symbols) if args.symbols else DEFAULT_UNIVERSE,
    )

    audit_store: BingXAuditStore | None = BingXAuditStore(args.persist) if args.persist else None

    config = SchedulerConfig(
        cycle_interval_s=args.cycle_interval,
        universe_refresh_interval_s=args.universe_interval,
        respect_market_hours=not args.no_market_hours,
        require_healthcheck=not args.no_healthcheck_gate,
        refresh_universe=args.symbols is None,
    )

    scheduler = BingXBotScheduler(service, config, audit_store=audit_store)

    logger.info(
        "bingx_paper_daemon.starting cycle_interval_s=%d universe_interval_s=%d "
        "market_hours=%s healthcheck_gate=%s refresh_universe=%s persist=%s",
        config.cycle_interval_s,
        config.universe_refresh_interval_s,
        config.respect_market_hours,
        config.require_healthcheck,
        config.refresh_universe,
        args.persist or "disabled",
    )

    try:
        await scheduler.start()
        # Block until interrupted — the scheduler loop runs as a background task.
        while scheduler.state.value == "running":
            await asyncio.sleep(5)
    except (KeyboardInterrupt, asyncio.CancelledError):
        pass
    finally:
        await scheduler.stop()
        await client.aclose()

    logger.info("bingx_paper_daemon.stopped")
    return 0


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    try:
        return asyncio.run(_run(args))
    except Exception as exc:
        logger.error("bingx_paper_daemon.startup_failed error=%s", exc)
        return 1


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
