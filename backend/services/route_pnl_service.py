"""Agrega PnL y actividad por ruta desde audits, journal y EOD. # [PD-3][TH]"""

from __future__ import annotations

import json
import logging
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from backend.domain.route_pnl_models import (
    RouteBucket,
    RoutePnLBucket,
    RoutePnLDailyPoint,
    RoutePnLDashboardResponse,
)
from backend.services.route_pnl_reconciliation import (
    allocate_alpaca_equity_delta,
    alpaca_equity_delta_from_daily,
    bingx_activity_from_fills,
    reconcile_bingx_realized_pnl,
)
from backend.services.trade_journal_service import list_trades

logger = logging.getLogger(__name__)

_ALPACA_DB = Path("data/alpaca_bot_audit.duckdb")
_BINGX_DB = Path("data/bingx_bot_audit.duckdb")
_EOD_DIR = Path("data/eod_snapshots")
_OPTIONS_DB = Path("backend/data/options_strategy_audit.sqlite3")
_JOURNAL_DB = Path("data/quantum_analyzer.duckdb")


def _empty_buckets() -> dict[RouteBucket, RoutePnLBucket]:
    return {route: RoutePnLBucket(route=route) for route in ("R1", "R2", "BINGX", "OPTIONS_R1")}


def _set_bucket_pnl(
    buckets: dict[RouteBucket, RoutePnLBucket],
    route: RouteBucket,
    *,
    realized_pnl: float,
    win_count: int,
    loss_count: int,
) -> None:
    current = buckets[route]
    buckets[route] = current.model_copy(
        update={
            "realized_pnl_usd": round(realized_pnl, 4),
            "win_count": win_count,
            "loss_count": loss_count,
        }
    )


def _merge_bucket(
    buckets: dict[RouteBucket, RoutePnLBucket],
    route: RouteBucket,
    *,
    trades: int = 0,
    executions: int = 0,
    pnl: float = 0.0,
    notional: float = 0.0,
    win: bool | None = None,
) -> None:
    current = buckets[route]
    win_count = current.win_count + (1 if win is True else 0)
    loss_count = current.loss_count + (1 if win is False else 0)
    buckets[route] = current.model_copy(
        update={
            "trade_count": current.trade_count + trades,
            "execution_count": current.execution_count + executions,
            "realized_pnl_usd": round(current.realized_pnl_usd + pnl, 4),
            "notional_usd": round(current.notional_usd + notional, 2),
            "win_count": win_count,
            "loss_count": loss_count,
        }
    )


from backend.services.audit_duckdb_utils import connect_audit_duckdb


def _load_alpaca_activity(buckets: dict[RouteBucket, RoutePnLBucket], *, limit: int) -> None:
    if not _ALPACA_DB.exists():
        return
    try:
        conn = connect_audit_duckdb(_ALPACA_DB, read_only=True)
        rows = conn.execute(
            "SELECT payload FROM alpaca_audit_cycles ORDER BY started_at DESC LIMIT 500"
        ).fetchall()
        conn.close()
    except Exception as exc:
        logger.warning("route_pnl.alpaca_audit_failed error=%s", exc)
        return

    operations: list[dict[str, Any]] = []
    from backend.services.alpaca_audit_store import _alpaca_operations_from_payload

    for (raw,) in rows:
        payload = json.loads(raw)
        operations.extend(_alpaca_operations_from_payload(payload))
        if len(operations) >= limit:
            break

    for op in operations[:limit]:
        route_raw = str(op.get("route") or "scan")
        bucket: RouteBucket = "R1" if route_raw == "priority" else "R2"
        pnl = float(op.get("realized_pnl_usd") or 0.0)
        notional = float(op.get("notional_usd") or 0.0)
        executed = op.get("event_type") == "execution"
        _merge_bucket(
            buckets,
            bucket,
            trades=1,
            executions=1 if executed else 0,
            pnl=pnl if pnl != 0.0 else 0.0,
            notional=notional,
            win=pnl > 0 if pnl != 0 else None,
        )


