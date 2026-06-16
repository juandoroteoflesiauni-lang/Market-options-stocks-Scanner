"""Tests calibración C5 R1 opciones. # [TH][IM]"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from backend.domain.alpaca_r1_calibration_models import (
    R1FamilyWeights,
    R1OptionsCalibrationMetrics,
    R1OptionsCalibrationResult,
)
from backend.services.alpaca_r1_options_calibration_service import (
    extract_proxy_engine_signals,
    grid_search_family_weights,
    persist_calibration_result,
    run_r1_options_calibration,
)
from backend.services.alpaca_r1_options_confluence import OptionsConfluenceScorer


def _sample_signals():
    return extract_proxy_engine_signals(
        {
            "shadow_delta_signal": 0.4,
            "composite_directional_signal": 0.3,
            "gamma_flip_directional_signal": 0.2,
            "ndde_signal": 120.0,
        },
        {"engine_signal": {"total_gex": 500_000.0}, "spot": 100.0},
    )


def test_extract_proxy_engine_signals_returns_eight() -> None:
    signals = _sample_signals()
    assert len(signals) == 8
    assert all(0.0 <= s.score <= 1.0 for s in signals)


def test_score_with_weights_respects_family_weights() -> None:
    signals = _sample_signals()
    bullish = OptionsConfluenceScorer.score_with_weights(
        signals,
        family_weights={"momentum": 0.7, "volume": 0.2, "structure": 0.1},
    )
    assert bullish is not None
    assert bullish.score > 0.4


def test_grid_search_returns_normalized_weights() -> None:
    from backend.services import alpaca_r1_options_calibration_service as svc

    samples = []
    for i in range(20):
        sigs = tuple(_sample_signals())
        ret = 0.02 if i % 2 == 0 else -0.01
        samples.append(
            svc._CalibrationSample(
                symbol="AAPL",
                as_of=f"t{i}",
                forward_return=ret,
                label_long_win=1 if ret > 0 else 0,
                signals=sigs,
            )
        )

    weights, metrics = grid_search_family_weights(samples)
    assert abs(sum(weights.values()) - 1.0) < 0.02
    assert metrics.n_samples == 20


def test_persist_calibration_result_writes_json(tmp_path: Path) -> None:
    target = tmp_path / "calibrated.json"
    result = R1OptionsCalibrationResult(
        calibrated_at="2026-06-12T00:00:00+00:00",
        family_weights=R1FamilyWeights(momentum=0.4, volume=0.3, structure=0.3),
        metrics=R1OptionsCalibrationMetrics(n_samples=10, n_trades=4, sharpe=0.5),
    )
    persist_calibration_result(result, target)
    payload = json.loads(target.read_text(encoding="utf-8"))
    assert payload["family_weights"]["momentum"] == 0.4


def test_run_calibration_empty_db(tmp_path: Path) -> None:
    missing_db = tmp_path / "missing.db"
    out = tmp_path / "out.json"
    result = run_r1_options_calibration(
        db_path=missing_db,
        output_path=out,
        symbols=("AAPL",),
        limit_per_symbol=10,
    )
    assert result.metrics.n_samples == 0
    assert out.exists()
