from __future__ import annotations
from typing import TYPE_CHECKING, Literal, Any

import math
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from pathlib import Path

if TYPE_CHECKING:
    from backend.domain.probabilistic_models import (
        PredictiveOptionsBundleReport,
    )

from backend.config.logger_setup import get_logger

logger = get_logger(__name__)

from backend.config.sqlite_db_paths import OPTIONS_GEX_SNAPSHOTS_DB, PREDICTIONS_DB

_DB_PATH: Path = PREDICTIONS_DB
_OPTIONS_GEX_DB_PATH: Path = OPTIONS_GEX_SNAPSHOTS_DB
_DUCKDB_PATH: Path = (
    Path(__file__).resolve().parent.parent.parent.parent / "data" / "quantum_analyzer.duckdb"
)

SourceStatus = Literal["available", "unavailable"]

REASON_DESK_NOT_IMPLEMENTED = "desk_not_yet_implemented"
REASON_DESK_FETCH_FAILED = "desk_fetch_failed"
REASON_DESK_NO_FETCHER = "desk_no_fetcher_wired"
REASON_DESK_DATA_INCOMPLETE = "desk_data_incomplete"
REASON_MARKET_TYPE_EXCLUDED = "desk_market_type_excluded"
REASON_NO_UNDERLYING = "desk_no_underlying_resolved"


@dataclass(frozen=True)
class DeskReadStatus:
    """Availability token for one analysis desk.

    ``status`` mirrors the ``SourceStatus`` convention used across all BingX
    bridges — ``"available"`` means the desk produced a usable reading;
    ``"unavailable"`` means it degraded and the reason is in ``reason``.

    ``quality_score`` is ``None`` when unavailable and a clamped ``[0, 1]``
    float when available — callers may use it to weight the desk's contribution
    to a composite signal.

    ``latency_ms`` is advisory and intended for observability dashboards; it is
    *not* used in any gating logic.
    """

    status: SourceStatus
    source: str  # stable identifier, e.g. "predictive_options_2"
    reason: str | None = None  # non-None only when unavailable
    quality_score: float | None = None
    latency_ms: float | None = None
    captured_at: str = ""

    @property
    def is_available(self) -> bool:
        """Convenience alias so callers read ``desk.status.is_available``."""
        return self.status == "available"

    def __post_init__(self) -> None:
        # Clamp quality_score to [0, 1] without mutating the frozen instance.
        # Use object.__setattr__ because the dataclass is frozen.
        qs = self.__dict__.get("quality_score")
        if qs is not None:
            clamped = max(0.0, min(1.0, float(qs)))
            if not math.isfinite(clamped):
                object.__setattr__(self, "quality_score", None)
            else:
                object.__setattr__(self, "quality_score", round(clamped, 4))


