"""
Motor de Divergencias Híbrido — Precio × Delta-RSI × NDDE
══════════════════════════════════════════════════════════
Detecta divergencias en TRES pares simultáneos y produce
un score de fuerza multidimensional para scalping 1m.

Los tres pares analizados:

    Par A: Precio vs Delta-RSI
           Divergencia clásica potenciada: el RSI del flujo
           institucional es mucho más limpio que el RSI de precio.
           Una divergencia aquí significa que los dealers están
           posicionándose en dirección contraria al precio.

    Par B: Precio vs NDDE
           La más valiosa. NDDE = exposición neta de dealers.
           Si el precio hace nuevos máximos pero el NDDE baja,
           los dealers están reduciendo cobertura alcista →
           el soporte institucional del movimiento se está yendo.

    Par C: Delta-RSI vs NDDE
           Desacoplamiento interno de derivados. Cuando el flujo
           de opciones (Delta-RSI) y la exposición acumulada
           (NDDE) divergen entre sí, hay un cambio de régimen
           institucional en curso.

Sistema de scoring multidimensional (0 a 100):

    Score = Σ factores ponderados:
        F1. Magnitud del precio en el pivot (10-25 pts)
        F2. Magnitud de la divergencia del indicador (10-25 pts)
        F3. Zona extrema de overbought/oversold (0-20 pts)
        F4. Número de pares confirmando la misma dirección (0-20 pts)
        F5. Velocidad de formación del pivot (0-10 pts)
        F6. Confirmación por régimen de Gamma (0-15 pts)
        F7. Premium de opciones en el momento del pivot (0-10 pts)

    Score < 30: DÉBIL — ignorar
    30-50:      MEDIA — monitorear
    50-70:      FUERTE — señal accionable
    70-85:      MUY_FUERTE — alta convicción
    > 85:       EXTREMA — entrada de máxima prioridad

Fuentes:
    BingX WebSocket → velas 1m OHLCV
    Massive API     → Delta-RSI (flujo de opciones) + NDDE por snapshot
"""

import warnings
from collections import deque
from dataclasses import dataclass
from enum import Enum

import numpy as np
import pandas as pd

warnings.filterwarnings("ignore")


# ─────────────────────────────────────────────
# 1. ENUMS Y CONSTANTES
# ─────────────────────────────────────────────


class DivType(Enum):
    REGULAR_BULL = (
        "REGULAR_BULL"  # precio min más bajo, indicador min más alto  → reversión alcista
    )
    REGULAR_BEAR = (
        "REGULAR_BEAR"  # precio max más alto, indicador max más bajo  → reversión bajista
    )
    HIDDEN_BULL = (
        "HIDDEN_BULL"  # precio min más alto, indicador min más bajo  → continuación alcista
    )
    HIDDEN_BEAR = (
        "HIDDEN_BEAR"  # precio max más bajo, indicador max más alto  → continuación bajista
    )


class DivStrength(Enum):
    WEAK = "DÉBIL"
    MEDIUM = "MEDIA"
    STRONG = "FUERTE"
    VSTRONG = "MUY_FUERTE"
    EXTREME = "EXTREMA"

    @classmethod
    def from_score(cls, score: float) -> "DivStrength":
        if score >= 85:
            return cls.EXTREME
        if score >= 70:
            return cls.VSTRONG
        if score >= 50:
            return cls.STRONG
        if score >= 30:
            return cls.MEDIUM
        return cls.WEAK


# ─────────────────────────────────────────────
# 2. ESTRUCTURAS DE DATOS
# ─────────────────────────────────────────────


@dataclass
class Pivot:
    """Un pivot (máximo o mínimo local) en una serie."""

    timestamp: pd.Timestamp
    index: int
    value: float
    is_high: bool  # True = pivot high, False = pivot low
    series_name: str  # "price" | "delta_rsi" | "ndde"


@dataclass
class Divergence:
    """Una divergencia detectada entre dos pivots consecutivos."""

    div_type: DivType
    pair: str  # "A_PRICE_DRSI" | "B_PRICE_NDDE" | "C_DRSI_NDDE"
    direction: str  # "BULL" | "BEAR"

    # Pivots del par primario (precio o serie A)
    ts_curr: pd.Timestamp
    ts_prev: pd.Timestamp
    val_a_curr: float
    val_a_prev: float

    # Pivots del par secundario (indicador o serie B)
    val_b_curr: float
    val_b_prev: float

    # Scoring
    score: float  # 0 - 100
    strength: DivStrength
    score_breakdown: dict  # desglose de factores

    # Contexto
    regime: str
    iv_atm: float
    net_gex: float
    net_premium: float
    actionable: bool  # score >= 50

    def to_dict(self) -> dict:
        return {
            "div_type": self.div_type.value,
            "pair": self.pair,
            "direction": self.direction,
            "ts_curr": self.ts_curr,
            "ts_prev": self.ts_prev,
            "val_a_curr": round(self.val_a_curr, 4),
            "val_a_prev": round(self.val_a_prev, 4),
            "val_b_curr": round(self.val_b_curr, 4),
            "val_b_prev": round(self.val_b_prev, 4),
            "score": round(self.score, 2),
            "strength": self.strength.value,
            "regime": self.regime,
            "iv_atm": round(self.iv_atm, 4),
            "net_gex": round(self.net_gex, 0),
            "net_premium": round(self.net_premium, 0),
            "actionable": self.actionable,
            **{f"score_{k}": round(v, 2) for k, v in self.score_breakdown.items()},
        }


