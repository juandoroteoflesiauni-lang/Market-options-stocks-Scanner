"""
WaveTrend Híbrido — WT Clásico de Precio + WT sobre GEX de Opciones
═════════════════════════════════════════════════════════════════════
El WaveTrend clásico (LazyBear) calcula un oscilador de momentum
basado en la distancia del precio a su media exponencial, normalizada
por la volatilidad local. Produce dos curvas: WT1 (rápida) y WT2
(suavizada de WT1), y las señales clásicas son los cruces entre ellas.

Este motor calcula TRES curvas en paralelo:

    WT1_P  = WaveTrend rápida del PRECIO (canal n1=10, n2=21)
    WT2_P  = EMA(WT1_P, 4) — línea de señal del precio
    WT1_G  = WaveTrend rápida del GEX neto de opciones
    WT2_G  = EMA(WT1_G, 4) — línea de señal del GEX

    WT_HYBRID = w_P × WT1_P + w_G × WT1_G   (ponderado por régimen)

Las señales emergen de CUATRO tipos de cruce:
    A. WT1_P × WT2_P   (cruce clásico de precio — confirmación)
    B. WT1_G × WT2_G   (cruce de flujo GEX — anticipación)
    C. WT1_P × WT1_G   (divergencia cruzada — acumulación/distribución)
    D. WT_HYBRID × cero (cruce del eje — momentum neto institucional)

Zonas de overbought/oversold:
    OB1 = +60   OB2 = +53  (zonas clásicas de sobrecompra)
    OS1 = −60   OS2 = −53  (zonas clásicas de sobreventa)

Fuentes:
    BingX WebSocket  → velas 1m OHLCV
    Massive API      → GEX neto por snapshot (Gamma × OI × 100)

Compatibilidad: pandas >= 2.0 · numpy >= 1.24 · pandas-ta
"""

import warnings
from collections import deque
from dataclasses import dataclass, field

import numpy as np
import pandas as pd

warnings.filterwarnings("ignore")


# ─────────────────────────────────────────────
# 1. ESTRUCTURAS DE DATOS
# ─────────────────────────────────────────────


@dataclass
class GEXSnapshot:
    """
    Snapshot de GEX neto de Massive API — 1 por minuto.
    GEX = Σ [ Gamma(k) × OI(k) × 100 ] para toda la cadena.
    Positivo: dealers net-long Gamma (absorben volatilidad).
    Negativo: dealers net-short Gamma (amplifican volatilidad).
    """

    timestamp: pd.Timestamp
    ticker: str
    net_gex: float  # GEX neto total de la cadena
    gex_calls: float  # GEX solo de calls
    gex_puts: float  # GEX solo de puts (generalmente negativo)
    gamma_flip: float  # Precio donde GEX = 0
    iv_atm: float  # IV ATM para régimen
    spot: float  # Precio spot del subyacente real


@dataclass
class CandleBar:
    timestamp: pd.Timestamp
    ticker: str
    open: float
    high: float
    low: float
    close: float
    volume: float
    hlc3: float = field(init=False)  # HLC/3 = precio típico

    def __post_init__(self):
        self.hlc3 = (self.high + self.low + self.close) / 3.0


# ─────────────────────────────────────────────
# 2. EMA INCREMENTAL
# ─────────────────────────────────────────────


class EMA:
    """EMA incremental con warm-up por SMA."""

    def __init__(self, period: int):
        self.period = period
        self.alpha = 2.0 / (period + 1)
        self._val: float | None = None
        self._buf: list[float] = []
        self._ready = False

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


# ─────────────────────────────────────────────
# 3. NÚCLEO WAVETREND (genérico)
# ─────────────────────────────────────────────


