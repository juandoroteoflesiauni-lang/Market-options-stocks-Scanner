"""Options Terminal Router — QuantumAnalyzer API Layer.

Exposes the opciones_gex specialist engine via versioned REST endpoints.
Data flow:
  fetch_option_chain_raw: Finnhub → Massive/Polygon REST (Layer 1)
  → raw chain normalization (vectorized NumPy)
  → OptionsEngine.analyze_chain (Layer 3)
  → GEXMath / VolatilitySurfaceMath / iv_primitives (Layer 3)
  → typed Pydantic response models (this layer)

No cross-layer imports above Layer 3. Layer 4 orchestration consumes the
snapshot shape defined here via OptionsSnapshotResponse.
"""

from __future__ import annotations

import asyncio
import functools
import hashlib
import math
import time
from datetime import UTC, datetime
from typing import Any

import numpy as np
from fastapi import APIRouter, HTTPException, Query, Request
from fastapi.encoders import jsonable_encoder
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field
from starlette.responses import Response

from backend.config.logger_setup import get_logger
from backend.domain.strategy_models import OptionPayoffScenario, OptionStrategy
from backend.layer_1_data.datos.intraday_bars_fetcher import fetch_intraday_bars
from backend.layer_1_data.datos.massive_equity_bars_fetcher import fetch_equity_daily_bars
from backend.layer_1_data.datos.massive_options_fetcher import fetch_option_chain_raw
from backend.layer_3_specialists.opciones_gex.bsm import BlackScholesPricer, OptionType
from backend.layer_3_specialists.opciones_gex.chain_analytics_history import (
    ChainAnalyticsHistoryResponse,
    OptionsChainAnalyticsHistoryStore,
    enrich_chain_analytics_with_history,
)
from backend.layer_3_specialists.opciones_gex.chain_institutional_analytics import (
    ChainInstitutionalAnalyticsResponse,
    build_chain_institutional_analytics,
)
from backend.layer_3_specialists.opciones_gex.confluence_models import GEXLevels
from backend.layer_3_specialists.opciones_gex.derivatives import GEXMath, VolatilitySurfaceMath
from backend.layer_3_specialists.opciones_gex.gamma_flip_probability import (
    estimate_gamma_flip_probability,
)
from backend.layer_3_specialists.opciones_gex.iv_primitives import (
    atm_iv_from_chain,
    compute_skew_metrics,
    compute_skew_metrics_institutional_svi,
    compute_term_structure,
    historical_volatility,
    iv_percentile,
    iv_rank,
    rolling_historical_volatility,
    vrp_log_ratio,
)
from backend.layer_3_specialists.opciones_gex.options import OptionsEngine
from backend.layer_3_specialists.opciones_gex.options_confluence import OptionsConfluenceEngine
from backend.layer_3_specialists.opciones_gex.options_flow_signal import OptionsFlowSignalEngine
from backend.layer_3_specialists.opciones_gex.options_models import OptionsResult
from backend.layer_3_specialists.opciones_gex.strategy_payoff import StrategyPayoffEngine
from backend.layer_3_specialists.tecnico.smc import SMCEngine
from backend.services.options_gex_feature_assembler import assemble_options_gex_features
from backend.services.options_gex_snapshot_store import OptionsGexSnapshotStore

logger = get_logger(__name__)

router = APIRouter(prefix="/api/v1/options", tags=["options"])

# [PD-3][TH] API Optimization: Decoupling fetch cycle to 5 minutes
_OPTIONS_RAW_CHAIN_TTL_S = 300.0
_OPTIONS_RAW_CHAIN_CACHE: dict[
    str, tuple[float, tuple[dict[str, Any] | None, str, dict[str, Any]]]
] = {}
_OPTIONS_RAW_CHAIN_INFLIGHT: dict[
    str, asyncio.Task[tuple[dict[str, Any] | None, str, dict[str, Any]]]
] = {}
_OPTIONS_SNAPSHOT_SERVICE_TTL_S = 300.0
_OPTIONS_SNAPSHOT_SERVICE_CACHE: dict[str, tuple[float, OptionsSnapshotResponse]] = {}
_OPTIONS_SNAPSHOT_SERVICE_INFLIGHT: dict[str, asyncio.Task[OptionsSnapshotResponse]] = {}
_OPTIONS_CHAIN_ANALYTICS_TTL_S = 300.0
_OPTIONS_CHAIN_ANALYTICS_TIMEOUT_S = 12.0
_OPTIONS_CHAIN_ANALYTICS_CACHE: dict[str, tuple[float, ChainInstitutionalAnalyticsResponse]] = {}
_OPTIONS_CHAIN_ANALYTICS_INFLIGHT: dict[str, asyncio.Task[ChainInstitutionalAnalyticsResponse]] = {}
_OPTIONS_CHAIN_ANALYTICS_HISTORY_STORE = OptionsChainAnalyticsHistoryStore()
_OPTIONS_GEX_SNAPSHOT_STORE = OptionsGexSnapshotStore()


# ─────────────────────────────────────────────────────────────────────────────
# §0  DISCOVERY (Swagger tag: options)
# ─────────────────────────────────────────────────────────────────────────────


@router.get(
    "/meta",
    summary="Índice de rutas de opciones (por si /docs no lista el tag)",
)
async def options_meta() -> dict[str, Any]:
    """Stable discovery payload for clients and humans."""
    return {
        "tag": "options",
        "base": "/api/v1/options",
        "endpoints": {
            "snapshot": "GET /api/v1/options/snapshot/{symbol}?expiry=&r= (r=tasa libre anual; paralelo: cadena + aggs HV)",
            "chain": "GET /api/v1/options/chain/{symbol}?expiry=&r=",
            "chain_analytics": "GET /api/v1/options/chain-analytics/{symbol}?expiry=&r= (institutional per-contract/strike/expiry analytics)",
            "gex": "GET /api/v1/options/gex/{symbol}?expiry=&r=",
            "breakdown": "GET /api/v1/options/breakdown/{symbol}?r= (multi-expiry OI/GEX/volume; Advanced Metrics + charts usan esto; snapshot chain = una expiry / Chain tab)",
            "strategy_payoff": "POST /api/v1/options/strategy/payoff",
            "flow_analyze": "POST /api/v1/options/flow/analyze",
            "max_pain_history": "GET /api/v1/options/max-pain-history/{symbol}?limit= (serie Redis; job 30m — sin Redis → [])",
            "ws_stream": "WS /ws/options/{symbol}?expiry=&r=&contracts= (snapshot+chain+OPRA quotes opcional)",
            "chart_massive_ws": "WS /ws/chart_massive/{symbol} (T + AM; 1s añade agregados A; keys PRIMARY/SECONDARY/…)",
        },
        "docs_hint": "Open http://127.0.0.1:8000/docs and expand the section named 'options'.",
        "chain_sources": (
            "Finnhub first; if empty, REST GET /v3/snapshot/options/{ticker} on each MASSIVE_KEY_* × hosts. "
            "HV/VRP + SMC: GET /v2/aggs/… devuelve OHLCV; snapshot incluye ``smc_gex_confluence`` "
            "(SMCEngine técnico × OptionsConfluenceEngine)."
        ),
    }


# ─────────────────────────────────────────────────────────────────────────────
# §1  RESPONSE CONTRACTS (Pydantic V2, frozen=True)
# ─────────────────────────────────────────────────────────────────────────────


class OptionPayoffRequest(BaseModel):
    """Payload for stable v1 strategy payoff alias."""

    strategy: OptionStrategy
    scenario: OptionPayoffScenario


class OptionsFlowRequest(BaseModel):
    """Payload for stable v1 options flow alias."""

    rows: list[dict[str, Any]] = Field(default_factory=list)


@router.post("/strategy/payoff", summary="Options strategy payoff curve (stable v1 alias)")
async def options_strategy_payoff(payload: OptionPayoffRequest) -> dict[str, Any]:
    curve = StrategyPayoffEngine().compute_payoff(payload.strategy, payload.scenario)
    return {"ok": True, "curve": curve.model_dump(mode="json")}


@router.post("/flow/analyze", summary="Options flow signal from supplied rows (stable v1 alias)")
async def options_flow_analyze(payload: OptionsFlowRequest) -> dict[str, Any]:
    signal = OptionsFlowSignalEngine().analyze(payload.rows)
    return {"ok": True, "signal": signal.model_dump(mode="json")}


class OptionStrikeRow(BaseModel):
    """Per-strike row for the options chain table."""

    strike: float
    expiration: str
    # Call side
    call_bid: float | None = None
    call_ask: float | None = None
    call_last: float | None = None
    call_oi: float | None = None
    call_oi_change: float | None = None
    call_volume: float | None = None
    call_iv: float | None = None
    call_delta: float | None = None
    call_gamma: float | None = None
    call_theta: float | None = None
    call_vega: float | None = None
    call_gex: float | None = None
    call_bid_size: float | None = None
    call_ask_size: float | None = None
    call_break_even: float | None = None
    call_change: float | None = None
    call_change_pct: float | None = None
    call_vwap: float | None = None
    call_day_open: float | None = None
    call_day_high: float | None = None
    call_day_low: float | None = None
    call_day_close: float | None = None
    call_previous_close: float | None = None
    call_contract_ticker: str | None = None
    call_exercise_style: str | None = None
    call_shares_per_contract: float | None = None
    call_primary_exchange: str | None = None
    call_additional_underlyings_count: int | None = None
    call_mid: float | None = None
    call_mark: float | None = None
    call_spread_abs: float | None = None
    call_spread_pct: float | None = None
    call_quote_age_ms: float | None = None
    call_last_trade_age_ms: float | None = None
    call_liquidity_score: float | None = None
    call_bid_ask_size_imbalance: float | None = None
    call_intrinsic_value: float | None = None
    call_extrinsic_value: float | None = None
    call_breakeven_distance_pct: float | None = None
    call_model_price: float | None = None
    call_theoretical_edge: float | None = None
    call_rho: float | None = None
    call_lambda: float | None = None
    call_vomma: float | None = None
    call_vanna: float | None = None
    call_charm: float | None = None
    call_speed: float | None = None
    call_color: float | None = None
    call_zomma: float | None = None
    call_ultima: float | None = None
    call_vex: float | None = None
    call_cex: float | None = None
    call_premium_volume: float | None = None
    call_notional_volume: float | None = None
    call_volume_oi_ratio: float | None = None
    call_oi_turnover_proxy: float | None = None
    # Put side
    put_bid: float | None = None
    put_ask: float | None = None
    put_last: float | None = None
    put_oi: float | None = None
    put_oi_change: float | None = None
    put_volume: float | None = None
    put_iv: float | None = None
    put_delta: float | None = None
    put_gamma: float | None = None
    put_theta: float | None = None
    put_vega: float | None = None
    put_gex: float | None = None
    put_bid_size: float | None = None
    put_ask_size: float | None = None
    put_break_even: float | None = None
    put_change: float | None = None
    put_change_pct: float | None = None
    put_vwap: float | None = None
    put_day_open: float | None = None
    put_day_high: float | None = None
    put_day_low: float | None = None
    put_day_close: float | None = None
    put_previous_close: float | None = None
    put_contract_ticker: str | None = None
    put_exercise_style: str | None = None
    put_shares_per_contract: float | None = None
    put_primary_exchange: str | None = None
    put_additional_underlyings_count: int | None = None
    put_mid: float | None = None
    put_mark: float | None = None
    put_spread_abs: float | None = None
    put_spread_pct: float | None = None
    put_quote_age_ms: float | None = None
    put_last_trade_age_ms: float | None = None
    put_liquidity_score: float | None = None
    put_bid_ask_size_imbalance: float | None = None
    put_intrinsic_value: float | None = None
    put_extrinsic_value: float | None = None
    put_breakeven_distance_pct: float | None = None
    put_model_price: float | None = None
    put_theoretical_edge: float | None = None
    put_rho: float | None = None
    put_lambda: float | None = None
    put_vomma: float | None = None
    put_vanna: float | None = None
    put_charm: float | None = None
    put_speed: float | None = None
    put_color: float | None = None
    put_zomma: float | None = None
    put_ultima: float | None = None
    put_vex: float | None = None
    put_cex: float | None = None
    put_premium_volume: float | None = None
    put_notional_volume: float | None = None
    put_volume_oi_ratio: float | None = None
    put_oi_turnover_proxy: float | None = None
    # Vanna / Charm (alineado a GEXMath.vanna_cex_exposure: Vanna×OI×100, Charm×OI×100)
    call_vanna_exposure: float | None = None
    put_vanna_exposure: float | None = None
    net_vanna_exposure: float | None = None
    call_charm_exposure: float | None = None
    put_charm_exposure: float | None = None
    net_charm_exposure: float | None = None
    # Combined
    total_oi: float | None = None
    net_gex: float | None = None
    moneyness: float | None = None  # (strike - spot) / spot
    # Delta exposure (USD nocional, convención DeltaExposureEngine: δ × OI × 100 × spot)
    call_dex: float | None = None
    put_dex: float | None = None
    net_dex: float | None = None
    net_vex: float | None = None
    net_cex: float | None = None
    premium_volume: float | None = None
    notional_volume: float | None = None
    volume_oi_ratio: float | None = None
    oi_turnover_proxy: float | None = None
    gex_share_pct: float | None = None
    dex_share_pct: float | None = None
    put_call_parity_residual: float | None = None
    metric_sources: dict[str, str] = Field(default_factory=dict)


