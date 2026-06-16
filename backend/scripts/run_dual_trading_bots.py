"""Daemon dual: Alpaca + BingX con ejecución, vetos relajados y auditoría total. # [PD-3][TH]

Usage:
    python backend/scripts/run_dual_trading_bots.py --execute
    python backend/scripts/run_dual_trading_bots.py --execute --fast-interval 75 --slow-interval 240
    python backend/scripts/run_dual_trading_bots.py --execute --no-market-hours
"""

from __future__ import annotations

import argparse
import asyncio
import json
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
        DEFAULT_BOT_CYCLE_INTERVAL_S,
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


def _market_session_open(et_now_fn) -> bool:
    """True si estamos dentro de la sesión regular ET (lun–vie 09:30–16:00)."""
    now_et = et_now_fn(datetime.now(UTC))
    if now_et.weekday() >= 5:
        return False
    now_time = now_et.time().replace(second=0, microsecond=0)
    return _MARKET_OPEN <= now_time < _MARKET_CLOSE


async def _export_eod_audit(
    *,
    alpaca_client: object,
    bingx_client: object,
    alpaca_audit_db: str,
    bingx_audit_db: str,
) -> None:
    """Exporta snapshot EOD: balances, consumo API y rutas de auditoría."""
    from backend.config.logger_setup import get_logger
    from backend.hub.api_consumption_monitor import api_consumption_monitor

    logger = get_logger(__name__)
    out_dir = Path("data/eod_snapshots")
    out_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(UTC).strftime("%Y%m%d")
    out_path = out_dir / f"eod_audit_{stamp}.json"

    payload: dict[str, object] = {
        "generated_at": datetime.now(UTC).isoformat(),
        "alpaca_audit_db": alpaca_audit_db,
        "bingx_audit_db": bingx_audit_db,
        "audit_complex_db": os.getenv("AUDIT_DB_PATH", "data/audit_complex.duckdb"),
        "trade_journal_db": "data/quantum_analyzer.duckdb",
    }

    try:
        fetch_bal = getattr(alpaca_client, "fetch_account_balance", None)
        if fetch_bal:
            payload["alpaca_balance"] = await fetch_bal()
    except Exception as exc:
        payload["alpaca_balance_error"] = str(exc)

    try:
        fetch_perp = getattr(bingx_client, "fetch_perp_balance", None)
        fetch_pos = getattr(bingx_client, "fetch_perp_positions", None)
        if fetch_perp:
            payload["bingx_perp_balance"] = await fetch_perp()
        if fetch_pos:
            payload["bingx_perp_positions"] = await fetch_pos()
    except Exception as exc:
        payload["bingx_balance_error"] = str(exc)

    try:
        reports = await api_consumption_monitor.get_report()
        payload["api_consumption"] = [
            {
                "provider": r.provider_name,
                "total_calls": r.stats.total_calls,
                "total_cost_usd": round(r.stats.total_cost_usd, 6),
                "cache_hit_rate": round(r.stats.cache_hit_rate, 4),
                "error_rate": round(r.stats.error_rate, 4),
            }
            for r in reports
        ]
        payload["api_consumption_summary"] = await api_consumption_monitor.get_dashboard()
    except Exception as exc:
        payload["api_consumption_error"] = str(exc)

    try:
        from backend.audit.audit_complex_store import AuditComplexStore
        from backend.config.settings import load_settings
        from backend.hub.market_data_ttl_cache import cache_metrics

        audit_store = AuditComplexStore(db_path=load_settings().audit_db_path)
        payload["api_consumption_persisted"] = audit_store.get_api_call_stats_by_module()
        payload["market_data_cache_metrics"] = cache_metrics()
    except Exception as exc:
        payload["api_consumption_persisted_error"] = str(exc)

    out_path.write_text(json.dumps(payload, indent=2, default=str), encoding="utf-8")
    logger.info("dual_bots.eod_audit_exported path=%s", out_path)


