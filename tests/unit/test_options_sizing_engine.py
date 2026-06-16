"""Unit tests for risk-adjusted confidence-based sizing. # [TH]"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from backend.models.options_strategy import NormalizedFeatures, RiskSessionState
from backend.services.options_strategy.portfolio_heat import (
    portfolio_heat_allowed,
    sector_correlation_size_mult,
    sector_heat_allowed,
    symbol_sector,
)
from backend.services.options_strategy.sizing_engine import (
    compute_risk_budget_pct,
    kelly_fraction,
    volatility_regime_scalar,
    vix_proxy_from_features,
)


def _features(**overrides: object) -> NormalizedFeatures:
    base = {
        "symbol": "GOOGL",
        "as_of": datetime(2026, 6, 16, 15, 0, tzinfo=UTC),
        "global_confidence": 0.75,
        "trend_quality_score": 0.8,
        "structure_alignment_score": 0.7,
        "forecast_dispersion_score": 0.2,
        "iv_state": "fair",
        "expected_move_pct": 2.5,
    }
    base.update(overrides)
    return NormalizedFeatures(**base)  # type: ignore[arg-type]


def test_kelly_fraction_positive_with_strong_edge() -> None:
    f = kelly_fraction(_features(), win_rate=0.6, win_loss_ratio=2.0, fractional=0.5)
    assert 0.0 < f <= 0.25


def test_kelly_fraction_zero_when_negative_edge() -> None:
    f = kelly_fraction(_features(), win_rate=0.2, win_loss_ratio=1.0, fractional=0.5)
    assert f == 0.0


def test_compute_risk_budget_pct_scales_with_confidence() -> None:
    low = compute_risk_budget_pct(
        _features(global_confidence=0.2),
        base_pct=0.75,
    )
    high = compute_risk_budget_pct(
        _features(global_confidence=0.9),
        base_pct=0.75,
    )
    assert high >= low


def test_volatility_regime_scalar_high_vix_reduces_size() -> None:
    normal = volatility_regime_scalar(18.0)
    stressed = volatility_regime_scalar(32.0)
    assert normal == 1.0
    assert stressed < normal


def test_vix_proxy_from_iv_state() -> None:
    proxy = vix_proxy_from_features(_features(iv_state="extreme"))
    assert proxy is not None
    assert volatility_regime_scalar(proxy) < 1.0


def test_sector_correlation_penalty() -> None:
    mult = sector_correlation_size_mult("MSFT", ("AAPL", "GOOGL"))
    assert mult < 1.0
    assert symbol_sector("MSFT") == "mega_tech"


def test_portfolio_heat_guard() -> None:
    assert portfolio_heat_allowed(10.0, 1.5, max_total_pct=12.0) is True
    assert portfolio_heat_allowed(11.0, 2.0, max_total_pct=12.0) is False


def test_sector_heat_guard() -> None:
    assert sector_heat_allowed(
        "mega_tech",
        {"mega_tech": 4.0},
        0.5,
        max_sector_pct=5.0,
    )
    assert not sector_heat_allowed(
        "mega_tech",
        {"mega_tech": 4.8},
        0.5,
        max_sector_pct=5.0,
    )


def test_risk_engine_blocks_portfolio_heat(monkeypatch: pytest.MonkeyPatch) -> None:
    from backend.config.options_strategy_loader import load_options_strategy_config
    from backend.models.options_strategy import (
        OptionsExecutionPayload,
        OptionsLegSpec,
        OptionsStructure,
        PlaybookDecision,
        StrategyDecision,
    )
    from backend.services.options_strategy.risk_engine import RiskEngine

    cfg = load_options_strategy_config()
    session = RiskSessionState(total_risk_budget_pct=11.5, open_positions=1)
    decision = PlaybookDecision(
        symbol="GOOGL",
        as_of=datetime(2026, 6, 16, 15, 0, tzinfo=UTC),
        decision=StrategyDecision.EXECUTE,
        playbook_family="trend_continuation",
        recommended_structure=OptionsStructure.LONG_CALL,
        direction="bullish",
        confidence=0.75,
        execution_ready=True,
        risk_budget_pct=1.0,
    )
    payload = OptionsExecutionPayload(
        symbol="GOOGL",
        timestamp=datetime(2026, 6, 16, 15, 0, tzinfo=UTC),
        decision=StrategyDecision.EXECUTE,
        playbook_family="trend_continuation",
        recommended_structure=OptionsStructure.LONG_CALL,
        direction="bullish",
        global_confidence=0.75,
        dte_target=14,
        delta_buy_target=0.38,
        max_premium_usd=__import__("decimal").Decimal("350.00"),
        risk_budget_pct=1.0,
        legs=(OptionsLegSpec(contract_symbol="GOOGL20260627C00180000", side="buy"),),
        dry_run=True,
    )
    result = RiskEngine.evaluate_entry(
        decision,
        payload,
        _features(),
        session=session,
        config=cfg,
    )
    assert result.passed is False
    assert result.veto_code == "portfolio_heat_limit"