class GEXLevelsResponse(BaseModel):
    """Key GEX price levels for the levels strip panel."""

    # ── Primary walls (backward-compatible) ──────────────────────────────────
    call_wall: float | None = None  # Strong = highest call GEX strike
    put_wall: float | None = None  # Strong = most negative put GEX strike
    # ── Multi-level walls (top-3 ranked by absolute GEX magnitude) ──────────
    call_wall_moderate: float | None = None
    call_wall_weak: float | None = None
    put_wall_moderate: float | None = None
    put_wall_weak: float | None = None
    # ── Other key levels ─────────────────────────────────────────────────────
    zero_gamma_level: float | None = None
    max_pain: float | None = None
    net_gex_total: float = 0.0
    call_gex_total: float = 0.0
    put_gex_total: float = 0.0
    dealer_bias: str = "NEUTRAL"  # BULLISH / BEARISH / NEUTRAL
    squeeze_probability: float = Field(
        default=0.0,
        description=(
            "Heurística 0–1: incluye P(GBM toca ZGL en DTE) como peso 0.1 vía "
            "estimate_gamma_flip_probability — no es probabilidad literal de squeeze corto."
        ),
    )
    gex_formula_version: str = Field(
        default="spotgamma_v1",
        description="Γ×OI×100×S²×0.01 por strike (GEX); VEX=Vanna×OI×100 (sin S extra).",
    )


class IVSurfacePoint(BaseModel):
    """Single cell in the IV surface grid (maturity × strike)."""

    expiration: str
    dte: float
    strike: float
    moneyness: float  # ln(K/F)
    call_iv: float | None = None
    put_iv: float | None = None
    mid_iv: float | None = None
    svi_iv: float | None = None  # SVI-fitted if calibration converges


class IVSurfaceResponse(BaseModel):
    """IV surface grid + scalar analytics."""

    surface: list[IVSurfacePoint] = Field(default_factory=list)
    atm_iv: float | None = None
    iv_rank_hv_rolling: float | None = Field(
        default=None,
        description=(
            "[0,1] IV ATM vs min–max de la serie de HV 20d rolling (~1 año de historia de "
            "realized vol del subyacente)."
        ),
    )
    iv_rank_cross_expiry: float | None = Field(
        default=None,
        description=(
            "[0,1] IV ATM vs min–max de las IV ATM entre expiries del mismo snapshot (mismo día)."
        ),
    )
    vrp: float | None = Field(
        default=None,
        description="ln(IV_ATM / HV_20) con HV de cierres diarios Massive/Polygon cuando hay datos.",
    )
    hv_20: float | None = Field(
        default=None, description="Volatilidad realizada 20d anualizada (subyacente)."
    )
    hv_60: float | None = Field(
        default=None, description="Volatilidad realizada 60d anualizada (subyacente)."
    )
    iv_percentile_cross_term: float | None = Field(
        default=None,
        description="Percentil de la IV ATM actual vs distribución de ATM por expiry en el snapshot (mismo día).",
    )
    vol_underlying_meta: dict[str, Any] = Field(
        default_factory=dict,
        description="bars, source (clave@host), error del fetch de aggs diarios.",
    )
    # Skew for nearest-term expiry
    skew_25d: float | None = None
    risk_reversal: float | None = None
    butterfly: float | None = None
    risk_reversal_10: float | None = Field(
        default=None,
        description="10Δ risk reversal: IV(10Δ call) − IV(10Δ put) vía SVI + BSM.",
    )
    butterfly_10: float | None = Field(
        default=None,
        description="10Δ butterfly: (IV(10Δ call) + IV(10Δ put)) / 2 − IV_ATM (K = forward).",
    )
    skew_slope: float | None = None
    # Term structure
    term_structure: dict[str, Any] = Field(default_factory=dict)
    # PDF analytics from SVI calibration (nearest-term expiry only)
    pdf_skewness: float | None = None
    pdf_excess_kurtosis: float | None = None
    pdf_left_tail: float | None = None
    pdf_right_tail: float | None = None
    pdf_tail_regime: str | None = None


class SMCGEXConfluenceSnapshot(BaseModel):
    """Cruce SMCEngine (tecnico) × niveles GEX — ``OptionsConfluenceEngine.validate``."""

    ran: bool = False
    smc_ok: bool = False
    smc_error: str | None = None
    smc_bias: str | None = None
    smc_sesgo: str | None = None
    smc_composite_score: float | None = None
    order_blocks: int = 0
    fvg_zones: int = 0
    liquidity_sweeps: int = 0
    gex_levels_ok: bool = False
    is_ob_validated: bool = False
    is_sweep_confirmed: bool = False
    is_magnet_active: bool = False
    confluence_score: float = 0.0
    summary: str = "NO DATA"


class ConfluenceResponse(BaseModel):
    """OptionsConfluenceEngine summary for the confluence panel."""

    ticker: str
    score: float = 0.0
    signal: str = "WAIT"
    confidence: float = 0.0
    conviction: str = "LOW"
    gex_sub_score: float = 0.0
    iv_sub_score: float = 0.0
    squeeze_override: bool = False
    vanna_exposure_regime: str = "NEUTRAL"
    vex_regime: str = "NEUTRAL"
    cex_regime: str = "NEUTRAL"
    total_vanna_exposure: float = 0.0
    total_vex: float = 0.0
    total_cex: float = 0.0
    hhi_concentration: float = 0.0
    pcr_oi: float | None = None
    pcr_volume: float | None = None


class MaxPainHistoryPoint(BaseModel):
    """Punto de serie temporal max pain (job 30m + Redis)."""

    timestamp: int = Field(..., description="Unix epoch seconds UTC")
    max_pain: float
    spot: float
    distance_pct: float = Field(..., description="(spot − max_pain) / spot × 100, signed")
    expiry: str = ""
    dte_days: float | None = None


class MaxPainHistoryResponse(BaseModel):
    ticker: str
    points: list[MaxPainHistoryPoint]
    ok: bool = True
    source: str = Field(default="redis", description="redis | empty")


class OptionsSnapshotResponse(BaseModel):
    """
    Master snapshot — canonical shape consumed by UI panels and injected
    into the options_gex agent pipeline as structured context.
    """

    ticker: str
    spot: float
    as_of: str  # ISO timestamp
    expiries: list[str]
    chain: list[OptionStrikeRow]
    total_dex: float = Field(
        default=0.0,
        description="Suma firmada de net_dex por strike (USD nocional, motor DEX layer 3).",
    )
    dex_flip_level: float | None = Field(
        default=None,
        description="Strike interpolado donde la suma acumulada de net_dex por strike cruza cero.",
    )
    gex_levels: GEXLevelsResponse
    iv_surface: IVSurfaceResponse
    confluence: ConfluenceResponse
    ok: bool = True
    error: str | None = None
    chain_quality: dict[str, Any] = Field(
        default_factory=dict,
        description="Cobertura de datos: proveedor, strikes, IV/precios rellenos, meta del fetcher.",
    )
    engine_signal: dict[str, Any] = Field(
        default_factory=dict,
        description="Salida de OptionsEngine.generate_signal (régimen vanna exposure/GEX, scores).",
    )
    options_gex_features: dict[str, Any] = Field(
        default_factory=dict,
        description="Canonical Options/GEX feature vector for scanner, backtest and risk desk.",
    )
    smc_gex_confluence: SMCGEXConfluenceSnapshot = Field(
        default_factory=SMCGEXConfluenceSnapshot,
        description="Confluencia SMC×GEX (velas diarias Massive + niveles del snapshot).",
    )
    ndde: float | None = Field(
        default=None,
        description="Net Dealer Delta Exposure (signed float).",
    )
    charm_flow: str | float | None = Field(
        default=None,
        description="Charm flow directional/pressure proxy.",
    )
    implied_percentile_99: float | None = Field(
        default=None,
        description="99th percentile of implied price distribution (extreme boundary).",
    )


class IntradayBarItem(BaseModel):
    """Single OHLCV bar with Unix-ms timestamp."""

    t: int  # Unix milliseconds
    open: float
    high: float
    low: float
    close: float
    volume: float


class IntradayBarsResponse(BaseModel):
    """Intraday OHLCV bars for chart rendering."""

    symbol: str
    interval: str
    bars: list[IntradayBarItem] = Field(default_factory=list)
    count: int = 0
    source: str = ""
    ok: bool = True
    error: str | None = None


class IVHistoryItem(BaseModel):
    """Historical IV point with rank and percentile."""

    date: str
    iv: float
    rank: float | None = None
    percentile: float | None = None


class IVHistoryResponse(BaseModel):
    """Time series of historical IV analytics."""

    symbol: str
    history: list[IVHistoryItem] = Field(default_factory=list)
    ok: bool = True
    error: str | None = None
    is_hv_proxy: bool = False  # True if data is HV instead of IV


# ─────────────────────────────────────────────────────────────────────────────
# §1b  INTRADAY BARS ENDPOINT
# ─────────────────────────────────────────────────────────────────────────────


@router.get(
    "/intraday/{symbol}",
    response_model=IntradayBarsResponse,
    summary="Velas OHLCV intraday (1s/5m/15m/30m/1h/4h/1d) para el gráfico de precio + GEX",
)
async def get_intraday_bars(
    symbol: str,
    interval: str = Query(
        default="5m", description="Intervalo: 1s | 1m | 5m | 15m | 30m | 1h | 4h | 1d"
    ),
) -> IntradayBarsResponse:
    """
    Fetch OHLCV candlestick bars.
    Source priority: Polygon.io → Alpaca Markets → Massive mirror keys.
    Interval ``1s`` usa agregados por segundo en Polygon/Massive REST; Alpaca no aplica.
    Returns raw bars suitable for a Plotly candlestick chart.
    """
    valid_intervals = {"1s", "1m", "5m", "15m", "30m", "1h", "4h", "1d"}
    if interval not in valid_intervals:
        raise HTTPException(
            status_code=422,
            detail=f"Invalid interval '{interval}'. Valid values: {sorted(valid_intervals)}",
        )

    result = fetch_intraday_bars(symbol.upper().strip(), interval=interval)

    if result.get("error") and not result.get("bars"):
        return IntradayBarsResponse(
            symbol=symbol.upper(),
            interval=interval,
            bars=[],
            count=0,
            source="",
            ok=False,
            error=result["error"],
        )

    raw_bars = result.get("bars", [])
    bars = [
        IntradayBarItem(
            t=int(b["t"]),
            open=float(b["open"]),
            high=float(b["high"]),
            low=float(b["low"]),
            close=float(b["close"]),
            volume=float(b["volume"]),
        )
        for b in raw_bars
        if all(k in b for k in ("t", "open", "high", "low", "close"))
    ]

    return IntradayBarsResponse(
        symbol=symbol.upper(),
        interval=interval,
        bars=bars,
        count=len(bars),
        source=result.get("source", ""),
        ok=True,
        error=None,
    )


@router.get(
    "/iv-history/{symbol}",
    response_model=IVHistoryResponse,
    summary="Historial de IV ATM + IV Rank + IV Percentile (ventana 252d)",
)
async def get_iv_history(symbol: str) -> IVHistoryResponse:
    """
    Fetch historical IV from FMP and compute rolling rank/percentile.
    Uses a 252-day window for rank/percentile calculation.
    """
    from backend.layer_1_data.fetchers.fmp_client import FMPClient

    sym = symbol.upper().strip()
    logger.info(f"get_iv_history: sym={sym}")
    try:
        fmp = FMPClient()
        raw_history = await fmp.get_options_iv_history(sym)
        logger.info(
            f"get_iv_history: received {len(raw_history) if raw_history else 0} raw items from FMP"
        )
    except Exception as exc:
        logger.error(f"get_iv_history: FMP client error for {sym}: {exc}")
        return IVHistoryResponse(symbol=sym, ok=False, error=f"FMP error: {exc}")
    if not raw_history:
        logger.warning(
            f"get_iv_history: No raw IV history found for {sym}. Attempting HV fallback..."
        )
        try:
            # Fallback to Historical Volatility (HV) using daily prices
            from backend.layer_1_data.datos.massive_equity_bars_fetcher import (
                fetch_equity_daily_bars,
            )
            from backend.layer_3_specialists.opciones_gex.iv_primitives import (
                rolling_historical_volatility,
            )

            # Fetch 500 days of daily bars (Massive/Polygon)
            # fetch_equity_daily_bars returns (closes, df, meta)
            closes_arr, df, meta = fetch_equity_daily_bars(sym, lookback_calendar_days=500)
            if closes_arr is None or df is None or len(closes_arr) < 22:
                logger.error(
                    f"get_iv_history: No historical price data for {sym} to calculate HV proxy"
                )
                return IVHistoryResponse(
                    symbol=sym, ok=False, error="No historical IV or price data available"
                )

            # Calculate log returns
            log_returns = np.diff(np.log(closes_arr))

            # Calculate 20-day rolling HV
            hv_series = rolling_historical_volatility(log_returns, window=20)

            # Convert timestamps to ISO dates
            # 't' is ms since epoch in the updated fetcher
            raw_ts = df["t"].to_numpy()
            dates_subset = [
                datetime.fromtimestamp(int(ts) / 1000, tz=UTC).strftime("%Y-%m-%d")
                for ts in raw_ts[21:]  # offset for diff + window
            ]

            history = [
                IVHistoryItem(date=d, iv=float(v))
                for d, v in zip(dates_subset, hv_series, strict=False)
            ]

            if not history:
                return IVHistoryResponse(
                    symbol=sym, ok=False, error="Failed to calculate HV fallback history"
                )

            dates = [h.date for h in history]
            ivs = [h.iv for h in history]
            is_hv_proxy = True
            logger.info(f"get_iv_history: HV fallback success for {sym} ({len(ivs)} points)")

        except Exception as fallback_exc:
            logger.error(f"get_iv_history: HV fallback failed: {fallback_exc}")
            return IVHistoryResponse(
                symbol=sym, ok=False, error=f"FMP IV missing and HV fallback failed: {fallback_exc}"
            )
    else:
        logger.info(f"get_iv_history: received {len(raw_history)} raw items from FMP")
        # Extract and sort existing IV data
        raw_history.sort(key=lambda x: x.date or "")
        dates = [
            item.date for item in raw_history if item.date and item.impliedVolatility is not None
        ]
        ivs = [
            item.impliedVolatility
            for item in raw_history
            if item.date and item.impliedVolatility is not None
        ]
        is_hv_proxy = False

    if len(ivs) < 20:  # Minimum to show something
        return IVHistoryResponse(
            symbol=sym,
            history=[IVHistoryItem(date=d, iv=v) for d, v in zip(dates, ivs, strict=False)],
            ok=True,
        )

    # Convert to numpy for vectorized ops
    iv_arr = np.array(ivs, dtype=np.float64)
    n = len(iv_arr)
    window = 252

    ranks: list[float | None] = [None] * n
    percentiles: list[float | None] = [None] * n

    for i in range(n):
        # We need at least some history to calculate rank
        start_idx = max(0, i - window + 1)
        # Only calculate if we have at least 20 points in the window
        if i - start_idx >= 5:
            subset = iv_arr[start_idx : i + 1]
            curr = iv_arr[i]

            mn, mx = np.min(subset), np.max(subset)
            if mx > mn:
                rank = (curr - mn) / (mx - mn)
                ranks[i] = round(float(rank), 4)
            else:
                ranks[i] = 0.5  # Neutral if no range

            # Percentile
            percentiles[i] = round(float(np.sum(subset <= curr) / len(subset)), 4)

    history = []
    for i in range(n):
        history.append(
            IVHistoryItem(
                date=dates[i], iv=round(ivs[i], 4), rank=ranks[i], percentile=percentiles[i]
            )
        )

    # Return only the recent history if requested or just the whole thing (FMP usually returns ~250-500 days)
    return IVHistoryResponse(symbol=sym, history=history, ok=True, is_hv_proxy=is_hv_proxy)


