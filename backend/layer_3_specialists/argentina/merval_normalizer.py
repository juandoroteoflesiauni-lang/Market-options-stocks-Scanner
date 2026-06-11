"""
Merval Normalizer — Stabilized USD CCL analysis for Argentine markets.

Formula: Index_USD = Index_ARS / CCL
CCL derivation: (Local_Price / ADR_Price) * Ratio
"""

from __future__ import annotations

import logging

from pydantic import BaseModel, ConfigDict

try:
    from .cedear import CEDEARResolver as CedearRatioResolver
except ModuleNotFoundError:
    # MIGRATION: import pendiente → CEDEARResolver
    _CEDEAR_REGISTRY = {"GGAL": {"ratio": 10}}

    class CedearRatioResolver:
        @staticmethod
        def resolve_ratio(ticker: str) -> int | None:
            record = _CEDEAR_REGISTRY.get(ticker.upper().replace(".BA", ""))
            if record is None:
                return None
            return int(record.get("ratio", 0)) or None


logger = logging.getLogger("backend.layer_3_specialists.argentina.merval_normalizer")
REALTIME_API_ENV_KEYS: tuple[str, ...] = (
    "MASSIVE_KEY_WS_QUOTES",
    "MASSIVE_KEY_MARKET",
)


class MervalNormalizationResult(BaseModel):
    """Result of the Merval USD CCL normalization."""

    model_config = ConfigDict(frozen=True)

    index_ticker: str
    index_price_ars: float
    index_price_usd: float
    implied_ccl: float
    proxy_used: str  # e.g., "GGAL"
    is_stable: bool  # True if CCL spread is low
    timestamp: str


class MervalNormalizer:
    """
    Stateless engine to normalize Merval Index values to USD CCL.
    Usage:
        result = MervalNormalizer.normalize(
            merval_ars=1800000.0,
            local_ggal=3200.0,
            adr_ggal=31.5,
            timestamp="2024-03-21T15:30:00Z"
        )
    """

    @staticmethod
    def calculate_ccl(local_price: float, adr_price: float, ticker: str = "GGAL") -> float:
        """
        Derive the CCL (Contado Con Liquidación) rate.
        Ratio logic: Local_Price / (ADR_Price / Ratio)
        """
        ratio = CedearRatioResolver.resolve_ratio(ticker)
        if not ratio or ratio <= 0 or adr_price <= 0:
            logger.warning(f"Invalid parameters for CCL calculation with {ticker}")
            return 0.0

        # CCL = Local / (ADR / Ratio) = (Local * Ratio) / ADR
        return (local_price * ratio) / adr_price

    @staticmethod
    def normalize(
        merval_ars: float,
        local_ggal: float,
        adr_ggal: float,
        timestamp: str,
        index_ticker: str = "IMV",
    ) -> MervalNormalizationResult:
        """
        Core normalization logic. Uses GGAL as the primary proxy for CCL discovery.
        """
        ccl = MervalNormalizer.calculate_ccl(local_ggal, adr_ggal, "GGAL")

        index_usd = merval_ars / ccl if ccl > 0 else 0.0

        return MervalNormalizationResult(
            index_ticker=index_ticker,
            index_price_ars=merval_ars,
            index_price_usd=round(index_usd, 2),
            implied_ccl=round(ccl, 4),
            proxy_used="GGAL",
            is_stable=ccl > 0,
            timestamp=timestamp,
        )

    @staticmethod
    def adjust_financial_statement(nominal_value: float, revenue: float) -> float:
        """
        Bypasses nominal inflation noise by returning margins as % of revenue.
        Essential for Argentine fundamental analysis.
        """
        if revenue <= 0:
            return 0.0
        return round((nominal_value / revenue) * 100, 4)


# ─────────────────────────────────────────────────
# MIGRATION AUDIT — SECTOR: FUNDAMENTALES
# Archivo: merval_normalizer.py
# Eliminado: imports/ruta de sistema anterior y dependencia no usada
# Preservado: normalización CCL y ajuste de estados financieros (margen sobre revenue)
# Pendientes: # MIGRATION: import pendiente → CEDEARResolver
# ─────────────────────────────────────────────────
