"""Domain contracts for the Market Scanner module."""

from __future__ import annotations

from contextlib import suppress
from datetime import UTC, datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

ScannerTimeframe = Literal["5m", "15m", "1h", "1D"]
ScannerDirection = Literal["long", "short", "both"]
ScannerSort = Literal[
    "scanner_score",
    "conviction_score",
    "regime_fit_score",
    "capacity_score",
    "symbol",
    "change_pct",
    "relative_volume",
    "universe_percentile",
]
ScannerLiquidityTier = Literal["high", "normal", "low", "unknown"]
DeskRegimeLabel = Literal[
    "BULL_QUIET",
    "BEAR_VOLATILE",
    "CRISIS",
    "RECOVERY",
    "TRANSITION",
]
ScannerSignalLabel = Literal["strong_buy", "buy", "neutral", "sell", "strong_sell"]
ScannerBias = Literal["bullish", "bearish", "neutral", "unavailable"]
ScannerGrade = Literal["A+", "A", "B", "C", "WATCH", "VETO"]
ScannerIndicatorModule = Literal[
    "core", "technical", "probabilistic", "options_gex", "fundamentals", "macro_micro"
]
ScannerModuleKey = Literal[
    "technical", "probabilistic", "options_gex", "fundamentals", "macro_micro"
]
ScannerCostTier = Literal["cheap", "phase_b", "cached_external"]
ScannerIndicatorStatus = Literal["real", "partial", "proxy", "not_connected"]
ScannerIndicatorSource = Literal[
    "bingx_l2",
    "bingx_trade",
    "massive_options",
    "deribit_options",
    "ohlcv_proxy",
    "not_connected",
]
ScannerBriefTone = Literal["bullish", "bearish", "neutral", "warning", "unavailable"]
ScannerSourceStatus = Literal["available", "partial", "source unavailable"]
ScannerNewsImpact = Literal["high", "medium", "low"]
ScannerNewsSentiment = Literal["bullish", "bearish", "neutral", "unavailable"]
ScannerPortfolioOptimizerEngine = Literal["internal", "skfolio", "riskfolio"]
ScannerPortfolioOptimizerStatus = Literal["ok", "degraded", "unavailable"]
ScannerPortfolioRiskBudgetMode = Literal[
    "equal_weight",
    "inverse_vol_from_sparkline",
    "score_weighted",
    "correlation_penalty",
    "barra_risk_budget",
]
ScannerRiskModelVersion = Literal["institutional-barra-v1", "legacy-gross-v1"]
BarraAssetClass = Literal["equity", "crypto", "other"]
ScannerExecutionSimStatus = Literal["ok", "degraded", "unavailable"]
ScannerExecutionDirection = Literal["long", "short"]
ScannerExecutionMode = Literal["paper", "replay", "backtest"]

VETO_NO_DATA = "VETO_NO_DATA"
VETO_ILLIQUID = "VETO_ILLIQUID"
VETO_COMPLETE_CONTRADICTION = "VETO_COMPLETE_CONTRADICTION"
VETO_EXTREME_EXHAUSTION = "VETO_EXTREME_EXHAUSTION"

WARN_LOW_RVOL = "WARN_LOW_RVOL"
WARN_TF_DIVERGENCE = "WARN_TF_DIVERGENCE"
WARN_MODERATE_RSI = "WARN_RSI_EXTENDED"
WARN_LOW_CONFIDENCE = "WARN_LOW_CONFIDENCE"

KNOWN_SCANNER_INDICATOR_KEYS: set[str] = {
    "seven_day",
    "signal",
    "rsi",
    "rsi_hist",
    "macd",
    "ema_7_14",
    "ema_21_42",
    "ema_100_200",
    "avwap_vwap",
    "supertrend",
    "bbp",
    "vix",
    "volume",
    "prf",
    "relative_strength",
    "smc",
    "market_structure",
    "fvg",
    "vsa",
    "volume_profile",
    "order_flow_delta",
    "hmm_regime",
    "tail_risk",
    "jump_risk",
    "regime",
    "expected_move",
    "squeeze",
    "net_gex",
    "dealer_bias",
    "gamma_flip",
    "squeeze_probability",
    "iv_vol_term",
    "flow_signal",
    "obv_oi",
    "mfi_flow",
    "cmf_iv",
    "vpin",
    "lob_microstructure",
    "sentiment_adjusted_rsi",
    "sentiment_adjusted_momentum",
    "composite_flow_fusion",
    "news_catalyst_alignment",
    "regime_sentiment_score",
    "bull_bear_fusion_index",
    "fund_liquidity_ttm",
    "fund_quality_ttm",
    "macro_desk_overlay",
}
KNOWN_SCANNER_MODULE_KEYS: set[str] = {
    "technical",
    "probabilistic",
    "options_gex",
    "fundamentals",
    "macro_micro",
}
KNOWN_SCANNER_TIMEFRAMES: set[str] = {"5m", "15m", "1h", "1D"}
SCANNER_WEIGHT_MIN = 0.0
SCANNER_WEIGHT_MAX = 5.0


class MarketScannerFilters(BaseModel):
    """User-configurable gates for scanning a universe."""

    model_config = ConfigDict(extra="ignore")

    min_price: float = Field(default=1.0, ge=0.0)
    min_volume: float = Field(default=250_000.0, ge=0.0)
    min_relative_volume: float = Field(default=0.5, ge=0.0)
    min_score: float = Field(default=0.0, ge=0.0, le=100.0)
    allow_reversal: bool = True
    include_vetoed: bool = False


class MarketScannerIndicatorCatalogResponse(BaseModel):
    """Strategy Control catalog: versioned indicator list for the scanner desk."""

    model_config = ConfigDict(extra="ignore")

    catalog_version: str
    indicators: list[ScannerIndicatorDefinition] = Field(default_factory=list)


class ScannerIndicatorDefinition(BaseModel):
    """Static definition for one configurable scanner indicator."""

    model_config = ConfigDict(extra="ignore")

    key: str
    label: str
    module: ScannerIndicatorModule
    description: str
    default_enabled: bool = True
    supports_timeframes: list[ScannerTimeframe] = Field(default_factory=list)
    weight_by_timeframe: dict[str, float] = Field(default_factory=dict)
    cost_tier: ScannerCostTier = "cheap"
    requires: list[str] = Field(default_factory=list)
    status: ScannerIndicatorStatus = "proxy"
    status_detail: str = ""


