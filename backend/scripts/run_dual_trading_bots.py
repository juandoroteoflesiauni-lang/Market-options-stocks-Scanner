"""Daemon dual: Alpaca + BingX con ejecución, vetos relajados y auditoría total. # [PD-3][TH]

Usage:
    python backend/scripts/run_dual_trading_bots.py --execute
    python backend/scripts/run_dual_trading_bots.py --execute --fast-interval 75 --slow-interval 240
    python backend/scripts/run_dual_trading_bots.py --execute --no-market-hours
"""

from __future__ import annotations

import argparse
import asyncio
import os
import sys
from datetime import UTC, datetime, time
from pathlib import Path

if __package__ in {None, ""}:  # pragma: no cover
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

try:
    from dotenv import load_dotenv

    load_dotenv()
except ImportError:  # pragma: no cover
    pass


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    from backend.config.bot_relaxed_thresholds import (
        BOT_FAST_CYCLE_INTERVAL_S,
        BOT_SLOW_CYCLE_INTERVAL_S,
    )

    parser = argparse.ArgumentParser(description="Dual trading bots daemon (Alpaca + BingX).")
    parser.add_argument(
        "--cycle-interval",
        type=int,
        default=BOT_SLOW_CYCLE_INTERVAL_S,
        help=f"Slow scan interval seconds (default: {BOT_SLOW_CYCLE_INTERVAL_S}).",
    )
    parser.add_argument(
        "--fast-interval",
        type=int,
        default=BOT_FAST_CYCLE_INTERVAL_S,
        help=f"Fast monitor interval seconds (default: {BOT_FAST_CYCLE_INTERVAL_S}).",
    )
    parser.add_argument(
        "--slow-interval",
        type=int,
        default=None,
        help="Slow scan interval (overrides --cycle-interval when set).",
    )
    parser.add_argument(
        "--no-dual-loop",
        action="store_true",
        help="Disable dual-loop; use --cycle-interval for a single fixed interval.",
    )
    parser.add_argument(
        "--execute",
        action="store_true",
        help="Send real orders (Alpaca paper + BingX VST demo by default).",
    )
    parser.add_argument(
        "--session-mode",
        choices=("verification", "profit"),
        default=None,
        help="verification=max trades for data; profit=strict PF gate + Kelly.",
    )
    parser.add_argument(
        "--no-market-hours",
        action="store_true",
        help="Run 24/7 ignoring US equity session gate.",
    )
    parser.add_argument(
        "--no-healthcheck-gate",
        action="store_true",
        help="BingX: skip provider healthcheck gate.",
    )
    parser.add_argument(
        "--alpaca-audit-db",
        default="data/alpaca_bot_audit.duckdb",
        help="DuckDB path for Alpaca cycle audit.",
    )
    parser.add_argument(
        "--bingx-audit-db",
        default="data/bingx_bot_audit.duckdb",
        help="DuckDB path for BingX cycle audit.",
    )
    parser.add_argument(
        "--audit-complex-db",
        default=None,
        help="Override AUDIT_DB_PATH for structured audit (default from settings).",
    )
    return parser.parse_args(argv)


_MARKET_OPEN = time(9, 30)
_MARKET_CLOSE = time(16, 0)
_EOD_TRIGGER = time(16, 5)


def _market_session_open(et_now_fn) -> bool:
    """True si estamos dentro de la sesion regular ET (lun-vie 09:30-16:00)."""
    now_et = et_now_fn(datetime.now(UTC))
    if now_et.weekday() >= 5:
        return False
    now_time = now_et.time().replace(second=0, microsecond=0)
    return _MARKET_OPEN <= now_time < _MARKET_CLOSE


def _eod_session_due(et_now_fn, *, already_ran: set[str]) -> bool:
    """True una vez por día hábil tras 16:05 ET para calibración EOD."""
    now_et = et_now_fn(datetime.now(UTC))
    if now_et.weekday() >= 5:
        return False
    day_key = now_et.date().isoformat()
    if day_key in already_ran:
        return False
    now_time = now_et.time().replace(second=0, microsecond=0)
    return now_time >= _EOD_TRIGGER


async def _run_eod_pipeline(
    *,
    alpaca_client: object,
    bingx_client: object,
    alpaca_audit_db: str,
    bingx_audit_db: str,
) -> None:
    """EOD: auditoría reforzada + calibración + meta-learner."""
    from backend.services.eod_session_pipeline import run_eod_session_pipeline

    await run_eod_session_pipeline(
        alpaca_client=alpaca_client,
        bingx_client=bingx_client,
        alpaca_audit_db=alpaca_audit_db,
        bingx_audit_db=bingx_audit_db,
    )


