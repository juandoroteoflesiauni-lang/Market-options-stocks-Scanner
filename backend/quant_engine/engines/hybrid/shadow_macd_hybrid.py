"""
Shadow MACD Híbrido — MACD de Precio + MACD sobre NDDE
═══════════════════════════════════════════════════════
Fusiona DOS fuentes de MACD en un único sistema de señales:

    MACD_PRECIO  = EMA12(close) − EMA26(close)   → momentum de precio
    MACD_NDDE    = EMA12(NDDE)  − EMA26(NDDE)    → momentum de dealers

    HISTOGRAMA_HIBRIDO = w_precio × Hist_precio + w_ndde × Hist_ndde

donde los pesos w son dinámicos según el régimen de Gamma:
    Gamma+  → w_precio=0.55, w_ndde=0.45  (precio más estable)
    Gamma−  → w_precio=0.35, w_ndde=0.65  (dealers dominan el movimiento)

Las señales más potentes:
    Cruce doble simultáneo  → ambas líneas cruzan en el mismo minuto
    Cruce divergente        → precio sube pero NDDE baja (distribución)
    Histograma híbrido 0    → momento exacto del cambio de momentum

Fuentes:
    BingX WebSocket  → velas 1m OHLCV
    Massive API      → cadena de opciones por strike: Delta, OI, Gamma

Compatibilidad: pandas >= 2.0 · numpy >= 1.24 · pandas-ta
"""

import warnings
from dataclasses import dataclass, field

import numpy as np
import pandas as pd

warnings.filterwarnings("ignore")


# ─────────────────────────────────────────────
# 1. ESTRUCTURAS DE DATOS
# ─────────────────────────────────────────────


@dataclass
class OptionStrike:
    strike: float
    call_delta: float
    put_delta: float
    call_oi: int
    put_oi: int
    call_gamma: float
    put_gamma: float


@dataclass
class OptionsChain:
    """
    Snapshot de cadena de opciones de Massive API — 1 por minuto.
    Calcula NDDE, GEX y Charm flow automáticamente.
    """

    timestamp: pd.Timestamp
    ticker: str
    spot: float
    strikes: list[OptionStrike]

    CONTRACT_SIZE: int = 100

    ndde: float = field(init=False)
    ndde_calls: float = field(init=False)
    ndde_puts: float = field(init=False)
    gex: float = field(init=False)
    charm_proxy: float = field(init=False)
    put_call_ratio: float = field(init=False)

    def __post_init__(self):
        self._compute()

    def _compute(self):
        nc = np = nd = gx = 0.0
        for s in self.strikes:
            # Dealer es SHORT las opciones que el cliente compra
            call_c = -s.call_delta * s.call_oi * self.CONTRACT_SIZE
            put_c = -s.put_delta * s.put_oi * self.CONTRACT_SIZE
            nc += call_c
            np += put_c
            gx += s.call_gamma * s.call_oi * self.CONTRACT_SIZE
            gx -= s.put_gamma * s.put_oi * self.CONTRACT_SIZE
        self.ndde_calls = nc
        self.ndde_puts = np
        self.ndde = nc + np
        self.gex = gx
        abs_c = abs(nc)
        abs_p = abs(np)
        self.put_call_ratio = abs_p / abs_c if abs_c > 0 else 1.0
        # Charm proxy: GEX de strikes ATM (dDelta/dt)
        atm = [s for s in self.strikes if abs(s.strike - self.spot) / self.spot < 0.015]
        self.charm_proxy = (
            sum(s.call_gamma * s.call_oi * self.CONTRACT_SIZE * -0.08 for s in atm) if atm else 0.0
        )


@dataclass
class CandleBar:
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
# 2. EMA INCREMENTAL (base del MACD)
# ─────────────────────────────────────────────


class IncrementalEMA:
    """EMA estándar con warm-up por SMA."""

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

    @property
    def ready(self) -> bool:
        return self._ready


# ─────────────────────────────────────────────
# 3. MOTOR MACD GENÉRICO
# ─────────────────────────────────────────────


