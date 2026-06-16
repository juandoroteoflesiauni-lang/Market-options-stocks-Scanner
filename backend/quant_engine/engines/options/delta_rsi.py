from typing import Any
"""
Delta-RSI — RSI calculado sobre flujo de derivados (opciones)
═════════════════════════════════════════════════════════════
El RSI clásico compara alzas vs. bajas del PRECIO.
El Delta-RSI ignora el precio y compara:

    RS_options = EMA(Volumen Delta Positivo) / EMA(Volumen Delta Negativo)

    Delta_RSI  = 100 − 100 / (1 + RS_options)

donde:
    Vol Delta Positivo = calls compradas + puts vendidas (flujo alcista)
    Vol Delta Negativo = puts compradas + calls vendidas (flujo bajista)

Esto mide la sobrecompra/sobreventa INSTITUCIONAL, no del precio.
La señal más poderosa es la DIVERGENCIA entre precio y Delta-RSI:
    precio baja  + Delta-RSI sube  → divergencia alcista (acumulación)
    precio sube  + Delta-RSI baja  → divergencia bajista (distribución)

Fuentes:
    BingX WebSocket  →  velas 1m OHLCV
    Massive API      →  trades de opciones: calls/puts × Delta × volumen

Compatibilidad: pandas >= 2.0 · numpy >= 1.24 · pandas-ta
"""

import warnings
from dataclasses import dataclass, field

import numpy as np
import pandas as pd
import pandas_ta as ta
import logging
logger = logging.getLogger(__name__)


warnings.filterwarnings("ignore")


# ─────────────────────────────────────────────
# 1. ESTRUCTURAS DE DATOS
# ─────────────────────────────────────────────


@dataclass
class OptionsFlow:
    """
    Flujo de opciones ejecutadas en un minuto desde Massive API.
    Cada campo representa el VOLUMEN DELTA de esa categoría:
        vol_delta = contratos_ejecutados × |Delta| × 100
    """

    timestamp: pd.Timestamp
    ticker: str

    # Flujo alcista (empuja precio hacia arriba)
    call_buy_vol_delta: float  # Calls compradas × Delta (agresivas)
    put_sell_vol_delta: float  # Puts vendidas × |Delta| (cubiertas)

    # Flujo bajista (empuja precio hacia abajo)
    put_buy_vol_delta: float  # Puts compradas × |Delta| (agresivas)
    call_sell_vol_delta: float  # Calls vendidas × Delta (cubiertas)

    # Datos extra para análisis de divergencia
    net_premium: float  # Prima neta pagada (USD) — mide convicción
    sweep_count: int  # Número de option sweeps del minuto
    iv_atm: float  # IV ATM del minuto (para régimen)
    net_gex: float  # GEX neto para régimen Gamma

    # Calculados en post_init
    vol_delta_pos: float = field(init=False)
    vol_delta_neg: float = field(init=False)
    net_flow: float = field(init=False)
    flow_ratio: float = field(init=False)

    def __post_init__(self):
        # Volumen delta positivo total (fuerza compradora institucional)
        self.vol_delta_pos = self.call_buy_vol_delta + self.put_sell_vol_delta

        # Volumen delta negativo total (fuerza vendedora institucional)
        # guardamos como positivo para la fórmula del RSI
        self.vol_delta_neg = abs(self.put_buy_vol_delta) + abs(self.call_sell_vol_delta)

        # Flujo neto (positivo = comprador, negativo = vendedor)
        self.net_flow = self.vol_delta_pos - self.vol_delta_neg

        # Ratio rápido para clasificación
        total = self.vol_delta_pos + self.vol_delta_neg
        self.flow_ratio = self.net_flow / total if total > 0 else 0.0


@dataclass
class CandleBar:
    """Vela 1m de BingX proxy."""

    timestamp: pd.Timestamp
    ticker: str
    open: float
    high: float
    low: float
    close: float
    volume: float
    typical_price: float = field(init=False)

    def __post_init__(self):
        self.typical_price = (self.high + self.low + self.close) / 3.0


# ─────────────────────────────────────────────
# 2. DETECTOR DE DIVERGENCIAS
# ─────────────────────────────────────────────


