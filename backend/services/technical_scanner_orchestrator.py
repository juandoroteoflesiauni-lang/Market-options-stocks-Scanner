from __future__ import annotations
from typing import Any
"""Phase B technical synthesis for Market Scanner candidates."""



from backend.config.logger_setup import get_logger
from backend.domain.market_scanner_models import (
    MarketScannerRow,
    ScannerCustomization,
    ScannerIndicatorDefinition,
    ScannerModuleSignal,
)
from backend.services.market_scanner_institutional_scoring import (
    TECHNICAL_ENGINE_TO_INDICATOR,
    institutional_scoring_enabled,
    normalize_engine_weights,
)
from backend.services.market_scanner_module_signals import (
    build_module_signal,
    neutral_module_signal,
)

logger = get_logger(__name__)

# Pesos de timeframe para la agregación en v2 (de menor a mayor importancia)
_TF_WEIGHTS: dict[str, float] = {"5m": 0.8, "15m": 1.2, "1h": 1.5, "1D": 2.0}

# Motores que contribuyen al score técnico agregado y sus pesos
_ENGINE_WEIGHTS: dict[str, float] = {
    "smc": 2.5,
    "fvg": 1.8,
    "vsa": 2.2,
    "market_structure": 2.0,
    "order_flow_delta": 1.5,
    "volume_profile": 1.0,
    "hmm_regime": 1.8,
}


def _regime_adjusted_engine_weights(aggregated: dict[str, Any]) -> dict[str, float]:
    """Scale engine weights using aggregated HMM regime (institutional multi-regime)."""
    weights = dict(_ENGINE_WEIGHTS)
    hmm = aggregated.get("hmm_regime")
    if hmm is None:
        return weights
    joined = " ".join(getattr(hmm, "reasons", []) or []).upper()
    score = float(getattr(hmm, "score", 50.0) or 50.0)
    if "BULL_QUIET" in joined or (getattr(hmm, "bias", "") == "bullish" and score >= 65):
        weights["smc"] *= 1.15
        weights["market_structure"] *= 1.12
        weights["fvg"] *= 0.92
    elif "CRISIS" in joined or getattr(hmm, "bias", "") == "bearish":
        weights["order_flow_delta"] *= 1.12
        weights["hmm_regime"] *= 1.22
        weights["volume_profile"] *= 1.05
    elif "MEAN_REVERT" in joined:
        weights["volume_profile"] *= 1.18
        weights["fvg"] *= 1.1
        weights["smc"] *= 0.94
    return weights


def synthesize_technical_signal(
    row: MarketScannerRow,
    customization: ScannerCustomization,
    indicators: list[ScannerIndicatorDefinition],
) -> ScannerModuleSignal:
    """Build a technical module signal from already-computed timeframe signals.

    Proxy legacy: usa las señales de timeframe ya calculadas (sin motores reales).
    Se mantiene como fallback cuando no hay barras disponibles para v2.
    """
    enabled = _enabled_indicators(customization, indicators, {"core", "technical"})
    if not enabled:
        return neutral_module_signal("technical", "Technical module disabled.")

    usable = [signal for signal in row.signals.values() if signal.ok]
    if not usable:
        return neutral_module_signal(
            "technical",
            "No usable OHLCV timeframe signals for technical synthesis.",
            engine_count=len(enabled),
        )

    weighted_sum = 0.0
    total_weight = 0.0
    for signal in usable:
        weight = _timeframe_weight(customization, enabled, signal.timeframe)
        weighted_sum += signal.score * weight
        total_weight += weight

    score = weighted_sum / total_weight if total_weight else row.scanner_score
    reasons = list(row.reasons)
    if any("VWAP confluence" in reason for reason in row.reasons):
        reasons.append("VWAP accepted by aggregated technical engine")
    if row.vetoes:
        reasons.append("Technical synthesis retains hard scanner vetoes")

    return build_module_signal(
        "technical",
        score,
        len(usable) / max(len(row.signals), 1),
        engine_count=len(enabled),
        available_count=len(usable),
        reasons=reasons or ["Technical score synthesized from scanner timeframe confluence."],
    )


