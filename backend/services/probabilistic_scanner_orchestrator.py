from __future__ import annotations
from typing import Any
"""Local probabilistic synthesis for Market Scanner Phase B candidates."""


import os
import time
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from concurrent.futures import TimeoutError as FuturesTimeoutError
from dataclasses import dataclass, field

from backend.config.logger_setup import get_logger
from backend.domain.market_scanner_models import (
    MarketScannerRow,
    ScannerCustomization,
    ScannerIndicatorDefinition,
    ScannerModuleSignal,
)
from backend.services.market_scanner_institutional_scoring import (
    PROB_ENGINE_TO_INDICATOR,
    effective_weight_for_timeframe,
    institutional_scoring_enabled,
    weight_scale_factor,
)
from backend.services.market_scanner_module_signals import (
    build_module_signal,
    label_for_module_score,
    neutral_module_signal,
)
from backend.services.market_scanner_technical_engines import aggregate_engine_features
from backend.services.motor_calibrator import calibrate_to_direction_prob
from backend.services.scanner_external_contracts import ForecastEvidence

logger = get_logger(__name__)

_FORECAST_ENGINE_KRONOS = "kronos"
_FORECAST_MIN_CONFIDENCE = 0.60
_FORECAST_MAX_IMPACT_POINTS = 5.0


def synthesize_probabilistic_signal(
    row: MarketScannerRow,
    customization: ScannerCustomization,
    indicators: list[ScannerIndicatorDefinition],
    *,
    effective_weights: dict[str, dict[str, float]] | None = None,
    primary_timeframe: str = "15m",
) -> ScannerModuleSignal:
    """Build a lightweight predictive signal from OHLCV metrics already in the row.

    Proxy legacy: uses local OHLCV risk proxies.
    """
    enabled = _enabled_indicators(customization, indicators)
    if not enabled:
        return neutral_module_signal("probabilistic", "Probabilistic module disabled.")

    usable = [signal for signal in row.signals.values() if signal.ok]
    if not usable:
        return neutral_module_signal(
            "probabilistic",
            "No usable OHLCV timeframe signals for probabilistic synthesis.",
            engine_count=len(enabled),
        )

    atr_values = [value for signal in usable if (value := _metric(signal, "atr_pct")) is not None]
    rvol_values = [
        value for signal in usable if (value := _metric(signal, "relative_volume")) is not None
    ]
    period_changes = [
        value for signal in usable if (value := _metric(signal, "period_change_pct")) is not None
    ]

    avg_score = sum(signal.score for signal in usable) / len(usable)
    avg_atr = sum(atr_values) / len(atr_values) if atr_values else 0.0
    avg_rvol = sum(rvol_values) / len(rvol_values) if rvol_values else 0.0
    avg_change = sum(period_changes) / len(period_changes) if period_changes else 0.0

    # Calibrate proxy scores → P(direction_correct) [0,1] then back to [0,100]
    cal_atr = calibrate_to_direction_prob("tail_risk", min(avg_atr / 10.0, 1.0))
    cal_rvol = calibrate_to_direction_prob("squeeze", min(avg_rvol / 3.0, 1.0))
    cal_chg = calibrate_to_direction_prob("jump_risk", min(abs(avg_change) / 10.0, 1.0))
    cal_reg = calibrate_to_direction_prob("regime", avg_score / 100.0)

    risk_penalty = 0.0
    reasons: list[str] = []
    warnings: list[str] = []

    # Engine contribution tracking (proxies)
    available_engines = 0
    if avg_atr > 0:
        available_engines += 2  # tail_risk, expected_move
    if avg_rvol > 0:
        available_engines += 1  # squeeze
    if abs(avg_change) > 0:
        available_engines += 1  # jump_risk
    if avg_score > 0:
        available_engines += 1  # regime

    tail_scale = 1.0
    if institutional_scoring_enabled() and effective_weights:
        tail_scale = weight_scale_factor(
            effective_weight_for_timeframe(
                effective_weights, "tail_risk", primary_timeframe, catalog_default=3.0
            )
        )
    if avg_atr > 8.0:
        risk_penalty += 18.0 * cal_atr * tail_scale  # calibrated penalty weight
        warnings.append("Elevated realized volatility raises tail-risk penalty.")
    elif 0.2 <= avg_atr <= 5.0:
        reasons.append("Realized volatility sits inside tradable regime.")

    if avg_rvol >= 1.3:
        reasons.append("Relative volume supports probability of follow-through.")
        avg_score += 4.0 * cal_rvol
    if abs(avg_change) >= 6.0:
        risk_penalty += 8.0 * cal_chg
        warnings.append("Large recent move increases jump-risk caution.")

    # Blend calibrated regime probability into base score
    avg_score = avg_score * 0.85 + cal_reg * 100.0 * 0.15

    score = avg_score - risk_penalty
    if row.direction == "bearish":
        score = 100.0 - score

    return build_module_signal(
        "probabilistic",
        score,
        len(usable) / max(len(row.signals), 1),
        engine_count=len(enabled),
        available_count=available_engines,
        reasons=reasons or ["Predictive score synthesized from local OHLCV risk proxies."],
        warnings=warnings,
    )


