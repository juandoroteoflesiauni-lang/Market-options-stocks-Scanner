"""Unified analysis contract for BingX candidates.

Connects venue, underlying, options, technical (underlying equity TA),
predictive (equity heuristic probabilities), and L2 (LOB dynamics) into a
single JSON-safe ``BingXCandidateAnalysis`` dataclass.

Survival contract:
- Every engine call degrades in isolation. No block failure propagates.
- ``readiness_score = 0.0`` when venue data is unavailable.
- ``to_dict()`` is always JSON-safe — all nested types are Python builtins.
"""

from __future__ import annotations

import asyncio
import math
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from backend.layer_3_specialists.ia_probabilistico.domain.probabilistic_models import (
        PredictiveOptionsBundleReport,
    )

from backend.config.logger_setup import get_logger
from backend.layer_1_data.datos.bingx_client import BingXClient, BingXKline
from backend.services.bingx_candidate_context import (
    AlpacaBarsClient,
    BingXCandidateContext,
    FMPQuoteClient,
    MassiveOptionsClient,
    MetaLearnerClient,
    SourceStatus,
    build_candidate_context,
)
from backend.services.bingx_exchange_derivatives_bridge import (
    ExchangeDerivativesClient,
    build_exchange_derivatives_bridge,
)
from backend.services.bingx_institutional_research_bridge import (
    InstitutionalResearchSnapshot,
    fetch_institutional_snapshot,
)
from backend.services.bingx_l2_integration import analyze_bingx_l2
from backend.services.bingx_options_bridge import OptionsSnapshotFn, build_options_bridge
from backend.services.bingx_predictive_bridge import EquitySummaryFn, build_predictive_bridge
from backend.services.bingx_symbol_linker import classify_underlying, underlying_from_bingx_symbol
from backend.services.bingx_technical_bridge import TechnicalCandlesFn, build_venue_technical
from backend.services.bingx_universe import MarketType
from backend.services.equity_ta_snapshot_service import (
    EquityTASnapshotService,
    equity_probabilistic_summary,
)

logger = get_logger(__name__)


# ── Engine status ─────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class BingXEngineStatus:
    """Availability snapshot for one analysis engine. Used for health reporting."""

    status: SourceStatus
    source: str
    reason: str | None = None
    quality_score: float | None = None
    captured_at: str = ""


# ── Per-engine blocks ────────────────────────────────────────────────────────


@dataclass(frozen=True)
class BingXVenueBlock:
    """BingX perp venue data: serialised klines, funding, OI, and basic TA."""

    venue_symbol: str
    status: SourceStatus
    source: str
    klines: tuple[dict[str, Any], ...] = ()
    funding_rate: float | None = None
    open_interest: float | None = None
    venue_ta: dict[str, Any] | None = None
    reason: str | None = None


@dataclass(frozen=True)
class BingXUnderlyingBlock:
    """Underlying asset data: FMP/Alpaca quote + OHLCV availability."""

    underlying_symbol: str
    market_type: str  # MarketType — plain str for JSON safety
    ohlcv_status: SourceStatus = "unavailable"
    source: str = "none"
    quote: dict[str, Any] | None = None
    reason: str | None = None


@dataclass(frozen=True)
class BingXOptionsBlock:
    """Options chain availability and chain-level metrics."""

    metrics: dict[str, Any] | None = None
    status: SourceStatus = "unavailable"
    source: str = "none"
    quality_score: float | None = None
    reason: str | None = None
    predictive_report: PredictiveOptionsBundleReport | None = None


