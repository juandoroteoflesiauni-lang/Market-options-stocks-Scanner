"""Standalone BingX bot dry-run probe.

Executes one full Scan -> Filter -> Risk -> Execute cycle against the BingX
public market data endpoints while keeping the client in ``dry_run=True``
mode — order placement is intercepted and logged, not sent.

Usage:

    python backend/scripts/bingx_dry_run.py
    python backend/scripts/bingx_dry_run.py --symbols BTC-USDT ETH-USDT
    python backend/scripts/bingx_dry_run.py --notional 5 --leverage 3 --json

Exit codes:
    0  — cycle completed, at least one snapshot returned.
    2  — no snapshots returned (likely network / venue issue).
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
from pathlib import Path
from typing import Any

if __package__ in {None, ""}:  # pragma: no cover - script execution shim
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

try:  # pragma: no cover - optional dev dependency
    from dotenv import load_dotenv

    load_dotenv()
except ImportError:  # pragma: no cover - dotenv not installed in all envs
    pass

from backend.config.logger_setup import get_logger
from backend.layer_1_data.datos.bingx_client import (
    BINGX_REST_BASE,
    BINGX_REST_VST_BASE,
    BingXClient,
)
from backend.layer_1_data.fetchers.fmp_client import FMPClient
from backend.layer_1_data.fetchers.massive_client import MassiveClient
from backend.routers.options_router import options_snapshot_service
from backend.services.bingx_audit_store import BingXAuditEntry, BingXAuditStore
from backend.services.bingx_bot_service import (
    DEFAULT_KLINES_PER_SYMBOL,
    DEFAULT_LEVERAGE,
    DEFAULT_MICRO_EQUITY_USDT,
    DEFAULT_NOTIONAL_PER_TRADE_USDT,
    DEFAULT_SCAN_INTERVAL,
    DEFAULT_UNIVERSE,
    BingXBotService,
    BingXCycleResult,
    BingXRiskPolicy,
)
from backend.services.technical_terminal_payload import (
    build_technical_terminal_payload_from_candles,
)

logger = get_logger(__name__)


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="BingX bot dry-run cycle probe.")
    parser.add_argument(
        "--symbols",
        nargs="*",
        default=None,
        help="Override universe (e.g. --symbols BTC-USDT AAPL-USDT). Defaults to bot universe.",
    )
    parser.add_argument(
        "--interval",
        default=DEFAULT_SCAN_INTERVAL,
        help=f"K-line interval (default: {DEFAULT_SCAN_INTERVAL}).",
    )
    parser.add_argument(
        "--klines",
        type=int,
        default=DEFAULT_KLINES_PER_SYMBOL,
        help=f"Bars to fetch per symbol (default: {DEFAULT_KLINES_PER_SYMBOL}).",
    )
    parser.add_argument(
        "--equity",
        type=float,
        default=DEFAULT_MICRO_EQUITY_USDT,
        help=f"Account equity in USDT (default: {DEFAULT_MICRO_EQUITY_USDT}).",
    )
    parser.add_argument(
        "--notional",
        type=float,
        default=DEFAULT_NOTIONAL_PER_TRADE_USDT,
        help=f"Per-trade notional in USDT (default: {DEFAULT_NOTIONAL_PER_TRADE_USDT}).",
    )
    parser.add_argument(
        "--leverage",
        type=float,
        default=DEFAULT_LEVERAGE,
        help=f"Leverage multiplier (default: {DEFAULT_LEVERAGE}).",
    )
    parser.add_argument(
        "--timeout",
        type=float,
        default=15.0,
        help="HTTP timeout in seconds.",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="Emit the full cycle result as JSON (default: human summary).",
    )
    parser.add_argument(
        "--persist",
        metavar="DB_PATH",
        default=None,
        help=(
            "Persist the cycle audit record to a DuckDB file at DB_PATH. "
            "Omit to skip persistence (default). "
            "The cycle_id is logged on success."
        ),
    )
    parser.add_argument(
        "--vst-demo",
        action="store_true",
        help="Submit eligible orders to BingX Demo/VST (open-api-vst); never uses real funds.",
    )
    return parser.parse_args(argv)


def _summarize(result: BingXCycleResult, instruments: list[dict[str, Any]]) -> dict[str, Any]:
    equity_types = {"stock_perp", "stock_index_perp"}
    equity = [i for i in instruments if i.get("market_type") in equity_types]
    return {
        "started_at": result.started_at,
        "finished_at": result.finished_at,
        "dry_run": result.dry_run,
        "trading_environment": getattr(result, "trading_environment", None),
        "universe_size": len(result.universe),
        "snapshots_with_bars": sum(1 for s in result.snapshots if s.bars > 0),
        "signals_long": sum(1 for s in result.signals if s.direction == "LONG"),
        "signals_short": sum(1 for s in result.signals if s.direction == "SHORT"),
        "signals_flat": sum(1 for s in result.signals if s.direction == "FLAT"),
        "decisions_allow": sum(1 for d in result.decisions if d.suitability == "ALLOW"),
        "decisions_size_down": sum(1 for d in result.decisions if d.suitability == "SIZE_DOWN"),
        "decisions_block": sum(1 for d in result.decisions if d.suitability == "BLOCK"),
        "decisions_insufficient": sum(
            1 for d in result.decisions if d.suitability == "INSUFFICIENT_DATA"
        ),
        "plans_authorized": sum(1 for p in result.plans if p.authorized),
        "executions": len(result.executions),
        # ── Universe composition ──────────────────────────────────────────────
        "stock_perp_count": sum(1 for i in instruments if i.get("market_type") == "stock_perp"),
        "stock_index_perp_count": sum(
            1 for i in instruments if i.get("market_type") == "stock_index_perp"
        ),
        "crypto_count": sum(1 for i in instruments if i.get("market_type") == "crypto_standard"),
        "execution_allowed_count": sum(1 for i in instruments if i.get("execution_allowed")),
        "l2_active_count": sum(1 for i in equity if i.get("execution_allowed")),
        "l2_pending_count": sum(1 for i in equity if not i.get("execution_allowed")),
        # ── Provider degradation (presence only — values never exposed) ───────
        "providers": {
            "bingx_api_key": bool(os.getenv("BINGX_API_KEY")),
            "fmp_api_key": bool(os.getenv("FMP_API_KEY")),
            "gemini_api_key": bool(os.getenv("GEMINI_API_KEY")),
        },
    }


async def _run(args: argparse.Namespace) -> int:
    policy = BingXRiskPolicy(
        equity_usdt=float(args.equity),
        notional_per_trade_usdt=float(args.notional),
        leverage=float(args.leverage),
    )
    client = BingXClient(
        api_key=os.getenv("BINGX_API_KEY"),
        secret_key=os.getenv("BINGX_SECRET"),
        base_url=BINGX_REST_VST_BASE if args.vst_demo else BINGX_REST_BASE,
        dry_run=not bool(args.vst_demo),
        allow_env_dry_run_override=not bool(args.vst_demo),
        timeout_seconds=float(args.timeout),
    )
    # Inject data providers for full analysis (options, fundamentals, etc.)
    fmp_client = FMPClient()
    massive_client = MassiveClient()

    # Initialize DuckDB tables for predictive/probabilistic engines
    from backend.layer_1_data.datos.predictive_storage import PredictiveStorage

    PredictiveStorage()

    service = BingXBotService(
        client=client,
        universe=tuple(args.symbols) if args.symbols else DEFAULT_UNIVERSE,
        risk_policy=policy,
        scan_interval=args.interval,
        klines_per_symbol=int(args.klines),
        options_snapshot_fn=options_snapshot_service,
        venue_technical_fn=build_technical_terminal_payload_from_candles,
        fmp_client=fmp_client,
        massive_client=massive_client,
    )
    try:
        result = await service.run_cycle()
        instruments = await service.get_universe()  # cache is warm after run_cycle
    finally:
        await client.aclose()
        await FMPClient.aclose_shared_client()
        await MassiveClient.aclose_shared_client()

    if args.persist:
        try:
            store = BingXAuditStore(args.persist)
            entry = BingXAuditEntry.from_cycle_result(result)
            cycle_id = store.persist(entry)
            logger.info("bingx_dry_run.persisted cycle_id=%s db=%s", cycle_id, args.persist)
        except Exception as exc:
            logger.warning("bingx_dry_run.persist_failed error=%s", exc)

    summary = _summarize(result, instruments)
    if args.json:
        logger.info(
            "bingx_dry_run.full %s", json.dumps(result.to_dict(), sort_keys=True, default=str)
        )
    else:
        logger.info("bingx_dry_run.summary %s", json.dumps(summary, sort_keys=True))
        for sig in result.signals:
            logger.info(
                "bingx_dry_run.signal symbol=%s direction=%s score=%.4f reasons=%s",
                sig.symbol,
                sig.direction,
                sig.score,
                ",".join(sig.reason_codes) or "-",
            )
        for plan in result.plans:
            logger.info(
                "bingx_dry_run.plan symbol=%s side=%s notional=%.4f qty=%s authorized=%s",
                plan.symbol,
                plan.side,
                plan.notional_usdt,
                plan.quantity,
                plan.authorized,
            )

    return 0 if summary["snapshots_with_bars"] > 0 else 2


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    return asyncio.run(_run(args))


if __name__ == "__main__":  # pragma: no cover - manual entry point
    raise SystemExit(main())