def synthesize_probabilistic_signal_v2(
    row: MarketScannerRow,
    bars_by_timeframe: dict[str, list[dict[str, Any]]],
    customization: ScannerCustomization,
    indicators: list[ScannerIndicatorDefinition],
    options_snapshot: Any | None = None,
    *,
    effective_weights: dict[str, dict[str, float]] | None = None,
    primary_timeframe: str = "15m",
) -> ScannerModuleSignal:
    """Build a probabilistic module signal running REAL Layer-3 engines.

    Executes Markov Regime, Squeeze Ignition, Tail Risk and Expected Move,
    then fuses results via regime-adaptive Bayesian weighting.
    """
    from backend.services.market_scanner_probabilistic_engines import run_probabilistic_engines
    from backend.services.probabilistic_signal_fusion import synthesize_fusion_signal

    enabled = _enabled_indicators(customization, indicators)
    if not enabled:
        return neutral_module_signal("probabilistic", "Probabilistic module disabled.")

    if not bars_by_timeframe:
        return synthesize_probabilistic_signal(row, customization, indicators)

    # 1. Run real engines per timeframe
    features_by_tf: dict[str, Any] = {}
    for tf, bars in bars_by_timeframe.items():
        if not bars:
            continue
        features_by_tf[tf] = run_probabilistic_engines(row.symbol, tf, bars, options_snapshot)

    if not features_by_tf:
        fallback_signal = synthesize_probabilistic_signal(row, customization, indicators)
        return _apply_kronos_forecast_if_enabled(row, fallback_signal, bars_by_timeframe)

    # 2. Aggregate multi-timeframe (higher TF = more weight for stability)
    tf_weights = {"5m": 0.5, "15m": 1.0, "1h": 1.5, "1D": 2.0}
    weights = {tf: tf_weights.get(tf, 1.0) for tf in features_by_tf}
    aggregated = aggregate_engine_features(features_by_tf, weights)

    # 3. Build engine_outputs dict for Bayesian fusion
    #    Maps EngineFeatures → directional scalar [-1, 1] using bias + score.
    score_neutral = 50.0
    engine_outputs: dict[str, Any] = {}
    real_engines = 0
    available_engines = 0
    reasons: list[str] = []

    for key in (
        "tail_risk",
        "expected_move",
        "squeeze",
        "jump_risk",
        "regime",
    ):
        feat = aggregated.get(key)
        if not feat or feat.engine_status == "fallback":
            continue
        available_engines += 1
        if feat.engine_status in ("real", "partial"):
            real_engines += 1
        reasons.extend(feat.reasons)

        if key == "fear_greed":
            # fear_greed score is already [0, 100] — pass raw to fusion normalizer
            engine_outputs[key] = feat.score
        elif key in ("expected_move", "jump_risk"):
            # magnitude-only engines: calibrate magnitude, fusion normalizer maps to 0
            raw_mag = abs(feat.score - score_neutral) / score_neutral
            engine_outputs[key] = calibrate_to_direction_prob(key, raw_mag)
        else:
            # Convert [0, 100] score + bias → calibrated [-1, 1] directional scalar
            direction_sign = (
                1.0 if feat.bias == "bullish" else -1.0 if feat.bias == "bearish" else 0.0
            )
            raw_mag = abs(feat.score - score_neutral) / score_neutral
            cal_mag = calibrate_to_direction_prob(key, raw_mag)
            scalar = direction_sign * cal_mag
            if institutional_scoring_enabled() and effective_weights:
                ind_key = PROB_ENGINE_TO_INDICATOR.get(key, key)
                user_w = effective_weight_for_timeframe(
                    effective_weights, ind_key, primary_timeframe, catalog_default=3.0
                )
                if user_w <= 0:
                    continue
                scalar *= weight_scale_factor(user_w)
            engine_outputs[key] = scalar

    if not engine_outputs:
        fallback_signal = synthesize_probabilistic_signal(row, customization, indicators)
        return _apply_kronos_forecast_if_enabled(row, fallback_signal, bars_by_timeframe)

    # 4. Extract regime_result from Markov engine features for fusion
    regime_result: dict[str, Any] | None = None
    regime_feat = aggregated.get("regime")
    if regime_feat and regime_feat.engine_status != "fallback":
        regime_label = _extract_regime_label(regime_feat)
        if regime_label:
            from backend.services.probabilistic_signal_fusion import Regime

            regime_result = {
                "regime": regime_label,
                "regime_probs": {
                    Regime.BULL_QUIET: 0.80 if regime_label == Regime.BULL_QUIET else 0.10,
                    Regime.BEAR_VOLATILE: 0.80 if regime_label == Regime.BEAR_VOLATILE else 0.10,
                    Regime.CHAOTIC: 0.80 if regime_label == Regime.CHAOTIC else 0.10,
                },
            }

    # 5. Bayesian fusion
    fusion = synthesize_fusion_signal(row.symbol, engine_outputs, regime_result)

    # 6. Map FusionResult → ScannerModuleSignal
    #    fusion.signal ∈ [-1, 1] → score ∈ [0, 100]
    fused_score = (fusion["signal"] + 1.0) * 50.0
    if row.direction == "bearish":
        fused_score = 100.0 - fused_score

    suppression_warning = (
        [f"Signal suppressed: {fusion['suppression_reason']}"] if fusion["suppressed"] else []
    )

    signal = build_module_signal(
        "probabilistic",
        fused_score,
        fusion["confidence"],
        engine_count=len(enabled),
        available_count=available_engines,
        reasons=(
            reasons
            + [
                f"Regime: {fusion['regime']} | conflict: {fusion['conflict_score']:.2f}",
                f"Drivers: {', '.join(fusion['conviction_drivers'])}",
            ]
        )[:6],
        warnings=suppression_warning[:4],
    )
    return _apply_kronos_forecast_if_enabled(row, signal, bars_by_timeframe)