@dataclass(frozen=True)
class BingXTechnicalBlock:
    """Technical-analysis block.

    ``metrics`` carries the underlying equity TA snapshot (RSI, EMA, trend
    from FMP daily bars) — backward-compatible with the original contract.

    ``venue_technical`` carries the full SMC/VSA/FVG/VP/OF payload computed
    from BingX venue klines via the technical bridge. ``status``,
    ``source``, ``quality_score``, and ``reason`` continue to describe the
    underlying-equity track so existing consumers stay valid; the venue
    track has its own status / score nested inside ``venue_technical``.
    """

    metrics: dict[str, Any] | None = None
    status: SourceStatus = "unavailable"
    source: str = "none"
    quality_score: float | None = None
    reason: str | None = None
    venue_technical: dict[str, Any] | None = None


@dataclass(frozen=True)
class BingXPredictiveBlock:
    """Predictive output for a BingX symbol.

    ``metrics`` carries the raw payload from whichever predictive source the
    bridge chose (meta-signal / predictive-options-2 / thesis / equity
    heuristic). ``signal`` is the normalised contract (directional_bias,
    probability_long/short, confidence, horizon, source, quality_score,
    reason_codes) — that is what Risk-Desk reads. ``source`` mirrors
    ``signal.source`` for consistency with the other blocks.
    """

    metrics: dict[str, Any] | None = None
    status: SourceStatus = "unavailable"
    source: str = "none"
    quality_score: float | None = None
    reason: str | None = None
    signal: dict[str, Any] | None = None


@dataclass(frozen=True)
class BingXL2Block:
    """LOB dynamics analysis from BingX REST depth snapshot."""

    lob_analysis: dict[str, Any] | None = None
    status: SourceStatus = "unavailable"
    source: str = "none"
    quality_score: float | None = None
    reason: str | None = None


# ── Top-level contract ────────────────────────────────────────────────────────


@dataclass(frozen=True)
class BingXExchangeDerivativesBlock:
    """Cross-venue crypto derivatives data from public exchange APIs."""

    metrics: dict[str, Any] | None = None
    providers: tuple[dict[str, Any], ...] = ()
    status: SourceStatus = "unavailable"
    source: str = "none"
    data_sources: tuple[str, ...] = ()
    quality_score: float | None = None
    reason: str | None = None


@dataclass(frozen=True)
class BingXCandidateAnalysis:
    """Unified analysis contract for one BingX candidate symbol.

    All blocks degrade independently. ``readiness_score`` is 0.0 when venue
    data is missing, otherwise a weighted fraction of available engine quality.
    ``to_dict()`` produces a JSON-safe plain-dict representation.
    """

    venue_symbol: str
    underlying_symbol: str
    market_type: str  # MarketType

    venue: BingXVenueBlock
    underlying: BingXUnderlyingBlock
    options: BingXOptionsBlock
    technical: BingXTechnicalBlock
    predictive: BingXPredictiveBlock
    l2: BingXL2Block
    exchange_derivatives: BingXExchangeDerivativesBlock = field(
        default_factory=BingXExchangeDerivativesBlock
    )
    # Institutional Research Snapshot — aggregates the three desk readings
    # (predictive, options-GEX, technical) into a single gating contract.
    # ``None`` when the bridge failed to initialise (degraded gracefully).
    institutional_research: InstitutionalResearchSnapshot | None = None

    data_sources: tuple[str, ...] = ()
    errors: dict[str, str] = field(default_factory=dict)
    readiness_score: float = 0.0
    captured_at: str = ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    def engine_statuses(self) -> dict[str, BingXEngineStatus]:
        """Per-engine status map for health reporting and log correlation."""
        ts = self.captured_at
        return {
            "venue": BingXEngineStatus(
                status=self.venue.status,
                source=self.venue.source,
                reason=self.venue.reason,
                captured_at=ts,
            ),
            "underlying": BingXEngineStatus(
                status=self.underlying.ohlcv_status,
                source=self.underlying.source,
                reason=self.underlying.reason,
                captured_at=ts,
            ),
            "options": BingXEngineStatus(
                status=self.options.status,
                source=self.options.source,
                reason=self.options.reason,
                quality_score=self.options.quality_score,
                captured_at=ts,
            ),
            "technical": BingXEngineStatus(
                status=self.technical.status,
                source=self.technical.source,
                reason=self.technical.reason,
                quality_score=self.technical.quality_score,
                captured_at=ts,
            ),
            "predictive": BingXEngineStatus(
                status=self.predictive.status,
                source=self.predictive.source,
                reason=self.predictive.reason,
                quality_score=self.predictive.quality_score,
                captured_at=ts,
            ),
            "l2": BingXEngineStatus(
                status=self.l2.status,
                source=self.l2.source,
                reason=self.l2.reason,
                quality_score=self.l2.quality_score,
                captured_at=ts,
            ),
            "exchange_derivatives": BingXEngineStatus(
                status=self.exchange_derivatives.status,
                source=self.exchange_derivatives.source,
                reason=self.exchange_derivatives.reason,
                quality_score=self.exchange_derivatives.quality_score,
                captured_at=ts,
            ),
        }


