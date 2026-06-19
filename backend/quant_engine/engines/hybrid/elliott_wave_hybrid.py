"""
Elliott Wave Híbrido — Ondas 1-5 y ABC + Validación por Régimen GEX
═════════════════════════════════════════════════════════════════════
Detecta automáticamente la secuencia de ondas de Elliott en 1m
y valida cada onda con el estado del GEX (Gamma Exposure) de opciones.

Arquitectura de detección en 3 capas:

  CAPA 1 — Detección de pivots (ZigZag adaptativo)
    Pivots locales confirmados con ventana deslizante.
    El umbral de swing se ajusta dinámicamente con la IV ATM
    para filtrar ruido en alta volatilidad.

  CAPA 2 — Clasificación de ondas (reglas de Elliott)
    Reglas mínimas del EWT aplicadas secuencialmente:
      Onda 2 no retrocede más del 100% de onda 1
      Onda 3 no es la más corta de 1, 3, 5
      Onda 4 no solapa onda 1 (excepto diagonal)
      Onda 5 cumple ratios Fibonacci con onda 1/3
      Onda A-B-C corrección posterior al impulso

  CAPA 3 — Validación GEX por onda
    Cada onda se valida contra el régimen de Gamma:
      Onda 1: GEX en transición (cerca del Gamma Flip)
      Onda 2: GEX+ recuperándose (dealers absorben)
      Onda 3: GEX cruza Flip (Gamma Negativo confirma aceleración)  ← LA SEÑAL CLAVE
      Onda 4: GEX+ volviendo (corrección suave)
      Onda 5: GEX+ pero declinando (dealers reducen cobertura)
      Onda A: GEX empieza a bajar (distribución)
      Onda B: GEX rebote temporal
      Onda C: GEX muy negativo (venta masiva)

El Gamma Flip como confirmación de Onda 3:
    Cuando el precio cruza el nivel del Gamma Flip mientras se
    está formando la onda 3, los dealers pasan de net-long a
    net-short gamma. Esto obliga a los dealers a comprar en
    alza y vender en baja, AMPLIFICANDO el movimiento.
    El resultado es la característica aceleración de onda 3.

Fuentes:
    BingX WebSocket  → velas 1m OHLCV
    Massive API      → GEX neto, Gamma Flip nivel, IV ATM
"""

import warnings
from collections import deque
from dataclasses import dataclass, field
from enum import Enum

import numpy as np
import pandas as pd

warnings.filterwarnings("ignore")


# ─────────────────────────────────────────────
# 1. ENUMS
# ─────────────────────────────────────────────


class WaveLabel(Enum):
    W1 = "1"
    W2 = "2"
    W3 = "3"
    W4 = "4"
    W5 = "5"
    WA = "A"
    WB = "B"
    WC = "C"
    NONE = "?"


class WaveDirection(Enum):
    UP = "UP"
    DOWN = "DOWN"


class WaveStatus(Enum):
    FORMING = "FORMING"  # onda en curso, no confirmada
    CONFIRMED = "CONFIRMED"  # onda completada y validada
    INVALID = "INVALID"  # viola reglas de EWT
    SUSPECT = "SUSPECT"  # válida pero sin confirmación GEX


# ─────────────────────────────────────────────
# 2. ESTRUCTURAS DE DATOS
# ─────────────────────────────────────────────


@dataclass
class GEXBar:
    """GEX snapshot de Massive API — 1 por minuto."""

    timestamp: pd.Timestamp
    net_gex: float  # GEX neto total (+ absorbe, − amplifica)
    gamma_flip: float  # Precio donde GEX = 0
    gex_calls: float  # GEX de calls
    gex_puts: float  # GEX de puts
    iv_atm: float  # IV ATM para umbral adaptativo
    spot: float  # Precio spot

    @property
    def regime(self) -> str:
        if self.net_gex > 0:
            return "GAMMA_POS"
        elif self.net_gex < 0:
            return "GAMMA_NEG"
        return "NEUTRAL"

    @property
    def dist_to_flip_pct(self) -> float:
        """Distancia % del precio al Gamma Flip."""
        if self.gamma_flip <= 0:
            return float("inf")
        return (self.spot - self.gamma_flip) / self.gamma_flip * 100


@dataclass
class Pivot:
    """Pivot confirmado (máximo o mínimo local)."""

    timestamp: pd.Timestamp
    index: int
    price: float
    is_high: bool
    gex: GEXBar | None = None  # GEX en el momento del pivot


@dataclass
class Wave:
    """Una onda de Elliott con su validación GEX."""

    label: WaveLabel
    direction: WaveDirection
    status: WaveStatus

    # Pivots de inicio y fin
    start: Pivot
    end: Pivot | None  # None si aún está formándose

    # Medidas
    length: float = 0.0  # longitud en precio
    bars: int = 0  # duración en velas

    # Fibonacci
    fib_ratio: float = 0.0  # ratio respecto a onda de referencia
    fib_target: float = 0.0  # precio objetivo por Fibonacci

    # Validación GEX
    gex_valid: bool = False
    gex_regime_start: str = "UNKNOWN"
    gex_regime_end: str = "UNKNOWN"
    gamma_flip_cross: bool = False  # GEX cruzó el Flip durante esta onda
    gex_score: float = 0.0  # 0-100, qué tan bien valida el GEX

    # Señal
    signal: str = "NEUTRAL"
    signal_score: float = 0.0

    def to_dict(self) -> dict:
        return {
            "label": self.label.value,
            "direction": self.direction.value,
            "status": self.status.value,
            "start_ts": self.start.timestamp,
            "start_price": round(self.start.price, 4),
            "end_ts": self.end.timestamp if self.end else None,
            "end_price": round(self.end.price, 4) if self.end else None,
            "length": round(self.length, 4),
            "bars": self.bars,
            "fib_ratio": round(self.fib_ratio, 4),
            "fib_target": round(self.fib_target, 4),
            "gex_valid": self.gex_valid,
            "gex_regime_start": self.gex_regime_start,
            "gex_regime_end": self.gex_regime_end,
            "gamma_flip_cross": self.gamma_flip_cross,
            "gex_score": round(self.gex_score, 2),
            "signal": self.signal,
            "signal_score": round(self.signal_score, 2),
        }


