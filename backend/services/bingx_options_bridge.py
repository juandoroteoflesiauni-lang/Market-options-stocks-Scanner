from __future__ import annotations
from typing import Literal, Any
"""Bridge BingX stock-perp symbols → institutional options metrics.

Resolves the right options snapshot for each BingX market type and extracts a
JSON-safe ``BingXOptionsBridgeResult`` carrying the full institutional metric
set (GEX walls, IV surface, dealer flow regimes, vanna/VEX/CEX, max pain,
zero gamma, net DEX). Each market type has explicit handling:

- ``stock_perp``       → query ``options_snapshot_service(underlying)``
- ``stock_index_perp`` → map index ticker → optionable ETF proxy (SPY/QQQ/IWM/DIA)
- ``crypto_standard``  → unavailable with ``no_equity_options_for_crypto``
- anything else        → unavailable with a stable reason code

The fetch function is injected so this module is independently testable; the
production caller wires it to
:func:`backend.api.routes.options_router.options_snapshot_service`.
"""


import math
from collections.abc import Awaitable, Callable
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime

from backend.config.logger_setup import get_logger
from backend.services.bingx_symbol_linker import classify_underlying, underlying_from_bingx_symbol

logger = get_logger(__name__)


# ── Stable reason codes ──────────────────────────────────────────────────────
# These strings are part of the public contract: dashboards and runbooks
# match on them. Do not rename without coordinating with consumers.
REASON_NO_OPTIONS_FOR_CRYPTO = "no_equity_options_for_crypto"
REASON_NO_PROXY_FOR_INDEX = "no_index_proxy_for_underlying"
REASON_MARKET_TYPE_EXCLUDED = "market_type_excluded"
REASON_SNAPSHOT_FETCH_FAILED = "snapshot_fetch_failed"
REASON_SNAPSHOT_NOT_OK = "snapshot_not_ok"
REASON_NO_FETCHER = "no_options_snapshot_fn"
REASON_NO_UNDERLYING = "underlying_not_resolved"

# Index → optionable ETF proxy. Keys are the BingX underlying root
# (after ``underlying_from_bingx_symbol``); values are the listed ETF that
# carries deep, liquid options chains. The mapping is intentionally narrow:
# every additional proxy is a survival risk because dealer positioning on the
# ETF only partially reflects the underlying index basket.
INDEX_OPTIONS_PROXIES: dict[str, str] = {
    "SPX": "SPY",
    "US500": "SPY",
    "NDX": "QQQ",
    "US100": "QQQ",
    "RUT": "IWM",
    "IWM": "IWM",  # ETF passthrough
    "DJI": "DIA",
    "DJX": "DIA",
}


SourceStatus = Literal["available", "unavailable"]

# Callable signature matches ``options_snapshot_service(symbol, expiry, r)``
OptionsSnapshotFn = Callable[[str, str | None, float], Awaitable[Any]]


# ── Dataclasses ──────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class BingXOptionsMetrics:
    """Institutional options metric set for one BingX equity symbol.

    All numeric fields are nullable so a partial snapshot degrades gracefully
    rather than raising. The dataclass is JSON-safe via ``asdict``.
    """

    # Spot used by the options pipeline (may differ from venue last price)
    spot: float | None

    # GEX walls
    call_wall: float | None
    put_wall: float | None
    call_wall_moderate: float | None
    put_wall_moderate: float | None
    zero_gamma: float | None
    max_pain: float | None
    net_gex_total: float
    call_gex_total: float
    put_gex_total: float
    dealer_bias: str  # BULLISH / BEARISH / NEUTRAL
    squeeze_probability: float

    # DEX
    total_dex: float
    dex_flip_level: float | None

    # Vanna / VEX / CEX
    total_vanna: float | None
    total_vex: float | None
    total_cex: float | None
    vanna_exposure_regime: str
    vex_regime: str
    cex_regime: str

    # IV surface
    atm_iv: float | None
    iv_rank_hv_rolling: float | None
    iv_rank_cross_expiry: float | None
    iv_percentile_cross_term: float | None
    vrp: float | None

    # Put/Call ratios
    pcr_oi: float | None
    pcr_volume: float | None

    # Wall geometry (computed against spot)
    wall_distance_pct: float | None
    wall_direction: str | None  # "above" | "below" | None

    # Confluence / engine
    confluence_score: float | None
    confluence_signal: str | None
    confluence_confidence: float | None

    # Chain stats
    chain_contracts: int

    # Advanced Metrics
    ndde: float | None = None
    charm_flow: str | float | None = None
    implied_percentile_99: float | None = None