# ─────────────────────────────────────────────────────────────────────────────
# §2  INTERNAL HELPERS
# ─────────────────────────────────────────────────────────────────────────────

_SMC_MIN_ROWS = 50  # alineado con ``tecnico.smc._MIN_ROWS``


def _smc_gex_confluence_blocking(
    sym: str,
    spot: float,
    ohlcv_df: object,
    gex_levels: GEXLevelsResponse,
) -> SMCGEXConfluenceSnapshot:
    """SMCEngine + OptionsConfluenceEngine — pensado para ``run_in_executor``."""
    snap = SMCGEXConfluenceSnapshot()
    if ohlcv_df is None or getattr(ohlcv_df, "empty", True):
        return snap.model_copy(update={"summary": "No OHLCV for SMC (aggs)"})
    nrows = int(len(ohlcv_df))
    if nrows < _SMC_MIN_ROWS:
        return snap.model_copy(
            update={"summary": f"Insufficient bars for SMC ({nrows} < {_SMC_MIN_ROWS})"}
        )
    walls = (
        gex_levels.call_wall,
        gex_levels.put_wall,
        gex_levels.zero_gamma_level,
        gex_levels.max_pain,
    )
    if any(x is None for x in walls):
        return snap.model_copy(update={"summary": "Incomplete GEX levels (null wall or ZGL)"})
    try:
        smc_res = SMCEngine().analyze(ohlcv_df, ticker=sym, timeframe="1D")
    except Exception as exc:
        return snap.model_copy(update={"smc_error": str(exc)[:300], "summary": "SMCEngine error"})
    if smc_res.error:
        return snap.model_copy(
            update={"smc_error": smc_res.error, "summary": "SMC analysis returned error"}
        )

    gx = GEXLevels(
        call_wall=float(gex_levels.call_wall),
        put_wall=float(gex_levels.put_wall),
        zero_gamma_level=float(gex_levels.zero_gamma_level),
        max_pain=float(gex_levels.max_pain),
        volatility_magnet=float(gex_levels.max_pain),
    )
    conf = OptionsConfluenceEngine.validate(smc_res, gx, spot=spot)
    return SMCGEXConfluenceSnapshot(
        ran=True,
        smc_ok=True,
        smc_bias=smc_res.bias,
        smc_sesgo=smc_res.sesgo.value,
        smc_composite_score=smc_res.composite_score,
        order_blocks=len(smc_res.order_blocks),
        fvg_zones=len(smc_res.fvg_zones),
        liquidity_sweeps=len(smc_res.liquidity_sweeps),
        gex_levels_ok=True,
        is_ob_validated=conf.is_ob_validated,
        is_sweep_confirmed=conf.is_sweep_confirmed,
        is_magnet_active=conf.is_magnet_active,
        confluence_score=conf.confluence_score,
        summary=conf.summary,
    )


def _chain_quality_dict(
    rows: list[OptionStrikeRow],
    expiries: list[str],
    expiry_used: str,
    chain_src: str,
    fetch_meta: dict[str, Any],
) -> dict[str, Any]:
    """Resumen para validar que la cadena trae IV, OI, cotizaciones, etc."""
    n = len(rows)
    if n == 0:
        return {"provider": chain_src or "none", "fetch_details": fetch_meta}

    def _pos(v: float | None) -> bool:
        return v is not None and v > 0

    call_iv_n = sum(1 for r in rows if _pos(r.call_iv))
    put_iv_n = sum(1 for r in rows if _pos(r.put_iv))
    call_oi_n = sum(1 for r in rows if _pos(r.call_oi))
    put_oi_n = sum(1 for r in rows if _pos(r.put_oi))
    call_bidask = sum(1 for r in rows if r.call_bid is not None or r.call_ask is not None)
    put_bidask = sum(1 for r in rows if r.put_bid is not None or r.put_ask is not None)

    return {
        "provider": chain_src or "unknown",
        "expiry_used": expiry_used,
        "expiries_available": len(expiries),
        "strikes_in_expiry": n,
        "call_iv_strikes": call_iv_n,
        "put_iv_strikes": put_iv_n,
        "call_iv_coverage_pct": round(100.0 * call_iv_n / n, 1),
        "put_iv_coverage_pct": round(100.0 * put_iv_n / n, 1),
        "call_oi_strikes": call_oi_n,
        "put_oi_strikes": put_oi_n,
        "call_bid_or_ask_strikes": call_bidask,
        "put_bid_or_ask_strikes": put_bidask,
        "fetch_details": fetch_meta,
    }


def _safe_float(x: object) -> float | None:
    """Convert raw value to float or None, stripping NaN/Inf."""
    try:
        f = float(x)  # type: ignore[arg-type]
        return None if (math.isnan(f) or math.isinf(f)) else f
    except (TypeError, ValueError):
        return None


def _safe_int(x: object) -> int | None:
    f = _safe_float(x)
    return None if f is None else int(round(f))


def _leg_dex_nominal_usd(
    delta: float | None,
    oi: float | None,
    spot: float,
    mult: float = 100.0,
) -> float | None:
    """DEX nocional por pata: δ × OI × mult × spot (``DeltaExposureEngine``)."""
    if delta is None or not math.isfinite(float(delta)):
        return None
    if spot <= 0 or not math.isfinite(float(spot)):
        return None
    o = float(oi or 0.0)
    if o <= 0:
        return None
    return float(delta) * o * mult * float(spot)


def _dex_flip_strike(sorted_strike_net: list[tuple[float, float]]) -> float | None:
    """Strike interpolado donde la suma acumulada de net DEX cruza cero."""
    if len(sorted_strike_net) < 2:
        return None
    cum = 0.0
    prev_k: float | None = None
    for k, d in sorted_strike_net:
        cum_before = cum
        cum_after = cum_before + d
        if prev_k is not None and cum_before * cum_after < 0:
            dd = d
            if abs(dd) > 1e-12:
                alpha = -cum_before / dd
                return round(float(prev_k + alpha * (float(k) - prev_k)), 4)
        cum = cum_after
        prev_k = float(k)
    return None


def enrich_chain_with_dex(
    rows: list[OptionStrikeRow],
    spot: float,
    mult: float = 100.0,
) -> tuple[list[OptionStrikeRow], float, float | None]:
    """Attach ``call_dex`` / ``put_dex`` / ``net_dex``; retorna total firmado y nivel flip."""
    if not rows or spot <= 0:
        return rows, 0.0, None

    out: list[OptionStrikeRow] = []
    strike_net_pairs: list[tuple[float, float]] = []
    total_signed = 0.0

    for row in rows:
        cd = _leg_dex_nominal_usd(row.call_delta, row.call_oi, spot, mult)
        pd = _leg_dex_nominal_usd(row.put_delta, row.put_oi, spot, mult)
        if cd is None and pd is None:
            nd: float | None = None
            net_for_cum = 0.0
        else:
            nd_val = (cd or 0.0) + (pd or 0.0)
            nd = round(nd_val, 2)
            net_for_cum = nd_val
            total_signed += nd_val
        strike_net_pairs.append((float(row.strike), net_for_cum))
        out.append(
            row.model_copy(
                update={
                    "call_dex": round(cd, 2) if cd is not None else None,
                    "put_dex": round(pd, 2) if pd is not None else None,
                    "net_dex": nd,
                }
            )
        )

    sorted_pairs = sorted(strike_net_pairs, key=lambda x: x[0])
    flip = _dex_flip_strike(sorted_pairs)
    return out, round(total_signed, 2), flip


def _dte_from_expiry(expiry: str) -> float:
    """Days to expiration from ISO date string. Returns 1/365 floor for same-day."""
    try:
        exp_dt = datetime.strptime(expiry[:10], "%Y-%m-%d")
        today = datetime.now(tz=UTC).replace(hour=0, minute=0, second=0, microsecond=0, tzinfo=None)
        dte = (exp_dt - today).days
        return max(float(dte), 1.0) / 365.0
    except ValueError:
        return 30.0 / 365.0


