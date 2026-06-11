/**
 * Market Scanner — Backend contract types (1:1 mapping from Pydantic models).
 *
 * Source of truth: backend/domain/market_scanner_models.py
 * These interfaces mirror the snake_case JSON serialization from FastAPI.
 *
 * @module types/marketScanner
 */

// ── Literal unions ──────────────────────────────────────────────────────────

export type ScannerTimeframe = "5m" | "15m" | "1h" | "1D";
export type ScannerDirection = "long" | "short" | "both";
export type ScannerSort =
  | "scanner_score"
  | "conviction_score"
  | "regime_fit_score"
  | "capacity_score"
  | "symbol"
  | "change_pct"
  | "relative_volume"
  | "universe_percentile";
export type ScannerLiquidityTier = "high" | "normal" | "low" | "unknown";
export type DeskRegimeLabel =
  | "BULL_QUIET"
  | "BEAR_VOLATILE"
  | "CRISIS"
  | "RECOVERY"
  | "TRANSITION";
export type ScannerSignalLabel =
  | "strong_buy"
  | "buy"
  | "neutral"
  | "sell"
  | "strong_sell";
export type ScannerBias = "bullish" | "bearish" | "neutral" | "unavailable";
export type ScannerGrade = "A+" | "A" | "B" | "C" | "WATCH" | "VETO";
export type ScannerIndicatorModule =
  | "core"
  | "technical"
  | "probabilistic"
  | "options_gex"
  | "fundamentals"
  | "macro_micro";
export type ScannerModuleKey =
  | "technical"
  | "probabilistic"
  | "options_gex"
  | "fundamentals"
  | "macro_micro";
export type ScannerCostTier = "cheap" | "phase_b" | "cached_external";
export type ScannerIndicatorStatus =
  | "real"
  | "partial"
  | "proxy"
  | "not_connected";
export type ScannerIndicatorSource =
  | "bingx_l2"
  | "bingx_trade"
  | "massive_options"
  | "deribit_options"
  | "ohlcv_proxy"
  | "not_connected";
export type ScannerBriefTone =
  | "bullish"
  | "bearish"
  | "neutral"
  | "warning"
  | "unavailable";
export type ScannerSourceStatus =
  | "available"
  | "partial"
  | "source unavailable";
export type ScannerNewsImpact = "high" | "medium" | "low";
export type ScannerNewsSentiment =
  | "bullish"
  | "bearish"
  | "neutral"
  | "unavailable";
export type ScannerPortfolioOptimizerEngine =
  | "internal"
  | "skfolio"
  | "riskfolio";
export type ScannerPortfolioOptimizerStatus = "ok" | "degraded" | "unavailable";
export type ScannerPortfolioRiskBudgetMode =
  | "equal_weight"
  | "inverse_vol_from_sparkline"
  | "score_weighted"
  | "correlation_penalty"
  | "barra_risk_budget";
export type ScannerRiskModelVersion =
  | "institutional-barra-v1"
  | "legacy-gross-v1";
export type BarraAssetClass = "equity" | "crypto" | "other";
export type ScannerExecutionSimStatus = "ok" | "degraded" | "unavailable";
export type ScannerExecutionDirection = "long" | "short";
export type ScannerExecutionMode = "paper" | "replay" | "backtest";

// ── Veto / Warning constants ────────────────────────────────────────────────

export const VETO_NO_DATA = "VETO_NO_DATA" as const;
export const VETO_ILLIQUID = "VETO_ILLIQUID" as const;
export const VETO_COMPLETE_CONTRADICTION =
  "VETO_COMPLETE_CONTRADICTION" as const;
export const VETO_EXTREME_EXHAUSTION = "VETO_EXTREME_EXHAUSTION" as const;

export const WARN_LOW_RVOL = "WARN_LOW_RVOL" as const;
export const WARN_TF_DIVERGENCE = "WARN_TF_DIVERGENCE" as const;
export const WARN_MODERATE_RSI = "WARN_RSI_EXTENDED" as const;
export const WARN_LOW_CONFIDENCE = "WARN_LOW_CONFIDENCE" as const;

// ── Request models ──────────────────────────────────────────────────────────