def _enabled_indicators(
    customization: ScannerCustomization,
    indicators: list[ScannerIndicatorDefinition],
) -> list[ScannerIndicatorDefinition]:
    requested = set(customization.enabled_indicators or [])
    out: list[ScannerIndicatorDefinition] = []
    for indicator in indicators:
        if indicator.module != "probabilistic":
            continue
        if customization.enabled_indicators is None and not indicator.default_enabled:
            continue
        if customization.enabled_indicators is not None and indicator.key not in requested:
            continue
        out.append(indicator)
    return out


def _metric(signal: object, key: str) -> float | None:
    metrics = getattr(signal, "metrics", {})
    value = metrics.get(key) if isinstance(metrics, dict) else None
    return float(value) if isinstance(value, int | float) else None


def _forecast_engine_flag() -> str:
    return os.getenv("SCANNER_FORECAST_ENGINE", "none").strip().lower() or "none"


def _apply_kronos_forecast_if_enabled(
    row: MarketScannerRow,
    signal: ScannerModuleSignal,
    bars_by_timeframe: dict[str, list[dict[str, Any]]],
) -> ScannerModuleSignal:
    if _forecast_engine_flag() != _FORECAST_ENGINE_KRONOS:
        return signal

    selected = _select_forecast_bars(row, bars_by_timeframe)
    if selected is None:
        forecast = ForecastEvidence(
            engine=_FORECAST_ENGINE_KRONOS,
            status="insufficient_data",
            reason="no_ohlcv_bars",
            symbol=row.symbol,
            timeframe=None,
        )
    else:
        timeframe, bars = selected
        try:
            from backend.services import kronos_forecast_provider

            forecast = kronos_forecast_provider.forecast_ohlcv(row.symbol, timeframe, bars)
        except Exception as exc:
            forecast = ForecastEvidence(
                engine=_FORECAST_ENGINE_KRONOS,
                status="error",
                reason=f"provider_error:{type(exc).__name__}",
                symbol=row.symbol,
                timeframe=timeframe,
                confidence=0.0,
                data_quality_score=0.0,
            )

    return _attach_forecast_evidence(row, signal, forecast)


