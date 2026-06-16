"""Domain models for the MFFU Builder Plan funding profile."""

from __future__ import annotations

from decimal import Decimal
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from backend.domain.portfolio_risk_models import FundingRulePreset

BuilderPhase = Literal[
    "EVAL_ACTIVE",
    "EVAL_PASSED_PENDING",
    "SIM_ACTIVE",
    "SIM_PAYOUT_ELIGIBLE",
    "SIM_BUFFER_BUILDING",
    "LIVE_ACTIVE",
    "LIVE_COOLDOWN",
    "BREACHED",
    "INACTIVE_RISK",
]

BuilderDdOption = Literal["default", "addon"]

MFFU_BUILDER_PROFILE_ID = "MFFU_BUILDER_50K"

# Stable reason codes consumed by orchestrator, API and dashboard.
BUILDER_TRAILING_DD_CRITICAL = "builder_trailing_dd_critical"
BUILDER_DAILY_SOFT_PAUSE_THREAT = "builder_daily_soft_pause_threat"
BUILDER_PAYOUT_CONSISTENCY_RISK = "builder_payout_consistency_risk"
BUILDER_BUFFER_NOT_REACHED = "builder_buffer_not_reached"
BUILDER_QUALIFYING_DAYS_MISSING = "builder_qualifying_days_missing"
BUILDER_PAYOUT_CAP_REACHED = "builder_payout_cap_reached"
BUILDER_INACTIVITY_RISK = "builder_inactivity_risk"
BUILDER_LIVE_COOLDOWN_ACTIVE = "builder_live_cooldown_active"
BUILDER_CONTRACT_CAP_EXCEEDED = "builder_contract_cap_exceeded"
BUILDER_PHASE_MISMATCH = "builder_phase_mismatch"
BUILDER_FLOOR_DRIFT_WARNING = "builder_floor_drift_warning"
BUILDER_WOULD_BREACH_ON_STOP = "builder_would_breach_on_stop"
BUILDER_STOP_TRIGGERS_SOFT_PAUSE = "builder_stop_triggers_soft_pause"

BUILDER_REASON_CODES: tuple[str, ...] = (
    BUILDER_TRAILING_DD_CRITICAL,
    BUILDER_DAILY_SOFT_PAUSE_THREAT,
    BUILDER_PAYOUT_CONSISTENCY_RISK,
    BUILDER_BUFFER_NOT_REACHED,
    BUILDER_QUALIFYING_DAYS_MISSING,
    BUILDER_PAYOUT_CAP_REACHED,
    BUILDER_INACTIVITY_RISK,
    BUILDER_LIVE_COOLDOWN_ACTIVE,
    BUILDER_CONTRACT_CAP_EXCEEDED,
    BUILDER_PHASE_MISMATCH,
    BUILDER_FLOOR_DRIFT_WARNING,
    BUILDER_WOULD_BREACH_ON_STOP,
    BUILDER_STOP_TRIGGERS_SOFT_PAUSE,
)


class BuilderProfile(BaseModel):
    """Configurable Builder Plan parameters."""

    model_config = ConfigDict(frozen=True)

    profile_id: str = MFFU_BUILDER_PROFILE_ID
    starting_balance: Decimal = Decimal("50000")
    profit_target: Decimal = Decimal("3000")
    daily_loss_limit: Decimal = Decimal("1000")
    max_loss: Decimal = Decimal("2000")
    payout_buffer: Decimal = Decimal("2100")
    consistency_cap: Decimal = Decimal("0.50")
    payout_cap: Decimal = Decimal("2000")
    min_profit_payout: Decimal = Decimal("500")
    max_sim_payouts: int = Field(default=5, ge=0)
    live_cooldown_days: int = Field(default=21, ge=0)
    inactivity_days: int = Field(default=7, ge=0)
    min_trading_days: int = Field(default=1, ge=0)
    max_minis: int = Field(default=4, gt=0)
    max_micros: int = Field(default=40, gt=0)
    base_risk_per_trade_pct: Decimal = Decimal("0.50")
    dd_option: BuilderDdOption = "default"

    @model_validator(mode="after")
    def _validate_dd_buffer_pair(self) -> BuilderProfile:
        if self.max_loss == Decimal("2000") and self.payout_buffer != Decimal("2100"):
            raise ValueError("default DD $2,000 requires payout buffer $2,100")
        if self.max_loss == Decimal("1500") and self.payout_buffer != Decimal("1600"):
            raise ValueError("addon DD $1,500 requires payout buffer $1,600")
        return self

    def to_funding_rule_preset(self) -> FundingRulePreset:
        """Bridge Builder profile into the shared FundingRulePreset shape."""
        return FundingRulePreset(
            id=self.profile_id,
            name="MFFU Builder $50k",
            initial_capital=float(self.starting_balance),
            drawdown_type="trailing_eod",
            timezone="America/New_York",
            daily_loss_amount=float(self.daily_loss_limit),
            max_loss_amount=float(self.max_loss),
            profit_target_pct=float(self.profit_target / self.starting_balance * Decimal("100")),
            consistency_cap=float(self.consistency_cap),
            consistency_warning=float(self.consistency_cap * Decimal("0.70")),
            min_trading_days=self.min_trading_days,
            risk_per_trade_pct=float(self.base_risk_per_trade_pct),
            max_contracts=self.max_minis,
            daily_loss_rule="equity",
            lockout_on_daily_breach=False,
        )