class ScannerCustomization(BaseModel):
    """Per-request scanner configuration supplied by the UI."""

    model_config = ConfigDict(extra="ignore")

    enabled_indicators: list[str] | None = None
    enabled_modules: list[ScannerModuleKey] | None = None
    weight_matrix: dict[str, dict[str, float]] = Field(default_factory=dict)
    module_synthesis_limit: int = Field(default=10, ge=0, le=100)
    primary_timeframe: ScannerTimeframe | None = None
    adaptive_weighting: bool = Field(
        default=False,
        description="When true, weight_matrix is multiplied by regime-specific coefficients before scoring.",
    )
    scoring_schema_version: str | None = Field(
        default=None,
        description="Scoring contract version (e.g. institutional-v1). Auto-migrated on scan.",
    )

    @field_validator("enabled_indicators", mode="before")
    @classmethod
    def _clean_enabled_indicators(
        cls: type[ScannerCustomization],
        values: object,
    ) -> list[str] | None:
        if values is None:
            return None
        cleaned: list[str] = []
        seen: set[str] = set()
        for raw in values if isinstance(values, list) else []:
            key = str(raw).strip().lower()
            if key in KNOWN_SCANNER_INDICATOR_KEYS and key not in seen:
                seen.add(key)
                cleaned.append(key)
        return cleaned

    @field_validator("enabled_modules", mode="before")
    @classmethod
    def _clean_enabled_modules(
        cls: type[ScannerCustomization],
        values: object,
    ) -> list[str] | None:
        if values is None:
            return None
        cleaned: list[str] = []
        seen: set[str] = set()
        for raw in values if isinstance(values, list) else []:
            key = str(raw).strip().lower()
            if key in KNOWN_SCANNER_MODULE_KEYS and key not in seen:
                seen.add(key)
                cleaned.append(key)
        # Empty list disables every Phase B engine — treat as "all modules" (UI mis-save).
        return cleaned or None

    @field_validator("weight_matrix", mode="before")
    @classmethod
    def _clean_weight_matrix(
        cls: type[ScannerCustomization],
        values: object,
    ) -> dict[str, dict[str, float]]:
        if not isinstance(values, dict):
            return {}
        cleaned: dict[str, dict[str, float]] = {}
        for raw_key, raw_weights in values.items():
            key = str(raw_key).strip().lower()
            if key not in KNOWN_SCANNER_INDICATOR_KEYS or not isinstance(raw_weights, dict):
                continue
            timeframe_weights: dict[str, float] = {}
            for raw_timeframe, raw_weight in raw_weights.items():
                timeframe = "1D" if str(raw_timeframe).lower() == "1d" else str(raw_timeframe)
                if timeframe not in KNOWN_SCANNER_TIMEFRAMES:
                    continue
                weight = float(raw_weight)
                if weight < SCANNER_WEIGHT_MIN or weight > SCANNER_WEIGHT_MAX:
                    raise ValueError(
                        f"indicator weight for {key}/{timeframe} must be between "
                        f"{SCANNER_WEIGHT_MIN:g} and {SCANNER_WEIGHT_MAX:g}"
                    )
                timeframe_weights[timeframe] = weight
            if timeframe_weights:
                cleaned[key] = timeframe_weights
        return cleaned


class ScannerModuleSignal(BaseModel):
    """Aggregated signal emitted by a Phase B scanner module."""

    model_config = ConfigDict(extra="ignore")

    module: ScannerModuleKey
    label: ScannerSignalLabel
    score: float = Field(ge=0.0, le=100.0)
    confidence: float = Field(ge=0.0, le=1.0)
    engine_count: int = Field(default=0, ge=0)
    available_count: int = Field(default=0, ge=0)
    reasons: list[str] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    execution_latency_ms: float | None = Field(
        default=None,
        ge=0.0,
        description="Module execution time in milliseconds.",
    )
    engine_latencies: dict[str, float] = Field(
        default_factory=dict,
        description="Per-engine latency breakdown within this module.",
    )


def _default_timeframes() -> list[ScannerTimeframe]:
    return ["5m", "15m", "1h", "1D"]


class MarketScannerRequest(BaseModel):
    """Request contract for a market scanner run."""

    model_config = ConfigDict(extra="ignore")

    universe: str = "magnificas"
    symbols: list[str] = Field(default_factory=list)
    timeframes: list[ScannerTimeframe] = Field(default_factory=_default_timeframes)
    filters: MarketScannerFilters = Field(default_factory=MarketScannerFilters)
    sort: ScannerSort = "scanner_score"
    direction: ScannerDirection = "long"
    max_rows: int = Field(default=50, ge=1, le=500)
    include_deep_metrics: bool = False
    customization: ScannerCustomization = Field(default_factory=ScannerCustomization)
    webhook_url: str | None = Field(
        default=None,
        description="Optional HTTPS URL notified asynchronously after scan (JSON payload).",
    )
    include_funding_gate: bool = Field(
        default=True,
        description=(
            "When True, scanner rows are enriched with funding-suitability evidence "
            "(directional/risk split, backtest grade, reason codes). Set False for "
            "warmup or environments where backtest evidence is not yet available."
        ),
    )

    @field_validator("webhook_url", mode="before")
    @classmethod
    def _clean_webhook_url(cls: type[MarketScannerRequest], value: object) -> str | None:
        if value is None:
            return None
        raw = str(value).strip()
        return raw or None

    @field_validator("universe")
    @classmethod
    def _clean_universe(cls: type[MarketScannerRequest], value: str) -> str:
        return (value or "magnificas").strip().lower()

    @field_validator("symbols")
    @classmethod
    def _clean_symbols(cls: type[MarketScannerRequest], values: list[str]) -> list[str]:
        cleaned: list[str] = []
        seen: set[str] = set()
        for raw in values:
            symbol = str(raw).upper().strip()
            if symbol and symbol not in seen:
                seen.add(symbol)
                cleaned.append(symbol)
        return cleaned

    @field_validator("timeframes")
    @classmethod
    def _dedupe_timeframes(
        cls: type[MarketScannerRequest],
        values: list[ScannerTimeframe],
    ) -> list[ScannerTimeframe]:
        out: list[ScannerTimeframe] = []
        for value in values or ["5m", "15m", "1h", "1D"]:
            tf: ScannerTimeframe = "1D" if str(value).lower() == "1d" else value
            if tf not in out:
                out.append(tf)
        return out or ["5m", "15m", "1h", "1D"]

    @model_validator(mode="after")
    def _validate_custom_universe(self) -> MarketScannerRequest:
        if self.universe == "custom" and not self.symbols:
            raise ValueError("custom universe requires at least one symbol")
        return self