# ── Pure venue TA helpers ─────────────────────────────────────────────────────
# Minimal RSI and EMA — inlined to avoid coupling to private internals of
# equity_ta_snapshot_service and to remain pandas-free in the service layer.


def _safe_float_local(v: object) -> float | None:
    try:
        out = float(v)  # type: ignore[arg-type]
        return out if math.isfinite(out) else None
    except (TypeError, ValueError):
        return None


def _rsi_wilder_local(closes: list[float], length: int = 14) -> float | None:
    if len(closes) < length + 1:
        return None
    gains = losses = 0.0
    for i in range(1, length + 1):
        d = closes[i] - closes[i - 1]
        if d >= 0:
            gains += d
        else:
            losses -= d
    avg_gain = gains / length
    avg_loss = losses / length
    for i in range(length + 1, len(closes)):
        d = closes[i] - closes[i - 1]
        g = d if d > 0 else 0.0
        ln = -d if d < 0 else 0.0
        avg_gain = (avg_gain * (length - 1) + g) / length
        avg_loss = (avg_loss * (length - 1) + ln) / length
    if avg_loss == 0 and avg_gain == 0:
        return None
    if avg_loss == 0:
        return 100.0
    rs = avg_gain / avg_loss
    return _safe_float_local(100.0 - 100.0 / (1.0 + rs))


def _ema_local(closes: list[float], length: int) -> float | None:
    if len(closes) < length or length <= 0:
        return None
    k = 2.0 / (length + 1.0)
    ema = sum(closes[:length]) / length
    for price in closes[length:]:
        ema = price * k + ema * (1.0 - k)
    return _safe_float_local(ema)


def _compute_venue_ta(klines: tuple[dict[str, Any], ...]) -> dict[str, Any] | None:
    """Basic venue TA from serialised klines. Pure function — no I/O."""
    if not klines:
        return None
    closes = [c for k in klines if (c := _safe_float_local(k.get("close"))) and c > 0]
    last_price = _safe_float_local(klines[-1].get("close"))
    bars_count = len(klines)
    base: dict[str, Any] = {
        "bars_count": bars_count,
        "last_price": last_price,
        "trend": "neutral",
        "rsi_14": None,
        "ema_9": None,
        "ema_21": None,
    }
    if len(closes) < 22:
        return base
    rsi = _rsi_wilder_local(closes)
    ema_9 = _ema_local(closes, 9)
    ema_21 = _ema_local(closes, 21)
    if ema_9 is not None and ema_21 is not None:
        trend = "bullish" if ema_9 > ema_21 else ("bearish" if ema_9 < ema_21 else "neutral")
    else:
        trend = "neutral"
    return {
        "bars_count": bars_count,
        "last_price": last_price,
        "trend": trend,
        "rsi_14": round(rsi, 2) if rsi is not None else None,
        "ema_9": round(ema_9, 4) if ema_9 is not None else None,
        "ema_21": round(ema_21, 4) if ema_21 is not None else None,
    }