@dataclass
class TickInput:
    """Input unificado de los 3 motores para el combiner de divergencias."""

    timestamp: pd.Timestamp
    ticker: str
    # Precio
    close: float
    high: float
    low: float
    # Delta-RSI (del motor Delta-RSI)
    delta_rsi: float  # valor 0-100
    rsi_flow: float  # RSI del flujo de opciones
    hist_flow: float  # histograma del Delta-RSI
    # NDDE (del motor Shadow MACD)
    ndde: float  # Net Dealer Delta Exposure
    ndde_smooth: float  # NDDE suavizado
    macd_ndde: float  # MACD sobre NDDE
    # Contexto de opciones
    net_gex: float
    net_premium: float
    iv_atm: float
    regime: str  # "GAMMA_POS" | "GAMMA_NEG" | "GAMMA_FLIP"
    sweep_count: int


# ─────────────────────────────────────────────
# 3. DETECTOR DE PIVOTS
# ─────────────────────────────────────────────


class PivotDetector:
    """
    Detecta pivots (máximos y mínimos locales) en tiempo real
    usando una ventana deslizante con confirmación bilateral.

    Un pivot se confirma cuando los `window` puntos a cada lado
    son menores (para high) o mayores (para low) que el centro.

    En scalping 1m se usa window=3 o window=5 según el ruido.
    """

    def __init__(self, window: int = 5, series_name: str = "price"):
        self.window = window
        self.series_name = series_name
        self._buf: deque = deque(maxlen=2 * window + 1)
        self._ts_buf: deque = deque(maxlen=2 * window + 1)
        self._idx: int = 0
        self._confirmed_highs: list[Pivot] = []
        self._confirmed_lows: list[Pivot] = []

    def update(self, ts: pd.Timestamp, value: float) -> tuple[Pivot | None, Pivot | None]:
        """
        Procesa un nuevo valor. Retorna (high_pivot, low_pivot) si se confirma
        alguno (o None si no hay confirmación en este tick).

        Nota: la confirmación llega con `window` ticks de retraso.
        Para scalping en 1m con window=5 → retraso máximo de 5 minutos.
        """
        self._buf.append(value)
        self._ts_buf.append(ts)
        self._idx += 1

        if len(self._buf) < 2 * self.window + 1:
            return None, None

        # El candidato es el centro de la ventana
        center_pos = self.window
        center_val = self._buf[center_pos]
        center_ts = self._ts_buf[center_pos]
        center_idx = self._idx - self.window - 1

        window_vals = list(self._buf)

        new_high = None
        new_low = None

        # ── Pivot High ─────────────────────────────────────────
        left = window_vals[:center_pos]
        right = window_vals[center_pos + 1 :]
        if (
            center_val == max(window_vals)
            and all(center_val >= v for v in left)
            and all(center_val >= v for v in right)
        ):
            new_high = Pivot(
                timestamp=center_ts,
                index=center_idx,
                value=center_val,
                is_high=True,
                series_name=self.series_name,
            )
            self._confirmed_highs.append(new_high)

        # ── Pivot Low ──────────────────────────────────────────
        if (
            center_val == min(window_vals)
            and all(center_val <= v for v in left)
            and all(center_val <= v for v in right)
        ):
            new_low = Pivot(
                timestamp=center_ts,
                index=center_idx,
                value=center_val,
                is_high=False,
                series_name=self.series_name,
            )
            self._confirmed_lows.append(new_low)

        return new_high, new_low

    def last_n_highs(self, n: int = 3) -> list[Pivot]:
        return self._confirmed_highs[-n:]

    def last_n_lows(self, n: int = 3) -> list[Pivot]:
        return self._confirmed_lows[-n:]


# ─────────────────────────────────────────────
# 4. SCORER MULTIDIMENSIONAL
# ─────────────────────────────────────────────


