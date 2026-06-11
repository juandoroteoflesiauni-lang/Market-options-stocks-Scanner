"""
Signal Combiner — Motor de Score Unificado
══════════════════════════════════════════
Toma los outputs de los 4 motores híbridos y produce una
señal única LONG / SHORT / NEUTRAL con score de confianza
entre −100 y +100, ponderado dinámicamente por el régimen
de Gamma del mercado.

Motores de entrada:
    ① GEX-VWAP      → price_vs_vwap, signal, shadow_ratio, regime
    ② BB-GEX        → pct_b, bandwidth, signal, k_multiplier, regime
    ③ Delta-RSI     → delta_rsi, histogram, zone, signal, strength
    ④ Shadow MACD   → macd, histogram, signal_name, ndde, charm_flow

Arquitectura del score:
    score_raw = Σ [ peso_i(régimen) × contribución_i ]
    score_final = tanh(score_raw / 50) × 100   ← acotado a [−100, +100]

    LONG  : score_final >= +umbral_entrada
    SHORT : score_final <= −umbral_entrada
    NEUTRAL: entre −umbral y +umbral

Los pesos se reasignan dinámicamente en función del régimen
de Gamma (Positivo / Negativo / Neutral) porque cada motor
tiene distinta utilidad según el estado del mercado.

Compatibilidad: pandas >= 2.0 · numpy >= 1.24 · pandas-ta
"""

import warnings
from dataclasses import dataclass
from enum import Enum
from typing import Any

import numpy as np
import pandas as pd

warnings.filterwarnings("ignore")


# ─────────────────────────────────────────────
# 1. ENUMS Y CONSTANTES
# ─────────────────────────────────────────────


class Direction(Enum):
    LONG = "LONG"
    SHORT = "SHORT"
    NEUTRAL = "NEUTRAL"


class GammaRegime(Enum):
    POSITIVE = "GAMMA_POS"  # dealers absorben → compresión de vol
    NEGATIVE = "GAMMA_NEG"  # dealers amplifican → expansión de vol
    FLIP = "GAMMA_FLIP"  # zona de transición (±5% del Gamma Flip)
    UNKNOWN = "UNKNOWN"


# Señales de cada motor y su contribución direccional (+1 alcista, -1 bajista, 0 neutro)
GEXVWAP_CONTRIB = {
    "LONG_STRONG": +2.0,
    "LONG_WEAK": +1.0,
    "FAKE_BREAKOUT_DOWN": +1.5,  # precio baja pero dealers compran
    "SHORT_STRONG": -2.0,
    "SHORT_WEAK": -1.0,
    "FAKE_BREAKOUT_UP": -1.5,  # precio sube pero dealers venden
    "GAMMA_SQUEEZE": +0.5,  # setup explosivo, leve sesgo alcista
    "NEUTRAL": 0.0,
}

BBGEX_CONTRIB = {
    "REVERSAL_LONG": +2.0,
    "SQUEEZE_BREAK_LONG": +2.0,
    "GAMMA_FLIP_LONG": +2.5,
    "WALKING_UPPER": +1.0,
    "REVERSAL_SHORT": -2.0,
    "SQUEEZE_BREAK_SHORT": -2.0,
    "GAMMA_FLIP_SHORT": -2.5,
    "WALKING_LOWER": -1.0,
    "WALL_REJECTION_UP": -1.0,
    "WALL_REJECTION_DOWN": +1.0,
    "COILING": 0.3,  # leve sesgo alcista por compresión
    "NEUTRAL": 0.0,
}

DELTARSI_CONTRIB = {
    "FLOW_EXHAUSTION_LONG": +3.0,
    "SWEEP_SURGE_LONG": +2.5,
    "MOMENTUM_LONG": +1.5,
    "FLOW_EXHAUSTION_SHORT": -3.0,
    "SWEEP_SURGE_SHORT": -2.5,
    "MOMENTUM_SHORT": -1.5,
    "NEUTRAL": 0.0,
}

SHADOWMACD_CONTRIB = {
    "ACCUMULATION_BOTTOM": +3.0,
    "NDDE_EXTREME_LONG": +2.5,
    "MACD_CROSS_BULL": +2.0,
    "MOMENTUM_ACCELERATION": +0.5,
    "DISTRIBUTION_TOP": -3.0,
    "NDDE_EXTREME_SHORT": -2.5,
    "MACD_CROSS_BEAR": -2.0,
    "MOMENTUM_DECELERATION": -0.5,
    "NEUTRAL": 0.0,
}


# ─────────────────────────────────────────────
# 2. PESOS DINÁMICOS POR RÉGIMEN DE GAMMA
# ─────────────────────────────────────────────

# Pesos base por motor. Deben sumar 1.0 en cada régimen.
# La lógica:
#   GAMMA_POS: mercado comprimido → medias móviles y VWAP más confiables
#              MACD sobre NDDE más ruidoso porque hay poca actividad de cobertura
#   GAMMA_NEG: mercado explosivo → flujo de opciones (Delta-RSI, MACD) dominante
#              BB con multiplicador 3σ más relevante
#   GAMMA_FLIP: zona de transición → todos los motores con peso igual + extra
#               atención al BB-GEX que detecta el cruce

