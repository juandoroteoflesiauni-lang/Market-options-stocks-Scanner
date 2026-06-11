"""Market Scanner service.

Ranks symbols by deterministic multi-timeframe technical confluence. The service is signal-only:
it never creates orders and it treats missing data as a first-class limitation.
"""

from __future__ import annotations

import asyncio
import math
import os
import time
from collections.abc import Callable, Coroutine
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, ClassVar, Literal, Protocol, cast

import numpy as np

from backend.config.logger_setup import get_logger
from backend.domain.market_scanner_models import (
    VETO_COMPLETE_CONTRADICTION,
    VETO_EXTREME_EXHAUSTION,
    VETO_ILLIQUID,
    VETO_NO_DATA,
    WARN_LOW_RVOL,
    WARN_MODERATE_RSI,
    WARN_TF_DIVERGENCE,
    LeadersCorrelationMatrix,
    MarketScannerFilters,
    MarketScannerPreset,
    MarketScannerRequest,
    MarketScannerResponse,
    MarketScannerRow,
    MarketScannerTimeframeSignal,
    MarketScannerUniverse,
    ScannerBias,
    ScannerCustomization,
    ScannerIndicatorDefinition,
    ScannerSignalLabel,
    ScannerTimeframe,
)
from backend.layer_2_quant_engine.math_core.vpin_proxy import compute_ofi_proxy, compute_vpin_proxy
from backend.quant_engine.math.technical.technical import TechnicalMath
from backend.services.market_scanner_cmf_iv import (
    analyze_cmf_iv_for_scanner,
    attach_cmf_iv_deep_metrics,
)
from backend.services.market_scanner_cmf_iv import (
    indicator_weight_for_timeframe as cmf_iv_weight_for_timeframe,
)
from backend.services.market_scanner_indicator_catalog import (
    CATALOG_VERSION,
    list_indicator_definitions,
)
from backend.services.market_scanner_institutional_scoring import (
    PHASE_A_INDICATOR_KEYS,
    SCORING_SCHEMA_VERSION,
    PhaseAMetricsInput,
    apply_concentration_penalty,
    attach_cross_sectional_scores,
    composite_base_score_from_signals,
    compute_effective_weights,
    compute_effective_weights_with_audit,
    decompose_timeframe_signal,
    institutional_scoring_enabled,
    migrate_customization_scoring_schema,
    module_blend_weight,
    scoring_version_label,
    timeframe_weight_sum,
    weight_concentration_audit,
    weighted_indicator_composite,
)
from backend.services.market_scanner_mfi_flow import (
    analyze_mfi_flow_for_scanner,
    attach_mfi_flow_deep_metrics,
    mark_double_conviction,
)
from backend.services.market_scanner_mfi_flow import (
    indicator_weight_for_timeframe as mfi_flow_weight_for_timeframe,
)
from backend.services.market_scanner_obv_oi import (
    analyze_obv_oi_for_scanner,
    attach_obv_oi_deep_metrics,
)
from backend.services.market_scanner_obv_oi import (
    indicator_weight_for_timeframe as obv_oi_weight_for_timeframe,
)
from backend.services.market_scanner_scoring import (
    assign_grade,
    blend_phase_b_scanner_score,
    build_risk_hints,
    score_confidence_band_68,
    summarize_universe_regime,
)
from backend.services.market_scanner_universes import DEFAULT_UNIVERSES
from backend.services.options_gex_scanner_orchestrator import synthesize_options_gex_signal
from backend.services.probabilistic_scanner_orchestrator import (
    synthesize_probabilistic_signal,
    synthesize_probabilistic_signal_v2,
)
from backend.services.scanner_cache_redis import redis_get_live_price, redis_set_live_price
from backend.services.scanner_funding_gate import (
    REASON_CONFLICTING_MODULES,
    SUITABILITY_INFORMATIONAL_ONLY,
    evaluate_funding_suitability,
    evaluate_module_evidence,
    risk_penalty_from_evidence,
    split_directional_and_risk_scores,
)
from backend.services.scanner_institutional_overlay import (
    attach_institutional_overlay,
    correlation_matrix_from_sparklines,
)
from backend.services.scanner_meta_learner_bridge import try_meta_learner_score_delta
from backend.services.scanner_rl_policy_bridge import get_rl_policy_score, scanner_rl_policy_enabled
from backend.services.scanner_types_coerce import scrub_metrics_dict
from backend.services.technical_scanner_orchestrator import (
    synthesize_technical_signal,
    synthesize_technical_signal_v2,
)

logger = get_logger(__name__)

PhaseBScoreMode = Literal["intraday", "swing", "scanner"]


def _env_float(
    name: str, default: float, *, minimum: float = 0.0, maximum: float | None = None
) -> float:
    raw = os.getenv(name, "").strip()
    if not raw:
        return default
    try:
        value = float(raw)
    except ValueError:
        return default
    if value < minimum:
        return default
    if maximum is not None and value > maximum:
        return default
    return value


def _env_int(name: str, default: int, *, minimum: int = 0, maximum: int | None = None) -> int:
    raw = os.getenv(name, "").strip()
    if not raw:
        return default
    try:
        value = int(raw)
    except ValueError:
        return default
    if value < minimum:
        return default
    if maximum is not None and value > maximum:
        return default
    return value


SCORING_VERSION = scoring_version_label()
SCANNER_PROVIDER_CACHE_TTL_SECONDS = float(
    _env_int("MARKET_SCANNER_PROVIDER_CACHE_TTL_SECONDS", 300, minimum=0, maximum=86_400)
)
SCANNER_SYMBOL_CONCURRENCY = _env_int(
    "MARKET_SCANNER_SYMBOL_CONCURRENCY", 10, minimum=1, maximum=50
)
SCANNER_SYMBOL_CONCURRENCY_DYNAMIC_MAX = _env_int(
    "MARKET_SCANNER_SYMBOL_CONCURRENCY_DYNAMIC_MAX", 50, minimum=1, maximum=100
)
SCANNER_FMP_HTTP_TIMEOUT = _env_float(
    "MARKET_SCANNER_FMP_TIMEOUT_SECONDS", 15.0, minimum=2.0, maximum=120.0
)
SCANNER_OPTIONS_SNAPSHOT_TIMEOUT_SECONDS = 15.0
SCANNER_LIVE_PRICE_TIMEOUT_SECONDS = 3.0
SCANNER_LIVE_PRICE_CACHE_TTL_SECONDS = float(
    _env_int("MARKET_SCANNER_LIVE_PRICE_CACHE_TTL_SECONDS", 3, minimum=0, maximum=300)
)
SCANNER_FMP_BATCH_SIZE = _env_int("MARKET_SCANNER_FMP_BATCH_SIZE", 100, minimum=1, maximum=500)
SCANNER_OPTIONS_CACHE_TTL_SECONDS = _env_float(
    "MARKET_SCANNER_OPTIONS_CACHE_TTL_SECONDS", 900.0, minimum=60.0, maximum=86400.0
)
SCANNER_GENERATED_CANDLE_PRICE_MAX_AGE_SECONDS = 18 * 60 * 60
SCANNER_GENERATED_CANDLE_TIMEFRAMES = ("1s", "1m", "5m", "15m", "30m", "1h", "1d")

# ── Adaptive risk weighting: regime-based weight multipliers ──────────────
# Each regime entry maps indicator keys → multiplication factor applied to the
# indicator's weight in weight_matrix before scoring.  Values >1.0 amplify the
# indicator's contribution for the given regime; values <1.0 attenuate it.
# Keys not present in a regime dict are left unchanged (multiplier = 1.0).
REGIME_WEIGHT_MULTIPLIERS: dict[str, dict[str, float]] = {
    # ── bull quiet ──────────────────────────────────────────────────────────
    # Low vol trending market.  Trend-following indicators get a slight boost;
    # mean-reversion indicators are attenuated.
    "BULL_QUIET": {
        "ema_21_42": 1.25,
        "macd": 1.20,
        "market_structure": 1.15,
        "bbp": 0.70,
        "rsi": 0.85,
        "rsi_hist": 0.80,
        "supertrend": 1.20,
    },
    # ── bear volatile ───────────────────────────────────────────────────────
    # High vol risk-off.  Momentum indicators are de-weighted; vol-protection
    # and mean-reversion signals gain importance.
    "BEAR_VOLATILE": {
        "ema_21_42": 0.60,
        "macd": 0.70,
        "market_structure": 1.15,
        "bbp": 1.30,
        "rsi": 1.20,
        "rsi_hist": 1.25,
        "supertrend": 1.25,
        "prf": 1.20,
        "avwap_vwap": 1.15,
    },
    # ── crisis / chaotic ────────────────────────────────────────────────────
    # Regime switch / tail-risk.  Few indicators are reliable; favour
    # volatility and range-break signals; heavily attenuate trend-following.
    "CRISIS": {
        "ema_21_42": 0.30,
        "macd": 0.40,
        "market_structure": 1.10,
        "bbp": 1.50,
        "rsi": 1.40,
        "rsi_hist": 1.40,
        "supertrend": 0.60,
        "prf": 1.50,
        "avwap_vwap": 1.30,
        "volume": 0.70,
    },
    # ── recovery (bullish transition) ───────────────────────────────────────
    # Exiting crisis, starting to trend up.  Trend picks up, vol begins to
    # decline.  Balanced approach.
    "RECOVERY": {
        "ema_21_42": 1.15,
        "macd": 1.10,
        "market_structure": 1.05,
        "bbp": 0.90,
        "rsi": 1.05,
        "rsi_hist": 1.00,
        "supertrend": 1.10,
        "prf": 0.85,
    },
    # ── transition (mixed / shifting) ────────────────────────────────────────
    # Signals disagree or the regime is shifting.  Stay close to neutral with a
    # mild defensive tilt: trim aggressive trend-following, give vol/structure a
    # small edge.  Normalised so the mean multiplier stays ≈ 1.0.
    "TRANSITION": {
        "ema_21_42": 0.90,
        "macd": 0.90,
        "market_structure": 1.10,
        "bbp": 1.10,
        "rsi": 1.05,
        "supertrend": 0.95,
    },
}
# Normalize flag — when True each regime dict is L1-normalised so the mean
# multiplier across defined indicators ≈ 1.0.
REGIME_WEIGHT_NORMALIZE = True

# ── Adaptive weighting: regime detection via HMM ──────────────────────────
# Representative symbol used to fetch OHLCV for the HMM regime engine.
REGIME_DETECTION_SYMBOL = "SPY"
# Number of daily bars requested for regime detection.
REGIME_DETECTION_DAYS = 90


# Default funding-gate evidence source. Reuses the existing institutional SQLite DB.
SCANNER_FUNDING_GATE_DEFAULT_DB = Path("backend/data/predictions.db")
# Modules supported by the prediction backtest service.
SCANNER_FUNDING_GATE_SUPPORTED_MODULES = {"predictive", "technical", "options_gex"}

_LIVE_PRICE_CACHE: dict[tuple[str, str], tuple[float, ScannerLivePrice]] = {}
_OPTIONS_SNAPSHOT_CACHE: dict[tuple[str, str], tuple[float, object | None]] = {}


def _funding_gate_backtest_engine() -> str:
    engine = os.getenv("MARKET_SCANNER_BACKTEST_ENGINE", "legacy").strip().lower()
    return engine if engine in {"legacy", "vectorbt"} else "legacy"


def conviction_attribution_enabled() -> bool:
    """Check if conviction attribution is enabled (Phase 1: factor drivers + historical percentiles)."""
    raw = os.getenv("SCANNER_CONVICTION_ATTRIBUTION", "true").strip().lower()
    return raw in {"1", "true", "yes", "on"}


def desk_regime_v2_enabled() -> bool:
    """Check if Fase 2 desk-regime detection + edge-regime mapping is enabled.

    When enabled, the scanner unifies HMM + VIX + macro + breadth into a single
    ``desk_regime`` snapshot, makes it the source of truth for ``regime_label``,
    and attaches ``regime_fit_score`` to every row.
    """
    raw = os.getenv("SCANNER_DESK_REGIME_V2", "true").strip().lower()
    return raw in {"1", "true", "yes", "on"}


class ScannerDataProvider(Protocol):
    async def get_bars(self, symbol: str, timeframe: str, max_bars: int) -> dict[str, Any]:
        """Return normalized OHLCV bars for ``symbol`` and ``timeframe``."""


OptionsSnapshotProvider = Callable[[str], Coroutine[Any, Any, object | None]]
LivePriceProvider = Callable[..., Coroutine[Any, Any, "ScannerLivePrice | None"]]


@dataclass(frozen=True)
class ScannerLivePrice:
    """Fresh display price used to override cached OHLCV closes in scanner rows."""

    price: float
    change_pct: float | None = None
    source: str = "live_price"
    timestamp_ms: int | None = None


def _live_price_from_redis_dict(data: dict[str, Any]) -> ScannerLivePrice | None:
    try:
        price = float(data["price"])
        if not math.isfinite(price) or price <= 0:
            return None
        cp_raw = data.get("change_pct")
        cp: float | None = None
        if cp_raw is not None:
            cp = float(cp_raw)
            if not math.isfinite(cp):
                cp = None
        ts_raw = data.get("timestamp_ms")
        ts_ms: int | None = None
        if ts_raw is not None:
            try:
                ts_ms = int(ts_raw)
            except (TypeError, ValueError):
                ts_ms = None
        return ScannerLivePrice(
            price=price,
            change_pct=cp,
            source=str(data.get("source") or "redis"),
            timestamp_ms=ts_ms,
        )
    except (KeyError, TypeError, ValueError):
        return None


def reset_market_scanner_runtime_caches() -> None:
    """Clear short-lived scanner runtime caches used by tests and diagnostics."""
    _LIVE_PRICE_CACHE.clear()
    _OPTIONS_SNAPSHOT_CACHE.clear()
    IntradayScannerDataProvider._cache.clear()
    IntradayScannerDataProvider._inflight.clear()


@dataclass
class InMemoryScannerDataProvider:
    """Deterministic test provider keyed by symbol and timeframe."""

    data: dict[str, dict[str, list[dict[str, Any]]]]

    async def get_bars(self, symbol: str, timeframe: str, max_bars: int) -> dict[str, Any]:
        tf = _provider_timeframe(timeframe)
        bars = self.data.get(symbol.upper(), {}).get(tf) or self.data.get(symbol.upper(), {}).get(
            timeframe
        )
        if bars is None:
            return {"bars": [], "source": "memory", "error": f"No bars for {symbol}/{timeframe}"}
        return {"bars": bars[-max_bars:], "source": "memory", "error": None}


class IntradayScannerDataProvider:
    """Production provider backed by the existing Layer 1 intraday bars fetcher."""

    _cache: ClassVar[dict[tuple[str, str, str, int], tuple[float, dict[str, Any]]]] = {}
    _inflight: ClassVar[dict[tuple[str, str, str, int], asyncio.Task[dict[str, Any]]]] = {}

    def __init__(
        self,
        fetcher: Callable[..., dict[str, Any]] | None = None,
        ttl_seconds: float = SCANNER_PROVIDER_CACHE_TTL_SECONDS,
    ) -> None:
        self._fetcher = fetcher
        self._ttl_seconds = ttl_seconds
        self._fetcher_key = "default" if fetcher is None else f"custom:{id(fetcher)}"

    async def get_bars(self, symbol: str, timeframe: str, max_bars: int) -> dict[str, Any]:
        fetcher = self._fetcher
        if fetcher is None:
            from backend.layer_1_data.datos.intraday_bars_fetcher import fetch_intraday_bars

            fetcher = fetch_intraday_bars

        sym = symbol.upper().strip()
        interval = _provider_timeframe(timeframe)
        cache_key = (self._fetcher_key, sym, interval, max_bars)
        now = time.monotonic()
        cached = self._cache.get(cache_key)
        if cached is not None:
            cached_at, payload = cached
            if now - cached_at <= self._ttl_seconds:
                return payload

        inflight = self._inflight.get(cache_key)
        if inflight is not None:
            return await asyncio.shield(inflight)

        async def _fetch_and_cache() -> dict[str, Any]:
            result = await asyncio.to_thread(
                fetcher,
                sym,
                interval,
                max_bars=max_bars,
                lookback_days=_lookback_days(interval),
                accept_stale_current_session=True,
            )
            payload = (
                result
                if isinstance(result, dict)
                else {"bars": [], "source": "", "error": "bad result"}
            )
            self._cache[cache_key] = (time.monotonic(), payload)
            return payload

        task = asyncio.create_task(_fetch_and_cache())
        self._inflight[cache_key] = task
        try:
            return await asyncio.shield(task)
        finally:
            if self._inflight.get(cache_key) is task:
                self._inflight.pop(cache_key, None)


