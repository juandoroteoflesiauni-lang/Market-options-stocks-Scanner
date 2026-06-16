from __future__ import annotations
from typing import Any
"""Equity TA snapshot for the BingX analysis drawer.

This module is intentionally minimal. It exists so the cockpit can show
*degraded-but-honest* underlying TA when a BingX synthetic-stock perp (e.g.
``GOOGL-USDT`` → ``GOOGL``) is inspected, instead of leaving the field
permanently ``UNAVAILABLE: engine_not_wired``.

Survival-first contract:

* Every return value is a JSON-serialisable ``dict``.
* If no equity data source is available the service returns
  ``{"ok": False, "reason": "no_equity_data_source", "ticker": ticker}``
  — never a fabricated/mock metric. This keeps the *insufficient_data* gate
  upstream honest.
* The probabilistic helper computes an explainable equity-only heuristic
  (RSI / momentum / return z-score / ATR) over the same FMP daily history.
  When coverage is below 0.5 it returns ``{"ok": False, "reason":
  "low_data_quality", ...}`` rather than fabricating a probability. The
  ``confidence`` field is always degraded by missing features and by elevated
  ATR — it can never reach 1.0 if any feature failed.

The service does **not** authorise trading on its own. It is one of several
inputs the Risk Desk consumes — the funding-rule gate remains the final
authorizer.
"""


import math

from backend.config.logger_setup import get_logger
from backend.layer_1_data.fetchers.fmp_client import FMPClient

logger = get_logger(__name__)

# Module-level shared FMP client. Tests should swap the client via constructor
# injection rather than mutating this singleton.
_DEFAULT_FMP_CLIENT: FMPClient | None = None


def _get_default_fmp_client() -> FMPClient:
    """Lazy singleton — defer instantiation so import never raises."""
    global _DEFAULT_FMP_CLIENT
    if _DEFAULT_FMP_CLIENT is None:
        _DEFAULT_FMP_CLIENT = FMPClient()
    return _DEFAULT_FMP_CLIENT


def _safe_float(value: object) -> float | None:
    try:
        out = float(value)  # type: ignore[arg-type]
        return out if math.isfinite(out) else None
    except (TypeError, ValueError):
        return None


def _rsi_wilder(closes: list[float], length: int = 14) -> float | None:
    """Compute Wilder's RSI without numpy/pandas to keep the dependency
    surface tiny. Returns ``None`` if there are not enough bars or all moves
    are flat (degenerate division)."""
    if len(closes) < length + 1:
        return None
    gains = 0.0
    losses = 0.0
    # Seed average (first ``length`` deltas)
    for i in range(1, length + 1):
        delta = closes[i] - closes[i - 1]
        if delta >= 0:
            gains += delta
        else:
            losses -= delta
    avg_gain = gains / length
    avg_loss = losses / length
    # Wilder smoothing for the remaining bars
    for i in range(length + 1, len(closes)):
        delta = closes[i] - closes[i - 1]
        gain = delta if delta > 0 else 0.0
        loss = -delta if delta < 0 else 0.0
        avg_gain = (avg_gain * (length - 1) + gain) / length
        avg_loss = (avg_loss * (length - 1) + loss) / length
    if avg_loss == 0 and avg_gain == 0:
        return None
    if avg_loss == 0:
        return 100.0
    rs = avg_gain / avg_loss
    rsi = 100.0 - (100.0 / (1.0 + rs))
    return _safe_float(rsi)


def _ema(closes: list[float], length: int) -> float | None:
    """Plain EMA over the closes list. Returns ``None`` when there are not
    enough bars to seed the running average."""
    if len(closes) < length or length <= 0:
        return None
    k = 2.0 / (length + 1.0)
    # Seed with the simple mean of the first ``length`` closes.
    ema = sum(closes[:length]) / length
    for price in closes[length:]:
        ema = price * k + ema * (1.0 - k)
    return _safe_float(ema)