class MarketScannerTimeframeSignal(BaseModel):
    """Compact signal for one symbol/timeframe pair."""

    model_config = ConfigDict(extra="ignore")

    timeframe: ScannerTimeframe
    ok: bool
    direction: ScannerBias
    label: ScannerSignalLabel
    score: float = Field(ge=0.0, le=100.0)
    confidence: float = Field(ge=0.0, le=1.0)
    metrics: dict[str, float | str | bool | None] = Field(default_factory=dict)
    reasons: list[str] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    vetoes: list[str] = Field(default_factory=list)
    contributions: dict[str, float] = Field(
        default_factory=dict,
        description="Per-indicator score contributions for institutional audit (Phase A).",
    )
    indicator_scores: dict[str, float] = Field(
        default_factory=dict,
        description="Normalized 0-100 score per Phase-A indicator key (factor model).",
    )


class ScannerFactorDriver(BaseModel):
    """Single factor driver for conviction attribution."""

    model_config = ConfigDict(extra="ignore")

    factor_key: str
    contribution_pct: float
    loading: float
    historical_percentile: float | None = None
    data_tier: str
    source: str


class ScannerConvictionBreakdown(BaseModel):
    """Institutional conviction score breakdown with factor attribution."""

    model_config = ConfigDict(extra="ignore")

    conviction_score: float = Field(ge=0.0, le=100.0)
    scanner_score: float = Field(ge=0.0, le=100.0)
    top_drivers: list[ScannerFactorDriver] = Field(default_factory=list)
    factor_contributions: dict[str, float] = Field(default_factory=dict)
    historical_percentiles: dict[str, float] = Field(default_factory=dict)
    coverage_pct: float = Field(ge=0.0, le=100.0)
    warnings: list[str] = Field(default_factory=list)
    schema_version: str = "institutional-conviction-v1"
    conviction_score_raw: float | None = Field(
        default=None,
        ge=0.0,
        le=100.0,
        description="Conviction before Fase 3 crowding penalty (audit).",
    )
    crowding_penalty_applied: float | None = Field(
        default=None,
        ge=0.0,
        le=100.0,
        description="Points subtracted from conviction by crowding adjustment.",
    )


class FactorCrowdingIndex(BaseModel):
    """Fase 3: universe-level crowding index for one factor family."""

    model_config = ConfigDict(extra="ignore")

    factor_key: str
    crowding_percentile: float | None = Field(
        default=None,
        ge=0.0,
        le=100.0,
        description="Cross-sectional percentile among factors in this scan (85+ = crowded).",
    )
    concentration_score: float = Field(
        default=0.0,
        ge=0.0,
        description="Herfindahl-style concentration of abs(loadings) in the universe.",
    )
    loading_dispersion: float = Field(
        default=0.0,
        ge=0.0,
        description="Coefficient of variation of loadings cross-section.",
    )
    pairwise_corr_mean: float = Field(
        default=0.0,
        ge=-1.0,
        le=1.0,
        description="Mean pairwise correlation vs other active factors.",
    )
    data_tier: Literal["real", "proxy"] = "proxy"


class ScannerCrowdedFactor(BaseModel):
    """One crowded factor flagged on a row."""

    model_config = ConfigDict(extra="ignore")

    factor_key: str
    crowding_percentile: float = Field(ge=0.0, le=100.0)


class ScannerCrowdingBreakdown(BaseModel):
    """Fase 3: row-level crowding penalty and crowded factor flags."""

    model_config = ConfigDict(extra="ignore")

    crowding_penalty: float = Field(
        default=0.0,
        ge=0.0,
        le=100.0,
        description="Points subtracted from conviction_score.",
    )
    crowded_factors: list[ScannerCrowdedFactor] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    schema_version: str = "institutional-crowding-v1"


class ScannerCapacitySignals(BaseModel):
    """Fase 3: liquidity / capacity hints for desk sizing (non-order-authorizing)."""

    model_config = ConfigDict(extra="ignore")

    capacity_score: float = Field(
        default=50.0,
        ge=0.0,
        le=100.0,
        description="Higher = more capacity to deploy size.",
    )
    relative_volume: float | None = None
    liquidity_tier: ScannerLiquidityTier = "unknown"
    estimated_adv_usd: float | None = Field(
        default=None,
        ge=0.0,
        description="Proxy average daily notional from sparkline/volume.",
    )
    short_interest_pct: float | None = Field(default=None, ge=0.0, le=100.0)
    institutional_ownership_pct: float | None = Field(default=None, ge=0.0, le=100.0)
    production_size_hint: float | None = Field(
        default=None,
        ge=0.0,
        le=1.0,
        description="Read-only copy of production_size_multiplier when risk stack ran.",
    )
    warnings: list[str] = Field(default_factory=list)
    schema_version: str = "institutional-capacity-v1"


class DeskRegimeSnapshot(BaseModel):
    """Fase 2: desk-level market regime unified from HMM + VIX + macro + breadth.

    This is the single source of truth for ``MarketScannerRow.regime_label`` when
    ``SCANNER_DESK_REGIME_V2`` is enabled. It never authorizes risk on its own — it
    is a context label consumed by regime-fit scoring and the desk cockpit.
    """

    model_config = ConfigDict(extra="ignore")

    label: DeskRegimeLabel
    confidence: float = Field(
        ge=0.0,
        le=1.0,
        description="Agreement-weighted confidence across the detector's components.",
    )
    components: dict[str, Any] = Field(
        default_factory=dict,
        description=(
            "Raw inputs that drove the label: hmm_label, hmm_signal, vix, vix_bucket, "
            "macro_stress, bullish_share, etc. (audit trail)."
        ),
    )
    reason_codes: list[str] = Field(
        default_factory=list,
        description="Stable reason codes describing why this regime was assigned.",
    )
    detected_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    method_version: str = "desk-regime-v2"