# ── Private block builders ────────────────────────────────────────────────────


def _klines_to_dicts(klines: tuple[BingXKline, ...]) -> tuple[dict[str, Any], ...]:
    return tuple(asdict(k) for k in klines)


def _build_venue_block(ctx: BingXCandidateContext) -> BingXVenueBlock:
    src = ctx.venue_ohlcv_source
    if src.status != "available":
        return BingXVenueBlock(
            venue_symbol=ctx.venue_symbol,
            status="unavailable",
            source=src.source_name,
            reason=src.reason,
        )
    klines_dicts = _klines_to_dicts(src.klines)
    return BingXVenueBlock(
        venue_symbol=ctx.venue_symbol,
        status="available",
        source=src.source_name,
        klines=klines_dicts,
        funding_rate=src.funding_rate,
        open_interest=src.open_interest,
        venue_ta=_compute_venue_ta(klines_dicts),
    )


def _build_underlying_block(ctx: BingXCandidateContext) -> BingXUnderlyingBlock:
    src = ctx.underlying_ohlcv_source
    return BingXUnderlyingBlock(
        underlying_symbol=ctx.underlying_symbol,
        market_type=ctx.market_type,
        ohlcv_status=src.status,
        source=src.source_name,
        quote=src.fmp_quote,
        reason=src.reason,
    )


def _build_options_block(ctx: BingXCandidateContext) -> BingXOptionsBlock:
    src = ctx.options_source
    if src.status != "available":
        return BingXOptionsBlock(
            status="unavailable",
            source=src.source_name,
            reason=src.reason,
        )
    chain_len = len(src.chain)
    quality = min(1.0, chain_len / 10.0) if chain_len > 0 else 0.0
    return BingXOptionsBlock(
        metrics={"chain_contracts": chain_len, "source": src.source_name},
        status="available",
        source=src.source_name,
        quality_score=round(quality, 4),
    )


async def _build_options_block_via_bridge(
    venue_symbol: str,
    market_type: str,
    options_snapshot_fn: OptionsSnapshotFn | None,
) -> BingXOptionsBlock:
    """Build the options block using the institutional ``build_options_bridge``.

    Returns a ``BingXOptionsBlock`` whose ``metrics`` is the bridge result's
    full ``to_dict()`` — every wall, IV rank, vanna/VEX/CEX exposure, dealer
    bias, max pain, and chain quality fields land here. Failures degrade to
    ``status="unavailable"`` with the bridge's stable reason code.
    """
    bridge_result = await build_options_bridge(
        venue_symbol,
        market_type=market_type,
        options_snapshot_fn=options_snapshot_fn,
    )
    payload = bridge_result.to_dict()
    if bridge_result.status != "available":
        return BingXOptionsBlock(
            status="unavailable",
            source=bridge_result.source,
            reason=bridge_result.reason,
            metrics=payload,
        )
    return BingXOptionsBlock(
        status="available",
        source=bridge_result.source,
        metrics=payload,
        quality_score=bridge_result.quality_score,
    )


async def _build_technical_block(underlying: str, market_type: str) -> BingXTechnicalBlock:
    """Underlying equity TA via EquityTASnapshotService. Unavailable for crypto."""
    if market_type not in ("stock_perp", "stock_index_perp"):
        return BingXTechnicalBlock(
            status="unavailable",
            source="none",
            reason="no_equity_ta_for_market_type",
        )
    try:
        service = EquityTASnapshotService(underlying)
        snapshot = await service.snapshot()
    except Exception as exc:
        logger.warning(
            "bingx_candidate_analysis.technical_failed ticker=%s error=%s", underlying, exc
        )
        return BingXTechnicalBlock(
            status="unavailable",
            source="fmp",
            reason="equity_ta_fetch_failed",
        )
    if not snapshot.get("ok"):
        return BingXTechnicalBlock(
            status="unavailable",
            source=str(snapshot.get("source", "fmp")),
            reason=str(snapshot.get("reason", "equity_ta_unavailable")),
        )
    bars_used = int(snapshot.get("bars_used") or 0)
    quality = min(1.0, bars_used / 200.0) if bars_used > 0 else 0.5
    return BingXTechnicalBlock(
        metrics=snapshot,
        status="available",
        source=str(snapshot.get("source", "fmp")),
        quality_score=round(quality, 4),
    )


