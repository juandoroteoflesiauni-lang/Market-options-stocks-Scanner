"""Audit Hooks — lightweight integration points for capturing audit data.

Provides helper functions that existing modules call to record:
* Process snapshots (engine state at decision time)
* API calls (with module tag for per-module tracking)
* Errors (with full stack trace and context)

These helpers are designed to be **non-blocking** and **never raise** — audit
failures must never break the trading pipeline.

Usage
-----
::

    from backend.audit.hooks import (
        audit_decision_snapshot,
        audit_api_call,
        audit_error,
    )

    # After a decision is made:
    await audit_decision_snapshot(
        module="bingx",
        symbol="MSFT-USDT",
        analysis=candidate_analysis,
        decision=bingx_decision,
    )
"""

from __future__ import annotations

import traceback
from typing import Any

from backend.audit.structured_logger import get_correlation_id, get_structured_logger

logger = get_structured_logger(__name__, module="audit")

# ── Lazy store accessor (avoids circular imports) ────────────────────────────

_store: Any = None


def _get_store() -> Any:
    global _store
    if _store is None:
        from backend.audit.audit_complex_store import AuditComplexStore
        from backend.config.settings import load_settings

        settings = load_settings()
        _store = AuditComplexStore(db_path=settings.audit_db_path)
    return _store


# ═══════════════════════════════════════════════════════════════════════════════
# Decision Snapshot Hook
# ═══════════════════════════════════════════════════════════════════════════════


async def audit_decision_snapshot(
    *,
    module: str,
    symbol: str,
    indicators: dict[str, Any],
    market_data: dict[str, Any] | None = None,
    signals: dict[str, Any] | None = None,
    decisions: dict[str, Any] | None = None,
    risk_metrics: dict[str, Any] | None = None,
    orderbook: dict[str, Any] | None = None,
    engine_state: dict[str, Any] | None = None,
    operation_id: str = "",
) -> str | None:
    """Capture a full process snapshot at decision time.

    Returns the snapshot_id or ``None`` if capture failed.
    """
    try:
        from backend.audit.audit_complex_store import ProcessSnapshotEntry

        store = _get_store()
        entry = ProcessSnapshotEntry(
            module=module,
            symbol=symbol,
            indicators=indicators,
            market_data=market_data or {},
            signals=signals or {},
            decisions=decisions or {},
            risk_metrics=risk_metrics or {},
            orderbook=orderbook or {},
            engine_state=engine_state or {},
            operation_id=operation_id,
            correlation_id=get_correlation_id() or "",
        )
        result = store.persist_process_snapshot(entry)
        return str(result) if result is not None else None
    except Exception:
        return None


def audit_decision_snapshot_sync(
    *,
    module: str,
    symbol: str,
    indicators: dict[str, Any],
    market_data: dict[str, Any] | None = None,
    signals: dict[str, Any] | None = None,
    decisions: dict[str, Any] | None = None,
    risk_metrics: dict[str, Any] | None = None,
    orderbook: dict[str, Any] | None = None,
    engine_state: dict[str, Any] | None = None,
    operation_id: str = "",
) -> str | None:
    """Synchronous version of audit_decision_snapshot for non-async contexts."""
    try:
        from backend.audit.audit_complex_store import ProcessSnapshotEntry

        store = _get_store()
        entry = ProcessSnapshotEntry(
            module=module,
            symbol=symbol,
            indicators=indicators,
            market_data=market_data or {},
            signals=signals or {},
            decisions=decisions or {},
            risk_metrics=risk_metrics or {},
            orderbook=orderbook or {},
            engine_state=engine_state or {},
            operation_id=operation_id,
            correlation_id=get_correlation_id() or "",
        )
        result = store.persist_process_snapshot(entry)
        return str(result) if result is not None else None
    except Exception:
        return None


# ═══════════════════════════════════════════════════════════════════════════════
# API Call Hook
# ═══════════════════════════════════════════════════════════════════════════════


