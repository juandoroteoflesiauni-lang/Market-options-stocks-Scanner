"""Institutional options-chain analytics.

Pure calculations over normalized option-chain rows. The router owns fetching and
normalization; this module enriches rows and builds auditable strike, expiry and
multi-expiry summaries.
"""

from __future__ import annotations

import math
from datetime import UTC, datetime
from typing import Any, Literal
from zoneinfo import ZoneInfo

import numpy as np
from pydantic import BaseModel, Field

from .bsm import BlackScholesPricer, OptionType

MetricSource = Literal["provider", "bsm_derived", "surface_derived", "session_proxy"]
GammaRegime = Literal["POSITIVE_GAMMA", "NEGATIVE_GAMMA", "TRANSITION_GAMMA", "NEUTRAL_GAMMA"]
MetricQualityStatus = Literal["real", "derived", "proxy", "unavailable"]

CONTRACT_SIZE = 100.0


class InstitutionalChainQuality(BaseModel):
    provider: str | None = None
    rows: int = 0
    expiries: int = 0
    derived_fields: list[str] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    data_quality_score: float = 0.0


class InstitutionalOptionStrikeRow(BaseModel):
    strike: float
    expiration: str
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
    call_customer_volume: float | None = None
    call_firm_volume: float | None = None
    call_market_maker_volume: float | None = None
    call_exchange_volumes: dict[str, float] | None = None
    call_aggressor_side: str | None = None
    call_trade_id: str | None = None
    call_trade_timestamp: str | None = None
    call_trade_condition_codes: list[str] = Field(default_factory=list)
    call_nbbo_at_trade_bid: float | None = None
    call_nbbo_at_trade_ask: float | None = None
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
    put_customer_volume: float | None = None
    put_firm_volume: float | None = None
    put_market_maker_volume: float | None = None
    put_exchange_volumes: dict[str, float] | None = None
    put_aggressor_side: str | None = None
    put_trade_id: str | None = None
    put_trade_timestamp: str | None = None
    put_trade_condition_codes: list[str] = Field(default_factory=list)
    put_nbbo_at_trade_bid: float | None = None
    put_nbbo_at_trade_ask: float | None = None
    total_oi: float | None = None
    net_gex: float | None = None
    moneyness: float | None = None
    call_dex: float | None = None
    put_dex: float | None = None
    net_dex: float | None = None
    call_mid: float | None = None
    put_mid: float | None = None
    call_mark: float | None = None
    put_mark: float | None = None
    call_spread_abs: float | None = None
    put_spread_abs: float | None = None
    call_spread_pct: float | None = None
    put_spread_pct: float | None = None
    call_quote_age_ms: float | None = None
    put_quote_age_ms: float | None = None
    call_last_trade_age_ms: float | None = None
    put_last_trade_age_ms: float | None = None
    call_liquidity_score: float | None = None
    put_liquidity_score: float | None = None
    call_bid_ask_size_imbalance: float | None = None
    put_bid_ask_size_imbalance: float | None = None
    call_intrinsic_value: float | None = None
    put_intrinsic_value: float | None = None
    call_extrinsic_value: float | None = None
    put_extrinsic_value: float | None = None
    call_breakeven_distance_pct: float | None = None
    put_breakeven_distance_pct: float | None = None
    call_model_price: float | None = None
    put_model_price: float | None = None
    call_theoretical_edge: float | None = None
    put_theoretical_edge: float | None = None
    put_call_parity_residual: float | None = None
    call_rho: float | None = None
    put_rho: float | None = None
    call_lambda: float | None = None
    put_lambda: float | None = None
    call_vomma: float | None = None
    put_vomma: float | None = None
    call_vanna: float | None = None
    put_vanna: float | None = None
    call_charm: float | None = None
    put_charm: float | None = None
    call_speed: float | None = None
    put_speed: float | None = None
    call_color: float | None = None
    put_color: float | None = None
    call_zomma: float | None = None
    put_zomma: float | None = None
    call_ultima: float | None = None
    put_ultima: float | None = None
    call_vex: float | None = None
    put_vex: float | None = None
    net_vex: float | None = None
    call_cex: float | None = None
    put_cex: float | None = None
    net_cex: float | None = None
    gex_share_pct: float | None = None
    dex_share_pct: float | None = None
    call_premium_volume: float | None = None
    put_premium_volume: float | None = None
    premium_volume: float | None = None
    call_notional_volume: float | None = None
    put_notional_volume: float | None = None
    notional_volume: float | None = None
    call_volume_oi_ratio: float | None = None
    put_volume_oi_ratio: float | None = None
    volume_oi_ratio: float | None = None
    call_oi_turnover_proxy: float | None = None
    put_oi_turnover_proxy: float | None = None
    oi_turnover_proxy: float | None = None
    metric_sources: dict[str, MetricSource] = Field(default_factory=dict)


class StrikeAnalyticsRow(BaseModel):
    strike: float
    expiration: str
    call_oi: float = 0.0
    put_oi: float = 0.0
    call_volume: float = 0.0
    put_volume: float = 0.0
    pcr_oi: float | None = None
    pcr_volume: float | None = None
    net_premium: float = 0.0
    cumulative_gex: float = 0.0
    cumulative_dex: float = 0.0
    wall_rank: int | None = None
    pin_risk_score: float = 0.0


class ExpiryAnalytics(BaseModel):
    expiration: str
    dte_days: float | None = None
    total_open_interest: float = 0.0
    total_volume: float = 0.0
    total_premium_volume: float = 0.0
    pcr_oi: float | None = None
    pcr_volume: float | None = None
    atm_iv: float | None = None
    iv_breadth: float | None = None
    zero_dte_gamma_share: float = 0.0
    expiry_gamma_pressure: float = 0.0
    expiry_charm_pressure: float = 0.0
    implied_move: float | None = None
    implied_move_pct: float | None = None


class ChainInstitutionalMetrics(BaseModel):
    total_open_interest: float = 0.0
    total_volume: float = 0.0
    total_premium_volume: float = 0.0
    total_notional_volume: float = 0.0
    total_gex: float = 0.0
    total_dex: float = 0.0
    total_vex: float = 0.0
    total_cex: float = 0.0
    pcr_oi: float | None = None
    pcr_volume: float | None = None
    gamma_regime: GammaRegime = "NEUTRAL_GAMMA"
    vol_trigger_proxy: float | None = None
    dealer_pressure_score: float = 0.0
    data_quality_score: float = 0.0
    gex_formula_version: str = "spotgamma_v1"
    dex_formula_version: str = "delta_oi_100_spot_v1"
    vex_formula_version: str = "vanna_oi_100_v1"
    cex_formula_version: str = "charm_oi_100_v1"


class DeltaMaturitySurfacePoint(BaseModel):
    expiration: str
    dte_days: float | None = None
    delta_bucket: str
    target_delta: float
    call_strike: float | None = None
    put_strike: float | None = None
    call_iv: float | None = None
    put_iv: float | None = None
    mid_iv: float | None = None
    risk_reversal: float | None = None
    butterfly_proxy: float | None = None


class ChainAlert(BaseModel):
    kind: str
    severity: Literal["info", "warning", "critical"] = "info"
    message: str
    level: float | None = None
    distance_pct: float | None = None
    source: MetricSource = "session_proxy"
    metadata: dict[str, float | str | int | None] = Field(default_factory=dict)


class DominantExpiryRow(BaseModel):
    expiration: str
    dte_days: float | None = None
    rank: int
    total_open_interest: float = 0.0
    total_volume: float = 0.0
    total_premium_volume: float = 0.0
    total_gex: float = 0.0
    abs_gex: float = 0.0
    gamma_share_pct: float = 0.0
    oi_share_pct: float = 0.0
    volume_share_pct: float = 0.0
    premium_share_pct: float = 0.0
    zero_dte_gamma_share: float = 0.0
    dominance_score: float = 0.0


class TapeAggressorFlowMetrics(BaseModel):
    source: str = "snapshot_proxy"
    call_aggressive_premium: float = 0.0
    put_aggressive_premium: float = 0.0
    net_aggressive_premium: float = 0.0
    buyer_initiated_volume: float = 0.0
    seller_initiated_volume: float = 0.0
    flow_imbalance: float | None = None
    sweep_score: float = 0.0


class QuotePressureMetrics(BaseModel):
    source: str = "snapshot_proxy"
    net_size_imbalance: float = 0.0
    quote_pressure_score: float = 0.0
    avg_spread_pct: float | None = None
    stale_quote_ratio: float | None = None
    executable_notional_top: float = 0.0


class IvSkewVelocityMetrics(BaseModel):
    source: str = "snapshot_proxy"
    atm_iv_proxy: float | None = None
    skew_slope_proxy: float | None = None
    front_back_iv_spread: float | None = None
    vol_trigger_velocity: float | None = None
    zero_gamma_velocity: float | None = None


class ContractMetadataRiskMetrics(BaseModel):
    source: str = "massive_contract_details"
    contracts_observed: int = 0
    adjusted_contracts: int = 0
    non_standard_deliverables: int = 0
    max_risk_score: float = 0.0
    warnings: list[str] = Field(default_factory=list)


class OptionRvIvMetrics(BaseModel):
    source: str = "snapshot_day_ohlc_proxy"
    option_realized_vol_proxy: float | None = None
    weighted_iv: float | None = None
    rv_iv_spread: float | None = None
    premium_compression_score: float = 0.0


class VenueQualityMetrics(BaseModel):
    source: str = "snapshot_contract_metadata"
    exchange_count: int = 0
    top_exchange: str | None = None
    top_exchange_share_pct: float = 0.0
    concentration_hhi: float = 0.0
    venue_dispersion_score: float = 0.0


class OpeningClosingFlowMetrics(BaseModel):
    source: str = "volume_vs_oi_change_proxy"
    opening_volume: float = 0.0
    closing_volume: float = 0.0
    fresh_positioning_ratio: float | None = None
    net_opening_premium: float = 0.0
    oi_confirmation_lag: str = "next_snapshot_required"


class GammaHedgeDemandMetrics(BaseModel):
    source: str = "gamma_delta_shock_proxy"
    up_1pct_shares_to_trade: float | None = None
    down_1pct_shares_to_trade: float | None = None
    up_2pct_shares_to_trade: float | None = None
    down_2pct_shares_to_trade: float | None = None
    charm_1h_shares_to_trade: float | None = None
    vanna_plus_1vol_shares_to_trade: float | None = None


class ZeroDteExhaustionMetrics(BaseModel):
    source: str = "snapshot_proxy"
    zero_dte_volume_share_pct: float = 0.0
    zero_dte_premium_share_pct: float = 0.0
    zero_dte_charm_pressure: float = 0.0
    late_day_decay_proxy: float = 0.0
    exhaustion_score: float = 0.0


class LiquidityStressMetrics(BaseModel):
    source: str = "snapshot_quote_proxy"
    stress_score: float = 0.0
    avg_spread_pct: float | None = None
    max_spread_pct: float | None = None
    stale_quote_ratio: float = 0.0
    depth_evaporation_score: float = 0.0
    locked_crossed_count: int = 0


class MetricQuality(BaseModel):
    status: MetricQualityStatus
    source: str
    formula_version: str
    required_fields: list[str] = Field(default_factory=list)
    missing_fields: list[str] = Field(default_factory=list)


class InstitutionalStandardMetrics(BaseModel):
    source: str = "chain_nbbo_volume_surface"
    normalized_25d_skew_30: float | None = None
    vix_style_variance_30d: float | None = None
    vix_style_vol_30d: float | None = None
    vega_notional_traded: float = 0.0
    trade_weighted_quoted_spread_pct: float | None = None
    dte_volume_distribution: dict[str, float] = Field(default_factory=dict)
    block_size_distribution: dict[str, float] = Field(default_factory=dict)
    constant_maturity_iv_surface: dict[str, dict[str, float | None]] = Field(default_factory=dict)
    implied_borrow_30d: float | None = None
    true_aggressor_net_premium: float | None = None
    true_aggressor_flow_imbalance: float | None = None
    effective_spread_pct: float | None = None
    participant_capacity_flow: dict[str, float] = Field(default_factory=dict)
    exchange_volume_hhi_real: float | None = None
    risk_neutral_density_moments: dict[str, float | None] = Field(default_factory=dict)
    metric_quality: dict[str, MetricQuality] = Field(default_factory=dict)
    benchmark_validation: dict[str, dict[str, str | float | None]] = Field(default_factory=dict)
    institutional_confidence_score: float = 0.0


class TradeLevelReadinessMetrics(BaseModel):
    status: Literal["trade_level_ready", "partial_trade_level", "snapshot_only"] = "snapshot_only"
    aggressor_coverage_pct: float = 0.0
    nbbo_coverage_pct: float = 0.0
    capacity_coverage_pct: float = 0.0
    exchange_volume_coverage_pct: float = 0.0
    required_feed: str = "OPRA trades + NBBO at-trade + capacity + exchange"
    missing_fields: list[str] = Field(default_factory=list)


class SurfaceDiagnosticsMetrics(BaseModel):
    source: str = "chain_iv_surface"
    svi_ready: bool = False
    points_used: int = 0
    expiries_used: int = 0
    vertical_arbitrage_violations: int = 0
    butterfly_arbitrage_violations: int = 0
    calendar_arbitrage_violations: int = 0
    no_arbitrage_score: float = 0.0
    warnings: list[str] = Field(default_factory=list)


class ProductConventionMetrics(BaseModel):
    underlying_type: str = "EQUITY"
    exercise_style: str | None = None
    settlement_type: str = "PM"
    multiplier_mode: str = "unknown"
    adjusted_contracts: int = 0
    non_standard_deliverables: int = 0
    convention_summary: str = ""
    warnings: list[str] = Field(default_factory=list)


class DealerPositioningV2Metrics(BaseModel):
    source: str = "customer_flow_signed_dealer_inverse"
    estimated_dealer_gamma_regime: Literal[
        "SHORT_GAMMA", "LONG_GAMMA", "MIXED_GAMMA", "UNKNOWN"
    ] = "UNKNOWN"
    customer_net_premium: float = 0.0
    customer_call_premium: float = 0.0
    customer_put_premium: float = 0.0
    dealer_estimated_net_gamma: float = 0.0
    dealer_estimated_net_delta: float = 0.0
    dealer_delta_hedge_notional_1pct: float = 0.0
    dealer_vanna_hedge_notional_1vol: float = 0.0
    dealer_charm_hedge_notional_1h: float = 0.0
    confidence: float = 0.0
    assumptions: list[str] = Field(default_factory=list)


IntradayState = Literal[
    "OPENING_FLOW",
    "LUNCH_DECAY",
    "POWER_HOUR_CHARM",
    "EXPIRY_PIN",
    "NEG_GAMMA_BREAKOUT",
    "CLOSED_OR_UNKNOWN",
]
TradeEligibility = Literal["eligible", "restricted", "blocked"]
VendorReconciliationStatus = Literal[
    "ready_for_vendor_compare", "partial_vendor_compare", "no_vendor_benchmarks"
]


class IntradayStateMachineMetrics(BaseModel):
    current_state: IntradayState = "CLOSED_OR_UNKNOWN"
    state_scores: dict[str, float] = Field(default_factory=dict)
    session_phase: str = "unknown"
    path_dependency_note: str = "snapshot_only_without_intraday_history"
    triggers: list[str] = Field(default_factory=list)


class PortfolioRiskOverlayMetrics(BaseModel):
    expected_hedge_flow_notional: float = 0.0
    tail_hedge_demand: float = 0.0
    iv_crush_risk: float = 0.0
    liquidity_haircut_pct: float = 0.0
    max_slippage_pct: float = 0.0
    trade_eligibility: TradeEligibility = "restricted"
    position_size_multiplier: float = 0.0
    guardrails: list[str] = Field(default_factory=list)