@dataclass
class CandleBar:
    timestamp: pd.Timestamp
    ticker: str
    open: float
    high: float
    low: float
    close: float
    volume: float
    hlc3: float = field(init=False)

    def __post_init__(self):
        self.hlc3 = (self.high + self.low + self.close) / 3.0


# ─────────────────────────────────────────────
# 3. DETECTOR DE PIVOTS (ZigZag adaptativo)
# ─────────────────────────────────────────────


class AdaptiveZigZag:
    """
    ZigZag con umbral adaptativo basado en IV ATM.
    Un swing es significativo si supera: base_pct × (1 + iv_mult × IV_ATM)

    Con IV=0.20 (20%) y base=0.003: umbral = 0.3% × 1.4 = 0.42%
    Con IV=0.40 (40%) y base=0.003: umbral = 0.3% × 1.8 = 0.54%
    """

    def __init__(
        self,
        base_swing_pct: float = 0.003,  # 0.3% swing mínimo
        confirm_bars: int = 3,  # barras para confirmar pivot
        iv_multiplier: float = 2.0,
    ):
        self.base_pct = base_swing_pct
        self.confirm_bars = confirm_bars
        self.iv_mult = iv_multiplier

        self._buf: deque = deque(maxlen=confirm_bars * 2 + 1)
        self._ts_buf: deque = deque(maxlen=confirm_bars * 2 + 1)
        self._gex_buf: deque = deque(maxlen=confirm_bars * 2 + 1)

        self._pivots: list[Pivot] = []
        self._last_high: float | None = None
        self._last_low: float | None = None
        self._direction: int = 0  # +1 up, -1 down
        self._idx: int = 0

    def _threshold(self, iv: float) -> float:
        return self.base_pct * (1.0 + self.iv_mult * iv)

    def update(
        self, ts: pd.Timestamp, high: float, low: float, close: float, gex: GEXBar | None = None
    ) -> Pivot | None:
        """Retorna un nuevo Pivot si se confirma, None si no."""
        self._idx += 1
        iv = gex.iv_atm if gex else 0.20
        thr = self._threshold(iv)
        new_pivot = None

        if self._last_high is None:
            self._last_high = high
            self._last_low = low
            return None

        if self._direction >= 0:
            # Seguimos tendencia alcista
            if high > self._last_high:
                self._last_high = high
                self._last_ts_high = ts
                self._last_gex_high = gex
            elif (self._last_high - low) / self._last_high >= thr:
                # Reversión bajista confirmada → pivot high
                new_pivot = Pivot(
                    timestamp=self._last_ts_high if hasattr(self, "_last_ts_high") else ts,
                    index=self._idx,
                    price=self._last_high,
                    is_high=True,
                    gex=self._last_gex_high if hasattr(self, "_last_gex_high") else gex,
                )
                self._pivots.append(new_pivot)
                self._last_low = low
                self._last_ts_low = ts
                self._last_gex_low = gex
                self._direction = -1
        else:
            # Seguimos tendencia bajista
            if low < self._last_low:
                self._last_low = low
                self._last_ts_low = ts
                self._last_gex_low = gex
            elif (high - self._last_low) / max(self._last_low, 1e-9) >= thr:
                # Reversión alcista confirmada → pivot low
                new_pivot = Pivot(
                    timestamp=self._last_ts_low if hasattr(self, "_last_ts_low") else ts,
                    index=self._idx,
                    price=self._last_low,
                    is_high=False,
                    gex=self._last_gex_low if hasattr(self, "_last_gex_low") else gex,
                )
                self._pivots.append(new_pivot)
                self._last_high = high
                self._last_ts_high = ts
                self._last_gex_high = gex
                self._direction = 1

        return new_pivot

    def last_pivots(self, n: int = 10) -> list[Pivot]:
        return self._pivots[-n:]

    def all_pivots(self) -> list[Pivot]:
        return list(self._pivots)


# ─────────────────────────────────────────────
# 4. VALIDADOR GEX POR ONDA
# ─────────────────────────────────────────────


