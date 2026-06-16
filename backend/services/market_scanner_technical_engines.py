from __future__ import annotations

from typing import Any

"""Market Scanner Technical Engines Adapter — Phase B.

Puente limpio entre las barras OHLCV ya descargadas por el scanner y los
motores reales de Layer 3.  No importa nada de la UI ni de payload de terminal.

Responsabilidades:
  - Convertir list[dict] OHLCV → pd.DataFrame normalizado.
  - Ejecutar cada motor con manejo de excepciones aislado.
  - Normalizar la salida de cada motor a un EngineFeatures estandarizado.
  - Proveer fallback proxy cuando el motor falla o hay barras insuficientes.

Contrato público:
  run_technical_engines(symbol, timeframe, bars) -> dict[str, EngineFeatures]
"""


import math
from dataclasses import dataclass, field

import numpy as np
import pandas as pd

from backend.config.logger_setup import get_logger

logger = get_logger(__name__)

# ─────────────────────────────────────────────────────────────────────────────
# §0  Contrato de salida normalizado
# ─────────────────────────────────────────────────────────────────────────────

EngineKey = str  # "smc" | "fvg" | "vsa" | "market_structure" | "order_flow_delta" | "volume_profile" | "hmm_regime"
EngineBias = str  # "bullish" | "bearish" | "neutral"
EngineStatus = str  # "real" | "partial" | "fallback"


@dataclass(frozen=True)
class EngineFeatures:
    """Representación normalizada de la salida de un motor técnico."""

    score: float = 50.0  # 0-100
    bias: EngineBias = "neutral"
    confidence: float = 0.0  # 0-1
    reasons: list[str] = field(default_factory=list)
    engine_status: EngineStatus = "fallback"

    def __post_init__(self: EngineFeatures) -> None:
        object.__setattr__(self, "score", float(np.clip(self.score, 0.0, 100.0)))
        object.__setattr__(self, "confidence", float(np.clip(self.confidence, 0.0, 1.0)))


# Mínimos de barras por motor para activar el análisis real
_MIN_BARS: dict[str, int] = {
    "smc": 30,
    "fvg": 3,
    "vsa": 20,
    "market_structure": 7,
    "order_flow_delta": 8,
    "volume_profile": 5,
    "hmm_regime": 25,
    "lob_dynamics": 10,
    "tpo": 150,
    "squeeze": 60,
    "footprint": 30,
}

_ALL_ENGINE_KEYS: tuple[str, ...] = (
    "smc",
    "fvg",
    "vsa",
    "market_structure",
    "order_flow_delta",
    "volume_profile",
    "hmm_regime",
    "lob_dynamics",
    "tpo",
    "squeeze",
    "footprint",
)

# ─────────────────────────────────────────────────────────────────────────────
# §1  Punto de entrada público
# ─────────────────────────────────────────────────────────────────────────────


def run_technical_engines(
    symbol: str,
    timeframe: str,
    bars: list[dict[str, Any]],
    *,
    real_microstructure: dict[str, Any] | None = None,
) -> dict[str, EngineFeatures]:
    """Ejecutar todos los motores técnicos y retornar features normalizados.

    Args:
        symbol:    Ticker (ej. "AAPL").
        timeframe: Marco temporal (ej. "15m", "1h", "1D").
        bars:      Lista de dicts OHLCV tal como los entrega el data provider.

    Returns:
        Dict con clave = motor y valor = EngineFeatures normalizado.
        Siempre retorna las 7 claves; usa fallback para motores que fallan.
    """
    df = _bars_to_dataframe(bars)
    n = len(df)

    results: dict[str, EngineFeatures] = {}
    for key in _ALL_ENGINE_KEYS:
        if n < _MIN_BARS[key]:
            results[key] = _fallback(f"Insufficient bars ({n} < {_MIN_BARS[key]})")
        else:
            results[key] = _run_engine(
                key,
                symbol,
                timeframe,
                df,
                real_microstructure=real_microstructure,
            )

    return results


# ─────────────────────────────────────────────────────────────────────────────
# §2  Dispatcher por motor
# ─────────────────────────────────────────────────────────────────────────────


