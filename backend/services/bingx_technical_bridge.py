"""Bridge BingX symbols → full technical-terminal analysis (SMC/VSA/FVG/VP/OF).

Wraps :func:`backend.services.technical_terminal_payload.build_technical_terminal_payload_from_candles`
and (optionally) :func:`backend.services.technical_terminal_payload.build_technical_terminal_payload`
so the BingX bot can drive the *complete* technical stack — not just the
lightweight RSI/EMA/VWAP triple — and surface a stable, JSON-safe summary
suitable for the analysis drawer and risk-desk inputs.

Two builders:

- :func:`build_venue_technical` operates on the BingX venue (synthetic perp).
  It converts ``BingXKline`` (or any object/dict carrying ``open_time_ms``,
  ``open/high/low/close/volume``) into the candle dicts the technical
  terminal expects, optionally injects an L2 ``LOBDynamicsAnalysis`` snapshot
  in place of the default ``lob_dynamics`` block, and returns the summary
  fields the task spec promises.

- :func:`build_underlying_technical` runs the equity-side technical path for
  stock_perp / stock_index_perp. The caller picks between the full payload
  (``build_technical_terminal_payload``, source ``technical_terminal_underlying``)
  and the lighter equity TA snapshot (``EquityTASnapshotService.snapshot``,
  source ``equity_ta_snapshot``) based on availability and cost. For crypto
  this builder returns unavailable with ``no_equity_technical_for_crypto``.

Survival contract:

- Every exception from a fetcher degrades to ``status="unavailable"`` with a
  stable reason code. The bridge never raises.
- Bars below the SMC pipeline minimum (35) return
  ``insufficient_bars_for_smc`` instead of producing a partial SMC.
- ``technical_quality_score`` collapses to ``0.0`` when no engine produced ok=True.
"""

from __future__ import annotations

import contextlib as _ctx
from collections.abc import Awaitable, Callable
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from typing import Any, Literal

from backend.config.logger_setup import get_logger

logger = get_logger(__name__)


# ── Stable reason codes ──────────────────────────────────────────────────────
# These strings are public contract — runbooks, dashboards and risk-desk
# gates match on them. Do NOT rename without coordinating with consumers.
REASON_INSUFFICIENT_BARS = "insufficient_bars_for_smc"
REASON_NO_VENUE_BARS = "no_venue_bars"
REASON_TECHNICAL_FETCH_FAILED = "technical_fetch_failed"
REASON_PAYLOAD_NOT_OK = "payload_not_ok"
REASON_NO_TECHNICAL_FETCHER = "no_technical_fetcher"
REASON_NO_EQUITY_FOR_CRYPTO = "no_equity_technical_for_crypto"
REASON_EQUITY_SNAPSHOT_NOT_OK = "equity_snapshot_not_ok"

# Matches the SMC pipeline gate inside ``build_technical_terminal_payload``.
# Bumping this without bumping the engine's gate would let "available"
# results leak through that the SMC engine then rejects.
MIN_BARS_FOR_SMC = 35

SourceStatus = Literal["available", "unavailable"]

# Source tags exposed in the result. Stable for log correlation.
SOURCE_VENUE = "technical_terminal_venue"
SOURCE_UNDERLYING_FULL = "technical_terminal_underlying"
SOURCE_EQUITY_SNAPSHOT = "equity_ta_snapshot"
SOURCE_NONE = "none"


# ── Fetcher protocols ────────────────────────────────────────────────────────
# Loose signatures so production and tests can pass the actual technical
# terminal callables (with their numerous kwargs) without rewriting them.
TechnicalCandlesFn = Callable[..., Awaitable[dict[str, Any]]]
TechnicalUnderlyingFn = Callable[..., Awaitable[dict[str, Any]]]
EquityTASnapshotFn = Callable[[str], Awaitable[dict[str, Any]]]


