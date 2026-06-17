"""Política de estructuras de opciones con riesgo definido (Alpaca paper). # [PD-8][TH]"""

from __future__ import annotations

import os

from backend.models.options_strategy import OptionsStructure

# Compras y spreads verticales/butterfly — sin premium vendido desnudo.
DEFINED_RISK_STRUCTURES: frozenset[OptionsStructure] = frozenset(
    {
        OptionsStructure.LONG_CALL,
        OptionsStructure.LONG_PUT,
        OptionsStructure.CALL_DEBIT_SPREAD,
        OptionsStructure.PUT_DEBIT_SPREAD,
        OptionsStructure.BULL_CALL_SPREAD,
        OptionsStructure.PUT_CREDIT_SPREAD,
        OptionsStructure.CALL_CREDIT_SPREAD,
        OptionsStructure.CALL_BUTTERFLY,
    }
)

NAKED_SHORT_STRUCTURES: frozenset[OptionsStructure] = frozenset({OptionsStructure.SHORT_PUT})


def options_defined_risk_only() -> bool:
    """True si solo se permiten estructuras con riesgo acotado (sin shorts desnudos)."""
    return os.getenv("OPTIONS_DEFINED_RISK_ONLY", "true").strip().lower() in {
        "1",
        "true",
        "yes",
        "on",
    }


def prefer_spread_over_single_leg() -> bool:
    """True si se prefiere vertical debit/credit en lugar de long call/put suelto."""
    default = "true" if options_defined_risk_only() else "false"
    return os.getenv("OPTIONS_PREFER_SPREAD_OVER_SINGLE_LEG", default).strip().lower() in {
        "1",
        "true",
        "yes",
        "on",
    }


def defined_risk_structure_values() -> tuple[str, ...]:
    """Valores string para ``allowed_structures`` en playbooks."""
    return tuple(sorted(s.value for s in DEFINED_RISK_STRUCTURES))


def is_defined_risk_structure(structure: OptionsStructure) -> bool:
    """Indica si la estructura cumple la política de riesgo definido."""
    if structure == OptionsStructure.NO_TRADE:
        return False
    if structure in NAKED_SHORT_STRUCTURES:
        return False
    if options_defined_risk_only():
        return structure in DEFINED_RISK_STRUCTURES
    return True


def filter_allowed_structure_values(structures: tuple[str, ...]) -> tuple[str, ...]:
    """Filtra lista de estructuras permitidas según política activa."""
    if not options_defined_risk_only():
        return tuple(s for s in structures if s != OptionsStructure.SHORT_PUT.value)
    allowed = set(defined_risk_structure_values())
    return tuple(s for s in structures if s in allowed)


def normalize_structure_for_execution(
    structure: OptionsStructure,
    *,
    rich_iv: bool = False,
) -> OptionsStructure:
    """Mapea estructuras no elegibles en Alpaca paper a verticales con riesgo acotado."""
    if structure == OptionsStructure.NO_TRADE:
        return structure

    if structure == OptionsStructure.SHORT_PUT:
        return OptionsStructure.PUT_CREDIT_SPREAD

    if not options_defined_risk_only():
        return structure

    if structure not in DEFINED_RISK_STRUCTURES:
        return OptionsStructure.NO_TRADE

    if not prefer_spread_over_single_leg():
        return structure

    if structure == OptionsStructure.LONG_CALL:
        return OptionsStructure.BULL_CALL_SPREAD
    if structure == OptionsStructure.LONG_PUT:
        return OptionsStructure.PUT_DEBIT_SPREAD

    return structure


__all__ = [
    "DEFINED_RISK_STRUCTURES",
    "NAKED_SHORT_STRUCTURES",
    "defined_risk_structure_values",
    "filter_allowed_structure_values",
    "is_defined_risk_structure",
    "normalize_structure_for_execution",
    "options_defined_risk_only",
    "prefer_spread_over_single_leg",
]