class MetricLineage(BaseModel):
    provider: str | None = None
    raw_fields: list[str] = Field(default_factory=list)
    timestamp: str | None = None
    latency_ms: float | None = None
    staleness: str = "unknown"
    formula_version: str
    fallback_used: bool = False
    confidence: float = 0.0


class DataLineageMetrics(BaseModel):
    metrics: dict[str, MetricLineage] = Field(default_factory=dict)
    metric_fields: dict[str, MetricLineage] = Field(default_factory=dict)
    generated_at: str | None = None
    coverage_pct: float = 0.0


class VendorDivergence(BaseModel):
    field: str
    vendor: str
    internal_value: float | None = None
    vendor_value: float | None = None
    divergence_pct: float | None = None
    status: str = "benchmark_unavailable"


class VendorReconciliationMetrics(BaseModel):
    status: VendorReconciliationStatus = "no_vendor_benchmarks"
    vendors_expected: list[str] = Field(
        default_factory=lambda: ["Cboe", "OCC", "Nasdaq", "Polygon", "Massive", "OptionMetrics"]
    )
    compared_fields: list[str] = Field(default_factory=list)
    divergences: list[VendorDivergence] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)


class SchedulerJobSpec(BaseModel):
    name: str
    schedule: str
    purpose: str
    enabled: bool = True


class SchedulerPlanMetrics(BaseModel):
    timezone: str = "America/New_York"
    cadence_minutes: int = 5
    jobs: list[SchedulerJobSpec] = Field(default_factory=list)
    rollover_policy: str = "advance_expiry_scope_after_market_close_and_after_expiration"
    persistence_target: str = "institutional_chain_analytics_history"


class AdvancedFlowMetrics(BaseModel):
    tape_aggressor_flow: TapeAggressorFlowMetrics = Field(default_factory=TapeAggressorFlowMetrics)
    quote_pressure: QuotePressureMetrics = Field(default_factory=QuotePressureMetrics)
    iv_skew_velocity: IvSkewVelocityMetrics = Field(default_factory=IvSkewVelocityMetrics)
    contract_metadata_risk: ContractMetadataRiskMetrics = Field(
        default_factory=ContractMetadataRiskMetrics
    )
    option_rv_iv: OptionRvIvMetrics = Field(default_factory=OptionRvIvMetrics)
    venue_quality: VenueQualityMetrics = Field(default_factory=VenueQualityMetrics)
    opening_closing_flow: OpeningClosingFlowMetrics = Field(
        default_factory=OpeningClosingFlowMetrics
    )
    gamma_hedge_demand: GammaHedgeDemandMetrics = Field(default_factory=GammaHedgeDemandMetrics)
    zero_dte_exhaustion: ZeroDteExhaustionMetrics = Field(default_factory=ZeroDteExhaustionMetrics)
    liquidity_stress: LiquidityStressMetrics = Field(default_factory=LiquidityStressMetrics)
    institutional_standard: InstitutionalStandardMetrics = Field(
        default_factory=InstitutionalStandardMetrics
    )
    trade_level_readiness: TradeLevelReadinessMetrics = Field(
        default_factory=TradeLevelReadinessMetrics
    )
    surface_diagnostics: SurfaceDiagnosticsMetrics = Field(
        default_factory=SurfaceDiagnosticsMetrics
    )
    product_conventions: ProductConventionMetrics = Field(default_factory=ProductConventionMetrics)
    dealer_positioning_v2: DealerPositioningV2Metrics = Field(
        default_factory=DealerPositioningV2Metrics
    )
    intraday_state_machine: IntradayStateMachineMetrics = Field(
        default_factory=IntradayStateMachineMetrics
    )
    portfolio_risk_overlay: PortfolioRiskOverlayMetrics = Field(
        default_factory=PortfolioRiskOverlayMetrics
    )
    data_lineage: DataLineageMetrics = Field(default_factory=DataLineageMetrics)
    vendor_reconciliation: VendorReconciliationMetrics = Field(
        default_factory=VendorReconciliationMetrics
    )
    scheduler_plan: SchedulerPlanMetrics = Field(default_factory=SchedulerPlanMetrics)


class ChainInstitutionalAnalyticsResponse(BaseModel):
    ticker: str
    spot: float
    as_of: str
    chain: list[InstitutionalOptionStrikeRow] = Field(default_factory=list)
    strike_analytics: list[StrikeAnalyticsRow] = Field(default_factory=list)
    expiry_analytics: list[ExpiryAnalytics] = Field(default_factory=list)
    institutional_metrics: ChainInstitutionalMetrics = Field(
        default_factory=ChainInstitutionalMetrics
    )
    delta_maturity_surface: list[DeltaMaturitySurfacePoint] = Field(default_factory=list)
    alerts: list[ChainAlert] = Field(default_factory=list)
    dominant_expiries: list[DominantExpiryRow] = Field(default_factory=list)
    advanced_flow_metrics: AdvancedFlowMetrics = Field(default_factory=AdvancedFlowMetrics)
    quality: InstitutionalChainQuality = Field(default_factory=InstitutionalChainQuality)
    ok: bool = True
    error: str | None = None


def _finite(value: Any) -> float | None:
    try:
        out = float(value)
    except (TypeError, ValueError):
        return None
    return out if math.isfinite(out) else None


def _round(value: float | None, digits: int = 6) -> float | None:
    return None if value is None or not math.isfinite(value) else round(float(value), digits)


def _safe_div(num: float, den: float) -> float | None:
    return (
        None if not math.isfinite(num) or not math.isfinite(den) or abs(den) < 1e-12 else num / den
    )


def _row_dict(row: Any) -> dict[str, Any]:
    if isinstance(row, dict):
        return dict(row)
    if hasattr(row, "model_dump"):
        dumped = row.model_dump(mode="python")
        return dumped if isinstance(dumped, dict) else {}
    return dict(row)


def _as_of_date(as_of: str | None) -> datetime:
    if as_of:
        try:
            parsed = datetime.fromisoformat(as_of.replace("Z", "+00:00"))
            if parsed.tzinfo is None:
                parsed = parsed.replace(tzinfo=UTC)
            return parsed.astimezone(UTC)
        except ValueError:
            pass
    return datetime.now(tz=UTC)


def _dte_years(expiration: str, as_of: str | None = None) -> float:
    try:
        exp = datetime.strptime(str(expiration)[:10], "%Y-%m-%d").replace(tzinfo=UTC)
    except ValueError:
        return 30.0 / 365.0
    days = (exp.date() - _as_of_date(as_of).date()).days
    return max(float(days), 1.0) / 365.0


def _side_mid(bid: float | None, ask: float | None, last: float | None) -> float | None:
    if bid is not None and ask is not None and bid >= 0 and ask >= bid:
        return (bid + ask) / 2.0
    return last if last is not None and last > 0 else None


def _size_imbalance(bid_size: float | None, ask_size: float | None) -> float | None:
    b = bid_size or 0.0
    a = ask_size or 0.0
    return _safe_div(b - a, b + a)


def _safe_int(value: Any) -> int | None:
    finite = _finite(value)
    return None if finite is None else int(round(finite))


def _liquidity_score(
    spread_pct: float | None, volume: float, oi: float, size_imbalance: float | None
) -> float | None:
    if spread_pct is None:
        return None
    spread_component = max(0.0, 1.0 - min(abs(spread_pct), 1.0))
    activity = math.log1p(max(volume, 0.0)) / math.log1p(max(volume + oi, 1.0))
    balance = 1.0 - min(abs(size_imbalance or 0.0), 1.0)
    return round(
        max(0.0, min((0.55 * spread_component + 0.30 * activity + 0.15 * balance) * 100.0, 100.0)),
        2,
    )


def _vomma(S: float, K: float, T: float, r: float, sigma: float) -> float:
    step = max(abs(sigma) * 1e-4, 1e-4)
    up = BlackScholesPricer.vega(S, K, T, r, sigma + step)
    down = BlackScholesPricer.vega(S, K, T, r, max(sigma - step, 1e-4))
    return (up - down) / (2.0 * step)


def _higher_greeks(S: float, K: float, T: float, r: float, sigma: float) -> dict[str, float]:
    k_arr = np.array([K], dtype=np.float64)
    sig_arr = np.array([sigma], dtype=np.float64)
    return {
        "vanna": float(BlackScholesPricer.vanna_vec(S, k_arr, T, r, sig_arr)[0]),
        "charm": float(BlackScholesPricer.charm_vec(S, k_arr, T, r, sig_arr)[0]),
        "speed": float(BlackScholesPricer.speed_vec(S, k_arr, T, r, sig_arr)[0]),
        "color": float(BlackScholesPricer.color_vec(S, k_arr, T, r, sig_arr)[0]),
        "zomma": float(BlackScholesPricer.zomma_vec(S, k_arr, T, r, sig_arr)[0]),
        "ultima": float(BlackScholesPricer.ultima_vec(S, k_arr, T, r, sig_arr)[0]),
        "vomma": _vomma(S, K, T, r, sigma),
    }


def _enrich_side(
    base: dict[str, Any],
    side: Literal["call", "put"],
    spot: float,
    strike: float,
    tte: float,
    r: float,
    metric_sources: dict[str, MetricSource],
    derived_fields: set[str],
) -> dict[str, Any]:
    opt = OptionType.CALL if side == "call" else OptionType.PUT
    bid = _finite(base.get(f"{side}_bid"))
    ask = _finite(base.get(f"{side}_ask"))
    last = _finite(base.get(f"{side}_last"))
    oi = _finite(base.get(f"{side}_oi")) or 0.0
    volume = _finite(base.get(f"{side}_volume")) or 0.0
    iv = _finite(base.get(f"{side}_iv"))
    if iv is None or iv <= 0:
        iv = 0.25
        metric_sources[f"{side}_iv"] = "surface_derived"
        derived_fields.add(f"{side}_iv")
    else:
        metric_sources[f"{side}_iv"] = "provider"

    mid = _side_mid(bid, ask, last)
    mark = mid if mid is not None else last
    spread_abs = (ask - bid) if bid is not None and ask is not None and ask >= bid else None
    spread_pct = (
        _safe_div(spread_abs or 0.0, mid or 0.0) if spread_abs is not None and mid else None
    )
    size_imb = _size_imbalance(
        _finite(base.get(f"{side}_bid_size")), _finite(base.get(f"{side}_ask_size"))
    )

    greeks = BlackScholesPricer.greeks(spot, strike, tte, r, iv, opt=opt, second_order=True)
    for greek_name in ("delta", "gamma", "theta", "vega"):
        key = f"{side}_{greek_name}"
        if _finite(base.get(key)) is None:
            base[key] = greeks[greek_name]
            derived_fields.add(key)
        metric_sources[key] = "bsm_derived"
        derived_fields.add(key)

    delta = _finite(base.get(f"{side}_delta")) or 0.0
    gamma = _finite(base.get(f"{side}_gamma")) or 0.0
    model_price = float(greeks["theoretical_price"])
    intrinsic = max(spot - strike, 0.0) if side == "call" else max(strike - spot, 0.0)
    extrinsic = max((mark or last or model_price) - intrinsic, 0.0)
    breakeven = strike + (mark or 0.0) if side == "call" else strike - (mark or 0.0)
    dex = delta * oi * CONTRACT_SIZE * spot
    gex = gamma * oi * CONTRACT_SIZE * spot * spot * 0.01
    if side == "put":
        gex = -abs(gex)

    higher = _higher_greeks(spot, strike, tte, r, iv)
    vanna = higher["vanna"]
    charm = higher["charm"]
    premium_volume = (mark or last or 0.0) * volume * CONTRACT_SIZE
    notional_volume = volume * CONTRACT_SIZE * spot
    vol_oi = _safe_div(volume, oi)
    lambda_val = _safe_div(abs(delta) * spot, mark or model_price)
    be_dist = _safe_div(breakeven - spot, spot)
    return {
        f"{side}_day_open": _finite(base.get(f"{side}_day_open")),
        f"{side}_day_high": _finite(base.get(f"{side}_day_high")),
        f"{side}_day_low": _finite(base.get(f"{side}_day_low")),
        f"{side}_day_close": _finite(base.get(f"{side}_day_close")),
        f"{side}_previous_close": _finite(base.get(f"{side}_previous_close")),
        f"{side}_oi_change": _finite(base.get(f"{side}_oi_change")),
        f"{side}_contract_ticker": str(base.get(f"{side}_contract_ticker") or "") or None,
        f"{side}_exercise_style": str(base.get(f"{side}_exercise_style") or "") or None,
        f"{side}_shares_per_contract": _finite(base.get(f"{side}_shares_per_contract")),
        f"{side}_primary_exchange": str(base.get(f"{side}_primary_exchange") or "") or None,
        f"{side}_additional_underlyings_count": _safe_int(
            base.get(f"{side}_additional_underlyings_count")
        ),
        f"{side}_mid": _round(mid),
        f"{side}_mark": _round(mark),
        f"{side}_spread_abs": _round(spread_abs),
        f"{side}_spread_pct": _round(spread_pct),
        f"{side}_quote_age_ms": _finite(base.get(f"{side}_quote_age_ms")),
        f"{side}_last_trade_age_ms": _finite(base.get(f"{side}_last_trade_age_ms")),
        f"{side}_liquidity_score": _liquidity_score(spread_pct, volume, oi, size_imb),
        f"{side}_bid_ask_size_imbalance": _round(size_imb),
        f"{side}_intrinsic_value": _round(intrinsic),
        f"{side}_extrinsic_value": _round(extrinsic),
        f"{side}_breakeven_distance_pct": _round(be_dist * 100.0 if be_dist is not None else None),
        f"{side}_model_price": _round(model_price),
        f"{side}_theoretical_edge": _round(model_price - (mark or model_price)),
        f"{side}_rho": _round(greeks["rho"]),
        f"{side}_lambda": _round(lambda_val),
        f"{side}_vomma": _round(higher["vomma"]),
        f"{side}_vanna": _round(vanna),
        f"{side}_charm": _round(charm),
        f"{side}_speed": _round(higher["speed"]),
        f"{side}_color": _round(higher["color"]),
        f"{side}_zomma": _round(higher["zomma"]),
        f"{side}_ultima": _round(higher["ultima"]),
        f"{side}_vex": _round(vanna * oi * CONTRACT_SIZE),
        f"{side}_cex": _round(charm * oi * CONTRACT_SIZE),
        f"{side}_dex": _round(dex),
        f"{side}_gex": _round(gex, 2),
        f"{side}_premium_volume": _round(premium_volume, 2),
        f"{side}_notional_volume": _round(notional_volume, 2),
        f"{side}_volume_oi_ratio": _round(vol_oi),
        f"{side}_oi_turnover_proxy": _round(vol_oi),
    }


