"""
backend/domain/probabilistic_models.py
════════════════════════════════════════════════════════════════════════════════
Domain models for Probabilistic AI Framework (Ingeniería IA).
Canonical location — layer_3_specialists re-exports from here for compat.
════════════════════════════════════════════════════════════════════════════════
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class TailRisk(BaseModel):
    model_config = ConfigDict(frozen=True)

    shape: float = 0.0
    scale: float = 0.0
    threshold: float = 0.0
    var_99: float = 0.0
    cvar_99: float = 0.0


class JumpRisk(BaseModel):
    model_config = ConfigDict(frozen=True)

    intensity: float = 0.0
    mu_j: float = 0.0
    sigma_j: float = 0.0
    probability: float = 0.0


class AdaptiveState(BaseModel):
    model_config = ConfigDict(frozen=True)

    pr_ordered: float = 0.5
    trend_strength: float = 0.0


class KellySizing(BaseModel):
    """Kelly Criterion sizing fractions."""

    model_config = ConfigDict(frozen=True)

    full_kelly: float = 0.0
    half_kelly: float = 0.0
    quarter_kelly: float = 0.0
    expected_value: float = 0.0


class CorrelationEntry(BaseModel):
    """Single pairwise correlation result (serialisable)."""

    model_config = ConfigDict(frozen=True)

    reference_ticker: str
    label: str
    rolling_corr: float
    long_corr: float
    decoupling_score: float
    is_decoupled: bool
    direction: str  # POSITIVE | NEGATIVE | NEUTRAL


class CrossAssetSummary(BaseModel):
    """Serialisable cross-asset report attached to ProbabilisticResult."""

    model_config = ConfigDict(frozen=True)

    strongest_link: str | None = None
    max_decoupling: float = 0.0
    decoupling_alert: bool = False
    systematic_risk: float = 0.0
    idiosyncratic_risk: float = 0.0
    regime_label: str = "UNKNOWN"
    correlations: list[CorrelationEntry] = Field(default_factory=list)


class UpcomingCatalystEntry(BaseModel):
    """Serialisable upcoming event catalyst."""

    model_config = ConfigDict(frozen=True)

    event_type: str
    date: str | None = None
    days_until: int | None = None
    label: str


class EventRiskSummary(BaseModel):
    """Serialisable catalyst NLP result attached to ProbabilisticResult."""

    model_config = ConfigDict(frozen=True)

    event_risk_score: float = 0.0
    tone: str = "NEUTRAL"
    tone_confidence: float = 0.0
    jump_intensity_adj: float = 1.0
    transcript_summary: str | None = None
    bullish_hits: int = 0
    bearish_hits: int = 0
    alarming_hits: int = 0
    news_count: int = 0
    news_sentiment: float = 0.5
    upcoming_catalysts: list[UpcomingCatalystEntry] = Field(default_factory=list)
    last_eps_surprise: float | None = None
    avg_eps_surprise: float | None = None


class VolumeNodeEntry(BaseModel):
    """Single bin in the volume profile."""

    model_config = ConfigDict(frozen=True)

    price: float
    volume_pct: float
    node_type: str  # "HVN" | "LVN" | "POC" | "NORMAL"


class VolumeProfileSummary(BaseModel):
    """Serialisable volume profile report."""

    model_config = ConfigDict(frozen=True)

    poc: float
    vah: float
    val: float
    hvn_levels: list[float] = Field(default_factory=list)
    lvn_levels: list[float] = Field(default_factory=list)
    nodes: list[VolumeNodeEntry] = Field(default_factory=list)


class SkewPointEntry(BaseModel):
    """Single point in the IV Skew history."""

    model_config = ConfigDict(frozen=True)

    date: str
    put_iv: float
    call_iv: float
    skew: float


class VolatilitySurfaceSummary(BaseModel):
    """Serialisable volatility surface/skew report."""

    model_config = ConfigDict(frozen=True)

    current_skew: float
    skew_percentile: float
    fear_regime: str
    put_call_iv_ratio: float
    risk_signal: str
    historical_skew: list[SkewPointEntry] = Field(default_factory=list)


class RegimeStateEntry(BaseModel):
    """Details of a single Markov regime state."""

    model_config = ConfigDict(frozen=True)

    index: int
    label: str
    probability: float


class MarkovRegimeSummary(BaseModel):
    """Serialisable markov regime switching report."""

    model_config = ConfigDict(frozen=True)

    current_state: str
    state_confidence: float
    transition_risk: float
    expected_days_in_state: int
    regime_signal: str  # "STABLE" | "SHIFTING" | "CRITICAL"
    states: list[RegimeStateEntry] = Field(default_factory=list)


class ExpectedMoveEntry(BaseModel):
    """Single timeframe expected move."""

    model_config = ConfigDict(frozen=True)

    timeframe: str
    dte: int
    expected_move: float
    upper_bound: float
    lower_bound: float
    iv: float


class ExpectedMoveSummary(BaseModel):
    """Serialisable expected move report."""

    model_config = ConfigDict(frozen=True)

    spot: float
    horizons: list[ExpectedMoveEntry] = Field(default_factory=list)


class SkewProfileEntry(BaseModel):
    """Single point in the skew profile."""

    model_config = ConfigDict(frozen=True)

    strike: float
    iv_call: float
    iv_put: float
    skew_spread: float
    moneyness: float


class SkewFatTailsSummary(BaseModel):
    """Serialisable skew and fat tails report."""

    model_config = ConfigDict(frozen=True)

    spot_price: float
    atm_iv: float
    implied_skewness: float
    tail_risk_factor: float
    put_call_iv_spread: float
    risk_flag: str  # RISK_CLEAR | RISK_CAUTION | RISK_AVOID
    risk_score: float
    flag_rationale: str
    profile: list[SkewProfileEntry] = Field(default_factory=list)


class DeltaFlowSummary(BaseModel):
    """Serialisable delta-weighted flow report."""

    model_config = ConfigDict(frozen=True)

    total_call_flow: float
    total_put_flow: float
    pc_flow_ratio: float
    z_score: float | None = None
    signal: str  # NEUTRAL | HOLD_STATE | EXHAUSTION_WARNING | LONG_SETUP_TRIGGER
    rolling_mean: float | None = None
    rolling_std: float | None = None


class COR3MSummary(BaseModel):
    """Serialisable systemic correlation risk report."""

    model_config = ConfigDict(frozen=True)

    cor3m_value: float
    percentile_rank: float
    market_state: str  # NORMAL | SYSTEMIC_PANIC_HOLD | LONG_LIQUIDITY_RALLY
    signal: str  # BUY | NEUTRAL
    bars_since_panic: int
    note: str = ""


class SqueezeSummary(BaseModel):
    """Serialisable squeeze ignition report."""

    model_config = ConfigDict(frozen=True)

    state: str  # MONITORING | VULNERABLE | IGNITION | COOLING
    vulnerability_score: float
    signal_type: str  # NONE | LONG_MOMENTUM_IGNITION | TAKE_PROFIT_SCALING | ALERT_VULNERABLE
    trigger_reasons: list[str] = Field(default_factory=list)
    spot_price: float
    call_wall_level: float
    suggested_entry: float | None = None
    take_profit_levels: list[float] = Field(default_factory=list)
    notes: str = ""


class StrikeDynamicsEntry(BaseModel):
    """Dynamics for a specific strike/contract."""

    strike: float
    option_type: str
    volume: int
    net_oi_change: int
    signal_type: str  # NEW_POSITION | DAY_TRADING | PROFIT_TAKING | STAGNATION
    volume_oi_ratio: float


class VolumeOISummary(BaseModel):
    """Serialisable summary of Volume/OI dynamics (Agarwal framework)."""

    model_config = ConfigDict(frozen=True)

    institutional_entry_pct: float  # % of volume that is New Positions
    speculation_pct: float  # % of volume that is Day Trading
    liquidation_pct: float  # % of volume that is Profit Taking
    top_dynamics: list[StrikeDynamicsEntry] = Field(default_factory=list)
    note: str = ""


class DEXStrikeEntry(BaseModel):
    """Net Delta Exposure for a single strike."""

    strike: float
    dex_net: float


class DEXSummary(BaseModel):
    """Dealer Delta Exposure (DEX) summary."""

    model_config = ConfigDict(frozen=True)

    total_dex_nominal: float
    dex_calls: float
    dex_puts: float
    gamma_flip_level: float
    profile_by_strike: list[DEXStrikeEntry] = Field(default_factory=list)
    cumulative_profile: list[DEXStrikeEntry] = Field(default_factory=list)
    note: str = ""


class VolTermStrikeEntry(BaseModel):
    """Interpolated IV for a specific standard tenor."""

    tenor_days: int
    iv: float


class VolTermSummary(BaseModel):
    """Volatility Term Structure analysis summary."""

    model_config = ConfigDict(frozen=True)

    regime: str
    inversion_alert: bool
    slope_bps: float
    ratio: float
    flat_warning: bool
    curve: list[VolTermStrikeEntry] = Field(default_factory=list)
    summary_msg: str = ""


class GammaFlipProfilePoint(BaseModel):
    """Single point on the dealer net-gamma vs hypothetical spot curve."""

    model_config = ConfigDict(frozen=True)

    price: float
    net_gamma: float


class GammaFlipOIByStrike(BaseModel):
    """Open interest by strike for the lower panel (calls up / puts down in UI)."""

    model_config = ConfigDict(frozen=True)

    strike: float
    call_oi: float
    put_oi: float


class GammaFlipResponse(BaseModel):
    """Gamma Flip Engine output for Predictive Options 2 (MM net gamma profile + flip)."""

    model_config = ConfigDict(frozen=True)

    ticker: str
    ok: bool = True
    error: str | None = None
    as_of: str | None = None
    spot: float = 0.0
    flip_point: float | None = None
    flip_put_shock_10pct: float | None = None
    regime: str = "UNKNOWN"
    distance_pct: float | None = None
    current_net_gamma: float = 0.0
    interpretation: str = ""
    gex_zero_gamma_level: float | None = None
    profile: list[GammaFlipProfilePoint] = Field(default_factory=list)
    oi_by_strike: list[GammaFlipOIByStrike] = Field(default_factory=list)


class ShadowDeltaRow(BaseModel):
    """Per-leg shadow delta metrics (OI-weighted chain row)."""

    model_config = ConfigDict(frozen=True)

    strike: float
    option_type: str
    iv: float
    bs_delta: float
    shadow_delta: float
    delta_gap: float
    delta_gap_pct: float
    skew_slope: float
    open_interest: float


class ShadowDeltaStressSummary(BaseModel):
    """Aggregated stress test (-5% spot) naive BS vs skew-adjusted delta."""

    model_config = ConfigDict(frozen=True)

    shock_pct: float
    mean_abs_delta_error: float
    max_abs_delta_error: float
    n_pct_error_over_5: int
    n_legs: int


class ShadowDeltaResponse(BaseModel):
    """Shadow Delta Engine output (skew-adjusted effective delta)."""

    model_config = ConfigDict(frozen=True)

    ticker: str
    ok: bool = True
    error: str | None = None
    as_of: str | None = None
    spot: float = 0.0
    net_bs_delta: float = 0.0
    net_shadow_delta: float = 0.0
    total_delta_gap: float = 0.0
    hedge_shares_needed: float = 0.0
    n_legs: int = 0
    rows: list[ShadowDeltaRow] = Field(default_factory=list)
    stress: ShadowDeltaStressSummary | None = None


class ZommaTopStrikeEntry(BaseModel):
    model_config = ConfigDict(frozen=True)

    strike: float
    option_type: str
    notional_zomma: float


class ZommaVolCrushPair(BaseModel):
    model_config = ConfigDict(frozen=True)

    gamma_before: float
    gamma_after: float


class ZommaVolCrushBuckets(BaseModel):
    """Notional gamma before vs after proportional IV crush, by zomma sign at baseline."""

    model_config = ConfigDict(frozen=True)

    atm_zomma_negative: ZommaVolCrushPair
    otm_zomma_positive: ZommaVolCrushPair


class ZommaAnalysisResponse(BaseModel):
    """Third-order Greek surface ∂Γ/∂σ (notional OI-weighted) + vol crush bars."""

    model_config = ConfigDict(frozen=True)

    ticker: str
    ok: bool = True
    error: str | None = None
    as_of: str | None = None
    spot: float = 0.0
    current_iv: float = 0.0
    post_crush_iv: float = 0.0
    vol_crush_pct: float = 0.20
    heatmap_spot_axis: list[float] = Field(default_factory=list)
    heatmap_iv_axis: list[float] = Field(default_factory=list)
    heatmap_z: list[list[float]] = Field(default_factory=list)
    gamma_vol_crush: ZommaVolCrushBuckets | None = None
    top_strikes: list[ZommaTopStrikeEntry] = Field(default_factory=list)


class SpeedInstabilitySummary(BaseModel):
    model_config = ConfigDict(frozen=True)

    total_net_swx: float
    max_abs_swx_single_strike: float
    n_gamma_traps: int
    top_gamma_trap_strike: float | None = None
    book_bias: str


class SpeedInstabilityZoneEntry(BaseModel):
    model_config = ConfigDict(frozen=True)

    strike: float
    total_net_swx: float
    abs_total_net_swx: float
    regime: str


class SpeedProfilePoint(BaseModel):
    model_config = ConfigDict(frozen=True)

    spot: float
    net_swx: float
    net_gex: float


class SpeedByStrikeEntry(BaseModel):
    model_config = ConfigDict(frozen=True)

    strike: float
    call_speed: float
    put_speed: float


class SpeedDecaySeries(BaseModel):
    model_config = ConfigDict(frozen=True)

    label: str
    strike: float
    color: str
    days_to_expiry: list[float]
    abs_speed: list[float]


class SpeedScatterPoint(BaseModel):
    model_config = ConfigDict(frozen=True)

    strike: float
    option_type: str
    gex: float
    net_swx: float
    speed_bs: float
    marker_norm: float


class SpeedTrapRow(BaseModel):
    model_config = ConfigDict(frozen=True)

    strike: float
    option_type: str
    speed_bs: float
    swx: float
    net_swx: float
    speed_zscore: float
    open_interest: float
    gamma_bs: float
    sigma: float


class SpeedInstabilityResponse(BaseModel):
    """∂Γ/∂S (Speed) — SWX profile, traps, decay, GEX vs SWX (Predictive Options 2)."""

    model_config = ConfigDict(frozen=True)

    ticker: str
    ok: bool = True
    error: str | None = None
    as_of: str | None = None
    spot: float = 0.0
    summary: SpeedInstabilitySummary | None = None
    zones: list[SpeedInstabilityZoneEntry] = Field(default_factory=list)
    profile: list[SpeedProfilePoint] = Field(default_factory=list)
    speed_by_strike: list[SpeedByStrikeEntry] = Field(default_factory=list)
    speed_decay: list[SpeedDecaySeries] = Field(default_factory=list)
    scatter: list[SpeedScatterPoint] = Field(default_factory=list)
    gamma_traps: list[SpeedTrapRow] = Field(default_factory=list)


class VolatilitySkewScenarioEntry(BaseModel):
    model_config = ConfigDict(frozen=True)

    shock_pct: float
    stressed_strike: float
    iv_stressed: float
    iv_atm: float
    iv_premium: float
    iv_ratio: float | None = None


class VolatilitySkewMetricsBlock(BaseModel):
    model_config = ConfigDict(frozen=True)

    slope_25d: float
    convexity: float
    iv_25d_put: float
    iv_25d_call: float
    iv_atm: float
    iv_10d_put: float
    iv_10d_call: float
    regime: str
    tail_risk_alert: bool
    alert_message: str
    poly_coeffs: list[float] = Field(default_factory=list)


class VolatilitySkewMarketPoint(BaseModel):
    model_config = ConfigDict(frozen=True)

    strike: float
    iv_pct: float
    option_type: str
    delta: float


class VolatilitySkewFittedPoint(BaseModel):
    model_config = ConfigDict(frozen=True)

    strike: float
    iv_fitted_pct: float


class VolatilitySkewCurvaturePoint(BaseModel):
    model_config = ConfigDict(frozen=True)

    strike: float
    curvature_norm: float


class VolatilitySkewResponse(BaseModel):
    """Vol smile / skew (polynomial fit) + curvature + stress scenarios."""

    model_config = ConfigDict(frozen=True)

    ticker: str
    ok: bool = True
    error: str | None = None
    as_of: str | None = None
    spot: float = 0.0
    fit_model: str = "polynomial"
    metrics: VolatilitySkewMetricsBlock | None = None
    market_points: list[VolatilitySkewMarketPoint] = Field(default_factory=list)
    fitted_curve: list[VolatilitySkewFittedPoint] = Field(default_factory=list)
    curvature: list[VolatilitySkewCurvaturePoint] = Field(default_factory=list)
    scenarios: list[VolatilitySkewScenarioEntry] = Field(default_factory=list)


class TailRiskSmileMetricsBlock(BaseModel):
    model_config = ConfigDict(frozen=True)

    skew_25d: float
    convexity_25d: float
    iv_put_25d: float
    iv_call_25d: float
    iv_atm: float
    min_iv_strike: float
    smile_skewness_pct: float
    as_of: str


class TailRiskAlertBlock(BaseModel):
    model_config = ConfigDict(frozen=True)

    level: str
    convexity_percentile: float
    skew_regime: str
    message: str


class TailRiskReversalBlock(BaseModel):
    model_config = ConfigDict(frozen=True)

    direction: str
    signal_strength: str
    skew_vol_pts: float
    iv_put_25d_pct: float
    iv_call_25d_pct: float
    iv_atm_pct: float
    convexity_vol_pts: float
    min_iv_strike: float
    smile_skewness_pct: float
    interpretation: str


class TailRiskObservedPoint(BaseModel):
    model_config = ConfigDict(frozen=True)

    strike: float
    iv_pct: float
    option_type: str
    delta: float


class TailRiskSplinePoint(BaseModel):
    model_config = ConfigDict(frozen=True)

    strike: float
    iv_pct: float


class TailRiskCurvaturePoint(BaseModel):
    model_config = ConfigDict(frozen=True)

    strike: float
    curvature: float


class TailRiskSmileResponse(BaseModel):
    """Tail risk from cubic smile (25Δ skew/convexity) + spline + curvature (Predictive Options 2)."""

    model_config = ConfigDict(frozen=True)

    ticker: str
    ok: bool = True
    error: str | None = None
    as_of: str | None = None
    spot: float = 0.0
    metrics: TailRiskSmileMetricsBlock | None = None
    alert: TailRiskAlertBlock | None = None
    risk_reversal: TailRiskReversalBlock | None = None
    observed: list[TailRiskObservedPoint] = Field(default_factory=list)
    smile_spline: list[TailRiskSplinePoint] = Field(default_factory=list)
    curvature: list[TailRiskCurvaturePoint] = Field(default_factory=list)


class ZeroDayGexBar(BaseModel):
    model_config = ConfigDict(frozen=True)

    strike: float
    gex_bn: float


class ZeroDayPinPoint(BaseModel):
    model_config = ConfigDict(frozen=True)

    strike: float
    pin_prob: float


class ZeroDayZoneSpan(BaseModel):
    model_config = ConfigDict(frozen=True)

    x0: float
    x1: float
    kind: str


class ZeroDayAlertEntry(BaseModel):
    model_config = ConfigDict(frozen=True)

    alert_type: str
    severity: str
    strike: float
    message: str
    confidence: float
    metadata: dict[str, Any] = Field(default_factory=dict)


class ZeroDayGammaWallResponse(BaseModel):
    """0DTE-style gamma wall (GEX by strike) + pinning curve + key levels (Predictive Options 2)."""

    model_config = ConfigDict(frozen=True)

    ticker: str
    ok: bool = True
    error: str | None = None
    as_of: str | None = None
    spot: float = 0.0
    minutes_to_close: float = 0.0
    gamma_flip: float = 0.0
    call_wall: float = 0.0
    put_wall: float = 0.0
    total_gex_bn: float = 0.0
    vanna_pressure_bn: float = 0.0
    charm_decay_mm: float = 0.0
    imbalance_ratio: float | None = None
    pinning_strike: float = 0.0
    pinning_prob: float = 0.0
    zone: ZeroDayZoneSpan | None = None
    gex_bars: list[ZeroDayGexBar] = Field(default_factory=list)
    pin_curve: list[ZeroDayPinPoint] = Field(default_factory=list)
    alerts: list[ZeroDayAlertEntry] = Field(default_factory=list)


class PredictiveOptions2Bundle(BaseModel):
    """Single snapshot: predictive options 2 (one chain fetch)."""

    model_config = ConfigDict(frozen=True)

    gamma_flip: GammaFlipResponse
    shadow_delta: ShadowDeltaResponse
    zomma: ZommaAnalysisResponse
    speed_instability: SpeedInstabilityResponse
    volatility_skew: VolatilitySkewResponse
    tail_risk_smile: TailRiskSmileResponse
    zero_day_gamma_wall: ZeroDayGammaWallResponse


class ProbabilisticResult(BaseModel):
    """Consolidated probabilistic analysis result (Ingeniería IA Framework)."""

    model_config = ConfigDict(frozen=True)

    ticker: str
    tail: TailRisk = Field(default_factory=TailRisk)
    jump: JumpRisk = Field(default_factory=JumpRisk)
    state: AdaptiveState = Field(default_factory=AdaptiveState)
    vov: float = 0.0
    etv: float = 0.0
    kelly_prob: float = 0.0
    is_ordered_gate: bool = True
    is_jump_gate: bool = True
    gex_gating_safe: bool = True
    dealer_bias: str = "NEUTRAL"
    is_local_ar: bool = False
    vix: float = 0.0
    us10y: float = 0.0
    gate_veto: bool = False
    cross_asset: CrossAssetSummary | None = None
    event_risk: EventRiskSummary | None = None
    volume_profile: VolumeProfileSummary | None = None
    volatility_surface: VolatilitySurfaceSummary | None = None
    markov_regime: MarkovRegimeSummary | None = None
    expected_move: ExpectedMoveSummary | None = None
    skew_fat_tails: SkewFatTailsSummary | None = None
    delta_flow: DeltaFlowSummary | None = None
    cor3m: COR3MSummary | None = None
    squeeze_ignition: SqueezeSummary | None = None
    volume_oi_dynamics: VolumeOISummary | None = None
    dex_exposure: DEXSummary | None = None
    vol_term_structure: VolTermSummary | None = None

    @property
    def signal_allowed(self) -> bool:
        """Capa 6 Gating Logic: Pr(Ordered) < 0.55 or Jump_Prob > 0.05 -> CASH."""
        if self.state.pr_ordered < 0.55:
            return False
        if self.jump.probability > 0.05:
            return False
        return True


# ────────────────────────────────────────────────────────────────
# MIGRATION AUDIT — SECTOR: IA / PROBABILÍSTICO
# Archivo        : probabilistic_models.py
# Sub-capa       : Modelo (Domain Contracts)
# Framework ML   : Pydantic
# Descripcion    : Sincronizado con framework Ingeniería IA.
# Eliminado      : EVTResult legacy logic.
# Preservado     : TailRisk, JumpRisk, AdaptiveState patterns.
# ────────────────────────────────────────────────────────────────
