from __future__ import annotations
from typing import Any
"""Fase 2: desk-level market regime detector for the Market Scanner.

Unifies four deterministic components into a single desk regime label:

1. SPY HMM state (via the layer-3 ``hmm_engine`` adapter) — only when bars are
   supplied (adaptive_weighting path).
2. VIX bucket (broad volatility read).
3. Macro stress (FRED + FMP economic-calendar high-impact events).
4. Universe breadth (bullish share from the cross-sectional regime summary).

The detector is a context provider only — it never authorizes risk. Missing
inputs degrade gracefully: with no usable signal it returns a low-confidence
``TRANSITION`` snapshot rather than fabricating a regime.

Architecture note: HMM is consumed through the layer-3 specialist adapter
(``analyze_hmm_regime_from_ohlcv``); this service performs no market IO.
"""



from backend.config.logger_setup import get_logger
from backend.domain.market_scanner_models import DeskRegimeLabel, DeskRegimeSnapshot

logger = get_logger(__name__)

METHOD_VERSION = "desk-regime-v2"

# VIX bucket thresholds (broad volatility read).
_VIX_LOW = 15.0
_VIX_NORMAL = 20.0
_VIX_ELEVATED = 28.0
_VIX_HIGH = 40.0

# Breadth thresholds on bullish share (0-1).
_BREADTH_BULLISH = 0.55
_BREADTH_BEARISH = 0.45

# Macro high-impact event counts mapped to a stress level.
_MACRO_STRESS_HIGH = 4
_MACRO_STRESS_MEDIUM = 1

# Canonicalises raw HMM ``current_label`` values into desk vocabulary.
_HMM_LABEL_MAP: dict[str, str] = {
    "BULL_QUIET": "BULL_QUIET",
    "BULLISH": "BULL_QUIET",
    "NEUTRAL": "BULL_QUIET",
    "MEAN_REVERT": "MEAN_REVERT",
    "BEARISH": "BEAR_VOLATILE",
    "BEAR_VOLATILE": "BEAR_VOLATILE",
    "CRISIS": "CRISIS",
    "CHAOTIC": "CRISIS",
    "RECOVERY": "RECOVERY",
}


def analyze_spy_regime(spy_bars: list[dict[str, Any]]) -> dict[str, Any] | None:
    """Run the layer-3 HMM engine on SPY daily bars (robust DataFrame adapter).

    Returns a compact dict ``{label, signal, transition_risk, confidence}`` or
    ``None`` when detection is not possible (too few bars, engine failure).
    """
    if not spy_bars or len(spy_bars) < 30:
        logger.info("desk_regime.hmm_skipped insufficient_bars=%d", len(spy_bars or []))
        return None

    try:
        import pandas as pd

        from backend.quant_engine.engines.technical.hmm_engine import analyze_hmm_regime_from_ohlcv

        closes: list[float] = []
        volumes: list[float] = []
        timestamps: list[pd.Timestamp] = []
        for bar in spy_bars:
            raw_close = bar.get("close", bar.get("c"))
            if raw_close is None:
                continue
            try:
                closes.append(float(raw_close))
                volumes.append(float(bar.get("volume", bar.get("v", 0.0)) or 0.0))
                ts_raw = bar.get("t", bar.get("timestamp", bar.get("date")))
                if ts_raw is not None:
                    if isinstance(ts_raw, (int, float)) and float(ts_raw) > 1e11:
                        timestamps.append(pd.to_datetime(int(ts_raw), unit="ms", utc=True))
                    else:
                        timestamps.append(pd.Timestamp(ts_raw))
            except (TypeError, ValueError):
                continue
        if len(closes) < 30:
            return None

        if len(timestamps) == len(closes):
            dates = pd.DatetimeIndex(timestamps)
        else:
            dates = pd.date_range(
                end=pd.Timestamp.now("UTC").normalize(),
                periods=len(closes),
                freq="D",
            )
        frame = pd.DataFrame(
            {"close": closes, "volume": volumes, "date": dates},
        )
        result = analyze_hmm_regime_from_ohlcv(frame)
        if result is None or not getattr(result, "ok", False):
            logger.info("desk_regime.hmm_not_ok result=%s", result)
            return None

        raw_label = str(getattr(result, "current_label", "") or "").upper().strip()
        transition_risk = float(getattr(result, "transition_risk", 1.0) or 1.0)
        signal = str(getattr(result, "regime_signal", "") or "").upper().strip()
        return {
            "label": _HMM_LABEL_MAP.get(raw_label, raw_label or "MEAN_REVERT"),
            "raw_label": raw_label,
            "signal": signal,
            "transition_risk": round(transition_risk, 4),
            "confidence": round(max(0.0, 1.0 - transition_risk), 4),
        }
    except Exception as exc:
        logger.warning("desk_regime.hmm_failed error=%s", str(exc)[:200])
        return None


def _vix_bucket(vix: float | None) -> str | None:
    if vix is None:
        return None
    if vix < _VIX_LOW:
        return "low"
    if vix < _VIX_NORMAL:
        return "normal"
    if vix < _VIX_ELEVATED:
        return "elevated"
    if vix < _VIX_HIGH:
        return "high"
    return "extreme"


def _macro_stress_level(macro_context: dict[str, Any] | None) -> str | None:
    if not isinstance(macro_context, dict):
        return None
    calendar = macro_context.get("calendar")
    if not isinstance(calendar, dict):
        return None
    high_impact = calendar.get("high_impact_14d")
    if not isinstance(high_impact, int | float):
        return None
    if high_impact >= _MACRO_STRESS_HIGH:
        return "high"
    if high_impact >= _MACRO_STRESS_MEDIUM:
        return "medium"
    return "low"