def _trend_from_emas(ema_fast: float | None, ema_slow: float | None) -> str:
    if ema_fast is None or ema_slow is None:
        return "neutral"
    if ema_fast > ema_slow:
        return "bullish"
    if ema_fast < ema_slow:
        return "bearish"
    return "neutral"


class EquityTASnapshotService:
    """Compute a small, honest TA snapshot for an equity ticker.

    The service prefers FMP daily history when the client has at least one
    API key configured. If FMP is not active *or* returns no data, the
    service degrades to ``{"ok": False, "reason": "no_equity_data_source"}``
    so the caller never displays a fabricated metric.
    """

    def __init__(
        self,
        ticker: str,
        *,
        fmp_client: FMPClient | None = None,
        bars_to_request: int = 200,
        ema_fast_length: int = 9,
        ema_slow_length: int = 21,
        rsi_length: int = 14,
    ) -> None:
        self._ticker = (ticker or "").upper().strip()
        self._fmp_client = fmp_client  # may be None — resolve lazily
        self._bars_to_request = max(50, bars_to_request)
        self._ema_fast_length = ema_fast_length
        self._ema_slow_length = ema_slow_length
        self._rsi_length = rsi_length
        # Cache of the most recent chronological OHLC bars used to compute the
        # last snapshot. Consumed by ``equity_probabilistic_summary`` so it can
        # reuse the same fetch without hitting FMP twice. Each entry is a plain
        # dict ``{"open", "high", "low", "close", "date"}`` — values may be
        # ``None`` if the upstream record was incomplete.
        self.last_bars: list[dict[str, Any]] = []

    @property
    def ticker(self) -> str:
        return self._ticker

    def _resolve_client(self) -> FMPClient:
        return self._fmp_client if self._fmp_client is not None else _get_default_fmp_client()

    @staticmethod
    def _unavailable(ticker: str, reason: str) -> dict[str, Any]:
        return {"ok": False, "ticker": ticker, "reason": reason}

    async def snapshot(self) -> dict[str, Any]:
        """Return a TA snapshot dict. Never raises — degrades to ``ok=False``."""
        ticker = self._ticker
        if not ticker:
            return self._unavailable(ticker, "invalid_ticker")

        client = self._resolve_client()
        if not client._is_active():
            logger.info("equity_ta.fmp_inactive ticker=%s", ticker)
            return self._unavailable(ticker, "no_equity_data_source")

        try:
            history = await client.get_historical_prices(ticker)
        except Exception as exc:
            logger.warning("equity_ta.fmp_fetch_failed ticker=%s error=%s", ticker, str(exc)[:180])
            return self._unavailable(ticker, "equity_data_fetch_failed")

        if not history:
            logger.info("equity_ta.fmp_empty ticker=%s", ticker)
            return self._unavailable(ticker, "no_equity_data_source")

        # FMP returns most-recent first. Reverse to chronological order and
        # prefer the adjusted close when available. We also keep a full OHLC
        # mirror in ``self.last_bars`` so callers (e.g. the probabilistic
        # summary) can reuse the fetch and compute ATR/momentum/z-score without
        # a second network round-trip.
        closes: list[float] = []
        bars: list[dict[str, Any]] = []
        for row in reversed(history):
            raw_close = getattr(row, "adjClose", None)
            if raw_close is None:
                raw_close = getattr(row, "close", None)
            close_value = _safe_float(raw_close)
            if close_value is None:
                continue
            closes.append(close_value)
            bars.append(
                {
                    "date": getattr(row, "date", None),
                    "open": _safe_float(getattr(row, "open", None)),
                    "high": _safe_float(getattr(row, "high", None)),
                    "low": _safe_float(getattr(row, "low", None)),
                    "close": close_value,
                }
            )

        # Cap to the same working window we use for indicators so that the
        # cached bars match what the snapshot was computed from.
        self.last_bars = bars[-self._bars_to_request :]

        # Need enough bars for RSI(14) plus a seed.
        required = max(self._rsi_length + 1, self._ema_slow_length)
        if len(closes) < required:
            logger.info(
                "equity_ta.insufficient_bars ticker=%s bars=%d required=%d",
                ticker,
                len(closes),
                required,
            )
            return self._unavailable(ticker, "insufficient_bars")

        # Cap the working window — RSI/EMA already converge with ~200 bars.
        window = closes[-self._bars_to_request :]
        rsi = _rsi_wilder(window, length=self._rsi_length)
        ema_fast = _ema(window, length=self._ema_fast_length)
        ema_slow = _ema(window, length=self._ema_slow_length)
        trend = _trend_from_emas(ema_fast, ema_slow)

        return {
            "ok": True,
            "ticker": ticker,
            "rsi_14": rsi,
            "ema_fast": ema_fast,
            "ema_slow": ema_slow,
            "trend_direction": trend,
            "source": "fmp",
            "bars_used": len(window),
        }