def list_market_scanner_universes() -> dict[str, MarketScannerUniverse]:
    return {
        key: MarketScannerUniverse(key=key, label=label, symbols=symbols, count=len(symbols))
        for key, (label, symbols) in DEFAULT_UNIVERSES.items()
    }


def list_market_scanner_presets() -> list[MarketScannerPreset]:
    return [
        MarketScannerPreset(
            key="institutional_lax",
            label="Institutional Lax v3.1",
            description="Configuración institucional flexible. Target pass rate 25–40%. Umbrales recalibrados, WATCH/C habilitado, solo 4 hard vetoes.",
            request=MarketScannerRequest(
                universe="wall_street",
                direction="both",
                max_rows=80,
                include_deep_metrics=True,
                filters=MarketScannerFilters(
                    min_price=1.0,
                    min_volume=250_000.0,
                    min_relative_volume=0.5,
                    min_score=35.0,
                    allow_reversal=True,
                    include_vetoed=False,
                ),
                customization=ScannerCustomization(
                    enabled_modules=[
                        "technical",
                        "probabilistic",
                        "options_gex",
                        "fundamentals",
                        "macro_micro",
                    ],
                    module_synthesis_limit=12,
                    weight_matrix={
                        "rsi_hist": {"5m": 0.0, "15m": 0.8, "1h": 0.8, "1D": 0.8},
                        "supertrend": {"5m": 1.0, "15m": 1.2, "1h": 1.2, "1D": 1.2},
                        "bbp": {"5m": 0.8, "15m": 0.8, "1h": 1.1, "1D": 1.1},
                        "obv_oi": {"5m": 2.2, "15m": 2.0, "1h": 0.5, "1D": 0.0},
                        "mfi_flow": {"5m": 2.0, "15m": 1.8, "1h": 0.6, "1D": 0.0},
                        "cmf_iv": {"5m": 1.8, "15m": 2.0, "1h": 0.8, "1D": 0.0},
                        "vix": {"1D": 0.0},
                        "gamma_flip": {"1D": 0.0},
                        "iv_vol_term": {"1D": 0.0},
                    },
                ),
            ),
        ),
        MarketScannerPreset(
            key="momentum",
            label="Momentum continuation",
            description="Ranks liquid symbols with aligned intraday and daily momentum.",
            request=MarketScannerRequest(universe="magnificas", direction="long"),
        ),
        MarketScannerPreset(
            key="watchlist",
            label="Broad watchlist",
            description="Shows a broader candidate set while retaining hard data-quality vetoes.",
            request=MarketScannerRequest(
                universe="wall_street",
                max_rows=75,
                direction="both",
                filters=MarketScannerFilters(
                    min_price=1.0,
                    min_volume=250_000.0,
                    min_relative_volume=0.5,
                    min_score=35.0,
                    allow_reversal=True,
                    include_vetoed=False,
                ),
                customization=ScannerCustomization(module_synthesis_limit=10),
            ),
        ),
        MarketScannerPreset(
            key="reversal",
            label="Reversal watch",
            description="Allows daily/intraday disagreement for reversal research.",
            request=MarketScannerRequest(
                universe="general",
                direction="both",
                filters=MarketScannerFilters(
                    min_price=1.0,
                    min_volume=250_000.0,
                    min_relative_volume=0.5,
                    min_score=30.0,
                    allow_reversal=True,
                    include_vetoed=False,
                ),
            ),
        ),
    ]


def list_market_scanner_indicators() -> list[ScannerIndicatorDefinition]:
    return list_indicator_definitions()


async def _get_global_vix() -> float | None:
    """Fetch the current ^VIX level via FMP for broad volatility adjustment."""
    try:
        from backend.layer_1_data.fetchers.fmp_client import FMPClient

        fmp = FMPClient(timeout=8.0)
        quote = await fmp.get_quote("^VIX")
        px = getattr(quote, "price", None) if quote is not None else None
        if isinstance(px, int | float):
            return float(px)
    except Exception as exc:
        logger.warning("market_scanner.global_vix_fetch_failed error=%s", str(exc)[:120])
    return None


# ── Adaptive risk weighting: regime detection & weight adjustment ────────


def _normalise_regime_multipliers(
    raw: dict[str, float],
) -> dict[str, float]:
    """L1-normalise a regime multiplier dict so the average multiplier ≈ 1.0.

    This prevents regime switching from systematically inflating or deflating
    the aggregate score across all symbols.
    """
    values = list(raw.values())
    mean_mul = sum(values) / len(values) if values else 1.0
    if abs(mean_mul - 1.0) < 1e-6:
        return raw
    scale = 1.0 / mean_mul
    return {k: round(v * scale, 4) for k, v in raw.items()}


def _lookup_regime_multipliers(
    regime_label: str,
) -> dict[str, float] | None:
    """Return the (optionally normalised) multiplier dict for a regime label.

    Returns None when the regime is not recognised (weights unchanged).
    """
    raw = REGIME_WEIGHT_MULTIPLIERS.get(regime_label)
    if raw is None:
        return None
    if REGIME_WEIGHT_NORMALIZE:
        return _normalise_regime_multipliers(raw)
    return raw


def _detect_current_regime(
    spy_bars: list[dict[str, Any]],
) -> str | None:
    """Run the HMM regime engine on SPY daily bars to determine the current macro regime.

    Returns a regime label string (e.g. ``"BULL_QUIET"``) or None when detection
    fails (insufficient data, engine error).
    """
    if len(spy_bars) < 60:
        logger.warning(
            "adaptive_weighting regime_detection insufficient_bars got=%d", len(spy_bars)
        )
        return None

    try:
        from backend.services.scanner_market_regime_detector import analyze_spy_regime

        hmm_block = analyze_spy_regime(spy_bars)
        if not hmm_block:
            logger.warning("adaptive_weighting regime_detection hmm_not_ok")
            return None

        current = hmm_block.get("label") or hmm_block.get("signal")
        if not current:
            return None

        # Map HMM output to our regime labels
        label = str(current).upper().strip()

        # Canonicalise known labels
        label_map: dict[str, str] = {
            "BULLISH": "BULL_QUIET",
            "BEARISH": "BEAR_VOLATILE",
            "NEUTRAL": "BULL_QUIET",
            "CHAOTIC": "CRISIS",
            "BEAR_VOLATILE": "BEAR_VOLATILE",
            "BULL_QUIET": "BULL_QUIET",
            "CRISIS": "CRISIS",
            "RECOVERY": "RECOVERY",
        }
        canonical = label_map.get(label)
        if canonical:
            return canonical

        logger.info(
            "adaptive_weighting regime_detection unknown_label raw=%s canonicalised=%s",
            current,
            label,
        )
        return label  # pass through even if not in map
    except Exception as exc:
        logger.warning("adaptive_weighting regime_detection failed error=%s", str(exc)[:200])
        return None


def _apply_regime_weight_multipliers(
    base_weights: dict[str, dict[str, float]],
    regime_multipliers: dict[str, float],
) -> dict[str, dict[str, float]]:
    """Apply per-indicator multipliers to a weight matrix (in place, returns same dict).

    For each indicator key present in *both* the weight matrix and the multipliers
    dict, every timeframe entry for that indicator is multiplied by the factor.
    Indicators not present in multipliers (or vice versa) are left untouched.
    """
    for indicator_key, factor in regime_multipliers.items():
        if indicator_key in base_weights:
            tf_map = base_weights[indicator_key]
            for tf in list(tf_map.keys()):
                tf_map[tf] = round(tf_map[tf] * factor, 4)
    return base_weights


# ── Core signal computation ──────────────────────────────────────────────


def compute_timeframe_signal(
    symbol: str,
    timeframe: str,
    bars: list[dict[str, Any]],
    vix_val: float | None = None,
    weights: dict[str, float] | None = None,
) -> MarketScannerTimeframeSignal:
    """Compute the fast deterministic signal for a single symbol/timeframe.

    When ``weights`` is provided, indicators with a zero weight for the current
    timeframe are skipped entirely to conserve CPU.  ``None`` means "compute
    everything" (backward-compatible default).
    """
    tf = _public_timeframe(timeframe)
    clean = [_coerce_bar(row) for row in bars]
    rows = [row for row in clean if row is not None]
    warnings: list[str] = []
    vetoes: list[str] = []
    reasons: list[str] = []

    if len(rows) < 60:
        return MarketScannerTimeframeSignal(
            timeframe=tf,
            ok=False,
            direction="unavailable",
            label="neutral",
            score=0.0,
            confidence=0.0,
            warnings=[f"Insufficient bars: {len(rows)}"],
            vetoes=[VETO_NO_DATA],
        )

    close = np.asarray([row["close"] for row in rows], dtype=np.float64)
    high = np.asarray([row["high"] for row in rows], dtype=np.float64)
    low = np.asarray([row["low"] for row in rows], dtype=np.float64)
    volume = np.asarray([row["volume"] for row in rows], dtype=np.float64)
    vpin_bundle = compute_vpin_proxy(close, high, low, volume)
    ofi_p = compute_ofi_proxy(close, volume)
    price = float(close[-1])
    previous = float(close[-2])
    first = float(close[0])
    change_pct = _pct(price, previous)
    period_change_pct = _pct(price, first)

    ema7 = _last(TechnicalMath.ema(close, 7))
    ema14 = _last(TechnicalMath.ema(close, 14))
    ema21 = _last(TechnicalMath.ema(close, 21))
    ema42 = _last(TechnicalMath.ema(close, 42))
    ema100 = _last(TechnicalMath.ema(close, 100))
    ema200 = _last(TechnicalMath.ema(close, 200))
    macd_line, signal_line, hist = TechnicalMath.macd(close)
    rsi = _last(TechnicalMath.rsi(close, 14))
    rsi_hist: float | None = None
    if weights is None or weights.get("rsi_hist", 1.0) > 0:
        rsi_hist_arr = TechnicalMath.rsi_hist(close, 14)
        rsi_hist = _last(rsi_hist_arr)
    atr = _last(TechnicalMath.atr(close, high, low, 14))
    vwap = _last(TechnicalMath.vwap(high, low, close, volume))

    supertrend_dir: float | None = None
    if weights is None or weights.get("supertrend", 1.0) > 0:
        st_line, st_dir = TechnicalMath.supertrend(close, high, low, 10, 3.0)
        supertrend_dir = _last(st_dir)
    bbp_val: float | None = None
    if weights is None or weights.get("bbp", 1.0) > 0:
        bbp_arr = TechnicalMath.bbp(close, 20, 2.0)
        bbp_val = _last(bbp_arr)
    avg_vol20 = (
        float(np.nanmean(volume[-21:-1])) if len(volume) >= 21 else float(np.nanmean(volume))
    )
    adv_window = min(20, len(volume))
    adv_estimate = float(np.mean(volume[-adv_window:])) if adv_window > 0 else float(volume[-1])
    relative_volume = float(volume[-1] / avg_vol20) if avg_vol20 > 0 else 0.0
    atr_pct = float(atr / price * 100.0) if price > 0 and math.isfinite(atr) else 0.0

    macd_hist = _last(hist)
    macd_value = _last(macd_line)
    macd_signal = _last(signal_line)

    indicator_scores: dict[str, float] = {}
    if institutional_scoring_enabled():
        metrics_input = PhaseAMetricsInput(
            ema7=ema7 if _finite(ema7) else None,
            ema14=ema14 if _finite(ema14) else None,
            ema21=ema21 if _finite(ema21) else None,
            ema42=ema42 if _finite(ema42) else None,
            ema100=ema100 if _finite(ema100) else None,
            ema200=ema200 if _finite(ema200) else None,
            macd_hist=macd_hist if _finite(macd_hist) else None,
            rsi=rsi if _finite(rsi) else None,
            rsi_hist=rsi_hist if _finite(rsi_hist) else None,
            price=price,
            vwap=vwap if _finite(vwap) else None,
            relative_volume=relative_volume,
            period_change_pct=period_change_pct,
            supertrend_dir=supertrend_dir if _finite(supertrend_dir) else None,
            bbp=bbp_val if _finite(bbp_val) else None,
            atr_pct=atr_pct,
            vix=vix_val if _finite(vix_val) else None,
        )
        phase_a_weights: dict[str, float] | None = None
        if weights:
            phase_a_weights = {
                k: float(v)
                for k, v in weights.items()
                if k in PHASE_A_INDICATOR_KEYS and float(v) > 0
            }
            if not phase_a_weights:
                phase_a_weights = None
        indicator_scores, reasons, warnings, bullish_votes, bearish_votes = (
            decompose_timeframe_signal(metrics_input, weights=phase_a_weights)
        )
        composite_weights: dict[str, float] | None = None
        if phase_a_weights:
            composite_weights = {
                k: phase_a_weights[k] for k in indicator_scores if k in phase_a_weights
            }
            if not composite_weights:
                composite_weights = None
        score = weighted_indicator_composite(indicator_scores, composite_weights)
    else:
        bullish_votes = 0
        bearish_votes = 0
        score = 50.0

        for fast, slow, label in (
            (ema7, ema14, "EMA 7/14"),
            (ema21, ema42, "EMA 21/42"),
            (ema100, ema200, "EMA 100/200"),
        ):
            if _finite(fast) and _finite(slow):
                if fast > slow:
                    bullish_votes += 1
                    score += 7.0
                    reasons.append(f"{label} bullish")
                elif fast < slow:
                    bearish_votes += 1
                    score -= 7.0

        if _finite(macd_hist):
            if macd_hist > 0:
                bullish_votes += 1
                score += 8.0
                reasons.append("MACD bull")
            elif macd_hist < 0:
                bearish_votes += 1
                score -= 8.0

        if _finite(rsi):
            if 50 <= rsi <= 72:
                score += 8.0
                reasons.append("RSI momentum zone")
            elif rsi > 82:
                score -= 8.0
                warnings.append(WARN_MODERATE_RSI)
            elif rsi < 35:
                score -= 7.0

            if _finite(rsi_hist):
                if rsi_hist > 2.0:
                    score += 5.0
                    reasons.append("RSI momentum bullish")
                elif rsi_hist < -2.0:
                    score -= 5.0
                    reasons.append("RSI momentum bearish")

        if _finite(vwap):
            if price > vwap:
                bullish_votes += 1
                score += 7.0
                reasons.append("Price above VWAP")
            elif price < vwap:
                bearish_votes += 1
                score -= 7.0

        if relative_volume >= 1.5:
            score += 8.0
            reasons.append("Relative volume expansion")
        elif relative_volume < 0.2:
            score -= 5.0
            warnings.append(WARN_LOW_RVOL)

        if period_change_pct > 2.0:
            score += 6.0
            reasons.append("Positive period momentum")
        elif period_change_pct < -2.0:
            score -= 6.0
            reasons.append("Negative period momentum")

        if _finite(supertrend_dir):
            if supertrend_dir > 0:
                bullish_votes += 1
                score += 5.0
                reasons.append("SuperTrend bull")
            elif supertrend_dir < 0:
                bearish_votes += 1
                score -= 5.0

        if _finite(bbp_val):
            if bbp_val > 0.8:
                score += 3.0
                reasons.append("BBP strong upper")
            elif bbp_val < 0.2:
                score -= 3.0

        if atr_pct < 0.05:
            warnings.append("atr_too_compressed")
            score -= 10.0
        elif atr_pct > 12.0:
            warnings.append("atr_too_extended")
            score -= 10.0
        if 0.2 <= atr_pct <= 5.0:
            score += 4.0

        if _finite(vix_val):
            if vix_val > 30:
                score -= 10.0
                reasons.append(f"High broad volatility (VIX={vix_val:.1f})")
            elif vix_val < 15:
                score += 5.0
                reasons.append(f"Low broad volatility (VIX={vix_val:.1f})")

        score = max(0.0, min(100.0, score))

    direction: ScannerBias
    if bullish_votes > bearish_votes:
        direction = "bullish"
    elif bearish_votes > bullish_votes:
        direction = "bearish"
    else:
        direction = "neutral"

    label = _label_for_score(score, direction)
    confidence = min(1.0, max(0.05, abs(score - 50.0) / 50.0))

    contributions: dict[str, float] = {}
    if institutional_scoring_enabled() and indicator_scores:
        for key, ind_score in indicator_scores.items():
            w = 1.0 if weights is None else float(weights.get(key, 0.0))
            if w > 0:
                contributions[key] = round(ind_score * w, 4)

    return MarketScannerTimeframeSignal(
        timeframe=tf,
        ok=True,
        direction=direction,
        label=label,
        score=round(score, 2),
        confidence=round(confidence, 3),
        contributions=contributions,
        indicator_scores={k: round(v, 2) for k, v in indicator_scores.items()},
        metrics=scrub_metrics_dict(
            {
                "price": round(price, 4),
                "change_pct": round(change_pct, 4),
                "period_change_pct": round(period_change_pct, 4),
                "ema_7": round(ema7, 4) if _finite(ema7) else None,
                "ema_14": round(ema14, 4) if _finite(ema14) else None,
                "ema_21": round(ema21, 4) if _finite(ema21) else None,
                "ema_42": round(ema42, 4) if _finite(ema42) else None,
                "ema_100": round(ema100, 4) if _finite(ema100) else None,
                "ema_200": round(ema200, 4) if _finite(ema200) else None,
                "macd": round(macd_value, 4) if _finite(macd_value) else None,
                "macd_signal": round(macd_signal, 4) if _finite(macd_signal) else None,
                "macd_hist": round(macd_hist, 4) if _finite(macd_hist) else None,
                "rsi": round(rsi, 2) if _finite(rsi) else None,
                "rsi_hist": round(rsi_hist, 2) if _finite(rsi_hist) else None,
                "atr": round(atr, 4) if _finite(atr) else None,
                "atr_pct": round(atr_pct, 4),
                "vwap": round(vwap, 4) if _finite(vwap) else None,
                "supertrend_dir": round(supertrend_dir, 4) if _finite(supertrend_dir) else None,
                "bbp": round(bbp_val, 4) if _finite(bbp_val) else None,
                "relative_volume": round(relative_volume, 4),
                "volume_avg": round(avg_vol20, 2),
                "adv": round(adv_estimate, 2),
                "vix": round(vix_val, 2) if _finite(vix_val) else None,
                "volume": round(float(volume[-1]), 2),
                "vpin_proxy": vpin_bundle.get("vpin_proxy"),
                "volume_imbalance": vpin_bundle.get("volume_imbalance"),
                "ofi_proxy": ofi_p,
            }
        ),
        reasons=reasons[:6],
        warnings=warnings,
        vetoes=vetoes,
    )