def build_chain_institutional_analytics(
    ticker: str,
    spot: float,
    rows: list[Any],
    *,
    r: float = 0.04,
    provider: str | None = None,
    as_of: str | None = None,
    vendor_benchmarks: dict[str, dict[str, float]] | None = None,
) -> ChainInstitutionalAnalyticsResponse:
    as_of_s = as_of or datetime.now(tz=UTC).isoformat()
    if spot <= 0 or not rows:
        return ChainInstitutionalAnalyticsResponse(
            ticker=ticker.upper(),
            spot=spot,
            as_of=as_of_s,
            quality=InstitutionalChainQuality(
                provider=provider, rows=len(rows), warnings=["empty_chain_or_spot"]
            ),
            ok=False,
            error="Empty chain or invalid spot",
        )

    derived_fields: set[str] = set()
    enriched: list[InstitutionalOptionStrikeRow] = []
    for raw in rows:
        base = _row_dict(raw)
        strike = _finite(base.get("strike"))
        if strike is None or strike <= 0:
            continue
        expiration = str(base.get("expiration") or "")[:10]
        tte = _dte_years(expiration, as_of_s)
        metric_sources: dict[str, MetricSource] = {}
        base.update(
            _enrich_side(base, "call", spot, strike, tte, r, metric_sources, derived_fields)
        )
        base.update(_enrich_side(base, "put", spot, strike, tte, r, metric_sources, derived_fields))
        base["strike"] = strike
        base["expiration"] = expiration
        base["total_oi"] = (base.get("call_oi") or 0.0) + (base.get("put_oi") or 0.0)
        base["net_gex"] = round((base.get("call_gex") or 0.0) + (base.get("put_gex") or 0.0), 2)
        base["net_dex"] = round((base.get("call_dex") or 0.0) + (base.get("put_dex") or 0.0), 2)
        base["net_vex"] = round((base.get("call_vex") or 0.0) - (base.get("put_vex") or 0.0), 2)
        base["net_cex"] = round((base.get("call_cex") or 0.0) - (base.get("put_cex") or 0.0), 6)
        base["premium_volume"] = round(
            (base.get("call_premium_volume") or 0.0) + (base.get("put_premium_volume") or 0.0), 2
        )
        base["notional_volume"] = round(
            (base.get("call_notional_volume") or 0.0) + (base.get("put_notional_volume") or 0.0), 2
        )
        total_vol = (base.get("call_volume") or 0.0) + (base.get("put_volume") or 0.0)
        total_oi = base["total_oi"] or 0.0
        base["volume_oi_ratio"] = _round(_safe_div(total_vol, total_oi))
        base["oi_turnover_proxy"] = base["volume_oi_ratio"]
        base["moneyness"] = _round((strike - spot) / spot, 6)
        tte = _dte_years(expiration, as_of_s)
        if base.get("call_model_price") is not None and base.get("put_model_price") is not None:
            parity = (
                float(base["call_model_price"])
                - float(base["put_model_price"])
                - (spot - strike * math.exp(-r * tte))
            )
            base["put_call_parity_residual"] = _round(parity)
        base["metric_sources"] = metric_sources
        enriched.append(InstitutionalOptionStrikeRow(**base))

    total_abs_gex = sum(abs(r.net_gex or 0.0) for r in enriched)
    total_abs_dex = sum(abs(r.net_dex or 0.0) for r in enriched)
    for idx, row in enumerate(enriched):
        data = row.model_dump()
        data["gex_share_pct"] = _round(
            (abs(row.net_gex or 0.0) / total_abs_gex) * 100.0 if total_abs_gex else None, 4
        )
        data["dex_share_pct"] = _round(
            (abs(row.net_dex or 0.0) / total_abs_dex) * 100.0 if total_abs_dex else None, 4
        )
        enriched[idx] = InstitutionalOptionStrikeRow(**data)

    strike_rows = _build_strike_analytics(enriched, spot)
    expiry_rows = _build_expiry_analytics(enriched, spot, as_of_s)
    metrics = _build_institutional_metrics(enriched, strike_rows)
    delta_surface = _build_delta_maturity_surface(enriched, as_of_s)
    dominant_expiries = _build_dominant_expiries(enriched, expiry_rows, as_of_s)
    warnings = _quality_warnings(enriched)
    quality_score = _quality_score(enriched, warnings)
    metrics.data_quality_score = quality_score
    quality = InstitutionalChainQuality(
        provider=provider,
        rows=len(enriched),
        expiries=len({r.expiration for r in enriched if r.expiration}),
        derived_fields=sorted(derived_fields),
        warnings=warnings,
        data_quality_score=quality_score,
    )
    advanced_flow_metrics = _build_advanced_flow_metrics(
        enriched,
        expiry_rows,
        metrics,
        strike_rows,
        spot,
        r,
        ticker,
        provider,
        as_of_s,
        quality,
        vendor_benchmarks,
    )
    alerts = _build_chain_alerts(
        enriched, spot, metrics, expiry_rows, dominant_expiries, advanced_flow_metrics
    )
    return ChainInstitutionalAnalyticsResponse(
        ticker=ticker.upper(),
        spot=round(spot, 4),
        as_of=as_of_s,
        chain=enriched,
        strike_analytics=strike_rows,
        expiry_analytics=expiry_rows,
        institutional_metrics=metrics,
        delta_maturity_surface=delta_surface,
        alerts=alerts,
        dominant_expiries=dominant_expiries,
        advanced_flow_metrics=advanced_flow_metrics,
        quality=quality,
    )


def _build_strike_analytics(
    rows: list[InstitutionalOptionStrikeRow], spot: float
) -> list[StrikeAnalyticsRow]:
    ranked = sorted(rows, key=lambda r: abs(r.net_gex or 0.0), reverse=True)
    ranks = {r.strike: i + 1 for i, r in enumerate(ranked)}
    out: list[StrikeAnalyticsRow] = []
    cumulative_gex = 0.0
    cumulative_dex = 0.0
    for row in sorted(rows, key=lambda r: r.strike):
        call_oi = row.call_oi or 0.0
        put_oi = row.put_oi or 0.0
        call_volume = row.call_volume or 0.0
        put_volume = row.put_volume or 0.0
        cumulative_gex += row.net_gex or 0.0
        cumulative_dex += row.net_dex or 0.0
        distance = abs((row.strike - spot) / spot) if spot > 0 else 1.0
        concentration = (row.gex_share_pct or 0.0) / 100.0
        pin_score = max(
            0.0, min(100.0, (1.0 - min(distance / 0.05, 1.0)) * 55.0 + concentration * 45.0)
        )
        out.append(
            StrikeAnalyticsRow(
                strike=row.strike,
                expiration=row.expiration,
                call_oi=call_oi,
                put_oi=put_oi,
                call_volume=call_volume,
                put_volume=put_volume,
                pcr_oi=_round(_safe_div(put_oi, call_oi), 6),
                pcr_volume=_round(_safe_div(put_volume, call_volume), 6),
                net_premium=round(
                    (row.call_premium_volume or 0.0) - (row.put_premium_volume or 0.0), 2
                ),
                cumulative_gex=round(cumulative_gex, 2),
                cumulative_dex=round(cumulative_dex, 2),
                wall_rank=ranks.get(row.strike),
                pin_risk_score=round(pin_score, 2),
            )
        )
    return out


def _build_expiry_analytics(
    rows: list[InstitutionalOptionStrikeRow], spot: float, as_of: str | None
) -> list[ExpiryAnalytics]:
    by_exp: dict[str, list[InstitutionalOptionStrikeRow]] = {}
    for row in rows:
        by_exp.setdefault(row.expiration, []).append(row)
    total_abs_gex = sum(abs(r.net_gex or 0.0) for r in rows)
    out: list[ExpiryAnalytics] = []
    for exp, items in sorted(by_exp.items()):
        call_oi = sum(r.call_oi or 0.0 for r in items)
        put_oi = sum(r.put_oi or 0.0 for r in items)
        call_vol = sum(r.call_volume or 0.0 for r in items)
        put_vol = sum(r.put_volume or 0.0 for r in items)
        atm = min(items, key=lambda r: abs(r.strike - spot))
        iv_values = [v for v in (atm.call_iv, atm.put_iv) if v is not None and v > 0]
        atm_iv = float(np.nanmean(iv_values)) if iv_values else None
        dte_years = _dte_years(exp, as_of)
        exp_abs_gex = sum(abs(r.net_gex or 0.0) for r in items)
        implied_move = spot * float(atm_iv or 0.0) * math.sqrt(dte_years)
        ivs = [v for r in items for v in (r.call_iv, r.put_iv) if v is not None and v > 0]
        out.append(
            ExpiryAnalytics(
                expiration=exp,
                dte_days=round(dte_years * 365.0, 2),
                total_open_interest=round(call_oi + put_oi, 2),
                total_volume=round(call_vol + put_vol, 2),
                total_premium_volume=round(sum(r.premium_volume or 0.0 for r in items), 2),
                pcr_oi=_round(_safe_div(put_oi, call_oi), 6),
                pcr_volume=_round(_safe_div(put_vol, call_vol), 6),
                atm_iv=_round(atm_iv),
                iv_breadth=_round((max(ivs) - min(ivs)) if ivs else None),
                zero_dte_gamma_share=round(
                    (
                        (exp_abs_gex / total_abs_gex) * 100.0
                        if total_abs_gex and dte_years * 365.0 <= 1.5
                        else 0.0
                    ),
                    4,
                ),
                expiry_gamma_pressure=round(sum(r.net_gex or 0.0 for r in items), 2),
                expiry_charm_pressure=round(sum(r.net_cex or 0.0 for r in items), 6),
                implied_move=_round(implied_move, 4),
                implied_move_pct=_round((implied_move / spot) * 100.0 if spot > 0 else None, 4),
            )
        )
    return out


def _build_institutional_metrics(
    rows: list[InstitutionalOptionStrikeRow], strike_rows: list[StrikeAnalyticsRow]
) -> ChainInstitutionalMetrics:
    call_oi = sum(r.call_oi or 0.0 for r in rows)
    put_oi = sum(r.put_oi or 0.0 for r in rows)
    call_vol = sum(r.call_volume or 0.0 for r in rows)
    put_vol = sum(r.put_volume or 0.0 for r in rows)
    total_gex = sum(r.net_gex or 0.0 for r in rows)
    total_abs_gex = sum(abs(r.net_gex or 0.0) for r in rows)
    if total_abs_gex <= 1e-9:
        regime: GammaRegime = "NEUTRAL_GAMMA"
    elif abs(total_gex) / total_abs_gex < 0.15:
        regime = "TRANSITION_GAMMA"
    elif total_gex > 0:
        regime = "POSITIVE_GAMMA"
    else:
        regime = "NEGATIVE_GAMMA"
    vol_trigger = (
        min(strike_rows, key=lambda r: abs(r.cumulative_gex)).strike if strike_rows else None
    )
    total_vex_abs = max(sum(abs(r.net_vex or 0.0) for r in rows), 1.0)
    total_cex_abs = max(sum(abs(r.net_cex or 0.0) for r in rows), 1.0)
    dealer_pressure = _safe_div(total_gex, total_abs_gex) or 0.0
    dealer_pressure += (_safe_div(sum(r.net_vex or 0.0 for r in rows), total_vex_abs) or 0.0) * 0.35
    dealer_pressure += (_safe_div(sum(r.net_cex or 0.0 for r in rows), total_cex_abs) or 0.0) * 0.15
    return ChainInstitutionalMetrics(
        total_open_interest=round(call_oi + put_oi, 2),
        total_volume=round(call_vol + put_vol, 2),
        total_premium_volume=round(sum(r.premium_volume or 0.0 for r in rows), 2),
        total_notional_volume=round(sum(r.notional_volume or 0.0 for r in rows), 2),
        total_gex=round(total_gex, 2),
        total_dex=round(sum(r.net_dex or 0.0 for r in rows), 2),
        total_vex=round(sum(r.net_vex or 0.0 for r in rows), 2),
        total_cex=round(sum(r.net_cex or 0.0 for r in rows), 6),
        pcr_oi=_round(_safe_div(put_oi, call_oi), 6),
        pcr_volume=_round(_safe_div(put_vol, call_vol), 6),
        gamma_regime=regime,
        vol_trigger_proxy=vol_trigger,
        dealer_pressure_score=round(max(-100.0, min(100.0, dealer_pressure * 100.0)), 2),
    )


def _by_expiry(
    rows: list[InstitutionalOptionStrikeRow],
) -> dict[str, list[InstitutionalOptionStrikeRow]]:
    by_exp: dict[str, list[InstitutionalOptionStrikeRow]] = {}
    for row in rows:
        by_exp.setdefault(row.expiration, []).append(row)
    return by_exp


def _nearest_by_delta(
    items: list[InstitutionalOptionStrikeRow],
    side: Literal["call", "put"],
    target: float,
) -> InstitutionalOptionStrikeRow | None:
    if side == "call":
        candidates = [
            r for r in items if r.call_delta is not None and r.call_iv is not None and r.call_iv > 0
        ]
        if not candidates:
            return None
        return min(candidates, key=lambda r: abs(abs(r.call_delta or 0.0) - target))
    candidates = [
        r for r in items if r.put_delta is not None and r.put_iv is not None and r.put_iv > 0
    ]
    if not candidates:
        return None
    return min(candidates, key=lambda r: abs(abs(r.put_delta or 0.0) - target))


def _build_delta_maturity_surface(
    rows: list[InstitutionalOptionStrikeRow], as_of: str | None
) -> list[DeltaMaturitySurfacePoint]:
    buckets: list[tuple[str, float]] = [
        ("10d", 0.10),
        ("25d", 0.25),
        ("50d", 0.50),
        ("75d", 0.75),
        ("90d", 0.90),
    ]
    out: list[DeltaMaturitySurfacePoint] = []
    for exp, items in sorted(_by_expiry(rows).items()):
        atm_call = _nearest_by_delta(items, "call", 0.50)
        atm_put = _nearest_by_delta(items, "put", 0.50)
        atm_ivs = [
            v
            for v in (
                atm_call.call_iv if atm_call else None,
                atm_put.put_iv if atm_put else None,
            )
            if v is not None and v > 0
        ]
        atm_mid = float(np.nanmean(atm_ivs)) if atm_ivs else None
        for label, target in buckets:
            call_row = _nearest_by_delta(items, "call", target)
            put_row = _nearest_by_delta(items, "put", target)
            call_iv = call_row.call_iv if call_row else None
            put_iv = put_row.put_iv if put_row else None
            ivs = [v for v in (call_iv, put_iv) if v is not None and v > 0]
            mid_iv = float(np.nanmean(ivs)) if ivs else None
            rr = (call_iv - put_iv) if call_iv is not None and put_iv is not None else None
            fly = (mid_iv - atm_mid) if mid_iv is not None and atm_mid is not None else None
            out.append(
                DeltaMaturitySurfacePoint(
                    expiration=exp,
                    dte_days=round(_dte_years(exp, as_of) * 365.0, 2),
                    delta_bucket=label,
                    target_delta=target,
                    call_strike=call_row.strike if call_row else None,
                    put_strike=put_row.strike if put_row else None,
                    call_iv=_round(call_iv),
                    put_iv=_round(put_iv),
                    mid_iv=_round(mid_iv),
                    risk_reversal=_round(rr),
                    butterfly_proxy=_round(fly),
                )
            )
    return out


def _build_dominant_expiries(
    rows: list[InstitutionalOptionStrikeRow],
    expiry_rows: list[ExpiryAnalytics],
    as_of: str | None,
) -> list[DominantExpiryRow]:
    by_exp = _by_expiry(rows)
    total_abs_gex = max(sum(abs(r.net_gex or 0.0) for r in rows), 1.0)
    total_oi = max(sum((r.call_oi or 0.0) + (r.put_oi or 0.0) for r in rows), 1.0)
    total_vol = max(sum((r.call_volume or 0.0) + (r.put_volume or 0.0) for r in rows), 1.0)
    total_premium = max(sum(r.premium_volume or 0.0 for r in rows), 1.0)
    zero_dte_by_exp = {e.expiration: e.zero_dte_gamma_share for e in expiry_rows}
    provisional: list[DominantExpiryRow] = []
    for exp, items in sorted(by_exp.items()):
        exp_oi = sum((r.call_oi or 0.0) + (r.put_oi or 0.0) for r in items)
        exp_vol = sum((r.call_volume or 0.0) + (r.put_volume or 0.0) for r in items)
        exp_premium = sum(r.premium_volume or 0.0 for r in items)
        exp_gex = sum(r.net_gex or 0.0 for r in items)
        exp_abs_gex = sum(abs(r.net_gex or 0.0) for r in items)
        gamma_share = (exp_abs_gex / total_abs_gex) * 100.0
        oi_share = (exp_oi / total_oi) * 100.0
        vol_share = (exp_vol / total_vol) * 100.0
        premium_share = (exp_premium / total_premium) * 100.0
        zero_dte_share = zero_dte_by_exp.get(exp, 0.0)
        dominance = gamma_share * 0.45 + oi_share * 0.25 + vol_share * 0.20 + premium_share * 0.10
        dominance += min(zero_dte_share, 100.0) * 0.10
        provisional.append(
            DominantExpiryRow(
                expiration=exp,
                dte_days=round(_dte_years(exp, as_of) * 365.0, 2),
                rank=0,
                total_open_interest=round(exp_oi, 2),
                total_volume=round(exp_vol, 2),
                total_premium_volume=round(exp_premium, 2),
                total_gex=round(exp_gex, 2),
                abs_gex=round(exp_abs_gex, 2),
                gamma_share_pct=round(gamma_share, 4),
                oi_share_pct=round(oi_share, 4),
                volume_share_pct=round(vol_share, 4),
                premium_share_pct=round(premium_share, 4),
                zero_dte_gamma_share=round(zero_dte_share, 4),
                dominance_score=round(dominance, 4),
            )
        )
    ranked = sorted(provisional, key=lambda e: e.dominance_score, reverse=True)
    return [e.model_copy(update={"rank": i + 1}) for i, e in enumerate(ranked)]


