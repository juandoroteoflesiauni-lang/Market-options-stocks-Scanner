"""
EMA Delta Ribbon Híbrido — Cinta EMA + Shadow Delta Acumulado
═════════════════════════════════════════════════════════════
Combina DOS cintas de EMAs en un único sistema de señales:

    CINTA A (precio):
        EMA_8, EMA_13, EMA_21, EMA_34, EMA_55 sobre el precio de cierre.
        La geometría de la cinta (orden, separación, pendiente) determina
        el estado de la tendencia y su madurez.

    CINTA B (Shadow Delta acumulado):
        EMA_8, EMA_13, EMA_21, EMA_34, EMA_55 sobre el Shadow Delta
        acumulado de opciones (flujo de cobertura neto de dealers).

        Shadow Delta acumulado(t) = Σ [ call_vol_delta − put_vol_delta ] de t=0 a t

        Esto es el equivalente del CVD (Cumulative Volume Delta) pero
        para el flujo de cobertura institucional, no para el volumen spot.

Las señales más potentes emergen de la relación entre ambas cintas:

    Cinta A expanding + Cinta B expanding en la misma dirección:
        → tendencia con respaldo institucional REAL
        → señal de momentum puro para scalping

    Cinta A expanding + Cinta B comprimida o contraria:
        → tendencia de precio sin respaldo de dealers
        → amago o distribución disfrazada

    Cinta A comprimida + Cinta B empezando a expandir:
        → pre-movimiento: dealers posicionándose ANTES que el precio
        → señal anticipatoria de alta calidad

    Cinta A comprimida + Cinta B también comprimida:
        → mercado en equilibrio, esperar señal

Métricas de la cinta:
    width:      separación entre EMA_8 y EMA_55 (px o %)
    slope:      pendiente promedio de las 5 EMAs
    order:      ¿están ordenadas de forma alcista/bajista/mezclada?
    spread_std: desviación estándar de las separaciones (coherencia)
    expansion:  width aumentando (tendencia acelerando)
    compression:width disminuyendo (tendencia decelerando o rebote inminente)

Score de señal (0-100):
    F1. Estado del orden de la cinta A        (0-20 pts)
    F2. Expansión/compresión cinta A          (0-20 pts)
    F3. Alineación cinta A vs cinta B         (0-25 pts)
    F4. Shadow Delta acumulado momentum       (0-20 pts)
    F5. Divergencia entre cintas              (0-15 pts bonus/malus)

Fuentes:
    BingX WebSocket  → velas 1m OHLCV
    Massive API      → Shadow Delta por minuto (call_vol_delta − put_vol_delta)
"""

import warnings
from collections import deque

import numpy as np
import pandas as pd

warnings.filterwarnings("ignore")


# ─────────────────────────────────────────────
# 1. ESTRUCTURAS DE DATOS REMOVIDAS
# ─────────────────────────────────────────────


# ─────────────────────────────────────────────
# 2. EMA INCREMENTAL
# ─────────────────────────────────────────────


class IncrementalEMA:
    """EMA incremental con warm-up SMA y estado persistente."""

    def __init__(self, period: int):
        self.period = period
        self.alpha = 2.0 / (period + 1)
        self._val: float | None = None
        self._buf: list[float] = []
        self._ready: bool = False

    def update(self, x: float) -> float | None:
        if not self._ready:
            self._buf.append(x)
            if len(self._buf) >= self.period:
                self._val = float(np.mean(self._buf))
                self._ready = True
            return self._val
        self._val = self.alpha * x + (1 - self.alpha) * self._val
        return self._val

    @property
    def value(self) -> float | None:
        return self._val

    @property
    def ready(self) -> bool:
        return self._ready


# ─────────────────────────────────────────────
# 3. RIBBON DE EMAs (genérico, reutilizable)
# ─────────────────────────────────────────────


