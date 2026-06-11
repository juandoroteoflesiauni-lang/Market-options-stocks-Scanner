"""
backend/engine/metrics/squeeze_ignition.py
Sector: IA / Probabilístico
[ARCH-1, PD-4]

Squeeze Ignition Engine — Gamma/Short Squeeze vulnerability and ignition detector.
Stateless and Pydantic-based implementation.
"""

from __future__ import annotations

import logging
import math
from enum import Enum

from pydantic import BaseModel, ConfigDict

from backend.models.result import Result

logger = logging.getLogger("quantumbeta.engines.squeeze_ignition")


class SqueezeState(Enum):
    """
    Estados posibles de la máquina de estados del SqueezeIgnitionEngine.
    """

    MONITORING = "MONITORING"
    VULNERABLE = "VULNERABLE"
    IGNITION = "IGNITION"
    COOLING = "COOLING"


class SignalType(Enum):
    """Tipos de señal operativa emitidos por el motor."""

    NONE = "NONE"
    LONG_MOMENTUM_IGNITION = "LONG_MOMENTUM_IGNITION"
    TAKE_PROFIT_SCALING = "TAKE_PROFIT_SCALING"
    ALERT_VULNERABLE = "ALERT_VULNERABLE"


class UnderlyingData(BaseModel):
    """Snapshot de datos del activo subyacente para un periodo temporal dado."""

    model_config = ConfigDict(frozen=True)

    ticker: str
    spot_price: float
    prev_spot_price: float
    volume: float
    volume_sma_20: float
    short_interest_ratio: float
    days_to_cover: float


class OptionChainData(BaseModel):
    """Métricas agregadas de la cadena de opciones relevantes para el squeeze."""

    model_config = ConfigDict(frozen=True)

    call_volume: float
    call_volume_sma_20: float
    call_open_interest: float
    put_call_ratio_volume: float
    dealer_net_gamma: float
    call_wall_level: float
    gamma_zero_level: float


class SqueezeSignal(BaseModel):
    """Señal operativa emitida por el SqueezeIgnitionEngine."""

    model_config = ConfigDict(frozen=True)

    signal_type: SignalType
    state: SqueezeState
    new_cooling_counter: int
    new_ignition_price: float | None
    squeeze_vulnerability_score: float
    trigger_reasons: list[str]
    spot_price: float
    call_wall_level: float
    suggested_entry: float | None = None
    take_profit_levels: list[float] = []
    notes: str = ""


# Umbrales para el Squeeze_Vulnerability_Score
SVS_VULNERABLE_THRESHOLD: float = 65.0
SVS_IGNITION_THRESHOLD: float = 85.0

# Short Interest crítico (% del float)
SI_HIGH_THRESHOLD: float = 20.0
SI_EXTREME_THRESHOLD: float = 50.0

# Days-to-Cover crítico
DTC_HIGH_THRESHOLD: float = 5.0
DTC_EXTREME_THRESHOLD: float = 10.0

# Multiplicador de volumen de Calls sobre su SMA para considerarlo "inusual"
CALL_VOL_MULTIPLIER_HIGH: float = 3.0
CALL_VOL_MULTIPLIER_EXTREME: float = 6.0

# Multiplicador de volumen del subyacente para "aceleración de volumen"
VOLUME_ACCELERATION_MULTIPLIER: float = 2.5

# Tolerancia de precio para considerar que el spot ha "cruzado" el Call Wall
WALL_CROSS_TOLERANCE_PCT: float = 0.5

# Periodos en COOLING antes de volver a MONITORING
COOLING_PERIODS: int = 4


def _sigmoid_normalize(value: float, low: float, high: float) -> float:
    """Normaliza un valor entre [low, high] al rango [0, 1] usando una curva sigmoide."""
    if high <= low:
        return 1.0
    x = (value - low) / (high - low)
    x = max(0.0, min(x, 1.0))
    steepness = 6.0
    sigmoid = 1.0 / (1.0 + math.exp(-steepness * (x - 0.5)))
    s0 = 1.0 / (1.0 + math.exp(steepness * 0.5))
    s1 = 1.0 / (1.0 + math.exp(-steepness * 0.5))
    return (sigmoid - s0) / (s1 - s0)