def _side_premium(row: InstitutionalOptionStrikeRow, side: Literal["call", "put"]) -> float:
    volume = getattr(row, f"{side}_volume") or 0.0
    mark = getattr(row, f"{side}_mark") or getattr(row, f"{side}_last") or 0.0
    return float(volume) * float(mark) * CONTRACT_SIZE


def _side_aggressor_sign(row: InstitutionalOptionStrikeRow, side: Literal["call", "put"]) -> int:
    last = _finite(getattr(row, f"{side}_last"))
    bid = _finite(getattr(row, f"{side}_bid"))
    ask = _finite(getattr(row, f"{side}_ask"))
    mid = _finite(getattr(row, f"{side}_mid"))
    if last is None or mid is None:
        return 0
    half_spread = (
        abs((ask or mid) - (bid or mid)) / 2.0 if bid is not None and ask is not None else 0.0
    )
    tolerance = max(half_spread * 0.15, 0.005)
    if last >= mid + tolerance:
        return 1
    if last <= mid - tolerance:
        return -1
    return 0


def _build_tape_aggressor_flow(
    rows: list[InstitutionalOptionStrikeRow],
) -> TapeAggressorFlowMetrics:
    call_premium = 0.0
    put_premium = 0.0
    buy_volume = 0.0
    sell_volume = 0.0
    aggressive_contracts = 0
    total_contracts = 0
    for row in rows:
        for side in ("call", "put"):
            volume = float(getattr(row, f"{side}_volume") or 0.0)
            if volume <= 0:
                continue
            total_contracts += 1
            if side == "call":
                sign = _side_aggressor_sign(row, "call")
                premium = _side_premium(row, "call")
            else:
                sign = _side_aggressor_sign(row, "put")
                premium = _side_premium(row, "put")
            if sign > 0:
                buy_volume += volume
                aggressive_contracts += 1
            elif sign < 0:
                sell_volume += volume
                aggressive_contracts += 1
            signed_premium = premium * sign
            if side == "call":
                call_premium += signed_premium
            else:
                put_premium += signed_premium
    flow_imbalance = _safe_div(buy_volume - sell_volume, buy_volume + sell_volume)
    sweep_score = (
        0.0 if total_contracts == 0 else min(100.0, aggressive_contracts / total_contracts * 100.0)
    )
    return TapeAggressorFlowMetrics(
        call_aggressive_premium=round(call_premium, 2),
        put_aggressive_premium=round(put_premium, 2),
        net_aggressive_premium=round(call_premium + put_premium, 2),
        buyer_initiated_volume=round(buy_volume, 2),
        seller_initiated_volume=round(sell_volume, 2),
        flow_imbalance=_round(flow_imbalance, 6),
        sweep_score=round(sweep_score, 2),
    )


def _build_quote_pressure(rows: list[InstitutionalOptionStrikeRow]) -> QuotePressureMetrics:
    weighted_imbalance = 0.0
    total_weight = 0.0
    spreads: list[float] = []
    stale = 0
    observed_quotes = 0
    executable_notional = 0.0
    for row in rows:
        for side in ("call", "put"):
            bid_size = _finite(getattr(row, f"{side}_bid_size")) or 0.0
            ask_size = _finite(getattr(row, f"{side}_ask_size")) or 0.0
            imbalance = _finite(getattr(row, f"{side}_bid_ask_size_imbalance"))
            spread = _finite(getattr(row, f"{side}_spread_pct"))
            mark = _finite(getattr(row, f"{side}_mark")) or 0.0
            age = _finite(getattr(row, f"{side}_quote_age_ms"))
            weight = bid_size + ask_size
            if imbalance is not None and weight > 0:
                signed = imbalance if side == "call" else -imbalance
                weighted_imbalance += signed * weight
                total_weight += weight
                executable_notional += min(bid_size, ask_size) * mark * CONTRACT_SIZE
            if spread is not None:
                spreads.append(abs(spread) * 100.0)
            if age is not None:
                observed_quotes += 1
                if age > 60_000:
                    stale += 1
    net_imbalance = _safe_div(weighted_imbalance, total_weight) or 0.0
    avg_spread = float(np.nanmean(spreads)) if spreads else None
    spread_penalty = min(avg_spread or 0.0, 25.0) / 25.0
    pressure = max(-100.0, min(100.0, net_imbalance * 100.0 * (1.0 - 0.35 * spread_penalty)))
    return QuotePressureMetrics(
        net_size_imbalance=round(net_imbalance, 6),
        quote_pressure_score=round(pressure, 2),
        avg_spread_pct=_round(avg_spread, 4),
        stale_quote_ratio=_round(_safe_div(stale, observed_quotes), 6),
        executable_notional_top=round(executable_notional, 2),
    )


def _build_iv_skew_velocity(
    rows: list[InstitutionalOptionStrikeRow],
    expiry_rows: list[ExpiryAnalytics],
    metrics: ChainInstitutionalMetrics,
) -> IvSkewVelocityMetrics:
    atm_iv = expiry_rows[0].atm_iv if expiry_rows else None
    points: list[tuple[float, float]] = []
    for row in rows:
        mid_iv_values = [v for v in (row.call_iv, row.put_iv) if v is not None and v > 0]
        if not mid_iv_values:
            continue
        points.append((row.moneyness or 0.0, float(np.nanmean(mid_iv_values))))
    skew_slope = None
    if len(points) >= 2:
        x = np.array([p[0] for p in points], dtype=np.float64)
        y = np.array([p[1] for p in points], dtype=np.float64)
        if np.nanstd(x) > 1e-9:
            skew_slope = float(np.polyfit(x, y, 1)[0])
    front_back = None
    valid_exp_iv = [exp.atm_iv for exp in expiry_rows if exp.atm_iv is not None]
    if len(valid_exp_iv) >= 2:
        front_back = float(valid_exp_iv[0] - valid_exp_iv[-1])
    return IvSkewVelocityMetrics(
        atm_iv_proxy=_round(atm_iv),
        skew_slope_proxy=_round(skew_slope),
        front_back_iv_spread=_round(front_back),
        vol_trigger_velocity=_round(metrics.vol_trigger_proxy),
        zero_gamma_velocity=_round(metrics.vol_trigger_proxy),
    )


def _side_contract_risk(
    row: InstitutionalOptionStrikeRow, side: Literal["call", "put"]
) -> tuple[float, list[str]]:
    score = 0.0
    warnings: list[str] = []
    shares = _finite(getattr(row, f"{side}_shares_per_contract"))
    additional_count = _safe_int(getattr(row, f"{side}_additional_underlyings_count"))
    exercise_style = str(getattr(row, f"{side}_exercise_style") or "").lower()
    if shares is not None and abs(shares - CONTRACT_SIZE) > 1e-9:
        score += 45.0
        warnings.append(f"{side}_nonstandard_shares")
    if additional_count is not None and additional_count > 0:
        score += 50.0
        warnings.append(f"{side}_additional_underlyings")
    if exercise_style and exercise_style not in {"american", "european"}:
        score += 15.0
        warnings.append(f"{side}_unknown_exercise_style")
    return min(score, 100.0), warnings


def _build_contract_metadata_risk(
    rows: list[InstitutionalOptionStrikeRow],
) -> ContractMetadataRiskMetrics:
    observed = 0
    adjusted = 0
    non_standard = 0
    max_score = 0.0
    warnings: list[str] = []
    for row in rows:
        for side in ("call", "put"):
            if (
                getattr(row, f"{side}_contract_ticker")
                or getattr(row, f"{side}_shares_per_contract") is not None
            ):
                observed += 1
            score, side_warnings = _side_contract_risk(row, "call" if side == "call" else "put")
            if score > 0:
                adjusted += 1
                max_score = max(max_score, score)
                warnings.extend(side_warnings)
            if any(
                "nonstandard" in warning or "additional" in warning for warning in side_warnings
            ):
                non_standard += 1
    return ContractMetadataRiskMetrics(
        contracts_observed=observed,
        adjusted_contracts=adjusted,
        non_standard_deliverables=non_standard,
        max_risk_score=round(max_score, 2),
        warnings=sorted(set(warnings)),
    )


def _side_day_realized_vol(
    row: InstitutionalOptionStrikeRow, side: Literal["call", "put"]
) -> float | None:
    high = _finite(getattr(row, f"{side}_day_high"))
    low = _finite(getattr(row, f"{side}_day_low"))
    close = _finite(getattr(row, f"{side}_day_close")) or _finite(getattr(row, f"{side}_mark"))
    if (
        high is None
        or low is None
        or close is None
        or high <= 0
        or low <= 0
        or close <= 0
        or high < low
    ):
        return None
    parkinson_var = (math.log(high / low) ** 2) / (4.0 * math.log(2.0))
    return math.sqrt(max(parkinson_var, 0.0)) * math.sqrt(252.0)


def _build_option_rv_iv(rows: list[InstitutionalOptionStrikeRow]) -> OptionRvIvMetrics:
    weighted_rv = 0.0
    weighted_iv = 0.0
    total_weight = 0.0
    compression_weighted = 0.0
    compression_total = 0.0
    for row in rows:
        for side in ("call", "put"):
            rv = _side_day_realized_vol(row, "call" if side == "call" else "put")
            iv = _finite(getattr(row, f"{side}_iv"))
            volume = _finite(getattr(row, f"{side}_volume")) or 0.0
            if rv is not None and iv is not None and iv > 0 and volume > 0:
                weighted_rv += rv * volume
                weighted_iv += iv * volume
                total_weight += volume
            open_px = _finite(getattr(row, f"{side}_day_open"))
            close_px = _finite(getattr(row, f"{side}_day_close"))
            if open_px is not None and close_px is not None and open_px > 0:
                compression_weighted += max(0.0, (open_px - close_px) / open_px) * volume
                compression_total += volume
    rv_proxy = _safe_div(weighted_rv, total_weight)
    iv_proxy = _safe_div(weighted_iv, total_weight)
    return OptionRvIvMetrics(
        option_realized_vol_proxy=_round(rv_proxy),
        weighted_iv=_round(iv_proxy),
        rv_iv_spread=_round(
            (rv_proxy - iv_proxy) if rv_proxy is not None and iv_proxy is not None else None
        ),
        premium_compression_score=round(
            ((_safe_div(compression_weighted, compression_total) or 0.0) * 100.0), 4
        ),
    )


def _build_venue_quality(rows: list[InstitutionalOptionStrikeRow]) -> VenueQualityMetrics:
    by_exchange: dict[str, float] = {}
    for row in rows:
        for side in ("call", "put"):
            exchange = str(getattr(row, f"{side}_primary_exchange") or "").strip().upper()
            volume = _finite(getattr(row, f"{side}_volume")) or 0.0
            if exchange and volume > 0:
                by_exchange[exchange] = by_exchange.get(exchange, 0.0) + volume
    total = sum(by_exchange.values())
    if total <= 0:
        return VenueQualityMetrics()
    top_exchange, top_volume = max(by_exchange.items(), key=lambda item: item[1])
    shares = [volume / total for volume in by_exchange.values()]
    hhi = sum(share * share for share in shares)
    dispersion = max(0.0, min(100.0, (1.0 - hhi) * 100.0))
    return VenueQualityMetrics(
        exchange_count=len(by_exchange),
        top_exchange=top_exchange,
        top_exchange_share_pct=round(top_volume / total * 100.0, 4),
        concentration_hhi=round(hhi, 6),
        venue_dispersion_score=round(dispersion, 4),
    )


def _build_opening_closing_flow(
    rows: list[InstitutionalOptionStrikeRow],
) -> OpeningClosingFlowMetrics:
    opening_volume = 0.0
    closing_volume = 0.0
    net_opening_premium = 0.0
    for row in rows:
        for side in ("call", "put"):
            volume = _finite(getattr(row, f"{side}_volume")) or 0.0
            oi_change = _finite(getattr(row, f"{side}_oi_change"))
            premium = _side_premium(row, "call" if side == "call" else "put")
            if volume <= 0 or oi_change is None:
                continue
            opening = min(volume, max(oi_change, 0.0))
            closing = min(volume, abs(min(oi_change, 0.0)))
            opening_volume += opening
            closing_volume += closing
            sign = 1.0 if side == "call" else -1.0
            net_opening_premium += premium * (opening / volume) * sign
    fresh_ratio = _safe_div(opening_volume - closing_volume, opening_volume + closing_volume)
    return OpeningClosingFlowMetrics(
        opening_volume=round(opening_volume, 2),
        closing_volume=round(closing_volume, 2),
        fresh_positioning_ratio=_round(fresh_ratio, 6),
        net_opening_premium=round(net_opening_premium, 2),
    )


def _build_gamma_hedge_demand(
    rows: list[InstitutionalOptionStrikeRow], spot: float
) -> GammaHedgeDemandMetrics:
    if spot <= 0:
        return GammaHedgeDemandMetrics()
    net_gamma_shares = sum(
        (
            (row.call_gamma or 0.0) * (row.call_oi or 0.0)
            - (row.put_gamma or 0.0) * (row.put_oi or 0.0)
        )
        * CONTRACT_SIZE
        for row in rows
    )
    net_charm_shares_per_year = sum(
        (
            (row.call_charm or 0.0) * (row.call_oi or 0.0)
            - (row.put_charm or 0.0) * (row.put_oi or 0.0)
        )
        * CONTRACT_SIZE
        for row in rows
    )
    net_vanna_shares = sum(
        (
            (row.call_vanna or 0.0) * (row.call_oi or 0.0)
            - (row.put_vanna or 0.0) * (row.put_oi or 0.0)
        )
        * CONTRACT_SIZE
        for row in rows
    )

    def shock(delta_pct: float) -> float:
        return -net_gamma_shares * spot * delta_pct

    return GammaHedgeDemandMetrics(
        up_1pct_shares_to_trade=_round(shock(0.01), 2),
        down_1pct_shares_to_trade=_round(shock(-0.01), 2),
        up_2pct_shares_to_trade=_round(shock(0.02), 2),
        down_2pct_shares_to_trade=_round(shock(-0.02), 2),
        charm_1h_shares_to_trade=_round(-(net_charm_shares_per_year / (365.0 * 24.0)), 2),
        vanna_plus_1vol_shares_to_trade=_round(-(net_vanna_shares * 0.01), 2),
    )


