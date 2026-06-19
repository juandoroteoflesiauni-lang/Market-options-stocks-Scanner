"""Tests F6-F10 audit fixes. # [PD-6][TH]"""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal

import pytest

from backend.models.options_strategy import OptionsStructure, SelectedOptionContract
from backend.services.meta_learner_promotion import (
    promotion_skip_reason,
    should_promote_meta_learner_to_router,
)
from backend.services.options_strategy.alpaca_executor import build_alpaca_options_order
from backend.services.options_strategy.limit_price import (
    compute_limit_price_per_contract,
    validate_options_execution_ready,
)
from backend.services.trade_journal_eod import summarize_trade_journal_today


def test_meta_learner_blocks_synthetic_promotion() -> None:
    metrics = {"source": "synthetic_yfinance", "mean_accuracy": 0.55}
    assert should_promote_meta_learner_to_router(metrics) is False
    assert promotion_skip_reason(metrics) == "synthetic_source_blocked"


def test_meta_learner_allows_real_promotion() -> None:
    metrics = {"source": "prediction_logger", "mean_accuracy": 0.61}
    assert should_promote_meta_learner_to_router(metrics) is True
    assert promotion_skip_reason(metrics) is None


def test_meta_learner_synthetic_allowed_when_flag_on(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("META_LEARNER_PROMOTE_SYNTHETIC", "true")
    metrics = {"source": "synthetic_yfinance", "mean_accuracy": 0.5}
    assert should_promote_meta_learner_to_router(metrics) is True


def test_trade_journal_eod_summary_maps_real_columns() -> None:
    today = datetime.now(tz=UTC).date().isoformat()
    trades = [
        {
            "execution_timestamp": f"{today}T15:00:00+00:00",
            "symbol": "META-USDT",
            "notional_usdt": 500.0,
            "realized_pnl": -12.5,
            "dry_run": False,
        }
    ]
    summary = summarize_trade_journal_today(trades)
    assert summary["trades_today"] == 1
    assert summary["realized_pnl_usdt_today"] == -12.5
    assert summary["notional_usdt_today"] == 500.0
    assert summary["symbols"] == ["META-USDT"]


def _leg(
    *,
    side: str,
    mark: float,
    symbol: str = "GOOGL260627C00180000",
) -> SelectedOptionContract:
    return SelectedOptionContract(
        underlying="GOOGL",
        expiry=datetime(2026, 6, 27, tzinfo=UTC).date(),
        strike=180.0,
        right="call",
        side=side,  # type: ignore[arg-type]
        mark=mark,
        contract_symbol=symbol,
    )


def test_compute_debit_spread_limit_price() -> None:
    legs = (
        _leg(side="long", mark=4.20, symbol="GOOGL260627C00180000"),
        _leg(side="short", mark=2.10, symbol="GOOGL260627C00190000"),
    )
    limit = compute_limit_price_per_contract(
        legs,
        structure=OptionsStructure.CALL_DEBIT_SPREAD,
    )
    assert limit == Decimal("2.10")


def test_validate_spread_requires_two_legs() -> None:
    reason = validate_options_execution_ready(
        OptionsStructure.CALL_DEBIT_SPREAD,
        (_leg(side="long", mark=3.0),),
        limit_price=Decimal("3.00"),
    )
    assert reason == "missing_spread_legs"


def test_limit_price_per_contract_used_in_alpaca_order() -> None:
    from backend.models.options_strategy import (
        OptionsExecutionPayload,
        OptionsLegSpec,
        StrategyDecision,
    )

    payload = OptionsExecutionPayload(
        symbol="GOOGL",
        timestamp=datetime(2026, 6, 13, 15, 0, tzinfo=UTC),
        decision=StrategyDecision.EXECUTE,
        playbook_family="trend",
        recommended_structure=OptionsStructure.LONG_CALL,
        direction="bullish",
        global_confidence=0.7,
        dte_target=14,
        delta_buy_target=0.38,
        max_premium_usd=Decimal("999.00"),
        limit_price_per_contract=Decimal("4.25"),
        risk_budget_pct=0.5,
        legs=(OptionsLegSpec(contract_symbol="GOOGL260627C00180000", side="buy"),),
        dry_run=True,
    )
    order = build_alpaca_options_order(payload)
    assert order.limit_price == pytest.approx(4.25)
