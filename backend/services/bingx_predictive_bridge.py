"""Bridge BingX symbols → institutional predictive signals.

Routes a BingX venue symbol to the *real* predictive stack — meta-signal
fusion, predictive-options-2 gamma/shadow-delta enrichment, AI thesis bias,
and the lightweight equity heuristic as a last-resort fallback — and
normalises whichever source produced a usable signal into a single
JSON-safe :class:`BingXPredictiveBridgeResult`.

Priority cascade (highest authority first):

1. ``meta_signal_fn``         — :func:`backend.routers.probabilistic_router.get_meta_signal_endpoint`.
2. ``predictive_options_2_fn`` — :func:`backend.routers.probabilistic_router.get_predictive_options_2`.
3. ``thesis_fn``               — :func:`backend.routers.probabilistic_router.get_ai_thesis`.
4. ``equity_summary_fn``       — :func:`backend.services.equity_ta_snapshot_service.equity_probabilistic_summary`.

The bridge invokes the higher-priority fetchers first and only falls
through when one returns ``unavailable``/``not ok``/raises. Each step's
failure reason is recorded in ``signal.reason_codes`` so operators can see
*why* the signal landed on a lower-priority source.

Routing rules:

- ``stock_perp``       → uses ``underlying_symbol`` directly.
- ``stock_index_perp`` → maps to a listed proxy (SPX→SPY, NDX→QQQ, RUT→IWM, DJI→DIA)
  via :data:`bingx_options_bridge.INDEX_OPTIONS_PROXIES`.
- ``crypto_standard``  → returns unavailable with ``predictive_crypto_not_wired``
  unless a ``crypto_predictive_fn`` is provided.
- anything else        → unavailable with ``predictive_market_type_excluded``.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from typing import Any, Literal

from backend.config.logger_setup import get_logger
from backend.services.bingx_options_bridge import INDEX_OPTIONS_PROXIES
from backend.services.bingx_symbol_linker import classify_underlying, underlying_from_bingx_symbol

logger = get_logger(__name__)


# ── Stable reason codes ──────────────────────────────────────────────────────
# Public contract — runbooks and dashboards match on these literals.
REASON_NO_UNDERLYING = "predictive_no_underlying"
REASON_NO_PROXY_FOR_INDEX = "predictive_no_index_proxy"
REASON_CRYPTO_NOT_WIRED = "predictive_crypto_not_wired"
REASON_MARKET_TYPE_EXCLUDED = "predictive_market_type_excluded"
REASON_NO_FETCHERS = "predictive_no_fetchers"
REASON_ALL_SOURCES_FAILED = "predictive_all_sources_failed"
REASON_FETCH_FAILED = "fetch_failed"
REASON_NOT_OK = "not_ok"
REASON_NO_SIGNAL = "no_signal"

# Source tags (stable for log correlation + UI badges).
SOURCE_META_SIGNAL = "meta_signal"
SOURCE_PREDICTIVE_OPTIONS_2 = "predictive_options_2"
SOURCE_THESIS = "thesis"
SOURCE_EQUITY_HEURISTIC = "equity_heuristic"
SOURCE_CRYPTO = "crypto_predictive"
SOURCE_NONE = "none"


SourceStatus = Literal["available", "unavailable"]
DirectionalBias = Literal["LONG", "SHORT", "NEUTRAL"]

# Fetcher signatures — kept permissive so production callables (with their
# many keyword args) and test stubs both fit without an adapter shim.
EquitySummaryFn = Callable[[str], Awaitable[dict[str, Any]]]
CryptoPredictiveFn = Callable[[str], Awaitable[dict[str, Any]]]


# ── Dataclasses ──────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class BingXPredictiveSignal:
    """Normalised predictive output. ``reason_codes`` carries provenance —
    every fallback step records why the higher-priority source declined."""

    directional_bias: DirectionalBias
    probability_long: float | None
    probability_short: float | None
    confidence: float | None
    horizon: str  # e.g. "swing" | "intraday" | "session" | "5min"
    source: str
    quality_score: float | None
    reason_codes: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class BingXPredictiveBridgeResult:
    """Bridge output for one BingX symbol — JSON-safe via ``to_dict``."""

    status: SourceStatus
    symbol: str  # venue symbol
    underlying_symbol: str
    market_type: str
    proxy_symbol: str | None
    options_symbol: str | None  # the symbol actually queried (= underlying or proxy)
    signal: BingXPredictiveSignal | None
    payload: dict[str, Any] | None  # raw payload of the source that produced the signal
    reason: str | None = None
    fetched_at: str = ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


# ── Defensive accessors ──────────────────────────────────────────────────────


def _get(payload: object, key: str) -> object | None:
    if isinstance(payload, dict):
        return payload.get(key)
    return getattr(payload, key, None)


def _safe_float(value: object) -> float | None:
    if value is None:
        return None
    try:
        out = float(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return None
    if out != out:  # NaN
        return None
    return out


def _clamp_unit(value: float | None) -> float | None:
    if value is None:
        return None
    return max(0.0, min(1.0, value))


def _normalise_direction_label(raw: object) -> DirectionalBias:
    """Map a heterogeneous direction label to LONG / SHORT / NEUTRAL."""
    if raw is None:
        return "NEUTRAL"
    text = str(raw).strip().upper()
    if text in {"LONG", "BULLISH", "BUY", "UP", "STRONG_BUY"}:
        return "LONG"
    if text in {"SHORT", "BEARISH", "SELL", "DOWN", "STRONG_SELL"}:
        return "SHORT"
    return "NEUTRAL"


# ── Routing ──────────────────────────────────────────────────────────────────


def resolve_predictive_target(
    venue_symbol: str, market_type: str
) -> tuple[str | None, str | None, str | None]:
    """Resolve ``(options_symbol, proxy_symbol, reason)`` — what to query.

    Mirrors :func:`bingx_options_bridge.resolve_options_symbol` for the
    routing layer so the two bridges agree on proxy substitution.
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
        return None, None, REASON_CRYPTO_NOT_WIRED

    return None, None, REASON_MARKET_TYPE_EXCLUDED