def _select_forecast_bars(
    row: MarketScannerRow,
    bars_by_timeframe: dict[str, list[dict[str, Any]]],
) -> tuple[str, list[dict[str, Any]]] | None:
    preferred = ["15m", "1h", "1D", "5m"]
    for timeframe in row.signals:
        if timeframe not in preferred:
            preferred.append(timeframe)
    for timeframe in bars_by_timeframe:
        if timeframe not in preferred:
            preferred.append(timeframe)
    for timeframe in preferred:
        bars = bars_by_timeframe.get(timeframe)
        if bars:
            return timeframe, bars
    return None


def _attach_forecast_evidence(
    row: MarketScannerRow,
    signal: ScannerModuleSignal,
    forecast: ForecastEvidence,
) -> ScannerModuleSignal:
    payload = forecast.model_dump(mode="json")
    impact = _forecast_impact_points(row.direction, forecast)

    deep = dict(row.deep_metrics or {})
    deep["forecast"] = payload
    row.deep_metrics = deep

    audit = dict(row.score_audit or {})
    prob_audit = dict(audit.get("probabilistic") or {})
    prob_audit["forecast"] = {
        "engine": forecast.engine,
        "status": forecast.status,
        "reason": forecast.reason,
        "timeframe": forecast.timeframe,
        "direction": forecast.forecast_direction,
        "confidence": round(float(forecast.confidence), 4),
        "expected_return_pct": forecast.expected_return_pct,
        "impact_points": round(impact, 4),
        "model_name": forecast.model_name,
    }
    audit["probabilistic"] = prob_audit
    row.score_audit = audit

    reasons = list(signal.reasons)
    warnings = list(signal.warnings)
    if forecast.status in {"available", "partial"} and forecast.forecast_direction != "unavailable":
        reason = (
            f"Kronos forecast {forecast.forecast_direction} "
            f"({forecast.confidence:.2f} confidence)"
        )
        if reason not in reasons:
            reasons.append(reason)
    elif forecast.reason:
        warning = f"Kronos forecast unavailable: {forecast.reason}"
        if warning not in warnings:
            warnings.append(warning)

    adjusted_score = max(0.0, min(100.0, float(signal.score) + impact))
    return signal.model_copy(
        update={
            "score": round(adjusted_score, 2),
            "label": label_for_module_score(adjusted_score),
            "reasons": reasons[:6],
            "warnings": warnings[:6],
        }
    )


def _forecast_impact_points(row_direction: str, forecast: ForecastEvidence) -> float:
    if forecast.status not in {"available", "partial"}:
        return 0.0
    if float(forecast.confidence) < _FORECAST_MIN_CONFIDENCE:
        return 0.0
    if forecast.forecast_direction not in {"bullish", "bearish"}:
        return 0.0

    raw = (
        (float(forecast.confidence) - _FORECAST_MIN_CONFIDENCE)
        / (1.0 - _FORECAST_MIN_CONFIDENCE)
        * _FORECAST_MAX_IMPACT_POINTS
    )
    magnitude = max(0.0, min(_FORECAST_MAX_IMPACT_POINTS, raw))
    direction = str(row_direction or "neutral").lower()
    if direction in {"bullish", "bearish"}:
        return magnitude if forecast.forecast_direction == direction else -magnitude
    return magnitude if forecast.forecast_direction == "bullish" else -magnitude


def _extract_regime_label(regime_feat: object) -> str | None:
    """Parse the regime label out of an EngineFeatures.reasons string.

    MarkovRegimeEngine stores the regime name as the first reason item,
    e.g. "Regime: BULL_QUIET (STABLE)".  Returns None if unparseable.
    """
    from backend.services.probabilistic_signal_fusion import Regime

    reasons = getattr(regime_feat, "reasons", [])
    if not reasons:
        return None
    first = str(reasons[0])
    for label in (Regime.BULL_QUIET, Regime.BEAR_VOLATILE, Regime.CHAOTIC):
        if label in first:
            return label
    return None