WEIGHTS = {
    GammaRegime.POSITIVE: {
        # Suite 1 (33%)
        "gex_vwap": 0.08,
        "bb_gex": 0.08,
        "delta_rsi": 0.085,
        "shadow_macd": 0.085,
        # Suite 2 (33%)
        "sma_gamma": 0.08,
        "fractal_oi": 0.08,
        "hull_iv": 0.085,
        "hybrid_ribbon": 0.085,
        # Suite 3 (34%)
        "vol_profile": 0.03,
        "gex_profile": 0.03,
        "delta_profile": 0.03,
        "cvd_div": 0.03,
        "cvd_gamma": 0.03,
        "cvd_footprint": 0.03,
        "block_sweep": 0.03,
        "vol_bubble": 0.03,
        "iceberg": 0.03,
        "vidya_iv": 0.02,
        "vidya_gamma": 0.02,
        "vidya_cvd": 0.03,
    },
    GammaRegime.NEGATIVE: {
        "gex_vwap": 0.08,
        "bb_gex": 0.08,
        "delta_rsi": 0.085,
        "shadow_macd": 0.085,
        "sma_gamma": 0.08,
        "fractal_oi": 0.08,
        "hull_iv": 0.085,
        "hybrid_ribbon": 0.085,
        "vol_profile": 0.03,
        "gex_profile": 0.03,
        "delta_profile": 0.03,
        "cvd_div": 0.03,
        "cvd_gamma": 0.03,
        "cvd_footprint": 0.03,
        "block_sweep": 0.03,
        "vol_bubble": 0.03,
        "iceberg": 0.03,
        "vidya_iv": 0.02,
        "vidya_gamma": 0.02,
        "vidya_cvd": 0.03,
    },
    GammaRegime.FLIP: {
        "gex_vwap": 0.08,
        "bb_gex": 0.08,
        "delta_rsi": 0.085,
        "shadow_macd": 0.085,
        "sma_gamma": 0.08,
        "fractal_oi": 0.08,
        "hull_iv": 0.085,
        "hybrid_ribbon": 0.085,
        "vol_profile": 0.03,
        "gex_profile": 0.03,
        "delta_profile": 0.03,
        "cvd_div": 0.03,
        "cvd_gamma": 0.03,
        "cvd_footprint": 0.03,
        "block_sweep": 0.03,
        "vol_bubble": 0.03,
        "iceberg": 0.03,
        "vidya_iv": 0.02,
        "vidya_gamma": 0.02,
        "vidya_cvd": 0.03,
    },
    GammaRegime.UNKNOWN: {
        "gex_vwap": 0.08,
        "bb_gex": 0.08,
        "delta_rsi": 0.085,
        "shadow_macd": 0.085,
        "sma_gamma": 0.08,
        "fractal_oi": 0.08,
        "hull_iv": 0.085,
        "hybrid_ribbon": 0.085,
        "vol_profile": 0.03,
        "gex_profile": 0.03,
        "delta_profile": 0.03,
        "cvd_div": 0.03,
        "cvd_gamma": 0.03,
        "cvd_footprint": 0.03,
        "block_sweep": 0.03,
        "vol_bubble": 0.03,
        "iceberg": 0.03,
        "vidya_iv": 0.02,
        "vidya_gamma": 0.02,
        "vidya_cvd": 0.03,
    },
}

# Multiplicadores de confianza: ciertos contextos elevan o reducen el score
CONFIDENCE_MULTIPLIERS = {
    # Acuerdo entre los 4 motores (+30% confianza)
    "full_agreement": 1.30,
    # Acuerdo entre 3 de 4 motores (+15%)
    "partial_agreement": 1.15,
    # Solo 2 motores de acuerdo (base)
    "split": 1.00,
    # Señales contradictorias (−20%)
    "contradiction": 0.80,
    # Divergencia de alta calidad detectada (+20%)
    "divergence_present": 1.20,
    # Gamma Flip reciente (±3 velas) — volatilidad del score
    "near_flip": 0.90,
    # Bandwidth muy bajo (squeeze) — señal menos confiable hasta ruptura
    "squeeze": 0.85,
    # Sweep de opciones confirmando dirección (+25%)
    "sweep_confirmed": 1.25,
}


# ─────────────────────────────────────────────
# 3. ESTRUCTURAS DE INPUT DE CADA MOTOR
# ─────────────────────────────────────────────


@dataclass
class GEXVWAPInput:
    """Output del motor GEX-VWAP para el combiner."""

    signal: str
    price_vs_vwap: float  # % distancia del precio al GEX-VWAP
    shadow_ratio: float  # V_delta_hedge / V_spot
    regime: str  # "GAMMA_POS" | "GAMMA_NEG" | etc.
    net_gamma: float
    band_mult: float  # multiplicador activo (2.0 o 3.0)


@dataclass
class BBGEXInput:
    """Output del motor BB-GEX para el combiner."""

    signal: str
    pct_b: float  # posición del precio en la banda (0-1)
    bandwidth: float  # ancho de banda en % del precio
    k_multiplier: float  # multiplicador activo
    regime: str
    gamma_flip_cross: bool  # si hubo cruce del Gamma Flip en este tick


@dataclass
class DeltaRSIInput:
    """Output del motor Delta-RSI para el combiner."""

    signal: str
    strength: int  # 0-3
    delta_rsi: float  # valor del Delta-RSI (0-100)
    histogram: float  # Delta-RSI − señal
    zone: str  # "INST_OVERBOUGHT" | "INST_OVERSOLD" | etc.
    sweep_count: int
    flow_ratio: float  # ratio neto del flujo de opciones


@dataclass
class ShadowMACDInput:
    """Output del motor Shadow MACD para el combiner."""

    signal_name: str
    strength: int
    macd: float
    histogram: float
    ndde: float
    charm_flow: float
    put_call_ratio: float


@dataclass
class SMAGammaInput:
    signal: str
    strength: int
    bias: str
    deviation: float


@dataclass
class FractalOIInput:
    signal: str
    strength: int
    zona_rechazo: bool


@dataclass
class HullIVInput:
    signal: str
    strength: int
    regimen: str


@dataclass
class HybridRibbonInput:
    signal: str
    strength: int
    score: float


@dataclass
class VolumeProfileOIInput:
    signal: str
    strength: int
    score: float


@dataclass
class GEXProfileInput:
    signal: str
    strength: int
    score: float


@dataclass
class DeltaProfileInput:
    signal: str
    strength: int
    score: float


@dataclass
class CVDDivergenceInput:
    signal: str
    strength: int
    score: float


@dataclass
class CVDGammaInput:
    signal: str
    strength: int
    score: float


@dataclass
class CVDFootprintInput:
    signal: str
    strength: int
    score: float


@dataclass
class BlockSweepInput:
    signal: str
    strength: int
    score: float


@dataclass
class VolumeBubbleInput:
    signal: str
    strength: int
    score: float


@dataclass
class IcebergVannaInput:
    signal: str
    strength: int
    score: float


@dataclass
class VidyaIVInput:
    signal: str
    strength: int
    score: float


@dataclass
class VidyaGammaInput:
    signal: str
    strength: int
    score: float


@dataclass
class VidyaCVDInput:
    signal: str
    strength: int
    score: float


# ─────────────────────────────────────────────
# 4. OUTPUT DEL COMBINER
# ─────────────────────────────────────────────