BuilderPayoutCycleStatus = Literal["open", "eligible", "paid", "closed"]


class BuilderAccountState(BaseModel):
    """Runtime Builder account snapshot."""

    model_config = ConfigDict(frozen=True)

    account_id: str = "default"
    profile_id: str = MFFU_BUILDER_PROFILE_ID
    phase: BuilderPhase = "EVAL_ACTIVE"
    initial_capital: Decimal = Decimal("50000")
    current_equity: Decimal = Decimal("50000")
    start_of_day_balance: Decimal = Decimal("50000")
    high_watermark_balance: Decimal | None = None
    realized_daily_pnl: Decimal = Decimal("0")
    unrealized_pnl: Decimal = Decimal("0")
    trading_days_count: int = Field(default=0, ge=0)
    sim_payouts_count: int = Field(default=0, ge=0)


class BuilderPayoutCycleRecord(BaseModel):
    """Persisted sim-funded payout cycle for Builder accounts."""

    model_config = ConfigDict(frozen=True)

    cycle_id: str
    account_id: str = "default"
    cycle_number: int = Field(ge=1)
    status: BuilderPayoutCycleStatus = "open"
    buffer_target: Decimal
    buffer_progress: Decimal = Decimal("0")
    qualified_days_count: int = Field(default=0, ge=0)
    withdrawable_amount: Decimal = Decimal("0")


class BuilderStateTransitionResult(BaseModel):
    """Outcome of a Builder phase evaluation."""

    model_config = ConfigDict(frozen=True)

    previous_phase: BuilderPhase
    new_phase: BuilderPhase
    state: BuilderAccountState
    transitioned: bool = False
    reason: str = ""
    reason_codes: tuple[str, ...] = ()


class BuilderRuleEvaluation(BaseModel):
    """Continuous Builder contractual risk metrics for a single account snapshot."""

    model_config = ConfigDict(frozen=True)

    distance_to_trailing_dd: Decimal
    distance_to_dll_soft_pause: Decimal
    remaining_daily_risk: Decimal
    remaining_cycle_risk: Decimal
    available_contract_cap: int = Field(ge=0)
    trailing_dd_floor: Decimal
    dll_soft_pause_floor: Decimal
    daily_loss_used: Decimal = Field(ge=0)
    is_breached: bool = False
    is_daily_soft_pause: bool = False
    blocks_new_entries: bool = False
    contract_cap_exceeded: bool = False
    reason_codes: tuple[str, ...] = ()


class BuilderDailyPnl(BaseModel):
    """Single-day PnL entry for payout consistency and qualified-day tracking."""

    model_config = ConfigDict(frozen=True)

    date: str
    pnl: Decimal


BuilderSurvivalStatus = Literal["SAFE", "MONITOR", "AT_RISK", "WOULD_BREACH"]


class BuilderSurvivalSnapshot(BaseModel):
    """Deterministic Builder-native survival score and objective metrics."""

    model_config = ConfigDict(frozen=True)

    status: BuilderSurvivalStatus = "SAFE"
    score: Decimal = Decimal("0")
    recommended_risk_pct: Decimal = Decimal("0")
    eval_progress_pct: Decimal = Decimal("0")
    buffer_progress_pct: Decimal = Decimal("0")
    distance_to_trailing_dd: Decimal = Decimal("0")
    distance_to_dll_soft_pause: Decimal = Decimal("0")
    payout_eligibility_state: str = "not_applicable"
    reason_codes: tuple[str, ...] = ()
    score_components: dict[str, Decimal] = Field(default_factory=dict)


class PayoutEvaluation(BaseModel):
    """Builder payout eligibility snapshot."""

    model_config = ConfigDict(frozen=True)

    eligible: bool = False
    withdrawable_amount: Decimal = Decimal("0")
    buffer_progress_pct: Decimal = Decimal("0")
    consistency_ratio_live: Decimal = Decimal("0")
    qualified_days_count: int = Field(default=0, ge=0)
    suggested_phase: BuilderPhase | None = None
    reason_codes: tuple[str, ...] = ()


