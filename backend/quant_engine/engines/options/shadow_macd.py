"""
Shadow MACD — MACD sobre Net Dealer Delta Exposure (NDDE)
══════════════════════════════════════════════════════════
El MACD clásico aplica EMA rápida − EMA lenta sobre el PRECIO.
El Shadow MACD aplica exactamente la misma matemática sobre la
exposición neta de delta de los Market Makers (NDDE):

    NDDE(t)         = Σ [ Delta(strike) × OI(strike) × 100 ] para toda la cadena
                      (positivo = dealers long delta, deben VENDER para cubrirse)
                      (negativo = dealers short delta, deben COMPRAR para cubrirse)

    MACD_shadow     = EMA12(NDDE) − EMA26(NDDE)
    Signal_shadow   = EMA9(MACD_shadow)
    Histogram       = MACD_shadow − Signal_shadow

El histograma mide la ACELERACIÓN del posicionamiento de los dealers.
    Histograma decreciendo en máximos de precio → DISTRIBUCIÓN (dealers desarmando)
    Histograma creciendo en mínimos de precio   → ACUMULACIÓN (dealers cubriendo)

Fuentes:
    BingX WebSocket  → velas 1m OHLCV
    Massive API      → cadena de opciones completa por strike: Delta, OI, Gamma

Compatibilidad: pandas >= 2.0 · numpy >= 1.24 · pandas-ta
"""

import warnings
from dataclasses import dataclass, field

import numpy as np
import pandas as pd
import pandas_ta as ta

warnings.filterwarnings("ignore")


# ─────────────────────────────────────────────
# 1. ESTRUCTURAS DE DATOS
# ─────────────────────────────────────────────


@dataclass
class OptionStrike:
    """Un strike individual de la cadena de opciones."""

    strike: float
    expiry: str  # "2025-01-17"
    call_delta: float  # Delta de la call (0 a 1)
    put_delta: float  # Delta de la put (-1 a 0)
    call_oi: int  # Open Interest calls
    put_oi: int  # Open Interest puts
    call_gamma: float  # Gamma de la call
    put_gamma: float  # Gamma de la put
    iv: float  # IV de este strike


@dataclass
class OptionsChainSnapshot:
    """
    Snapshot completo de la cadena de opciones en un momento t.
    Se obtiene de Massive API cada 1 minuto.
    """

    timestamp: pd.Timestamp
    ticker: str
    spot_price: float
    strikes: list[OptionStrike]

    # Calculados automáticamente
    ndde: float = field(init=False)  # Net Dealer Delta Exposure
    gex: float = field(init=False)  # Gamma Exposure total
    charm_flow: float = field(init=False)  # dDelta/dt proxy
    ndde_calls: float = field(init=False)  # NDDE solo de calls
    ndde_puts: float = field(init=False)  # NDDE solo de puts
    put_call_delta_ratio: float = field(init=False)

    CONTRACT_SIZE: int = 100

    def __post_init__(self):
        self._compute_exposures()

    def _compute_exposures(self):
        """
        NDDE = Σ [ Delta(k) × OI(k) × 100 ] para todos los strikes k

        Convención de dealer:
          Si un cliente COMPRA una call → el dealer está SHORT call
          → dealer tiene delta negativo → debe COMPRAR acciones para cubrirse
          → contribución al NDDE es NEGATIVA (dealer necesita comprar)

          Si un cliente COMPRA una put → el dealer está SHORT put
          → dealer tiene delta positivo → debe VENDER acciones para cubrirse
          → contribución al NDDE es POSITIVA (dealer necesita vender)

        NDDE > 0: dealers en conjunto necesitan VENDER → presión bajista
        NDDE < 0: dealers en conjunto necesitan COMPRAR → presión alcista
        """
        ndde_total = 0.0
        ndde_calls = 0.0
        ndde_puts = 0.0
        gex_total = 0.0

        for s in self.strikes:
            # Dealer está SHORT las opciones que el cliente compró
            # Call: dealer short → delta negativo del dealer
            call_contribution = -s.call_delta * s.call_oi * self.CONTRACT_SIZE
            # Put: dealer short → |put_delta| positivo del dealer
            put_contribution = -s.put_delta * s.put_oi * self.CONTRACT_SIZE

            ndde_calls += call_contribution
            ndde_puts += put_contribution
            ndde_total += call_contribution + put_contribution

            # GEX = Gamma × OI × 100 (mismo signo que NDDE calls)
            gex_total += s.call_gamma * s.call_oi * self.CONTRACT_SIZE
            gex_total -= s.put_gamma * s.put_oi * self.CONTRACT_SIZE

        self.ndde = ndde_total
        self.ndde_calls = ndde_calls
        self.ndde_puts = ndde_puts
        self.gex = gex_total

        # Put/Call delta ratio (>1 = más presión de puts)
        total_abs = abs(ndde_calls) + abs(ndde_puts)
        self.put_call_delta_ratio = abs(ndde_puts) / abs(ndde_calls) if abs(ndde_calls) > 0 else 1.0

        # Charm flow proxy: cambio esperado de NDDE por paso del tiempo
        # (negativo cuando calls OTM pierden delta → dealers deben comprar menos)
        atm_strikes = [
            s for s in self.strikes if abs(s.strike - self.spot_price) / self.spot_price < 0.02
        ]
        if atm_strikes:
            self.charm_flow = sum(
                s.call_gamma * s.call_oi * self.CONTRACT_SIZE * -0.1 for s in atm_strikes
            )
        else:
            self.charm_flow = 0.0


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
# 2. DETECTOR DE DISTRIBUCIÓN / ACUMULACIÓN
# ─────────────────────────────────────────────