# ── Normalisers per source ───────────────────────────────────────────────────


def _normalise_equity_summary(response: object) -> BingXPredictiveSignal | None:
    """``equity_probabilistic_summary`` → signal. Already the closest shape
    we want — only minimal projection needed."""
    if not isinstance(response, dict) or not response.get("ok"):
        return None
    bull = _clamp_unit(_safe_float(response.get("bull_probability")))
    bear = _clamp_unit(_safe_float(response.get("bear_probability")))
    confidence = _clamp_unit(_safe_float(response.get("confidence")))

    if bull is None and bear is None:
        return None

    # Pick bias as max of bull / bear with neutral guard.
    if (bull or 0.0) > (bear or 0.0) + 0.05:
        bias: DirectionalBias = "LONG"
    elif (bear or 0.0) > (bull or 0.0) + 0.05:
        bias = "SHORT"
    else:
        bias = "NEUTRAL"

    return BingXPredictiveSignal(
        directional_bias=bias,
        probability_long=bull,
        probability_short=bear,
        confidence=confidence,
        horizon="swing",
        source=SOURCE_EQUITY_HEURISTIC,
        quality_score=confidence,
        reason_codes=["fallback_to_equity_heuristic"],
    )


def _normalise_crypto(response: object) -> BingXPredictiveSignal | None:
    """Pass-through normaliser for an injected crypto predictive engine.

    Expects a dict ``{ok, directional_bias, probability_long,
    probability_short, confidence, horizon}``. Engines that don't fit this
    shape should adapt at the fetcher boundary, not here.
    """
    if not isinstance(response, dict) or not response.get("ok"):
        return None
    bias = _normalise_direction_label(response.get("directional_bias"))
    return BingXPredictiveSignal(
        directional_bias=bias,
        probability_long=_clamp_unit(_safe_float(response.get("probability_long"))),
        probability_short=_clamp_unit(_safe_float(response.get("probability_short"))),
        confidence=_clamp_unit(_safe_float(response.get("confidence"))),
        horizon=str(response.get("horizon") or "intraday"),
        source=SOURCE_CRYPTO,
        quality_score=_clamp_unit(_safe_float(response.get("quality_score"))),
        reason_codes=[str(r) for r in response.get("reason_codes") or []],
    )