def _calculate_squeeze_vulnerability_score(
    u: UnderlyingData,
    o: OptionChainData,
) -> float:
    """Calcula el Squeeze_Vulnerability_Score ponderando múltiples factores."""
    score = 0.0

    # ── A: Short Interest (40 pts) ─────────────────────────────────────────
    si_score = 0.0
    si_pct = u.short_interest_ratio
    if si_pct >= 100.0:
        si_score += 25.0
    elif si_pct >= SI_EXTREME_THRESHOLD:
        si_score += 25.0 * _sigmoid_normalize(si_pct, SI_EXTREME_THRESHOLD, 100.0)
    elif si_pct >= SI_HIGH_THRESHOLD:
        si_score += 15.0 * _sigmoid_normalize(si_pct, SI_HIGH_THRESHOLD, SI_EXTREME_THRESHOLD)

    dtc = u.days_to_cover
    if dtc >= DTC_EXTREME_THRESHOLD:
        si_score += 15.0
    elif dtc >= DTC_HIGH_THRESHOLD:
        si_score += 15.0 * _sigmoid_normalize(dtc, DTC_HIGH_THRESHOLD, DTC_EXTREME_THRESHOLD)

    score += min(si_score, 40.0)

    # ── B: Presión de Opciones Call (35 pts) ──────────────────────────────
    call_score = 0.0
    if o.call_volume_sma_20 > 0:
        call_multiplier = o.call_volume / o.call_volume_sma_20
        if call_multiplier >= CALL_VOL_MULTIPLIER_EXTREME:
            call_score = 35.0
        elif call_multiplier >= CALL_VOL_MULTIPLIER_HIGH:
            call_score = 35.0 * _sigmoid_normalize(
                call_multiplier,
                CALL_VOL_MULTIPLIER_HIGH,
                CALL_VOL_MULTIPLIER_EXTREME,
            )

    score += min(call_score, 35.0)

    # ── C: Gamma Neta del Dealer (15 pts) ─────────────────────────────────
    if o.dealer_net_gamma < 0:
        gamma_intensity = min(abs(o.dealer_net_gamma) / 1_000_000.0, 1.0)
        score += 15.0 * gamma_intensity

    # ── D: Put/Call Ratio (10 pts) ─────────────────────────────────────────
    pcr = o.put_call_ratio_volume
    if pcr >= 2.5:
        score += 10.0
    elif pcr >= 1.5:
        score += 10.0 * _sigmoid_normalize(pcr, 1.5, 2.5)

    return round(min(score, 100.0), 2)


def _calculate_take_profit_levels(
    entry: float,
    options: OptionChainData,
    si_ratio: float,
) -> list[float]:
    """Calcula niveles de toma de ganancias."""
    cw = options.call_wall_level

    tp1 = round(entry * 1.15, 2)
    tp2 = round(cw * 1.10, 2)
    tp3 = round(cw * 1.25, 2)

    si_extension_factor = 1.0 + (min(si_ratio, 200.0) / 200.0) * 0.75
    tp4 = round(entry * si_extension_factor * 1.40, 2)

    return sorted({tp1, tp2, tp3, tp4})


