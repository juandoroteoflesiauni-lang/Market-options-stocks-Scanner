"""Fase 8 — runtime calibrado + signal loop + outcomes/PnL. # [TH][IM]"""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path

import numpy as np
import pytest

from backend.config.options_strategy_loader import (
    get_options_strategy_config,
    load_options_strategy_config,
)
from backend.models.options_strategy import (
    NormalizedFeatures,
    OptionsStrategyAuditLog,
    OptionsStrategyInput,
    OptionsStructure,
    OptionsTradeOutcome,
    PlaybookDecision,
    StrategyDecision,
)
from backend.services.options_strategy.audit_store import OptionsStrategyAuditStore
from backend.services.options_strategy.calibration_loop import OptionsStrategyCalibrationLoop
from backend.services.options_strategy.outcome_store import OptionsStrategyOutcomeStore
from backend.services.options_strategy.signal_loop import OptionsStrategySignalLoop


def _make_audit_log(*, execute: bool, idx: int, symbol: str = "GOOGL") -> OptionsStrategyAuditLog:
    as_of = datetime(2026, 6, 1, 15, idx % 60, tzinfo=UTC)
    bias = 0.2 + (idx % 10) * 0.05
    return OptionsStrategyAuditLog(
        input=OptionsStrategyInput(symbol=symbol, as_of=as_of),
        features=NormalizedFeatures(
            symbol=symbol,
            as_of=as_of,
            technical_direction_bias=bias,
            predictive_direction_bias=bias * 0.9,
            options_direction_bias=bias * 0.8,
            global_bias=bias,
            global_confidence=0.5 + (idx % 5) * 0.05,
        ),
        playbook_decision=PlaybookDecision(
            symbol=symbol,
            as_of=as_of,
            decision=StrategyDecision.EXECUTE if execute else StrategyDecision.NO_TRADE,
            playbook_family="trend_continuation" if execute else None,
            direction="bullish",
            confidence=0.7 if execute else 0.2,
            execution_ready=execute,
            veto_triggered=None if execute else "chain_liquidity_poor",
        ),
        config_version="phase6-mvp",
        pipeline_phase="phase5-risk-audit",
    )


# --- Feature 3: outcomes/PnL store ---------------------------------------


def test_outcome_store_persist_and_win_map(tmp_path: Path) -> None:
    db_path = tmp_path / "audit.sqlite3"
    store = OptionsStrategyOutcomeStore(db_path=db_path)
    win = OptionsTradeOutcome(
        audit_id="aud-1",
        symbol="GOOGL",
        structure=OptionsStructure.LONG_CALL,
        status="win",
        realized_pnl_usd=Decimal("120.50"),
        entry_premium_usd=Decimal("300.00"),
        return_pct=0.40,
        closed_at=datetime(2026, 6, 2, 20, 0, tzinfo=UTC),
    )
    loss = win.model_copy(
        update={
            "audit_id": "aud-2",
            "status": "loss",
            "realized_pnl_usd": Decimal("-150.00"),
        }
    )
    store.persist(win)
    store.persist(loss)

    win_map = store.load_win_map()
    assert win_map == {"aud-1": True, "aud-2": False}
    assert store.get("aud-1").is_win() is True
    assert store.get("aud-2").is_win() is False


def test_outcome_open_status_excluded_from_win_map(tmp_path: Path) -> None:
    db_path = tmp_path / "audit.sqlite3"
    store = OptionsStrategyOutcomeStore(db_path=db_path)
    store.persist(
        OptionsTradeOutcome(
            audit_id="aud-open",
            symbol="AAPL",
            structure=OptionsStructure.LONG_PUT,
            status="open",
            realized_pnl_usd=Decimal("0"),
            entry_premium_usd=Decimal("200.00"),
        )
    )
    assert store.load_win_map() == {}


def test_outcome_rejects_non_route1_symbol() -> None:
    with pytest.raises(ValueError, match="route1"):
        OptionsTradeOutcome(
            audit_id="x",
            symbol="XYZ",
            structure=OptionsStructure.LONG_CALL,
            status="win",
            realized_pnl_usd=Decimal("10"),
            entry_premium_usd=Decimal("100"),
        )


# --- Feature 3: calibración con outcomes reales --------------------------


def test_calibration_uses_realized_outcomes(tmp_path: Path) -> None:
    db_path = tmp_path / "audit.sqlite3"
    audit_store = OptionsStrategyAuditStore(db_path=db_path)
    outcome_store = OptionsStrategyOutcomeStore(db_path=db_path)
    rng = np.random.default_rng(7)

    for idx in range(40):
        log = _make_audit_log(execute=True, idx=idx)
        audit_store.persist(log)
        win = bool(rng.random() > 0.4)
        outcome_store.persist(
            OptionsTradeOutcome(
                audit_id=log.audit_id,
                symbol="GOOGL",
                structure=OptionsStructure.LONG_CALL,
                status="win" if win else "loss",
                realized_pnl_usd=Decimal("100") if win else Decimal("-80"),
                entry_premium_usd=Decimal("250"),
            )
        )

    report = OptionsStrategyCalibrationLoop.run(
        audit_store=audit_store,
        outcome_store=outcome_store,
        use_outcomes=True,
        limit=100,
    )
    assert report.observation_count >= 30
    assert "calibrated_from_realized_outcomes" in report.recommendations
    assert abs(sum(report.suggested_weights.values()) - 1.0) < 0.01


