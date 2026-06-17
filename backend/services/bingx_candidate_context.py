from __future__ import annotations

from typing import Any, Literal, Protocol

"""BingX candidate data-context contract.

Maps a BingX venue symbol to all data sources needed for analysis,
separating venue data (BingX perp OHLCV/OI/funding) from underlying data
(FMP/Alpaca for stocks; BingX for crypto) and signalling per-source
availability without raising.
"""


import asyncio
from collections.abc import Awaitable
from dataclasses import asdict, dataclass
from datetime import UTC, datetime

from backend.config.logger_setup import get_logger
from backend.layer_1_data.datos.bingx_client import BingXClient, BingXKline
from backend.layer_1_data.datos.bingx_ws_hub import BingXMicroBar
from backend.services.bingx_symbol_linker import classify_underlying, underlying_from_bingx_symbol
from backend.services.bingx_universe import MarketType

logger = get_logger(__name__)

# ── Source status ──────────────────────────────────────────────────────────────
SourceStatus = Literal["available", "unavailable"]

# Reason codes — stable strings; do not rename without updating consumers.
REASON_NO_CLIENT = "no_client_configured"
REASON_FETCH_FAILED = "fetch_failed"
REASON_NO_OPTIONS_FOR_CRYPTO = "no_options_chain_for_crypto"
REASON_NO_OPTIONS_FOR_INDEX = "no_options_chain_for_index"
REASON_NOT_FITTED = "model_not_fitted"


# ── Optional-client Protocols ──────────────────────────────────────────────────
class FMPQuoteClient(Protocol):
    def get_quote(self, symbol: str) -> Awaitable[object | None]: ...


class MassiveOptionsClient(Protocol):
    def get_options_chain(self, ticker: str) -> Awaitable[list[dict[str, Any]] | None]: ...


class AlpacaBarsClient(Protocol):
    def get_historical_bars(
        self,
        symbol: str,
        timeframe: str,
        *,
        max_bars: int,
        limit: int | None,
    ) -> Awaitable[list[dict[str, Any]]]: ...


class MetaLearnerClient(Protocol):
    is_fitted: bool
    model_type: str