def _run_engine(
    key: str,
    symbol: str,
    timeframe: str,
    df: pd.DataFrame,
    *,
    real_microstructure: dict[str, Any] | None = None,
) -> EngineFeatures:
    """Ejecutar un motor específico con aislamiento de excepciones."""
    import time

    t0 = time.perf_counter()
    try:
        if key == "smc":
            return _run_smc(df, symbol, timeframe)
        if key == "fvg":
            return _run_fvg(df)
        if key == "vsa":
            return _run_vsa(df, symbol, timeframe)
        if key == "market_structure":
            return _run_market_structure(df)
        if key == "order_flow_delta":
            return _run_order_flow_delta(df, real_microstructure=real_microstructure)
        if key == "volume_profile":
            return _run_volume_profile(df, real_microstructure=real_microstructure)
        if key == "hmm_regime":
            return _run_hmm(df)
        if key == "lob_dynamics":
            return _run_lob_dynamics(df, real_microstructure=real_microstructure)
        if key == "tpo":
            return _run_tpo(symbol, df)
        if key == "squeeze":
            return _run_squeeze(df)
        if key == "footprint":
            return _run_footprint(symbol, df)
    except Exception as exc:
        latency_ms = (time.perf_counter() - t0) * 1000.0
        logger.warning(
            "scanner_technical_engines.engine_failed engine=%s symbol=%s tf=%s latency_ms=%.1f error=%s",
            key,
            symbol,
            timeframe,
            latency_ms,
            str(exc)[:200],
        )
    return _fallback(f"Engine {key} raised exception")


# ─────────────────────────────────────────────────────────────────────────────
# §3  Adaptadores por motor
# ─────────────────────────────────────────────────────────────────────────────


def _run_smc(df: pd.DataFrame, symbol: str, timeframe: str) -> EngineFeatures:
    from backend.quant_engine.engines.technical.smc import DirectionalBias, SMCEngine

    result = SMCEngine().analyze(df, ticker=symbol, timeframe=timeframe)
    if not result.ok:
        return _fallback(result.error or "SMC error")

    # Bias
    bias: EngineBias
    if result.sesgo in (DirectionalBias.BULLISH, DirectionalBias.BULLISH_WATCH):
        bias = "bullish"
    elif result.sesgo == DirectionalBias.CASH:
        bias = "bearish"
    else:
        bias = "neutral"

    # Score ya es 0-100
    score = float(result.composite_score)
    # Confidence desde aggregate_confidence (ya 0-1)
    confidence = float(np.clip(result.aggregate_confidence, 0.0, 1.0))

    reasons: list[str] = []
    if result.order_blocks:
        bull_obs = sum(1 for ob in result.order_blocks if ob.direction == "BULLISH")
        reasons.append(f"SMC: {bull_obs} Order Block(s) bullish detectado(s)")
    if result.structure_events:
        last_evt = result.structure_events[-1]
        reasons.append(f"SMC: Estructura {last_evt.event_type} en barra {last_evt.bar_index}")
    if result.dominant_model:
        reasons.append(f"SMC: Modelo ICT dominante {result.dominant_model.name.value}")
    if result.liquidity_sweeps:
        reasons.append(f"SMC: {len(result.liquidity_sweeps)} sweep(s) de liquidez")

    return EngineFeatures(
        score=score,
        bias=bias,
        confidence=confidence,
        reasons=reasons[:4],
        engine_status="real",
    )


def _run_fvg(df: pd.DataFrame) -> EngineFeatures:
    from backend.quant_engine.engines.technical.fvg_engine import analyze_fvg_from_ohlcv

    result = analyze_fvg_from_ohlcv(df)
    if not result.ok:
        return _fallback(result.error or "FVG error")

    active = result.active_count
    bullish = result.bullish_active_count
    bearish = result.bearish_active_count

    # Score: base 50 + diferencial de FVGs activos, escalonado
    imbalance = bullish - bearish
    score = float(np.clip(50.0 + imbalance * 8.0, 0.0, 100.0))

    bias: EngineBias
    if bullish > bearish:
        bias = "bullish"
    elif bearish > bullish:
        bias = "bearish"
    else:
        bias = "neutral"

    total = active + result.history_count
    confidence = float(active / max(total, 1))

    reasons: list[str] = []
    if active > 0:
        reasons.append(f"FVG: {active} zona(s) activa(s) ({bullish}↑/{bearish}↓)")
    if result.partial_count:
        reasons.append(f"FVG: {result.partial_count} zona(s) parcialmente mitigada(s)")
    if result.iofed_count:
        reasons.append(f"FVG: {result.iofed_count} IoFED detectado(s)")

    return EngineFeatures(
        score=score,
        bias=bias,
        confidence=confidence,
        reasons=reasons[:4],
        engine_status="real",
    )