def _load_bingx_audit_activity(buckets: dict[RouteBucket, RoutePnLBucket], *, limit: int) -> int:
    """Carga actividad BingX desde audit; retorna filas cargadas."""
    if not _BINGX_DB.exists():
        return 0
    try:
        from backend.services.bingx_audit_store import _operations_from_payload

        conn = connect_audit_duckdb(_BINGX_DB, read_only=True)
        rows = conn.execute(
            "SELECT payload FROM bingx_audit_cycles ORDER BY started_at DESC LIMIT 500"
        ).fetchall()
        conn.close()
    except Exception as exc:
        logger.warning("route_pnl.bingx_audit_failed error=%s", exc)
        return 0

    operations: list[dict[str, Any]] = []
    for (raw,) in rows:
        operations.extend(_operations_from_payload(json.loads(raw)))
        if len(operations) >= limit:
            break

    for op in operations[:limit]:
        pnl = float(op.get("realized_pnl_usdt") or op.get("realized_pnl") or 0.0)
        notional = float(op.get("notional_usdt") or op.get("notional") or 0.0)
        executed = op.get("event_type") == "execution"
        _merge_bucket(
            buckets,
            "BINGX",
            trades=1,
            executions=1 if executed else 0,
            pnl=pnl if pnl != 0.0 else 0.0,
            notional=notional,
            win=pnl > 0 if pnl != 0 else None,
        )
    return len(operations[:limit])


def _apply_bingx_realized_reconciliation(
    buckets: dict[RouteBucket, RoutePnLBucket],
    *,
    limit: int,
) -> list[str]:
    """Sobrescribe PnL BingX con fills del exchange + journal."""
    notes: list[str] = []
    journal_trades = list_trades(_JOURNAL_DB, limit=limit) if _JOURNAL_DB.exists() else []
    rollup, report_fills = reconcile_bingx_realized_pnl(
        journal_trades=journal_trades,
        report_limit=limit,
    )

    if buckets["BINGX"].execution_count == 0 and report_fills:
        trades, executions, notional = bingx_activity_from_fills(report_fills)
        current = buckets["BINGX"]
        buckets["BINGX"] = current.model_copy(
            update={
                "trade_count": trades,
                "execution_count": executions,
                "notional_usd": notional,
            }
        )
        notes.append(
            f"BingX activity from exchange reports ({trades} fills; audit unavailable or empty)"
        )

    if rollup.realized_pnl != 0.0 or rollup.close_count > 0:
        _set_bucket_pnl(
            buckets,
            "BINGX",
            realized_pnl=rollup.realized_pnl,
            win_count=rollup.win_count,
            loss_count=rollup.loss_count,
        )
        notes.append(
            f"BingX realized PnL ({rollup.source}): {rollup.realized_pnl:+.4f} USDT "
            f"({rollup.close_count} closes, W{rollup.win_count}/L{rollup.loss_count})"
        )
    elif report_fills:
        notes.append("BingX exchange fills loaded; no closed PnL rows yet")
    return notes


def _apply_alpaca_equity_reconciliation(
    buckets: dict[RouteBucket, RoutePnLBucket],
    daily: tuple[RoutePnLDailyPoint, ...],
) -> list[str]:
    """Si audit no trae P/L, asigna delta EOD Alpaca por notional de ruta."""
    notes: list[str] = []
    audit_pnl = buckets["R1"].realized_pnl_usd + buckets["R2"].realized_pnl_usd
    if audit_pnl != 0.0:
        return notes

    delta = alpaca_equity_delta_from_daily(daily)
    if delta is None or delta == 0.0:
        return notes

    allocation = allocate_alpaca_equity_delta(
        total_delta_usd=delta,
        r1_notional=buckets["R1"].notional_usd,
        r2_notional=buckets["R2"].notional_usd,
        options_notional=buckets["OPTIONS_R1"].notional_usd,
    )
    for route in ("R1", "R2", "OPTIONS_R1"):
        route_key: RouteBucket = route  # type: ignore[assignment]
        share = allocation[route]
        if share == 0.0:
            continue
        _set_bucket_pnl(
            buckets,
            route_key,
            realized_pnl=share,
            win_count=1 if share > 0 else 0,
            loss_count=1 if share < 0 else 0,
        )

    first_date = daily[0].date if daily else "?"
    last_date = daily[-1].date if daily else "?"
    notes.append(
        f"Alpaca realized PnL from EOD equity delta ({first_date}→{last_date}): "
        f"{delta:+.2f} USD allocated by route notional (audit fills lack closed P/L)"
    )
    return notes