# ─────────────────────────────────────────────────────────────────────────────
# Equity-only feature pipeline + heuristic probabilistic summary
# ─────────────────────────────────────────────────────────────────────────────
#
# These helpers turn a list of daily OHLC bars into a small, explainable
# probability triplet. There is no ML, no scaler, no calibrated meta-learner
# behind this — only RSI / momentum / return z-score / ATR. The point is to
# expose a *honest* signal for the cockpit when the heavy probabilistic stack
# cannot be invoked (BingX equity perp with no chain/macro/sentiment context),
# while keeping the survival contract intact:
#
#   * Coverage tracks how many of the target features were actually computed.
#     If a feature raises, it becomes ``None`` and coverage degrades.
#   * If coverage < 0.5 the summary returns ``ok=False`` with reason
#     ``low_data_quality`` instead of fabricating a probability.
#   * Confidence is bounded by coverage and degraded by elevated ATR. It can
#     never reach 1.0 if any feature is missing.
#
# Bar tiers (governs the *target* feature count, against which coverage is
# measured):
#   * < 15 bars  → 0 features computable                → coverage = 0.0
#   * 15-24 bars → RSI + momentum (target = 2)
#   * >= 25 bars → RSI + momentum + zscore + atr (target = 4)


_TIER_FULL_MIN_BARS = 25
_TIER_LIGHT_MIN_BARS = 15


def _bar_close(bar: dict[str, Any]) -> float | None:
    return _safe_float(bar.get("close"))


def _bar_high(bar: dict[str, Any]) -> float | None:
    return _safe_float(bar.get("high"))


def _bar_low(bar: dict[str, Any]) -> float | None:
    return _safe_float(bar.get("low"))


def _closes_from_bars(bars: list[dict[str, Any]]) -> list[float]:
    out: list[float] = []
    for bar in bars:
        value = _bar_close(bar)
        if value is not None:
            out.append(value)
    return out


def _momentum_pct(closes: list[float], lookback: int = 10) -> float | None:
    """Percent change between ``closes[-1]`` and ``closes[-1 - lookback]``."""
    if lookback <= 0 or len(closes) < lookback + 1:
        return None
    base = closes[-1 - lookback]
    if not math.isfinite(base) or base == 0.0:
        return None
    return _safe_float((closes[-1] - base) / base * 100.0)


def _return_zscore(closes: list[float], window: int = 20) -> float | None:
    """Z-score of the latest daily return vs the prior ``window`` returns."""
    if window <= 1 or len(closes) < window + 2:
        return None
    returns: list[float] = []
    for i in range(1, len(closes)):
        prev = closes[i - 1]
        if not math.isfinite(prev) or prev == 0.0:
            return None
        returns.append((closes[i] - prev) / prev)
    if len(returns) < window + 1:
        return None
    last_return = returns[-1]
    history = returns[-1 - window : -1]
    mean = sum(history) / window
    var = sum((r - mean) ** 2 for r in history) / window
    if var <= 0.0:
        return None
    std = math.sqrt(var)
    if std == 0.0:
        return None
    return _safe_float((last_return - mean) / std)