def _run_vsa(df: pd.DataFrame, symbol: str, timeframe: str) -> EngineFeatures:
    # Importar con fallback silencioso si VSAForecastEngine no está disponible
    try:
        from backend.quant_engine.engines.technical.vsa import DirectionalBias as VSABias
        from backend.quant_engine.engines.technical.vsa import VSAEngine

        result = VSAEngine().analyze(df, ticker=symbol, timeframe=timeframe)
    except ImportError:
        # Si VSAForecastEngine falla, usar la lógica VSA básica directamente
        return _run_vsa_basic(df, symbol, timeframe)

    if not result.ok:
        return _fallback(result.error or "VSA error")

    bias: EngineBias = "bullish" if result.signal == VSABias.BULLISH else "neutral"

    score = float(result.composite_score)
    # Confidence desde a_index_zscore (normalizado 0-3 → 0-1)
    confidence = float(np.clip(abs(result.last_a_index_zscore) / 3.0, 0.0, 1.0))

    reasons: list[str] = []
    bullish_labels = sum(
        1
        for lbl in result.recent_labels
        if lbl.value in {"STOPPING_VOLUME", "CLIMAX_SELL", "NO_SUPPLY"}
    )
    if bullish_labels:
        reasons.append(f"VSA: {bullish_labels} señal(es) bullish reciente(s)")
    if result.is_absorption_active:
        reasons.append("VSA: Absorción institucional activa")
    if result.long_signal_active:
        reasons.append("VSA: Señal Long 0DTE activa (MFI bounce + absorción)")
    if result.is_buying_climax_active:
        reasons.append("VSA: Climax de compra detectado (cuidado)")

    return EngineFeatures(
        score=score,
        bias=bias,
        confidence=confidence,
        reasons=reasons[:4],
        engine_status="real",
    )


def _run_vsa_basic(df: pd.DataFrame, symbol: str, timeframe: str) -> EngineFeatures:
    """Fallback VSA básico sin dependencias de ia_probabilistico."""
    try:
        import numpy as np

        df_norm = df.copy()
        df_norm.columns = [str(c).lower() for c in df_norm.columns]
        vol = df_norm["volume"].values.astype(float)
        close = df_norm["close"].values.astype(float)
        high = df_norm["high"].values.astype(float)
        low = df_norm["low"].values.astype(float)

        w = min(20, len(vol))
        vol_mean = float(np.mean(vol[-w:]))
        vol_last = float(vol[-1])
        spread_last = float(high[-1] - low[-1])
        close_loc = float((close[-1] - low[-1]) / max(spread_last, 1e-9))

        rvol = vol_last / max(vol_mean, 1e-9)
        # Señal simplificada: STOPPING_VOLUME si alto volumen y close alto
        if rvol > 2.0 and close_loc > 0.7:
            score = 65.0
            bias: EngineBias = "bullish"
            reasons = ["VSA-basic: Alto volumen con cierre fuerte (absorción posible)"]
        elif rvol < 0.5:
            score = 35.0
            bias = "neutral"
            reasons = ["VSA-basic: Volumen bajo (ausencia de demanda)"]
        else:
            score = 50.0
            bias = "neutral"
            reasons = ["VSA-basic: Contexto neutral de volumen"]

        return EngineFeatures(
            score=score,
            bias=bias,
            confidence=0.3,
            reasons=reasons,
            engine_status="partial",
        )
    except Exception as exc:
        return _fallback(f"VSA basic failed: {exc}")