@dataclass(frozen=True)
class PredictiveDeskState:
    """Directional signal produced by the probabilistic / meta-signal desk.

    Fields
    ------
    directional_bias : ``"LONG"`` | ``"SHORT"`` | ``"NEUTRAL"``
        Consensus direction from whichever predictive source answered.
    probability_long / probability_short : float | None
        Win-probability estimates in ``[0, 1]``.  Both may be ``None`` if the
        source only reports direction without explicit probabilities.
    confidence : float | None
        Aggregate confidence in ``[0, 1]`` across the source's internal signals.
    horizon : str
        Human-readable horizon label, e.g. ``"swing"`` or ``"intraday"``.
    source_tag : str
        Which sub-source answered: ``"meta_signal"`` | ``"predictive_options_2"``
        | ``"equity_heuristic"`` | ``"none"``.
    reason_codes : list[str]
        Provenance chain — every cascade step that declined appends a code.
    """

    desk_status: DeskReadStatus
    directional_bias: str = "NEUTRAL"  # LONG | SHORT | NEUTRAL
    probability_long: float | None = None
    probability_short: float | None = None
    confidence: float | None = None
    horizon: str = "unknown"
    source_tag: str = "none"
    reason_codes: list[str] = field(default_factory=list)

    # Extended probabilistic fields for institutional research
    pr_ordered: float | None = None
    trend_strength: float | None = None
    evt_var_99: float | None = None
    evt_cvar_99: float | None = None
    merton_jump_probability: float | None = None
    particle_filter_state: dict[str, Any] = field(default_factory=dict)
    regime_state: dict[str, Any] = field(default_factory=dict)
    raw: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class OptionsGexDeskState:
    """Readings from the Predictive Options 2 desk (second/third-order Greeks).

    All price-level fields are expressed in the *underlying's price units*
    (USD for US equities, ARS for Argentine equities, etc.).  The decision
    engine never compares these to raw BingX perp prices directly — it uses
    the spot price extracted from the venue block as its reference.

    Fields
    ------
    gamma_flip_level : float | None
        Price at which dealer net-gamma crosses zero.  Below this level
        dealers are short-gamma and must chase moves; above it they absorb.
    is_gamma_negative_regime : bool
        ``True`` when spot < gamma_flip_level, indicating the high-volatility,
        trend-following dealer posture.
    shadow_delta_imbalance : float | None
        Net skew-adjusted delta imbalance in ``[-1, 1]``.  Positive values
        indicate call-heavy dealer hedging pressure (bullish flow); negative
        values indicate put-heavy (bearish flow).
    zero_day_pinning_strike : float | None
        Strike with the highest 0DTE pinning probability for today's session.
    speed_instability_warning : bool
        ``True`` when ∂Γ/∂S (Speed) flags a gamma-trap zone near the current
        spot that could accelerate a breakout.
    tail_risk_severity : ``"LOW"`` | ``"MEDIUM"`` | ``"HIGH"`` | ``"CRITICAL"``
        Bucketed tail-risk level derived from the vol-smile cubic fit (25Δ
        skew + convexity percentile).  ``"CRITICAL"`` is a hard-block trigger
        in the decision engine.
    zomma_risk_score : float | None
        Normalised third-order Greek ∂Γ/∂σ score in ``[0, 1]``.  Values > 0.8
        trigger a SIZE_DOWN gate.
    """

    desk_status: DeskReadStatus

    # Core gamma levels
    gamma_flip_level: float | None = None
    is_gamma_negative_regime: bool = False

    # Shadow delta
    shadow_delta_imbalance: float | None = None

    # 0DTE pinning
    zero_day_pinning_strike: float | None = None

    # Speed instability
    speed_instability_warning: bool = False

    # Tail risk
    tail_risk_severity: str = "LOW"  # LOW | MEDIUM | HIGH | CRITICAL

    # Zomma
    zomma_risk_score: float | None = None

    # Raw supplementary fields — populated when available, None otherwise.
    # These do not drive gating logic in Phase 1; they are persisted for
    # observability and future use.
    atm_iv: float | None = None
    call_wall: float | None = None
    put_wall: float | None = None
    net_gex_total: float | None = None
    dealer_bias: str = "NEUTRAL"
    predictive_report: PredictiveOptionsBundleReport | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class TechnicalDeskState:
    """SMC/VSA/FVG/VP composite trend reading from the venue kline bridge.

    Fields
    ------
    trend_direction : ``"bullish"`` | ``"bearish"`` | ``"neutral"``
        Resolved direction from the SMC market-structure composite.
    smc_bias : ``"BULLISH"`` | ``"BEARISH"`` | ``"NEUTRAL"``
        Secondary direction label for compatibility with legacy consumers.
    technical_quality_score : float | None
        Bridge-reported quality in ``[0, 1]``.
    bars_count : int
        Number of klines used; used for confidence weighting upstream.
    """

    desk_status: DeskReadStatus
    trend_direction: str = "neutral"  # bullish | bearish | neutral
    smc_bias: str = "NEUTRAL"  # BULLISH | BEARISH | NEUTRAL
    technical_quality_score: float | None = None
    bars_count: int = 0

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class InstitutionalResearchSnapshot:
    """Master container merging the three institutional desks for one cycle.

    This is the object that the decision engine and risk-desk consumers read.
    It is designed to be *directly* convertible to
    :class:`~backend.domain.probabilistic_models.PredictiveOptionsBundleReport`
    via :meth:`to_bundle_report`, which will be implemented in Phase 2 once
    the real desk fetchers are wired.

    Survival contract
    -----------------
    - The snapshot is always constructable even when all desks are
      ``unavailable`` — the engine must never fail to build a snapshot.
    - :meth:`is_actionable` returns ``False`` unless at least two desks are
      ``available`` — the engine must never size a position from a single
      degraded reading.
    - ``to_dict()`` is always JSON-safe.
    """

    # Identifiers
    venue_symbol: str
    underlying_symbol: str
    market_type: str

    # Desk readings
    predictive: PredictiveDeskState
    options_gex: OptionsGexDeskState
    technical: TechnicalDeskState

    # Metadata
    data_version: str = "1.0"  # bump when the contract changes
    fetched_at: str = ""
    errors: dict[str, str] = field(default_factory=dict)

    # ── Actionability gate ────────────────────────────────────────────────────

    def is_actionable(self) -> bool:
        """Return ``True`` only when at least 2 of the 3 desks are available.

        The decision engine enforces this guard before consuming any field.
        A snapshot that fails ``is_actionable()`` must be treated as
        ``INSUFFICIENT_DATA`` regardless of its individual field values.

        Rationale: a single desk can produce a directional signal that
        contradicts reality (e.g., the predictive desk gives LONG while the
        GEX desk shows a critical tail-risk event).  Two confirmatory desks
        provide the minimum institutional cross-check.
        """
        available_desks = sum(
            1
            for desk in (
                self.predictive.desk_status,
                self.options_gex.desk_status,
                self.technical.desk_status,
            )
            if desk.status == "available"
        )
        return available_desks >= 2

    # ── Serialisation ─────────────────────────────────────────────────────────

    def to_dict(self) -> dict[str, Any]:
        """Return a JSON-safe plain-dict representation of the snapshot."""
        return asdict(self)

    def desk_summary(self) -> dict[str, str]:
        """Return a compact status map for log lines and health endpoints."""
        return {
            "predictive": self.predictive.desk_status.status,
            "options_gex": self.options_gex.desk_status.status,
            "technical": self.technical.desk_status.status,
            "actionable": str(self.is_actionable()),
        }