@dataclass(frozen=True)
class BingXOptionsBridgeResult:
    """Result of bridging a BingX symbol to its options snapshot."""

    status: SourceStatus
    source: str  # "underlying_options" | "index_proxy_options" | "none"
    market_type: str
    underlying_symbol: str
    proxy_symbol: str | None
    options_symbol: str  # what was actually queried
    metrics: BingXOptionsMetrics | None
    raw_snapshot: dict[str, Any] | None = None
    chain_quality: dict[str, Any] = field(default_factory=dict)
    quality_score: float | None = None
    reason: str | None = None
    fetched_at: str = ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


# ── Value-extraction helpers (defensive against dict | Pydantic model) ───────


def _get(payload: object, key: str) -> object | None:
    if isinstance(payload, dict):
        return payload.get(key)
    return getattr(payload, key, None)


def _safe_float(value: object) -> float | None:
    if value is None:
        return None
    try:
        out = float(value)  # type: ignore[arg-type]
        return out if math.isfinite(out) else None
    except (TypeError, ValueError):
        return None


def _safe_int(value: object, default: int = 0) -> int:
    if value is None:
        return default
    try:
        return int(cast(Any, value))
    except (TypeError, ValueError):
        return default


def _round_or_none(value: float | None, ndigits: int = 4) -> float | None:
    return None if value is None else round(value, ndigits)


# ── Spot-relative wall geometry ──────────────────────────────────────────────


def _nearest_wall(gex_levels: object, spot: float | None) -> float | None:
    candidates = [
        _safe_float(_get(gex_levels, "zero_gamma_level")),
        _safe_float(_get(gex_levels, "call_wall")),
        _safe_float(_get(gex_levels, "put_wall")),
    ]
    valid = [c for c in candidates if c is not None and c > 0]
    if not valid:
        return None
    if spot is None or spot <= 0:
        return valid[0]
    return min(valid, key=lambda v: abs(v - spot))


def _wall_direction(wall: float | None, spot: float | None) -> str | None:
    if wall is None or spot is None:
        return None
    return "above" if wall >= spot else "below"


def _wall_distance_pct(wall: float | None, spot: float | None) -> float | None:
    if wall is None or spot is None or spot <= 0:
        return None
    return round(abs(wall - spot) / spot * 100.0, 4)


# ── Chain analytics ──────────────────────────────────────────────────────────


def _put_call_ratio_oi(chain: object) -> float | None:
    if not isinstance(chain, list) or not chain:
        return None
    call_total = 0.0
    put_total = 0.0
    for row in chain:
        call_total += _safe_float(_get(row, "call_oi")) or 0.0
        put_total += _safe_float(_get(row, "put_oi")) or 0.0
    if call_total <= 0:
        return None
    return round(put_total / call_total, 4)


def _put_call_ratio_volume(chain: object) -> float | None:
    if not isinstance(chain, list) or not chain:
        return None
    call_total = 0.0
    put_total = 0.0
    for row in chain:
        call_total += _safe_float(_get(row, "call_volume")) or 0.0
        put_total += _safe_float(_get(row, "put_volume")) or 0.0
    if call_total <= 0:
        return None
    return round(put_total / call_total, 4)


# ── Quality scoring ──────────────────────────────────────────────────────────


def _compute_quality_score(metrics: BingXOptionsMetrics) -> float:
    """Heuristic quality in [0, 1] — does the snapshot cover the four
    institutional analytics quadrants (chain breadth, walls, IV, exposure)?

    Survival framing: a snapshot missing walls or IV cannot back risk-sizing
    decisions; the score collapses well before risk-desk thresholds bite.
    """
    score = 0.0
    if metrics.chain_contracts >= 10:
        score += 0.4
    elif metrics.chain_contracts >= 3:
        score += 0.2
    if metrics.call_wall is not None and metrics.put_wall is not None:
        score += 0.2
    elif metrics.call_wall is not None or metrics.put_wall is not None:
        score += 0.1
    if metrics.atm_iv is not None or metrics.iv_rank_hv_rolling is not None:
        score += 0.2
    if metrics.net_gex_total != 0.0 or metrics.total_dex != 0.0:
        score += 0.2
    return round(min(1.0, score), 4)


# ── Metric extraction ────────────────────────────────────────────────────────