def _run_market_structure(df: pd.DataFrame) -> EngineFeatures:
    from backend.quant_engine.engines.technical.market_structure_engine import (
        MarketRegime,
        analyze_market_structure_from_ohlcv,
    )

    result = analyze_market_structure_from_ohlcv(df)
    if not result.ok:
        return _fallback(result.error or "MarketStructure error")

    # Score base desde régimen
    regime_score = {
        MarketRegime.BULLISH: 70.0,
        MarketRegime.BEARISH: 30.0,
        MarketRegime.CONSOLIDATION: 50.0,
    }.get(result.regime, 50.0)

    # Bonificación por MSS confirmados (max +15)
    mss_bonus = min(result.mss_count * 5.0, 15.0)
    # Penalización por sweeps sin confirmación (max -10)
    sweep_penalty = min(result.sweep_count * 2.0, 10.0)
    score = float(np.clip(regime_score + mss_bonus - sweep_penalty, 0.0, 100.0))

    bias: EngineBias
    if result.regime == MarketRegime.BULLISH:
        bias = "bullish"
    elif result.regime == MarketRegime.BEARISH:
        bias = "bearish"
    else:
        bias = "neutral"

    total_events = result.mss_count + result.sweep_count
    confidence = float(result.mss_count / max(total_events, 1))

    reasons: list[str] = []
    reasons.append(f"MarketStructure: Régimen {result.regime.value}")
    if result.mss_count:
        reasons.append(f"MarketStructure: {result.mss_count} MSS confirmado(s)")
    if result.sweep_count:
        reasons.append(f"MarketStructure: {result.sweep_count} sweep(s) de liquidez")
    if result.latest_event:
        reasons.append(f"MarketStructure: Último evento {result.latest_event.type.value}")

    return EngineFeatures(
        score=score,
        bias=bias,
        confidence=confidence,
        reasons=reasons[:4],
        engine_status="real",
    )


def _run_order_flow_delta(
    df: pd.DataFrame,
    *,
    real_microstructure: dict[str, Any] | None = None,
) -> EngineFeatures:
    if real_microstructure and real_microstructure.get("ok"):
        cvd = real_microstructure.get("cvd")
        if cvd is not None:
            bias: EngineBias = (
                "bullish" if float(cvd) > 0 else "bearish" if float(cvd) < 0 else "neutral"
            )
            score = float(np.clip(50.0 + float(cvd) * 2.0, 0.0, 100.0))
            return EngineFeatures(
                score=score,
                bias=bias,
                confidence=0.72,
                reasons=[f"OFDelta: BingX trade tape CVD {float(cvd):.2f}"],
                engine_status="real",
            )

    from backend.quant_engine.engines.technical.order_flow_delta_engine import (
        DeltaDirection,
        analyze_order_flow_delta_from_ohlcv,
    )

    result = analyze_order_flow_delta_from_ohlcv(df)
    if not result.ok:
        return _fallback(result.error or "OrderFlowDelta error")

    # Bias desde delta de la última barra
    bias: EngineBias
    if result.delta_bias == DeltaDirection.BULLISH:
        bias = "bullish"
    elif result.delta_bias == DeltaDirection.BEARISH:
        bias = "bearish"
    else:
        bias = "neutral"

    # Score desde CVD y divergencias bullish
    base_score = 50.0
    bull_divs = sum(1 for div in result.divergences if div.direction == DeltaDirection.BULLISH)
    bear_divs = sum(1 for div in result.divergences if div.direction == DeltaDirection.BEARISH)
    bull_absorptions = sum(1 for abs_ in result.absorptions if abs_.side.value == "Bullish")
    score = float(
        np.clip(
            base_score + bull_divs * 8.0 - bear_divs * 8.0 + bull_absorptions * 5.0,
            0.0,
            100.0,
        )
    )

    total = result.divergence_count + result.absorption_count
    confidence = float(result.divergence_count / max(total, 1))

    reasons: list[str] = []
    if result.latest_cvd != 0:
        direction_str = "positivo" if result.latest_cvd > 0 else "negativo"
        reasons.append(f"OFDelta: CVD {direction_str} ({result.latest_cvd:.1f})")
    if result.divergence_count:
        reasons.append(
            f"OFDelta: {result.divergence_count} divergencia(s) " f"({bull_divs}↑/{bear_divs}↓)"
        )
    if result.absorption_count:
        reasons.append(f"OFDelta: {result.absorption_count} barra(s) de absorción")

    return EngineFeatures(
        score=score,
        bias=bias,
        confidence=confidence,
        reasons=reasons[:4],
        engine_status="partial",  # sin tick data real → proxy CVD
    )