export interface MarketScannerFilters {
  min_price: number;
  min_volume: number;
  min_relative_volume: number;
  min_score: number;
  allow_reversal: boolean;
  include_vetoed: boolean;
}

export interface ScannerCustomization {
  enabled_indicators: string[] | null;
  enabled_modules: ScannerModuleKey[] | null;
  weight_matrix: Record<string, Record<string, number>>;
  module_synthesis_limit: number;
  primary_timeframe: ScannerTimeframe | null;
  adaptive_weighting: boolean;
  scoring_schema_version: string | null;
}

export interface MarketScannerRequest {
  universe: string;
  symbols: string[];
  timeframes: ScannerTimeframe[];
  filters: MarketScannerFilters;
  sort: ScannerSort;
  direction: ScannerDirection;
  max_rows: number;
  include_deep_metrics: boolean;
  customization: ScannerCustomization;
  webhook_url: string | null;
  include_funding_gate: boolean;
}

// ── Response models ─────────────────────────────────────────────────────────

export interface MarketScannerTimeframeSignal {
  timeframe: ScannerTimeframe;
  ok: boolean;
  direction: ScannerBias;
  label: ScannerSignalLabel;
  score: number;
  confidence: number;
  metrics: Record<string, number | string | boolean | null>;
  reasons: string[];
  warnings: string[];
  vetoes: string[];
  contributions: Record<string, number>;
  indicator_scores: Record<string, number>;
}

export interface ScannerModuleSignal {
  module: ScannerModuleKey;
  label: ScannerSignalLabel;
  score: number;
  confidence: number;
  engine_count: number;
  available_count: number;
  reasons: string[];
  warnings: string[];
  execution_latency_ms: number | null;
  engine_latencies: Record<string, number>;
}

export interface ScannerFactorDriver {
  factor_key: string;
  contribution_pct: number;
  loading: number;
  historical_percentile: number | null;
  data_tier: string;
  source: string;
}

export interface ScannerConvictionBreakdown {
  conviction_score: number;
  scanner_score: number;
  top_drivers: ScannerFactorDriver[];
  factor_contributions: Record<string, number>;
  historical_percentiles: Record<string, number>;
  coverage_pct: number;
  warnings: string[];
  schema_version: string;
  conviction_score_raw: number | null;
  crowding_penalty_applied: number | null;
}

export interface ScannerCrowdedFactor {
  factor_key: string;
  crowding_percentile: number;
}

export interface ScannerCrowdingBreakdown {
  crowding_penalty: number;
  crowded_factors: ScannerCrowdedFactor[];
  warnings: string[];
  schema_version: string;
}

export interface ScannerCapacitySignals {
  capacity_score: number;
  relative_volume: number | null;
  liquidity_tier: ScannerLiquidityTier;
  estimated_adv_usd: number | null;
  short_interest_pct: number | null;
  institutional_ownership_pct: number | null;
  production_size_hint: number | null;
  warnings: string[];
  schema_version: string;
}

export interface GexPressureLevel {
  strike: number;
  net_gex: number;
  call_gex: number | null;
  put_gex: number | null;
  net_gamma_exposure: number | null;
  call_gamma_exposure: number | null;
  put_gamma_exposure: number | null;
  open_interest: number | null;
}

export interface ScannerInstitutionalOverlay {
  snapshot_ok: boolean;
  spot: number | null;
  gamma_flip: number | null;
  net_gex_total: number | null;
  dealer_bias: string | null;
  call_wall: number | null;
  put_wall: number | null;
  zero_gamma_distance_pct: number | null;
  net_vanna_exposure: number | null;
  net_charm_exposure: number | null;
  greek_flow_status: "available" | "degraded" | "unavailable";
  greek_flow_source_tier: string | null;
  greek_flow_data_quality_score: number | null;
  greek_flow_missing_components: string[];
  pressure_by_strike: GexPressureLevel[];
  microstructure: Record<string, number | string | boolean | null>;
  iv_term_structure: Record<string, unknown>;
}

export interface ScannerRegimeFitLine {
  factor_key: string;
  regime: DeskRegimeLabel;
  contribution_pct: number;
  fit_score: number;
  sample_count: number;
  win_rate: number | null;
  avg_forward_return: number | null;
  sharpe_annualized: number | null;
  note: string;
}

