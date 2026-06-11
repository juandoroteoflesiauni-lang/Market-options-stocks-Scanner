"""Canonical Options/GEX feature vector assembly.

This module keeps live snapshots, scanner synthesis, backfill and backtests
aligned on the same small set of risk-aware Options/GEX signals.
"""

from __future__ import annotations

import math
from typing import Any

SOURCE_TIER_SCORES = {
    "light_proxy": 0.25,
    "snapshot_chain": 0.65,
    "full_chain_gex": 1.0,
}

EXPECTED_COMPONENTS = (
    "gamma_flip",
    "tail_risk",
    "dealer_flow",
    "shadow_delta",
    "squeeze",
    "volatility_skew",
    "max_pain",
    "walls_gex",
)


class OptionsGexFeatureAssembler:
    """Central adapter from live Options/GEX payloads to canonical features."""

    def assemble(self, snapshot: object | None = None, **overrides: object) -> dict[str, Any]:
        return assemble_options_gex_features(snapshot, **overrides)

    def snapshot_fields(self, features: dict[str, Any]) -> dict[str, float]:
        return options_gex_feature_snapshot_fields(features)


def assemble_options_gex_features(
    snapshot: object | None = None, **overrides: object
) -> dict[str, Any]:
    payload = _as_dict(snapshot)
    payload.update({key: value for key, value in overrides.items() if value is not None})

    spot = _float(payload.get("spot"))
    gex = _as_dict(payload.get("gex_levels"))
    iv_surface = _as_dict(payload.get("iv_surface"))
    engine = _as_dict(payload.get("engine_signal"))
    quality = _as_dict(payload.get("chain_quality"))
    chain = _as_list(payload.get("chain"))
    flow = _as_dict(payload.get("flow_signal"))
    analytics = _as_dict(payload.get("chain_analytics"))

    source_tier = _source_tier(
        chain=chain, chain_quality=quality, gex_levels=gex, engine_signal=engine
    )
    provider = str(
        quality.get("provider")
        or _as_dict(quality.get("fetch_details")).get("provider")
        or "unknown"
    )

    net_gex = _first_float(gex.get("net_gex_total"), engine.get("total_gex"))
    squeeze = _bounded_01(
        _first_float(gex.get("squeeze_probability"), engine.get("squeeze_probability"))
    )
    zero_gamma = _first_float(gex.get("zero_gamma_level"), gex.get("gamma_flip"))
    call_wall = _float(gex.get("call_wall"))
    put_wall = _float(gex.get("put_wall"))
    max_pain = _float(gex.get("max_pain"))

    component_signals: dict[str, float] = {}
    if spot and spot > 0 and zero_gamma and zero_gamma > 0:
        component_signals["gamma_flip"] = _clamp((spot - zero_gamma) / spot * 10.0)
    if squeeze is not None:
        component_signals["tail_risk"] = _clamp(-squeeze * 1.25)
        component_signals["squeeze"] = squeeze
    dealer_pressure = _first_float(
        _as_dict(analytics.get("institutional_metrics")).get("dealer_pressure_score"),
        engine.get("total_vanna_exposure"),
        engine.get("total_vex"),
    )
    if dealer_pressure is not None:
        component_signals["dealer_flow"] = _clamp(dealer_pressure / max(abs(dealer_pressure), 1.0))
    total_dex = _float(payload.get("total_dex"))
    dex_flip = _float(payload.get("dex_flip_level"))
    if total_dex is not None:
        component_signals["shadow_delta"] = _clamp(total_dex / max(abs(total_dex), 1.0))
    elif spot and spot > 0 and dex_flip and dex_flip > 0:
        component_signals["shadow_delta"] = _clamp((spot - dex_flip) / spot * 8.0)
    if net_gex is not None:
        component_signals["walls_gex"] = _clamp(net_gex / max(abs(net_gex), 1.0))
    vol_signal = _volatility_skew_signal(iv_surface)
    if vol_signal is not None:
        component_signals["volatility_skew"] = vol_signal
    flow_score = _first_float(flow.get("score"), flow.get("toxicity_score"))
    if flow_score is not None:
        component_signals["flow_toxicity"] = _clamp(flow_score)
    if spot and spot > 0 and max_pain and max_pain > 0:
        component_signals["max_pain"] = _clamp((spot - max_pain) / spot * 6.0)

    active_engines = sorted(component_signals)
    missing_components = [
        component for component in EXPECTED_COMPONENTS if component not in active_engines
    ]
    composite = _weighted_composite(component_signals)
    data_quality_score = _data_quality_score(source_tier, active_engines, chain_quality=quality)

    return {
        "source_tier": source_tier,
        "provider": provider,
        "data_quality_score": data_quality_score,
        "missing_components": missing_components,
        "active_engines": active_engines,
        "component_signals": component_signals,
        "gamma_flip_directional_signal": component_signals.get("gamma_flip"),
        "tail_risk_directional_signal": component_signals.get("tail_risk"),
        "dealer_flow_vanna_pressure": component_signals.get("dealer_flow"),
        "shadow_delta_signal": component_signals.get("shadow_delta"),
        "squeeze_probability": squeeze,
        "volatility_skew_signal": component_signals.get("volatility_skew"),
        "net_gex_signal": component_signals.get("walls_gex"),
        "call_wall_distance_pct": _distance_pct(spot, call_wall),
        "put_wall_distance_pct": _distance_pct(spot, put_wall),
        "zero_gamma_distance_pct": _distance_pct(spot, zero_gamma),
        "max_pain_distance_pct": _distance_pct(spot, max_pain),
        "flow_toxicity_signal": component_signals.get("flow_toxicity"),
        "composite_directional_signal": composite,
    }