def _run_volume_profile(
    df: pd.DataFrame,
    *,
    real_microstructure: dict[str, Any] | None = None,
) -> EngineFeatures:
    if real_microstructure and real_microstructure.get("ok"):
        poc = real_microstructure.get("poc_price")
        last_close = float(df["close"].iloc[-1]) if len(df) else None
        if poc is not None and last_close:
            dist = (last_close - float(poc)) / max(float(poc), 1e-9)
            bias: EngineBias = (
                "bullish" if dist > 0.002 else "bearish" if dist < -0.002 else "neutral"
            )
            score = float(np.clip(50.0 + dist * 500.0, 0.0, 100.0))
            return EngineFeatures(
                score=score,
                bias=bias,
                confidence=0.65,
                reasons=[f"Volume profile: BingX tape POC {float(poc):.4f}"],
                engine_status="partial",
            )

    from backend.quant_engine.engines.technical.volume_node_engine import (
        analyze_volume_nodes_from_ohlcv,
    )

    result = analyze_volume_nodes_from_ohlcv(df)
    if not result.ok:
        return _fallback(result.error or "VolumeNode error")

    last_close = result.last_close
    poc = result.poc_price

    # Score base: posición relativa respecto al POC
    bias: EngineBias = "neutral"
    score = 50.0
    if (
        poc is not None
        and last_close is not None
        and _finite(poc)
        and _finite(last_close)
        and poc > 0
    ):
        pct_from_poc = (last_close - poc) / poc * 100.0
        # Precio sobre POC → soporte bajo → bullish
        score = float(np.clip(50.0 + pct_from_poc * 3.0, 0.0, 100.0))
        if last_close > poc:
            bias = "bullish"
        elif last_close < poc:
            bias = "bearish"

    # Ajuste por HVN/LVN cercanos
    if result.nearest_hvn_above is not None:
        # HVN encima → resistencia potencial → penalizar ligeramente
        score = float(np.clip(score - 3.0, 0.0, 100.0))
    if result.nearest_hvn_below is not None:
        # HVN debajo → soporte potencial → bonificar ligeramente
        score = float(np.clip(score + 3.0, 0.0, 100.0))

    confidence = float(result.node_count / max(result.node_count + 1, 1))

    reasons: list[str] = []
    if poc is not None:
        reasons.append(f"VolumeProfile: POC en {poc:.4f}")
    if result.hvn_count:
        reasons.append(f"VolumeProfile: {result.hvn_count} HVN / {result.lvn_count} LVN")
    if result.nearest_hvn_above:
        reasons.append(f"VolumeProfile: HVN resistencia en {result.nearest_hvn_above.price:.4f}")
    if result.nearest_hvn_below:
        reasons.append(f"VolumeProfile: HVN soporte en {result.nearest_hvn_below.price:.4f}")

    return EngineFeatures(
        score=score,
        bias=bias,
        confidence=confidence,
        reasons=reasons[:4],
        engine_status="partial",  # sin L2 real; proxy de perfil de volumen
    )


def _run_hmm(df: pd.DataFrame) -> EngineFeatures:
    from backend.quant_engine.engines.technical.hmm_engine import analyze_hmm_regime_from_ohlcv

    result = analyze_hmm_regime_from_ohlcv(df)
    if not result.ok:
        return _fallback(result.error or "HMM error")

    # Score desde signal y transition_risk
    regime_base = {
        "STABLE": 70.0,
        "SHIFTING": 50.0,
        "CRITICAL": 25.0,
    }.get(result.regime_signal, 50.0)

    # Ajuste por estado actual (BULL_QUIET es bullish → +10, CRISIS → -20)
    state_adj = {
        "BULL_QUIET": 10.0,
        "MEAN_REVERT": 0.0,
        "CRISIS": -20.0,
    }.get(result.current_label, 0.0)

    score = float(np.clip(regime_base + state_adj, 0.0, 100.0))

    # Bias desde label
    bias: EngineBias
    if result.current_label == "BULL_QUIET":
        bias = "bullish"
    elif result.current_label == "CRISIS":
        bias = "bearish"
    else:
        bias = "neutral"

    # Confidence = 1 - transition_risk
    confidence = float(np.clip(1.0 - result.transition_risk, 0.0, 1.0))

    reasons: list[str] = [
        f"HMM: Estado {result.current_label} (señal {result.regime_signal})",
        f"HMM: Riesgo de transición {result.transition_risk:.2f}",
    ]
    if result.state_probabilities:
        max_prob = max(result.state_probabilities)
        reasons.append(f"HMM: Probabilidad máxima de estado {max_prob:.1%}")

    return EngineFeatures(
        score=score,
        bias=bias,
        confidence=confidence,
        reasons=reasons[:4],
        engine_status="real",
    )