class DivergenceScorer:
    """
    Calcula el score multidimensional de una divergencia (0-100).

    Los 7 factores y sus rangos:
        F1. Magnitud del pivot de precio     0 - 25 pts
        F2. Magnitud de divergencia indic.   0 - 25 pts
        F3. Zona OB/OS del indicador         0 - 20 pts
        F4. Convergencia de pares            0 - 20 pts  (cuántos pares coinciden)
        F5. Velocidad de formación pivot     0 - 10 pts
        F6. Régimen de Gamma alineado        0 - 15 pts
        F7. Premium de opciones              0 - 10 pts
    """

    # Referencias para normalización
    PRICE_REF_CHANGE_PCT = 0.50  # 0.5% de cambio de precio = score F1 máximo
    INDIC_REF_RSI_CHANGE = 20.0  # 20 puntos de RSI = score F2 máximo para RSI
    INDIC_REF_NDDE_CHANGE = 2e6  # 2M de cambio en NDDE = score F2 máximo
    PREMIUM_REF = 200_000  # $200k de premium = score F7 máximo
    OB_LEVEL = 70.0
    OS_LEVEL = 30.0

    def score(
        self,
        div_type: DivType,
        pair: str,
        val_a_curr: float,
        val_a_prev: float,
        val_b_curr: float,
        val_b_prev: float,
        n_confirming_pairs: int,  # cuántos otros pares confirman la misma dirección
        bars_between_pivots: int,  # velas entre pivot anterior y actual
        regime: str,
        iv_atm: float,
        net_gex: float,
        net_premium: float,
    ) -> tuple[float, dict]:
        """
        Calcula el score total y su desglose.
        Retorna (score_total, breakdown_dict).
        """
        is_bull = "BULL" in div_type.value

        # ── F1: Magnitud del cambio de precio / serie A ────────
        a_change_pct = abs(val_a_curr - val_a_prev) / max(abs(val_a_prev), 1e-9) * 100
        f1 = min(25.0, (a_change_pct / self.PRICE_REF_CHANGE_PCT) * 25.0)

        # ── F2: Magnitud de la divergencia del indicador ───────
        b_change = abs(val_b_curr - val_b_prev)
        if "DRSI" in pair or "RSI" in pair:
            ref = self.INDIC_REF_RSI_CHANGE
        else:
            ref = self.INDIC_REF_NDDE_CHANGE
        f2 = min(25.0, (b_change / ref) * 25.0)

        # ── F3: Zona OB/OS del indicador ──────────────────────
        if "DRSI" in pair or "RSI" in pair:
            if is_bull and val_b_curr <= self.OS_LEVEL:
                f3 = 20.0 * (1 - val_b_curr / self.OS_LEVEL)
            elif not is_bull and val_b_curr >= self.OB_LEVEL:
                f3 = 20.0 * ((val_b_curr - self.OB_LEVEL) / (100 - self.OB_LEVEL))
            else:
                f3 = 0.0
        else:
            # Para NDDE: zona extrema = abs(NDDE) > 1M
            ndde_extreme = abs(val_b_curr) / 1_000_000
            f3 = min(20.0, ndde_extreme * 10.0)

        # ── F4: Convergencia de pares ──────────────────────────
        # 0 pares extra = 0, 1 par extra = 10, 2 pares extra = 20
        f4 = min(20.0, n_confirming_pairs * 10.0)

        # ── F5: Velocidad de formación ─────────────────────────
        # Divergencia rápida (5-15 barras) vale más que una lenta (>40 barras)
        if bars_between_pivots <= 15:
            f5 = 10.0
        elif bars_between_pivots <= 30:
            f5 = 7.0
        elif bars_between_pivots <= 50:
            f5 = 4.0
        else:
            f5 = 1.0

        # ── F6: Régimen de Gamma alineado ─────────────────────
        f6 = 0.0
        if is_bull:
            if regime == "GAMMA_NEG" and net_gex < 0:
                f6 = 15.0  # Gamma neg + NDDE neg = dealers comprando masivo
            elif regime == "GAMMA_POS":
                f6 = 8.0  # Gamma pos = rebote más probable
        else:
            if regime == "GAMMA_NEG" and net_gex > 0:
                f6 = 15.0  # Gamma neg + NDDE pos = dealers vendiendo masivo
            elif regime == "GAMMA_POS":
                f6 = 8.0

        # Bonus por IV baja (señal más limpia en baja volatilidad)
        if iv_atm < 0.18:
            f6 = min(15.0, f6 + 3.0)

        # ── F7: Premium de opciones ───────────────────────────
        premium_abs = abs(net_premium)
        f7 = min(10.0, (premium_abs / self.PREMIUM_REF) * 10.0)
        # Premium en la dirección correcta vale doble
        if (is_bull and net_premium > 0) or (not is_bull and net_premium < 0):
            f7 = min(10.0, f7 * 1.5)

        total = f1 + f2 + f3 + f4 + f5 + f6 + f7

        breakdown = {
            "f1_price_magnitude": f1,
            "f2_indic_divergence": f2,
            "f3_ob_os_zone": f3,
            "f4_pair_convergence": f4,
            "f5_pivot_speed": f5,
            "f6_gamma_regime": f6,
            "f7_premium": f7,
        }

        return min(100.0, total), breakdown


# ─────────────────────────────────────────────
# 5. MOTOR DE DIVERGENCIAS HÍBRIDO
# ─────────────────────────────────────────────