def _atr_normalized_pct(bars: list[dict[str, Any]], length: int = 14) -> float | None:
    """Wilder ATR normalised by the latest close, expressed as a percent.

    Returns ``None`` if there are not enough bars or any required HLC field is
    missing. We never invent a value when the upstream data is incomplete.
    """
    if length <= 0 or len(bars) < length + 1:
        return None
    last_close = _bar_close(bars[-1])
    if last_close is None or last_close <= 0:
        return None

    # Compute True Range series. ATR requires high/low/prev_close.
    trs: list[float] = []
    for i in range(1, len(bars)):
        high = _bar_high(bars[i])
        low = _bar_low(bars[i])
        prev_close = _bar_close(bars[i - 1])
        if high is None or low is None or prev_close is None:
            return None
        tr = max(high - low, abs(high - prev_close), abs(low - prev_close))
        trs.append(tr)
    if len(trs) < length:
        return None

    # Seed with simple mean of first ``length`` TRs, then Wilder smoothing.
    atr = sum(trs[:length]) / length
    for tr in trs[length:]:
        atr = (atr * (length - 1) + tr) / length

    if not math.isfinite(atr):
        return None
    return _safe_float(atr / last_close * 100.0)


def _compute_equity_features(bars: list[dict[str, Any]]) -> dict[str, Any]:
    """Compute a small equity-only feature dict from daily OHLC bars.

    Coverage is the fraction of *target* features that were successfully
    computed for the bar tier. A feature that raises or returns ``None``
    degrades coverage proportionally. Below 15 bars no feature is attempted —
    coverage is 0.0 and the caller is expected to degrade to ``ok=False``.
    """
    bar_count = len(bars)
    closes = _closes_from_bars(bars)

    if bar_count < _TIER_LIGHT_MIN_BARS or len(closes) < _TIER_LIGHT_MIN_BARS:
        return {
            "rsi_14": None,
            "momentum_10": None,
            "return_zscore_20": None,
            "atr_norm_14": None,
            "coverage": 0.0,
        }

    full_tier = bar_count >= _TIER_FULL_MIN_BARS and len(closes) >= _TIER_FULL_MIN_BARS
    target_features = 4 if full_tier else 2

    def _safe_call(fn: Any, *args: Any, **kwargs: Any) -> float | None:
        try:
            return fn(*args, **kwargs)
        except Exception as exc:
            logger.warning("equity_features.compute_failed fn=%s error=%s", fn.__name__, exc)
            return None

    rsi = _safe_call(_rsi_wilder, closes, length=14)
    momentum = _safe_call(_momentum_pct, closes, lookback=10)
    zscore: float | None = None
    atr_norm: float | None = None
    if full_tier:
        zscore = _safe_call(_return_zscore, closes, window=20)
        atr_norm = _safe_call(_atr_normalized_pct, bars, length=14)

    computed = 0
    if rsi is not None:
        computed += 1
    if momentum is not None:
        computed += 1
    if full_tier and zscore is not None:
        computed += 1
    if full_tier and atr_norm is not None:
        computed += 1

    coverage = computed / target_features if target_features > 0 else 0.0
    coverage = max(0.0, min(1.0, coverage))

    return {
        "rsi_14": rsi,
        "momentum_10": momentum,
        "return_zscore_20": zscore,
        "atr_norm_14": atr_norm,
        "coverage": coverage,
    }


def _clip(value: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, value))


