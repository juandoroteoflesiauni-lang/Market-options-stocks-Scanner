"""Reporte EOD de TCA: slippage bps vs arrival por ruta. # [PD-3][TH]"""

from __future__ import annotations

import statistics
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from backend.services.trade_journal_eod import _trade_day_key


def _route_bucket(trade: dict[str, Any]) -> str:
    route = str(trade.get("route") or "").strip().upper()
    if route:
        return route
    symbol = str(trade.get("symbol") or "")
    if symbol.endswith("-USDT"):
        return "BINGX"
    return "UNKNOWN"


def _is_bps(trade: dict[str, Any]) -> float | None:
    raw = trade.get("implementation_shortfall_bps")
    if raw is None:
        tca = trade.get("engine_decision_payload")
        if isinstance(tca, dict):
            nested = tca.get("tca")
            if isinstance(nested, dict):
                raw = nested.get("implementation_shortfall_bps")
    try:
        return float(raw) if raw is not None else None
    except (TypeError, ValueError):
        return None


def _aggregate_route(trades: list[dict[str, Any]]) -> dict[str, Any]:
    bps_values = [b for b in (_is_bps(t) for t in trades) if b is not None]
    slip_usd = sum(float(t.get("slippage_usd") or 0.0) for t in trades)
    delays = [int(t.get("delay_ms") or 0) for t in trades if t.get("delay_ms") is not None]
    notional = sum(float(t.get("notional_usdt") or t.get("notional_usd") or 0.0) for t in trades)
    return {
        "trade_count": len(trades),
        "avg_is_bps": round(statistics.mean(bps_values), 2) if bps_values else None,
        "median_is_bps": round(statistics.median(bps_values), 2) if bps_values else None,
        "p95_is_bps": (
            round(sorted(bps_values)[max(0, int(len(bps_values) * 0.95) - 1)], 2)
            if len(bps_values) >= 2
            else (round(bps_values[0], 2) if bps_values else None)
        ),
        "total_slippage_usd": round(slip_usd, 4),
        "avg_delay_ms": round(statistics.mean(delays), 0) if delays else 0,
        "notional_usd": round(notional, 2),
        "symbols": sorted({str(t.get("symbol")) for t in trades if t.get("symbol")}),
    }


def build_tca_eod_report(
    db_path: str | Path,
    *,
    limit: int = 500,
) -> dict[str, Any]:
    """Agrega TCA del día desde trade_journal."""
    from backend.services.trade_journal_service import list_trades

    path = Path(db_path)
    if not path.exists():
        return {"status": "missing_db", "generated_at": datetime.now(tz=UTC).isoformat()}

    trades = list_trades(path, limit=limit)
    today = datetime.now(tz=UTC).date().isoformat()
    today_trades = [t for t in trades if _trade_day_key(t) == today]
    with_tca = [t for t in today_trades if _is_bps(t) is not None]

    by_route: dict[str, list[dict[str, Any]]] = {}
    for trade in with_tca:
        bucket = _route_bucket(trade)
        by_route.setdefault(bucket, []).append(trade)

    routes = {route: _aggregate_route(rows) for route, rows in by_route.items()}
    all_bps = [b for b in (_is_bps(t) for t in with_tca) if b is not None]

    return {
        "generated_at": datetime.now(tz=UTC).isoformat(),
        "session_date": today,
        "trades_with_tca": len(with_tca),
        "trades_today_total": len(today_trades),
        "portfolio_avg_is_bps": round(statistics.mean(all_bps), 2) if all_bps else None,
        "portfolio_total_slippage_usd": round(
            sum(float(t.get("slippage_usd") or 0.0) for t in with_tca), 4
        ),
        "by_route": routes,
        "interpretation": (
            "implementation_shortfall_bps > 0 = ejecución peor que precio de decisión (coste)"
        ),
    }


__all__ = ["build_tca_eod_report"]