def _build_zero_dte_exhaustion(
    rows: list[InstitutionalOptionStrikeRow], expiry_rows: list[ExpiryAnalytics]
) -> ZeroDteExhaustionMetrics:
    zero_expiries = {
        expiry.expiration for expiry in expiry_rows if (expiry.dte_days or 999.0) <= 1.5
    }
    total_volume = sum((row.call_volume or 0.0) + (row.put_volume or 0.0) for row in rows)
    total_premium = sum(row.premium_volume or 0.0 for row in rows)
    zero_rows = [row for row in rows if row.expiration in zero_expiries]
    zero_volume = sum((row.call_volume or 0.0) + (row.put_volume or 0.0) for row in zero_rows)
    zero_premium = sum(row.premium_volume or 0.0 for row in zero_rows)
    zero_charm = sum(row.net_cex or 0.0 for row in zero_rows)
    volume_share = (_safe_div(zero_volume, total_volume) or 0.0) * 100.0
    premium_share = (_safe_div(zero_premium, total_premium) or 0.0) * 100.0
    charm_component = min(abs(zero_charm) / 10_000.0, 1.0) * 35.0
    concentration_component = min(volume_share, 100.0) * 0.45
    premium_component = min(premium_share, 100.0) * 0.20
    return ZeroDteExhaustionMetrics(
        zero_dte_volume_share_pct=round(volume_share, 4),
        zero_dte_premium_share_pct=round(premium_share, 4),
        zero_dte_charm_pressure=round(zero_charm, 6),
        late_day_decay_proxy=round(charm_component, 4),
        exhaustion_score=round(
            min(100.0, concentration_component + premium_component + charm_component), 4
        ),
    )


def _build_liquidity_stress(rows: list[InstitutionalOptionStrikeRow]) -> LiquidityStressMetrics:
    spreads: list[float] = []
    stale = 0
    observed = 0
    locked_crossed = 0
    depth_scores: list[float] = []
    for row in rows:
        for side in ("call", "put"):
            bid = _finite(getattr(row, f"{side}_bid"))
            ask = _finite(getattr(row, f"{side}_ask"))
            spread_pct = _finite(getattr(row, f"{side}_spread_pct"))
            age = _finite(getattr(row, f"{side}_quote_age_ms"))
            bid_size = _finite(getattr(row, f"{side}_bid_size")) or 0.0
            ask_size = _finite(getattr(row, f"{side}_ask_size")) or 0.0
            if spread_pct is not None:
                spreads.append(abs(spread_pct) * 100.0)
            if age is not None:
                observed += 1
                if age > 60_000:
                    stale += 1
            if bid is not None and ask is not None and bid >= ask:
                locked_crossed += 1
            if bid_size + ask_size > 0:
                depth_scores.append(1.0 - min(bid_size, ask_size) / max(bid_size, ask_size))
    avg_spread = float(np.nanmean(spreads)) if spreads else None
    max_spread = max(spreads) if spreads else None
    stale_ratio = _safe_div(stale, observed) or 0.0
    depth_evap = float(np.nanmean(depth_scores)) if depth_scores else 0.0
    spread_component = min((avg_spread or 0.0) / 25.0, 1.0) * 35.0
    stale_component = stale_ratio * 30.0
    depth_component = depth_evap * 25.0
    crossed_component = min(locked_crossed * 10.0, 10.0)
    return LiquidityStressMetrics(
        stress_score=round(
            min(100.0, spread_component + stale_component + depth_component + crossed_component), 4
        ),
        avg_spread_pct=_round(avg_spread, 4),
        max_spread_pct=_round(max_spread, 4),
        stale_quote_ratio=round(stale_ratio, 6),
        depth_evaporation_score=round(depth_evap * 100.0, 4),
        locked_crossed_count=locked_crossed,
    )


def _interp_by_dte(points: list[tuple[float, float]], target_days: float) -> float | None:
    clean = sorted((d, v) for d, v in points if math.isfinite(d) and math.isfinite(v) and d > 0)
    if not clean:
        return None
    if len(clean) == 1:
        return clean[0][1]
    if target_days <= clean[0][0]:
        return clean[0][1]
    if target_days >= clean[-1][0]:
        return clean[-1][1]
    for (d0, v0), (d1, v1) in zip(clean, clean[1:], strict=False):
        if d0 <= target_days <= d1:
            weight = (target_days - d0) / max(d1 - d0, 1e-9)
            return v0 + (v1 - v0) * weight
    return min(clean, key=lambda p: abs(p[0] - target_days))[1]


def _build_normalized_25d_skew_30(
    rows: list[InstitutionalOptionStrikeRow], as_of: str | None
) -> float | None:
    points: list[tuple[float, float]] = []
    for exp, items in _by_expiry(rows).items():
        call_row = _nearest_by_delta(items, "call", 0.25)
        put_row = _nearest_by_delta(items, "put", 0.25)
        atm_call = _nearest_by_delta(items, "call", 0.50)
        atm_put = _nearest_by_delta(items, "put", 0.50)
        call_iv = call_row.call_iv if call_row else None
        put_iv = put_row.put_iv if put_row else None
        atm_ivs = [
            v
            for v in (
                atm_call.call_iv if atm_call else None,
                atm_put.put_iv if atm_put else None,
            )
            if v is not None and v > 0
        ]
        if call_iv is None or put_iv is None or not atm_ivs:
            continue
        atm_iv = float(np.nanmean(atm_ivs))
        if atm_iv <= 0:
            continue
        points.append((_dte_years(exp, as_of) * 365.0, (put_iv - call_iv) / atm_iv))
    return _interp_by_dte(points, 30.0)


def _single_expiry_vix_style_variance(
    items: list[InstitutionalOptionStrikeRow], r: float, as_of: str | None
) -> tuple[float, float] | None:
    if len(items) < 3:
        return None
    ordered = sorted(items, key=lambda row: row.strike)
    t = _dte_years(ordered[0].expiration, as_of)
    if t <= 0:
        return None
    parity_rows: list[tuple[float, float]] = []
    for row in ordered:
        if row.call_mid is not None and row.put_mid is not None:
            parity_rows.append(
                (
                    abs(row.call_mid - row.put_mid),
                    row.strike + math.exp(r * t) * (row.call_mid - row.put_mid),
                )
            )
    if not parity_rows:
        return None
    _, forward = min(parity_rows, key=lambda item: item[0])
    k0_candidates = [row.strike for row in ordered if row.strike <= forward]
    if not k0_candidates:
        return None
    k0 = max(k0_candidates)
    strikes = [row.strike for row in ordered]
    variance_sum = 0.0
    used = 0
    for idx, row in enumerate(ordered):
        if len(strikes) == 1:
            continue
        if idx == 0:
            delta_k = strikes[1] - strikes[0]
        elif idx == len(strikes) - 1:
            delta_k = strikes[-1] - strikes[-2]
        else:
            delta_k = (strikes[idx + 1] - strikes[idx - 1]) / 2.0
        if row.strike < k0:
            q = row.put_mid
        elif row.strike > k0:
            q = row.call_mid
        else:
            mids = [v for v in (row.call_mid, row.put_mid) if v is not None and v > 0]
            q = float(np.nanmean(mids)) if mids else None
        if q is None or q <= 0 or delta_k <= 0:
            continue
        variance_sum += (delta_k / (row.strike * row.strike)) * math.exp(r * t) * q
        used += 1
    if used < 2:
        return None
    variance = (2.0 / t) * variance_sum - (1.0 / t) * ((forward / k0) - 1.0) ** 2
    return (t * 365.0, max(variance, 0.0))


def _build_vix_style_variance_30(
    rows: list[InstitutionalOptionStrikeRow], r: float, as_of: str | None
) -> float | None:
    points: list[tuple[float, float]] = []
    for items in _by_expiry(rows).values():
        point = _single_expiry_vix_style_variance(items, r, as_of)
        if point is not None:
            points.append(point)
    return _interp_by_dte(points, 30.0)


def _build_constant_maturity_iv_surface(
    rows: list[InstitutionalOptionStrikeRow], as_of: str | None
) -> dict[str, dict[str, float | None]]:
    tenors = {"30d": 30.0, "60d": 60.0, "90d": 90.0, "180d": 180.0}
    by_label: dict[str, list[tuple[float, float]]] = {
        "put_25d_iv": [],
        "atm_iv": [],
        "call_25d_iv": [],
        "risk_reversal_25d": [],
    }
    for exp, items in _by_expiry(rows).items():
        days = _dte_years(exp, as_of) * 365.0
        call_25 = _nearest_by_delta(items, "call", 0.25)
        put_25 = _nearest_by_delta(items, "put", 0.25)
        call_50 = _nearest_by_delta(items, "call", 0.50)
        put_50 = _nearest_by_delta(items, "put", 0.50)
        call_25_iv = call_25.call_iv if call_25 else None
        put_25_iv = put_25.put_iv if put_25 else None
        atm_ivs = [
            v
            for v in (
                call_50.call_iv if call_50 else None,
                put_50.put_iv if put_50 else None,
            )
            if v is not None and v > 0
        ]
        if call_25_iv is not None and call_25_iv > 0:
            by_label["call_25d_iv"].append((days, call_25_iv))
        if put_25_iv is not None and put_25_iv > 0:
            by_label["put_25d_iv"].append((days, put_25_iv))
        if atm_ivs:
            by_label["atm_iv"].append((days, float(np.nanmean(atm_ivs))))
        if call_25_iv is not None and put_25_iv is not None:
            by_label["risk_reversal_25d"].append((days, call_25_iv - put_25_iv))
    return {
        tenor: {
            label: _round(_interp_by_dte(points, days), 6) for label, points in by_label.items()
        }
        for tenor, days in tenors.items()
    }


def _build_implied_borrow_30(
    rows: list[InstitutionalOptionStrikeRow], spot: float, r: float, as_of: str | None
) -> float | None:
    points: list[tuple[float, float]] = []
    if spot <= 0:
        return None
    for exp, items in _by_expiry(rows).items():
        t = _dte_years(exp, as_of)
        exp_points: list[tuple[float, float]] = []
        for row in items:
            if row.call_mid is None or row.put_mid is None:
                continue
            forward = row.strike + math.exp(r * t) * (row.call_mid - row.put_mid)
            if forward <= 0:
                continue
            borrow = r - (math.log(forward / spot) / t)
            weight = max((row.call_oi or 0.0) + (row.put_oi or 0.0), 1.0) / (
                1.0 + abs((row.strike - spot) / spot)
            )
            exp_points.append((borrow, weight))
        if exp_points:
            total_weight = sum(weight for _, weight in exp_points)
            points.append(
                (t * 365.0, sum(value * weight for value, weight in exp_points) / total_weight)
            )
    return _interp_by_dte(points, 30.0)


def _dte_bucket(days: float) -> str:
    if days <= 1.0:
        return "dte_0_1"
    if days <= 5.0:
        return "dte_2_5"
    if days <= 30.0:
        return "dte_6_30"
    if days <= 90.0:
        return "dte_31_90"
    if days <= 180.0:
        return "dte_91_180"
    if days <= 360.0:
        return "dte_181_360"
    return "dte_360_plus"


def _block_bucket(volume: float) -> str:
    if volume <= 1.0:
        return "size_1"
    if volume <= 10.0:
        return "size_2_10"
    if volume <= 100.0:
        return "size_11_100"
    if volume <= 500.0:
        return "size_101_500"
    if volume <= 1000.0:
        return "size_501_1000"
    return "size_1001_plus"


def _build_rnd_moments(
    rows: list[InstitutionalOptionStrikeRow], spot: float, r: float, as_of: str | None
) -> dict[str, float | None]:
    valid_expiries = sorted(
        _by_expiry(rows).items(),
        key=lambda item: abs((_dte_years(item[0], as_of) * 365.0) - 30.0),
    )
    for exp, items in valid_expiries:
        calls = sorted(
            [
                (row.strike, row.call_mid)
                for row in items
                if row.call_mid is not None and row.call_mid > 0
            ],
            key=lambda item: item[0],
        )
        if len(calls) < 5:
            continue
        strikes = np.array([k for k, _ in calls], dtype=np.float64)
        prices = np.array([p for _, p in calls], dtype=np.float64)
        grid = np.linspace(float(strikes.min()), float(strikes.max()), 80)
        interp_prices = np.interp(grid, strikes, prices)
        dk = float(grid[1] - grid[0])
        t = _dte_years(exp, as_of)
        density = np.exp(r * t) * np.gradient(np.gradient(interp_prices, dk), dk)
        density = np.clip(density, 0.0, None)
        mass = float(np.trapezoid(density, grid))
        if mass <= 1e-12:
            continue
        density = density / mass
        mean = float(np.trapezoid(grid * density, grid))
        var = float(np.trapezoid((grid - mean) ** 2 * density, grid))
        std = math.sqrt(max(var, 0.0))
        if std <= 1e-12:
            continue
        skew = float(np.trapezoid(((grid - mean) / std) ** 3 * density, grid))
        kurt = float(np.trapezoid(((grid - mean) / std) ** 4 * density, grid) - 3.0)
        cdf = np.cumsum(density) * dk
        modal = float(grid[int(np.argmax(density))])
        downside = float(np.interp(spot * 0.95, grid, np.clip(cdf, 0.0, 1.0)))
        upside = 1.0 - float(np.interp(spot * 1.05, grid, np.clip(cdf, 0.0, 1.0)))
        return {
            "q_mean": _round(mean, 6),
            "q_std": _round(std, 6),
            "q_skewness": _round(skew, 6),
            "q_kurtosis": _round(kurt, 6),
            "modal_price": _round(modal, 6),
            "downside_5pct_probability": _round(downside, 6),
            "upside_5pct_probability": _round(upside, 6),
        }
    return {
        "q_mean": None,
        "q_std": None,
        "q_skewness": None,
        "q_kurtosis": None,
        "modal_price": None,
        "downside_5pct_probability": None,
        "upside_5pct_probability": None,
    }


def _quality(
    status: MetricQualityStatus,
    source: str,
    formula_version: str,
    required_fields: list[str],
    missing_fields: list[str] | None = None,
) -> MetricQuality:
    return MetricQuality(
        status=status,
        source=source,
        formula_version=formula_version,
        required_fields=required_fields,
        missing_fields=missing_fields or [],
    )


def _confidence_score(metric_quality: dict[str, MetricQuality], data_quality_score: float) -> float:
    weights = {"real": 1.0, "derived": 0.76, "proxy": 0.45, "unavailable": 0.0}
    if not metric_quality:
        return round(max(0.0, min(data_quality_score, 100.0)), 2)
    quality_component = (
        float(np.nanmean([weights[item.status] for item in metric_quality.values()])) * 72.0
    )
    data_component = max(0.0, min(data_quality_score, 100.0)) * 0.28
    return round(max(0.0, min(100.0, quality_component + data_component)), 2)


def _benchmark_validation() -> dict[str, dict[str, str | float | None]]:
    return {
        "vix_style_vol_30d": {
            "benchmark": "Cboe VIX-style variance",
            "status": "formula_aligned",
            "tolerance_note": "Validate against listed Cboe index/vendor value when available.",
        },
        "normalized_25d_skew_30": {
            "benchmark": "OptionMetrics/IvyDB 30D 25-delta skew",
            "status": "definition_aligned",
            "tolerance_note": "Compare after provider delta and IV convention normalization.",
        },
        "trade_weighted_quoted_spread_pct": {
            "benchmark": "OPRA/NBBO trade-weighted quoted spread",
            "status": "snapshot_proxy_until_trade_nbbo",
            "tolerance_note": "Use at-trade NBBO for production-grade validation.",
        },
    }


def _capacity_value(
    row: InstitutionalOptionStrikeRow, side: Literal["call", "put"], key: str
) -> float:
    return _finite(getattr(row, f"{side}_{key}_volume")) or 0.0


