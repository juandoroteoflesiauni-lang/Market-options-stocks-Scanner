"""BingX Bot service — orchestrates Scan -> Filter -> Risk -> Execute.

This service is intentionally decoupled from the Funding Lab pipeline. It targets
a 10-USDT micro-account on BingX synthetic perpetual futures of US equities such
as ``AAPL-USDT``, ``MSFTON/USDT``, ``PLTRON/USDT``.

Design principles:

* **Survival first.** Sizing is *not* Kelly-fractional. A fixed-notional policy
  is enforced (10 USDT cash, optional 2x-5x leverage). Position size never
  exceeds the configured per-trade notional.
* **Scanner confirmation.** We still use a self-contained VSA / volume-spike
  heuristic to propose candidates, then require Market Scanner confirmation.
* **Strict filter.** A signal must pass a meta-learner probability gate (when a
  provider is supplied) or a deterministic heuristic gate. Insufficient data
  always returns ``insufficient_data`` — never silently mocked.
* **Dry-run by default.** Execution defers to ``BingXClient`` which intercepts
  order placement unless ``dry_run=False`` is set explicitly.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import asdict, dataclass, field
from typing import Any, Literal, Protocol

from backend.config.logger_setup import get_logger
from backend.domain.market_scanner_models import (
    MarketScannerRequest,
)
from backend.layer_1_data.datos.bingx_client import (
    VALID_KLINE_INTERVAL,
    BingXOrderResponse,
)
from backend.layer_3_specialists.tecnico.lob_dynamics_engine import LOBDynamicsAnalysis
from backend.services.bingx_candidate_analysis import (
    BingXCandidateAnalysis,
)
from backend.services.bingx_decision_engine import (
    BingXDecision,
)
from backend.services.bingx_risk_desk import (
    OrderIntent,
    RiskDeskDecision,
)

logger = get_logger(__name__)

# ─── Defaults tuned for a 10-USDT micro account ───────────────────────────────
DEFAULT_MICRO_EQUITY_USDT: float = 10.0
DEFAULT_NOTIONAL_PER_TRADE_USDT: float = 10.0
DEFAULT_LEVERAGE: float = 2.0
DEFAULT_MAX_LEVERAGE: float = 5.0
DEFAULT_HORIZON: str = "1h"
DEFAULT_SCAN_INTERVAL: VALID_KLINE_INTERVAL = "5m"
DEFAULT_KLINES_PER_SYMBOL: int = 2000
DEFAULT_MIN_BARS_FOR_SIGNAL: int = 40
DEFAULT_VOLUME_Z_THRESHOLD: float = 2.0
DEFAULT_HEURISTIC_PROB_FLOOR: float = 0.55
DEFAULT_SCANNER_MIN_SCORE: float = 45.0
SCANNER_CONFIRMATION_TIMEFRAMES: tuple[str, ...] = ("5m", "15m", "1h", "1D")
SCANNER_CONFIRMATION_MODULES: tuple[str, ...] = ("technical", "probabilistic", "options_gex")
DEFAULT_UNIVERSE: tuple[str, ...] = (
    "AMZN-USDT",
    "AAPL-USDT",
    "TSLA-USDT",
    "GOOGL-USDT",
    "META-USDT",
    "MSFT-USDT",
    "NVDA-USDT",
    "PLTR-USDT",
)

# Reason codes — stable strings; do not rename without updating consumers.
REASON_INSUFFICIENT_BARS = "insufficient_bars"
REASON_NO_VOLUME_SPIKE = "no_volume_spike"
REASON_FLAT_RANGE = "flat_price_range"
REASON_META_BLOCK = "meta_learner_block"
REASON_META_LOW_PROB = "meta_learner_low_probability"
REASON_HEURISTIC_LOW_PROB = "heuristic_low_probability"
REASON_RISK_BUDGET_EXHAUSTED = "risk_budget_exhausted"
REASON_LEVERAGE_CAP = "leverage_above_cap"
REASON_NO_VENUE_PRICE = "no_venue_price"
# L2 execution-quality reason codes — fired by ``_evaluate_l2_execution_quality``
# only for synthetic stock perpetuals. Crypto symbols never carry these.
REASON_L2_UNAVAILABLE = "l2_unavailable"
REASON_L2_SPREAD_TOO_WIDE = "l2_spread_too_wide"
REASON_L2_DEPTH_TOO_THIN = "l2_depth_too_thin"
REASON_L2_IMBALANCE_EXTREME = "l2_imbalance_extreme"
# Execution-spam protection reason codes
REASON_POSITION_ALREADY_OPEN = "position_already_open"
REASON_EXECUTION_COOLDOWN = "execution_cooldown"


# Cooldown window for re-executing the same symbol (minutes).
EXECUTION_COOLDOWN_MINUTES: float = 15.0

# Parametric fade-and-flip exit ladder (unrealized PnL % vs entry, Massive/Polygon spot).
PARAMETRIC_TP_TRIGGER_PCT: float = 3.0
PARAMETRIC_TP_STEP_PCT: float = 0.5
PARAMETRIC_HALF_EXIT_RATIO: float = 0.50
PARAMETRIC_STRONG_CONFLUENCE_FLOOR: float = 0.85
PARAMETRIC_STRONG_CONFLUENCE_TRIM_RATIO: float = 0.10
PARAMETRIC_FATIGUE_TRIM_RATIO: float = 0.25
PARAMETRIC_FLIP_CONFLUENCE_CEILING: float = 0.30
PARAMETRIC_FLIP_LEVERAGE: int = 5
PARAMETRIC_FLIP_MARGIN_TYPE: str = "CROSSED"
PARAMETRIC_PROFIT_ZONE_MIN_PCT: float = 3.0

Suitability = Literal["ALLOW", "SIZE_DOWN", "BLOCK", "INSUFFICIENT_DATA"]


@dataclass
class _ParametricExitState:
    """Per-symbol ladder state for partial take-profits."""

    initial_size: float
    half_tp_done: bool = False
    last_adaptive_milestone: int = 0


# ── Typed contracts ──────────────────────────────────────────────────────────
@dataclass(frozen=True)
class BingXMarketSnapshot:
    """Recent OHLCV slice plus light VSA features for one symbol."""

    symbol: str
    interval: str
    bars: int
    latest_close: float | None
    last_volume: float | None
    volume_mean: float | None
    volume_std: float | None
    volume_z_score: float | None
    close_position_in_range: float | None
    range_pct: float | None
    captured_at: str
    closes_recent: tuple[float, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class BingXSignal:
    """Scanner output before the filter gate runs.

    ``lob_analysis`` carries the optional L2 (depth) analysis for synthetic
    stock perpetuals. It is ``None`` for crypto and unsupported instruments
    so downstream consumers can detect ``insufficient_data`` instead of
    fabricating quality scores. ``data_quality_score`` lives on the analysis
    itself when ``ok=True``.
    """

    symbol: str
    direction: Literal["LONG", "SHORT", "FLAT"]
    score: float
    horizon: str
    reason_codes: tuple[str, ...]
    snapshot: BingXMarketSnapshot
    timestamp: str
    source: str = "bingx_light_vsa_scanner"
    lob_analysis: LOBDynamicsAnalysis | None = None

    def to_dict(self) -> dict[str, Any]:
        # Build the payload explicitly — ``dataclasses.asdict`` deep-copies
        # pydantic models which is both wasteful and breaks JSON serialization
        # for ``LOBDynamicsAnalysis``. Each field is normalized inline.
        return {
            "symbol": self.symbol,
            "direction": self.direction,
            "score": self.score,
            "horizon": self.horizon,
            "reason_codes": list(self.reason_codes),
            "snapshot": self.snapshot.to_dict(),
            "timestamp": self.timestamp,
            "source": self.source,
            "lob_analysis": (
                self.lob_analysis.model_dump(mode="json") if self.lob_analysis is not None else None
            ),
        }


@dataclass(frozen=True)
class FilterDecision:
    """Output of the meta-learner / heuristic filter for one signal."""

    symbol: str
    suitability: Suitability
    probability: float | None
    threshold: float
    provider: str
    reason_codes: tuple[str, ...]

    def to_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["reason_codes"] = list(self.reason_codes)
        return payload


@dataclass(frozen=True)
class BingXOrderPlan:
    """Risk-adapted execution plan for one signal."""

    symbol: str
    side: Literal["BUY", "SELL"]
    notional_usdt: float
    leverage: float
    quantity: float | None
    reference_price: float | None
    reason_codes: tuple[str, ...]
    authorized: bool

    def to_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["reason_codes"] = list(self.reason_codes)
        return payload


@dataclass(frozen=True)
class BingXCycleResult:
    """Aggregate result of a full Analysis -> Decision -> Risk -> Execute cycle."""

    started_at: str
    finished_at: str
    universe: tuple[str, ...]
    snapshots: tuple[BingXMarketSnapshot, ...]
    signals: tuple[BingXSignal, ...]
    decisions: tuple[FilterDecision, ...]
    plans: tuple[BingXOrderPlan, ...]
    executions: tuple[BingXOrderResponse, ...]
    dry_run: bool
    trading_environment: str = "paper"
    analyses: tuple[BingXCandidateAnalysis, ...] = ()
    engine_decisions: tuple[BingXDecision, ...] = ()
    order_intents: tuple[OrderIntent, ...] = ()
    risk_decisions: tuple[RiskDeskDecision, ...] = ()
    blocked_reasons: dict[str, list[str]] = field(default_factory=dict)
    l2_snapshots: dict[str, dict[str, Any]] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        analyses_payload = [a.to_dict() for a in self.analyses]
        return {
            "started_at": self.started_at,
            "finished_at": self.finished_at,
            "universe": list(self.universe),
            "snapshots": [s.to_dict() for s in self.snapshots],
            "signals": [s.to_dict() for s in self.signals],
            "decisions": [d.to_dict() for d in self.decisions],
            "plans": [p.to_dict() for p in self.plans],
            "executions": [e.to_dict() for e in self.executions],
            "dry_run": self.dry_run,
            "trading_environment": self.trading_environment,
            "analyses": analyses_payload,
            "candidate_analyses": analyses_payload,
            "engine_decisions": [d.to_dict() for d in self.engine_decisions],
            "order_intents": [_order_intent_to_dict(i) for i in self.order_intents],
            "risk_decisions": [_risk_decision_to_dict(d) for d in self.risk_decisions],
            "blocked_reasons": {
                symbol: list(reasons) for symbol, reasons in self.blocked_reasons.items()
            },
            "l2_snapshots": self.l2_snapshots,
        }


@dataclass(frozen=True)
class BingXRiskPolicy:
    """Micro-account sizing policy — strictly bounded, no Kelly-fractional."""

    equity_usdt: float = DEFAULT_MICRO_EQUITY_USDT
    notional_per_trade_usdt: float = DEFAULT_NOTIONAL_PER_TRADE_USDT
    leverage: float = DEFAULT_LEVERAGE
    max_leverage: float = DEFAULT_MAX_LEVERAGE
    max_open_positions: int = 1
    stop_loss_pct: float = 0.02
    take_profit_pct: float = 0.04

    def effective_notional(self) -> float:
        return max(0.0, min(self.notional_per_trade_usdt, self.equity_usdt * self.max_leverage))


@dataclass(frozen=True)
class ExecutionQualityPolicy:
    """Pre-trade L2 (depth) execution-quality gate for synthetic stock perps.

    The policy is *block-only* — it never authorizes sizing, it can only veto
    a candidate whose book is too thin / too wide / too lopsided to execute
    safely on a micro-account where slippage represents a meaningful share of
    the per-trade notional.

    Fields
    ------
    max_spread_pct:
        Maximum tolerated spread as a percent of mid-price. ``0.5`` = 0.5%.
        Block fires when the relative spread strictly exceeds the threshold.
    min_bid_depth_usdt / min_ask_depth_usdt:
        Minimum aggregate depth per side, expressed in the same units the L2
        adapter returns. For BingX swap depth these are contract quantities;
        we treat them as a *floor proxy* — they do not need exact USDT
        conversion to be useful as a thin-book filter on a 10-USDT account.
    max_imbalance_abs:
        ``None`` disables the imbalance gate. When set, the absolute value of
        ``LOBDynamicsResult.imbalance_rho`` is compared against the threshold
        (e.g. ``0.8`` blocks one-sided books where one side is >9× the other).
    """

    max_spread_pct: float = 0.5
    min_bid_depth_usdt: float = 500.0
    min_ask_depth_usdt: float = 500.0
    max_imbalance_abs: float | None = None


# Pluggable meta-learner provider: maps a signal -> probability in [0, 1].
MetaLearnerProvider = Callable[[BingXSignal], Awaitable[float | None]]


class ScannerConfirmationService(Protocol):
    async def scan(self, request: MarketScannerRequest) -> object:
        """Return a scanner response-like object with a ``rows`` attribute."""


__all__ = [name for name in dir() if not name.startswith("__")]