class FactorRegimeStat(BaseModel):
    """Fase 2: rolling forward-return statistics for one (factor_key × regime) pair.

    Outcomes are snapshot-based (price at scan N vs N+1), never sourced from
    predictions.db. ``sample_count`` below the min-samples gate marks the stat as
    not yet actionable (caller should treat as insufficient history).
    """

    model_config = ConfigDict(extra="ignore")

    factor_key: str
    regime: DeskRegimeLabel
    sample_count: int = Field(ge=0)
    win_rate: float = Field(ge=0.0, le=1.0)
    avg_forward_return: float
    sharpe_annualized: float
    lookback_days: int = Field(ge=1)
    sufficient: bool = Field(
        default=False,
        description="True when sample_count >= min-samples gate (actionable evidence).",
    )


class ScannerRegimeFitLine(BaseModel):
    """Fase 2: one factor's contribution to a row's regime-fit score."""

    model_config = ConfigDict(extra="ignore")

    factor_key: str
    regime: DeskRegimeLabel
    contribution_pct: float = Field(ge=0.0, le=100.0)
    fit_score: float = Field(ge=0.0, le=100.0)
    sample_count: int = Field(ge=0)
    win_rate: float | None = None
    avg_forward_return: float | None = None
    sharpe_annualized: float | None = None
    note: str = ""


class ScannerRegimeFitBreakdown(BaseModel):
    """Fase 2: explainable regime-fit breakdown attached to a scanner row."""

    model_config = ConfigDict(extra="ignore")

    regime: DeskRegimeLabel
    regime_fit_score: float = Field(ge=0.0, le=100.0)
    lines: list[ScannerRegimeFitLine] = Field(default_factory=list)
    stress_overlay: list[str] = Field(
        default_factory=list,
        description="Human-readable stress lines (e.g. 'In BEAR_VOLATILE, momentum had Sharpe X over last 12m (n=...)').",
    )
    warnings: list[str] = Field(default_factory=list)
    schema_version: str = "institutional-regime-fit-v1"


class GexPressureLevel(BaseModel):
    """One strike's aggregated net GEX for pressure-field visualization."""

    model_config = ConfigDict(extra="ignore")

    strike: float
    net_gex: float
    call_gex: float | None = None
    put_gex: float | None = None
    net_gamma_exposure: float | None = None
    call_gamma_exposure: float | None = None
    put_gamma_exposure: float | None = None
    open_interest: float | None = None


class ScannerInstitutionalOverlay(BaseModel):
    """Extended institutional payload for scanner UI (GEX pressure, microstructure hints)."""

    model_config = ConfigDict(extra="ignore")

    snapshot_ok: bool = False
    spot: float | None = None
    gamma_flip: float | None = None
    net_gex_total: float | None = None
    dealer_bias: str | None = None
    call_wall: float | None = None
    put_wall: float | None = None
    zero_gamma_distance_pct: float | None = None
    net_vanna_exposure: float | None = None
    net_charm_exposure: float | None = None
    greek_flow_status: Literal["available", "degraded", "unavailable"] = "unavailable"
    greek_flow_source_tier: str | None = None
    greek_flow_data_quality_score: float | None = Field(default=None, ge=0.0, le=1.0)
    greek_flow_missing_components: list[str] = Field(default_factory=list)
    pressure_by_strike: list[GexPressureLevel] = Field(default_factory=list)
    microstructure: dict[str, float | str | bool | None] = Field(default_factory=dict)
    iv_term_structure: dict[str, Any] = Field(default_factory=dict)


class LeadersCorrelationMatrix(BaseModel):
    """Pairwise correlation of leader sparkline returns."""

    model_config = ConfigDict(extra="ignore")

    symbols: list[str] = Field(default_factory=list)
    matrix: list[list[float | None]] = Field(default_factory=list)