def _parse_finnhub_chain(
    raw: dict[str, Any],
    spot: float,
    expiry_filter: str | None,
    r: float = 0.04,
) -> tuple[
    list[OptionStrikeRow],
    np.ndarray,
    np.ndarray,
    np.ndarray,
    np.ndarray,
    np.ndarray,
    float,
    str,
]:
    """
    Parse Finnhub option_chain payload into typed rows + NumPy arrays.

    Returns:
        rows, strikes, call_oi, put_oi, call_iv, put_iv, tte, expiry_used
    """
    data_list = raw.get("data") or []
    if not isinstance(data_list, list) or not data_list:
        return [], np.array([]), np.array([]), np.array([]), np.array([]), np.array([]), 0.03, ""

    # Select expiry: prefer filter, else nearest-term with data
    expiry_used = ""
    selected_exp_data: dict[str, Any] | None = None

    for exp_block in data_list:
        if not isinstance(exp_block, dict):
            continue
        exp_date: str = str(exp_block.get("expirationDate", ""))
        if expiry_filter and exp_date != expiry_filter:
            continue
        selected_exp_data = exp_block
        expiry_used = exp_date
        if not expiry_filter:
            break  # nearest-term first in Finnhub response

    if selected_exp_data is None:
        return [], np.array([]), np.array([]), np.array([]), np.array([]), np.array([]), 0.03, ""

    tte = _dte_from_expiry(expiry_used)
    options_list = selected_exp_data.get("options") or []
    if not isinstance(options_list, list):
        options_list = []

    # Build strike-keyed dicts
    calls: dict[float, dict[str, Any]] = {}
    puts: dict[float, dict[str, Any]] = {}
    for opt in options_list:
        if not isinstance(opt, dict):
            continue
        k_raw = _safe_float(opt.get("strike"))
        if k_raw is None or k_raw <= 0:
            continue
        opt_type = str(opt.get("type", "")).upper()
        if opt_type == "CALL":
            calls[k_raw] = opt
        elif opt_type == "PUT":
            puts[k_raw] = opt

    all_strikes = sorted(set(calls.keys()) | set(puts.keys()))
    if not all_strikes:
        return (
            [],
            np.array([]),
            np.array([]),
            np.array([]),
            np.array([]),
            np.array([]),
            tte,
            expiry_used,
        )

    rows: list[OptionStrikeRow] = []
    strikes_arr: list[float] = []
    call_oi_arr: list[float] = []
    put_oi_arr: list[float] = []
    call_iv_arr: list[float] = []
    put_iv_arr: list[float] = []

    leg_meta: list[
        tuple[float, dict[str, Any], dict[str, Any], float | None, float | None, float, float]
    ] = []

    for k in all_strikes:
        c = calls.get(k, {})
        p = puts.get(k, {})
        c_iv_raw = _safe_float(c.get("impliedVolatility"))
        p_iv_raw = _safe_float(p.get("impliedVolatility"))
        c_oi = _safe_float(c.get("openInterest")) or 0.0
        p_oi = _safe_float(p.get("openInterest")) or 0.0
        leg_meta.append((k, c, p, c_iv_raw, p_iv_raw, c_oi, p_oi))
        strikes_arr.append(k)
        call_oi_arr.append(c_oi)
        put_oi_arr.append(p_oi)
        call_iv_arr.append(c_iv_raw if c_iv_raw is not None else np.nan)
        put_iv_arr.append(p_iv_raw if p_iv_raw is not None else np.nan)

    strikes_np = np.array(strikes_arr, dtype=np.float64)
    call_iv_np = np.array(call_iv_arr, dtype=np.float64)
    put_iv_np = np.array(put_iv_arr, dtype=np.float64)
    atm_iv_chain = float(atm_iv_from_chain(strikes_np, call_iv_np, put_iv_np, spot))
    if math.isnan(atm_iv_chain) or atm_iv_chain <= 0:
        atm_iv_chain = 0.25
    c_iv_eff = np.where(np.isfinite(call_iv_np) & (call_iv_np > 0), call_iv_np, atm_iv_chain)
    p_iv_eff = np.where(np.isfinite(put_iv_np) & (put_iv_np > 0), put_iv_np, atm_iv_chain)

    for i, (k, c, p, c_iv_raw, p_iv_raw, c_oi, p_oi) in enumerate(leg_meta):
        c_iv_bs = float(c_iv_eff[i])
        p_iv_bs = float(p_iv_eff[i])
        c_iv_for_bs = c_iv_raw if c_iv_raw is not None and c_iv_raw > 0 else c_iv_bs
        p_iv_for_bs = p_iv_raw if p_iv_raw is not None and p_iv_raw > 0 else p_iv_bs

        c_delta = c_gamma = c_theta = c_vega = None
        p_delta = p_gamma = p_theta = p_vega = None
        c_gex = p_gex = net_gex_val = None

        if c_iv_for_bs > 0 and tte > 0:
            c_delta = _safe_float(
                BlackScholesPricer.delta(spot, k, tte, r, c_iv_for_bs, OptionType.CALL)
            )
            c_gamma = _safe_float(BlackScholesPricer.gamma(spot, k, tte, r, c_iv_for_bs))
            c_theta = _safe_float(
                BlackScholesPricer.theta(spot, k, tte, r, c_iv_for_bs, OptionType.CALL)
            )
            c_vega = _safe_float(BlackScholesPricer.vega(spot, k, tte, r, c_iv_for_bs))

        if c_delta is None:
            c_delta = _safe_float(c.get("delta"))
        if c_gamma is None:
            c_gamma = _safe_float(c.get("gamma"))
        if c_theta is None:
            c_theta = _safe_float(c.get("theta"))
        if c_vega is None:
            c_vega = _safe_float(c.get("vega"))

        if c_gamma is not None:
            c_gex = round(c_gamma * c_oi * 100.0 * spot * spot * 0.01, 2)

        if p_iv_for_bs > 0 and tte > 0:
            p_delta = _safe_float(
                BlackScholesPricer.delta(spot, k, tte, r, p_iv_for_bs, OptionType.PUT)
            )
            p_gamma = _safe_float(BlackScholesPricer.gamma(spot, k, tte, r, p_iv_for_bs))
            p_theta = _safe_float(
                BlackScholesPricer.theta(spot, k, tte, r, p_iv_for_bs, OptionType.PUT)
            )
            p_vega = _safe_float(BlackScholesPricer.vega(spot, k, tte, r, p_iv_for_bs))

        if p_delta is None:
            p_delta = _safe_float(p.get("delta"))
        if p_gamma is None:
            p_gamma = _safe_float(p.get("gamma"))
        if p_theta is None:
            p_theta = _safe_float(p.get("theta"))
        if p_vega is None:
            p_vega = _safe_float(p.get("vega"))

        if p_gamma is not None:
            p_gex = round(-p_gamma * p_oi * 100.0 * spot * spot * 0.01, 2)

        _mult = 100.0
        c_vanna_usd: float | None = None
        p_vanna_usd: float | None = None
        c_charm_usd: float | None = None
        p_charm_usd: float | None = None
        if c_iv_for_bs > 0 and tte > 0 and c_oi > 0:
            k_arr = np.array([k], dtype=np.float64)
            sig_c = np.array([c_iv_for_bs], dtype=np.float64)
            va_c = float(BlackScholesPricer.vanna_vec(spot, k_arr, tte, r, sig_c)[0])
            c_vanna_usd = round(float(va_c * c_oi * _mult), 2)
            ch_c = float(BlackScholesPricer.charm_vec(spot, k_arr, tte, r, sig_c)[0])
            c_charm_usd = round(float(ch_c * c_oi * _mult), 4)
        if p_iv_for_bs > 0 and tte > 0 and p_oi > 0:
            k_arr = np.array([k], dtype=np.float64)
            sig_p = np.array([p_iv_for_bs], dtype=np.float64)
            va_p = float(BlackScholesPricer.vanna_vec(spot, k_arr, tte, r, sig_p)[0])
            p_vanna_usd = round(float(va_p * p_oi * _mult), 2)
            ch_p = float(BlackScholesPricer.charm_vec(spot, k_arr, tte, r, sig_p)[0])
            p_charm_usd = round(float(ch_p * p_oi * _mult), 4)

        net_vanna_val: float | None = None
        net_charm_val: float | None = None
        if c_vanna_usd is not None or p_vanna_usd is not None:
            net_vanna_val = round(float((c_vanna_usd or 0.0) - (p_vanna_usd or 0.0)), 2)
        if c_charm_usd is not None or p_charm_usd is not None:
            net_charm_val = round(float((c_charm_usd or 0.0) - (p_charm_usd or 0.0)), 4)

        if c_gex is not None and p_gex is not None:
            net_gex_val = round(c_gex + p_gex, 2)
        elif c_gex is not None:
            net_gex_val = c_gex
        elif p_gex is not None:
            net_gex_val = p_gex

        moneyness = round((k - spot) / spot, 4) if spot > 0 else None
        call_iv_out = c_iv_raw if c_iv_raw is not None and c_iv_raw > 0 else round(c_iv_bs, 6)
        put_iv_out = p_iv_raw if p_iv_raw is not None and p_iv_raw > 0 else round(p_iv_bs, 6)

        rows.append(
            OptionStrikeRow(
                strike=k,
                expiration=expiry_used,
                call_bid=_safe_float(c.get("bid")),
                call_ask=_safe_float(c.get("ask")),
                call_last=_safe_float(c.get("lastPrice")),
                call_oi=c_oi if c_oi > 0 else None,
                call_oi_change=_safe_float(c.get("openInterestChange")),
                call_volume=_safe_float(c.get("volume")),
                call_iv=call_iv_out,
                call_delta=c_delta,
                call_gamma=c_gamma,
                call_theta=c_theta,
                call_vega=c_vega,
                call_gex=c_gex,
                call_bid_size=_safe_float(c.get("bidSize")),
                call_ask_size=_safe_float(c.get("askSize")),
                call_break_even=_safe_float(c.get("breakEvenPrice")),
                call_change=_safe_float(c.get("change")),
                call_change_pct=_safe_float(c.get("changePercent")),
                call_vwap=_safe_float(c.get("vwap")),
                call_day_open=_safe_float(c.get("open")),
                call_day_high=_safe_float(c.get("high")),
                call_day_low=_safe_float(c.get("low")),
                call_day_close=_safe_float(c.get("close")),
                call_previous_close=_safe_float(c.get("previousClose")),
                call_contract_ticker=str(c.get("contractTicker") or "") or None,
                call_exercise_style=str(c.get("exerciseStyle") or "") or None,
                call_shares_per_contract=_safe_float(c.get("sharesPerContract")),
                call_primary_exchange=str(c.get("primaryExchange") or "") or None,
                call_additional_underlyings_count=_safe_int(c.get("additionalUnderlyingsCount")),
                call_quote_age_ms=_age_ms_from_timestamp(c.get("quoteTimestamp")),
                call_last_trade_age_ms=_age_ms_from_timestamp(c.get("tradeTimestamp")),
                put_bid=_safe_float(p.get("bid")),
                put_ask=_safe_float(p.get("ask")),
                put_last=_safe_float(p.get("lastPrice")),
                put_oi=p_oi if p_oi > 0 else None,
                put_oi_change=_safe_float(p.get("openInterestChange")),
                put_volume=_safe_float(p.get("volume")),
                put_iv=put_iv_out,
                put_delta=p_delta,
                put_gamma=p_gamma,
                put_theta=p_theta,
                put_vega=p_vega,
                put_gex=p_gex,
                put_bid_size=_safe_float(p.get("bidSize")),
                put_ask_size=_safe_float(p.get("askSize")),
                put_break_even=_safe_float(p.get("breakEvenPrice")),
                put_change=_safe_float(p.get("change")),
                put_change_pct=_safe_float(p.get("changePercent")),
                put_vwap=_safe_float(p.get("vwap")),
                put_day_open=_safe_float(p.get("open")),
                put_day_high=_safe_float(p.get("high")),
                put_day_low=_safe_float(p.get("low")),
                put_day_close=_safe_float(p.get("close")),
                put_previous_close=_safe_float(p.get("previousClose")),
                put_contract_ticker=str(p.get("contractTicker") or "") or None,
                put_exercise_style=str(p.get("exerciseStyle") or "") or None,
                put_shares_per_contract=_safe_float(p.get("sharesPerContract")),
                put_primary_exchange=str(p.get("primaryExchange") or "") or None,
                put_additional_underlyings_count=_safe_int(p.get("additionalUnderlyingsCount")),
                put_quote_age_ms=_age_ms_from_timestamp(p.get("quoteTimestamp")),
                put_last_trade_age_ms=_age_ms_from_timestamp(p.get("tradeTimestamp")),
                call_vanna_exposure=c_vanna_usd,
                put_vanna_exposure=p_vanna_usd,
                net_vanna_exposure=net_vanna_val,
                call_charm_exposure=c_charm_usd,
                put_charm_exposure=p_charm_usd,
                net_charm_exposure=net_charm_val,
                total_oi=(c_oi + p_oi) if (c_oi + p_oi) > 0 else None,
                net_gex=net_gex_val,
                moneyness=moneyness,
            )
        )

    return (
        rows,
        np.array(strikes_arr, dtype=np.float64),
        np.array(call_oi_arr, dtype=np.float64),
        np.array(put_oi_arr, dtype=np.float64),
        np.array(call_iv_arr, dtype=np.float64),
        np.array(put_iv_arr, dtype=np.float64),
        tte,
        expiry_used,
    )


def _build_gex_levels(
    strikes: np.ndarray,
    call_oi: np.ndarray,
    put_oi: np.ndarray,
    call_iv: np.ndarray,
    put_iv: np.ndarray,
    spot: float,
    tte: float,
    r: float = 0.04,
) -> GEXLevelsResponse:
    """Compute GEX wall/flip levels from vectorized arrays."""
    if len(strikes) == 0:
        return GEXLevelsResponse()

    # Replace NaN IVs with ATM IV proxy
    atm_iv = atm_iv_from_chain(strikes, call_iv, put_iv, spot)
    if math.isnan(atm_iv):
        atm_iv = 0.25
    c_iv_clean = np.where(np.isfinite(call_iv) & (call_iv > 0), call_iv, atm_iv)
    p_iv_clean = np.where(np.isfinite(put_iv) & (put_iv > 0), put_iv, atm_iv)

    net_gex_arr, call_gex_arr, put_gex_arr = GEXMath.net_gex(
        strikes, call_oi, put_oi, c_iv_clean, p_iv_clean, spot, tte, r
    )
    net_total = float(np.sum(net_gex_arr))
    call_total = float(np.sum(call_gex_arr))
    put_total = float(np.sum(put_gex_arr))

    # ── Top-3 Call Walls (ranked by call_gex_arr descending) ─────────────────
    # Use argpartition for partial sort (O(n) vs O(n log n) for full sort)
    if len(call_gex_arr) >= 3:
        call_partitioned = np.argpartition(call_gex_arr, -3)[-3:]
        call_sorted_idx = call_partitioned[np.argsort(call_gex_arr[call_partitioned])[::-1]]
    else:
        call_sorted_idx = np.argsort(call_gex_arr)[::-1]

    cw_strong = int(call_sorted_idx[0]) if len(call_sorted_idx) > 0 else 0
    cw_moderate = int(call_sorted_idx[1]) if len(call_sorted_idx) > 1 else None
    cw_weak = int(call_sorted_idx[2]) if len(call_sorted_idx) > 2 else None

    # ── Top-3 Put Walls (ranked by put_gex_arr ascending = most negative first) ──
    # Use argpartition for partial sort (O(n) vs O(n log n) for full sort)
    if len(put_gex_arr) >= 3:
        put_partitioned = np.argpartition(put_gex_arr, 3)[:3]
        put_sorted_idx = put_partitioned[np.argsort(put_gex_arr[put_partitioned])]
    else:
        put_sorted_idx = np.argsort(put_gex_arr)

    pw_strong = int(put_sorted_idx[0]) if len(put_sorted_idx) > 0 else 0
    pw_moderate = int(put_sorted_idx[1]) if len(put_sorted_idx) > 1 else None
    pw_weak = int(put_sorted_idx[2]) if len(put_sorted_idx) > 2 else None

    zgl = GEXMath.zero_gamma_level(strikes, net_gex_arr)
    max_pain = OptionsEngine.calculate_max_pain(strikes, call_oi, put_oi)

    vex, cex = GEXMath.vanna_cex_exposure(
        strikes, call_oi, put_oi, c_iv_clean, p_iv_clean, spot, tte, r=r
    )
    spot_to_zgl = (spot - zgl) / max(spot, 1.0)
    dte_days = max(float(tte) * 365.0, 1.0)
    iv_atm = max(float(atm_iv) if math.isfinite(atm_iv) and atm_iv > 0 else 0.25, 1e-6)
    gamma_flip_p = estimate_gamma_flip_probability(spot, zgl, iv_atm, dte_days, r=r)
    sq_prob = GEXMath.squeeze_probability(net_total, vex, gamma_flip_p, spot_to_zgl)

    dealer_bias = "NEUTRAL"
    if net_total > 0:
        dealer_bias = "BULLISH"
    elif net_total < 0:
        dealer_bias = "BEARISH"

    return GEXLevelsResponse(
        call_wall=float(strikes[cw_strong]),
        call_wall_moderate=float(strikes[cw_moderate]) if cw_moderate is not None else None,
        call_wall_weak=float(strikes[cw_weak]) if cw_weak is not None else None,
        put_wall=float(strikes[pw_strong]),
        put_wall_moderate=float(strikes[pw_moderate]) if pw_moderate is not None else None,
        put_wall_weak=float(strikes[pw_weak]) if pw_weak is not None else None,
        zero_gamma_level=round(zgl, 4),
        max_pain=round(max_pain, 4),
        net_gex_total=round(net_total, 2),
        call_gex_total=round(call_total, 2),
        put_gex_total=round(put_total, 2),
        dealer_bias=dealer_bias,
        squeeze_probability=sq_prob,
        gex_formula_version="spotgamma_v1",
    )