class DistributionDetector:
    """
    Detecta fases de distribución y acumulación analizando la
    relación entre el histograma del Shadow MACD y el precio.

    Patrones detectados:

    DISTRIBUCIÓN:
        El histograma del Shadow MACD se vuelve negativo (o decrece)
        mientras el precio está en máximos. Los dealers están
        DESARMANDO su cobertura (Charm flow negativo) porque anticipan
        que el precio va a caer y necesitarán menos delta hedge.

    ACUMULACIÓN:
        El histograma se vuelve positivo (o crece) mientras el precio
        está en mínimos. Los dealers están AUMENTANDO su cobertura
        (comprando acciones) porque el flujo de puts compradas aumentó.

    DIVERGENCIA MACD-PRECIO:
        El precio hace un nuevo extremo pero el Shadow MACD no lo confirma.
        Idéntico al análisis de divergencias del RSI pero sobre NDDE.
    """

    def __init__(self, pivot_window: int = 5, histogram_smooth: int = 3):
        self.pivot_window = pivot_window
        self.histogram_smooth = histogram_smooth

    def analyze(
        self,
        df: pd.DataFrame,
    ) -> dict:
        """
        Análisis completo de distribución/acumulación sobre el DataFrame
        del Shadow MACD.

        Requiere columnas: close, macd, signal, histogram, ndde

        Returns dict con:
            current_phase    : fase actual del mercado
            phase_duration   : velas en la fase actual
            distribution_zones : índices donde se detectó distribución
            accumulation_zones : índices donde se detectó acumulación
            divergences      : divergencias MACD-precio
            histogram_trend  : tendencia reciente del histograma
        """
        if len(df) < self.pivot_window * 2 + 5:
            return {"current_phase": "INSUFFICIENT_DATA"}

        # Suavizar histograma para reducir ruido
        hist_smooth = df["histogram"].rolling(self.histogram_smooth).mean()

        # ── Fase actual ────────────────────────────────────────
        current_phase = self._classify_current_phase(df, hist_smooth)

        # ── Duración de la fase ────────────────────────────────
        phase_duration = self._get_phase_duration(df, hist_smooth)

        # ── Zonas de distribución ─────────────────────────────
        dist_zones = self._find_distribution_zones(df, hist_smooth)

        # ── Zonas de acumulación ──────────────────────────────
        acc_zones = self._find_accumulation_zones(df, hist_smooth)

        # ── Divergencias ──────────────────────────────────────
        divergences = self._find_divergences(df)

        # ── Tendencia del histograma (últimas 5 velas) ─────────
        recent_hist = hist_smooth.dropna().tail(5)
        if len(recent_hist) >= 2:
            hist_trend_slope = (recent_hist.iloc[-1] - recent_hist.iloc[0]) / len(recent_hist)
        else:
            hist_trend_slope = 0.0

        return {
            "current_phase": current_phase,
            "phase_duration": phase_duration,
            "distribution_zones": dist_zones,
            "accumulation_zones": acc_zones,
            "divergences": divergences,
            "histogram_trend": round(hist_trend_slope, 2),
            "histogram_slope_label": (
                "ACELERANDO_ALCISTA" if hist_trend_slope > 0 else "ACELERANDO_BAJISTA"
            ),
        }

    def _classify_current_phase(self, df: pd.DataFrame, hist_smooth: pd.Series) -> str:
        """Clasifica la fase actual mirando las últimas N velas."""
        last_hist = hist_smooth.dropna().tail(5)
        last_price = df["close"].tail(5)
        last_ndde = df["ndde"].tail(5)

        if last_hist.empty:
            return "UNKNOWN"

        hist_now = last_hist.iloc[-1]
        hist_prev = last_hist.iloc[0]
        price_now = last_price.iloc[-1]
        price_prev = last_price.iloc[0]
        ndde_now = last_ndde.iloc[-1]

        # Histograma negativo y cayendo en precio alto → distribución activa
        if hist_now < 0 and hist_now < hist_prev and price_now >= price_prev:
            return "DISTRIBUCION_ACTIVA"

        # Histograma negativo pero estabilizándose → distribución terminando
        if hist_now < 0 and hist_now >= hist_prev:
            return "DISTRIBUCION_TERMINANDO"

        # Histograma positivo y subiendo en precio bajo → acumulación activa
        if hist_now > 0 and hist_now > hist_prev and price_now <= price_prev:
            return "ACUMULACION_ACTIVA"

        # NDDE muy negativo = dealers comprando masivamente
        if ndde_now < -1_000_000:
            return "COBERTURA_MASIVA_DEALERS"

        # NDDE muy positivo = dealers vendiendo masivamente
        if ndde_now > 1_000_000:
            return "VENTA_MASIVA_DEALERS"

        if hist_now > 0:
            return "MOMENTUM_ALCISTA"
        if hist_now < 0:
            return "MOMENTUM_BAJISTA"

        return "TRANSICION"

    def _get_phase_duration(self, df: pd.DataFrame, hist_smooth: pd.Series) -> int:
        """Cuenta cuántas velas consecutivas lleva la fase actual."""
        if hist_smooth.empty:
            return 0
        current_sign = np.sign(hist_smooth.dropna().iloc[-1])
        count = 0
        for val in reversed(hist_smooth.dropna().values):
            if np.sign(val) == current_sign:
                count += 1
            else:
                break
        return count

    def _find_distribution_zones(self, df: pd.DataFrame, hist_smooth: pd.Series) -> list[dict]:
        """
        Zona de distribución:
            1. El histograma estaba positivo y cruza a negativo
            2. El precio está en el cuartil superior de la sesión
        """
        zones = []
        price_75 = df["close"].quantile(0.75)

        for i in range(1, len(hist_smooth)):
            if hist_smooth.iloc[i - 1] > 0 and hist_smooth.iloc[i] < 0:
                if df["close"].iloc[i] >= price_75:
                    zones.append(
                        {
                            "index": i,
                            "timestamp": (
                                df.index[i]
                                if hasattr(df.index[i], "isoformat")
                                else str(df.index[i])
                            ),
                            "price": round(df["close"].iloc[i], 4),
                            "ndde": round(df["ndde"].iloc[i], 0),
                            "histogram": round(hist_smooth.iloc[i], 4),
                            "type": "DISTRIBUCION",
                        }
                    )
        return zones

    def _find_accumulation_zones(self, df: pd.DataFrame, hist_smooth: pd.Series) -> list[dict]:
        """
        Zona de acumulación:
            1. El histograma estaba negativo y cruza a positivo
            2. El precio está en el cuartil inferior de la sesión
        """
        zones = []
        price_25 = df["close"].quantile(0.25)

        for i in range(1, len(hist_smooth)):
            if hist_smooth.iloc[i - 1] < 0 and hist_smooth.iloc[i] > 0:
                if df["close"].iloc[i] <= price_25:
                    zones.append(
                        {
                            "index": i,
                            "timestamp": (
                                df.index[i]
                                if hasattr(df.index[i], "isoformat")
                                else str(df.index[i])
                            ),
                            "price": round(df["close"].iloc[i], 4),
                            "ndde": round(df["ndde"].iloc[i], 0),
                            "histogram": round(hist_smooth.iloc[i], 4),
                            "type": "ACUMULACION",
                        }
                    )
        return zones

    def _find_divergences(self, df: pd.DataFrame) -> list[dict]:
        """
        Divergencias entre precio y Shadow MACD (línea MACD, no histograma).
        Misma lógica que divergencias de RSI pero sobre MACD.
        """
        divergences = []
        w = self.pivot_window
        price = df["close"]
        macd = df["macd"]
        n = len(df)

        for i in range(w, n - w):
            # Pivot high de precio
            if price.iloc[i] == price.iloc[i - w : i + w + 1].max():
                # Buscar pivot high anterior
                for j in range(i - w - 1, max(0, i - 50), -1):
                    if price.iloc[j] == price.iloc[max(0, j - w) : j + w + 1].max():
                        # Regular Bear: precio sube + MACD baja
                        if price.iloc[i] > price.iloc[j] and macd.iloc[i] < macd.iloc[j]:
                            divergences.append(
                                {
                                    "type": "REGULAR_BEAR",
                                    "idx_curr": i,
                                    "idx_prev": j,
                                    "price_curr": round(price.iloc[i], 4),
                                    "price_prev": round(price.iloc[j], 4),
                                    "macd_curr": round(macd.iloc[i], 2),
                                    "macd_prev": round(macd.iloc[j], 2),
                                    "interpretation": "Dealers desarmando cobertura en máximos → distribución",
                                }
                            )
                        break

            # Pivot low de precio
            if price.iloc[i] == price.iloc[i - w : i + w + 1].min():
                for j in range(i - w - 1, max(0, i - 50), -1):
                    if price.iloc[j] == price.iloc[max(0, j - w) : j + w + 1].min():
                        # Regular Bull: precio baja + MACD sube
                        if price.iloc[i] < price.iloc[j] and macd.iloc[i] > macd.iloc[j]:
                            divergences.append(
                                {
                                    "type": "REGULAR_BULL",
                                    "idx_curr": i,
                                    "idx_prev": j,
                                    "price_curr": round(price.iloc[i], 4),
                                    "price_prev": round(price.iloc[j], 4),
                                    "macd_curr": round(macd.iloc[i], 2),
                                    "macd_prev": round(macd.iloc[j], 2),
                                    "interpretation": "Dealers aumentando cobertura en mínimos → acumulación",
                                }
                            )
                        break

        return divergences


