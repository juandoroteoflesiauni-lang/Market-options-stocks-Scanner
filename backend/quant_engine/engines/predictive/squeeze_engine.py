"""
================================================================================
  QuantumBeta Terminal — Core Module
  squeeze_ignition_engine.py
  Módulo: Squeeze_Ignition_Engine  v1.0.0

  Detecta y señaliza condiciones previas a un Gamma Squeeze / Short Squeeze
  mediante análisis de la mecánica de cobertura Delta de Market Makers y
  métricas de Short Interest.

  Arquitectura interna: Máquina de Estados Finitos (FSM)
    MONITORING → VULNERABLE → IGNITION → COOLING

  Política operativa: LONG ONLY / MOMENTUM
  No se emiten señales de venta en corto bajo ninguna circunstancia.

  Basado en la teoría del bucle de retroalimentación Delta-Hedging descrita en:
  - Carbone, O. (2021). "How options have affected short squeeze phenomenon."
    Tesi di Laurea, LUISS, Roma. (Matrícula 719761)
  - Brunnermeier & Pedersen (2005). "Predator Trading." Journal of Finance.

  Autor  : QuantumBeta Engineering Team
  Fecha  : 2026
================================================================================
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from enum import Enum, auto

# ──────────────────────────────────────────────────────────────────────────────
#  TIPOS Y ESTRUCTURAS DE DATOS
# ──────────────────────────────────────────────────────────────────────────────


class SqueezeState(Enum):
    """
    Estados posibles de la máquina de estados del Squeeze_Ignition_Engine.

    MONITORING : Observación pasiva. No se detectan condiciones especiales.
    VULNERABLE : Condiciones previas al squeeze identificadas (score alto).
    IGNITION   : ¡Bucle de retroalimentación activo! Señal LONG_MOMENTUM_IGNITION.
    COOLING    : Post-evento. Solo se emiten señales de TAKE_PROFIT_SCALING.
    """

    MONITORING = auto()
    VULNERABLE = auto()
    IGNITION = auto()
    COOLING = auto()


class SignalType(Enum):
    """Tipos de señal operativa emitidos por el motor."""

    NONE = "NONE"
    LONG_MOMENTUM_IGNITION = "LONG_MOMENTUM_IGNITION"
    TAKE_PROFIT_SCALING = "TAKE_PROFIT_SCALING"
    ALERT_VULNERABLE = "ALERT_VULNERABLE"


@dataclass
class UnderlyingData:
    """
    Snapshot de datos del activo subyacente para un periodo temporal dado.

    Attributes
    ----------
    ticker                : Símbolo del activo (ej. "GME").
    spot_price            : Precio actual del mercado.
    prev_spot_price       : Precio de cierre del periodo anterior.
    volume                : Volumen de compra en el periodo actual.
    volume_sma_20         : Media móvil simple del volumen a 20 periodos.
    short_interest_ratio  : Porcentaje del float vendido en corto (0–100).
    days_to_cover         : Días necesarios para cubrir posiciones cortas
                            basándose en el volumen promedio diario.
    """

    ticker: str
    spot_price: float
    prev_spot_price: float
    volume: float
    volume_sma_20: float
    short_interest_ratio: float  # %, ej. 140.0 = 140% del float
    days_to_cover: float


@dataclass
class OptionChainData:
    """
    Métricas agregadas de la cadena de opciones relevantes para el squeeze.

    Attributes
    ----------
    call_volume           : Volumen total de contratos Call en el periodo.
    call_volume_sma_20    : Media móvil del volumen de Calls a 20 periodos.
    call_open_interest    : Open Interest total de contratos Call.
    put_call_ratio_volume : Ratio put/call por volumen.
    dealer_net_gamma      : Gamma neta estimada de los Dealers/Market Makers.
                            Negativo = los MMs están cortos en Gamma (riesgo
                            de bucle de cobertura alcista).
    call_wall_level       : Precio de ejercicio con mayor OI de Calls
                            (nivel de resistencia por Gamma; el "Call Wall").
    gamma_zero_level      : Precio donde la Gamma neta del dealer es cero
                            (cruce = aceleración de cobertura).
    """

    call_volume: float
    call_volume_sma_20: float
    call_open_interest: float
    put_call_ratio_volume: float
    dealer_net_gamma: float  # negativo = MMs cortos en Gamma
    call_wall_level: float
    gamma_zero_level: float


@dataclass
class SqueezeSignal:
    """
    Señal operativa emitida por el Squeeze_Ignition_Engine.

    Attributes
    ----------
    signal_type           : Tipo de señal (LONG_MOMENTUM_IGNITION, etc.).
    state                 : Estado de la FSM al momento de la emisión.
    squeeze_vulnerability_score : Score calculado (0–100).
    trigger_reasons       : Lista de razones que activaron el trigger.
    spot_price            : Precio del activo en el momento de la señal.
    call_wall_level       : Nivel del Call Wall detectado.
    suggested_entry       : Precio sugerido de entrada (solo informativo).
    take_profit_levels    : Niveles escalonados de toma de ganancias.
    notes                 : Información adicional y advertencias.
    """

    signal_type: SignalType
    state: SqueezeState
    squeeze_vulnerability_score: float
    trigger_reasons: list[str]
    spot_price: float
    call_wall_level: float
    suggested_entry: float | None = None
    take_profit_levels: list[float] = field(default_factory=list)
    notes: str = ""


# ──────────────────────────────────────────────────────────────────────────────
#  CONSTANTES DE CALIBRACIÓN
# ──────────────────────────────────────────────────────────────────────────────

# Umbrales para el Squeeze_Vulnerability_Score
SVS_VULNERABLE_THRESHOLD: float = 65.0  # Entra en estado VULNERABLE
SVS_IGNITION_THRESHOLD: float = 85.0  # Condición necesaria para IGNITION

# Short Interest crítico (% del float)
SI_HIGH_THRESHOLD: float = 20.0  # Nivel alto de SI
SI_EXTREME_THRESHOLD: float = 50.0  # Nivel extremo (>50% = casi seguro squeeze)

# Days-to-Cover crítico
DTC_HIGH_THRESHOLD: float = 5.0
DTC_EXTREME_THRESHOLD: float = 10.0

# Multiplicador de volumen de Calls sobre su SMA para considerarlo "inusual"
CALL_VOL_MULTIPLIER_HIGH: float = 3.0  # 3x la SMA = inusual
CALL_VOL_MULTIPLIER_EXTREME: float = 6.0  # 6x la SMA = extremo

# Multiplicador de volumen del subyacente para "aceleración de volumen"
VOLUME_ACCELERATION_MULTIPLIER: float = 2.5

# Tolerancia de precio para considerar que el spot ha "cruzado" el Call Wall
WALL_CROSS_TOLERANCE_PCT: float = 0.5  # 0.5% sobre el Call Wall

# Periodos en COOLING antes de volver a MONITORING
# El contador se incrementa en cada evaluación dentro del estado COOLING.
# El estado IGNITION ya consume el primer periodo al hacer la transición.
# Con 4, la secuencia es: IGNITION(P0) → COOLING x4 → MONITORING.
COOLING_PERIODS: int = 4


# ──────────────────────────────────────────────────────────────────────────────
#  MOTOR PRINCIPAL
# ──────────────────────────────────────────────────────────────────────────────


class SqueezeIgnitionEngine:
    """
    Motor de detección de Gamma Squeeze y Short Squeeze para QuantumBeta Terminal.

    Opera como una Máquina de Estados Finitos (FSM) con cuatro estados:
        MONITORING → VULNERABLE → IGNITION → COOLING

    La lógica es LONG ONLY. Nunca emite señales de venta en corto.
    Tras un evento IGNITION, solo emite señales de TAKE_PROFIT_SCALING.

    Ejemplo de uso
    --------------
    >>> engine = SqueezeIgnitionEngine(ticker="GME")
    >>> signal = engine.evaluate(underlying_data, option_chain_data)
    >>> print(signal.signal_type)
    """

    def __init__(self, ticker: str, verbose: bool = True) -> None:
        """
        Inicializa el motor para un activo específico.

        Parameters
        ----------
        ticker  : Símbolo del activo a monitorear.
        verbose : Si True, imprime logs de estado en cada evaluación.
        """
        self.ticker: str = ticker
        self.verbose: bool = verbose
        self._state: SqueezeState = SqueezeState.MONITORING
        self._cooling_counter: int = 0
        self._svs_history: list[float] = []
        self._ignition_price: float | None = None

    # ── Propiedad de estado (solo lectura externa) ─────────────────────────────

    @property
    def state(self) -> SqueezeState:
        """Estado actual de la FSM."""
        return self._state

    # ── Método principal de evaluación ────────────────────────────────────────

    def evaluate(
        self,
        underlying: UnderlyingData,
        options: OptionChainData,
    ) -> SqueezeSignal:
        """
        Evalúa el periodo temporal actual y devuelve una señal operativa.

        Implementa la lógica completa de la FSM:
          1. Calcula el Squeeze_Vulnerability_Score.
          2. Evalúa las condiciones de transición de estado.
          3. Emite la señal correspondiente al estado resultante.

        Parameters
        ----------
        underlying : Datos del activo subyacente en el periodo actual.
        options    : Datos de la cadena de opciones en el periodo actual.

        Returns
        -------
        SqueezeSignal con tipo de señal, estado FSM, score y metadatos.
        """
        svs = self._calculate_squeeze_vulnerability_score(underlying, options)
        self._svs_history.append(svs)

        # Evaluaciones de condición para la FSM
        wall_crossed = self._check_wall_cross(underlying, options)
        vol_accel = self._check_volume_acceleration(underlying)
        neg_gamma_zone = options.dealer_net_gamma < 0.0

        signal = self._transition(
            svs=svs,
            underlying=underlying,
            options=options,
            wall_crossed=wall_crossed,
            vol_accel=vol_accel,
            neg_gamma=neg_gamma_zone,
        )

        if self.verbose:
            self._log(svs, wall_crossed, vol_accel, signal)

        return signal

    # ── Cálculo del Squeeze Vulnerability Score (0–100) ───────────────────────

    def _calculate_squeeze_vulnerability_score(
        self,
        u: UnderlyingData,
        o: OptionChainData,
    ) -> float:
        """
        Calcula el Squeeze_Vulnerability_Score ponderando múltiples factores.

        Composición del score (suma máxima = 100):

        Componente A — Short Interest (40 pts máx.)
            Pondera el % del float en corto y los días para cubrir.
            Un SI > 100% y DTC > 10 otorga el máximo.

        Componente B — Presión de Opciones Call (35 pts máx.)
            Pondera la divergencia del volumen de Calls sobre su SMA.
            Una divergencia de 6x+ otorga el máximo.

        Componente C — Gamma Neta del Dealer (15 pts máx.)
            Gamma neta negativa en MMs = riesgo máximo de bucle de cobertura.

        Componente D — Put/Call Ratio (10 pts máx.)
            Un PCR alto post-pico indica hedging masivo de inversores largos,
            lo que paradójicamente acelera la presión alcista.

        Parameters
        ----------
        u : Datos del subyacente.
        o : Datos de la cadena de opciones.

        Returns
        -------
        Score de 0.0 a 100.0 (float).
        """
        score = 0.0

        # ── A: Short Interest (40 pts) ─────────────────────────────────────────
        si_score = 0.0

        # Subcomponente A1: % del float en corto (0–25 pts)
        si_pct = u.short_interest_ratio
        if si_pct >= 100.0:
            si_score += 25.0
        elif si_pct >= SI_EXTREME_THRESHOLD:
            si_score += 25.0 * _sigmoid_normalize(si_pct, SI_EXTREME_THRESHOLD, 100.0)
        elif si_pct >= SI_HIGH_THRESHOLD:
            si_score += 15.0 * _sigmoid_normalize(si_pct, SI_HIGH_THRESHOLD, SI_EXTREME_THRESHOLD)
        # Por debajo del 20% no aporta puntos de SI

        # Subcomponente A2: Days-to-Cover (0–15 pts)
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
        # MMs con Gamma muy negativa = máximo riesgo de bucle de cobertura
        if o.dealer_net_gamma < 0:
            # Normaliza: cuanto más negativa, mayor el score
            gamma_intensity = min(abs(o.dealer_net_gamma) / 1_000_000.0, 1.0)
            score += 15.0 * gamma_intensity
        # Gamma positiva no aporta (MMs en posición estabilizadora)

        # ── D: Put/Call Ratio (10 pts) ─────────────────────────────────────────
        # PCR > 1.5 en medio de una subida = hedging masivo = presión alcista adicional
        pcr = o.put_call_ratio_volume
        if pcr >= 2.5:
            score += 10.0
        elif pcr >= 1.5:
            score += 10.0 * _sigmoid_normalize(pcr, 1.5, 2.5)

        return round(min(score, 100.0), 2)

    # ── Condiciones de Trigger ─────────────────────────────────────────────────

    def _check_wall_cross(
        self,
        u: UnderlyingData,
        o: OptionChainData,
    ) -> bool:
        """
        Verifica si el spot ha cruzado violentamente hacia arriba el Call Wall
        o el nivel de Gamma Cero.

        Un cruce se define como: el precio actual está por encima del nivel
        (más una tolerancia de WALL_CROSS_TOLERANCE_PCT%), habiendo estado
        por debajo en el periodo anterior.

        Parameters
        ----------
        u : Datos del subyacente (spot actual y anterior).
        o : Datos de la cadena de opciones (niveles de wall y gamma zero).

        Returns
        -------
        True si hay un cruce confirmado en alguno de los dos niveles.
        """

        def crossed_level(level: float) -> bool:
            effective_level = level * (1 + WALL_CROSS_TOLERANCE_PCT / 100)
            was_below = u.prev_spot_price < level
            is_above = u.spot_price >= effective_level
            return was_below and is_above

        return crossed_level(o.call_wall_level) or crossed_level(o.gamma_zero_level)

    def _check_volume_acceleration(self, u: UnderlyingData) -> bool:
        """
        Verifica si hay una aceleración significativa en el volumen del subyacente.

        Se considera aceleración cuando el volumen actual supera
        VOLUME_ACCELERATION_MULTIPLIER veces la SMA de 20 periodos.

        Parameters
        ----------
        u : Datos del subyacente.

        Returns
        -------
        True si el volumen está en modo de aceleración.
        """
        if u.volume_sma_20 <= 0:
            return False
        return (u.volume / u.volume_sma_20) >= VOLUME_ACCELERATION_MULTIPLIER

    # ── Máquina de Estados Finitos (FSM) ──────────────────────────────────────

    def _transition(
        self,
        svs: float,
        underlying: UnderlyingData,
        options: OptionChainData,
        wall_crossed: bool,
        vol_accel: bool,
        neg_gamma: bool,
    ) -> SqueezeSignal:
        """
        Gestiona las transiciones de estado y genera la señal operativa.

        Diagrama de transiciones:

            MONITORING ──(svs > 65)──────────────────────────► VULNERABLE
            VULNERABLE ──(svs > 85 & wall_cross & vol_accel)─► IGNITION
            IGNITION   ──(periodo siguiente)──────────────────► COOLING
            COOLING    ──(cooling_counter >= N)───────────────► MONITORING
            COOLING    ──(en cada periodo)────────────────────► TAKE_PROFIT_SCALING

        Parameters
        ----------
        svs          : Squeeze Vulnerability Score calculado.
        underlying   : Datos del subyacente.
        options      : Datos de la cadena de opciones.
        wall_crossed : Indica si el spot cruzó el Call Wall o Gamma Zero.
        vol_accel    : Indica aceleración de volumen.
        neg_gamma    : Indica Gamma neta negativa de los Dealers.

        Returns
        -------
        SqueezeSignal con la señal y metadatos del estado resultante.
        """

        # ── Estado: MONITORING ─────────────────────────────────────────────────
        if self._state == SqueezeState.MONITORING:
            if svs >= SVS_VULNERABLE_THRESHOLD:
                self._state = SqueezeState.VULNERABLE
                return self._build_signal(
                    signal_type=SignalType.ALERT_VULNERABLE,
                    svs=svs,
                    underlying=underlying,
                    options=options,
                    reasons=[
                        f"SVS={svs:.1f} superó umbral VULNERABLE ({SVS_VULNERABLE_THRESHOLD})",
                        f"Short Interest={underlying.short_interest_ratio:.1f}% del float",
                        f"Call Vol={underlying.volume:.0f} "
                        f"({options.call_volume/options.call_volume_sma_20:.1f}x SMA20)",
                    ],
                    notes=(
                        "Condiciones previas al squeeze identificadas. "
                        "Monitoreo intensivo activado. Sin señal de entrada aún."
                    ),
                )
            return self._build_signal(
                signal_type=SignalType.NONE,
                svs=svs,
                underlying=underlying,
                options=options,
                reasons=["SVS por debajo del umbral. Condiciones normales."],
            )

        # ── Estado: VULNERABLE ─────────────────────────────────────────────────
        if self._state == SqueezeState.VULNERABLE:

            # ¿Descenso de SVS? → Volver a MONITORING
            if svs < SVS_VULNERABLE_THRESHOLD - 5.0:  # Histéresis de 5 pts
                self._state = SqueezeState.MONITORING
                return self._build_signal(
                    signal_type=SignalType.NONE,
                    svs=svs,
                    underlying=underlying,
                    options=options,
                    reasons=["SVS declinó bajo el umbral. Vuelta a MONITORING."],
                )

            # ¿Convergen los tres factores de IGNITION?
            ignition_conditions_met = svs >= SVS_IGNITION_THRESHOLD and wall_crossed and vol_accel

            if ignition_conditions_met:
                self._state = SqueezeState.IGNITION
                self._ignition_price = underlying.spot_price

                tp_levels = self._calculate_take_profit_levels(
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
                if neg_gamma:
                    reasons.append(
                        f"✅ Dealer Gamma NEGATIVA ({options.dealer_net_gamma:,.0f}): "
                        "bucle de Delta-Hedging activo"
                    )
                if underlying.short_interest_ratio >= SI_HIGH_THRESHOLD:
                    reasons.append(
                        f"✅ Short Interest={underlying.short_interest_ratio:.1f}% "
                        f"(DTC={underlying.days_to_cover:.1f}d): presión de cubrir posiciones cortas"
                    )

                return self._build_signal(
                    signal_type=SignalType.LONG_MOMENTUM_IGNITION,
                    svs=svs,
                    underlying=underlying,
                    options=options,
                    reasons=reasons,
                    suggested_entry=underlying.spot_price,
                    take_profit=tp_levels,
                    notes=(
                        "⚡ BUCLE DE RETROALIMENTACIÓN ACTIVO. "
                        "Delta-Hedging de MMs + cubrimiento de shorts = presión compradora convergente. "
                        "Gestión de riesgo estricta. No perseguir entradas lejanas al trigger."
                    ),
                )

            # En VULNERABLE pero sin ignición aún
            return self._build_signal(
                signal_type=SignalType.ALERT_VULNERABLE,
                svs=svs,
                underlying=underlying,
                options=options,
                reasons=[
                    f"SVS={svs:.1f}. Esperando convergencia de gatillos.",
                    f"Cruce de Wall: {'SÍ' if wall_crossed else 'NO'}",
                    f"Aceleración de Vol: {'SÍ' if vol_accel else 'NO'}",
                    f"SVS ≥ {SVS_IGNITION_THRESHOLD}: "
                    f"{'SÍ' if svs >= SVS_IGNITION_THRESHOLD else 'NO'}",
                ],
            )

        # ── Estado: IGNITION ───────────────────────────────────────────────────
        if self._state == SqueezeState.IGNITION:
            # Transición automática a COOLING en el siguiente periodo
            self._state = SqueezeState.COOLING
            self._cooling_counter = 0

            tp_levels = self._calculate_take_profit_levels(
                entry=self._ignition_price or underlying.spot_price,
                options=options,
                si_ratio=underlying.short_interest_ratio,
            )

            return self._build_signal(
                signal_type=SignalType.TAKE_PROFIT_SCALING,
                svs=svs,
                underlying=underlying,
                options=options,
                reasons=[
                    "Periodo post-IGNITION. Gestión de posición activa.",
                    f"Precio de entrada referencia: {self._ignition_price:.2f}",
                    f"Precio actual: {underlying.spot_price:.2f}",
                ],
                take_profit=tp_levels,
                notes=(
                    "📊 TOMA DE GANANCIAS ESCALONADA. "
                    "Reducir posición en los niveles indicados. "
                    "PROHIBIDO abrir cortos. Solo gestión de largo existente."
                ),
            )

        # ── Estado: COOLING ────────────────────────────────────────────────────
        if self._state == SqueezeState.COOLING:
            self._cooling_counter += 1

            if self._cooling_counter >= COOLING_PERIODS:
                self._state = SqueezeState.MONITORING
                self._ignition_price = None
                self._cooling_counter = 0
                return self._build_signal(
                    signal_type=SignalType.NONE,
                    svs=svs,
                    underlying=underlying,
                    options=options,
                    reasons=[
                        f"Periodo de cooling completado ({COOLING_PERIODS} periodos). "
                        "Vuelta a MONITORING."
                    ],
                )

            tp_levels = self._calculate_take_profit_levels(
                entry=self._ignition_price or underlying.spot_price,
                options=options,
                si_ratio=underlying.short_interest_ratio,
            )

            return self._build_signal(
                signal_type=SignalType.TAKE_PROFIT_SCALING,
                svs=svs,
                underlying=underlying,
                options=options,
                reasons=[
                    f"COOLING periodo {self._cooling_counter}/{COOLING_PERIODS}.",
                    "Continúa la gestión escalonada de la posición larga.",
                ],
                take_profit=tp_levels,
                notes=(
                    "📊 Seguir el plan de salida escalonada. "
                    "No añadir ni abrir nuevas posiciones cortas."
                ),
            )

        # Fallback (no debería alcanzarse)
        return self._build_signal(  # type: ignore[unreachable]
            signal_type=SignalType.NONE,
            svs=svs,
            underlying=underlying,
            options=options,
            reasons=["Estado desconocido — fallback a NONE."],
        )

    # ── Utilidades internas ───────────────────────────────────────────────────

    def _calculate_take_profit_levels(
        self,
        entry: float,
        options: OptionChainData,
        si_ratio: float,
    ) -> list[float]:
        """
        Calcula niveles escalonados de toma de ganancias.

        La lógica escala los objetivos en función de la intensidad del squeeze:
          - Nivel 1 (25% posición): +15% sobre entrada.
          - Nivel 2 (25% posición): Call Wall + 10%.
          - Nivel 3 (25% posición): Call Wall + 25% (estimación de extensión).
          - Nivel 4 (25% posición): Objetivo dinámico basado en SI Ratio
                                    (más SI → objetivo más alto).

        Parameters
        ----------
        entry    : Precio de entrada (al momento del IGNITION).
        options  : Datos de opciones para usar el Call Wall como referencia.
        si_ratio : Short Interest Ratio para escalar el objetivo máximo.

        Returns
        -------
        Lista de cuatro precios objetivo ordenados de menor a mayor.
        """
        cw = options.call_wall_level

        tp1 = round(entry * 1.15, 2)
        tp2 = round(cw * 1.10, 2)
        tp3 = round(cw * 1.25, 2)

        # Objetivo dinámico: SI muy alto → squeeze más extenso → objetivo mayor
        si_extension_factor = 1.0 + (min(si_ratio, 200.0) / 200.0) * 0.75
        tp4 = round(entry * si_extension_factor * 1.40, 2)

        levels = sorted(set([tp1, tp2, tp3, tp4]))
        return levels

    def _build_signal(
        self,
        signal_type: SignalType,
        svs: float,
        underlying: UnderlyingData,
        options: OptionChainData,
        reasons: list[str],
        suggested_entry: float | None = None,
        take_profit: list[float] | None = None,
        notes: str = "",
    ) -> SqueezeSignal:
        """Construye y retorna un objeto SqueezeSignal."""
        return SqueezeSignal(
            signal_type=signal_type,
            state=self._state,
            squeeze_vulnerability_score=svs,
            trigger_reasons=reasons,
            spot_price=underlying.spot_price,
            call_wall_level=options.call_wall_level,
            suggested_entry=suggested_entry,
            take_profit_levels=take_profit or [],
            notes=notes,
        )

    def _log(
        self,
        svs: float,
        wall_crossed: bool,
        vol_accel: bool,
        signal: SqueezeSignal,
    ) -> None:
        """Imprime un log formateado del estado actual del motor."""
        bar = "═" * 72
        print(f"\n{bar}")
        print(f"  QuantumBeta │ {self.ticker} │ Estado: {self._state.name}")
        print(
            f"  SVS: {svs:6.2f}/100  │  "
            f"Cruce Wall: {'✅' if wall_crossed else '❌'}  │  "
            f"Vol Accel: {'✅' if vol_accel else '❌'}"
        )
        print(f"  Señal: {signal.signal_type.value}")
        for r in signal.trigger_reasons:
            print(f"    → {r}")
        if signal.notes:
            print(f"  Nota: {signal.notes}")
        if signal.take_profit_levels:
            print(f"  TPs: {signal.take_profit_levels}")
        print(bar)


# ──────────────────────────────────────────────────────────────────────────────
#  FUNCIONES AUXILIARES MATEMÁTICAS
# ──────────────────────────────────────────────────────────────────────────────


def _sigmoid_normalize(value: float, low: float, high: float) -> float:
    """
    Normaliza un valor entre [low, high] al rango [0, 1] usando una curva
    sigmoide suavizada, evitando la linearidad abrupta de los umbrales.

    Parameters
    ----------
    value : Valor a normalizar.
    low   : Límite inferior del rango (0 lógico).
    high  : Límite superior del rango (1 lógico).

    Returns
    -------
    Float en [0, 1].
    """
    if high <= low:
        return 1.0
    x = (value - low) / (high - low)
    x = max(0.0, min(x, 1.0))
    # Transformación sigmoide centrada en 0.5
    steepness = 6.0
    sigmoid = 1.0 / (1.0 + math.exp(-steepness * (x - 0.5)))
    # Renormalizar para que [0,1] mapee a [0,1] exactamente
    s0 = 1.0 / (1.0 + math.exp(steepness * 0.5))
    s1 = 1.0 / (1.0 + math.exp(-steepness * 0.5))
    return (sigmoid - s0) / (s1 - s0)


# ──────────────────────────────────────────────────────────────────────────────
#  BLOQUE DE DEMOSTRACIÓN
# ──────────────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    """
    Simulación de un evento de bucle de retroalimentación Delta-Hedging
    inspirado en el caso GameStop (GME) de enero de 2021.

    Secuencia simulada de 8 periodos (cada periodo = 1 día):

    P1 (Jan 12): SI alto, volumen de calls empieza a subir. Score moderado.
    P2 (Jan 14): Calls se disparan 4x SMA. Score VULNERABLE.
    P3 (Jan 19): Condiciones madurando. Gamma negativa extrema.
    P4 (Jan 21): Score crítico (>85). Cruce del Call Wall. Vol 3.5x. → IGNITION 🔥
    P5 (Jan 22): COOLING: primer periodo post-ignición. → TAKE_PROFIT_SCALING
    P6 (Jan 25): COOLING: segundo periodo.
    P7 (Jan 26): COOLING: tercer periodo.
    P8 (Jan 27): COOLING: cuarto periodo.
    P9 (Jan 28): COOLING: quinto periodo → vuelta a MONITORING.
    """

    print("\n" + "█" * 72)
    print("  QUANTUMBETA TERMINAL — Squeeze_Ignition_Engine DEMO")
    print("  Simulando bucle de retroalimentación Delta-Hedging: GME")
    print("█" * 72)

    engine = SqueezeIgnitionEngine(ticker="GME", verbose=True)

    # ── Periodo 1: Condiciones iniciales (Short Interest ya extremo) ──────────
    p1_underlying = UnderlyingData(
        ticker="GME",
        spot_price=19.94,
        prev_spot_price=18.50,
        volume=6_000_000,
        volume_sma_20=4_800_000,
        short_interest_ratio=138.0,  # 138% del float vendido en corto
        days_to_cover=4.2,
    )
    p1_options = OptionChainData(
        call_volume=45_000,
        call_volume_sma_20=30_000,
        call_open_interest=120_000,
        put_call_ratio_volume=0.55,
        dealer_net_gamma=-150_000,  # Ligeramente negativo
        call_wall_level=30.0,
        gamma_zero_level=25.0,
    )
    print("\n[PERIODO 1 — Ene 12] Condiciones iniciales. SI extremo.")
    engine.evaluate(p1_underlying, p1_options)

    # ── Periodo 2: Calls empiezan a dispararse ────────────────────────────────
    p2_underlying = UnderlyingData(
        ticker="GME",
        spot_price=22.40,
        prev_spot_price=19.94,
        volume=12_000_000,
        volume_sma_20=5_000_000,
        short_interest_ratio=140.0,
        days_to_cover=6.8,
    )
    p2_options = OptionChainData(
        call_volume=120_000,  # 4x la SMA
        call_volume_sma_20=30_000,
        call_open_interest=280_000,
        put_call_ratio_volume=0.75,
        dealer_net_gamma=-450_000,  # Más negativo
        call_wall_level=30.0,
        gamma_zero_level=25.0,
    )
    print("\n[PERIODO 2 — Ene 14] Calls 4x SMA. Gamma negativa creciendo.")
    engine.evaluate(p2_underlying, p2_options)

    # ── Periodo 3: Presión masiva de opciones ─────────────────────────────────
    p3_underlying = UnderlyingData(
        ticker="GME",
        spot_price=35.50,
        prev_spot_price=22.40,
        volume=25_000_000,
        volume_sma_20=6_000_000,
        short_interest_ratio=140.0,
        days_to_cover=9.5,
    )
    p3_options = OptionChainData(
        call_volume=210_000,  # 7x SMA — extremo
        call_volume_sma_20=30_000,
        call_open_interest=520_000,
        put_call_ratio_volume=1.20,
        dealer_net_gamma=-900_000,  # Gamma muy negativa
        call_wall_level=40.0,  # Wall subió con el precio
        gamma_zero_level=38.0,
    )
    print("\n[PERIODO 3 — Ene 19] Calls 7x SMA. Gamma extremadamente negativa.")
    engine.evaluate(p3_underlying, p3_options)

    # ── Periodo 4: IGNITION — Cruce del Call Wall + Vol Accel + SVS > 85 ─────
    p4_underlying = UnderlyingData(
        ticker="GME",
        spot_price=43.03,  # Cruzó el Call Wall de 40$
        prev_spot_price=35.50,  # Estaba por debajo del wall
        volume=89_000_000,  # 3.5x la SMA20 — aceleración confirmada
        volume_sma_20=25_000_000,
        short_interest_ratio=140.0,  # 140% — short sellers atrapados
        days_to_cover=12.0,  # DTC extremo
    )
    p4_options = OptionChainData(
        call_volume=280_000,  # 9.3x SMA — fuera de escala
        call_volume_sma_20=30_000,
        call_open_interest=680_000,
        put_call_ratio_volume=2.85,  # PCR extremo post-cruce
        dealer_net_gamma=-1_100_000,  # Gamma muy negativa → MMs deben comprar
        call_wall_level=40.0,
        gamma_zero_level=38.0,
    )
    print(
        "\n[PERIODO 4 — Ene 21] ⚡ CONVERGENCIA DE GATILLOS. "
        "Spot cruza Call Wall. Vol 3.5x. SVS > 85."
    )
    signal_p4 = engine.evaluate(p4_underlying, p4_options)
    assert (
        signal_p4.signal_type == SignalType.LONG_MOMENTUM_IGNITION
    ), "ERROR: Se esperaba señal LONG_MOMENTUM_IGNITION en el periodo 4."

    # ── Periodos 5-9: COOLING — Gestión de posición ───────────────────────────
    cooling_scenarios = [
        (65.01, 43.03, 150_000_000, 50_000_000, 1.80, -800_000, 80.0, "Ene 22"),
        (96.73, 65.01, 95_000_000, 55_000_000, 2.20, -600_000, 80.0, "Ene 25"),
        (147.98, 96.73, 180_000_000, 60_000_000, 3.50, -400_000, 200.0, "Ene 26"),
        (347.51, 147.98, 200_000_000, 65_000_000, 7.80, -200_000, 400.0, "Ene 27"),
        (193.60, 347.51, 140_000_000, 70_000_000, 4.20, 50_000, 400.0, "Ene 28"),
    ]

    for i, (spot, prev, vol, sma_vol, pcr, gamma, cw, fecha) in enumerate(
        cooling_scenarios, start=5
    ):
        u = UnderlyingData(
            ticker="GME",
            spot_price=spot,
            prev_spot_price=prev,
            volume=vol,
            volume_sma_20=sma_vol,
            short_interest_ratio=max(5.0, 140.0 - (i - 4) * 25),  # SI declinando
            days_to_cover=max(1.0, 12.0 - (i - 4) * 2),
        )
        o = OptionChainData(
            call_volume=150_000,
            call_volume_sma_20=30_000,
            call_open_interest=400_000,
            put_call_ratio_volume=pcr,
            dealer_net_gamma=gamma,
            call_wall_level=cw,
            gamma_zero_level=cw * 0.85,
        )
        print(f"\n[PERIODO {i} — {fecha}] Gestión de posición larga. COOLING.")
        engine.evaluate(u, o)

    # ── Verificaciones finales ────────────────────────────────────────────────
    print("\n" + "█" * 72)
    print("  VERIFICACIONES DE INTEGRIDAD DEL SISTEMA")
    print("█" * 72)

    assert (
        signal_p4.signal_type == SignalType.LONG_MOMENTUM_IGNITION
    ), "✗ Señal IGNITION no detectada"
    print("  ✓ Señal LONG_MOMENTUM_IGNITION disparada correctamente en P4")

    assert signal_p4.suggested_entry is not None, "✗ Precio de entrada sugerido ausente"
    print(f"  ✓ Precio de entrada sugerido: ${signal_p4.suggested_entry:.2f}")

    assert len(signal_p4.take_profit_levels) > 0, "✗ Niveles de TP ausentes"
    print(f"  ✓ Niveles de Take Profit: {signal_p4.take_profit_levels}")

    assert engine.state == SqueezeState.MONITORING, f"✗ Estado final inesperado: {engine.state}"
    print("  ✓ Motor volvió a MONITORING tras el periodo de COOLING")

    print("\n  ✅ Todas las verificaciones pasaron correctamente.")
    print("█" * 72 + "\n")