def _build_iv_surface(
    raw_data: list[Any],
    spot: float,
    r: float = 0.04,
    underlying_closes: np.ndarray | None = None,
    vol_fetch_meta: dict[str, Any] | None = None,
) -> IVSurfaceResponse:
    """Build IV surface grid across all expiries in the raw chain."""
    if not isinstance(raw_data, list) or not raw_data:
        return IVSurfaceResponse(vol_underlying_meta=dict(vol_fetch_meta or {}))

    surface_points: list[IVSurfacePoint] = []
    atm_ivs_all: list[float] = []
    dtes_all: list[float] = []
    nearest_skew: dict[str, float] = {}
    nearest_tte: float = 999.0
    nearest_strikes = nearest_call_ivs = nearest_put_ivs = None
    cached_svi_nearest: Any = None

    for exp_block in raw_data:
        if not isinstance(exp_block, dict):
            continue
        exp_date: str = str(exp_block.get("expirationDate", ""))
        tte = _dte_from_expiry(exp_date)
        forward = spot * math.exp(r * tte)
        options_list = exp_block.get("options") or []
        if not isinstance(options_list, list):
            continue

        calls_k: dict[float, float] = {}
        puts_k: dict[float, float] = {}
        for opt in options_list:
            if not isinstance(opt, dict):
                continue
            k_raw = _safe_float(opt.get("strike"))
            if k_raw is None or k_raw <= 0:
                continue
            iv_raw = _safe_float(opt.get("impliedVolatility"))
            opt_type = str(opt.get("type", "")).upper()
            if opt_type == "CALL" and iv_raw is not None and iv_raw > 0:
                calls_k[k_raw] = iv_raw
            elif opt_type == "PUT" and iv_raw is not None and iv_raw > 0:
                puts_k[k_raw] = iv_raw

        all_k = sorted(set(calls_k) | set(puts_k))
        if not all_k:
            continue

        strikes_arr = np.array(all_k, dtype=np.float64)
        call_ivs_arr = np.array([calls_k.get(k, np.nan) for k in all_k], dtype=np.float64)
        put_ivs_arr = np.array([puts_k.get(k, np.nan) for k in all_k], dtype=np.float64)

        atm_iv_exp = atm_iv_from_chain(strikes_arr, call_ivs_arr, put_ivs_arr, spot)
        if math.isfinite(atm_iv_exp):
            atm_ivs_all.append(atm_iv_exp)
            dtes_all.append(tte * 365.0)

        # Track nearest expiry for skew/SVI
        if tte < nearest_tte:
            nearest_tte = tte
            nearest_strikes = strikes_arr
            nearest_call_ivs = call_ivs_arr
            nearest_put_ivs = put_ivs_arr

        # SVI calibration attempt
        svi_vols: dict[float, float] = {}
        mid_ivs_arr = np.where(
            np.isfinite(call_ivs_arr) & np.isfinite(put_ivs_arr),
            (call_ivs_arr + put_ivs_arr) / 2.0,
            np.where(np.isfinite(call_ivs_arr), call_ivs_arr, put_ivs_arr),
        )
        valid_mask = np.isfinite(mid_ivs_arr) & (mid_ivs_arr > 0) & (strikes_arr > 0)
        if np.sum(valid_mask) >= 5:
            try:
                svi_params = VolatilitySurfaceMath.svi_calibrate(
                    strikes_arr[valid_mask], mid_ivs_arr[valid_mask], tte, forward
                )
                svi_fitted = VolatilitySurfaceMath.svi_to_vol_slice(strikes_arr, svi_params)
                for idx_k, k_val in enumerate(all_k):
                    svi_vols[k_val] = (
                        float(svi_fitted[idx_k]) if np.isfinite(svi_fitted[idx_k]) else None
                    )  # type: ignore[assignment]
            except Exception:
                pass  # SVI calibration is best-effort

        for _idx_k, k_val in enumerate(all_k):
            c_iv = _safe_float(calls_k.get(k_val))
            p_iv = _safe_float(puts_k.get(k_val))
            mid_iv: float | None = None
            if c_iv is not None and p_iv is not None:
                mid_iv = round((c_iv + p_iv) / 2.0, 6)
            elif c_iv is not None:
                mid_iv = c_iv
            elif p_iv is not None:
                mid_iv = p_iv

            moneyness = round(math.log(k_val / forward), 6) if forward > 0 else 0.0

            surface_points.append(
                IVSurfacePoint(
                    expiration=exp_date,
                    dte=round(tte * 365.0, 1),
                    strike=k_val,
                    moneyness=moneyness,
                    call_iv=c_iv,
                    put_iv=p_iv,
                    mid_iv=mid_iv,
                    svi_iv=_safe_float(svi_vols.get(k_val)),
                )
            )

    # Term structure
    ts_result: dict[str, Any] = {}
    if len(atm_ivs_all) >= 2:
        ts_result = compute_term_structure(
            np.array(atm_ivs_all, dtype=np.float64), np.array(dtes_all, dtype=np.float64)
        )

    # Nearest-term skew (mid IVs; optional SVI-based 25Δ/10Δ overrides)
    atm_iv_near: float | None = None
    if nearest_strikes is not None and nearest_call_ivs is not None and nearest_put_ivs is not None:
        mid_skew = np.where(
            np.isfinite(nearest_call_ivs) & np.isfinite(nearest_put_ivs),
            (nearest_call_ivs + nearest_put_ivs) / 2.0,
            np.where(np.isfinite(nearest_call_ivs), nearest_call_ivs, nearest_put_ivs),
        )
        skew_m = compute_skew_metrics(nearest_strikes, mid_skew, spot)
        valid_m = np.isfinite(mid_skew) & (mid_skew > 0) & (nearest_strikes > 0)
        if np.sum(valid_m) >= 5:
            try:
                fwd_n = spot * math.exp(r * nearest_tte)
                cached_svi_nearest = VolatilitySurfaceMath.svi_calibrate(
                    nearest_strikes[valid_m], mid_skew[valid_m], nearest_tte, fwd_n
                )
                inst = compute_skew_metrics_institutional_svi(cached_svi_nearest, spot, r)
                for key in (
                    "atm_iv",
                    "skew_25d",
                    "risk_reversal",
                    "butterfly",
                    "risk_reversal_10",
                    "butterfly_10",
                ):
                    val = inst.get(key)
                    if val is None:
                        continue
                    try:
                        fv = float(val)
                    except (TypeError, ValueError):
                        continue
                    if math.isfinite(fv):
                        skew_m[key] = fv
            except Exception:
                cached_svi_nearest = None
        nearest_skew = {}
        for k, v in skew_m.items():
            try:
                fv = float(v)
            except (TypeError, ValueError):
                continue
            if math.isfinite(fv):
                nearest_skew[k] = fv
        atm_iv_near = nearest_skew.get("atm_iv")

    atm_iv_out = _safe_float(atm_iv_near) or (_safe_float(atm_ivs_all[-1]) if atm_ivs_all else None)

    vol_meta = dict(vol_fetch_meta or {})
    hv_20: float | None = None
    hv_60: float | None = None
    resolved_vrp: float | None = None
    resolved_iv_rank_hv_rolling: float | None = None
    resolved_iv_rank_cross_expiry: float | None = None
    iv_pct_cross: float | None = None

    if underlying_closes is not None and len(underlying_closes) >= 32:
        log_ret = np.diff(np.log(np.maximum(underlying_closes, 1e-12)))
        hv20_raw = historical_volatility(log_ret, 20)
        hv60_raw = historical_volatility(log_ret, 60)
        if math.isfinite(hv20_raw):
            hv_20 = round(float(hv20_raw), 6)
        if math.isfinite(hv60_raw):
            hv_60 = round(float(hv60_raw), 6)
        atm_iv_num = _safe_float(atm_iv_out)
        if atm_iv_num is not None and hv_20 is not None and hv_20 > 0:
            vrp_raw = vrp_log_ratio(float(atm_iv_num), float(hv_20))
            if math.isfinite(vrp_raw):
                resolved_vrp = round(float(vrp_raw), 6)

    # Volatility rank: (1) IV vs rolling HV history (2) IV ATM vs cross-expiry ATM IVs
    if atm_iv_out is not None:
        atm_iv_val = float(atm_iv_out)

        # 1. Historical — compare ATM IV to min–max of rolling 20d HV series (~1y)
        if underlying_closes is not None and len(underlying_closes) >= 40:
            log_ret_full = np.diff(np.log(np.maximum(underlying_closes, 1e-12)))
            hv_history = rolling_historical_volatility(log_ret_full, 20)
            valid_hv_hist = hv_history[np.isfinite(hv_history) & (hv_history > 0)]
            if len(valid_hv_hist) >= 10:
                h_lo, h_hi = float(np.min(valid_hv_hist)), float(np.max(valid_hv_hist))
                if h_hi > h_lo:
                    rnk_h = iv_rank(atm_iv_val, h_lo, h_hi)
                    if math.isfinite(rnk_h):
                        resolved_iv_rank_hv_rolling = round(float(rnk_h), 6)

        # 2. Cross-expiry — ATM IV vs min–max of per-expiry ATM IV in this snapshot
        if len(atm_ivs_all) >= 2:
            arr = np.array(atm_ivs_all, dtype=np.float64)
            valid_iv = arr[np.isfinite(arr) & (arr > 0)]
            if len(valid_iv) >= 2:
                lo, hi = float(np.min(valid_iv)), float(np.max(valid_iv))
                if hi > lo:
                    rnk_c = iv_rank(atm_iv_val, lo, hi)
                    if math.isfinite(rnk_c):
                        resolved_iv_rank_cross_expiry = round(float(rnk_c), 6)

            if len(valid_iv) >= 3:
                pct = iv_percentile(atm_iv_val, valid_iv)
                if math.isfinite(pct):
                    iv_pct_cross = round(float(pct), 6)

    # PDF analytics (SVI BL — nearest expiry only, best-effort)
    pdf_skew = pdf_kurt = pdf_left = pdf_right = pdf_regime = None
    svi_for_pdf = cached_svi_nearest
    if (
        svi_for_pdf is None
        and nearest_strikes is not None
        and nearest_call_ivs is not None
        and nearest_put_ivs is not None
        and nearest_tte < 999.0
    ):
        mid_arr = np.where(
            np.isfinite(nearest_call_ivs) & np.isfinite(nearest_put_ivs),
            (nearest_call_ivs + nearest_put_ivs) / 2.0,
            np.where(np.isfinite(nearest_call_ivs), nearest_call_ivs, nearest_put_ivs),
        )
        valid_m = np.isfinite(mid_arr) & (mid_arr > 0) & (nearest_strikes > 0)
        if np.sum(valid_m) >= 5:
            try:
                fwd = spot * math.exp(r * nearest_tte)
                svi_for_pdf = VolatilitySurfaceMath.svi_calibrate(
                    nearest_strikes[valid_m], mid_arr[valid_m], nearest_tte, fwd
                )
            except Exception:
                pass
    if svi_for_pdf is not None:
        try:
            pdf_result = VolatilitySurfaceMath.bl_pdf(svi_for_pdf, r=r)
            pdf_skew = pdf_result.skewness
            pdf_kurt = pdf_result.excess_kurtosis
            pdf_left = pdf_result.left_tail_prob
            pdf_right = pdf_result.right_tail_prob
            pdf_regime = pdf_result.tail_regime
        except Exception:
            pass

    return IVSurfaceResponse(
        surface=surface_points,
        atm_iv=atm_iv_out,
        iv_rank_hv_rolling=resolved_iv_rank_hv_rolling,
        iv_rank_cross_expiry=resolved_iv_rank_cross_expiry,
        vrp=resolved_vrp,
        hv_20=hv_20,
        hv_60=hv_60,
        iv_percentile_cross_term=iv_pct_cross,
        vol_underlying_meta=vol_meta,
        skew_25d=nearest_skew.get("skew_25d"),
        risk_reversal=nearest_skew.get("risk_reversal"),
        butterfly=nearest_skew.get("butterfly"),
        risk_reversal_10=nearest_skew.get("risk_reversal_10"),
        butterfly_10=nearest_skew.get("butterfly_10"),
        skew_slope=nearest_skew.get("skew_slope"),
        term_structure=dict(ts_result.items()),
        pdf_skewness=_safe_float(pdf_skew),
        pdf_excess_kurtosis=_safe_float(pdf_kurt),
        pdf_left_tail=_safe_float(pdf_left),
        pdf_right_tail=_safe_float(pdf_right),
        pdf_tail_regime=pdf_regime,
    )


def _iv_rank_confluence_hint(iv: IVSurfaceResponse) -> float | None:
    """Prefer HV-rolling IV rank; fallback cross-expiry ATM IV rank for IV sub-score."""
    if iv.iv_rank_hv_rolling is not None and math.isfinite(iv.iv_rank_hv_rolling):
        return float(iv.iv_rank_hv_rolling)
    if iv.iv_rank_cross_expiry is not None and math.isfinite(iv.iv_rank_cross_expiry):
        return float(iv.iv_rank_cross_expiry)
    return None