def _extract_metrics(snapshot: object) -> BingXOptionsMetrics:
    """Pull every institutional analytic from an ``OptionsSnapshotResponse``-shaped object."""
    spot = _safe_float(_get(snapshot, "spot"))
    gex = _get(snapshot, "gex_levels")
    iv = _get(snapshot, "iv_surface")
    confluence = _get(snapshot, "confluence")
    chain = _get(snapshot, "chain")
    total_dex = _safe_float(_get(snapshot, "total_dex"))
    if total_dex is None and isinstance(chain, list):
        # Fall back to per-strike aggregation. ``OptionsSnapshotResponse``
        # populates the top-level field via ``enrich_chain_with_dex`` but
        # older fixtures and lightweight test snapshots only carry per-row
        # ``net_dex`` — degrading to None here would silently drop signal.
        per_row = [_safe_float(_get(row, "net_dex")) for row in chain]
        finite = [v for v in per_row if v is not None]
        total_dex = sum(finite) if finite else 0.0
    elif total_dex is None:
        total_dex = 0.0
    dex_flip = _safe_float(_get(snapshot, "dex_flip_level"))

    # Net GEX / dealer bias — prefer real GEX wall first
    primary_wall = _nearest_wall(gex, spot)

    metrics = BingXOptionsMetrics(
        spot=spot,
        call_wall=_safe_float(_get(gex, "call_wall")),
        put_wall=_safe_float(_get(gex, "put_wall")),
        call_wall_moderate=_safe_float(_get(gex, "call_wall_moderate")),
        put_wall_moderate=_safe_float(_get(gex, "put_wall_moderate")),
        zero_gamma=_safe_float(_get(gex, "zero_gamma_level")),
        max_pain=_safe_float(_get(gex, "max_pain")),
        net_gex_total=_safe_float(_get(gex, "net_gex_total")) or 0.0,
        call_gex_total=_safe_float(_get(gex, "call_gex_total")) or 0.0,
        put_gex_total=_safe_float(_get(gex, "put_gex_total")) or 0.0,
        dealer_bias=str(_get(gex, "dealer_bias") or "NEUTRAL"),
        squeeze_probability=_safe_float(_get(gex, "squeeze_probability")) or 0.0,
        total_dex=total_dex,
        dex_flip_level=dex_flip,
        total_vanna=_safe_float(_get(confluence, "total_vanna_exposure")),
        total_vex=_safe_float(_get(confluence, "total_vex")),
        total_cex=_safe_float(_get(confluence, "total_cex")),
        vanna_exposure_regime=str(_get(confluence, "vanna_exposure_regime") or "NEUTRAL"),
        vex_regime=str(_get(confluence, "vex_regime") or "NEUTRAL"),
        cex_regime=str(_get(confluence, "cex_regime") or "NEUTRAL"),
        atm_iv=_safe_float(_get(iv, "atm_iv")),
        iv_rank_hv_rolling=_safe_float(_get(iv, "iv_rank_hv_rolling")),
        iv_rank_cross_expiry=_safe_float(_get(iv, "iv_rank_cross_expiry")),
        iv_percentile_cross_term=_safe_float(_get(iv, "iv_percentile_cross_term")),
        vrp=_safe_float(_get(iv, "vrp")),
        pcr_oi=_safe_float(_get(confluence, "pcr_oi")) or _put_call_ratio_oi(chain),
        pcr_volume=_safe_float(_get(confluence, "pcr_volume")) or _put_call_ratio_volume(chain),
        wall_distance_pct=_wall_distance_pct(primary_wall, spot),
        wall_direction=_wall_direction(primary_wall, spot),
        confluence_score=_safe_float(_get(confluence, "score")),
        confluence_signal=(str(sig) if (sig := _get(confluence, "signal")) is not None else None),
        confluence_confidence=_safe_float(_get(confluence, "confidence")),
        ndde=_safe_float(_get(snapshot, "ndde")),
        charm_flow=_get(snapshot, "charm_flow"),
        implied_percentile_99=_safe_float(_get(snapshot, "implied_percentile_99")),
        chain_contracts=len(chain) if isinstance(chain, list) else 0,
    )
    return metrics


def _chain_quality_dict(snapshot: object) -> dict[str, Any]:
    """Normalise the snapshot's ``chain_quality`` into a JSON-safe dict."""
    raw = _get(snapshot, "chain_quality")
    if isinstance(raw, dict):
        # Shallow-copy to detach from any model-internal reference; nested
        # values may themselves be plain dicts which is fine for JSON.
        return dict(raw)
    return {}


# ── Symbol → options-query routing ───────────────────────────────────────────


def resolve_options_symbol(
    venue_symbol: str, market_type: str
) -> tuple[str | None, str | None, str | None]:
    """Resolve ``(options_symbol, proxy_symbol, reason)`` for a BingX symbol.

    ``proxy_symbol`` is non-None only for index perps that route to an ETF.
    ``reason`` is non-None only when no options query is possible — the
    caller surfaces it on the unavailable result.
    """
    underlying = underlying_from_bingx_symbol(venue_symbol)
    if not underlying:
        return None, None, REASON_NO_UNDERLYING

    if market_type == "stock_perp":
        return underlying, None, None

    if market_type == "stock_index_perp":
        proxy = INDEX_OPTIONS_PROXIES.get(underlying)
        if proxy:
            return proxy, proxy, None
        return None, None, REASON_NO_PROXY_FOR_INDEX

    if market_type == "crypto_standard":
        return None, None, REASON_NO_OPTIONS_FOR_CRYPTO

    return None, None, REASON_MARKET_TYPE_EXCLUDED


