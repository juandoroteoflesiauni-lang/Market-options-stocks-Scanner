"""Fase 6 — integración Alpaca paper trading para Options Strategy. # [TH][IM]"""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path

import pytest

from backend.layer_1_data.datos.alpaca_client import AlpacaClient
from backend.models.options_strategy import (
    OptionsExecutionPayload,
    OptionsLegSpec,
    OptionsStrategyAuditLog,
    OptionsStructure,
    PlaybookDecision,
    StrategyDecision,
)
from backend.services.options_strategy.alpaca_executor import (
    AlpacaOptionsExecutor,
    build_alpaca_options_order,
)
from backend.services.options_strategy.execution_store import OptionsStrategyExecutionStore
from backend.services.options_strategy.pipeline import OptionsStrategyPipeline
from tests.unit.test_options_strategy_phase3 import _make_googl_input


def _execute_payload(*, spread: bool = False) -> OptionsExecutionPayload:
    legs: tuple[OptionsLegSpec, ...]
    structure = OptionsStructure.LONG_CALL
    if spread:
        structure = OptionsStructure.CALL_DEBIT_SPREAD
        legs = (
            OptionsLegSpec(contract_symbol="GOOGL260627C00180000", side="buy"),
            OptionsLegSpec(contract_symbol="GOOGL260627C00190000", side="sell"),
        )
    else:
        legs = (OptionsLegSpec(contract_symbol="GOOGL260627C00180000", side="buy"),)
    return OptionsExecutionPayload(
        symbol="GOOGL",
        timestamp=datetime(2026, 6, 13, 15, 0, tzinfo=UTC),
        decision=StrategyDecision.EXECUTE,
        playbook_family="trend_continuation",
        recommended_structure=structure,
        direction="bullish",
        global_confidence=0.75,
        dte_target=14,
        delta_buy_target=0.38,
        delta_sell_target=0.20 if spread else None,
        max_premium_usd=Decimal("350.00"),
        risk_budget_pct=0.6,
        legs=legs,
        dry_run=True,
        client_order_id="opt-phase6-test",
    )


def test_build_alpaca_options_order_single_leg() -> None:
    order = build_alpaca_options_order(_execute_payload())
    assert len(order.legs) == 1
    assert order.legs[0].symbol == "GOOGL260627C00180000"
    assert order.limit_price == pytest.approx(3.50)


def test_build_alpaca_options_order_mleg_spread() -> None:
    order = build_alpaca_options_order(_execute_payload(spread=True))
    assert len(order.legs) == 2
    assert order.legs[0].side == "buy"
    assert order.legs[1].side == "sell"


def test_build_alpaca_options_order_strips_polygon_prefix() -> None:
    payload = _execute_payload(spread=True).model_copy(
        update={
            "legs": (
                OptionsLegSpec(contract_symbol="O:GOOGL260627C00180000", side="buy"),
                OptionsLegSpec(contract_symbol="O:GOOGL260627C00190000", side="sell"),
            )
        }
    )
    order = build_alpaca_options_order(payload)
    assert order.legs[0].symbol == "GOOGL260627C00180000"
    assert order.legs[1].symbol == "GOOGL260627C00190000"


@pytest.mark.asyncio
async def test_alpaca_executor_dry_run() -> None:
    client = AlpacaClient(api_key="k", secret_key="s", dry_run=True)
    result = await AlpacaOptionsExecutor.execute(_execute_payload(), client)
    assert result.ok is True
    assert result.dry_run is True
    assert result.venue_order_id is None
    assert "execution_dry_run" in result.reason_codes


@pytest.mark.asyncio
async def test_alpaca_executor_skips_no_trade() -> None:
    payload = _execute_payload().model_copy(update={"decision": StrategyDecision.NO_TRADE})
    client = AlpacaClient(api_key="k", secret_key="s", dry_run=True)
    result = await AlpacaOptionsExecutor.execute(payload, client)
    assert result.ok is False
    assert "execution_skipped_not_execute" in result.reason_codes


@pytest.mark.asyncio
async def test_pipeline_run_execute_dry_persists(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    payload = _execute_payload()
    inp = _make_googl_input()
    fake_log = OptionsStrategyAuditLog(
        input=inp,
        playbook_decision=PlaybookDecision(
            symbol="GOOGL",
            as_of=inp.as_of,
            decision=StrategyDecision.EXECUTE,
            playbook_family="trend_continuation",
            recommended_structure=OptionsStructure.LONG_CALL,
            direction="bullish",
            confidence=0.75,
            execution_ready=True,
            risk_budget_pct=0.6,
        ),
        execution_payload=payload,
        config_version="phase6-mvp",
        pipeline_phase="phase5-risk-audit",
    )
    monkeypatch.setattr(
        OptionsStrategyPipeline,
        "run_dry",
        classmethod(lambda cls, *args, **kwargs: fake_log),
    )
    result = await OptionsStrategyPipeline.run(
        inp,
        execute=True,
        persist=True,
        audit_db_path=tmp_path / "phase6.sqlite3",
        client=AlpacaClient(api_key="k", secret_key="s", dry_run=True),
    )
    assert result.execution is not None
    assert result.execution.ok is True
    assert result.audit_log.pipeline_phase == "phase6-alpaca-execution"
    rows = OptionsStrategyExecutionStore(db_path=tmp_path / "phase6.sqlite3").list_by_audit(
        result.audit_log.audit_id
    )
    assert len(rows) == 1


@pytest.mark.asyncio
async def test_place_options_order_dry_run_via_client() -> None:
    from backend.layer_1_data.datos.alpaca_client import (
        AlpacaOptionsLegRequest,
        AlpacaOptionsOrderRequest,
    )

    client = AlpacaClient(api_key="k", secret_key="s", dry_run=True)
    order = AlpacaOptionsOrderRequest(
        underlying="GOOGL",
        legs=(AlpacaOptionsLegRequest(symbol="GOOGL260627C00180000", side="buy"),),
        order_type="limit",
        limit_price=3.5,
    )
    response = await client.place_options_order(order)
    assert response.ok is True
    assert response.dry_run is True