class MarketScannerService:
    def __init__(
        self,
        data_provider: ScannerDataProvider | None = None,
        options_snapshot_provider: OptionsSnapshotProvider | None = None,
        live_price_provider: LivePriceProvider | None = None,
        funding_gate_db_path: Path | str | None = None,
        funding_gate_backtest_runner: Callable[..., dict[str, Any]] | None = None,
        funding_gate_batch_runner: Callable[..., dict[str, Any]] | None = None,
    ) -> None:
        self._data_provider = data_provider or IntradayScannerDataProvider()
        self._options_snapshot_provider = (
            options_snapshot_provider or _fetch_options_snapshot_for_scanner
        )
        self._live_price_provider = live_price_provider or _fetch_live_price_for_scanner
        # Funding-gate evidence source. None disables the gate entirely (tests / warmup).
        if funding_gate_db_path is None:
            self._funding_gate_db_path: Path | None = SCANNER_FUNDING_GATE_DEFAULT_DB
        else:
            self._funding_gate_db_path = Path(funding_gate_db_path)
        # Injectable so tests can stub the backtest runner deterministically.
        # ``funding_gate_backtest_runner`` retains the per-(symbol, module) contract used
        # by existing tests and as a fallback when the batch path fails. The batch
        # runner is preferred when both are absent so we make a single SQL pass per scan.
        self._funding_gate_runner = funding_gate_backtest_runner
        self._funding_gate_batch_runner = funding_gate_batch_runner

    async def scan(self, request: MarketScannerRequest) -> MarketScannerResponse:
        started = time.monotonic()
        request = request.model_copy(
            update={
                "customization": migrate_customization_scoring_schema(request.customization),
            }
        )
        symbols = _symbols_for_request(request)
        skipped: dict[str, str] = {}
        rows: list[MarketScannerRow] = []
        if not symbols:
            return MarketScannerResponse(
                universe=request.universe,
                rows=rows,
                skipped_symbols=skipped,
                data_quality={
                    "requested_symbols": 0,
                    "returned_symbols": 0,
                    "skipped_symbols": 0,
                    "timeframes": 0,
                },
                feature_freshness={},
                cost_estimate={},
            )
        concurrency = max(
            SCANNER_SYMBOL_CONCURRENCY,
            min(SCANNER_SYMBOL_CONCURRENCY_DYNAMIC_MAX, len(symbols) // 4),
        )
        semaphore = asyncio.Semaphore(concurrency)

        # Fetch global VIX once — all symbols share the same broad volatility read.
        vix_val = await _get_global_vix()

        # ── Adaptive risk weighting ──────────────────────────────────────
        # When enabled: detect the macro regime once, apply per-indicator
        # multipliers to the weight matrix, and inject regime info into every row.
        regime_label: str | None = None
        regime_weight_multipliers: dict[str, float] | None = None
        # Fase 2: HMM block captured here (when bars fetched) for desk-regime unification.
        spy_hmm: dict[str, Any] | None = None
        if request.customization.adaptive_weighting:
            try:
                spy_bars_resp = await self._data_provider.get_bars(
                    REGIME_DETECTION_SYMBOL, "1D", max_bars=REGIME_DETECTION_DAYS
                )
                spy_bars: list[dict[str, Any]] = (
                    spy_bars_resp.get("bars")
                    if isinstance(spy_bars_resp, dict)
                    else spy_bars_resp if isinstance(spy_bars_resp, list) else []
                )
                if desk_regime_v2_enabled():
                    from backend.services.scanner_market_regime_detector import analyze_spy_regime

                    spy_hmm = analyze_spy_regime(spy_bars)
                detected = (
                    spy_hmm.get("label") if isinstance(spy_hmm, dict) else None
                ) or _detect_current_regime(spy_bars)
                if detected:
                    regime_label = detected
                    regime_weight_multipliers = _lookup_regime_multipliers(detected)
                    if regime_weight_multipliers:
                        # Deep-copy the weight matrix before mutation so the original input is untouched.
                        adjusted = {
                            k: dict(v) for k, v in request.customization.weight_matrix.items()
                        }
                        _apply_regime_weight_multipliers(adjusted, regime_weight_multipliers)
                        request.customization.weight_matrix = adjusted
                        logger.info(
                            "adaptive_weighting applied regime=%s indicators=%s",
                            regime_label,
                            ", ".join(sorted(regime_weight_multipliers)),
                        )
                    else:
                        logger.info("adaptive_weighting regime=%s no_multipliers_found", detected)
                else:
                    logger.warning("adaptive_weighting regime_detection returned_None")
            except Exception as exc:
                logger.warning("adaptive_weighting setup_failed error=%s", str(exc)[:200])

        # Phase A bars cache: avoids re-fetching OHLCV in Phase B for top-N symbols.
        bars_cache: dict[tuple[str, str], list[dict[str, Any]]] = {}

        async def _scan_symbol(symbol: str) -> MarketScannerRow | None:
            async with semaphore:
                try:
                    return await self._scan_symbol(
                        symbol,
                        request,
                        skipped,
                        vix_val=vix_val,
                        bars_cache=bars_cache,
                    )
                except Exception as exc:
                    logger.warning(
                        "market_scanner.symbol_crashed symbol=%s error=%s",
                        symbol,
                        exc,
                    )
                    skipped[symbol] = f"internal_error: {exc}"
                    return None

        results = await asyncio.gather(
            *(_scan_symbol(symbol) for symbol in symbols),
            return_exceptions=True,
        )
        rows = [row for row in results if isinstance(row, MarketScannerRow)]
        # Stamp regime info on every row (only populated when adaptive_weighting is active).
        if regime_label is not None:
            for row in rows:
                row.regime_label = regime_label
                row.regime_weight_multipliers = regime_weight_multipliers
        live_price_rows = await _apply_live_prices(rows, self._live_price_provider)
        rows = _filter_rows(rows, request)
        rows = _sort_rows(rows, request)
        phase_b_limit = min(request.customization.module_synthesis_limit, 100, len(rows))
        indicators_catalog = list_market_scanner_indicators()
        effective_weight_matrix: dict[str, dict[str, float]] | None = None
        data_tier_audit: dict[str, Any] | None = None
        if institutional_scoring_enabled():
            effective_weight_matrix, data_tier_audit = compute_effective_weights_with_audit(
                request.customization,
                indicators_catalog,
                regime_multipliers=regime_weight_multipliers,
            )

        options_provider = self._options_snapshot_provider
        from backend.services.scanner_real_data_service import (
            fetch_options_snapshot_routed,
            scanner_real_data_enabled,
        )

        if scanner_real_data_enabled():

            async def _routed_options_provider(sym: str) -> object | None:
                return await fetch_options_snapshot_routed(sym)

            options_provider = _routed_options_provider

        phase_b_out = await _apply_phase_b_module_synthesis(
            rows,
            request,
            options_provider,
            data_provider=self._data_provider,
            bars_cache=bars_cache,
            effective_weights=effective_weight_matrix,
            indicators=indicators_catalog,
        )
        macro_ctx_used: dict[str, Any] | None = None
        phase_b_score_mode: PhaseBScoreMode = _phase_b_score_mode(
            request.customization.primary_timeframe
        )
        phase_b_min_relevance = float(request.filters.min_score)
        if isinstance(phase_b_out, dict):
            macro_raw = phase_b_out.get("macro_context")
            macro_ctx_used = macro_raw if isinstance(macro_raw, dict) else None
            phase_b_score_mode = cast(
                PhaseBScoreMode,
                phase_b_out.get("phase_b_score_mode", phase_b_score_mode),
            )
            phase_b_min_relevance = float(
                phase_b_out.get("phase_b_min_relevance_score", phase_b_min_relevance)
            )
            if phase_b_out.get("effective_weight_matrix"):
                effective_weight_matrix = phase_b_out["effective_weight_matrix"]
            if isinstance(phase_b_out.get("real_data"), dict):
                tier_recalc = phase_b_out["real_data"].get("tier_recalc")
                if isinstance(tier_recalc, dict):
                    data_tier_audit = tier_recalc
        factor_crowding: list[Any] = []
        # Conviction attribution: factor drivers + historical percentiles (Phase 1).
        if conviction_attribution_enabled() and rows:
            from backend.services.scanner_factor_attribution import attach_conviction_to_rows
            from backend.services.scanner_factor_history_store import append_snapshots_batch

            attach_conviction_to_rows(rows)
            factor_loadings_by_symbol = {
                r.symbol: (
                    r.conviction_breakdown.factor_contributions if r.conviction_breakdown else {}
                )
                for r in rows
            }
            append_snapshots_batch(rows, factor_loadings_by_symbol=factor_loadings_by_symbol)
        # Fase 3: factor crowding + capacity (full universe, before max_rows trim).
        if rows:
            try:
                import uuid

                from backend.services.scanner_capacity_signals import (
                    attach_capacity_signals,
                    capacity_enabled,
                    enrich_capacity_with_production_hint,
                )
                from backend.services.scanner_factor_crowding import (
                    apply_crowding_penalty_to_rows,
                    compute_universe_factor_crowding,
                    crowding_enabled,
                    persist_crowding_snapshots,
                )

                scan_id_f3 = uuid.uuid4().hex
                if crowding_enabled():
                    factor_crowding = compute_universe_factor_crowding(rows)
                    apply_crowding_penalty_to_rows(rows, factor_crowding)
                    persist_crowding_snapshots(scan_id_f3, factor_crowding, rows)
                if capacity_enabled():
                    attach_capacity_signals(rows)
            except Exception as exc:
                logger.warning("fase3_crowding_capacity.failed error=%s", str(exc)[:200])
        # Funding-gate enrichment: never mutates score or order — only adds evidence.
        await _apply_funding_gate(
            rows,
            request,
            db_path=self._funding_gate_db_path,
            backtest_runner=self._funding_gate_runner,
            batch_runner=self._funding_gate_batch_runner,
        )
        _apply_rl_policy_evidence(rows)
        universe_stats: dict[str, float] | None = None
        if institutional_scoring_enabled() and rows:
            universe_stats = attach_cross_sectional_scores(rows)
            rows = _sort_rows(rows, request)
        if data_tier_audit or (isinstance(phase_b_out, dict) and phase_b_out.get("real_data")):
            real_block = phase_b_out.get("real_data") if isinstance(phase_b_out, dict) else None
            for row in rows:
                audit = dict(row.score_audit or {})
                if data_tier_audit:
                    audit["data_tier_policy"] = data_tier_audit
                if real_block:
                    audit["real_data_policy"] = real_block
                if row.source_attribution:
                    audit["source_attribution"] = row.source_attribution
                row.score_audit = audit
        rows = rows[: request.max_rows]
        universe_regime_summary = summarize_universe_regime(rows)
        # ── Fase 2: desk regime + edge-regime mapping ─────────────────────
        # Unify HMM (captured pre-scan) + VIX + macro + universe breadth into a
        # single desk regime, make it the source of truth for regime_label, and
        # attach a contribution-weighted regime_fit_score to every row. Outcomes
        # are persisted (snapshot price now; forward return closed next scan).
        desk_regime = None
        if desk_regime_v2_enabled() and rows:
            try:
                import uuid

                from backend.services.scanner_market_regime_detector import detect_desk_regime
                from backend.services.scanner_regime_fit import (
                    attach_regime_fit,
                    build_regime_factor_rows,
                )

                desk_regime = detect_desk_regime(
                    spy_hmm=spy_hmm,
                    vix=vix_val,
                    macro_context=macro_ctx_used,
                    universe_summary=universe_regime_summary,
                )
                for row in rows:
                    row.regime_label = desk_regime.label
                attach_regime_fit(rows, desk_regime)
                if conviction_attribution_enabled():
                    from backend.services.scanner_factor_regime_performance import (
                        persist_regime_outcomes,
                    )

                    persist_regime_outcomes(
                        scan_id=uuid.uuid4().hex,
                        desk_regime=desk_regime.label,
                        factor_rows=build_regime_factor_rows(rows),
                    )
                rows = _sort_rows(rows, request)
            except Exception as exc:
                logger.warning("desk_regime_v2.failed error=%s", str(exc)[:200])
        portfolio_risk_stack = None
        barra_risk_model = None
        risk_model_version = None
        if rows:
            from backend.services.scanner_risk_stack import run_scanner_risk_stack

            portfolio_risk_stack = run_scanner_risk_stack(
                rows,
                universe_regime=universe_regime_summary,
            )
            if portfolio_risk_stack and portfolio_risk_stack.barra_risk_model:
                barra_risk_model = portfolio_risk_stack.barra_risk_model
                risk_model_version = barra_risk_model.schema_version
            try:
                from backend.services.scanner_capacity_signals import (
                    capacity_enabled,
                    enrich_capacity_with_production_hint,
                )

                if capacity_enabled():
                    enrich_capacity_with_production_hint(rows)
            except Exception as exc:
                logger.warning("fase3_capacity_hint.failed error=%s", str(exc)[:200])
        elapsed_ms = round((time.monotonic() - started) * 1000.0, 2)
        lc_raw = correlation_matrix_from_sparklines(rows)
        leaders_corr = LeadersCorrelationMatrix(**lc_raw) if lc_raw else None

        phase_b_with_signals = sum(1 for row in rows if row.module_signals)
        fmp_available: bool | None = None
        if any(
            m in (request.customization.enabled_modules or [])
            or request.customization.enabled_modules is None
            for m in ("fundamentals", "macro_micro")
        ):
            try:
                from backend.layer_1_data.fetchers.fmp_client import fmp_client_configured

                fmp_available = fmp_client_configured()
            except Exception:
                fmp_available = False
        result = MarketScannerResponse(
            universe=request.universe,
            rows=rows,
            skipped_symbols=skipped,
            data_quality={
                "requested_symbols": len(symbols),
                "returned_symbols": len(rows),
                "skipped_symbols": len(skipped),
                "timeframes": len(request.timeframes),
                "live_price_rows": live_price_rows,
                "module_synthesis_limit": min(
                    request.customization.module_synthesis_limit,
                    len(rows),
                ),
                "phase_b_rows_with_signals": phase_b_with_signals,
                "phase_b_score_mode": phase_b_score_mode,
                "phase_b_min_relevance_score": phase_b_min_relevance,
                "phase_b_limit_applied": min(
                    max(int(request.customization.module_synthesis_limit), 1) if rows else 0,
                    100,
                    len(rows),
                ),
                "elapsed_ms": elapsed_ms,
                **({"data_tier_policy": data_tier_audit} if data_tier_audit else {}),
                **({"fmp_client_configured": fmp_available} if fmp_available is not None else {}),
                **(
                    {"real_data": phase_b_out.get("real_data")}
                    if isinstance(phase_b_out, dict) and phase_b_out.get("real_data")
                    else {}
                ),
                **({"risk_model_version": risk_model_version} if risk_model_version else {}),
            },
            scoring_version=SCORING_VERSION,
            scoring_schema_version=(
                SCORING_SCHEMA_VERSION if institutional_scoring_enabled() else None
            ),
            effective_weight_matrix=effective_weight_matrix,
            regime_multipliers_applied=regime_weight_multipliers,
            universe_stats=universe_stats,
            catalog_version=CATALOG_VERSION,
            feature_freshness=_feature_freshness(request),
            cost_estimate=_estimate_scanner_cost(
                request,
                symbols=symbols,
                rows_after_filter=len(rows),
                phase_b_limit=phase_b_limit,
                live_price_rows=live_price_rows,
            ),
            leaders_correlation=leaders_corr,
            universe_regime_summary=universe_regime_summary,
            portfolio_risk_stack=portfolio_risk_stack,
            barra_risk_model=barra_risk_model,
            risk_model_version=risk_model_version,
            macro_context=macro_ctx_used,
            desk_regime=desk_regime,
            factor_crowding=factor_crowding,
        )
        if request.webhook_url:
            from backend.services.scanner_webhook import build_webhook_payload, post_scanner_webhook

            payload = build_webhook_payload(request, result)
            asyncio.create_task(post_scanner_webhook(request.webhook_url, payload))

        # Audit: capture scanner results summary (fire-and-forget)
        try:
            from backend.audit.hooks import audit_scanner_result

            for row in rows[:20]:  # Top 20 results
                try:
                    sym = getattr(row, "symbol", None) or (
                        row.get("symbol") if isinstance(row, dict) else None
                    )
                    if sym:
                        asyncio.get_event_loop().create_task(
                            audit_scanner_result(
                                symbol=sym,
                                row=row,
                                phase="scan_complete",
                                score=getattr(row, "composite_score", None)
                                or (row.get("composite_score", 0) if isinstance(row, dict) else 0),
                            )
                        )
                except RuntimeError:
                    pass
        except Exception:
            pass

        return result

    async def _scan_symbol(
        self,
        symbol: str,
        request: MarketScannerRequest,
        skipped: dict[str, str],
        *,
        vix_val: float | None = None,
        bars_cache: dict[tuple[str, str], list[dict[str, Any]]] | None = None,
    ) -> MarketScannerRow | None:
        signals: dict[str, MarketScannerTimeframeSignal] = {}
        source: str | None = None
        sparkline: list[float] = []
        weight_matrix = request.customization.weight_matrix
        effective_weights: dict[str, dict[str, float]] | None = None
        concentration_audit: dict[str, Any] | None = None
        if institutional_scoring_enabled():
            effective_weights = compute_effective_weights(
                request.customization,
                list_market_scanner_indicators(),
            )
            concentration_audit = weight_concentration_audit(effective_weights)
        for timeframe in request.timeframes:
            try:
                fetched = await self._data_provider.get_bars(symbol, timeframe, max_bars=260)
            except Exception as exc:
                logger.warning(
                    "market_scanner.provider_failed symbol=%s timeframe=%s error=%s",
                    symbol,
                    timeframe,
                    exc,
                )
                fetched = {"bars": [], "source": "", "error": str(exc)}
            bars = fetched.get("bars") if isinstance(fetched, dict) else []
            bars_list = bars if isinstance(bars, list) else []
            if bars_cache is not None and bars_list:
                bars_cache[(symbol, timeframe)] = bars_list
            signal_weights: dict[str, float] | None = None
            use_custom_phase_a_weights = _customization_has_phase_a_weights(request.customization)
            if use_custom_phase_a_weights and effective_weights:
                signal_weights = {
                    k: v.get(_public_timeframe(timeframe), 0.0)
                    for k, v in effective_weights.items()
                    if k in PHASE_A_INDICATOR_KEYS and _public_timeframe(timeframe) in v
                }
            elif use_custom_phase_a_weights and weight_matrix:
                signal_weights = {
                    k: v.get(timeframe, 1.0)
                    for k, v in weight_matrix.items()
                    if k in PHASE_A_INDICATOR_KEYS and timeframe in v
                }
            signal = compute_timeframe_signal(
                symbol,
                timeframe,
                bars_list,
                vix_val=vix_val,
                weights=signal_weights,
            )
            signals[signal.timeframe] = signal
            if source is None:
                source = str(fetched.get("source") or "") if isinstance(fetched, dict) else None
            if not sparkline and bars_list:
                sparkline = _sparkline(bars_list)

        if not signals:
            skipped[symbol] = "no_timeframes"
            return None

        primary_timeframe = request.customization.primary_timeframe
        if primary_timeframe is not None:
            primary_signal = signals.get(primary_timeframe)
            if primary_signal is None or not primary_signal.ok:
                skipped[symbol] = "primary_timeframe_unavailable"
                return None

        row = _build_row(
            symbol,
            signals,
            source,
            sparkline,
            request.include_deep_metrics,
            request.customization,
            indicators=list_market_scanner_indicators(),
            effective_weights=effective_weights,
            concentration_audit=concentration_audit,
        )
        if row.price is None:
            skipped[symbol] = "no_price"
            return None
        return row


def _build_row(
    symbol: str,
    signals: dict[str, MarketScannerTimeframeSignal],
    source: str | None,
    sparkline: list[float],
    include_deep_metrics: bool,
    customization: ScannerCustomization,
    *,
    indicators: list[ScannerIndicatorDefinition] | None = None,
    effective_weights: dict[str, dict[str, float]] | None = None,
    concentration_audit: dict[str, Any] | None = None,
) -> MarketScannerRow:
    usable = [signal for signal in signals.values() if signal.ok]
    warnings = [warning for signal in signals.values() for warning in signal.warnings]
    vetoes = sorted({veto for signal in signals.values() for veto in signal.vetoes})
    bullish = sum(1 for signal in usable if signal.direction == "bullish")
    bearish = sum(1 for signal in usable if signal.direction == "bearish")
    aligned = max(bullish, bearish)
    direction: ScannerBias = "neutral"
    if bullish > bearish:
        direction = "bullish"
    elif bearish > bullish:
        direction = "bearish"

    primary_tf = customization.primary_timeframe
    if institutional_scoring_enabled() and usable and any(s.indicator_scores for s in usable):
        base_score = _weighted_timeframe_score(
            usable, customization, effective_weights=effective_weights
        )
        _, phase_a_contribs = composite_base_score_from_signals(
            usable,
            effective_weights,
            str(primary_tf) if primary_tf else None,
        )
    else:
        base_score = (
            _weighted_timeframe_score(usable, customization, effective_weights=effective_weights)
            if usable
            else 0.0
        )
        phase_a_contribs = {}
    alignment_bonus = 0.0
    if usable:
        alignment_bonus = 18.0 * (aligned / max(len(signals), 1))

    daily = signals.get("1D")
    intraday = [signals.get(tf) for tf in ("5m", "15m", "1h") if signals.get(tf)]
    if daily and daily.direction in {"bullish", "bearish"}:
        contradictions = [
            sig for sig in intraday if sig and sig.direction not in {daily.direction, "neutral"}
        ]
        if contradictions:
            warnings.append(WARN_TF_DIVERGENCE)
            alignment_bonus -= 10.0

    data_quality = 5.0 * (len(usable) / max(len(signals), 1))
    score = max(0.0, min(100.0, base_score * 0.78 + alignment_bonus + data_quality))
    if institutional_scoring_enabled() and concentration_audit:
        score = apply_concentration_penalty(score, concentration_audit)

    intraday_scores = [
        signals[tf].score for tf in ("5m", "15m") if signals.get(tf) and signals[tf].ok
    ]
    swing_scores = [signals[tf].score for tf in ("1h", "1D") if signals.get(tf) and signals[tf].ok]
    intraday_score = float(np.mean(intraday_scores)) if intraday_scores else 0.0
    swing_score = float(np.mean(swing_scores)) if swing_scores else 0.0
    reasons = _row_reasons(direction, aligned, usable, vetoes)
    latest = _latest_signal(usable)
    price = _metric_float(latest, "price")
    change_pct = _metric_float(latest, "change_pct")

    deep_metrics = None
    if include_deep_metrics:
        deep_metrics = {
            tf: scrub_metrics_dict(dict(signal.metrics)) for tf, signal in signals.items()
        }

    grade_tf: str = customization.primary_timeframe or "15m"
    if isinstance(grade_tf, str) and grade_tf.lower() == "1d":
        grade_tf = "1D"

    score_audit: dict[str, Any] = {
        "base_score": round(base_score, 2),
        "alignment_bonus": round(alignment_bonus, 2),
        "data_quality_component": round(data_quality, 2),
        "scanner_score_pre_phase_b": round(score, 2),
        "scoring_version": SCORING_VERSION,
        "scoring_schema_version": (
            SCORING_SCHEMA_VERSION if institutional_scoring_enabled() else None
        ),
    }
    if phase_a_contribs:
        score_audit["phase_a_indicator_contributions"] = {
            k: round(v, 2) for k, v in phase_a_contribs.items()
        }
    if concentration_audit:
        score_audit["weight_concentration"] = concentration_audit

    return MarketScannerRow(
        symbol=symbol,
        price=price,
        change_pct=change_pct,
        sparkline=sparkline,
        signals=dict(signals),
        scanner_score=round(score, 2),
        intraday_score=round(intraday_score, 2),
        swing_score=round(swing_score, 2),
        setup_grade=assign_grade(score, grade_tf),
        direction=direction,
        reasons=reasons,
        warnings=warnings[:8],
        vetoes=sorted(set(vetoes)),
        source=source,
        deep_metrics=deep_metrics,
        score_audit=score_audit,
        risk_hints={},
        score_ci_low=None,
        score_ci_high=None,
    )


def _phase_b_score_mode(primary_timeframe: ScannerTimeframe | str | None) -> PhaseBScoreMode:
    """Map desk timeframe to the Phase-A sub-score that gates Phase B synthesis."""
    pt = str(primary_timeframe or "15m").strip()
    if pt in {"5m", "15m"}:
        return "intraday"
    if pt in {"1h", "1D", "1d"}:
        return "swing"
    return "scanner"


def _phase_b_relevance_score(row: MarketScannerRow, mode: PhaseBScoreMode) -> float:
    if mode == "intraday":
        return float(row.intraday_score)
    if mode == "swing":
        return float(row.swing_score)
    return float(row.scanner_score)


def _select_rows_for_phase_b(
    rows: list[MarketScannerRow], request: MarketScannerRequest
) -> tuple[list[MarketScannerRow], PhaseBScoreMode, float]:
    """Pick Phase B candidates: sorted list, relevance score >= min_score, cap at module limit.

    Rows are expected to already be sorted by the same relevance axis (see ``_sort_rows``).
    Swing score does not gate Phase B when the desk is on 5m/15m, and vice versa.
    """
    mode = _phase_b_score_mode(request.customization.primary_timeframe)
    min_gate = float(request.filters.min_score)
    raw_limit = int(request.customization.module_synthesis_limit)
    if raw_limit <= 0 and rows:
        raw_limit = 1
    cap = min(max(raw_limit, 0), 100)

    selected: list[MarketScannerRow] = []
    for row in rows:
        relevance = _phase_b_relevance_score(row, mode)
        if relevance < min_gate:
            continue
        selected.append(row)
        if len(selected) >= cap:
            break
    return selected, mode, min_gate


async def _apply_phase_b_module_synthesis(
    rows: list[MarketScannerRow],
    request: MarketScannerRequest,
    options_snapshot_provider: OptionsSnapshotProvider,
    data_provider: ScannerDataProvider | None = None,
    bars_cache: dict[tuple[str, str], list[dict[str, Any]]] | None = None,
    *,
    effective_weights: dict[str, dict[str, float]] | None = None,
    indicators: list[ScannerIndicatorDefinition] | None = None,
) -> dict[str, Any] | None:
    phase_b_rows, score_mode, min_relevance = _select_rows_for_phase_b(rows, request)
    if not phase_b_rows:
        logger.info(
            "Phase B skipped — no symbol meets %s score >= %.1f (primary_tf=%s limit=%s)",
            score_mode,
            min_relevance,
            request.customization.primary_timeframe,
            request.customization.module_synthesis_limit,
        )
        return {"phase_b_score_mode": score_mode, "phase_b_min_relevance_score": min_relevance}

    limit = len(phase_b_rows)
    selected = [r.symbol for r in phase_b_rows]
    excluded_by_rank = [r.symbol for r in rows if r.symbol not in set(selected)]
    logger.info(
        "Phase B mode=%s min_relevance=%.1f selected=%s excluded_by_rank_or_score=%s",
        score_mode,
        min_relevance,
        selected,
        excluded_by_rank[:12],
    )
    top_relevance = {
        r.symbol: round(_phase_b_relevance_score(r, score_mode), 1) for r in phase_b_rows
    }
    logger.info("Phase B relevance scores: %s", top_relevance)

    real_data_audit: dict[str, Any] | None = None
    from backend.services.scanner_real_data_service import (
        enrich_phase_b_rows_with_real_data,
        scanner_real_data_enabled,
    )

    if scanner_real_data_enabled():
        real_data_audit = await enrich_phase_b_rows_with_real_data(phase_b_rows)

    from backend.services.scanner_barra_factor_model import (
        apply_barra_to_rows,
        barra_factors_enabled,
    )

    if barra_factors_enabled():
        apply_barra_to_rows(phase_b_rows)

    indicators = indicators or list_market_scanner_indicators()
    if institutional_scoring_enabled():
        from backend.services.market_scanner_institutional_scoring import (
            compute_effective_weights_with_audit,
        )

        effective_weights, tier_recalc = compute_effective_weights_with_audit(
            request.customization,
            indicators,
        )
        if real_data_audit:
            real_data_audit["tier_recalc"] = tier_recalc
    elif effective_weights is None:
        from backend.services.market_scanner_institutional_scoring import compute_effective_weights

        effective_weights = compute_effective_weights(request.customization, indicators)
    primary_tf = _public_timeframe(request.customization.primary_timeframe or request.timeframes[0])
    module_blend_weights: dict[str, float] = {}
    if institutional_scoring_enabled() and effective_weights:
        for mod in ("technical", "probabilistic", "options_gex", "fundamentals", "macro_micro"):
            module_blend_weights[mod] = module_blend_weight(
                mod, effective_weights, indicators, primary_tf
            )
    enabled_modules = set(request.customization.enabled_modules or [])
    include_all = request.customization.enabled_modules is None
    options_enabled = include_all or "options_gex" in enabled_modules
    technical_enabled = include_all or "technical" in enabled_modules
    probabilistic_enabled = include_all or "probabilistic" in enabled_modules
    fundamentals_enabled = include_all or "fundamentals" in enabled_modules
    macro_micro_enabled = include_all or "macro_micro" in enabled_modules

    ratios_by_symbol: dict[str, dict[str, Any] | None] = {}
    scores_by_symbol: dict[str, dict[str, Any] | None] = {}
    key_metrics_by_symbol: dict[str, dict[str, Any] | None] = {}
    earnings_by_symbol: dict[str, list[dict[str, Any]] | None] = {}

    if fundamentals_enabled and limit > 0:
        from backend.services.fundamentals_scanner_orchestrator import (
            fetch_earnings_surprises_batch,
            fetch_financial_scores_batch,
            fetch_key_metrics_ttm_batch,
            fetch_ratios_ttm_batch,
        )

        syms = [r.symbol for r in phase_b_rows]
        (
            ratios_by_symbol,
            scores_by_symbol,
            key_metrics_by_symbol,
            earnings_by_symbol,
        ) = await asyncio.gather(
            fetch_ratios_ttm_batch(syms),
            fetch_financial_scores_batch(syms),
            fetch_key_metrics_ttm_batch(syms),
            fetch_earnings_surprises_batch(syms),
        )

    macro_ctx: dict[str, Any] | None = None
    if macro_micro_enabled and limit > 0:
        from backend.services.macro_scanner_context import fetch_macro_scanner_context

        try:
            macro_ctx = await fetch_macro_scanner_context()
        except Exception as exc:
            logger.debug("market_scanner.macro_context_failed err=%s", str(exc)[:120])
            macro_ctx = None
    # Solo se hace para los rows dentro del límite; se paraleliza por símbolo.
    bars_by_symbol: dict[str, dict[str, list[dict[str, Any]]]] = {}
    if (
        technical_enabled or probabilistic_enabled or options_enabled
    ) and data_provider is not None:

        async def _fetch_bars_for_row(
            row: MarketScannerRow,
        ) -> tuple[str, dict[str, list[dict[str, Any]]]]:
            tf_bars: dict[str, list[dict[str, Any]]] = {}
            for timeframe in request.timeframes:
                cache_key = (row.symbol, timeframe)
                cached = bars_cache.get(cache_key) if bars_cache is not None else None
                if cached is not None:
                    tf_bars[_public_timeframe(timeframe)] = cached
                    continue
                try:
                    fetched = await data_provider.get_bars(row.symbol, timeframe, max_bars=260)
                    raw_bars = fetched.get("bars") if isinstance(fetched, dict) else []
                    tf_bars[_public_timeframe(timeframe)] = (
                        raw_bars if isinstance(raw_bars, list) else []
                    )
                except Exception as exc:
                    logger.debug(
                        "market_scanner.phase_b_bars_failed symbol=%s tf=%s error=%s",
                        row.symbol,
                        timeframe,
                        str(exc)[:120],
                    )
            return row.symbol, tf_bars

        fetch_tasks = [_fetch_bars_for_row(row) for row in phase_b_rows]
        fetch_results = await asyncio.gather(*fetch_tasks, return_exceptions=True)
        for fetch_result in fetch_results:
            if isinstance(fetch_result, Exception):
                continue
            symbol, tf_bars = fetch_result
            bars_by_symbol[symbol] = tf_bars

    # ── Opciones GEX (sin cambios) ─────────────────────────────────────────
    options_snapshots: dict[str, object | None] = {}
    if options_enabled:
        snapshot_tasks: dict[str, asyncio.Task[object | None]] = {
            row.symbol: asyncio.create_task(
                _get_cached_options_snapshot(row.symbol, options_snapshot_provider)
            )
            for row in phase_b_rows
            if not row.symbol.endswith(("USD",))
        }
        if snapshot_tasks:
            results = await asyncio.gather(*snapshot_tasks.values(), return_exceptions=True)
            for symbol, result in zip(snapshot_tasks, results, strict=True):
                options_snapshots[symbol] = None if isinstance(result, Exception) else result

    # ── Síntesis por símbolo ───────────────────────────────────────────────
    for row in phase_b_rows:
        options_snapshot = options_snapshots.get(row.symbol)
        greek_flow = _compute_options_greek_flow(options_snapshot)
        _attach_options_greek_flow(row, greek_flow)

        module_signals = {}
        if technical_enabled:
            bars_by_tf = bars_by_symbol.get(row.symbol, {})
            if bars_by_tf:
                # v2: motores reales con barras re-fetched
                module_signals["technical"] = synthesize_technical_signal_v2(
                    row,
                    bars_by_tf,
                    request.customization,
                    indicators,
                    effective_weights=effective_weights,
                    primary_timeframe=primary_tf,
                )
            else:
                # fallback proxy (sin barras disponibles)
                module_signals["technical"] = synthesize_technical_signal(
                    row,
                    request.customization,
                    indicators,
                )
        if probabilistic_enabled:
            bars_by_tf = bars_by_symbol.get(row.symbol, {})
            if bars_by_tf:
                module_signals["probabilistic"] = synthesize_probabilistic_signal_v2(
                    row,
                    bars_by_tf,
                    request.customization,
                    indicators,
                    options_snapshot=options_snapshot,
                    effective_weights=effective_weights,
                    primary_timeframe=primary_tf,
                )
            else:
                module_signals["probabilistic"] = synthesize_probabilistic_signal(
                    row,
                    request.customization,
                    indicators,
                )
        obv_oi_result = None
        obv_oi_weight = 0.0
        mfi_flow_result = None
        mfi_flow_weight = 0.0
        cmf_iv_result = None
        cmf_iv_weight = 0.0
        if options_enabled:
            bars_by_tf = bars_by_symbol.get(row.symbol, {})
            for timeframe in request.timeframes:
                pub_tf = _public_timeframe(timeframe)
                tf_bars = bars_by_tf.get(pub_tf, [])
                if not tf_bars:
                    continue

                obv_weight = _fusion_weight_for_timeframe(
                    "obv_oi",
                    request.customization,
                    indicators,
                    pub_tf,
                    effective_weights=effective_weights,
                    legacy_resolver=obv_oi_weight_for_timeframe,
                )
                if obv_weight > 0:
                    obv_tf_result = analyze_obv_oi_for_scanner(
                        row.symbol, pub_tf, tf_bars, options_snapshot
                    )
                    if request.include_deep_metrics:
                        row.deep_metrics = attach_obv_oi_deep_metrics(
                            row.deep_metrics, pub_tf, obv_tf_result
                        )
                    if pub_tf == primary_tf:
                        obv_oi_result = obv_tf_result
                        obv_oi_weight = obv_weight

                mfi_weight = _fusion_weight_for_timeframe(
                    "mfi_flow",
                    request.customization,
                    indicators,
                    pub_tf,
                    effective_weights=effective_weights,
                    legacy_resolver=mfi_flow_weight_for_timeframe,
                )
                if mfi_weight > 0:
                    mfi_tf_result = analyze_mfi_flow_for_scanner(
                        row.symbol, pub_tf, tf_bars, options_snapshot
                    )
                    if request.include_deep_metrics:
                        row.deep_metrics = attach_mfi_flow_deep_metrics(
                            row.deep_metrics, pub_tf, mfi_tf_result
                        )
                    if pub_tf == primary_tf:
                        mfi_flow_result = mfi_tf_result
                        mfi_flow_weight = mfi_weight

                cmf_weight = _fusion_weight_for_timeframe(
                    "cmf_iv",
                    request.customization,
                    indicators,
                    pub_tf,
                    effective_weights=effective_weights,
                    legacy_resolver=cmf_iv_weight_for_timeframe,
                )
                if cmf_weight > 0:
                    cmf_tf_result = analyze_cmf_iv_for_scanner(
                        row.symbol, pub_tf, tf_bars, options_snapshot
                    )
                    if request.include_deep_metrics:
                        row.deep_metrics = attach_cmf_iv_deep_metrics(
                            row.deep_metrics, pub_tf, cmf_tf_result
                        )
                    if pub_tf == primary_tf:
                        cmf_iv_result = cmf_tf_result
                        cmf_iv_weight = cmf_weight

            if mfi_flow_result is not None and obv_oi_result is not None:
                mfi_flow_result = mark_double_conviction(
                    mfi_flow_result, obv_oi_result=obv_oi_result
                )

            module_signals["options_gex"] = synthesize_options_gex_signal(
                row,
                request.customization,
                indicators,
                options_snapshot,
                obv_oi_result=obv_oi_result,
                obv_oi_weight=obv_oi_weight,
                mfi_flow_result=mfi_flow_result,
                mfi_flow_weight=mfi_flow_weight,
                cmf_iv_result=cmf_iv_result,
                cmf_iv_weight=cmf_iv_weight,
                effective_weights=effective_weights,
                primary_timeframe=primary_tf,
            )
        if fundamentals_enabled:
            from backend.services.fundamentals_scanner_orchestrator import (
                synthesize_fundamentals_signal,
            )

            module_signals["fundamentals"] = synthesize_fundamentals_signal(
                row,
                request.customization,
                indicators,
                ratios_by_symbol.get(row.symbol),
                scores_by_symbol.get(row.symbol),
                key_metrics_by_symbol.get(row.symbol),
                earnings_by_symbol.get(row.symbol),
            )
        if macro_micro_enabled:
            from backend.services.macro_micro_scanner_orchestrator import (
                synthesize_macro_micro_signal,
            )

            module_signals["macro_micro"] = synthesize_macro_micro_signal(
                row,
                request.customization,
                indicators,
                macro_ctx,
            )
        row.module_signals = module_signals
        primary_bars = bars_by_symbol.get(row.symbol, {}).get(primary_tf, [])
        row.institutional_overlay = attach_institutional_overlay(
            options_snapshot,
            primary_bars,
            greek_flow,
        )

        if module_signals:
            pre_blend = float(row.scanner_score)
            per_indicator_contributions: dict[str, float] | None = None
            concentration_audit: dict[str, Any] | None = None
            if institutional_scoring_enabled() and effective_weights:
                per_indicator_contributions = {
                    k: sum(v.values()) for k, v in effective_weights.items()
                }
                concentration_audit = weight_concentration_audit(effective_weights)
            blended, blend_audit = blend_phase_b_scanner_score(
                pre_blend,
                module_signals,
                base_weight=0.78,
                module_weight=0.22,
                module_blend_weights=module_blend_weights or None,
                per_indicator_contributions=per_indicator_contributions,
                concentration_audit=concentration_audit,
            )
            if concentration_audit:
                blended = apply_concentration_penalty(blended, concentration_audit)
            meta_delta, meta_info = try_meta_learner_score_delta(
                blended,
                module_signals,
                primary_bars,
            )
            final_score = max(0.0, min(100.0, blended + meta_delta))
            row.scanner_score = round(final_score, 2)
            row.setup_grade = assign_grade(final_score, primary_tf)

            latest_sig = row.signals.get(primary_tf) or next(iter(row.signals.values()), None)
            atr_pct: float | None = None
            if latest_sig is not None and isinstance(latest_sig.metrics, dict):
                raw_atr = latest_sig.metrics.get("atr_pct")
                if raw_atr is not None:
                    try:
                        atr_pct = float(raw_atr)
                    except (TypeError, ValueError):
                        atr_pct = None

            row.risk_hints = build_risk_hints(
                row.scanner_score,
                str(row.direction),
                module_signals,
                atr_pct,
            )
            opt_features = _plain_dict(
                _plain_dict(options_snapshots.get(row.symbol)).get("options_gex_features")
            )
            if opt_features:
                row.risk_hints["options_gex_source_tier"] = str(
                    opt_features.get("source_tier") or ""
                )
                quality = opt_features.get("data_quality_score")
                if quality is not None:
                    try:
                        parsed_quality = round(float(quality), 4)
                    except (TypeError, ValueError):
                        parsed_quality = None
                    if parsed_quality is not None:
                        row.risk_hints["options_gex_data_quality_score"] = parsed_quality
            lo, hi = score_confidence_band_68(row.scanner_score, module_signals)
            row.score_ci_low = round(lo, 2)
            row.score_ci_high = round(hi, 2)

            audit = dict(row.score_audit)
            audit["phase_b_blend"] = blend_audit
            audit["meta_learner"] = meta_info or {"status": "unavailable"}
            audit["calibration"] = {
                "grade_thresholds": os.getenv("MARKET_SCANNER_GRADE_THRESHOLDS_JSON")
                or "builtin_default",
                "meta_learner_path": os.getenv("MARKET_SCANNER_META_LEARNER_PATH")
                or "backend/models/meta_learner.joblib",
            }
            row.score_audit = audit

    out: dict[str, Any] = {
        "macro_context": macro_ctx,
        "phase_b_score_mode": score_mode,
        "phase_b_min_relevance_score": min_relevance,
        "phase_b_candidate_count": limit,
    }
    if real_data_audit:
        out["real_data"] = real_data_audit
    if effective_weights:
        out["effective_weight_matrix"] = effective_weights
    return out


def _compute_options_greek_flow(snapshot: object | None) -> dict[str, Any] | None:
    payload = _plain_dict(snapshot)
    if not payload:
        return None
    chain_raw = payload.get("chain")
    chain = chain_raw if isinstance(chain_raw, list) else []
    chain_rows = [_plain_dict(row) for row in chain]
    try:
        from backend.services.options_greek_flow_provider import compute_greek_flow_snapshot
    except Exception as exc:
        logger.debug("market_scanner.greek_flow_import_unavailable error=%s", str(exc)[:120])
        return None
    try:
        spot = _number_or_none(payload.get("spot"))
        computed = compute_greek_flow_snapshot(chain_rows, spot)
    except Exception as exc:
        logger.debug("market_scanner.greek_flow_failed error=%s", str(exc)[:120])
        return None
    return _compact_options_greek_flow(computed, spot=_number_or_none(payload.get("spot")))


def _compact_options_greek_flow(
    greek_flow: dict[str, Any],
    *,
    spot: float | None,
) -> dict[str, Any]:
    keys = (
        "source_tier",
        "data_quality_score",
        "missing_components",
        "row_count",
        "usable_row_count",
        "net_gamma_exposure",
        "net_delta_exposure",
        "net_vanna_exposure",
        "net_charm_exposure",
        "call_wall",
        "put_wall",
        "gamma_flip",
        "zero_gamma_distance_pct",
        "zero_dte_gamma_pressure",
        "pressure_by_strike",
    )
    compact = {key: greek_flow.get(key) for key in keys if key in greek_flow}
    if spot is not None:
        compact["spot"] = spot
    return compact


def _number_or_none(value: object) -> float | None:
    if isinstance(value, bool) or value is None:
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def _attach_options_greek_flow(
    row: MarketScannerRow,
    greek_flow: dict[str, Any] | None,
) -> None:
    if not greek_flow:
        return
    deep = dict(row.deep_metrics or {})
    deep["options_greek_flow"] = greek_flow
    row.deep_metrics = deep

    audit = dict(row.score_audit or {})
    options_audit = dict(_plain_dict(audit.get("options_gex")))
    options_audit["greek_flow"] = {
        "source_tier": str(greek_flow.get("source_tier") or "unavailable"),
        "data_quality_score": greek_flow.get("data_quality_score"),
        "missing_components": (
            [str(item) for item in greek_flow.get("missing_components", []) if item]
            if isinstance(greek_flow.get("missing_components"), list)
            else []
        ),
    }
    audit["options_gex"] = options_audit
    row.score_audit = audit


def _primary_module_for_row(row: MarketScannerRow) -> str | None:
    """Pick the dominant module for funding-gate evidence.

    Prefers ``options_gex`` when present (richest evidence), then falls back to
    ``technical``, then ``predictive``/``probabilistic``. ``probabilistic`` maps
    to the backtest service's ``predictive`` module. Returns None when nothing
    is available.
    """
    signals = row.module_signals or {}
    if not signals:
        return None
    for preferred in ("options_gex", "technical", "predictive", "probabilistic"):
        if preferred in signals:
            return "predictive" if preferred == "probabilistic" else preferred
    return None


def _row_source_tier(row: MarketScannerRow) -> str | None:
    """Extract the options source tier from the row's risk_hints, if any."""
    hints = row.risk_hints or {}
    tier = hints.get("options_gex_source_tier")
    if tier is None:
        return None
    tier_str = str(tier).strip().lower()
    return tier_str or None


def _row_data_quality_score(row: MarketScannerRow) -> float | None:
    hints = row.risk_hints or {}
    quality = hints.get("options_gex_data_quality_score")
    if quality is None:
        return None
    try:
        return max(0.0, min(1.0, float(quality)))
    except (TypeError, ValueError):
        return None


def _row_conflict_score(row: MarketScannerRow) -> float | None:
    """Approximate cross-module disagreement as a 0-1 score (1 = full conflict)."""
    signals = row.module_signals or {}
    if len(signals) < 2:
        return None
    labels = [str(getattr(sig, "label", "") or "").lower() for sig in signals.values()]
    bullish = sum(1 for label in labels if label in {"buy", "strong_buy"})
    bearish = sum(1 for label in labels if label in {"sell", "strong_sell"})
    total = bullish + bearish
    if total == 0:
        return 0.0
    return round(min(bullish, bearish) / total, 4)


async def _apply_funding_gate(
    rows: list[MarketScannerRow],
    request: MarketScannerRequest,
    *,
    db_path: Path | None,
    backtest_runner: Callable[..., dict[str, Any]] | None,
    batch_runner: Callable[..., dict[str, Any]] | None = None,
) -> None:
    """Enrich rows with funding-suitability evidence (additive, never mutates score).

    Survival rule: high scanner_score must never authorize normal sizing if backtest
    evidence is missing, the source is a light proxy, or the simulated history
    would have breached a funding-account rule. The gate writes:
      - directional_score / risk_score (decomposed from scanner_score + evidence)
      - data_quality_score, module_backtest_grade
      - funding_suitability ('allow' | 'size_down' | 'block' | 'informational_only')
      - funding_reason_codes (stable strings shared with portfolio_risk_service)

    Evidence resolution order:
      1. ``batch_runner`` (preferred when no per-symbol runner was injected) — one
         SQL pass per scan via ``run_prediction_backtest_batch``.
      2. ``backtest_runner`` — per-(symbol, module) fallback, preserves the legacy
         injection point used by existing tests and used when the batch path fails.
    Missing evidence is surfaced as ``informational_only`` with
    ``insufficient_backtest_evidence`` — never a Phase B veto. We never mock.
    """
    if not rows or not getattr(request, "include_funding_gate", True):
        return
    if db_path is None or not Path(db_path).exists():
        _apply_funding_gate_missing_db(rows)
        return

    # Decide which evidence path to use.
    # - If the caller injected a per-symbol ``backtest_runner`` we honour it (tests rely
    #   on this contract). Otherwise we prefer batch — single SQL pass per scan.
    use_batch = backtest_runner is None
    resolved_batch_runner = batch_runner
    if use_batch and resolved_batch_runner is None:
        if _funding_gate_backtest_engine() == "vectorbt":
            from backend.services.vectorbt_prediction_backtest_service import (
                run_vectorbt_prediction_backtest_batch,
            )

            resolved_batch_runner = run_vectorbt_prediction_backtest_batch
        else:
            from backend.services.prediction_backtest_service import run_prediction_backtest_batch

            resolved_batch_runner = run_prediction_backtest_batch

    per_symbol_runner = backtest_runner
    if per_symbol_runner is None:
        from backend.services.prediction_backtest_service import run_prediction_backtest

        per_symbol_runner = run_prediction_backtest

    # Build the unique (symbol, module) work set — collect ALL supported modules per row,
    # not just the primary one, so multi-module evidence can be assembled.
    # Module name mapping: "probabilistic" in module_signals → "predictive" for backtest service.
    def _backtest_module_name(mod: str) -> str:
        return "predictive" if mod == "probabilistic" else mod

    pairs: list[tuple[MarketScannerRow, str]] = []
    # row_module_pairs maps each row to every supported module it has signal data for.
    row_module_pairs: dict[str, list[str]] = {}  # symbol → [backtest_module_names]
    for row in rows:
        supported: list[str] = []
        for signal_mod in row.module_signals or {}:
            bt_mod = _backtest_module_name(signal_mod)
            if bt_mod in SCANNER_FUNDING_GATE_SUPPORTED_MODULES and bt_mod not in supported:
                supported.append(bt_mod)
        if not supported:
            continue
        row_module_pairs[row.symbol] = supported
        for bt_mod in supported:
            pairs.append((row, bt_mod))

    if not pairs:
        return

    unique_symbols = sorted({row.symbol for row, _ in pairs})
    unique_modules = sorted({module for _, module in pairs})
    cache: dict[tuple[str, str], dict[str, Any] | None] = {}

    async def _fallback_per_symbol() -> None:
        """Per-(symbol, module) fallback. Used when batch fails or was injected as None."""

        async def _eval(symbol: str, module: str) -> None:
            key = (symbol, module)
            if key in cache:
                return
            try:
                result = await asyncio.to_thread(
                    per_symbol_runner,
                    db_path=db_path,
                    module=module,
                    symbol=symbol,
                )
            except FileNotFoundError:
                cache[key] = None
                return
            except Exception as exc:
                logger.warning(
                    "market_scanner.funding_gate_backtest_failed symbol=%s module=%s error=%s",
                    symbol,
                    module,
                    str(exc)[:180],
                )
                cache[key] = None
                return
            cache[key] = result if isinstance(result, dict) else None

        # Only evaluate distinct (symbol, module) pairs needed by the current rows.
        needed = {(row.symbol, module) for row, module in pairs}
        await asyncio.gather(*(_eval(symbol, module) for symbol, module in needed))

    if use_batch and resolved_batch_runner is not None:
        # Single batch call covering every (symbol, module) the scanner needs.
        try:
            batch_payload = await asyncio.to_thread(
                resolved_batch_runner,
                db_path=db_path,
                symbols=unique_symbols,
                modules=unique_modules,
            )
        except FileNotFoundError:
            batch_payload = None
        except Exception as exc:
            logger.warning(
                "market_scanner.funding_gate_batch_failed symbols=%d modules=%d error=%s",
                len(unique_symbols),
                len(unique_modules),
                str(exc)[:180],
            )
            batch_payload = None

        if isinstance(batch_payload, dict):
            for result in batch_payload.get("results") or []:
                if not isinstance(result, dict):
                    continue
                symbol = str(result.get("symbol") or "").upper().strip()
                module = str(result.get("module") or "").strip().lower()
                if not symbol or not module:
                    continue
                cache[(symbol, module)] = result
        else:
            # Batch path failed entirely — fall back to per-symbol calls so that one
            # bad evidence row does not blank the whole scan.
            logger.warning(
                "market_scanner.funding_gate_batch_unavailable falling_back_to_per_symbol pairs=%d",
                len(pairs),
            )
            await _fallback_per_symbol()
    else:
        # Caller wanted per-symbol path (test injection). Keep behaviour byte-for-byte.
        await _fallback_per_symbol()

    # Critical modules — their block/insufficient_data result blocks the whole row.
    critical_modules = frozenset({"options_gex", "technical"})

    # Process each row by collecting evidence for all its supported modules.
    processed_rows: set[str] = set()
    for row in rows:
        if row.symbol in processed_rows:
            continue
        bt_modules = row_module_pairs.get(row.symbol)
        if not bt_modules:
            continue
        processed_rows.add(row.symbol)

        # -- Resolve source tier: options_gex gets it from risk_hints, others from evidence.
        row_level_source_tier = _row_source_tier(row)
        row_level_data_quality = _row_data_quality_score(row)

        # -- Build per-module evidence dicts.
        module_evidences: dict[str, dict[str, Any]] = {}
        for bt_mod in bt_modules:
            evidence = cache.get((row.symbol, bt_mod))

            # Source tier: prefer risk_hints for options_gex (most authoritative), else evidence.
            if bt_mod == "options_gex" and row_level_source_tier is not None:
                source_tier: str | None = row_level_source_tier
            elif evidence and evidence.get("source_tier"):
                source_tier = str(evidence["source_tier"]).strip().lower() or None
            else:
                source_tier = None

            # Data quality: prefer risk_hints for options_gex, else evidence.
            if bt_mod == "options_gex" and row_level_data_quality is not None:
                data_quality: float | None = row_level_data_quality
            elif evidence and evidence.get("data_quality_score") is not None:
                try:
                    data_quality = max(0.0, min(1.0, float(evidence["data_quality_score"])))
                except (TypeError, ValueError):
                    data_quality = None
            else:
                data_quality = None

            # Signal coverage from ScannerModuleSignal: engine_count / available_count.
            # Use the signal key that maps to this backtest module.
            signal_key = "probabilistic" if bt_mod == "predictive" else bt_mod
            mod_signal = (row.module_signals or {}).get(signal_key)
            if mod_signal is not None:
                available = getattr(mod_signal, "available_count", 0) or 0
                engine = getattr(mod_signal, "engine_count", 0) or 0
                signal_coverage: float | None = (
                    round(engine / available, 4) if available > 0 else 0.0
                )
            else:
                signal_coverage = None

            mod_ev = evaluate_module_evidence(
                module=bt_mod,
                backtest_evidence=evidence,
                source_tier=source_tier,
                data_quality_score=data_quality,
                signal_coverage=signal_coverage,
            )
            if evidence and evidence.get("engine"):
                mod_ev["engine"] = str(evidence["engine"])
            if evidence and evidence.get("data_source"):
                mod_ev["data_source"] = str(evidence["data_source"])
            module_evidences[bt_mod] = mod_ev

        # Store per-module evidence on the row.
        row.evidence_by_module = module_evidences

        # -- Multi-module funding policy ------------------------------------------
        # 1. Block if any critical module (options_gex, technical) with real signal data
        #    returns an explicit "block" suitability (overfit, would_breach, etc.).
        # 2. If ALL modules have "insufficient_data" — propagate as insufficient_data overall.
        # 3. Size-down if any module has size-down conditions or a non-critical insufficient.
        # 4. Allow only if all available modules clear their checks.

        overall_suitability = "allow"
        all_reason_codes: list[str] = []
        size_multipliers: list[float] = []
        all_informational = all(
            ev.get("suitability") == SUITABILITY_INFORMATIONAL_ONLY
            for ev in module_evidences.values()
        )

        for bt_mod, ev in module_evidences.items():
            mod_suit = ev.get("suitability", SUITABILITY_INFORMATIONAL_ONLY)
            mod_reasons = list(ev.get("reasons") or [])
            mod_size = float(ev.get("size_multiplier") or 1.0)
            signal_key = "probabilistic" if bt_mod == "predictive" else bt_mod
            has_real_signal = (row.module_signals or {}).get(signal_key) is not None
            is_critical = bt_mod in critical_modules and has_real_signal

            if mod_suit == "block" and is_critical:
                # Critical module explicitly blocked → block everything.
                overall_suitability = "block"
            elif mod_suit == "block" and overall_suitability not in {"block"}:
                # Non-critical block → size_down (cannot proceed at normal size).
                overall_suitability = (
                    "size_down" if overall_suitability == "allow" else overall_suitability
                )
            elif mod_suit == "size_down" and overall_suitability == "allow":
                overall_suitability = "size_down"

            all_reason_codes.extend(r for r in mod_reasons if r not in all_reason_codes)
            size_multipliers.append(mod_size)

        # All modules lack backtest history — advisory only, never a veto.
        if all_informational:
            overall_suitability = SUITABILITY_INFORMATIONAL_ONLY

        # Inter-module conflict contributes additional size-down pressure.
        conflict_score = _row_conflict_score(row)
        if conflict_score is not None and conflict_score >= 0.5:
            if REASON_CONFLICTING_MODULES not in all_reason_codes:
                all_reason_codes.append(REASON_CONFLICTING_MODULES)
            if overall_suitability == "allow":
                overall_suitability = "size_down"

        # Recommended size multiplier — most restrictive across modules.
        recommended_mult = round(min(size_multipliers), 4) if size_multipliers else None

        # -- best_supporting_module / weakest_link_module --------------------------
        # Score each non-blocked module by data_quality_score * signal_coverage.
        def _module_support_score(ev: dict[str, Any]) -> float:
            dq = float(ev.get("data_quality_score") or 0.0)
            sc = float(ev.get("signal_coverage") or 0.0)
            return dq * sc

        non_blocked = {
            mod: ev
            for mod, ev in module_evidences.items()
            if ev.get("suitability") not in {"block"}
        }
        has_signal = {
            mod: ev
            for mod, ev in module_evidences.items()
            if (row.module_signals or {}).get("probabilistic" if mod == "predictive" else mod)
            is not None
        }

        best_mod: str | None = None
        if non_blocked:
            best_mod = max(non_blocked, key=lambda m: _module_support_score(non_blocked[m]))

        weak_mod: str | None = None
        if has_signal:
            weak_mod = min(has_signal, key=lambda m: _module_support_score(has_signal[m]))

        row.best_supporting_module = best_mod
        row.weakest_link_module = weak_mod
        row.recommended_size_multiplier = recommended_mult

        # -- Backward-compat: populate legacy fields from dominant module ----------
        # Use best supporting module for grade/quality; fall back to primary module.
        primary_module = _primary_module_for_row(row)
        dominant_mod = best_mod or primary_module
        dominant_evidence = module_evidences.get(dominant_mod) if dominant_mod else None
        dominant_backtest = cache.get((row.symbol, dominant_mod)) if dominant_mod else None

        # data_quality_score: use dominant module's value.
        dom_dq: float | None = None
        if dominant_evidence:
            dq_val = dominant_evidence.get("data_quality_score")
            if dq_val is not None:
                try:
                    dom_dq = max(0.0, min(1.0, float(dq_val)))
                except (TypeError, ValueError):
                    dom_dq = None

        # Compute risk penalty from dominant module for directional/risk decomposition.
        dom_source_tier = dominant_evidence.get("source_tier") if dominant_evidence else None
        penalty = risk_penalty_from_evidence(
            backtest_evidence=dominant_backtest,
            source_tier=dom_source_tier,
            data_quality_score=dom_dq,
            conflict_score=conflict_score,
        )
        directional, risk = split_directional_and_risk_scores(
            float(row.scanner_score), risk_penalty=penalty
        )

        row.directional_score = round(directional, 2)
        row.risk_score = round(risk, 2)
        row.data_quality_score = round(dom_dq, 4) if dom_dq is not None else None
        row.module_backtest_grade = (
            str(dominant_backtest.get("module_backtest_grade")) if dominant_backtest else None
        )
        row.funding_suitability = overall_suitability
        row.funding_reason_codes = all_reason_codes

        # Audit trail — explainable, never silently overrides scoring.
        audit = dict(row.score_audit)
        audit["funding_gate"] = {
            "dominant_module": dominant_mod,
            "modules_evaluated": list(module_evidences.keys()),
            "source_tier": dom_source_tier,
            "size_multiplier": recommended_mult,
            "risk_penalty": round(penalty, 2),
            "evidence_present": dominant_backtest is not None,
            "evidence_grade": row.module_backtest_grade,
            "best_supporting_module": best_mod,
            "weakest_link_module": weak_mod,
        }
        row.score_audit = audit


def _apply_funding_gate_missing_db(rows: list[MarketScannerRow]) -> None:
    """Mark rows when the predictions DB is absent — informational, not a veto."""
    for row in rows:
        outcome = evaluate_funding_suitability(
            backtest_evidence=None,
            source_tier=_row_source_tier(row),
            data_quality_score=_row_data_quality_score(row),
            conflict_score=_row_conflict_score(row),
        )
        penalty = risk_penalty_from_evidence(
            backtest_evidence=None,
            source_tier=_row_source_tier(row),
            data_quality_score=_row_data_quality_score(row),
            conflict_score=_row_conflict_score(row),
        )
        directional, risk = split_directional_and_risk_scores(
            float(row.scanner_score), risk_penalty=penalty
        )
        row.directional_score = round(directional, 2)
        row.risk_score = round(risk, 2)
        row.funding_suitability = str(outcome["suitability"])
        row.funding_reason_codes = list(outcome.get("reason_codes") or [])
        row.recommended_size_multiplier = float(outcome.get("size_multiplier") or 1.0)
        audit = dict(row.score_audit)
        audit["funding_gate"] = {
            "evidence_present": False,
            "risk_penalty": round(penalty, 2),
            "size_multiplier": row.recommended_size_multiplier,
            "missing_db": True,
        }
        row.score_audit = audit


def _apply_rl_policy_evidence(rows: list[MarketScannerRow]) -> None:
    """Attach experimental offline RL evidence without mutating gate decisions."""
    if not rows or not scanner_rl_policy_enabled():
        return
    for row in rows:
        evidence = get_rl_policy_score(row.symbol, _rl_policy_features(row))
        audit = dict(row.score_audit or {})
        audit["rl_policy"] = evidence
        row.score_audit = audit
        deep = dict(row.deep_metrics or {})
        deep["rl_policy"] = evidence
        row.deep_metrics = deep


def _rl_policy_features(row: MarketScannerRow) -> dict[str, Any]:
    features: dict[str, Any] = {
        "scanner_score": row.scanner_score,
        "direction": row.direction,
        "directional_score": row.directional_score,
        "risk_score": row.risk_score,
        "data_quality_score": row.data_quality_score,
        "funding_suitability": row.funding_suitability,
    }
    for module_name, signal in (row.module_signals or {}).items():
        score = getattr(signal, "score", None)
        confidence = getattr(signal, "confidence", None)
        features[f"module_score_{module_name}"] = score
        features[f"module_confidence_{module_name}"] = confidence
    if row.risk_hints:
        for key, value in row.risk_hints.items():
            features[f"risk_hint_{key}"] = value
    return features


async def _fetch_options_snapshot_for_scanner(symbol: str) -> object | None:
    try:
        from backend.layer_3_specialists.opciones_gex.chain_analytics_history import (
            OptionsChainAnalyticsHistoryStore,
        )
        from backend.routers.options_router import (
            options_chain_analytics_service,
            options_snapshot_service,
        )
        from backend.services.thesis_domain_narratives import get_risk_free_for_options_snapshot

        risk_free_rate = get_risk_free_for_options_snapshot()
        snapshot_result, analytics_result = await asyncio.gather(
            asyncio.wait_for(
                options_snapshot_service(symbol, None, risk_free_rate),
                timeout=SCANNER_OPTIONS_SNAPSHOT_TIMEOUT_SECONDS,
            ),
            asyncio.wait_for(
                options_chain_analytics_service(symbol, None, risk_free_rate),
                timeout=SCANNER_OPTIONS_SNAPSHOT_TIMEOUT_SECONDS,
            ),
            return_exceptions=True,
        )
        if isinstance(snapshot_result, Exception):
            raise snapshot_result

        if hasattr(snapshot_result, "model_dump"):
            payload = snapshot_result.model_dump(mode="json")
        elif isinstance(snapshot_result, dict):
            payload = dict(snapshot_result)
        else:
            payload = {}

        if not isinstance(analytics_result, Exception) and analytics_result is not None:
            payload["chain_analytics"] = (
                analytics_result.model_dump(mode="json")
                if hasattr(analytics_result, "model_dump")
                else analytics_result
            )
            history = OptionsChainAnalyticsHistoryStore().history_response(
                symbol, expiry=None, limit=12
            )
            payload["chain_analytics_history"] = history.model_dump(mode="json")
            from backend.services.options_gex_feature_assembler import assemble_options_gex_features

            payload["options_gex_features"] = assemble_options_gex_features(payload)
        return payload
    except Exception as exc:
        logger.warning(
            "market_scanner.options_snapshot_failed symbol=%s error=%s",
            symbol,
            str(exc)[:180],
        )
        return None


def _plain_dict(value: object) -> dict[str, object]:
    if isinstance(value, dict):
        return value
    if hasattr(value, "model_dump"):
        dumped = value.model_dump(mode="json")  # type: ignore[attr-defined]
        return dumped if isinstance(dumped, dict) else {}
    return {}


async def _apply_live_prices(
    rows: list[MarketScannerRow],
    live_price_provider: LivePriceProvider,
) -> int:
    if not rows:
        return 0

    applied = 0
    live_prices = await fetch_market_scanner_live_prices(
        [row.symbol for row in rows],
        live_price_provider,
    )
    for row in rows:
        live = live_prices.get(row.symbol)
        if live is None or not _finite(live.price) or live.price <= 0:
            continue
        row.price = round(float(live.price), 4)
        if live.change_pct is not None and _finite(live.change_pct):
            row.change_pct = round(float(live.change_pct), 4)
        if row.source:
            row.source = f"{row.source}; live_price={live.source}"
        else:
            row.source = f"live_price={live.source}"
        applied += 1
    return applied


async def fetch_market_scanner_live_prices(
    symbols: list[str],
    live_price_provider: LivePriceProvider | None = None,
) -> dict[str, ScannerLivePrice]:
    default_provider_selected = live_price_provider is None
    provider = live_price_provider or _fetch_live_price_for_scanner
    supports_fmp_prefetch = provider is _DEFAULT_FMP_LIVE_PRICE_PROVIDER
    provider_key = _provider_cache_key(provider)
    unique: list[str] = []
    seen: set[str] = set()
    for raw in symbols:
        symbol = str(raw).upper().strip()
        if symbol and symbol not in seen:
            seen.add(symbol)
            unique.append(symbol)

    try:
        from backend.layer_1_data.real_time_ws.scanner_ws_manager import scanner_ws_manager

        asyncio.create_task(scanner_ws_manager.subscribe_symbols(unique))
    except Exception as exc:
        logger.debug("market_scanner.ws_manager_subscribe_failed error=%s", str(exc)[:180])

    bulk_fmp_quotes: dict[str, Any] = {}
    bulk_fmp_aftermarket_trades: dict[str, Any] = {}
    bulk_fmp_aftermarket_quotes: dict[str, Any] = {}
    fmp_client = None
    if supports_fmp_prefetch:
        try:
            from backend.layer_1_data.fetchers.fmp_client import FMPClient

            fmp_client = FMPClient(timeout=SCANNER_FMP_HTTP_TIMEOUT)
            symbols_to_fetch: list[str] = []
            now = time.monotonic()
            for symbol in unique:
                cache_key = (provider_key, symbol)
                cached = _LIVE_PRICE_CACHE.get(cache_key)
                if not (
                    cached is not None and now - cached[0] <= SCANNER_LIVE_PRICE_CACHE_TTL_SECONDS
                ):
                    symbols_to_fetch.append(symbol)
            if symbols_to_fetch:
                chunks = [
                    symbols_to_fetch[i : i + SCANNER_FMP_BATCH_SIZE]
                    for i in range(0, len(symbols_to_fetch), SCANNER_FMP_BATCH_SIZE)
                ]
                for chunk in chunks:
                    quote_result, trade_result, after_quote_result = await asyncio.gather(
                        fmp_client.get_quotes(chunk),
                        fmp_client.get_aftermarket_trades(chunk),
                        fmp_client.get_aftermarket_quotes(chunk),
                        return_exceptions=True,
                    )
                    if isinstance(quote_result, dict):
                        bulk_fmp_quotes.update(quote_result)
                    if isinstance(trade_result, dict):
                        bulk_fmp_aftermarket_trades.update(trade_result)
                    if isinstance(after_quote_result, dict):
                        bulk_fmp_aftermarket_quotes.update(after_quote_result)
        except Exception as exc:
            logger.debug("market_scanner.bulk_fmp_fetch_failed error=%s", str(exc)[:180])

    async def _fetch_provider_live(symbol: str) -> ScannerLivePrice | None:
        cache_key = (provider_key, symbol)
        now = time.monotonic()
        cached = _LIVE_PRICE_CACHE.get(cache_key)
        if cached is not None:
            cached_at, live = cached
            if now - cached_at <= SCANNER_LIVE_PRICE_CACHE_TTL_SECONDS:
                try:
                    from backend.layer_1_data.real_time_ws.scanner_ws_manager import (
                        scanner_ws_manager,
                    )

                    scanner_ws_manager.update_change_pct(symbol, live.change_pct)
                except Exception:
                    pass
                return live
        rd = redis_get_live_price(provider_key, symbol)
        if rd is not None:
            live_redis = _live_price_from_redis_dict(rd)
            if live_redis is not None:
                _LIVE_PRICE_CACHE[cache_key] = (time.monotonic(), live_redis)
                return live_redis
        try:
            if supports_fmp_prefetch:
                live = await asyncio.wait_for(
                    provider(
                        symbol,
                        prefetched_quote=bulk_fmp_quotes.get(symbol),
                        prefetched_aftermarket_trade=bulk_fmp_aftermarket_trades.get(symbol),
                        prefetched_aftermarket_quote=bulk_fmp_aftermarket_quotes.get(symbol),
                        fmp_client=fmp_client,
                    ),
                    timeout=SCANNER_LIVE_PRICE_TIMEOUT_SECONDS,
                )
            else:
                live = await asyncio.wait_for(
                    provider(symbol),
                    timeout=SCANNER_LIVE_PRICE_TIMEOUT_SECONDS,
                )
        except Exception as exc:
            logger.debug(
                "market_scanner.live_price_batch_failed symbol=%s error=%s",
                symbol,
                str(exc)[:180],
            )
            return None
        if live is None or not _finite(live.price) or live.price <= 0:
            return None
        _LIVE_PRICE_CACHE[cache_key] = (time.monotonic(), live)
        ttl = int(SCANNER_LIVE_PRICE_CACHE_TTL_SECONDS)
        if ttl > 0:
            redis_set_live_price(provider_key, symbol, asdict(live), ttl)

        try:
            from backend.layer_1_data.real_time_ws.scanner_ws_manager import scanner_ws_manager

            scanner_ws_manager.update_change_pct(symbol, live.change_pct)
        except Exception:
            pass

        return live

    async def _fetch(symbol: str) -> tuple[str, ScannerLivePrice | None]:
        if default_provider_selected:
            rest_live = await _fetch_provider_live(symbol)
            if rest_live is not None:
                return symbol, rest_live

        # 1. WS Manager Cache
        try:
            from backend.layer_1_data.real_time_ws.scanner_ws_manager import scanner_ws_manager

            ws_live = scanner_ws_manager.get_price(symbol)
            if ws_live is not None and ws_live.price is not None and ws_live.price > 0:
                if ws_live.change_pct is None:
                    # Attempt to fill change_pct from generated candle
                    generated = _generated_candle_live_price(symbol)
                    if generated and generated.change_pct is not None:
                        ws_live.change_pct = generated.change_pct
                        scanner_ws_manager.update_change_pct(symbol, generated.change_pct)

                return symbol, ws_live
        except Exception:
            pass

        # 2. Generated candle overlay from local WS/rest chart streams.
        generated_live = _generated_candle_live_price(symbol)
        if generated_live is not None:
            return symbol, generated_live

        # 3. REST Cache / Live Fetch
        return symbol, await _fetch_provider_live(symbol)

    results = await asyncio.gather(*(_fetch(symbol) for symbol in unique))
    return {symbol: live for symbol, live in results if live is not None}


def _generated_candle_live_price(symbol: str) -> ScannerLivePrice | None:
    try:
        from backend.layer_1_data.real_time_ws.generated_candle_store import (
            get_generated_candle_store,
        )
    except Exception as exc:
        logger.debug(
            "market_scanner.generated_candle_store_unavailable symbol=%s error=%s",
            symbol,
            str(exc)[:180],
        )
        return None

    now_ms = int(time.time() * 1000)
    max_age_ms = SCANNER_GENERATED_CANDLE_PRICE_MAX_AGE_SECONDS * 1000
    candidates: list[tuple[int, int, ScannerLivePrice]] = []
    store = get_generated_candle_store()
    for timeframe in SCANNER_GENERATED_CANDLE_TIMEFRAMES:
        snapshot = store.snapshot(symbol, timeframe)
        if snapshot is None or not snapshot.candles:
            continue
        last_time = int(snapshot.last_candle_time or 0)
        if last_time <= 0 or last_time > now_ms + 5 * 60 * 1000:
            continue
        if now_ms - last_time > max_age_ms:
            continue
        last = snapshot.candles[-1]
        try:
            price = float(last["close"])
        except (KeyError, TypeError, ValueError):
            continue
        if not _finite(price) or price <= 0:
            continue
        previous = _previous_generated_close(snapshot.candles)
        change_pct = _pct(price, previous) if previous is not None else None
        source = snapshot.source or "generated_candles"
        source_rank = 1 if "ws" in source.lower() or snapshot.live_partial_bar else 0
        candidates.append(
            (
                last_time,
                source_rank,
                ScannerLivePrice(
                    price=price,
                    change_pct=change_pct,
                    source=f"generated_candles:{snapshot.timeframe}:{source}",
                    timestamp_ms=last_time,
                ),
            )
        )
    if not candidates:
        return None
    return max(candidates, key=lambda item: (item[0], item[1]))[2]


def _previous_generated_close(candles: list[dict[str, Any]]) -> float | None:
    for row in reversed(candles[:-1]):
        try:
            close = float(row["close"])
        except (KeyError, TypeError, ValueError):
            continue
        if _finite(close) and close > 0:
            return close
    return None


async def _get_cached_options_snapshot(
    symbol: str,
    options_snapshot_provider: OptionsSnapshotProvider,
) -> object | None:
    provider_key = _provider_cache_key(options_snapshot_provider)
    cache_key = (provider_key, symbol.upper())
    now = time.monotonic()
    cached = _OPTIONS_SNAPSHOT_CACHE.get(cache_key)
    if cached is not None:
        cached_at, snapshot = cached
        if now - cached_at <= SCANNER_OPTIONS_CACHE_TTL_SECONDS:
            return snapshot
    snapshot = await options_snapshot_provider(symbol)
    _OPTIONS_SNAPSHOT_CACHE[cache_key] = (time.monotonic(), snapshot)
    return snapshot


async def _fetch_live_price_for_scanner(
    symbol: str,
    prefetched_quote: Any = None,
    prefetched_aftermarket_trade: Any = None,
    prefetched_aftermarket_quote: Any = None,
    fmp_client: Any = None,
) -> ScannerLivePrice | None:
    # 1. FMP Client (Primary for After-Market / Real-Time Institutional)
    try:
        from backend.layer_1_data.fetchers.fmp_client import FMPClient

        fmp = fmp_client or FMPClient(timeout=SCANNER_FMP_HTTP_TIMEOUT)

        fmp_quote = prefetched_quote
        extended_trade = prefetched_aftermarket_trade

        if fmp_quote is None and extended_trade is None:
            gathered_results = await asyncio.gather(
                fmp.get_quote(symbol),
                fmp.get_aftermarket_trade(symbol),
                return_exceptions=True,
            )
            quote_result: Any = gathered_results[0]
            trade_result: Any = gathered_results[1]
            fmp_quote = None if isinstance(quote_result, Exception) else quote_result
            extended_trade = None if isinstance(trade_result, Exception) else trade_result
        elif fmp_quote is None:
            try:
                fmp_quote = await fmp.get_quote(symbol)
            except Exception:
                fmp_quote = None
        elif extended_trade is None:
            try:
                extended_trade = await fmp.get_aftermarket_trade(symbol)
            except Exception:
                extended_trade = None
        previous_close = (
            float(fmp_quote.previousClose)
            if fmp_quote is not None
            and getattr(fmp_quote, "previousClose", None) is not None
            and _finite(fmp_quote.previousClose)
            and fmp_quote.previousClose > 0
            else None
        )
        extended_price = None
        extended_timestamp_raw: object = None
        if (
            extended_trade is not None
            and getattr(extended_trade, "price", None) is not None
            and _finite(extended_trade.price)
            and extended_trade.price > 0
        ):
            extended_price = float(extended_trade.price)
            extended_timestamp_raw = getattr(extended_trade, "timestamp", None)
        extended_quote = prefetched_aftermarket_quote
        if extended_price is None:
            if extended_quote is None:
                extended_quote = await fmp.get_aftermarket_quote(symbol)
            extended_price = _extended_quote_midpoint(extended_quote)
            extended_timestamp_raw = getattr(extended_quote, "timestamp", None)
        if extended_price is not None and extended_price > 0:
            change_pct = _pct(extended_price, previous_close) if previous_close else None
            timestamp_ms = _fmp_timestamp_ms(extended_timestamp_raw)
            return ScannerLivePrice(
                price=extended_price,
                change_pct=change_pct,
                source="fmp_extended",
                timestamp_ms=timestamp_ms,
            )
        if (
            fmp_quote is not None
            and getattr(fmp_quote, "price", None) is not None
            and fmp_quote.price > 0
        ):
            return ScannerLivePrice(
                price=float(fmp_quote.price),
                change_pct=(
                    float(fmp_quote.changesPercentage)
                    if getattr(fmp_quote, "changesPercentage", None) is not None
                    else None
                ),
                source="fmp_quote",
                timestamp_ms=(
                    int(fmp_quote.timestamp * 1000)
                    if getattr(fmp_quote, "timestamp", None)
                    else None
                ),
            )
    except Exception as exc:
        logger.debug(
            "market_scanner.fmp_live_price_failed symbol=%s error=%s", symbol, str(exc)[:180]
        )

    # 2. Polygon Client (Fallback, might return end-of-day close on basic tier)
    try:
        from backend.layer_1_data.fetchers.polygon_client import PolygonClient

        quote = await PolygonClient(timeout=SCANNER_LIVE_PRICE_TIMEOUT_SECONDS).get_quote(symbol)
    except Exception as exc:
        logger.debug(
            "market_scanner.live_price_snapshot_failed symbol=%s error=%s",
            symbol,
            str(exc)[:180],
        )
        return None
    if quote is None or not _finite(quote.price) or quote.price <= 0:
        return None
    return ScannerLivePrice(
        price=float(quote.price),
        change_pct=float(quote.change_pct) if quote.change_pct is not None else None,
        source="polygon_snapshot",
        timestamp_ms=quote.timestamp,
    )


_DEFAULT_FMP_LIVE_PRICE_PROVIDER = _fetch_live_price_for_scanner


def _extended_quote_midpoint(quote: object | None) -> float | None:
    if quote is None:
        return None
    bid_float = _positive_float(getattr(quote, "bid", None))
    ask_float = _positive_float(getattr(quote, "ask", None))
    if bid_float is not None and ask_float is not None:
        return (bid_float + ask_float) / 2.0
    return bid_float or ask_float


def _positive_float(raw: object) -> float | None:
    try:
        value = float(raw) if raw is not None else None
    except (TypeError, ValueError):
        return None
    return value if value is not None and _finite(value) and value > 0 else None


def _fmp_timestamp_ms(raw: object) -> int | None:
    if raw is None:
        return None
    if isinstance(raw, int | float):
        value = int(raw)
        return value if value > 10_000_000_000 else value * 1000
    text = str(raw).strip()
    if not text:
        return None
    if text.isdigit():
        value = int(text)
        return value if value > 10_000_000_000 else value * 1000
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return int(parsed.timestamp() * 1000)


def _row_reasons(
    direction: ScannerBias,
    aligned: int,
    usable: list[MarketScannerTimeframeSignal],
    vetoes: list[str],
) -> list[str]:
    reasons: list[str] = []
    if direction == "bullish" and aligned >= 3:
        reasons.append("Multi-timeframe bullish alignment")
    elif direction == "bearish" and aligned >= 3:
        reasons.append("Multi-timeframe bearish alignment")
    elif aligned >= 2:
        reasons.append("Partial timeframe alignment")
    if any(
        (rvol := _metric_float(signal, "relative_volume")) is not None and rvol > 1.2
        for signal in usable
    ):
        reasons.append("Volume confirms move")
    if any("Price above VWAP" in signal.reasons for signal in usable):
        reasons.append("VWAP confluence")
    if vetoes:
        reasons.append("Hard veto present")
    return reasons[:6]


def _fusion_weight_for_timeframe(
    indicator_key: str,
    customization: ScannerCustomization,
    indicators: list[ScannerIndicatorDefinition],
    timeframe: str,
    *,
    effective_weights: dict[str, dict[str, float]] | None,
    legacy_resolver: Callable[..., float],
) -> float:
    if institutional_scoring_enabled() and effective_weights:
        w = effective_weights.get(indicator_key, {}).get(timeframe, 0.0)
        if w > 0:
            return float(w)
    return float(legacy_resolver(customization, indicators, timeframe))


def _weighted_timeframe_score(
    usable: list[MarketScannerTimeframeSignal],
    customization: ScannerCustomization,
    *,
    effective_weights: dict[str, dict[str, float]] | None = None,
) -> float:
    if not usable:
        return 0.0
    if institutional_scoring_enabled() and effective_weights:
        total_weight = 0.0
        weighted = 0.0
        for signal in usable:
            tf_w = timeframe_weight_sum(effective_weights, signal.timeframe)
            if tf_w <= 0:
                tf_w = _aggregate_timeframe_weight(signal.timeframe, customization)
            total_weight += tf_w
            weighted += signal.score * tf_w
        if total_weight <= 0:
            return float(np.mean([signal.score for signal in usable]))
        return weighted / total_weight

    weights = [_aggregate_timeframe_weight(signal.timeframe, customization) for signal in usable]
    total_weight = sum(weights)
    if total_weight <= 0:
        return float(np.mean([signal.score for signal in usable]))
    weighted = sum(signal.score * weight for signal, weight in zip(usable, weights, strict=True))
    return weighted / total_weight


def _aggregate_timeframe_weight(
    timeframe: str,
    customization: ScannerCustomization,
) -> float:
    """Legacy: mean indicator weight for a timeframe (used when institutional path inactive)."""
    if not customization.weight_matrix:
        return 1.0
    weights = [
        values[timeframe]
        for indicator, values in customization.weight_matrix.items()
        if timeframe in values
        and (
            customization.enabled_indicators is None
            or indicator in customization.enabled_indicators
        )
    ]
    return sum(weights) / len(weights) if weights else 1.0


def _get_adv_from_deep_metrics(row: MarketScannerRow) -> float | None:
    if not row.deep_metrics:
        return None
    for tf_data in row.deep_metrics.values():
        if isinstance(tf_data, dict):
            vol = tf_data.get("volume_avg") or tf_data.get("adv")
            if vol is not None:
                return float(vol)
    return None


def _get_rvol(row: MarketScannerRow) -> float | None:
    """Best-effort relative volume from timeframe signals."""
    for signal in row.signals.values():
        if signal is not None and isinstance(signal.metrics, dict):
            rv = signal.metrics.get("relative_volume")
            if rv is not None:
                try:
                    return float(rv)
                except (TypeError, ValueError):
                    continue
    return None


def apply_hard_vetoes(row: MarketScannerRow, request: MarketScannerRequest) -> MarketScannerRow:
    primary_signal = row.signals.get(request.customization.primary_timeframe or "15m")
    if primary_signal is None or primary_signal.score is None:
        row.vetoes.append(VETO_NO_DATA)
        row.setup_grade = "VETO"
        return row

    if row.price is not None and row.price < 1.0:
        row.vetoes.append(VETO_ILLIQUID)
        row.setup_grade = "VETO"
        return row

    adv = _get_adv_from_deep_metrics(row)
    if adv is not None and adv < 100_000:
        row.vetoes.append(VETO_ILLIQUID)
        row.setup_grade = "VETO"
        return row

    available_signals = [
        s for s in row.signals.values() if s is not None and s.direction is not None
    ]
    request_direction = request.direction

    if request_direction != "both" and len(available_signals) >= 3:
        opposite_direction = "bearish" if request_direction == "long" else "bullish"
        opposing = [
            s
            for s in available_signals
            if s.direction == opposite_direction and getattr(s, "confidence", 0.0) > 0.70
        ]
        if len(opposing) >= 3 and len(opposing) == len(available_signals):
            row.vetoes.append(VETO_COMPLETE_CONTRADICTION)
            row.setup_grade = "VETO"
            return row

    deep = row.deep_metrics or {}
    primary_tf = request.customization.primary_timeframe or "15m"
    tf_metrics = deep.get(primary_tf, {})

    rsi_val = tf_metrics.get("rsi")
    rvol_val = tf_metrics.get("relative_volume") or _get_rvol(row)
    atr_pct = tf_metrics.get("atr_pct")
    change = abs(row.change_pct or 0.0) / 100.0

    if rsi_val is not None and rvol_val is not None and atr_pct is not None:
        rsi_exhausted = (
            (request_direction == "long" and rsi_val > 88)
            or (request_direction == "short" and rsi_val < 12)
            or (request_direction == "both" and (rsi_val > 88 or rsi_val < 12))
        )
        volume_dead = rvol_val < 0.4
        atr_spike = change > (3.0 * atr_pct / 100.0) if atr_pct > 0 else False

        if rsi_exhausted and volume_dead and atr_spike:
            row.vetoes.append(VETO_EXTREME_EXHAUSTION)
            row.setup_grade = "VETO"
            return row

    return row


def _filter_rows(
    rows: list[MarketScannerRow], request: MarketScannerRequest
) -> list[MarketScannerRow]:
    filters = request.filters
    out: list[MarketScannerRow] = []
    for row in rows:
        if row.scanner_score < filters.min_score:
            continue
        if row.price is not None and row.price < filters.min_price:
            continue

        primary = _latest_signal([signal for signal in row.signals.values() if signal.ok])
        volume = _metric_float(primary, "volume") or 0.0
        rvol = _metric_float(primary, "relative_volume") or 0.0

        if volume < filters.min_volume:
            row.warnings.append("WARN_LOW_VOLUME")
        if rvol < filters.min_relative_volume:
            row.warnings.append(WARN_LOW_RVOL)

        row = apply_hard_vetoes(row, request)

        if row.vetoes and not filters.include_vetoed:
            continue
        if request.direction == "long" and row.direction == "bearish":
            continue
        if request.direction == "short" and row.direction == "bullish":
            continue

        out.append(row)
    return out


def _sort_rows(
    rows: list[MarketScannerRow], request: MarketScannerRequest
) -> list[MarketScannerRow]:
    if request.sort == "symbol":
        return sorted(rows, key=lambda row: row.symbol)
    if request.sort == "change_pct":
        return sorted(rows, key=lambda row: row.change_pct or -9999.0, reverse=True)
    if request.sort == "relative_volume":
        return sorted(
            rows,
            key=lambda row: (
                _metric_float(
                    _latest_signal([s for s in row.signals.values() if s.ok]), "relative_volume"
                )
                or 0.0
            ),
            reverse=True,
        )
    pt = request.customization.primary_timeframe
    if pt in ("5m", "15m"):
        return sorted(rows, key=lambda row: row.intraday_score, reverse=True)
    if pt in ("1h", "1D"):
        return sorted(rows, key=lambda row: row.swing_score, reverse=True)
    if request.sort == "universe_percentile":
        return sorted(
            rows,
            key=lambda row: (
                row.universe_percentile if row.universe_percentile is not None else -1.0
            ),
            reverse=True,
        )
    if request.sort == "regime_fit_score":
        return sorted(
            rows,
            key=lambda row: row.regime_fit_score if row.regime_fit_score is not None else -1.0,
            reverse=True,
        )
    if request.sort == "conviction_score":
        return sorted(
            rows,
            key=lambda row: row.conviction_score if row.conviction_score is not None else -1.0,
            reverse=True,
        )
    if request.sort == "capacity_score":
        return sorted(
            rows,
            key=lambda row: (
                row.capacity_signals.capacity_score if row.capacity_signals is not None else -1.0
            ),
            reverse=True,
        )
    return sorted(rows, key=lambda row: row.scanner_score, reverse=True)


def _symbols_for_request(request: MarketScannerRequest) -> list[str]:
    if request.symbols:
        return request.symbols
    universe = DEFAULT_UNIVERSES.get(request.universe)
    if universe is None:
        return []
    return list(universe[1])


def _provider_cache_key(provider: object) -> str:
    module = getattr(provider, "__module__", provider.__class__.__module__)
    name = getattr(provider, "__qualname__", provider.__class__.__qualname__)
    return f"{module}.{name}:{id(provider)}"


def _feature_freshness(request: MarketScannerRequest) -> dict[str, int | float | str | bool]:
    return {
        "catalog_version": CATALOG_VERSION,
        "ohlcv_cache_ttl_seconds": int(SCANNER_PROVIDER_CACHE_TTL_SECONDS),
        "live_price_cache_ttl_seconds": int(SCANNER_LIVE_PRICE_CACHE_TTL_SECONDS),
        "options_snapshot_cache_ttl_seconds": int(SCANNER_OPTIONS_CACHE_TTL_SECONDS),
        "primary_timeframe": request.customization.primary_timeframe or "auto",
        "deep_metrics": bool(request.include_deep_metrics),
    }


def _estimate_scanner_cost(
    request: MarketScannerRequest,
    *,
    symbols: list[str],
    rows_after_filter: int,
    phase_b_limit: int,
    live_price_rows: int,
) -> dict[str, int | float | str | bool]:
    enabled_modules = set(request.customization.enabled_modules or [])
    include_all = request.customization.enabled_modules is None
    options_enabled = include_all or "options_gex" in enabled_modules
    fundamentals_enabled = include_all or "fundamentals" in enabled_modules
    optionable_rows = [
        symbol for symbol in symbols[:phase_b_limit] if not symbol.upper().endswith("USD")
    ]
    return {
        "estimated_ohlcv_requests": len(symbols) * len(request.timeframes),
        "estimated_live_price_requests": rows_after_filter,
        "estimated_phase_b_rows": phase_b_limit,
        "estimated_options_snapshot_requests": len(optionable_rows) if options_enabled else 0,
        "estimated_fundamentals_requests": (
            min(phase_b_limit, len(symbols)) if fundamentals_enabled else 0
        ),
        "actual_live_price_rows": live_price_rows,
        "timeframes": len(request.timeframes),
        "symbols": len(symbols),
        "options_enabled": bool(options_enabled),
        "cache_policy": "runtime_ttl",
    }


def _coerce_bar(row: dict[str, Any]) -> dict[str, float] | None:
    try:
        raw_close = row.get("close", row.get("c"))
        if raw_close is None:
            return None
        close = float(raw_close)
        open_price = float(row.get("open", row.get("o", close)))
        high = float(row.get("high", row.get("h", close)))
        low = float(row.get("low", row.get("l", close)))
        volume = float(row.get("volume", row.get("v", 0.0)) or 0.0)
    except (TypeError, ValueError):
        return None
    if min(open_price, high, low, close) <= 0 or not all(
        map(math.isfinite, (open_price, high, low, close))
    ):
        return None
    return {
        "open": open_price,
        "high": high,
        "low": low,
        "close": close,
        "volume": max(volume, 0.0),
    }


def _sparkline(bars: list[dict[str, Any]]) -> list[float]:
    values: list[float] = []
    for row in bars[-18:]:
        coerced = _coerce_bar(row)
        if coerced is not None:
            values.append(round(coerced["close"], 4))
    return values


def _provider_timeframe(timeframe: str) -> str:
    return "1d" if str(timeframe).lower() in {"1d", "1D".lower()} else str(timeframe)


def _public_timeframe(timeframe: str) -> ScannerTimeframe:
    return cast(ScannerTimeframe, "1D" if str(timeframe).lower() == "1d" else str(timeframe))


def _lookback_days(interval: str) -> int:
    return {"5m": 10, "15m": 21, "1h": 90, "1d": 365}.get(interval, 21)


def _last(values: np.ndarray) -> float:
    finite = values[np.isfinite(values)]
    return float(finite[-1]) if len(finite) else float("nan")


def _finite(value: float | None) -> bool:
    return value is not None and math.isfinite(float(value))


def _pct(new: float, old: float) -> float:
    return ((new - old) / old * 100.0) if old else 0.0


def _customization_has_phase_a_weights(customization: ScannerCustomization) -> bool:
    """True when the user explicitly customized Phase-A indicator weights."""
    if customization.enabled_indicators is not None:
        return True
    if customization.weight_matrix:
        return any(key in PHASE_A_INDICATOR_KEYS for key in customization.weight_matrix)
    return False


def _label_for_score(score: float, direction: ScannerBias) -> ScannerSignalLabel:
    if direction == "bullish":
        return "strong_buy" if score >= 82 else "buy" if score >= 60 else "neutral"
    if direction == "bearish":
        return "strong_sell" if score <= 18 else "sell" if score <= 42 else "neutral"
    return "neutral"


def _latest_signal(
    signals: list[MarketScannerTimeframeSignal],
) -> MarketScannerTimeframeSignal | None:
    priority = {"15m": 0, "5m": 1, "1h": 2, "1D": 3}
    ordered = sorted(signals, key=lambda signal: priority.get(signal.timeframe, 99))
    return ordered[0] if ordered else None


def _metric_float(signal: MarketScannerTimeframeSignal | None, key: str) -> float | None:
    if signal is None:
        return None
    value = signal.metrics.get(key)
    if isinstance(value, int | float) and math.isfinite(float(value)):
        return float(value)
    return None