# ── Public builder ───────────────────────────────────────────────────────────


async def build_options_bridge(
    venue_symbol: str,
    *,
    market_type: str | None = None,
    options_snapshot_fn: OptionsSnapshotFn | None = None,
    r: float = 0.04,
    expiry: str | None = None,
) -> BingXOptionsBridgeResult:
    """Bridge a BingX venue symbol to its institutional options snapshot.

    The fetch function is injected so the bridge stays decoupled from FastAPI
    routers and can be unit-tested without the full options pipeline. The
    production caller passes
    :func:`backend.api.routes.options_router.options_snapshot_service`.

    Each unavailable path returns a stable ``reason`` code (one of the
    ``REASON_*`` constants in this module). Exceptions raised by the fetcher
    are caught and surfaced as ``snapshot_fetch_failed`` — the bridge never
    propagates errors to callers.
    """
    resolved_market_type = market_type or classify_underlying(venue_symbol)
    underlying = underlying_from_bingx_symbol(venue_symbol)
    options_symbol, proxy_symbol, route_reason = resolve_options_symbol(
        venue_symbol, resolved_market_type
    )

    if route_reason is not None or options_symbol is None:
        return BingXOptionsBridgeResult(
            status="unavailable",
            source="none",
            market_type=resolved_market_type,
            underlying_symbol=underlying,
            proxy_symbol=proxy_symbol,
            options_symbol=options_symbol or underlying,
            metrics=None,
            reason=route_reason or REASON_NO_UNDERLYING,
            fetched_at=_now_iso(),
        )

    if options_snapshot_fn is None:
        return BingXOptionsBridgeResult(
            status="unavailable",
            source="none",
            market_type=resolved_market_type,
            underlying_symbol=underlying,
            proxy_symbol=proxy_symbol,
            options_symbol=options_symbol,
            metrics=None,
            reason=REASON_NO_FETCHER,
            fetched_at=_now_iso(),
        )

    source_tag = "index_proxy_options" if proxy_symbol is not None else "underlying_options"

    try:
        snapshot = await options_snapshot_fn(options_symbol, expiry, r)
    except Exception as exc:
        logger.warning(
            "bingx_options_bridge.fetch_failed venue=%s options_symbol=%s error=%s",
            venue_symbol,
            options_symbol,
            str(exc)[:180],
        )
        return BingXOptionsBridgeResult(
            status="unavailable",
            source=source_tag,
            market_type=resolved_market_type,
            underlying_symbol=underlying,
            proxy_symbol=proxy_symbol,
            options_symbol=options_symbol,
            metrics=None,
            reason=REASON_SNAPSHOT_FETCH_FAILED,
            fetched_at=_now_iso(),
        )

    if snapshot is None or _get(snapshot, "ok") is False:
        snapshot_error = _get(snapshot, "error") if snapshot is not None else None
        return BingXOptionsBridgeResult(
            status="unavailable",
            source=source_tag,
            market_type=resolved_market_type,
            underlying_symbol=underlying,
            proxy_symbol=proxy_symbol,
            options_symbol=options_symbol,
            metrics=None,
            chain_quality=_chain_quality_dict(snapshot),
            reason=(str(snapshot_error)[:180] if snapshot_error else REASON_SNAPSHOT_NOT_OK),
            fetched_at=_now_iso(),
        )

    metrics = _extract_metrics(snapshot)
    quality = _compute_quality_score(metrics)
    return BingXOptionsBridgeResult(
        status="available",
        source=source_tag,
        market_type=resolved_market_type,
        underlying_symbol=underlying,
        proxy_symbol=proxy_symbol,
        options_symbol=options_symbol,
        metrics=metrics,
        raw_snapshot=snapshot,
        chain_quality=_chain_quality_dict(snapshot),
        quality_score=quality,
        reason=None,
        fetched_at=_now_iso(),
    )


def _now_iso() -> str:
    return datetime.now(UTC).isoformat()


__all__ = [
    "INDEX_OPTIONS_PROXIES",
    "REASON_MARKET_TYPE_EXCLUDED",
    "REASON_NO_FETCHER",
    "REASON_NO_OPTIONS_FOR_CRYPTO",
    "REASON_NO_PROXY_FOR_INDEX",
    "REASON_NO_UNDERLYING",
    "REASON_SNAPSHOT_FETCH_FAILED",
    "REASON_SNAPSHOT_NOT_OK",
    "BingXOptionsBridgeResult",
    "BingXOptionsMetrics",
    "OptionsSnapshotFn",
    "build_options_bridge",
    "resolve_options_symbol",
]
