"""Fase 5 — risk engine, exit manager y auditoría persistente. # [TH][IM]"""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path

from backend.config.options_strategy_loader import load_options_strategy_config
from backend.models.options_strategy import (
    NormalizedFeatures,
    OpenOptionsPosition,
    OptionsExecutionPayload,
    OptionsLegSpec,
    OptionsStructure,
    PlaybookDecision,
    RiskSessionState,
    StrategyDecision,
)
from backend.services.options_strategy.audit_store import OptionsStrategyAuditStore
from backend.services.options_strategy.exit_manager import ExitManager
from backend.services.options_strategy.pipeline import OptionsStrategyPipeline
from backend.services.options_strategy.risk_engine import RiskEngine
from tests.unit.test_options_strategy_phase3 import _make_googl_input
from tests.unit.test_options_strategy_phase4 import (
    _bullish_candidate,
    _relaxed_config,
    _strong_features,
)
from backend.services.options_strategy.fusion_router import FusionRouter, fuse_features


def _execute_decision() -> PlaybookDecision:
    return PlaybookDecision(
        symbol="GOOGL",
        as_of=datetime(2026, 6, 13, 15, 0, tzinfo=UTC),
        decision=StrategyDecision.EXECUTE,
        playbook_family="trend_continuation",
        recommended_structure=OptionsStructure.LONG_CALL,
        direction="bullish",
        confidence=0.75,
        execution_ready=True,
        risk_budget_pct=0.6,
    )


def _execute_payload() -> OptionsExecutionPayload:
    return OptionsExecutionPayload(
        symbol="GOOGL",
        timestamp=datetime(2026, 6, 13, 15, 0, tzinfo=UTC),
        decision=StrategyDecision.EXECUTE,
        playbook_family="trend_continuation",
        recommended_structure=OptionsStructure.LONG_CALL,
        direction="bullish",
        global_confidence=0.75,
        dte_target=14,
        delta_buy_target=0.38,
        max_premium_usd=Decimal("350.00"),
        risk_budget_pct=0.6,
        legs=(OptionsLegSpec(contract_symbol="GOOGL20260627C00180000", side="buy"),),
        dry_run=True,
    )


def test_risk_engine_blocks_max_open_positions() -> None:
    cfg = load_options_strategy_config()
    session = RiskSessionState(open_positions=cfg.risk.max_open_positions)
    result = RiskEngine.evaluate_entry(
        _execute_decision(),
        _execute_payload(),
        _strong_features(),
        session=session,
        config=cfg,
    )
    assert result.passed is False
    assert result.veto_code == "max_open_positions"


def test_risk_engine_reduces_size_on_high_dispersion() -> None:
    cfg = load_options_strategy_config()
    features = _strong_features().model_copy(update={"forecast_dispersion_score": 0.80})
    result = RiskEngine.evaluate_entry(
        _execute_decision(),
        _execute_payload(),
        features,
        config=cfg,
    )
    assert result.passed is True
    assert result.size_multiplier < 1.0
    assert "dispersion_size_reduction" in result.reason_codes


def test_risk_engine_apply_to_payload_scales_premium() -> None:
    payload = _execute_payload()
    evaluation = RiskEngine.evaluate_entry(
        _execute_decision(),
        payload,
        _strong_features().model_copy(update={"forecast_dispersion_score": 0.50}),
        config=load_options_strategy_config(),
    )
    scaled = RiskEngine.apply_to_payload(payload, evaluation)
    assert scaled.max_premium_usd < payload.max_premium_usd


def test_exit_manager_premium_stop() -> None:
    cfg = load_options_strategy_config()
    position = OpenOptionsPosition(
        symbol="GOOGL",
        playbook_family="trend_continuation",
        structure=OptionsStructure.LONG_CALL,
        direction="bullish",
        entry_premium_usd=Decimal("400.00"),
        current_premium_usd=Decimal("150.00"),
        dte=10,
        opened_at=datetime(2026, 6, 1, 15, 0, tzinfo=UTC),
    )
    result = ExitManager.evaluate(position, _strong_features(), config=cfg)
    assert result.decision == StrategyDecision.EXIT
    assert "premium_stop_loss" in result.reason_codes


def test_exit_manager_time_stop() -> None:
    cfg = load_options_strategy_config()
    position = OpenOptionsPosition(
        symbol="GOOGL",
        playbook_family="trend_continuation",
        structure=OptionsStructure.LONG_CALL,
        direction="bullish",
        entry_premium_usd=Decimal("400.00"),
        current_premium_usd=Decimal("380.00"),
        dte=2,
        opened_at=datetime(2026, 6, 1, 15, 0, tzinfo=UTC),
    )
    result = ExitManager.evaluate(position, _strong_features(), config=cfg)
    assert result.decision == StrategyDecision.EXIT
    assert "time_stop_dte" in result.reason_codes


def test_exit_manager_thesis_flip() -> None:
    cfg = load_options_strategy_config()
    position = OpenOptionsPosition(
        symbol="GOOGL",
        playbook_family="trend_continuation",
        structure=OptionsStructure.LONG_CALL,
        direction="bullish",
        entry_premium_usd=Decimal("400.00"),
        current_premium_usd=Decimal("390.00"),
        dte=10,
        opened_at=datetime(2026, 6, 1, 15, 0, tzinfo=UTC),
    )
    features = _strong_features().model_copy(update={"global_bias": -0.40})
    result = ExitManager.evaluate(position, features, config=cfg)
    assert result.decision == StrategyDecision.EXIT
    assert "thesis_bias_flip" in result.reason_codes


def test_audit_store_persist_and_list(tmp_path: Path) -> None:
    db_path = tmp_path / "audit.sqlite3"
    store = OptionsStrategyAuditStore(db_path=db_path)
    cfg = _relaxed_config()
    log = OptionsStrategyPipeline.run_dry(_make_googl_input(), config=cfg)
    result = store.persist(log)
    assert result.inserted is True
    assert result.audit_id == log.audit_id
    rows = store.list_recent(symbol="GOOGL", limit=5)
    assert len(rows) == 1
    assert rows[0]["decision"] in {"EXECUTE", "NO_TRADE"}
    loaded = store.get(log.audit_id)
    assert loaded is not None
    assert loaded["playbook_decision"]["symbol"] == "GOOGL"


def test_pipeline_blocks_on_risk_session(tmp_path: Path) -> None:
    cfg = _relaxed_config()
    session = RiskSessionState(open_positions=cfg.risk.max_open_positions)
    log = OptionsStrategyPipeline.run_dry(
        _make_googl_input(),
        config=cfg,
        session=session,
        persist=True,
        audit_db_path=tmp_path / "blocked.sqlite3",
    )
    assert log.playbook_decision.decision == StrategyDecision.NO_TRADE
    assert log.playbook_decision.veto_triggered == "max_open_positions"


def test_fusion_plus_risk_execute_path() -> None:
    cfg = _relaxed_config()
    inp = _make_googl_input()
    features = fuse_features(_strong_features(), config=cfg)
    candidate = _bullish_candidate()
    decision, payload = FusionRouter.decide(inp, features, candidate, config=cfg)
    risk = RiskEngine.evaluate_entry(decision, payload, features, config=cfg)
    assert decision.decision == StrategyDecision.EXECUTE
    assert risk.passed is True
    assert payload is not None