class EMARibbon:
    """
    Cinta de N EMAs sobre cualquier serie numérica.
    Calcula el width, slope, order y estado de expansión.

    Periods default: [8, 13, 21, 34, 55] (Fibonacci)
    """

    def __init__(self, periods: list[int] = None, label: str = ""):
        self.periods = sorted(periods or [8, 13, 21, 34, 55])
        self.label = label
        self._emas = {p: IncrementalEMA(p) for p in self.periods}
        self._prev_width: float | None = None

    def update(self, value: float) -> dict:
        """
        Procesa un nuevo valor y retorna el estado completo de la cinta.
        Retorna None values hasta que todos los períodos tengan warm-up.
        """
        vals = {}
        for p, ema in self._emas.items():
            v = ema.update(value)
            vals[p] = v

        # Verificar si todas las EMAs tienen valor
        if any(v is None for v in vals.values()):
            return self._empty(vals)

        ema_values = [vals[p] for p in self.periods]

        # ── Width: separación entre la más rápida y la más lenta ──
        width = abs(ema_values[0] - ema_values[-1])
        width_pct = width / abs(ema_values[-1]) * 100 if abs(ema_values[-1]) > 1e-9 else 0.0

        # ── Slope: pendiente promedio (dirección de la cinta) ─────
        slopes = []
        for p in self.periods:
            ema_obj = self._emas[p]
            if ema_obj.ready and ema_obj._buf:
                # Usando la diferencia entre valor actual y promedio del buffer
                prev_approx = float(
                    np.mean(list(ema_obj._buf[-3:]) if len(ema_obj._buf) >= 3 else ema_obj._buf)
                )
                slopes.append(ema_values[self.periods.index(p)] - prev_approx)
        slope = float(np.mean(slopes)) if slopes else 0.0

        # ── Order: están ordenadas correctamente ──────────────────
        # Bull order: EMA_8 > EMA_13 > EMA_21 > EMA_34 > EMA_55
        # Bear order: EMA_8 < EMA_13 < EMA_21 < EMA_34 < EMA_55
        bull_ordered = all(ema_values[i] > ema_values[i + 1] for i in range(len(ema_values) - 1))
        bear_ordered = all(ema_values[i] < ema_values[i + 1] for i in range(len(ema_values) - 1))

        if bull_ordered:
            order = "BULL"
        elif bear_ordered:
            order = "BEAR"
        else:
            # Contar cuántas están en orden
            bull_pairs = sum(
                1 for i in range(len(ema_values) - 1) if ema_values[i] > ema_values[i + 1]
            )
            bear_pairs = sum(
                1 for i in range(len(ema_values) - 1) if ema_values[i] < ema_values[i + 1]
            )
            if bull_pairs > bear_pairs:
                order = "PARTIAL_BULL"
            elif bear_pairs > bull_pairs:
                order = "PARTIAL_BEAR"
            else:
                order = "MIXED"

        # ── Spread std: coherencia interna de la cinta ────────────
        # Diferencias entre EMAs consecutivas
        gaps = [abs(ema_values[i + 1] - ema_values[i]) for i in range(len(ema_values) - 1)]
        spread_std = float(np.std(gaps)) if len(gaps) > 1 else 0.0

        # ── Expansión / compresión ─────────────────────────────────
        if self._prev_width is not None:
            expansion = width > self._prev_width * 1.01
            compression = width < self._prev_width * 0.99
        else:
            expansion = compression = False
        self._prev_width = width

        # ── Distancia del precio/serie a la EMA central ───────────
        ema_mid = vals[self.periods[len(self.periods) // 2]]
        dist_to_mid = (
            (value - ema_mid) / abs(ema_mid) * 100 if ema_mid and abs(ema_mid) > 1e-9 else 0.0
        )

        return {f"{self.label}_ema_{p}": round(vals[p], 6) for p in self.periods} | {
            f"{self.label}_width": round(width, 6),
            f"{self.label}_width_pct": round(width_pct, 4),
            f"{self.label}_slope": round(slope, 6),
            f"{self.label}_order": order,
            f"{self.label}_spread_std": round(spread_std, 6),
            f"{self.label}_expansion": expansion,
            f"{self.label}_compression": compression,
            f"{self.label}_dist_mid": round(dist_to_mid, 4),
            f"{self.label}_bull_ordered": bull_ordered,
            f"{self.label}_bear_ordered": bear_ordered,
            f"{self.label}_ready": True,
        }

    def _empty(self, vals: dict) -> dict:
        return {f"{self.label}_ema_{p}": vals.get(p) for p in self.periods} | {
            f"{self.label}_width": 0.0,
            f"{self.label}_width_pct": 0.0,
            f"{self.label}_slope": 0.0,
            f"{self.label}_order": "WARMING_UP",
            f"{self.label}_spread_std": 0.0,
            f"{self.label}_expansion": False,
            f"{self.label}_compression": False,
            f"{self.label}_dist_mid": 0.0,
            f"{self.label}_bull_ordered": False,
            f"{self.label}_bear_ordered": False,
            f"{self.label}_ready": False,
        }

    def get_ema_values(self) -> list[float | None]:
        return [self._emas[p].value for p in self.periods]


# ─────────────────────────────────────────────
# 4. ACUMULADOR DE SHADOW DELTA
# ─────────────────────────────────────────────


class ShadowDeltaAccumulator:
    """
    Calcula el Shadow Delta acumulado con normalización y suavizado.

    El Shadow Delta crudo puede tener escala muy diferente al precio
    (en unidades de acciones equivalentes, típicamente 10k-500k).
    La normalización lo convierte a una serie comparable con el precio.

    Modos de normalización:
        "zscore":  normaliza por z-score sobre ventana rolling (default)
        "minmax":  normaliza por min-max sobre ventana rolling
        "raw":     sin normalización (para debugging)
    """

    def __init__(
        self,
        norm_window: int = 50,
        norm_mode: str = "zscore",
        smooth_period: int = 3,
    ):
        self.norm_window = norm_window
        self.norm_mode = norm_mode
        self._acc: float = 0.0  # acumulado neto
        self._acc_buf: deque = deque(maxlen=norm_window)
        self._smooth_ema: IncrementalEMA = IncrementalEMA(smooth_period)
        self._prev_iv: float | None = None

    def update(self, shadow_delta_bar: ShadowDeltaBar | None) -> tuple[float, float]:
        """
        Retorna (shadow_delta_acum_raw, shadow_delta_acum_norm).
        El valor normalizado es el que se alimenta al EMARibbon de Shadow Delta.
        """
        if shadow_delta_bar is not None:
            self._acc += shadow_delta_bar.net_shadow_delta
        # else: mantener acumulado (sin datos de opciones = sin cambio)

        self._acc_buf.append(self._acc)

        raw = self._acc

        # Normalización
        if len(self._acc_buf) >= 5:
            arr = np.array(self._acc_buf)
            if self.norm_mode == "zscore":
                mean = float(np.mean(arr))
                std = float(np.std(arr)) + 1e-9
                norm = (self._acc - mean) / std
            elif self.norm_mode == "minmax":
                lo = float(np.min(arr))
                hi = float(np.max(arr))
                norm = (self._acc - lo) / (hi - lo + 1e-9) * 2 - 1  # [-1, 1]
            else:
                norm = self._acc
        else:
            norm = 0.0

        # Suavizado final
        smooth = self._smooth_ema.update(norm)
        return raw, smooth if smooth is not None else norm


# ─────────────────────────────────────────────
# 5. DETECTOR DE SEÑALES RIBBON
# ─────────────────────────────────────────────


class RibbonSignalDetector:
    """
    Detecta los patrones de señal del EMA Delta Ribbon híbrido.

    Señales por categoría:

    MOMENTUM CONFIRMADO (ambas cintas alineadas):
        BULL_MOMENTUM_CONFIRMED:  A expanding bull + B expanding positive
        BEAR_MOMENTUM_CONFIRMED:  A expanding bear + B expanding negative

    PRE-MOVIMIENTO (B lidera a A):
        BULL_PREEMPTIVE:  B ya bullish/expanding pero A aún comprimida/mixta
        BEAR_PREEMPTIVE:  B ya bearish/expanding pero A aún comprimida/mixta

    AMAGO / TRAMPA (A expande pero B contradice):
        BULL_TRAP_SUSPECTED:  A expanding bull + B comprimida o negativa
        BEAR_TRAP_SUSPECTED:  A expanding bear + B comprimida o positiva

    COMPRESIÓN + SETUP:
        COILING_BULL:  ambas comprimidas + B con leve sesgo positivo
        COILING_BEAR:  ambas comprimidas + B con leve sesgo negativo
        COILING_NEUTRAL: ambas comprimidas sin sesgo claro

    DIVERGENCIA:
        DIVERGENCE_BULL:  A bear ordered + B bull expanding → inversión alcista inminente
        DIVERGENCE_BEAR:  A bull ordered + B bear expanding → inversión bajista inminente

    CRUCE DE CINTA (clásico con validación):
        RIBBON_CROSS_BULL:  EMAs de precio cruzan de bear a bull + B confirma
        RIBBON_CROSS_BEAR:  EMAs de precio cruzan de bull a bear + B confirma
    """

    def __init__(self):
        self._prev_a_order: str | None = None

    def detect(self, ra: dict, rb: dict, price: float) -> tuple[str, int, str]:
        """
        Analiza las dos cintas y retorna (signal, strength, interpretation).
        """
        if not ra.get("p_ready") or not rb.get("sd_ready"):
            return "WARMING_UP", 0, "Inicializando EMAs..."

        a_order = ra["p_order"]
        b_order = rb["sd_order"]
        a_exp = ra["p_expansion"]
        a_comp = ra["p_compression"]
        b_exp = rb["sd_expansion"]
        b_comp = rb["sd_compression"]
        a_bull = ra["p_bull_ordered"]
        a_bear = ra["p_bear_ordered"]
        b_bull = rb["sd_bull_ordered"]
        b_bear = rb["sd_bear_ordered"]
        a_slope = ra["p_slope"]
        b_slope = rb["sd_slope"]
        a_width = ra["p_width_pct"]
        b_width = rb["sd_width_pct"]

        # ── 1. MOMENTUM CONFIRMADO (máxima prioridad) ──────────
        if a_bull and b_bull and a_exp and b_exp:
            strength = 5 if a_slope > 0 and b_slope > 0 else 4
            return (
                "BULL_MOMENTUM_CONFIRMED",
                strength,
                "Cinta precio Y cinta Shadow Delta expandiendo alcista → momentum institucional real",
            )

        if a_bear and b_bear and a_exp and b_exp:
            strength = 5 if a_slope < 0 and b_slope < 0 else 4
            return (
                "BEAR_MOMENTUM_CONFIRMED",
                strength,
                "Cinta precio Y cinta Shadow Delta expandiendo bajista → momentum institucional real",
            )

        # ── 2. PRE-MOVIMIENTO (B lidera a A) ──────────────────
        if (b_bull or b_order == "PARTIAL_BULL") and b_exp:
            if not a_bull and (a_comp or a_order in ("MIXED", "PARTIAL_BULL")):
                return (
                    "BULL_PREEMPTIVE",
                    4,
                    "Shadow Delta ribbon ya expande alcista pero precio aún comprimido "
                    "→ dealers posicionándose ANTES que el precio",
                )

        if (b_bear or b_order == "PARTIAL_BEAR") and b_exp:
            if not a_bear and (a_comp or a_order in ("MIXED", "PARTIAL_BEAR")):
                return (
                    "BEAR_PREEMPTIVE",
                    4,
                    "Shadow Delta ribbon ya expande bajista pero precio aún comprimido "
                    "→ dealers vendiendo ANTES que el precio reaccione",
                )

        # ── 3. AMAGO / TRAMPA ─────────────────────────────────
        if a_bull and a_exp:
            if b_bear or (b_comp and b_slope < 0):
                return (
                    "BULL_TRAP_SUSPECTED",
                    4,
                    "Cinta precio expande alcista PERO Shadow Delta bajista o comprimido "
                    "→ subida sin respaldo de dealers = trampa potencial",
                )

        if a_bear and a_exp:
            if b_bull or (b_comp and b_slope > 0):
                return (
                    "BEAR_TRAP_SUSPECTED",
                    4,
                    "Cinta precio expande bajista PERO Shadow Delta alcista o comprimido "
                    "→ caída sin respaldo de dealers = trampa potencial",
                )

        # ── 4. DIVERGENCIA (señal de inversión) ───────────────
        if a_bear and (b_bull and b_exp):
            return (
                "DIVERGENCE_BULL",
                3,
                "Precio en cinta bajista pero Shadow Delta expande alcista "
                "→ acumulación institucional mientras precio baja = inversión inminente",
            )

        if a_bull and (b_bear and b_exp):
            return (
                "DIVERGENCE_BEAR",
                3,
                "Precio en cinta alcista pero Shadow Delta expande bajista "
                "→ distribución institucional mientras precio sube = inversión inminente",
            )

        # ── 5. COILING (ambas comprimidas) ────────────────────
        is_coiling = a_comp and (b_comp or b_width < 0.1)
        if is_coiling:
            if b_slope > 0:
                return (
                    "COILING_BULL",
                    2,
                    "Ambas cintas comprimidas con sesgo Shadow Delta positivo "
                    "→ acumulación silenciosa de dealers antes del movimiento",
                )
            elif b_slope < 0:
                return (
                    "COILING_BEAR",
                    2,
                    "Ambas cintas comprimidas con sesgo Shadow Delta negativo "
                    "→ distribución silenciosa de dealers antes del movimiento",
                )
            return (
                "COILING_NEUTRAL",
                1,
                "Ambas cintas comprimidas sin sesgo claro → esperar señal",
            )

        # ── 6. CRUCE DE CINTA DE PRECIO (con validación B) ───
        if self._prev_a_order is not None:
            if self._prev_a_order in ("BEAR", "PARTIAL_BEAR") and a_order in (
                "BULL",
                "PARTIAL_BULL",
            ):
                if b_bull or b_slope > 0:
                    self._prev_a_order = a_order
                    return (
                        "RIBBON_CROSS_BULL",
                        3,
                        f"Cinta precio cruza de bajista a alcista con Shadow Delta confirmando "
                        f"(slope={b_slope:.4f})",
                    )
                else:
                    self._prev_a_order = a_order
                    return (
                        "RIBBON_CROSS_BULL_UNCONFIRMED",
                        2,
                        "Cruce alcista de cinta sin confirmación de Shadow Delta → baja convicción",
                    )

            if self._prev_a_order in ("BULL", "PARTIAL_BULL") and a_order in (
                "BEAR",
                "PARTIAL_BEAR",
            ):
                if b_bear or b_slope < 0:
                    self._prev_a_order = a_order
                    return (
                        "RIBBON_CROSS_BEAR",
                        3,
                        f"Cinta precio cruza de alcista a bajista con Shadow Delta confirmando "
                        f"(slope={b_slope:.4f})",
                    )
                else:
                    self._prev_a_order = a_order
                    return (
                        "RIBBON_CROSS_BEAR_UNCONFIRMED",
                        2,
                        "Cruce bajista de cinta sin confirmación de Shadow Delta → baja convicción",
                    )

        self._prev_a_order = a_order

        # ── 7. Sin señal clara ─────────────────────────────────
        return ("NEUTRAL", 0, "Cintas sin patrón accionable")


# ─────────────────────────────────────────────
# 6. SCORER HÍBRIDO
# ─────────────────────────────────────────────


class RibbonScorer:
    """
    Calcula el score de calidad de la señal (0-100).

    F1. Estado del orden de la cinta A (precio)       0-20 pts
    F2. Expansión/compresión cinta A                   0-20 pts
    F3. Alineación cinta A vs cinta B                  0-25 pts
    F4. Shadow Delta momentum (slope de cinta B)       0-20 pts
    F5. Divergencia o convergencia de cintas           0-15 pts
    """

    def score(self, ra: dict, rb: dict, signal: str, strength: int) -> tuple[float, dict]:
        a_order = ra.get("p_order", "MIXED")
        a_exp = ra.get("p_expansion", False)
        a_comp = ra.get("p_compression", False)
        a_slope = ra.get("p_slope", 0.0)
        a_width = ra.get("p_width_pct", 0.0)
        b_slope = rb.get("sd_slope", 0.0)
        b_width = rb.get("sd_width_pct", 0.0)
        b_exp = rb.get("sd_expansion", False)
        b_bull = rb.get("sd_bull_ordered", False)
        b_bear = rb.get("sd_bear_ordered", False)

        is_bull_signal = any(x in signal for x in ("BULL", "LONG", "PREEMPTIVE_BULL"))
        is_bear_signal = any(x in signal for x in ("BEAR", "SHORT", "PREEMPTIVE_BEAR"))

        # F1: Orden de cinta A
        order_scores = {
            "BULL": 20.0,
            "PARTIAL_BULL": 12.0,
            "MIXED": 5.0,
            "PARTIAL_BEAR": 12.0,
            "BEAR": 20.0,
            "WARMING_UP": 0.0,
        }
        f1 = order_scores.get(a_order, 5.0)
        if is_bull_signal and a_order not in ("BULL", "PARTIAL_BULL"):
            f1 *= 0.5
        if is_bear_signal and a_order not in ("BEAR", "PARTIAL_BEAR"):
            f1 *= 0.5

        # F2: Expansión de cinta A
        f2 = 0.0
        if a_exp:
            f2 = min(20.0, 10.0 + a_width * 2.0)
        elif a_comp:
            f2 = 5.0  # compresión puede preceder señal
        else:
            f2 = 3.0

        # F3: Alineación A vs B
        aligned = (is_bull_signal and (b_bull or b_slope > 0)) or (
            is_bear_signal and (b_bear or b_slope < 0)
        )
        if aligned:
            f3 = min(25.0, 15.0 + b_width * 5.0)
        else:
            f3 = 0.0  # desalineación = no score de alineación

        # F4: Shadow Delta momentum (magnitud del slope de B)
        slope_abs = abs(b_slope)
        f4 = min(20.0, slope_abs * 200.0)

        # F5: Divergencia o convergencia
        f5 = 0.0
        if "DIVERGENCE" in signal:
            f5 = 15.0
        elif "TRAP" in signal:
            f5 = -10.0  # penalización por trampa
        elif "MOMENTUM_CONFIRMED" in signal:
            f5 = 15.0
        elif "PREEMPTIVE" in signal:
            f5 = 10.0

        total = max(0.0, min(100.0, f1 + f2 + f3 + f4 + f5))

        breakdown = {
            "f1_order": round(f1, 2),
            "f2_expansion": round(f2, 2),
            "f3_alignment": round(f3, 2),
            "f4_sd_momentum": round(f4, 2),
            "f5_divergence": round(f5, 2),
        }
        return round(total, 2), breakdown


# ─────────────────────────────────────────────
# 7. MOTOR PRINCIPAL EMA DELTA RIBBON HÍBRIDO
# ─────────────────────────────────────────────


class HybridEMADeltaRibbonEngine:
    """
    Motor principal: EMA Ribbon de precio + EMA Ribbon de Shadow Delta.

    Args:
        ticker:        Símbolo del proxy
        periods:       Períodos de la cinta. Default [8, 13, 21, 34, 55].
        norm_window:   Ventana de normalización del Shadow Delta. Default 50.
        norm_mode:     Modo de normalización: "zscore"|"minmax"|"raw". Default "zscore".
        smooth_sd:     Suavizado del Shadow Delta antes de la cinta. Default 3.
        min_score:     Score mínimo para emitir señal. Default 35.
    """

    def __init__(
        self,
        ticker: str,
        periods: list[int] = None,
        norm_window: int = 50,
        norm_mode: str = "zscore",
        smooth_sd: int = 3,
        min_score: float = 35.0,
    ):
        self.ticker = ticker
        self.periods = sorted(periods or [8, 13, 21, 34, 55])
        self.min_score = min_score

        # Cinta A: precio
        self._ribbon_price = EMARibbon(self.periods, label="p")

        # Shadow Delta acumulador + Cinta B
        self._sd_accum = ShadowDeltaAccumulator(norm_window, norm_mode, smooth_sd)
        self._ribbon_sd = EMARibbon(self.periods, label="sd")

        # Detector y scorer
        self._detector = RibbonSignalDetector()
        self._scorer = RibbonScorer()

        self._history: list[dict] = []

    def update(
        self,
        close: float,
        net_shadow_delta: float,
        iv_atm: float,
        net_gex: float,
        gamma_flip: float,
        sweep_count: int,
        timestamp: pd.Timestamp,
    ) -> dict:
        """Procesa una vela de 1m con su Shadow Delta del mismo minuto."""

        # ── Cinta A: precio ───────────────────────────────────
        ra = self._ribbon_price.update(close)

        # ── Shadow Delta acumulado ────────────────────────────
        class DummySD:
            def __init__(self, net):
                self.net_shadow_delta = net

        sd_raw, sd_norm = self._sd_accum.update(
            DummySD(net_shadow_delta) if net_shadow_delta else None
        )

        # ── Cinta B: Shadow Delta normalizado ─────────────────
        rb = self._ribbon_sd.update(sd_norm)

        # ── Contexto de opciones ──────────────────────────────
        sweep_cnt = sweep_count
        net_sd = net_shadow_delta

        # ── Señal ─────────────────────────────────────────────
        signal, strength, interpretation = self._detector.detect(ra, rb, close)

        # ── Score ─────────────────────────────────────────────
        score, breakdown = self._scorer.score(ra, rb, signal, strength)

        if score < self.min_score:
            signal = "NEUTRAL"
            strength = 0

        # ── Spread combinado (divergencia entre cintas) ────────
        a_vals = self._ribbon_price.get_ema_values()
        b_vals = self._ribbon_sd.get_ema_values()
        ribbon_divergence = 0.0
        if all(v is not None for v in a_vals + b_vals):
            # Dirección relativa: +1 si A y B van en la misma dirección
            a_trend = np.sign(a_vals[0] - a_vals[-1]) if a_vals[0] and a_vals[-1] else 0
            b_trend = np.sign(b_vals[0] - b_vals[-1]) if b_vals[0] and b_vals[-1] else 0
            ribbon_divergence = float(a_trend * b_trend)  # +1=alineadas, -1=divergentes

        result = {
            # Identificación
            "timestamp": timestamp,
            "ticker": self.ticker,
            "close": close,
            # Cinta A (precio) — valores individuales
            **{f"ema_p_{p}": ra.get(f"p_ema_{p}") for p in self.periods},
            "p_width": ra.get("p_width", 0.0),
            "p_width_pct": ra.get("p_width_pct", 0.0),
            "p_slope": ra.get("p_slope", 0.0),
            "p_order": ra.get("p_order", "WARMING_UP"),
            "p_expansion": ra.get("p_expansion", False),
            "p_compression": ra.get("p_compression", False),
            "p_dist_mid": ra.get("p_dist_mid", 0.0),
            # Shadow Delta + Cinta B
            "sd_raw": round(sd_raw, 0),
            "sd_norm": round(sd_norm, 4),
            "net_sd_1m": round(net_sd, 0),
            **{f"ema_sd_{p}": rb.get(f"sd_ema_{p}") for p in self.periods},
            "sd_width": rb.get("sd_width", 0.0),
            "sd_width_pct": rb.get("sd_width_pct", 0.0),
            "sd_slope": rb.get("sd_slope", 0.0),
            "sd_order": rb.get("sd_order", "WARMING_UP"),
            "sd_expansion": rb.get("sd_expansion", False),
            "sd_compression": rb.get("sd_compression", False),
            # Relación entre cintas
            "ribbon_divergence": ribbon_divergence,
            # Opciones
            "iv_atm": round(iv_atm, 4),
            "net_gex": round(net_gex, 0),
            "gamma_flip": round(gamma_flip, 4),
            "sweep_count": sweep_cnt,
            # Score y señal
            "score": score,
            "f1_order": breakdown["f1_order"],
            "f2_expansion": breakdown["f2_expansion"],
            "f3_alignment": breakdown["f3_alignment"],
            "f4_sd_momentum": breakdown["f4_sd_momentum"],
            "f5_divergence": breakdown["f5_divergence"],
            "signal": signal,
            "strength": strength,
            "interpretation": interpretation,
        }

        self._history.append(result)
        return result

    def to_dataframe(self) -> pd.DataFrame:
        if not self._history:
            return pd.DataFrame()
        df = pd.DataFrame(self._history)
        df.set_index("timestamp", inplace=True)
        return df


# ─────────────────────────────────────────────
# 8. GENERADOR DE DATOS DEMO
# ─────────────────────────────────────────────