class DivergenceDetector:
    """
    Detecta divergencias entre dos series usando pivots (máximos/mínimos locales).

    Algoritmo:
        1. Identificar pivots en la serie de PRECIO (highs y lows locales)
        2. En los mismos timestamps, comparar los valores del DELTA-RSI
        3. Si la dirección del precio y del Delta-RSI difieren → divergencia

    Tipos:
        REGULAR_BULL  : precio hace mínimo más bajo, Delta-RSI hace mínimo más alto
                        → acumulación oculta, reversión alcista inminente
        REGULAR_BEAR  : precio hace máximo más alto, Delta-RSI hace máximo más bajo
                        → distribución oculta, reversión bajista inminente
        HIDDEN_BULL   : precio hace mínimo más alto, Delta-RSI hace mínimo más bajo
                        → pullback en tendencia alcista, continuación esperada
        HIDDEN_BEAR   : precio hace máximo más bajo, Delta-RSI hace máximo más alto
                        → rebote en tendencia bajista, continuación bajista esperada
    """

    def __init__(self, pivot_window: int = 5, lookback: int = 50):
        """
        Args:
            pivot_window: velas a cada lado para confirmar un pivot (default 5)
            lookback:     máximo de velas hacia atrás para buscar el pivot anterior
        """
        self.pivot_window = pivot_window
        self.lookback = lookback

    def find_pivots(self, series: pd.Series) -> tuple[pd.Series, pd.Series]:
        """
        Encuentra pivot highs y pivot lows en una serie.

        Returns:
            (pivot_highs, pivot_lows) — Series con NaN donde no hay pivot
        """
        n = len(series)
        highs = pd.Series(np.nan, index=series.index)
        lows = pd.Series(np.nan, index=series.index)
        w = self.pivot_window

        for i in range(w, n - w):
            window = series.iloc[i - w : i + w + 1]
            val = series.iloc[i]

            if val == window.max():
                highs.iloc[i] = val
            if val == window.min():
                lows.iloc[i] = val

        return highs, lows

    def detect(
        self,
        price: pd.Series,
        delta_rsi: pd.Series,
    ) -> pd.DataFrame:
        """
        Detecta todas las divergencias entre precio y Delta-RSI.

        Returns:
            DataFrame con columnas:
                timestamp, type, price_val, rsi_val,
                prev_price, prev_rsi, strength, confirmed
        """
        # Pivots de precio y Delta-RSI
        ph_p, pl_p = self.find_pivots(price)
        ph_r, pl_r = self.find_pivots(delta_rsi)

        divergences = []

        # ── Divergencias en MÁXIMOS (bajistas) ────────────────
        pivot_high_idx = ph_p.dropna().index
        for i in range(1, len(pivot_high_idx)):
            curr_idx = pivot_high_idx[i]
            prev_idx = pivot_high_idx[i - 1]

            # Buscar el pivot de RSI más cercano al mismo período
            rsi_window = ph_r[prev_idx:curr_idx].dropna()
            if rsi_window.empty:
                continue

            curr_p = ph_p[curr_idx]
            prev_p = ph_p[prev_idx]
            curr_r = (
                ph_r[curr_idx] if not np.isnan(ph_r.get(curr_idx, np.nan)) else delta_rsi[curr_idx]
            )
            prev_r = rsi_window.iloc[-1]

            # Regular Bear: precio sube + RSI baja
            if curr_p > prev_p and curr_r < prev_r:
                strength = self._calc_strength(curr_p, prev_p, curr_r, prev_r, "bear")
                divergences.append(
                    {
                        "timestamp": curr_idx,
                        "type": "REGULAR_BEAR",
                        "price_val": curr_p,
                        "prev_price": prev_p,
                        "rsi_val": curr_r,
                        "prev_rsi": prev_r,
                        "strength": strength,
                        "confirmed": curr_r < 60,  # RSI ya no está en zona alta
                    }
                )

            # Hidden Bear: precio baja + RSI sube (en contexto bajista)
            elif curr_p < prev_p and curr_r > prev_r:
                strength = self._calc_strength(curr_p, prev_p, curr_r, prev_r, "hidden_bear")
                divergences.append(
                    {
                        "timestamp": curr_idx,
                        "type": "HIDDEN_BEAR",
                        "price_val": curr_p,
                        "prev_price": prev_p,
                        "rsi_val": curr_r,
                        "prev_rsi": prev_r,
                        "strength": strength,
                        "confirmed": curr_r > 40,
                    }
                )

        # ── Divergencias en MÍNIMOS (alcistas) ────────────────
        pivot_low_idx = pl_p.dropna().index
        for i in range(1, len(pivot_low_idx)):
            curr_idx = pivot_low_idx[i]
            prev_idx = pivot_low_idx[i - 1]

            rsi_window = pl_r[prev_idx:curr_idx].dropna()
            if rsi_window.empty:
                continue

            curr_p = pl_p[curr_idx]
            prev_p = pl_p[prev_idx]
            curr_r = (
                pl_r[curr_idx] if not np.isnan(pl_r.get(curr_idx, np.nan)) else delta_rsi[curr_idx]
            )
            prev_r = rsi_window.iloc[-1]

            # Regular Bull: precio baja + RSI sube ← LA MÁS VALIOSA
            if curr_p < prev_p and curr_r > prev_r:
                strength = self._calc_strength(curr_p, prev_p, curr_r, prev_r, "bull")
                divergences.append(
                    {
                        "timestamp": curr_idx,
                        "type": "REGULAR_BULL",
                        "price_val": curr_p,
                        "prev_price": prev_p,
                        "rsi_val": curr_r,
                        "prev_rsi": prev_r,
                        "strength": strength,
                        "confirmed": curr_r > 40,  # RSI ya salió de zona baja
                    }
                )

            # Hidden Bull: precio sube + RSI baja (pullback en uptrend)
            elif curr_p > prev_p and curr_r < prev_r:
                strength = self._calc_strength(curr_p, prev_p, curr_r, prev_r, "hidden_bull")
                divergences.append(
                    {
                        "timestamp": curr_idx,
                        "type": "HIDDEN_BULL",
                        "price_val": curr_p,
                        "prev_price": prev_p,
                        "rsi_val": curr_r,
                        "prev_rsi": prev_r,
                        "strength": strength,
                        "confirmed": curr_r < 60,
                    }
                )

        if not divergences:
            return pd.DataFrame()

        df = pd.DataFrame(divergences)
        df["strength_label"] = df["strength"].map({1: "DÉBIL", 2: "MEDIA", 3: "FUERTE"})
        return df.sort_values("timestamp").reset_index(drop=True)

    def _calc_strength(
        self,
        curr_p: float,
        prev_p: float,
        curr_r: float,
        prev_r: float,
        div_type: str,
    ) -> int:
        """
        Calcula la fuerza de la divergencia (1=débil, 2=media, 3=fuerte).

        Factores:
          - Distancia del RSI a las zonas extremas (< 30 o > 70)
          - Magnitud de la divergencia (cuánto difieren precio y RSI)
          - Confirmación por zona sobrecompra/sobreventa
        """
        price_change_pct = abs(curr_p - prev_p) / prev_p * 100
        rsi_change = abs(curr_r - prev_r)

        # Divergencia más pronunciada = más fuerte
        score = 1
        if price_change_pct > 0.3:
            score += 0.5
        if rsi_change > 10:
            score += 0.5

        # RSI en zona extrema confirma fuerza
        if div_type in ("bull", "hidden_bull") and curr_r < 35:
            score += 1
        if div_type in ("bear", "hidden_bear") and curr_r > 65:
            score += 1

        return min(3, int(score))


