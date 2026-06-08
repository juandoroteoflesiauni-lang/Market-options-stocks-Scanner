"""Motor de Orquestación Volume Profile & AVWAP — Sector Técnico.

Combina el cálculo de histogramas de volumen de alta fidelidad con el precio medio ponderado
por volumen anclado (AVWAP) para identificar niveles institucionales de soporte y resistencia.
"""

from __future__ import annotations

import logging
from typing import List, Optional, Tuple

import numpy as np
import pandas as pd
from pydantic import BaseModel, ConfigDict

# Importamos la lógica base del especialista técnico local
from .volume import VolumeAnalytics, VolumeProfileResult

logger = logging.getLogger("quantum_analyzer.volume_profile")


class VolumeProfileOutput(BaseModel):
    """Resultado integrado del Perfil de Volumen y AVWAP."""
    model_config = ConfigDict(frozen=True, extra="ignore")

    ok: bool
    error:         str | None = None
    poc:           float = 0.0  # Point of Control
    vah:           float = 0.0  # Value Area High
    val:           float = 0.0  # Value Area Low
    avwap:         float = 0.0  # Anchored VWAP
    avwap_anchor_date: str = ""
    is_above_avwap: bool = False
    is_above_poc:   bool = False
    volume_bias:    str = "NEUTRAL"  # BULLISH | BEARISH | NEUTRAL


class VolumeProfileEngine:
    """
    Orquestador para cálculos de Perfil de Volumen y VWAP Anclado.
    """

    @staticmethod
    def calculate(
        df: pd.DataFrame,
        anchor_date: str | None = None
    ) -> VolumeProfileOutput:
        """
        Calcula POC, VAH/VAL y AVWAP para un DataFrame OHLCV.

        Parameters
        ----------
        df          : DataFrame con columnas OHLCV.
        anchor_date : Fecha de anclaje opcional 'YYYY-MM-DD' (ej. Earnings).
                      Si es None, se ancla al inicio de los datos proporcionados.
        """
        if df.empty:
            return VolumeProfileOutput(ok=False, error="Empty DataFrame")

        try:
            # 1. Perfil de Volumen (reutilizando kernel local optimizado)
            profile = VolumeAnalytics.compute_profile(df)
            if not profile.success:
                return VolumeProfileOutput(ok=False, error=profile.error)

            # 2. Anchored VWAP
            # Asegurar que el índice es datetime para filtrado preciso
            if not isinstance(df.index, pd.DatetimeIndex):
                df = df.copy()
                df.index = pd.to_datetime(df.index)

            if anchor_date:
                anchor_ts = pd.to_datetime(anchor_date)
                view_df = df[df.index >= anchor_ts]
            else:
                view_df = df
                anchor_date = str(df.index[0].date())

            if view_df.empty:
                return VolumeProfileOutput(ok=False, error=f"No data after anchor {anchor_date}")

            # Fórmula: Sum(Typical Price * Volume) / Sum(Volume)
            # Typical Price = (H+L+C)/3
            tp = (view_df["high"] + view_df["low"] + view_df["close"]) / 3.0
            pv = tp * view_df["volume"]
            avwap = pv.sum() / (view_df["volume"].sum() + 1e-9)

            last_close = float(df["close"].iloc[-1])

            # 3. Lógica de Sesgo (Volume Bias)
            # Bullish si el cierre está por encima del AVWAP y del POC
            is_above_avwap = last_close > avwap
            is_above_poc   = last_close > profile.poc

            bias = "NEUTRAL"
            if is_above_avwap and is_above_poc:
                bias = "BULLISH"
            elif not is_above_avwap and not is_above_poc:
                bias = "BEARISH"

            return VolumeProfileOutput(
                ok                = True,
                poc               = round(float(profile.poc), 4),
                vah               = round(float(profile.value_area_high), 4),
                val               = round(float(profile.value_area_low), 4),
                avwap             = round(float(avwap), 4),
                avwap_anchor_date = anchor_date or "",
                is_above_avwap    = is_above_avwap,
                is_above_poc      = is_above_poc,
                volume_bias       = bias
            )

        except Exception as exc:
            logger.error("[VolumeProfileEngine] Fallo en cálculo: %s", exc)
            return VolumeProfileOutput(ok=False, error=str(exc))


# ─────────────────────────────────────────────────────────
# MIGRATION AUDIT — SECTOR: TÉCNICO
# Archivo      : volume_profile.py
# Sub-capa     : Engine (Orquestador de Volumen)
# Eliminado    : Referencias QuantumBeta.
# Corregido    : Inconsistencia entre VolumeProfileResult y VolumeProfileOutput (renombrado a Output).
# Preservado   : Lógica de sesgo (Bias) basada en confluencia POC/AVWAP.
# ─────────────────────────────────────────────────────────
