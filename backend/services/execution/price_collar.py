"""Price collar dinámico pre-send (FIA / implementation shortfall guard). # [TH]"""

from __future__ import annotations

from dataclasses import dataclass

REASON_PRICE_COLLAR = "execution_price_collar_violation"


@dataclass(frozen=True)
class PriceCollarVerdict:
    """Resultado de validación de collar de precio."""

    allowed: bool
    deviation_pct: float
    reason_code: str | None = None


def evaluate_price_collar(
    *,
    reference_price: float,
    order_price: float | None,
    max_deviation_pct: float,
    enabled: bool = True,
    is_exit: bool = False,
) -> PriceCollarVerdict:
    """Comprueba que el precio de orden no se aleje demasiado del referencia.

    Args:
        reference_price: Precio de decisión / mid de mercado al autorizar.
        order_price: Precio límite de la orden; ``None`` en market puro.
        max_deviation_pct: Fracción máxima (0.0075 = 0.75 %).
        enabled: Si False, siempre permite.
        is_exit: Salidas (reduce_only) omiten el collar.

    Returns:
        PriceCollarVerdict con ``allowed=False`` si viola el collar.
    """
    if not enabled or is_exit or max_deviation_pct <= 0 or reference_price <= 0:
        return PriceCollarVerdict(allowed=True, deviation_pct=0.0)

    benchmark = order_price if order_price is not None and order_price > 0 else reference_price
    deviation_pct = abs(benchmark - reference_price) / reference_price
    if deviation_pct > max_deviation_pct:
        return PriceCollarVerdict(
            allowed=False,
            deviation_pct=deviation_pct * 100.0,
            reason_code=REASON_PRICE_COLLAR,
        )
    return PriceCollarVerdict(allowed=True, deviation_pct=deviation_pct * 100.0)


__all__ = ["REASON_PRICE_COLLAR", "PriceCollarVerdict", "evaluate_price_collar"]
