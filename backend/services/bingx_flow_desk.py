"""Flow Desk — OBV-OI + MFI-Flow confluence into the BingX analysis path.

Reuses the scanner pipelines (``analyze_obv_oi_for_scanner`` /
``analyze_mfi_flow_for_scanner``) verbatim — no math is reimplemented here.
Network-free (PD-3): operates on venue klines and the options snapshot already
present on the analysis path. Never raises; degrades to an unavailable, NEUTRAL
snapshot. pandas runs inside the scanners (already a dependency).
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any, Literal

from backend.config.bingx_flow_desk_calibration import flow_desk_min_bars, flow_total_weight
from backend.config.logger_setup import get_logger
from backend.services.bingx_technical_bridge import klines_to_candles
from backend.services.market_scanner_mfi_flow import analyze_mfi_flow_for_scanner
from backend.services.market_scanner_obv_oi import analyze_obv_oi_for_scanner

logger = get_logger(__name__)

Vote = Literal["BULLISH", "BEARISH", "NEUTRAL"]


@dataclass(frozen=True)
class FlowDeskSnapshot:
    """OBV-OI + MFI-Flow confluence reading (scores are 0-100, 50 = neutral)."""

    status: Literal["available", "unavailable"]
    obv_oi_score: float
    obv_oi_bias: str
    mfi_flow_score: float
    mfi_flow_bias: str
    confluence_vote: Vote
    weight: float
    reason: str | None = None
    engine_blocks: dict[str, dict[str, Any]] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def _norm_bias(bias: object) -> str:
    token = str(bias or "").strip().upper()
    if token in ("BULLISH", "BULL", "LONG", "BUY"):
        return "BULLISH"
    if token in ("BEARISH", "BEAR", "SHORT", "SELL"):
        return "BEARISH"
    return "NEUTRAL"


def _unavailable(reason: str) -> FlowDeskSnapshot:
    return FlowDeskSnapshot(
        status="unavailable",
        obv_oi_score=50.0,
        obv_oi_bias="NEUTRAL",
        mfi_flow_score=50.0,
        mfi_flow_bias="NEUTRAL",
        confluence_vote="NEUTRAL",
        weight=flow_total_weight(),
        reason=reason,
        engine_blocks={},
    )


def build_flow_desk_snapshot(
    underlying_symbol: str,
    *,
    klines: tuple[Any, ...] | list[Any],
    options_snapshot: dict[str, Any] | object | None,
    timeframe: str = "5m",
) -> FlowDeskSnapshot:
    """Build the flow-desk snapshot for *underlying_symbol* (never raises)."""
    bars = klines_to_candles(klines) if klines else []
    if len(bars) < flow_desk_min_bars():
        return _unavailable("insufficient_bars")

    try:
        obv = analyze_obv_oi_for_scanner(underlying_symbol, timeframe, bars, options_snapshot)
        mfi = analyze_mfi_flow_for_scanner(underlying_symbol, timeframe, bars, options_snapshot)
    except Exception as exc:
        logger.warning(
            "bingx_flow_desk.failed symbol=%s error=%s",
            underlying_symbol,
            str(exc)[:180],
        )
        return _unavailable("flow_desk_failed")

    if not obv.ok and not mfi.ok:
        return _unavailable("scanner_unavailable")

    obv_bias = _norm_bias(obv.bias)
    mfi_bias = _norm_bias(mfi.bias)
    if obv_bias == "BULLISH" and mfi_bias == "BULLISH":
        vote: Vote = "BULLISH"
    elif obv_bias == "BEARISH" and mfi_bias == "BEARISH":
        vote = "BEARISH"
    else:
        vote = "NEUTRAL"

    engine_blocks: dict[str, dict[str, Any]] = {
        "flow_obv_oi": {"ok": bool(obv.ok), "bias": obv_bias, "score": round(obv.score / 100.0, 4)},
        "flow_mfi_flow": {
            "ok": bool(mfi.ok),
            "bias": mfi_bias,
            "score": round(mfi.score / 100.0, 4),
        },
    }

    logger.info(
        "bingx_flow_desk.attached symbol=%s obv=%s mfi=%s vote=%s",
        underlying_symbol,
        obv_bias,
        mfi_bias,
        vote,
    )
    return FlowDeskSnapshot(
        status="available",
        obv_oi_score=float(obv.score),
        obv_oi_bias=obv_bias,
        mfi_flow_score=float(mfi.score),
        mfi_flow_bias=mfi_bias,
        confluence_vote=vote,
        weight=flow_total_weight(),
        reason=None,
        engine_blocks=engine_blocks,
    )


__all__ = ["FlowDeskSnapshot", "build_flow_desk_snapshot"]