class SqueezeIgnitionEngine:
    """
    Motor de detección de Gamma Squeeze y Short Squeeze para QuantumBeta Terminal.
    Puremente stateless.
    """

    def __init__(self, ticker: str) -> None:
        self.ticker = ticker

    def analyze(
        self,
        underlying: UnderlyingData,
        options: OptionChainData,
        prev_state: SqueezeState,
        cooling_counter: int,
        ignition_price: float | None,
    ) -> Result[SqueezeSignal]:
        """
        Analiza las condiciones actuales y retorna una señal de squeeze.
        """
        try:
            # Validations
            if (
                math.isnan(underlying.spot_price)
                or math.isnan(underlying.prev_spot_price)
                or math.isnan(underlying.volume)
                or math.isnan(underlying.volume_sma_20)
                or math.isnan(underlying.short_interest_ratio)
                or math.isnan(underlying.days_to_cover)
            ):
                return Result.failure(reason="UnderlyingData contains NaN values")

            if (
                math.isnan(options.call_volume)
                or math.isnan(options.call_volume_sma_20)
                or math.isnan(options.call_open_interest)
                or math.isnan(options.put_call_ratio_volume)
                or math.isnan(options.dealer_net_gamma)
                or math.isnan(options.call_wall_level)
                or math.isnan(options.gamma_zero_level)
            ):
                return Result.failure(reason="OptionChainData contains NaN values")

            if underlying.spot_price <= 0.0 or underlying.prev_spot_price <= 0.0:
                return Result.failure(reason="Spot price must be positive")

            if underlying.volume < 0.0 or underlying.volume_sma_20 <= 0.0:
                return Result.failure(
                    reason="Volume must be non-negative, and SMA_20 must be positive"
                )

            if options.call_volume < 0.0 or options.call_volume_sma_20 <= 0.0:
                return Result.failure(
                    reason="Call volume must be non-negative, and SMA_20 must be positive"
                )

            if options.call_wall_level <= 0.0 or options.gamma_zero_level <= 0.0:
                return Result.failure(reason="Call wall and gamma zero levels must be positive")

            # Calculations
            svs = _calculate_squeeze_vulnerability_score(underlying, options)

            effective_wall = options.call_wall_level * (1 + WALL_CROSS_TOLERANCE_PCT / 100)
            effective_gamma = options.gamma_zero_level * (1 + WALL_CROSS_TOLERANCE_PCT / 100)
            wall_crossed = (
                underlying.prev_spot_price < options.call_wall_level
                and underlying.spot_price >= effective_wall
            ) or (
                underlying.prev_spot_price < options.gamma_zero_level
                and underlying.spot_price >= effective_gamma
            )

            vol_accel = (
                underlying.volume / underlying.volume_sma_20
            ) >= VOLUME_ACCELERATION_MULTIPLIER
            neg_gamma_zone = options.dealer_net_gamma < 0.0

            current_state = prev_state
            new_cooling_counter = cooling_counter
            new_ignition_price = ignition_price

            if prev_state == SqueezeState.MONITORING:
                if svs >= SVS_VULNERABLE_THRESHOLD:
                    current_state = SqueezeState.VULNERABLE
                    signal = SqueezeSignal(
                        signal_type=SignalType.ALERT_VULNERABLE,
                        state=current_state,
                        new_cooling_counter=new_cooling_counter,
                        new_ignition_price=new_ignition_price,
                        squeeze_vulnerability_score=svs,
                        trigger_reasons=[
                            f"SVS={svs:.1f} superó umbral VULNERABLE ({SVS_VULNERABLE_THRESHOLD})",
                            f"Short Interest={underlying.short_interest_ratio:.1f}% del float",
                            f"Call Vol={underlying.volume:.0f} "
                            f"({options.call_volume/options.call_volume_sma_20:.1f}x SMA20)",
                        ],
                        spot_price=underlying.spot_price,
                        call_wall_level=options.call_wall_level,
                        notes=(
                            "Condiciones previas al squeeze identificadas. "
                            "Monitoreo intensivo activado. Sin señal de entrada aún."
                        ),
                    )
                else:
                    signal = SqueezeSignal(
                        signal_type=SignalType.NONE,
                        state=current_state,
                        new_cooling_counter=new_cooling_counter,
                        new_ignition_price=new_ignition_price,
                        squeeze_vulnerability_score=svs,
                        trigger_reasons=["SVS por debajo del umbral. Condiciones normales."],
                        spot_price=underlying.spot_price,
                        call_wall_level=options.call_wall_level,
                    )

            elif prev_state == SqueezeState.VULNERABLE:
                if svs < SVS_VULNERABLE_THRESHOLD - 5.0:
                    current_state = SqueezeState.MONITORING
                    signal = SqueezeSignal(
                        signal_type=SignalType.NONE,
                        state=current_state,
                        new_cooling_counter=new_cooling_counter,
                        new_ignition_price=new_ignition_price,
                        squeeze_vulnerability_score=svs,
                        trigger_reasons=["SVS declinó bajo el umbral. Vuelta a MONITORING."],
                        spot_price=underlying.spot_price,
                        call_wall_level=options.call_wall_level,
                    )
                elif svs >= SVS_IGNITION_THRESHOLD and wall_crossed and vol_accel:
                    current_state = SqueezeState.IGNITION
                    new_ignition_price = underlying.spot_price
                    tp_levels = _calculate_take_profit_levels(
                        entry=underlying.spot_price,
                        options=options,
                        si_ratio=underlying.short_interest_ratio,
                    )
                    reasons = [
                        f"✅ SVS={svs:.1f} ≥ {SVS_IGNITION_THRESHOLD} (CRÍTICO)",
                        f"✅ Cruce del Call Wall / Gamma Zero: "
                        f"spot={underlying.spot_price:.2f} > wall={options.call_wall_level:.2f}",
                        f"✅ Aceleración de volumen: "
                        f"{underlying.volume / underlying.volume_sma_20:.1f}x SMA20",
                    ]
                    if neg_gamma_zone:
                        reasons.append(
                            f"✅ Dealer Gamma NEGATIVA ({options.dealer_net_gamma:,.0f}): "
                            "bucle de Delta-Hedging activo"
                        )
                    if underlying.short_interest_ratio >= SI_HIGH_THRESHOLD:
                        reasons.append(
                            f"✅ Short Interest={underlying.short_interest_ratio:.1f}% "
                            f"(DTC={underlying.days_to_cover:.1f}d): "
                            f"presión de cubrir posiciones cortas"
                        )
                    signal = SqueezeSignal(
                        signal_type=SignalType.LONG_MOMENTUM_IGNITION,
                        state=current_state,
                        new_cooling_counter=new_cooling_counter,
                        new_ignition_price=new_ignition_price,
                        squeeze_vulnerability_score=svs,
                        trigger_reasons=reasons,
                        spot_price=underlying.spot_price,
                        call_wall_level=options.call_wall_level,
                        suggested_entry=underlying.spot_price,
                        take_profit_levels=tp_levels,
                        notes=(
                            "⚡ BUCLE DE RETROALIMENTACIÓN ACTIVO. "
                            "Delta-Hedging de MMs + cubrimiento de shorts = "
                            "presión compradora convergente. "
                            "Gestión de riesgo estricta. "
                            "No perseguir entradas lejanas al trigger."
                        ),
                    )
                else:
                    signal = SqueezeSignal(
                        signal_type=SignalType.ALERT_VULNERABLE,
                        state=current_state,
                        new_cooling_counter=new_cooling_counter,
                        new_ignition_price=new_ignition_price,
                        squeeze_vulnerability_score=svs,
                        trigger_reasons=[
                            f"SVS={svs:.1f}. Esperando convergencia de gatillos.",
                            f"Cruce de Wall: {'SÍ' if wall_crossed else 'NO'}",
                            f"Aceleración de Vol: {'SÍ' if vol_accel else 'NO'}",
                            f"SVS ≥ {SVS_IGNITION_THRESHOLD}: "
                            f"{'SÍ' if svs >= SVS_IGNITION_THRESHOLD else 'NO'}",
                        ],
                        spot_price=underlying.spot_price,
                        call_wall_level=options.call_wall_level,
                    )

            elif prev_state == SqueezeState.IGNITION:
                current_state = SqueezeState.COOLING
                new_cooling_counter = 0
                tp_levels = _calculate_take_profit_levels(
                    entry=ignition_price or underlying.spot_price,
                    options=options,
                    si_ratio=underlying.short_interest_ratio,
                )
                signal = SqueezeSignal(
                    signal_type=SignalType.TAKE_PROFIT_SCALING,
                    state=current_state,
                    new_cooling_counter=new_cooling_counter,
                    new_ignition_price=new_ignition_price,
                    squeeze_vulnerability_score=svs,
                    trigger_reasons=[
                        "Periodo post-IGNITION. Gestión de posición activa.",
                        (
                            f"Precio de entrada referencia: {ignition_price:.2f}"
                            if ignition_price
                            else "Precio de entrada referencia: N/A"
                        ),
                        f"Precio actual: {underlying.spot_price:.2f}",
                    ],
                    spot_price=underlying.spot_price,
                    call_wall_level=options.call_wall_level,
                    take_profit_levels=tp_levels,
                    notes=(
                        "📊 TOMA DE GANANCIAS ESCALONADA. "
                        "Reducir posición en los niveles indicados. "
                        "PROHIBIDO abrir cortos. Solo gestión de largo existente."
                    ),
                )

            elif prev_state == SqueezeState.COOLING:
                new_cooling_counter = cooling_counter + 1
                if new_cooling_counter >= COOLING_PERIODS:
                    current_state = SqueezeState.MONITORING
                    new_ignition_price = None
                    new_cooling_counter = 0
                    signal = SqueezeSignal(
                        signal_type=SignalType.NONE,
                        state=current_state,
                        new_cooling_counter=new_cooling_counter,
                        new_ignition_price=new_ignition_price,
                        squeeze_vulnerability_score=svs,
                        trigger_reasons=[
                            f"Periodo de cooling completado ({COOLING_PERIODS} periodos). "
                            "Vuelta a MONITORING."
                        ],
                        spot_price=underlying.spot_price,
                        call_wall_level=options.call_wall_level,
                    )
                else:
                    tp_levels = _calculate_take_profit_levels(
                        entry=ignition_price or underlying.spot_price,
                        options=options,
                        si_ratio=underlying.short_interest_ratio,
                    )
                    signal = SqueezeSignal(
                        signal_type=SignalType.TAKE_PROFIT_SCALING,
                        state=current_state,
                        new_cooling_counter=new_cooling_counter,
                        new_ignition_price=new_ignition_price,
                        squeeze_vulnerability_score=svs,
                        trigger_reasons=[
                            f"COOLING periodo {new_cooling_counter}/{COOLING_PERIODS}.",
                            "Continúa la gestión escalonada de la posición larga.",
                        ],
                        spot_price=underlying.spot_price,
                        call_wall_level=options.call_wall_level,
                        take_profit_levels=tp_levels,
                        notes=(
                            "📊 Seguir el plan de salida escalonada. "
                            "No añadir ni abrir nuevas posiciones cortas."
                        ),
                    )

            return Result.success(signal)

        except Exception as e:
            logger.error("SqueezeIgnition engine analysis failed: %s", e)
            return Result.failure(reason=f"SqueezeIgnition engine analysis failed: {e}")