# ─────────────────────────────────────────────
# 3. MOTOR SHADOW MACD
# ─────────────────────────────────────────────


class ShadowMACDEngine:
    """
    MACD aplicado sobre el NDDE (Net Dealer Delta Exposure).

    MACD_shadow   = EMA12(NDDE) − EMA26(NDDE)
    Signal_shadow = EMA9(MACD_shadow)
    Histogram     = MACD_shadow − Signal_shadow

    Adicionalmente calcula el Classic MACD sobre precio para
    comparación y detección de divergencias cruzadas.

    Args:
        ticker:       Símbolo del proxy
        fast:         Período EMA rápida del NDDE. Default 12.
        slow:         Período EMA lenta del NDDE. Default 26.
        signal:       Período EMA de señal. Default 9.
        ndde_smooth:  Suavizado del NDDE crudo antes del MACD. Default 3.
    """

    def __init__(
        self,
        ticker: str,
        fast: int = 12,
        slow: int = 26,
        signal: int = 9,
        ndde_smooth: int = 3,
    ):
        self.ticker = ticker
        self.fast = fast
        self.slow = slow
        self.signal_period = signal
        self.ndde_smooth = ndde_smooth

        # Alphas EMA
        self._af = 2.0 / (fast + 1)
        self._as = 2.0 / (slow + 1)
        self._ag = 2.0 / (signal + 1)

        # Estado EMAs del NDDE
        self._ema_fast: float | None = None
        self._ema_slow: float | None = None
        self._ema_signal: float | None = None

        # Buffer para suavizado del NDDE crudo
        self._ndde_buffer: list[float] = []

        # Historia
        self._history: list[dict] = []

        # Detector de distribución
        self._dist_detector = DistributionDetector()

    # ── EMA ────────────────────────────────────────────────────
    def _ema(self, val: float, prev: float | None, alpha: float) -> float:
        return val if prev is None else alpha * val + (1 - alpha) * prev

    # ── Señal de trading ───────────────────────────────────────
    def _generate_signal(
        self,
        macd: float,
        signal_line: float,
        histogram: float,
        prev_histogram: float | None,
        ndde: float,
        charm_flow: float,
        price: float,
        prev_price: float | None,
    ) -> tuple[str, int, str]:
        """
        Señales del Shadow MACD:

        DISTRIBUTION_TOP     : histograma bajando en precio alto + Charm negativo
        ACCUMULATION_BOTTOM  : histograma subiendo en precio bajo + NDDE negativo
        MACD_CROSS_BULL      : cruce alcista de señal (MACD cruza arriba de Signal)
        MACD_CROSS_BEAR      : cruce bajista de señal
        MOMENTUM_ACCELERATION: histograma creciendo (aceleración del momentum)
        MOMENTUM_DECELERATION: histograma decreciendo (pérdida de momentum)
        NDDE_EXTREME_LONG    : NDDE muy negativo (dealers comprando masivo)
        NDDE_EXTREME_SHORT   : NDDE muy positivo (dealers vendiendo masivo)
        """
        cross_bull = prev_histogram is not None and prev_histogram < 0 and histogram >= 0
        cross_bear = prev_histogram is not None and prev_histogram > 0 and histogram <= 0

        hist_decelerating = (
            prev_histogram is not None and histogram < prev_histogram and histogram > 0
        )
        hist_accelerating_down = (
            prev_histogram is not None and histogram > prev_histogram and histogram < 0
        )

        price_rising = prev_price is not None and price > prev_price
        price_falling = prev_price is not None and price < prev_price

        # ── Distribución (más importante para scalping) ────────
        if hist_decelerating and price_rising and charm_flow < 0:
            interpretation = (
                "Dealers desarmando delta hedge (Charm flow neg) "
                "mientras precio sube → distribución activa"
            )
            return "DISTRIBUTION_TOP", 3, interpretation

        # ── Acumulación ────────────────────────────────────────
        if hist_accelerating_down and price_falling and ndde < -500_000:
            interpretation = (
                "Dealers aumentando cobertura compradora (NDDE neg) "
                "mientras precio baja → acumulación institucional"
            )
            return "ACCUMULATION_BOTTOM", 3, interpretation

        # ── Extremos de NDDE ───────────────────────────────────
        if ndde < -2_000_000:
            return "NDDE_EXTREME_LONG", 3, "Dealers en cobertura masiva → presión compradora"
        if ndde > 2_000_000:
            return "NDDE_EXTREME_SHORT", 3, "Dealers vendiendo masivo → presión bajista"

        # ── Cruces de señal ────────────────────────────────────
        if cross_bull:
            return "MACD_CROSS_BULL", 2, "MACD Shadow cruza arriba de señal → momentum alcista"
        if cross_bear:
            return "MACD_CROSS_BEAR", 2, "MACD Shadow cruza abajo de señal → momentum bajista"

        # ── Aceleración / deceleración ─────────────────────────
        if hist_decelerating:
            return "MOMENTUM_DECELERATION", 1, "Histograma perdiendo fuerza alcista"
        if hist_accelerating_down:
            return "MOMENTUM_DECELERATION", 1, "Histograma perdiendo fuerza bajista"

        if prev_histogram is not None and histogram > prev_histogram and histogram > 0:
            return "MOMENTUM_ACCELERATION", 1, "Histograma acelerando alcista"
        if prev_histogram is not None and histogram < prev_histogram and histogram < 0:
            return "MOMENTUM_ACCELERATION", 1, "Histograma acelerando bajista"

        return "NEUTRAL", 0, "Sin señal clara"

    # ── Tick principal ──────────────────────────────────────────
    def update(
        self,
        candle: CandleBar,
        chain: OptionsChainSnapshot | None = None,
    ) -> dict:

        # ── NDDE crudo o fallback ──────────────────────────────
        if chain is not None:
            ndde_raw = chain.ndde
            gex = chain.gex
            charm_flow = chain.charm_flow
            ndde_calls = chain.ndde_calls
            ndde_puts = chain.ndde_puts
            pc_ratio = chain.put_call_delta_ratio
        else:
            ndde_raw = gex = charm_flow = 0.0
            ndde_calls = ndde_puts = 0.0
            pc_ratio = 1.0

        # ── Suavizado del NDDE (reduce spikes de snapshot) ────
        self._ndde_buffer.append(ndde_raw)
        if len(self._ndde_buffer) > self.ndde_smooth:
            self._ndde_buffer.pop(0)
        ndde = float(np.mean(self._ndde_buffer))

        # ── EMAs sobre NDDE ────────────────────────────────────
        self._ema_fast = self._ema(ndde, self._ema_fast, self._af)
        self._ema_slow = self._ema(ndde, self._ema_slow, self._as)

        macd_val = self._ema_fast - self._ema_slow

        self._ema_signal = self._ema(macd_val, self._ema_signal, self._ag)
        signal_val = self._ema_signal

        histogram = macd_val - signal_val

        # ── Datos anteriores ───────────────────────────────────
        prev = self._history[-1] if self._history else None
        prev_histogram = prev["histogram"] if prev else None
        prev_price = prev["close"] if prev else None

        # ── Señal ──────────────────────────────────────────────
        signal_name, strength, interpretation = self._generate_signal(
            macd=macd_val,
            signal_line=signal_val,
            histogram=histogram,
            prev_histogram=prev_histogram,
            ndde=ndde,
            charm_flow=charm_flow,
            price=candle.close,
            prev_price=prev_price,
        )

        # ── Classic MACD para comparación ─────────────────────
        # (se calcula sobre el DataFrame al final, no tick a tick)

        result = {
            "timestamp": candle.timestamp,
            "ticker": self.ticker,
            "close": candle.close,
            "typical_price": candle.typical_price,
            # Shadow MACD sobre NDDE
            "ndde": round(ndde, 0),
            "ndde_raw": round(ndde_raw, 0),
            "ndde_calls": round(ndde_calls, 0),
            "ndde_puts": round(ndde_puts, 0),
            "ema_fast": round(self._ema_fast, 0),
            "ema_slow": round(self._ema_slow, 0),
            "macd": round(macd_val, 2),
            "signal": round(signal_val, 2),
            "histogram": round(histogram, 2),
            # Métricas de opciones
            "gex": round(gex, 0),
            "charm_flow": round(charm_flow, 2),
            "put_call_ratio": round(pc_ratio, 4),
            # Señal
            "signal_name": signal_name,
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

        # Classic MACD sobre precio para comparación
        macd_classic = ta.macd(df["close"], fast=12, slow=26, signal=9)
        if macd_classic is not None and not macd_classic.empty:
            df["classic_macd"] = macd_classic.iloc[:, 0].values
            df["classic_signal"] = macd_classic.iloc[:, 1].values
            df["classic_histogram"] = macd_classic.iloc[:, 2].values

        return df

    def get_distribution_analysis(self) -> dict:
        df = self.to_dataframe()
        if df.empty:
            return {}
        return self._dist_detector.analyze(df)


# ─────────────────────────────────────────────
# 4. GENERADOR DE DATOS DEMO
# ─────────────────────────────────────────────


def _make_chain(
    spot: float,
    ts: pd.Timestamp,
    ticker: str,
    ndde_bias: float,
    gex_level: float,
    rng: np.random.Generator,
) -> OptionsChainSnapshot:
    """Genera una cadena de opciones sintética alrededor del spot."""
    strikes_data = []
    for pct in np.arange(-0.06, 0.07, 0.01):
        k = round(spot * (1 + pct), 2)
        moneyness = pct  # negativo = OTM put, positivo = OTM call

        # Delta aproximado (BSM simplificado)
        call_d = max(0.01, min(0.99, 0.5 - moneyness * 5))
        put_d = call_d - 1.0

        # OI sesgado por ndde_bias
        base_oi = int(rng.integers(500, 5000))
        if ndde_bias > 0:  # sesgo alcista → más calls
            call_oi = int(base_oi * rng.uniform(1.2, 1.8))
            put_oi = int(base_oi * rng.uniform(0.5, 0.9))
        else:  # sesgo bajista → más puts
            call_oi = int(base_oi * rng.uniform(0.5, 0.9))
            put_oi = int(base_oi * rng.uniform(1.2, 1.8))

        gamma = max(0.001, 0.05 - abs(moneyness) * 0.4)

        strikes_data.append(
            OptionStrike(
                strike=k,
                expiry="2025-01-17",
                call_delta=call_d,
                put_delta=put_d,
                call_oi=call_oi,
                put_oi=put_oi,
                call_gamma=gamma,
                put_gamma=gamma,
                iv=float(rng.uniform(0.15, 0.40)),
            )
        )

    return OptionsChainSnapshot(
        timestamp=ts,
        ticker=ticker,
        spot_price=spot,
        strikes=strikes_data,
    )


def generate_demo_data(
    ticker: str = "AAPL",
    n: int = 390,
    base_price: float = 192.50,
    seed: int = 42,
) -> tuple[list[CandleBar], list[OptionsChainSnapshot]]:
    """
    4 fases diseñadas para mostrar claramente distribución y acumulación:
        Fase 1: precio sube, NDDE positivo (dealers vendiendo) → distribución
        Fase 2: precio en techo, NDDE cae fuertemente → señal de distribución
        Fase 3: precio baja, NDDE se vuelve negativo → acumulación
        Fase 4: precio rebota, NDDE confirma alcista
    """
    rng = np.random.default_rng(seed)
    start = pd.Timestamp("2025-01-15 09:30:00", tz="America/New_York")
    ts_range = pd.date_range(start, periods=n, freq="1min")

    # (barras, p_trend, ndde_bias, gex_level, noise)
    phases = [
        (97, 0.0005, 0.6, 1.5e6, 0.0006),  # Subida + dealers vendiendo
        (98, 0.0001, -0.8, -0.5e6, 0.0004),  # Techo + NDDE cae (dist)
        (98, -0.0004, -0.5, -1.2e6, 0.0008),  # Caída + acumulación dealers
        (97, 0.0003, 0.4, 0.8e6, 0.0005),  # Rebote + confirmación
    ]

    candles, chains = [], []
    price = base_price
    idx = 0

    for n_bars, p_trend, ndde_bias, gex_level, noise in phases:
        for _ in range(n_bars):
            if idx >= n:
                break
            ts = ts_range[idx]
            ret = p_trend + rng.normal(0, noise)
            price *= 1 + ret
            spread = price * rng.uniform(0.0005, 0.002)

            candles.append(
                CandleBar(
                    timestamp=ts,
                    ticker=ticker,
                    open=price * (1 - rng.uniform(0, 0.0002)),
                    high=price + spread * rng.uniform(0.2, 1.0),
                    low=price - spread * rng.uniform(0.2, 1.0),
                    close=price,
                    volume=float(rng.integers(60_000, 450_000)),
                )
            )

            chain = _make_chain(
                spot=price,
                ts=ts,
                ticker=ticker,
                ndde_bias=ndde_bias + rng.normal(0, 0.15),
                gex_level=gex_level + rng.normal(0, abs(gex_level) * 0.1),
                rng=rng,
            )
            chains.append(chain)
            idx += 1

    return candles, chains


# ─────────────────────────────────────────────
# 5. PIPELINE COMPLETO
# ─────────────────────────────────────────────


def run_shadow_macd_pipeline(
    ticker: str = "AAPL",
    n: int = 390,
    verbose: bool = True,
) -> pd.DataFrame:

    print(f"\n{'═'*66}")
    print(f"  SHADOW MACD ENGINE  |  {ticker}  |  {n} velas 1m")
    print(f"{'═'*66}")

    candles, chains = generate_demo_data(ticker, n)
    engine = ShadowMACDEngine(ticker=ticker)

    for candle, chain in zip(candles, chains, strict=False):
        engine.update(candle, chain)

    df = engine.to_dataframe()
    analysis = engine.get_distribution_analysis()

    if verbose:
        _print_report(df, analysis, ticker)

    return df


def _print_report(df: pd.DataFrame, analysis: dict, ticker: str):
    last = df.iloc[-1]

    print(f"\n── Shadow MACD {ticker} — estado actual ──────────────────")
    print(f"  Precio final       : ${last['close']:.2f}")
    print(f"  NDDE actual        : {last['ndde']:+,.0f}")
    print(f"  NDDE calls         : {last['ndde_calls']:+,.0f}")
    print(f"  NDDE puts          : {last['ndde_puts']:+,.0f}")
    print(f"  EMA rápida (12)    : {last['ema_fast']:+,.0f}")
    print(f"  EMA lenta (26)     : {last['ema_slow']:+,.0f}")
    print(f"  MACD Shadow        : {last['macd']:+,.2f}")
    print(f"  Línea señal        : {last['signal']:+,.2f}")
    print(f"  Histograma         : {last['histogram']:+,.2f}")
    print(f"  GEX actual         : {last['gex']:+,.0f}")
    print(f"  Charm flow         : {last['charm_flow']:+,.2f}")
    print(f"  Put/Call ratio     : {last['put_call_ratio']:.3f}")

    if "classic_macd" in df.columns:
        print(f"  Classic MACD       : {last['classic_macd']:+.4f}")
        corr = df["macd"].corr(df["classic_macd"].dropna())
        print(f"  Correlación NDDE/precio MACD: {corr:.4f}")

    print("\n── Análisis de distribución/acumulación ──")
    print(f"  Fase actual        : {analysis.get('current_phase', 'N/A')}")
    print(f"  Duración fase      : {analysis.get('phase_duration', 0)} velas")
    print(f"  Tendencia hist.    : {analysis.get('histogram_trend', 0):+.2f}")
    print(f"  Dirección          : {analysis.get('histogram_slope_label', 'N/A')}")

    dist_z = analysis.get("distribution_zones", [])
    acc_z = analysis.get("accumulation_zones", [])
    divs = analysis.get("divergences", [])

    print("\n── Zonas detectadas ──")
    print(f"  Distribución       : {len(dist_z)} zonas")
    for z in dist_z[-3:]:
        print(f"    → ${z['price']:.2f} | NDDE {z['ndde']:+,.0f} | Hist {z['histogram']:+.2f}")
    print(f"  Acumulación        : {len(acc_z)} zonas")
    for z in acc_z[-3:]:
        print(f"    → ${z['price']:.2f} | NDDE {z['ndde']:+,.0f} | Hist {z['histogram']:+.2f}")

    print(f"\n── Divergencias MACD-precio: {len(divs)} ──")
    for d in divs[-5:]:
        print(
            f"  {d['type']:14s} | "
            f"P: ${d['price_curr']:.2f}→${d['price_prev']:.2f} | "
            f"MACD: {d['macd_curr']:+.0f}→{d['macd_prev']:+.0f} | "
            f"{d['interpretation']}"
        )

    print("\n── Señales de mayor fuerza ──")
    strong = df[df["strength"] >= 2]
    if not strong.empty:
        cols = ["close", "ndde", "macd", "histogram", "signal_name", "strength"]
        print(strong[cols].tail(10).to_string())

    print("\n── Distribución de señales ──")
    print(df["signal_name"].value_counts().to_string())

    print(f"\n{'═'*66}")


# ─────────────────────────────────────────────
# 6. INTEGRACIÓN PRODUCCIÓN
# ─────────────────────────────────────────────


class ShadowMACDLive:
    """
    Wrapper para BingX WebSocket + Massive API en producción.

    Uso:
        engine = ShadowMACDLive("AAPL")

        def on_candle(raw_candle, raw_chain):
            candle = ShadowMACDLive.parse_bingx_candle(raw_candle)
            chain  = ShadowMACDLive.parse_massive_chain("AAPL", raw_chain, spot)
            result = engine.core.update(candle, chain)
            engine.on_signal(result)
    """

    def __init__(self, ticker: str, **kwargs):
        self.ticker = ticker
        self.core = ShadowMACDEngine(ticker=ticker, **kwargs)

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
    def parse_massive_chain(
        ticker: str,
        raw_strikes: list[dict],
        spot: float,
    ) -> OptionsChainSnapshot:
        """
        Formato esperado de Massive API (lista de strikes):
        [
          {
            "strike": 190.0,
            "expiry": "2025-01-17",
            "callDelta": 0.72, "putDelta": -0.28,
            "callOI": 12500,   "putOI": 8300,
            "callGamma": 0.04, "putGamma": 0.04,
            "iv": 0.22
          },
          ...
        ]
        """
        strikes = [
            OptionStrike(
                strike=float(s["strike"]),
                expiry=s.get("expiry", ""),
                call_delta=float(s.get("callDelta", 0.5)),
                put_delta=float(s.get("putDelta", -0.5)),
                call_oi=int(s.get("callOI", 0)),
                put_oi=int(s.get("putOI", 0)),
                call_gamma=float(s.get("callGamma", 0.01)),
                put_gamma=float(s.get("putGamma", 0.01)),
                iv=float(s.get("iv", 0.20)),
            )
            for s in raw_strikes
        ]
        return OptionsChainSnapshot(
            timestamp=pd.Timestamp.now(tz="UTC"),
            ticker=ticker,
            spot_price=spot,
            strikes=strikes,
        )

    PRIORITY = {
        "DISTRIBUTION_TOP": 5,
        "ACCUMULATION_BOTTOM": 5,
        "NDDE_EXTREME_LONG": 4,
        "NDDE_EXTREME_SHORT": 4,
        "MACD_CROSS_BULL": 3,
        "MACD_CROSS_BEAR": 3,
        "MOMENTUM_DECELERATION": 2,
        "MOMENTUM_ACCELERATION": 1,
        "NEUTRAL": 0,
    }

    def on_signal(self, result: dict):
        p = self.PRIORITY.get(result["signal_name"], 0)
        if p >= 3:
            print(
                f"[{result['timestamp']}] {self.ticker:5s} | "
                f"P{p} {result['signal_name']:22s} | "
                f"${result['close']:.2f} | "
                f"NDDE={result['ndde']:+,.0f} | "
                f"Hist={result['histogram']:+.0f} | "
                f"{result['interpretation'][:50]}"
            )


# ─────────────────────────────────────────────
# 7. EJECUCIÓN DEMO
# ─────────────────────────────────────────────

if __name__ == "__main__":
    TICKERS = ["AAPL", "MSFT", "PLTR", "TSLA", "GOOGL"]

    for ticker in TICKERS:
        df = run_shadow_macd_pipeline(ticker=ticker, n=390, verbose=True)
        df.to_csv(f"/tmp/shadow_macd_{ticker.lower()}.csv")

    print("\n✓ Shadow MACD completado para los 5 proxies BingX.")