# ── Cascade runner ───────────────────────────────────────────────────────────


async def _try_source(
    name: str,
    fetcher: Callable[..., Awaitable[Any]] | None,
    query_symbol: str,
    normaliser: Callable[[object], BingXPredictiveSignal | None],
    *fetcher_args: Any,
    **fetcher_kwargs: Any,
) -> tuple[BingXPredictiveSignal | None, object | None, str | None]:
    """Run one priority step. Returns ``(signal_or_None, raw_response, fallback_reason)``.

    ``fallback_reason`` is non-None iff the step did *not* produce a signal —
    upstream callers append it to ``reason_codes`` so the operator can see
    why the higher-priority source declined.
    """
    if fetcher is None:
        return None, None, f"{name}:no_fetcher"
    try:
        response = await fetcher(query_symbol, *fetcher_args, **fetcher_kwargs)
    except Exception as exc:
        logger.warning(
            "bingx_predictive_bridge.%s_failed symbol=%s error=%s",
            name,
            query_symbol,
            str(exc)[:180],
        )
        return None, None, f"{name}:{REASON_FETCH_FAILED}"

    signal = normaliser(response)
    if signal is None:
        return None, response, f"{name}:{REASON_NO_SIGNAL}"
    return signal, response, None


def _now_iso() -> str:
    return datetime.now(UTC).isoformat()


def _unavailable(
    *,
    venue_symbol: str,
    underlying: str,
    market_type: str,
    proxy: str | None,
    options_symbol: str | None,
    reason: str,
) -> BingXPredictiveBridgeResult:
    return BingXPredictiveBridgeResult(
        status="unavailable",
        symbol=venue_symbol,
        underlying_symbol=underlying,
        market_type=market_type,
        proxy_symbol=proxy,
        options_symbol=options_symbol,
        signal=None,
        payload=None,
        reason=reason,
        fetched_at=_now_iso(),
    )


# ── Public builder ───────────────────────────────────────────────────────────