@dataclass
class CombinerOutput:
    """Señal unificada producida por el Signal Combiner."""

    timestamp: pd.Timestamp
    ticker: str

    # Señal final
    direction: Direction
    score: float  # −100 a +100
    confidence: float  # 0.0 a 1.0 (normalizado)

    # Descomposición por motor
    score_gex_vwap: float
    score_bb_gex: float
    score_delta_rsi: float
    score_shadow_macd: float
    score_sma_gamma: float
    score_fractal_oi: float
    score_hull_iv: float
    score_hybrid_ribbon: float
    score_vol_profile: float
    score_gex_profile: float
    score_delta_profile: float
    score_cvd_div: float
    score_cvd_gamma: float
    score_cvd_footprint: float
    score_block_sweep: float
    score_vol_bubble: float
    score_iceberg: float
    score_vidya_iv: float
    score_vidya_gamma: float
    score_vidya_cvd: float

    # Pesos aplicados
    regime: GammaRegime
    weights: dict

    # Contexto de confianza
    agreement_level: str  # "full" | "partial" | "split" | "contradiction"
    active_multipliers: list[str]
    confidence_multiplier: float

    # Flags de contexto
    near_gamma_flip: bool
    divergence_present: bool
    sweep_confirmed: bool

    # Para el bot BingX
    entry_allowed: bool  # False si contexto de alta incertidumbre
    risk_level: str  # "LOW" | "MEDIUM" | "HIGH" | "EXTREME"
    recommended_size_pct: float  # % del tamaño base recomendado (0.25-1.0)

    def to_dict(self) -> dict:
        return {
            "timestamp": self.timestamp,
            "ticker": self.ticker,
            "direction": self.direction.value,
            "score": round(self.score, 2),
            "confidence": round(self.confidence, 3),
            "score_gex_vwap": round(self.score_gex_vwap, 3),
            "score_bb_gex": round(self.score_bb_gex, 3),
            "score_delta_rsi": round(self.score_delta_rsi, 3),
            "score_shadow_macd": round(self.score_shadow_macd, 3),
            "score_sma_gamma": round(self.score_sma_gamma, 3),
            "score_fractal_oi": round(self.score_fractal_oi, 3),
            "score_hull_iv": round(self.score_hull_iv, 3),
            "score_hybrid_ribbon": round(self.score_hybrid_ribbon, 3),
            "regime": self.regime.value,
            "agreement_level": self.agreement_level,
            "confidence_mult": round(self.confidence_multiplier, 3),
            "near_gamma_flip": self.near_gamma_flip,
            "divergence_present": self.divergence_present,
            "sweep_confirmed": self.sweep_confirmed,
            "entry_allowed": self.entry_allowed,
            "risk_level": self.risk_level,
            "size_pct": round(self.recommended_size_pct, 2),
        }


# ─────────────────────────────────────────────
# 5. SIGNAL COMBINER ENGINE
# ─────────────────────────────────────────────