def _run_lob_dynamics(
    df: pd.DataFrame, *, real_microstructure: dict[str, Any] | None = None
) -> EngineFeatures:
    """Ejecuta el motor de LOB Dynamics. Si no hay datos L2, devuelve partial/fallback."""
    if (
        not real_microstructure
        or not real_microstructure.get("ok")
        or not real_microstructure.get("order_book")
    ):
        return _fallback("No L2 Order Book data provided by data layer")

    try:
        from backend.quant_engine.engines.technical.lob_dynamics_engine import SpoofingState
        from backend.services.bingx_l2_integration import order_book_dict_to_lob_analysis

        analysis = order_book_dict_to_lob_analysis(
            real_microstructure["order_book"],
            symbol=str(real_microstructure.get("venue_symbol") or ""),
            market_type="stock_perp",
        )
        if not analysis.ok or analysis.result is None:
            return _fallback(analysis.error or "LOB Dynamics unavailable")

        rho = float(analysis.result.imbalance_rho)
        score = 50.0
        bias: EngineBias = "neutral"
        if rho > 0.3:
            bias = "bullish"
            score = 65.0 + (rho * 30.0)
        elif rho < -0.3:
            bias = "bearish"
            score = 35.0 + (rho * 30.0)

        score = float(np.clip(score, 0.0, 100.0))
        reasons = [f"LOB: Imbalance de {rho:.2f} (Bid/Ask pressure)"]
        if analysis.result.spoofing_state is not SpoofingState.NORMAL:
            reasons.append("LOB: Posible spoofing detectado")

        return EngineFeatures(
            score=score, bias=bias, confidence=0.8, reasons=reasons, engine_status="real"
        )
    except Exception as exc:
        return _fallback(f"LOB Dynamics error: {exc}")


def _run_tpo(symbol: str, df: pd.DataFrame) -> EngineFeatures:
    try:
        from backend.quant_engine.engines.technical.tpo_skewness import (
            TPOSkewnessConfig,
            TPOSkewnessEngine,
        )

        engine = TPOSkewnessEngine(symbol, TPOSkewnessConfig(compact_level_limit=2500))
        engine.ingest_frame(df)
        result = engine.evaluate()
        if not result.ok:
            return _fallback(result.error or "TPO error")

        score = 50.0
        bias: EngineBias = "neutral"

        if result.skewness_value > 0.5:
            bias = "bullish"
            score = 70.0
        elif result.skewness_value < -0.5:
            bias = "bearish"
            score = 30.0

        reasons = [f"TPO: Sesgo {result.skewness_value:.2f} ({result.profile_shape.value})"]

        return EngineFeatures(
            score=score, bias=bias, confidence=0.7, reasons=reasons, engine_status="real"
        )
    except Exception as exc:
        return _fallback(f"TPO error: {exc}")


def _run_squeeze(df: pd.DataFrame) -> EngineFeatures:
    try:
        from backend.quant_engine.engines.technical.squeeze_ignition import SqueezeIgnitionEngine

        engine = SqueezeIgnitionEngine()
        result = engine.analyze(df)
        if not result.ok:
            return _fallback(result.error or "Squeeze error")

        score = 50.0
        bias: EngineBias = "neutral"
        if result.momentum > 0:
            bias = "bullish"
            score = 60.0 + (result.momentum * 10.0)
        elif result.momentum < 0:
            bias = "bearish"
            score = 40.0 + (result.momentum * 10.0)

        score = float(np.clip(score, 0.0, 100.0))
        reasons = [f"Squeeze: Momentum {result.momentum:.2f}"]
        if result.is_squeezing:
            reasons.append("Squeeze: Compresión activa detectada")

        return EngineFeatures(
            score=score,
            bias=bias,
            confidence=0.75 if result.is_squeezing else 0.4,
            reasons=reasons,
            engine_status="real",
        )
    except Exception as exc:
        return _fallback(f"Squeeze error: {exc}")