class BuilderIntradayDdSnapshot(BaseModel):
    """Intraday trailing-DD projection (EOD floor drift early warning)."""

    model_config = ConfigDict(frozen=True)

    intraday_equity: Decimal = Decimal("0")
    current_eod_floor: Decimal = Decimal("0")
    projected_eod_floor: Decimal = Decimal("0")
    floor_drift_usd: Decimal = Decimal("0")
    distance_to_current_floor: Decimal = Decimal("0")
    distance_to_projected_floor: Decimal = Decimal("0")
    is_new_high_watermark: bool = False
    is_floor_drift_warning: bool = False
    reason_codes: tuple[str, ...] = ()


class BuilderLossScenario(BaseModel):
    """What-if outcome if a candidate trade hits its stop."""

    model_config = ConfigDict(frozen=True)

    contracts: int = Field(default=0, ge=0)
    risk_per_contract_usd: Decimal = Decimal("0")
    loss_if_stopped_usd: Decimal = Decimal("0")
    equity_after_loss: Decimal = Decimal("0")
    distance_to_trailing_dd_after: Decimal = Decimal("0")
    distance_to_dll_after: Decimal = Decimal("0")
    breaches_trailing_dd: bool = False
    triggers_daily_soft_pause: bool = False
    reason_codes: tuple[str, ...] = ()


class BuilderConsistencyGuidance(BaseModel):
    """Live guidance to keep a single day under the consistency cap."""

    model_config = ConfigDict(frozen=True)

    consistency_cap: Decimal = Decimal("0.50")
    consistency_ratio_live: Decimal = Decimal("0")
    total_profit: Decimal = Decimal("0")
    best_day_profit: Decimal = Decimal("0")
    prior_positive_profit: Decimal = Decimal("0")
    max_profit_today_usd: Decimal = Decimal("0")
    is_consistency_at_risk: bool = False
    needs_more_days: bool = False


class BuilderPayoutPlan(BaseModel):
    """Projection of remaining requirements to reach the first payout."""

    model_config = ConfigDict(frozen=True)

    buffer_target: Decimal = Decimal("0")
    buffer_progress: Decimal = Decimal("0")
    buffer_remaining: Decimal = Decimal("0")
    qualified_days_count: int = Field(default=0, ge=0)
    qualified_days_required: int = Field(default=0, ge=0)
    qualified_days_remaining: int = Field(default=0, ge=0)
    min_profit_payout: Decimal = Decimal("0")
    projected_withdrawable: Decimal = Decimal("0")
    avg_daily_profit: Decimal = Decimal("0")
    estimated_days_to_payout: int | None = None
    is_eligible: bool = False
    blocking_reason_codes: tuple[str, ...] = ()


class BuilderSizingDecision(BaseModel):
    """Contract-based sizing output for the Builder overlay."""

    model_config = ConfigDict(frozen=True)

    contracts: int = Field(default=0, ge=0)
    contract_symbol: str = ""
    allowed_risk_pct: Decimal = Decimal("0")
    risk_budget_usd: Decimal = Decimal("0")
    risk_used_usd: Decimal = Decimal("0")
    stop_ticks: int = Field(default=0, ge=0)
    capped_by: str = ""
    builder_factors: dict[str, Decimal] = Field(default_factory=dict)

    # Extended fields for options sizing / non-linear assets
    asset_type: Literal["equity", "option", "future", "crypto", "cash", "other"] = "future"
    margin_required_usd: Decimal = Decimal("0")
    slippage_penalty_pct: Decimal = Decimal("0")
    buying_power_limit_triggered: bool = False


class BuilderDecision(BaseModel):
    """End-to-end Builder orchestration decision."""

    model_config = ConfigDict(frozen=True)

    is_allowed: bool = False
    contracts: int = Field(default=0, ge=0)
    phase: BuilderPhase = "EVAL_ACTIVE"
    allowed_risk_pct: Decimal = Decimal("0")
    risk_used_usd: Decimal = Decimal("0")
    capped_by: str = ""
    payout_state: PayoutEvaluation | None = None
    reason_codes: tuple[str, ...] = ()


def mffu_builder_50k_profile(*, dd_option: BuilderDdOption = "default") -> BuilderProfile:
    """Factory for the MFFU Builder $50k profile with configurable DD tier."""
    if dd_option == "addon":
        return BuilderProfile(
            max_loss=Decimal("1500"),
            payout_buffer=Decimal("1600"),
            dd_option="addon",
        )
    return BuilderProfile(
        max_loss=Decimal("2000"),
        payout_buffer=Decimal("2100"),
        dd_option="default",
    )