# ─────────────────────────────────────────────
# 3. MOTOR DELTA-RSI
# ─────────────────────────────────────────────


class DeltaRSIEngine:
    """
    Calcula el Delta-RSI exclusivamente sobre flujo de derivados.

    Matemática:
        gain_i = Vol_Delta_Positivo(i)   (flujo alcista de opciones)
        loss_i = Vol_Delta_Negativo(i)   (flujo bajista de opciones)

        EMA_gain = EMA(gain, period)
        EMA_loss = EMA(loss, period)

        RS         = EMA_gain / EMA_loss
        Delta_RSI  = 100 − 100 / (1 + RS)

    Adicionalmente calcula:
        - Delta-RSI suavizado (señal, EMA del Delta-RSI)
        - Histograma (Delta-RSI − señal), equivalente al MACD histogram
        - Zona de régimen (oversold/neutral/overbought institucional)
        - Divergencias con el precio spot

    Args:
        ticker:          Símbolo
        period:          Período EMA para RS. Default 14.
        signal_period:   Período EMA de la línea de señal. Default 9.
        ob_level:        Nivel sobrecompra institucional. Default 70.
        os_level:        Nivel sobreventa institucional. Default 30.
        div_window:      Ventana de pivot para detector de divergencias.
        premium_weight:  Si True, pondera vol_delta por net_premium (convicción $).
    """

    def __init__(
        self,
        ticker: str,
        period: int = 14,
        signal_period: int = 9,
        ob_level: float = 70.0,
        os_level: float = 30.0,
        div_window: int = 5,
        premium_weight: bool = True,
    ):
        self.ticker = ticker
        self.period = period
        self.signal_period = signal_period
        self.ob_level = ob_level
        self.os_level = os_level
        self.premium_weight = premium_weight

        # Multiplicador EMA: alpha = 2 / (n + 1)
        self._alpha_rsi = 2.0 / (period + 1)
        self._alpha_signal = 2.0 / (signal_period + 1)

        # Estado EMA (Wilder suavizado, igual que RSI clásico)
        self._ema_gain: float | None = None
        self._ema_loss: float | None = None
        self._ema_signal: float | None = None

        # Buffers para inicialización SMA (primeras `period` velas)
        self._init_gains: list[float] = []
        self._init_losses: list[float] = []
        self._initialized = False

        # Historia
        self._history: list[dict] = []

        # Detector de divergencias
        self._divergence_detector = DivergenceDetector(pivot_window=div_window, lookback=50)

    # ── Ponderación por premium ────────────────────────────────
    def _apply_premium_weight(self, flow: OptionsFlow) -> tuple[float, float]:
        """
        Si premium_weight=True, amplifica el vol_delta de los flujos
        que pagaron más prima (mayor convicción institucional).

        Un sweep que pagó $500k de prima tiene más peso que uno de $10k.
        """
        if not self.premium_weight or flow.net_premium == 0:
            return flow.vol_delta_pos, flow.vol_delta_neg

        # Normalizamos por un premium de referencia de $100k
        premium_factor = min(3.0, 1.0 + abs(flow.net_premium) / 100_000)

        # El premium positivo amplifica la señal alcista y viceversa
        if flow.net_premium > 0:
            return (flow.vol_delta_pos * premium_factor, flow.vol_delta_neg)
        else:
            return (flow.vol_delta_pos, flow.vol_delta_neg * premium_factor)

    # ── EMA Wilder ─────────────────────────────────────────────
    def _update_ema(
        self,
        current: float,
        prev_ema: float | None,
        alpha: float,
    ) -> float:
        if prev_ema is None:
            return current
        return alpha * current + (1 - alpha) * prev_ema

    # ── Clasificación de zona ──────────────────────────────────
    def _classify_zone(self, rsi: float) -> str:
        if rsi >= self.ob_level:
            return "INST_OVERBOUGHT"  # Instituciones sobrecompradas
        if rsi <= self.os_level:
            return "INST_OVERSOLD"  # Instituciones sobrevendidas
        if rsi >= 55:
            return "BULLISH_BIAS"
        if rsi <= 45:
            return "BEARISH_BIAS"
        return "NEUTRAL"

    # ── Señal de trading ───────────────────────────────────────
    def _generate_signal(
        self,
        delta_rsi: float,
        signal_line: float,
        histogram: float,
        zone: str,
        prev_histogram: float | None,
        flow: OptionsFlow,
    ) -> tuple[str, int]:
        """
        Señales del Delta-RSI:

        FLOW_EXHAUSTION_LONG  : RSI institucional sobrevendido + histogram cruza +
        FLOW_EXHAUSTION_SHORT : RSI institucional sobrecomprado + histogram cruza −
        MOMENTUM_LONG         : Cruce alcista de línea de señal + zona alcista
        MOMENTUM_SHORT        : Cruce bajista de línea de señal + zona bajista
        SWEEP_SURGE_LONG      : Option sweep alcista + RSI < 50 (acumulación oculta)
        SWEEP_SURGE_SHORT     : Option sweep bajista + RSI > 50 (distribución oculta)
        NEUTRAL               : Sin señal clara
        """
        histogram_cross_up = prev_histogram is not None and prev_histogram < 0 and histogram > 0
        histogram_cross_down = prev_histogram is not None and prev_histogram > 0 and histogram < 0

        # Agotamiento institucional (los más fuertes)
        if zone == "INST_OVERSOLD" and histogram_cross_up:
            return "FLOW_EXHAUSTION_LONG", 3
        if zone == "INST_OVERBOUGHT" and histogram_cross_down:
            return "FLOW_EXHAUSTION_SHORT", 3

        # Sweeps en contratendencia (segunda señal más fuerte)
        if flow.sweep_count >= 3:
            if flow.net_flow > 0 and delta_rsi < 50:
                return "SWEEP_SURGE_LONG", 3
            if flow.net_flow < 0 and delta_rsi > 50:
                return "SWEEP_SURGE_SHORT", 3

        # Cruces de señal con confirmación de zona
        if histogram_cross_up and zone in ("BULLISH_BIAS", "NEUTRAL"):
            return "MOMENTUM_LONG", 2
        if histogram_cross_down and zone in ("BEARISH_BIAS", "NEUTRAL"):
            return "MOMENTUM_SHORT", 2

        # Cruces simples
        if histogram_cross_up:
            return "MOMENTUM_LONG", 1
        if histogram_cross_down:
            return "MOMENTUM_SHORT", 1

        return "NEUTRAL", 0

    # ── Tick principal ──────────────────────────────────────────
    def update(
        self,
        candle: CandleBar,
        flow: OptionsFlow | None = None,
    ) -> dict[str, Any]:
        """
        Procesa una vela de 1m con su flujo de opciones del mismo minuto.
        """
        # ── Flujo de opciones ──────────────────────────────────
        if flow is not None:
            gain, loss = self._apply_premium_weight(flow)
            net_flow = flow.net_flow
            net_premium = flow.net_premium
            sweep_count = flow.sweep_count
            iv_atm = flow.iv_atm
            net_gex = flow.net_gex
            flow_ratio = flow.flow_ratio
        else:
            gain = loss = 1.0  # neutro si no hay opciones
            net_flow = net_premium = 0.0
            sweep_count = 0
            iv_atm = net_gex = flow_ratio = 0.0

        gain = max(gain, 1e-9)  # evitar división por cero
        loss = max(loss, 1e-9)

        # ── Inicialización SMA (primeras `period` velas) ───────
        if not self._initialized:
            self._init_gains.append(gain)
            self._init_losses.append(loss)

            if len(self._init_gains) >= self.period:
                self._ema_gain = float(np.mean(self._init_gains))
                self._ema_loss = float(np.mean(self._init_losses))
                self._initialized = True
            else:
                # Aún no tenemos suficiente historia
                return self._empty_row(candle, iv_atm, net_gex)

        # ── EMA Wilder de gain y loss ──────────────────────────
        self._ema_gain = self._update_ema(gain, self._ema_gain, self._alpha_rsi)
        self._ema_loss = self._update_ema(loss, self._ema_loss, self._alpha_rsi)

        # ── Delta-RSI ──────────────────────────────────────────
        rs = self._ema_gain / max(self._ema_loss, 1e-9)
        delta_rsi = 100.0 - (100.0 / (1.0 + rs))

        # ── Línea de señal (EMA del Delta-RSI) ─────────────────
        self._ema_signal = self._update_ema(delta_rsi, self._ema_signal, self._alpha_signal)
        signal_line = self._ema_signal

        # ── Histograma ─────────────────────────────────────────
        histogram = delta_rsi - signal_line

        # Histograma anterior para detección de cruces
        prev_histogram = self._history[-1]["histogram"] if self._history else None

        # ── Zona y señal ───────────────────────────────────────
        zone = self._classify_zone(delta_rsi)
        signal, strength = self._generate_signal(
            delta_rsi,
            signal_line,
            histogram,
            zone,
            prev_histogram,
            flow
            or OptionsFlow(
                timestamp=candle.timestamp,
                ticker=self.ticker,
                call_buy_vol_delta=1,
                put_sell_vol_delta=0,
                put_buy_vol_delta=0,
                call_sell_vol_delta=1,
                net_premium=0,
                sweep_count=0,
                iv_atm=0,
                net_gex=0,
            ),
        )

        result = {
            "timestamp": candle.timestamp,
            "ticker": self.ticker,
            "close": candle.close,
            # Delta-RSI principal
            "delta_rsi": round(delta_rsi, 3),
            "signal_line": round(signal_line, 3),
            "histogram": round(histogram, 4),
            "ema_gain": round(self._ema_gain, 2),
            "ema_loss": round(self._ema_loss, 2),
            "rs": round(rs, 4),
            # Flujo de opciones
            "gain": round(gain, 2),
            "loss": round(loss, 2),
            "net_flow": round(net_flow, 2),
            "flow_ratio": round(flow_ratio, 4),
            "net_premium": round(net_premium, 2),
            "sweep_count": sweep_count,
            "iv_atm": round(iv_atm, 4),
            "net_gex": round(net_gex, 0),
            # Clasificación
            "zone": zone,
            "signal": signal,
            "strength": strength,
        }

        self._history.append(result)
        return result

    def _empty_row(self, candle: CandleBar, iv_atm: float, net_gex: float) -> dict[str, Any]:
        """Fila vacía durante el período de inicialización."""
        return {
            "timestamp": candle.timestamp,
            "ticker": self.ticker,
            "close": candle.close,
            "delta_rsi": np.nan,
            "signal_line": np.nan,
            "histogram": np.nan,
            "ema_gain": np.nan,
            "ema_loss": np.nan,
            "rs": np.nan,
            "gain": np.nan,
            "loss": np.nan,
            "net_flow": np.nan,
            "flow_ratio": np.nan,
            "net_premium": np.nan,
            "sweep_count": 0,
            "iv_atm": iv_atm,
            "net_gex": net_gex,
            "zone": "INITIALIZING",
            "signal": "NEUTRAL",
            "strength": 0,
        }

    def to_dataframe(self) -> pd.DataFrame:
        if not self._history:
            return pd.DataFrame()
        df = pd.DataFrame(self._history)
        df.set_index("timestamp", inplace=True)
        return df.dropna(subset=["delta_rsi"])

    def get_divergences(self) -> pd.DataFrame:
        """
        Detecta todas las divergencias entre precio y Delta-RSI
        sobre la historia acumulada.
        """
        df = self.to_dataframe()
        if df.empty or len(df) < 20:
            return pd.DataFrame()

        return self._divergence_detector.detect(
            price=df["close"],
            delta_rsi=df["delta_rsi"],
        )