def _exchange_volumes(
    row: InstitutionalOptionStrikeRow, side: Literal["call", "put"]
) -> dict[str, float]:
    raw = getattr(row, f"{side}_exchange_volumes") or {}
    if not isinstance(raw, dict):
        return {}
    out: dict[str, float] = {}
    for exchange, volume in raw.items():
        v = _finite(volume) or 0.0
        if v > 0:
            out[str(exchange).upper()] = out.get(str(exchange).upper(), 0.0) + v
    return out


def _build_institutional_standard_metrics(
    rows: list[InstitutionalOptionStrikeRow], spot: float, r: float, as_of: str | None
) -> InstitutionalStandardMetrics:
    dte_distribution = {
        "dte_0_1": 0.0,
        "dte_2_5": 0.0,
        "dte_6_30": 0.0,
        "dte_31_90": 0.0,
        "dte_91_180": 0.0,
        "dte_181_360": 0.0,
        "dte_360_plus": 0.0,
    }
    block_distribution = {
        "size_1": 0.0,
        "size_2_10": 0.0,
        "size_11_100": 0.0,
        "size_101_500": 0.0,
        "size_501_1000": 0.0,
        "size_1001_plus": 0.0,
    }
    vega_notional = 0.0
    spread_weighted = 0.0
    spread_weight = 0.0
    effective_weighted = 0.0
    effective_weight = 0.0
    true_aggr_premium = 0.0
    true_aggr_buy_volume = 0.0
    true_aggr_sell_volume = 0.0
    true_aggr_observed = 0
    capacity = {"customer": 0.0, "firm": 0.0, "market_maker": 0.0}
    exchange_volumes: dict[str, float] = {}
    for row in rows:
        days = _dte_years(row.expiration, as_of) * 365.0
        for side in ("call", "put"):
            volume = _finite(getattr(row, f"{side}_volume")) or 0.0
            if volume <= 0:
                continue
            mid = _finite(getattr(row, f"{side}_mid"))
            last = _finite(getattr(row, f"{side}_last"))
            mark = _finite(getattr(row, f"{side}_mark")) or mid or last or 0.0
            dte_distribution[_dte_bucket(days)] += volume
            block_distribution[_block_bucket(volume)] += volume
            vega = _finite(getattr(row, f"{side}_vega")) or 0.0
            vega_notional += abs(vega) * volume * CONTRACT_SIZE * 0.01
            spread_pct = _finite(getattr(row, f"{side}_spread_pct"))
            if spread_pct is not None:
                spread_weighted += abs(spread_pct) * 100.0 * volume
                spread_weight += volume
            if last is not None and mid is not None and last > 0:
                effective_weighted += 2.0 * abs(last - mid) / last * 100.0 * volume
                effective_weight += volume
            aggressor = str(getattr(row, f"{side}_aggressor_side") or "").strip().lower()
            if aggressor in {"buy", "buyer", "bought", "ask"}:
                true_aggr_premium += mark * volume * CONTRACT_SIZE
                true_aggr_buy_volume += volume
                true_aggr_observed += 1
            elif aggressor in {"sell", "seller", "sold", "bid"}:
                true_aggr_premium -= mark * volume * CONTRACT_SIZE
                true_aggr_sell_volume += volume
                true_aggr_observed += 1
            capacity["customer"] += _capacity_value(
                row, "call" if side == "call" else "put", "customer"
            )
            capacity["firm"] += _capacity_value(row, "call" if side == "call" else "put", "firm")
            capacity["market_maker"] += _capacity_value(
                row, "call" if side == "call" else "put", "market_maker"
            )
            for exchange, exchange_volume in _exchange_volumes(
                row, "call" if side == "call" else "put"
            ).items():
                exchange_volumes[exchange] = exchange_volumes.get(exchange, 0.0) + exchange_volume

    variance = _build_vix_style_variance_30(rows, r, as_of)
    vol = math.sqrt(variance) if variance is not None and variance >= 0 else None
    exchange_total = sum(exchange_volumes.values())
    exchange_hhi = (
        sum((volume / exchange_total) ** 2 for volume in exchange_volumes.values())
        if exchange_total > 0
        else None
    )
    normalized_skew = _build_normalized_25d_skew_30(rows, as_of)
    implied_borrow = _build_implied_borrow_30(rows, spot, r, as_of)
    rnd_moments = _build_rnd_moments(rows, spot, r, as_of)
    metric_quality = {
        "normalized_25d_skew_30": _quality(
            "derived" if normalized_skew is not None else "unavailable",
            "delta_iv_surface",
            "normalized_25d_skew_30_v1",
            ["call_delta", "put_delta", "call_iv", "put_iv"],
            [] if normalized_skew is not None else ["25d_or_atm_surface"],
        ),
        "vix_style_vol_30d": _quality(
            "derived" if variance is not None else "unavailable",
            "chain_nbbo_mid",
            "vix_style_variance_30d_v1",
            ["call_mid", "put_mid", "strike", "expiration"],
            [] if variance is not None else ["sufficient_otm_strip"],
        ),
        "true_aggressor_net_premium": _quality(
            "real" if true_aggr_observed else "unavailable",
            "opra_trade_aggressor" if true_aggr_observed else "missing_trade_aggressor",
            "signed_aggressor_premium_v1",
            ["aggressor_side", "volume", "mark"],
            [] if true_aggr_observed else ["aggressor_side"],
        ),
        "risk_neutral_density_moments": _quality(
            "derived" if rnd_moments.get("q_std") is not None else "unavailable",
            "breeden_litzenberger_calls",
            "rnd_moments_v1",
            ["call_mid", "strike"],
            [] if rnd_moments.get("q_std") is not None else ["five_valid_call_prices"],
        ),
        "participant_capacity_flow": _quality(
            "real" if any(capacity.values()) else "unavailable",
            "participant_capacity",
            "capacity_volume_sum_v1",
            ["customer_volume", "firm_volume", "market_maker_volume"],
            [] if any(capacity.values()) else ["capacity_fields"],
        ),
        "exchange_volume_hhi_real": _quality(
            "real" if exchange_hhi is not None else "unavailable",
            "exchange_volume_map",
            "exchange_hhi_v1",
            ["exchange_volumes"],
            [] if exchange_hhi is not None else ["exchange_volumes"],
        ),
        "effective_spread_pct": _quality(
            "proxy" if effective_weight > 0 else "unavailable",
            "last_vs_nbbo_mid",
            "effective_spread_proxy_v1",
            ["last", "mid", "volume"],
            [] if effective_weight > 0 else ["last_or_mid"],
        ),
    }
    return InstitutionalStandardMetrics(
        normalized_25d_skew_30=_round(normalized_skew, 6),
        vix_style_variance_30d=_round(variance, 8),
        vix_style_vol_30d=_round(vol, 6),
        vega_notional_traded=round(vega_notional, 2),
        trade_weighted_quoted_spread_pct=_round(_safe_div(spread_weighted, spread_weight), 4),
        dte_volume_distribution={k: round(v, 2) for k, v in dte_distribution.items()},
        block_size_distribution={k: round(v, 2) for k, v in block_distribution.items()},
        constant_maturity_iv_surface=_build_constant_maturity_iv_surface(rows, as_of),
        implied_borrow_30d=_round(implied_borrow, 6),
        true_aggressor_net_premium=round(true_aggr_premium, 2) if true_aggr_observed else None,
        true_aggressor_flow_imbalance=_round(
            _safe_div(
                true_aggr_buy_volume - true_aggr_sell_volume,
                true_aggr_buy_volume + true_aggr_sell_volume,
            ),
            6,
        ),
        effective_spread_pct=_round(_safe_div(effective_weighted, effective_weight), 4),
        participant_capacity_flow={k: round(v, 2) for k, v in capacity.items()},
        exchange_volume_hhi_real=_round(exchange_hhi, 6),
        risk_neutral_density_moments=rnd_moments,
        metric_quality=metric_quality,
        benchmark_validation=_benchmark_validation(),
        institutional_confidence_score=_confidence_score(
            metric_quality, _quality_score(rows, _quality_warnings(rows))
        ),
    )


def _coverage_pct(rows: list[InstitutionalOptionStrikeRow], predicate: Any) -> float:
    total = max(len(rows) * 2, 1)
    observed = 0
    for row in rows:
        for side in ("call", "put"):
            if predicate(row, side):
                observed += 1
    return round(observed / total * 100.0, 4)


def _build_trade_level_readiness(
    rows: list[InstitutionalOptionStrikeRow],
) -> TradeLevelReadinessMetrics:
    aggressor = _coverage_pct(
        rows, lambda row, side: bool(str(getattr(row, f"{side}_aggressor_side") or "").strip())
    )
    nbbo = _coverage_pct(
        rows,
        lambda row, side: (
            _finite(getattr(row, f"{side}_nbbo_at_trade_bid")) is not None
            and _finite(getattr(row, f"{side}_nbbo_at_trade_ask")) is not None
        )
        or (
            _finite(getattr(row, f"{side}_bid")) is not None
            and _finite(getattr(row, f"{side}_ask")) is not None
        ),
    )
    capacity = _coverage_pct(
        rows,
        lambda row, side: any(
            (_finite(getattr(row, f"{side}_{key}_volume")) or 0.0) > 0
            for key in ("customer", "firm", "market_maker")
        ),
    )
    exchange_volume = _coverage_pct(
        rows, lambda row, side: bool(_exchange_volumes(row, "call" if side == "call" else "put"))
    )
    missing = []
    if aggressor < 90:
        missing.append("aggressor_side")
    if nbbo < 90:
        missing.append("bid_ask_nbbo")
    if capacity < 90:
        missing.append("participant_capacity")
    if exchange_volume < 90:
        missing.append("exchange_volumes")
    status: Literal["trade_level_ready", "partial_trade_level", "snapshot_only"]
    if aggressor >= 80 and nbbo >= 80 and (capacity >= 50 or exchange_volume >= 50):
        status = "trade_level_ready"
    elif aggressor > 0 or capacity > 0 or exchange_volume > 0:
        status = "partial_trade_level"
    else:
        status = "snapshot_only"
    return TradeLevelReadinessMetrics(
        status=status,
        aggressor_coverage_pct=aggressor,
        nbbo_coverage_pct=nbbo,
        capacity_coverage_pct=capacity,
        exchange_volume_coverage_pct=exchange_volume,
        missing_fields=missing,
    )


def _build_surface_diagnostics(
    rows: list[InstitutionalOptionStrikeRow], as_of: str | None
) -> SurfaceDiagnosticsMetrics:
    by_exp = _by_expiry(rows)
    points_used = 0
    vertical = 0
    butterfly = 0
    calendar = 0
    warnings: list[str] = []
    atm_by_exp: list[tuple[float, float]] = []
    for exp, items in by_exp.items():
        ordered = sorted(items, key=lambda row: row.strike)
        call_prices = [
            (row.strike, row.call_mid)
            for row in ordered
            if row.call_mid is not None and row.call_mid > 0
        ]
        put_prices = [
            (row.strike, row.put_mid)
            for row in ordered
            if row.put_mid is not None and row.put_mid > 0
        ]
        points_used += len(
            [row for row in ordered if (row.call_iv or 0.0) > 0 or (row.put_iv or 0.0) > 0]
        )
        for series, direction in ((call_prices, -1), (put_prices, 1)):
            for (_, prev), (_, cur) in zip(series, series[1:], strict=False):
                if direction < 0 and cur > prev + 1e-6:
                    vertical += 1
                if direction > 0 and cur + 1e-6 < prev:
                    vertical += 1
            if len(series) >= 3:
                for (_, left), (_, mid), (_, right) in zip(
                    series, series[1:], series[2:], strict=False
                ):
                    if left - 2.0 * mid + right < -1e-6:
                        butterfly += 1
        atm = _nearest_by_delta(ordered, "call", 0.50)
        if atm and atm.call_iv:
            atm_by_exp.append((_dte_years(exp, as_of) * 365.0, atm.call_iv))
    for (_, short_iv), (_, long_iv) in zip(
        sorted(atm_by_exp), sorted(atm_by_exp)[1:], strict=False
    ):
        if long_iv + 1e-6 < short_iv * 0.65:
            calendar += 1
    if points_used < 5:
        warnings.append("insufficient_surface_points_for_svi")
    if vertical:
        warnings.append("vertical_arbitrage_detected")
    if butterfly:
        warnings.append("butterfly_arbitrage_detected")
    if calendar:
        warnings.append("calendar_arbitrage_detected")
    score = max(
        0.0,
        100.0
        - vertical * 20.0
        - butterfly * 25.0
        - calendar * 20.0
        - (0.0 if points_used >= 5 else 30.0),
    )
    return SurfaceDiagnosticsMetrics(
        svi_ready=points_used >= 5 and vertical == 0 and butterfly == 0,
        points_used=points_used,
        expiries_used=len(by_exp),
        vertical_arbitrage_violations=vertical,
        butterfly_arbitrage_violations=butterfly,
        calendar_arbitrage_violations=calendar,
        no_arbitrage_score=round(min(score, 100.0), 2),
        warnings=warnings,
    )


def _infer_underlying_type(ticker: str) -> str:
    sym = ticker.upper().strip()
    if sym in {"SPY", "QQQ", "IWM", "DIA", "TLT", "GLD", "SLV", "USO"}:
        return "ETF"
    if sym in {"SPX", "NDX", "RUT", "VIX"}:
        return "INDEX"
    return "EQUITY"


def _build_product_conventions(
    ticker: str, rows: list[InstitutionalOptionStrikeRow]
) -> ProductConventionMetrics:
    styles = [
        str(getattr(row, f"{side}_exercise_style") or "").lower()
        for row in rows
        for side in ("call", "put")
        if str(getattr(row, f"{side}_exercise_style") or "").strip()
    ]
    shares = [
        _finite(getattr(row, f"{side}_shares_per_contract"))
        for row in rows
        for side in ("call", "put")
    ]
    shares_clean = [s for s in shares if s is not None]
    adjusted = sum(1 for s in shares_clean if abs(s - CONTRACT_SIZE) > 1e-9)
    underlying_type = _infer_underlying_type(ticker)
    settlement = "AM/PM" if ticker.upper() in {"SPX", "NDX", "RUT"} else "PM"
    multiplier_mode = "standard_100" if shares_clean and adjusted == 0 else "adjusted_or_unknown"
    warnings = []
    if adjusted:
        warnings.append("adjusted_contract_multiplier")
    if not shares_clean:
        warnings.append("missing_multiplier_metadata")
    style = (
        max(set(styles), key=styles.count)
        if styles
        else ("european" if underlying_type == "INDEX" else "american")
    )
    return ProductConventionMetrics(
        underlying_type=underlying_type,
        exercise_style=style,
        settlement_type=settlement,
        multiplier_mode=multiplier_mode,
        adjusted_contracts=adjusted,
        non_standard_deliverables=adjusted,
        convention_summary=f"type={underlying_type}; exercise={style}; settlement={settlement}; multiplier={multiplier_mode}",
        warnings=warnings,
    )


def _dealer_customer_sign(row: InstitutionalOptionStrikeRow, side: Literal["call", "put"]) -> float:
    aggr = str(getattr(row, f"{side}_aggressor_side") or "").strip().lower()
    if aggr in {"buy", "buyer", "bought", "ask"}:
        return 1.0
    if aggr in {"sell", "seller", "sold", "bid"}:
        return -1.0
    return 0.0