async def _export_eod_audit(
    *,
    alpaca_client: object,
    bingx_client: object,
    alpaca_audit_db: str,
    bingx_audit_db: str,
) -> None:
    """Compat: delega al pipeline EOD completo."""
    await _run_eod_pipeline(
        alpaca_client=alpaca_client,
        bingx_client=bingx_client,
        alpaca_audit_db=alpaca_audit_db,
        bingx_audit_db=bingx_audit_db,
    )


async def _build_services(args: argparse.Namespace) -> tuple[object, object]:
    from backend.config.bot_relaxed_thresholds import (
        ALPACA_PAPER_EQUITY_USD,
        BINGX_DEMO_EQUITY_USDT,
        BINGX_NOTIONAL_PER_TRADE_USDT,
        VERIFICATION_ALPACA_NOTIONAL_USD,
        VERIFICATION_BINGX_NOTIONAL_USDT,
        apply_session_mode_env,
    )
    from backend.config.dual_bot_core_universe import (
        core_bingx_venue_symbols,
        dual_bot_fixed_universe_enabled,
        resolve_active_equity_universe,
    )
    from backend.config.logger_setup import get_logger
    from backend.config.settings import load_settings
    from backend.layer_1_data.datos.alpaca_client import AlpacaClient
    from backend.layer_1_data.datos.bingx_client import BINGX_REST_VST_BASE, BingXClient
    from backend.layer_1_data.fetchers.fmp_client import FMPClient
    from backend.layer_1_data.fetchers.massive_client import MassiveClient
    from backend.services.alpaca_bot_service import AlpacaBotService
    from backend.services.alpaca_decision_engine import AlpacaDecisionConfig
    from backend.services.alpaca_risk_desk import AlpacaRiskDesk, AlpacaRiskPolicy
    from backend.services.bingx_bot_service import BingXBotService
    from backend.services.bingx_risk_desk import BingXRiskDeskPolicy
    from backend.services.bingx_universe import BingXUniverseService
    from backend.services.bot.bingx_bot_types import BingXRiskPolicy
    from backend.services.shared_options_snapshot_resolver import shared_options_snapshot_service
    from backend.services.technical_terminal_payload import (
        build_technical_terminal_payload_from_candles,
    )

    logger = get_logger(__name__)
    settings = load_settings()
    session_mode = (
        args.session_mode or os.getenv("BOT_SESSION_MODE", "verification").strip().lower()
    )
    apply_session_mode_env(session_mode, execute_orders=args.execute)
    logger.info("dual_bots.session_mode mode=%s execute=%s", session_mode, args.execute)

    if args.audit_complex_db:
        os.environ["AUDIT_DB_PATH"] = args.audit_complex_db

    alpaca_mode = os.getenv("ALPACA_TRADING_MODE", "paper").strip().lower()
    alpaca_base = (
        settings.alpaca_live_base_url if alpaca_mode == "live" else settings.alpaca_trading_base_url
    )
    alpaca_dry = alpaca_mode == "dry_run" or (
        not args.execute and os.getenv("ALPACA_DRY_RUN", "true").lower() in {"1", "true", "yes"}
    )
    alpaca_client = AlpacaClient(
        api_key=settings.alpaca_api_key.get_secret_value() if settings.alpaca_api_key else None,
        secret_key=(
            settings.alpaca_api_secret.get_secret_value() if settings.alpaca_api_secret else None
        ),
        base_url=alpaca_base,
        dry_run=alpaca_dry,
    )
    alpaca_risk = AlpacaRiskPolicy.from_env()
    alpaca_service = AlpacaBotService(
        client=alpaca_client,
        universe=resolve_active_equity_universe(),
        trading_mode=alpaca_mode,
        risk_policy=alpaca_risk,
        decision_config=AlpacaDecisionConfig.from_env(),
        risk_desk=AlpacaRiskDesk(policy=alpaca_risk),
    )

    trading_env = os.getenv("BINGX_BOT_TRADING_ENV", "prod-vst").strip().lower()
    bx_key = settings.bingx_api_key.get_secret_value() if settings.bingx_api_key else None
    bx_secret = settings.bingx_secret.get_secret_value() if settings.bingx_secret else None
    bingx_dry = not args.execute and os.getenv("BINGX_DRY_RUN", "true").lower() in {
        "1",
        "true",
        "yes",
    }
    if trading_env == "prod-vst" and args.execute:
        bingx_client = BingXClient(
            api_key=bx_key,
            secret_key=bx_secret,
            base_url=BINGX_REST_VST_BASE,
            dry_run=False,
            allow_env_dry_run_override=False,
        )
    else:
        bingx_client = BingXClient(
            api_key=bx_key,
            secret_key=bx_secret,
            dry_run=bingx_dry,
            allow_env_dry_run_override=False,
        )

    bingx_equity = float(os.getenv("BINGX_EQUITY_USDT", str(BINGX_DEMO_EQUITY_USDT)))
    bingx_notional = float(
        os.getenv("BINGX_NOTIONAL_PER_TRADE_USDT", str(BINGX_NOTIONAL_PER_TRADE_USDT))
    )
    bingx_risk_policy = BingXRiskPolicy(
        equity_usdt=bingx_equity,
        notional_per_trade_usdt=bingx_notional,
        max_open_positions=int(os.getenv("RISK_MAX_OPEN_POSITIONS", "10")),
    )
    desk_policy = BingXRiskDeskPolicy.from_env()

    fmp = FMPClient()
    massive = MassiveClient()
    universe_service = BingXUniverseService(
        client=bingx_client, fmp_client=fmp, massive_client=massive
    )

    async def _venue_technical(sym: str, candles: list[dict], timeframe: str) -> dict:
        return await build_technical_terminal_payload_from_candles(sym, candles, timeframe)

    options_fn = shared_options_snapshot_service

    bingx_universe = core_bingx_venue_symbols() if dual_bot_fixed_universe_enabled() else None
    bingx_service = BingXBotService(
        client=bingx_client,
        universe=bingx_universe,
        options_snapshot_fn=options_fn,
        venue_technical_fn=_venue_technical,
        fmp_client=fmp,
        massive_client=massive,
        universe_service=universe_service,
        risk_policy=bingx_risk_policy,
        risk_desk_policy=desk_policy,
    )

    try:
        alpaca_balance = await alpaca_client.fetch_account_balance()
        bingx_balance = await bingx_client.fetch_perp_balance()
        bingx_positions = await bingx_client.fetch_perp_positions()
        logger.info(
            "dual_bots.accounts alpaca_mode=%s alpaca_equity=%s target_equity_usd=%.2f "
            "bingx_env=%s bingx_balance=%s bingx_positions=%d bingx_dry=%s "
            "verification_notional alpaca=%.0f bingx=%.0f",
            alpaca_mode,
            alpaca_balance,
            ALPACA_PAPER_EQUITY_USD,
            trading_env,
            bingx_balance,
            len(bingx_positions),
            bingx_service.dry_run,
            VERIFICATION_ALPACA_NOTIONAL_USD,
            VERIFICATION_BINGX_NOTIONAL_USDT,
        )
    except Exception as exc:
        logger.warning("dual_bots.account_probe_failed error=%s", exc)

    return alpaca_service, bingx_service