class MACDCore:
    """
    MACD reutilizable para cualquier serie (precio o NDDE).
    Mantiene estado incremental: procesa tick a tick sin re-calcular todo.

    Retorna (macd_line, signal_line, histogram) o (None,None,None) en warm-up.
    """

    def __init__(self, fast: int = 12, slow: int = 26, signal: int = 9):
        self.fast = fast
        self.slow = slow
        self.signal = signal
        self._ema_f = IncrementalEMA(fast)
        self._ema_s = IncrementalEMA(slow)
        self._ema_g = IncrementalEMA(signal)

    def update(self, x: float) -> tuple[float | None, float | None, float | None]:
        ef = self._ema_f.update(x)
        es = self._ema_s.update(x)
        if ef is None or es is None:
            return None, None, None
        macd_line = ef - es
        sig_line = self._ema_g.update(macd_line)
        if sig_line is None:
            return macd_line, None, None
        return macd_line, sig_line, macd_line - sig_line

    def reset(self):
        self._ema_f = IncrementalEMA(self.fast)
        self._ema_s = IncrementalEMA(self.slow)
        self._ema_g = IncrementalEMA(self.signal)


# ─────────────────────────────────────────────
# 4. CLASIFICADOR DE RÉGIMEN
# ─────────────────────────────────────────────


class RegimeClassifier:
    """
    Determina el régimen de Gamma y los pesos del histograma híbrido.
    Recuerda cruces del Gamma Flip por N velas.
    """

    W_GAMMA_POS = {"price": 0.55, "ndde": 0.45}
    W_GAMMA_NEG = {"price": 0.35, "ndde": 0.65}
    W_GAMMA_FLIP = {"price": 0.40, "ndde": 0.60}
    W_UNKNOWN = {"price": 0.50, "ndde": 0.50}

    def __init__(self, gamma_flip_memory: int = 3):
        self._memory = gamma_flip_memory
        self._countdown = 0
        self._last_gex_sign: int | None = None

    def classify(self, gex: float, gamma_flip_level: float = 0.0) -> tuple[str, dict]:
        """
        Retorna (regime_name, weight_dict).
        Detecta cruces del Gamma Flip y activa memoria.
        """
        gex_sign = 1 if gex > gamma_flip_level else -1

        # Detectar cruce
        if self._last_gex_sign is not None and gex_sign != self._last_gex_sign:
            self._countdown = self._memory

        self._last_gex_sign = gex_sign

        if self._countdown > 0:
            self._countdown -= 1
            return "GAMMA_FLIP", self.W_GAMMA_FLIP

        if gex > gamma_flip_level:
            return "GAMMA_POS", self.W_GAMMA_POS
        else:
            return "GAMMA_NEG", self.W_GAMMA_NEG


# ─────────────────────────────────────────────
# 5. DETECTOR DE CRUCES Y PATRONES
# ─────────────────────────────────────────────