class HybridDivergenceEngine:
    """
    Motor principal de divergencias híbridas.

    Opera sobre tres series simultáneas:
        - Precio (close o HLC3)
        - Delta-RSI (RSI del flujo de opciones)
        - NDDE (Net Dealer Delta Exposure)

    Detecta divergencias en los 3 pares en tiempo real, las
    puntúa con el sistema de 7 factores, y determina si se
    confirman mutuamente (divergencia triple = score máximo).

    Args:
        ticker:          Símbolo del proxy
        pivot_window:    Velas a cada lado para confirmar pivot. Default 5.
        min_score:       Score mínimo para emitir una señal. Default 30.
        lookback:        Pivots anteriores a considerar. Default 3.
    """

    def __init__(
        self,
        ticker: str,
        pivot_window: int = 5,
        min_score: float = 30.0,
        lookback: int = 3,
    ):
        self.ticker = ticker
        self.min_score = min_score
        self.lookback = lookback

        # Detectores de pivots por serie
        self._piv_price = PivotDetector(pivot_window, "price")
        self._piv_drsi = PivotDetector(pivot_window, "delta_rsi")
        self._piv_ndde = PivotDetector(pivot_window, "ndde")

        # Scorer
        self._scorer = DivergenceScorer()

        # Historia de divergencias detectadas
        self._divergences: list[Divergence] = []

        # Buffer del contexto actual (para scoring F6/F7)
        self._ctx_buf: deque = deque(maxlen=pivot_window + 1)

        self._tick_count = 0

    # ── Actualización de pivots ────────────────────────────────
    def update(self, tick: TickInput) -> list[Divergence]:
        """
        Procesa un tick. Retorna lista de divergencias nuevas detectadas
        (puede ser vacía si no hay nueva confirmación de pivot).
        """
        self._tick_count += 1
        self._ctx_buf.append(tick)

        # Actualizar los 3 detectores de pivots
        ph_p, pl_p = self._piv_price.update(tick.timestamp, tick.close)
        ph_d, pl_d = self._piv_drsi.update(tick.timestamp, tick.delta_rsi)
        ph_n, pl_n = self._piv_ndde.update(tick.timestamp, tick.ndde_smooth)

        new_divs = []

        # Si se confirmó algún pivot de precio, revisar divergencias
        if ph_p is not None:  # nuevo pivot high de precio confirmado
            new_divs.extend(self._scan_highs(tick))
        if pl_p is not None:  # nuevo pivot low de precio confirmado
            new_divs.extend(self._scan_lows(tick))

        # Filtrar por score mínimo
        new_divs = [d for d in new_divs if d.score >= self.min_score]

        self._divergences.extend(new_divs)
        return new_divs

    # ── Escaneo de máximos (divergencias bajistas) ─────────────
    def _scan_highs(self, tick: TickInput) -> list[Divergence]:
        """
        Cuando se confirma un nuevo pivot high de precio, busca:
        - Pivot high previo en precio
        - Comparar con el valor de Delta-RSI y NDDE en esos pivots
        - Detectar REGULAR_BEAR o HIDDEN_BEAR
        """
        divs = []
        price_highs = self._piv_price.last_n_highs(self.lookback + 1)
        if len(price_highs) < 2:
            return divs

        curr_ph = price_highs[-1]
        prev_ph = price_highs[-2]

        # Obtener valores de Delta-RSI y NDDE en los timestamps de los pivots
        drsi_curr = self._interp_series_at(
            self._piv_drsi._confirmed_highs, curr_ph.timestamp, is_high=True
        )
        drsi_prev = self._interp_series_at(
            self._piv_drsi._confirmed_highs, prev_ph.timestamp, is_high=True
        )
        ndde_curr = self._interp_series_at(
            self._piv_ndde._confirmed_highs, curr_ph.timestamp, is_high=True
        )
        ndde_prev = self._interp_series_at(
            self._piv_ndde._confirmed_highs, prev_ph.timestamp, is_high=True
        )

        bars_between = curr_ph.index - prev_ph.index

        # ── Par A: Precio vs Delta-RSI ─────────────────────────
        if drsi_curr is not None and drsi_prev is not None:
            if curr_ph.value > prev_ph.value and drsi_curr < drsi_prev:
                # REGULAR_BEAR: precio sube, Delta-RSI baja
                div = self._make_div(
                    DivType.REGULAR_BEAR,
                    "A_PRICE_DRSI",
                    "BEAR",
                    curr_ph,
                    prev_ph,
                    drsi_curr,
                    drsi_prev,
                    bars_between,
                    tick,
                )
                if div:
                    divs.append(div)
            elif curr_ph.value < prev_ph.value and drsi_curr > drsi_prev:
                # HIDDEN_BEAR
                div = self._make_div(
                    DivType.HIDDEN_BEAR,
                    "A_PRICE_DRSI",
                    "BEAR",
                    curr_ph,
                    prev_ph,
                    drsi_curr,
                    drsi_prev,
                    bars_between,
                    tick,
                )
                if div:
                    divs.append(div)

        # ── Par B: Precio vs NDDE ─────────────────────────────
        if ndde_curr is not None and ndde_prev is not None:
            if curr_ph.value > prev_ph.value and ndde_curr < ndde_prev:
                div = self._make_div(
                    DivType.REGULAR_BEAR,
                    "B_PRICE_NDDE",
                    "BEAR",
                    curr_ph,
                    prev_ph,
                    ndde_curr,
                    ndde_prev,
                    bars_between,
                    tick,
                )
                if div:
                    divs.append(div)
            elif curr_ph.value < prev_ph.value and ndde_curr > ndde_prev:
                div = self._make_div(
                    DivType.HIDDEN_BEAR,
                    "B_PRICE_NDDE",
                    "BEAR",
                    curr_ph,
                    prev_ph,
                    ndde_curr,
                    ndde_prev,
                    bars_between,
                    tick,
                )
                if div:
                    divs.append(div)

        # ── Par C: Delta-RSI vs NDDE ──────────────────────────
        drsi_highs = self._piv_drsi.last_n_highs(self.lookback + 1)
        ndde_highs = self._piv_ndde.last_n_highs(self.lookback + 1)
        if len(drsi_highs) >= 2 and len(ndde_highs) >= 2:
            dh_curr = drsi_highs[-1]
            dh_prev = drsi_highs[-2]
            nh_curr_val = self._interp_series_at(ndde_highs, dh_curr.timestamp, True)
            nh_prev_val = self._interp_series_at(ndde_highs, dh_prev.timestamp, True)
            if nh_curr_val is not None and nh_prev_val is not None:
                if dh_curr.value > dh_prev.value and nh_curr_val < nh_prev_val:
                    div = self._make_div(
                        DivType.REGULAR_BEAR,
                        "C_DRSI_NDDE",
                        "BEAR",
                        dh_curr,
                        dh_prev,
                        nh_curr_val,
                        nh_prev_val,
                        dh_curr.index - dh_prev.index,
                        tick,
                    )
                    if div:
                        divs.append(div)

        return divs

    # ── Escaneo de mínimos (divergencias alcistas) ─────────────
    def _scan_lows(self, tick: TickInput) -> list[Divergence]:
        """
        Cuando se confirma un nuevo pivot low de precio, busca
        divergencias alcistas (REGULAR_BULL, HIDDEN_BULL).
        """
        divs = []
        price_lows = self._piv_price.last_n_lows(self.lookback + 1)
        if len(price_lows) < 2:
            return divs

        curr_pl = price_lows[-1]
        prev_pl = price_lows[-2]

        drsi_curr = self._interp_series_at(
            self._piv_drsi._confirmed_lows, curr_pl.timestamp, is_high=False
        )
        drsi_prev = self._interp_series_at(
            self._piv_drsi._confirmed_lows, prev_pl.timestamp, is_high=False
        )
        ndde_curr = self._interp_series_at(
            self._piv_ndde._confirmed_lows, curr_pl.timestamp, is_high=False
        )
        ndde_prev = self._interp_series_at(
            self._piv_ndde._confirmed_lows, prev_pl.timestamp, is_high=False
        )

        bars_between = curr_pl.index - prev_pl.index

        # ── Par A: Precio vs Delta-RSI ─────────────────────────
        if drsi_curr is not None and drsi_prev is not None:
            if curr_pl.value < prev_pl.value and drsi_curr > drsi_prev:
                # REGULAR_BULL: precio hace mín más bajo, Delta-RSI hace mín más alto
                div = self._make_div(
                    DivType.REGULAR_BULL,
                    "A_PRICE_DRSI",
                    "BULL",
                    curr_pl,
                    prev_pl,
                    drsi_curr,
                    drsi_prev,
                    bars_between,
                    tick,
                )
                if div:
                    divs.append(div)
            elif curr_pl.value > prev_pl.value and drsi_curr < drsi_prev:
                div = self._make_div(
                    DivType.HIDDEN_BULL,
                    "A_PRICE_DRSI",
                    "BULL",
                    curr_pl,
                    prev_pl,
                    drsi_curr,
                    drsi_prev,
                    bars_between,
                    tick,
                )
                if div:
                    divs.append(div)

        # ── Par B: Precio vs NDDE ─────────────────────────────
        if ndde_curr is not None and ndde_prev is not None:
            if curr_pl.value < prev_pl.value and ndde_curr > ndde_prev:
                div = self._make_div(
                    DivType.REGULAR_BULL,
                    "B_PRICE_NDDE",
                    "BULL",
                    curr_pl,
                    prev_pl,
                    ndde_curr,
                    ndde_prev,
                    bars_between,
                    tick,
                )
                if div:
                    divs.append(div)
            elif curr_pl.value > prev_pl.value and ndde_curr < ndde_prev:
                div = self._make_div(
                    DivType.HIDDEN_BULL,
                    "B_PRICE_NDDE",
                    "BULL",
                    curr_pl,
                    prev_pl,
                    ndde_curr,
                    ndde_prev,
                    bars_between,
                    tick,
                )
                if div:
                    divs.append(div)

        # ── Par C: Delta-RSI vs NDDE ──────────────────────────
        drsi_lows = self._piv_drsi.last_n_lows(self.lookback + 1)
        ndde_lows = self._piv_ndde.last_n_lows(self.lookback + 1)
        if len(drsi_lows) >= 2 and len(ndde_lows) >= 2:
            dl_curr = drsi_lows[-1]
            dl_prev = drsi_lows[-2]
            nl_curr_val = self._interp_series_at(ndde_lows, dl_curr.timestamp, False)
            nl_prev_val = self._interp_series_at(ndde_lows, dl_prev.timestamp, False)
            if nl_curr_val is not None and nl_prev_val is not None:
                if dl_curr.value < dl_prev.value and nl_curr_val > nl_prev_val:
                    div = self._make_div(
                        DivType.REGULAR_BULL,
                        "C_DRSI_NDDE",
                        "BULL",
                        dl_curr,
                        dl_prev,
                        nl_curr_val,
                        nl_prev_val,
                        dl_curr.index - dl_prev.index,
                        tick,
                    )
                    if div:
                        divs.append(div)

        return divs

    # ── Constructor de divergencia ─────────────────────────────
    def _make_div(
        self,
        div_type: DivType,
        pair: str,
        direction: str,
        pivot_curr: Pivot,
        pivot_prev: Pivot,
        val_b_curr: float,
        val_b_prev: float,
        bars_between: int,
        tick: TickInput,
    ) -> Divergence | None:
        """
        Construye una Divergence con score completo.
        """
        # Contar cuántos otros pares ya detectaron la misma dirección recientemente
        recent_cutoff = tick.timestamp - pd.Timedelta(minutes=10)
        n_confirming = sum(
            1
            for d in self._divergences[-20:]
            if d.direction == direction and d.ts_curr >= recent_cutoff and d.pair != pair
        )

        score, breakdown = self._scorer.score(
            div_type=div_type,
            pair=pair,
            val_a_curr=pivot_curr.value,
            val_a_prev=pivot_prev.value,
            val_b_curr=val_b_curr,
            val_b_prev=val_b_prev,
            n_confirming_pairs=n_confirming,
            bars_between_pivots=max(1, bars_between),
            regime=tick.regime,
            iv_atm=tick.iv_atm,
            net_gex=tick.net_gex,
            net_premium=tick.net_premium,
        )

        if score < self.min_score:
            return None

        return Divergence(
            div_type=div_type,
            pair=pair,
            direction=direction,
            ts_curr=pivot_curr.timestamp,
            ts_prev=pivot_prev.timestamp,
            val_a_curr=pivot_curr.value,
            val_a_prev=pivot_prev.value,
            val_b_curr=val_b_curr,
            val_b_prev=val_b_prev,
            score=score,
            strength=DivStrength.from_score(score),
            score_breakdown=breakdown,
            regime=tick.regime,
            iv_atm=tick.iv_atm,
            net_gex=tick.net_gex,
            net_premium=tick.net_premium,
            actionable=score >= 50,
        )

    # ── Interpolación de valor en timestamp ───────────────────
    def _interp_series_at(
        self,
        pivots: list[Pivot],
        target_ts: pd.Timestamp,
        is_high: bool,
    ) -> float | None:
        """
        Busca el valor del pivot más cercano al timestamp objetivo.
        Tolerancia: ±15 minutos.
        """
        if not pivots:
            return None

        # Buscar en los últimos lookback pivots
        candidates = pivots[-max(1, self.lookback * 2) :]
        best = None
        best_delta = pd.Timedelta(minutes=15)

        for p in candidates:
            delta = abs(p.timestamp - target_ts)
            if delta <= best_delta:
                best_delta = delta
                best = p.value

        return best

    # ── Helpers de output ──────────────────────────────────────
    def to_dataframe(self) -> pd.DataFrame:
        if not self._divergences:
            return pd.DataFrame()
        rows = [d.to_dict() for d in self._divergences]
        df = pd.DataFrame(rows)
        return df.sort_values("ts_curr").reset_index(drop=True)

    def get_active_divergences(
        self,
        since_minutes: int = 10,
        min_score: float = 50.0,
    ) -> list[Divergence]:
        """
        Retorna divergencias activas (recientes y accionables).
        Útil para el Signal Combiner en tiempo real.
        """
        cutoff = (
            self._ctx_buf[-1].timestamp - pd.Timedelta(minutes=since_minutes)
            if self._ctx_buf
            else pd.Timestamp.min
        )
        return [d for d in self._divergences if d.score >= min_score and d.ts_curr >= cutoff]

    def get_triple_divergences(self) -> list[tuple]:
        """
        Devuelve grupos de divergencias donde los 3 pares coinciden
        en dirección y timestamp (las de mayor calidad del sistema).
        """
        df = self.to_dataframe()
        if df.empty:
            return []

        triples = []
        # Agrupar por ventana de 15 minutos y dirección
        df["ts_curr"] = pd.to_datetime(df["ts_curr"])
        df["window"] = df["ts_curr"].dt.floor("15min")
        grouped = df.groupby(["window", "direction"])

        for (window, direction), group in grouped:
            pairs_present = set(group["pair"].unique())
            if len(pairs_present) >= 2:
                max_score = group["score"].max()
                triples.append(
                    {
                        "window": window,
                        "direction": direction,
                        "pairs": list(pairs_present),
                        "n_pairs": len(pairs_present),
                        "max_score": round(max_score, 2),
                        "strength": DivStrength.from_score(max_score).value,
                    }
                )
        return sorted(triples, key=lambda x: x["max_score"], reverse=True)


