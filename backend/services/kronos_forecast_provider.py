"""Experimental optional Kronos-style OHLCV forecast provider for Scanner Phase B.

The module is intentionally dependency-light at import time. Any external Kronos
runtime must be installed separately and is imported lazily inside forecast_ohlcv.
"""

from __future__ import annotations

import importlib
import math
import os
from typing import Any, cast

from backend.config.logger_setup import get_logger
from backend.services.scanner_external_contracts import (
    ExternalResultStatus,
    ForecastDirection,
    ForecastEvidence,
    unavailable_result,
)

logger = get_logger(__name__)

_ENGINE_NAME = "kronos"
_SUPPORTED_DEVICES = {"cpu", "cuda"}
_MIN_BARS = 30


def forecast_ohlcv(symbol: str, timeframe: str, bars: list[dict]) -> ForecastEvidence:
    """Return normalized experimental forecast evidence.

    If the feature flag is disabled, no model is configured, or the optional
    runtime is not installed, the provider returns a structured unavailable
    evidence object instead of raising.
    """
    sym = str(symbol or "").upper().strip()
    tf = str(timeframe or "").strip()
    clean_bars = [_compact_bar(row) for row in bars or []]
    clean_bars = [row for row in clean_bars if row is not None]

    metadata = {
        "bars_count": len(clean_bars),
        "device": _device(),
    }

    if os.getenv("SCANNER_FORECAST_ENGINE", "none").strip().lower() != _ENGINE_NAME:
        return _unavailable(sym, tf, "forecast_engine_disabled", metadata)

    model_name = os.getenv("KRONOS_MODEL_NAME", "").strip()
    if not model_name:
        return _unavailable(sym, tf, "model_not_configured", metadata)

    if len(clean_bars) < _MIN_BARS:
        return ForecastEvidence(
            engine=_ENGINE_NAME,
            status="insufficient_data",
            reason=f"requires_at_least_{_MIN_BARS}_bars",
            symbol=sym,
            timeframe=tf,
            confidence=0.0,
            data_quality_score=0.0,
            metadata={**metadata, "model_name": model_name},
            model_name=model_name,
        )

    try:
        kronos = importlib.import_module("kronos")
    except Exception as exc:
        logger.debug("kronos_forecast.import_unavailable error=%s", str(exc)[:160])
        return _unavailable(sym, tf, "model_not_installed", {**metadata, "model_name": model_name})

    entrypoint = getattr(kronos, "forecast_ohlcv", None)
    if not callable(entrypoint):
        return _unavailable(
            sym,
            tf,
            "adapter_entrypoint_missing",
            {**metadata, "model_name": model_name},
        )

    try:
        raw = entrypoint(
            symbol=sym,
            timeframe=tf,
            bars=clean_bars,
            model_name=model_name,
            device=metadata["device"],
        )
    except Exception as exc:
        logger.warning(
            "kronos_forecast.failed symbol=%s timeframe=%s error=%s",
            sym,
            tf,
            str(exc)[:180],
        )
        return _unavailable(sym, tf, f"forecast_failed:{type(exc).__name__}", metadata)

    return _coerce_forecast(raw, symbol=sym, timeframe=tf, model_name=model_name, metadata=metadata)


def _unavailable(
    symbol: str,
    timeframe: str,
    reason: str,
    metadata: dict[str, Any],
) -> ForecastEvidence:
    return ForecastEvidence(
        **unavailable_result(_ENGINE_NAME, reason),
        symbol=symbol,
        timeframe=timeframe,
        metadata=metadata,
        model_name=str(metadata.get("model_name") or "") or None,
    )


def _device() -> str:
    raw = os.getenv("KRONOS_DEVICE", "cpu").strip().lower()
    return raw if raw in _SUPPORTED_DEVICES else "cpu"


def _compact_bar(row: dict) -> dict[str, float | int] | None:
    if not isinstance(row, dict):
        return None
    try:
        close = _positive_float(row.get("close", row.get("c")))
        if close is None:
            return None
        open_price = _positive_float(row.get("open", row.get("o"))) or close
        high = _positive_float(row.get("high", row.get("h"))) or max(open_price, close)
        low = _positive_float(row.get("low", row.get("l"))) or min(open_price, close)
        volume = _non_negative_float(row.get("volume", row.get("v"))) or 0.0
    except (TypeError, ValueError):
        return None
    out: dict[str, float | int] = {
        "open": open_price,
        "high": high,
        "low": low,
        "close": close,
        "volume": volume,
    }
    timestamp = row.get("t", row.get("time", row.get("timestamp")))
    if isinstance(timestamp, int | float) and math.isfinite(float(timestamp)):
        out["t"] = int(timestamp)
    return out


def _positive_float(value: object) -> float | None:
    number = _finite_float(value)
    return number if number is not None and number > 0.0 else None


def _non_negative_float(value: object) -> float | None:
    number = _finite_float(value)
    return number if number is not None and number >= 0.0 else None


def _finite_float(value: object) -> float | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def _coerce_forecast(
    raw: object,
    *,
    symbol: str,
    timeframe: str,
    model_name: str,
    metadata: dict[str, Any],
) -> ForecastEvidence:
    payload = raw.model_dump(mode="json") if hasattr(raw, "model_dump") else raw
    if not isinstance(payload, dict):
        return _unavailable(symbol, timeframe, "bad_provider_payload", metadata)

    direction = str(
        payload.get("forecast_direction") or payload.get("direction") or "unavailable"
    ).lower()
    if direction not in {"bullish", "bearish", "neutral"}:
        direction = "unavailable"

    expected_return = _finite_float(payload.get("expected_return_pct", payload.get("return_pct")))
    volatility = _finite_float(
        payload.get("forecast_volatility_pct", payload.get("volatility_pct"))
    )
    path_dispersion = _finite_float(payload.get("path_dispersion"))
    confidence = _finite_float(payload.get("confidence")) or 0.0
    data_quality = _finite_float(payload.get("data_quality_score")) or confidence
    scenarios = payload.get("scenarios") if isinstance(payload.get("scenarios"), dict) else {}
    extra_metadata = payload.get("metadata") if isinstance(payload.get("metadata"), dict) else {}

    return ForecastEvidence(
        engine=str(payload.get("engine") or _ENGINE_NAME),
        status=_coerce_status(payload.get("status")),
        reason=str(payload.get("reason") or "ok"),
        symbol=symbol,
        timeframe=timeframe,
        horizon=str(payload.get("horizon") or "next_bar"),
        forecast_direction=cast(ForecastDirection, direction),
        expected_return_pct=expected_return,
        forecast_volatility_pct=volatility,
        path_dispersion=path_dispersion,
        scenarios=scenarios,
        confidence=confidence,
        data_quality_score=data_quality,
        warnings=(
            [str(item) for item in payload.get("warnings", []) if item]
            if isinstance(payload.get("warnings"), list)
            else []
        ),
        metadata={**metadata, **extra_metadata, "model_name": model_name},
        model_name=str(payload.get("model_name") or model_name),
    )


def _coerce_status(value: object) -> ExternalResultStatus:
    status = str(value or "available").strip().lower()
    if status in {"available", "partial", "unavailable", "insufficient_data", "error"}:
        return cast(ExternalResultStatus, status)
    return "available"