async def _attach_venue_technical(
    block: BingXTechnicalBlock,
    *,
    venue_symbol: str,
    ctx: BingXCandidateContext,
    l2_block: BingXL2Block,
    timeframe: str,
    technical_fn: TechnicalCandlesFn | None,
) -> BingXTechnicalBlock:
    """Run the venue technical bridge and attach its result to ``block``.

    The bridge degrades cleanly when ``technical_fn`` is None or when the
    venue has insufficient klines — the returned block has the same
    underlying-side fields as ``block`` and a populated ``venue_technical``
    sub-dict with the bridge's status / reason / summary / quality.

    The L2 snapshot is injected so the venue technical payload's
    ``lob_dynamics`` block reflects the actual BingX L2 result rather than
    the technical terminal's default unavailable stub.
    """
    venue_src = ctx.venue_ohlcv_source
    klines: tuple[Any, ...] = venue_src.klines if venue_src.status == "available" else ()
    venue_result = await build_venue_technical(
        venue_symbol,
        klines,
        timeframe=timeframe,
        l2_snapshot=l2_block.lob_analysis,
        technical_fn=technical_fn,
    )
    return BingXTechnicalBlock(
        metrics=block.metrics,
        status=block.status,
        source=block.source,
        quality_score=block.quality_score,
        reason=block.reason,
        venue_technical=venue_result.to_dict(),
    )


async def _build_predictive_block(
    venue_symbol: str,
    market_type: str,
    *,
    equity_summary_fn: EquitySummaryFn | None = None,
) -> BingXPredictiveBlock:
    """Run the predictive bridge cascade and project to ``BingXPredictiveBlock``.

    When no fetcher is injected, the bridge falls all the
    way to ``equity_probabilistic_summary`` — which is the historical
    default.
    """
    # The equity heuristic is the default fallback fetcher — preserves the
    # behavior callers had before the bridge existed.
    fallback_equity_fn: EquitySummaryFn = equity_summary_fn or equity_probabilistic_summary

    bridge_result = await build_predictive_bridge(
        venue_symbol,
        market_type=market_type,
        equity_summary_fn=fallback_equity_fn,
    )

    if bridge_result.status != "available" or bridge_result.signal is None:
        return BingXPredictiveBlock(
            status="unavailable",
            source=bridge_result.signal.source if bridge_result.signal else "none",
            reason=bridge_result.reason or "predictive_unavailable",
            signal=bridge_result.signal.__dict__ if bridge_result.signal else None,
        )

    signal = bridge_result.signal
    return BingXPredictiveBlock(
        metrics=bridge_result.payload,
        status="available",
        source=signal.source,
        quality_score=signal.quality_score,
        signal={
            "directional_bias": signal.directional_bias,
            "probability_long": signal.probability_long,
            "probability_short": signal.probability_short,
            "confidence": signal.confidence,
            "horizon": signal.horizon,
            "source": signal.source,
            "quality_score": signal.quality_score,
            "reason_codes": list(signal.reason_codes),
        },
    )


