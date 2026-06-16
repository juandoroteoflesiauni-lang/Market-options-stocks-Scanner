from decimal import Decimal

from pydantic_settings import BaseSettings, SettingsConfigDict


class FundingThresholds(BaseSettings):
    """Thresholds configuration for Funding Module 05."""

    model_config = SettingsConfigDict(
        env_prefix="QA_",
        env_file=".env",
        extra="ignore",
    )

    # FTMO profile
    ftmo_profile_id: str = "ftmo_2_step_standard"
    ftmo_initial_capital: Decimal = Decimal("100000.0")
    ftmo_daily_loss_limit_pct: Decimal = Decimal("5.0")
    ftmo_max_loss_limit_pct: Decimal = Decimal("10.0")
    ftmo_base_risk_per_trade_pct: Decimal = Decimal("0.50")

    # Performance Analytics
    pa_min_expectancy_r: Decimal = Decimal("0.20")
    pa_min_profit_factor: float = 1.6
    pa_min_sharpe: float = 1.5
    pa_min_sortino: float = 2.0
    pa_min_calmar: float = 2.0
    pa_max_bur: float = 0.60
    pa_max_ulcer_index: float = 2.0
    pa_max_cvar99_vs_dll: float = 0.60
    pa_max_risk_of_ruin_pct: float = 0.001

    # Kelly
    sizing_kelly_base_fraction: float = 0.25
    sizing_kelly_cap: float = 0.25

    # MFFU Builder Plan ($50k)
    builder_profile_id: str = "MFFU_BUILDER_50K"
    builder_starting_balance: Decimal = Decimal("50000")
    builder_profit_target: Decimal = Decimal("3000")
    builder_daily_loss_amount: Decimal = Decimal("1000")
    builder_max_loss_amount: Decimal = Decimal("2000")
    builder_payout_buffer: Decimal = Decimal("2100")
    builder_consistency_cap: Decimal = Decimal("0.50")
    builder_payout_cap: Decimal = Decimal("2000")
    builder_min_profit_payout: Decimal = Decimal("500")
    builder_max_sim_payouts: int = 5
    builder_live_cooldown_days: int = 21
    builder_inactivity_days: int = 7
    builder_min_trading_days: int = 1
    builder_max_minis: int = 4
    builder_max_micros: int = 40
    builder_base_risk_per_trade_pct: Decimal = Decimal("0.50")
    builder_use_addon_dd: bool = False
    builder_trailing_dd_critical_usd: Decimal = Decimal("200")
    builder_dll_soft_pause_threat_usd: Decimal = Decimal("200")
    builder_phase_factor_eval: Decimal = Decimal("1.0")
    builder_phase_factor_sim: Decimal = Decimal("0.75")
    builder_phase_factor_live: Decimal = Decimal("0.50")
    builder_consistency_penalty_factor: Decimal = Decimal("0.50")
    builder_min_qualified_days_payout: int = 2


def builder_profile_from_thresholds(
    thresholds: FundingThresholds | None = None,
) -> "BuilderProfile":
    """Build a BuilderProfile from environment-backed funding thresholds."""
    from backend.domain.builder_models import BuilderProfile, mffu_builder_50k_profile

    active = thresholds or FundingThresholds()
    if active.builder_use_addon_dd:
        return mffu_builder_50k_profile(dd_option="addon")

    return BuilderProfile(
        profile_id=active.builder_profile_id,
        starting_balance=active.builder_starting_balance,
        profit_target=active.builder_profit_target,
        daily_loss_limit=active.builder_daily_loss_amount,
        max_loss=active.builder_max_loss_amount,
        payout_buffer=active.builder_payout_buffer,
        consistency_cap=active.builder_consistency_cap,
        payout_cap=active.builder_payout_cap,
        min_profit_payout=active.builder_min_profit_payout,
        max_sim_payouts=active.builder_max_sim_payouts,
        live_cooldown_days=active.builder_live_cooldown_days,
        inactivity_days=active.builder_inactivity_days,
        min_trading_days=active.builder_min_trading_days,
        max_minis=active.builder_max_minis,
        max_micros=active.builder_max_micros,
        base_risk_per_trade_pct=active.builder_base_risk_per_trade_pct,
        dd_option="default",
    )