class CrossDetector:
    """
    Detecta cruces y patrones de señal en los dos MACDs simultáneamente.

    Patrones:
        DOUBLE_CROSS_BULL   : ambas líneas cruzan al alza en ≤2 velas
        DOUBLE_CROSS_BEAR   : ambas líneas cruzan a la baja en ≤2 velas
        LEAD_PRICE_BULL     : MACD_precio cruza primero al alza
        LEAD_NDDE_BULL      : MACD_ndde  cruza primero al alza
        LEAD_PRICE_BEAR     : MACD_precio cruza primero a la baja
        LEAD_NDDE_BEAR      : MACD_ndde  cruza primero a la baja
        DIVERGENT_BULL_DIST : precio hist sube, ndde hist baja (distribución)
        DIVERGENT_BEAR_ACC  : precio hist baja, ndde hist sube (acumulación)
        HYBRID_ZERO_CROSS   : histograma híbrido cruza el cero
    """

    def __init__(self, sync_window: int = 2):
        """
        sync_window: velas de tolerancia para considerar cruces 'simultáneos'.
        """
        self.sync_window = sync_window
        self._price_cross_buf: list[int] = []  # +1 bull, -1 bear
        self._ndde_cross_buf: list[int] = []
        self._prev_hp: float | None = None
        self._prev_hn: float | None = None
        self._prev_hyb: float | None = None

    def update(
        self,
        hist_price: float | None,
        hist_ndde: float | None,
        hist_hybrid: float | None,
    ) -> tuple[str, int, str]:
        """
        Procesa un tick y retorna (signal_name, strength, interpretation).
        """
        if hist_price is None or hist_ndde is None or hist_hybrid is None:
            return "WARMING_UP", 0, "Período de inicialización"

        # ── Cruces del cero ────────────────────────────────────
        price_cross = self._zero_cross(hist_price, self._prev_hp)
        ndde_cross = self._zero_cross(hist_ndde, self._prev_hn)
        hyb_cross = self._zero_cross(hist_hybrid, self._prev_hyb)

        self._price_cross_buf.append(price_cross)
        self._ndde_cross_buf.append(ndde_cross)
        if len(self._price_cross_buf) > self.sync_window + 1:
            self._price_cross_buf.pop(0)
            self._ndde_cross_buf.pop(0)

        # ── Patrones ──────────────────────────────────────────
        result = self._classify(
            price_cross,
            ndde_cross,
            hyb_cross,
            hist_price,
            hist_ndde,
            hist_hybrid,
        )

        self._prev_hp = hist_price
        self._prev_hn = hist_ndde
        self._prev_hyb = hist_hybrid
        return result

    def _zero_cross(self, cur: float, prev: float | None) -> int:
        if prev is None:
            return 0
        if prev < 0 <= cur:
            return +1  # cruce alcista
        if prev > 0 >= cur:
            return -1  # cruce bajista
        return 0

    def _classify(
        self,
        pc: int,
        nc: int,
        hc: int,
        hp: float,
        hn: float,
        hh: float,
    ) -> tuple[str, int, str]:

        recent_p = self._price_cross_buf
        recent_n = self._ndde_cross_buf

        # ── Cruce doble simultáneo (máxima prioridad) ──────────
        if pc == +1 and nc == +1:
            return (
                "DOUBLE_CROSS_BULL",
                4,
                "Precio Y NDDE cruzan al alza en el mismo tick → momentum confirmado",
            )
        if pc == -1 and nc == -1:
            return (
                "DOUBLE_CROSS_BEAR",
                4,
                "Precio Y NDDE cruzan a la baja en el mismo tick → distribución confirmada",
            )

        # ── Cruce doble dentro de ventana sync_window ──────────
        bull_p_recent = any(x == +1 for x in recent_p)
        bull_n_recent = any(x == +1 for x in recent_n)
        bear_p_recent = any(x == -1 for x in recent_p)
        bear_n_recent = any(x == -1 for x in recent_n)

        if bull_p_recent and bull_n_recent and nc == +1:
            return (
                "SYNC_CROSS_BULL",
                3,
                f"Cruces alcistas sincronizados (≤{self.sync_window} velas) → entrada de momentum",
            )
        if bear_p_recent and bear_n_recent and nc == -1:
            return (
                "SYNC_CROSS_BEAR",
                3,
                f"Cruces bajistas sincronizados (≤{self.sync_window} velas) → salida de momentum",
            )

        # ── Cruce del histograma híbrido ───────────────────────
        if hc == +1:
            str_ = 3 if hp > 0 and hn > 0 else 2
            return (
                "HYBRID_ZERO_CROSS_BULL",
                str_,
                "Histograma híbrido cruza el cero al alza → momentum institucional neto positivo",
            )
        if hc == -1:
            str_ = 3 if hp < 0 and hn < 0 else 2
            return (
                "HYBRID_ZERO_CROSS_BEAR",
                str_,
                "Histograma híbrido cruza el cero a la baja → momentum institucional neto negativo",
            )

        # ── Divergencia: precio sube, NDDE baja (distribución) ─
        if hp > 0 and hn < 0 and abs(hp) > abs(hn) * 0.3:
            return (
                "DIVERGENT_DISTRIBUTION",
                3,
                "Precio en momentum alcista pero NDDE negativo → distribución institucional activa",
            )
        # ── Divergencia: precio baja, NDDE sube (acumulación) ──
        if hp < 0 and hn > 0 and abs(hp) > abs(hn) * 0.3:
            return (
                "DIVERGENT_ACCUMULATION",
                3,
                "Precio en momentum bajista pero NDDE positivo → acumulación institucional activa",
            )

        # ── Cruces individuales con contexto ───────────────────
        if pc == +1:
            str_ = 2 if hn > 0 else 1
            return (
                "LEAD_PRICE_BULL" if hn <= 0 else "PRICE_CROSS_BULL",
                str_,
                "MACD precio cruza al alza"
                + (" (NDDE confirma)" if hn > 0 else " (NDDE no confirma)"),
            )
        if pc == -1:
            str_ = 2 if hn < 0 else 1
            return (
                "LEAD_PRICE_BEAR" if hn >= 0 else "PRICE_CROSS_BEAR",
                str_,
                "MACD precio cruza a la baja"
                + (" (NDDE confirma)" if hn < 0 else " (NDDE no confirma)"),
            )
        if nc == +1:
            str_ = 2 if hp > 0 else 1
            return (
                "LEAD_NDDE_BULL",
                str_,
                "MACD NDDE cruza primero al alza"
                + (" (precio confirma)" if hp > 0 else " → anticipa subida"),
            )
        if nc == -1:
            str_ = 2 if hp < 0 else 1
            return (
                "LEAD_NDDE_BEAR",
                str_,
                "MACD NDDE cruza primero a la baja"
                + (" (precio confirma)" if hp < 0 else " → anticipa caída"),
            )

        # ── Aceleración / deceleración del híbrido ─────────────
        if self._prev_hyb is not None:
            if hh > self._prev_hyb and hh > 0:
                return ("HYBRID_ACCELERATING_BULL", 1, "Histograma híbrido acelerando al alza")
            if hh < self._prev_hyb and hh < 0:
                return ("HYBRID_ACCELERATING_BEAR", 1, "Histograma híbrido acelerando a la baja")

        return ("NEUTRAL", 0, "Sin cruce ni patrón activo")