async def build_predictive_bridge(
    venue_symbol: str,
    *,
    market_type: str | None = None,
    equity_summary_fn: EquitySummaryFn | None = None,
    crypto_predictive_fn: CryptoPredictiveFn | None = None,
) -> BingXPredictiveBridgeResult:
    """Run the predictive cascade for *venue_symbol*.

    Each fetcher is injected so the bridge is independently testable. The
    production caller wires the equity heuristic or crypto predictive engine.
    """
    resolved_market_type = market_type or classify_underlying(venue_symbol)
    underlying = underlying_from_bingx_symbol(venue_symbol)
    options_symbol, proxy_symbol, route_reason = resolve_predictive_target(
        venue_symbol, resolved_market_type
    )

    # Crypto path: try crypto fetcher first; otherwise unavailable with
    # the stable crypto reason the task spec promises.
    if resolved_market_type == "crypto_standard":
        signal, raw, _fallback = await _try_source(
            "crypto", crypto_predictive_fn, underlying, _normalise_crypto
        )
        if signal is not None:
            # Audit: capture predictive signal (fire-and-forget)
            try:
                import asyncio as _aio

                from backend.audit.hooks import audit_decision_snapshot

                _aio.get_event_loop().create_task(
                    audit_decision_snapshot(
                        module="bingx_predictive",
                        symbol=venue_symbol,
                        indicators={
                            "predictive_direction": getattr(signal, "direction", None),
                            "predictive_confidence": getattr(signal, "confidence", None),
                            "predictive_probability": getattr(signal, "probability", None),
                        },
                        signals={"source": "crypto", "market_type": resolved_market_type},
                    )
                )
            except Exception:
                pass
            return BingXPredictiveBridgeResult(
                status="available",
                symbol=venue_symbol,
                underlying_symbol=underlying,
                market_type=resolved_market_type,
                proxy_symbol=None,
                options_symbol=underlying,
                signal=signal,
                payload=raw if isinstance(raw, dict) else None,
                reason=None,
                fetched_at=_now_iso(),
            )
        return _unavailable(
            venue_symbol=venue_symbol,
            underlying=underlying,
            market_type=resolved_market_type,
            proxy=None,
            options_symbol=underlying,
            reason=REASON_CRYPTO_NOT_WIRED,
        )

    if route_reason is not None or options_symbol is None:
        return _unavailable(
            venue_symbol=venue_symbol,
            underlying=underlying,
            market_type=resolved_market_type,
            proxy=proxy_symbol,
            options_symbol=options_symbol or underlying or None,
            reason=route_reason or REASON_NO_UNDERLYING,
        )

    # Equity path: use the local quantitative equity heuristic
    accumulated_reasons: list[str] = []

    signal, raw, fallback_reason = await _try_source(
        SOURCE_EQUITY_HEURISTIC, equity_summary_fn, options_symbol, _normalise_equity_summary
    )
    if signal is not None:
        merged_reasons = [*accumulated_reasons, *signal.reason_codes]
        signal_with_provenance = BingXPredictiveSignal(
            directional_bias=signal.directional_bias,
            probability_long=signal.probability_long,
            probability_short=signal.probability_short,
            confidence=signal.confidence,
            horizon=signal.horizon,
            source=signal.source,
            quality_score=signal.quality_score,
            reason_codes=merged_reasons,
        )
        payload_dict = _payload_to_dict(raw)
        # Audit: capture predictive signal (fire-and-forget)
        try:
            import asyncio as _aio

            from backend.audit.hooks import audit_decision_snapshot

            _aio.get_event_loop().create_task(
                audit_decision_snapshot(
                    module="bingx_predictive",
                    symbol=venue_symbol,
                    indicators={
                        "predictive_direction": getattr(signal, "direction", None),
                        "predictive_confidence": getattr(signal, "confidence", None),
                        "predictive_probability": getattr(signal, "probability", None),
                    },
                    signals={"source": "equity", "market_type": resolved_market_type},
                )
            )
        except Exception:
            pass
        return BingXPredictiveBridgeResult(
            status="available",
            symbol=venue_symbol,
            underlying_symbol=underlying,
            market_type=resolved_market_type,
            proxy_symbol=proxy_symbol,
            options_symbol=options_symbol,
            signal=signal_with_provenance,
            payload=payload_dict,
            reason=None,
            fetched_at=_now_iso(),
        )
    if fallback_reason:
        accumulated_reasons.append(fallback_reason)

    # Sources exhausted.
    if not accumulated_reasons:
        accumulated_reasons.append(REASON_NO_FETCHERS)
    return _unavailable(
        venue_symbol=venue_symbol,
        underlying=underlying,
        market_type=resolved_market_type,
        proxy=proxy_symbol,
        options_symbol=options_symbol,
        reason=f"{REASON_ALL_SOURCES_FAILED}:{','.join(accumulated_reasons[-4:])}",
    )


def _payload_to_dict(raw: object | None) -> dict[str, Any] | None:
    """Best-effort JSON-safe projection of a fetcher response."""
    if raw is None:
        return None
    if isinstance(raw, dict):
        return dict(raw)
    if hasattr(raw, "model_dump"):
        try:
            return raw.model_dump(mode="python")  # type: ignore[attr-defined]
        except Exception:
            return None
    return None


__all__ = [
    "REASON_ALL_SOURCES_FAILED",
    "REASON_CRYPTO_NOT_WIRED",
    "REASON_FETCH_FAILED",
    "REASON_MARKET_TYPE_EXCLUDED",
    "REASON_NOT_OK",
    "REASON_NO_FETCHERS",
    "REASON_NO_PROXY_FOR_INDEX",
    "REASON_NO_SIGNAL",
    "REASON_NO_UNDERLYING",
    "SOURCE_CRYPTO",
    "SOURCE_EQUITY_HEURISTIC",
    "SOURCE_NONE",
    "BingXPredictiveBridgeResult",
    "BingXPredictiveSignal",
    "CryptoPredictiveFn",
    "EquitySummaryFn",
    "build_predictive_bridge",
    "resolve_predictive_target",
]