async def audit_api_call(
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
) -> str | None:
    """Record an API call to the audit store.

    Returns the call_id or ``None`` if recording failed.
    """
    try:
        from backend.audit.audit_complex_store import ApiCallAuditEntry

        store = _get_store()
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
        result = store.persist_api_call(entry)
        return str(result) if result is not None else None
    except Exception:
        return None


# ═══════════════════════════════════════════════════════════════════════════════
# Error Hook
# ═══════════════════════════════════════════════════════════════════════════════


async def audit_error(
    *,
    module: str,
    error_type: str,
    message: str,
    severity: str = "error",
    exc: BaseException | None = None,
    context: dict[str, Any] | None = None,
) -> str | None:
    """Record an error to the audit store with full stack trace.

    Returns the error_id or ``None`` if recording failed.
    """
    try:
        from backend.audit.audit_complex_store import ErrorAuditEntry

        store = _get_store()
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
        result = store.persist_error(entry)
        return str(result) if result is not None else None
    except Exception:
        return None


# ═══════════════════════════════════════════════════════════════════════════════
# BingX-specific helpers
# ═══════════════════════════════════════════════════════════════════════════════


def extract_bingx_indicators(analysis: Any) -> dict[str, Any]:
    """Extract indicator values from a BingXCandidateAnalysis for audit."""
    indicators: dict[str, Any] = {}
    try:
        tech = getattr(analysis, "technical", None)
        if tech:
            for attr in (
                "rsi",
                "macd",
                "macd_signal",
                "macd_hist",
                "vwap",
                "atr",
                "adx",
                "cci",
                "mfi",
                "obv",
                "bollinger_upper",
                "bollinger_lower",
                "bollinger_mid",
                "ema_9",
                "ema_21",
                "ema_50",
                "sma_200",
                "stoch_k",
                "stoch_d",
                "williams_r",
                "volume_sma",
                "volume_ratio",
            ):
                val = getattr(tech, attr, None)
                if val is not None:
                    indicators[attr] = val
            # Include signal summary if available
            consensus = getattr(tech, "consensus", None)
            if consensus:
                indicators["technical_consensus"] = consensus
    except Exception:
        pass
    return indicators


def extract_bingx_signals(analysis: Any, decision: Any) -> dict[str, Any]:
    """Extract signal data from analysis and decision for audit."""
    signals: dict[str, Any] = {}
    try:
        # Predictive signal
        pred = getattr(analysis, "predictive", None)
        if pred:
            signals["predictive_direction"] = getattr(pred, "direction", None)
            signals["predictive_confidence"] = getattr(pred, "confidence", None)
            signals["predictive_probability"] = getattr(pred, "probability", None)

        # Options signal
        opts = getattr(analysis, "options", None)
        if opts:
            signals["options_direction"] = getattr(opts, "direction", None)
            signals["options_iv_rank"] = getattr(opts, "iv_rank", None)
            signals["options_put_call_ratio"] = getattr(opts, "put_call_ratio", None)

        # L2 signal
        l2 = getattr(analysis, "l2", None)
        if l2:
            signals["l2_quality_score"] = getattr(l2, "quality_score", None)
            signals["l2_spread_pct"] = getattr(l2, "spread_pct", None)
            signals["l2_bid_depth"] = getattr(l2, "bid_depth", None)
            signals["l2_ask_depth"] = getattr(l2, "ask_depth", None)

        # Decision
        if decision:
            signals["decision_status"] = getattr(decision, "decision", None)
            signals["decision_direction"] = getattr(decision, "direction", None)
            signals["decision_confidence"] = getattr(decision, "confidence", None)
            signals["decision_score_total"] = getattr(decision, "score_total", None)
    except Exception:
        pass
    return signals


def extract_bingx_decision_data(decision: Any) -> dict[str, Any]:
    """Extract decision data for audit."""
    data: dict[str, Any] = {}
    try:
        if decision is None:
            return data
        for attr in (
            "symbol",
            "decision",
            "direction",
            "confidence",
            "score_total",
            "reason_codes",
        ):
            val = getattr(decision, attr, None)
            if val is not None:
                data[attr] = val
        # Module scores
        ms = getattr(decision, "module_scores", None)
        if ms:
            data["module_scores"] = {
                "venue": getattr(ms, "venue", 0),
                "technical": getattr(ms, "technical", 0),
                "options": getattr(ms, "options", 0),
                "predictive": getattr(ms, "predictive", 0),
                "l2": getattr(ms, "l2", 0),
                "risk": getattr(ms, "risk", 0),
            }
    except Exception:
        pass
    return data