# ─────────────────────────────────────────────
# 6. MOTOR SHADOW MACD HÍBRIDO
# ─────────────────────────────────────────────


class HybridShadowMACDEngine:
    """
    Motor principal: MACD de precio + MACD de NDDE + histograma combinado.

    Args:
        ticker:            Símbolo del proxy
        fast, slow, sig:   Períodos del MACD. Default 12/26/9.
        ndde_smooth:       Suavizado del NDDE crudo antes del MACD. Default 3.
        gamma_flip_mem:    Velas que recuerda un cruce del Gamma Flip. Default 3.
        sync_window:       Tolerancia de velas para cruces simultáneos. Default 2.
    """

    def __init__(
        self,
        ticker: str,
        fast: int = 12,
        slow: int = 26,
        sig: int = 9,
        ndde_smooth: int = 3,
        gamma_flip_mem: int = 3,
        sync_window: int = 2,
    ):
        self.ticker = ticker

        # Dos MACDs independientes
        self._macd_price = MACDCore(fast, slow, sig)
        self._macd_ndde = MACDCore(fast, slow, sig)

        # Suavizado NDDE
        self._ndde_buf: list[float] = []
        self._ndde_smooth = ndde_smooth

        # Clasificador de régimen
        self._regime = RegimeClassifier(gamma_flip_mem)

        # Detector de cruces
        self._cross = CrossDetector(sync_window)

        # Historia
        self._history: list[dict] = []

    # ── NDDE suavizado ─────────────────────────────────────────
    def _smooth_ndde(self, raw: float) -> float:
        self._ndde_buf.append(raw)
        if len(self._ndde_buf) > self._ndde_smooth:
            self._ndde_buf.pop(0)
        return float(np.mean(self._ndde_buf))

    # ── Tick principal ──────────────────────────────────────────
    def update(
        self,
        candle: CandleBar,
        chain: OptionsChain | None = None,
    ) -> dict:
        """
        Procesa una vela 1m + snapshot de opciones.
        Retorna dict con todas las métricas y la señal compuesta.
        """
        # ── 1. MACD de precio ─────────────────────────────────
        mp, sp, hp = self._macd_price.update(candle.close)

        # ── 2. MACD de NDDE ───────────────────────────────────
        if chain is not None:
            ndde_raw = chain.ndde
            gex = chain.gex
            charm = chain.charm_proxy
            pc_ratio = chain.put_call_ratio
            ndde_calls = chain.ndde_calls
            ndde_puts = chain.ndde_puts
        else:
            ndde_raw = gex = charm = 0.0
            pc_ratio = 1.0
            ndde_calls = ndde_puts = 0.0

        ndde = self._smooth_ndde(ndde_raw)
        mn, sn, hn = self._macd_ndde.update(ndde)

        # ── 3. Régimen y pesos ────────────────────────────────
        regime, weights = self._regime.classify(gex)

        # ── 4. Histograma híbrido ─────────────────────────────
        if hp is not None and hn is not None:
            # Normalizar antes de combinar (escalas muy distintas)
            # hp en unidades de precio (~centavos), hn en unidades de NDDE (~miles)
            hp_norm = hp / max(abs(candle.close) * 0.001, 1e-9)
            hn_norm = hn / max(abs(ndde) * 0.01 if abs(ndde) > 1000 else 1e3, 1e-9)
            hist_hybrid = weights["price"] * hp_norm + weights["ndde"] * hn_norm
        else:
            hist_hybrid = None
            hp_norm = hn_norm = None

        # ── 5. Cruces y señal ─────────────────────────────────
        signal, strength, interpretation = self._cross.update(
            hist_price=hp, hist_ndde=hn, hist_hybrid=hist_hybrid
        )

        # ── 6. Métricas de convergencia ───────────────────────
        price_ndde_agreement = self._agreement(mp, mn, hp, hn)
        lead_indicator = self._lead(hp, hn)

        result = {
            # Identificación
            "timestamp": candle.timestamp,
            "ticker": self.ticker,
            "close": candle.close,
            # MACD precio
            "macd_price": round(mp, 6) if mp is not None else np.nan,
            "sig_price": round(sp, 6) if sp is not None else np.nan,
            "hist_price": round(hp, 6) if hp is not None else np.nan,
            "hist_price_norm": round(hp_norm, 6) if hp_norm is not None else np.nan,
            # MACD NDDE
            "ndde": round(ndde, 0),
            "ndde_raw": round(ndde_raw, 0),
            "ndde_calls": round(ndde_calls, 0),
            "ndde_puts": round(ndde_puts, 0),
            "macd_ndde": round(mn, 2) if mn is not None else np.nan,
            "sig_ndde": round(sn, 2) if sn is not None else np.nan,
            "hist_ndde": round(hn, 2) if hn is not None else np.nan,
            "hist_ndde_norm": round(hn_norm, 6) if hn_norm is not None else np.nan,
            # Histograma híbrido
            "hist_hybrid": round(hist_hybrid, 6) if hist_hybrid is not None else np.nan,
            "regime": regime,
            "w_price": weights["price"],
            "w_ndde": weights["ndde"],
            # Opciones
            "gex": round(gex, 0),
            "charm": round(charm, 2),
            "put_call_ratio": round(pc_ratio, 4),
            # Análisis
            "agreement": price_ndde_agreement,
            "lead_indicator": lead_indicator,
            # Señal
            "signal": signal,
            "strength": strength,
            "interpretation": interpretation,
        }

        self._history.append(result)
        return result

    def _agreement(
        self,
        mp: float | None,
        mn: float | None,
        hp: float | None,
        hn: float | None,
    ) -> str:
        """Mide el acuerdo entre MACD precio y MACD NDDE."""
        if any(x is None for x in [mp, mn, hp, hn]):
            return "UNKNOWN"
        both_pos = mp > 0 and mn > 0
        both_neg = mp < 0 and mn < 0
        hist_agree = (hp > 0 and hn > 0) or (hp < 0 and hn < 0)
        if both_pos and hist_agree:
            return "FULL_BULL"
        if both_neg and hist_agree:
            return "FULL_BEAR"
        if both_pos or both_neg:
            return "PARTIAL"
        return "DIVERGENT"

    def _lead(
        self,
        hp: float | None,
        hn: float | None,
    ) -> str:
        """Indica qué indicador lidera el movimiento actual."""
        if hp is None or hn is None:
            return "UNKNOWN"
        if abs(hp) > abs(hn) * 1.5:
            return "PRICE_LEADING"
        if abs(hn) > abs(hp) * 1.5:
            return "NDDE_LEADING"
        return "IN_SYNC"

    def to_dataframe(self) -> pd.DataFrame:
        if not self._history:
            return pd.DataFrame()
        df = pd.DataFrame(self._history)
        df.set_index("timestamp", inplace=True)
        return df.dropna(subset=["macd_price", "macd_ndde"])


