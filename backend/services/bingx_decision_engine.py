from __future__ import annotations

from typing import Any, Literal

"""BingX multi-module decision engine.

Replaces the historical VSA/heuristic filter with a rule-based engine that
inspects the *full* :class:`BingXCandidateAnalysis` — venue, technical, options,
predictive, and L2 — and returns a single ``BingXDecision`` carrying:

- ``decision``      — ``ALLOW`` / ``SIZE_DOWN`` / ``BLOCK`` / ``INSUFFICIENT_DATA``
- ``direction``     — ``LONG`` / ``SHORT`` / ``FLAT``
- ``confidence``    — final aggregated confidence
- ``score_total``   — weighted aggregate across available modules
- ``module_scores`` — per-module scores (venue/technical/options/predictive/l2/risk)
- ``reason_codes``  — every gate the candidate passed *or* failed

Survival contract:

- Any single missing module degrades to a 0.0 contribution, never raises.
- Insufficient evidence (< 2 core modules) returns ``INSUFFICIENT_DATA`` — the
  engine never invents a direction from one motor.
- The L2 gate is policy-aware: dry-run mode warns but does not BLOCK; live
  mode BLOCKS equity perps without depth. The toggle is env-controlled.

Configurable thresholds (env-overridable, clamped server-side):

- ``BINGX_MIN_DECISION_SCORE``         — minimum aggregate score for ALLOW.
- ``BINGX_MIN_PREDICTIVE_CONFIDENCE``  — predictive confidence floor.
- ``BINGX_REQUIRE_L2_FOR_EQUITY_LIVE`` — when true, live mode BLOCKS equity
  perps without an L2 snapshot.
"""


import math
import os
from dataclasses import asdict, dataclass, field
from datetime import datetime

from backend.config.bingx_hybrid_motors_calibration import (
    HYBRID_CONSENSUS_LONG,
    HYBRID_CONSENSUS_SHORT,
)
from backend.config.bingx_hybrid_motors_calibration import (
    TECHNICAL_WEIGHT_MATRIX as _HYBRID_CALIB_WEIGHT_MATRIX,
)
from backend.config.bingx_options_combiner_calibration import (
    combiner_contradiction_penalty,
    combiner_entry_score,
    combiner_extreme_blocks,
    combiner_extreme_risk_penalty,
    combiner_options_score_weight,
    combiner_quality_weight,
)
from backend.config.bingx_risk_sizing_v2_calibration import dark_pool_min_confidence
from backend.config.logger_setup import get_logger
from backend.domain.probabilistic_models import PredictiveOptionsBundleReport
from backend.services.bingx_candidate_analysis import BingXCandidateAnalysis
from backend.services.bingx_gex_wall_stop import compute_gex_wall_stop
from backend.services.bingx_risk_sizing_v2 import risk_sizing_multiplier
from backend.services.calibration.bayesian_kelly_sizer import bayesian_kelly_for_decide
from backend.services.hybrid_motors_service import hybrid_bias_from_block

logger = get_logger(__name__)


# ── Constants & enums ────────────────────────────────────────────────────────

DecisionStatus = Literal["ALLOW", "SIZE_DOWN", "BLOCK", "INSUFFICIENT_DATA"]
Direction = Literal["LONG", "SHORT", "FLAT"]
ExecutionMode = Literal["dry_run", "live"]

# Default thresholds. Production deployments override via env.
_DEFAULT_MIN_DECISION_SCORE = 0.55
_DEFAULT_MIN_PREDICTIVE_CONFIDENCE = 0.50
_DEFAULT_SIZE_DOWN_BAND = 0.10  # score within [min - band, min] → SIZE_DOWN
_EQUITY_MARKET_TYPES = frozenset({"stock_perp", "stock_index_perp"})

# Module weights for ``score_total``. Sum is normalised to the *available*
# subset so missing engines don't artificially deflate the result.
_DEFAULT_WEIGHTS: dict[str, float] = {
    "venue": 0.15,
    "technical": 0.25,
    "options": 0.20,
    "predictive": 0.25,
    "l2": 0.10,
    "risk": 0.05,
}

# Stable reason codes — runbooks and dashboards match on these literals.
REASON_INSUFFICIENT_CORE_MOTORS = "insufficient_core_motors"
REASON_VENUE_UNAVAILABLE = "venue_unavailable"
REASON_L2_REQUIRED_FOR_EQUITY_LIVE = "l2_required_for_equity_live"
REASON_PREDICTIVE_BELOW_FLOOR = "predictive_below_floor"
REASON_TECHNICAL_NOT_CONFIRMING = "technical_not_confirming"
REASON_OPTIONS_CONTRADICTS_DIRECTION = "options_contradicts_direction"
REASON_TECHNICAL_DOES_NOT_COMPENSATE = "technical_does_not_compensate_options"
REASON_LOW_AGGREGATE_SCORE = "low_aggregate_score"
REASON_PARTIAL_CONFLUENCE = "partial_confluence"
REASON_MEDIUM_DATA_QUALITY = "medium_data_quality"
REASON_L2_QUALITY_LOW = "l2_quality_low"
REASON_DIRECTION_CONFLICT = "direction_conflict"
REASON_DIRECTION_NEUTRAL = "predictive_neutral"
REASON_TECHNICAL_CONSENSUS_NEUTRAL = "technical_consensus_neutral"
REASON_NO_PREDICTIVE_DIRECTION = "no_predictive_direction"
REASON_FULL_CONFLUENCE = "full_confluence"
REASON_VENUE_TECHNICAL_ALIGNED = "venue_technical_aligned"
REASON_GAMMA_NEGATIVE_REGIME_BLOCK = "gamma_negative_regime_block"
REASON_TAIL_RISK_BLOCK = "tail_risk_block"
REASON_SHADOW_DELTA_BLOCK = "shadow_delta_block"
REASON_GEX_WALL_STOP_ACTIVE = "gex_wall_stop_active"
REASON_GEX_WALL_INVALIDATION = "gex_wall_invalidation"
REASON_DARK_POOL_CONFIRMS = "dark_pool_confirms"
REASON_DARK_POOL_CONTRADICTS = "dark_pool_contradicts"
REASON_SPEED_INSTABILITY_SIZE_DOWN = "speed_instability_size_down"
REASON_ZOMMA_RISK_SIZE_DOWN = "zomma_risk_size_down"
REASON_NDDE_CONTRADICTS_DIRECTION = "ndde_contradicts_direction"
REASON_CHARM_FLOW_PENALTY = "charm_flow_penalty"
REASON_CONFLUENCE_DIVERGENCE = "confluence_divergence"
REASON_COMBINER_ENTRY_BLOCKED = "combiner_entry_blocked"
REASON_COMBINER_EXTREME_RISK = "combiner_extreme_risk"
REASON_COMBINER_CONTRADICTION = "combiner_contradiction"
REASON_COMBINER_DIRECTION = "combiner_direction_used"
REASON_CRYPTO_DERIVATIVES_OVERHEATING = "crypto_derivatives_overheating"
REASON_VOLATILITY_PANIC_SIZE_DOWN = "volatility_panic_size_down"
REASON_DEX_ZGL_INVALIDATION = "dex_zgl_invalidation"


# ── Dataclasses ──────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class BingXModuleScores:
    """Per-module score in [0, 1]. ``risk`` is a quality multiplier (higher = better)."""

    venue: float
    technical: float
    options: float
    predictive: float
    l2: float
    risk: float