# ─────────────────────────────────────────────
# 6. GENERADOR DE DATOS DEMO
# ─────────────────────────────────────────────


def generate_demo(
    ticker: str = "AAPL",
    n: int = 390,
    base: float = 192.50,
    seed: int = 42,
) -> list[TickInput]:
    """
    Genera ticks con 4 fases que producen las 4 divergencias clásicas.
    Las fases están diseñadas para que los 3 pares diverjan
    en el mismo período, generando divergencias triples.
    """
    rng = np.random.default_rng(seed)
    start = pd.Timestamp("2025-01-15 09:30:00", tz="America/New_York")
    tss = pd.date_range(start, periods=n, freq="1min")

    # EMA incrementales para simular Delta-RSI y NDDE coherentes
    # Delta-RSI (0-100), NDDE (en millones)
    drsi = 50.0
    ndde = 0.0
    price = base

    # (bars, p_tr, drsi_tr, ndde_tr, regime, noise_p, noise_d, noise_n)
    phases = [
        # Fase 1: momentum alcista limpio
        (95, 0.00055, +0.30, +80_000, "GAMMA_POS", 0.0005, 0.5, 50_000),
        # Fase 2: distribución — precio sube, indicadores bajan
        (100, 0.00025, -0.40, -90_000, "GAMMA_NEG", 0.0004, 0.4, 45_000),
        # Fase 3: caída confirmada
        (100, -0.00045, -0.35, -70_000, "GAMMA_NEG", 0.0007, 0.5, 55_000),
        # Fase 4: acumulación — precio baja, indicadores suben
        (95, -0.00020, +0.45, +85_000, "GAMMA_POS", 0.0005, 0.4, 40_000),
    ]

    ticks = []
    idx = 0
    ndde_smooth_buf = deque(maxlen=3)

    for n_b, p_tr, d_tr, n_tr, regime, p_n, d_n, n_n in phases:
        for _ in range(n_b):
            if idx >= n:
                break
            ts = tss[idx]

            # Precio
            price *= 1 + p_tr + rng.normal(0, p_n)

            # Delta-RSI con tendencia y ruido
            drsi += d_tr + rng.normal(0, d_n)
            drsi = float(np.clip(drsi, 5, 95))

            # NDDE con tendencia y ruido
            ndde += n_tr + rng.normal(0, n_n)
            ndde = float(np.clip(ndde, -3_000_000, 3_000_000))

            ndde_smooth_buf.append(ndde)
            ndde_smooth = float(np.mean(ndde_smooth_buf))

            iv = float(rng.uniform(0.12, 0.35))
            gex = ndde * rng.uniform(0.3, 0.7)
            prem = ndde * rng.uniform(0.1, 0.3)
            sweep = int(rng.poisson(abs(d_tr) * 5))

            ticks.append(
                TickInput(
                    timestamp=ts,
                    ticker=ticker,
                    close=price,
                    high=price * (1 + abs(rng.normal(0, 0.0005))),
                    low=price * (1 - abs(rng.normal(0, 0.0005))),
                    delta_rsi=drsi,
                    rsi_flow=drsi + rng.normal(0, 2),
                    hist_flow=float(rng.normal(d_tr * 2, 1)),
                    ndde=ndde,
                    ndde_smooth=ndde_smooth,
                    macd_ndde=float(rng.normal(n_tr * 0.5, 10_000)),
                    net_gex=gex,
                    net_premium=prem,
                    iv_atm=iv,
                    regime=regime,
                    sweep_count=sweep,
                )
            )
            idx += 1

    return ticks


