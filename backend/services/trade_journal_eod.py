"""Resumen EOD del trade journal con mapeo de columnas reales (F7). # [PD-3][TH]"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any


def _trade_day_key(trade: dict[str, Any]) -> str:
    for field in ("execution_timestamp", "closed_at", "opened_at", "_created_at"):
        raw = trade.get(field)
        if raw:
            return str(raw)[:10]
    return ""


def summarize_trade_journal_today(trades: list[dict[str, Any]]) -> dict[str, Any]:
    """Agrega trades del día usando columnas del schema DuckDB real."""
    today = datetime.now(tz=UTC).date().isoformat()
    today_trades = [t for t in trades if _trade_day_key(t) == today]

    realized = 0.0
    notional = 0.0
    dry_run_count = 0
    for trade in today_trades:
        realized += float(trade.get("realized_pnl") or trade.get("realized_pnl_usd") or 0.0)
        notional += float(trade.get("notional_usdt") or trade.get("notional_usd") or 0.0)
        if bool(trade.get("dry_run")):
            dry_run_count += 1

    symbols = sorted({str(t.get("symbol")) for t in today_trades if t.get("symbol")})
    return {
        "trades_today": len(today_trades),
        "realized_pnl_usdt_today": round(realized, 4),
        "realized_pnl_usd_today": round(realized, 4),
        "notional_usdt_today": round(notional, 2),
        "dry_run_count_today": dry_run_count,
        "symbols": symbols,
    }


__all__ = ["summarize_trade_journal_today"]