@dataclass(frozen=True)
class BingXDecision:
    """Final per-candidate decision — JSON-safe via ``to_dict``."""

    symbol: str
    decision: DecisionStatus
    direction: Direction
    confidence: float
    score_total: float
    module_scores: BingXModuleScores
    reason_codes: list[str] = field(default_factory=list)
    market_type: str = ""
    sizing_multiplier: float = 1.0
    combiner_size_pct: float | None = None
    gex_wall_stop_price: float | None = None
    bayesian_kelly_pct: float | None = None

    def to_dict(self) -> dict[str, Any]:
        out = asdict(self)
        out["reason_codes"] = list(self.reason_codes)
        return out


@dataclass(frozen=True)
class BingXDecisionConfig:
    """Resolved thresholds for one engine invocation.

    Values are clamped at construction time so the rest of the engine can
    treat them as already-safe.
    """

    min_decision_score: float = _DEFAULT_MIN_DECISION_SCORE
    min_predictive_confidence: float = _DEFAULT_MIN_PREDICTIVE_CONFIDENCE
    require_l2_for_equity_live: bool = True
    size_down_band: float = _DEFAULT_SIZE_DOWN_BAND

    @classmethod
    def from_env(cls: type[BingXDecisionConfig]) -> BingXDecisionConfig:
        return cls(
            min_decision_score=_env_float(
                "BINGX_MIN_DECISION_SCORE",
                _DEFAULT_MIN_DECISION_SCORE,
                min_val=0.0,
                max_val=1.0,
            ),
            min_predictive_confidence=_env_float(
                "BINGX_MIN_PREDICTIVE_CONFIDENCE",
                _DEFAULT_MIN_PREDICTIVE_CONFIDENCE,
                min_val=0.0,
                max_val=1.0,
            ),
            require_l2_for_equity_live=_env_bool(
                "BINGX_REQUIRE_L2_FOR_EQUITY_LIVE",
                default=True,
            ),
            size_down_band=_DEFAULT_SIZE_DOWN_BAND,
        )


# ── Env helpers ──────────────────────────────────────────────────────────────


def _env_float(name: str, default: float, *, min_val: float, max_val: float) -> float:
    raw = os.getenv(name)
    if raw is None or raw.strip() == "":
        return default
    try:
        value = float(raw)
    except (TypeError, ValueError):
        return default
    if not math.isfinite(value):
        return default
    return max(min_val, min(max_val, value))


def _env_bool(name: str, *, default: bool) -> bool:
    raw = os.getenv(name)
    if raw is None or raw.strip() == "":
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


# ── Safe value extraction ────────────────────────────────────────────────────