# ─────────────────────────────────────────────
# 7. GENERADOR DE DATOS DEMO
# ─────────────────────────────────────────────


def _make_chain(
    spot: float,
    ts: pd.Timestamp,
    ticker: str,
    ndde_bias: float,
    gex_level: float,
    rng: np.random.Generator,
) -> OptionsChain:
    strikes = []
    for pct in np.arange(-0.06, 0.07, 0.01):
        k = round(spot * (1 + pct), 2)
        cd = max(0.01, min(0.99, 0.5 - pct * 5))
        pd_ = cd - 1.0
        base_oi = int(rng.integers(500, 5000))
        if ndde_bias > 0:
            coi = int(base_oi * rng.uniform(1.2, 1.8))
            poi = int(base_oi * rng.uniform(0.5, 0.9))
        else:
            coi = int(base_oi * rng.uniform(0.5, 0.9))
            poi = int(base_oi * rng.uniform(1.2, 1.8))
        gamma = max(0.001, 0.05 - abs(pct) * 0.4)
        strikes.append(
            OptionStrike(
                strike=k,
                call_delta=cd,
                put_delta=pd_,
                call_oi=coi,
                put_oi=poi,
                call_gamma=gamma,
                put_gamma=gamma,
            )
        )
    return OptionsChain(
        timestamp=ts,
        ticker=ticker,
        spot=spot,
        strikes=strikes,
    )