def _build_dealer_positioning_v2(
    rows: list[InstitutionalOptionStrikeRow], spot: float
) -> DealerPositioningV2Metrics:
    cust_call_premium = 0.0
    cust_put_premium = 0.0
    dealer_gamma = 0.0
    dealer_delta = 0.0
    dealer_vanna = 0.0
    dealer_charm = 0.0
    observed = 0
    for row in rows:
        for side in ("call", "put"):
            sign = _dealer_customer_sign(row, "call" if side == "call" else "put")
            if sign == 0:
                continue
            observed += 1
            volume = _finite(getattr(row, f"{side}_volume")) or 0.0
            mark = (
                _finite(getattr(row, f"{side}_mark"))
                or _finite(getattr(row, f"{side}_mid"))
                or _finite(getattr(row, f"{side}_last"))
                or 0.0
            )
            premium = sign * mark * volume * CONTRACT_SIZE
            if side == "call":
                cust_call_premium += premium
            else:
                cust_put_premium += premium
            # Dealer is the counterparty to customer flow.
            dealer_sign = -sign
            gamma = (_finite(getattr(row, f"{side}_gamma")) or 0.0) * volume * CONTRACT_SIZE
            delta = (_finite(getattr(row, f"{side}_delta")) or 0.0) * volume * CONTRACT_SIZE * spot
            vanna = (
                (_finite(getattr(row, f"{side}_vanna")) or 0.0)
                * volume
                * CONTRACT_SIZE
                * spot
                * 0.01
            )
            charm = (
                (_finite(getattr(row, f"{side}_charm")) or 0.0)
                * volume
                * CONTRACT_SIZE
                * spot
                / (365.0 * 24.0)
            )
            dealer_gamma += dealer_sign * gamma
            dealer_delta += dealer_sign * delta
            dealer_vanna += dealer_sign * vanna
            dealer_charm += dealer_sign * charm
    if observed == 0:
        regime: Literal["SHORT_GAMMA", "LONG_GAMMA", "MIXED_GAMMA", "UNKNOWN"] = "UNKNOWN"
    elif abs(dealer_gamma) < 1e-9:
        regime = "MIXED_GAMMA"
    elif dealer_gamma < 0:
        regime = "SHORT_GAMMA"
    else:
        regime = "LONG_GAMMA"
    hedge_notional_1pct = -dealer_gamma * spot * 0.01 * spot
    return DealerPositioningV2Metrics(
        estimated_dealer_gamma_regime=regime,
        customer_net_premium=round(cust_call_premium + cust_put_premium, 2),
        customer_call_premium=round(cust_call_premium, 2),
        customer_put_premium=round(cust_put_premium, 2),
        dealer_estimated_net_gamma=_round(dealer_gamma, 6) or 0.0,
        dealer_estimated_net_delta=_round(dealer_delta, 2) or 0.0,
        dealer_delta_hedge_notional_1pct=_round(hedge_notional_1pct, 2) or 0.0,
        dealer_vanna_hedge_notional_1vol=_round(-dealer_vanna, 2) or 0.0,
        dealer_charm_hedge_notional_1h=_round(-dealer_charm, 2) or 0.0,
        confidence=round(min(100.0, observed / max(len(rows) * 2, 1) * 100.0), 2),
        assumptions=[
            "customer initiated flow sign inferred from aggressor_side",
            "dealer exposure is modeled as inverse of customer initiated flow",
            "volume used when open/close tags are unavailable",
        ],
    )


def _asof_market_minutes(as_of: str | None) -> tuple[int | None, str]:
    if not as_of:
        return None, "unknown"
    try:
        dt = datetime.fromisoformat(as_of.replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=UTC)
        local = dt.astimezone(ZoneInfo("America/New_York"))
    except Exception:
        return None, "unknown"
    minutes = local.hour * 60 + local.minute
    if minutes < 570:
        phase = "premarket"
    elif minutes < 660:
        phase = "open"
    elif minutes < 780:
        phase = "midday"
    elif minutes < 900:
        phase = "afternoon"
    elif minutes <= 960:
        phase = "power_hour"
    else:
        phase = "post_close"
    return minutes, phase


def _build_intraday_state_machine(
    rows: list[InstitutionalOptionStrikeRow],
    expiry_rows: list[ExpiryAnalytics],
    metrics: ChainInstitutionalMetrics,
    spot: float,
    as_of: str | None,
    dealer: DealerPositioningV2Metrics,
) -> IntradayStateMachineMetrics:
    minutes, phase = _asof_market_minutes(as_of)
    total_volume = max(sum((row.call_volume or 0.0) + (row.put_volume or 0.0) for row in rows), 1.0)
    near_volume = sum(
        (row.call_volume or 0.0) + (row.put_volume or 0.0)
        for row in rows
        if abs(row.strike - spot) / spot <= 0.01
    )
    same_day = any((expiry.dte_days or 999.0) <= 0.75 for expiry in expiry_rows)
    pin_score = min(100.0, near_volume / total_volume * 100.0 + (25.0 if same_day else 0.0))
    neg_gamma_score = min(
        100.0, abs(metrics.total_gex) / max(metrics.total_notional_volume, 1.0) * 10000.0
    )
    if (
        metrics.gamma_regime == "NEGATIVE_GAMMA"
        or dealer.estimated_dealer_gamma_regime == "SHORT_GAMMA"
    ):
        neg_gamma_score = max(neg_gamma_score, 65.0)
    charm_score = min(
        100.0,
        abs(dealer.dealer_charm_hedge_notional_1h)
        / max(abs(dealer.dealer_delta_hedge_notional_1pct), 1.0)
        * 100.0,
    )
    if phase == "power_hour":
        charm_score = max(charm_score, 55.0)
    opening_score = 70.0 if phase == "open" else 0.0
    lunch_score = 65.0 if phase == "midday" else 0.0
    state_scores = {
        "OPENING_FLOW": round(opening_score, 2),
        "LUNCH_DECAY": round(lunch_score, 2),
        "POWER_HOUR_CHARM": round(charm_score, 2),
        "EXPIRY_PIN": round(pin_score, 2),
        "NEG_GAMMA_BREAKOUT": round(neg_gamma_score, 2),
    }
    priority: list[IntradayState] = [
        "NEG_GAMMA_BREAKOUT",
        "EXPIRY_PIN",
        "POWER_HOUR_CHARM",
        "OPENING_FLOW",
        "LUNCH_DECAY",
    ]
    current: IntradayState = "CLOSED_OR_UNKNOWN"
    for state in priority:
        if state_scores[state] >= 50.0:
            current = state
            break
    triggers = [state for state, score in state_scores.items() if score >= 50.0]
    if minutes is None:
        triggers.append("missing_or_unparseable_as_of")
    return IntradayStateMachineMetrics(
        current_state=current,
        state_scores=state_scores,
        session_phase=phase,
        path_dependency_note="state is inferred from current snapshot; intraday history improves transition confidence",
        triggers=triggers,
    )


def _build_portfolio_risk_overlay(
    metrics: ChainInstitutionalMetrics,
    standard: InstitutionalStandardMetrics,
    liquidity: LiquidityStressMetrics,
    dealer: DealerPositioningV2Metrics,
    state: IntradayStateMachineMetrics,
) -> PortfolioRiskOverlayMetrics:
    expected_hedge = dealer.dealer_delta_hedge_notional_1pct
    rnd = standard.risk_neutral_density_moments or {}
    tail_hedge = max(
        0.0, float(rnd.get("downside_5pct_probability") or 0.0) * metrics.total_notional_volume
    )
    iv_crush = 0.0
    if standard.vix_style_vol_30d is not None:
        iv_crush = max(0.0, min(100.0, (standard.vix_style_vol_30d - 0.18) * 250.0))
    liquidity_haircut = max(
        0.0, min(75.0, liquidity.stress_score * 0.5 + (standard.effective_spread_pct or 0.0) * 2.0)
    )
    max_slippage = max(standard.effective_spread_pct or 0.0, liquidity.avg_spread_pct or 0.0, 0.0)
    risk_score = min(
        100.0,
        liquidity_haircut
        + iv_crush * 0.25
        + (25.0 if state.current_state == "NEG_GAMMA_BREAKOUT" else 0.0),
    )
    if risk_score >= 75.0:
        eligibility: TradeEligibility = "blocked"
    elif risk_score >= 45.0:
        eligibility = "restricted"
    else:
        eligibility = "eligible"
    size_multiplier = max(0.0, min(1.0, 1.0 - risk_score / 100.0))
    guardrails = [
        "cap order size by liquidity haircut",
        "avoid market orders when max_slippage_pct exceeds desk threshold",
        "reduce size during negative gamma breakout or expiry pin states",
    ]
    return PortfolioRiskOverlayMetrics(
        expected_hedge_flow_notional=round(expected_hedge, 2),
        tail_hedge_demand=round(tail_hedge, 2),
        iv_crush_risk=round(iv_crush, 2),
        liquidity_haircut_pct=round(liquidity_haircut, 2),
        max_slippage_pct=round(max_slippage, 4),
        trade_eligibility=eligibility,
        position_size_multiplier=round(size_multiplier, 4),
        guardrails=guardrails,
    )


def _staleness_from_latency(latency_ms: float | None) -> str:
    if latency_ms is None:
        return "unknown"
    if latency_ms <= 1_000:
        return "live"
    if latency_ms <= 60_000:
        return "recent"
    if latency_ms <= 900_000:
        return "stale"
    return "expired"


def _build_data_lineage(
    provider: str | None,
    as_of: str | None,
    quality: InstitutionalChainQuality,
    readiness: TradeLevelReadinessMetrics,
    standard: InstitutionalStandardMetrics,
    dealer: DealerPositioningV2Metrics,
    surface: SurfaceDiagnosticsMetrics,
) -> DataLineageMetrics:
    now = datetime.now(tz=UTC)
    latency_ms: float | None = None
    if as_of:
        try:
            dt = datetime.fromisoformat(as_of.replace("Z", "+00:00"))
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=UTC)
            latency_ms = max(0.0, (now - dt.astimezone(UTC)).total_seconds() * 1000.0)
        except Exception:
            latency_ms = None
    base_provider = provider or quality.provider
    metric_map = {
        "institutional_standard": MetricLineage(
            provider=base_provider,
            raw_fields=["bid", "ask", "last", "volume", "open_interest", "iv", "greeks"],
            timestamp=as_of,
            latency_ms=_round(latency_ms, 2),
            staleness=_staleness_from_latency(latency_ms),
            formula_version="institutional_standard_v1",
            fallback_used=any(
                q.status in {"proxy", "unavailable"} for q in standard.metric_quality.values()
            ),
            confidence=standard.institutional_confidence_score,
        ),
        "dealer_positioning_v2": MetricLineage(
            provider=base_provider,
            raw_fields=[
                "aggressor_side",
                "customer_volume",
                "volume",
                "mark",
                "gamma",
                "delta",
                "vanna",
                "charm",
            ],
            timestamp=as_of,
            latency_ms=_round(latency_ms, 2),
            staleness=_staleness_from_latency(latency_ms),
            formula_version="dealer_inverse_customer_flow_v1",
            fallback_used=dealer.estimated_dealer_gamma_regime == "UNKNOWN",
            confidence=dealer.confidence,
        ),
        "surface_diagnostics": MetricLineage(
            provider=base_provider,
            raw_fields=["strike", "expiration", "bid", "ask", "mid", "iv"],
            timestamp=as_of,
            latency_ms=_round(latency_ms, 2),
            staleness=_staleness_from_latency(latency_ms),
            formula_version="surface_no_arbitrage_qa_v1",
            fallback_used=not surface.svi_ready,
            confidence=surface.no_arbitrage_score,
        ),
        "trade_level_readiness": MetricLineage(
            provider=base_provider,
            raw_fields=["aggressor_side", "bid", "ask", "participant_capacity", "exchange_volumes"],
            timestamp=as_of,
            latency_ms=_round(latency_ms, 2),
            staleness=_staleness_from_latency(latency_ms),
            formula_version="opra_readiness_v1",
            fallback_used=readiness.status != "trade_level_ready",
            confidence=(
                readiness.aggressor_coverage_pct
                + readiness.nbbo_coverage_pct
                + readiness.capacity_coverage_pct
                + readiness.exchange_volume_coverage_pct
            )
            / 4.0,
        ),
    }
    field_map: dict[str, MetricLineage] = {
        "total_gex": MetricLineage(
            provider=base_provider,
            raw_fields=["gamma", "open_interest", "spot", "contract_multiplier"],
            timestamp=as_of,
            latency_ms=_round(latency_ms, 2),
            staleness=_staleness_from_latency(latency_ms),
            formula_version="spotgamma_v1",
            fallback_used=False,
            confidence=quality.data_quality_score,
        ),
        "total_dex": MetricLineage(
            provider=base_provider,
            raw_fields=["delta", "open_interest", "spot", "contract_multiplier"],
            timestamp=as_of,
            latency_ms=_round(latency_ms, 2),
            staleness=_staleness_from_latency(latency_ms),
            formula_version="delta_oi_100_spot_v1",
            fallback_used=False,
            confidence=quality.data_quality_score,
        ),
    }
    for name, item in standard.metric_quality.items():
        field_map[name] = MetricLineage(
            provider=base_provider,
            raw_fields=item.required_fields,
            timestamp=as_of,
            latency_ms=_round(latency_ms, 2),
            staleness=_staleness_from_latency(latency_ms),
            formula_version=item.formula_version,
            fallback_used=item.status in {"proxy", "unavailable"},
            confidence={"real": 100.0, "derived": 76.0, "proxy": 45.0, "unavailable": 0.0}[
                item.status
            ],
        )
    return DataLineageMetrics(
        metrics=metric_map,
        metric_fields=field_map,
        generated_at=now.isoformat(),
        coverage_pct=round(
            sum(m.confidence for m in [*metric_map.values(), *field_map.values()])
            / max(len(metric_map) + len(field_map), 1),
            2,
        ),
    )


def _vendor_tolerance(field: str) -> float:
    return {
        "iv": 1.0,
        "open_interest": 0.5,
        "volume": 0.5,
        "greeks": 2.0,
        "walls": 0.25,
        "expiries": 0.0,
    }.get(field, 1.0)


def _build_vendor_reconciliation(
    readiness: TradeLevelReadinessMetrics,
    standard: InstitutionalStandardMetrics,
    metrics: ChainInstitutionalMetrics,
    expiry_rows: list[ExpiryAnalytics],
    vendor_benchmarks: dict[str, dict[str, float]] | None = None,
) -> VendorReconciliationMetrics:
    compared = ["iv", "open_interest", "volume", "greeks", "walls", "expiries"]
    atm_ivs = [
        expiry.atm_iv for expiry in expiry_rows if expiry.atm_iv is not None and expiry.atm_iv > 0
    ]
    internal_values = {
        "iv": float(np.nanmean(atm_ivs)) if atm_ivs else standard.vix_style_vol_30d,
        "open_interest": metrics.total_open_interest,
        "volume": metrics.total_volume,
        "greeks": abs(metrics.total_gex),
        "walls": metrics.vol_trigger_proxy,
        "expiries": float(len(expiry_rows)),
    }
    divergences: list[VendorDivergence] = []
    if vendor_benchmarks:
        for field in compared:
            for vendor, vendor_value in (vendor_benchmarks.get(field) or {}).items():
                internal = _finite(internal_values.get(field))
                vendor_f = _finite(vendor_value)
                if internal is None or vendor_f is None:
                    divergences.append(
                        VendorDivergence(
                            field=field,
                            vendor=vendor,
                            internal_value=internal,
                            vendor_value=vendor_f,
                            status="insufficient_internal_or_vendor_value",
                        )
                    )
                    continue
                divergence = (
                    None
                    if abs(vendor_f) <= 1e-12
                    else abs((internal - vendor_f) / vendor_f) * 100.0
                )
                tolerance = _vendor_tolerance(field)
                status = (
                    "matched_within_tolerance"
                    if divergence is not None and divergence <= tolerance
                    else "divergence_outside_tolerance"
                )
                divergences.append(
                    VendorDivergence(
                        field=field,
                        vendor=vendor,
                        internal_value=_round(internal, 6),
                        vendor_value=_round(vendor_f, 6),
                        divergence_pct=_round(divergence, 6),
                        status=status,
                    )
                )
        return VendorReconciliationMetrics(
            status="ready_for_vendor_compare",
            compared_fields=compared,
            divergences=divergences,
            warnings=[],
        )
    if (
        readiness.exchange_volume_coverage_pct >= 50.0
        and standard.institutional_confidence_score >= 70.0
    ):
        status: VendorReconciliationStatus = "ready_for_vendor_compare"
    elif readiness.nbbo_coverage_pct > 0 or standard.institutional_confidence_score > 0:
        status = "partial_vendor_compare"
    else:
        status = "no_vendor_benchmarks"
    return VendorReconciliationMetrics(
        status=status,
        compared_fields=compared,
        divergences=[
            VendorDivergence(
                field=field,
                vendor="external_benchmark",
                internal_value=_round(_finite(internal_values.get(field)), 6),
                status="benchmark_unavailable",
            )
            for field in compared
        ],
        warnings=["external_vendor_benchmarks_not_attached"],
    )