# ─────────────────────────────────────────────
# 4. COMPARACIÓN CLÁSICO VS HÍBRIDO
# ─────────────────────────────────────────────


def compute_classic_rsi(closes: pd.Series, period: int = 14) -> pd.Series:
    """RSI clásico sobre precio para comparación."""
    rsi = ta.rsi(closes, length=period)
    return rsi if rsi is not None else pd.Series(np.nan, index=closes.index)


# ─────────────────────────────────────────────
# 5. GENERADOR DE DATOS DEMO
# ─────────────────────────────────────────────


def generate_demo_data(
    ticker: str = "AAPL",
    n: int = 390,
    base_price: float = 192.50,
    seed: int = 42,
) -> tuple[list[CandleBar], list[OptionsFlow]]:
    """
    Simula 4 fases con comportamientos deliberados de divergencia:

    Fase 1 (mañana):     Tendencia alcista con flujo institucional positivo
    Fase 2 (mediodía):   DIVERGENCIA BAJISTA — precio sube, flujo baja
    Fase 3 (tarde):      Distribución — precio cae con flujo confirmando
    Fase 4 (cierre):     DIVERGENCIA ALCISTA — precio baja, flujo sube
    """
    rng = np.random.default_rng(seed)
    start = pd.Timestamp("2025-01-15 09:30:00", tz="America/New_York")
    ts_range = pd.date_range(start, periods=n, freq="1min")

    # (barras, precio_trend, flow_trend, noise_p, noise_f, sweeps_mu)
    phases = [
        (97, 0.0005, 0.0006, 0.0006, 0.0008, 1.2),  # Alcista limpio
        (98, 0.0003, -0.0005, 0.0005, 0.0007, 0.8),  # DIV BAJISTA ←
        (98, -0.0004, -0.0004, 0.0008, 0.0009, 2.0),  # Distribución
        (97, -0.0002, 0.0005, 0.0007, 0.0008, 1.5),  # DIV ALCISTA ←
    ]

    candles, flows = [], []
    price = base_price
    flow_level = 100_000.0  # nivel base del flujo de opciones

    idx = 0
    for n_bars, p_trend, f_trend, p_noise, f_noise, sw_mu in phases:
        for _ in range(n_bars):
            if idx >= n:
                break
            ts = ts_range[idx]

            # ── Precio ────────────────────────────────────────
            ret = p_trend + rng.normal(0, p_noise)
            price *= 1 + ret
            spread = price * rng.uniform(0.0005, 0.0018)

            candles.append(
                CandleBar(
                    timestamp=ts,
                    ticker=ticker,
                    open=price * (1 - rng.uniform(0, 0.0002)),
                    high=price + spread * rng.uniform(0.2, 1.0),
                    low=price - spread * rng.uniform(0.2, 1.0),
                    close=price,
                    volume=float(rng.integers(80_000, 500_000)),
                )
            )

            # ── Flujo de opciones ─────────────────────────────
            flow_level *= 1 + f_trend + rng.normal(0, f_noise)
            flow_level = max(10_000, flow_level)

            if flow_level > 100_000:  # sesgo alcista
                call_buy = flow_level * rng.uniform(0.5, 0.8)
                put_sell = flow_level * rng.uniform(0.1, 0.3)
                put_buy = flow_level * rng.uniform(0.05, 0.2)
                call_sell = flow_level * rng.uniform(0.02, 0.1)
            else:  # sesgo bajista
                call_buy = flow_level * rng.uniform(0.05, 0.2)
                put_sell = flow_level * rng.uniform(0.02, 0.1)
                put_buy = flow_level * rng.uniform(0.5, 0.8)
                call_sell = flow_level * rng.uniform(0.1, 0.3)

            # Premium proporcional al flujo neto
            net_fl = (call_buy + put_sell) - (put_buy + call_sell)
            premium = net_fl * rng.uniform(0.8, 1.2)

            flows.append(
                OptionsFlow(
                    timestamp=ts,
                    ticker=ticker,
                    call_buy_vol_delta=float(call_buy),
                    put_sell_vol_delta=float(put_sell),
                    put_buy_vol_delta=float(put_buy),
                    call_sell_vol_delta=float(call_sell),
                    net_premium=float(premium),
                    sweep_count=int(rng.poisson(sw_mu)),
                    iv_atm=float(rng.uniform(0.12, 0.35)),
                    net_gex=float(rng.normal(500_000, 300_000)),
                )
            )
            idx += 1

    return candles, flows


