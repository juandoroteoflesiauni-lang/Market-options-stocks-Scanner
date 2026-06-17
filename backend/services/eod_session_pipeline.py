"""Pipeline EOD: auditoría reforzada, calibración y meta-learner. # [PD-3][TH]"""

from __future__ import annotations

import asyncio
import json
import os
import shutil
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from backend.config.alpaca_priority_route import ALPACA_ROUTE1_WATCHLIST
from backend.config.logger_setup import get_logger
from backend.config.shared_options_tier_policy import shared_options_tier_enabled

logger = get_logger(__name__)

_EOD_DIR = Path("data/eod_snapshots")
_REPORTS_DIR = Path("reports")
_MODELS_DIR = Path("backend/models")
_TRADE_JOURNAL = Path("data/quantum_analyzer.duckdb")
_ROUTER_MODEL = _MODELS_DIR / "meta_learner.joblib"


def eod_calibration_enabled() -> bool:
    return os.getenv("EOD_CALIBRATION_ENABLED", "true").strip().lower() in {
        "1",
        "true",
        "yes",
        "on",
    }


def eod_meta_learner_enabled() -> bool:
    return os.getenv("EOD_META_LEARNER_ENABLED", "true").strip().lower() in {
        "1",
        "true",
        "yes",
        "on",
    }


async def _fetch_balances(
    alpaca_client: object,
    bingx_client: object,
) -> dict[str, Any]:
    payload: dict[str, Any] = {}
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
    return payload


def _audit_store_summary(alpaca_audit_db: str, bingx_audit_db: str) -> dict[str, Any]:
    summary: dict[str, Any] = {}
    try:
        from backend.services.bingx_audit_store import BingXAuditStore

        bx = BingXAuditStore(bingx_audit_db)
        summary["bingx_cycles"] = bx.count()
    except Exception as exc:
        summary["bingx_cycles_error"] = str(exc)
    try:
        from backend.services.audit_duckdb_utils import connect_audit_duckdb

        if Path(alpaca_audit_db).exists():
            conn = connect_audit_duckdb(alpaca_audit_db, read_only=True)
            row = conn.execute("SELECT COUNT(*) FROM alpaca_audit_cycles").fetchone()
            conn.close()
            summary["alpaca_cycles"] = int(row[0]) if row else 0
    except Exception as exc:
        summary["alpaca_cycles_error"] = str(exc)
    try:
        from backend.audit.audit_complex_store import AuditComplexStore
        from backend.config.settings import load_settings

        store = AuditComplexStore(db_path=load_settings().audit_db_path)
        summary["audit_complex"] = {
            "api_calls": store.count_api_calls(),
            "process_snapshots": store.count_process_snapshots(),
            "errors": store.count_errors(),
            "logs": store.count_logs(),
        }
    except Exception as exc:
        summary["audit_complex_error"] = str(exc)
    return summary


def _trade_journal_today_summary() -> dict[str, Any]:
    if not _TRADE_JOURNAL.exists():
        return {"status": "missing_db"}
    try:
        from backend.services.trade_journal_eod import summarize_trade_journal_today
        from backend.services.trade_journal_service import list_trades

        trades = list_trades(_TRADE_JOURNAL, limit=500)
        return summarize_trade_journal_today(trades)
    except Exception as exc:
        return {"error": str(exc)}


def _run_r1_options_calibration() -> dict[str, Any]:
    from backend.services.alpaca_r1_options_calibration_service import run_r1_options_calibration

    result = run_r1_options_calibration(symbols=ALPACA_ROUTE1_WATCHLIST, limit_per_symbol=500)
    return {
        "calibrated_at": result.calibrated_at,
        "family_weights": result.family_weights.model_dump(),
        "metrics": result.metrics.model_dump(),
        "classic_weight": result.classic_weight,
        "options_weight": result.options_weight,
        "notes": list(result.notes),
    }


def _run_options_strategy_calibration() -> dict[str, Any]:
    from backend.services.options_strategy.calibration_loop import OptionsStrategyCalibrationLoop
    from backend.services.options_strategy.calibration_store import OptionsStrategyCalibrationStore

    report = OptionsStrategyCalibrationLoop.run(limit=500)
    store = OptionsStrategyCalibrationStore()
    persist = store.persist(report)
    return {
        "calibration_id": report.calibration_id,
        "observation_count": report.observation_count,
        "execute_rate": report.execute_rate,
        "suggested_weights": report.suggested_weights,
        "limitations": list(report.limitations),
        "persisted": persist.inserted,
    }