def generate_demo(
    ticker: str = "AAPL",
    n: int = 390,
    base: float = 192.50,
    seed: int = 42,
) -> tuple[list[CandleBar], list[OptionsChain]]:
    """
    4 fases que exhiben los 4 patrones clave:
        Fase 1: ambos MACDs suben → DOUBLE_CROSS_BULL
        Fase 2: precio sube, NDDE baja → DIVERGENT_DISTRIBUTION
        Fase 3: ambos MACDs bajan → DOUBLE_CROSS_BEAR
        Fase 4: precio baja, NDDE sube → DIVERGENT_ACCUMULATION
    """
    rng = np.random.default_rng(seed)
    start = pd.Timestamp("2025-01-15 09:30:00", tz="America/New_York")
    tss = pd.date_range(start, periods=n, freq="1min")

    # (bars, p_trend, ndde_bias, gex, noise_p, noise_n)
    phases = [
        (96, 0.00055, 0.70, 1.8e6, 0.0006, 0.12),  # ambos suben
        (99, 0.00035, -0.65, -0.4e6, 0.0005, 0.10),  # distribución
        (99, -0.00045, -0.60, -1.4e6, 0.0008, 0.12),  # ambos bajan
        (96, -0.00025, 0.55, 0.7e6, 0.0007, 0.10),  # acumulación
    ]

    candles, chains = [], []
    price = base
    idx = 0
    for n_b, p_tr, nbias, gex_b, pn, nn in phases:
        for _ in range(n_b):
            if idx >= n:
                break
            ts = tss[idx]
            price *= 1 + p_tr + rng.normal(0, pn)
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
            chains.append(
                _make_chain(
                    spot=price,
                    ts=ts,
                    ticker=ticker,
                    ndde_bias=nbias + rng.normal(0, 0.15),
                    gex_level=gex_b + rng.normal(0, abs(gex_b) * 0.12),
                    rng=rng,
                )
            )
            idx += 1
    return candles, chains