def _build_confluence(
    ticker: str,
    strikes: np.ndarray,
    call_oi: np.ndarray,
    put_oi: np.ndarray,
    call_iv: np.ndarray,
    put_iv: np.ndarray,
    spot: float,
    tte: float,
    gex_levels: GEXLevelsResponse,
    r: float = 0.04,
    *,
    iv_rank_hint: float | None = None,
    vrp_hint: float | None = None,
    rows: list[OptionStrikeRow] | None = None,
) -> tuple[ConfluenceResponse, OptionsResult | None]:
    """Build a lightweight confluence snapshot from engine outputs."""
    if len(strikes) == 0:
        return ConfluenceResponse(ticker=ticker), None

    # Calculate ATM IV once and reuse
    atm_iv = atm_iv_from_chain(strikes, call_iv, put_iv, spot)
    if math.isnan(atm_iv):
        atm_iv = 0.25
    c_iv_clean = np.where(np.isfinite(call_iv) & (call_iv > 0), call_iv, atm_iv)
    p_iv_clean = np.where(np.isfinite(put_iv) & (put_iv > 0), put_iv, atm_iv)

    engine_result = OptionsEngine.analyze_chain(
        ticker=ticker,
        spot=spot,
        strikes=strikes,
        call_oi=call_oi,
        put_oi=put_oi,
        call_iv=c_iv_clean,
        put_iv=p_iv_clean,
        tte=tte,
        atm_iv=atm_iv,
        r=r,
    )

    # Extract vanna/charm totals from engine result to avoid redundant calculation
    vanna_x = engine_result.exposures.total_vex
    cex = engine_result.exposures.total_cex
    vanna_regime = engine_result.exposures.vex_regime
    cex_regime = engine_result.exposures.cex_regime

    # Derive a simple confluence score from GEX + IV signals
    gex_score = float(
        np.clip(
            gex_levels.net_gex_total / max(abs(gex_levels.net_gex_total) + 1e-6, 1.0), -1.0, 1.0
        )
    )
    iv_score = 0.0
    if iv_rank_hint is not None and math.isfinite(iv_rank_hint):
        iv_score = float(np.clip(2.0 * (float(iv_rank_hint) - 0.5), -1.0, 1.0))
    elif vrp_hint is not None and math.isfinite(vrp_hint):
        iv_score = float(np.clip(math.tanh(float(vrp_hint)), -1.0, 1.0))

    total_oi_c = float(np.nansum(call_oi))
    total_oi_p = float(np.nansum(put_oi))
    pcr_oi: float | None = round(total_oi_p / total_oi_c, 4) if total_oi_c > 0 else None

    # PCR volume = put_volume / call_volume (same convention as pcr_oi), summed per strike row
    total_vol_c = 0.0
    total_vol_p = 0.0
    if rows:
        for row in rows:
            cv = row.call_volume
            pv = row.put_volume
            if cv is not None and math.isfinite(float(cv)):
                total_vol_c += float(cv)
            if pv is not None and math.isfinite(float(pv)):
                total_vol_p += float(pv)
    pcr_vol: float | None = round(total_vol_p / total_vol_c, 4) if total_vol_c > 0 else None

    hhi = engine_result.positioning.hhi_concentration if engine_result.ok else 0.0
    squeeze = gex_levels.squeeze_probability
    squeeze_override = squeeze > 0.85

    signal = "WAIT"
    confidence = round(abs(gex_score) * 0.6 + (1.0 - hhi) * 0.4, 4)
    conviction = "LOW"
    if confidence >= 0.65:
        conviction = "HIGH"
    elif confidence >= 0.40:
        conviction = "MEDIUM"

    if gex_score > 0.25 and not squeeze_override:
        signal = "BUY"
    elif squeeze_override:
        signal = "CASH"

    conf = ConfluenceResponse(
        ticker=ticker,
        score=round(gex_score * 0.5 + iv_score * 0.15, 4),
        signal=signal,
        confidence=confidence,
        conviction=conviction,
        gex_sub_score=round(gex_score, 4),
        iv_sub_score=round(iv_score, 4),
        squeeze_override=squeeze_override,
        vanna_exposure_regime=vanna_regime.value,
        vex_regime=vanna_regime.value,
        cex_regime=cex_regime.value,
        total_vanna_exposure=round(vanna_x, 4),
        total_vex=round(vanna_x, 4),
        total_cex=round(cex, 6),
        hhi_concentration=round(hhi, 4),
        pcr_oi=pcr_oi,
        pcr_volume=pcr_vol,
    )
    return conf, engine_result


# ─────────────────────────────────────────────────────────────────────────────
# §3  SNAPSHOT SERVICE (REST + WS aggregator)
# ─────────────────────────────────────────────────────────────────────────────


async def _fetch_options_raw_chain_uncached(
    sym: str,
) -> tuple[dict[str, Any] | None, str, dict[str, Any]]:
    loop = asyncio.get_event_loop()
    raw, chain_src, fetch_meta = await loop.run_in_executor(
        None,
        functools.partial(fetch_option_chain_raw, sym, None),
    )
    meta = dict(fetch_meta) if isinstance(fetch_meta, dict) else {}
    return raw, chain_src or "", meta


def _start_options_raw_chain_task(
    sym: str,
    cache_key: str,
) -> asyncio.Task[tuple[dict[str, Any] | None, str, dict[str, Any]]]:
    task = asyncio.create_task(_fetch_options_raw_chain_uncached(sym))
    _OPTIONS_RAW_CHAIN_INFLIGHT[cache_key] = task

    def _remember_result(
        done: asyncio.Task[tuple[dict[str, Any] | None, str, dict[str, Any]]],
    ) -> None:
        try:
            result = done.result()
        except (
            Exception,
            asyncio.CancelledError,
        ) as exc:  # pragma: no cover - provider failures are handled by callers
            logger.warning("Options raw chain refresh failed for %s: %s", sym, exc)
        else:
            _OPTIONS_RAW_CHAIN_CACHE[cache_key] = (time.monotonic(), result)
        finally:
            if _OPTIONS_RAW_CHAIN_INFLIGHT.get(cache_key) is done:
                _OPTIONS_RAW_CHAIN_INFLIGHT.pop(cache_key, None)

    task.add_done_callback(_remember_result)
    return task


async def _load_options_raw_chain(
    sym: str,
) -> tuple[dict[str, Any] | None, str, dict[str, Any]]:
    """Shared full-chain provider load for Options/GEX and Predictive consumers."""
    symbol = sym.upper().strip()
    cache_key = symbol
    now = time.monotonic()
    cached = _OPTIONS_RAW_CHAIN_CACHE.get(cache_key)
    if cached is not None and now - cached[0] <= _OPTIONS_RAW_CHAIN_TTL_S:
        return cached[1]

    task = _OPTIONS_RAW_CHAIN_INFLIGHT.get(cache_key)
    if cached is not None:
        if task is None or task.done():
            _start_options_raw_chain_task(symbol, cache_key)
        return cached[1]

    if task is None or task.done():
        task = _start_options_raw_chain_task(symbol, cache_key)
    return await asyncio.shield(task)


async def _options_snapshot_service_uncached(
    symbol: str,
    expiry: str | None,
    r: float,
) -> OptionsSnapshotResponse:
    """Construye el snapshot completo (GET /snapshot y ``/ws/options``)."""
    sym = symbol.upper().strip()
    as_of = datetime.now(tz=UTC).isoformat()

    loop = asyncio.get_event_loop()
    chain_task = _load_options_raw_chain(sym)
    bars_task = loop.run_in_executor(
        None,
        functools.partial(fetch_equity_daily_bars, sym),
    )
    (raw, chain_src, fetch_meta), (closes_arr, ohlcv_df, vol_bars_meta) = await asyncio.gather(
        chain_task,
        bars_task,
    )
    logger.info("options_snapshot: sym=%s chain_source=%s", sym, chain_src or "none")

    dl0 = raw.get("data") if isinstance(raw, dict) else None
    if raw is None or not isinstance(dl0, list) or len(dl0) == 0:
        logger.warning(
            "options_snapshot: no chain data for %s (source=%s)", sym, chain_src or "none"
        )
        fd = dict(fetch_meta) if isinstance(fetch_meta, dict) else {}
        fd["equity_bars"] = vol_bars_meta
        return OptionsSnapshotResponse(
            ticker=sym,
            spot=0.0,
            as_of=as_of,
            expiries=[],
            chain=[],
            total_dex=0.0,
            dex_flip_level=None,
            gex_levels=GEXLevelsResponse(),
            iv_surface=IVSurfaceResponse(vol_underlying_meta=dict(vol_bars_meta)),
            confluence=ConfluenceResponse(ticker=sym),
            ok=False,
            error=(
                "Option chain unavailable — set FINNHUB_API_KEY and/or any MASSIVE_KEY_* "
                "(FINANCIALS, DISTRESS, MACRO, WS_QUOTES, WS_TRADES) with options snapshot access"
            ),
            chain_quality={"provider": chain_src or "none", "fetch_details": fd},
            engine_signal={},
            options_gex_features=assemble_options_gex_features(
                {
                    "spot": 0.0,
                    "chain": [],
                    "chain_quality": {"provider": chain_src or "none", "fetch_details": fd},
                    "engine_signal": {},
                    "gex_levels": {},
                    "iv_surface": {"vol_underlying_meta": dict(vol_bars_meta)},
                }
            ),
            smc_gex_confluence=SMCGEXConfluenceSnapshot(),
        )

    # Extract spot from quote field in payload if present
    spot_raw = _safe_float(
        raw.get("quote", {}).get("c") if isinstance(raw.get("quote"), dict) else None
    )
    if spot_raw is None:
        # Attempt to derive from ATM strikes
        data_list = raw.get("data") or []
        if isinstance(data_list, list) and data_list:
            first_block = data_list[0] if isinstance(data_list[0], dict) else {}
            spot_raw = _safe_float(first_block.get("underlying", {}).get("close"))

    spot = spot_raw or 100.0  # fallback — UI will show N/A on zero spot
    logger.info("options_snapshot: sym=%s spot=%.2f", sym, spot)

    # Extract all expiry dates
    expiry_blocks: list[Any] = raw.get("data") or []
    expiries: list[str] = []
    if isinstance(expiry_blocks, list):
        expiries = [
            str(blk.get("expirationDate", ""))
            for blk in expiry_blocks
            if isinstance(blk, dict) and blk.get("expirationDate")
        ]

    # Parse chain for selected expiry
    rows, strikes, call_oi, put_oi, call_iv, put_iv, tte, expiry_used = _parse_finnhub_chain(
        raw, spot, expiry, r
    )
    rows, total_dex, dex_flip_level = enrich_chain_with_dex(rows, spot)

    # GEX levels
    gex_levels = _build_gex_levels(strikes, call_oi, put_oi, call_iv, put_iv, spot, tte, r)

    smc_gex = await loop.run_in_executor(
        None,
        functools.partial(_smc_gex_confluence_blocking, sym, spot, ohlcv_df, gex_levels),
    )

    # IV surface (all expiries) + HV/VRP desde aggs del subyacente
    iv_surface = _build_iv_surface(
        expiry_blocks,
        spot,
        r,
        underlying_closes=closes_arr,
        vol_fetch_meta=vol_bars_meta,
    )

    # Confluence (+ OptionsResult para señal MIC)
    confluence, engine_res = _build_confluence(
        ticker=sym,
        strikes=strikes,
        call_oi=call_oi,
        put_oi=put_oi,
        call_iv=call_iv,
        put_iv=put_iv,
        spot=spot,
        tte=tte,
        gex_levels=gex_levels,
        r=r,
        iv_rank_hint=_iv_rank_confluence_hint(iv_surface),
        vrp_hint=iv_surface.vrp,
        rows=rows,
    )

    engine_signal: dict[str, Any] = {}
    if engine_res is not None and engine_res.ok:
        sig = OptionsEngine.generate_signal(engine_res)
        surf = engine_res.surface
        greek_surface_populated = bool(surf.speed or surf.zomma or surf.color or surf.ultima)
        engine_signal = {
            "regime": sig.regime.value,
            "vanna_exposure_score": sig.vex_score,
            "cex_score": sig.cex_score,
            "skewness_premium": sig.skewness_premium,
            "squeeze_score": engine_res.options_mic_score,
            "total_gex": engine_res.exposures.total_gex,
            "total_vanna_exposure": engine_res.exposures.total_vex,
            "total_vex": engine_res.exposures.total_vex,
            "total_cex": engine_res.exposures.total_cex,
            "vanna_exposure_regime": engine_res.exposures.vex_regime.value,
            "vex_regime": engine_res.exposures.vex_regime.value,
            "cex_regime": engine_res.exposures.cex_regime.value,
            "max_gex_strike": engine_res.positioning.max_gex_strike,
            "hhi_concentration": engine_res.positioning.hhi_concentration,
            "vanna_sensitivity": engine_res.vanna_volatility_sensitivity,
            "charm_acceleration": engine_res.charm_time_decay_acceleration,
            "pdf_analytics": engine_res.pdf_analytics.model_dump(),
            "greek_surface_populated": greek_surface_populated,
        }

    quality = _chain_quality_dict(rows, expiries, expiry_used, chain_src, fetch_meta)
    quality["equity_bars"] = vol_bars_meta
    if ohlcv_df is not None and not getattr(ohlcv_df, "empty", True):
        try:
            from backend.layer_3_specialists.tecnico.volume_profile import VolumeProfileEngine

            vp = VolumeProfileEngine.calculate(ohlcv_df)
            if vp.ok and float(vp.val) > 0 and float(vp.vah) > float(vp.val):
                quality["value_area"] = {
                    "val": round(float(vp.val), 6),
                    "vah": round(float(vp.vah), 6),
                    "poc": round(float(vp.poc), 6),
                    "source": "massive_equity_bars",
                    "bars": int(len(ohlcv_df)),
                }
        except Exception as exc:
            logger.debug("options_snapshot.value_area_failed sym=%s error=%s", sym, exc)
    options_gex_features = assemble_options_gex_features(
        {
            "spot": spot,
            "chain": rows,
            "total_dex": total_dex,
            "dex_flip_level": dex_flip_level,
            "gex_levels": gex_levels,
            "iv_surface": iv_surface,
            "chain_quality": quality,
            "engine_signal": engine_signal,
        }
    )

    ndde_val = _safe_float(raw.get("ndde")) if isinstance(raw, dict) else None
    charm_flow_val = raw.get("charm_flow") if isinstance(raw, dict) else None
    if isinstance(charm_flow_val, int | float):
        charm_flow_val = float(charm_flow_val)
    elif charm_flow_val is not None:
        charm_flow_val = str(charm_flow_val)
    implied_p99_val = (
        _safe_float(raw.get("implied_percentile_99")) if isinstance(raw, dict) else None
    )

    return OptionsSnapshotResponse(
        ticker=sym,
        spot=round(spot, 4),
        as_of=as_of,
        expiries=expiries,
        chain=rows,
        total_dex=total_dex,
        dex_flip_level=dex_flip_level,
        gex_levels=gex_levels,
        iv_surface=iv_surface,
        confluence=confluence,
        ok=True,
        chain_quality=quality,
        engine_signal=engine_signal,
        options_gex_features=options_gex_features,
        smc_gex_confluence=smc_gex,
        ndde=ndde_val,
        charm_flow=charm_flow_val,
        implied_percentile_99=implied_p99_val,
    )