class SignalCombiner:
    """
    Combina los outputs de los 4 motores híbridos en una señal
    unificada LONG / SHORT / NEUTRAL con score de confianza.

    Args:
        ticker:              Símbolo del proxy
        entry_threshold:     Score mínimo para emitir LONG/SHORT. Default 35.
        gamma_flip_memory:   Velas que recuerda un cruce del Gamma Flip. Default 3.
        min_motors_required: Mínimo de motores con señal válida para operar. Default 2.
        max_contradiction:   Score máximo permitido antes de bloquear entrada. Default 10.
    """

    def __init__(
        self,
        ticker: str,
        entry_threshold: float = 35.0,
        gamma_flip_memory: int = 3,
        min_motors_required: int = 2,
        max_contradiction: float = 10.0,
    ):
        self.ticker = ticker
        self.entry_threshold = entry_threshold
        self.gamma_flip_memory = gamma_flip_memory
        self.min_motors_required = min_motors_required
        self.max_contradiction = max_contradiction

        self._history: list[dict] = []
        self._flip_counter: int = 0  # countdown del Gamma Flip memory

    # ── Clasificar régimen de Gamma ────────────────────────────
    def _classify_regime(
        self,
        vwap: GEXVWAPInput,
        bb: BBGEXInput,
    ) -> GammaRegime:
        """
        Consenso de régimen entre GEX-VWAP y BB-GEX.
        Si hay cruce del Gamma Flip en BB → FLIP
        """
        if bb.gamma_flip_cross:
            self._flip_counter = self.gamma_flip_memory
            return GammaRegime.FLIP

        if self._flip_counter > 0:
            self._flip_counter -= 1
            return GammaRegime.FLIP

        # Mapeo de regímenes de cada motor
        pos_signals = {"GAMMA_POS", "PINNED", "CONTROLLED"}
        neg_signals = {"GAMMA_NEG", "COILING", "TRENDING"}

        vwap_pos = vwap.regime in pos_signals
        bb_pos = bb.regime in pos_signals
        vwap_neg = vwap.regime in neg_signals
        bb_neg = bb.regime in neg_signals

        if vwap_pos and bb_pos:
            return GammaRegime.POSITIVE
        if vwap_neg and bb_neg:
            return GammaRegime.NEGATIVE
        if vwap_pos or bb_pos:
            return GammaRegime.POSITIVE  # mayoría positiva
        if vwap_neg or bb_neg:
            return GammaRegime.NEGATIVE

        return GammaRegime.UNKNOWN

    # ── Contribución de cada motor ─────────────────────────────
    def _score_gex_vwap(self, inp: GEXVWAPInput, w: float) -> float:
        """
        Score del GEX-VWAP.
        Contribución base por señal + ajuste continuo por distancia al VWAP.
        """
        base = GEXVWAP_CONTRIB.get(inp.signal, 0.0)

        # Ajuste por magnitud: cuanto más lejos está el precio del VWAP,
        # más fuerte es la señal de reversión (en Gamma+) o continuación (en Gamma-)
        dist_adj = np.clip(abs(inp.price_vs_vwap) / 0.5, 0, 1.5)
        if inp.signal in ("LONG_STRONG", "LONG_WEAK", "FAKE_BREAKOUT_DOWN"):
            base += dist_adj * 0.3
        elif inp.signal in ("SHORT_STRONG", "SHORT_WEAK", "FAKE_BREAKOUT_UP"):
            base -= dist_adj * 0.3

        # Shadow ratio alto = más actividad de cobertura de dealers
        shadow_boost = np.clip(inp.shadow_ratio * 0.5, 0, 0.5)
        if base > 0:
            base += shadow_boost
        elif base < 0:
            base -= shadow_boost

        return base * w * 25.0  # escalar a rango ~[-25, +25] por motor

    def _score_bb_gex(self, inp: BBGEXInput, w: float) -> float:
        """
        Score del BB-GEX.
        Contribución base + ajuste por %B (posición en la banda).
        """
        base = BBGEX_CONTRIB.get(inp.signal, 0.0)

        # Ajuste continuo por posición en banda
        # %B < 0.1 → precio muy abajo de la banda (alcista)
        # %B > 0.9 → precio muy arriba de la banda (bajista)
        if inp.pct_b < 0.1:
            pctb_adj = (0.1 - inp.pct_b) * 5.0  # positivo
        elif inp.pct_b > 0.9:
            pctb_adj = (0.9 - inp.pct_b) * 5.0  # negativo
        else:
            pctb_adj = 0.0

        # Squeeze activo: señal de ruptura vale más
        if inp.bandwidth < 1.5:
            base *= 1.3

        # Multiplicador alto (3σ) = Gamma Negativo activo
        if inp.k_multiplier >= 3.0:
            base *= 1.15

        return (base + pctb_adj) * w * 25.0

    def _score_delta_rsi(self, inp: DeltaRSIInput, w: float) -> float:
        """
        Score del Delta-RSI.
        Contribución base + ajuste por valor del RSI y sweeps.
        """
        base = DELTARSI_CONTRIB.get(inp.signal, 0.0)

        # Zona extrema amplifica la señal
        if inp.zone == "INST_OVERSOLD" and base > 0:
            zone_mult = 1.0 + (30 - min(inp.delta_rsi, 30)) / 30
            base *= zone_mult
        elif inp.zone == "INST_OVERBOUGHT" and base < 0:
            zone_mult = 1.0 + (min(inp.delta_rsi, 70) - 70) / 30 * -1
            base *= zone_mult

        # Sweeps amplifican la señal (convicción institucional)
        if inp.sweep_count >= 3:
            sweep_boost = min(0.5, inp.sweep_count * 0.1)
            if base > 0:
                base += sweep_boost
            elif base < 0:
                base -= sweep_boost

        # Fuerza de la señal (0-3) como multiplicador
        strength_mult = 1.0 + inp.strength * 0.15
        base *= strength_mult

        return base * w * 25.0

    def _score_shadow_macd(self, inp: ShadowMACDInput, w: float) -> float:
        """
        Score del Shadow MACD.
        Contribución base + ajuste por histograma y Charm flow.
        """
        base = SHADOWMACD_CONTRIB.get(inp.signal_name, 0.0)

        # El histograma mide la ACELERACIÓN del momentum de dealers
        # Normalizamos por un histograma de referencia de 50,000
        hist_norm = np.tanh(inp.histogram / 50_000)
        hist_adj = hist_norm * 0.5  # contribución máx ±0.5

        # Charm flow negativo = dealers desarmando cobertura (bajista)
        charm_adj = np.clip(-inp.charm_flow / 10_000, -0.3, 0.3)

        # Put/call ratio > 1.5 = sesgo bajista institucional
        if inp.put_call_ratio > 1.5:
            base -= 0.3
        elif inp.put_call_ratio < 0.7:
            base += 0.3

        # NDDE extremo amplifica
        ndde_norm = np.tanh(abs(inp.ndde) / 2_000_000)
        if inp.ndde < 0:
            base += ndde_norm * 0.4
        else:
            base -= ndde_norm * 0.4

        return (base + hist_adj + charm_adj) * w * 25.0

    def _score_sma_gamma(self, inp: SMAGammaInput, w: float) -> float:
        base = 0.0
        if inp.signal == "LONG":
            base = 2.0
        elif inp.signal == "SHORT":
            base = -2.0

        if inp.bias == "BULL":
            base += 0.5
        elif inp.bias == "BEAR":
            base -= 0.5

        # Amplificar si hay fuerte desviación
        desv_norm = np.clip(inp.deviation / 0.001, -1.0, 1.0)
        base += desv_norm * 0.5

        strength_mult = 1.0 + inp.strength * 0.15
        return base * strength_mult * w * 25.0

    def _score_fractal_oi(self, inp: FractalOIInput, w: float) -> float:
        base = 0.0
        if inp.signal == "LONG":
            base = 2.5
        elif inp.signal == "SHORT":
            base = -2.5

        if inp.zona_rechazo:
            base *= 1.2

        strength_mult = 1.0 + inp.strength * 0.15
        return base * strength_mult * w * 25.0

    def _score_hull_iv(self, inp: HullIVInput, w: float) -> float:
        base = 0.0
        if inp.signal == "LONG":
            base = 2.0
        elif inp.signal == "SHORT":
            base = -2.0

        if inp.regimen == "baja_iv":
            base *= 1.5
        elif inp.regimen == "alta_iv":
            base *= 0.5

        strength_mult = 1.0 + inp.strength * 0.15
        return base * strength_mult * w * 25.0

    def _score_hybrid_ribbon(self, inp: HybridRibbonInput, w: float) -> float:
        base = 0.0
        if "LONG" in inp.signal or "BULL" in inp.signal:
            base = 2.0
            if "CONFIRMED" in inp.signal:
                base = 3.0
            elif "TRAP" in inp.signal:
                base = -1.0
        elif "SHORT" in inp.signal or "BEAR" in inp.signal:
            base = -2.0
            if "CONFIRMED" in inp.signal:
                base = -3.0
            elif "TRAP" in inp.signal:
                base = 1.0

        score_adj = inp.score / 100.0
        strength_mult = 1.0 + inp.strength * 0.15
        return base * score_adj * strength_mult * w * 25.0

    # ── Scores Suite 3 ─────────────────────────────────────────
    def _score_generic_suite3(self, inp: Any, w: float) -> float:
        base = 0.0
        if (
            "LONG" in inp.signal
            or "BUY" in inp.signal
            or "BULL" in inp.signal
            or "ACCUMULATION" in inp.signal
        ):
            base = 2.0
        elif (
            "SHORT" in inp.signal
            or "SELL" in inp.signal
            or "BEAR" in inp.signal
            or "DISTRIBUTION" in inp.signal
        ):
            base = -2.0

        strength_mult = 1.0 + inp.strength * 0.15
        # The scores in Suite 3 are directly normalized [0, 1].
        # The combiner expects around [-1, +1] per engine to scale to [-100, 100].
        # So we just use inp.score (which is 0.0 to 1.0).
        return base * inp.score * strength_mult * w * 25.0

    # ── Nivel de acuerdo ───────────────────────────────────────
    def _compute_agreement(
        self,
        scores: dict[str, float],
    ) -> tuple[str, float]:
        """
        Mide cuántos motores están de acuerdo en la dirección.
        Retorna (nivel_acuerdo, multiplicador_confianza).
        """
        signs = [np.sign(s) for s in scores.values() if abs(s) > 1.0]
        if not signs:
            return "split", CONFIDENCE_MULTIPLIERS["split"]

        positive = sum(1 for s in signs if s > 0)
        negative = sum(1 for s in signs if s < 0)
        total = len(signs)

        if positive == total:
            return "full_agreement", CONFIDENCE_MULTIPLIERS["full_agreement"]
        if negative == total:
            return "full_agreement", CONFIDENCE_MULTIPLIERS["full_agreement"]
        if max(positive, negative) >= total - 1:
            return "partial_agreement", CONFIDENCE_MULTIPLIERS["partial_agreement"]
        if positive > 0 and negative > 0:
            return "contradiction", CONFIDENCE_MULTIPLIERS["contradiction"]
        return "split", CONFIDENCE_MULTIPLIERS["split"]

    # ── Contexto y multiplicadores ─────────────────────────────
    def _compute_context(
        self,
        vwap: GEXVWAPInput,
        bb: BBGEXInput,
        drsi: DeltaRSIInput,
        smacd: ShadowMACDInput,
        regime: GammaRegime,
    ) -> tuple[list[str], float, bool, bool, bool]:
        """
        Detecta contextos especiales y acumula multiplicadores.
        Retorna:
            (multiplicadores_activos, mult_total,
             near_flip, divergence_present, sweep_confirmed)
        """
        mults = []
        mult_total = 1.0

        # Cerca del Gamma Flip
        near_flip = regime == GammaRegime.FLIP or bb.gamma_flip_cross
        if near_flip:
            mults.append("near_flip")
            mult_total *= CONFIDENCE_MULTIPLIERS["near_flip"]

        # Squeeze activo
        if bb.bandwidth < 1.5:
            mults.append("squeeze")
            mult_total *= CONFIDENCE_MULTIPLIERS["squeeze"]

        # Divergencia presente (si Shadow MACD o Delta-RSI emiten señales de divergencia)
        divergence_signals = {
            "FLOW_EXHAUSTION_LONG",
            "FLOW_EXHAUSTION_SHORT",
            "ACCUMULATION_BOTTOM",
            "DISTRIBUTION_TOP",
        }
        divergence_present = (
            drsi.signal in divergence_signals or smacd.signal_name in divergence_signals
        )
        if divergence_present:
            mults.append("divergence_present")
            mult_total *= CONFIDENCE_MULTIPLIERS["divergence_present"]

        # Sweep confirmando dirección
        sweep_confirmed = drsi.sweep_count >= 3
        if sweep_confirmed:
            mults.append("sweep_confirmed")
            mult_total *= CONFIDENCE_MULTIPLIERS["sweep_confirmed"]

        return mults, mult_total, near_flip, divergence_present, sweep_confirmed

    # ── Riesgo y tamaño ────────────────────────────────────────
    def _compute_risk(
        self,
        score: float,
        confidence: float,
        regime: GammaRegime,
        near_flip: bool,
        bb: BBGEXInput,
    ) -> tuple[str, float, bool]:
        """
        Determina el nivel de riesgo, el tamaño recomendado de posición
        y si la entrada está permitida.
        """
        abs_score = abs(score)

        # Score muy bajo → no entrar
        if abs_score < self.entry_threshold:
            return "LOW", 0.0, False

        # Score suficiente pero con factores de riesgo
        if near_flip or regime == GammaRegime.FLIP:
            risk = "HIGH"
            size = 0.40
        elif regime == GammaRegime.NEGATIVE and bb.bandwidth > 3.0:
            risk = "EXTREME"
            size = 0.25  # volatilidad extrema → tamaño mínimo
        elif confidence >= 0.75 and abs_score >= 65:
            risk = "LOW"
            size = 1.00  # máxima convicción
        elif confidence >= 0.60 and abs_score >= 50:
            risk = "MEDIUM"
            size = 0.75
        else:
            risk = "MEDIUM"
            size = 0.50

        entry_allowed = risk != "EXTREME" or abs_score >= 70

        return risk, size, entry_allowed

    # ── Tick principal ──────────────────────────────────────────
    def combine(
        self,
        timestamp: pd.Timestamp,
        vwap: GEXVWAPInput,
        bb: BBGEXInput,
        drsi: DeltaRSIInput,
        smacd: ShadowMACDInput,
        smag: SMAGammaInput,
        fractal: FractalOIInput,
        hull: HullIVInput,
        hybrid: HybridRibbonInput,
        vol_profile: VolumeProfileOIInput,
        gex_profile: GEXProfileInput,
        delta_profile: DeltaProfileInput,
        cvd_div: CVDDivergenceInput,
        cvd_gamma: CVDGammaInput,
        cvd_footprint: CVDFootprintInput,
        block_sweep: BlockSweepInput,
        vol_bubble: VolumeBubbleInput,
        iceberg: IcebergVannaInput,
        vidya_iv: VidyaIVInput,
        vidya_gamma: VidyaGammaInput,
        vidya_cvd: VidyaCVDInput,
    ) -> CombinerOutput:
        """
        Procesa los 20 inputs y produce el CombinerOutput unificado.
        """
        # ── 1. Régimen de Gamma ────────────────────────────────
        regime = self._classify_regime(vwap, bb)
        weights = WEIGHTS[regime]

        # ── 2. Score de cada motor ─────────────────────────────
        s_vwap = self._score_gex_vwap(vwap, weights["gex_vwap"])
        s_bb = self._score_bb_gex(bb, weights["bb_gex"])
        s_drsi = self._score_delta_rsi(drsi, weights["delta_rsi"])
        s_smacd = self._score_shadow_macd(smacd, weights["shadow_macd"])
        s_smag = self._score_sma_gamma(smag, weights["sma_gamma"])
        s_fractal = self._score_fractal_oi(fractal, weights["fractal_oi"])
        s_hull = self._score_hull_iv(hull, weights["hull_iv"])
        s_hybrid = self._score_hybrid_ribbon(hybrid, weights["hybrid_ribbon"])

        # Suite 3
        s_vp = self._score_generic_suite3(vol_profile, weights["vol_profile"])
        s_gp = self._score_generic_suite3(gex_profile, weights["gex_profile"])
        s_dp = self._score_generic_suite3(delta_profile, weights["delta_profile"])
        s_cd = self._score_generic_suite3(cvd_div, weights["cvd_div"])
        s_cg = self._score_generic_suite3(cvd_gamma, weights["cvd_gamma"])
        s_cf = self._score_generic_suite3(cvd_footprint, weights["cvd_footprint"])
        s_bs = self._score_generic_suite3(block_sweep, weights["block_sweep"])
        s_vb = self._score_generic_suite3(vol_bubble, weights["vol_bubble"])
        s_ib = self._score_generic_suite3(iceberg, weights["iceberg"])
        s_vi = self._score_generic_suite3(vidya_iv, weights["vidya_iv"])
        s_vg = self._score_generic_suite3(vidya_gamma, weights["vidya_gamma"])
        s_vc = self._score_generic_suite3(vidya_cvd, weights["vidya_cvd"])

        scores = {
            "gex_vwap": s_vwap,
            "bb_gex": s_bb,
            "delta_rsi": s_drsi,
            "shadow_macd": s_smacd,
            "sma_gamma": s_smag,
            "fractal_oi": s_fractal,
            "hull_iv": s_hull,
            "hybrid_ribbon": s_hybrid,
            "vol_profile": s_vp,
            "gex_profile": s_gp,
            "delta_profile": s_dp,
            "cvd_div": s_cd,
            "cvd_gamma": s_cg,
            "cvd_footprint": s_cf,
            "block_sweep": s_bs,
            "vol_bubble": s_vb,
            "iceberg": s_ib,
            "vidya_iv": s_vi,
            "vidya_gamma": s_vg,
            "vidya_cvd": s_vc,
        }

        # ── 3. Score bruto (suma ponderada) ────────────────────
        score_raw = sum(scores.values())

        # ── 4. Normalización suave a [−100, +100] ──────────────
        # tanh mantiene la linealidad cerca del 0 pero acota los extremos
        score_normalized = float(np.tanh(score_raw / 40.0) * 100.0)

        # ── 5. Acuerdo entre motores ───────────────────────────
        agreement, agree_mult = self._compute_agreement(scores)

        # ── 6. Contexto y multiplicadores ─────────────────────
        active_mults, context_mult, near_flip, div_present, sweep_conf = self._compute_context(
            vwap, bb, drsi, smacd, regime
        )

        # ── 7. Score final con multiplicadores ────────────────
        total_mult = agree_mult * context_mult
        score_final = float(np.clip(score_normalized * total_mult, -100, 100))

        # ── 8. Confianza (0-1) ─────────────────────────────────
        # Combina la magnitud del score con el acuerdo y el contexto
        raw_confidence = (abs(score_final) / 100.0) * agree_mult
        confidence = float(np.clip(raw_confidence, 0.0, 1.0))

        # ── 9. Dirección final ─────────────────────────────────
        if score_final >= self.entry_threshold:
            direction = Direction.LONG
        elif score_final <= -self.entry_threshold:
            direction = Direction.SHORT
        else:
            direction = Direction.NEUTRAL

        # ── 10. Riesgo y tamaño ────────────────────────────────
        risk, size_pct, entry_allowed = self._compute_risk(
            score_final, confidence, regime, near_flip, bb
        )

        # ── 11. Construir output ───────────────────────────────
        result = CombinerOutput(
            timestamp=timestamp,
            ticker=self.ticker,
            direction=direction,
            score=score_final,
            confidence=confidence,
            score_gex_vwap=s_vwap,
            score_bb_gex=s_bb,
            score_delta_rsi=s_drsi,
            score_shadow_macd=s_smacd,
            score_sma_gamma=s_smag,
            score_fractal_oi=s_fractal,
            score_hull_iv=s_hull,
            score_hybrid_ribbon=s_hybrid,
            score_vol_profile=s_vp,
            score_gex_profile=s_gp,
            score_delta_profile=s_dp,
            score_cvd_div=s_cd,
            score_cvd_gamma=s_cg,
            score_cvd_footprint=s_cf,
            score_block_sweep=s_bs,
            score_vol_bubble=s_vb,
            score_iceberg=s_ib,
            score_vidya_iv=s_vi,
            score_vidya_gamma=s_vg,
            score_vidya_cvd=s_vc,
            regime=regime,
            weights=weights,
            agreement_level=agreement,
            active_multipliers=active_mults,
            confidence_multiplier=total_mult,
            near_gamma_flip=near_flip,
            divergence_present=div_present,
            sweep_confirmed=sweep_conf,
            entry_allowed=entry_allowed,
            risk_level=risk,
            recommended_size_pct=size_pct,
        )

        self._history.append(result.to_dict())
        return result

    def to_dataframe(self) -> pd.DataFrame:
        if not self._history:
            return pd.DataFrame()
        df = pd.DataFrame(self._history)
        df.set_index("timestamp", inplace=True)
        return df


