"""Tests Fase C — modo profit, rolling PF gate, Kelly fraccional."""

from __future__ import annotations

import pytest

from backend.config.profit_calibration import ProfitCalibrationPolicy, profit_calibration_env_flags
from backend.services.calibration.kelly_session_sizer import compute_fractional_kelly
from backend.services.calibration.rolling_pf_gate import (
    REASON_ROLLING_PF_LOW,
    _profit_factor_from_pnls,
    evaluate_rolling_pf_gate,
)


def test_profit_factor_from_pnls() -> None:
    assert _profit_factor_from_pnls([100.0, -50.0, 80.0, -20.0]) == pytest.approx(2.5714, rel=1e-3)


def test_rolling_pf_blocks_in_profit_mode_when_below_floor(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("BOT_SESSION_MODE", "profit")
    monkeypatch.setenv("PROFIT_ROLLING_PF_MIN_SAMPLE", "3")
    monkeypatch.setenv("PROFIT_ROLLING_PF_MIN", "1.5")

    def fake_list_trades(_path, limit=100):
        return [
            {"realized_pnl": -10.0, "route": "ALPACA"},
            {"realized_pnl": -5.0, "route": "ALPACA"},
            {"realized_pnl": 3.0, "route": "ALPACA"},
            {"realized_pnl": 2.0, "route": "ALPACA"},
        ]

    monkeypatch.setattr(
        "backend.services.trade_journal_service.list_trades",
        fake_list_trades,
    )
    monkeypatch.setattr("pathlib.Path.exists", lambda self: True)

    policy = ProfitCalibrationPolicy.from_env()
    verdict = evaluate_rolling_pf_gate(route="ALPACA", policy=policy)
    assert not verdict.allowed
    assert verdict.reason_code == REASON_ROLLING_PF_LOW


def test_rolling_pf_allows_insufficient_sample(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("BOT_SESSION_MODE", "profit")
    monkeypatch.setenv("PROFIT_ROLLING_PF_MIN_SAMPLE", "20")

    def fake_list_trades(_path, limit=100):
        return [{"realized_pnl": -50.0, "route": "BINGX"}]

    monkeypatch.setattr(
        "backend.services.trade_journal_service.list_trades",
        fake_list_trades,
    )
    monkeypatch.setattr("pathlib.Path.exists", lambda self: True)

    verdict = evaluate_rolling_pf_gate(route="BINGX")
    assert verdict.allowed


def test_fractional_kelly_positive_edge() -> None:
    pnls = [10.0, 8.0, -5.0, 12.0, -4.0, 6.0]
    kelly = compute_fractional_kelly(pnls, fraction=0.25)
    assert kelly > 0.0


def test_profit_calibration_env_flags_strict() -> None:
    flags = profit_calibration_env_flags()
    assert flags["BOT_SESSION_MODE"] == "profit"
    assert float(flags["ALPACA_PROB_FLOOR"]) >= 0.55
    assert float(flags["PROFIT_ROLLING_PF_MIN"]) >= 1.1
    assert flags["PROFIT_KELLY_SIZING_ENABLED"] == "true"