# ─────────────────────────────────────────────
# 8. PIPELINE + REPORTE
# ─────────────────────────────────────────────


def run_hybrid_shadow_macd(
    ticker: str = "AAPL",
    n: int = 390,
    verbose: bool = True,
) -> pd.DataFrame:

    print(f"\n{'═'*68}")
    print(f"  SHADOW MACD HÍBRIDO  |  {ticker}  |  {n} velas 1m-5m")
    print(f"{'═'*68}")

    candles, chains = generate_demo(ticker, n)
    engine = HybridShadowMACDEngine(ticker=ticker)

    for c, ch in zip(candles, chains, strict=False):
        engine.update(c, ch)

    df = engine.to_dataframe()

    if verbose:
        _print_report(df, ticker)

    return df


def _print_report(df: pd.DataFrame, ticker: str):
    last = df.iloc[-1]
    print(f"\n── Estado actual {ticker} ──────────────────────────────")
    print(f"  Precio             : ${last['close']:.2f}")
    print(f"  MACD precio        : {last['macd_price']:+.6f}")
    print(
        f"  Hist precio        : {last['hist_price']:+.6f}  (norm: {last['hist_price_norm']:+.4f})"
    )
    print(f"  NDDE               : {last['ndde']:+,.0f}")
    print(f"  MACD NDDE          : {last['macd_ndde']:+,.2f}")
    print(
        f"  Hist NDDE          : {last['hist_ndde']:+,.2f}  (norm: {last['hist_ndde_norm']:+.4f})"
    )
    print(f"  Hist híbrido       : {last['hist_hybrid']:+.6f}")
    print(
        f"  Régimen Gamma      : {last['regime']}  (w_p={last['w_price']:.2f} / w_n={last['w_ndde']:.2f})"
    )
    print(f"  Acuerdo            : {last['agreement']}")
    print(f"  Indicador líder    : {last['lead_indicator']}")
    print(f"  GEX                : {last['gex']:+,.0f}")
    print(f"  Charm proxy        : {last['charm']:+.2f}")
    print(f"  Put/Call ratio     : {last['put_call_ratio']:.3f}")
    print("  ── Señal ──────────────────────────────────────────")
    print(f"  Señal              : {last['signal']}  (fuerza {last['strength']})")
    print(f"  Interpretación     : {last['interpretation']}")

    # Correlación entre los dos MACDs
    corr = df["macd_price"].corr(df["macd_ndde"])
    print(f"\n── Correlación MACD_precio / MACD_NDDE : {corr:.4f}")
    print("   (< 0.5 = alta independencia, señales complementarias)")

    print("\n── Distribución de acuerdos ──")
    print(df["agreement"].value_counts().to_string())

    print("\n── Distribución de líder ──")
    print(df["lead_indicator"].value_counts().to_string())

    print("\n── Señales por fuerza ──")
    sig_counts = df[df["strength"] > 0].groupby(["signal", "strength"]).size()
    print(sig_counts.to_string())

    # Señales fuertes
    strong = df[df["strength"] >= 3]
    print(f"\n── Señales fuerza ≥ 3 : {len(strong)} ──")
    if not strong.empty:
        cols = [
            "close",
            "hist_price_norm",
            "hist_ndde_norm",
            "hist_hybrid",
            "regime",
            "agreement",
            "signal",
            "strength",
        ]
        print(strong[cols].tail(10).to_string())

    # Patrones divergentes (los más valiosos)
    div_sigs = ["DIVERGENT_DISTRIBUTION", "DIVERGENT_ACCUMULATION"]
    divs = df[df["signal"].isin(div_sigs)]
    print(f"\n── Patrones divergentes detectados: {len(divs)} ──")
    if not divs.empty:
        print(
            divs[
                ["close", "macd_price", "macd_ndde", "hist_hybrid", "signal", "interpretation"]
            ].to_string()
        )

    print(f"\n{'═'*68}")