class WaveTrendCore:
    """
    Motor WaveTrend genérico (LazyBear) para cualquier serie numérica.

    Algoritmo original:
        ap  = hlc3                        (precio típico o serie de entrada)
        esa = EMA(ap, n1)                 (tendencia base)
        d   = EMA(|ap − esa|, n1)         (volatilidad local)
        ci  = (ap − esa) / (0.015 × d)   (índice de canal normalizado)
        wt1 = EMA(ci, n2)                 (oscilador rápido)
        wt2 = EMA(wt1, 4)                 (señal suavizada)

    Adaptación para GEX:
        En lugar de hlc3 se usa el GEX neto normalizado.
        El factor 0.015 se ajusta dinámicamente según la escala del GEX.

    Args:
        n1:      Período de la EMA base. Default 10.
        n2:      Período del oscilador. Default 21.
        sig:     Período de la línea de señal (wt2). Default 4.
        scale:   Factor de normalización del canal. Default 0.015.
                 Para GEX se recalibra automáticamente.
    """

    def __init__(
        self,
        n1: int = 10,
        n2: int = 21,
        sig: int = 4,
        scale: float = 0.015,
        adaptive_scale: bool = False,
    ):
        self.n1 = n1
        self.n2 = n2
        self.scale = scale
        self.adaptive_scale = adaptive_scale

        # EMAs del algoritmo
        self._ema_ap = EMA(n1)  # EMA del precio/serie
        self._ema_d = EMA(n1)  # EMA de la desviación absoluta
        self._ema_ci = EMA(n2)  # EMA del CI = WT1
        self._ema_wt2 = EMA(sig)  # EMA de WT1 = WT2

        # Buffer de escala adaptativa
        self._scale_buf: deque = deque(maxlen=100)

    def update(self, value: float) -> tuple[float | None, float | None]:
        """
        Procesa un nuevo valor y retorna (wt1, wt2).
        Retorna (None, None) durante el warm-up.
        """
        # ── EMA base ──────────────────────────────────────────
        esa = self._ema_ap.update(value)
        if esa is None:
            return None, None

        # ── Desviación absoluta ───────────────────────────────
        dev = abs(value - esa)
        d = self._ema_d.update(dev)
        if d is None:
            return None, None

        # ── Factor de escala ──────────────────────────────────
        if self.adaptive_scale:
            self._scale_buf.append(d)
            if len(self._scale_buf) >= 20:
                ref_d = float(np.median(self._scale_buf))
                effective_scale = max(self.scale, ref_d * 0.015) if ref_d > 0 else self.scale
            else:
                effective_scale = self.scale
        else:
            effective_scale = self.scale

        # ── Canal Index ───────────────────────────────────────
        denom = effective_scale * d
        ci = (value - esa) / denom if denom > 1e-12 else 0.0
        ci = float(np.clip(ci, -4.0, 4.0))  # evitar explosiones

        # ── WT1 y WT2 ─────────────────────────────────────────
        wt1 = self._ema_ci.update(ci)
        if wt1 is None:
            return None, None

        wt1 = float(np.clip(wt1 * 100, -150, 150))  # escalar a ±100

        wt2_raw = self._ema_wt2.update(wt1)
        wt2 = float(wt2_raw) if wt2_raw is not None else None

        return wt1, wt2

    def reset(self):
        self._ema_ap = EMA(self.n1)
        self._ema_d = EMA(self.n1)
        self._ema_ci = EMA(self.n2)
        self._ema_wt2 = EMA(4)
        self._scale_buf.clear()


# ─────────────────────────────────────────────
# 4. CLASIFICADOR DE RÉGIMEN
# ─────────────────────────────────────────────


class RegimeClassifier:
    """
    Determina el régimen de Gamma y los pesos del WaveTrend híbrido.

    Gamma+ (GEX > flip): dealers absorben → precio más predecible
                          → dar más peso a WT de precio
    Gamma- (GEX < flip): dealers amplifican → GEX más informativo
                          → dar más peso a WT de GEX
    """

    WEIGHTS = {
        "GAMMA_POS": {"price": 0.60, "gex": 0.40},
        "GAMMA_NEG": {"price": 0.35, "gex": 0.65},
        "GAMMA_FLIP": {"price": 0.45, "gex": 0.55},
        "UNKNOWN": {"price": 0.50, "gex": 0.50},
    }

    def __init__(self, flip_memory: int = 3):
        self._memory = flip_memory
        self._countdown = 0
        self._prev_sign: int | None = None

    def classify(self, gex: float, gamma_flip: float = 0.0) -> tuple[str, dict]:
        sign = 1 if gex > gamma_flip else -1

        if self._prev_sign is not None and sign != self._prev_sign:
            self._countdown = self._memory
        self._prev_sign = sign

        if self._countdown > 0:
            self._countdown -= 1
            return "GAMMA_FLIP", self.WEIGHTS["GAMMA_FLIP"]

        regime = "GAMMA_POS" if sign > 0 else "GAMMA_NEG"
        return regime, self.WEIGHTS[regime]


# ─────────────────────────────────────────────
# 5. DETECTOR DE CRUCES Y PATRONES
# ─────────────────────────────────────────────


