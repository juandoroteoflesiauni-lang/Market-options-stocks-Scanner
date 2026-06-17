"""Política de ejecución institucional — Fase B (TWAP/VWAP, collar, repeated limit). # [PD-8][TH]"""

from __future__ import annotations

import os
from dataclasses import dataclass


@dataclass(frozen=True)
class ExecutionPolicy:
    """Umbrales de enrutamiento algorítmico y controles pre-send."""

    bingx_twap_enabled: bool = True
    bingx_twap_min_notional_usdt: float = 400.0
    alpaca_elite_enabled: bool = True
    alpaca_elite_algorithm: str = "VWAP"
    alpaca_elite_min_notional_usd: float = 1_500.0
    price_collar_enabled: bool = True
    price_collar_max_deviation_pct: float = 0.0075
    repeated_execution_enabled: bool = True
    repeated_execution_max_per_symbol: int = 6

    @classmethod
    def from_env(cls) -> ExecutionPolicy:
        def _bool(name: str, default: bool) -> bool:
            raw = os.getenv(name, "").strip().lower()
            if raw in {"1", "true", "yes", "on"}:
                return True
            if raw in {"0", "false", "no", "off"}:
                return False
            return default

        def _float(name: str, default: float) -> float:
            raw = os.getenv(name, "").strip()
            try:
                return max(0.0, float(raw))
            except (ValueError, TypeError):
                return default

        def _int(name: str, default: int) -> int:
            raw = os.getenv(name, "").strip()
            try:
                return max(0, int(raw))
            except (ValueError, TypeError):
                return default

        return cls(
            bingx_twap_enabled=_bool("BINGX_TWAP_SLIVERING_ENABLED", True),
            bingx_twap_min_notional_usdt=_float("EXECUTION_BINGX_TWAP_MIN_NOTIONAL_USDT", 400.0),
            alpaca_elite_enabled=_bool("ALPACA_ELITE_SMART_ROUTER", False),
            alpaca_elite_algorithm=os.getenv("ALPACA_ELITE_ALGORITHM", "VWAP").upper(),
            alpaca_elite_min_notional_usd=_float("ALPACA_ELITE_MIN_NOTIONAL_USD", 1_500.0),
            price_collar_enabled=_bool("EXECUTION_PRICE_COLLAR_ENABLED", True),
            price_collar_max_deviation_pct=_float("EXECUTION_PRICE_COLLAR_PCT", 0.0075),
            repeated_execution_enabled=_bool("EXECUTION_REPEATED_LIMIT_ENABLED", True),
            repeated_execution_max_per_symbol=_int("EXECUTION_REPEATED_MAX_PER_SYMBOL", 6),
        )


def execution_phase_b_env_flags() -> dict[str, str]:
    """Variables de entorno para activar Fase B en modo verificación."""
    policy = ExecutionPolicy(
        bingx_twap_enabled=True,
        bingx_twap_min_notional_usdt=400.0,
        alpaca_elite_enabled=True,
        alpaca_elite_algorithm="VWAP",
        alpaca_elite_min_notional_usd=1_500.0,
        price_collar_enabled=True,
        price_collar_max_deviation_pct=0.0075,
        repeated_execution_enabled=True,
        repeated_execution_max_per_symbol=6,
    )
    return {
        "BINGX_TWAP_SLIVERING_ENABLED": str(policy.bingx_twap_enabled).lower(),
        "EXECUTION_BINGX_TWAP_MIN_NOTIONAL_USDT": str(policy.bingx_twap_min_notional_usdt),
        "ALPACA_ELITE_SMART_ROUTER": str(policy.alpaca_elite_enabled).lower(),
        "ALPACA_ELITE_ALGORITHM": policy.alpaca_elite_algorithm,
        "ALPACA_ELITE_MIN_NOTIONAL_USD": str(policy.alpaca_elite_min_notional_usd),
        "EXECUTION_PRICE_COLLAR_ENABLED": str(policy.price_collar_enabled).lower(),
        "EXECUTION_PRICE_COLLAR_PCT": str(policy.price_collar_max_deviation_pct),
        "EXECUTION_REPEATED_LIMIT_ENABLED": str(policy.repeated_execution_enabled).lower(),
        "EXECUTION_REPEATED_MAX_PER_SYMBOL": str(policy.repeated_execution_max_per_symbol),
    }


__all__ = ["ExecutionPolicy", "execution_phase_b_env_flags"]
