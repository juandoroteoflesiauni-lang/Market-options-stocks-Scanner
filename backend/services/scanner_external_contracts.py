from __future__ import annotations
from typing import Literal, Any
"""Serializable contracts for optional Market Scanner external integrations.

These models are intentionally dependency-free. External engines may adapt their
outputs into these contracts, but importing this module must never import heavy
runtime libraries or authorize scanner/funding decisions.
"""


import math

from pydantic import BaseModel, ConfigDict, Field, field_validator

ExternalResultStatus = Literal["available", "partial", "unavailable", "insufficient_data", "error"]
ForecastDirection = Literal["bullish", "bearish", "neutral", "unavailable"]
ExecutionDirection = Literal["long", "short", "unavailable"]


def clamp_score(value: object, min_value: float, max_value: float) -> float:
    """Return ``value`` as a finite float clamped to the inclusive range."""
    lower = float(min_value)
    upper = float(max_value)
    if lower > upper:
        lower, upper = upper, lower
    try:
        parsed = float(value) if value is not None else lower
    except (TypeError, ValueError):
        parsed = lower
    if not math.isfinite(parsed):
        parsed = lower
    return max(lower, min(upper, parsed))


def unavailable_result(engine: str, reason: str) -> dict[str, Any]:
    """Build a normalized unavailable payload for providers that cannot run."""
    normalized_engine = str(engine or "external_engine").strip() or "external_engine"
    normalized_reason = str(reason or "insufficient_data").strip() or "insufficient_data"
    return {
        "engine": normalized_engine,
        "status": "unavailable",
        "reason": normalized_reason,
        "confidence": 0.0,
        "data_quality_score": 0.0,
        "warnings": [],
        "metadata": {},
    }


class ScannerExternalResult(BaseModel):
    """Base fields shared by optional external scanner evidence."""

    model_config = ConfigDict(extra="ignore")

    engine: str = "not_configured"
    status: ExternalResultStatus = "unavailable"
    reason: str = "insufficient_data"
    confidence: float = Field(default=0.0, ge=0.0, le=1.0)
    data_quality_score: float = Field(default=0.0, ge=0.0, le=1.0)
    warnings: list[str] = Field(default_factory=list)
    metadata: dict[str, Any] = Field(default_factory=dict)

    @field_validator("engine", "reason", mode="before")
    @classmethod
    def _clean_required_text(cls: type[ScannerExternalResult], value: object) -> str:
        text = str(value or "").strip()
        return text or "insufficient_data"

    @field_validator("confidence", "data_quality_score", mode="before")
    @classmethod
    def _clamp_unit_score(cls: type[ScannerExternalResult], value: object) -> float:
        return clamp_score(value, 0.0, 1.0)


class GreekFlowSnapshot(ScannerExternalResult):
    """Greek-flow evidence normalized from future options-flow adapters."""

    symbol: str | None = None
    spot: float | None = None
    delta_pressure: float | None = None
    gamma_pressure: float | None = None
    vanna_pressure: float | None = None
    charm_pressure: float | None = None
    zero_dte_pressure: float | None = None
    iv_average: float | None = None
    gamma_flip: float | None = None
    call_wall: float | None = None
    put_wall: float | None = None
    source_tier: str = "not_connected"
    pressure_by_strike: list[dict[str, float | str | None]] = Field(default_factory=list)


class ForecastEvidence(ScannerExternalResult):
    """Forecast evidence from optional experimental OHLCV models."""

    symbol: str | None = None
    timeframe: str | None = None
    horizon: str | None = None
    forecast_direction: ForecastDirection = "unavailable"
    expected_return_pct: float | None = None
    forecast_volatility_pct: float | None = None
    path_dispersion: float | None = None
    scenarios: dict[str, float | str | None] = Field(default_factory=dict)
    model_name: str | None = None


class PortfolioOptimizationResult(ScannerExternalResult):
    """Portfolio allocation evidence for scanner leaders."""

    weights: dict[str, float] = Field(default_factory=dict)
    risk_contribution: dict[str, float] = Field(default_factory=dict)
    expected_volatility: float | None = None
    cvar_95: float | None = None
    max_drawdown_estimate: float | None = None
    constraints: dict[str, float | str | bool | None] = Field(default_factory=dict)
    selected_symbols: list[str] = Field(default_factory=list)
    long_only: bool = True


class ResearchBriefResult(ScannerExternalResult):
    """Research synthesis from optional NLP or multi-agent engines."""

    symbols: list[str] = Field(default_factory=list)
    mode: str = "unavailable"
    title: str = ""
    summary: str = ""
    key_points: list[str] = Field(default_factory=list)
    risks: list[str] = Field(default_factory=list)
    citations: list[dict[str, str]] = Field(default_factory=list)
    fallback_narrative: str | None = None


class ExecutionSimulationResult(ScannerExternalResult):
    """Replay or execution-feasibility evidence from optional sidecars."""

    symbol: str | None = None
    direction: ExecutionDirection = "unavailable"
    feasible: bool = False
    estimated_fill_price: float | None = None
    slippage_bps: float | None = None
    latency_ms: float | None = None
    rejection_reason: str = "insufficient_data"
    fills: list[dict[str, float | str | None]] = Field(default_factory=list)
    pnl_path: list[float] = Field(default_factory=list)
    execution_risk_hints: dict[str, float | str | bool | None] = Field(default_factory=dict)


__all__ = [
    "ExecutionSimulationResult",
    "ForecastEvidence",
    "GreekFlowSnapshot",
    "PortfolioOptimizationResult",
    "ResearchBriefResult",
    "ScannerExternalResult",
    "clamp_score",
    "unavailable_result",
]