async def _build_l2_block(
    bingx_client: BingXClient | None,
    venue_symbol: str,
    market_type: str,
) -> BingXL2Block:
    """LOB analysis via analyze_bingx_l2. Unavailable when client is absent."""
    if bingx_client is None:
        return BingXL2Block(
            status="unavailable",
            source="bingx_l2_unavailable",
            reason="no_client_configured",
        )
    try:
        analysis = await analyze_bingx_l2(
            bingx_client,  # type: ignore[arg-type]
            venue_symbol,
            market_type=market_type,
        )
    except Exception as exc:
        logger.warning("bingx_candidate_analysis.l2_failed symbol=%s error=%s", venue_symbol, exc)
        return BingXL2Block(
            status="unavailable",
            source="bingx_l2_unavailable",
            reason="l2_fetch_failed",
        )
    lob_dict = analysis.model_dump(mode="python")
    if not analysis.ok:
        return BingXL2Block(
            lob_analysis=lob_dict,
            status="unavailable",
            source=analysis.source,
            reason=analysis.error,
        )
    return BingXL2Block(
        lob_analysis=lob_dict,
        status="available",
        source=analysis.source,
        quality_score=analysis.data_quality_score,
    )


# ── Readiness and metadata ────────────────────────────────────────────────────


async def _build_exchange_derivatives_block(
    venue_symbol: str,
    market_type: str,
    exchange_derivatives_client: ExchangeDerivativesClient | None,
) -> BingXExchangeDerivativesBlock:
    result = await build_exchange_derivatives_bridge(
        venue_symbol,
        market_type=market_type,
        client=exchange_derivatives_client,
    )
    return BingXExchangeDerivativesBlock(
        metrics=result.metrics,
        providers=result.providers,
        status=result.status,
        source=result.source,
        data_sources=result.data_sources,
        quality_score=result.quality_score,
        reason=result.reason,
    )


def _compute_readiness_score(
    market_type: str,
    venue: BingXVenueBlock,
    technical: BingXTechnicalBlock,
    predictive: BingXPredictiveBlock,
    l2: BingXL2Block,
) -> float:
    """Aggregate quality score in [0.0, 1.0].

    Venue is mandatory — returns 0.0 if unavailable. Other blocks contribute
    their quality_score (falling back to 0.5 for available blocks without a
    score, or 0.0 for unavailable ones). Market-type-aware: missing
    technical/predictive for crypto is expected and does not penalize the score.
    """
    if venue.status != "available":
        return 0.0

    contributions: list[float] = [1.0]  # venue confirmed available

    if market_type in ("stock_perp", "stock_index_perp"):
        contributions.append(
            float(technical.quality_score or 0.5) if technical.status == "available" else 0.0
        )
        contributions.append(
            float(predictive.quality_score or 0.5) if predictive.status == "available" else 0.0
        )

    # L2 applies to all market types but degrades gracefully (pre-production default).
    contributions.append(float(l2.quality_score or 0.5) if l2.status == "available" else 0.2)

    return round(sum(contributions) / len(contributions), 4)


def _collect_data_sources(
    venue: BingXVenueBlock,
    underlying: BingXUnderlyingBlock,
    options: BingXOptionsBlock,
    technical: BingXTechnicalBlock,
    predictive: BingXPredictiveBlock,
    l2: BingXL2Block,
    exchange_derivatives: BingXExchangeDerivativesBlock | None = None,
) -> tuple[str, ...]:
    sources: list[str] = []
    if venue.status == "available":
        sources.append(venue.source)
    if underlying.ohlcv_status == "available":
        sources.append(underlying.source)
    if options.status == "available":
        sources.append(options.source)
    if technical.status == "available":
        sources.append(technical.source)
    if predictive.status == "available":
        sources.append(predictive.source)
    if l2.status == "available":
        sources.append(l2.source)
    if exchange_derivatives and exchange_derivatives.status == "available":
        sources.extend(exchange_derivatives.data_sources or (exchange_derivatives.source,))
    return tuple(sources)


