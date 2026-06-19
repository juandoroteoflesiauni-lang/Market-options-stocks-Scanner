from __future__ import annotations

from typing import Any

"""Standalone BingX bot live-execution script.

Executes a full Scan -> Filter -> Risk -> Execute cycle against the BingX
public market data endpoints, and SUBMITS orders to the venue if signals are ALLOW.
By default, respects the BINGX_BOT_ENABLE_LIVE / BINGX_BOT_TRADING_ENV environment
variables for ultimate control.

Usage:

    python backend/scripts/bingx_live_bot.py --confirm-live
    python backend/scripts/bingx_live_bot.py --symbols BTC-USDT ETH-USDT --confirm-live
    python backend/scripts/bingx_live_bot.py --continuous --sleep 60 --confirm-live

Exit codes:
    0  — cycle completed, at least one snapshot returned (or continuous mode gracefully exited).
    2  — no snapshots returned (likely network / venue issue).
    3  — blocked by safety checks (missing --confirm-live flag).
"""


import argparse
import asyncio
import json
import os
import sys
import uuid
from datetime import UTC, datetime
from pathlib import Path

from backend.services.trade_journal_service import init_table, record_bot_cycle

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
from backend.services.shared_options_snapshot_resolver import shared_options_snapshot_service
from backend.services.technical_terminal_payload import (
    build_technical_terminal_payload_from_candles,
)

logger = get_logger(__name__)


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="BingX bot live execution script.")
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
            "Omit to skip persistence. The cycle_id is logged on success."
        ),
    )
    parser.add_argument(
        "--vst-demo",
        action="store_true",
        help="Submit eligible orders to BingX Demo/VST (open-api-vst); uses VST instead of real USDT.",
    )
    parser.add_argument(
        "--confirm-live",
        action="store_true",
        help="Safety flag to confirm intention to execute live trades.",
    )
    parser.add_argument(
        "--continuous",
        action="store_true",
        help="Run continuously in a loop.",
    )
    parser.add_argument(
        "--sleep",
        type=int,
        default=60,
        help="Seconds to sleep between cycles in continuous mode (default: 60).",
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
        "stock_perp_count": sum(1 for i in instruments if i.get("market_type") == "stock_perp"),
        "stock_index_perp_count": sum(
            1 for i in instruments if i.get("market_type") == "stock_index_perp"
        ),
        "crypto_count": sum(1 for i in instruments if i.get("market_type") == "crypto_standard"),
        "execution_allowed_count": sum(1 for i in instruments if i.get("execution_allowed")),
        "l2_active_count": sum(1 for i in equity if i.get("execution_allowed")),
        "l2_pending_count": sum(1 for i in equity if not i.get("execution_allowed")),
        "providers": {
            "bingx_api_key": bool(os.getenv("BINGX_API_KEY")),
            "fmp_api_key": bool(os.getenv("FMP_API_KEY")),
            "gemini_api_key": bool(os.getenv("GEMINI_API_KEY")),
        },
    }