def _run_meta_learner_eod_batch() -> dict[str, Any]:
    from backend.scripts.train_meta_learner import train_for_symbol, train_side_models

    symbols = tuple(ALPACA_ROUTE1_WATCHLIST)
    per_symbol: dict[str, Any] = {}
    best_symbol = "SPY"
    best_acc = -1.0

    for sym in symbols:
        out_path = _MODELS_DIR / f"meta_learner_{sym.upper()}_latest.joblib"
        try:
            metrics = train_for_symbol(
                sym,
                days=int(os.getenv("EOD_META_LEARNER_DAYS", "90")),
                output_path=out_path,
                target_horizon="eod",
            )
            per_symbol[sym] = {
                "source": metrics.get("source"),
                "n_samples": metrics.get("n_samples"),
                "mean_accuracy": metrics.get("mean_accuracy"),
                "output_path": metrics.get("output_path"),
            }
            acc = float(metrics.get("mean_accuracy") or 0.0)
            if acc >= best_acc:
                best_acc = acc
                best_symbol = sym
        except Exception as exc:
            logger.warning("eod_pipeline.meta_learner_failed symbol=%s error=%s", sym, exc)
            per_symbol[sym] = {"error": str(exc)[:200]}

    side_result: dict[str, Any] = {}
    try:
        side_result = train_side_models(
            best_symbol,
            db_path=Path("backend/data/predictions.db"),
            side="both",
            output_path=None,
            target_horizon="eod",
            min_real_samples=int(os.getenv("EOD_META_LEARNER_MIN_SAMPLES", "50")),
        )
    except Exception as exc:
        side_result = {"error": str(exc)[:200]}

    from backend.services.meta_learner_promotion import (
        promotion_skip_reason,
        should_promote_meta_learner_to_router,
    )

    best_metrics = per_symbol.get(best_symbol) or {}
    activated_from = _MODELS_DIR / f"meta_learner_{best_symbol.upper()}_latest.joblib"
    router_updated = False
    promotion_blocked_reason: str | None = None
    if should_promote_meta_learner_to_router(best_metrics):
        if activated_from.exists():
            _MODELS_DIR.mkdir(parents=True, exist_ok=True)
            shutil.copy2(activated_from, _ROUTER_MODEL)
            router_updated = True
            try:
                from backend.api.routes.probabilistic_router import get_or_load_meta_learner

                get_or_load_meta_learner(force_reload=True)
            except Exception as exc:
                logger.debug("eod_pipeline.meta_learner_hot_reload_failed error=%s", exc)
    else:
        promotion_blocked_reason = promotion_skip_reason(best_metrics)
        logger.info(
            "eod_pipeline.meta_learner_promotion_skipped symbol=%s reason=%s source=%s",
            best_symbol,
            promotion_blocked_reason,
            best_metrics.get("source"),
        )

    return {
        "symbols_trained": per_symbol,
        "best_router_symbol": best_symbol,
        "router_model_updated": router_updated,
        "router_promotion_blocked": promotion_blocked_reason,
        "side_models": side_result,
    }


def _run_risk_calibration() -> dict[str, Any]:
    """Kelly / Monte Carlo desde trade journal (sin salir del proceso)."""
    try:
        from backend.domain.portfolio_risk_models import AccountState
        from backend.infrastructure.repositories.trade_history_repository import (
            TradeHistoryRepository,
        )
        from backend.services.monte_carlo_simulator import MonteCarloSimulator
        from backend.services.risk_of_ruin_engine import RiskOfRuinEngine
    except ImportError as exc:
        return {"skipped": True, "reason": str(exc)}

    repo = TradeHistoryRepository()
    trades = repo.get_recent(window=500)
    if len(trades) < 30:
        return {"skipped": True, "reason": f"insufficient_trades:{len(trades)}"}

    historical_rs = [float(t.realized_r) for t in trades if t.realized_r is not None]
    engine = RiskOfRuinEngine(MonteCarloSimulator())
    account_state = AccountState(
        initial_capital=100_000.0,
        current_equity=100_000.0,
        start_of_day_balance=100_000.0,
        phase="eod_calibration",
    )
    res = engine.evaluate_risk_of_ruin(
        historical_rs=historical_rs,
        account_state=account_state,
        max_loss_limit_pct=10.0,
        risk_per_trade_pct=0.5,
        num_simulations=5000,
        sim_length=100,
    )
    return {
        "trade_count": len(trades),
        "ror_pct": res.get("ror_pct"),
        "p50_equity": res.get("p50_equity"),
        "p5_equity": res.get("p5_equity"),
    }


