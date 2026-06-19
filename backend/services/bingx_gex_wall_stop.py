"""Motor ④ — GEX Wall Stop + Color Decay (BingX). # [PD-3][TH][IM]

Computes a dynamic stop anchored to the *directional* GEX wall — the call wall
for LONG, the put wall for SHORT — with a buffer that adapts to gamma "color
decay": in a negative-GEX (dealer-short-gamma) regime, the further spot drifts
from zero gamma the faster the buffer erodes, tightening the stop toward the
wall.

Pure, network-free (PD-3): it reads only options metrics already present on
``BingXCandidateAnalysis.options.metrics``. The low-level
:func:`resolve_wall_stop` works on primitives so the bot exits mixin can reuse
it directly (Turno 2b) without rebuilding an analysis object.

Price *levels* (walls, spot, stop) are analytical floats sourced from the
options bridge, consistent with the rest of the analysis path. PD-2 (Decimal)
governs money/notional, which this module does not produce.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any, Literal

from backend.config.bingx_gex_wall_stop_calibration import (
    base_buffer_pct,
    color_decay_k,
    erosion_max,
    gex_wall_stop_enabled,
    proximity_pct,
    size_down_mult,
)
from backend.config.logger_setup import get_logger
from backend.services.bingx_candidate_analysis import BingXCandidateAnalysis

logger = get_logger(__name__)

Direction = Literal["LONG", "SHORT", "FLAT"]


@dataclass(frozen=True)
class GexWallStopResult:
    """Outcome of a GEX wall stop evaluation for one candidate/direction."""

    stop_price: float | None
    stop_reason: str
    wall_erosion_score: float  # [0, 1]
    buffer_pct: float  # fraction of wall price actually applied
    wall_price: float | None
    wall_distance_pct: float | None  # percent units (matches wall_distance_pct)
    active: bool
    invalidates_direction: bool
    size_multiplier: float  # 1.0 neutral, <1.0 size-down, 0.0 invalidated

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def _safe_float(value: object) -> float | None:
    if value is None:
        return None
    try:
        out = float(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return None
    return out if abs(out) != float("inf") else None


def _inner_metrics(analysis: BingXCandidateAnalysis) -> dict[str, Any]:
    """Unwrap the nested options metrics dict (bridge wraps under 'metrics')."""
    metrics = analysis.options.metrics or {}
    if not isinstance(metrics, dict):
        return {}
    nested = metrics.get("metrics")
    return nested if isinstance(nested, dict) else metrics


def _clamp(value: float, low: float, high: float) -> float:
    return max(low, min(high, value))


def _neutral(reason: str) -> GexWallStopResult:
    """A non-active, non-invalidating result that leaves sizing untouched."""
    return GexWallStopResult(
        stop_price=None,
        stop_reason=reason,
        wall_erosion_score=0.0,
        buffer_pct=base_buffer_pct(),
        wall_price=None,
        wall_distance_pct=None,
        active=False,
        invalidates_direction=False,
        size_multiplier=1.0,
    )


def _erosion_score(
    spot: float,
    zero_gamma: float | None,
    net_gex_total: float,
    k: float,
) -> float:
    """Color-decay erosion in [0, erosion_max].

    Only fires in a negative-GEX regime (dealers short gamma → hedging
    accelerates moves). The further spot sits from zero gamma, the higher the
    erosion and the tighter the resulting buffer.
    """
    if net_gex_total >= 0.0 or zero_gamma is None or spot <= 0.0:
        return 0.0
    raw = abs(spot - zero_gamma) / spot * k
    return _clamp(raw, 0.0, erosion_max())


def resolve_wall_stop(
    *,
    direction: str,
    spot: float | None,
    call_wall: float | None,
    put_wall: float | None,
    zero_gamma: float | None,
    net_gex_total: float,
) -> GexWallStopResult:
    """Core wall-stop computation on primitive inputs (network-free, reusable).

    Args:
        direction: ``LONG`` or ``SHORT`` (anything else → neutral).
        spot: reference spot used by the options pipeline.
        call_wall: GEX call wall (resistance) — used for LONG.
        put_wall: GEX put wall (support) — used for SHORT.
        zero_gamma: zero-gamma flip level for color decay (optional).
        net_gex_total: net dealer gamma exposure (sign drives the regime).
    """
    if not gex_wall_stop_enabled():
        return _neutral("gex_wall_stop_disabled")

    side = direction.upper()
    if side not in ("LONG", "SHORT"):
        return _neutral("non_directional")
    if spot is None or spot <= 0.0:
        return _neutral("no_spot")

    wall_price = call_wall if side == "LONG" else put_wall
    if wall_price is None or wall_price <= 0.0:
        return _neutral("no_directional_wall")

    if side == "LONG":
        distance_pct = (wall_price - spot) / spot * 100.0
    else:
        distance_pct = (spot - wall_price) / spot * 100.0

    erosion = _erosion_score(spot, zero_gamma, net_gex_total, color_decay_k())
    buffer = base_buffer_pct() * (1.0 - erosion)

    def _stop_at_wall() -> float:
        # Anchor the protective stop just inside the wall.
        if side == "LONG":
            return wall_price * (1.0 - buffer)
        return wall_price * (1.0 + buffer)

    # Wall already breached against the trade → no room; invalidate direction.
    if distance_pct <= 0.0:
        reason = "long_above_call_wall" if side == "LONG" else "short_below_put_wall"
        return GexWallStopResult(
            stop_price=round(_stop_at_wall(), 6),
            stop_reason=reason,
            wall_erosion_score=round(erosion, 4),
            buffer_pct=round(buffer, 6),
            wall_price=round(wall_price, 6),
            wall_distance_pct=round(distance_pct, 4),
            active=True,
            invalidates_direction=True,
            size_multiplier=0.0,
        )

    if distance_pct > proximity_pct():
        return GexWallStopResult(
            stop_price=None,
            stop_reason="wall_out_of_range",
            wall_erosion_score=round(erosion, 4),
            buffer_pct=round(buffer, 6),
            wall_price=round(wall_price, 6),
            wall_distance_pct=round(distance_pct, 4),
            active=False,
            invalidates_direction=False,
            size_multiplier=1.0,
        )

    return GexWallStopResult(
        stop_price=round(_stop_at_wall(), 6),
        stop_reason="gex_wall_proximity",
        wall_erosion_score=round(erosion, 4),
        buffer_pct=round(buffer, 6),
        wall_price=round(wall_price, 6),
        wall_distance_pct=round(distance_pct, 4),
        active=True,
        invalidates_direction=False,
        size_multiplier=size_down_mult(),
    )


def compute_gex_wall_stop(
    analysis: BingXCandidateAnalysis,
    *,
    direction: str = "FLAT",
) -> GexWallStopResult:
    """Evaluate the GEX wall stop from a candidate analysis (PD-3 safe).

    Extracts the already-computed options metrics and delegates to
    :func:`resolve_wall_stop`. Never raises and never touches the network.
    """
    inner = _inner_metrics(analysis)
    if not inner:
        return _neutral("no_options_metrics")

    result = resolve_wall_stop(
        direction=direction,
        spot=_safe_float(inner.get("spot")),
        call_wall=_safe_float(inner.get("call_wall")),
        put_wall=_safe_float(inner.get("put_wall")),
        zero_gamma=_safe_float(inner.get("zero_gamma") or inner.get("zero_gamma_level")),
        net_gex_total=_safe_float(inner.get("net_gex_total")) or 0.0,
    )

    if result.active:
        logger.debug(
            "bingx_gex_wall_stop venue=%s dir=%s reason=%s dist=%.4f stop=%s erosion=%.4f mult=%.3f",
            analysis.venue_symbol,
            direction,
            result.stop_reason,
            result.wall_distance_pct if result.wall_distance_pct is not None else float("nan"),
            result.stop_price,
            result.wall_erosion_score,
            result.size_multiplier,
        )
    return result


def gex_wall_stop_multiplier(
    analysis: BingXCandidateAnalysis,
    *,
    direction: str = "FLAT",
) -> float:
    """Public helper — returns only the sizing multiplier (Turno 2 decide())."""
    return compute_gex_wall_stop(analysis, direction=direction).size_multiplier


__all__ = [
    "Direction",
    "GexWallStopResult",
    "compute_gex_wall_stop",
    "gex_wall_stop_multiplier",
    "resolve_wall_stop",
]
