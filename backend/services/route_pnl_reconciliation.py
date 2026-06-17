"""Reconciliación de PnL realizado por ruta (fills cerrados, journal, EOD). # [PD-3][TH]"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from backend.layer_1_data.datos.bingx_fill_price import resolve_fill_price_from_row

logger = logging.getLogger(__name__)

_REPORTS_DIR = Path("reports")
_JOURNAL_DB = Path("data/quantum_analyzer.duckdb")


@dataclass(frozen=True)
class RealizedPnLRollup:
    """PnL realizado agregado para un bucket."""

    realized_pnl: float = 0.0
    close_count: int = 0
    win_count: int = 0
    loss_count: int = 0
    source: str = "unknown"


def bingx_fill_dedupe_key(row: dict[str, Any]) -> str:
    """Clave estable para deduplicar fills BingX entre fuentes."""
    return "|".join(
        str(row.get(key) or "")
        for key in ("venue_order_id", "client_order_id", "symbol", "executed_at_utc", "filled_qty")
    )


def _closed_pnl_from_row(row: dict[str, Any]) -> float | None:
    for key in (
        "closed_pnl_vst",
        "closed_pnl_usdt",
        "realized_pnl",
        "realisedPNL",
        "profit",
        "realizedProfit",
    ):
        raw = row.get(key)
        if raw in (None, ""):
            continue
        try:
            return float(raw)
        except (TypeError, ValueError):
            continue
    return None


def _notional_from_fill_row(row: dict[str, Any]) -> float:
    qty_raw = row.get("filled_qty") or row.get("quantity") or row.get("qty")
    try:
        qty = float(qty_raw or 0.0)
    except (TypeError, ValueError):
        qty = 0.0
    price = resolve_fill_price_from_row(row) or row.get("avg_price")
    try:
        px = float(price or 0.0)
    except (TypeError, ValueError):
        px = 0.0
    if qty <= 0 or px <= 0:
        return 0.0
    return round(qty * px, 2)


def rollup_realized_from_rows(
    rows: list[dict[str, Any]],
    *,
    source: str,
) -> RealizedPnLRollup:
    """Suma PnL cerrado y win/loss desde filas con campo de PnL."""
    realized = 0.0
    wins = 0
    losses = 0
    closes = 0
    for row in rows:
        pnl = _closed_pnl_from_row(row)
        if pnl is None:
            continue
        realized += pnl
        if pnl == 0.0:
            continue
        closes += 1
        if pnl > 0:
            wins += 1
        else:
            losses += 1
    return RealizedPnLRollup(
        realized_pnl=round(realized, 4),
        close_count=closes,
        win_count=wins,
        loss_count=losses,
        source=source,
    )


def load_bingx_exchange_fills_from_reports(
    *,
    limit: int = 500,
) -> list[dict[str, Any]]:
    """Carga fills deduplicados desde ``reports/bingx_bot_operations_*.json``."""
    if not _REPORTS_DIR.exists():
        return []
    fills: list[dict[str, Any]] = []
    seen: set[str] = set()
    paths = sorted(_REPORTS_DIR.glob("bingx_bot_operations_*.json"), reverse=True)
    for path in paths:
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            logger.warning("route_pnl.bingx_report_read_failed path=%s error=%s", path, exc)
            continue
        block = data.get("exchange_orders_filled") or []
        if not isinstance(block, list):
            continue
        for row in block:
            if not isinstance(row, dict):
                continue
            key = bingx_fill_dedupe_key(row)
            if key in seen:
                continue
            seen.add(key)
            fills.append(row)
            if len(fills) >= limit:
                return fills
    return fills


def reconcile_bingx_realized_pnl(
    *,
    journal_trades: list[dict[str, Any]] | None = None,
    report_limit: int = 500,
) -> tuple[RealizedPnLRollup, list[dict[str, Any]]]:
    """PnL BingX: reports (exchange fills) + journal sin doble conteo."""
    report_fills = load_bingx_exchange_fills_from_reports(limit=report_limit)
    report_keys = {bingx_fill_dedupe_key(row) for row in report_fills}
    report_rollup = rollup_realized_from_rows(report_fills, source="bingx_exchange_reports")

    journal_rows: list[dict[str, Any]] = []
    if journal_trades:
        for trade in journal_trades:
            pnl = float(trade.get("realized_pnl") or 0.0)
            if pnl == 0.0:
                continue
            key = "|".join(
                (
                    str(trade.get("venue_order_id") or ""),
                    str(trade.get("client_order_id") or ""),
                    str(trade.get("symbol") or ""),
                    str(trade.get("execution_timestamp") or ""),
                    str(trade.get("quantity") or ""),
                )
            )
            if key in report_keys:
                continue
            journal_rows.append(trade)

    journal_rollup = rollup_realized_from_rows(
        [{"realized_pnl": t.get("realized_pnl")} for t in journal_rows],
        source="trade_journal",
    )

    combined = RealizedPnLRollup(
        realized_pnl=round(report_rollup.realized_pnl + journal_rollup.realized_pnl, 4),
        close_count=report_rollup.close_count + journal_rollup.close_count,
        win_count=report_rollup.win_count + journal_rollup.win_count,
        loss_count=report_rollup.loss_count + journal_rollup.loss_count,
        source="bingx_reports+journal" if journal_rollup.close_count else "bingx_exchange_reports",
    )
    return combined, report_fills


def bingx_activity_from_fills(fills: list[dict[str, Any]]) -> tuple[int, int, float]:
    """trade_count, execution_count, notional_usd desde fills FILLED."""
    if not fills:
        return 0, 0, 0.0
    notional = 0.0
    for row in fills:
        notional += _notional_from_fill_row(row)
    count = len(fills)
    return count, count, round(notional, 2)


def allocate_alpaca_equity_delta(
    *,
    total_delta_usd: float,
    r1_notional: float,
    r2_notional: float,
    options_notional: float,
) -> dict[str, float]:
    """Distribuye delta de equity Alpaca por notional de ruta (sin P/L por fill)."""
    weights = {
        "R1": max(r1_notional, 0.0),
        "R2": max(r2_notional, 0.0),
        "OPTIONS_R1": max(options_notional, 0.0),
    }
    total_weight = sum(weights.values())
    if total_weight <= 0:
        return {"R1": total_delta_usd, "R2": 0.0, "OPTIONS_R1": 0.0}
    return {
        route: round(total_delta_usd * (weight / total_weight), 4)
        for route, weight in weights.items()
    }


def alpaca_equity_delta_from_daily(
    daily: tuple[Any, ...],
) -> float | None:
    """Delta de equity Alpaca entre primer y último punto EOD."""
    if len(daily) < 2:
        return None
    first, last = daily[0], daily[-1]
    first_eq = getattr(first, "alpaca_equity_usd", None)
    last_eq = getattr(last, "alpaca_equity_usd", None)
    if first_eq is None or last_eq is None:
        return None
    return round(float(last_eq) - float(first_eq), 4)


__all__ = [
    "RealizedPnLRollup",
    "allocate_alpaca_equity_delta",
    "alpaca_equity_delta_from_daily",
    "bingx_activity_from_fills",
    "bingx_fill_dedupe_key",
    "load_bingx_exchange_fills_from_reports",
    "reconcile_bingx_realized_pnl",
    "rollup_realized_from_rows",
]