# ─────────────────────────────────────────────
# 6. PIPELINE COMPLETO
# ─────────────────────────────────────────────


def run_delta_rsi_pipeline(
    ticker: str = "AAPL",
    n: int = 390,
    verbose: bool = True,
) -> tuple[pd.DataFrame, pd.DataFrame]:
    """
    Retorna (df_principal, df_divergencias).
    """
    logger.info(f"\n{'═'*64}")
    logger.info(f"  DELTA-RSI ENGINE  |  {ticker}  |  {n} velas 1m")
    logger.info(f"{'═'*64}")

    candles, flows = generate_demo_data(ticker, n)

    engine = DeltaRSIEngine(
        ticker=ticker,
        period=14,
        signal_period=9,
        premium_weight=True,
    )

    for candle, flow in zip(candles, flows, strict=False):
        engine.update(candle, flow)

    df = engine.to_dataframe()

    # RSI clásico para comparación
    df["classic_rsi"] = compute_classic_rsi(df["close"]).values

    # Divergencias
    div_df = engine.get_divergences()

    if verbose:
        _print_report(df, div_df, ticker)

    return df, div_df


def _print_report(df: pd.DataFrame, div_df: pd.DataFrame, ticker: str):
    last = df.iloc[-1]
    logger.info(f"\n── Resumen Delta-RSI {ticker} ──────────────────────────")
    logger.info(f"  Precio final       : ${last['close']:.2f}")
    logger.info(f"  Delta-RSI final    : {last['delta_rsi']:.2f}")
    logger.info(f"  Línea señal        : {last['signal_line']:.2f}")
    logger.info(f"  Histograma         : {last['histogram']:.4f}")
    logger.info(f"  RSI clásico final  : {last['classic_rsi']:.2f}")
    logger.info(f"  Divergencia actual : {last['delta_rsi'] - last['classic_rsi']:+.2f} pts")
    logger.info(f"  Zona institucional : {last['zone']}")
    logger.info(f"  EMA Gain           : {last['ema_gain']:,.0f}")
    logger.info(f"  EMA Loss           : {last['ema_loss']:,.0f}")
    logger.info(f"  Flujo neto         : {last['net_flow']:+,.0f}")

    logger.info("\n── Distribución de zonas ──")
    logger.info(df["zone"].value_counts().to_string())

    logger.info("\n── Distribución de señales ──")
    logger.info(df["signal"].value_counts().to_string())

    # Señales fuertes
    strong = df[df["strength"] >= 2]
    logger.info(f"\n── Señales fuerza ≥ 2 ({len(strong)} eventos) ──")
    if not strong.empty:
        cols = ["close", "delta_rsi", "classic_rsi", "histogram", "zone", "signal", "strength"]
        logger.info(strong[cols].tail(10).to_string())

    # Divergencias detectadas
    logger.info(f"\n── Divergencias detectadas: {len(div_df)} ──")
    if not div_df.empty:
        logger.info(
            div_df[
                ["timestamp", "type", "price_val", "rsi_val", "strength_label", "confirmed"]
            ].to_string()
        )

        # Resumen por tipo
        logger.info("\n── Conteo por tipo de divergencia ──")
        logger.info(div_df["type"].value_counts().to_string())
        strong_divs = div_df[div_df["strength"] >= 2]
        confirmed_divs = div_df[div_df["confirmed"] == True]
        logger.info(f"  Divergencias fuertes (≥2)  : {len(strong_divs)}")
        logger.info(f"  Divergencias confirmadas   : {len(confirmed_divs)}")

    # Correlación Delta-RSI vs RSI clásico
    corr = df["delta_rsi"].corr(df["classic_rsi"])
    logger.info(f"\n── Correlación Delta-RSI / RSI clásico : {corr:.4f}")
    logger.info("   (< 0.7 = señales independientes, > 0.7 = redundantes)")

    # Estadísticas de sweeps
    logger.info("\n── Estadísticas de sweeps ──")
    logger.info(f"  Sweeps totales     : {df['sweep_count'].sum():.0f}")
    logger.info(f"  Sweeps promedio/m  : {df['sweep_count'].mean():.2f}")
    logger.info(f"  Minutos con ≥3 sw  : {(df['sweep_count'] >= 3).sum()}")
    logger.info(f"\n{'═'*64}")


