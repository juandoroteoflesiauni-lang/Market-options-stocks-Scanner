from __future__ import annotations
from typing import Literal, Any
"""Portfolio and prop-firm risk models for the Management desk."""



from pydantic import BaseModel, ConfigDict, Field, model_validator

AccountStatus = Literal["ACTIVE", "AT_RISK", "LOCKED", "BREACHED"]
CandidateDecision = Literal["ALLOW", "SIZE_DOWN", "BLOCK"]
DrawdownType = Literal["static", "trailing_eod", "trailing_intraday"]
DailyLossRule = Literal["balance", "equity", "hybrid"]
PositionSide = Literal["LONG", "SHORT"]
AssetType = Literal["equity", "option", "future", "crypto", "cash", "other"]
ModuleBacktestGrade = Literal["validated", "weak_edge", "overfit_risk", "insufficient_data"]


class FundingRulePreset(BaseModel):
    model_config = ConfigDict(extra="allow")

    id: str = "custom"
    name: str = "Custom"
    initial_capital: float | None = Field(default=None, gt=0)
    drawdown_type: DrawdownType = "static"
    timezone: str = "America/New_York"
    reset_time: str = "00:00"
    daily_loss_pct: float | None = Field(default=None, ge=0)
    daily_loss_amount: float | None = Field(default=None, ge=0)
    max_loss_pct: float | None = Field(default=None, ge=0)
    max_loss_amount: float | None = Field(default=None, ge=0)
    profit_target_pct: float | None = Field(default=None, ge=0)
    verification_profit_target_pct: float | None = Field(default=None, ge=0)
    consistency_cap: float = Field(default=0.50, ge=0, le=1)
    consistency_warning: float = Field(default=0.35, ge=0, le=1)
    min_trading_days: int = Field(default=0, ge=0)
    max_position_exposure_pct: float = Field(default=20.0, gt=0)
    max_symbol_exposure_pct: float = Field(default=20.0, gt=0)
    max_direction_exposure_pct: float = Field(default=60.0, gt=0)
    risk_per_trade_pct: float = Field(default=0.50, gt=0)
    max_contracts: int | None = Field(default=None, gt=0)
    daily_lock_threshold: float = Field(default=0.80, ge=0, le=1)
    daily_loss_rule: DailyLossRule = "equity"
    lockout_on_daily_breach: bool = True


class AccountState(BaseModel):
    initial_capital: float = Field(gt=0)
    current_equity: float = Field(gt=0)
    start_of_day_balance: float = Field(gt=0)
    high_watermark_balance: float | None = Field(default=None, gt=0)
    phase: str = "challenge"


class PortfolioPosition(BaseModel):
    symbol: str
    asset_type: AssetType = "equity"
    quantity: float
    avg_price: float = Field(ge=0)
    mark_price: float = Field(ge=0)
    side: PositionSide = "LONG"
    stop_price: float | None = Field(default=None, ge=0)
    beta: float | None = None
    greeks: dict[str, float] = Field(default_factory=dict)
    sector: str | None = None

    @model_validator(mode="after")
    def normalize_symbol(self) -> PortfolioPosition:
        self.symbol = self.symbol.upper().strip()
        return self


class TradeCandidate(BaseModel):
    symbol: str
    direction: PositionSide
    entry: float = Field(gt=0)
    stop: float | None = Field(default=None, gt=0)
    target: float | None = Field(default=None, gt=0)
    confidence: float = Field(default=0.0, ge=0, le=1)
    source_module: str = "manual"
    expected_win_prob: float | None = Field(default=None, ge=0, le=1)
    rr_ratio: float | None = Field(default=None, gt=0)
    scanner_score: float | None = Field(default=None, ge=0, le=100)
    conflict_score: float | None = Field(default=None, ge=0, le=1)
    tail_risk: float | None = Field(default=None, ge=0)
    jump_risk: float | None = Field(default=None, ge=0)
    gamma_regime: str | None = None
    iv_term_structure: str | None = None
    squeeze_probability: float | None = Field(default=None, ge=0, le=1)
    atr_pct: float | None = Field(default=None, ge=0)
    module_backtest_grade: ModuleBacktestGrade | None = None
    module_backtest_trades: int | None = Field(default=None, ge=0)
    module_backtest_sharpe: float | None = None
    module_backtest_profit_factor: float | None = None
    options_gex_source_tier: str | None = None
    options_gex_data_quality_score: float | None = Field(default=None, ge=0, le=1)
    options_gex_missing_components: list[str] = Field(default_factory=list)
    # Scanner multi-module evidence fields (populated via candidate_from_scanner_row).
    funding_suitability: str | None = None
    funding_reason_codes: list[str] = Field(default_factory=list)
    evidence_by_module: dict[str, Any] = Field(default_factory=dict)
    best_supporting_module: str | None = None
    weakest_link_module: str | None = None
    scanner_recommended_size_multiplier: float | None = Field(default=None, ge=0.0, le=1.0)

    @model_validator(mode="after")
    def normalize_symbol(self) -> TradeCandidate:
        self.symbol = self.symbol.upper().strip()
        return self


