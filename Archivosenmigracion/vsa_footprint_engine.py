"""Motor de Huella de Liquidez (VSA Footprint) — Sector Técnico.

Identifica Nodos de Alto Volumen (HVN) y zonas de 'Trapped Traders' para el mapeo
de puntos de memoria institucional y niveles de liquidez activa.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime, timezone
from typing import List, Optional

import numpy as np
import pandas as pd
from pydantic import BaseModel, ConfigDict, Field

logger = logging.getLogger("quantum_analyzer.vsa_footprint")


class FootprintNode(BaseModel):
    """Nodo de alto volumen que representa interés institucional o órdenes atrapadas."""
    model_config = ConfigDict(frozen=True, extra="ignore")

    price:      float
    volume:     float
    bar_index:  int
    is_support: bool = True
    is_active:  bool = True


class VSAFootprintResult(BaseModel):
    """Resultado del análisis de huella de liquidez."""
    model_config = ConfigDict(frozen=True, extra="ignore")

    ticker:     str
    timestamp:  datetime

    active_levels: list[FootprintNode] = Field(default_factory=list)
    nearest_support:    float | None = None
    nearest_resistance: float | None = None

    ok:         bool = True
    error:      str | None = None


class VSAFootprintEngine:
    """
    Motor de Huella de Alto Volumen.
    Detecta clusters volumétricos que actúan como niveles de memoria institucional.
    """

    def __init__(
        self,
        node_vol_threshold: float = 1.5,  # Umbral de z-score o multiplicador
        price_buffer_pct:   float = 0.005, # Buffer para proximidad
    ) -> None:
        self.node_vol_threshold = node_vol_threshold
        self.price_buffer_pct = price_buffer_pct

    def analyze_footprints(
        self,
        df_ohlcv:  pd.DataFrame,
        ticker:    str = "UNKNOWN",
    ) -> VSAFootprintResult:
        """
        Identifica nodos HVN activos y calcula los niveles de soporte/resistencia más cercanos.
        """
        ts = datetime.now(tz=UTC)
        try:
            if len(df_ohlcv) < 50:
                 return VSAFootprintResult(ticker=ticker, timestamp=ts, ok=False, error="Insufficient data")

            # 1. Identificación de Barras de Alto Volumen mediante Z-Score
            v_mean = df_ohlcv["volume"].rolling(20, min_periods=20).mean()
            v_std  = df_ohlcv["volume"].rolling(20, min_periods=20).std()
            vz     = (df_ohlcv["volume"] - v_mean) / (v_std + 1e-9)

            # Filtro para barras con volumen significativo
            high_vol_mask = vz > self.node_vol_threshold
            high_vol_indices = df_ohlcv.index[high_vol_mask]

            active_nodes: list[FootprintNode] = []
            curr_price = float(df_ohlcv["close"].iloc[-1])

            for idx in high_vol_indices:
                bar = df_ohlcv.loc[idx]
                level = float(bar["close"])
                vol = float(bar["volume"])

                # Lógica Institucional:
                # Si el nivel está bajo el precio actual -> Soporte (Demanda)
                # Si está sobre el precio actual -> Resistencia (Suministro/Trapped Buyers)
                is_support = level < curr_price

                active_nodes.append(FootprintNode(
                    price=level,
                    volume=vol,
                    bar_index=int(df_ohlcv.index.get_loc(idx)),
                    is_support=is_support,
                    is_active=True
                ))

            # 2. Extracción de Niveles Críticos (Más cercanos al precio spot)
            supports = [n.price for n in active_nodes if n.is_support]
            resistances = [n.price for n in active_nodes if not n.is_support]

            near_s = max(supports) if supports else None
            near_r = min(resistances) if resistances else None

            # Retornar los últimos 10 nodos detectados para eficiencia
            return VSAFootprintResult(
                ticker=ticker,
                timestamp=ts,
                active_levels=active_nodes[-10:],
                nearest_support=near_s,
                nearest_resistance=near_r,
                ok=True
            )

        except Exception as exc:
            logger.exception("[VSAFootprint] Error en análisis para %s: %s", ticker, exc)
            return VSAFootprintResult(
                ticker=ticker, timestamp=ts,
                ok=False, error=str(exc)
            )


# ─────────────────────────────────────────────────────────
# MIGRATION AUDIT — SECTOR: TÉCNICO
# Archivo      : vsa_footprint_engine.py
# Sub-capa     : Engine (Huella de Volumen)
# Eliminado    : Referencias QuantumBeta / OpticoChart.
# Actualizado  : Modelos Pydantic V2 ConfigDict(frozen=True).
# Preservado   : Lógica de detección de HVN mediante z-score.
# ─────────────────────────────────────────────────────────