class MarketScannerRow(BaseModel):
    """Single ranked symbol row returned to the scanner UI."""

    model_config = ConfigDict(extra="ignore")

    symbol: str
    price: float | None = None
    change_pct: float | None = None
    sparkline: list[float] = Field(default_factory=list)
    signals: dict[str, MarketScannerTimeframeSignal] = Field(default_factory=dict)
    scanner_score: float = Field(ge=0.0, le=100.0)
    setup_grade: ScannerGrade
    direction: ScannerBias
    reasons: list[str] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    vetoes: list[str] = Field(default_factory=list)
    module_signals: dict[str, ScannerModuleSignal] = Field(default_factory=dict)
    source: str | None = None
    deep_metrics: dict[str, Any] | None = None
    institutional_overlay: ScannerInstitutionalOverlay | None = None
    score_audit: dict[str, Any] = Field(
        default_factory=dict,
        description="Explainable scoring: base components, blends, calibration source.",
    )
    intraday_score: float = Field(
        default=0.0,
        ge=0.0,
        le=100.0,
        description="Phase-A sub-score averaged from 5m + 15m signals (falls back to 0.0 when no intraday data).",
    )
    swing_score: float = Field(
        default=0.0,
        ge=0.0,
        le=100.0,
        description="Phase-A sub-score averaged from 1h + 1D signals (falls back to 0.0 when no swing data).",
    )
    regime_label: str | None = Field(
        default=None,
        description="Detected market regime for this scan run (e.g. BULL_QUIET, CRISIS). Populated when adaptive_weighting is active.",
    )
    regime_weight_multipliers: dict[str, float] | None = Field(
        default=None,
        description="Per-indicator multipliers applied by the active regime. Key = indicator key, value = coefficient.",
    )
    risk_hints: dict[str, float | str] = Field(
        default_factory=dict,
        description="Non-binding Kelly / VaR-style hints for desk review (not orders).",
    )
    score_ci_low: float | None = Field(default=None, ge=0.0, le=100.0)
    score_ci_high: float | None = Field(default=None, ge=0.0, le=100.0)
    directional_score: float | None = Field(
        default=None,
        ge=0.0,
        le=100.0,
        description="Directional confluence (kept separate from risk).",
    )
    risk_score: float | None = Field(
        default=None,
        ge=0.0,
        le=100.0,
        description="Risk-side score (drawdown / quality / overfit) — higher = safer.",
    )
    data_quality_score: float | None = Field(
        default=None,
        ge=0.0,
        le=1.0,
        description="Data-quality score from upstream backtest evidence; 0 if missing.",
    )
    module_backtest_grade: str | None = Field(
        default=None,
        description="Backtest grade injected by the Risk Desk / backtest service.",
    )
    funding_suitability: str = Field(
        default="insufficient_data",
        description=(
            "Funding-account suitability label: allow | size_down | block | "
            "informational_only | insufficient_data (legacy default)."
        ),
    )
    funding_reason_codes: list[str] = Field(
        default_factory=list,
        description="Stable reason codes describing the funding-suitability call.",
    )
    evidence_by_module: dict[str, dict[str, Any]] = Field(
        default_factory=dict,
        description=(
            "Per-module evidence: module_backtest_grade, source_tier, data_quality_score, "
            "signal_coverage, funding_survival_grade, reasons."
        ),
    )
    best_supporting_module: str | None = Field(default=None)
    weakest_link_module: str | None = Field(default=None)
    recommended_size_multiplier: float | None = Field(default=None, ge=0.0, le=1.0)
    universe_z_score: float | None = Field(
        default=None,
        description="Cross-sectional z-score of scanner_score within this scan universe.",
    )
    universe_percentile: float | None = Field(
        default=None,
        ge=0.0,
        le=100.0,
        description="Percentile rank within scan universe (100 = best).",
    )
    universe_rank: int | None = Field(
        default=None,
        ge=1,
        description="1-based rank by scanner_score within returned universe.",
    )
    portfolio_weight: float | None = Field(
        default=None,
        ge=0.0,
        le=1.0,
        description="Non-binding basket weight after factorial constraints (Point 6).",
    )
    production_size_multiplier: float | None = Field(
        default=None,
        ge=0.0,
        le=1.0,
        description="Binding production cap: funding gate × portfolio × Kelly × drawdown.",
    )
    production_kelly_fraction: float | None = Field(
        default=None,
        ge=0.0,
        le=1.0,
        description="Regime-aware fractional Kelly used for production sizing.",
    )
    factor_loadings: dict[str, float] = Field(
        default_factory=dict,
        description="Barra-style factor exposures (momentum, liquidity, gex, volatility).",
    )
    source_attribution: dict[str, dict[str, str]] = Field(
        default_factory=dict,
        description="Per-indicator data tier and provider (bingx_trade, massive_options, ...).",
    )
    indicator_metrics: dict[str, dict[str, Any]] = Field(
        default_factory=dict,
        description="Per-indicator observability: execution_latency_ms, data_tier, source, score_contribution.",
    )
    engine_latency_ms: dict[str, float] = Field(
        default_factory=dict,
        description="Per-engine execution latency in milliseconds (Phase B modules).",
    )
    barra_exposure: BarraFactorExposure | None = Field(
        default=None,
        description="Institutional Barra-style multi-factor exposure (Point 2).",
    )
    specific_risk: float | None = Field(
        default=None,
        ge=0.0,
        description="Idiosyncratic annualized vol fraction from Barra residual.",
    )
    conviction_breakdown: ScannerConvictionBreakdown | None = Field(
        default=None,
        description="Phase 1: deterministic factor attribution and historical conviction.",
    )
    conviction_score: float | None = Field(
        default=None,
        ge=0.0,
        le=100.0,
        description="Institutional conviction score (separate from scanner_score ranking).",
    )
    regime_fit_score: float | None = Field(
        default=None,
        ge=0.0,
        le=100.0,
        description=(
            "Fase 2: how well this row's top conviction factors fit the active desk "
            "regime historically (0-100; ~50 = neutral / insufficient history)."
        ),
    )
    regime_fit_breakdown: ScannerRegimeFitBreakdown | None = Field(
        default=None,
        description="Fase 2: per-factor regime-fit lines + stress overlay text.",
    )
    crowding_breakdown: ScannerCrowdingBreakdown | None = Field(
        default=None,
        description="Fase 3: crowding penalty and crowded factor flags.",
    )
    capacity_signals: ScannerCapacitySignals | None = Field(
        default=None,
        description="Fase 3: liquidity and capacity hints for desk sizing.",
    )


class BarraFactorExposure(BaseModel):
    """Per-symbol factor exposures for institutional risk model."""

    model_config = ConfigDict(extra="ignore")

    symbol: str
    asset_class: BarraAssetClass = "other"
    factors: dict[str, float] = Field(default_factory=dict)
    factor_sources: dict[str, str] = Field(
        default_factory=dict,
        description="Per-factor data tier: real | partial | proxy.",
    )
    specific_risk: float | None = Field(default=None, ge=0.0)


class BarraFactorCovariance(BaseModel):
    """Factor covariance matrix (annualized variance units)."""

    model_config = ConfigDict(extra="ignore")

    factor_names: list[str] = Field(default_factory=list)
    matrix: list[list[float]] = Field(default_factory=list)
    half_life_days: int = 60
    as_of: datetime = Field(default_factory=lambda: datetime.now(UTC))
    asset_class: BarraAssetClass | None = None


class BarraRiskModelOutput(BaseModel):
    """Basket-level Barra risk model diagnostics."""

    model_config = ConfigDict(extra="ignore")

    enabled: bool = True
    schema_version: ScannerRiskModelVersion = "institutional-barra-v1"
    exposures: list[BarraFactorExposure] = Field(default_factory=list)
    covariance: BarraFactorCovariance | None = None
    factor_risk_contribution: dict[str, float] = Field(default_factory=dict)
    specific_risk_by_symbol: dict[str, float] = Field(default_factory=dict)
    marginal_risk_contribution: dict[str, float] = Field(default_factory=dict)
    factor_risk_budget: dict[str, float] = Field(default_factory=dict)
    warnings: list[str] = Field(default_factory=list)


class ScannerIndicatorAttribution(BaseModel):
    """Runtime data source for one scanner indicator."""

    model_config = ConfigDict(extra="ignore")

    indicator_key: str
    tier: ScannerIndicatorStatus
    source: ScannerIndicatorSource
    detail: str | None = None


class ScannerRiskStackFactorLimits(BaseModel):
    """Max gross exposure per normalized factor bucket (sum of abs loadings × weight)."""

    model_config = ConfigDict(extra="ignore")

    momentum: float = Field(default=0.45, ge=0.05, le=1.0)
    liquidity: float = Field(default=0.40, ge=0.05, le=1.0)
    gex: float = Field(default=0.35, ge=0.05, le=1.0)
    volatility: float = Field(default=0.50, ge=0.05, le=1.0)