# ════════════════════════════════════════════════════════════════════════════════
# Full per-symbol pipeline — circuit-broken parallel orchestration
# ════════════════════════════════════════════════════════════════════════════════
#
# scan_symbol_full() runs every available motor in parallel for one symbol,
# protected by per-motor timeouts and exception isolation. Failed motors are
# logged in motor_health and excluded from fusion — no single bad motor blocks
# the pipeline. scan_all() fans this out across symbols using a thread pool.
#
# Backward compat: the legacy synthesize_probabilistic_signal(row, customization,
# indicators) above is untouched. New entry points live below.
# ════════════════════════════════════════════════════════════════════════════════

_META_LEARNER_FEATURE_PREFIXES = {
    "risk_neutral_density": "rnd",
    "macro_regime_prior": "macro_regime",
}

# Default per-motor timeout (seconds).
_DEFAULT_MOTOR_TIMEOUT_S = 2.0
_DEFAULT_MAX_WORKERS = 4
_DEFAULT_MIN_MOTORS = 5

_LEGACY_MOTORS = (
    "tail_risk",
    "gamma_flip",
    "vsa_forecast",
    "sentiment",
    "fear_greed",
    "cross_asset",
    "squeeze",
    "shadow_delta",
    "zomma",
)
_NEW_MOTORS = (
    "risk_neutral_density",
    "dealer_flow_dynamics",
    "options_flow_toxicity",
    "macro_regime_prior",
)


@dataclass
class ScannerConfig:
    """Configuration for the new full-pipeline scanner."""

    symbols: list[str] = field(default_factory=list)
    max_workers: int = _DEFAULT_MAX_WORKERS
    motor_timeouts: dict[str, float] = field(default_factory=dict)
    use_meta_learner: bool = True
    use_new_motors: bool = True
    min_motors_required: int = _DEFAULT_MIN_MOTORS

    def timeout_for(self, motor: str) -> float:
        return float(self.motor_timeouts.get(motor, _DEFAULT_MOTOR_TIMEOUT_S))


# ── Motor registry ──────────────────────────────────────────────────────────

MotorCallable = Callable[[str, dict], dict]
_MOTOR_REGISTRY: dict[str, MotorCallable] = {}


def register_motor(name: str) -> Callable[[MotorCallable], MotorCallable]:
    """Decorator: register a motor callable under `name`.

    Callable signature: (symbol: str, market_data: dict) -> dict.
    """

    def _wrap(fn: MotorCallable) -> MotorCallable:
        _MOTOR_REGISTRY[name] = fn
        return fn

    return _wrap


def clear_motor_registry() -> None:
    """Test helper — empties the motor registry."""
    _MOTOR_REGISTRY.clear()


def _run_motor_with_circuit_breaker(
    name: str,
    fn: MotorCallable,
    symbol: str,
    market_data: dict,
    timeout_s: float,
) -> tuple[str, dict | None, str | None, float]:
    """Run a motor with hard timeout. Returns (name, output, error, latency_ms)."""
    t0 = time.perf_counter()
    with ThreadPoolExecutor(max_workers=1) as ex:
        future = ex.submit(fn, symbol, market_data)
        try:
            result = future.result(timeout=timeout_s)
            elapsed = (time.perf_counter() - t0) * 1000
            if not isinstance(result, dict):
                return (
                    name,
                    None,
                    f"motor returned {type(result).__name__}, expected dict",
                    elapsed,
                )
            return (name, result, None, elapsed)
        except FuturesTimeoutError:
            elapsed = (time.perf_counter() - t0) * 1000
            future.cancel()
            return (name, None, f"timeout>{timeout_s:.1f}s", elapsed)
        except Exception as exc:
            elapsed = (time.perf_counter() - t0) * 1000
            return (name, None, f"{type(exc).__name__}: {exc}", elapsed)


def _select_motors(config: ScannerConfig) -> list[str]:
    motors = list(_LEGACY_MOTORS)
    if config.use_new_motors:
        motors.extend(_NEW_MOTORS)
    return [m for m in motors if m in _MOTOR_REGISTRY]


def _fetch_market_data(symbol: str) -> dict[str, Any]:
    """
    Fetch the once-per-symbol data bundle. Default returns empty dict; tests
    monkeypatch this. Production wiring is layered in by the caller.
    """
    return {}


