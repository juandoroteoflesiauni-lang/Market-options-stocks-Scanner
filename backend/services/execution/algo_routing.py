"""Enrutamiento algorítmico TWAP/VWAP según notional y microestructura. # [TH]"""

from __future__ import annotations

from backend.config.execution_policy import ExecutionPolicy


def should_use_bingx_twap(
    *,
    policy: ExecutionPolicy,
    notional_usdt: float,
    reduce_only: bool,
    lob_dynamics_trigger: bool,
) -> bool:
    """Determina si una entrada BingX debe fragmentarse vía TWAP slivering."""
    if reduce_only or not policy.bingx_twap_enabled:
        return False
    if notional_usdt >= policy.bingx_twap_min_notional_usdt:
        return True
    return lob_dynamics_trigger


def should_use_alpaca_elite(
    *,
    policy: ExecutionPolicy,
    notional_usd: float,
) -> bool:
    """True si la orden Alpaca califica para VWAP/TWAP Elite."""
    if not policy.alpaca_elite_enabled:
        return False
    return notional_usd >= policy.alpaca_elite_min_notional_usd


__all__ = ["should_use_alpaca_elite", "should_use_bingx_twap"]
