from decimal import Decimal

import pytest

from backend.config.funding_thresholds import FundingThresholds, builder_profile_from_thresholds
from backend.domain.builder_models import (
    BUILDER_REASON_CODES,
    BUILDER_TRAILING_DD_CRITICAL,
    MFFU_BUILDER_PROFILE_ID,
    BuilderProfile,
    mffu_builder_50k_profile,
)


def test_mffu_builder_50k_default_profile_parameters() -> None:
    profile = mffu_builder_50k_profile()

    assert profile.profile_id == MFFU_BUILDER_PROFILE_ID
    assert profile.starting_balance == Decimal("50000")
    assert profile.profit_target == Decimal("3000")
    assert profile.daily_loss_limit == Decimal("1000")
    assert profile.max_loss == Decimal("2000")
    assert profile.payout_buffer == Decimal("2100")
    assert profile.consistency_cap == Decimal("0.50")
    assert profile.payout_cap == Decimal("2000")
    assert profile.min_profit_payout == Decimal("500")
    assert profile.min_trading_days == 1
    assert profile.max_minis == 4
    assert profile.max_micros == 40
    assert profile.dd_option == "default"


def test_mffu_builder_50k_addon_profile_parameters() -> None:
    profile = mffu_builder_50k_profile(dd_option="addon")

    assert profile.max_loss == Decimal("1500")
    assert profile.payout_buffer == Decimal("1600")
    assert profile.dd_option == "addon"


def test_builder_profile_rejects_mismatched_dd_and_buffer() -> None:
    with pytest.raises(ValueError, match="requires payout buffer"):
        BuilderProfile(max_loss=Decimal("2000"), payout_buffer=Decimal("1600"))


def test_builder_profile_to_funding_rule_preset_uses_trailing_eod() -> None:
    preset = mffu_builder_50k_profile().to_funding_rule_preset()

    assert preset.id == MFFU_BUILDER_PROFILE_ID
    assert preset.drawdown_type == "trailing_eod"
    assert preset.daily_loss_amount == 1000.0
    assert preset.max_loss_amount == 2000.0
    assert preset.max_contracts == 4
    assert preset.lockout_on_daily_breach is False


def test_builder_reason_codes_are_stable_and_unique() -> None:
    assert BUILDER_TRAILING_DD_CRITICAL in BUILDER_REASON_CODES
    assert len(BUILDER_REASON_CODES) == len(set(BUILDER_REASON_CODES))


def test_builder_profile_from_thresholds_default_dd() -> None:
    thresholds = FundingThresholds()
    profile = builder_profile_from_thresholds(thresholds)

    assert profile.max_loss == Decimal("2000")
    assert profile.payout_buffer == Decimal("2100")


def test_builder_profile_from_thresholds_addon_flag() -> None:
    thresholds = FundingThresholds(builder_use_addon_dd=True)
    profile = builder_profile_from_thresholds(thresholds)

    assert profile.max_loss == Decimal("1500")
    assert profile.payout_buffer == Decimal("1600")
