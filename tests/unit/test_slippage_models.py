"""Tests for VolumeShareSlippageModel."""

from __future__ import annotations

from decimal import Decimal

from backend.backtesting.slippage_models import VolumeShareSlippageModel


def test_slippage_monotonic_in_size() -> None:
    low = VolumeShareSlippageModel.estimate(Decimal("1"), 1000, Decimal("10"), 0.2)
    high = VolumeShareSlippageModel.estimate(Decimal("50"), 1000, Decimal("10"), 0.2)
    assert high.slippage >= low.slippage


def test_zero_volume_guarded() -> None:
    est = VolumeShareSlippageModel.estimate(Decimal("5"), 0, Decimal("10"), 0.3)
    assert est.slippage >= Decimal("0")


def test_higher_iv_higher_slippage() -> None:
    low_iv = VolumeShareSlippageModel.estimate(Decimal("10"), 500, Decimal("5"), 0.1)
    high_iv = VolumeShareSlippageModel.estimate(Decimal("10"), 500, Decimal("5"), 0.5)
    assert high_iv.slippage >= low_iv.slippage