async def options_snapshot_service(
    symbol: str,
    expiry: str | None,
    r: float,
) -> OptionsSnapshotResponse:
    """Construye o reutiliza el snapshot completo para rutas internas."""
    sym = symbol.upper().strip()
    cache_key = f"{sym}:{expiry or ''}:{round(float(r), 5)}"
    now = time.monotonic()
    cached = _OPTIONS_SNAPSHOT_SERVICE_CACHE.get(cache_key)
    if cached is not None and now - cached[0] <= _OPTIONS_SNAPSHOT_SERVICE_TTL_S:
        return cached[1]

    inflight = _OPTIONS_SNAPSHOT_SERVICE_INFLIGHT.get(cache_key)
    if cached is not None:
        if inflight is None or inflight.done():
            task = asyncio.create_task(_options_snapshot_service_uncached(sym, expiry, r))
            _OPTIONS_SNAPSHOT_SERVICE_INFLIGHT[cache_key] = task

            def _remember_snapshot(done: asyncio.Task[OptionsSnapshotResponse]) -> None:
                try:
                    snap = done.result()
                except Exception as exc:  # pragma: no cover - stale cache remains valid
                    logger.warning(
                        "Options snapshot background refresh failed for %s: %s", sym, exc
                    )
                else:
                    _OPTIONS_SNAPSHOT_SERVICE_CACHE[cache_key] = (time.monotonic(), snap)
                    _persist_options_gex_snapshot_forward(snap)
                finally:
                    if _OPTIONS_SNAPSHOT_SERVICE_INFLIGHT.get(cache_key) is done:
                        _OPTIONS_SNAPSHOT_SERVICE_INFLIGHT.pop(cache_key, None)

            task.add_done_callback(_remember_snapshot)
        return cached[1]

    if inflight is not None and not inflight.done():
        return await asyncio.shield(inflight)

    task = asyncio.create_task(_options_snapshot_service_uncached(sym, expiry, r))
    _OPTIONS_SNAPSHOT_SERVICE_INFLIGHT[cache_key] = task
    try:
        snap = await asyncio.shield(task)
        _OPTIONS_SNAPSHOT_SERVICE_CACHE[cache_key] = (time.monotonic(), snap)
        _persist_options_gex_snapshot_forward(snap)
        return snap
    finally:
        _OPTIONS_SNAPSHOT_SERVICE_INFLIGHT.pop(cache_key, None)


def _persist_options_gex_snapshot_forward(snapshot: OptionsSnapshotResponse) -> None:
    try:
        result = _OPTIONS_GEX_SNAPSHOT_STORE.persist(snapshot)
    except Exception as exc:  # pragma: no cover - live endpoints must degrade
        logger.warning("Options/GEX forward snapshot persistence failed: %s", str(exc)[:180])
        return
    if bool(getattr(result, "inserted", False)):
        logger.info(
            "Options/GEX forward snapshot persisted id=%s",
            getattr(result, "snapshot_id", "unknown"),
        )


async def load_option_chain_rows(
    sym: str,
    expiry: str | None,
    r: float,
) -> tuple[list[OptionStrikeRow], str, str, dict[str, Any]]:
    """Cadena liviana: filas + ``as_of`` + fuente + meta de fetch.

    Misma estrategia de fetch que ``options_snapshot_service`` (``expiry=None`` al
    proveedor): evita respuestas Massive/Polygon distintas al filtrar por
    ``expiration_date`` (NBBO/IV incompletos) frente al snapshot completo.
    El filtro por expiry sigue aplicándose en ``_parse_finnhub_chain``.
    """
    raw, chain_src, fetch_meta = await _load_options_raw_chain(sym)
    as_of = datetime.now(tz=UTC).isoformat()
    meta = dict(fetch_meta) if isinstance(fetch_meta, dict) else {}
    if raw is None:
        return [], as_of, chain_src or "", meta
    spot_raw = _safe_float(
        raw.get("quote", {}).get("c") if isinstance(raw.get("quote"), dict) else None
    )
    spot = spot_raw or 100.0
    rows, *_ = _parse_finnhub_chain(raw, spot, expiry, r)
    rows, _, _ = enrich_chain_with_dex(rows, spot)
    return rows, as_of, chain_src or "", meta


async def options_chain_stream_payload(symbol: str, expiry: str | None, r: float) -> dict[str, Any]:
    """Evento JSON ``chain`` para el WebSocket agregador (``api_server``)."""
    sym_u = symbol.upper().strip()
    rows, as_of, src, meta = await load_option_chain_rows(sym_u, expiry, r)
    return {
        "type": "chain",
        "as_of": as_of,
        "ticker": sym_u,
        "rows": [row.model_dump() for row in rows],
        "chain_source": src,
        "fetch_meta": meta,
    }


def _options_snapshot_etag(as_of: str) -> str:
    """Weak ETag from snapshot ``as_of`` — conditional GET / bandwith sin recomputar JSON en cliente."""
    digest = hashlib.sha256(as_of.encode("utf-8")).hexdigest()[:24]
    return f'W/"{digest}"'


def _if_none_match_has_etag(if_none_match: str | None, etag: str) -> bool:
    if not if_none_match or not etag:
        return False
    for token in if_none_match.split(","):
        if token.strip() == etag:
            return True
    return False


_SNAPSHOT_CACHE_CONTROL = "private, max-age=25"


def _spot_from_options_raw(raw: dict[str, Any]) -> float:
    spot_raw = _safe_float(
        raw.get("quote", {}).get("c") if isinstance(raw.get("quote"), dict) else None
    )
    if spot_raw is None:
        dl = raw.get("data") or []
        if isinstance(dl, list) and dl:
            first_block = dl[0] if isinstance(dl[0], dict) else {}
            underlying = first_block.get("underlying") if isinstance(first_block, dict) else None
            if isinstance(underlying, dict):
                spot_raw = _safe_float(underlying.get("close") or underlying.get("price"))
    return spot_raw or 100.0


def _expiry_list_from_options_raw(raw: dict[str, Any]) -> list[str]:
    data_list = raw.get("data") or []
    if not isinstance(data_list, list):
        return []
    out: list[str] = []
    for block in data_list:
        if not isinstance(block, dict):
            continue
        exp = str(block.get("expirationDate", ""))[:10]
        if exp and exp not in out:
            out.append(exp)
    return out


def _parse_rows_for_chain_analytics(
    raw: dict[str, Any],
    spot: float,
    expiry: str | None,
    r: float,
) -> list[OptionStrikeRow]:
    if expiry:
        rows, *_ = _parse_finnhub_chain(raw, spot, expiry, r)
        rows, _, _ = enrich_chain_with_dex(rows, spot)
        return rows

    out: list[OptionStrikeRow] = []
    for exp in _expiry_list_from_options_raw(raw):
        rows, *_ = _parse_finnhub_chain(raw, spot, exp, r)
        rows, _, _ = enrich_chain_with_dex(rows, spot)
        out.extend(rows)
    return out


def _age_ms_from_timestamp(value: object, now_ms: float | None = None) -> float | None:
    ts = _safe_float(value)
    if ts is None or ts <= 0:
        return None
    if ts > 1.0e15:
        ts = ts / 1_000_000.0
    elif ts > 1.0e12 or ts > 1.0e10:
        ts = ts / 1_000.0
    elif ts > 1.0e9:
        pass
    else:
        return None
    now = now_ms if now_ms is not None else time.time() * 1000.0
    ts_ms = ts * 1000.0 if ts < 1.0e11 else ts
    age = now - ts_ms
    if age < 0:
        return 0.0
    return round(age, 2)


def _chain_analytics_cache_key(symbol: str, expiry: str | None, r: float) -> str:
    return f"{symbol.upper().strip()}:{expiry or ''}:{round(float(r), 5)}"


def _clone_chain_analytics_response(
    response: ChainInstitutionalAnalyticsResponse,
    warning: str | None = None,
    error: str | None = None,
) -> ChainInstitutionalAnalyticsResponse:
    out = response.model_copy(deep=True)
    if warning and warning not in out.quality.warnings:
        out.quality.warnings.append(warning)
    if error:
        out.error = error
    return out


def _empty_chain_analytics_response(
    symbol: str,
    as_of: str,
    provider: str | None,
    warning: str,
    error: str,
) -> ChainInstitutionalAnalyticsResponse:
    return ChainInstitutionalAnalyticsResponse(
        ticker=symbol,
        spot=0.0,
        as_of=as_of,
        ok=False,
        error=error,
        quality={
            "provider": provider or "none",
            "rows": 0,
            "warnings": [warning],
            "data_quality_score": 0.0,
        },
    )


async def _options_chain_analytics_uncached(
    sym: str,
    expiry: str | None,
    r: float,
) -> ChainInstitutionalAnalyticsResponse:
    loop = asyncio.get_event_loop()
    raw, chain_src, fetch_meta = await _load_options_raw_chain(sym)
    as_of = datetime.now(tz=UTC).isoformat()
    if raw is None or not isinstance(raw.get("data"), list) or len(raw.get("data") or []) == 0:
        return _empty_chain_analytics_response(
            sym,
            as_of,
            chain_src or "none",
            "empty_chain",
            "Option chain unavailable",
        )

    spot = _spot_from_options_raw(raw)
    rows = _parse_rows_for_chain_analytics(raw, spot, expiry, r)
    response = build_chain_institutional_analytics(
        sym,
        spot,
        rows,
        r=r,
        provider=chain_src or "",
        as_of=as_of,
    )
    if isinstance(fetch_meta, dict) and fetch_meta.get("maybe_truncated"):
        response.quality.warnings.append("provider_maybe_truncated")
        response.quality.data_quality_score = max(response.quality.data_quality_score - 8.0, 0.0)
        response.institutional_metrics.data_quality_score = response.quality.data_quality_score
    try:
        response = await loop.run_in_executor(
            None,
            functools.partial(enrich_chain_analytics_with_history, response, expiry),
        )
    except Exception as exc:  # pragma: no cover - history is non-critical runtime persistence
        logger.warning("Options chain analytics history persistence failed for %s: %s", sym, exc)
        response.quality.warnings.append("history_persistence_failed")
    return response


async def options_chain_analytics_service(
    symbol: str,
    expiry: str | None,
    r: float,
) -> ChainInstitutionalAnalyticsResponse:
    sym = symbol.upper().strip()
    cache_key = _chain_analytics_cache_key(sym, expiry, r)
    now = time.monotonic()
    cached = _OPTIONS_CHAIN_ANALYTICS_CACHE.get(cache_key)
    if cached is not None and now - cached[0] <= _OPTIONS_CHAIN_ANALYTICS_TTL_S:
        return _clone_chain_analytics_response(cached[1])

    task = _OPTIONS_CHAIN_ANALYTICS_INFLIGHT.get(cache_key)
    if task is None or task.done():
        task = asyncio.create_task(_options_chain_analytics_uncached(sym, expiry, r))
        _OPTIONS_CHAIN_ANALYTICS_INFLIGHT[cache_key] = task

        def _remember_result(done: asyncio.Task[ChainInstitutionalAnalyticsResponse]) -> None:
            try:
                response = done.result()
            except Exception as exc:  # pragma: no cover - defensive logging
                logger.warning("Options chain analytics refresh failed for %s: %s", sym, exc)
            else:
                _OPTIONS_CHAIN_ANALYTICS_CACHE[cache_key] = (time.monotonic(), response)
            finally:
                if _OPTIONS_CHAIN_ANALYTICS_INFLIGHT.get(cache_key) is done:
                    _OPTIONS_CHAIN_ANALYTICS_INFLIGHT.pop(cache_key, None)

        task.add_done_callback(_remember_result)

    try:
        response = await asyncio.wait_for(
            asyncio.shield(task),
            timeout=_OPTIONS_CHAIN_ANALYTICS_TIMEOUT_S,
        )
    except TimeoutError:
        if cached is not None:
            return _clone_chain_analytics_response(
                cached[1],
                warning="stale_cache_provider_timeout",
                error="Provider refresh timed out; serving cached chain analytics.",
            )
        return _empty_chain_analytics_response(
            sym,
            datetime.now(tz=UTC).isoformat(),
            "none",
            "provider_timeout",
            "Provider refresh timed out",
        )
    except Exception as exc:
        if cached is not None:
            return _clone_chain_analytics_response(
                cached[1],
                warning="stale_cache_provider_refresh_failed",
                error=f"Provider refresh failed; serving cached chain analytics: {exc}",
            )
        return _empty_chain_analytics_response(
            sym,
            datetime.now(tz=UTC).isoformat(),
            "none",
            "provider_refresh_failed",
            f"Provider refresh failed: {exc}",
        )

    _OPTIONS_CHAIN_ANALYTICS_CACHE[cache_key] = (time.monotonic(), response)
    return _clone_chain_analytics_response(response)


# ─────────────────────────────────────────────────────────────────────────────
# §4  ENDPOINTS (REST)
# ─────────────────────────────────────────────────────────────────────────────


