"""
============================================================
  VolatilityTermStructureEngine
  Quant Analytics · Equity Options · IV Term Structure
============================================================

Referencia académica:
  Vasquez (2015) – "Equity Volatility Term Structures and the
  Cross-Section of Option Returns"
  → Documenta que la PENDIENTE de la curva de IV predice
    retornos futuros de straddles: curvas en contango fuerte
    anticipan reversión de volatilidad; curvas invertidas
    (backwardation) señalan stress/pánico de mercado.

Nota sobre "Flat Curve":
  Una curva que se aplana (slope → 0) suele PRECEDER expansión
  de volatilidad porque el mercado deja de descontar un
  diferencial temporal: los market-makers comprimen los precios
  de opciones largas y la incertidumbre de corto plazo sube.
  Históricamente, períodos de IV plana han precedido en
  2-4 semanas eventos de volatility spike (p.ej., Lunes Negro
  1987, Flash Crash 2010, selloffs post-FOMC).
============================================================
"""

import logging
import warnings
from datetime import datetime, timedelta
from typing import Any

import numpy as np
import pandas as pd  # type: ignore[import-not-found, import-untyped]
from scipy.interpolate import CubicSpline  # type: ignore[import-not-found, import-untyped]

warnings.filterwarnings("ignore")

logger = logging.getLogger(__name__)


# ─────────────────────────────────────────────────────────
#  DATOS SINTÉTICOS DE DEMOSTRACIÓN
#  (Reemplazar con feed real: OptionMetrics, CBOE, yfinance…)
# ─────────────────────────────────────────────────────────
def generate_synthetic_option_chain(
    base_iv: float = 0.22,
    regime: str = "contango",  # "contango" | "backwardation" | "flat"
    noise_scale: float = 0.005,
    n_history_days: int = 35,
) -> pd.DataFrame:
    """
    Genera cadena de opciones ATM sintética para demostración.
    Cada fila representa (fecha_snapshot, días_al_vencimiento, IV_atm).

    En producción reemplaza esta función con tu feed de datos:
        df = load_from_optionmetrics(ticker, start, end)
    """
    rng = np.random.default_rng(42)
    raw_expirations = [7, 14, 21, 30, 45, 60, 90, 120, 180, 270, 360]

    records = []
    for day_offset in range(n_history_days, -1, -1):
        snap_date = datetime.today() - timedelta(days=day_offset)

        # Simula evolución temporal del régimen de IV
        t_norm = day_offset / n_history_days  # 1 → 0 (pasado → hoy)
        regime_drift = rng.normal(0, noise_scale)

        for dte in raw_expirations:
            if regime == "contango":
                # IV sube con el plazo (normal / calma)
                iv = base_iv + 0.03 * np.log1p(dte / 30) + regime_drift
            elif regime == "backwardation":
                # IV de corto plazo SUPERA largo plazo (pánico)
                iv = base_iv + 0.06 * np.exp(-dte / 45) + regime_drift
            else:  # flat
                # Curva plana con leve pendiente positiva
                iv = base_iv + 0.008 * (dte / 180) + regime_drift

            # Añade ruido realista cross-seccional
            iv += rng.normal(0, noise_scale * 0.5)
            iv = max(iv, 0.05)  # IV mínima del 5%

            records.append(
                {
                    "snapshot_date": snap_date.strftime("%Y-%m-%d"),
                    "dte": dte,  # días al vencimiento
                    "iv_atm": round(iv, 6),  # IV at-the-money (decimal)
                }
            )

    return pd.DataFrame(records)