class ScannerRiskStackConstraints(BaseModel):
    """Institutional portfolio risk stack constraints (Point 6)."""

    model_config = ConfigDict(extra="ignore")

    max_weight: float = Field(default=0.25, ge=0.01, le=1.0)
    min_weight: float = Field(default=0.0, ge=0.0, le=1.0)
    long_only: bool = True
    risk_budget_mode: ScannerPortfolioRiskBudgetMode = "score_weighted"
    factor_limits: ScannerRiskStackFactorLimits = Field(
        default_factory=ScannerRiskStackFactorLimits
    )
    max_drawdown_usage_pct: float = Field(
        default=85.0,
        ge=0.0,
        le=100.0,
        description="Cap sizing when simulated trailing drawdown usage exceeds this %.",
    )
    kelly_cap: float = Field(default=0.25, ge=0.0, le=1.0)
    equity: float | None = Field(default=None, gt=0)
    preset_id: str = "ftmo_2_step"


class ScannerRiskStackAllocation(BaseModel):
    """Per-symbol output from score → constraints → sizing chain."""

    model_config = ConfigDict(extra="ignore")

    symbol: str
    portfolio_weight: float = Field(ge=0.0, le=1.0)
    production_size_multiplier: float = Field(ge=0.0, le=1.0)
    production_kelly_fraction: float = Field(ge=0.0, le=1.0)
    factor_loadings: dict[str, float] = Field(default_factory=dict)
    funding_size_multiplier: float = Field(default=1.0, ge=0.0, le=1.0)
    drawdown_cap_multiplier: float = Field(default=1.0, ge=0.0, le=1.0)
    warnings: list[str] = Field(default_factory=list)


class ScannerRiskStackResponse(BaseModel):
    """Basket-level institutional risk stack (non-order-authorizing diagnostics)."""

    model_config = ConfigDict(extra="ignore")

    enabled: bool = True
    schema_version: str = "institutional-risk-stack-v1"
    allocations: list[ScannerRiskStackAllocation] = Field(default_factory=list)
    factor_exposure: dict[str, float] = Field(default_factory=dict)
    constraints_applied: ScannerRiskStackConstraints = Field(
        default_factory=ScannerRiskStackConstraints
    )
    warnings: list[str] = Field(default_factory=list)
    optimizer_status: ScannerPortfolioOptimizerStatus = "ok"
    barra_risk_model: BarraRiskModelOutput | None = Field(
        default=None,
        description="Point 2: full Barra factor model (exposures, cov, risk contrib).",
    )


class MarketScannerResponse(BaseModel):
    """Top-level market scanner response."""

    model_config = ConfigDict(extra="ignore")

    generated_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    universe: str
    rows: list[MarketScannerRow] = Field(default_factory=list)
    skipped_symbols: dict[str, str] = Field(default_factory=dict)
    data_quality: dict[str, Any] = Field(default_factory=dict)
    scoring_version: str = "market-scanner-v3"
    scoring_schema_version: str | None = Field(
        default=None,
        description="Institutional scoring schema (institutional-v1 when active).",
    )
    effective_weight_matrix: dict[str, dict[str, float]] | None = Field(
        default=None,
        description="Weights after regime multipliers (desk transparency).",
    )
    regime_multipliers_applied: dict[str, float] | None = Field(
        default=None,
        description="Regime coefficients applied to this scan when adaptive_weighting is on.",
    )
    universe_stats: dict[str, float] | None = Field(
        default=None,
        description="Cross-sectional stats for scanner_score (mean, std, count, min, max).",
    )
    catalog_version: str = "quantumbeta-v3.0"
    feature_freshness: dict[str, int | float | str | bool] = Field(default_factory=dict)
    cost_estimate: dict[str, int | float | str | bool] = Field(default_factory=dict)
    leaders_correlation: LeadersCorrelationMatrix | None = None
    observability: dict[str, Any] = Field(
        default_factory=dict,
        description="Per-engine/indicator execution metrics: latency, data tier usage, degradation signals.",
    )
    universe_regime_summary: dict[str, Any] = Field(
        default_factory=dict,
        description="Cross-sectional tone from current scan rows (risk-on/off diagnostic).",
    )
    portfolio_risk_stack: ScannerRiskStackResponse | None = Field(
        default=None,
        description="Basket-level Point-6 risk stack applied after funding gate.",
    )
    barra_risk_model: BarraRiskModelOutput | None = Field(
        default=None,
        description="Basket-level Point-2 Barra risk model.",
    )
    risk_model_version: ScannerRiskModelVersion | None = Field(
        default=None,
        description="Active portfolio risk model schema.",
    )
    macro_context: dict[str, Any] | None = Field(
        default=None,
        description="Shared Phase B macro snapshot (FRED + FMP calendar) for this scan run.",
    )
    desk_regime: DeskRegimeSnapshot | None = Field(
        default=None,
        description="Fase 2: unified desk regime (HMM + VIX + macro + breadth) for this scan run.",
    )
    factor_crowding: list[FactorCrowdingIndex] = Field(
        default_factory=list,
        description="Fase 3: universe-level factor crowding indices for this scan.",
    )
    compliance_metadata: dict[str, Any] = Field(
        default_factory=lambda: {
            "validation_approach": "pre_validated_models",
            "oos_wfa_in_live_flow": False,
            "backtest_evidence_source": "pre_computed_database",
            "research_endpoints": [
                "/api/v1/backtest/walk-forward/{symbol}",
                "/api/v1/backtest/prediction-v1/walk-forward-oos",
            ],
            "gips_compliance_note": (
                "Live scanner uses pre-validated models. OOS/Walk-Forward Analysis "
                "is available via dedicated research endpoints only. Scanner output "
                "represents live predictions, not hypothetical backtested results."
            ),
        },
        description=(
            "Institutional compliance metadata: documents that live scanner flow never "
            "invokes OOS/WFA computation. Research validation is performed offline via "
            "dedicated backtest endpoints."
        ),
    )


class ScannerExecutionCandidate(BaseModel):
    """Scanner-generated candidate sent to an optional execution simulator.

    This contract is research-only: it contains no broker credentials and does
    not represent an executable order.
    """

    model_config = ConfigDict(extra="ignore")

    symbol: str
    direction: ScannerExecutionDirection
    scanner_score: float | None = Field(default=None, ge=0.0, le=100.0)
    setup_grade: ScannerGrade | None = None
    price: float | None = Field(default=None, ge=0.0)
    quantity: float | None = Field(default=None, ge=0.0)
    notional_usd: float | None = Field(default=None, ge=0.0)
    timeframe: ScannerTimeframe | None = None
    funding_suitability: str | None = None
    recommended_size_multiplier: float | None = Field(default=None, ge=0.0, le=1.0)
    risk_hints: dict[str, float | str | bool | None] = Field(default_factory=dict)
    metadata: dict[str, Any] = Field(default_factory=dict)

    @field_validator("symbol")
    @classmethod
    def _clean_symbol(cls: type[ScannerExecutionCandidate], value: str) -> str:
        return str(value or "").upper().strip()

    @field_validator("direction", mode="before")
    @classmethod
    def _clean_direction(cls: type[ScannerExecutionCandidate], value: object) -> str:
        text = str(value or "").strip().lower()
        if text in {"buy", "bullish", "up"}:
            return "long"
        if text in {"sell", "bearish", "down"}:
            return "short"
        return text


