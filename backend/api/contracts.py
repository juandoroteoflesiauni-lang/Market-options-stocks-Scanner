from __future__ import annotations
"""API response contracts for the Deep Funnel Station frontend.

All monetary values are serialized as strings (never float) to prevent
floating-point drift — per PD-2 and Wall Street precision standards.
"""


from typing import Literal

from pydantic import BaseModel, ConfigDict

# ── System Health ──────────────────────────────────────────────


class ProviderHealthResponse(BaseModel):
    """Health status of a single data provider."""

    model_config = ConfigDict(frozen=True)

    name: str
    status: Literal["HEALTHY", "DEGRADED", "DOWN"]
    circuit_state: Literal["CLOSED", "OPEN", "HALF_OPEN"]
    latency_ms: int


class QueueMetricsResponse(BaseModel):
    """Event bus queue size metrics."""

    model_config = ConfigDict(frozen=True)

    standard_size: int
    standard_max: int
    priority_size: int
    priority_max: int


class HealthResponse(BaseModel):
    """Overall system health for the status bar."""

    model_config = ConfigDict(frozen=True)

    status: Literal["ONLINE", "DEGRADED", "OFFLINE"]
    uptime_seconds: int
    providers: list[ProviderHealthResponse]
    queues: QueueMetricsResponse
    last_scan_at: str | None


# ── Funnel Overview ────────────────────────────────────────────


class PhaseMetricsResponse(BaseModel):
    """Metrics for a single funnel phase."""

    model_config = ConfigDict(frozen=True)

    phase_id: Literal["A", "B", "C", "D"]
    label: str
    status: Literal["ACTIVE", "IDLE", "ERROR", "DISABLED"]
    input_count: int
    output_count: int
    last_processed_at: str | None
    processing_time_ms: int | None


class MarketBreadthResponse(BaseModel):
    """Real-time market breadth snapshot from SuperTrend Regime."""

    model_config = ConfigDict(frozen=True)

    bullish: int
    bearish: int
    no_data: int
    total_scanned: int
    bullish_pct: float
    bearish_pct: float
    coverage_pct: float
    last_updated: str | None


class FunnelOverviewResponse(BaseModel):
    """Overview of all funnel phases."""

    model_config = ConfigDict(frozen=True)

    phases: list[PhaseMetricsResponse]
    total_signals_emitted: int
    updated_at: str
    market_breadth: MarketBreadthResponse | None = None


# ── Scanner Candidates ────────────────────────────────────────


class DataLineageResponse(BaseModel):
    """Lineage metadata for a market snapshot."""

    model_config = ConfigDict(frozen=True)

    source: str
    ingestion_latency_ms: int
    raw_field_count: int


class GreeksResponse(BaseModel):
    """Option Greeks for the asset."""

    model_config = ConfigDict(frozen=True)

    delta: str
    gamma: str
    theta: str
    vega: str
    rho: str | None = None


class EngineSignalResponse(BaseModel):
    """A predictive or technical signal for the scanner."""

    model_config = ConfigDict(frozen=True)

    engineName: str
    value: str
    direction: Literal["BULL", "BEAR", "NEUTRAL"]
    weight: int


class CandidateResponse(BaseModel):
    """A scanner candidate — aligned with the frontend Ticker interface."""

    model_config = ConfigDict(frozen=True)

    symbol: str
    price: str
    priceChange: str
    priceChangePct: str
    volume: str
    avgVolume: str
    iv: str
    ivRank: str
    phase: Literal["A", "B", "C", "D"]
    momentum: str
    signals: list[EngineSignalResponse]
    greeks: GreeksResponse
    sparkline: list[float]


# ── Execution Signals ──────────────────────────────────────────


class SignalResponse(BaseModel):
    """An execution signal from Phase D."""

    model_config = ConfigDict(frozen=True)

    id: str
    ticker: str
    signal_type: Literal["BUY", "SELL", "NEUTRAL"]
    strength: Literal["CRITICAL", "HIGH", "MEDIUM", "LOW"]
    price_at_signal: str  # Decimal as string — PD-2
    emitted_at: str
    reason: str