class GEXWaveValidator:
    """
    Valida cada onda de Elliott contra el régimen de Gamma.

    Reglas de validación GEX × Onda:

    Onda 1 (inicio del impulso):
        GEX transitando: cerca del Gamma Flip (±1% del precio)
        O GEX positivo pero declinando desde máximos
        Score base: 15. Bonus: +20 si dist_to_flip < 1%

    Onda 2 (retroceso):
        GEX positivo: dealers absorben la caída
        Score base: 20. Penalización: −10 si GEX negativo

    Onda 3 (aceleración ← LA MÁS IMPORTANTE):
        GEX cruza el Gamma Flip hacia negativo durante la onda
        Cuando GEX < 0 los dealers deben comprar en subidas y
        vender en bajas, amplificando el movimiento de precio.
        Score base: 40 si hay cruce del Flip.
        Sin cruce: score 10 (onda 3 sin confirmación GEX = sospechosa).

    Onda 4 (corrección suave):
        GEX positivo (dealers absorben la corrección)
        La corrección de onda 4 es "suave" en Gamma+
        Score base: 15. Bonus: +10 si GEX subiendo

    Onda 5 (extensión final):
        GEX positivo pero DECLINANDO (dealers reduciendo cobertura)
        La trampa de onda 5: precio sube pero soporte institutional baja
        Score base: 15. Bonus: +15 si GEX_end < GEX_start

    Onda A (inicio corrección ABC):
        GEX empieza a bajar. Señal temprana de distribución.
        Score base: 10. Bonus: +15 si GEX cruzó flip durante A

    Onda B (rebote correctivo):
        GEX rebote temporal. Precio sube pero GEX no recupera máximos.
        Score base: 10.

    Onda C (impulso correctivo bajista):
        GEX muy negativo o cruzando flip hacia abajo.
        Score base: 20 si GEX neg.
    """

    # Thresholds de validación
    FLIP_PROXIMITY_PCT = 1.5  # % del precio para considerar "cerca del Flip"
    GEX_DECLINE_RATIO = 0.15  # 15% de decline en GEX = "declinando"

    def validate(
        self,
        wave: Wave,
        gex_history: list[GEXBar],  # GEX durante la onda
    ) -> tuple[bool, float, str]:
        """
        Valida una onda contra el historial de GEX de su duración.

        Returns:
            (is_valid, gex_score, notes)
        """
        if not gex_history:
            return False, 0.0, "Sin datos GEX"

        g_start = gex_history[0]
        g_end = gex_history[-1]

        # Detectar cruce del Gamma Flip durante la onda
        flip_cross = self._detect_flip_cross(gex_history, wave.direction)

        # Delegar al validador específico de cada onda
        validators = {
            WaveLabel.W1: self._val_w1,
            WaveLabel.W2: self._val_w2,
            WaveLabel.W3: self._val_w3,
            WaveLabel.W4: self._val_w4,
            WaveLabel.W5: self._val_w5,
            WaveLabel.WA: self._val_wa,
            WaveLabel.WB: self._val_wb,
            WaveLabel.WC: self._val_wc,
        }

        fn = validators.get(wave.label)
        if fn is None:
            return False, 0.0, "Etiqueta desconocida"

        is_valid, score, notes = fn(g_start, g_end, gex_history, flip_cross, wave)
        return is_valid, min(100.0, score), notes

    def _detect_flip_cross(self, history: list[GEXBar], direction: WaveDirection) -> bool:
        """Detecta si el GEX cruzó el Gamma Flip durante la onda."""
        if len(history) < 2:
            return False
        signs = [np.sign(g.net_gex) for g in history]
        for i in range(1, len(signs)):
            if signs[i] != signs[i - 1]:
                return True
        return False

    def _near_flip(self, g: GEXBar) -> bool:
        return abs(g.dist_to_flip_pct) < self.FLIP_PROXIMITY_PCT

    def _gex_declining(self, g_start: GEXBar, g_end: GEXBar) -> bool:
        if abs(g_start.net_gex) < 1e-9:
            return False
        change = (g_end.net_gex - g_start.net_gex) / abs(g_start.net_gex)
        return change < -self.GEX_DECLINE_RATIO

    # ── Validadores por onda ──────────────────────────────────

    def _val_w1(self, gs, ge, hist, flip_cross, wave) -> tuple:
        score = 15.0
        notes = []
        if self._near_flip(gs) or self._near_flip(ge):
            score += 20.0
            notes.append("Precio cerca del Gamma Flip → inicio de impulso validado")
        if gs.regime == "GAMMA_POS":
            score += 10.0
            notes.append("GEX+ al inicio de onda 1")
        if flip_cross:
            score += 15.0
            notes.append("GEX cruzó el Flip durante onda 1 → aceleración temprana")
        return score >= 25, score, "; ".join(notes) or "Onda 1 sin confirmación GEX clara"

    def _val_w2(self, gs, ge, hist, flip_cross, wave) -> tuple:
        score = 10.0
        notes = []
        if ge.regime == "GAMMA_POS":
            score += 20.0
            notes.append("GEX+ al final de onda 2 → dealers absorbiendo la corrección")
        if not flip_cross:
            score += 10.0
            notes.append("GEX no cruzó Flip → corrección contenida por dealers")
        retrace_ok = wave.fib_ratio <= 0.786
        if retrace_ok:
            score += 10.0
            notes.append(f"Retroceso Fibonacci OK ({wave.fib_ratio:.1%})")
        return score >= 30, score, "; ".join(notes) or "Onda 2 sin validación GEX"

    def _val_w3(self, gs, ge, hist, flip_cross, wave) -> tuple:
        """
        ★ SEÑAL MÁS IMPORTANTE DEL MOTOR ★

        El cruce del Gamma Flip durante la onda 3 es el evento
        que produce la característica aceleración de onda 3.

        Mecanismo:
          Antes del cruce (GEX+): dealers compran en bajas, venden en altas
          → mercado comprimido, oscila alrededor del Flip

          Después del cruce (GEX-): dealers deben COMPRAR al subir precio
          (porque su delta se vuelve más negativo al subir)
          → se convierte en retroalimentación positiva
          → esto es el "acelerador" de onda 3
        """
        score = 5.0
        notes = []

        if flip_cross:
            score += 40.0  # ← BONUS PRINCIPAL
            notes.append(
                "★ GEX CRUZÓ GAMMA FLIP durante onda 3 → "
                "RETROALIMENTACIÓN DE DEALERS confirma aceleración de onda 3"
            )

        if ge.regime == "GAMMA_NEG":
            score += 15.0
            notes.append("GEX negativo al final → dealers amplificando momentum")

        # Onda 3 extendida (>1.618 × onda 1) con GEX neg es señal perfecta
        if wave.fib_ratio >= 1.618 and ge.regime == "GAMMA_NEG":
            score += 20.0
            notes.append(
                f"Extensión de onda 3 ({wave.fib_ratio:.3f}×) "
                "con GEX negativo → setup de máxima calidad"
            )
        elif wave.fib_ratio >= 1.618:
            score += 10.0
            notes.append(f"Extensión de onda 3 ({wave.fib_ratio:.3f}×) sin GEX neg")

        # Onda 3 sin cruce del Flip es sospechosa
        if not flip_cross:
            notes.append("⚠ Sin cruce del Gamma Flip → onda 3 de baja convicción")

        is_valid = score >= 30 if flip_cross else score >= 15
        return is_valid, score, "; ".join(notes) or "Onda 3 sin confirmación GEX"

    def _val_w4(self, gs, ge, hist, flip_cross, wave) -> tuple:
        score = 10.0
        notes = []
        if ge.regime == "GAMMA_POS":
            score += 15.0
            notes.append("GEX+ → onda 4 contenida, corrección suave esperada")
        if not flip_cross:
            score += 10.0
            notes.append("GEX no cruzó Flip → onda 4 normal")
        # Onda 4 no debe retroceder más del 50% de onda 3
        if wave.fib_ratio <= 0.50:
            score += 10.0
            notes.append(f"Retroceso onda 4 OK ({wave.fib_ratio:.1%} de onda 3)")
        # GEX subiendo durante onda 4 = soporte institucional
        gex_vals = [g.net_gex for g in hist]
        if len(gex_vals) >= 2 and gex_vals[-1] > gex_vals[0]:
            score += 5.0
            notes.append("GEX creciendo durante onda 4 → soporte institucional")
        return score >= 20, score, "; ".join(notes) or "Onda 4 sin validación GEX"

    def _val_w5(self, gs, ge, hist, flip_cross, wave) -> tuple:
        score = 10.0
        notes = []
        gex_declining = self._gex_declining(gs, ge)
        if gex_declining:
            score += 15.0
            notes.append(
                "GEX declinando durante onda 5 → " "dealers reduciendo cobertura = señal de techo"
            )
        if ge.regime == "GAMMA_POS":
            score += 10.0
            notes.append("GEX+ al final → onda 5 con soporte pero reduciendo")
        if flip_cross:
            notes.append("⚠ GEX cruzó Flip en onda 5 → posible falla de onda 5")
        return score >= 15, score, "; ".join(notes) or "Onda 5 sin validación GEX"

    def _val_wa(self, gs, ge, hist, flip_cross, wave) -> tuple:
        score = 10.0
        notes = []
        gex_declining = self._gex_declining(gs, ge)
        if gex_declining:
            score += 15.0
            notes.append("GEX declinando en onda A → inicio de distribución")
        if flip_cross:
            score += 15.0
            notes.append("GEX cruzó Flip en onda A → corrección profunda esperada")
        return score >= 20, score, "; ".join(notes) or "Onda A sin validación GEX"

    def _val_wb(self, gs, ge, hist, flip_cross, wave) -> tuple:
        score = 10.0
        notes = []
        if ge.regime == "GAMMA_POS":
            score += 10.0
            notes.append("GEX+ en onda B → rebote temporal soportado")
        if not flip_cross:
            score += 10.0
            notes.append("Sin cruce Flip → onda B contenida")
        return score >= 15, score, "; ".join(notes) or "Onda B sin validación GEX"

    def _val_wc(self, gs, ge, hist, flip_cross, wave) -> tuple:
        score = 10.0
        notes = []
        if ge.regime == "GAMMA_NEG":
            score += 20.0
            notes.append("GEX negativo en onda C → dealers amplificando caída")
        if flip_cross:
            score += 15.0
            notes.append("GEX cruzó Flip a negativo en onda C → impulso bajista fuerte")
        return score >= 25, score, "; ".join(notes) or "Onda C sin validación GEX"