# ─────────────────────────────────────────────
# 7. PIPELINE + REPORTE
# ─────────────────────────────────────────────


def run_hybrid_divergence_engine(
    ticker: str = "AAPL",
    n: int = 390,
    verbose: bool = True,
) -> tuple[pd.DataFrame, list[dict]]:

    print(f"\n{'═'*68}")
    print(f"  MOTOR DIVERGENCIAS HÍBRIDO  |  {ticker}  |  {n} velas 1m")
    print(f"{'═'*68}")

    ticks = generate_demo(ticker, n)
    engine = HybridDivergenceEngine(ticker=ticker, pivot_window=5, min_score=25)

    for tick in ticks:
        engine.update(tick)

    df = engine.to_dataframe()
    triples = engine.get_triple_divergences()

    if verbose:
        _print_report(df, triples, ticker)

    return df, triples


def _print_report(df: pd.DataFrame, triples: list[dict], ticker: str):
    print(f"\n── Divergencias detectadas: {len(df)} total ──────────────")

    if df.empty:
        print("  Sin divergencias en esta sesión.")
        print(f"\n{'═'*68}")
        return

    # Por par
    print("\n── Por par ──")
    for pair in ["A_PRICE_DRSI", "B_PRICE_NDDE", "C_DRSI_NDDE"]:
        sub = df[df["pair"] == pair]
        if not sub.empty:
            print(
                f"  {pair:20s}: {len(sub):2d} divs | "
                f"score max={sub['score'].max():.1f} "
                f"avg={sub['score'].mean():.1f}"
            )

    # Por tipo
    print("\n── Por tipo ──")
    print(df["div_type"].value_counts().to_string())

    # Por fuerza
    print("\n── Por fuerza ──")
    print(df["strength"].value_counts().to_string())

    # Accionables
    act = df[df["actionable"] == True]
    print(f"\n── Accionables (score≥50): {len(act)} ──")
    if not act.empty:
        cols = [
            "ts_curr",
            "pair",
            "div_type",
            "direction",
            "score",
            "strength",
            "regime",
            "actionable",
        ]
        print(act[cols].tail(10).to_string(index=False))

    # Desglose de scores de las top 5
    top5 = df.nlargest(5, "score")
    print("\n── Top 5 por score ── (desglose de factores F1-F7)")
    score_cols = [c for c in df.columns if c.startswith("score_f")]
    if score_cols:
        display_cols = ["pair", "div_type", "direction", "score"] + score_cols
        print(top5[display_cols].to_string(index=False))

    # Triples
    print(f"\n── Divergencias triples (≥2 pares alineados): {len(triples)} ──")
    for t in triples[:5]:
        print(
            f"  {t['direction']:4s} | {t['window']!s:22s} | "
            f"pares={t['n_pairs']} {t['pairs']} | "
            f"score={t['max_score']:.1f} [{t['strength']}]"
        )

    # Estadísticas de scoring
    print("\n── Estadísticas de score ──")
    print(f"  Score máximo       : {df['score'].max():.2f}")
    print(f"  Score promedio     : {df['score'].mean():.2f}")
    print(f"  Score mediana      : {df['score'].median():.2f}")
    print(f"  Score std          : {df['score'].std():.2f}")

    if score_cols:
        print("\n── Contribución promedio por factor ──")
        for col in score_cols:
            label = col.replace("score_", "").replace("_", " ")
            print(f"  {label:30s}: {df[col].mean():.2f} pts")

    print(f"\n{'═'*68}")