class ScannerExecutionSimRequest(BaseModel):
    """Request contract for the optional Nautilus-style execution sidecar."""

    model_config = ConfigDict(extra="ignore")

    candidates: list[ScannerExecutionCandidate] = Field(
        default_factory=list, min_length=1, max_length=50
    )
    simulation_mode: ScannerExecutionMode = "paper"
    venue: str | None = None
    horizon_minutes: int = Field(default=60, ge=1, le=10_080)
    request_id: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)


class ScannerExecutionSimResponse(BaseModel):
    """Normalized response from the optional execution simulator."""

    model_config = ConfigDict(extra="ignore")

    status: ScannerExecutionSimStatus = "unavailable"
    engine: str = "nautilus_sidecar"
    reason: str = "sidecar_not_configured"
    error: str | None = None
    results: list[dict[str, Any]] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    sidecar_latency_ms: float | None = Field(default=None, ge=0.0)
    metadata: dict[str, Any] = Field(default_factory=dict)


class ScannerPortfolioOptimizeRequest(BaseModel):
    """Request contract for non-binding Scanner leaders basket optimization."""

    model_config = ConfigDict(extra="ignore")

    rows: list[MarketScannerRow] = Field(default_factory=list, max_length=100)
    row_summaries: list[dict[str, Any]] = Field(default_factory=list, max_length=100)
    constraints: dict[str, Any] = Field(default_factory=dict)

    @field_validator("constraints", mode="before")
    @classmethod
    def _clean_constraints(
        cls: type[ScannerPortfolioOptimizeRequest],
        value: object,
    ) -> dict[str, Any]:
        raw = value if isinstance(value, dict) else {}
        constraints: dict[str, Any] = {
            "max_weight": 1.0,
            "long_only": True,
            "min_weight": 0.0,
            "risk_budget_mode": "inverse_vol_from_sparkline",
        }
        if "max_weight" in raw:
            with suppress(TypeError, ValueError):
                constraints["max_weight"] = max(0.0, min(1.0, float(raw["max_weight"])))
        if "min_weight" in raw:
            with suppress(TypeError, ValueError):
                constraints["min_weight"] = max(0.0, min(1.0, float(raw["min_weight"])))
        if "long_only" in raw:
            constraints["long_only"] = bool(raw["long_only"])
        mode = str(raw.get("risk_budget_mode") or constraints["risk_budget_mode"]).strip().lower()
        if mode in {
            "equal_weight",
            "inverse_vol_from_sparkline",
            "correlation_penalty",
            "score_weighted",
            "barra_risk_budget",
        }:
            constraints["risk_budget_mode"] = mode
        if "risk_model_version" in raw:
            constraints["risk_model_version"] = str(raw["risk_model_version"])
        factor_budget = raw.get("factor_risk_budget")
        if isinstance(factor_budget, dict):
            constraints["factor_risk_budget"] = {
                str(k): float(v) for k, v in factor_budget.items() if _is_finite_float(v)
            }
        factor_raw = raw.get("factor_limits")
        if isinstance(factor_raw, dict):
            constraints["factor_limits"] = factor_raw
        for key in ("max_drawdown_usage_pct", "kelly_cap", "equity"):
            if key in raw:
                with suppress(TypeError, ValueError):
                    constraints[key] = float(raw[key])
        if "preset_id" in raw:
            constraints["preset_id"] = str(raw["preset_id"])
        return constraints


def _is_finite_float(value: object) -> bool:
    if value is None:
        return False
    try:
        number = float(value)
    except (TypeError, ValueError):
        return False
    return number == number and number >= 0


class ScannerPortfolioWeight(BaseModel):
    """Single non-binding optimizer allocation for one Scanner leader."""

    model_config = ConfigDict(extra="ignore")

    symbol: str
    weight: float = Field(ge=0.0, le=1.0)
    risk_contribution: float = Field(default=0.0, ge=0.0, le=1.0)
    volatility: float | None = Field(default=None, ge=0.0)
    factor_risk_contribution: float = Field(default=0.0, ge=0.0, le=1.0)
    specific_risk_contribution: float = Field(default=0.0, ge=0.0, le=1.0)
    marginal_risk_contribution: float = Field(default=0.0, ge=0.0, le=1.0)


class ScannerPortfolioOptimizeResponse(BaseModel):
    """Response contract for Scanner leaders basket optimization."""

    model_config = ConfigDict(extra="ignore")

    engine: ScannerPortfolioOptimizerEngine = "internal"
    status: ScannerPortfolioOptimizerStatus = "unavailable"
    weights: list[ScannerPortfolioWeight] = Field(default_factory=list)
    risk_contribution: dict[str, float] = Field(default_factory=dict)
    warnings: list[str] = Field(default_factory=list)
    risk_budget_mode: ScannerPortfolioRiskBudgetMode = "inverse_vol_from_sparkline"
    risk_stack: ScannerRiskStackResponse | None = Field(
        default=None,
        description="Point 6: factorial constraints + production Kelly/drawdown sizing.",
    )
    risk_model: BarraRiskModelOutput | None = Field(
        default=None,
        description="Point 2: Barra factor covariance and risk attribution.",
    )
    factor_risk_contribution: dict[str, float] = Field(default_factory=dict)


class MarketScannerLivePricesRequest(BaseModel):
    """Batch request for lightweight scanner price refreshes."""

    model_config = ConfigDict(extra="ignore")

    symbols: list[str] = Field(default_factory=list, max_length=500)

    @field_validator("symbols")
    @classmethod
    def _clean_symbols(cls: type[MarketScannerLivePricesRequest], values: list[str]) -> list[str]:
        cleaned: list[str] = []
        seen: set[str] = set()
        for raw in values:
            symbol = str(raw).upper().strip()
            if symbol and symbol not in seen:
                seen.add(symbol)
                cleaned.append(symbol)
        return cleaned


