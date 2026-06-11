"""Optional HTTP bridge from Market Scanner candidates to a Nautilus-style sidecar.

The main runtime never imports NautilusTrader. This adapter only posts a
sanitized, simulation-only payload to an explicitly configured sidecar URL.
"""

from __future__ import annotations

import os
import time
from typing import Any

import httpx

from backend.config.logger_setup import get_logger
from backend.domain.market_scanner_models import (
    ScannerExecutionSimRequest,
    ScannerExecutionSimResponse,
)

logger = get_logger(__name__)

_DEFAULT_TIMEOUT_SECONDS = 8.0
_MAX_ERROR_LENGTH = 220
_SECRET_KEY_PARTS = (
    "api_key",
    "apikey",
    "authorization",
    "credential",
    "password",
    "private_key",
    "secret",
    "token",
)


async def run_scanner_execution_sim(
    request: ScannerExecutionSimRequest,
    *,
    transport: httpx.AsyncBaseTransport | None = None,
) -> ScannerExecutionSimResponse:
    """Run an optional execution simulation against a configured sidecar.

    No sidecar means a normal ``unavailable`` response. Sidecar errors degrade
    the response and are logged; they never affect scanner ranking or funding
    gates.
    """
    sidecar_url = os.getenv("NAUTILUS_SCANNER_SIDECAR_URL", "").strip()
    if not sidecar_url:
        return ScannerExecutionSimResponse(
            status="unavailable",
            engine="nautilus_sidecar",
            reason="sidecar_not_configured",
            results=[],
        )

    timeout_seconds = _timeout_seconds()
    payload = _sidecar_payload(request)
    started = time.perf_counter()
    try:
        async with httpx.AsyncClient(timeout=timeout_seconds, transport=transport) as client:
            response = await client.post(sidecar_url, json=payload)
            response.raise_for_status()
            raw = response.json()
    except httpx.TimeoutException as exc:
        error = _summarize_error(exc)
        logger.warning("nautilus_scanner_bridge.timeout error=%s", error)
        return _degraded_response("sidecar_timeout", error, started)
    except (httpx.HTTPError, ValueError, TypeError) as exc:
        error = _summarize_error(exc)
        logger.warning("nautilus_scanner_bridge.failed error=%s", error)
        return _degraded_response("sidecar_failed", error, started)

    return _normalize_sidecar_response(raw, started)


def _timeout_seconds() -> float:
    raw = os.getenv("NAUTILUS_SCANNER_TIMEOUT_SECONDS", "").strip()
    if not raw:
        return _DEFAULT_TIMEOUT_SECONDS
    try:
        value = float(raw)
    except ValueError:
        return _DEFAULT_TIMEOUT_SECONDS
    if value <= 0:
        return _DEFAULT_TIMEOUT_SECONDS
    return min(value, 60.0)


def _sidecar_payload(request: ScannerExecutionSimRequest) -> dict[str, Any]:
    payload = request.model_dump(mode="json", exclude_none=True)
    payload["simulation_only"] = True
    payload["allow_live_orders"] = False
    payload["adapter"] = "quantum_analyzer.market_scanner"
    return _strip_sensitive(payload)


def _normalize_sidecar_response(raw: object, started: float) -> ScannerExecutionSimResponse:
    data = raw if isinstance(raw, dict) else {}
    status = str(data.get("status") or "ok").strip().lower()
    if status not in {"ok", "degraded", "unavailable"}:
        status = "degraded"
    sanitized = _strip_sensitive(data)
    results = _normalize_results(sanitized.get("results"))
    warnings = sanitized.get("warnings") if isinstance(sanitized.get("warnings"), list) else []
    metadata = sanitized.get("metadata") if isinstance(sanitized.get("metadata"), dict) else {}
    return ScannerExecutionSimResponse(
        status=status,  # type: ignore[arg-type]
        engine=str(sanitized.get("engine") or "nautilus_sidecar"),
        reason=str(sanitized.get("reason") or "completed"),
        error=sanitized.get("error") if isinstance(sanitized.get("error"), str) else None,
        results=results,
        warnings=[str(item)[:120] for item in warnings],
        sidecar_latency_ms=_latency_ms(started),
        metadata=metadata,
    )


def _normalize_results(raw_results: object) -> list[dict[str, Any]]:
    if not isinstance(raw_results, list):
        return []
    normalized: list[dict[str, Any]] = []
    for raw in raw_results:
        if not isinstance(raw, dict):
            continue
        result = _strip_sensitive(raw)
        symbol = str(result.get("symbol") or "").upper().strip()
        if symbol:
            result["symbol"] = symbol
        direction = str(result.get("direction") or "").strip().lower()
        if direction in {"buy", "bullish", "up"}:
            direction = "long"
        elif direction in {"sell", "bearish", "down"}:
            direction = "short"
        if direction in {"long", "short"}:
            result["direction"] = direction
        for key in ("slippage_bps", "latency_ms", "estimated_fill_price"):
            if key in result:
                result[key] = _float_or_none(result[key])
        normalized.append({key: value for key, value in result.items() if value is not None})
    return normalized


def _degraded_response(reason: str, error: str, started: float) -> ScannerExecutionSimResponse:
    return ScannerExecutionSimResponse(
        status="degraded",
        engine="nautilus_sidecar",
        reason=reason,
        error=error,
        results=[],
        warnings=[reason],
        sidecar_latency_ms=_latency_ms(started),
    )


def _strip_sensitive(value: object) -> Any:
    if isinstance(value, dict):
        cleaned: dict[str, Any] = {}
        for raw_key, raw_value in value.items():
            key = str(raw_key)
            if _is_sensitive_key(key):
                continue
            cleaned[key] = _strip_sensitive(raw_value)
        return cleaned
    if isinstance(value, list):
        return [_strip_sensitive(item) for item in value]
    return value


def _is_sensitive_key(key: str) -> bool:
    normalized = key.lower().replace("-", "_")
    return any(part in normalized for part in _SECRET_KEY_PARTS)


def _float_or_none(value: object) -> float | None:
    try:
        return float(value) if value is not None else None
    except (TypeError, ValueError):
        return None


def _summarize_error(exc: BaseException) -> str:
    text = str(exc).strip() or exc.__class__.__name__
    return text[:_MAX_ERROR_LENGTH]


def _latency_ms(started: float) -> float:
    return round((time.perf_counter() - started) * 1000.0, 3)