def options_gex_feature_snapshot_fields(features: dict[str, Any]) -> dict[str, float]:
    """Return numeric feature-snapshot fields compatible with backtesting."""
    fields = {
        "gamma_flip__directional_signal": features.get("gamma_flip_directional_signal"),
        "tail_risk__directional_signal": features.get("tail_risk_directional_signal"),
        "dealer_flow__vanna_pressure": features.get("dealer_flow_vanna_pressure"),
        "shadow_delta__shadow_delta": features.get("shadow_delta_signal"),
        "options_gex__squeeze_probability": features.get("squeeze_probability"),
        "options_gex__volatility_skew_signal": features.get("volatility_skew_signal"),
        "options_gex__net_gex_signal": features.get("net_gex_signal"),
        "options_gex__composite_directional_signal": features.get("composite_directional_signal"),
        "options_gex__data_quality_score": features.get("data_quality_score"),
        "options_gex__source_tier_score": SOURCE_TIER_SCORES.get(
            str(features.get("source_tier")), 0.0
        ),
        "options_gex__call_wall_distance_pct": features.get("call_wall_distance_pct"),
        "options_gex__put_wall_distance_pct": features.get("put_wall_distance_pct"),
        "options_gex__zero_gamma_distance_pct": features.get("zero_gamma_distance_pct"),
        "options_gex__max_pain_distance_pct": features.get("max_pain_distance_pct"),
    }
    return {key: float(value) for key, value in fields.items() if _float(value) is not None}


def _source_tier(
    *,
    chain: list[Any],
    chain_quality: dict[str, Any],
    gex_levels: dict[str, Any],
    engine_signal: dict[str, Any],
) -> str:
    strikes = (
        _first_float(chain_quality.get("strikes_in_expiry"), len(chain) if chain else None) or 0.0
    )
    has_oi = (_float(chain_quality.get("call_oi_strikes")) or 0.0) > 0 or (
        _float(chain_quality.get("put_oi_strikes")) or 0.0
    ) > 0
    has_gex = (
        _first_float(gex_levels.get("net_gex_total"), engine_signal.get("total_gex")) is not None
    )
    if strikes > 0 and has_oi and has_gex:
        return "full_chain_gex"
    if strikes > 0 and has_gex:
        return "snapshot_chain"
    return "light_proxy"