def _breadth_state(universe_summary: dict[str, Any] | None) -> tuple[str | None, float | None]:
    if not isinstance(universe_summary, dict):
        return None, None
    raw = universe_summary.get("bullish_share")
    if not isinstance(raw, int | float):
        return None, None
    share = float(raw)
    if share >= _BREADTH_BULLISH:
        return "bullish", share
    if share <= _BREADTH_BEARISH:
        return "bearish", share
    return "mixed", share


def detect_desk_regime(
    *,
    spy_hmm: dict[str, Any] | None = None,
    vix: float | None = None,
    macro_context: dict[str, Any] | None = None,
    universe_summary: dict[str, Any] | None = None,
    method_version: str = METHOD_VERSION,
) -> DeskRegimeSnapshot:
    """Deterministically unify components into one desk regime snapshot.

    All inputs are optional. The label is chosen defensively (crisis/bear first)
    and confidence reflects how many independent components corroborate it.
    """
    vix_bucket = _vix_bucket(vix)
    macro_stress = _macro_stress_level(macro_context)
    breadth, bullish_share = _breadth_state(universe_summary)
    hmm_label = str(spy_hmm.get("label")) if isinstance(spy_hmm, dict) else None
    hmm_signal = str(spy_hmm.get("signal")) if isinstance(spy_hmm, dict) else None

    components: dict[str, Any] = {
        "hmm_label": hmm_label,
        "hmm_signal": hmm_signal,
        "hmm_transition_risk": (
            spy_hmm.get("transition_risk") if isinstance(spy_hmm, dict) else None
        ),
        "vix": vix,
        "vix_bucket": vix_bucket,
        "macro_stress": macro_stress,
        "breadth": breadth,
        "bullish_share": bullish_share,
    }

    reason_codes: list[str] = []
    available = sum(1 for v in (hmm_label, vix_bucket, macro_stress, breadth) if v is not None)

    crisis = (
        hmm_label == "CRISIS"
        or vix_bucket == "extreme"
        or (vix_bucket == "high" and breadth == "bearish")
    )
    bear_volatile = (not crisis) and vix_bucket in {"elevated", "high"} and breadth == "bearish"
    recovery = ((not crisis) and breadth == "bullish" and vix_bucket in {"elevated", "high"}) or (
        (not crisis)
        and hmm_signal == "SHIFTING"
        and breadth == "bullish"
        and vix_bucket == "elevated"
    )
    bull_quiet = (
        (not crisis)
        and breadth == "bullish"
        and vix_bucket in {"low", "normal"}
        and hmm_label != "CRISIS"
    )

    label: DeskRegimeLabel
    agree = 0
    if crisis:
        label = "CRISIS"
        if hmm_label == "CRISIS":
            reason_codes.append("hmm_crisis_state")
            agree += 1
        if vix_bucket == "extreme":
            reason_codes.append("vix_extreme")
            agree += 1
        if vix_bucket == "high":
            reason_codes.append("vix_high")
            agree += 1
        if breadth == "bearish":
            reason_codes.append("breadth_bearish")
            agree += 1
        if macro_stress == "high":
            reason_codes.append("macro_stress_high")
            agree += 1
    elif bear_volatile:
        label = "BEAR_VOLATILE"
        if vix_bucket in {"elevated", "high"}:
            reason_codes.append(f"vix_{vix_bucket}")
            agree += 1
        if breadth == "bearish":
            reason_codes.append("breadth_bearish")
            agree += 1
        if macro_stress in {"high", "medium"}:
            reason_codes.append(f"macro_stress_{macro_stress}")
            agree += 1
    elif recovery:
        label = "RECOVERY"
        reason_codes.append("breadth_bullish")
        agree += 1
        if vix_bucket in {"elevated", "high"}:
            reason_codes.append(f"vix_{vix_bucket}_declining")
            agree += 1
        if hmm_signal == "SHIFTING":
            reason_codes.append("hmm_shifting")
            agree += 1
    elif bull_quiet:
        label = "BULL_QUIET"
        reason_codes.append("breadth_bullish")
        agree += 1
        if vix_bucket in {"low", "normal"}:
            reason_codes.append(f"vix_{vix_bucket}")
            agree += 1
        if hmm_label in {"BULL_QUIET", "MEAN_REVERT"}:
            reason_codes.append("hmm_constructive")
            agree += 1
    else:
        label = "TRANSITION"
        reason_codes.append("mixed_or_conflicting_signals")
        if breadth == "mixed":
            reason_codes.append("breadth_mixed")
        if hmm_signal in {"SHIFTING", "CRITICAL"}:
            reason_codes.append("hmm_unstable")

    if available == 0:
        reason_codes.append("insufficient_inputs")
        confidence = 0.0
    else:
        confidence = min(0.95, 0.35 + 0.15 * agree)
        if available <= 1:
            confidence = min(confidence, 0.45)

    logger.info(
        "desk_regime.detected label=%s confidence=%.2f components_available=%d reasons=%s",
        label,
        confidence,
        available,
        ",".join(reason_codes),
    )

    return DeskRegimeSnapshot(
        label=label,
        confidence=round(confidence, 4),
        components=components,
        reason_codes=reason_codes,
        method_version=method_version,
    )
