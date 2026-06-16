"""Fase 7 — calibration loop offline Options Strategy. # [TH][IM]"""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import numpy as np

from backend.config.options_strategy_loader import load_options_strategy_config
from backend.models.options_strategy import (
    NormalizedFeatures,
    OptionsStrategyAuditLog,
    OptionsStrategyInput,
    PlaybookDecision,
    StrategyDecision,
)
from backend.services.options_strategy.audit_store import OptionsStrategyAuditStore
from backend.services.options_strategy.calibration_loop import (
    OptionsStrategyCalibrationLoop,
    load_calibrated_config_bundle,
)
from backend.services.options_strategy.calibration_store import OptionsStrategyCalibrationStore


def _make_audit_log(*, execute: bool, idx: int) -> OptionsStrategyAuditLog:
    as_of = datetime(2026, 6, 1, 15, idx % 60, tzinfo=UTC)
    bias = 0.2 + (idx % 10) * 0.05
    return OptionsStrategyAuditLog(
        input=OptionsStrategyInput(symbol="GOOGL", as_of=as_of),
        features=NormalizedFeatures(
            symbol="GOOGL",
            as_of=as_of,
            technical_direction_bias=bias,
            predictive_direction_bias=bias * 0.9,
            options_direction_bias=bias * 0.8,
            global_bias=bias,
            global_confidence=0.5 + (idx % 5) * 0.05,
        ),
        playbook_decision=PlaybookDecision(
            symbol="GOOGL",
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


def _seed_audit_db(db_path: Path, *, count: int = 40) -> None:
    store = OptionsStrategyAuditStore(db_path=db_path)
    rng = np.random.default_rng(42)
    for idx in range(count):
        execute = bool(rng.random() > 0.65)
        store.persist(_make_audit_log(execute=execute, idx=idx))


def test_calibration_loop_insufficient_observations(tmp_path: Path) -> None:
    db_path = tmp_path / "audit.sqlite3"
    store = OptionsStrategyAuditStore(db_path=db_path)
    store.persist(_make_audit_log(execute=True, idx=1))
    report = OptionsStrategyCalibrationLoop.run(audit_store=store, limit=10)
    assert report.observation_count == 1
    assert "insufficient_observations" in report.limitations[0]


def test_calibration_loop_produces_suggested_weights(tmp_path: Path) -> None:
    db_path = tmp_path / "audit.sqlite3"
    _seed_audit_db(db_path, count=45)
    report = OptionsStrategyCalibrationLoop.run(
        audit_store=OptionsStrategyAuditStore(db_path=db_path),
        limit=100,
    )
    assert report.observation_count >= 40
    assert report.suggested_weights
    assert abs(sum(report.suggested_weights.values()) - 1.0) < 0.01
    assert report.playbook_stats
    assert "technical" in report.suggested_weights


def test_calibration_store_persist_and_latest(tmp_path: Path) -> None:
    db_path = tmp_path / "audit.sqlite3"
    _seed_audit_db(db_path, count=40)
    report = OptionsStrategyCalibrationLoop.run(
        audit_store=OptionsStrategyAuditStore(db_path=db_path),
    )
    result = OptionsStrategyCalibrationStore(db_path=db_path).persist(report)
    assert result.inserted is True
    latest = OptionsStrategyCalibrationStore(db_path=db_path).latest()
    assert latest is not None
    assert latest["calibration_id"] == report.calibration_id


def test_write_calibrated_config_and_load(tmp_path: Path) -> None:
    db_path = tmp_path / "audit.sqlite3"
    _seed_audit_db(db_path, count=40)
    report = OptionsStrategyCalibrationLoop.run(
        audit_store=OptionsStrategyAuditStore(db_path=db_path),
    )
    out = tmp_path / "omni_engine_calibrated.yaml"
    OptionsStrategyCalibrationLoop.write_calibrated_config(report, out)
    assert out.exists()
    bundle = load_calibrated_config_bundle(out, base_config=load_options_strategy_config())
    assert bundle.omni_engine.weights == report.suggested_weights
    assert (
        bundle.omni_engine.min_global_confidence
        == report.suggested_min_global_confidence
    )
