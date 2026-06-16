from __future__ import annotations
"""Regime Proxy — indicador macro global ligero para Phase A.

Proxy de los Modelos Ocultos de Markov (HMM) que corren en capas
superiores. En lugar de modelar 5,000 tickers, usa un único valor
de VIX para ajustar los umbrales del PhaseAGlobalFilter antes de
lanzar el scan_universe, adaptando la agresividad del filtro al
régimen macro actual.

Buckets VIX:
  LOW      < 15   →  Calma: filtro permisivo
  NORMAL   15-20  →  Normal: defaults
  ELEVATED 20-28  →  Tensión: ATR gate más restrictivo
  HIGH     28-40  →  Estrés: filtro agresivo
  EXTREME  > 40   →  Crisis: máxima restricción
"""


import logging
from dataclasses import dataclass
from enum import Enum

from backend.hub.market_data_hub import MarketDataHub
from backend.models.strategy_weights import PhaseAWeights

logger = logging.getLogger(__name__)

_VIX_LOW: float = 15.0
_VIX_NORMAL: float = 20.0
_VIX_ELEVATED: float = 28.0
_VIX_HIGH: float = 40.0


class RegimeLabel(str, Enum):
    LOW = "LOW"
    NORMAL = "NORMAL"
    ELEVATED = "ELEVATED"
    HIGH = "HIGH"
    EXTREME = "EXTREME"


@dataclass(frozen=True)
class RegimeOverride:
    """Overrides parcial de PhaseAWeights dictado por el régimen macro."""

    label: RegimeLabel
    vix: float
    adjusted: PhaseAWeights


def _classify_vix(vix: float) -> RegimeLabel:
    if vix < _VIX_LOW:
        return RegimeLabel.LOW
    if vix < _VIX_NORMAL:
        return RegimeLabel.NORMAL
    if vix < _VIX_ELEVATED:
        return RegimeLabel.ELEVATED
    if vix < _VIX_HIGH:
        return RegimeLabel.HIGH
    return RegimeLabel.EXTREME


def _build_adjusted_weights(
    base: PhaseAWeights,
    label: RegimeLabel,
) -> PhaseAWeights:
    """Construye un PhaseAWeights con ajustes según el régimen.

    Los ajustes son no-destructivos: parten de la config base y solo
    sobreescriben los campos que cambian por régimen.
    """
    kw = base.model_dump()

    if label == RegimeLabel.LOW:
        kw["validation_strictness"] = 0.70
        kw["atr_gate_min_score"] = 30.0
        kw["min_atr_pct"] = 0.002
        kw["max_atr_pct"] = 0.08
        kw["rsi_extreme_min_score"] = 30.0
        kw["vwap_zscore_min_score"] = 30.0
        kw["entropy_min_score"] = 30.0
        kw["supertrend_min_score"] = 30.0

    elif label == RegimeLabel.ELEVATED:
        kw["validation_strictness"] = 0.90
        kw["atr_gate_min_score"] = 60.0
        kw["min_atr_pct"] = 0.005
        kw["max_atr_pct"] = 0.04
        kw["rsi_oversold_threshold"] = 20.0
        kw["rsi_overbought_threshold"] = 80.0

    elif label == RegimeLabel.HIGH:
        kw["validation_strictness"] = 0.95
        kw["atr_gate_min_score"] = 70.0
        kw["min_atr_pct"] = 0.008
        kw["max_atr_pct"] = 0.03
        kw["max_spread_pct"] = 0.15
        kw["vwap_max_zscore"] = 2.0
        kw["max_entropy"] = 3.0

    elif label == RegimeLabel.EXTREME:
        kw["validation_strictness"] = 0.99
        kw["atr_gate_min_score"] = 80.0
        kw["min_atr_pct"] = 0.01
        kw["max_atr_pct"] = 0.025
        kw["max_spread_pct"] = 0.10
        kw["vwap_max_zscore"] = 1.5
        kw["max_entropy"] = 2.5
        kw["ema_cluster_min_score"] = 70.0
        kw["supertrend_min_score"] = 70.0

    return PhaseAWeights(**kw)


class RegimeProxy:
    """Proxy ligero de régimen macro para Phase A.

    Uso:
        proxy = RegimeProxy()
        override = await proxy.fetch_override(hub)
        if override:
            # usar override.adjusted como cfg en PhaseAGlobalFilter
    """

    @staticmethod
    async def fetch_override(hub: MarketDataHub) -> RegimeOverride | None:
        """Obtiene el VIX y retorna un RegimeOverride si hay ajuste que aplicar.

        Returns:
            RegimeOverride cuando el régimen es distinto de NORMAL,
            None si el VIX no pudo obtenerse o el régimen es NORMAL.
        """
        result = await hub.get_vix_level()
        if result.is_failure:
            logger.warning("RegimeProxy: VIX fetch failed — %s", result.reason)
            return None

        vix = result.unwrap()
        label = _classify_vix(vix)

        if label == RegimeLabel.NORMAL:
            logger.info("RegimeProxy: VIX=%.1f — NORMAL, usando defaults", vix)
            return None

        base = PhaseAWeights()
        adjusted = _build_adjusted_weights(base, label)
        logger.info(
            "RegimeProxy: VIX=%.1f — %s | validation_strictness=%.2f atr_min=%.3f atr_max=%.3f",
            vix,
            label.value,
            adjusted.validation_strictness,
            adjusted.min_atr_pct,
            adjusted.max_atr_pct,
        )

        return RegimeOverride(label=label, vix=vix, adjusted=adjusted)