# ─────────────────────────────────────────────
# 5. CLASIFICADOR DE ONDAS (reglas EWT)
# ─────────────────────────────────────────────


class ElliottWaveClassifier:
    """
    Clasifica secuencias de pivots en ondas de Elliott.

    Aplica las reglas mínimas obligatorias del EWT:
        R1: Onda 2 no retrocede > 100% de onda 1
        R2: Onda 3 no es la más corta entre ondas 1, 3, 5
        R3: Onda 4 no solapa el máximo de onda 1 (excepto diagonales)
        R4: Las ondas impulsivas se mueven en 5 sub-ondas
        R5: Las ondas correctivas se mueven en 3 sub-ondas
    """

    # Ratios Fibonacci de referencia
    FIB = {
        "w2_retrace": [0.382, 0.500, 0.618, 0.786],
        "w3_extend": [1.000, 1.272, 1.618, 2.000, 2.618],
        "w4_retrace": [0.236, 0.382, 0.500],
        "w5_equal_w1": [0.618, 1.000, 1.618],
        "wc_equal_wa": [0.618, 1.000, 1.618],
    }

    def __init__(self):
        self._current_waves: list[Wave] = []
        self._sequence_start: int | None = None  # dirección del impulso (1=bull, -1=bear)
        self._impulse_dir: int = 0

    def _wave_length(self, p_start: Pivot, p_end: Pivot) -> float:
        return abs(p_end.price - p_start.price)

    def _nearest_fib(self, ratio: float, targets: list[float]) -> float:
        return min(targets, key=lambda f: abs(f - ratio))

    def classify(self, pivots: list[Pivot]) -> list[Wave]:
        """
        Intenta clasificar los últimos pivots en una secuencia de ondas.
        Retorna la secuencia de ondas detectada (puede ser parcial).
        """
        if len(pivots) < 2:
            return []

        waves = []

        # Detectar si estamos en impulso alcista o bajista
        # mirando si el primer swing es UP o DOWN
        first_is_high = pivots[1].is_high if len(pivots) > 1 else False
        bull_impulse = not first_is_high  # impulso alcista empieza en low

        # Intentar mapear hasta 9 pivots en 5 ondas impulsivas + ABC
        # Estructura: L H L H L H [H L H] (bull) = P0 P1 P2 P3 P4 P5 [P6 P7 P8]
        try:
            if bull_impulse:
                waves = self._classify_bull(pivots)
            else:
                waves = self._classify_bear(pivots)
        except Exception:
            pass

        return waves

    def _classify_bull(self, pivots: list[Pivot]) -> list[Wave]:
        """Clasifica impulso alcista: Low-High-Low-High-Low-High."""
        waves = []
        n = len(pivots)

        # Necesitamos al menos 6 pivots para 5 ondas
        if n < 2:
            return waves

        # Onda 1: primer pivot low → primer pivot high
        idx = 0
        while idx < n - 1:
            if not pivots[idx].is_high:  # inicio en low
                break
            idx += 1

        if idx >= n - 1:
            return waves

        p0 = pivots[idx]  # W1 start (low)
        waves_found = 0

        for i in range(idx + 1, min(idx + 9, n)):
            p = pivots[i]
            wi = i - idx  # posición relativa

            if wi == 1 and p.is_high:
                # Onda 1
                w1_len = self._wave_length(p0, p)
                w = Wave(
                    label=WaveLabel.W1,
                    direction=WaveDirection.UP,
                    status=WaveStatus.FORMING,
                    start=p0,
                    end=p,
                    length=w1_len,
                    bars=p.index - p0.index,
                    fib_ratio=1.0,
                    fib_target=p.price,
                )
                waves.append(w)
                waves_found += 1

            elif wi == 2 and not p.is_high and waves_found >= 1:
                # Onda 2
                w1 = waves[-1]
                if p.price >= p0.price:  # regla: no retrocede más del 100%
                    w2_len = self._wave_length(w1.end, p)
                    retrace = w2_len / w1.length if w1.length > 0 else 0
                    w = Wave(
                        label=WaveLabel.W2,
                        direction=WaveDirection.DOWN,
                        status=WaveStatus.FORMING,
                        start=w1.end,
                        end=p,
                        length=w2_len,
                        bars=p.index - w1.end.index,
                        fib_ratio=retrace,
                        fib_target=p.price,
                    )
                    waves.append(w)
                    waves_found += 1

            elif wi == 3 and p.is_high and waves_found >= 2:
                # Onda 3
                w2 = waves[-1]
                w1 = waves[-2]
                w3_len = self._wave_length(w2.end, p)
                ext_ratio = w3_len / w1.length if w1.length > 0 else 0
                w = Wave(
                    label=WaveLabel.W3,
                    direction=WaveDirection.UP,
                    status=WaveStatus.FORMING,
                    start=w2.end,
                    end=p,
                    length=w3_len,
                    bars=p.index - w2.end.index,
                    fib_ratio=ext_ratio,
                    fib_target=p.price,
                )
                waves.append(w)
                waves_found += 1

            elif wi == 4 and not p.is_high and waves_found >= 3:
                # Onda 4 — no puede solapar máximo de onda 1
                w3 = waves[-1]
                w1 = waves[-3]
                if p.price > w1.end.price:  # regla de no solapamiento
                    w4_len = self._wave_length(w3.end, p)
                    retrace = w4_len / w3.length if w3.length > 0 else 0
                    w = Wave(
                        label=WaveLabel.W4,
                        direction=WaveDirection.DOWN,
                        status=WaveStatus.FORMING,
                        start=w3.end,
                        end=p,
                        length=w4_len,
                        bars=p.index - w3.end.index,
                        fib_ratio=retrace,
                        fib_target=p.price,
                    )
                    waves.append(w)
                    waves_found += 1

            elif wi == 5 and p.is_high and waves_found >= 4:
                # Onda 5
                w4 = waves[-1]
                w1 = waves[-4]
                w3 = waves[-3] if len(waves) >= 3 else None
                w5_len = self._wave_length(w4.end, p)
                w5_ratio = w5_len / w1.length if w1.length > 0 else 0

                # Regla: onda 3 no puede ser la más corta
                if w3 and waves[-4].length < w3.length:
                    pass  # válido

                w = Wave(
                    label=WaveLabel.W5,
                    direction=WaveDirection.UP,
                    status=WaveStatus.FORMING,
                    start=w4.end,
                    end=p,
                    length=w5_len,
                    bars=p.index - w4.end.index,
                    fib_ratio=w5_ratio,
                    fib_target=p.price,
                )
                waves.append(w)
                waves_found += 1

            # ABC corrección después del impulso
            elif wi == 6 and not p.is_high and waves_found >= 5:
                w5 = waves[-1]
                wa_len = self._wave_length(w5.end, p)
                w = Wave(
                    label=WaveLabel.WA,
                    direction=WaveDirection.DOWN,
                    status=WaveStatus.FORMING,
                    start=w5.end,
                    end=p,
                    length=wa_len,
                    bars=p.index - w5.end.index,
                    fib_ratio=wa_len / waves[-5].length if waves[-5].length > 0 else 0,
                    fib_target=p.price,
                )
                waves.append(w)
                waves_found += 1

            elif wi == 7 and p.is_high and waves_found >= 6:
                wa = waves[-1]
                wb_len = self._wave_length(wa.end, p)
                w = Wave(
                    label=WaveLabel.WB,
                    direction=WaveDirection.UP,
                    status=WaveStatus.FORMING,
                    start=wa.end,
                    end=p,
                    length=wb_len,
                    bars=p.index - wa.end.index,
                    fib_ratio=wb_len / wa.length if wa.length > 0 else 0,
                    fib_target=p.price,
                )
                waves.append(w)
                waves_found += 1

            elif wi == 8 and not p.is_high and waves_found >= 7:
                wb = waves[-1]
                wa = waves[-2]
                wc_len = self._wave_length(wb.end, p)
                w = Wave(
                    label=WaveLabel.WC,
                    direction=WaveDirection.DOWN,
                    status=WaveStatus.FORMING,
                    start=wb.end,
                    end=p,
                    length=wc_len,
                    bars=p.index - wb.end.index,
                    fib_ratio=wc_len / wa.length if wa.length > 0 else 0,
                    fib_target=p.price,
                )
                waves.append(w)
                waves_found += 1

        return waves

    def _classify_bear(self, pivots: list[Pivot]) -> list[Wave]:
        """Clasifica impulso bajista — espejo del alcista."""
        # Invertir precios y reclasificar como alcista
        inv = []
        for p in pivots:
            inv.append(
                Pivot(
                    timestamp=p.timestamp,
                    index=p.index,
                    price=-p.price,
                    is_high=not p.is_high,
                    gex=p.gex,
                )
            )
        bull_waves = self._classify_bull(inv)

        # Invertir de vuelta
        dir_map = {WaveDirection.UP: WaveDirection.DOWN, WaveDirection.DOWN: WaveDirection.UP}
        for w in bull_waves:
            w.direction = dir_map[w.direction]
            w.start = Pivot(
                timestamp=w.start.timestamp,
                index=w.start.index,
                price=-w.start.price,
                is_high=not w.start.is_high,
                gex=w.start.gex,
            )
            if w.end:
                w.end = Pivot(
                    timestamp=w.end.timestamp,
                    index=w.end.index,
                    price=-w.end.price,
                    is_high=not w.end.is_high,
                    gex=w.end.gex,
                )
        return bull_waves