class WaveTrendCrossDetector:
    """
    Detecta los 4 tipos de cruce del sistema WaveTrend híbrido.

    Cruce A: WT1_P cruza WT2_P  → señal clásica de precio
    Cruce B: WT1_G cruza WT2_G  → señal de flujo GEX (anticipa precio)
    Cruce C: WT1_P cruza WT1_G  → divergencia entre precio y dealers
    Cruce D: WT_HYB cruza cero  → momentum neto institucional

    Además detecta:
        - Divergencias de sobrecompra/sobreventa
        - Cruces en zona de alta convicción (OB/OS)
        - Cruces dobles (A+B simultáneos = máxima señal)
    """

    OB1 = +60.0
    OB2 = +53.0
    OS1 = -60.0
    OS2 = -53.0

    def __init__(self, sync_window: int = 2):
        self.sync_window = sync_window
        # Buffers de cruces recientes para detectar sincronía
        self._cross_a_buf: deque = deque(maxlen=sync_window + 1)
        self._cross_b_buf: deque = deque(maxlen=sync_window + 1)
        # Valores anteriores
        self._p_wt1: float | None = None
        self._p_wt2: float | None = None
        self._g_wt1: float | None = None
        self._g_wt2: float | None = None
        self._p_hyb: float | None = None

    def _cross(self, prev_a, prev_b, cur_a, cur_b) -> int:
        """Detecta cruce entre dos series. +1=bull, -1=bear, 0=nada."""
        if any(x is None for x in [prev_a, prev_b, cur_a, cur_b]):
            return 0
        if prev_a <= prev_b and cur_a > cur_b:
            return +1
        if prev_a >= prev_b and cur_a < cur_b:
            return -1
        return 0

    def _zero_cross(self, prev: float | None, cur: float) -> int:
        if prev is None:
            return 0
        if prev <= 0 < cur:
            return +1
        if prev >= 0 > cur:
            return -1
        return 0

    def _in_ob(self, v: float) -> bool:
        return v >= self.OB2

    def _in_os(self, v: float) -> bool:
        return v <= self.OS2

    def update(
        self,
        wt1_p: float | None,
        wt2_p: float | None,
        wt1_g: float | None,
        wt2_g: float | None,
        wt_hyb: float | None,
    ) -> tuple[str, int, str]:
        """
        Detecta el patrón más relevante del tick actual.
        Retorna (signal_name, strength, interpretation).
        """
        if any(x is None for x in [wt1_p, wt2_p, wt1_g, wt2_g, wt_hyb]):
            ca = cb = cc = cd = 0
        else:
            # ── Los 4 cruces ──────────────────────────────────
            ca = self._cross(self._p_wt1, self._p_wt2, wt1_p, wt2_p)  # precio WT
            cb = self._cross(self._g_wt1, self._g_wt2, wt1_g, wt2_g)  # GEX WT
            cc = self._cross(self._p_wt1, self._g_wt1, wt1_p, wt1_g)  # precio vs GEX
            cd = self._zero_cross(self._p_hyb, wt_hyb)  # híbrido vs cero

            self._cross_a_buf.append(ca)
            self._cross_b_buf.append(cb)

        # Guardar para próxima iteración
        self._p_wt1 = wt1_p
        self._p_wt2 = wt2_p
        self._g_wt1 = wt1_g
        self._g_wt2 = wt2_g
        self._p_hyb = wt_hyb

        if all(x is None for x in [wt1_p, wt2_p, wt1_g]):
            return "WARMING_UP", 0, "Período de inicialización WaveTrend"

        return self._classify(
            ca,
            cb,
            cc,
            cd,
            wt1_p,
            wt2_p,
            wt1_g,
            wt2_g,
            wt_hyb,
        )

    def _classify(
        self,
        ca: int,
        cb: int,
        cc: int,
        cd: int,
        wt1_p: float,
        wt2_p: float,
        wt1_g: float,
        wt2_g: float,
        wt_hyb: float,
    ) -> tuple[str, int, str]:

        in_ob_p = self._in_ob(wt1_p)
        in_os_p = self._in_os(wt1_p)
        in_ob_g = self._in_ob(wt1_g)
        in_os_g = self._in_os(wt1_g)

        # ── Nivel 5: Cruce doble simultáneo (A+B) ─────────────
        if ca == +1 and cb == +1:
            zone_str = " en zona OS" if in_os_p or in_os_g else ""
            strength = 5 if (in_os_p and in_os_g) else 4
            return (
                "DOUBLE_CROSS_BULL",
                strength,
                f"WT precio Y WT GEX cruzan al alza{zone_str} → máximo momentum institucional",
            )

        if ca == -1 and cb == -1:
            zone_str = " en zona OB" if in_ob_p or in_ob_g else ""
            strength = 5 if (in_ob_p and in_ob_g) else 4
            return (
                "DOUBLE_CROSS_BEAR",
                strength,
                f"WT precio Y WT GEX cruzan a la baja{zone_str} → máxima distribución confirmada",
            )

        # ── Nivel 4: Cruce B sincronizado con A reciente ───────
        recent_ca_bull = any(x == +1 for x in self._cross_a_buf)
        recent_ca_bear = any(x == -1 for x in self._cross_a_buf)

        if cb == +1 and recent_ca_bull:
            strength = 4 if in_os_g else 3
            return (
                "SYNC_CROSS_BULL",
                strength,
                f"WT GEX confirma cruce alcista de precio (≤{self.sync_window} velas) → entrada momentum",
            )
        if cb == -1 and recent_ca_bear:
            strength = 4 if in_ob_g else 3
            return (
                "SYNC_CROSS_BEAR",
                strength,
                f"WT GEX confirma cruce bajista de precio (≤{self.sync_window} velas) → salida momentum",
            )

        # ── Nivel 3: Cruce B anticipatorio (GEX antes que precio) ──
        if cb == +1 and not recent_ca_bull:
            strength = 3 if in_os_g else 2
            interp = (
                "WT GEX cruza al alza en sobreventa → anticipación de subida de precio"
                if in_os_g
                else "WT GEX cruza al alza → flujo de dealers alcista antes del precio"
            )
            return ("GEX_LEAD_BULL", strength, interp)

        if cb == -1 and not recent_ca_bear:
            strength = 3 if in_ob_g else 2
            interp = (
                "WT GEX cruza a la baja en sobrecompra → anticipación de caída de precio"
                if in_ob_g
                else "WT GEX cruza a la baja → flujo de dealers bajista antes del precio"
            )
            return ("GEX_LEAD_BEAR", strength, interp)

        # ── Nivel 3: Cruce A en zona OB/OS ────────────────────
        if ca == +1 and in_os_p:
            return (
                "PRICE_CROSS_BULL_OS",
                3,
                f"WT precio cruza al alza desde sobreventa ({wt1_p:.1f}) → rebote de alta probabilidad",
            )
        if ca == -1 and in_ob_p:
            return (
                "PRICE_CROSS_BEAR_OB",
                3,
                f"WT precio cruza a la baja desde sobrecompra ({wt1_p:.1f}) → reversión de alta probabilidad",
            )

        # ── Nivel 3: Cruce C — Divergencia precio vs GEX ──────
        if cc == +1:
            # precio cruza arriba de GEX mientras ambos estaban bajos
            if wt1_p < 0 and wt1_g < 0:
                return (
                    "DIVERGE_CROSS_BULL",
                    3,
                    "WT precio cruza sobre WT GEX en zona negativa → acumulación: precio liderando",
                )
            return (
                "DIVERGE_CROSS_BULL",
                2,
                "WT precio cruza sobre WT GEX → precio más fuerte que flujo de dealers",
            )

        if cc == -1:
            if wt1_p > 0 and wt1_g > 0:
                return (
                    "DIVERGE_CROSS_BEAR",
                    3,
                    "WT precio cae bajo WT GEX en zona positiva → distribución: precio debilitando",
                )
            return (
                "DIVERGE_CROSS_BEAR",
                2,
                "WT precio cae bajo WT GEX → precio más débil que flujo de dealers",
            )

        # ── Nivel 2: Cruce D — Híbrido vs cero ────────────────
        if cd == +1:
            strength = 3 if (wt1_p > 0 or wt1_g > 0) else 2
            return (
                "HYBRID_ZERO_CROSS_BULL",
                strength,
                f"Híbrido cruza cero al alza (p:{wt1_p:.1f} g:{wt1_g:.1f}) → momentum neto positivo",
            )
        if cd == -1:
            strength = 3 if (wt1_p < 0 or wt1_g < 0) else 2
            return (
                "HYBRID_ZERO_CROSS_BEAR",
                strength,
                f"Híbrido cruza cero a la baja (p:{wt1_p:.1f} g:{wt1_g:.1f}) → momentum neto negativo",
            )

        # ── Nivel 1: Cruce A simple ────────────────────────────
        if ca == +1:
            return (
                "PRICE_CROSS_BULL",
                1,
                f"WT precio cruza al alza ({wt1_p:.1f}) sin confirmación de GEX",
            )
        if ca == -1:
            return (
                "PRICE_CROSS_BEAR",
                1,
                f"WT precio cruza a la baja ({wt1_p:.1f}) sin confirmación de GEX",
            )

        # ── Zona extrema sin cruce (estado de presión) ─────────
        if in_os_p and in_os_g:
            return (
                "DUAL_OVERSOLD",
                2,
                f"Precio ({wt1_p:.1f}) Y GEX ({wt1_g:.1f}) en sobreventa → rebote inminente",
            )
        if in_ob_p and in_ob_g:
            return (
                "DUAL_OVERBOUGHT",
                2,
                f"Precio ({wt1_p:.1f}) Y GEX ({wt1_g:.1f}) en sobrecompra → techo inminente",
            )

        return ("NEUTRAL", 0, "Sin cruce ni zona extrema activa")