# ─────────────────────────────────────────────
# 6. GENERADOR DE DATOS DEMO
# ─────────────────────────────────────────────


def generate_demo_inputs(
    n: int = 390,
    seed: int = 42,
) -> list[tuple]:
    """
    Genera inputs sintéticos de los 4 motores para demostrar
    las 4 fases de mercado con señales coordinadas y contradictorias.
    """
    rng = np.random.default_rng(seed)
    start = pd.Timestamp("2025-01-15 09:30:00", tz="America/New_York")
    ts_range = pd.date_range(start, periods=n, freq="1min")

    # (barras, descripcion, sesgo_neto)
    phases = [
        (97, "ALCISTA_LIMPIO", +1.0),
        (98, "DISTRIBUCION", -0.6),
        (98, "BAJISTA_LIMPIO", -1.0),
        (97, "ACUMULACION", +0.5),
    ]

    inputs = []
    idx = 0

    for n_bars, phase_name, bias in phases:
        for _ in range(n_bars):
            if idx >= n:
                break
            ts = ts_range[idx]
            noise = rng.uniform(-0.3, 0.3)
            b = bias + noise

            # ── GEX-VWAP ──────────────────────────────────────
            if b > 0.5:
                vwap_signal = rng.choice(
                    ["LONG_STRONG", "FAKE_BREAKOUT_DOWN", "LONG_WEAK"], p=[0.4, 0.3, 0.3]
                )
                vwap_regime = "GAMMA_POS" if rng.random() > 0.3 else "GAMMA_NEG"
            elif b < -0.5:
                vwap_signal = rng.choice(
                    ["SHORT_STRONG", "FAKE_BREAKOUT_UP", "SHORT_WEAK"], p=[0.4, 0.3, 0.3]
                )
                vwap_regime = "GAMMA_NEG" if rng.random() > 0.3 else "GAMMA_POS"
            else:
                vwap_signal = "NEUTRAL"
                vwap_regime = "GAMMA_POS"

            vwap_inp = GEXVWAPInput(
                signal=vwap_signal,
                price_vs_vwap=float(b * rng.uniform(0.1, 0.8)),
                shadow_ratio=float(rng.uniform(0.1, 0.5)),
                regime=vwap_regime,
                net_gamma=float(rng.normal(b * 500_000, 200_000)),
                band_mult=2.0 if vwap_regime == "GAMMA_POS" else 3.0,
            )

            # ── BB-GEX ────────────────────────────────────────
            if b > 0.5:
                bb_signal = rng.choice(
                    ["REVERSAL_LONG", "SQUEEZE_BREAK_LONG", "WALKING_UPPER", "NEUTRAL"],
                    p=[0.3, 0.2, 0.3, 0.2],
                )
                pct_b = float(rng.uniform(0.0, 0.4))
            elif b < -0.5:
                bb_signal = rng.choice(
                    ["REVERSAL_SHORT", "SQUEEZE_BREAK_SHORT", "WALKING_LOWER", "NEUTRAL"],
                    p=[0.3, 0.2, 0.3, 0.2],
                )
                pct_b = float(rng.uniform(0.6, 1.1))
            else:
                bb_signal = "NEUTRAL"
                pct_b = float(rng.uniform(0.3, 0.7))

            bb_inp = BBGEXInput(
                signal=bb_signal,
                pct_b=pct_b,
                bandwidth=float(rng.uniform(0.8, 3.5)),
                k_multiplier=2.0 if bias > 0 else 3.0,
                regime="PINNED" if b > 0 else "TRENDING",
                gamma_flip_cross=bool(rng.random() < 0.015),
            )

            # ── Delta-RSI ─────────────────────────────────────
            if b > 0.5:
                drsi_signal = rng.choice(
                    ["FLOW_EXHAUSTION_LONG", "SWEEP_SURGE_LONG", "MOMENTUM_LONG", "NEUTRAL"],
                    p=[0.2, 0.2, 0.4, 0.2],
                )
                drsi_val = float(rng.uniform(45, 75))
                drsi_zone = (
                    "INST_OVERSOLD"
                    if drsi_val < 35
                    else ("BULLISH_BIAS" if drsi_val > 55 else "NEUTRAL")
                )
            elif b < -0.5:
                drsi_signal = rng.choice(
                    ["FLOW_EXHAUSTION_SHORT", "SWEEP_SURGE_SHORT", "MOMENTUM_SHORT", "NEUTRAL"],
                    p=[0.2, 0.2, 0.4, 0.2],
                )
                drsi_val = float(rng.uniform(25, 55))
                drsi_zone = (
                    "INST_OVERBOUGHT"
                    if drsi_val > 65
                    else ("BEARISH_BIAS" if drsi_val < 45 else "NEUTRAL")
                )
            else:
                drsi_signal = "NEUTRAL"
                drsi_val = float(rng.uniform(40, 60))
                drsi_zone = "NEUTRAL"

            drsi_inp = DeltaRSIInput(
                signal=drsi_signal,
                strength=int(rng.integers(0, 4)),
                delta_rsi=drsi_val,
                histogram=float(rng.normal(b * 3, 2)),
                zone=drsi_zone,
                sweep_count=int(rng.poisson(abs(b) * 3)),
                flow_ratio=float(b * 0.3 + rng.normal(0, 0.1)),
            )

            # ── Shadow MACD ───────────────────────────────────
            if b > 0.5:
                smacd_signal = rng.choice(
                    ["ACCUMULATION_BOTTOM", "MACD_CROSS_BULL", "MOMENTUM_ACCELERATION", "NEUTRAL"],
                    p=[0.2, 0.3, 0.3, 0.2],
                )
            elif b < -0.5:
                smacd_signal = rng.choice(
                    ["DISTRIBUTION_TOP", "MACD_CROSS_BEAR", "MOMENTUM_DECELERATION", "NEUTRAL"],
                    p=[0.2, 0.3, 0.3, 0.2],
                )
            else:
                smacd_signal = "NEUTRAL"

            smacd_inp = ShadowMACDInput(
                signal_name=smacd_signal,
                strength=int(rng.integers(0, 4)),
                macd=float(rng.normal(b * 30_000, 15_000)),
                histogram=float(rng.normal(b * 15_000, 8_000)),
                ndde=float(rng.normal(-b * 800_000, 300_000)),
                charm_flow=float(rng.normal(-b * 5_000, 2_000)),
                put_call_ratio=float(rng.uniform(0.5, 2.0)),
            )

            inputs.append((ts, vwap_inp, bb_inp, drsi_inp, smacd_inp))
            idx += 1

    return inputs