def test_calibration_outcomes_insufficient(tmp_path: Path) -> None:
    db_path = tmp_path / "audit.sqlite3"
    audit_store = OptionsStrategyAuditStore(db_path=db_path)
    outcome_store = OptionsStrategyOutcomeStore(db_path=db_path)
    for idx in range(40):
        log = _make_audit_log(execute=True, idx=idx)
        audit_store.persist(log)
    # Solo 2 outcomes -> insuficiente para target outcome-driven.
    for idx in range(2):
        log = _make_audit_log(execute=True, idx=idx)
        audit_store.persist(log)
        outcome_store.persist(
            OptionsTradeOutcome(
                audit_id=log.audit_id,
                symbol="GOOGL",
                structure=OptionsStructure.LONG_CALL,
                status="win",
                realized_pnl_usd=Decimal("50"),
                entry_premium_usd=Decimal("100"),
            )
        )
    report = OptionsStrategyCalibrationLoop.run(
        audit_store=audit_store,
        outcome_store=outcome_store,
        use_outcomes=True,
        limit=100,
    )
    assert report.limitations
    assert "insufficient" in report.limitations[0]


# --- Feature 2: signal loop ----------------------------------------------


def test_signal_loop_scan_once_summarizes(tmp_path: Path, monkeypatch) -> None:
    calls: list[str] = []

    def _fake_run_dry(inp, *, config=None, session=None, persist=False, audit_db_path=None):
        calls.append(inp.symbol)
        execute = inp.symbol == "GOOGL"
        return _make_audit_log(execute=execute, idx=len(calls), symbol=inp.symbol)

    monkeypatch.setattr(
        "backend.services.options_strategy.signal_loop.OptionsStrategyPipeline.run_dry",
        _fake_run_dry,
    )
    report = OptionsStrategySignalLoop.scan_once(symbols=("GOOGL", "AAPL", "MSFT"))
    assert report.scanned == 3
    assert report.execute_count == 1
    assert report.no_trade_count == 2
    assert report.error_count == 0
    assert {e.symbol for e in report.entries} == {"GOOGL", "AAPL", "MSFT"}
    payload = report.as_dict()
    assert payload["scanned"] == 3
    assert len(payload["entries"]) == 3


def test_signal_loop_isolates_symbol_errors(monkeypatch) -> None:
    def _boom(inp, **kwargs):
        if inp.symbol == "TSLA":
            raise RuntimeError("layer_failure")
        return _make_audit_log(execute=False, idx=1, symbol=inp.symbol)

    monkeypatch.setattr(
        "backend.services.options_strategy.signal_loop.OptionsStrategyPipeline.run_dry",
        _boom,
    )
    report = OptionsStrategySignalLoop.scan_once(symbols=("AAPL", "TSLA"))
    assert report.scanned == 2
    assert report.error_count == 1
    assert report.errors[0][0] == "TSLA"


# --- Feature 1: runtime calibrado vía env --------------------------------


def test_runtime_calibrated_config_via_env(tmp_path: Path, monkeypatch) -> None:
    import yaml

    base = load_options_strategy_config()
    calibrated = tmp_path / "omni_engine_calibrated.yaml"
    calibrated.write_text(
        yaml.safe_dump(
            {
                "omni_engine": {
                    "weights": {"technical": 0.5, "predictive": 0.25, "options": 0.25},
                    "min_global_confidence": 0.72,
                }
            }
        ),
        encoding="utf-8",
    )

    monkeypatch.setenv("OPTIONS_STRATEGY_USE_CALIBRATED", "1")
    monkeypatch.setenv("OPTIONS_STRATEGY_CALIBRATED_PATH", str(calibrated))
    get_options_strategy_config.cache_clear()
    try:
        bundle = get_options_strategy_config()
        assert bundle.omni_engine.weights == {
            "technical": 0.5,
            "predictive": 0.25,
            "options": 0.25,
        }
        assert bundle.omni_engine.min_global_confidence == 0.72
        # No debe alterar el universo R1.
        assert bundle.universe.enforce_route1_only == base.universe.enforce_route1_only
    finally:
        get_options_strategy_config.cache_clear()


def test_runtime_calibrated_missing_file_falls_back(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("OPTIONS_STRATEGY_USE_CALIBRATED", "1")
    monkeypatch.setenv(
        "OPTIONS_STRATEGY_CALIBRATED_PATH", str(tmp_path / "nope.yaml")
    )
    get_options_strategy_config.cache_clear()
    try:
        bundle = get_options_strategy_config()
        assert bundle.omni_engine.weights  # cae a base sin romper
    finally:
        get_options_strategy_config.cache_clear()