def _load_options_r1_buckets(buckets: dict[RouteBucket, RoutePnLBucket], *, limit: int) -> None:
    if not _OPTIONS_DB.exists():
        return
    try:
        import duckdb

        conn = duckdb.connect(str(_OPTIONS_DB), read_only=True)
        rows = conn.execute(
            """
            SELECT symbol, decision, payload_json
            FROM options_strategy_executions
            ORDER BY created_at DESC
            LIMIT ?
            """,
            [limit],
        ).fetchall()
        conn.close()
    except Exception as exc:
        logger.warning("route_pnl.options_audit_failed error=%s", exc)
        return

    for _sym, decision, payload_raw in rows:
        if str(decision).upper() not in {"EXECUTE", "FILLED"}:
            continue
        payload = json.loads(payload_raw) if isinstance(payload_raw, str) else {}
        pnl = float(payload.get("realized_pnl_usd") or 0.0)
        premium = float(payload.get("max_premium_usd") or 0.0)
        _merge_bucket(
            buckets,
            "OPTIONS_R1",
            trades=1,
            executions=1,
            pnl=pnl if pnl != 0.0 else 0.0,
            notional=premium,
            win=pnl > 0 if pnl != 0 else None,
        )


def _load_eod_daily() -> tuple[RoutePnLDailyPoint, ...]:
    if not _EOD_DIR.exists():
        return ()
    points: list[RoutePnLDailyPoint] = []
    for path in sorted(_EOD_DIR.glob("eod_audit_*.json")):
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            logger.warning("route_pnl.eod_read_failed path=%s error=%s", path, exc)
            continue
        date = path.stem.replace("eod_audit_", "")
        alp = data.get("alpaca_balance") or {}
        bx = (data.get("bingx_perp_balance") or {}).get("balance") or {}
        points.append(
            RoutePnLDailyPoint(
                date=date,
                alpaca_equity_usd=float(alp["equity"]) if alp.get("equity") else None,
                bingx_equity_usdt=float(bx["equity"]) if bx.get("equity") else None,
                bingx_unrealized_usdt=(
                    float(bx["unrealizedProfit"]) if bx.get("unrealizedProfit") else None
                ),
            )
        )
    return tuple(points)


def build_route_pnl_dashboard(*, limit: int = 200) -> RoutePnLDashboardResponse:
    """Construye rollup PnL por ruta desde fuentes locales reconciliadas."""
    buckets = _empty_buckets()
    notes: list[str] = []

    _load_alpaca_activity(buckets, limit=limit)
    _load_bingx_audit_activity(buckets, limit=limit)
    _load_options_r1_buckets(buckets, limit=limit)

    notes.extend(_apply_bingx_realized_reconciliation(buckets, limit=limit))

    daily = _load_eod_daily()
    notes.extend(_apply_alpaca_equity_reconciliation(buckets, daily))

    if daily:
        first, last = daily[0], daily[-1]
        if first.alpaca_equity_usd and last.alpaca_equity_usd:
            delta = last.alpaca_equity_usd - first.alpaca_equity_usd
            notes.append(f"Alpaca equity EOD delta ({first.date}→{last.date}): {delta:+.2f} USD")
        if first.bingx_equity_usdt and last.bingx_equity_usdt:
            delta = last.bingx_equity_usdt - first.bingx_equity_usdt
            notes.append(f"BingX equity EOD delta ({first.date}→{last.date}): {delta:+.2f} USDT")

    return RoutePnLDashboardResponse(
        generated_at=datetime.now(tz=UTC).isoformat(),
        buckets=tuple(buckets[r] for r in ("R1", "R2", "BINGX", "OPTIONS_R1")),
        daily=daily,
        notes=tuple(notes),
    )


__all__ = ["build_route_pnl_dashboard"]