def _run_motors_parallel(
    symbol: str,
    market_data: dict,
    motors: list[str],
    config: ScannerConfig,
) -> tuple[dict[str, dict], dict[str, dict]]:
    """Run motors concurrently. Returns (outputs, motor_health)."""
    outputs: dict[str, dict] = {}
    motor_health: dict[str, dict] = {}

    if not motors:
        return outputs, motor_health

    workers = max(1, min(config.max_workers, len(motors)))
    with ThreadPoolExecutor(max_workers=workers) as pool:
        futures = {
            pool.submit(
                _run_motor_with_circuit_breaker,
                name,
                _MOTOR_REGISTRY[name],
                symbol,
                market_data,
                config.timeout_for(name),
            ): name
            for name in motors
        }
        for fut in futures:
            try:
                name, output, error, latency_ms = fut.result()
            except Exception as exc:
                name = futures[fut]
                output, error, latency_ms = None, f"executor_error:{exc}", 0.0

            if output is not None:
                outputs[name] = output
                motor_health[name] = {"ok": True, "latency_ms": latency_ms, "error": None}
            else:
                motor_health[name] = {"ok": False, "latency_ms": latency_ms, "error": error}

    return outputs, motor_health


def _extract_scalar_signal(motor_output: dict) -> float:
    """Pull a numeric signal from a motor output dict."""
    for key in ("signal", "directional_signal", "score", "value"):
        val = motor_output.get(key)
        if isinstance(val, int | float):
            return float(val)
    return 0.0


def _build_engine_outputs(motor_outputs: dict[str, dict]) -> dict[str, float]:
    return {name: _extract_scalar_signal(out) for name, out in motor_outputs.items()}


def _maybe_meta_learner_blend(
    component_signals: dict[str, float],
    config: ScannerConfig,
) -> dict | None:
    """Best-effort meta-learner predict_proba; returns None if unavailable."""
    if not config.use_meta_learner:
        return None
    learner = globals().get("_META_LEARNER_INSTANCE")
    if learner is None or not getattr(learner, "is_fitted", False):
        return None
    try:
        import pandas as pd

        feature_row = {n: 0.0 for n in learner.feature_names}
        for motor, val in component_signals.items():
            feature_prefix = _META_LEARNER_FEATURE_PREFIXES.get(motor, motor)
            for col in learner.feature_names:
                if col.startswith(f"{feature_prefix}__"):
                    feature_row[col] = val
        return learner.predict_proba(pd.DataFrame([feature_row]))
    except Exception as exc:
        logger.warning("scanner meta-learner predict failed: %s", exc)
        return None


def _final_signal_from_fusion(fusion: dict, meta_proba: dict | None) -> dict[str, Any]:
    """Compose flat scanner-facing payload from fusion + optional meta proba."""
    base_signal = float(fusion.get("signal", 0.0))
    confidence = float(fusion.get("confidence", 0.0))

    if meta_proba:
        p_up = float(meta_proba.get("UP", 0.0))
        p_down = float(meta_proba.get("DOWN", 0.0))
        p_neutral = float(meta_proba.get("NEUTRAL", max(0.0, 1.0 - p_up - p_down)))
        meta_sig = max(-1.0, min(1.0, p_up - p_down))
        blended_signal = float(max(-1.0, min(1.0, 0.5 * base_signal + 0.5 * meta_sig)))
        blended_confidence = float(
            max(0.0, min(1.0, 0.5 * confidence + 0.5 * max(p_up, p_down, p_neutral)))
        )
    else:
        p_up = p_down = p_neutral = None
        blended_signal = base_signal
        blended_confidence = confidence

    direction = "UP" if blended_signal > 0.05 else "DOWN" if blended_signal < -0.05 else "NEUTRAL"

    return {
        "signal": blended_signal,
        "confidence": blended_confidence,
        "direction": direction,
        "regime": fusion.get("regime", "UNKNOWN"),
        "regime_alignment": bool(fusion.get("regime_alignment", True)),
        "conflict_score": float(fusion.get("conflict_score", 0.0)),
        "p_up": p_up,
        "p_down": p_down,
        "p_neutral": p_neutral,
    }


