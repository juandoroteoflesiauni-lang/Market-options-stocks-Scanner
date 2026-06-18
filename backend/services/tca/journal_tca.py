"""Persistencia TCA en trade_journal (BingX + Alpaca). # [PD-3][TH]"""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from backend.config.logger_setup import get_logger
from backend.services.tca.implementation_shortfall import (
    TcaExecutionMetrics,
    compute_implementation_shortfall,
)
from backend.services.trade_journal_service import (
    TradeJournalEntry,
    persist_trade_execution,
    persist_trade_execution_jsonl,
)

logger = get_logger(__name__)

_DEFAULT_JOURNAL = Path("data/quantum_analyzer.duckdb")
_DEFAULT_JSONL = Path("backend/logs/trades")


def resolve_arrival_price(
    *,
    decision_price: float | None,
    reference_price: float | None,
    fill_price: float,
) -> float:
    """Precio de decisión (arrival) con fallback seguro."""
    for candidate in (decision_price, reference_price):
        if candidate is not None and candidate > 0:
            return float(candidate)
    return float(fill_price)


def metrics_to_journal_fields(metrics: TcaExecutionMetrics) -> dict[str, Any]:
    """Campos TCA para ``TradeJournalEntry`` y DuckDB."""
    return {
        "route": metrics.route,
        "decision_price": metrics.decision_price,
        "decision_timestamp": metrics.decision_timestamp,
        "fill_price": metrics.fill_price,
        "implementation_shortfall_bps": metrics.implementation_shortfall_bps,
        "slippage_usd": metrics.slippage_usd,
        "delay_ms": metrics.delay_ms,
        "fill_rate": metrics.fill_rate,
    }


def persist_equity_tca_execution(
    *,
    symbol: str,
    side: str,
    quantity: float,
    decision_price: float,
    fill_price: float,
    route: str,
    cycle_id: str,
    venue_order_id: str | None,
    dry_run: bool,
    decision_timestamp: str | None = None,
    execution_timestamp: str | None = None,
    notional_usd: float | None = None,
    decision_score: float = 0.0,
    correlation_id: str = "",
    db_path: Path | str = _DEFAULT_JOURNAL,
) -> bool:
    """Registra ejecución Alpaca con TCA en trade_journal."""
    exec_ts = execution_timestamp or datetime.now(tz=UTC).isoformat()
    arrival = resolve_arrival_price(
        decision_price=decision_price,
        reference_price=None,
        fill_price=fill_price,
    )
    fill = fill_price if fill_price > 0 else arrival
    metrics = compute_implementation_shortfall(
        route=route,
        side=side,
        quantity=quantity,
        decision_price=arrival,
        fill_price=fill,
        decision_timestamp=decision_timestamp,
        execution_timestamp=exec_ts,
    )
    notional = notional_usd if notional_usd is not None else arrival * quantity
    entry = TradeJournalEntry(
        execution_timestamp=exec_ts,
        symbol=symbol.upper(),
        side=side.upper(),
        quantity=quantity,
        notional_usdt=notional,
        entry_price=fill,
        decision_score=max(0.0, min(1.0, decision_score)),
        reason_codes=["tca_equity_execution"],
        venue_order_id=venue_order_id,
        realized_pnl=0.0,
        institutional_research_snapshot={"source": "alpaca_equity_tca"},
        engine_decision_payload={"tca": metrics_to_journal_fields(metrics)},
        dry_run=dry_run,
        cycle_id=cycle_id or "unknown",
        correlation_id=correlation_id,
        **metrics_to_journal_fields(metrics),
    )
    ok = persist_trade_execution(entry, db_path)
    if ok:
        persist_trade_execution_jsonl(entry, _DEFAULT_JSONL)
    return ok


__all__ = [
    "metrics_to_journal_fields",
    "persist_equity_tca_execution",
    "resolve_arrival_price",
]