# ─────────────────────────────────────────────
# 7. PIPELINE COMPLETO
# ─────────────────────────────────────────────


def run_signal_combiner_pipeline(
    ticker: str = "AAPL",
    n: int = 390,
    entry_threshold: float = 35.0,
    verbose: bool = True,
) -> pd.DataFrame:

    print(f"\n{'═'*68}")
    print(f"  SIGNAL COMBINER  |  {ticker}  |  {n} velas  |  umbral={entry_threshold}")
    print(f"{'═'*68}")

    combiner = SignalCombiner(
        ticker=ticker,
        entry_threshold=entry_threshold,
    )

    demo_inputs = generate_demo_inputs(n)
    outputs = []

    for ts, vwap, bb, drsi, smacd in demo_inputs:
        out = combiner.combine(ts, vwap, bb, drsi, smacd)
        outputs.append(out)

    df = combiner.to_dataframe()

    if verbose:
        _print_report(df, ticker, entry_threshold)

    return df


def _print_report(df: pd.DataFrame, ticker: str, threshold: float):
    last = df.iloc[-1]

    print(f"\n── Score actual {ticker} ────────────────────────────────")
    print(f"  Dirección          : {last['direction']}")
    print(f"  Score              : {last['score']:+.2f} / 100")
    print(f"  Confianza          : {last['confidence']:.1%}")
    print(f"  Régimen Gamma      : {last['regime']}")
    print(f"  Acuerdo motores    : {last['agreement_level']}")
    print(f"  Mult. confianza    : {last['confidence_mult']:.3f}")
    print(f"  Multiplicadores    : {last.get('active_multipliers', [])}")

    print("\n── Descomposición del score ──")
    print(f"  GEX-VWAP          : {last['score_gex_vwap']:+.2f}")
    print(f"  BB-GEX            : {last['score_bb_gex']:+.2f}")
    print(f"  Delta-RSI         : {last['score_delta_rsi']:+.2f}")
    print(f"  Shadow MACD       : {last['score_shadow_macd']:+.2f}")
    print("  ─────────────────────────")
    total = (
        last["score_gex_vwap"]
        + last["score_bb_gex"]
        + last["score_delta_rsi"]
        + last["score_shadow_macd"]
    )
    print(f"  Suma bruta        : {total:+.2f}")
    print(f"  Score final       : {last['score']:+.2f}")

    print("\n── Contexto de riesgo ──")
    print(f"  Nivel de riesgo    : {last['risk_level']}")
    print(f"  Tamaño recomendado : {last['size_pct']:.0%}")
    print(f"  Entrada permitida  : {last['entry_allowed']}")
    print(f"  Cerca Gamma Flip   : {last['near_gamma_flip']}")
    print(f"  Divergencia activa : {last['divergence_present']}")
    print(f"  Sweep confirmado   : {last['sweep_confirmed']}")

    # Estadísticas de sesión
    longs = (df["direction"] == "LONG").sum()
    shorts = (df["direction"] == "SHORT").sum()
    neutral = (df["direction"] == "NEUTRAL").sum()
    allowed = df["entry_allowed"].sum()

    print("\n── Estadísticas de sesión ──")
    print(f"  LONG               : {longs:3d} señales")
    print(f"  SHORT              : {shorts:3d} señales")
    print(f"  NEUTRAL            : {neutral:3d} señales")
    print(f"  Entradas permitidas: {allowed:3d} / {len(df)}")
    print(f"  Score promedio     : {df['score'].mean():+.2f}")
    print(f"  Confianza promedio : {df['confidence'].mean():.1%}")
    print(
        f"  Max score LONG     : {df[df['direction']=='LONG']['score'].max() if longs > 0 else 0:+.2f}"
    )
    print(
        f"  Max score SHORT    : {df[df['direction']=='SHORT']['score'].min() if shorts > 0 else 0:+.2f}"
    )

    # Distribución de acuerdos
    print("\n── Acuerdo entre motores ──")
    print(df["agreement_level"].value_counts().to_string())

    # Distribución de regímenes
    print("\n── Regímenes detectados ──")
    print(df["regime"].value_counts().to_string())

    # Señales de alta confianza
    high_conf = df[(df["entry_allowed"] == True) & (df["confidence"] >= 0.65)]
    print(f"\n── Señales alta confianza (≥65%): {len(high_conf)} ──")
    if not high_conf.empty:
        cols = [
            "direction",
            "score",
            "confidence",
            "regime",
            "agreement_level",
            "risk_level",
            "size_pct",
        ]
        print(high_conf[cols].tail(8).to_string())

    print(f"\n{'═'*68}")