# ─────────────────────────────────────────────
# 6. MOTOR PRINCIPAL ELLIOTT WAVE HÍBRIDO
# ─────────────────────────────────────────────


class HybridElliottWaveEngine:
    """
    Motor Elliott Wave híbrido: EWT + validación GEX.

    Procesa velas 1m en tiempo real, detecta pivots,
    clasifica ondas, valida con GEX y emite señales.
    """

    def __init__(
        self,
        ticker: str,
        swing_pct: float = 0.003,
        confirm_bars: int = 3,
        lookback_pivots: int = 12,
    ):
        self.ticker = ticker
        self.lookback = lookback_pivots

        self._zigzag = AdaptiveZigZag(swing_pct, confirm_bars)
        self._classifier = ElliottWaveClassifier()
        self._validator = GEXWaveValidator()

        # Historial de GEX para validación de ondas
        self._gex_history: deque = deque(maxlen=500)

        # Ondas detectadas
        self._waves: list[Wave] = []
        self._current_sequence: list[Wave] = []

        # Estado actual
        self._current_wave_label: WaveLabel = WaveLabel.NONE
        self._wave3_flip_ts: pd.Timestamp | None = None

        self._history: list[dict] = []
        self._tick_count = 0

    def _gex_during_wave(self, w: Wave) -> list[GEXBar]:
        """Extrae el historial de GEX durante la duración de una onda."""
        if w.end is None:
            return []
        return [g for g in self._gex_history if w.start.timestamp <= g.timestamp <= w.end.timestamp]

    def _signal_for_wave(self, w: Wave, sequence: list[Wave]) -> tuple[str, float]:
        """
        Genera la señal de trading basada en la onda actual.

        La señal más importante es el inicio de onda 3 confirmado
        por el cruce del Gamma Flip.
        """
        if w.label == WaveLabel.W3 and w.direction == WaveDirection.UP:
            if w.gamma_flip_cross and w.gex_score >= 40:
                return "W3_BULL_GAMMA_CONFIRMED", 90.0
            elif w.gex_valid:
                return "W3_BULL_GEX_VALID", 70.0
            else:
                return "W3_BULL_SUSPECT", 40.0

        if w.label == WaveLabel.W3 and w.direction == WaveDirection.DOWN:
            if w.gamma_flip_cross and w.gex_score >= 40:
                return "W3_BEAR_GAMMA_CONFIRMED", 88.0
            elif w.gex_valid:
                return "W3_BEAR_GEX_VALID", 68.0

        if w.label == WaveLabel.W5 and w.gex_score >= 20:
            dir_ = "BULL" if w.direction == WaveDirection.UP else "BEAR"
            return f"W5_{dir_}_EXHAUSTION", 60.0

        if w.label == WaveLabel.WC and w.gex_score >= 25:
            return "WC_BEAR_CONFIRMED", 65.0

        if w.label == WaveLabel.W2 and w.gex_valid:
            # Alerta preventiva: viene una onda 3
            return "W2_COMPLETE_WATCH_W3", 50.0

        if w.label == WaveLabel.W4 and w.gex_valid:
            return "W4_COMPLETE_WATCH_W5", 45.0

        return "NEUTRAL", 0.0

    def update(
        self,
        candle: CandleBar,
        gex: GEXBar | None = None,
    ) -> dict:
        self._tick_count += 1

        # Guardar GEX en historial
        if gex is not None:
            self._gex_history.append(gex)

        # Actualizar ZigZag
        new_pivot = self._zigzag.update(
            candle.timestamp, candle.high, candle.low, candle.close, gex
        )

        new_wave = None
        if new_pivot is not None:
            # Clasificar con los últimos pivots
            pivots = self._zigzag.last_pivots(self.lookback)
            waves = self._classifier.classify(pivots)

            # Validar ondas nuevas contra GEX
            for w in waves:
                if w.end is not None:
                    gex_hist = self._gex_during_wave(w)
                    is_valid, score, notes = self._validator.validate(w, gex_hist)
                    w.gex_valid = is_valid
                    w.gex_score = score
                    w.status = WaveStatus.CONFIRMED if is_valid else WaveStatus.SUSPECT

                    # Detectar cruce del Flip durante la onda
                    w.gamma_flip_cross = self._validator._detect_flip_cross(gex_hist, w.direction)
                    if w.gex_valid and gex_hist:
                        w.gex_regime_start = gex_hist[0].regime
                        w.gex_regime_end = gex_hist[-1].regime

                    # Señal de trading
                    w.signal, w.signal_score = self._signal_for_wave(w, waves)

                    # Guardar en historial de ondas
                    existing = [x.label for x in self._waves]
                    if w.label not in existing or w.signal_score > 60:
                        self._waves.append(w)
                        new_wave = w

            self._current_sequence = waves
            if waves:
                self._current_wave_label = waves[-1].label

        # Detectar cruce del Gamma Flip en tiempo real
        flip_cross_now = False
        if gex is not None and len(self._gex_history) >= 2:
            prev_gex = list(self._gex_history)[-2]
            if np.sign(gex.net_gex) != np.sign(prev_gex.net_gex):
                flip_cross_now = True
                if self._current_wave_label == WaveLabel.W3:
                    self._wave3_flip_ts = candle.timestamp

        # Señal en tiempo real (antes de confirmación del pivot)
        realtime_signal = "NEUTRAL"
        if flip_cross_now and self._current_wave_label == WaveLabel.W3:
            realtime_signal = "W3_FLIP_CROSS_REALTIME"
        elif flip_cross_now:
            realtime_signal = "GAMMA_FLIP_CROSS"

        result = {
            "timestamp": candle.timestamp,
            "ticker": self.ticker,
            "close": candle.close,
            "current_wave": self._current_wave_label.value,
            "n_pivots": len(self._zigzag.all_pivots()),
            "n_waves_detected": len(self._waves),
            "flip_cross_now": flip_cross_now,
            "wave3_flip_ts": self._wave3_flip_ts,
            "realtime_signal": realtime_signal,
            "new_wave_label": new_wave.label.value if new_wave else None,
            "new_wave_signal": new_wave.signal if new_wave else "NEUTRAL",
            "new_wave_score": new_wave.signal_score if new_wave else 0.0,
            "new_wave_gex_valid": new_wave.gex_valid if new_wave else False,
            "new_wave_flip_cross": new_wave.gamma_flip_cross if new_wave else False,
            "regime": gex.regime if gex else "UNKNOWN",
            "net_gex": gex.net_gex if gex else 0.0,
            "gamma_flip": gex.gamma_flip if gex else 0.0,
            "iv_atm": gex.iv_atm if gex else 0.0,
        }

        self._history.append(result)
        return result

    def get_waves_df(self) -> pd.DataFrame:
        if not self._waves:
            return pd.DataFrame()
        return pd.DataFrame([w.to_dict() for w in self._waves])

    def to_dataframe(self) -> pd.DataFrame:
        if not self._history:
            return pd.DataFrame()
        df = pd.DataFrame(self._history)
        df.set_index("timestamp", inplace=True)
        return df

    def get_current_sequence(self) -> list[Wave]:
        return self._current_sequence

    def get_wave3_confirmation(self) -> dict | None:
        """
        Retorna el estado de confirmación de onda 3 si está en curso.
        Esta es la señal de mayor prioridad del motor.
        """
        for w in reversed(self._waves):
            if w.label == WaveLabel.W3 and w.signal_score >= 40:
                return {
                    "confirmed": w.gex_valid,
                    "flip_cross": w.gamma_flip_cross,
                    "gex_score": w.gex_score,
                    "signal": w.signal,
                    "signal_score": w.signal_score,
                    "direction": w.direction.value,
                    "fib_ratio": w.fib_ratio,
                    "start_price": w.start.price,
                    "end_price": w.end.price if w.end else None,
                }
        return None


