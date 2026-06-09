"""Orquestador de Análisis de Opciones — Sector Opciones/GEX.

El OptionsEngine consolida la lógica de superficies de volatilidad y exposición
para generar resultados de confluencia (MIC Ready). Coordina el cálculo de GEX,
VEX, CEX, Max Pain e identificación de niveles clave (Walls y ZGL).
"""

from __future__ import annotations

import math

import numpy as np
import numpy.typing as npt

from ...math.options.bsm import BlackScholesPricer
from ...math.options.derivatives import GEXMath
from ...math.options.gamma_flip_probability import estimate_gamma_flip_probability
from ...math.options.iv_primitives import atm_iv_from_chain
from ...domain.options.options_models import (
    DealerExposures,
    ExposureRegime,
    GreekSurface,
    OptionsResult,
    OptionsSignal,
    PDFAnalytics,
    PositioningMetrics,
)


class OptionsEngine:
    """Orquestador Stateless de Análisis de Opciones Institucional."""

    FloatArray = npt.NDArray[np.float64]

    @staticmethod
    def analyze_chain(
        ticker: str,
        spot: float,
        strikes: FloatArray,
        call_oi: FloatArray,
        put_oi: FloatArray,
        call_iv: FloatArray,
        put_iv: FloatArray,
        tte: float,
        atm_iv: float = 0.20,
        r: float = 0.04,
        populate_higher_greeks: bool = False,
        pdf_analytics: PDFAnalytics | None = None,
    ) -> OptionsResult:
        """
        Ejecuta el análisis completo de una cadena de opciones para una expiración.
        Retorna un OptionsResult consolidado (Stateless).
        """
        if len(strikes) == 0:
            return OptionsResult(
                ticker=ticker,
                surface=GreekSurface(),
                exposures=DealerExposures(),
                ok=False,
                error="Empty chain",
            )

        # 1. Cálculo de Exposición Gamma (GEX)
        net_gex, call_gex, put_gex = GEXMath.net_gex(
            strikes, call_oi, put_oi, call_iv, put_iv, spot, tte, r
        )
        total_gex = float(np.sum(net_gex))

        # 2. Identificación de Niveles Clave
        # Use argpartition for efficiency when we only need min/max
        if len(call_gex) > 0:
            cw_idx = int(np.argmax(call_gex))  # argmax is already O(n), but keeping for clarity
        else:
            cw_idx = 0

        zgl = GEXMath.zero_gamma_level(strikes, net_gex)

        # 3. Exposición Vanna y Charm
        vex, cex = GEXMath.vanna_cex_exposure(strikes, call_oi, put_oi, call_iv, put_iv, spot, tte)

        # 4. Regímenes de Exposición
        vex_reg = (
            ExposureRegime.BULLISH
            if vex > 0
            else (ExposureRegime.BEARISH if vex < 0 else ExposureRegime.NEUTRAL)
        )
        cex_reg = (
            ExposureRegime.BULLISH
            if cex > 0
            else (ExposureRegime.BEARISH if cex < 0 else ExposureRegime.NEUTRAL)
        )

        # 5. Heurística de squeeze (misma P(flip) que options_router: GBM → ZGL)
        spot_to_zgl = (spot - zgl) / max(spot, 1.0)
        iv_chain = float(atm_iv_from_chain(strikes, call_iv, put_iv, spot))
        if not math.isfinite(iv_chain) or iv_chain <= 0:
            iv_chain = max(atm_iv, 1e-6)
        dte_days = max(float(tte) * 365.0, 1.0)
        gamma_flip_p = estimate_gamma_flip_probability(spot, zgl, iv_chain, dte_days, r=r)
        sq_prob = GEXMath.squeeze_probability(total_gex, vex, gamma_flip_p, spot_to_zgl)

        surface = GreekSurface()
        if populate_higher_greeks:
            mid_iv = np.where(
                np.isfinite(call_iv) & np.isfinite(put_iv),
                (call_iv + put_iv) / 2.0,
                np.where(np.isfinite(call_iv), call_iv, put_iv),
            )
            mid_iv = np.where(np.isfinite(mid_iv) & (mid_iv > 0), mid_iv, max(atm_iv, 1e-4))
            surface = GreekSurface(
                speed=np.nan_to_num(BlackScholesPricer.speed_vec(spot, strikes, tte, r, mid_iv))
                .tolist(),
                zomma=np.nan_to_num(BlackScholesPricer.zomma_vec(spot, strikes, tte, r, mid_iv))
                .tolist(),
                color=np.nan_to_num(BlackScholesPricer.color_vec(spot, strikes, tte, r, mid_iv))
                .tolist(),
                ultima=np.nan_to_num(BlackScholesPricer.ultima_vec(spot, strikes, tte, r, mid_iv))
                .tolist(),
            )

        # 6. Construcción del Resultado
        return OptionsResult(
            ticker=ticker,
            surface=surface,
            exposures=DealerExposures(
                total_gex=total_gex,
                total_vex=vex,
                total_cex=cex,
                vex_regime=vex_reg,
                cex_regime=cex_reg,
            ),
            positioning=PositioningMetrics(
                max_gex_strike=float(strikes[cw_idx]),
                hhi_concentration=OptionsEngine._calculate_hhi(call_oi, put_oi),
            ),
            pdf_analytics=pdf_analytics or PDFAnalytics(),
            vanna_volatility_sensitivity=vex,
            charm_time_decay_acceleration=cex,
            options_mic_score=sq_prob * 100.0,  # MIC Score basado en probabilidad de squeeze
            ok=True,
        )

    @staticmethod
    def generate_signal(result: OptionsResult) -> OptionsSignal:
        """Convierte un resultado de análisis en una señal lista para MIC."""
        regime = ExposureRegime.NEUTRAL
        if result.exposures.total_gex > 0 and result.exposures.total_vex > 0:
            regime = ExposureRegime.BULLISH
        elif result.exposures.total_gex < 0 and result.exposures.total_vex < 0:
            regime = ExposureRegime.BEARISH

        return OptionsSignal(
            vex_score=result.exposures.total_vex,
            cex_score=result.exposures.total_cex,
            regime=regime,
        )

    @staticmethod
    def calculate_max_pain(
        strikes: FloatArray,
        call_oi: FloatArray,
        put_oi: FloatArray,
    ) -> float:
        """Punto de strike donde los tenedores de opciones sufren la mayor pérdida agregada."""
        if len(strikes) == 0:
            return float("nan")
        x = strikes.astype(np.float64)[:, np.newaxis]
        srow = strikes.astype(np.float64)[np.newaxis, :]
        coi = call_oi.astype(np.float64)
        poi = put_oi.astype(np.float64)
        call_loss = (np.maximum(x - srow, 0.0) * coi).sum(axis=1)
        put_loss = (np.maximum(srow - x, 0.0) * poi).sum(axis=1)
        total = call_loss + put_loss
        return float(strikes[int(np.argmin(total))])

    @staticmethod
    def _calculate_hhi(call_oi: FloatArray, put_oi: FloatArray) -> float:
        """Calcula el índice Herfindahl-Hirschman de concentración de Open Interest."""
        total = np.sum(call_oi + put_oi)
        if total <= 0:
            return 0.0
        shares = (call_oi + put_oi) / total
        return float(np.sum(shares**2))