# ─────────────────────────────────────────────
# 6. MOTOR WAVETREND HÍBRIDO
# ─────────────────────────────────────────────


class HybridWaveTrendEngine:
    """
    Motor principal: WT clásico sobre precio + WT sobre GEX + curva híbrida.

    Produce por cada tick:
        wt1_p, wt2_p   : WaveTrend de precio (clásico LazyBear)
        wt1_g, wt2_g   : WaveTrend de GEX neto de opciones
        wt_hybrid      : Promedio ponderado por régimen de Gamma
        spread         : wt1_p − wt1_g (desacoplamiento institucional)

    Args:
        ticker:        Símbolo del proxy
        n1, n2, sig:   Períodos del WT clásico. Default 10/21/4.
        gex_smooth:    Suavizado del GEX crudo antes del WT. Default 3.
        flip_memory:   Velas de memoria del Gamma Flip. Default 3.
    """

    def __init__(
        self,
        ticker: str,
        n1: int = 10,
        n2: int = 21,
        sig: int = 4,
        gex_smooth: int = 3,
        flip_memory: int = 3,
        sync_window: int = 2,
    ):
        self.ticker = ticker

        # WT de precio (escala clásica 0.015)
        self._wt_price = WaveTrendCore(n1, n2, sig, scale=0.015, adaptive_scale=False)

        # WT de GEX (escala adaptativa por ser valores en millones)
        self._wt_gex = WaveTrendCore(n1, n2, sig, scale=0.015, adaptive_scale=True)

        # Suavizado del GEX crudo (3 velas)
        self._gex_buf: deque = deque(maxlen=gex_smooth)
        self._gex_smooth = gex_smooth

        # Régimen y pesos
        self._regime = RegimeClassifier(flip_memory)

        # Detector de cruces
        self._cross = WaveTrendCrossDetector(sync_window)

        self._history: list[dict] = []

    def _smooth_gex(self, raw: float) -> float:
        self._gex_buf.append(raw)
        return float(np.mean(self._gex_buf))

    def update(
        self,
        candle: CandleBar,
        gex_snap: GEXSnapshot | None = None,
    ) -> dict:

        # ── WT de precio ──────────────────────────────────────
        wt1_p, wt2_p = self._wt_price.update(candle.hlc3)

        # ── WT de GEX ─────────────────────────────────────────
        if gex_snap is not None:
            gex_raw = gex_snap.net_gex
            gamma_flip = gex_snap.gamma_flip
            iv_atm = gex_snap.iv_atm
            gex_calls = gex_snap.gex_calls
            gex_puts = gex_snap.gex_puts
        else:
            gex_raw = gamma_flip = iv_atm = 0.0
            gex_calls = gex_puts = 0.0

        gex_smooth = self._smooth_gex(gex_raw)
        wt1_g, wt2_g = self._wt_gex.update(gex_smooth)

        # ── Régimen y pesos ───────────────────────────────────
        regime, weights = self._regime.classify(gex_raw, gamma_flip)

        # ── Híbrido ponderado ─────────────────────────────────
        if wt1_p is not None and wt1_g is not None:
            wt_hybrid = weights["price"] * wt1_p + weights["gex"] * wt1_g
            spread = wt1_p - wt1_g  # positivo = precio lidera, negativo = GEX lidera
        else:
            wt_hybrid = None
            spread = None

        # ── Zona del híbrido ──────────────────────────────────
        if wt_hybrid is not None:
            if wt_hybrid >= 60:
                zone_h = "OVERBOUGHT"
            elif wt_hybrid <= -60:
                zone_h = "OVERSOLD"
            elif wt_hybrid > 0:
                zone_h = "BULLISH"
            elif wt_hybrid < 0:
                zone_h = "BEARISH"
            else:
                zone_h = "NEUTRAL"
        else:
            zone_h = "WARMING_UP"

        # ── Cruces y señal ────────────────────────────────────
        signal, strength, interpretation = self._cross.update(wt1_p, wt2_p, wt1_g, wt2_g, wt_hybrid)

        result = {
            # Identificación
            "timestamp": candle.timestamp,
            "ticker": self.ticker,
            "close": candle.close,
            "hlc3": candle.hlc3,
            # WT de precio (clásico)
            "wt1_p": round(wt1_p, 3) if wt1_p is not None else np.nan,
            "wt2_p": round(wt2_p, 3) if wt2_p is not None else np.nan,
            # WT de GEX (institucional)
            "wt1_g": round(wt1_g, 3) if wt1_g is not None else np.nan,
            "wt2_g": round(wt2_g, 3) if wt2_g is not None else np.nan,
            # Híbrido
            "wt_hybrid": round(wt_hybrid, 3) if wt_hybrid is not None else np.nan,
            "spread": round(spread, 3) if spread is not None else np.nan,
            "zone_hybrid": zone_h,
            "w_price": weights["price"],
            "w_gex": weights["gex"],
            # Opciones
            "gex_raw": round(gex_raw, 0),
            "gex_smooth": round(gex_smooth, 0),
            "gex_calls": round(gex_calls, 0),
            "gex_puts": round(gex_puts, 0),
            "gamma_flip": round(gamma_flip, 2),
            "iv_atm": round(iv_atm, 4),
            # Régimen
            "regime": regime,
            # Señal
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
        return df.dropna(subset=["wt1_p", "wt1_g"])


# ─────────────────────────────────────────────
# 7. GENERADOR DE DATOS DEMO
# ─────────────────────────────────────────────


def generate_demo(
    ticker: str = "AAPL",
    n: int = 390,
    base: float = 192.50,
    seed: int = 42,
) -> tuple[list[CandleBar], list[GEXSnapshot]]:
    """
    5 fases para exhibir los 5 tipos de cruce principales:
        Fase 1: ambos WT suben → DOUBLE_CROSS_BULL
        Fase 2: GEX lidera la baja (anticipa precio) → GEX_LEAD_BEAR
        Fase 3: precio baja, GEX sube → DIVERGE_CROSS_BULL (acumulación)
        Fase 4: ambos WT en OS → DUAL_OVERSOLD → rebote
        Fase 5: GEX lidera la suba → GEX_LEAD_BULL
    """
    rng = np.random.default_rng(seed)
    start = pd.Timestamp("2025-01-15 09:30:00", tz="America/New_York")
    tss = pd.date_range(start, periods=n, freq="1min")

    # (bars, p_trend, gex_trend, gex_base, gamma_flip_offset, noise_p, noise_g)
    phases = [
        (77, 0.00060, 0.0010, 1.5e6, +2.0, 0.0006, 0.15),  # ambos suben
        (78, 0.00020, -0.0012, -0.8e6, +1.0, 0.0005, 0.15),  # GEX baja primero
        (78, -0.00040, 0.0008, -1.2e6, -1.0, 0.0008, 0.12),  # div: precio baja, GEX sube
        (78, -0.00010, -0.0005, -1.8e6, -2.0, 0.0004, 0.10),  # dual OS
        (79, 0.00050, 0.0009, 0.6e6, 0.0, 0.0006, 0.13),  # GEX lidera suba
    ]

    candles, snaps = [], []
    price = base
    gex_val = 1_000_000.0
    idx = 0

    for n_b, p_tr, g_tr, g_base, flip_off, p_n, g_n in phases:
        gex_val = g_base
        for _ in range(n_b):
            if idx >= n:
                break
            ts = tss[idx]

            # Precio
            price *= 1 + p_tr + rng.normal(0, p_n)
            sp = price * rng.uniform(0.0005, 0.002)
            candles.append(
                CandleBar(
                    timestamp=ts,
                    ticker=ticker,
                    open=price * (1 - rng.uniform(0, 0.0002)),
                    high=price + sp * rng.uniform(0.2, 1.0),
                    low=price - sp * rng.uniform(0.2, 1.0),
                    close=price,
                    volume=float(rng.integers(60_000, 450_000)),
                )
            )

            # GEX
            gex_val *= 1 + g_tr + rng.normal(0, g_n)
            gamma_flip = price + flip_off
            gex_calls = max(0, gex_val * rng.uniform(0.5, 0.8))
            gex_puts = gex_val - gex_calls

            snaps.append(
                GEXSnapshot(
                    timestamp=ts,
                    ticker=ticker,
                    net_gex=float(gex_val),
                    gex_calls=float(gex_calls),
                    gex_puts=float(gex_puts),
                    gamma_flip=float(gamma_flip),
                    iv_atm=float(rng.uniform(0.12, 0.35)),
                    spot=price,
                )
            )
            idx += 1

    return candles, snaps


# ─────────────────────────────────────────────
# 8. PIPELINE + REPORTE
# ─────────────────────────────────────────────


def run_hybrid_wavetrend(
    ticker: str = "AAPL",
    n: int = 390,
    verbose: bool = True,
) -> pd.DataFrame:

    print(f"\n{'═'*68}")
    print(f"  WAVETREND HÍBRIDO  |  {ticker}  |  {n} velas 1m")
    print(f"{'═'*68}")

    candles, snaps = generate_demo(ticker, n)
    engine = HybridWaveTrendEngine(ticker=ticker)

    for c, s in zip(candles, snaps, strict=False):
        engine.update(c, s)

    df = engine.to_dataframe()

    if verbose:
        _print_report(df, ticker)

    return df


def _print_report(df: pd.DataFrame, ticker: str):
    last = df.iloc[-1]
    print(f"\n── Estado actual {ticker} ──────────────────────────────")
    print(f"  Precio             : ${last['close']:.2f}")
    print(f"  WT1 precio         : {last['wt1_p']:+.2f}")
    print(f"  WT2 precio         : {last['wt2_p']:+.2f}")
    print(f"  WT1 GEX            : {last['wt1_g']:+.2f}")
    print(f"  WT2 GEX            : {last['wt2_g']:+.2f}")
    print(f"  WT Híbrido         : {last['wt_hybrid']:+.2f}  [{last['zone_hybrid']}]")
    print(f"  Spread (P−G)       : {last['spread']:+.2f}  ← desacoplamiento")
    print(f"  Pesos              : precio={last['w_price']:.2f} / GEX={last['w_gex']:.2f}")
    print(f"  Régimen            : {last['regime']}")
    print(f"  GEX neto           : {last['gex_raw']:+,.0f}")
    print(f"  Gamma Flip         : ${last['gamma_flip']:.2f}")
    print(f"  IV ATM             : {last['iv_atm']:.2%}")
    print("  ── Señal ──────────────────────────────────────────")
    print(f"  Señal              : {last['signal']}  (fuerza {last['strength']})")
    print(f"  Interpretación     : {last['interpretation']}")

    corr = df["wt1_p"].corr(df["wt1_g"])
    print(f"\n── Correlación WT precio / WT GEX : {corr:.4f}")
    print("   (< 0.5 = alta independencia de señales)")

    print("\n── Distribución de regímenes ──")
    print(df["regime"].value_counts().to_string())

    print("\n── Distribución de zonas híbrido ──")
    print(df["zone_hybrid"].value_counts().to_string())

    print("\n── Señales por tipo y fuerza ──")
    sigs = df[df["strength"] > 0].groupby(["signal", "strength"]).size()
    print(sigs.to_string())

    # Señales de alta prioridad
    top = df[df["strength"] >= 3]
    print(f"\n── Señales fuerza ≥ 3 : {len(top)} ──")
    if not top.empty:
        cols = ["close", "wt1_p", "wt1_g", "wt_hybrid", "spread", "regime", "signal", "strength"]
        print(top[cols].tail(10).to_string())

    # Señales de anticipación GEX (las más valiosas para scalping)
    gex_leads = df[df["signal"].isin(["GEX_LEAD_BULL", "GEX_LEAD_BEAR"])]
    print(f"\n── Señales GEX_LEAD (anticipa precio): {len(gex_leads)} ──")
    if not gex_leads.empty:
        print(
            gex_leads[
                ["close", "wt1_p", "wt1_g", "spread", "regime", "signal", "interpretation"]
            ].to_string()
        )

    # Estadísticas del spread
    print("\n── Estadísticas del spread (WT_P − WT_G) ──")
    print(f"  Spread promedio    : {df['spread'].mean():+.2f}")
    print(f"  Spread máximo      : {df['spread'].max():+.2f}")
    print(f"  Spread mínimo      : {df['spread'].min():+.2f}")
    print(f"  Spread std         : {df['spread'].std():.2f}")
    extremo = df[df["spread"].abs() > df["spread"].std() * 2]
    print(f"  Spreads extremos   : {len(extremo)} velas (>2σ)")

    print(f"\n{'═'*68}")


# ─────────────────────────────────────────────
# 9. INTEGRACIÓN PRODUCCIÓN
# ─────────────────────────────────────────────


class HybridWaveTrendLive:
    """
    Wrapper listo para BingX WebSocket + Massive API.

    Uso:
        engine = HybridWaveTrendLive("AAPL")

        def on_1m_candle(raw_bingx, raw_massive):
            candle = HybridWaveTrendLive.parse_bingx(raw_bingx)
            snap   = HybridWaveTrendLive.parse_massive("AAPL", raw_massive, spot)
            result = engine.core.update(candle, snap)
            engine.on_signal(result)
    """

    PRIORITY = {
        "DOUBLE_CROSS_BULL": 5,
        "DOUBLE_CROSS_BEAR": 5,
        "SYNC_CROSS_BULL": 4,
        "SYNC_CROSS_BEAR": 4,
        "GEX_LEAD_BULL": 4,
        "GEX_LEAD_BEAR": 4,
        "PRICE_CROSS_BULL_OS": 3,
        "PRICE_CROSS_BEAR_OB": 3,
        "DIVERGE_CROSS_BULL": 3,
        "DIVERGE_CROSS_BEAR": 3,
        "DUAL_OVERSOLD": 3,
        "DUAL_OVERBOUGHT": 3,
        "HYBRID_ZERO_CROSS_BULL": 2,
        "HYBRID_ZERO_CROSS_BEAR": 2,
        "PRICE_CROSS_BULL": 1,
        "PRICE_CROSS_BEAR": 1,
        "NEUTRAL": 0,
        "WARMING_UP": 0,
    }

    def __init__(self, ticker: str, **kwargs):
        self.ticker = ticker
        self.core = HybridWaveTrendEngine(ticker=ticker, **kwargs)

    @staticmethod
    def parse_bingx(raw: dict) -> CandleBar:
        return CandleBar(
            timestamp=pd.Timestamp(int(raw["T"]), unit="ms", tz="UTC"),
            ticker=raw.get("s", "").replace("-USDT", ""),
            open=float(raw["o"]),
            high=float(raw["h"]),
            low=float(raw["l"]),
            close=float(raw["c"]),
            volume=float(raw["v"]),
        )

    @staticmethod
    def parse_massive(ticker: str, raw: dict, spot: float) -> GEXSnapshot:
        """
        Formato Massive API para GEX snapshot:
        {
          "netGex":    1500000,
          "gexCalls":  2200000,
          "gexPuts":  -700000,
          "gammaFlip": 192.00,
          "ivAtm":     0.22
        }
        """
        return GEXSnapshot(
            timestamp=pd.Timestamp.now(tz="UTC"),
            ticker=ticker,
            net_gex=float(raw.get("netGex", 0)),
            gex_calls=float(raw.get("gexCalls", 0)),
            gex_puts=float(raw.get("gexPuts", 0)),
            gamma_flip=float(raw.get("gammaFlip", spot)),
            iv_atm=float(raw.get("ivAtm", 0.20)),
            spot=spot,
        )

    def on_signal(self, result: dict):
        p = self.PRIORITY.get(result["signal"], 0)
        if p >= 3:
            print(
                f"[{result['timestamp']}] {self.ticker:5s} | "
                f"P{p} {result['signal']:24s} | "
                f"${result['close']:.2f} | "
                f"WTP={result['wt1_p']:+.1f} "
                f"WTG={result['wt1_g']:+.1f} "
                f"HYB={result['wt_hybrid']:+.1f} | "
                f"{result['regime']:12s} | "
                f"{result['interpretation'][:45]}"
            )


# ─────────────────────────────────────────────
# 10. EJECUCIÓN DEMO
# ─────────────────────────────────────────────

if __name__ == "__main__":
    TICKERS = ["AAPL", "MSFT", "PLTR", "TSLA", "GOOGL"]

    for ticker in TICKERS:
        df = run_hybrid_wavetrend(ticker=ticker, n=390, verbose=True)
        df.to_csv(f"/tmp/hybrid_wavetrend_{ticker.lower()}.csv")

    print("\n✓ WaveTrend Híbrido completado para los 5 proxies BingX.")
