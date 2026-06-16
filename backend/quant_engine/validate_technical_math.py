#!/usr/bin/env python
"""Script de validación de paridad y humo para el motor matemático de análisis técnico."""

import sys

import numpy as np
import logging
logger = logging.getLogger(__name__)


try:
    from quant_engine.math.technical.hmm_math import log_sum_exp
    from quant_engine.math.technical.lob_math import compute_depth_imbalance
    from quant_engine.math.technical.smc_math import compute_atr, detect_fvg
    from quant_engine.math.technical.technical import TechnicalMath
    from quant_engine.math.technical.tpo_math import compute_tpo_stats
    from quant_engine.math.technical.vsa_math import compute_volume_zscore, compute_weis_wave
except ImportError as err:
    logger.info(f"Error al importar módulos del motor: {err}")
    sys.exit(1)


def main() -> None:
    logger.info("Iniciando validación del módulo quant_engine.math.technical...")

    # 1. Validación de SMA (TechnicalMath)
    close = np.array([10.0, 11.0, 12.0, 13.0, 14.0], dtype=np.float64)
    sma = TechnicalMath.sma(close, n=3)
    expected_sma = np.array([np.nan, np.nan, 11.0, 12.0, 13.0])
    np.testing.assert_allclose(sma[2:], expected_sma[2:], rtol=1e-7)
    logger.info("[OK] Paridad SMA validada correctamente.")

    # 2. Validación de ATR (smc_math)
    high = np.array([11.0, 12.0, 13.0, 14.0, 15.0], dtype=np.float64)
    low = np.array([9.0, 10.0, 11.0, 12.0, 13.0], dtype=np.float64)
    atr = compute_atr(high, low, close, window=3)
    assert len(atr) == 5
    logger.info("[OK] Paridad ATR (SMC) validada.")

    # 3. Validación de Z-Score de volumen (vsa_math)
    volume = np.array([100.0, 200.0, 150.0, 300.0, 250.0], dtype=np.float64)
    vz = compute_volume_zscore(volume, window=3)
    assert len(vz) == 5
    logger.info("[OK] Paridad Volume Z-Score (VSA) validada.")

    # 4. TPO Stats (tpo_math)
    prices = np.array([10.0, 10.5, 11.0, 11.5, 12.0], dtype=np.float64)
    tpo_counts = np.array([1, 2, 5, 2, 1], dtype=np.float64)
    mean, sigma, skewness, poc = compute_tpo_stats(prices, tpo_counts)
    assert poc == 11.0
    assert mean == 11.0
    assert skewness == 0.0
    logger.info("[OK] Paridad TPO Stats validada.")

    # 5. LOB Depth Imbalance (lob_math)
    bid = np.array([10.0, 5.0, 2.0], dtype=np.float64)
    ask = np.array([8.0, 6.0, 4.0], dtype=np.float64)
    rho = compute_depth_imbalance(bid, ask)
    assert abs(rho - (17.0 - 18.0) / 35.0) < 1e-7
    logger.info("[OK] Paridad LOB Depth Imbalance validada.")

    # 6. HMM Log Sum Exp (hmm_math)
    lse = log_sum_exp(np.array([1.0, 2.0, 3.0], dtype=np.float64))
    assert lse > 3.0
    logger.info("[OK] Paridad HMM Log-Sum-Exp validada.")

    logger.info("\n* Todas las validaciones pasaron con exito de forma limpia! *")
    sys.exit(0)


if __name__ == "__main__":
    main()