def _run_calibrations_sync() -> dict[str, Any]:
    out: dict[str, Any] = {"started_at": datetime.now(tz=UTC).isoformat()}
    if eod_meta_learner_enabled():
        try:
            out["meta_learner"] = _run_meta_learner_eod_batch()
        except Exception as exc:
            out["meta_learner_error"] = str(exc)
    else:
        out["meta_learner"] = {"skipped": True}

    if eod_calibration_enabled():
        try:
            out["r1_options_calibration"] = _run_r1_options_calibration()
        except Exception as exc:
            out["r1_options_calibration_error"] = str(exc)
        try:
            out["options_strategy_calibration"] = _run_options_strategy_calibration()
        except Exception as exc:
            out["options_strategy_calibration_error"] = str(exc)
        try:
            out["risk_calibration"] = _run_risk_calibration()
        except Exception as exc:
            out["risk_calibration_error"] = str(exc)
    else:
        out["calibrations_skipped"] = True

    out["finished_at"] = datetime.now(tz=UTC).isoformat()
    return out


def _write_process_audit_report(
    eod_payload: dict[str, Any],
    calibrations: dict[str, Any],
) -> Path:
    stamp = datetime.now(tz=UTC).strftime("%Y%m%d")
    report = {
        "generated_at": datetime.now(tz=UTC).isoformat(),
        "eod_snapshot": {
            "alpaca_equity": (eod_payload.get("alpaca_balance") or {}).get("equity"),
            "bingx_equity": (
                (eod_payload.get("bingx_perp_balance") or {}).get("balance") or {}
            ).get("equity"),
            "bingx_positions": len(eod_payload.get("bingx_perp_positions") or []),
        },
        "route_pnl": eod_payload.get("route_pnl"),
        "trade_journal": eod_payload.get("trade_journal_today"),
        "tca_report": eod_payload.get("tca_report"),
        "profit_calibration": eod_payload.get("profit_calibration"),
        "audit_summary": eod_payload.get("audit_summary"),
        "shared_options_tier_enabled": shared_options_tier_enabled(),
        "calibrations": calibrations,
    }
    cal_r1 = calibrations.get("r1_options_calibration") or {}
    meta = calibrations.get("meta_learner") or {}
    lines = [
        f"# Auditoría EOD — {stamp}",
        "",
        f"Generado: {report['generated_at']}",
        "",
        "## Balances",
        f"- Alpaca equity: {report['eod_snapshot'].get('alpaca_equity')}",
        f"- BingX equity: {report['eod_snapshot'].get('bingx_equity')}",
        f"- Posiciones BingX: {report['eod_snapshot'].get('bingx_positions')}",
        "",
        "## Meta-learner",
        f"- Router actualizado: {meta.get('router_model_updated')}",
        f"- Mejor símbolo: {meta.get('best_router_symbol')}",
        "",
        "## Calibración R1 opciones",
        f"- Pesos: {(cal_r1.get('family_weights') or {})}",
        "",
        "## TCA (Implementation Shortfall)",
        f"- Trades con TCA: {(eod_payload.get('tca_report') or {}).get('trades_with_tca')}",
        f"- IS promedio portfolio (bps): {(eod_payload.get('tca_report') or {}).get('portfolio_avg_is_bps')}",
        f"- Slippage total USD: {(eod_payload.get('tca_report') or {}).get('portfolio_total_slippage_usd')}",
        "",
        "## Calibración profit (Fase C)",
        f"- Modo sesión: {(eod_payload.get('profit_calibration') or {}).get('session_mode')}",
        f"- PF portfolio: {((eod_payload.get('profit_calibration') or {}).get('by_route') or {}).get('PORTFOLIO', {}).get('rolling_pf')}",
        f"- Kelly scalar portfolio: {((eod_payload.get('profit_calibration') or {}).get('by_route') or {}).get('PORTFOLIO', {}).get('kelly_scalar')}",
        "",
    ]
    out_json = _REPORTS_DIR / f"process_audit_{stamp}.json"
    out_md = _REPORTS_DIR / f"process_audit_{stamp}.md"
    _REPORTS_DIR.mkdir(parents=True, exist_ok=True)
    out_json.write_text(json.dumps(report, indent=2, default=str), encoding="utf-8")
    out_md.write_text("\n".join(lines), encoding="utf-8")
    return out_json