async def _run_single_cycle(
    args: argparse.Namespace, client: BingXClient, policy: BingXRiskPolicy
) -> int:
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
        options_snapshot_fn=shared_options_snapshot_service,
        venue_technical_fn=build_technical_terminal_payload_from_candles,
        fmp_client=fmp_client,
        massive_client=massive_client,
    )
    result = await service.run_cycle()
    instruments = await service.get_universe()

    if args.persist:
        try:
            store = BingXAuditStore(args.persist)
            entry = BingXAuditEntry.from_cycle_result(result)
            cycle_id = store.persist(entry)
            logger.info("bingx_live_bot.persisted cycle_id=%s db=%s", cycle_id, args.persist)
        except Exception as exc:
            logger.warning("bingx_live_bot.persist_failed error=%s", exc)

    summary = _summarize(result, instruments)
    if args.json:
        logger.info(
            "bingx_live_bot.full %s", json.dumps(result.to_dict(), sort_keys=True, default=str)
        )
    else:
        logger.info("bingx_live_bot.summary %s", json.dumps(summary, sort_keys=True))
        for sig in result.signals:
            logger.info(
                "bingx_live_bot.signal symbol=%s direction=%s score=%.4f reasons=%s",
                sig.symbol,
                sig.direction,
                sig.score,
                ",".join(sig.reason_codes) or "-",
            )
        for plan in result.plans:
            logger.info(
                "bingx_live_bot.plan symbol=%s side=%s notional=%.4f qty=%s authorized=%s",
                plan.symbol,
                plan.side,
                plan.notional_usdt,
                plan.quantity,
                plan.authorized,
            )
        for ex in result.executions:
            logger.info(
                "bingx_live_bot.execution symbol=%s side=%s qty=%s response=%s",
                ex.symbol,
                ex.side,
                ex.requested_qty,
                ex.raw,
            )

    # ── Cycle Flight Recorder ────────────────────────────────────────────────
    try:
        db_path = Path("data/quantum_analyzer.duckdb")
        init_table(db_path)

        # 1. Fetch latest account state
        try:
            account_state = await service.get_account_state()
            total_equity = float(account_state.get("total_equity_usdt") or 0.0)
            available_margin = float(account_state.get("available_margin_usdt") or 0.0)
            open_positions_list = account_state.get("open_positions") or []
            open_positions = len(open_positions_list)
        except Exception as exc:
            logger.warning("bingx_live_bot.flight_recorder.account_state_failed error=%s", exc)
            total_equity = 0.0
            available_margin = 0.0
            open_positions = 0
            open_positions_list = []

        # 2. Build active positions mapping
        active_positions_map = {}
        for pos in open_positions_list:
            if isinstance(pos, dict) and pos.get("symbol"):
                sym = pos["symbol"]
                active_positions_map[sym] = {
                    "side": pos.get("side"),
                    "leverage": pos.get("leverage"),
                    "unrealized_pnl": pos.get("unrealized_pnl"),
                }

        # 3. Resolve cycle ID
        final_cycle_id = locals().get("cycle_id")
        if not final_cycle_id:
            ts = datetime.now(UTC).strftime("%Y%m%dT%H%M%S")
            final_cycle_id = f"{ts}_{uuid.uuid4().hex[:8]}"

        # 4. Serialize metrics
        serialized_metrics = {}
        for analysis in result.analyses:
            sym = analysis.venue_symbol

            # Reference price
            ref_price = None
            if analysis.venue.klines and len(analysis.venue.klines) > 0:
                ref_price = analysis.venue.klines[-1].get("close")
            if ref_price is None and isinstance(analysis.underlying.quote, dict):
                ref_price = analysis.underlying.quote.get("price")
            ref_price_float = float(ref_price) if ref_price is not None else 0.0

            # Resolve price zone
            zone = service.resolve_price_zone(ref_price_float, analysis)

            # Resolve FSM state
            pos_info = active_positions_map.get(sym)
            if pos_info:
                side = pos_info["side"]
                if side == "LONG":
                    if zone == "ACUMULACION":
                        fsm_state = "ACCUMULATING_LONG"
                    elif zone == "DISTRIBUCION":
                        fsm_state = "FADING_LONG"
                    else:
                        fsm_state = "LONG_FULL"
                else:
                    if zone == "DISTRIBUCION":
                        fsm_state = "ACCUMULATING_SHORT"
                    elif zone == "ACUMULACION":
                        fsm_state = "FADING_SHORT"
                    else:
                        fsm_state = "SHORT_FULL"
            else:
                fsm_state = "STANDBY"

            # Options metrics
            opts_report = getattr(analysis.options, "predictive_report", None)
            gamma_flip = (
                float(opts_report.gamma_flip_level)
                if (opts_report and opts_report.gamma_flip_level is not None)
                else None
            )
            shadow_delta = (
                float(opts_report.shadow_delta_imbalance)
                if (opts_report and opts_report.shadow_delta_imbalance is not None)
                else None
            )

            confluence_score = None
            if analysis.options.metrics:
                confluence_score = analysis.options.metrics.get("confluence_score")

            serialized_metrics[sym] = {
                "reference_price": ref_price_float,
                "current_zone": zone,
                "fsm_state": fsm_state,
                "gamma_flip": gamma_flip,
                "shadow_delta": shadow_delta,
                "confluence_score": confluence_score,
                "options_metrics": analysis.options.metrics or {},
                "readiness_score": analysis.readiness_score,
            }

        # 5. Actions taken
        result_dict = result.to_dict()
        actions_taken = {
            "executions": result_dict.get("executions", []),
            "plans": result_dict.get("plans", []),
            "decisions": result_dict.get("decisions", []),
            "risk_decisions": result_dict.get("risk_decisions", []),
            "blocked_reasons": result_dict.get("blocked_reasons", {}),
        }

        # 6. Record cycle
        cycle_data = {
            "cycle_id": final_cycle_id,
            "timestamp": datetime.now(UTC),
            "total_equity": total_equity,
            "available_margin": available_margin,
            "open_positions": open_positions,
            "serialized_metrics": serialized_metrics,
            "actions_taken": actions_taken,
            "summary": summary,
        }
        await record_bot_cycle(cycle_data, db_path=db_path)
    except Exception as exc:
        logger.warning("bingx_live_bot.flight_recorder.failed error=%s", exc, exc_info=True)

    return 0 if summary["snapshots_with_bars"] > 0 else 2


