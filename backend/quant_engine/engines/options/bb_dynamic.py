"""
BB-GEX — Bollinger Bands con Multiplicador Dinámico de Gamma + IV
══════════════════════════════════════════════════════════════════
Las Bollinger Bands clásicas usan una σ histórica fija y un
multiplicador estático (2.0). Este motor reemplaza ambos:

  σ  →  IV implícita interpolada de la cadena de opciones (predictiva)
  k  →  multiplicador dinámico que cambia con el régimen de Gamma:
             k = 2.0  si  GEX > Gamma Flip   (Gamma Positivo)
             k = 3.0  si  GEX < Gamma Flip   (Gamma Negativo)

La banda resultante anticipa la volatilidad en lugar de reaccionar a ella.

Fuentes:
  BingX WebSocket  →  velas 1m OHLCV
  Massive API      →  IV ATM, GEX, Gamma Flip nivel

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
class OptionsRegime:
    """
    Estado del régimen de opciones en un momento t.
    Se actualiza cada 1m desde Massive API.
    """

    timestamp: pd.Timestamp
    ticker: str

    # Volatilidad implícita
    iv_atm: float  # IV del strike ATM  (0.0 – 1.0 como decimal)
    iv_25d_call: float  # IV 25-delta call   (skew)
    iv_25d_put: float  # IV 25-delta put    (skew)
    iv_term_1w: float  # IV término 1 semana
    iv_term_1m: float  # IV término 1 mes

    # Exposición de Gamma
    net_gex: float  # GEX neto total de la cadena
    gamma_flip: float  # Nivel de precio donde GEX = 0
    gamma_wall_up: float  # Strike con mayor GEX positivo sobre spot
    gamma_wall_down: float  # Strike con mayor GEX negativo bajo spot

    # Métricas calculadas
    iv_skew: float = field(init=False)  # Asimetría put/call
    iv_term_slope: float = field(init=False)  # Pendiente de la curva de plazos
    regime: str = field(init=False)

    def __post_init__(self):
        self.iv_skew = self.iv_25d_put - self.iv_25d_call
        self.iv_term_slope = self.iv_term_1m - self.iv_term_1w
        self._classify_regime()

    def _classify_regime(self):
        """
        Clasifica el régimen de mercado por combinación de GEX e IV.
        Esto determina la lógica de señal del motor.
        """
        gex_pos = self.net_gex > self.gamma_flip

        if gex_pos and self.iv_atm < 0.20:
            self.regime = "PINNED"  # Gamma+ / IV baja → precio anclado
        elif gex_pos and self.iv_atm >= 0.20:
            self.regime = "CONTROLLED"  # Gamma+ / IV alta → dealers manejan
        elif not gex_pos and self.iv_atm < 0.20:
            self.regime = "COILING"  # Gamma− / IV baja → explosión inminente
        else:
            self.regime = "TRENDING"  # Gamma− / IV alta → tendencia explosiva

    def annualized_iv_to_per_minute(self) -> float:
        """
        Convierte la IV anualizada al equivalente por vela de 1 minuto.
        IV_1min = IV_anual / √(252 × 390)
        donde 390 = minutos por sesión de trading
        """
        return self.iv_atm / np.sqrt(252 * 390)

    def get_dynamic_multiplier(self, spot: float) -> tuple[float, str]:
        """
        Retorna el multiplicador k según la posición del precio
        respecto al Gamma Flip y el régimen de GEX.

        Returns:
            (k, razon) donde razon explica el ajuste aplicado
        """
        if self.net_gex > self.gamma_flip:
            # ── Gamma Positivo ─────────────────────────────────
            # Dealers absorben volatilidad. El precio tiende a regresar
            # al centro. Banda estándar es suficiente.
            base_k = 2.0

            # Ajuste por skew: si el put skew es muy alto los dealers
            # anticipan riesgo bajista → ampliar banda inferior
            skew_adj = min(0.3, self.iv_skew * 1.5)
            return base_k + skew_adj, f"GAMMA_POS (skew_adj={skew_adj:.3f})"

        else:
            # ── Gamma Negativo ─────────────────────────────────
            # Dealers amplifican el movimiento. El precio puede
            # alejarse mucho. Necesitamos banda más ancha.
            base_k = 3.0

            # Ajuste adicional por IV extrema (eventos de cola)
            if self.iv_atm > 0.40:
                iv_adj = (self.iv_atm - 0.40) * 2.0  # extra por IV muy alta
                return base_k + iv_adj, f"GAMMA_NEG + IV_EXTREMA (adj={iv_adj:.3f})"

            # Ajuste por término: si IV 1w > IV 1m (inversión de curva)
            # hay riesgo de evento inmediato → ampliar más
            if self.iv_term_slope < -0.02:
                return base_k + 0.5, "GAMMA_NEG + INVERSION_CURVA"

            return base_k, "GAMMA_NEG"


@dataclass
class CandleBar:
    """Vela de 1 minuto de BingX proxy."""

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
# 2. MOTOR BB-GEX
# ─────────────────────────────────────────────


class BBGEXEngine:
    """
    Bollinger Bands con multiplicador dinámico por régimen Gamma + IV.

    Tres modos de σ seleccionables:
        "iv"       → usa IV ATM de opciones (predictivo, recomendado)
        "hybrid"   → promedio ponderado de σ histórica e IV (balanceado)
        "classic"  → σ histórica estándar (baseline de comparación)

    Args:
        ticker:      Símbolo del proxy
        period:      Período de la media base (SMA). Default 20.
        sigma_mode:  "iv" | "hybrid" | "classic"
        iv_weight:   Peso de IV en modo "hybrid" (0-1). Default 0.65.
        gamma_flip:  Nivel GEX que separa regímenes. Default 0.
    """

    def __init__(
        self,
        ticker: str,
        period: int = 20,
        sigma_mode: str = "iv",
        iv_weight: float = 0.65,
        gamma_flip: float = 0.0,
    ):
        assert sigma_mode in (
            "iv",
            "hybrid",
            "classic",
        ), "sigma_mode debe ser 'iv', 'hybrid' o 'classic'"
        assert 0 < iv_weight <= 1, "iv_weight debe estar en (0, 1]"

        self.ticker = ticker
        self.period = period
        self.sigma_mode = sigma_mode
        self.iv_weight = iv_weight
        self.gamma_flip = gamma_flip

        # Buffers de precio típico (ventana deslizante)
        self._price_buffer: list[float] = []
        self._history: list[dict] = []

        # Estado del último régimen (para detectar cruces del Gamma Flip)
        self._last_regime: str | None = None
        self._flip_count: int = 0  # Número de cruces del Gamma Flip

    # ── σ según modo ────────────────────────────────────────────
    def _compute_sigma(
        self,
        prices: list[float],
        opt: OptionsRegime | None,
        spot: float,
    ) -> tuple[float, float, float]:
        """
        Calcula tres versiones de σ y retorna la seleccionada + ambas
        para comparación.

        Returns:
            (sigma_used, sigma_historic, sigma_iv)
        """
        # σ histórica (clásica Bollinger)
        if len(prices) >= 2:
            sigma_hist = float(np.std(prices, ddof=1))
        else:
            sigma_hist = spot * 0.001  # fallback mínimo

        # σ de IV (predictiva)
        if opt is not None:
            # IV anualizada → equivalente en precio para 1 vela de 1m
            iv_1min = opt.annualized_iv_to_per_minute()
            sigma_iv = spot * iv_1min
        else:
            sigma_iv = sigma_hist  # fallback si no hay opciones

        # Selección según modo
        if self.sigma_mode == "iv":
            sigma_used = sigma_iv
        elif self.sigma_mode == "hybrid":
            sigma_used = self.iv_weight * sigma_iv + (1 - self.iv_weight) * sigma_hist
        else:
            sigma_used = sigma_hist

        return sigma_used, sigma_hist, sigma_iv

    # ── Detección de cruce del Gamma Flip ──────────────────────
    def _detect_flip_cross(self, current_regime: str) -> bool:
        """
        Detecta cuando el régimen cambia de Gamma+ a Gamma−
        o viceversa (cruce del nivel de Gamma Flip).
        """
        crossed = (
            (self._last_regime is not None
            and self._last_regime != current_regime
            and "GAMMA" in (self._last_regime or "")
            and "GAMMA" not in (current_regime or ""))
            or (
                self._last_regime in ("PINNED", "CONTROLLED")
                and current_regime in ("COILING", "TRENDING")
            )
            or (
                self._last_regime in ("COILING", "TRENDING")
                and current_regime in ("PINNED", "CONTROLLED")
            )
        )
        if crossed:
            self._flip_count += 1
        return crossed

    # ── Tick principal ──────────────────────────────────────────
    def update(
        self,
        candle: CandleBar,
        opt: OptionsRegime | None = None,
    ) -> dict:
        """
        Procesa una vela de 1m y calcula las BB-GEX.

        Returns dict con todas las métricas y señales.
        """
        p = candle.typical_price
        self._price_buffer.append(p)

        # Mantener solo la ventana necesaria
        if len(self._price_buffer) > self.period:
            self._price_buffer.pop(0)

        # ── Media base (SMA del precio típico) ─────────────────
        sma = float(np.mean(self._price_buffer))

        # ── Sigma y multiplicador ───────────────────────────────
        sigma_used, sigma_hist, sigma_iv = self._compute_sigma(self._price_buffer, opt, p)

        if opt is not None:
            k, k_reason = opt.get_dynamic_multiplier(p)
            regime = opt.regime
            net_gex = opt.net_gex
            gamma_flip_level = opt.gamma_flip
            iv_atm = opt.iv_atm
            iv_skew = opt.iv_skew
            gamma_wall_up = opt.gamma_wall_up
            gamma_wall_down = opt.gamma_wall_down
        else:
            k, k_reason = 2.0, "NO_OPTIONS"
            regime = "NO_OPTIONS"
            net_gex = 0.0
            gamma_flip_level = self.gamma_flip
            iv_atm = 0.0
            iv_skew = 0.0
            gamma_wall_up = p * 1.02
            gamma_wall_down = p * 0.98

        # ── Bandas ─────────────────────────────────────────────
        upper = sma + k * sigma_used
        lower = sma - k * sigma_used

        # Banda asimétrica por skew:
        # Si hay put skew (iv_skew > 0), la banda inferior se amplía
        # porque el mercado de opciones está pagando más por protección bajista
        if opt is not None and abs(iv_skew) > 0.02:
            skew_factor = 1.0 + abs(iv_skew) * 0.5
            if iv_skew > 0:  # más riesgo bajista
                lower = sma - k * sigma_used * skew_factor
            else:  # más riesgo alcista
                upper = sma + k * sigma_used * skew_factor

        # ── Bandwidth y %B ─────────────────────────────────────
        bandwidth = (upper - lower) / sma * 100  # % del precio
        pct_b = (p - lower) / (upper - lower) if upper != lower else 0.5

        # ── Cruce del Gamma Flip ────────────────────────────────
        gamma_flip_cross = self._detect_flip_cross(regime)
        self._last_regime = regime

        # ── Señales ────────────────────────────────────────────
        signal, signal_strength = self._generate_signal(
            price=p,
            sma=sma,
            upper=upper,
            lower=lower,
            pct_b=pct_b,
            bandwidth=bandwidth,
            regime=regime,
            gamma_flip_cross=gamma_flip_cross,
            iv_atm=iv_atm,
            gamma_wall_up=gamma_wall_up,
            gamma_wall_down=gamma_wall_down,
            net_gex=net_gex,
        )

        result = {
            # Identificación
            "timestamp": candle.timestamp,
            "ticker": self.ticker,
            "close": candle.close,
            "typical_price": round(p, 4),
            # Bandas BB-GEX
            "sma": round(sma, 4),
            "upper": round(upper, 4),
            "lower": round(lower, 4),
            "sigma_used": round(sigma_used, 4),
            "sigma_hist": round(sigma_hist, 4),
            "sigma_iv": round(sigma_iv, 4),
            "k_multiplier": round(k, 3),
            "k_reason": k_reason,
            # Métricas derivadas
            "bandwidth": round(bandwidth, 4),
            "pct_b": round(pct_b, 4),
            "price_vs_sma": round((p - sma) / sma * 100, 4),
            # Opciones
            "regime": regime,
            "net_gex": round(net_gex, 0),
            "gamma_flip_level": round(gamma_flip_level, 2),
            "gamma_flip_cross": gamma_flip_cross,
            "flip_count": self._flip_count,
            "iv_atm": round(iv_atm, 4),
            "iv_skew": round(iv_skew, 4),
            "gamma_wall_up": round(gamma_wall_up, 2),
            "gamma_wall_down": round(gamma_wall_down, 2),
            # Señal
            "signal": signal,
            "signal_strength": signal_strength,
        }

        self._history.append(result)
        return result

    # ── Lógica de señales ───────────────────────────────────────
    def _generate_signal(
        self,
        price: float,
        sma: float,
        upper: float,
        lower: float,
        pct_b: float,
        bandwidth: float,
        regime: str,
        gamma_flip_cross: bool,
        iv_atm: float,
        gamma_wall_up: float,
        gamma_wall_down: float,
        net_gex: float,
    ) -> tuple[str, int]:
        """
        Señales BB-GEX y su fuerza (1=débil, 2=media, 3=fuerte).

        SQUEEZE_BREAK_LONG   : Banda estrecha + Gamma− + precio rompe arriba
        SQUEEZE_BREAK_SHORT  : Banda estrecha + Gamma− + precio rompe abajo
        REVERSAL_LONG        : Precio bajo banda inferior en Gamma+ (rebote)
        REVERSAL_SHORT       : Precio sobre banda superior en Gamma+ (rebote)
        GAMMA_FLIP_LONG      : Precio cruza Gamma Flip hacia arriba (aceleración)
        GAMMA_FLIP_SHORT     : Precio cruza Gamma Flip hacia abajo (aceleración)
        WALL_REJECTION_UP    : Precio rechazado por Gamma Wall superior
        WALL_REJECTION_DOWN  : Precio rechazado por Gamma Wall inferior
        WALKING_UPPER        : %B > 0.9 sostenido (tendencia alcista fuerte)
        WALKING_LOWER        : %B < 0.1 sostenido (tendencia bajista fuerte)
        NEUTRAL              : Sin señal clara
        """
        is_gamma_pos = regime in ("PINNED", "CONTROLLED")
        is_squeeze = bandwidth < 1.5  # Banda < 1.5% del precio

        # ── Cruce del Gamma Flip (evento de alta prioridad) ─────
        if gamma_flip_cross:
            if price > sma:
                return "GAMMA_FLIP_LONG", 3
            else:
                return "GAMMA_FLIP_SHORT", 3

        # ── Squeeze + ruptura (Gamma Negativo) ──────────────────
        if is_squeeze and not is_gamma_pos:
            if price > upper:
                return "SQUEEZE_BREAK_LONG", 3
            if price < lower:
                return "SQUEEZE_BREAK_SHORT", 3
            return "COILING", 2  # Aún sin ruptura pero bajo presión

        # ── Reversiones en Gamma Positivo ───────────────────────
        if is_gamma_pos:
            if pct_b < 0.0:  # Precio bajo banda inferior
                strength = 3 if pct_b < -0.1 else 2
                return "REVERSAL_LONG", strength
            if pct_b > 1.0:  # Precio sobre banda superior
                strength = 3 if pct_b > 1.1 else 2
                return "REVERSAL_SHORT", strength

        # ── Rechazo en Gamma Walls ──────────────────────────────
        wall_tol = price * 0.003  # tolerancia 0.3%
        if abs(price - gamma_wall_up) < wall_tol and price > sma:
            return "WALL_REJECTION_UP", 2
        if abs(price - gamma_wall_down) < wall_tol and price < sma:
            return "WALL_REJECTION_DOWN", 2

        # ── Walking the bands (tendencia sostenida) ─────────────
        if pct_b > 0.9:
            return "WALKING_UPPER", 1
        if pct_b < 0.1:
            return "WALKING_LOWER", 1

        return "NEUTRAL", 0

    # ── DataFrame histórico ─────────────────────────────────────
    def to_dataframe(self) -> pd.DataFrame:
        if not self._history:
            return pd.DataFrame()
        df = pd.DataFrame(self._history)
        df.set_index("timestamp", inplace=True)
        return df


# ─────────────────────────────────────────────
# 3. CAPA DE ENRIQUECIMIENTO (pandas-ta)
# ─────────────────────────────────────────────


def enrich_bbgex(df: pd.DataFrame) -> pd.DataFrame:
    """
    Agrega indicadores técnicos complementarios al DataFrame BB-GEX.

    Columnas añadidas:
        rsi_14          : RSI para confirmar señales de reversión
        stoch_k / stoch_d : Estocástico para %B < 0.1 / > 0.9
        bandwidth_ema5  : EMA de bandwidth (detecta squeeze acelerando)
        pct_b_ema3      : %B suavizado (reduce señales falsas)
    """
    if df.empty:
        return df

    src = df["typical_price"]

    rsi = ta.rsi(src, length=14)
    if rsi is not None:
        df["rsi_14"] = rsi

    # Estocástico aproximado sobre precio típico
    stoch = ta.stoch(src, src, src, k=14, d=3, smooth_k=3)
    if stoch is not None and not stoch.empty:
        col_k = [c for c in stoch.columns if "STOCHk" in c]
        col_d = [c for c in stoch.columns if "STOCHd" in c]
        if col_k:
            df["stoch_k"] = stoch[col_k[0]]
        if col_d:
            df["stoch_d"] = stoch[col_d[0]]

    bw_ema = ta.ema(df["bandwidth"], length=5)
    if bw_ema is not None:
        df["bandwidth_ema5"] = bw_ema

    pctb_ema = ta.ema(df["pct_b"], length=3)
    if pctb_ema is not None:
        df["pct_b_ema3"] = pctb_ema

    return df


# ─────────────────────────────────────────────
# 4. GENERADOR DE DATOS DEMO
# ─────────────────────────────────────────────


def generate_demo_data(
    ticker: str = "AAPL",
    n: int = 390,
    base_price: float = 192.50,
    seed: int = 42,
) -> tuple[list[CandleBar], list[OptionsRegime]]:
    """
    Simula una sesión completa con 4 regímenes de mercado:
        - Mañana:    PINNED    (Gamma+ / IV baja)
        - Mediodía:  COILING   (Gamma− / IV baja) → squeeze inminente
        - Media tarde: TRENDING (Gamma− / IV alta) → explosión
        - Cierre:    CONTROLLED (Gamma+ / IV alta)
    """
    rng = np.random.default_rng(seed)
    start = pd.Timestamp("2025-01-15 09:30:00", tz="America/New_York")
    ts_range = pd.date_range(start, periods=n, freq="1min")

    # Parámetros por fase
    phases = [
        # (barras, trend,   vol_noise, gex,    iv_atm, iv_skew)
        (97, 0.00010, 0.0004, 2.0e6, 0.14, 0.03),  # PINNED
        (98, 0.00002, 0.0003, -0.3e6, 0.13, 0.04),  # COILING
        (98, -0.00030, 0.0012, -1.5e6, 0.38, 0.08),  # TRENDING
        (97, 0.00005, 0.0005, 0.8e6, 0.22, 0.02),  # CONTROLLED
    ]

    candles, opts = [], []
    price = base_price
    gamma_flip = base_price  # el Gamma Flip empieza en el precio inicial

    idx = 0
    for n_bars, trend, noise, gex_base, iv_base, skew_base in phases:
        for _ in range(n_bars):
            if idx >= n:
                break
            ts = ts_range[idx]

            # Precio
            ret = trend + rng.normal(0, noise)
            price *= 1 + ret
            spread = price * rng.uniform(0.0005, 0.0018)
            high = price + spread * rng.uniform(0.2, 1.0)
            low = price - spread * rng.uniform(0.2, 1.0)

            candles.append(
                CandleBar(
                    timestamp=ts,
                    ticker=ticker,
                    open=price * (1 - rng.uniform(0, 0.0002)),
                    high=high,
                    low=low,
                    close=price,
                    volume=float(rng.integers(60_000, 450_000)),
                )
            )

            # Régimen de opciones
            gex = gex_base + rng.normal(0, abs(gex_base) * 0.15)
            iv = max(0.08, iv_base + rng.normal(0, 0.02))
            skew = skew_base + rng.normal(0, 0.01)

            # El Gamma Flip se mueve levemente con el precio
            gamma_flip += rng.normal(0, 0.05)

            opts.append(
                OptionsRegime(
                    timestamp=ts,
                    ticker=ticker,
                    iv_atm=float(iv),
                    iv_25d_call=float(iv - abs(skew) / 2),
                    iv_25d_put=float(iv + abs(skew) / 2),
                    iv_term_1w=float(iv * (1 + rng.uniform(-0.05, 0.05))),
                    iv_term_1m=float(iv * (1 + rng.uniform(-0.08, 0.08))),
                    net_gex=float(gex),
                    gamma_flip=float(gamma_flip),
                    gamma_wall_up=float(price * rng.uniform(1.005, 1.02)),
                    gamma_wall_down=float(price * rng.uniform(0.98, 0.995)),
                )
            )
            idx += 1

    return candles, opts


# ─────────────────────────────────────────────
# 5. PIPELINE COMPLETO
# ─────────────────────────────────────────────


def run_bbgex_pipeline(
    ticker: str = "AAPL",
    n: int = 390,
    sigma_mode: str = "iv",
    verbose: bool = True,
) -> pd.DataFrame:

    print(f"\n{'═'*62}")
    print(f"  BB-GEX ENGINE  |  {ticker}  |  {n} velas 1m  |  σ={sigma_mode}")
    print(f"{'═'*62}")

    candles, opts = generate_demo_data(ticker, n)

    # Comparamos los 3 modos en paralelo
    engines = {
        "iv": BBGEXEngine(ticker, sigma_mode="iv"),
        "hybrid": BBGEXEngine(ticker, sigma_mode="hybrid"),
        "classic": BBGEXEngine(ticker, sigma_mode="classic"),
    }

    rows_main = []
    for candle, opt in zip(candles, opts, strict=False):
        row = engines[sigma_mode].update(candle, opt)
        rows_main.append(row)

        # Los otros dos modos se calculan para comparación
        for mode, eng in engines.items():
            if mode != sigma_mode:
                eng.update(candle, opt)

    df = pd.DataFrame(rows_main)
    df.set_index("timestamp", inplace=True)

    # Agregar columnas de los otros modos para comparar bandwidth
    for mode, eng in engines.items():
        if mode != sigma_mode:
            df_other = eng.to_dataframe()
            if not df_other.empty:
                df[f"bandwidth_{mode}"] = df_other["bandwidth"].values
                df[f"sigma_{mode}"] = df_other["sigma_used"].values
                df[f"upper_{mode}"] = df_other["upper"].values
                df[f"lower_{mode}"] = df_other["lower"].values

    df = enrich_bbgex(df)

    if verbose:
        _print_report(df, ticker, sigma_mode)

    return df


def _print_report(df: pd.DataFrame, ticker: str, mode: str):
    print(f"\n── Resumen BB-GEX {ticker} (modo: {mode}) ──────────────")
    last = df.iloc[-1]
    print(f"  Precio final       : ${last['close']:.2f}")
    print(f"  SMA({20})           : ${last['sma']:.4f}")
    print(f"  Banda superior     : ${last['upper']:.4f}")
    print(f"  Banda inferior     : ${last['lower']:.4f}")
    print(f"  σ usada            : ${last['sigma_used']:.4f}")
    print(f"  σ histórica        : ${last['sigma_hist']:.4f}")
    print(f"  σ de IV            : ${last['sigma_iv']:.4f}")
    print(f"  Multiplicador k    : {last['k_multiplier']:.2f} ({last['k_reason']})")
    print(f"  Bandwidth actual   : {last['bandwidth']:.3f}%")
    print(f"  %B actual          : {last['pct_b']:.4f}")
    print(f"  Régimen actual     : {last['regime']}")
    print(f"  Cruces Gamma Flip  : {last['flip_count']}")
    print(f"  IV ATM             : {last['iv_atm']:.2%}")

    print("\n── Distribución de regímenes ──")
    print(df["regime"].value_counts().to_string())

    print("\n── Distribución de señales ──")
    print(df["signal"].value_counts().to_string())

    # Señales fuertes
    strong = df[df["signal_strength"] >= 2]
    print(f"\n── Señales fuerza ≥ 2 ({len(strong)} eventos) ──")
    if not strong.empty:
        cols = [
            "close",
            "sma",
            "upper",
            "lower",
            "k_multiplier",
            "bandwidth",
            "pct_b",
            "regime",
            "signal",
            "signal_strength",
        ]
        print(strong[cols].tail(12).to_string())

    # Comparación de bandwidth entre modos
    bw_cols = [c for c in df.columns if "bandwidth" in c]
    if len(bw_cols) > 1:
        print("\n── Comparación bandwidth promedio por modo ──")
        for col in bw_cols:
            print(f"  {col:22s}: {df[col].mean():.4f}%")

    # Cruces del Gamma Flip
    flips = df[df["gamma_flip_cross"] == True]
    print(f"\n── Cruces del Gamma Flip detectados: {len(flips)} ──")
    if not flips.empty:
        print(flips[["close", "k_multiplier", "regime", "signal"]].to_string())

    print(f"\n{'═'*62}")


# ─────────────────────────────────────────────
# 6. INTEGRACIÓN PRODUCCIÓN
# ─────────────────────────────────────────────


class BBGEXLive:
    """
    Wrapper listo para conectar BingX WebSocket + Massive API.

    Uso en el bot:
        engine = BBGEXLive("AAPL", sigma_mode="iv")

        # En el callback de BingX 1m:
        def on_candle(raw_bingx: dict):
            candle = BBGEXLive.parse_bingx_candle(raw_bingx)
            opt    = BBGEXLive.parse_massive_regime("AAPL", raw_massive)
            result = engine.core.update(candle, opt)
            engine.on_signal(result)
    """

    SIGNAL_PRIORITY = {
        "GAMMA_FLIP_LONG": 5,
        "GAMMA_FLIP_SHORT": 5,
        "SQUEEZE_BREAK_LONG": 4,
        "SQUEEZE_BREAK_SHORT": 4,
        "REVERSAL_LONG": 3,
        "REVERSAL_SHORT": 3,
        "WALL_REJECTION_UP": 2,
        "WALL_REJECTION_DOWN": 2,
        "COILING": 2,
        "WALKING_UPPER": 1,
        "WALKING_LOWER": 1,
        "NEUTRAL": 0,
    }

    def __init__(self, ticker: str, sigma_mode: str = "iv", **kwargs):
        self.ticker = ticker
        self.core = BBGEXEngine(ticker=ticker, sigma_mode=sigma_mode, **kwargs)

    @staticmethod
    def parse_bingx_candle(raw: dict) -> CandleBar:
        """
        Formato BingX WebSocket 1m kline:
        { "T": 1705312260000, "o": "192.45", "h": "192.89",
          "l": "192.30", "c": "192.75", "v": "125430", "s": "AAPL-USDT" }
        """
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
    def parse_massive_regime(ticker: str, raw: dict) -> OptionsRegime:
        """
        Campos esperados de Massive API para régimen de opciones:
        {
            "ivAtm": 0.22,
            "iv25DCall": 0.20, "iv25DPut": 0.25,
            "ivTerm1W": 0.21,  "ivTerm1M": 0.23,
            "netGex": 1500000,
            "gammaFlip": 192.00,
            "gammaWallUp": 195.00, "gammaWallDown": 189.00
        }
        """
        return OptionsRegime(
            timestamp=pd.Timestamp.now(tz="UTC"),
            ticker=ticker,
            iv_atm=float(raw.get("ivAtm", 0.20)),
            iv_25d_call=float(raw.get("iv25DCall", 0.18)),
            iv_25d_put=float(raw.get("iv25DPut", 0.23)),
            iv_term_1w=float(raw.get("ivTerm1W", 0.20)),
            iv_term_1m=float(raw.get("ivTerm1M", 0.22)),
            net_gex=float(raw.get("netGex", 0)),
            gamma_flip=float(raw.get("gammaFlip", 0)),
            gamma_wall_up=float(raw.get("gammaWallUp", 0)),
            gamma_wall_down=float(raw.get("gammaWallDown", 0)),
        )

    def on_signal(self, result: dict):
        """Hook de señal — conectar a la lógica de órdenes del bot."""
        priority = self.SIGNAL_PRIORITY.get(result["signal"], 0)
        if priority >= 2:
            print(
                f"[{result['timestamp']}] {self.ticker:5s} | "
                f"P{priority} {result['signal']:22s} | "
                f"${result['close']:.2f} | "
                f"k={result['k_multiplier']:.1f}σ | "
                f"BW={result['bandwidth']:.2f}% | "
                f"{result['regime']}"
            )


# ─────────────────────────────────────────────
# 7. EJECUCIÓN DEMO
# ─────────────────────────────────────────────

if __name__ == "__main__":
    TICKERS = ["AAPL", "MSFT", "PLTR", "TSLA", "GOOGL"]
    all_results = {}

    for ticker in TICKERS:
        df = run_bbgex_pipeline(
            ticker=ticker,
            n=390,
            sigma_mode="iv",  # cambiar a "hybrid" o "classic" para comparar
            verbose=True,
        )
        all_results[ticker] = df
        df.to_csv(f"/tmp/bbgex_{ticker.lower()}.csv")

    print("\n✓ BB-GEX completado para los 5 proxies BingX.\n")