def _collect_errors(
    venue: BingXVenueBlock,
    underlying: BingXUnderlyingBlock,
    options: BingXOptionsBlock,
    technical: BingXTechnicalBlock,
    predictive: BingXPredictiveBlock,
    l2: BingXL2Block,
    exchange_derivatives: BingXExchangeDerivativesBlock | None = None,
) -> dict[str, str]:
    errors: dict[str, str] = {}
    if venue.status != "available" and venue.reason:
        errors["venue"] = venue.reason
    if underlying.ohlcv_status != "available" and underlying.reason:
        errors["underlying"] = underlying.reason
    if options.status != "available" and options.reason:
        errors["options"] = options.reason
    if technical.status != "available" and technical.reason:
        errors["technical"] = technical.reason
    if predictive.status != "available" and predictive.reason:
        errors["predictive"] = predictive.reason
    if l2.status != "available" and l2.reason:
        errors["l2"] = l2.reason
    if (
        exchange_derivatives
        and exchange_derivatives.status != "available"
        and exchange_derivatives.reason
        and exchange_derivatives.reason != "exchange_derivatives_only_for_crypto"
    ):
        errors["exchange_derivatives"] = exchange_derivatives.reason
    return errors


# ── Public builder ────────────────────────────────────────────────────────────


async def build_candidate_analysis(
    venue_symbol: str,
    *,
    bingx_client: BingXClient | None = None,
    fmp_client: FMPQuoteClient | None = None,
    massive_client: MassiveOptionsClient | None = None,
    alpaca_client: AlpacaBarsClient | None = None,
    ws_hub: object | None = None,
    meta_learner: MetaLearnerClient | None = None,
    options_snapshot_fn: OptionsSnapshotFn | None = None,
    venue_technical_fn: TechnicalCandlesFn | None = None,
    equity_summary_fn: EquitySummaryFn | None = None,
    exchange_derivatives_client: ExchangeDerivativesClient | None = None,
    kline_interval: str = "5m",
    kline_limit: int = 2000,
) -> BingXCandidateAnalysis:
    """Build a unified BingXCandidateAnalysis for *venue_symbol*.

    Context (venue OHLCV, underlying OHLCV, options) is fetched concurrently
    with L2, technical, predictive, and the institutional options bridge.
    Each engine degrades to an unavailable block on failure; no single
    failure blocks the others.

    ``options_snapshot_fn`` is the injectable fetcher used by the options
    bridge. When ``None``, the bridge returns ``unavailable`` with reason
    ``no_options_snapshot_fn`` — equivalent to "no options pipeline wired".
    The router-level caller passes
    :func:`backend.routers.options_router.options_snapshot_service`.

    ``venue_technical_fn`` enables the full SMC/VSA/FVG/VP/OF stack against
    the BingX venue klines (via the technical bridge). When ``None``, the
    venue-technical sub-block is left unavailable. The router-level caller
    passes
    :func:`backend.services.technical_terminal_payload.build_technical_terminal_payload_from_candles`.
    """
    underlying = underlying_from_bingx_symbol(venue_symbol)
    market_type: MarketType = classify_underlying(venue_symbol)

    ctx_coro = build_candidate_context(
        venue_symbol,
        bingx_client=bingx_client,
        fmp_client=fmp_client,
        massive_client=massive_client,
        alpaca_client=alpaca_client,
        ws_hub=ws_hub,
        meta_learner=meta_learner,
        kline_interval=kline_interval,
        kline_limit=kline_limit,
    )
    l2_coro = _build_l2_block(bingx_client, venue_symbol, market_type)
    tech_coro = _build_technical_block(underlying, market_type)
    pred_coro = _build_predictive_block(
        venue_symbol,
        market_type,
        equity_summary_fn=equity_summary_fn,
    )
    options_coro = _build_options_block_via_bridge(venue_symbol, market_type, options_snapshot_fn)
    exchange_derivatives_coro = _build_exchange_derivatives_block(
        venue_symbol,
        market_type,
        exchange_derivatives_client,
    )

    ctx, l2, technical, predictive, bridge_options, exchange_derivatives = await asyncio.gather(
        ctx_coro, l2_coro, tech_coro, pred_coro, options_coro, exchange_derivatives_coro
    )

    venue = _build_venue_block(ctx)
    underlying_block = _build_underlying_block(ctx)
    # Prefer the institutional bridge result when it succeeded. If the bridge
    # is unavailable (no fetcher wired, crypto, etc.) fall back to the
    # context-derived options block so chain-level metadata still surfaces.
    if bridge_options.status == "available":
        options = bridge_options
    else:
        context_options = _build_options_block(ctx)
        options = context_options if context_options.status == "available" else bridge_options

    # Enrich the technical block with the full venue SMC/VSA/FVG/VP/OF stack.
    # Runs sequentially after the gather because it needs both ctx.klines and
    # the L2 result — the bridge degrades cleanly when either is missing.
    technical = await _attach_venue_technical(
        technical,
        venue_symbol=venue_symbol,
        ctx=ctx,
        l2_block=l2,
        timeframe=kline_interval,
        technical_fn=venue_technical_fn,
    )

    # ── Institutional Research Snapshot ──────────────────────────────────────
    # Fetches the three-desk reading (predictive, options-GEX, technical).
    # Runs after venue_technical is attached so all resolved symbols are
    # available.  The bridge is always safe to call — it never raises and
    # degrades to all-unavailable desks (Phase 1 stub returns that by default).
    institutional_research: InstitutionalResearchSnapshot | None = None
    try:
        options_snapshot_data = None
        if hasattr(options, "raw_snapshot"):
            options_snapshot_data = options.raw_snapshot

        institutional_research = await fetch_institutional_snapshot(
            venue_symbol,
            underlying_symbol=underlying,
            market_type=market_type,
            technical_payload=technical.venue_technical,
            options_snapshot=options_snapshot_data,
            klines=venue.klines,
        )
        logger.debug(
            "bingx_candidate_analysis.institutional_snapshot " "venue=%s actionable=%s desks=%s",
            venue_symbol,
            institutional_research.is_actionable(),
            institutional_research.desk_summary(),
        )
    except Exception as _ir_exc:
        logger.warning(
            "bingx_candidate_analysis.institutional_snapshot_failed " "venue=%s error=%s",
            venue_symbol,
            str(_ir_exc)[:180],
        )

    if (
        institutional_research is not None
        and institutional_research.options_gex.desk_status.is_available
    ):
        # Inyectar el reporte real para que el motor de decisiones no use el fallback
        object.__setattr__(
            options,
            "predictive_report",
            institutional_research.options_gex.predictive_report,
        )

    data_sources = _collect_data_sources(
        venue, underlying_block, options, technical, predictive, l2, exchange_derivatives
    )
    errors = _collect_errors(
        venue, underlying_block, options, technical, predictive, l2, exchange_derivatives
    )
    if institutional_research is None:
        errors["institutional_research"] = "institutional_snapshot_failed"
    readiness = _compute_readiness_score(market_type, venue, technical, predictive, l2)

    return BingXCandidateAnalysis(
        venue_symbol=venue_symbol,
        underlying_symbol=underlying,
        market_type=market_type,
        venue=venue,
        underlying=underlying_block,
        options=options,
        technical=technical,
        predictive=predictive,
        l2=l2,
        exchange_derivatives=exchange_derivatives,
        institutional_research=institutional_research,
        data_sources=data_sources,
        errors=errors,
        readiness_score=readiness,
        captured_at=datetime.now(UTC).isoformat(),
    )


__all__ = [
    "BingXCandidateAnalysis",
    "BingXEngineStatus",
    "BingXExchangeDerivativesBlock",
    "BingXL2Block",
    "BingXOptionsBlock",
    "BingXPredictiveBlock",
    "BingXTechnicalBlock",
    "BingXUnderlyingBlock",
    "BingXVenueBlock",
    "InstitutionalResearchSnapshot",
    "build_candidate_analysis",
]