# ── Dataclasses ──────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class BingXTechnicalSummary:
    """Compact, JSON-safe summary derived from the full technical payload."""

    trend_direction: str  # "bullish" / "bearish" / "neutral"
    smc_bias: str | None  # "BULLISH" / "BEARISH" / "NEUTRAL"
    vsa_signal: str | None  # VSA engine signal label
    fvg_state: str | None  # "bullish_dominant" / "bearish_dominant" / "balanced" / "none"
    volume_profile_bias: str | None  # "bullish" / "bearish" / "neutral"
    composite_score: float | None
    bars_used: int


@dataclass(frozen=True)
class BingXTechnicalBridgeResult:
    """Bridge output for one BingX analysis target (venue OR underlying)."""

    status: SourceStatus
    source: str  # see SOURCE_* constants
    symbol: str
    timeframe: str | None
    summary: BingXTechnicalSummary | None
    payload: dict[str, Any] | None
    lob_quality_score: float | None
    technical_quality_score: float | None
    reason: str | None = None
    fetched_at: str = ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


# ── Kline → candle conversion ────────────────────────────────────────────────


def _kline_field(kline: object, name: str) -> Any:
    """Read a field from either a dict or a dataclass/object-like kline."""
    if isinstance(kline, dict):
        return kline.get(name)
    return getattr(kline, name, None)


def klines_to_candles(klines: object) -> list[dict[str, Any]]:
    """Project BingX klines into the candle-dict shape the technical terminal
    expects. Accepts any iterable of objects/dicts with ``open_time_ms`` and
    the standard OHLCV attributes; non-finite or missing rows are dropped.

    The terminal builder accepts either millisecond or second-resolution
    timestamps under ``time``; we pass through milliseconds — same precision
    BingX returns natively.
    """
    out: list[dict[str, Any]] = []
    if not klines:
        return out
    for kline in klines:
        ts = _kline_field(kline, "open_time_ms")
        if ts is None:
            ts = _kline_field(kline, "time")
        try:
            ts_ms = int(ts)
            o = float(_kline_field(kline, "open"))
            h = float(_kline_field(kline, "high"))
            lo = float(_kline_field(kline, "low"))
            c = float(_kline_field(kline, "close"))
            v_raw = _kline_field(kline, "volume")
            v = float(v_raw) if v_raw is not None else 0.0
        except (TypeError, ValueError):
            continue
        if ts_ms <= 0 or min(o, h, lo, c) <= 0:
            continue
        out.append({"time": ts_ms, "open": o, "high": h, "low": lo, "close": c, "volume": v})
    return out


# ── L2 injection ─────────────────────────────────────────────────────────────


def _l2_to_dict(l2_snapshot: object | None) -> dict[str, Any] | None:
    """Coerce a Pydantic ``LOBDynamicsAnalysis`` or pre-serialised dict to a
    JSON-safe dict; anything else returns ``None`` (caller leaves the
    payload's default unavailable block untouched)."""
    if l2_snapshot is None:
        return None
    if isinstance(l2_snapshot, dict):
        return dict(l2_snapshot)
    if hasattr(l2_snapshot, "model_dump"):
        try:
            return l2_snapshot.model_dump(mode="python")  # type: ignore[attr-defined]
        except Exception:
            return None
    return None


def inject_l2_into_payload(payload: dict[str, Any], l2_snapshot: object | None) -> float | None:
    """Replace the payload's ``lob_dynamics`` block with the provided L2
    snapshot. Returns the quality score the snapshot carried (if any).

    The technical terminal seeds ``lob_dynamics`` with an unavailable stub
    when no L2 feed is wired; this function swaps in the real BingX L2 result
    so consumers see one unified ``lob_dynamics`` block. The L2's
    ``data_quality_score`` is surfaced separately because risk-desk consumers
    read it without descending into the lob_dynamics block.
    """
    as_dict = _l2_to_dict(l2_snapshot)
    if as_dict is None:
        return None
    payload["lob_dynamics"] = as_dict
    engine_status = payload.get("engine_status")
    if isinstance(engine_status, dict):
        engine_status["lob_dynamics"] = {
            "enabled": True,
            "ok": bool(as_dict.get("ok")),
            "error": as_dict.get("error"),
        }
    return _safe_float(as_dict.get("data_quality_score"))