export interface ScannerRegimeFitBreakdown {
  regime: DeskRegimeLabel;
  regime_fit_score: number;
  lines: ScannerRegimeFitLine[];
  stress_overlay: string[];
  warnings: string[];
  schema_version: string;
}

export interface BarraFactorExposure {
  symbol: string;
  asset_class: BarraAssetClass;
  factors: Record<string, number>;
  factor_sources: Record<string, string>;
  specific_risk: number | null;
}

export interface MarketScannerRow {
  symbol: string;
  price: number | null;
  change_pct: number | null;
  sparkline: number[];
  signals: Record<string, MarketScannerTimeframeSignal>;
  scanner_score: number;
  setup_grade: ScannerGrade;
  direction: ScannerBias;
  reasons: string[];
  warnings: string[];
  vetoes: string[];
  module_signals: Record<string, ScannerModuleSignal>;
  source: string | null;
  deep_metrics: Record<string, unknown> | null;
  institutional_overlay: ScannerInstitutionalOverlay | null;
  score_audit: Record<string, unknown>;
  intraday_score: number;
  swing_score: number;
  regime_label: string | null;
  regime_weight_multipliers: Record<string, number> | null;
  risk_hints: Record<string, number | string>;
  score_ci_low: number | null;
  score_ci_high: number | null;
  directional_score: number | null;
  risk_score: number | null;
  data_quality_score: number | null;
  module_backtest_grade: string | null;
  funding_suitability: string;
  funding_reason_codes: string[];
  evidence_by_module: Record<string, Record<string, unknown>>;
  best_supporting_module: string | null;
  weakest_link_module: string | null;
  recommended_size_multiplier: number | null;
  universe_z_score: number | null;
  universe_percentile: number | null;
  universe_rank: number | null;
  portfolio_weight: number | null;
  production_size_multiplier: number | null;
  production_kelly_fraction: number | null;
  factor_loadings: Record<string, number>;
  source_attribution: Record<string, Record<string, string>>;
  indicator_metrics: Record<string, Record<string, unknown>>;
  engine_latency_ms: Record<string, number>;
  barra_exposure: BarraFactorExposure | null;
  specific_risk: number | null;
  conviction_breakdown: ScannerConvictionBreakdown | null;
  conviction_score: number | null;
  regime_fit_score: number | null;
  regime_fit_breakdown: ScannerRegimeFitBreakdown | null;
  crowding_breakdown: ScannerCrowdingBreakdown | null;
  capacity_signals: ScannerCapacitySignals | null;
}

export interface LeadersCorrelationMatrix {
  symbols: string[];
  matrix: (number | null)[][];
}

export interface DeskRegimeSnapshot {
  label: DeskRegimeLabel;
  confidence: number;
  components: Record<string, unknown>;
  reason_codes: string[];
  detected_at: string;
  method_version: string;
}

export interface FactorCrowdingIndex {
  factor_key: string;
  crowding_percentile: number | null;
  concentration_score: number;
  loading_dispersion: number;
  pairwise_corr_mean: number;
  data_tier: "real" | "proxy";
}

export interface ScannerRiskStackAllocation {
  symbol: string;
  portfolio_weight: number;
  production_size_multiplier: number;
  production_kelly_fraction: number;
  factor_loadings: Record<string, number>;
  funding_size_multiplier: number;
  drawdown_cap_multiplier: number;
  warnings: string[];
}

export interface ScannerRiskStackResponse {
  enabled: boolean;
  schema_version: string;
  allocations: ScannerRiskStackAllocation[];
  factor_exposure: Record<string, number>;
  constraints_applied: Record<string, unknown>;
  warnings: string[];
  optimizer_status: ScannerPortfolioOptimizerStatus;
  barra_risk_model: BarraRiskModelOutput | null;
}

export interface BarraRiskModelOutput {
  enabled: boolean;
  schema_version: ScannerRiskModelVersion;
  exposures: BarraFactorExposure[];
  covariance: Record<string, unknown> | null;
  factor_risk_contribution: Record<string, number>;
  specific_risk_by_symbol: Record<string, number>;
  marginal_risk_contribution: Record<string, number>;
  factor_risk_budget: Record<string, number>;
  warnings: string[];
}