async def _run(args: argparse.Namespace) -> int:
    if not args.confirm_live:
        logger.error(
            "bingx_live_bot.aborted reason='Missing --confirm-live flag. "
            "This is a safety measure for live execution script.'"
        )
        return 3

    enable_live_raw = (os.getenv("BINGX_BOT_ENABLE_LIVE") or "false").strip().lower()
    enable_live = enable_live_raw in {"1", "true", "yes"}

    dry_run_raw = (os.getenv("BINGX_DRY_RUN") or "true").strip().lower()
    client_dry_run = dry_run_raw not in {"0", "false", "no", "live"}

    if enable_live and client_dry_run:
        logger.error(
            "bingx_live_bot.aborted reason='BINGX_BOT_ENABLE_LIVE is true but "
            "BINGX_DRY_RUN (CLIENT_DRY_RUN) is true. Live trading requires BINGX_DRY_RUN=false.'"
        )
        return 3

    policy = BingXRiskPolicy(
        equity_usdt=float(args.equity),
        notional_per_trade_usdt=float(args.notional),
        leverage=float(args.leverage),
    )

    # In live mode, we enforce dry_run=False inside the client for operations
    # Real environment endpoints are used unless overrode by --vst-demo.
    # allow_env_dry_run_override=False ensures env vars cannot force dry_run=True
    client = BingXClient(
        api_key=os.getenv("BINGX_API_KEY"),
        secret_key=os.getenv("BINGX_SECRET"),
        base_url=BINGX_REST_VST_BASE if args.vst_demo else BINGX_REST_BASE,
        dry_run=False,
        allow_env_dry_run_override=False,  # Strict: no env var override in live mode
        timeout_seconds=float(args.timeout),
    )

    try:
        if args.continuous:
            logger.info("bingx_live_bot.starting_continuous_mode interval_seconds=%s", args.sleep)
            while True:
                logger.info("bingx_live_bot.starting_cycle")
                try:
                    await _run_single_cycle(args, client, policy)
                except Exception as e:
                    logger.error("bingx_live_bot.cycle_error error=%s", str(e), exc_info=True)

                logger.info("bingx_live_bot.sleeping seconds=%s", args.sleep)
                await asyncio.sleep(args.sleep)
        else:
            return await _run_single_cycle(args, client, policy)
    except KeyboardInterrupt:
        logger.info("bingx_live_bot.shutting_down_gracefully")
        return 0
    finally:
        await client.aclose()


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    return asyncio.run(_run(args))


if __name__ == "__main__":  # pragma: no cover - manual entry point
    raise SystemExit(main())
