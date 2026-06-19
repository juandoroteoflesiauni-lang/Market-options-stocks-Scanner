"""Tests Fase B — política de ejecución, collar, repeated limit, algo routing."""

from __future__ import annotations

import pytest

from backend.config.execution_policy import ExecutionPolicy, execution_phase_b_env_flags
from backend.domain.alpaca_models import EquityOrderIntent, EquityRiskDecision
from backend.services.alpaca_pre_trade_risk_gate import PreTradeRiskGate
from backend.services.execution.algo_routing import should_use_alpaca_elite, should_use_bingx_twap
from backend.services.execution.price_collar import REASON_PRICE_COLLAR, evaluate_price_collar
from backend.services.execution.repeated_execution_guard import (
    REASON_REPEATED_EXECUTION,
    SessionRepeatedExecutionGuard,
)


@pytest.fixture(autouse=True)
def _reset_guards() -> None:
    SessionRepeatedExecutionGuard.reset_instance()
    PreTradeRiskGate.reset_instance()
    yield
    SessionRepeatedExecutionGuard.reset_instance()
    PreTradeRiskGate.reset_instance()


def test_price_collar_blocks_wide_deviation() -> None:
    verdict = evaluate_price_collar(
        reference_price=100.0,
        order_price=101.5,
        max_deviation_pct=0.0075,
        enabled=True,
    )
    assert not verdict.allowed
    assert verdict.reason_code == REASON_PRICE_COLLAR


def test_price_collar_allows_exits() -> None:
    verdict = evaluate_price_collar(
        reference_price=100.0,
        order_price=105.0,
        max_deviation_pct=0.0075,
        enabled=True,
        is_exit=True,
    )
    assert verdict.allowed


def test_bingx_twap_by_notional_threshold() -> None:
    policy = ExecutionPolicy(bingx_twap_enabled=True, bingx_twap_min_notional_usdt=400.0)
    assert should_use_bingx_twap(
        policy=policy,
        notional_usdt=500.0,
        reduce_only=False,
        lob_dynamics_trigger=False,
    )
    assert not should_use_bingx_twap(
        policy=policy,
        notional_usdt=200.0,
        reduce_only=False,
        lob_dynamics_trigger=False,
    )


def test_alpaca_elite_by_notional() -> None:
    policy = ExecutionPolicy(alpaca_elite_enabled=True, alpaca_elite_min_notional_usd=1_500.0)
    assert should_use_alpaca_elite(policy=policy, notional_usd=2_000.0)
    assert not should_use_alpaca_elite(policy=policy, notional_usd=1_000.0)


def test_repeated_execution_guard_blocks_after_max() -> None:
    guard = SessionRepeatedExecutionGuard.instance()
    for _ in range(3):
        guard.record_entry_fill("AAPL")
    assert guard.can_execute_entry("AAPL", max_per_symbol=3) is False
    assert guard.can_execute_entry("MSFT", max_per_symbol=3) is True


def test_pre_trade_gate_repeated_execution(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("EXECUTION_REPEATED_LIMIT_ENABLED", "true")
    monkeypatch.setenv("EXECUTION_REPEATED_MAX_PER_SYMBOL", "2")
    monkeypatch.setenv("EXECUTION_PRICE_COLLAR_ENABLED", "false")
    gate = PreTradeRiskGate()
    intent = EquityOrderIntent(
        symbol="NVDA",
        route="scan",
        quantity=10,
        reference_price=100.0,
        notional_usd=1000.0,
        client_order_id="test-1",
        cycle_id="c1",
    )
    decision = EquityRiskDecision(
        authorized=True,
        intent=intent,
        idempotency_key="idem-1",
        reason_codes=(),
    )
    gate.record_entry_fill("NVDA")
    gate.record_entry_fill("NVDA")
    verdict = gate.evaluate(decision)
    assert not verdict.allowed
    assert REASON_REPEATED_EXECUTION in verdict.reason_codes


def test_execution_phase_b_env_flags_enable_twap_and_elite() -> None:
    flags = execution_phase_b_env_flags()
    assert flags["BINGX_TWAP_SLIVERING_ENABLED"] == "true"
    assert flags["ALPACA_ELITE_SMART_ROUTER"] == "true"
    assert float(flags["EXECUTION_BINGX_TWAP_MIN_NOTIONAL_USDT"]) == 400.0