class TradeHistoryItem(BaseModel):
    date: str
    pnl: float


class PortfolioRiskRequest(BaseModel):
    account_state: AccountState
    preset: FundingRulePreset = Field(default_factory=FundingRulePreset)
    positions: list[PortfolioPosition] = Field(default_factory=list)
    candidates: list[TradeCandidate] = Field(default_factory=list)
    realized_daily_pnl: float = 0.0
    unrealized_pnl: float = 0.0
    trade_history: list[TradeHistoryItem] = Field(default_factory=list)
    returns_pct: list[float] = Field(default_factory=list)
    context: dict[str, Any] = Field(default_factory=dict)


class RuleUsage(BaseModel):
    limit_amount: float
    limit_equity: float | None = None
    used_amount: float
    remaining_amount: float
    usage_pct: float
    breached: bool


class AllowedRiskBudget(BaseModel):
    daily_remaining_amount: float
    daily_lock_remaining_amount: float
    per_trade_pct: float
    per_trade_amount: float
    max_position_notional: float
    max_attempts_remaining: int


class CandidateGateDecision(BaseModel):
    symbol: str
    direction: PositionSide
    decision: CandidateDecision
    allowed_risk_pct: float
    suggested_notional: float
    size_multiplier: float
    max_loss_at_stop: float
    reasons: list[str] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    remaining_daily_risk_after: float
    module_backtest_grade: ModuleBacktestGrade | None = None
    options_gex_source_tier: str | None = None
    options_gex_data_quality_score: float | None = None
    funding_suitability: str | None = None
    funding_reason_codes: list[str] = Field(default_factory=list)
    evidence_by_module: dict[str, Any] = Field(default_factory=dict)
    best_supporting_module: str | None = None
    weakest_link_module: str | None = None
    scanner_recommended_size_multiplier: float | None = Field(default=None, ge=0.0, le=1.0)
    remaining_daily_risk_pct: float = 0.0
    remaining_max_loss_pct: float = 0.0


class PortfolioMetrics(BaseModel):
    positions_count: int
    gross_exposure: float
    net_exposure: float
    long_exposure: float
    short_exposure: float
    concentration_hhi: float
    largest_symbol_weight_pct: float
    hist_var_95_pct: float | None = None
    stress: dict[str, float] = Field(default_factory=dict)


class ConsistencyMetrics(BaseModel):
    total_profit: float
    best_day_profit: float
    best_day_ratio: float
    trading_days: int
    status: Literal["insufficient_data", "ok", "warning", "blocked"]


class FundingSurvivalSummary(BaseModel):
    """Risk Desk top-level survival summary — additive Phase D field."""

    funding_survival_score: float = Field(
        default=0.0,
        ge=0.0,
        le=100.0,
        description="0=imminent breach, 100=safe. Computed from rule usages + consistency.",
    )
    max_attempts_remaining_today: int = Field(
        default=0,
        ge=0,
        description="Conservative count of full-size attempts before the daily lock fires.",
    )
    recommended_risk_per_trade_pct: float = Field(
        default=0.0,
        ge=0.0,
        description="Final, post-degradation risk-per-trade cap. May be below preset value.",
    )
    kill_switch_reasons: list[str] = Field(
        default_factory=list,
        description="Stable reason codes — non-empty means at least one hard rule fired.",
    )
    funding_grade: Literal[
        "safe", "monitor", "at_risk", "locked", "breached", "insufficient_data"
    ] = Field(default="insufficient_data")
    remaining_daily_risk_pct: float = Field(default=0.0, ge=0.0)
    remaining_max_loss_pct: float = Field(default=0.0, ge=0.0)
    consistency_headroom_pct: float = Field(
        default=0.0,
        ge=0.0,
        description="Headroom before consistency cap fires (%).",
    )


class ChallengeSimulationResult(BaseModel):
    preset_id: str
    preset_name: str
    account_status: AccountStatus
    first_breach_rule: str | None = None  # "daily_loss" | "max_loss" | "consistency" | None
    daily_loss_usage_pct: float
    max_loss_usage_pct: float
    consistency_ratio: float | None = None
    notes: list[str] = Field(default_factory=list)


class PortfolioRiskResponse(BaseModel):
    account_status: AccountStatus
    preset: FundingRulePreset
    rule_usage: dict[str, RuleUsage]
    breach_warnings: list[str]
    allowed_risk_budget: AllowedRiskBudget
    candidate_decisions: list[CandidateGateDecision]
    portfolio_metrics: PortfolioMetrics
    consistency_metrics: ConsistencyMetrics
    action_plan: list[str]
    data_quality: dict[str, str]
    funding_survival: FundingSurvivalSummary = Field(default_factory=FundingSurvivalSummary)
    challenge_simulation: list[ChallengeSimulationResult] = Field(default_factory=list)