async def run_eod_session_pipeline(
    *,
    alpaca_client: object,
    bingx_client: object,
    alpaca_audit_db: str,
    bingx_audit_db: str,
) -> Path:
    """Exporta auditoría EOD, corre calibraciones y persiste snapshot de proceso."""
    from backend.hub.api_consumption_monitor import api_consumption_monitor
    from backend.hub.market_data_ttl_cache import cache_metrics
    from backend.services.route_pnl_service import build_route_pnl_dashboard

    _EOD_DIR.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(tz=UTC).strftime("%Y%m%d")
    out_path = _EOD_DIR / f"eod_audit_{stamp}.json"

    payload: dict[str, Any] = {
        "generated_at": datetime.now(tz=UTC).isoformat(),
        "pipeline_version": "eod_v2",
        "alpaca_audit_db": alpaca_audit_db,
        "bingx_audit_db": bingx_audit_db,
        "audit_complex_db": os.getenv("AUDIT_DB_PATH", "data/audit_complex.duckdb"),
        "trade_journal_db": str(_TRADE_JOURNAL),
        "shared_options_tier_enabled": shared_options_tier_enabled(),
    }
    payload.update(await _fetch_balances(alpaca_client, bingx_client))

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

        audit_store = AuditComplexStore(db_path=load_settings().audit_db_path)
        payload["api_consumption_persisted"] = audit_store.get_api_call_stats_by_module()
        payload["market_data_cache_metrics"] = cache_metrics()
    except Exception as exc:
        payload["api_consumption_persisted_error"] = str(exc)

    payload["audit_summary"] = _audit_store_summary(alpaca_audit_db, bingx_audit_db)
    payload["trade_journal_today"] = _trade_journal_today_summary()
    try:
        from backend.services.tca.tca_eod_report import build_tca_eod_report

        payload["tca_report"] = build_tca_eod_report(_TRADE_JOURNAL)
    except Exception as exc:
        payload["tca_report_error"] = str(exc)

    try:
        dashboard = build_route_pnl_dashboard()
        payload["route_pnl"] = dashboard.model_dump(mode="json")
    except Exception as exc:
        payload["route_pnl_error"] = str(exc)

    try:
        from backend.services.calibration.rolling_pf_gate import build_profit_calibration_eod_report

        payload["profit_calibration"] = build_profit_calibration_eod_report(db_path=_TRADE_JOURNAL)
    except Exception as exc:
        payload["profit_calibration_error"] = str(exc)

    calibrations = await asyncio.to_thread(_run_calibrations_sync)
    payload["eod_calibrations"] = calibrations

    out_path.write_text(json.dumps(payload, indent=2, default=str), encoding="utf-8")
    process_path = _write_process_audit_report(payload, calibrations)

    try:
        from backend.audit.process_recorder import record_process_snapshot

        await record_process_snapshot(
            module="eod_pipeline",
            symbol="SESSION",
            indicators={"pipeline_version": "eod_v2"},
            market_data=payload.get("eod_snapshot") or {},
            signals={"calibrations": list(calibrations.keys())},
            decisions={
                "meta_router_updated": (calibrations.get("meta_learner") or {}).get(
                    "router_model_updated"
                )
            },
            risk_metrics=calibrations.get("risk_calibration") or {},
            context={
                "eod_audit_path": str(out_path),
                "process_audit_path": str(process_path),
            },
        )
    except Exception as exc:
        logger.warning("eod_pipeline.process_snapshot_failed error=%s", exc)

    logger.info(
        "eod_pipeline.complete eod=%s process=%s meta_updated=%s",
        out_path,
        process_path,
        (calibrations.get("meta_learner") or {}).get("router_model_updated"),
    )
    return out_path


__all__ = [
    "eod_calibration_enabled",
    "eod_meta_learner_enabled",
    "run_eod_session_pipeline",
]