def _now_iso() -> str:
    return datetime.now(UTC).isoformat()


def _unavailable_desk_status(source: str, reason: str) -> DeskReadStatus:
    return DeskReadStatus(
        status="unavailable",
        source=source,
        reason=reason,
        quality_score=None,
        captured_at=_now_iso(),
    )


def _safe_predictive_desk() -> PredictiveDeskState:
    """Return a fully degraded predictive desk with no signal."""
    return PredictiveDeskState(
        desk_status=_unavailable_desk_status(
            source="none",
            reason=REASON_DESK_NOT_IMPLEMENTED,
        ),
        directional_bias="NEUTRAL",
        reason_codes=[REASON_DESK_NOT_IMPLEMENTED],
    )


def _safe_options_gex_desk() -> OptionsGexDeskState:
    """Return a fully degraded GEX desk with all-safe field values.

    All numeric fields are ``None`` so downstream consumers that check for
    ``None`` before applying thresholds will skip any gating logic, and the
    boolean flags default to their safe positions (no regime, no warning).
    """
    return OptionsGexDeskState(
        desk_status=_unavailable_desk_status(
            source="predictive_options_2",
            reason=REASON_DESK_NOT_IMPLEMENTED,
        ),
        gamma_flip_level=None,
        is_gamma_negative_regime=False,
        shadow_delta_imbalance=None,
        zero_day_pinning_strike=None,
        speed_instability_warning=False,
        tail_risk_severity="LOW",
        zomma_risk_score=None,
        predictive_report=None,
    )


def _safe_technical_desk() -> TechnicalDeskState:
    """Return a fully degraded technical desk."""
    return TechnicalDeskState(
        desk_status=_unavailable_desk_status(
            source="venue_technical_bridge",
            reason=REASON_DESK_NOT_IMPLEMENTED,
        ),
        trend_direction="neutral",
        smc_bias="NEUTRAL",
        technical_quality_score=None,
        bars_count=0,
    )


def _safe_snapshot(
    venue_symbol: str,
    underlying_symbol: str,
    market_type: str,
    errors: dict[str, str] | None = None,
) -> InstitutionalResearchSnapshot:
    """Build a fully-degraded snapshot — all desks ``unavailable``.

    Used as the safe fallback when :func:`fetch_institutional_snapshot` cannot
    reach real desk fetchers.  ``is_actionable()`` will return ``False`` on
    this snapshot.
    """
    return InstitutionalResearchSnapshot(
        venue_symbol=venue_symbol,
        underlying_symbol=underlying_symbol,
        market_type=market_type,
        predictive=_safe_predictive_desk(),
        options_gex=_safe_options_gex_desk(),
        technical=_safe_technical_desk(),
        fetched_at=_now_iso(),
        errors=errors or {},
    )


def _safe_float(value: object) -> float | None:
    """Best-effort coercion to finite float, returns ``None`` on failure."""
    if value is None:
        return None
    try:
        out = float(value)  # type: ignore[arg-type]
        return out if math.isfinite(out) else None
    except (TypeError, ValueError):
        return None


def _bucket_tail_risk(signal: float | None) -> str:
    """Map the ``tail_risk_directional_signal`` scalar to a severity bucket.

    The ``tail_risk_directional_signal`` in ``options_gex_features`` is the
    output of the tail-risk engine normalised to ``[-1, 1]``; negative values
    indicate put-skew / fear premium; magnitude encodes severity.

    Mapping
    -------
    abs(signal) < 0.20  → LOW        (no meaningful tail premium)
    abs(signal) < 0.50  → MEDIUM     (elevated skew, watch)
    abs(signal) < 0.80  → HIGH       (strong tail demand — SIZE_DOWN territory)
    abs(signal) ≥ 0.80  → CRITICAL   (extreme tail bid — hard BLOCK)
    """
    if signal is None:
        return "LOW"
    magnitude = abs(signal)
    if magnitude < 0.20:
        return "LOW"
    if magnitude < 0.50:
        return "MEDIUM"
    if magnitude < 0.80:
        return "HIGH"
    return "CRITICAL"