def scan_symbol_full(
    symbol: str,
    config: ScannerConfig | None = None,
    market_data: dict | None = None,
) -> dict[str, Any]:
    """
    Run the full probabilistic pipeline for a single symbol.

    Failures: per-motor timeouts/exceptions are isolated in motor_health.error
    without aborting. If fewer than config.min_motors_required motors succeed,
    payload['ok']=False and suppression_reason='insufficient_motors'.
    """
    cfg = config or ScannerConfig()
    sym = (symbol or "").upper().strip()
    t0 = time.perf_counter()

    if not sym:
        raise ValueError("symbol must be a non-empty string")

    market_data = market_data if market_data is not None else _fetch_market_data(sym)
    motors = _select_motors(cfg)

    motor_outputs, motor_health = _run_motors_parallel(sym, market_data, motors, cfg)
    motors_ok = sorted(name for name, h in motor_health.items() if h["ok"])
    motors_failed = sorted(name for name, h in motor_health.items() if not h["ok"])
    n_ok = len(motors_ok)

    regime_result: dict | None = None
    macro_out = motor_outputs.get("macro_regime_prior")
    if isinstance(macro_out, dict):
        regime_result = {
            "regime": str(macro_out.get("macro_regime_dominant", "UNKNOWN")).upper(),
            "regime_probs": macro_out.get("macro_regime_prior"),
        }

    component_signals = _build_engine_outputs(motor_outputs)

    try:
        from backend.services.probabilistic_signal_fusion import synthesize_fusion_signal

        fusion = synthesize_fusion_signal(sym, component_signals, regime_result)
    except Exception as exc:
        logger.warning("scanner fusion failed for %s: %s", sym, exc)
        fusion = {
            "signal": 0.0,
            "confidence": 0.0,
            "direction": "NEUTRAL",
            "regime": (regime_result or {}).get("regime", "UNKNOWN"),
            "conflict_score": 0.0,
            "regime_alignment": False,
            "suppressed": True,
            "suppression_reason": f"fusion_failed:{exc}",
            "conviction_drivers": [],
            "motor_signals": component_signals,
            "motor_weights": {},
        }

    meta_proba = _maybe_meta_learner_blend(component_signals, cfg)
    final = _final_signal_from_fusion(fusion, meta_proba)
    latency_total_ms = (time.perf_counter() - t0) * 1000

    insufficient = n_ok < cfg.min_motors_required
    should_trade = (
        (not insufficient)
        and (not bool(fusion.get("suppressed", False)))
        and final["confidence"] >= 0.20
    )
    suppression_reason = (
        "insufficient_motors"
        if insufficient
        else fusion.get("suppression_reason") if fusion.get("suppressed") else None
    )

    payload = {
        "symbol": sym,
        "ok": not insufficient,
        "should_trade": bool(should_trade),
        "suppression_reason": suppression_reason,
        "signal": final["signal"],
        "confidence": final["confidence"],
        "direction": final["direction"],
        "regime": final["regime"],
        "regime_alignment": final["regime_alignment"],
        "conflict_score": final["conflict_score"],
        "p_up": final["p_up"],
        "p_down": final["p_down"],
        "p_neutral": final["p_neutral"],
        "component_signals": component_signals,
        "motor_health": motor_health,
        "motors_ok": motors_ok,
        "motors_failed": motors_failed,
        "n_motors_ok": n_ok,
        "latency_total_ms": latency_total_ms,
    }

    logger.info(
        "scanner.scan_symbol_full",
        extra={
            "symbol": sym,
            "motores_exitosos": motors_ok,
            "motores_fallidos": motors_failed,
            "señal_final": final["signal"],
            "latencia_total_ms": latency_total_ms,
        },
    )

    return payload


def scan_all(config: ScannerConfig) -> list[dict]:
    """
    Scan every symbol in `config.symbols` in parallel (max_workers cap).
    Returns one scan_symbol_full() payload per symbol, in input order.
    """
    if not config.symbols:
        return []

    workers = max(1, min(config.max_workers, len(config.symbols)))
    results: list[dict | None] = [None] * len(config.symbols)

    with ThreadPoolExecutor(max_workers=workers) as pool:
        futures = {
            pool.submit(scan_symbol_full, sym, config): idx
            for idx, sym in enumerate(config.symbols)
        }
        for fut in futures:
            idx = futures[fut]
            try:
                results[idx] = fut.result()
            except Exception as exc:
                logger.error("scan_symbol_full crashed for %s: %s", config.symbols[idx], exc)
                results[idx] = {
                    "symbol": config.symbols[idx].upper().strip(),
                    "ok": False,
                    "should_trade": False,
                    "suppression_reason": f"crashed:{exc}",
                    "motor_health": {},
                    "motors_ok": [],
                    "motors_failed": [],
                    "n_motors_ok": 0,
                    "latency_total_ms": 0.0,
                }

    return [r for r in results if r is not None]