# ─────────────────────────────────────────────
# 9. INTEGRACIÓN PRODUCCIÓN
# ─────────────────────────────────────────────


class HybridShadowMACDLive:
    """
    Wrapper para BingX WebSocket + Massive API en producción.

    Uso:
        engine = HybridShadowMACDLive("AAPL")

        def on_1m_close(raw_bingx, raw_massive_strikes, spot):
            candle = HybridShadowMACDLive.parse_bingx(raw_bingx)
            chain  = HybridShadowMACDLive.parse_massive("AAPL", raw_massive_strikes, spot)
            result = engine.core.update(candle, chain)
            engine.on_signal(result)
    """

    PRIORITY = {
        "DOUBLE_CROSS_BULL": 5,
        "DOUBLE_CROSS_BEAR": 5,
        "SYNC_CROSS_BULL": 4,
        "SYNC_CROSS_BEAR": 4,
        "DIVERGENT_DISTRIBUTION": 4,
        "DIVERGENT_ACCUMULATION": 4,
        "HYBRID_ZERO_CROSS_BULL": 3,
        "HYBRID_ZERO_CROSS_BEAR": 3,
        "LEAD_NDDE_BULL": 2,
        "LEAD_NDDE_BEAR": 2,
        "PRICE_CROSS_BULL": 2,
        "PRICE_CROSS_BEAR": 2,
        "LEAD_PRICE_BULL": 1,
        "LEAD_PRICE_BEAR": 1,
        "HYBRID_ACCELERATING_BULL": 1,
        "HYBRID_ACCELERATING_BEAR": 1,
        "NEUTRAL": 0,
        "WARMING_UP": 0,
    }

    def __init__(self, ticker: str, **kwargs):
        self.ticker = ticker
        self.core = HybridShadowMACDEngine(ticker=ticker, **kwargs)

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
    def parse_massive(ticker: str, raw_strikes: list[dict], spot: float) -> OptionsChain:
        """
        Formato esperado Massive API (lista de strikes):
        [{"strike":190,"callDelta":0.72,"putDelta":-0.28,
          "callOI":12500,"putOI":8300,"callGamma":0.04,"putGamma":0.04}, ...]
        """
        strikes = [
            OptionStrike(
                strike=float(s["strike"]),
                call_delta=float(s.get("callDelta", 0.5)),
                put_delta=float(s.get("putDelta", -0.5)),
                call_oi=int(s.get("callOI", 0)),
                put_oi=int(s.get("putOI", 0)),
                call_gamma=float(s.get("callGamma", 0.01)),
                put_gamma=float(s.get("putGamma", 0.01)),
            )
            for s in raw_strikes
        ]
        return OptionsChain(
            timestamp=pd.Timestamp.now(tz="UTC"),
            ticker=ticker,
            spot=spot,
            strikes=strikes,
        )

    def on_signal(self, result: dict):
        p = self.PRIORITY.get(result["signal"], 0)
        if p >= 3:
            print(
                f"[{result['timestamp']}] {self.ticker:5s} | "
                f"P{p} {result['signal']:28s} | "
                f"${result['close']:.2f} | "
                f"HP={result['hist_price_norm']:+.4f} "
                f"HN={result['hist_ndde_norm']:+.4f} "
                f"HH={result['hist_hybrid']:+.4f} | "
                f"{result['regime']:12s} | "
                f"{result['interpretation'][:40]}"
            )


# ─────────────────────────────────────────────
# 10. EJECUCIÓN DEMO
# ─────────────────────────────────────────────

if __name__ == "__main__":
    TICKERS = ["AAPL", "MSFT", "PLTR", "TSLA", "GOOGL"]

    for ticker in TICKERS:
        df = run_hybrid_shadow_macd(ticker=ticker, n=390, verbose=True)
        df.to_csv(f"/tmp/hybrid_shadow_macd_{ticker.lower()}.csv")

    print("\n✓ Shadow MACD Híbrido completado para los 5 proxies BingX.")
