"""Cálculo de limit_price neto para órdenes de opciones (F8). # [PD-2][TH]"""

from __future__ import annotations

from decimal import ROUND_HALF_UP, Decimal

from backend.models.options_strategy import OptionsStructure, SelectedOptionContract

_SPREAD_STRUCTURES: frozenset[OptionsStructure] = frozenset(
    {
        OptionsStructure.CALL_DEBIT_SPREAD,
        OptionsStructure.PUT_DEBIT_SPREAD,
        OptionsStructure.BULL_CALL_SPREAD,
        OptionsStructure.PUT_CREDIT_SPREAD,
        OptionsStructure.CALL_CREDIT_SPREAD,
        OptionsStructure.CALL_BUTTERFLY,
    }
)
_CREDIT_STRUCTURES: frozenset[OptionsStructure] = frozenset(
    {
        OptionsStructure.PUT_CREDIT_SPREAD,
        OptionsStructure.CALL_CREDIT_SPREAD,
    }
)


def spread_min_legs(structure: OptionsStructure) -> int:
    """Mínimo de patas requeridas antes de EXECUTE."""
    if structure == OptionsStructure.CALL_BUTTERFLY:
        return 3
    if structure in _SPREAD_STRUCTURES:
        return 2
    return 1


def compute_limit_price_per_contract(
    legs: tuple[SelectedOptionContract, ...],
    *,
    structure: OptionsStructure,
    slippage_pct: float = 0.0,
) -> Decimal | None:
    """Neto por contrato (positivo = débito, negativo = crédito)."""
    if not legs:
        return None

    net = Decimal("0")
    for leg in legs:
        if leg.mark is None or leg.mark <= 0:
            return None
        mark = Decimal(str(leg.mark))
        signed = mark * Decimal(str(leg.ratio))
        if leg.side == "long":
            net += signed
        else:
            net -= signed

    if slippage_pct > 0:
        buffer = Decimal(str(1.0 + slippage_pct / 100.0))
        if structure in _CREDIT_STRUCTURES:
            net = (net * buffer).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
        else:
            net = (net * buffer).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)

    return net.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)


def validate_options_execution_ready(
    structure: OptionsStructure,
    legs: tuple[SelectedOptionContract, ...],
    *,
    limit_price: Decimal | None,
) -> str | None:
    """Bloquea EXECUTE si faltan patas o no hay limit_price válido."""
    min_legs = spread_min_legs(structure)
    tradeable = [leg for leg in legs if leg.contract_symbol]
    if len(tradeable) < min_legs:
        return "missing_spread_legs"

    if limit_price is None:
        return "missing_limit_price"

    if structure in _CREDIT_STRUCTURES:
        if limit_price >= Decimal("0"):
            return "invalid_credit_limit_price"
        return None

    if limit_price <= Decimal("0"):
        return "invalid_debit_limit_price"
    return None


__all__ = [
    "compute_limit_price_per_contract",
    "spread_min_legs",
    "validate_options_execution_ready",
]
