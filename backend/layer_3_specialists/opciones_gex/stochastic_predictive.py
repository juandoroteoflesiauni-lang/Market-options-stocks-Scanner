"""Motor Predictivo Estocástico — Sector Opciones/GEX.

Orquestador de proyecciones probabilísticas utilizando simulación Monte Carlo
de alta fidelidad con modelos de saltos (Merton) y volatilidad estocástica (Heston).
"""

from __future__ import annotations

import importlib
from datetime import datetime, timedelta
from typing import Any

import numpy as np

from ...config.logger_setup import get_logger
from .stochastic_models import FanChart, StochasticPredictiveResult

# Configuración de Logging institucional
logger = get_logger(__name__)


class StochasticPredictiveEngine:
    """Orquestador Stateless para proyecciones estocásticas de precio."""

    @staticmethod
    def analyze(
        ticker: str, ohlcv: Any, iv_series: Any = None, horizon_days: int = 30, n_paths: int = 2000
    ) -> StochasticPredictiveResult:
        """
        Ejecuta el análisis proyectivo completo para un ticker.
        Combina MJD (Merton Jump-Diffusion) con dinámica Vol-of-Vol de Heston.
        """
        try:
            prob_engine = importlib.import_module(
                "backend.layer_3_specialists.ia_probabilistico.engines.probabilistic_engine"
            )
            estimate_mjd_params = prob_engine.estimate_mjd_params
            calibrate_heston_vov = prob_engine.calibrate_heston_vov
            project_trajectories = prob_engine.project_trajectories
            run_particle_filter = prob_engine.run_particle_filter
            pd = importlib.import_module("pandas")

            # 1. Validación de Datos
            if ohlcv is None or len(ohlcv) < 30:
                return StochasticPredictiveResult(
                    ticker=ticker, ok=False, error="Insufficient data (min 30 bars)"
                )

            # Extracción de retornos y precio actual
            close_prices = ohlcv["close"].values
            returns = np.diff(np.log(np.maximum(close_prices, 1e-6)))
            current_price = float(close_prices[-1])

            # 2. Estimación de Parámetros de Saltos (Merton)
            mjd_params = estimate_mjd_params(returns)

            # 3. Calibración de Vol-of-Vol (Heston)
            vov = 0.5  # Default institucional
            if iv_series is not None and not iv_series.empty:
                # Si tenemos IV, la usamos para calibrar la volatilidad de la volatilidad
                vov = calibrate_heston_vov(returns, iv_series.values)

            # 4. Estimación de Volatilidad Latente (Particle Filter)
            # El filtro de partículas es superior a la std móvil en regímenes no estacionarios
            # MIGRATION: Se utiliza run_particle_filter con la lógica de 60 días
            regime_state = run_particle_filter(ohlcv.tail(60))
            pr_ordered = regime_state.pr_ordered
            estimated_var = np.var(returns[-60:]) * (1.1 if pr_ordered < 0.5 else 0.9)
            sigma = np.sqrt(estimated_var)

            # 5. Simulación Monte Carlo de Trayectorias
            paths = project_trajectories(
                current_price=current_price,
                returns=returns,
                mjd_params=mjd_params,
                sigma=sigma,
                vov=vov,
                horizon_days=horizon_days,
                n_paths=n_paths,
            )

            # 6. Extracción de Métricas y Fan Chart
            # Fan Chart extraction (Integrated logic)
            percentiles = [10, 25, 50, 75, 90]
            fan_data = {f"p{p}": np.percentile(paths, p, axis=0).tolist() for p in percentiles}

            # Generación de Timestamps ISO
            last_date = ohlcv.index[-1]
            if isinstance(last_date, str):
                last_dt = datetime.fromisoformat(last_date.replace("Z", "+00:00"))
            else:
                last_dt = datetime.fromisoformat(str(pd.to_datetime(last_date).isoformat()))

            ts_list = [(last_dt + timedelta(days=i)).isoformat() for i in range(horizon_days + 1)]

            fan_chart = FanChart(
                p10=fan_data["p10"],
                p25=fan_data["p25"],
                p50=fan_data["p50"],
                p75=fan_data["p75"],
                p90=fan_data["p90"],
                timestamps=ts_list,
            )

            # 7. Determinación de Drift Bias
            # Basado en la mediana del horizonte vs precio actual con umbral de 5%
            ev_horizon = fan_data["p50"][-1]
            drift = "NEUTRAL"
            if ev_horizon > current_price * 1.05:
                drift = "BULLISH"
            elif ev_horizon < current_price * 0.95:
                drift = "BEARISH"

            return StochasticPredictiveResult(
                ticker=ticker,
                jump_intensity=mjd_params["jump_intensity"],
                vol_of_vol=vov,
                fan_chart=fan_chart,
                drift_bias=drift,
                expected_value_horizon=round(ev_horizon, 4),
            )

        except Exception as e:
            logger.error(f"Error en StochasticPredictiveEngine para {ticker}: {e!s}")
            return StochasticPredictiveResult(
                ticker=ticker, ok=False, error=f"Engine failure: {e!s}"
            )


# ─────────────────────────────────────────────────────────────
# MIGRATION AUDIT — SECTOR: OPCIONES
# Archivo      : stochastic_predictive.py
# Sub-capa     : Specialist (Predictive Analyst)
# Eliminado    : Imports legacy de 'quantumbeta.math.probabilistic'.
# Inyectado    : Integración con StochasticKernels de Capa 2.
# Preservado   : Lógica de Drift Bias y granularidad Monte Carlo (2000 paths).
# ─────────────────────────────────────────────────────────────
