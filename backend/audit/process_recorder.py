"""Process Recorder — captures full engine/indicator state at decision time.

Central entry point for recording snapshots of the trading engine's internal
state whenever a decision is made.  Each snapshot captures the complete set of
indicators, orderbook, market data, signals, and risk metrics so that
post-trade analytics can reconstruct exactly what the engine "saw".

Usage
-----
::

    from backend.audit.process_recorder import record_process_snapshot

    snapshot_id = await record_process_snapshot(
        module="bingx",
        symbol="MSFT-USDT",
        indicators={"rsi": 65.2, "macd": 0.04, "vwap": 420.5},
        orderbook={"bids": [...], "asks": [...]},
        market_data={"price": 421.0, "volume": 15000},
        signals={"technical": "BUY", "predictive": 0.72},
        decisions={"action": "LONG", "confidence": 0.85},
        risk_metrics={"var_95": -2.3, "max_drawdown": -1.5},
    )
"""

from __future__ import annotations

import asyncio
import traceback
from datetime import UTC, datetime
from typing import Any

from backend.audit.audit_complex_store import AuditComplexStore, ProcessSnapshotEntry
from backend.audit.structured_logger import get_correlation_id, get_structured_logger

logger = get_structured_logger(__name__, module="audit")

# Module-level store (lazy init)
_store: AuditComplexStore | None = None
_store_lock = asyncio.Lock()


async def _get_store() -> AuditComplexStore:
    global _store
    if _store is None:
        async with _store_lock:
            if _store is None:
                from backend.config.settings import load_settings

                settings = load_settings()
                _store = AuditComplexStore(db_path=settings.audit_db_path)
    return _store


def set_process_recorder_store(store: AuditComplexStore) -> None:
    """Inject a store instance (useful for tests)."""
    global _store
    _store = store


async def record_process_snapshot(
    *,
    module: str,
    symbol: str,
    indicators: dict[str, Any],
    orderbook: dict[str, Any] | None = None,
    market_data: dict[str, Any] | None = None,
    signals: dict[str, Any] | None = None,
    decisions: dict[str, Any] | None = None,
    risk_metrics: dict[str, Any] | None = None,
    engine_state: dict[str, Any] | None = None,
    context: dict[str, Any] | None = None,
    operation_id: str = "",
) -> str:
    """Persist a full process snapshot to the audit store.

    Returns the generated ``snapshot_id``.
    """
    store = await _get_store()

    entry = ProcessSnapshotEntry(
        module=module,
        symbol=symbol,
        indicators=indicators,
        orderbook=orderbook or {},
        market_data=market_data or {},
        signals=signals or {},
        decisions=decisions or {},
        risk_metrics=risk_metrics or {},
        engine_state=engine_state or {},
        context=context or {},
        operation_id=operation_id,
        timestamp=datetime.now(UTC).isoformat(),
    )

    snapshot_id = store.persist_process_snapshot(entry)
    logger.debug(
        "Process snapshot recorded",
        tags=["process_recorder", module],
        snapshot_id=snapshot_id,
        symbol=symbol,
        module=module,
    )
    return snapshot_id


async def record_error(
    *,
    module: str,
    error_type: str,
    message: str,
    severity: str = "error",
    exc: BaseException | None = None,
    context: dict[str, Any] | None = None,
) -> str:
    """Persist an error to the audit_errors table.

    Returns the generated ``error_id``.
    """
    from backend.audit.audit_complex_store import ErrorAuditEntry

    store = await _get_store()

    stack = ""
    if exc is not None:
        stack = "".join(traceback.format_exception(type(exc), exc, exc.__traceback__))

    entry = ErrorAuditEntry(
        module=module,
        severity=severity,
        error_type=error_type,
        message=message,
        stack_trace=stack,
        context=context or {},
        correlation_id=get_correlation_id() or "",
    )

    error_id = store.persist_error(entry)
    logger.error(
        "Error recorded to audit",
        tags=["audit_error", module, severity],
        error_id=error_id,
        error_type=error_type,
    )
    return error_id


async def record_api_call(
    *,
    module: str,
    provider: str,
    endpoint: str,
    status: str = "success",
    duration_ms: float = 0.0,
    estimated_cost: float = 0.0,
    api_key_label: str = "default",
    cache_hit: bool = False,
    bytes_received: int = 0,
    retry_count: int = 0,
    error_message: str = "",
    error_stack: str = "",
    request_context: dict[str, Any] | None = None,
) -> str:
    """Persist an API call record to the audit_api_calls table.

    Returns the generated ``call_id``.
    """
    from backend.audit.audit_complex_store import ApiCallAuditEntry

    store = await _get_store()

    entry = ApiCallAuditEntry(
        module=module,
        provider=provider,
        endpoint=endpoint,
        status=status,
        duration_ms=duration_ms,
        estimated_cost=estimated_cost,
        api_key_label=api_key_label,
        cache_hit=cache_hit,
        bytes_received=bytes_received,
        retry_count=retry_count,
        error_message=error_message,
        error_stack=error_stack,
        request_context=request_context or {},
        correlation_id=get_correlation_id() or "",
    )

    call_id = store.persist_api_call(entry)
    return call_id


__all__ = [
    "record_api_call",
    "record_error",
    "record_process_snapshot",
    "set_process_recorder_store",
]