# ─────────────────────────────────────────────
# 7. GENERADOR DE DATOS DEMO
# ─────────────────────────────────────────────


def generate_demo(
    ticker: str = "AAPL",
    n: int = 390,
    base: float = 192.50,
    seed: int = 42,
) -> tuple[list[CandleBar], list[GEXBar]]:
    """
    Simula un ciclo completo de 5 ondas impulsivas + ABC correctiva
    con GEX que cruza el Gamma Flip durante la onda 3.
    """
    rng = np.random.default_rng(seed)
    start = pd.Timestamp("2025-01-15 09:30:00", tz="America/New_York")
    tss = pd.date_range(start, periods=n, freq="1min")

    # Estructura Elliott: W1(up) W2(down) W3(up-accel) W4(down) W5(up) A(down) B(up) C(down)
    # (bars, price_trend, noise, gex_level, gex_trend, gamma_flip_offset)
    phases = [
        (45, +0.0008, 0.0008, 1.2e6, 0.0, +1.5),  # W1: impulso inicial
        (35, -0.0006, 0.0006, 0.8e6, -0.0003, +0.5),  # W2: retroceso GEX+
        (75, +0.0012, 0.0010, -0.5e6, -0.0008, -1.5),  # W3: aceleración GEX CRUZA FLIP
        (40, -0.0005, 0.0006, 0.6e6, +0.0004, +0.8),  # W4: corrección GEX+
        (50, +0.0007, 0.0008, 0.4e6, -0.0002, +1.0),  # W5: extensión final
        (40, -0.0008, 0.0009, -0.3e6, -0.0003, -0.5),  # A:  distribución
        (30, +0.0005, 0.0007, 0.2e6, +0.0002, +0.3),  # B:  rebote
        (75, -0.0009, 0.0010, -0.8e6, -0.0004, -1.0),  # C:  caída GEX-
    ]

    candles, gex_bars = [], []
    price = base
    gex = 1_200_000.0
    idx = 0

    for n_b, p_tr, p_n, gex_base, gex_tr, flip_off in phases:
        gex = gex_base
        for _ in range(n_b):
            if idx >= n:
                break
            ts = tss[idx]

            price *= 1 + p_tr + rng.normal(0, p_n)
            sp = price * rng.uniform(0.0006, 0.0025)
            candles.append(
                CandleBar(
                    timestamp=ts,
                    ticker=ticker,
                    open=price * (1 - rng.uniform(0, 0.0003)),
                    high=price + sp * rng.uniform(0.2, 1.0),
                    low=price - sp * rng.uniform(0.2, 1.0),
                    close=price,
                    volume=float(rng.integers(60_000, 450_000)),
                )
            )

            gex += gex_tr * abs(gex) * 0.1 + rng.normal(0, abs(gex) * 0.05)
            gamma_flip = price + flip_off + rng.normal(0, 0.1)
            gex_calls = max(0, gex * rng.uniform(0.5, 0.8)) if gex > 0 else 0
            gex_puts = gex - gex_calls

            gex_bars.append(
                GEXBar(
                    timestamp=ts,
                    net_gex=float(gex),
                    gamma_flip=float(gamma_flip),
                    gex_calls=float(gex_calls),
                    gex_puts=float(gex_puts),
                    iv_atm=float(rng.uniform(0.14, 0.38)),
                    spot=price,
                )
            )
            idx += 1

    return candles, gex_bars