class MarketScannerLivePriceRow(BaseModel):
    """Fresh price snapshot for one scanner symbol."""

    model_config = ConfigDict(extra="ignore")

    symbol: str
    price: float
    change_pct: float | None = None
    source: str
    timestamp_ms: int | None = None


class MarketScannerLivePricesResponse(BaseModel):
    """Batch response for lightweight scanner price refreshes."""

    model_config = ConfigDict(extra="ignore")

    generated_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    prices: dict[str, MarketScannerLivePriceRow] = Field(default_factory=dict)


class MarketScannerContextRequest(BaseModel):
    """Lightweight market briefing context for the scanner dashboard."""

    model_config = ConfigDict(extra="ignore")

    universe: str = "wall_street"
    symbols: list[str] = Field(default_factory=list, max_length=50)
    leaders: list[str] = Field(default_factory=list, max_length=20)
    limit_per_symbol: int = Field(default=3, ge=1, le=8)

    @field_validator("universe")
    @classmethod
    def _clean_universe(cls: type[MarketScannerContextRequest], value: str) -> str:
        return (value or "wall_street").strip().lower()

    @field_validator("symbols", "leaders")
    @classmethod
    def _clean_symbol_list(
        cls: type[MarketScannerContextRequest],
        values: list[str],
    ) -> list[str]:
        cleaned: list[str] = []
        seen: set[str] = set()
        for raw in values:
            symbol = str(raw).upper().strip()
            if symbol and symbol not in seen:
                seen.add(symbol)
                cleaned.append(symbol)
        return cleaned


class ScannerBriefBlock(BaseModel):
    """One explainable market-context block rendered in Market Now."""

    model_config = ConfigDict(extra="ignore")

    key: str
    title: str
    value: str
    detail: str = ""
    tone: ScannerBriefTone = "neutral"
    source: str = "market-scanner"
    status: ScannerSourceStatus = "available"


class ScannerNewsItem(BaseModel):
    """Normalized headline item for the scanner news/sentiment rail."""

    model_config = ConfigDict(extra="ignore")

    symbol: str
    published_date: str | None = None
    title: str
    source: str = "unknown"
    url: str | None = None
    summary: str | None = None
    impact: ScannerNewsImpact = "low"
    sentiment: ScannerNewsSentiment = "unavailable"


class MarketScannerContextResponse(BaseModel):
    """Dashboard context around scanner results without mutating scanner scoring."""

    model_config = ConfigDict(extra="ignore")

    generated_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    market_brief: list[ScannerBriefBlock] = Field(default_factory=list)
    fear_greed: dict[str, Any] | None = None
    news: list[ScannerNewsItem] = Field(default_factory=list)
    sentiment_by_symbol: dict[str, dict[str, Any]] = Field(default_factory=dict)
    catalysts_by_symbol: dict[str, dict[str, Any]] = Field(default_factory=dict)
    argentina_summary: dict[str, Any] | None = None
    sources: dict[str, str] = Field(default_factory=dict)
    regulatory_scan_summary: dict[str, Any] = Field(
        default_factory=dict,
        description="Layer-1 regulatory pattern scan over aggregated headlines (veto hints).",
    )


class MarketScannerUniverse(BaseModel):
    """A named symbol universe exposed to clients."""

    model_config = ConfigDict(extra="ignore")

    key: str
    label: str
    symbols: list[str]
    count: int


class MarketScannerPreset(BaseModel):
    """Built-in scanner preset."""

    model_config = ConfigDict(extra="ignore")

    key: str
    label: str
    description: str
    request: MarketScannerRequest


class ScannerNaturalLanguageResponse(BaseModel):
    """Heuristic interpretation of a natural-language scanner query (no LLM required)."""

    model_config = ConfigDict(extra="ignore")

    matched_terms: list[str] = Field(default_factory=list)
    suggested_universe: str | None = None
    suggested_min_score: float | None = None
    suggested_modules: list[ScannerModuleKey] | None = None
    suggested_indicators: list[str] | None = None
    explanation: str = ""


class ScannerLeadersThesisRequest(BaseModel):
    """Request selective Layer-4 synthesis on scanner leaders."""

    model_config = ConfigDict(extra="ignore")

    symbols: list[str] = Field(default_factory=list, max_length=12)
    row_summaries: list[dict[str, Any]] = Field(default_factory=list)
    universe: str | None = Field(
        default=None,
        description="Active scanner universe id (e.g. wall_street) for LLM desk context.",
    )
    universe_regime_summary: dict[str, Any] | None = Field(
        default=None,
        description="Cross-sectional regime block from the last scan (optional).",
    )

    @field_validator("symbols")
    @classmethod
    def _clean_symbols(cls: type[ScannerLeadersThesisRequest], values: list[str]) -> list[str]:
        cleaned: list[str] = []
        seen: set[str] = set()
        for raw in values:
            symbol = str(raw).upper().strip()
            if symbol and symbol not in seen:
                seen.add(symbol)
                cleaned.append(symbol)
        return cleaned


class ScannerLeadersThesisResponse(BaseModel):
    """Orchestrated thesis + deterministic fallback."""

    model_config = ConfigDict(extra="ignore")

    ok: bool = True
    mode: str = "deterministic"  # "focused_llm" | "full_agents" | "deterministic" | "error"
    orchestrator: str | None = None
    agent_summaries: dict[str, str] | None = None
    fallback_narrative: str | None = None
    error: str | None = None


class NaturalLanguageScannerRequest(BaseModel):
    """Natural language query for scanner parameter hints."""

    model_config = ConfigDict(extra="ignore")

    query: str = ""
    active_universe: str | None = None


class ScannerFusionEnrichRequest(BaseModel):
    """Merge context-rail sentiment/catalyst payloads into scanner row deep_metrics."""

    model_config = ConfigDict(extra="ignore")

    rows: list[dict[str, Any]] = Field(default_factory=list)
    sentiment_by_symbol: dict[str, dict[str, Any]] = Field(default_factory=dict)
    catalysts_by_symbol: dict[str, dict[str, Any]] = Field(default_factory=dict)
    primary_timeframe: ScannerTimeframe = "15m"
    argentina_summary: dict[str, Any] | None = None


class ScannerFusionEnrichResponse(BaseModel):
    """Rows after sentiment × technical fusion enrichment."""

    model_config = ConfigDict(extra="ignore")

    rows: list[MarketScannerRow] = Field(default_factory=list)