def _run_footprint(symbol: str, df: pd.DataFrame) -> EngineFeatures:
    try:
        from backend.quant_engine.engines.technical.vsa_footprint_engine import VSAFootprintEngine

        result = VSAFootprintEngine().analyze_footprints(df, ticker=symbol)
        if not result.ok:
            return _fallback(result.error or "Footprint error")

        score = 50.0
        bias: EngineBias = "neutral"
        reasons = []

        last_close = float(df["close"].iloc[-1])
        if result.nearest_support and result.nearest_resistance:
            dist_supp = last_close - result.nearest_support
            dist_res = result.nearest_resistance - last_close
            if dist_supp < dist_res:
                bias = "bullish"
                score = 65.0
            else:
                bias = "bearish"
                score = 35.0

        if result.active_levels:
            reasons.append(f"Footprint: {len(result.active_levels)} niveles activos")

        return EngineFeatures(
            score=score, bias=bias, confidence=0.6, reasons=reasons, engine_status="real"
        )
    except Exception as exc:
        return _fallback(f"Footprint error: {exc}")


# ─────────────────────────────────────────────────────────────────────────────
# §4  Helpers internos
# ─────────────────────────────────────────────────────────────────────────────


def _bars_to_dataframe(bars: list[dict[str, Any]]) -> pd.DataFrame:
    """Convertir lista de dicts OHLCV a DataFrame limpio con columnas estándar."""
    if not bars:
        return pd.DataFrame(columns=["open", "high", "low", "close", "volume"])

    rows: list[dict[str, float]] = []
    for raw in bars:
        try:
            close_raw = raw.get("close", raw.get("c"))
            if close_raw is None:
                continue
            close = float(close_raw)
            open_price = float(raw.get("open", raw.get("o", close)))
            high = float(raw.get("high", raw.get("h", close)))
            low = float(raw.get("low", raw.get("l", close)))
            volume = float(raw.get("volume", raw.get("v", 0.0)) or 0.0)
            if not all(map(math.isfinite, (open_price, high, low, close, volume))):
                continue
            if min(open_price, high, low, close) <= 0:
                continue
            rows.append(
                {
                    "open": open_price,
                    "high": high,
                    "low": low,
                    "close": close,
                    "volume": volume,
                }
            )
        except (TypeError, ValueError):
            continue

    if not rows:
        return pd.DataFrame(columns=["open", "high", "low", "close", "volume"])

    df = pd.DataFrame(rows)
    df["date"] = range(len(df))  # índice sintético; los motores aceptan int
    return df


def _fallback(reason: str = "") -> EngineFeatures:
    """Retornar un EngineFeatures neutral de fallback."""
    return EngineFeatures(
        score=50.0,
        bias="neutral",
        confidence=0.0,
        reasons=[f"Fallback: {reason}"] if reason else [],
        engine_status="fallback",
    )


def _finite(value: float | None) -> bool:
    return value is not None and math.isfinite(float(value))


# ─────────────────────────────────────────────────────────────────────────────
# §5  Utilidad de agregación multi-motor / multi-timeframe
# ─────────────────────────────────────────────────────────────────────────────


def aggregate_engine_features(
    features_by_tf: dict[str, dict[str, EngineFeatures]],
    weights_by_tf: dict[str, float] | None = None,
) -> dict[str, EngineFeatures]:
    """Agregar features de múltiples timeframes en un único dict de motores.

    Args:
        features_by_tf: {timeframe: {engine_key: EngineFeatures}}
        weights_by_tf:  Peso de cada timeframe; si None, peso uniforme.

    Returns:
        {engine_key: EngineFeatures} con score/bias/confidence ponderados.
    """
    if not features_by_tf:
        return {key: _fallback("No timeframe data") for key in _ALL_ENGINE_KEYS}

    tfs = list(features_by_tf.keys())
    if weights_by_tf is None:
        weights_by_tf = {tf: 1.0 for tf in tfs}

    aggregated: dict[str, EngineFeatures] = {}
    for engine_key in _ALL_ENGINE_KEYS:
        weighted_score = 0.0
        total_weight = 0.0
        all_reasons: list[str] = []
        best_status: EngineStatus = "fallback"
        best_confidence = 0.0
        bias_votes: dict[str, float] = {"bullish": 0.0, "bearish": 0.0, "neutral": 0.0}

        for tf, features_map in features_by_tf.items():
            feat = features_map.get(engine_key)
            if feat is None:
                continue
            w = weights_by_tf.get(tf, 1.0)
            weighted_score += feat.score * w
            total_weight += w
            bias_votes[feat.bias] = bias_votes.get(feat.bias, 0.0) + w
            all_reasons.extend(feat.reasons)
            if feat.confidence > best_confidence:
                best_confidence = feat.confidence
            # Prioridad: real > partial > fallback
            if best_status == "fallback" or (
                best_status == "partial" and feat.engine_status == "real"
            ):
                best_status = feat.engine_status

        if total_weight <= 0:
            aggregated[engine_key] = _fallback("No engine data across timeframes")
            continue

        final_score = weighted_score / total_weight
        dominant_bias: EngineBias = max(bias_votes, key=lambda k: bias_votes[k])

        aggregated[engine_key] = EngineFeatures(
            score=final_score,
            bias=dominant_bias,
            confidence=best_confidence,
            reasons=list(dict.fromkeys(all_reasons))[:6],  # deduplicar, max 6
            engine_status=best_status,
        )

    return aggregated