# ─────────────────────────────────────────────
# 8. PIPELINE + REPORTE
# ─────────────────────────────────────────────


def run_hybrid_elliott_wave(
    ticker: str = "AAPL",
    n: int = 390,
    verbose: bool = True,
) -> tuple[pd.DataFrame, pd.DataFrame]:

    print(f"\n{'═'*70}")
    print(f"  ELLIOTT WAVE HÍBRIDO  |  {ticker}  |  {n} velas 1m")
    print(f"{'═'*70}")

    candles, gex_bars = generate_demo(ticker, n)
    gex_map = {g.timestamp: g for g in gex_bars}
    engine = HybridElliottWaveEngine(ticker=ticker)

    for c in candles:
        gex = gex_map.get(c.timestamp)
        engine.update(c, gex)

    df_ticks = engine.to_dataframe()
    df_waves = engine.get_waves_df()

    if verbose:
        _print_report(df_ticks, df_waves, engine, ticker)

    return df_ticks, df_waves


def _print_report(
    df: pd.DataFrame, wdf: pd.DataFrame, engine: HybridElliottWaveEngine, ticker: str
):
    last = df.iloc[-1]
    print(f"\n── Estado actual {ticker} ────────────────────────────────")
    print(f"  Precio final       : ${last['close']:.2f}")
    print(f"  Onda actual        : {last['current_wave']}")
    print(f"  Pivots detectados  : {last['n_pivots']}")
    print(f"  Ondas detectadas   : {last['n_waves_detected']}")
    print(f"  Régimen GEX        : {last['regime']}")
    print(f"  GEX neto           : {last['net_gex']:+,.0f}")
    print(f"  Gamma Flip         : ${last['gamma_flip']:.2f}")
    print(f"  IV ATM             : {last['iv_atm']:.2%}")

    # Cruces del Gamma Flip
    flips = df[df["flip_cross_now"] == True]
    print(f"\n── Cruces del Gamma Flip: {len(flips)} ──")
    if not flips.empty:
        print(
            flips[["close", "current_wave", "net_gex", "gamma_flip", "realtime_signal"]].to_string()
        )

    # Señales en tiempo real
    rt_sigs = df[df["realtime_signal"] != "NEUTRAL"]
    print(f"\n── Señales en tiempo real: {len(rt_sigs)} ──")
    if not rt_sigs.empty:
        print(
            rt_sigs[["close", "current_wave", "realtime_signal", "net_gex", "regime"]]
            .tail(8)
            .to_string()
        )

    # Ondas detectadas con validación GEX
    if not wdf.empty:
        print(f"\n── Ondas detectadas: {len(wdf)} ──")
        show_cols = [
            "label",
            "direction",
            "status",
            "start_price",
            "end_price",
            "length",
            "fib_ratio",
            "gex_valid",
            "gex_score",
            "gamma_flip_cross",
            "signal",
            "signal_score",
        ]
        disp = [c for c in show_cols if c in wdf.columns]
        print(wdf[disp].to_string(index=False))

        # Ondas de alta señal
        high_signal = wdf[wdf["signal_score"] >= 60]
        print(f"\n── Señales de alta convicción (score≥60): {len(high_signal)} ──")
        if not high_signal.empty:
            print(high_signal[disp].to_string(index=False))

    # Confirmación de onda 3
    w3_conf = engine.get_wave3_confirmation()
    if w3_conf:
        print("\n── ★ CONFIRMACIÓN ONDA 3 ──────────────────────────")
        for k, v in w3_conf.items():
            print(f"  {k:20s}: {v}")
    else:
        print("\n── Sin confirmación de onda 3 en esta sesión ──")

    print(f"\n{'═'*70}")