def extract_bingx_market_data(analysis: Any) -> dict[str, Any]:
    """Extract market data snapshot from analysis for audit."""
    data: dict[str, Any] = {}
    try:
        venue = getattr(analysis, "venue", None)
        if venue:
            data["price"] = getattr(venue, "price", None)
            data["volume_24h"] = getattr(venue, "volume_24h", None)
            data["change_24h_pct"] = getattr(venue, "change_24h_pct", None)
            data["market_type"] = getattr(venue, "market_type", None)

        l2 = getattr(analysis, "l2", None)
        if l2:
            data["best_bid"] = getattr(l2, "best_bid", None)
            data["best_ask"] = getattr(l2, "best_ask", None)
            data["spread_pct"] = getattr(l2, "spread_pct", None)
    except Exception:
        pass
    return data


async def audit_bingx_decision(
    *,
    analysis: Any,
    decision: Any,
    operation_id: str = "",
) -> str | None:
    """High-level hook: capture a full BingX decision snapshot.

    Extracts indicators, signals, market data, and decision data from the
    analysis and decision objects and persists them as a process snapshot.
    """
    raw_symbol = getattr(analysis, "venue_symbol", None) or getattr(
        analysis, "underlying_symbol", "UNKNOWN"
    )
    symbol = str(raw_symbol) if raw_symbol else "UNKNOWN"
    return await audit_decision_snapshot(
        module="bingx",
        symbol=symbol,
        indicators=extract_bingx_indicators(analysis),
        market_data=extract_bingx_market_data(analysis),
        signals=extract_bingx_signals(analysis, decision),
        decisions=extract_bingx_decision_data(decision),
        operation_id=operation_id,
    )


# ═══════════════════════════════════════════════════════════════════════════════
# Scanner-specific helpers
# ═══════════════════════════════════════════════════════════════════════════════


def extract_scanner_indicators(row: Any) -> dict[str, Any]:
    """Extract indicator values from a scanner result row for audit."""
    indicators: dict[str, Any] = {}
    try:
        if isinstance(row, dict):
            # Scanner rows are dicts with indicator values
            for key in (
                "rsi",
                "macd",
                "macd_signal",
                "vwap",
                "atr",
                "adx",
                "volume_sma",
                "volume_ratio",
                "obv",
                "mfi",
                "cci",
                "phase_a_score",
                "phase_b_score",
                "composite_score",
                "momentum_score",
                "volatility_score",
            ):
                if key in row and row[key] is not None:
                    indicators[key] = row[key]
        else:
            # Object-style access
            for attr in (
                "rsi",
                "macd",
                "vwap",
                "atr",
                "adx",
                "phase_a_score",
                "phase_b_score",
                "composite_score",
            ):
                val = getattr(row, attr, None)
                if val is not None:
                    indicators[attr] = val
    except Exception:
        pass
    return indicators


async def audit_scanner_result(
    *,
    symbol: str,
    row: Any,
    phase: str = "",
    score: float = 0.0,
) -> str | None:
    """Capture a scanner result snapshot for audit."""
    indicators = extract_scanner_indicators(row)
    if phase:
        indicators["phase"] = phase
    if score:
        indicators["score"] = score

    return await audit_decision_snapshot(
        module="scanner",
        symbol=symbol,
        indicators=indicators,
        decisions={"phase": phase, "score": score},
    )


__all__ = [
    "audit_api_call",
    "audit_bingx_decision",
    "audit_decision_snapshot",
    "audit_decision_snapshot_sync",
    "audit_error",
    "audit_scanner_result",
    "extract_bingx_decision_data",
    "extract_bingx_indicators",
    "extract_bingx_market_data",
    "extract_bingx_signals",
    "extract_scanner_indicators",
]