def _data_quality_score(
    source_tier: str, active_engines: list[str], *, chain_quality: dict[str, Any]
) -> float:
    tier_score = SOURCE_TIER_SCORES.get(source_tier, 0.0)
    engine_score = min(1.0, len(active_engines) / max(len(EXPECTED_COMPONENTS), 1))
    iv_score = _coverage_score(chain_quality, "call_iv_coverage_pct", "put_iv_coverage_pct")
    oi_score = _coverage_score(chain_quality, "call_oi_strikes", "put_oi_strikes", scale=20.0)
    return round(
        max(
            0.0,
            min(1.0, tier_score * 0.45 + engine_score * 0.35 + iv_score * 0.12 + oi_score * 0.08),
        ),
        4,
    )


def _coverage_score(
    payload: dict[str, Any], left: str, right: str, *, scale: float = 100.0
) -> float:
    values = [_float(payload.get(left)), _float(payload.get(right))]
    finite = [max(0.0, min(scale, value)) / scale for value in values if value is not None]
    return sum(finite) / len(finite) if finite else 0.0


def _weighted_composite(component_signals: dict[str, float]) -> float:
    weights = {
        "gamma_flip": 0.18,
        "walls_gex": 0.18,
        "dealer_flow": 0.14,
        "shadow_delta": 0.12,
        "tail_risk": 0.14,
        "squeeze": 0.08,
        "volatility_skew": 0.08,
        "max_pain": 0.05,
        "flow_toxicity": 0.03,
    }
    total_weight = sum(weights[key] for key in component_signals if key in weights)
    if total_weight <= 0:
        return 0.0
    value = (
        sum(component_signals[key] * weights[key] for key in component_signals if key in weights)
        / total_weight
    )
    return round(_clamp(value), 4)


def _volatility_skew_signal(iv_surface: dict[str, Any]) -> float | None:
    term = _as_dict(iv_surface.get("term_structure"))
    if bool(term.get("backwardation")):
        return -0.6
    if bool(term.get("contango")):
        return 0.2
    skew = _first_float(
        iv_surface.get("skew"), iv_surface.get("put_call_skew"), iv_surface.get("risk_reversal")
    )
    if skew is not None:
        return _clamp(-skew * 4.0)
    vrp = _float(iv_surface.get("vrp"))
    if vrp is not None:
        return _clamp(-vrp * 3.0)
    return None


def _distance_pct(spot: float | None, level: float | None) -> float | None:
    if spot is None or level is None or spot <= 0 or level <= 0:
        return None
    return round((spot - level) / spot * 100.0, 4)


def _as_dict(value: object) -> dict[str, Any]:
    if value is None:
        return {}
    if isinstance(value, dict):
        return dict(value)
    if hasattr(value, "model_dump"):
        try:
            dumped = value.model_dump(mode="json")  # type: ignore[attr-defined]
            return dumped if isinstance(dumped, dict) else {}
        except TypeError:
            dumped = value.model_dump()  # type: ignore[attr-defined]
            return dumped if isinstance(dumped, dict) else {}
    return {
        key: getattr(value, key)
        for key in dir(value)
        if not key.startswith("_") and not callable(getattr(value, key, None))
    }


def _as_list(value: object) -> list[Any]:
    if isinstance(value, list):
        return value
    if isinstance(value, tuple):
        return list(value)
    return []


def _first_float(*values: object) -> float | None:
    for value in values:
        number = _float(value)
        if number is not None:
            return number
    return None


def _float(value: object) -> float | None:
    if isinstance(value, bool) or value is None:
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def _bounded_01(value: float | None) -> float | None:
    if value is None:
        return None
    return max(0.0, min(1.0, value))


def _clamp(value: float) -> float:
    if not math.isfinite(value):
        return 0.0
    return max(-1.0, min(1.0, value))