def _features_to_probabilities(features: dict[str, Any]) -> dict[str, Any]:
    """Convert the equity features into a bull/bear/neutral probability triplet.

    Heuristic (conservative, fully explainable):

    * RSI > 60 → bullish; < 40 → bearish; else neutral.
    * momentum_10 > 3% → bullish; < -3% → bearish; else neutral.
    * return_zscore_20 > 1.5 → bullish; < -1.5 → bearish; else neutral.
    * atr_norm_14 > 3% → high volatility → confidence penalty of 0.15.

    Score is the sum of individual signals (range -3..+3). Probabilities are
    clipped to [0.05, 0.90]. Confidence is bounded by coverage and degraded
    by the ATR penalty — it can never reach 1.0 if any feature is missing or
    if volatility is elevated.
    """
    coverage = _safe_float(features.get("coverage")) or 0.0
    if coverage < 0.5:
        return {
            "ok": False,
            "reason": "low_data_quality",
            "coverage": coverage,
        }

    score = 0
    rsi = _safe_float(features.get("rsi_14"))
    if rsi is not None:
        if rsi > 60:
            score += 1
        elif rsi < 40:
            score -= 1

    momentum = _safe_float(features.get("momentum_10"))
    if momentum is not None:
        if momentum > 3.0:
            score += 1
        elif momentum < -3.0:
            score -= 1

    zscore = _safe_float(features.get("return_zscore_20"))
    if zscore is not None:
        if zscore > 1.5:
            score += 1
        elif zscore < -1.5:
            score -= 1

    atr_norm = _safe_float(features.get("atr_norm_14"))
    atr_penalty = 0.15 if (atr_norm is not None and atr_norm > 3.0) else 0.0

    bull_probability = _clip((3 + score) / 6.0, 0.05, 0.90)
    bear_probability = _clip((3 - score) / 6.0, 0.05, 0.90)
    neutral_probability = max(0.0, 1.0 - bull_probability - bear_probability)

    confidence = _clip(coverage * (1.0 - atr_penalty), 0.0, 1.0)

    return {
        "ok": True,
        "bull_probability": round(bull_probability, 4),
        "bear_probability": round(bear_probability, 4),
        "neutral_probability": round(neutral_probability, 4),
        "confidence": round(confidence, 4),
        "source": "equity_heuristic",
    }


async def equity_probabilistic_summary(ticker: str) -> dict[str, Any]:
    """Return an equity-only probabilistic summary from heuristic features.

    The summary is derived purely from FMP daily OHLC bars via
    ``_compute_equity_features`` + ``_features_to_probabilities``. There is no
    calibrated meta-learner behind this — it is an explainable, conservative
    heuristic designed to keep the cockpit honest:

    * If the snapshot is unavailable → returns ``ok=False`` with the snapshot's
      reason code (typically ``no_equity_data_source`` or ``insufficient_bars``).
    * If coverage < 0.5 → returns ``ok=False`` with reason ``low_data_quality``.
    * Otherwise → returns the probability triplet plus a ``features`` echo so
      the cockpit can render exactly which signals supported the score.

    The bars used for feature computation are reused from
    ``EquityTASnapshotService.last_bars`` to avoid a second FMP fetch.
    """
    clean = (ticker or "").upper().strip()
    if not clean:
        return {"ok": False, "ticker": clean, "reason": "invalid_ticker"}

    service = EquityTASnapshotService(clean)
    snapshot = await service.snapshot()
    if not snapshot.get("ok"):
        return {
            "ok": False,
            "ticker": clean,
            "reason": snapshot.get("reason", "no_equity_data_source"),
        }

    bars = service.last_bars
    features = _compute_equity_features(bars)
    coverage = _safe_float(features.get("coverage")) or 0.0
    if coverage < 0.5:
        return {
            "ok": False,
            "ticker": clean,
            "reason": "low_data_quality",
            "coverage": coverage,
        }

    probs = _features_to_probabilities(features)
    if not probs.get("ok"):
        # Defensive: _features_to_probabilities only signals ok=False when
        # coverage < 0.5, which we already screened. Surface verbatim so the
        # caller still sees a stable reason code.
        return {
            "ok": False,
            "ticker": clean,
            "reason": probs.get("reason", "low_data_quality"),
            "coverage": coverage,
        }

    return {
        "ok": True,
        "ticker": clean,
        **{k: v for k, v in probs.items() if k != "ok"},
        "features": features,
    }