# ─────────────────────────────────────────────
# 8. INTEGRACIÓN PRODUCCIÓN — CONECTOR AL BOT BINGX
# ─────────────────────────────────────────────


class SignalCombinerLive:
    """
    Wrapper para integrar el Signal Combiner con el bot BingX.

    En producción, este es el único punto de contacto entre
    los 4 motores de indicadores y el motor de órdenes del bot.

    Flujo:
        ┌─────────────┐  ┌──────────┐  ┌──────────────┐  ┌─────────────┐
        │  GEX-VWAP   │  │  BB-GEX  │  │  Delta-RSI   │  │ Shadow MACD │
        └──────┬──────┘  └────┬─────┘  └──────┬───────┘  └──────┬──────┘
               └──────────────┴───────────────┴─────────────────┘
                                        │
                                ┌───────▼──────┐
                                │ SignalCombiner│
                                └───────┬──────┘
                                        │
                              ┌─────────▼─────────┐
                              │   Bot BingX        │
                              │ (orden sintética)  │
                              └───────────────────┘
    """

    def __init__(self, ticker: str, **kwargs):
        self.ticker = ticker
        self.core = SignalCombiner(ticker=ticker, **kwargs)
        self._last_direction = Direction.NEUTRAL
        self._consecutive_same = 0

    def process(
        self,
        timestamp: pd.Timestamp,
        vwap_result: dict,
        bb_result: dict,
        drsi_result: dict,
        smacd_result: dict,
    ) -> dict | None:
        """
        Convierte los dicts raw de cada motor en inputs del combiner
        y retorna la orden para el bot si procede.

        Retorna None si no hay señal accionable.
        """
        vwap = GEXVWAPInput(
            signal=vwap_result.get("signal", "NEUTRAL"),
            price_vs_vwap=vwap_result.get("price_vs_vwap", 0.0),
            shadow_ratio=vwap_result.get("shadow_ratio", 0.0),
            regime=vwap_result.get("regime", "UNKNOWN"),
            net_gamma=vwap_result.get("net_gamma", 0.0),
            band_mult=vwap_result.get("band_mult", 2.0),
        )
        bb = BBGEXInput(
            signal=bb_result.get("signal", "NEUTRAL"),
            pct_b=bb_result.get("pct_b", 0.5),
            bandwidth=bb_result.get("bandwidth", 2.0),
            k_multiplier=bb_result.get("k_multiplier", 2.0),
            regime=bb_result.get("regime", "UNKNOWN"),
            gamma_flip_cross=bb_result.get("gamma_flip_cross", False),
        )
        drsi = DeltaRSIInput(
            signal=drsi_result.get("signal", "NEUTRAL"),
            strength=drsi_result.get("strength", 0),
            delta_rsi=drsi_result.get("delta_rsi", 50.0),
            histogram=drsi_result.get("histogram", 0.0),
            zone=drsi_result.get("zone", "NEUTRAL"),
            sweep_count=drsi_result.get("sweep_count", 0),
            flow_ratio=drsi_result.get("flow_ratio", 0.0),
        )
        smacd = ShadowMACDInput(
            signal_name=smacd_result.get("signal_name", "NEUTRAL"),
            strength=smacd_result.get("strength", 0),
            macd=smacd_result.get("macd", 0.0),
            histogram=smacd_result.get("histogram", 0.0),
            ndde=smacd_result.get("ndde", 0.0),
            charm_flow=smacd_result.get("charm_flow", 0.0),
            put_call_ratio=smacd_result.get("put_call_ratio", 1.0),
        )

        out = self.core.combine(timestamp, vwap, bb, drsi, smacd)

        if not out.entry_allowed:
            return None
        if out.direction == Direction.NEUTRAL:
            return None

        # Filtro de persistencia: evitar flips rápidos
        if out.direction == self._last_direction:
            self._consecutive_same += 1
        else:
            self._consecutive_same = 0
            self._last_direction = out.direction

        return {
            "ticker": self.ticker,
            "side": "BUY" if out.direction == Direction.LONG else "SELL",
            "score": out.score,
            "confidence": out.confidence,
            "size_pct": out.recommended_size_pct,
            "risk": out.risk_level,
            "regime": out.regime.value,
            "timestamp": timestamp,
        }


# ─────────────────────────────────────────────
# 9. EJECUCIÓN DEMO
# ─────────────────────────────────────────────

if __name__ == "__main__":
    TICKERS = ["AAPL", "MSFT", "PLTR", "TSLA", "GOOGL"]

    for ticker in TICKERS:
        df = run_signal_combiner_pipeline(
            ticker=ticker,
            n=390,
            entry_threshold=35.0,
            verbose=True,
        )
        df.to_csv(f"/tmp/signal_combiner_{ticker.lower()}.csv")

    print("\n✓ Signal Combiner completado para los 5 proxies BingX.")
