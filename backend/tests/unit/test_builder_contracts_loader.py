from decimal import Decimal

import pytest

from backend.config.builder_contracts_loader import (
    load_builder_contracts,
    resolve_builder_contract,
)


def test_load_builder_contracts_returns_expected_mini_and_micro_specs() -> None:
    catalog = load_builder_contracts()

    mes = catalog.contracts["MES"]
    mnq = catalog.contracts["MNQ"]

    assert mes.tick_size == Decimal("0.25")
    assert mes.tick_value == Decimal("1.25")
    assert mes.is_micro is True
    assert mes.mini_equivalent == Decimal("0.1")

    assert mnq.tick_value == Decimal("0.50")
    assert mnq.is_micro is True


def test_resolve_us100_cash_alias_to_nq_with_expected_tick_value() -> None:
    spec = resolve_builder_contract("US100.CASH")

    assert spec.symbol == "NQ"
    assert spec.tick_size == Decimal("0.25")
    assert spec.tick_value == Decimal("5.00")
    assert spec.is_micro is False


def test_resolve_alias_prefers_micro_when_requested() -> None:
    spec = resolve_builder_contract("US100.CASH", prefer_micro=True)

    assert spec.symbol == "MNQ"
    assert spec.tick_value == Decimal("0.50")
    assert spec.is_micro is True


def test_resolve_xauusd_alias_to_gc() -> None:
    spec = resolve_builder_contract("XAUUSD")

    assert spec.symbol == "GC"
    assert spec.tick_value == Decimal("10.00")


def test_resolve_xagusd_alias_to_si() -> None:
    spec = resolve_builder_contract("XAGUSD")

    assert spec.symbol == "SI"
    assert spec.tick_value == Decimal("25.00")


def test_catalog_defaults_expose_contract_caps() -> None:
    catalog = load_builder_contracts()

    assert catalog.defaults.max_minis == 4
    assert catalog.defaults.max_micros == 40


def test_unknown_symbol_raises_value_error() -> None:
    with pytest.raises(ValueError, match="not part of the Builder futures universe"):
        resolve_builder_contract("AAPL")