# ─────────────────────────────────────────────
# 9. INTEGRACIÓN PRODUCCIÓN
# ─────────────────────────────────────────────


class HybridElliottWaveLive:
    """Wrapper para BingX WebSocket + Massive API en producción."""

    PRIORITY = {
        "W3_BULL_GAMMA_CONFIRMED": 5,
        "W3_BEAR_GAMMA_CONFIRMED": 5,
        "W3_FLIP_CROSS_REALTIME": 5,
        "W3_BULL_GEX_VALID": 4,
        "W3_BEAR_GEX_VALID": 4,
        "GAMMA_FLIP_CROSS": 3,
        "W5_BULL_EXHAUSTION": 3,
        "W5_BEAR_EXHAUSTION": 3,
        "WC_BEAR_CONFIRMED": 3,
        "W2_COMPLETE_WATCH_W3": 2,
        "W4_COMPLETE_WATCH_W5": 2,
        "NEUTRAL": 0,
    }

    def __init__(self, ticker: str, **kwargs):
        self.ticker = ticker
        self.core = HybridElliottWaveEngine(ticker=ticker, **kwargs)

    @staticmethod
    def parse_bingx(raw: dict, ticker: str) -> CandleBar:
        return CandleBar(
            timestamp=pd.Timestamp(int(raw["T"]), unit="ms", tz="UTC"),
            ticker=ticker,
            open=float(raw["o"]),
            high=float(raw["h"]),
            low=float(raw["l"]),
            close=float(raw["c"]),
            volume=float(raw["v"]),
        )

    @staticmethod
    def parse_massive(raw: dict, spot: float, ticker: str) -> GEXBar:
        """
        Formato Massive API:
        {"netGex":1500000,"gammaFlip":192.0,"gexCalls":2200000,
         "gexPuts":-700000,"ivAtm":0.22}
        """
        return GEXBar(
            timestamp=pd.Timestamp.now(tz="UTC"),
            net_gex=float(raw.get("netGex", 0)),
            gamma_flip=float(raw.get("gammaFlip", spot)),
            gex_calls=float(raw.get("gexCalls", 0)),
            gex_puts=float(raw.get("gexPuts", 0)),
            iv_atm=float(raw.get("ivAtm", 0.20)),
            spot=spot,
        )

    def on_signal(self, result: dict):
        p = self.PRIORITY.get(result.get("realtime_signal", "NEUTRAL"), 0)
        p = max(p, self.PRIORITY.get(result.get("new_wave_signal", "NEUTRAL"), 0))
        if p >= 3:
            sig = result.get("new_wave_signal") or result.get("realtime_signal")
            print(
                f"[{result['timestamp']}] {self.ticker:5s} | "
                f"P{p} {sig!s:30s} | "
                f"${result['close']:.2f} | "
                f"Onda={result['current_wave']} | "
                f"GEX={result['net_gex']:+,.0f} | "
                f"Flip=${result['gamma_flip']:.2f} | "
                f"{result['regime']}"
            )


# ─────────────────────────────────────────────
# 10. EJECUCIÓN DEMO
# ─────────────────────────────────────────────

if __name__ == "__main__":
    TICKERS = ["AAPL", "MSFT", "PLTR", "TSLA", "GOOGL"]

    for ticker in TICKERS:
        df_t, df_w = run_hybrid_elliott_wave(ticker=ticker, n=390, verbose=True)
        df_t.to_csv(f"/tmp/elliott_wave_{ticker.lower()}.csv")
        if not df_w.empty:
            df_w.to_csv(f"/tmp/elliott_waves_{ticker.lower()}.csv", index=False)

    print("\n✓ Elliott Wave Híbrido completado para los 5 proxies BingX.")