# ── Summary extraction ───────────────────────────────────────────────────────


def _safe_float(value: object) -> float | None:
    if value is None:
        return None
    try:
        out = float(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return None
    if out != out:  # NaN check (NaN != NaN)
        return None
    return out


def _trend_from_smc_or_market_structure(payload: dict[str, Any]) -> str:
    """Prefer ``market_structure.bias`` (richer signal) then ``smc.sesgo``."""
    market_structure = payload.get("market_structure") or {}
    if isinstance(market_structure, dict):
        bias = market_structure.get("bias") or market_structure.get("trend")
        if isinstance(bias, str) and bias:
            return bias.lower()
    smc = payload.get("smc") or {}
    if isinstance(smc, dict):
        sesgo = smc.get("sesgo")
        if isinstance(sesgo, str) and sesgo:
            return {"BULLISH": "bullish", "BEARISH": "bearish", "NEUTRAL": "neutral"}.get(
                sesgo.upper(), sesgo.lower()
            )
    return "neutral"


def _fvg_state(payload: dict[str, Any]) -> str | None:
    """Derive a single-token bias from FVG counts."""
    fvg = payload.get("fvg")
    if not isinstance(fvg, dict) or not fvg.get("ok"):
        return None
    bull = int(fvg.get("bullish_active_count") or 0)
    bear = int(fvg.get("bearish_active_count") or 0)
    if bull == 0 and bear == 0:
        return "none"
    if bull > bear:
        return "bullish_dominant"
    if bear > bull:
        return "bearish_dominant"
    return "balanced"


def _extract_summary(payload: dict[str, Any]) -> BingXTechnicalSummary:
    smc = payload.get("smc") if isinstance(payload.get("smc"), dict) else {}
    vsa = payload.get("vsa") if isinstance(payload.get("vsa"), dict) else {}
    volume_profile = (
        payload.get("volume_profile") if isinstance(payload.get("volume_profile"), dict) else {}
    )
    meta = payload.get("meta") if isinstance(payload.get("meta"), dict) else {}

    smc_bias = smc.get("sesgo")
    vsa_signal = vsa.get("signal") if vsa.get("ok") else None
    vp_bias = volume_profile.get("volume_bias") if volume_profile.get("ok") else None
    composite = _safe_float(smc.get("composite_score")) or _safe_float(meta.get("composite_score"))
    bars_used = int(meta.get("bars") or 0)

    return BingXTechnicalSummary(
        trend_direction=_trend_from_smc_or_market_structure(payload),
        smc_bias=str(smc_bias) if smc_bias else None,
        vsa_signal=str(vsa_signal) if vsa_signal else None,
        fvg_state=_fvg_state(payload),
        volume_profile_bias=str(vp_bias) if vp_bias else None,
        composite_score=composite,
        bars_used=bars_used,
    )


def _summary_from_equity_snapshot(snapshot: dict[str, Any]) -> BingXTechnicalSummary:
    """Lite path: ``EquityTASnapshotService.snapshot`` only carries RSI/EMA/trend."""
    return BingXTechnicalSummary(
        trend_direction=str(snapshot.get("trend_direction") or "neutral"),
        smc_bias=None,
        vsa_signal=None,
        fvg_state=None,
        volume_profile_bias=None,
        composite_score=None,
        bars_used=int(snapshot.get("bars_used") or 0),
    )


# ── Quality scoring ──────────────────────────────────────────────────────────


def compute_technical_quality_score(payload: dict[str, Any]) -> float:
    """Heuristic quality in [0, 1] based on which engines produced ok=True.

    Survival framing: a payload where SMC + VSA + FVG + volume_profile all
    failed cannot back risk-sizing decisions, even if the bars are nominally
    sufficient. The score collapses well before risk-desk thresholds bite.
    """
    score = 0.0
    smc = payload.get("smc")
    if isinstance(smc, dict) and smc.get("sesgo") is not None:
        # SMC always emits sesgo when it ran successfully — there is no
        # explicit ``ok`` flag on the model dump, so we treat ``sesgo``
        # presence as the success signal.
        score += 0.3
    vsa = payload.get("vsa")
    if isinstance(vsa, dict) and vsa.get("ok") and vsa.get("signal") is not None:
        score += 0.2
    fvg = payload.get("fvg")
    if isinstance(fvg, dict) and fvg.get("ok"):
        if int(fvg.get("active_count") or 0) > 0 or int(fvg.get("history_count") or 0) > 0:
            score += 0.2
        else:
            score += 0.1
    volume_profile = payload.get("volume_profile")
    if isinstance(volume_profile, dict) and volume_profile.get("ok"):
        score += 0.2
    order_flow = payload.get("order_flow_delta")
    if isinstance(order_flow, dict) and order_flow.get("ok"):
        score += 0.1
    return round(min(1.0, score), 4)


# ── Public builder: venue ────────────────────────────────────────────────────


def _now_iso() -> str:
    return datetime.now(UTC).isoformat()


def _unavailable(
    *,
    source: str,
    symbol: str,
    timeframe: str | None,
    reason: str,
    payload: dict[str, Any] | None = None,
    lob_quality_score: float | None = None,
) -> BingXTechnicalBridgeResult:
    return BingXTechnicalBridgeResult(
        status="unavailable",
        source=source,
        symbol=symbol,
        timeframe=timeframe,
        summary=None,
        payload=payload,
        lob_quality_score=lob_quality_score,
        technical_quality_score=None,
        reason=reason,
        fetched_at=_now_iso(),
    )


async def build_venue_technical(
    venue_symbol: str,
    klines: object,
    *,
    timeframe: str = "5m",
    l2_snapshot: object | None = None,
    technical_fn: TechnicalCandlesFn | None = None,
) -> BingXTechnicalBridgeResult:
    """Run the full technical-terminal pipeline against BingX venue klines.

    The pipeline runs SMC, VSA, FVG, volume profile, order-flow proxies,
    market-structure, HMM regime, and footprint engines (per the
    ``TECHNICAL_ENABLE_*`` env flags inside the terminal). When an L2
    snapshot is provided, ``lob_dynamics`` is replaced with it so downstream
    consumers see one unified block.
    """
    candles = klines_to_candles(klines)

    if not candles:
        return _unavailable(
            source=SOURCE_VENUE,
            symbol=venue_symbol,
            timeframe=timeframe,
            reason=REASON_NO_VENUE_BARS,
        )

    if len(candles) < MIN_BARS_FOR_SMC:
        return _unavailable(
            source=SOURCE_VENUE,
            symbol=venue_symbol,
            timeframe=timeframe,
            reason=REASON_INSUFFICIENT_BARS,
        )

    if technical_fn is None:
        return _unavailable(
            source=SOURCE_VENUE,
            symbol=venue_symbol,
            timeframe=timeframe,
            reason=REASON_NO_TECHNICAL_FETCHER,
        )

    try:
        payload = await technical_fn(venue_symbol, candles, timeframe)
    except Exception as exc:
        logger.warning(
            "bingx_technical_bridge.venue_fetch_failed symbol=%s tf=%s error=%s",
            venue_symbol,
            timeframe,
            str(exc)[:180],
        )
        return _unavailable(
            source=SOURCE_VENUE,
            symbol=venue_symbol,
            timeframe=timeframe,
            reason=REASON_TECHNICAL_FETCH_FAILED,
        )

    if not isinstance(payload, dict):
        return _unavailable(
            source=SOURCE_VENUE,
            symbol=venue_symbol,
            timeframe=timeframe,
            reason=REASON_PAYLOAD_NOT_OK,
        )

    if not payload.get("ok"):
        return _unavailable(
            source=SOURCE_VENUE,
            symbol=venue_symbol,
            timeframe=timeframe,
            reason=str(payload.get("error") or REASON_PAYLOAD_NOT_OK)[:180],
            payload=payload,
        )

    lob_quality = inject_l2_into_payload(payload, l2_snapshot)
    summary = _extract_summary(payload)
    quality = compute_technical_quality_score(payload)

    # Audit: capture technical indicators snapshot (fire-and-forget)
    try:
        import asyncio as _aio

        from backend.audit.hooks import audit_decision_snapshot

        indicator_data: dict[str, Any] = {}
        if isinstance(payload, dict):
            indicator_data = {
                k: v
                for k, v in payload.items()
                if k not in ("candles", "raw") and not isinstance(v, (list, dict))
            }
        with _ctx.suppress(Exception):
            _aio.get_event_loop().create_task(
                audit_decision_snapshot(
                    module="bingx_technical",
                    symbol=venue_symbol,
                    indicators=indicator_data,
                    engine_state={
                        "source": SOURCE_VENUE,
                        "timeframe": timeframe,
                        "technical_quality_score": quality,
                        "lob_quality_score": lob_quality,
                    },
                )
            )
    except Exception:
        pass

    return BingXTechnicalBridgeResult(
        status="available",
        source=SOURCE_VENUE,
        symbol=venue_symbol,
        timeframe=timeframe,
        summary=summary,
        payload=payload,
        lob_quality_score=lob_quality,
        technical_quality_score=quality,
        reason=None,
        fetched_at=_now_iso(),
    )


# ── Public builder: underlying ───────────────────────────────────────────────


async def build_underlying_technical(
    underlying_symbol: str,
    *,
    market_type: str | None = None,
    technical_fn: TechnicalUnderlyingFn | None = None,
    equity_snapshot_fn: EquityTASnapshotFn | None = None,
    timeframe: str = "1d",
    days: int = 320,
) -> BingXTechnicalBridgeResult:
    """Run the equity-side technical pipeline for a BingX synthetic stock.

    When ``technical_fn`` is provided, the full terminal payload is fetched
    (``source="technical_terminal_underlying"``). Otherwise, if
    ``equity_snapshot_fn`` is provided, the lighter RSI/EMA/trend snapshot is
    used (``source="equity_ta_snapshot"``). For crypto market types the
    bridge returns unavailable with ``no_equity_technical_for_crypto``
    without invoking any fetcher.

    The full payload carries SMC/VSA/FVG/VP/OF metrics; the lite snapshot
    only carries ``trend_direction`` and bars_used. Risk-desk consumers
    inspect ``source`` before relying on any field that only the full path
    populates.
    """
    if market_type == "crypto_standard":
        return _unavailable(
            source=SOURCE_NONE,
            symbol=underlying_symbol,
            timeframe=timeframe,
            reason=REASON_NO_EQUITY_FOR_CRYPTO,
        )

    if not underlying_symbol:
        return _unavailable(
            source=SOURCE_NONE,
            symbol=underlying_symbol,
            timeframe=timeframe,
            reason=REASON_NO_TECHNICAL_FETCHER,
        )

    if technical_fn is not None:
        try:
            payload = await technical_fn(underlying_symbol, days, timeframe)
        except Exception as exc:
            logger.warning(
                "bingx_technical_bridge.underlying_full_failed symbol=%s tf=%s error=%s",
                underlying_symbol,
                timeframe,
                str(exc)[:180],
            )
            return _unavailable(
                source=SOURCE_UNDERLYING_FULL,
                symbol=underlying_symbol,
                timeframe=timeframe,
                reason=REASON_TECHNICAL_FETCH_FAILED,
            )

        if not isinstance(payload, dict) or not payload.get("ok"):
            return _unavailable(
                source=SOURCE_UNDERLYING_FULL,
                symbol=underlying_symbol,
                timeframe=timeframe,
                reason=(
                    str((payload or {}).get("error") or REASON_PAYLOAD_NOT_OK)[:180]
                    if isinstance(payload, dict)
                    else REASON_PAYLOAD_NOT_OK
                ),
                payload=payload if isinstance(payload, dict) else None,
            )

        return BingXTechnicalBridgeResult(
            status="available",
            source=SOURCE_UNDERLYING_FULL,
            symbol=underlying_symbol,
            timeframe=timeframe,
            summary=_extract_summary(payload),
            payload=payload,
            lob_quality_score=None,  # equity underlying has no L2
            technical_quality_score=compute_technical_quality_score(payload),
            reason=None,
            fetched_at=_now_iso(),
        )

    if equity_snapshot_fn is not None:
        try:
            snapshot = await equity_snapshot_fn(underlying_symbol)
        except Exception as exc:
            logger.warning(
                "bingx_technical_bridge.equity_snapshot_failed symbol=%s error=%s",
                underlying_symbol,
                str(exc)[:180],
            )
            return _unavailable(
                source=SOURCE_EQUITY_SNAPSHOT,
                symbol=underlying_symbol,
                timeframe=timeframe,
                reason=REASON_TECHNICAL_FETCH_FAILED,
            )

        if not isinstance(snapshot, dict) or not snapshot.get("ok"):
            return _unavailable(
                source=SOURCE_EQUITY_SNAPSHOT,
                symbol=underlying_symbol,
                timeframe=timeframe,
                reason=(
                    str((snapshot or {}).get("reason") or REASON_EQUITY_SNAPSHOT_NOT_OK)[:180]
                    if isinstance(snapshot, dict)
                    else REASON_EQUITY_SNAPSHOT_NOT_OK
                ),
            )

        bars_used = int(snapshot.get("bars_used") or 0)
        quality = min(1.0, bars_used / 200.0) if bars_used > 0 else 0.0
        return BingXTechnicalBridgeResult(
            status="available",
            source=SOURCE_EQUITY_SNAPSHOT,
            symbol=underlying_symbol,
            timeframe=timeframe,
            summary=_summary_from_equity_snapshot(snapshot),
            payload=snapshot,
            lob_quality_score=None,
            technical_quality_score=round(quality, 4),
            reason=None,
            fetched_at=_now_iso(),
        )

    return _unavailable(
        source=SOURCE_NONE,
        symbol=underlying_symbol,
        timeframe=timeframe,
        reason=REASON_NO_TECHNICAL_FETCHER,
    )


__all__ = [
    "MIN_BARS_FOR_SMC",
    "REASON_EQUITY_SNAPSHOT_NOT_OK",
    "REASON_INSUFFICIENT_BARS",
    "REASON_NO_EQUITY_FOR_CRYPTO",
    "REASON_NO_TECHNICAL_FETCHER",
    "REASON_NO_VENUE_BARS",
    "REASON_PAYLOAD_NOT_OK",
    "REASON_TECHNICAL_FETCH_FAILED",
    "SOURCE_EQUITY_SNAPSHOT",
    "SOURCE_NONE",
    "SOURCE_UNDERLYING_FULL",
    "SOURCE_VENUE",
    "BingXTechnicalBridgeResult",
    "BingXTechnicalSummary",
    "EquityTASnapshotFn",
    "TechnicalCandlesFn",
    "TechnicalUnderlyingFn",
    "build_underlying_technical",
    "build_venue_technical",
    "compute_technical_quality_score",
    "inject_l2_into_payload",
    "klines_to_candles",
]