@router.get(
    "/snapshot/{symbol}",
    response_model=OptionsSnapshotResponse,
    summary="Full options snapshot — chain + GEX levels + IV surface + confluence",
)
async def get_options_snapshot(
    request: Request,
    symbol: str,
    expiry: str | None = Query(
        default=None,
        description="ISO date filter (YYYY-MM-DD). If omitted, nearest-term expiry is used.",
    ),
    r: float = Query(default=0.04, ge=0.0, le=1.0, description="Risk-free rate (annual)"),
) -> Response | JSONResponse:
    """
    Master endpoint. Returns the complete options snapshot for one expiry.
    Polling: UI default 30s with ``If-None-Match`` / ``ETag`` (304 si ``as_of`` sin cambio).
    Provider: Finnhub first; if empty, REST Massive/Polygon (`/v3/snapshot/options`) with every
    `MASSIVE_KEY_*` in .env across default hosts (override via `MASSIVE_REST_BASE_URLS`).
    Recommend 30s poll on UI. Graceful degradation: empty chain → ok=False.
    En paralelo: aggs diarios del subyacente (Massive/Polygon) para HV/VRP vía
    ``MASSIVE_KEY_OPTIONS_*`` y el resto de claves Massive configuradas.
    """
    sym = symbol.upper().strip()
    if not sym:
        raise HTTPException(status_code=400, detail="symbol required")
    snap = await options_snapshot_service(sym, expiry, r)
    etag = _options_snapshot_etag(snap.as_of)
    if _if_none_match_has_etag(request.headers.get("if-none-match"), etag):
        return Response(
            status_code=304,
            headers={
                "ETag": etag,
                "Cache-Control": _SNAPSHOT_CACHE_CONTROL,
            },
        )
    return JSONResponse(
        content=jsonable_encoder(snap),
        headers={
            "ETag": etag,
            "Cache-Control": _SNAPSHOT_CACHE_CONTROL,
        },
    )


@router.get(
    "/chain/{symbol}",
    response_model=list[OptionStrikeRow],
    summary="Options chain rows only — lighter endpoint for table refresh",
)
async def get_options_chain(
    symbol: str,
    expiry: str | None = Query(default=None),
    r: float = Query(default=0.04, ge=0.0, le=1.0),
) -> list[OptionStrikeRow]:
    """
    Returns only the chain rows. Use for frequent table polling without
    re-computing the full surface.
    """
    sym = symbol.upper().strip()
    if not sym:
        raise HTTPException(status_code=400, detail="symbol required")

    rows, _, _, _ = await load_option_chain_rows(sym, expiry, r)
    return rows


@router.get(
    "/chain-analytics/{symbol}",
    response_model=ChainInstitutionalAnalyticsResponse,
    summary="Institutional chain analytics — contract, strike, expiry and multi-expiry metrics",
)
async def get_options_chain_analytics(
    symbol: str,
    expiry: str | None = Query(
        default=None,
        description="ISO expiry filter. If omitted, all available expiries are aggregated.",
    ),
    r: float = Query(default=0.04, ge=0.0, le=1.0),
) -> ChainInstitutionalAnalyticsResponse:
    sym = symbol.upper().strip()
    if not sym:
        raise HTTPException(status_code=400, detail="symbol required")

    return await options_chain_analytics_service(sym, expiry, r)


@router.get(
    "/chain-analytics-history/{symbol}",
    response_model=ChainAnalyticsHistoryResponse,
    summary="Institutional chain analytics temporal history",
)
async def get_options_chain_analytics_history(
    symbol: str,
    expiry: str | None = Query(
        default=None,
        description="ISO expiry history scope. If omitted, reads the all-expiry scope.",
    ),
    limit: int = Query(default=20, ge=1, le=250),
) -> ChainAnalyticsHistoryResponse:
    sym = symbol.upper().strip()
    if not sym:
        raise HTTPException(status_code=400, detail="symbol required")
    try:
        return _OPTIONS_CHAIN_ANALYTICS_HISTORY_STORE.history_response(
            sym, expiry=expiry, limit=limit
        )
    except Exception as exc:  # pragma: no cover - defensive endpoint guard
        logger.warning("Options chain analytics history read failed for %s: %s", sym, exc)
        return ChainAnalyticsHistoryResponse(
            ticker=sym,
            expiry_scope=expiry or "__ALL__",
            ok=False,
            error=f"History read failed: {exc}",
        )


@router.get(
    "/gex/{symbol}",
    response_model=GEXLevelsResponse,
    summary="GEX levels only — call wall, put wall, ZGL, max pain",
)
async def get_gex_levels(
    symbol: str,
    expiry: str | None = Query(default=None),
    r: float = Query(default=0.04, ge=0.0, le=1.0),
) -> GEXLevelsResponse:
    sym = symbol.upper().strip()
    if not sym:
        raise HTTPException(status_code=400, detail="symbol required")

    raw, _, _ = await _load_options_raw_chain(sym)

    if raw is None:
        return GEXLevelsResponse()

    spot_raw = _safe_float(
        raw.get("quote", {}).get("c") if isinstance(raw.get("quote"), dict) else None
    )
    spot = spot_raw or 100.0
    _, strikes, call_oi, put_oi, call_iv, put_iv, tte, _ = _parse_finnhub_chain(
        raw, spot, expiry, r
    )
    return _build_gex_levels(strikes, call_oi, put_oi, call_iv, put_iv, spot, tte, r)


@router.get(
    "/max-pain-history/{symbol}",
    response_model=MaxPainHistoryResponse,
    summary="Serie temporal max pain (nearest expiry) — Redis (job batch 30m horario mercado)",
)
async def get_max_pain_history(
    symbol: str,
    limit: int = Query(default=500, ge=1, le=2500),
) -> MaxPainHistoryResponse:
    """Lee puntos persistidos; sin Redis o sin datos → lista vacía."""
    from backend.services.max_pain_history_service import read_max_pain_history

    sym = symbol.upper().strip()
    if not sym:
        raise HTTPException(status_code=400, detail="symbol required")
    raw_list = read_max_pain_history(sym, limit=limit)
    pts: list[MaxPainHistoryPoint] = []
    for r in raw_list:
        try:
            pts.append(
                MaxPainHistoryPoint(
                    timestamp=int(r["timestamp"]),
                    max_pain=float(r["max_pain"]),
                    spot=float(r["spot"]),
                    distance_pct=float(r["distance_pct"]),
                    expiry=str(r.get("expiry", "")),
                    dte_days=float(r["dte_days"]) if r.get("dte_days") is not None else None,
                )
            )
        except (KeyError, TypeError, ValueError):
            continue
    src = "empty" if not pts else "redis"
    return MaxPainHistoryResponse(ticker=sym, points=pts, ok=True, source=src)


# ─────────────────────────────────────────────────────────────────────────────
# §5  BREAKDOWN BY STRIKE (multi-expiry aggregation)
# ─────────────────────────────────────────────────────────────────────────────


class BreakdownStrikeRow(BaseModel):
    """Single strike row in the breakdown panel."""

    strike: float
    calls: float = 0.0
    puts: float = 0.0
    net: float = 0.0


class BreakdownPanel(BaseModel):
    """One of the 3 breakdown panels."""

    id: str
    title: str
    strikes: list[float] = Field(default_factory=list)
    calls: list[float] = Field(default_factory=list)
    puts: list[float] = Field(default_factory=list)
    nets: list[float] = Field(default_factory=list)


class BreakdownByStrikeResponse(BaseModel):
    """3-panel breakdown: OI, GEX, Volume aggregated across expiries."""

    ticker: str
    spot: float
    as_of: str
    expiries_aggregated: list[str] = Field(default_factory=list)
    panels: list[BreakdownPanel] = Field(default_factory=list)
    chain_source: str = ""
    ok: bool = True
    error: str | None = None


def _aggregate_breakdown_panels(
    raw: dict[str, Any],
    spot: float,
    r: float,
) -> tuple[list[BreakdownPanel], list[str]]:
    """Aggregate chain data across ALL expiries into 3 panel datasets.

    Panel 1 — Open Interest: call OI (positive), put OI (negative), net.
    Panel 2 — Gamma Exposure: call GEX (positive), put GEX (negative), net.
    Panel 3 — Volume: call volume (positive), put volume (negative), net.

    Field mapping from Massive/Polygon snapshot → Finnhub-normalized shape:
      - callsValue (OI panel): ``openInterest`` of CALL contracts per strike
      - putsValue  (OI panel): ``openInterest`` of PUT contracts per strike (shown negative)
      - netValue   (OI panel): call_oi - put_oi
      - GEX panel : gamma × OI × 100 × spot² × 0.01 (BSM or API-provided gamma)
      - Volume panel: ``volume`` field from ``day`` object per contract
    """
    data_list = raw.get("data") or []
    if not isinstance(data_list, list):
        return [], []

    oi_by_strike: dict[float, dict[str, float]] = {}
    gex_by_strike: dict[float, dict[str, float]] = {}
    vol_by_strike: dict[float, dict[str, float]] = {}
    expiries_seen: list[str] = []

    for exp_block in data_list:
        if not isinstance(exp_block, dict):
            continue
        exp_date = str(exp_block.get("expirationDate", ""))
        if exp_date and exp_date not in expiries_seen:
            expiries_seen.append(exp_date)

        tte = _dte_from_expiry(exp_date)
        options_list = exp_block.get("options") or []
        if not isinstance(options_list, list):
            continue

        for opt in options_list:
            if not isinstance(opt, dict):
                continue
            k = _safe_float(opt.get("strike"))
            if k is None or k <= 0:
                continue
            opt_type = str(opt.get("type", "")).upper()
            if opt_type not in ("CALL", "PUT"):
                continue

            oi = _safe_float(opt.get("openInterest")) or 0.0
            vol = _safe_float(opt.get("volume")) or 0.0
            iv = _safe_float(opt.get("impliedVolatility"))

            gamma = _safe_float(opt.get("gamma"))
            if gamma is None and iv is not None and iv > 0 and tte > 0:
                gamma = _safe_float(BlackScholesPricer.gamma(spot, k, tte, r, iv))

            gex_val = 0.0
            if gamma is not None and gamma > 0:
                gex_val = gamma * oi * 100.0 * spot * spot * 0.01
                if opt_type == "PUT":
                    gex_val = -gex_val

            if k not in oi_by_strike:
                oi_by_strike[k] = {"call": 0.0, "put": 0.0}
            if k not in gex_by_strike:
                gex_by_strike[k] = {"call": 0.0, "put": 0.0}
            if k not in vol_by_strike:
                vol_by_strike[k] = {"call": 0.0, "put": 0.0}

            if opt_type == "CALL":
                oi_by_strike[k]["call"] += oi
                gex_by_strike[k]["call"] += abs(gex_val)
                vol_by_strike[k]["call"] += vol
            else:
                oi_by_strike[k]["put"] += oi
                gex_by_strike[k]["put"] += abs(gex_val)
                vol_by_strike[k]["put"] += vol

    all_strikes = sorted(oi_by_strike.keys())
    if not all_strikes:
        return [], expiries_seen

    # Filter to ±30% of spot for readability
    lo = spot * 0.7
    hi = spot * 1.3
    filtered = [s for s in all_strikes if lo <= s <= hi]
    if not filtered:
        filtered = all_strikes

    def _panel(pid: str, title: str, data: dict[float, dict[str, float]]) -> BreakdownPanel:
        strikes = filtered
        calls = [round(data.get(s, {}).get("call", 0.0), 2) for s in strikes]
        puts = [round(-data.get(s, {}).get("put", 0.0), 2) for s in strikes]
        nets = [round(c + p, 2) for c, p in zip(calls, puts, strict=False)]
        return BreakdownPanel(
            id=pid,
            title=title,
            strikes=strikes,
            calls=calls,
            puts=puts,
            nets=nets,
        )

    panels = [
        _panel("oi", "Open Interest", oi_by_strike),
        _panel("gex", "Gamma Exposure", gex_by_strike),
        _panel("volume", "Volume", vol_by_strike),
    ]
    return panels, expiries_seen


@router.get(
    "/breakdown/{symbol}",
    response_model=BreakdownByStrikeResponse,
    summary="Breakdown By Strike — 3-panel aggregation (OI, GEX, Volume) across expiries",
)
async def get_breakdown_by_strike(
    symbol: str,
    r: float = Query(default=0.04, ge=0.0, le=1.0),
) -> BreakdownByStrikeResponse:
    """
    Aggregates options chain data across ALL expiries into 3 views:
    1. Open Interest positioning (call OI vs put OI)
    2. Gamma Exposure (call GEX vs put GEX via BSM or API greeks)
    3. Volume flow (call volume vs put volume)

    Data source: Massive/Polygon REST snapshot (same pipeline as /snapshot).
    Strikes filtered to ±30% of spot for chart readability.
    """
    sym = symbol.upper().strip()
    if not sym:
        raise HTTPException(status_code=400, detail="symbol required")

    as_of = datetime.now(tz=UTC).isoformat()
    raw, chain_src, _ = await _load_options_raw_chain(sym)

    if raw is None or not isinstance(raw.get("data"), list) or len(raw["data"]) == 0:
        return BreakdownByStrikeResponse(
            ticker=sym,
            spot=0.0,
            as_of=as_of,
            ok=False,
            error="No chain data for breakdown",
        )

    spot_raw = _safe_float(
        raw.get("quote", {}).get("c") if isinstance(raw.get("quote"), dict) else None
    )
    if spot_raw is None:
        dl = raw.get("data") or []
        if isinstance(dl, list) and dl:
            spot_raw = _safe_float(
                (dl[0] if isinstance(dl[0], dict) else {}).get("underlying", {}).get("close")
            )
    spot = spot_raw or 100.0

    panels, expiries = _aggregate_breakdown_panels(raw, spot, r)

    return BreakdownByStrikeResponse(
        ticker=sym,
        spot=round(spot, 4),
        as_of=as_of,
        expiries_aggregated=expiries,
        panels=panels,
        chain_source=chain_src,
        ok=True,
    )