# ─────────────────────────────────────────────
# 7. INTEGRACIÓN PRODUCCIÓN
# ─────────────────────────────────────────────


class DeltaRSILive:
    """
    Wrapper para integración con BingX WebSocket + Massive API.

    Uso:
        engine = DeltaRSILive("AAPL")

        def on_candle_and_flow(raw_candle, raw_flow):
            candle = DeltaRSILive.parse_bingx_candle(raw_candle)
            flow   = DeltaRSILive.parse_massive_flow("AAPL", raw_flow)
            result = engine.core.update(candle, flow)
            engine.on_signal(result)

        # Al final de la sesión o cada N velas:
        div_df = engine.core.get_divergences()
        engine.on_divergences(div_df)
    """

    def __init__(self, ticker: str, **kwargs):
        self.ticker = ticker
        self.core = DeltaRSIEngine(ticker=ticker, **kwargs)

    @staticmethod
    def parse_bingx_candle(raw: dict) -> CandleBar:
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
    def parse_massive_flow(ticker: str, raw: dict) -> OptionsFlow:
        """
        Campos esperados de Massive API (flujo de opciones ejecutadas por minuto):
        {
            "callBuyVolDelta":  45000,   // calls agresivas compradas × Delta
            "putSellVolDelta":  12000,   // puts vendidas × |Delta|
            "putBuyVolDelta":   8000,    // puts agresivas compradas × |Delta|
            "callSellVolDelta": 3000,    // calls vendidas × Delta
            "netPremium":       125000,  // USD netos pagados
            "sweepCount":       2,       // sweeps del minuto
            "ivAtm":            0.22,
            "netGex":           1500000
        }
        """
        return OptionsFlow(
            timestamp=pd.Timestamp.now(tz="UTC"),
            ticker=ticker,
            call_buy_vol_delta=float(raw.get("callBuyVolDelta", 0)),
            put_sell_vol_delta=float(raw.get("putSellVolDelta", 0)),
            put_buy_vol_delta=float(raw.get("putBuyVolDelta", 0)),
            call_sell_vol_delta=float(raw.get("callSellVolDelta", 0)),
            net_premium=float(raw.get("netPremium", 0)),
            sweep_count=int(raw.get("sweepCount", 0)),
            iv_atm=float(raw.get("ivAtm", 0.20)),
            net_gex=float(raw.get("netGex", 0)),
        )

    SIGNAL_PRIORITY = {
        "FLOW_EXHAUSTION_LONG": 5,
        "FLOW_EXHAUSTION_SHORT": 5,
        "SWEEP_SURGE_LONG": 4,
        "SWEEP_SURGE_SHORT": 4,
        "MOMENTUM_LONG": 2,
        "MOMENTUM_SHORT": 2,
        "NEUTRAL": 0,
    }

    def on_signal(self, result: dict):
        p = self.SIGNAL_PRIORITY.get(result["signal"], 0)
        if p >= 2:
            logger.info(
                f"[{result['timestamp']}] {self.ticker:5s} | "
                f"P{p} {result['signal']:24s} | "
                f"${result['close']:.2f} | "
                f"ΔRSI={result['delta_rsi']:.1f} | "
                f"Hist={result['histogram']:+.3f} | "
                f"{result['zone']}"
            )

    def on_divergences(self, div_df: pd.DataFrame):
        if div_df.empty:
            return
        confirmed = div_df[div_df["confirmed"] == True]
        for _, row in confirmed.iterrows():
            logger.info(
                f"[DIVERGENCIA] {self.ticker} | "
                f"{row['type']:16s} | "
                f"Fuerza: {row['strength_label']:6s} | "
                f"Precio: ${row['price_val']:.2f} | "
                f"Delta-RSI: {row['rsi_val']:.1f}"
            )


