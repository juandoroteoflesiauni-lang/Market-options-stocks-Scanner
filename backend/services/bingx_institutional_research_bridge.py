"""Institutional Research Bridge — BingX decision-engine desk vocabulary.

Defines the *Institutional Research Snapshot*: a single, JSON-safe container
that captures the read status and quantitative findings of the three analysis
desks that feed :func:`backend.services.bingx_decision_engine.decide`:

- **Predictive Desk** — directional bias, confidence and probability surfaces
  derived from the probabilistic / meta-signal stack.
- **Options / GEX Desk** — second-order Greeks, gamma-flip level, shadow-delta
  imbalance, zomma risk, speed instability and tail-risk severity from
  Predictive Options 2.
- **Technical Desk** — SMC/VSA/FVG composite trend quality from the venue
  kline bridge.

Each desk exposes a :class:`DeskReadStatus` that follows the same
``available`` / ``unavailable`` contract used everywhere in the bridge layer
so the decision engine can degrade gracefully when any desk is offline.

The entry point :func:`fetch_institutional_snapshot` is the single public
async function callers should await.  In Phase 1 it returns a safe default
(all desks ``unavailable``).  Later phases will wire real fetchers without
changing the public interface.

Design rules
------------
- **No cross-layer imports** — only ``backend.config`` and stdlib are imported
  here.  Fetchers are injected, not hardcoded.
- **No IO at import time** — all network/DB work lives inside coroutines.
- **Frozen dataclasses** — every contract object is immutable after creation;
  downstream consumers may cache them freely.
- **JSON-safe via ``asdict``** — every nested type is a plain Python builtin
  when serialised.
"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING, Any, Literal

if TYPE_CHECKING:
    pass

from backend.config.logger_setup import get_logger

logger = get_logger(__name__)

# ── Database path (resolved relative to this file's package root) ─────────────
# Path(__file__) = .../backend/services/bingx_institutional_research_bridge.py
# parents[0] = .../backend/services/
# parents[1] = .../backend/
# parents[1] / "data" / "predictions.db" = .../backend/data/predictions.db  ✓
_DB_PATH: Path = Path(__file__).resolve().parent.parent / "data" / "predictions.db"
# parents[2] / "data" / "quantum_analyzer.duckdb" = .../data/quantum_analyzer.duckdb  ✓
_DUCKDB_PATH: Path = (
    Path(__file__).resolve().parent.parent.parent / "data" / "quantum_analyzer.duckdb"
)


# ── Public type aliases ───────────────────────────────────────────────────────

SourceStatus = Literal["available", "unavailable"]

# Stable reason-code literals — runbooks and dashboards match on these strings.
# Do not rename without coordinating with consumers.
REASON_DESK_NOT_IMPLEMENTED = "desk_not_yet_implemented"
REASON_DESK_FETCH_FAILED = "desk_fetch_failed"
REASON_DESK_NO_FETCHER = "desk_no_fetcher_wired"
REASON_DESK_DATA_INCOMPLETE = "desk_data_incomplete"
REASON_MARKET_TYPE_EXCLUDED = "desk_market_type_excluded"
REASON_NO_UNDERLYING = "desk_no_underlying_resolved"


# ─────────────────────────────────────────────────────────────────────────────
# Tier 1 — DeskReadStatus
# Describes whether a single desk produced actionable data for this cycle.
# ─────────────────────────────────────────────────────────────────────────────


# ─────────────────────────────────────────────────────────────────────────────
# Tier 2 — Per-desk state containers
# Each desk holds its DeskReadStatus plus the quantitative readings.
# All numeric fields are nullable so a partial read degrades gracefully.
# ─────────────────────────────────────────────────────────────────────────────


# ─────────────────────────────────────────────────────────────────────────────
# Tier 3 — InstitutionalResearchSnapshot (master container)
# ─────────────────────────────────────────────────────────────────────────────


# ─────────────────────────────────────────────────────────────────────────────
# Safe-default factories
# ─────────────────────────────────────────────────────────────────────────────


# ───────────────────────────────────────────────────────────────────────────────
# Private helpers — value extraction and projection
# ───────────────────────────────────────────────────────────────────────────────


# ─────────────────────────────────────────────────────────────────────────────
# Public entry point
# ─────────────────────────────────────────────────────────────────────────────


from backend.services.research.bingx_research_options import _fetch_options_gex_desk
from backend.services.research.bingx_research_predictive import _fetch_predictive_desk
from backend.services.research.bingx_research_technical import _project_technical_desk
from backend.services.research.research_types import *


async def fetch_institutional_snapshot(
    venue_symbol: str,
    underlying_symbol: str,
    market_type: str,
    *,
    technical_payload: dict | object | None = None,
    options_snapshot: dict[str, Any] | None = None,
    klines: tuple[dict[str, Any], ...] | None = None,
) -> InstitutionalResearchSnapshot:
    """Fetch and consolidate the three institutional desk readings for one cycle.

    This is the **single async entry point** that the decision engine and any
    other consumer should call.  It is designed to be injected-friendly: callers
    can monkey-patch this function in tests or pass it as a callable to higher-
    level builders.

    Current implementation (Phase 2 — Options/GEX + Technical ingestion)
    -----------------------------------------------------------------------
    - **Options / GEX Desk** → reads from ``predictions.db`` via
      :func:`_fetch_options_gex_desk`.  Real data: ``gamma_flip_level``,
      ``shadow_delta_imbalance``, ``tail_risk_severity``,
      ``speed_instability_warning``, ``zomma_risk_score``.
    - **Technical Desk** → projects the already-computed ``technical_payload``
      (the ``BingXTechnicalBridgeResult.to_dict()`` from
      ``build_candidate_analysis``) via :func:`_project_technical_desk`.
      Real data: ``trend_direction``, ``smc_bias``, ``technical_quality_score``,
      ``bars_count`` and engine-coverage stats from the ``engine_status`` block.
    - **Predictive Desk** → Phase 1 stub (``unavailable`` / not yet wired).

    Survival contract
    -----------------
    This function **must never raise**.  Any exception is caught per-desk;
    the affected desk degrades to ``unavailable`` and the snapshot is always
    returned.

    Parameters
    ----------
    venue_symbol : str
        BingX perp symbol (e.g. ``"AAPL-USDT"``).
    underlying_symbol : str
        Resolved underlying ticker (e.g. ``"AAPL"``).
    market_type : str
        BingX market classification (e.g. ``"stock_perp"``).
    technical_payload : dict | object | None
        The ``venue_technical`` field from ``BingXTechnicalBlock``, which is
        the ``BingXTechnicalBridgeResult`` serialised to a plain dict via
        ``to_dict()`` / ``asdict()``.  When ``None``, the Technical Desk
        degrades to ``unavailable``.

    Returns
    -------
    InstitutionalResearchSnapshot
        A fully-populated (or fully-degraded) snapshot ready for consumption
        by the decision engine.
    """
    errors: dict[str, str] = {}

    # ── Options / GEX desk (Phase 2: real DB ingestion + dynamic engines) ─────
    options_gex_desk = await _fetch_options_gex_desk(
        underlying_symbol, market_type, options_snapshot=options_snapshot, klines=klines
    )
    if not options_gex_desk.desk_status.is_available:
        errors["options_gex"] = options_gex_desk.desk_status.reason or REASON_DESK_FETCH_FAILED

    # ── Technical desk (Phase 2: project from venue_technical payload) ────────
    try:
        technical_desk = _project_technical_desk(technical_payload)
    except Exception as _tech_exc:
        logger.warning(
            "fetch_institutional_snapshot._project_technical_desk.failed " "venue=%s error=%s",
            venue_symbol,
            str(_tech_exc)[:180],
        )
        technical_desk = _safe_technical_desk()
    if not technical_desk.desk_status.is_available:
        errors["technical"] = technical_desk.desk_status.reason or REASON_DESK_FETCH_FAILED

    # ── Predictive desk (Fase 1: real DB ingestion) ──────────────────────────
    try:
        predictive_desk = await _fetch_predictive_desk(underlying_symbol, market_type)
    except Exception as _pred_exc:
        logger.warning(
            "fetch_institutional_snapshot._fetch_predictive_desk.failed " "underlying=%s error=%s",
            underlying_symbol,
            str(_pred_exc)[:180],
        )
        predictive_desk = _safe_predictive_desk()
    if not predictive_desk.desk_status.is_available:
        errors["predictive"] = predictive_desk.desk_status.reason or REASON_DESK_FETCH_FAILED

    snapshot = InstitutionalResearchSnapshot(
        venue_symbol=venue_symbol,
        underlying_symbol=underlying_symbol,
        market_type=market_type,
        predictive=predictive_desk,
        options_gex=options_gex_desk,
        technical=technical_desk,
        fetched_at=_now_iso(),
        errors=errors,
    )

    logger.debug(
        "fetch_institutional_snapshot | venue=%s underlying=%s " "actionable=%s desks=%s",
        venue_symbol,
        underlying_symbol,
        snapshot.is_actionable(),
        snapshot.desk_summary(),
    )

    return snapshot


# ─────────────────────────────────────────────────────────────────────────────
# Public API
# ─────────────────────────────────────────────────────────────────────────────

__all__ = [
    # Constants
    "REASON_DESK_DATA_INCOMPLETE",
    "REASON_DESK_FETCH_FAILED",
    "REASON_DESK_NOT_IMPLEMENTED",
    "REASON_DESK_NO_FETCHER",
    "REASON_MARKET_TYPE_EXCLUDED",
    "REASON_NO_UNDERLYING",
    # Dataclasses — Tier 1
    "DeskReadStatus",
    # Dataclasses — Tier 2
    "OptionsGexDeskState",
    "PredictiveDeskState",
    "TechnicalDeskState",
    # Dataclasses — Tier 3
    "InstitutionalResearchSnapshot",
    # Factories (exported for test-fixture convenience)
    "_safe_options_gex_desk",
    "_safe_predictive_desk",
    "_safe_snapshot",
    "_safe_technical_desk",
    # Entry point
    "fetch_institutional_snapshot",
]
