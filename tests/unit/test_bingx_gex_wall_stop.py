"""Unit tests for Motor ④ — GEX Wall Stop + Color Decay."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import pytest

from backend.services.bingx_gex_wall_stop import (
    GexWallStopResult,
    compute_gex_wall_stop,
    gex_wall_stop_multiplier,
    resolve_wall_stop,
)


@dataclass
class _Opts:
    metrics: dict[str, Any] | None = None


@dataclass
class _Analysis:
    venue_symbol: str = "INTC-USDT"
    options: _Opts = field(default_factory=_Opts)


def _analysis(**metrics: Any) -> _Analysis:
    return _Analysis(options=_Opts(metrics={"metrics": dict(metrics)}))


# ── resolve_wall_stop (primitive core) ───────────────────────────────────────


def test_resolve_wall_stop_long_proximity_sizes_down() -> None:
    # ARRANGE — call wall 1% above spot, positive GEX (no erosion).
    # ACT
    result = resolve_wall_stop(
        direction="LONG",
        spot=100.0,
        call_wall=101.0,
        put_wall=None,
        zero_gamma=None,
        net_gex_total=500_000.0,
    )
    # ASSERT
    assert result.active is True
    assert result.invalidates_direction is False
    assert result.stop_reason == "gex_wall_proximity"
    assert result.wall_erosion_score == 0.0
    # buffer = 0.5% with no erosion → stop just below the wall
    assert result.stop_price == pytest.approx(101.0 * (1 - 0.005), rel=1e-6)
    assert 0.0 < result.size_multiplier < 1.0


def test_resolve_wall_stop_long_far_wall_is_inactive() -> None:
    result = resolve_wall_stop(
        direction="LONG",
        spot=100.0,
        call_wall=105.0,
        put_wall=None,
        zero_gamma=None,
        net_gex_total=0.0,
    )
    assert result.active is False
    assert result.stop_price is None
    assert result.stop_reason == "wall_out_of_range"
    assert result.size_multiplier == 1.0


def test_resolve_wall_stop_long_above_call_wall_invalidates() -> None:
    # Spot already above the call wall → no upside room.
    result = resolve_wall_stop(
        direction="LONG",
        spot=102.0,
        call_wall=101.0,
        put_wall=None,
        zero_gamma=None,
        net_gex_total=100.0,
    )
    assert result.invalidates_direction is True
    assert result.active is True
    assert result.size_multiplier == 0.0
    assert result.stop_reason == "long_above_call_wall"


def test_resolve_wall_stop_short_uses_put_wall() -> None:
    # SHORT anchors to the put wall (support) 1% below spot.
    result = resolve_wall_stop(
        direction="SHORT",
        spot=100.0,
        call_wall=None,
        put_wall=99.0,
        zero_gamma=None,
        net_gex_total=200_000.0,
    )
    assert result.active is True
    assert result.invalidates_direction is False
    assert result.wall_price == pytest.approx(99.0)
    # stop just above the put wall
    assert result.stop_price == pytest.approx(99.0 * (1 + 0.005), rel=1e-6)


def test_color_decay_tightens_buffer_in_negative_gex() -> None:
    # ARRANGE — same proximity, but negative GEX with spot far from zero gamma.
    decayed = resolve_wall_stop(
        direction="LONG",
        spot=100.0,
        call_wall=101.0,
        put_wall=None,
        zero_gamma=95.0,
        net_gex_total=-500_000.0,
    )
    baseline = resolve_wall_stop(
        direction="LONG",
        spot=100.0,
        call_wall=101.0,
        put_wall=None,
        zero_gamma=95.0,
        net_gex_total=500_000.0,  # positive → no erosion
    )
    # ASSERT — erosion shrinks the buffer, pushing the stop closer to the wall.
    assert decayed.wall_erosion_score > 0.0
    assert decayed.buffer_pct < baseline.buffer_pct
    assert decayed.stop_price is not None and baseline.stop_price is not None
    assert decayed.stop_price > baseline.stop_price


def test_color_decay_is_capped_at_erosion_max() -> None:
    # Spot very far from zero gamma → erosion would exceed 1.0 but is capped.
    result = resolve_wall_stop(
        direction="LONG",
        spot=100.0,
        call_wall=101.0,
        put_wall=None,
        zero_gamma=10.0,
        net_gex_total=-1_000_000.0,
    )
    assert result.wall_erosion_score == pytest.approx(0.80)
    assert result.buffer_pct == pytest.approx(0.005 * (1 - 0.80), rel=1e-6)


def test_resolve_wall_stop_non_directional_is_neutral() -> None:
    result = resolve_wall_stop(
        direction="FLAT",
        spot=100.0,
        call_wall=101.0,
        put_wall=99.0,
        zero_gamma=None,
        net_gex_total=0.0,
    )
    assert result.active is False
    assert result.size_multiplier == 1.0
    assert result.stop_reason == "non_directional"


def test_resolve_wall_stop_missing_directional_wall() -> None:
    result = resolve_wall_stop(
        direction="LONG",
        spot=100.0,
        call_wall=None,
        put_wall=99.0,
        zero_gamma=None,
        net_gex_total=0.0,
    )
    assert result.stop_reason == "no_directional_wall"
    assert result.size_multiplier == 1.0


# ── compute_gex_wall_stop (analysis adapter) ─────────────────────────────────


def test_compute_from_analysis_proximity() -> None:
    analysis = _analysis(spot=100.0, call_wall=101.0, net_gex_total=500_000.0)
    result = compute_gex_wall_stop(analysis, direction="LONG")  # type: ignore[arg-type]
    assert isinstance(result, GexWallStopResult)
    assert result.active is True
    assert result.stop_price is not None


def test_compute_from_analysis_no_metrics_neutral() -> None:
    analysis = _Analysis(options=_Opts(metrics=None))
    result = compute_gex_wall_stop(analysis, direction="LONG")  # type: ignore[arg-type]
    assert result.stop_reason == "no_options_metrics"
    assert result.size_multiplier == 1.0


def test_multiplier_helper_matches_result() -> None:
    analysis = _analysis(spot=100.0, call_wall=101.0, net_gex_total=500_000.0)
    mult = gex_wall_stop_multiplier(analysis, direction="LONG")  # type: ignore[arg-type]
    full = compute_gex_wall_stop(analysis, direction="LONG")  # type: ignore[arg-type]
    assert mult == full.size_multiplier


def test_disabled_via_env_is_no_op(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("BINGX_GEX_WALL_STOP_ENABLED", "false")
    result = resolve_wall_stop(
        direction="LONG",
        spot=100.0,
        call_wall=101.0,
        put_wall=None,
        zero_gamma=None,
        net_gex_total=-500_000.0,
    )
    assert result.stop_reason == "gex_wall_stop_disabled"
    assert result.active is False
    assert result.size_multiplier == 1.0