# ─────────────────────────────────────────────
# 8. EJECUCIÓN DEMO
# ─────────────────────────────────────────────

if __name__ == "__main__":
    TICKERS = ["AAPL", "MSFT", "PLTR", "TSLA", "GOOGL"]
    all_divs = {}

    for ticker in TICKERS:
        df, div_df = run_delta_rsi_pipeline(ticker=ticker, n=390, verbose=True)
        all_divs[ticker] = div_df
        df.to_csv(f"/tmp/delta_rsi_{ticker.lower()}.csv")
        if not div_df.empty:
            div_df.to_csv(f"/tmp/divergencias_{ticker.lower()}.csv")

    # Resumen cross-ticker de divergencias
    logger.info(f"\n{'═'*64}")
    logger.info("  RESUMEN CROSS-TICKER DE DIVERGENCIAS")
    logger.info(f"{'═'*64}")
    for ticker, div_df in all_divs.items():
        if div_df.empty:
            logger.info(f"  {ticker:5s}: sin divergencias detectadas")
        else:
            strong = div_df[div_df["strength"] >= 2]
            conf = div_df[div_df["confirmed"] == True]
            logger.info(
                f"  {ticker:5s}: {len(div_df):2d} total | "
                f"{len(strong):2d} fuertes | "
                f"{len(conf):2d} confirmadas"
            )

    logger.info("\n✓ Delta-RSI completado para los 5 proxies BingX.")