export interface MarketScannerResponse {
  generated_at: string;
  universe: string;
  rows: MarketScannerRow[];
  skipped_symbols: Record<string, string>;
  data_quality: Record<string, unknown>;
  scoring_version: string;
  scoring_schema_version: string | null;
  effective_weight_matrix: Record<string, Record<string, number>> | null;
  regime_multipliers_applied: Record<string, number> | null;
  universe_stats: Record<string, number> | null;
  catalog_version: string;
  feature_freshness: Record<string, number | string | boolean>;
  cost_estimate: Record<string, number | string | boolean>;
  leaders_correlation: LeadersCorrelationMatrix | null;
  observability: Record<string, unknown>;
  universe_regime_summary: Record<string, unknown>;
  portfolio_risk_stack: ScannerRiskStackResponse | null;
  barra_risk_model: BarraRiskModelOutput | null;
  risk_model_version: ScannerRiskModelVersion | null;
  macro_context: Record<string, unknown> | null;
  desk_regime: DeskRegimeSnapshot | null;
  factor_crowding: FactorCrowdingIndex[];
  compliance_metadata: Record<string, unknown>;
}

// ── Universe / Preset models ────────────────────────────────────────────────

export interface MarketScannerUniverse {
  key: string;
  label: string;
  symbols: string[];
  count: number;
}

export interface MarketScannerPreset {
  key: string;
  label: string;
  description: string;
  request: MarketScannerRequest;
}

// ── Live prices ─────────────────────────────────────────────────────────────

export interface MarketScannerLivePricesRequest {
  symbols: string[];
}

export interface MarketScannerLivePriceRow {
  symbol: string;
  price: number;
  change_pct: number | null;
  source: string;
  timestamp_ms: number | null;
}

export interface MarketScannerLivePricesResponse {
  generated_at: string;
  prices: Record<string, MarketScannerLivePriceRow>;
}

// ── Indicator catalog ───────────────────────────────────────────────────────

export interface ScannerIndicatorDefinition {
  key: string;
  label: string;
  module: ScannerIndicatorModule;
  description: string;
  default_enabled: boolean;
  supports_timeframes: ScannerTimeframe[];
  weight_by_timeframe: Record<string, number>;
  cost_tier: ScannerCostTier;
  requires: string[];
  status: ScannerIndicatorStatus;
  status_detail: string;
}

export interface MarketScannerIndicatorCatalogResponse {
  catalog_version: string;
  indicators: ScannerIndicatorDefinition[];
}

// ── Context / News ──────────────────────────────────────────────────────────

export interface ScannerBriefBlock {
  key: string;
  title: string;
  value: string;
  detail: string;
  tone: ScannerBriefTone;
  source: string;
  status: ScannerSourceStatus;
}

export interface ScannerNewsItem {
  symbol: string;
  published_date: string | null;
  title: string;
  source: string;
  url: string | null;
  summary: string | null;
  impact: ScannerNewsImpact;
  sentiment: ScannerNewsSentiment;
}

export interface MarketScannerContextResponse {
  generated_at: string;
  market_brief: ScannerBriefBlock[];
  fear_greed: Record<string, unknown> | null;
  news: ScannerNewsItem[];
  sentiment_by_symbol: Record<string, Record<string, unknown>>;
  catalysts_by_symbol: Record<string, Record<string, unknown>>;
  argentina_summary: Record<string, unknown> | null;
  sources: Record<string, string>;
  regulatory_scan_summary: Record<string, unknown>;
}

// ── Derived display types (Frontend-only) ───────────────────────────────────

/**
 * Display-friendly ticker derived from MarketScannerRow.
 * Maps backend snake_case to frontend-friendly structure.
 * Prices are strings to avoid JS float precision issues (PD-2).
 */
export interface ScannerTickerDisplay {
  symbol: string;
  price: string;
  change_pct: string;
  phase: "A" | "B" | "C" | "D";
  scanner_score: string;
  setup_grade: ScannerGrade;
  direction: ScannerBias;
  intraday_score: string;
  swing_score: string;
  regime_label: string | null;
  sparkline: number[];
  reasons: string[];
  warnings: string[];
  vetoes: string[];
  funding_suitability: string;
  conviction_score: string | null;
  capacity_score: string | null;
  universe_rank: number | null;
}