# ── Source blocks ──────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class VenueOHLCVBlock:
    """BingX perp venue data: OHLCV klines + open interest + funding + ticker."""

    status: SourceStatus
    source_name: str
    reason: str | None = None
    klines: tuple[BingXKline, ...] = ()
    open_interest: float | None = None
    funding_rate: float | None = None
    last_price: float | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class UnderlyingOHLCVBlock:
    """Underlying-asset OHLCV: FMP+Alpaca for stocks; BingX daily for crypto."""

    status: SourceStatus
    source_name: str  # "alpaca" | "fmp_historical" | "bingx_perp"
    reason: str | None = None
    bars: tuple[dict[str, Any], ...] = ()
    fmp_quote: dict[str, Any] | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class OptionsSourceBlock:
    """Options chain: Massive/Polygon for stock underlyings; unavailable for crypto."""

    status: SourceStatus
    source_name: str  # "massive_polygon" | "none"
    reason: str | None = None
    chain: tuple[dict[str, Any], ...] = ()

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class PredictiveSourceBlock:
    """Ensemble meta-learner readiness signal (no prediction without feature matrix)."""

    status: SourceStatus
    source_name: str  # "ensemble_meta_learner" | "unavailable"
    reason: str | None = None
    is_fitted: bool = False
    model_version: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class L2SourceBlock:
    """BingX WebSocket hub L2 presence signal. Streaming-only — snapshot is always empty."""

    status: SourceStatus
    source_name: str  # "bingx_ws_hub" | "unavailable"
    reason: str | None = None
    micro_bars: tuple[BingXMicroBar, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


# ── Top-level context ──────────────────────────────────────────────────────────


@dataclass(frozen=True)
class BingXCandidateContext:
    """Full data-source routing map for one BingX candidate symbol.

    Each *_source block carries status + reason (UNAVAILABLE with reason when
    the corresponding client is absent or the fetch fails) plus whatever data
    was successfully fetched.  No source failure propagates to other blocks.
    """

    venue_symbol: str
    underlying_symbol: str
    market_type: MarketType

    venue_ohlcv_source: VenueOHLCVBlock
    underlying_ohlcv_source: UnderlyingOHLCVBlock
    options_source: OptionsSourceBlock
    predictive_source: PredictiveSourceBlock
    l2_source: L2SourceBlock

    captured_at: str = ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


# ── Private helpers ────────────────────────────────────────────────────────────


def _object_to_dict(value: object | None) -> dict[str, Any] | None:
    if value is None:
        return None
    if isinstance(value, dict):
        return dict(value)
    model_dump = getattr(value, "model_dump", None)
    if callable(model_dump):
        dumped = model_dump(mode="python")
        return dumped if isinstance(dumped, dict) else None
    return None


def _parse_float(d: Any, *keys: str) -> float | None:
    if not isinstance(d, dict):
        return None
    for k in keys:
        v = d.get(k)
        if v is not None:
            try:
                return float(v)
            except (TypeError, ValueError):
                continue
    return None


# ── Private block builders ─────────────────────────────────────────────────────


async def _build_venue_ohlcv(
    venue_symbol: str,
    bingx_client: BingXClient | None,
    kline_interval: str,
    kline_limit: int,
) -> VenueOHLCVBlock:
    if bingx_client is None:
        return VenueOHLCVBlock(
            status="unavailable",
            source_name="bingx_perp",
            reason=REASON_NO_CLIENT,
        )
    try:
        klines = tuple(
            await bingx_client.fetch_klines_perp(
                venue_symbol,
                kline_interval,
                limit=kline_limit,
            )
        )
    except Exception:
        logger.debug("bingx_candidate_context.klines_failed symbol=%s", venue_symbol)
        return VenueOHLCVBlock(
            status="unavailable",
            source_name="bingx_perp",
            reason=REASON_FETCH_FAILED,
        )

    oi: float | None = None
    funding: float | None = None

    try:
        oi_raw = await bingx_client.fetch_open_interest(venue_symbol)
        oi = _parse_float(oi_raw, "openInterest", "openInterestValue")
    except Exception:
        logger.debug("bingx_candidate_context.oi_failed symbol=%s", venue_symbol)

    try:
        funding_raw = await bingx_client.fetch_funding_rate(venue_symbol)
        funding = _parse_float(funding_raw, "lastFundingRate", "fundingRate")
    except Exception:
        logger.debug("bingx_candidate_context.funding_failed symbol=%s", venue_symbol)

    return VenueOHLCVBlock(
        status="available",
        source_name="bingx_perp",
        klines=klines,
        open_interest=oi,
        funding_rate=funding,
        last_price=klines[-1].close if klines else None,
    )


async def _build_underlying_ohlcv(
    underlying: str,
    market_type: MarketType,
    venue_symbol: str,
    fmp_client: FMPQuoteClient | None,
    alpaca_client: AlpacaBarsClient | None,
    bingx_client: BingXClient | None,
) -> UnderlyingOHLCVBlock:
    if market_type == "crypto_standard":
        if bingx_client is None:
            return UnderlyingOHLCVBlock(
                status="unavailable",
                source_name="bingx_perp",
                reason=REASON_NO_CLIENT,
            )
        try:
            raw_klines = await bingx_client.fetch_klines_perp(venue_symbol, "1D", limit=90)
            bars: tuple[dict[str, Any], ...] = tuple(
                {
                    "t": k.open_time_ms,
                    "o": k.open,
                    "h": k.high,
                    "l": k.low,
                    "c": k.close,
                    "v": k.volume,
                }
                for k in raw_klines
            )
            return UnderlyingOHLCVBlock(
                status="available",
                source_name="bingx_perp",
                bars=bars,
            )
        except Exception:
            logger.debug("bingx_candidate_context.crypto_underlying_failed symbol=%s", venue_symbol)
            return UnderlyingOHLCVBlock(
                status="unavailable",
                source_name="bingx_perp",
                reason=REASON_FETCH_FAILED,
            )

    # stock_perp / stock_index_perp — best-effort FMP quote + Alpaca bars
    fmp_quote: dict[str, Any] | None = None
    if fmp_client is not None:
        try:
            fmp_quote = _object_to_dict(await fmp_client.get_quote(underlying))
        except Exception:
            logger.debug("bingx_candidate_context.fmp_quote_failed symbol=%s", underlying)

    bars_list: list[dict[str, Any]] = []
    source_name = "fmp_historical"
    if alpaca_client is not None:
        try:
            bars_list = await alpaca_client.get_historical_bars(
                underlying, "1Day", max_bars=252, limit=252
            )
            source_name = "alpaca"
        except Exception:
            logger.debug("bingx_candidate_context.alpaca_bars_failed symbol=%s", underlying)

    if fmp_quote is None and not bars_list:
        return UnderlyingOHLCVBlock(
            status="unavailable",
            source_name=source_name,
            reason=(
                REASON_NO_CLIENT
                if (fmp_client is None and alpaca_client is None)
                else REASON_FETCH_FAILED
            ),
        )

    return UnderlyingOHLCVBlock(
        status="available",
        source_name=source_name,
        bars=tuple(bars_list),
        fmp_quote=fmp_quote,
    )


async def _skipped_options_block() -> OptionsSourceBlock:
    """Placeholder when institutional bridge supplies options (evita Massive duplicado)."""
    return OptionsSourceBlock(
        status="unavailable",
        source_name="none",
        reason="skipped_light_options_institutional_tier",
    )


async def _build_options(
    underlying: str,
    market_type: MarketType,
    massive_client: MassiveOptionsClient | None,
) -> OptionsSourceBlock:
    if market_type == "crypto_standard":
        return OptionsSourceBlock(
            status="unavailable",
            source_name="none",
            reason=REASON_NO_OPTIONS_FOR_CRYPTO,
        )
    if market_type == "stock_index_perp":
        return OptionsSourceBlock(
            status="unavailable",
            source_name="none",
            reason=REASON_NO_OPTIONS_FOR_INDEX,
        )
    if massive_client is None:
        return OptionsSourceBlock(
            status="unavailable",
            source_name="massive_polygon",
            reason=REASON_NO_CLIENT,
        )
    try:
        chain_raw = await massive_client.get_options_chain(underlying)
        chain: tuple[dict[str, Any], ...] = tuple(chain_raw) if chain_raw else ()
        return OptionsSourceBlock(
            status="available" if chain else "unavailable",
            source_name="massive_polygon",
            reason=None if chain else REASON_FETCH_FAILED,
            chain=chain,
        )
    except Exception:
        logger.debug("bingx_candidate_context.options_failed symbol=%s", underlying)
        return OptionsSourceBlock(
            status="unavailable",
            source_name="massive_polygon",
            reason=REASON_FETCH_FAILED,
        )


def _build_predictive(meta_learner: MetaLearnerClient | None) -> PredictiveSourceBlock:
    if meta_learner is None:
        return PredictiveSourceBlock(
            status="unavailable",
            source_name="ensemble_meta_learner",
            reason=REASON_NO_CLIENT,
        )
    if not meta_learner.is_fitted:
        return PredictiveSourceBlock(
            status="unavailable",
            source_name="ensemble_meta_learner",
            reason=REASON_NOT_FITTED,
        )
    version = getattr(meta_learner, "model_type", None)
    return PredictiveSourceBlock(
        status="available",
        source_name="ensemble_meta_learner",
        is_fitted=True,
        model_version=str(version) if version is not None else None,
    )


def _build_l2(ws_hub: object | None) -> L2SourceBlock:
    if ws_hub is None:
        return L2SourceBlock(
            status="unavailable",
            source_name="bingx_ws_hub",
            reason=REASON_NO_CLIENT,
        )
    return L2SourceBlock(
        status="available",
        source_name="bingx_ws_hub",
    )


# ── Public builder ─────────────────────────────────────────────────────────────


async def build_candidate_context(
    venue_symbol: str,
    *,
    bingx_client: BingXClient | None = None,
    fmp_client: FMPQuoteClient | None = None,
    massive_client: MassiveOptionsClient | None = None,
    alpaca_client: AlpacaBarsClient | None = None,
    ws_hub: object | None = None,
    meta_learner: MetaLearnerClient | None = None,
    kline_interval: str = "5m",
    kline_limit: int = 2000,
    skip_light_options: bool = False,
) -> BingXCandidateContext:
    """Build a :class:`BingXCandidateContext` for *venue_symbol*.

    Source blocks are fetched concurrently where possible.  A failed or
    unconfigured source yields an UNAVAILABLE block with a reason code rather
    than propagating; no single source failure breaks the others.
    """
    underlying = underlying_from_bingx_symbol(venue_symbol)
    market_type = classify_underlying(venue_symbol)

    venue_ohlcv, underlying_ohlcv, options = await asyncio.gather(
        _build_venue_ohlcv(venue_symbol, bingx_client, kline_interval, kline_limit),
        _build_underlying_ohlcv(
            underlying, market_type, venue_symbol, fmp_client, alpaca_client, bingx_client
        ),
        (
            _skipped_options_block()
            if skip_light_options
            else _build_options(underlying, market_type, massive_client)
        ),
    )

    return BingXCandidateContext(
        venue_symbol=venue_symbol,
        underlying_symbol=underlying,
        market_type=market_type,
        venue_ohlcv_source=venue_ohlcv,
        underlying_ohlcv_source=underlying_ohlcv,
        options_source=options,
        predictive_source=_build_predictive(meta_learner),
        l2_source=_build_l2(ws_hub),
        captured_at=datetime.now(UTC).isoformat(),
    )