async def _build_services(args: argparse.Namespace) -> tuple[object, object]:
    from backend.api.routes.options_router import options_snapshot_service
    from backend.config.bot_relaxed_thresholds import (
        ALPACA_PAPER_EQUITY_USD,
        BINGX_DEMO_EQUITY_USDT,
        BINGX_NOTIONAL_PER_TRADE_USDT,
        VERIFICATION_ALPACA_NOTIONAL_USD,
        VERIFICATION_BINGX_NOTIONAL_USDT,
        apply_verification_session_env,
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
    from backend.services.bot.bingx_bot_types import BingXRiskPolicy
    from backend.services.bingx_universe import BingXUniverseService
    from backend.services.technical_terminal_payload import (
        build_technical_terminal_payload_from_candles,
    )

    logger = get_logger(__name__)
    settings = load_settings()
    apply_verification_session_env(execute_orders=args.execute)

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
        secret_key=settings.alpaca_api_secret.get_secret_value() if settings.alpaca_api_secret else None,
        base_url=alpaca_base,
        dry_run=alpaca_dry,
    )
    alpaca_risk = AlpacaRiskPolicy.from_env()
    alpaca_service = AlpacaBotService(
        client=alpaca_client,
        universe=settings.default_universe,
        trading_mode=alpaca_mode,
        risk_policy=alpaca_risk,
        decision_config=AlpacaDecisionConfig.from_env(),
        risk_desk=AlpacaRiskDesk(policy=alpaca_risk),
    )

    trading_env = os.getenv("BINGX_BOT_TRADING_ENV", "prod-vst").strip().lower()
    bx_key = settings.bingx_api_key.get_secret_value() if settings.bingx_api_key else None
    bx_secret = settings.bingx_secret.get_secret_value() if settings.bingx_secret else None
    bingx_dry = not args.execute and os.getenv("BINGX_DRY_RUN", "true").lower() in {"1", "true", "yes"}
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

    skip_options = os.getenv("BINGX_SKIP_OPTIONS_SNAPSHOT", "").lower() in {"1", "true", "yes"}
    options_fn = None if skip_options else options_snapshot_service

    bingx_service = BingXBotService(
        client=bingx_client,
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
    from backend.config.bot_relaxed_thresholds import (
        BINGX_NOTIONAL_PER_TRADE_USDT,
        VERIFICATION_ALPACA_NOTIONAL_USD,
    )
    from backend.config.logger_setup import get_logger
    from backend.config.settings import load_settings
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

    slow_s = args.slow_interval if args.slow_interval is not None else args.cycle_interval
    dual = DualLoopConfig(
        enabled=not args.no_dual_loop,
        fast_interval_s=args.fast_interval,
        slow_interval_s=slow_s,
    )
    sched_kw = dict(respect_market_hours=not args.no_market_hours)

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

    logger.info(
        "dual_bots.starting dual_loop=%s fast_s=%d slow_s=%d execute=%s alpaca_dry=%s bingx_dry=%s "
        "alpaca_notional=%.0f bingx_equity=%.2f bingx_notional=%.0f "
        "alpaca_audit=%s bingx_audit=%s audit_complex=%s market_hours=%s verification=True",
        dual.enabled,
        dual.fast_interval_s,
        dual.slow_interval_s,
        args.execute,
        alpaca_service.dry_run,
        bingx_service.dry_run,
        VERIFICATION_ALPACA_NOTIONAL_USD,
        bingx_service.risk_policy.equity_usdt,
        bingx_service.risk_policy.notional_per_trade_usdt,
        args.alpaca_audit_db,
        args.bingx_audit_db,
        os.getenv("AUDIT_DB_PATH", load_settings().audit_db_path),
        not args.no_market_hours,
    )

    await alpaca_scheduler.start()
    await bingx_scheduler.start()

    try:
        while (
            alpaca_scheduler.state.value == "running"
            and bingx_scheduler.state.value == "running"
        ):
            if not args.no_market_hours and not _market_session_open(_et_now):
                logger.info("dual_bots.market_closed stopping schedulers for EOD audit")
                break
            await asyncio.sleep(10)
    except (KeyboardInterrupt, asyncio.CancelledError):
        pass
    finally:
        await alpaca_scheduler.stop()
        await bingx_scheduler.stop()
        await _export_eod_audit(
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
    from backend.config.bot_relaxed_thresholds import apply_verification_session_env
    from backend.config.logger_setup import get_logger

    args = _parse_args(argv)
    apply_verification_session_env(execute_orders=args.execute)
    logger = get_logger(__name__)
    try:
        return asyncio.run(_run(args))
    except Exception as exc:
        logger.exception("dual_bots.startup_failed error=%s", exc)
        return 1


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