def _build_scheduler_plan() -> SchedulerPlanMetrics:
    return SchedulerPlanMetrics(
        jobs=[
            SchedulerJobSpec(
                name="premarket_snapshot",
                schedule="08:45 America/New_York trading_days",
                purpose="warm chain, surface and prior OI context",
            ),
            SchedulerJobSpec(
                name="opening_snapshot",
                schedule="09:30 America/New_York trading_days",
                purpose="capture opening flow and initial dealer state",
            ),
            SchedulerJobSpec(
                name="intraday_refresh",
                schedule="every 5 minutes 09:35-15:55 America/New_York trading_days",
                purpose="persist institutional snapshots independent of UI requests",
            ),
            SchedulerJobSpec(
                name="close_snapshot",
                schedule="16:00 America/New_York trading_days",
                purpose="lock closing chain state and risk overlay",
            ),
            SchedulerJobSpec(
                name="post_close_oi_update",
                schedule="18:30 America/New_York trading_days",
                purpose="refresh OCC/open-interest updates when vendor publishes",
            ),
            SchedulerJobSpec(
                name="expiry_rollover",
                schedule="16:15 America/New_York expiration_days",
                purpose="roll active expiry scope and pin-risk history",
            ),
        ],
    )


def _build_advanced_flow_metrics(
    rows: list[InstitutionalOptionStrikeRow],
    expiry_rows: list[ExpiryAnalytics],
    metrics: ChainInstitutionalMetrics,
    strike_rows: list[StrikeAnalyticsRow],
    spot: float,
    r: float,
    ticker: str,
    provider: str | None,
    as_of: str | None,
    quality: InstitutionalChainQuality,
    vendor_benchmarks: dict[str, dict[str, float]] | None = None,
) -> AdvancedFlowMetrics:
    _ = strike_rows
    standard = _build_institutional_standard_metrics(rows, spot, r, as_of)
    liquidity = _build_liquidity_stress(rows)
    readiness = _build_trade_level_readiness(rows)
    surface = _build_surface_diagnostics(rows, as_of)
    dealer = _build_dealer_positioning_v2(rows, spot)
    state = _build_intraday_state_machine(rows, expiry_rows, metrics, spot, as_of, dealer)
    return AdvancedFlowMetrics(
        tape_aggressor_flow=_build_tape_aggressor_flow(rows),
        quote_pressure=_build_quote_pressure(rows),
        iv_skew_velocity=_build_iv_skew_velocity(rows, expiry_rows, metrics),
        contract_metadata_risk=_build_contract_metadata_risk(rows),
        option_rv_iv=_build_option_rv_iv(rows),
        venue_quality=_build_venue_quality(rows),
        opening_closing_flow=_build_opening_closing_flow(rows),
        gamma_hedge_demand=_build_gamma_hedge_demand(rows, spot),
        zero_dte_exhaustion=_build_zero_dte_exhaustion(rows, expiry_rows),
        liquidity_stress=liquidity,
        institutional_standard=standard,
        trade_level_readiness=readiness,
        surface_diagnostics=surface,
        product_conventions=_build_product_conventions(ticker, rows),
        dealer_positioning_v2=dealer,
        intraday_state_machine=state,
        portfolio_risk_overlay=_build_portfolio_risk_overlay(
            metrics, standard, liquidity, dealer, state
        ),
        data_lineage=_build_data_lineage(
            provider, as_of, quality, readiness, standard, dealer, surface
        ),
        vendor_reconciliation=_build_vendor_reconciliation(
            readiness, standard, metrics, expiry_rows, vendor_benchmarks
        ),
        scheduler_plan=_build_scheduler_plan(),
    )


def _top_wall(
    rows: list[InstitutionalOptionStrikeRow],
    side: Literal["call", "put"],
) -> InstitutionalOptionStrikeRow | None:
    if side == "call":
        candidates = [r for r in rows if r.call_gex is not None and r.call_gex > 0]
        if not candidates:
            return None
        return max(candidates, key=lambda r: abs(r.call_gex if r.call_gex is not None else 0.0))
    candidates = [r for r in rows if r.put_gex is not None and r.put_gex < 0]
    if not candidates:
        return None
    return max(candidates, key=lambda r: abs(r.put_gex if r.put_gex is not None else 0.0))


def _alert_severity(
    distance_pct: float, warning_threshold: float, critical_threshold: float
) -> Literal["info", "warning", "critical"]:
    if distance_pct <= critical_threshold:
        return "critical"
    if distance_pct <= warning_threshold:
        return "warning"
    return "info"


def _build_chain_alerts(
    rows: list[InstitutionalOptionStrikeRow],
    spot: float,
    metrics: ChainInstitutionalMetrics,
    expiry_rows: list[ExpiryAnalytics],
    dominant_expiries: list[DominantExpiryRow],
    advanced_flow_metrics: AdvancedFlowMetrics | None = None,
) -> list[ChainAlert]:
    alerts: list[ChainAlert] = []
    if spot <= 0:
        return alerts

    if metrics.vol_trigger_proxy is not None:
        distance = abs(metrics.vol_trigger_proxy - spot) / spot * 100.0
        if distance <= 5.0:
            alerts.append(
                ChainAlert(
                    kind="gamma_flip_near_spot",
                    severity=_alert_severity(distance, 5.0, 1.0),
                    message="Vol trigger / gamma flip proxy is close to spot.",
                    level=_round(metrics.vol_trigger_proxy),
                    distance_pct=_round(distance, 4),
                    metadata={"spot": _round(spot), "gamma_regime": metrics.gamma_regime},
                )
            )

    for expiry in expiry_rows:
        if expiry.zero_dte_gamma_share >= 35.0:
            alerts.append(
                ChainAlert(
                    kind="zero_dte_gamma_concentration",
                    severity="critical" if expiry.zero_dte_gamma_share >= 50.0 else "warning",
                    message="0DTE/near-term gamma concentration dominates chain risk.",
                    level=_round(expiry.expiry_gamma_pressure, 2),
                    distance_pct=None,
                    metadata={
                        "expiration": expiry.expiration,
                        "zero_dte_gamma_share": _round(expiry.zero_dte_gamma_share, 4),
                        "dte_days": expiry.dte_days,
                    },
                )
            )

    aggregate_call_wall = _top_wall(rows, "call")
    aggregate_put_wall = _top_wall(rows, "put")
    dominant_exp = dominant_expiries[0].expiration if dominant_expiries else None
    dominant_rows = [r for r in rows if r.expiration == dominant_exp] if dominant_exp else []
    dominant_call_wall = _top_wall(dominant_rows, "call")
    dominant_put_wall = _top_wall(dominant_rows, "put")

    if (
        aggregate_call_wall
        and dominant_call_wall
        and aggregate_call_wall.strike != dominant_call_wall.strike
    ):
        distance = abs(dominant_call_wall.strike - aggregate_call_wall.strike) / spot * 100.0
        alerts.append(
            ChainAlert(
                kind="call_wall_displacement",
                severity="warning" if distance >= 1.0 else "info",
                message="Dominant-expiry call wall differs from aggregate call wall.",
                level=dominant_call_wall.strike,
                distance_pct=_round(distance, 4),
                metadata={
                    "aggregate_wall": aggregate_call_wall.strike,
                    "dominant_expiry": dominant_exp,
                    "dominant_wall": dominant_call_wall.strike,
                },
            )
        )
    elif dominant_call_wall:
        for exp, items in _by_expiry(rows).items():
            if exp == dominant_exp:
                continue
            wall = _top_wall(items, "call")
            if wall and wall.strike != dominant_call_wall.strike:
                distance = abs(wall.strike - dominant_call_wall.strike) / spot * 100.0
                alerts.append(
                    ChainAlert(
                        kind="call_wall_displacement",
                        severity="warning" if distance >= 1.0 else "info",
                        message="A secondary expiry call wall differs from the dominant-expiry call wall.",
                        level=wall.strike,
                        distance_pct=_round(distance, 4),
                        metadata={
                            "comparison_expiry": exp,
                            "dominant_expiry": dominant_exp,
                            "dominant_wall": dominant_call_wall.strike,
                        },
                    )
                )
                break

    if (
        aggregate_put_wall
        and dominant_put_wall
        and aggregate_put_wall.strike != dominant_put_wall.strike
    ):
        distance = abs(dominant_put_wall.strike - aggregate_put_wall.strike) / spot * 100.0
        alerts.append(
            ChainAlert(
                kind="put_wall_displacement",
                severity="warning" if distance >= 1.0 else "info",
                message="Dominant-expiry put wall differs from aggregate put wall.",
                level=dominant_put_wall.strike,
                distance_pct=_round(distance, 4),
                metadata={
                    "aggregate_wall": aggregate_put_wall.strike,
                    "dominant_expiry": dominant_exp,
                    "dominant_wall": dominant_put_wall.strike,
                },
            )
        )
    elif dominant_put_wall:
        for exp, items in _by_expiry(rows).items():
            if exp == dominant_exp:
                continue
            wall = _top_wall(items, "put")
            if wall and wall.strike != dominant_put_wall.strike:
                distance = abs(wall.strike - dominant_put_wall.strike) / spot * 100.0
                alerts.append(
                    ChainAlert(
                        kind="put_wall_displacement",
                        severity="warning" if distance >= 1.0 else "info",
                        message="A secondary expiry put wall differs from the dominant-expiry put wall.",
                        level=wall.strike,
                        distance_pct=_round(distance, 4),
                        metadata={
                            "comparison_expiry": exp,
                            "dominant_expiry": dominant_exp,
                            "dominant_wall": dominant_put_wall.strike,
                        },
                    )
                )
                break
    if advanced_flow_metrics is not None:
        standard = advanced_flow_metrics.institutional_standard
        skew = standard.normalized_25d_skew_30
        if skew is not None and skew >= 0.35:
            alerts.append(
                ChainAlert(
                    kind="skew_steepening",
                    severity="warning" if skew < 0.75 else "critical",
                    message="30D normalized 25-delta skew is elevated versus ATM volatility.",
                    level=_round(skew, 6),
                    source="surface_derived",
                    metadata={"normalized_25d_skew_30": _round(skew, 6)},
                )
            )
        borrow = standard.implied_borrow_30d
        if borrow is not None and borrow >= 0.08:
            alerts.append(
                ChainAlert(
                    kind="borrow_stress",
                    severity="warning" if borrow < 0.20 else "critical",
                    message="Synthetic 30D implied borrow is elevated.",
                    level=_round(borrow, 6),
                    source="surface_derived",
                    metadata={"implied_borrow_30d": _round(borrow, 6)},
                )
            )
        if standard.vega_notional_traded >= 1_000.0:
            alerts.append(
                ChainAlert(
                    kind="vega_volume_surge",
                    severity="info",
                    message="Dollar-vega traded is elevated for the current chain snapshot.",
                    level=_round(standard.vega_notional_traded, 2),
                    source="session_proxy",
                    metadata={"vega_notional_traded": _round(standard.vega_notional_traded, 2)},
                )
            )
        if standard.effective_spread_pct is not None and standard.effective_spread_pct >= 4.0:
            alerts.append(
                ChainAlert(
                    kind="effective_spread_deterioration",
                    severity="warning",
                    message="Effective spread proxy indicates deteriorating execution quality.",
                    level=_round(standard.effective_spread_pct, 4),
                    source="session_proxy",
                    metadata={"effective_spread_pct": _round(standard.effective_spread_pct, 4)},
                )
            )
        rnd = standard.risk_neutral_density_moments
        if (rnd.get("downside_5pct_probability") or 0.0) >= 0.25 or (
            rnd.get("q_skewness") or 0.0
        ) <= -0.8:
            alerts.append(
                ChainAlert(
                    kind="rnd_left_tail_expansion",
                    severity="warning",
                    message="Risk-neutral density indicates elevated left-tail pricing.",
                    level=_round(rnd.get("q_skewness"), 6),
                    source="surface_derived",
                    metadata={
                        "q_skewness": _round(rnd.get("q_skewness"), 6),
                        "downside_5pct_probability": _round(
                            rnd.get("downside_5pct_probability"), 6
                        ),
                    },
                )
            )
        if (standard.true_aggressor_net_premium or 0.0) > 0 and (
            standard.true_aggressor_flow_imbalance or 0.0
        ) > 0.15:
            alerts.append(
                ChainAlert(
                    kind="true_aggressor_call_buying",
                    severity="info",
                    message="Trade-level aggressor premium is net buyer-initiated.",
                    level=_round(standard.true_aggressor_net_premium, 2),
                    source="provider",
                    metadata={
                        "true_aggressor_flow_imbalance": _round(
                            standard.true_aggressor_flow_imbalance, 6
                        )
                    },
                )
            )
        capacity = standard.participant_capacity_flow
        total_capacity = sum(capacity.values())
        customer_share = _safe_div(capacity.get("customer", 0.0), total_capacity) or 0.0
        if total_capacity > 0 and customer_share >= 0.65:
            alerts.append(
                ChainAlert(
                    kind="customer_put_hedging_surge",
                    severity="info",
                    message="Customer capacity dominates reported option flow.",
                    level=_round(capacity.get("customer", 0.0), 2),
                    source="provider",
                    metadata={k: _round(v, 2) for k, v in capacity.items()},
                )
            )
    return alerts


def _quality_warnings(rows: list[InstitutionalOptionStrikeRow]) -> list[str]:
    if not rows:
        return ["empty_chain"]
    warnings: list[str] = []
    iv_rows = sum(1 for r in rows if (r.call_iv and r.call_iv > 0) or (r.put_iv and r.put_iv > 0))
    quote_rows = sum(1 for r in rows if r.call_mid is not None or r.put_mid is not None)
    oi_rows = sum(1 for r in rows if (r.call_oi or 0.0) + (r.put_oi or 0.0) > 0)
    if iv_rows / len(rows) < 0.7:
        warnings.append("low_iv_coverage")
    if quote_rows / len(rows) < 0.7:
        warnings.append("low_quote_coverage")
    if oi_rows / len(rows) < 0.7:
        warnings.append("low_open_interest_coverage")
    return warnings


def _quality_score(rows: list[InstitutionalOptionStrikeRow], warnings: list[str]) -> float:
    if not rows:
        return 0.0
    iv_rows = sum(1 for r in rows if (r.call_iv and r.call_iv > 0) or (r.put_iv and r.put_iv > 0))
    quote_rows = sum(1 for r in rows if r.call_mid is not None or r.put_mid is not None)
    oi_rows = sum(1 for r in rows if (r.call_oi or 0.0) + (r.put_oi or 0.0) > 0)
    score = (
        (iv_rows / len(rows)) * 35.0
        + (quote_rows / len(rows)) * 35.0
        + (oi_rows / len(rows)) * 30.0
    )
    return round(max(0.0, min(score - min(len(warnings) * 8.0, 24.0), 100.0)), 2)