# ─────────────────────────────────────────────────────────────────────────────
# §6  SYMMETRIC ENGINE ADAPTERS (additive, bidirectional)
# ─────────────────────────────────────────────────────────────────────────────


def _run_smc_updated(df, ticker, timeframe, weight: float) -> dict[str, Any]:
    """Reemplaza _run_smc para output simétrico [-weight, +weight]."""
    from backend.quant_engine.engines.technical.smc import SMCEngine

    engine = SMCEngine()
    result = engine.analyze(df, ticker=ticker, timeframe=timeframe)

    if not result.ok:
        return {"score": 0.0, "direction": "NEUTRAL", "raw": result}

    weighted = result.composite_score * weight / 100.0

    return {
        "score": weighted,
        "direction": result.direction,
        "raw": result,
    }


def _run_vsa_updated(df, ticker, timeframe, weight: float) -> dict[str, Any]:
    """Reemplaza _run_vsa. Score simétrico [-weight, +weight]."""
    from backend.quant_engine.engines.technical.vsa import VSAEngine

    engine = VSAEngine()
    result = engine.analyze(df, ticker=ticker, timeframe=timeframe)

    weighted = result.composite_score * weight / 100.0

    return {
        "score": weighted,
        "direction": result.direction,
        "raw": result,
    }


def aggregate_engine_features_symmetric(engine_results: dict) -> dict[str, Any]:
    """
    Agrega scores de todos los engines simétricamente [-1, +1].
    Negativo → señal SHORT, positivo → señal LONG.
    """
    import statistics

    _ENGINE_WEIGHTS_SYM = {
        "smc": 2.5,
        "fvg": 1.8,
        "vsa": 2.2,
        "structure": 2.0,
        "ofd": 1.5,
        "volume": 1.0,
        "hmm": 1.8,
        "lob_dynamics": 1.5,
        "tpo": 1.8,
        "squeeze": 1.4,
        "footprint": 1.5,
    }

    total_weight = sum(_ENGINE_WEIGHTS_SYM.values())
    weighted_sum = 0.0
    polarities = {}
    missing_engines = []

    for engine_name, weight in _ENGINE_WEIGHTS_SYM.items():
        result = engine_results.get(engine_name)
        if result is None:
            missing_engines.append(engine_name)
            continue
        score = result.get("score", 0.0)
        weighted_sum += score * weight
        polarities[engine_name] = 1 if score > 0.2 else (-1 if score < -0.2 else 0)

    final_score = weighted_sum / total_weight if total_weight > 0 else 0.0
    final_score = max(-1.0, min(1.0, final_score))

    pol_values = list(polarities.values())
    dispersion = statistics.stdev(pol_values) if len(pol_values) > 1 else 0.0

    conflict = dispersion > 0.7 and abs(final_score) < 0.4

    if conflict:
        direction = "CONFLICT"
        action = "CONFLICT"
    elif final_score >= 0.50:
        direction, action = "LONG", "BUY"
    elif final_score >= 0.25:
        direction, action = "LONG", "BUY_WATCH"
    elif final_score <= -0.50:
        direction, action = "SHORT", "SELL"
    elif final_score <= -0.25:
        direction, action = "SHORT", "SELL_WATCH"
    else:
        direction, action = "NEUTRAL", "WAIT"

    return {
        "final_score": round(final_score, 4),
        "direction": direction,
        "action": action,
        "polarities": polarities,
        "dispersion": round(dispersion, 4),
        "conflict": conflict,
        "missing_engines": missing_engines,
        "engine_scores": {name: round(engine_results[name]["score"], 4) for name in engine_results},
    }