async def _run(args: argparse.Namespace) -> int:
    from backend.config.bot_relaxed_thresholds import VERIFICATION_ALPACA_NOTIONAL_USD
    from backend.config.dual_bot_core_universe import (
        dual_bot_fixed_universe_enabled,
        resolve_active_equity_universe,
        warmup_core_quant_stack,
    )
    from backend.config.logger_setup import get_logger
    from backend.services.alpaca_audit_store import AlpacaAuditStore
    from backend.services.bingx_audit_store import BingXAuditStore
    from backend.services.trade_journal_service import init_trade_journal_table
    from backend.tasks.alpaca_bot_scheduler import AlpacaBotScheduler, AlpacaSchedulerConfig
    from backend.tasks.bingx_bot_scheduler import BingXBotScheduler, SchedulerConfig, _et_now
    from backend.tasks.dual_loop_policy import DualLoopConfig

    logger = get_logger(__name__)
    journal_path = Path("data/quantum_analyzer.duckdb")
    journal_path.parent.mkdir(parents=True, exist_ok=True)
    init_trade_journal_table(journal_path)
    alpaca_service, bingx_service = await _build_services(args)

    if dual_bot_fixed_universe_enabled():
        try:
            await bingx_service.refresh_universe()
            warmup_stats = await warmup_core_quant_stack()
            logger.info(
                "dual_bots.core_quant_stack_ready symbols=%d warmup=%s",
                len(resolve_active_equity_universe()),
                warmup_stats,
            )
        except Exception as exc:
            logger.warning("dual_bots.core_universe_warmup_failed error=%s", exc)

    slow_s = args.slow_interval if args.slow_interval is not None else args.cycle_interval
    dual = DualLoopConfig(
        enabled=not args.no_dual_loop,
        fast_interval_s=args.fast_interval,
        slow_interval_s=slow_s,
    )
    sched_kw = {"respect_market_hours": not args.no_market_hours}

    alpaca_audit = AlpacaAuditStore(args.alpaca_audit_db)
    bingx_audit = BingXAuditStore(args.bingx_audit_db)

    alpaca_scheduler = AlpacaBotScheduler(
        alpaca_service,
        AlpacaSchedulerConfig.from_dual_loop(dual, **sched_kw),
        audit_store=alpaca_audit,
    )
    bingx_scheduler = BingXBotScheduler(
        bingx_service,
        SchedulerConfig.from_dual_loop(
            dual,
            require_healthcheck=not args.no_healthcheck_gate,
            **sched_kw,
        ),
        audit_store=bingx_audit,
    )

    from backend.config.execution_policy import ExecutionPolicy
    from backend.config.profit_calibration import ProfitCalibrationPolicy

    exec_policy = ExecutionPolicy.from_env()
    cal_policy = ProfitCalibrationPolicy.from_env()
    session_mode = (
        args.session_mode or os.getenv("BOT_SESSION_MODE", "verification").strip().lower()
    )

    logger.info(
        "dual_bots.starting session_mode=%s dual_loop=%s fast_s=%d slow_s=%d execute=%s "
        "alpaca_dry=%s bingx_dry=%s alpaca_notional=%.0f bingx_notional=%.0f "
        "phase_ab_tca=on twap_bingx=%s elite_alpaca=%s price_collar=%s repeated_limit=%d "
        "pf_gate=%s kelly=%s alpaca_audit=%s bingx_audit=%s market_hours=%s",
        session_mode,
        dual.enabled,
        dual.fast_interval_s,
        dual.slow_interval_s,
        args.execute,
        alpaca_service.dry_run,
        bingx_service.dry_run,
        float(os.getenv("ALPACA_NOTIONAL_PER_TRADE_USD", str(VERIFICATION_ALPACA_NOTIONAL_USD))),
        bingx_service.risk_policy.notional_per_trade_usdt,
        exec_policy.bingx_twap_enabled,
        exec_policy.alpaca_elite_enabled,
        exec_policy.price_collar_enabled,
        exec_policy.repeated_execution_max_per_symbol,
        cal_policy.rolling_pf_enabled,
        cal_policy.kelly_enabled,
        args.alpaca_audit_db,
        args.bingx_audit_db,
        not args.no_market_hours,
    )

    await alpaca_scheduler.start()
    await bingx_scheduler.start()

    eod_ran_days: set[str] = set()
    eod_ran_on_shutdown = False

    try:
        while (
            alpaca_scheduler.state.value == "running" and bingx_scheduler.state.value == "running"
        ):
            if not args.no_market_hours and not _market_session_open(_et_now):
                logger.info("dual_bots.market_closed stopping schedulers for EOD audit")
                break
            if _eod_session_due(_et_now, already_ran=eod_ran_days):
                day_key = _et_now(datetime.now(UTC)).date().isoformat()
                eod_ran_days.add(day_key)
                logger.info("dual_bots.eod_session_trigger day=%s", day_key)
                await _run_eod_pipeline(
                    alpaca_client=alpaca_service._client,
                    bingx_client=bingx_service._client,
                    alpaca_audit_db=args.alpaca_audit_db,
                    bingx_audit_db=args.bingx_audit_db,
                )
                eod_ran_on_shutdown = True
            await asyncio.sleep(10)
    except (KeyboardInterrupt, asyncio.CancelledError):
        pass
    finally:
        await alpaca_scheduler.stop()
        await bingx_scheduler.stop()
        if not eod_ran_on_shutdown:
            await _run_eod_pipeline(
                alpaca_client=alpaca_service._client,
                bingx_client=bingx_service._client,
                alpaca_audit_db=args.alpaca_audit_db,
                bingx_audit_db=args.bingx_audit_db,
            )
        await alpaca_service._client.aclose()
        await bingx_service._client.aclose()

    logger.info("dual_bots.stopped")
    return 0


def main(argv: list[str] | None = None) -> int:
    from backend.config.bot_relaxed_thresholds import apply_session_mode_env
    from backend.config.logger_setup import get_logger

    args = _parse_args(argv)
    session_mode = (
        args.session_mode or os.getenv("BOT_SESSION_MODE", "verification").strip().lower()
    )
    apply_session_mode_env(session_mode, execute_orders=args.execute)
    logger = get_logger(__name__)
    try:
        return asyncio.run(_run(args))
    except Exception as exc:
        logger.exception("dual_bots.startup_failed error=%s", exc)
        return 1


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