def synthesize_technical_signal_v2(
    row: MarketScannerRow,
    bars_by_timeframe: dict[str, list[dict[str, Any]]],
    customization: ScannerCustomization,
    indicators: list[ScannerIndicatorDefinition],
    *,
    effective_weights: dict[str, dict[str, float]] | None = None,
    primary_timeframe: str = "15m",
) -> ScannerModuleSignal:
    """Build a technical module signal running REAL Layer-3 engines.

    Ejecuta SMC, FVG, VSA, MarketStructure, OrderFlowDelta, VolumeNode y HMM
    por cada timeframe disponible y agrega los resultados ponderados.

    Si no hay barras o todos los motores fallan, degrade a synthesize_technical_signal.

    Args:
        row:               Scanner row (usa row.symbol para identificar).
        bars_by_timeframe: {timeframe: list[dict]} barras OHLCV por timeframe.
        customization:     Configuración del usuario.
        indicators:        Catálogo de indicadores.
    """
    from backend.services.market_scanner_technical_engines import (
        EngineFeatures,
        aggregate_engine_features,
        run_technical_engines,
    )

    if not bars_by_timeframe:
        logger.debug("scanner_orchestrator_v2.no_bars symbol=%s → fallback proxy", row.symbol)
        return synthesize_technical_signal(row, customization, indicators)

    enabled = _enabled_indicators(customization, indicators, {"core", "technical"})
    if not enabled:
        return neutral_module_signal("technical", "Technical module disabled.")

    # ── Ejecutar motores reales por timeframe ──────────────────────────────
    features_by_tf: dict[str, dict[str, EngineFeatures]] = {}
    for timeframe, bars in bars_by_timeframe.items():
        if not bars:
            continue
        try:
            real_ms = (row.deep_metrics or {}).get("real_microstructure")
            features_by_tf[timeframe] = run_technical_engines(
                row.symbol,
                timeframe,
                bars,
                real_microstructure=real_ms if isinstance(real_ms, dict) else None,
            )
        except Exception as exc:
            logger.warning(
                "scanner_orchestrator_v2.tf_failed symbol=%s tf=%s error=%s",
                row.symbol,
                timeframe,
                str(exc)[:180],
            )

    if not features_by_tf:
        logger.debug(
            "scanner_orchestrator_v2.all_tfs_failed symbol=%s → fallback proxy", row.symbol
        )
        return synthesize_technical_signal(row, customization, indicators)

    # ── Agregar features multi-timeframe ──────────────────────────────────
    tf_weights = {tf: _TF_WEIGHTS.get(tf, 1.0) for tf in features_by_tf}
    aggregated = aggregate_engine_features(features_by_tf, tf_weights)

    engine_weights = _regime_adjusted_engine_weights(aggregated)
    if institutional_scoring_enabled() and effective_weights:
        engine_weights = normalize_engine_weights(
            engine_weights,
            TECHNICAL_ENGINE_TO_INDICATOR,
            effective_weights,
            primary_timeframe,
        )

    # ── Calcular score final ponderado por motor ───────────────────────────
    weighted_score = 0.0
    total_engine_weight = 0.0
    real_engines = 0
    available_engines = 0
    reasons: list[str] = []
    warnings: list[str] = []

    for engine_key, weight in engine_weights.items():
        feat = aggregated.get(engine_key)
        if feat is None:
            continue
        if feat.engine_status == "fallback":
            warnings.append(f"{engine_key}: sin datos suficientes")
            continue

        available_engines += 1
        if feat.engine_status == "real":
            real_engines += 1

        weighted_score += feat.score * weight
        total_engine_weight += weight
        reasons.extend(feat.reasons)

    if total_engine_weight <= 0:
        # Todos los motores fallaron → proxy legacy
        return synthesize_technical_signal(row, customization, indicators)

    final_score = weighted_score / total_engine_weight

    # Ajuste mínimo desde señales de timeframe ya computadas (para no romper continuidad)
    usable_tf = [sig for sig in row.signals.values() if sig.ok]
    if usable_tf:
        tf_avg = sum(sig.score for sig in usable_tf) / len(usable_tf)
        # Blend 80% engines reales + 20% timeframe legacy
        final_score = final_score * 0.80 + tf_avg * 0.20

    confidence = real_engines / max(len(engine_weights), 1)

    # Deduplicar y limitar reasons
    unique_reasons = list(dict.fromkeys(reasons))[:6]
    if not unique_reasons:
        unique_reasons = [
            f"Motores reales: {real_engines}/{len(engine_weights)} activos "
            f"({available_engines} con datos)"
        ]

    logger.debug(
        "scanner_orchestrator_v2.done symbol=%s score=%.1f real_engines=%d/%d",
        row.symbol,
        final_score,
        real_engines,
        len(engine_weights),
    )

    return build_module_signal(
        "technical",
        final_score,
        confidence,
        engine_count=len(_ENGINE_WEIGHTS),
        available_count=available_engines,
        reasons=unique_reasons,
        warnings=warnings[:4],
    )


def _enabled_indicators(
    customization: ScannerCustomization,
    indicators: list[ScannerIndicatorDefinition],
    modules: set[str],
) -> list[ScannerIndicatorDefinition]:
    requested = set(customization.enabled_indicators or [])
    out: list[ScannerIndicatorDefinition] = []
    for indicator in indicators:
        if indicator.module not in modules:
            continue
        if customization.enabled_indicators is None and not indicator.default_enabled:
            continue
        if customization.enabled_indicators is not None and indicator.key not in requested:
            continue
        out.append(indicator)
    return out


def _timeframe_weight(
    customization: ScannerCustomization,
    indicators: list[ScannerIndicatorDefinition],
    timeframe: str,
) -> float:
    weights: list[float] = []
    for indicator in indicators:
        if timeframe not in indicator.supports_timeframes:
            continue
        custom = customization.weight_matrix.get(indicator.key, {}).get(timeframe)
        weights.append(
            custom if custom is not None else indicator.weight_by_timeframe.get(timeframe, 1.0)
        )
    return sum(weights) / len(weights) if weights else 1.0


def _build_scanner_signal_payload(aggregated: dict, ticker: str, timeframe: str) -> dict[str, Any]:
    """
    Construye el payload del scanner con soporte SHORT.
    Añadir al final de synthesize_technical_signal_v2.
    """
    return {
        "ticker": ticker,
        "timeframe": timeframe,
        "score": aggregated["final_score"],
        "direction": aggregated["direction"],
        "action": aggregated["action"],
        "conflict": aggregated["conflict"],
        "dispersion": aggregated["dispersion"],
        "engine_scores": aggregated["engine_scores"],
        "regime_filter": "bidirectional",
    }
