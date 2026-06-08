"""Motor Fractal FVG con Gate de Entropía — Sector Técnico.

Implementa la metodología de análisis Fractal FVG filtrada por Entropía de Shannon
para identificar regímenes de mercado ordenados y confluencias institucionales.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime, timezone
from typing import Final, Optional

import numpy as np
import pandas as pd

from .fractal_models import EntropyScore, FractalSignal

# Importamos el núcleo matemático local y los modelos
from .technical import TechnicalMath

logger = logging.getLogger("quantum_analyzer.smc_fractal")

# ─────────────────────────────────────────────────────────────────────────────
# §0  PARÁMETROS CALIBRADOS
# ─────────────────────────────────────────────────────────────────────────────

_ENTROPY_WINDOW:    Final[int] = 20
_ENTROPY_BINS:      Final[int] = 10
_ENTROPY_THRESHOLD: Final[float] = 3.2  # H < 3.2 = Mercado ordenado/tendencial
_DISPLACEMENT_MULT: Final[float] = 1.4  # Ratio cuerpo/ATR institucional
_FVG_MIN_SIZE_ATR:  Final[float] = 0.5


class SMCFractalEngine:
    """
    Motor SMC de Confluencia Fractal.

    Implementa el 'Entropy Gate' para asegurar que las rupturas estructurales
    ocurran en un régimen informacional ordenado.
    """

    @staticmethod
    def analyze(
        df_ohlcv: pd.DataFrame,
        ticker: str,
    ) -> FractalSignal:
        """
        Ejecuta el análisis Fractal FVG completo.
        """
        try:
            # 1. Preparación de datos (NumPy view)
            h = df_ohlcv["high"].to_numpy()
            l = df_ohlcv["low"].to_numpy()
            c = df_ohlcv["close"].to_numpy()

            # 2. Entropy Gate (Filtro de Caos)
            # Utilizamos retornos logarítmicos para capturar la complejidad informacional
            log_returns = np.log(c[1:] / (c[:-1] + 1e-12))
            entropy_series = TechnicalMath.shannon_entropy(log_returns, n=_ENTROPY_WINDOW, bins=_ENTROPY_BINS)

            # Alineamos el array de entropía con la longitud de OHLCV
            entropy_full = np.concatenate([[np.nan], entropy_series])
            current_h = float(entropy_full[-1]) if not np.isnan(entropy_full[-1]) else 4.0

            is_ordered = current_h < _ENTROPY_THRESHOLD

            # 3. Detección de FVG Fractal (Patrón de 3 barras)
            #    BULLISH: L[t] > H[t-2]  → gap alcista
            #    BEARISH: H[t] < L[t-2]  → gap bajista (espejo)
            fvg_active = False
            fvg_direction = "NEUTRAL"
            fvg_size = 0.0

            if len(l) >= 3:
                if l[-1] > h[-3]:
                    fvg_active = True
                    fvg_direction = "BULLISH"
                    fvg_size = l[-1] - h[-3]
                elif h[-1] < l[-3]:
                    fvg_active = True
                    fvg_direction = "BEARISH"
                    fvg_size = l[-3] - h[-1]

            # 4. Desplazamiento Institucional (δ) + dirección de la vela previa
            atr = TechnicalMath.atr(c, h, l, n=10)
            body = np.abs(df_ohlcv["close"] - df_ohlcv["open"]).to_numpy()

            displacement = 0.0
            if len(body) >= 2 and not np.isnan(atr[-2]) and atr[-2] > 0:
                displacement = body[-2] / atr[-2]

            is_institutional = displacement >= _DISPLACEMENT_MULT
            # Dirección del último impulso institucional
            prev_close = df_ohlcv["close"].iloc[-2] if len(df_ohlcv) >= 2 else c[-1]
            prev_open  = df_ohlcv["open"].iloc[-2]  if len(df_ohlcv) >= 2 else c[-1]
            impulse_bullish = prev_close > prev_open
            impulse_bearish = prev_close < prev_open

            # 5. Composición bidireccional
            # La señal es LONG/SHORT solo si el régimen es ordenado Y existe
            # confirmación institucional (FVG direccional o desplazamiento δ).
            bias = "CASH"
            if is_ordered:
                bullish_evidence = (fvg_active and fvg_direction == "BULLISH") or (is_institutional and impulse_bullish)
                bearish_evidence = (fvg_active and fvg_direction == "BEARISH") or (is_institutional and impulse_bearish)
                if bullish_evidence and not bearish_evidence:
                    bias = "LONG"
                elif bearish_evidence and not bullish_evidence:
                    bias = "SHORT"


            return FractalSignal(
                ticker=ticker,
                timestamp=datetime.now(UTC),
                bias=bias,
                fvg_size=float(fvg_size),
                entropy_score=float(current_h),
                is_fvg_active=fvg_active
            )

        except Exception as exc:
            logger.exception("[SMCFractal] Error en análisis para %s: %s", ticker, exc)
            return FractalSignal(
                ticker=ticker,
                timestamp=datetime.now(UTC),
                bias="CASH",
                fvg_size=0.0,
                entropy_score=4.0,
                is_fvg_active=False
            )

    @staticmethod
    def get_entropy_state(df: pd.DataFrame, ticker: str) -> EntropyScore:
        """
        Medición aislada del estado de entropía para el guardián de orquestación.
        """
        c = df["close"].to_numpy()
        log_returns = np.log(c[1:] / (c[:-1] + 1e-12))
        h_series = TechnicalMath.shannon_entropy(log_returns, n=_ENTROPY_WINDOW, bins=_ENTROPY_BINS)

        current_val = float(h_series[-1]) if len(h_series) > 0 and not np.isnan(h_series[-1]) else 4.0

        # Cálculo de Z-Score relativo a la ventana histórica (Métrica de anomalía)
        z = 0.0
        if len(h_series) > 50:
            baseline = h_series[~np.isnan(h_series)][-50:]
            if len(baseline) > 10:
                mu, sigma = np.mean(baseline), np.std(baseline)
                if sigma > 1e-6:
                    z = (current_val - mu) / sigma

        return EntropyScore(
            ticker=ticker,
            timestamp=datetime.now(UTC),
            value=current_val,
            z_score=float(z),
            is_ordered=current_val < _ENTROPY_THRESHOLD
        )


# ─────────────────────────────────────────────────────────
# MIGRATION AUDIT — SECTOR: TÉCNICO
# Archivo      : smc_fractal_engine.py
# Sub-capa     : Engine (Confluencia Fractal)
# Eliminado    : Referencias QuantumBeta / FractalFVG.
# Renombrado   : SMCFractalEngine (para evitar colisión con SMCEngine).
# Conectado    : TechnicalMath.shannon_entropy (Local).
# Preservado   : Entropy Gate (H < 3.2), Fractal FVG logic.
# ─────────────────────────────────────────────────────────