# ─────────────────────────────────────────────
# 8. INTEGRACIÓN CON SIGNAL COMBINER
# ─────────────────────────────────────────────


class DivergenceSignalAdapter:
    """
    Adaptador que convierte las divergencias activas del motor en
    una señal estructurada compatible con el Signal Combiner.

    Uso en el bot:
        adapter = DivergenceSignalAdapter(engine)

        # En cada tick, obtener señal para el combiner:
        signal_input = adapter.get_combiner_input()
        # signal_input tiene: direction_bias, score, strength, n_active
    """

    def __init__(self, engine: HybridDivergenceEngine):
        self._engine = engine

    def get_combiner_input(
        self,
        since_minutes: int = 15,
        min_score: float = 40.0,
    ) -> dict:
        """
        Agrega las divergencias activas recientes en una señal única.
        """
        active = self._engine.get_active_divergences(since_minutes, min_score)

        if not active:
            return {
                "direction_bias": "NEUTRAL",
                "score": 0.0,
                "strength": DivStrength.WEAK.value,
                "n_active": 0,
                "n_bull": 0,
                "n_bear": 0,
                "best_pair": None,
            }

        bulls = [d for d in active if d.direction == "BULL"]
        bears = [d for d in active if d.direction == "BEAR"]

        bull_score = sum(d.score for d in bulls)
        bear_score = sum(d.score for d in bears)

        if bull_score > bear_score:
            direction = "LONG"
            top_score = max(d.score for d in bulls)
            best = max(bulls, key=lambda d: d.score)
        elif bear_score > bull_score:
            direction = "SHORT"
            top_score = max(d.score for d in bears)
            best = max(bears, key=lambda d: d.score)
        else:
            direction = "NEUTRAL"
            top_score = 0.0
            best = None

        return {
            "direction_bias": direction,
            "score": round(top_score, 2),
            "strength": DivStrength.from_score(top_score).value,
            "n_active": len(active),
            "n_bull": len(bulls),
            "n_bear": len(bears),
            "best_pair": best.pair if best else None,
            "best_div_type": best.div_type.value if best else None,
        }


# ─────────────────────────────────────────────
# 9. EJECUCIÓN DEMO
# ─────────────────────────────────────────────

if __name__ == "__main__":
    TICKERS = ["AAPL", "MSFT", "PLTR", "TSLA", "GOOGL"]

    for ticker in TICKERS:
        df, triples = run_hybrid_divergence_engine(ticker=ticker, n=390, verbose=True)
        if not df.empty:
            df.to_csv(f"/tmp/divergencias_hybrid_{ticker.lower()}.csv", index=False)

    print("\n✓ Motor de Divergencias Híbrido completado para los 5 proxies BingX.")