def _safe_float(value: object) -> float | None:
    if value is None:
        return None
    try:
        out = float(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return None
    if not math.isfinite(out):
        return None
    return out


def _clamp_unit(value: float | None) -> float:
    if value is None:
        return 0.0
    return max(0.0, min(1.0, value))


# ── Weighted institutional consensus (16-engine voting) ──────────────────────

# Weight matrix: 16 venue engines + 7 hybrid motors (sums to 1.0 via calibration).
_TECHNICAL_WEIGHT_MATRIX: dict[str, float] = dict(_HYBRID_CALIB_WEIGHT_MATRIX)


def _engine_is_ok(block: dict[str, Any] | None) -> bool:
    """Check whether an engine block is present and produced ok=True."""
    return bool(isinstance(block, dict) and block.get("ok"))


def _engine_bias_vote(
    block: dict[str, Any] | None, engine: str
) -> Literal["BULLISH", "BEARISH", "NEUTRAL"]:
    """Extract a directional vote from a single engine's output block.

    Each engine has its own schema; this function normalises them all to
    ``BULLISH`` / ``BEARISH`` / ``NEUTRAL``.  Returns ``NEUTRAL`` when the
    block is missing, errored, or the signal is ambiguous.
    """
    if not isinstance(block, dict) or not block.get("ok"):
        return "NEUTRAL"

    if engine == "hmm_regime":
        signal = str(block.get("regime_signal") or "").upper()
        if signal == "BULLISH":
            return "BULLISH"
        if signal == "BEARISH":
            return "BEARISH"
        label = str(block.get("current_label") or "").upper()
        if label in ("BULLISH", "BULL"):
            return "BULLISH"
        if label in ("BEARISH", "BEAR"):
            return "BEARISH"
        return "NEUTRAL"

    if engine == "ofi":
        regime = str(block.get("regime") or "").upper()
        if regime == "BUYING":
            return "BULLISH"
        if regime == "SELLING":
            return "BEARISH"
        acc_ofi = _safe_float(block.get("latest_accumulated_ofi"))
        if acc_ofi is not None:
            return "BULLISH" if acc_ofi > 0 else "BEARISH" if acc_ofi < 0 else "NEUTRAL"
        return "NEUTRAL"

    if engine == "volume_profile":
        bias = str(block.get("volume_bias") or "").lower()
        if bias == "bullish":
            return "BULLISH"
        if bias == "bearish":
            return "BEARISH"
        above_avwap = block.get("is_above_avwap")
        above_poc = block.get("is_above_poc")
        if above_avwap is True and above_poc is True:
            return "BULLISH"
        if above_avwap is False and above_poc is False:
            return "BEARISH"
        return "NEUTRAL"

    if engine == "vwap_advanced":
        above = block.get("above_vwap")
        if above is True:
            return "BULLISH"
        if above is False:
            return "BEARISH"
        zscore = _safe_float(block.get("price_zscore"))
        if zscore is not None:
            return "BULLISH" if zscore > 0 else "BEARISH" if zscore < 0 else "NEUTRAL"
        price_vs = str(block.get("price_vs_vwap") or "").lower()
        if price_vs == "above":
            return "BULLISH"
        if price_vs == "below":
            return "BEARISH"
        return "NEUTRAL"

    if engine == "lob_dynamics":
        imbalance = _safe_float(block.get("imbalance"))
        if imbalance is not None:
            return "BULLISH" if imbalance > 0 else "BEARISH" if imbalance < 0 else "NEUTRAL"
        bid_ask = _safe_float(block.get("bid_ask_imbalance"))
        if bid_ask is not None:
            return "BULLISH" if bid_ask > 0 else "BEARISH" if bid_ask < 0 else "NEUTRAL"
        return "NEUTRAL"

    if engine == "vsa":
        signal = str(block.get("signal") or "").upper().replace(" ", "_")
        if signal in ("STRONG_BUY", "BUY"):
            return "BULLISH"
        if signal in ("STRONG_SELL", "SELL"):
            return "BEARISH"
        return "NEUTRAL"

    if engine == "fvg":
        bull = int(block.get("bullish_active_count") or 0)
        bear = int(block.get("bearish_active_count") or 0)
        if bull > bear:
            return "BULLISH"
        if bear > bull:
            return "BEARISH"
        return "NEUTRAL"

    if engine == "order_flow_delta":
        bias = str(block.get("delta_bias") or "").upper()
        if bias == "BULLISH":
            return "BULLISH"
        if bias == "BEARISH":
            return "BEARISH"
        latest = _safe_float(block.get("latest_period_delta"))
        if latest is not None:
            return "BULLISH" if latest > 0 else "BEARISH" if latest < 0 else "NEUTRAL"
        return "NEUTRAL"

    if engine == "delta_volume":
        bias = block.get("poc_delta_bias")
        if bias:
            bs = str(bias).upper()
            if bs in ("BULLISH", "BUYING"):
                return "BULLISH"
            if bs in ("BEARISH", "SELLING"):
                return "BEARISH"
        total_bull = _safe_float(block.get("total_bull"))
        total_bear = _safe_float(block.get("total_bear"))
        if total_bull is not None and total_bear is not None and (total_bull + total_bear) > 0:
            return (
                "BULLISH"
                if total_bull > total_bear
                else "BEARISH" if total_bear > total_bull else "NEUTRAL"
            )
        return "NEUTRAL"

    if engine == "vpoc_migration":
        state = str(block.get("state") or "").upper()
        if state == "BULLISH":
            return "BULLISH"
        if state == "BEARISH":
            return "BEARISH"
        poc_delta = _safe_float(block.get("poc_delta"))
        if poc_delta is not None:
            return "BULLISH" if poc_delta > 0 else "BEARISH" if poc_delta < 0 else "NEUTRAL"
        va_width = _safe_float(block.get("value_area_width_delta"))
        if va_width is not None:
            return "BULLISH" if va_width > 0 else "BEARISH" if va_width < 0 else "NEUTRAL"
        return "NEUTRAL"

    if engine == "tpo_skewness":
        skew = _safe_float(block.get("skewness_value"))
        if skew is not None:
            return "BULLISH" if skew > 0 else "BEARISH" if skew < 0 else "NEUTRAL"
        shape = str(block.get("profile_shape") or "").upper()
        if shape == "BULLISH":
            return "BULLISH"
        if shape == "BEARISH":
            return "BEARISH"
        return "NEUTRAL"

    if engine == "single_prints":
        return "NEUTRAL"

    if engine == "vsa_footprint":
        return "NEUTRAL"

    if engine.startswith("avwap_m"):
        decision = str(block.get("decision") or "").upper()
        if decision in ("LONG", "BULLISH"):
            return "BULLISH"
        if decision in ("SHORT", "BEARISH"):
            return "BEARISH"
        return "NEUTRAL"

    if engine.startswith("hybrid_"):
        vote = hybrid_bias_from_block(block)
        if vote == "BULLISH":
            return "BULLISH"
        if vote == "BEARISH":
            return "BEARISH"
        return "NEUTRAL"

    return "NEUTRAL"


def _technical_consensus(
    analysis: BingXCandidateAnalysis,
) -> tuple[float, Direction, dict[str, Any]]:
    """Run weighted institutional consensus across venue + hybrid engines.

    Returns
    -------
    consensus_score : float
        Normalised in [0, 1] where 0.5 is neutral, 0 = maximum BEARISH
        conviction, 1 = maximum BULLISH conviction.
    direction : Direction
        ``LONG`` when consensus > 0.60 (weighted), ``SHORT`` when < -0.60,
        ``FLAT`` otherwise.
    details : dict[str, Any]
        Per-engine vote & weight breakdown for diagnostics.
    """
    venue_tech = analysis.technical.venue_technical
    if not isinstance(venue_tech, dict) or venue_tech.get("status") != "available":
        return 0.5, "FLAT", {"fallback": "venue_technical_unavailable"}

    payload = venue_tech.get("payload")
    if not isinstance(payload, dict):
        return 0.5, "FLAT", {"fallback": "full_payload_missing"}

    avwap_signals = getattr(analysis, "avwap_hybrid_signals", None)
    if isinstance(avwap_signals, dict):
        payload.update(avwap_signals)

    weighted_sum = 0.0
    active_weight = 0.0
    details: dict[str, Any] = {"votes": {}}

    for engine, weight in _TECHNICAL_WEIGHT_MATRIX.items():
        block = payload.get(engine) if isinstance(payload.get(engine), dict) else None
        vote = _engine_bias_vote(block, engine)
        vote_value = 1.0 if vote == "BULLISH" else -1.0 if vote == "BEARISH" else 0.0

        details["votes"][engine] = {
            "vote": vote,
            "weight": weight,
            "ok": bool(block and block.get("ok")),
            "contribution": round(vote_value * weight, 4),
        }

        weighted_sum += vote_value * weight
        if vote != "NEUTRAL":
            active_weight += weight

    raw_consensus = weighted_sum  # range [-1.0, 1.0]

    # Directional threshold — calibrated for verification (0.55) vs legacy 0.60
    if raw_consensus > HYBRID_CONSENSUS_LONG:
        direction: Direction = "LONG"
    elif raw_consensus < HYBRID_CONSENSUS_SHORT:
        direction = "SHORT"
    else:
        direction = "FLAT"

    # Normalise to [0, 1] for the score_total pipeline
    consensus_score = round((raw_consensus + 1.0) / 2.0, 4)

    details["raw_consensus"] = round(raw_consensus, 4)
    details["active_weight"] = round(active_weight, 4)
    details["direction"] = direction

    return consensus_score, direction, details


def _has_full_technical_payload(analysis: BingXCandidateAnalysis) -> bool:
    """True when the 16-engine terminal payload is available for consensus."""
    venue_tech = analysis.technical.venue_technical
    if not isinstance(venue_tech, dict) or venue_tech.get("status") != "available":
        return False
    payload = venue_tech.get("payload")
    return isinstance(payload, dict) and bool(payload)


def _block_status(block: object) -> str:
    """Read the ``status`` attribute on any of the BingX*Block dataclasses."""
    return str(getattr(block, "status", "unavailable") or "unavailable")


# ── Direction helpers ────────────────────────────────────────────────────────


def _predictive_direction(analysis: BingXCandidateAnalysis) -> Direction:
    """Map predictive.signal.directional_bias → engine Direction."""
    signal = analysis.predictive.signal or {}
    if not isinstance(signal, dict):
        return "FLAT"
    bias = str(signal.get("directional_bias") or "").upper()
    if bias == "LONG":
        return "LONG"
    if bias == "SHORT":
        return "SHORT"
    return "FLAT"


def _technical_direction(analysis: BingXCandidateAnalysis) -> Direction:
    """Resolve venue technical direction via weighted institutional consensus.

    When the full 16-engine payload is available, runs the weighted voting
    system (see :func:`_technical_consensus`).  Falls back to the legacy
    summary-based path when the payload is absent (backward compat).
    """
    _, direction, _ = _technical_consensus(analysis)
    if direction != "FLAT":
        return direction

    # Legacy fallback — summary-only path (no full payload)
    venue_tech = analysis.technical.venue_technical or {}
    if not isinstance(venue_tech, dict):
        return "FLAT"
    summary = venue_tech.get("summary") or {}
    if isinstance(summary, dict):
        trend = str(summary.get("trend_direction") or "").lower()
        if trend == "bullish":
            return "LONG"
        if trend == "bearish":
            return "SHORT"
        smc = str(summary.get("smc_bias") or "").upper()
        if smc == "BULLISH":
            return "LONG"
        if smc == "BEARISH":
            return "SHORT"
    return "FLAT"


def _combiner_payload(analysis: BingXCandidateAnalysis) -> dict[str, Any] | None:
    """Read SignalCombiner output attached during candidate analysis."""
    combiner = getattr(analysis.options, "options_combiner", None)
    if isinstance(combiner, dict) and combiner.get("direction") is not None:
        return combiner
    metrics = analysis.options.metrics or {}
    if isinstance(metrics, dict):
        nested = metrics.get("combiner")
        if isinstance(nested, dict):
            return nested
    return None


def _combiner_size_pct(analysis: BingXCandidateAnalysis) -> float | None:
    combiner = _combiner_payload(analysis)
    if not combiner:
        return None
    return _safe_float(combiner.get("size_pct"))


def _confluence_direction(analysis: BingXCandidateAnalysis) -> Direction:
    metrics = analysis.options.metrics or {}
    inner = metrics.get("metrics") if isinstance(metrics.get("metrics"), dict) else metrics
    if not isinstance(inner, dict):
        return "FLAT"
    sig = inner.get("confluence_signal")
    if sig is None:
        return "FLAT"
    conf_sig_str = str(sig).upper().strip()
    if conf_sig_str in ("BULLISH", "LONG", "BUY"):
        return "LONG"
    if conf_sig_str in ("BEARISH", "SHORT", "SELL"):
        return "SHORT"
    return "FLAT"


def _combiner_direction(analysis: BingXCandidateAnalysis) -> Direction:
    """Direction from SignalCombiner when score exceeds entry threshold."""
    combiner = _combiner_payload(analysis)
    if not combiner:
        return "FLAT"
    score = _safe_float(combiner.get("score"))
    if score is None:
        return "FLAT"
    threshold = combiner_entry_score()
    direction = str(combiner.get("direction") or "NEUTRAL").upper()
    if direction == "LONG" and score >= threshold:
        return "LONG"
    if direction == "SHORT" and score <= -threshold:
        return "SHORT"
    return "FLAT"


def _options_direction(analysis: BingXCandidateAnalysis) -> Direction:
    """Read direction from SignalCombiner, then dealer_bias fallback."""
    combiner_dir = _combiner_direction(analysis)
    if combiner_dir != "FLAT":
        return combiner_dir
    metrics = analysis.options.metrics or {}
    if not isinstance(metrics, dict):
        return "FLAT"
    inner = metrics.get("metrics") if isinstance(metrics.get("metrics"), dict) else metrics
    bias = str(inner.get("dealer_bias") or "").upper()
    if bias == "BULLISH":
        return "LONG"
    if bias == "BEARISH":
        return "SHORT"
    return "FLAT"


def _directions_conflict(a: Direction, b: Direction) -> bool:
    """``True`` only when both sides are directional AND opposite."""
    if a == "FLAT" or b == "FLAT":
        return False
    return a != b


# ── Module score extraction ──────────────────────────────────────────────────


def _venue_score(analysis: BingXCandidateAnalysis) -> float:
    if _block_status(analysis.venue) != "available":
        return 0.0
    # The venue block has no explicit quality score — use venue_ta presence as
    # a coarse proxy. Klines available + venue_ta computed = 1.0; klines only
    # (insufficient for TA) = 0.5; nothing = 0.0.
    venue_ta = analysis.venue.venue_ta
    if isinstance(venue_ta, dict) and venue_ta.get("bars_count", 0) >= 22:
        return 1.0
    return 0.5


def _technical_score(analysis: BingXCandidateAnalysis) -> float:
    """Consensus-driven technical score in [0, 1].

    Uses the 16-engine weighted vote when the full payload is available.
    Falls back to the legacy ``technical_quality_score`` from the bridge
    summary, then to the underlying equity TA quality.
    """
    consensus_score, _, details = _technical_consensus(analysis)
    if details.get("fallback") is None:
        return consensus_score
    venue_tech = analysis.technical.venue_technical or {}
    if isinstance(venue_tech, dict) and venue_tech.get("status") == "available":
        return _clamp_unit(_safe_float(venue_tech.get("technical_quality_score")))
    if _block_status(analysis.technical) == "available":
        return _clamp_unit(_safe_float(analysis.technical.quality_score))
    return 0.0


def _options_score(analysis: BingXCandidateAnalysis) -> float:
    if _block_status(analysis.options) != "available":
        return 0.0
    quality = _clamp_unit(_safe_float(analysis.options.quality_score))
    combiner = _combiner_payload(analysis)
    if not combiner:
        return quality
    score = _safe_float(combiner.get("score"))
    if score is None:
        return quality
    directional = _clamp_unit(abs(score) / 100.0)
    qw = combiner_quality_weight()
    ow = combiner_options_score_weight()
    return _clamp_unit(qw * quality + ow * directional)


def _predictive_score(analysis: BingXCandidateAnalysis) -> float:
    if _block_status(analysis.predictive) != "available":
        return 0.0
    signal = analysis.predictive.signal or {}
    if isinstance(signal, dict):
        # Prefer quality_score; fall back to confidence.
        quality = _safe_float(signal.get("quality_score"))
        if quality is not None:
            return _clamp_unit(quality)
        return _clamp_unit(_safe_float(signal.get("confidence")))
    return _clamp_unit(_safe_float(analysis.predictive.quality_score))


def _l2_score(analysis: BingXCandidateAnalysis) -> float:
    if _block_status(analysis.l2) != "available":
        return 0.0
    return _clamp_unit(_safe_float(analysis.l2.quality_score))


def _risk_score(
    analysis: BingXCandidateAnalysis,
    *,
    direction_conflict: bool,
    options_contradicts: bool,
    technical_compensates: bool,
    has_other_signals: bool,
) -> float:
    """Composite quality multiplier.

    Starts at 1.0 and applies penalties for direction conflicts, missing
    technical compensation, and low L2 quality on equity perps. The final
    value caps at [0, 1] so callers can use it as a confidence multiplier.

    Returns ``0.0`` when no other module produced a non-zero score — risk is
    a multiplier on real signal, and a non-zero risk score with nothing to
    multiply would falsely inflate ``score_total``.
    """
    if not has_other_signals:
        return 0.0
    score = 1.0
    if direction_conflict:
        score -= 0.4
    if options_contradicts and not technical_compensates:
        score -= 0.3
    # Penalise medium L2 quality on equity perps (proxies execution risk).
    if analysis.market_type in _EQUITY_MARKET_TYPES and _block_status(analysis.l2) == "available":
        l2_q = _safe_float(analysis.l2.quality_score) or 0.0
        if l2_q < 0.4:
            score -= 0.15
    return max(0.0, min(1.0, score))


def _core_motors_available(analysis: BingXCandidateAnalysis) -> int:
    """Count of core motors (venue, technical, options, predictive) with status=available."""
    count = 0
    if _block_status(analysis.venue) == "available":
        count += 1
    # Technical is available if EITHER the underlying TA block OR the venue
    # bridge succeeded — both carry actionable signal.
    venue_tech = analysis.technical.venue_technical or {}
    venue_tech_ok = isinstance(venue_tech, dict) and venue_tech.get("status") == "available"
    if _block_status(analysis.technical) == "available" or venue_tech_ok:
        count += 1
    if _block_status(analysis.options) == "available":
        count += 1
    if _block_status(analysis.predictive) == "available":
        count += 1
    return count


# ── Aggregate score ──────────────────────────────────────────────────────────


def _aggregate_score(scores: BingXModuleScores) -> float:
    """Weighted average over modules whose score is > 0.

    A 0 module is treated as "no signal" and excluded from the denominator —
    this prevents a single missing engine from collapsing an otherwise solid
    candidate while still rewarding higher coverage.
    """
    weighted_sum = 0.0
    weight_sum = 0.0
    for name, weight in _DEFAULT_WEIGHTS.items():
        value = getattr(scores, name)
        if value > 0.0:
            weighted_sum += value * weight
            weight_sum += weight
    if weight_sum <= 0:
        return 0.0
    return round(weighted_sum / weight_sum, 4)


# ── Main entry point ─────────────────────────────────────────────────────────


def decide(
    analysis: BingXCandidateAnalysis,
    *,
    mode: ExecutionMode = "live",
    config: BingXDecisionConfig | None = None,
) -> BingXDecision:
    """Run the decision engine on one ``BingXCandidateAnalysis``.

    ``mode="live"`` enables the L2 BLOCK gate for equity perps when
    :attr:`BingXDecisionConfig.require_l2_for_equity_live` is True. ``mode``
    "dry_run" relaxes the gate (warning-only) so paper-trading still produces
    decisions that surface the rest of the cascade.
    """
    cfg = config or BingXDecisionConfig.from_env()
    reason_codes: list[str] = []

    predictive_dir = _predictive_direction(analysis)
    technical_dir = _technical_direction(analysis)
    options_dir = _options_direction(analysis)
    combiner_dir = _combiner_direction(analysis)

    # Direction priority: predictivo > técnico (23 motores) > combiner > confluencia
    if predictive_dir != "FLAT":
        direction: Direction = predictive_dir
    elif technical_dir != "FLAT":
        direction = technical_dir
    elif combiner_dir != "FLAT":
        direction = combiner_dir
        reason_codes.append(REASON_COMBINER_DIRECTION)
    else:
        conf_dir = _confluence_direction(analysis)
        direction = conf_dir if conf_dir != "FLAT" else "FLAT"

    # Log when the 16-engine consensus ran but produced no clear signal
    if _has_full_technical_payload(analysis):
        _, consensus_dir, _ = _technical_consensus(analysis)
        if consensus_dir == "FLAT":
            reason_codes.append(REASON_TECHNICAL_CONSENSUS_NEUTRAL)

    direction_conflict = _directions_conflict(predictive_dir, technical_dir)
    options_contradicts = (
        predictive_dir != "FLAT"
        and options_dir != "FLAT"
        and _directions_conflict(predictive_dir, options_dir)
    )
    technical_compensates = predictive_dir != "FLAT" and technical_dir == predictive_dir

    # ── Gamma Flip Guardrail ───────────────────────────────────────────────
    spot_price = None
    if (
        analysis.venue.klines
        and isinstance(analysis.venue.klines, list | tuple)
        and len(analysis.venue.klines) > 0
    ):
        spot_price = _safe_float(analysis.venue.klines[-1].get("close"))
    if spot_price is None and isinstance(analysis.underlying.quote, dict):
        spot_price = _safe_float(analysis.underlying.quote.get("price"))

    metrics = analysis.options.metrics or {}
    inner_metrics = metrics.get("metrics") if isinstance(metrics.get("metrics"), dict) else metrics
    gamma_flip = _safe_float(
        inner_metrics.get("gamma_flip") if isinstance(inner_metrics, dict) else None
    )

    if spot_price is not None and gamma_flip is not None and direction != "FLAT":
        gamma_regime = "Pos" if spot_price > gamma_flip else "Neg"
        gamma_contradicts = False

        if (direction == "LONG" and spot_price < gamma_flip) or (
            direction == "SHORT" and spot_price > gamma_flip
        ):
            gamma_contradicts = True

        if gamma_contradicts:
            options_contradicts = True

        logger.info(
            "[Gamma Engine] Spot: %.2f | Flip: %.2f | Regime: %s | Contradicts: %s",
            spot_price,
            gamma_flip,
            gamma_regime,
            gamma_contradicts,
        )

    venue_score_val = _venue_score(analysis)
    technical_score_val = _technical_score(analysis)
    options_score_val = _options_score(analysis)
    predictive_score_val = _predictive_score(analysis)
    l2_score_val = _l2_score(analysis)
    has_other_signals = any(
        score > 0.0
        for score in (
            venue_score_val,
            technical_score_val,
            options_score_val,
            predictive_score_val,
            l2_score_val,
        )
    )
    risk_score_val = _risk_score(
        analysis,
        direction_conflict=direction_conflict,
        options_contradicts=options_contradicts,
        technical_compensates=technical_compensates,
        has_other_signals=has_other_signals,
    )

    module_scores = BingXModuleScores(
        venue=venue_score_val,
        technical=technical_score_val,
        options=options_score_val,
        predictive=predictive_score_val,
        l2=l2_score_val,
        risk=risk_score_val,
    )
    score_total = _aggregate_score(module_scores)

    # --- ML SCORE BLEND ---
    try:
        from backend.ml_engine.models.random_forest_classifier import TradePredictor

        predictor = TradePredictor()
        if predictor.load():
            indicators = {
                "venue_score": venue_score_val,
                "technical_score": technical_score_val,
                "options_score": options_score_val,
                "predictive_score": predictive_score_val,
                "l2_score": l2_score_val,
                "risk_score": risk_score_val,
                "score_total": score_total,
            }
            ml_prob = predictor.predict_prob(indicators)
            score_total = round(0.80 * score_total + 0.20 * ml_prob, 4)
            if ml_prob < 0.45:
                reason_codes.append("ml_prob_low")
    except Exception as exc:
        logger.debug("bingx_bot.ml_predict_failed error=%s", exc)

    # ── Confluence Multiplier & Divergence Veto ──────────────────────────────
    confluence_score_val = (
        _safe_float(inner_metrics.get("confluence_score"))
        if isinstance(inner_metrics, dict)
        else None
    )
    confluence_sig_val = (
        inner_metrics.get("confluence_signal") if isinstance(inner_metrics, dict) else None
    )

    if confluence_sig_val is not None and predictive_dir != "FLAT":
        conf_sig_str = str(confluence_sig_val).upper().strip()
        conf_dir: Direction = "FLAT"
        if conf_sig_str in ("BULLISH", "LONG", "BUY"):
            conf_dir = "LONG"
        elif conf_sig_str in ("BEARISH", "SHORT", "SELL"):
            conf_dir = "SHORT"

        if conf_dir != "FLAT":
            if conf_dir == predictive_dir:
                score_total = min(1.0, round(score_total + 0.20, 4))
            else:
                reason_codes.append(REASON_CONFLUENCE_DIVERGENCE)
                return BingXDecision(
                    symbol=analysis.venue_symbol,
                    decision="BLOCK",
                    direction="FLAT",
                    confidence=0.0,
                    score_total=0.0,
                    module_scores=module_scores,
                    reason_codes=reason_codes,
                    market_type=analysis.market_type,
                )

    # ── Days To Expiry (DTE) Extraction ────────────────────────────────────
    dte = None
    if isinstance(metrics, dict):
        expiry_used = metrics.get("chain_quality", {}).get("expiry_used")
        fetched_at = metrics.get("fetched_at")
        if expiry_used and fetched_at:
            try:
                exp_date = datetime.strptime(expiry_used[:10], "%Y-%m-%d")
                fetch_date = datetime.strptime(fetched_at[:10], "%Y-%m-%d")
                dte = max((exp_date - fetch_date).days, 0)
            except Exception:
                pass

    # Dynamic Charm penalty weight based on DTE
    charm_penalty = 0.15
    if dte is not None:
        if dte >= 7:
            charm_penalty = 0.03
        elif dte <= 1:
            charm_penalty = 0.40
        else:
            charm_penalty = round(0.03 + (0.40 - 0.03) * (7 - dte) / 6.0, 4)

    # ── Charm Flow Penalty ──────────────────────────────────────────────────
    charm_flow_val = inner_metrics.get("charm_flow") if isinstance(inner_metrics, dict) else None
    charm_contradicts = False
    if charm_flow_val is not None and direction != "FLAT":
        if isinstance(charm_flow_val, int | float):
            if (direction == "LONG" and charm_flow_val < 0) or (
                direction == "SHORT" and charm_flow_val > 0
            ):
                charm_contradicts = True
        elif isinstance(charm_flow_val, str):
            cf_upper = charm_flow_val.upper().strip()
            if (direction == "LONG" and cf_upper in ("BEARISH", "SHORT", "NEGATIVE")) or (
                direction == "SHORT" and cf_upper in ("BULLISH", "LONG", "POSITIVE")
            ):
                charm_contradicts = True

    if charm_contradicts:
        score_total = max(0.0, round(score_total - charm_penalty, 4))
        reason_codes.append(REASON_CHARM_FLOW_PENALTY)
        logger.info(
            "[Charm Penalty Audit] dte=%s charm_penalty=%.4f score_total=%.4f",
            dte,
            charm_penalty,
            score_total,
        )

    if direction_conflict:
        reason_codes.append(REASON_DIRECTION_CONFLICT)

    needs_legacy_size_down = False
    combiner_size = _combiner_size_pct(analysis)
    combiner_cap: float | None = None

    # ── SignalCombiner gates ───────────────────────────────────────────────
    combiner_data = _combiner_payload(analysis)
    if combiner_data:
        agreement = str(combiner_data.get("agreement_level") or "").lower()
        if agreement == "contradiction":
            score_total = max(
                0.0,
                round(score_total - combiner_contradiction_penalty(), 4),
            )
            reason_codes.append(REASON_COMBINER_CONTRADICTION)

        risk_level = str(combiner_data.get("risk_level") or "").upper()
        if risk_level == "EXTREME":
            reason_codes.append(REASON_COMBINER_EXTREME_RISK)
            if combiner_extreme_blocks():
                return BingXDecision(
                    symbol=analysis.venue_symbol,
                    decision="BLOCK",
                    direction="FLAT",
                    confidence=0.0,
                    score_total=score_total,
                    module_scores=module_scores,
                    reason_codes=reason_codes,
                    market_type=analysis.market_type,
                    combiner_size_pct=combiner_size,
                )
            score_total = max(
                0.0,
                round(score_total - combiner_extreme_risk_penalty(), 4),
            )

        if combiner_data.get("entry_allowed") is False:
            needs_legacy_size_down = True
            reason_codes.append(REASON_COMBINER_ENTRY_BLOCKED)

        if combiner_size is not None and 0.0 < combiner_size < 1.0:
            combiner_cap = combiner_size

    # ── Volatility Sizing Adjustments (VRP & Skew) ──────────────────────────
    vrp_val = _safe_float(inner_metrics.get("vrp")) if isinstance(inner_metrics, dict) else None
    skew_25d_val = (
        _safe_float(inner_metrics.get("skew_25d")) if isinstance(inner_metrics, dict) else None
    )
    if skew_25d_val is None and isinstance(metrics.get("iv_surface"), dict):
        skew_25d_val = _safe_float(metrics["iv_surface"].get("skew_25d")) or _safe_float(
            metrics["iv_surface"].get("skew")
        )

    if (vrp_val is not None and vrp_val < -0.15) or (
        skew_25d_val is not None and skew_25d_val > 0.15
    ):
        needs_legacy_size_down = True
        reason_codes.append(REASON_VOLATILITY_PANIC_SIZE_DOWN)

    # Si el activo no tiene opciones disponibles, usar un reporte neutral (safe fallback)
    # para evitar bloqueos duros, pero el score de opciones será 0.0 impactando el total.
    default_safe_report = PredictiveOptionsBundleReport(
        gamma_flip_level=0.0,
        is_gamma_negative_regime=False,
        shadow_delta_imbalance=0.0,
        zero_day_pinning_strike=0.0,
        speed_instability_warning=False,
        tail_risk_severity="LOW",
        zomma_risk_score=0.0,
        pinning_probability=0.0,
    )
    bundle_report = getattr(analysis.options, "predictive_report", None) or default_safe_report

    # ── Diagnóstico Institucional: Trazar mapeo de subyacente ──────────────
    ndde_log = inner_metrics.get("ndde") if isinstance(inner_metrics, dict) else None
    charm_flow_log = inner_metrics.get("charm_flow") if isinstance(inner_metrics, dict) else None
    ip99_log = (
        inner_metrics.get("implied_percentile_99") if isinstance(inner_metrics, dict) else None
    )

    # Technical consensus for logging
    _, consensus_dir, consensus_details = _technical_consensus(analysis)
    consensus_raw = consensus_details.get("raw_consensus", 0.0)
    consensus_active = consensus_details.get("active_weight", 0.0)
    consensus_dir_str = str(consensus_dir) if consensus_details.get("fallback") is None else "N/A"

    logger.info(
        "DIAGNOSTICO INSTITUCIONAL | "
        "Ticker BingX: %s | "
        "Subyacente Massive: %s | "
        "Gamma Flip: %.4f | "
        "Tail Risk: %s | "
        "Shadow Delta: %.4f | "
        "Zero-Day Gamma: %.4f | "
        "Speed Instability: %s | "
        "Zomma Risk: %.4f | "
        "NDDE: %s | "
        "Charm Flow: %s | "
        "Implied Percentile 99: %s | "
        "Tech Consensus: %.2f (%s) | "
        "Active Weight: %.2f | "
        "Confluence Score: %s | "
        "Confluence Signal: %s | "
        "VRP: %s | "
        "Skew 25d: %s",
        analysis.venue_symbol,
        analysis.underlying_symbol,
        bundle_report.gamma_flip_level,
        bundle_report.tail_risk_severity,
        bundle_report.shadow_delta_imbalance,
        bundle_report.zero_day_pinning_strike,
        bundle_report.speed_instability_warning,
        bundle_report.zomma_risk_score,
        f"{ndde_log:.4f}" if isinstance(ndde_log, int | float) else str(ndde_log),
        str(charm_flow_log),
        f"{ip99_log:.4f}" if isinstance(ip99_log, int | float) else str(ip99_log),
        consensus_raw,
        consensus_dir_str,
        consensus_active,
        str(confluence_score_val),
        str(confluence_sig_val),
        f"{vrp_log:.4f}" if (vrp_log := vrp_val) is not None else str(vrp_log),
        f"{skew_25d_log:.4f}" if (skew_25d_log := skew_25d_val) is not None else str(skew_25d_log),
    )

    # GATE A: Hard Blockers (Tail Risk & Gamma Regime)
    if bundle_report.tail_risk_severity == "CRITICAL":
        reason_codes.append(REASON_TAIL_RISK_BLOCK)
        return BingXDecision(
            symbol=analysis.venue_symbol,
            decision="BLOCK",
            direction="FLAT",
            confidence=0.0,
            score_total=0.0,
            module_scores=module_scores,
            reason_codes=reason_codes,
            market_type=analysis.market_type,
        )

    # Veto NDDE
    ndde_val = _safe_float(inner_metrics.get("ndde")) if isinstance(inner_metrics, dict) else None
    if (
        ndde_val is not None
        and direction != "FLAT"
        and ((direction == "LONG" and ndde_val < 0.0) or (direction == "SHORT" and ndde_val > 0.0))
    ):
        reason_codes.append(REASON_NDDE_CONTRADICTS_DIRECTION)
        return BingXDecision(
            symbol=analysis.venue_symbol,
            decision="BLOCK",
            direction="FLAT",
            confidence=0.0,
            score_total=0.0,
            module_scores=module_scores,
            reason_codes=reason_codes,
            market_type=analysis.market_type,
        )

    # ── Crypto Derivatives Gate ──────────────────────────────────────────────
    deriv_block = getattr(analysis, "exchange_derivatives", None)
    if (
        deriv_block
        and getattr(deriv_block, "status", "unavailable") == "available"
        and direction != "FLAT"
    ):
        providers = getattr(deriv_block, "providers", ()) or ()
        funding_rates = []
        for p in providers:
            fr = _safe_float(p.get("funding_rate"))
            if fr is not None:
                funding_rates.append(fr)
        avg_funding = sum(funding_rates) / len(funding_rates) if funding_rates else 0.0

        any_liq_block = False
        for p in providers:
            if direction == "LONG":
                liq = _safe_float(p.get("long_liquidations_usd"))
                if liq is not None and liq > 100000.0:
                    any_liq_block = True
                    break
            elif direction == "SHORT":
                liq = _safe_float(p.get("short_liquidations_usd"))
                if liq is not None and liq > 100000.0:
                    any_liq_block = True
                    break

        funding_overheating = False
        if (direction == "LONG" and avg_funding > 0.0008) or (
            direction == "SHORT" and avg_funding < -0.0008
        ):
            funding_overheating = True

        if funding_overheating or any_liq_block:
            reason_codes.append(REASON_CRYPTO_DERIVATIVES_OVERHEATING)
            return BingXDecision(
                symbol=analysis.venue_symbol,
                decision="BLOCK",
                direction="FLAT",
                confidence=0.0,
                score_total=0.0,
                module_scores=module_scores,
                reason_codes=reason_codes,
                market_type=analysis.market_type,
            )

    # ── DEX/ZGL Invalidation Gate ────────────────────────────────────────────
    zgl = None
    if isinstance(inner_metrics, dict):
        zgl = (
            _safe_float(inner_metrics.get("zero_gamma"))
            or _safe_float(inner_metrics.get("zero_gamma_level"))
            or _safe_float(inner_metrics.get("dex_flip_level"))
        )
    if zgl is None:
        zgl = _safe_float(bundle_report.gamma_flip_level)

    total_dex_val = (
        _safe_float(inner_metrics.get("total_dex")) if isinstance(inner_metrics, dict) else None
    )

    if (
        direction == "LONG"
        and spot_price is not None
        and zgl is not None
        and total_dex_val is not None
        and spot_price < zgl
        and total_dex_val < 0.0
    ):
        reason_codes.append(REASON_DEX_ZGL_INVALIDATION)
        return BingXDecision(
            symbol=analysis.venue_symbol,
            decision="BLOCK",
            direction="FLAT",
            confidence=0.0,
            score_total=0.0,
            module_scores=module_scores,
            reason_codes=reason_codes,
            market_type=analysis.market_type,
        )

    if direction == "LONG" and bundle_report.is_gamma_negative_regime:
        reason_codes.append(REASON_GAMMA_NEGATIVE_REGIME_BLOCK)
        return BingXDecision(
            symbol=analysis.venue_symbol,
            decision="BLOCK",
            direction="FLAT",
            confidence=0.0,
            score_total=0.0,
            module_scores=module_scores,
            reason_codes=reason_codes,
            market_type=analysis.market_type,
        )

    if (direction == "LONG" and bundle_report.shadow_delta_imbalance < -0.8) or (
        direction == "SHORT" and bundle_report.shadow_delta_imbalance > 0.8
    ):
        reason_codes.append(REASON_SHADOW_DELTA_BLOCK)
        return BingXDecision(
            symbol=analysis.venue_symbol,
            decision="BLOCK",
            direction="FLAT",
            confidence=0.0,
            score_total=0.0,
            module_scores=module_scores,
            reason_codes=reason_codes,
            market_type=analysis.market_type,
        )

    # GATE B: Size Reducers (Speed, Zomma & Pinning)
    speed_factor = 0.70 if bundle_report.speed_instability_warning else 1.00
    zomma_factor = 1.00
    if bundle_report.zomma_risk_score > 0.0:
        zomma_factor = max(0.1, min(1.0, 1.2 - 1.2 * bundle_report.zomma_risk_score))

    pinning_factor = 1.00
    pin_prob = getattr(bundle_report, "pinning_probability", 0.0) or 0.0
    if dte is not None and dte <= 1 and pin_prob > 0.70:
        pinning_factor = max(0.1, min(1.0, 1.5 - 1.5 * pin_prob))

    greek_base = max(0.1, min(1.0, speed_factor * zomma_factor * pinning_factor))
    risk_v2_mult = risk_sizing_multiplier(analysis, direction=direction)
    greek_sizing_multiplier = greek_base * risk_v2_mult
    if combiner_cap is not None:
        greek_sizing_multiplier = min(greek_sizing_multiplier, combiner_cap)
    greek_sizing_multiplier = max(0.1, min(1.5, greek_sizing_multiplier))

    # ── Motor ④: GEX Wall Stop + Color Decay ───────────────────────────────
    # Network-free (PD-3): reads options metrics already on the analysis path.
    # Invalidation (spot breached the directional wall) → BLOCK; proximity hit
    # while direction is still valid → SIZE_DOWN via the sizing multiplier.
    gex_wall_stop_price: float | None = None
    wall_stop = compute_gex_wall_stop(analysis, direction=direction)
    if wall_stop.invalidates_direction and direction in ("LONG", "SHORT"):
        reason_codes.append(REASON_GEX_WALL_INVALIDATION)
        return BingXDecision(
            symbol=analysis.venue_symbol,
            decision="BLOCK",
            direction="FLAT",
            confidence=0.0,
            score_total=score_total,
            module_scores=module_scores,
            reason_codes=reason_codes,
            market_type=analysis.market_type,
        )
    if wall_stop.active:
        reason_codes.append(REASON_GEX_WALL_STOP_ACTIVE)
        greek_sizing_multiplier *= wall_stop.size_multiplier
        greek_sizing_multiplier = max(0.1, min(1.5, greek_sizing_multiplier))
        gex_wall_stop_price = wall_stop.stop_price

    # ── Motor ⑬: Bayesian Kelly (read-only here) ───────────────────────────
    # risk_sizing_multiplier() already folds the Bayesian Kelly factor into
    # risk_v2_mult; we only surface the raw fraction + a SIZE_DOWN reason here
    # to avoid double-applying the multiplier.
    bk_result = bayesian_kelly_for_decide(route="BINGX")
    bayesian_kelly_pct = bk_result.fraction
    if bk_result.active and bk_result.multiplier < 0.85:
        reason_codes.append("bayesian_kelly_size_down")

    # ── Motor ⑭: Dark pool directional confirmation (read-only) ─────────────
    # Sizing is already folded into risk_v2_mult via _dark_pool_mult; here we
    # only annotate confirm/contradict. Never blocks — a contradiction only
    # size-downs (through risk sizing). Silent when the block is unavailable.
    dp_block = getattr(analysis, "dark_pool", None)
    if (
        dp_block is not None
        and getattr(dp_block, "status", "unavailable") == "available"
        and float(getattr(dp_block, "confidence", 0.0) or 0.0) >= dark_pool_min_confidence()
        and direction in ("LONG", "SHORT")
    ):
        dp_bias = str(getattr(dp_block, "bias", "NEUTRAL")).upper()
        confirms = (direction == "LONG" and dp_bias == "BULLISH") or (
            direction == "SHORT" and dp_bias == "BEARISH"
        )
        contradicts = (direction == "LONG" and dp_bias == "BEARISH") or (
            direction == "SHORT" and dp_bias == "BULLISH"
        )
        if confirms:
            reason_codes.append(REASON_DARK_POOL_CONFIRMS)
        elif contradicts:
            reason_codes.append(REASON_DARK_POOL_CONTRADICTS)

    if risk_v2_mult < 0.85:
        reason_codes.append("risk_sizing_v2_size_down")

    if bundle_report.speed_instability_warning:
        reason_codes.append(REASON_SPEED_INSTABILITY_SIZE_DOWN)

    if bundle_report.zomma_risk_score > 0.8:
        reason_codes.append(REASON_ZOMMA_RISK_SIZE_DOWN)

    if dte is not None and dte <= 1 and pin_prob > 0.70:
        reason_codes.append("pinning_risk_size_down")

    # Remove legacy flag setter here as it will be evaluated at the end based on greek_sizing_multiplier <= 0.85
    pass

    # ── Gate 1: INSUFFICIENT_DATA ──────────────────────────────────────────
    if _core_motors_available(analysis) < 2:
        reason_codes.append(REASON_INSUFFICIENT_CORE_MOTORS)
        confidence = predictive_score_val
        return BingXDecision(
            symbol=analysis.venue_symbol,
            decision="INSUFFICIENT_DATA",
            direction="FLAT",
            confidence=round(confidence, 4),
            score_total=score_total,
            module_scores=module_scores,
            reason_codes=reason_codes,
            market_type=analysis.market_type,
        )

    # ── Gate 2: L2 required for equity perps in live mode ──────────────────
    if (
        mode == "live"
        and cfg.require_l2_for_equity_live
        and analysis.market_type in _EQUITY_MARKET_TYPES
        and _block_status(analysis.l2) != "available"
    ):
        reason_codes.append(REASON_L2_REQUIRED_FOR_EQUITY_LIVE)
        return BingXDecision(
            symbol=analysis.venue_symbol,
            decision="BLOCK",
            direction="FLAT",
            confidence=round(predictive_score_val, 4),
            score_total=score_total,
            module_scores=module_scores,
            reason_codes=reason_codes,
            market_type=analysis.market_type,
        )

    # ── Predictive confidence floor ────────────────────────────────────────
    predictive_confidence = _predictive_confidence_value(analysis)
    if predictive_dir == "FLAT":
        reason_codes.append(REASON_NO_PREDICTIVE_DIRECTION)

    below_predictive_floor = (
        predictive_confidence is not None and predictive_confidence < cfg.min_predictive_confidence
    )
    if below_predictive_floor:
        reason_codes.append(REASON_PREDICTIVE_BELOW_FLOOR)
        if not technical_compensates:
            reason_codes.append(REASON_TECHNICAL_NOT_CONFIRMING)
            return BingXDecision(
                symbol=analysis.venue_symbol,
                decision="BLOCK",
                direction="FLAT",
                confidence=round(predictive_confidence or 0.0, 4),
                score_total=score_total,
                module_scores=module_scores,
                reason_codes=reason_codes,
                market_type=analysis.market_type,
            )

    # ── Gate 4: options GEX contradicts direction + technical doesn't compensate
    if options_contradicts:
        reason_codes.append(REASON_OPTIONS_CONTRADICTS_DIRECTION)
        if not technical_compensates:
            reason_codes.append(REASON_TECHNICAL_DOES_NOT_COMPENSATE)
            return BingXDecision(
                symbol=analysis.venue_symbol,
                decision="BLOCK",
                direction="FLAT",
                confidence=round(predictive_confidence or predictive_score_val, 4),
                score_total=score_total,
                module_scores=module_scores,
                reason_codes=reason_codes,
                market_type=analysis.market_type,
            )

    # ── Gate 5: aggregate score gate ───────────────────────────────────────
    if score_total < cfg.min_decision_score - cfg.size_down_band:
        reason_codes.append(REASON_LOW_AGGREGATE_SCORE)
        return BingXDecision(
            symbol=analysis.venue_symbol,
            decision="BLOCK",
            direction="FLAT",
            confidence=round(predictive_confidence or predictive_score_val, 4),
            score_total=score_total,
            module_scores=module_scores,
            reason_codes=reason_codes,
            market_type=analysis.market_type,
        )

    # ── SIZE_DOWN: score in the band OR partial confluence OR L2 low ──────
    if score_total < cfg.min_decision_score:
        reason_codes.append(REASON_MEDIUM_DATA_QUALITY)
        needs_legacy_size_down = True
    if options_contradicts and technical_compensates:
        reason_codes.append(REASON_PARTIAL_CONFLUENCE)
        needs_legacy_size_down = True
    if analysis.market_type in _EQUITY_MARKET_TYPES:
        l2_q = _safe_float(analysis.l2.quality_score) or 0.0
        l2_degraded = _block_status(analysis.l2) != "available" or l2_q < 0.4
        if l2_degraded and mode != "live":
            # In dry_run, missing L2 is acceptable but degrades sizing.
            reason_codes.append(REASON_L2_QUALITY_LOW)
            needs_legacy_size_down = True
    if direction_conflict:
        # Conflicting directions never reach ALLOW; force at most SIZE_DOWN.
        needs_legacy_size_down = True

    # Determine final multiplier
    needs_size_down = needs_legacy_size_down or (greek_sizing_multiplier <= 0.85)
    final_multiplier = (
        min(greek_sizing_multiplier, 0.5) if needs_legacy_size_down else greek_sizing_multiplier
    )

    if needs_size_down:
        # When direction has conflicting evidence we deliberately surface
        # ``FLAT`` so the executor can't put on a position with one foot in
        # each camp.
        final_direction: Direction = "FLAT" if direction_conflict else direction
        return BingXDecision(
            symbol=analysis.venue_symbol,
            decision="SIZE_DOWN",
            direction=final_direction,
            confidence=round(predictive_confidence or predictive_score_val, 4),
            score_total=score_total,
            module_scores=module_scores,
            reason_codes=reason_codes,
            market_type=analysis.market_type,
            sizing_multiplier=final_multiplier,
            combiner_size_pct=combiner_size,
            gex_wall_stop_price=gex_wall_stop_price,
            bayesian_kelly_pct=bayesian_kelly_pct,
        )

    # ── ALLOW ───────────────────────────────────────────────────────────────
    if technical_compensates:
        reason_codes.append(REASON_VENUE_TECHNICAL_ALIGNED)
    if (
        predictive_dir == direction
        and technical_dir == direction
        and options_dir in (direction, "FLAT")
    ):
        reason_codes.append(REASON_FULL_CONFLUENCE)

    return BingXDecision(
        symbol=analysis.venue_symbol,
        decision="ALLOW",
        direction=direction,
        confidence=round(predictive_confidence or predictive_score_val, 4),
        score_total=score_total,
        module_scores=module_scores,
        reason_codes=reason_codes,
        market_type=analysis.market_type,
        sizing_multiplier=final_multiplier,
        combiner_size_pct=combiner_size,
        gex_wall_stop_price=gex_wall_stop_price,
        bayesian_kelly_pct=bayesian_kelly_pct,
    )


def _predictive_confidence_value(analysis: BingXCandidateAnalysis) -> float | None:
    """Read ``confidence`` from the predictive signal — bridge-normalised."""
    signal = analysis.predictive.signal or {}
    if not isinstance(signal, dict):
        return None
    return _safe_float(signal.get("confidence"))


__all__ = [
    "REASON_CHARM_FLOW_PENALTY",
    "REASON_CONFLUENCE_DIVERGENCE",
    "REASON_CRYPTO_DERIVATIVES_OVERHEATING",
    "REASON_DARK_POOL_CONFIRMS",
    "REASON_DARK_POOL_CONTRADICTS",
    "REASON_DEX_ZGL_INVALIDATION",
    "REASON_DIRECTION_CONFLICT",
    "REASON_DIRECTION_NEUTRAL",
    "REASON_FULL_CONFLUENCE",
    "REASON_GEX_WALL_INVALIDATION",
    "REASON_GEX_WALL_STOP_ACTIVE",
    "REASON_INSUFFICIENT_CORE_MOTORS",
    "REASON_L2_QUALITY_LOW",
    "REASON_L2_REQUIRED_FOR_EQUITY_LIVE",
    "REASON_LOW_AGGREGATE_SCORE",
    "REASON_MEDIUM_DATA_QUALITY",
    "REASON_NDDE_CONTRADICTS_DIRECTION",
    "REASON_NO_PREDICTIVE_DIRECTION",
    "REASON_OPTIONS_CONTRADICTS_DIRECTION",
    "REASON_PARTIAL_CONFLUENCE",
    "REASON_PREDICTIVE_BELOW_FLOOR",
    "REASON_TECHNICAL_CONSENSUS_NEUTRAL",
    "REASON_TECHNICAL_DOES_NOT_COMPENSATE",
    "REASON_TECHNICAL_NOT_CONFIRMING",
    "REASON_VENUE_TECHNICAL_ALIGNED",
    "REASON_VENUE_UNAVAILABLE",
    "REASON_VOLATILITY_PANIC_SIZE_DOWN",
    "BingXDecision",
    "BingXDecisionConfig",
    "BingXModuleScores",
    "DecisionStatus",
    "Direction",
    "ExecutionMode",
    "decide",
]
