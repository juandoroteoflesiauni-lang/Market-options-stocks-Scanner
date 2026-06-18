"""Unit tests for Risk & Sizing Engines v2."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from backend.services.bingx_risk_sizing_v2 import compute_risk_sizing_v2, risk_sizing_multiplier


@dataclass
class _Opts:
    metrics: dict[str, Any] | None = None


@dataclass
class _Analysis:
    venue_symbol: str = "INTC-USDT"
    options: _Opts = field(default_factory=_Opts)


def test_risk_sizing_v2_tailwind_long() -> None:
    analysis = _Analysis(
        options=_Opts(
            metrics={
                "metrics": {
                    "total_vex": 2_000_000.0,
                    "charm_flow": 80_000.0,
                    "iv_rank_hv_rolling": 0.25,
                    "vrp": 0.08,
                    "net_gex_total": 500_000.0,
                }
            }
        )
    )
    result = compute_risk_sizing_v2(analysis, direction="LONG")  # type: ignore[arg-type]
    assert result["ok"] is True
    assert result["flow_bias"] == "TAILWIND_LONG"
    assert result["multiplier"] > 0.5


def test_risk_sizing_v2_negative_vrp_blocks() -> None:
    analysis = _Analysis(options=_Opts(metrics={"metrics": {"vrp": -0.05, "total_vex": 0.0}}))
    mult = risk_sizing_multiplier(analysis, direction="FLAT")  # type: ignore[arg-type]
    assert mult == 0.0


def test_risk_sizing_v2_no_metrics_neutral() -> None:
    analysis = _Analysis(options=_Opts(metrics=None))
    payload = compute_risk_sizing_v2(analysis, direction="FLAT")  # type: ignore[arg-type]
    assert payload["ok"] is False
    assert payload["multiplier"] == 1.0