# ─────────────────────────────────────────────────────────
#  ENGINE PRINCIPAL
# ─────────────────────────────────────────────────────────
class VolatilityTermStructureEngine:
    """
    Motor de análisis de la Estructura Temporal de Volatilidad Implícita.

    Parámetros
    ----------
    standard_tenors : list[int]
        Plazos estándar (días) a los que se interpolará la IV.
        Default: [7, 30, 60, 90, 180, 360]
    short_tenor : int
        Tenor que define el "corto plazo" para métricas (días).
    long_tenor : int
        Tenor que define el "largo plazo" para métricas (días).
    zscore_window : int
        Ventana (días) para calcular el Z-Score de la pendiente.
    """

    STANDARD_TENORS = [7, 30, 60, 90, 180, 360]

    def __init__(
        self,
        standard_tenors: list[int] | None = None,
        short_tenor: int = 30,
        long_tenor: int = 90,
        zscore_window: int = 30,
    ):
        self.standard_tenors = standard_tenors or self.STANDARD_TENORS
        self.short_tenor = short_tenor
        self.long_tenor = long_tenor
        self.zscore_window = zscore_window

        # Almacenamiento interno
        self._raw_chain: pd.DataFrame | None = None
        self._term_structure: pd.DataFrame | None = None  # IV interpolada por fecha
        self._metrics_history: pd.DataFrame | None = None  # slope, ratio, zscore

    # ── 1. CARGA Y FILTRADO ────────────────────────────────
    def load_option_chain(self, df: pd.DataFrame) -> "VolatilityTermStructureEngine":
        """
        Carga la cadena de opciones.

        Columnas esperadas:
            snapshot_date (str|date) · dte (int, días) · iv_atm (float, decimal)

        Nota: El filtro ATM ya debe aplicarse antes de llamar este método
        (moneyness entre 0.95-1.05 según metodología Vasquez 2015).
        """
        required = {"snapshot_date", "dte", "iv_atm"}
        missing = required - set(df.columns)
        if missing:
            raise ValueError(f"Faltan columnas: {missing}")

        df = df.copy()
        df["snapshot_date"] = pd.to_datetime(df["snapshot_date"])
        df = df.sort_values(["snapshot_date", "dte"]).reset_index(drop=True)

        # Elimina IV fuera de rango razonable (filtro Vasquez: 3% – 200%)
        df = df[(df["iv_atm"] >= 0.03) & (df["iv_atm"] <= 2.00)]

        self._raw_chain = df
        logger.info(
            "Vol term chain loaded: %s snapshots, %s observations total.",
            df["snapshot_date"].nunique(),
            len(df),
        )
        return self

    # ── 2. INTERPOLACIÓN DE LA CURVA ──────────────────────
    def build_term_structure(self) -> "VolatilityTermStructureEngine":
        """
        Interpola la IV en los tenores estándar usando CubicSpline.

        Lógica de interpolación
        ───────────────────────
        • Para cada snapshot_date se ajusta una CubicSpline sobre
          (dte_observado, iv_atm_observado).
        • Si hay menos de 4 puntos se hace interpolación lineal (np.interp).
        • Los tenores fuera del rango observado se extrapolancon 'not-a-knot'.

        Ventaja sobre interpolación lineal pura:
        La spline cúbica mantiene segunda derivada continua, produciendo
        una curva más suave que refleja mejor el mercado real.
        """
        if self._raw_chain is None:
            raise RuntimeError("Primero llama load_option_chain().")

        records = []
        for snap_date, group in self._raw_chain.groupby("snapshot_date"):
            group = group.sort_values("dte")
            dte_obs = group["dte"].values.astype(float)
            iv_obs = group["iv_atm"].values.astype(float)

            row = {"snapshot_date": snap_date}

            if len(dte_obs) >= 4:
                # CubicSpline 'not-a-knot': condición de frontera natural
                cs = CubicSpline(dte_obs, iv_obs, bc_type="not-a-knot", extrapolate=True)
                interp_fn = cs
            else:
                # Fallback: interpolación lineal con extrapolación constante
                interp_fn = lambda x: np.interp(x, dte_obs, iv_obs)  # noqa: E731

            for tenor in self.standard_tenors:
                iv_interp = float(interp_fn(tenor))
                iv_interp = max(iv_interp, 0.01)  # cota inferior 1%
                row[f"iv_{tenor}d"] = round(iv_interp, 6)

            records.append(row)

        self._term_structure = pd.DataFrame(records).sort_values("snapshot_date")
        logger.info(
            "Vol term structure built: %s rows, tenors=%s days.",
            len(self._term_structure),
            self.standard_tenors,
        )
        return self

    # ── 3. MÉTRICAS DE RÉGIMEN ────────────────────────────
    def compute_metrics(self) -> "VolatilityTermStructureEngine":
        """
        Calcula las métricas de régimen para cada snapshot:

        1. Contango/Backwardation Ratio
           ratio = IV_short / IV_long
           · ratio < 1  → Contango  (IV corto < IV largo): mercado tranquilo
           · ratio > 1  → Backwardation (IV corto > IV largo): stress/pánico

        2. Pendiente (Slope)
           slope = (IV_long − IV_short) / (long_tenor − short_tenor)
           · > 0 → Contango   · < 0 → Backwardation   · ≈ 0 → Flat

           Interpretación Vasquez (2015):
           Una pendiente positiva alta predice retornos positivos de straddles
           en el decil superior (estrategia long volatility).

        3. Curvatura (Convexidad)
           curvature = IV_medio − (IV_short + IV_long) / 2
           · Positiva → curva cóncava (humped): incertidumbre de medio plazo
           · Negativa → curva convexa: normalización anticipada

        4. Z-Score de la pendiente (ventana = zscore_window días)
           Detecta anomalías estadísticas en la pendiente actual.
        """
        if self._term_structure is None:
            raise RuntimeError("Primero llama build_term_structure().")

        ts = self._term_structure.copy()
        s_col = f"iv_{self.short_tenor}d"
        l_col = f"iv_{self.long_tenor}d"

        # Tenores disponibles para curvatura (mediano)
        available = [t for t in self.standard_tenors if self.short_tenor < t < self.long_tenor]
        mid_tenor = available[len(available) // 2] if available else self.short_tenor
        m_col = f"iv_{mid_tenor}d"

        # ── Métricas base ───────────────────────────────────
        ts["iv_short"] = ts[s_col]
        ts["iv_long"] = ts[l_col]
        ts["ratio"] = ts["iv_short"] / ts["iv_long"]
        ts["slope"] = (ts["iv_long"] - ts["iv_short"]) / (self.long_tenor - self.short_tenor)
        ts["curvature"] = ts[m_col] - (ts["iv_short"] + ts["iv_long"]) / 2

        # ── Z-Score rolling de la pendiente ─────────────────
        ts["slope_roll_mean"] = ts["slope"].rolling(self.zscore_window, min_periods=5).mean()
        ts["slope_roll_std"] = ts["slope"].rolling(self.zscore_window, min_periods=5).std()
        ts["slope_zscore"] = (ts["slope"] - ts["slope_roll_mean"]) / ts["slope_roll_std"].replace(
            0, np.nan
        )

        # ── Clasificación de régimen ─────────────────────────
        ts["regime"] = np.where(
            ts["ratio"] > 1.0,
            "⚠️  PANIC / BACKWARDATION",
            np.where(
                ts["slope"].abs() < 0.0003,  # umbral de "flat"
                "⚡ FLAT / PRE-EXPANSION",
                "✅ NORMAL / CONTANGO",
            ),
        )

        # ── Alerta de inversión ──────────────────────────────
        ts["inversion_alert"] = ts["ratio"] > 1.0

        self._metrics_history = ts.reset_index(drop=True)
        logger.info("Vol term metrics calculated. Current regime: %s", ts["regime"].iloc[-1])
        return self

    # ── 4. ALERTAS DEL SISTEMA ────────────────────────────
    def generate_alerts(self) -> dict[str, Any]:
        """
        Genera alertas accionables basadas en el snapshot más reciente.

        Retorna diccionario con:
            - regime          : clasificación cualitativa
            - inversion_alert : bool (Backwardation activa)
            - slope_zscore    : desviaciones estándar respecto media 30d
            - flat_warning    : bool (pendiente anormalmente plana)
            - summary_msg     : mensaje ejecutivo
        """
        if self._metrics_history is None:
            raise RuntimeError("Primero llama compute_metrics().")

        latest = self._metrics_history.iloc[-1]
        zscore = latest["slope_zscore"]
        slope = latest["slope"]
        ratio = latest["ratio"]
        regime = latest["regime"]

        # Umbral de flat: pendiente menor a 0.5 puntos base por día
        flat_threshold = 0.0003
        flat_warning = abs(slope) < flat_threshold

        # Construcción del mensaje ejecutivo
        if latest["inversion_alert"]:
            msg = (
                f"🚨 ALERTA CRÍTICA — VOLATILITY INVERSION\n"
                f"   IV corto ({self.short_tenor}d): {latest['iv_short']:.2%}  >  "
                f"IV largo ({self.long_tenor}d): {latest['iv_long']:.2%}\n"
                f"   Ratio: {ratio:.3f}  |  Slope: {slope*10000:.2f} bps/día\n"
                f"   → Mercado en modo PÁNICO. Considerar estrategias "
                f"     de short vega en plazos largos."
            )
        elif flat_warning:
            msg = (
                f"⚡ ALERTA — CURVA PLANA (Precursor de Expansión)\n"
                f"   Slope: {slope*10000:.2f} bps/día  (umbral: "
                f"{flat_threshold*10000:.1f} bps/día)\n"
                f"   → Históricamente el aplanamiento de la curva precede\n"
                f"     expansiones de volatilidad en 2-4 semanas.\n"
                f"   → Considerar compra de volatilidad (long straddle / vega)."
            )
        else:
            msg = (
                f"✅ MERCADO NORMAL — CONTANGO\n"
                f"   Slope: {slope*10000:.2f} bps/día  |  "
                f"Ratio: {ratio:.3f}\n"
                f"   → Estructura temporal saludable. "
                f"     Vendedores de volatilidad en ventaja."
            )

        if not np.isnan(zscore):
            if abs(zscore) > 2.0:
                msg += f"\n   ⚠️  Z-Score slope: {zscore:.2f}σ — " f"ANOMALÍA ESTADÍSTICA DETECTADA"
            else:
                msg += f"\n   Z-Score slope: {zscore:.2f}σ (rango normal)"

        alerts = {
            "regime": regime,
            "inversion_alert": bool(latest["inversion_alert"]),
            "slope_zscore": round(float(zscore) if not np.isnan(zscore) else 0, 3),
            "flat_warning": flat_warning,
            "ratio": round(float(ratio), 4),
            "slope_bps": round(float(slope) * 10_000, 3),
            "summary_msg": msg,
        }
        return alerts
