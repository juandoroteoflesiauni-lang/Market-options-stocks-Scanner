"""Tests política de riesgo definido en opciones Alpaca. # [PD-6][TH]"""

from __future__ import annotations

from backend.config.options_defined_risk import (
    filter_allowed_structure_values,
    is_defined_risk_structure,
    normalize_structure_for_execution,
    options_defined_risk_only,
)
from backend.models.options_strategy import OptionsStructure


def test_short_put_maps_to_put_credit_spread(monkeypatch):
    monkeypatch.setenv("OPTIONS_DEFINED_RISK_ONLY", "true")
    monkeypatch.setenv("OPTIONS_PREFER_SPREAD_OVER_SINGLE_LEG", "true")
    out = normalize_structure_for_execution(OptionsStructure.SHORT_PUT)
    assert out == OptionsStructure.PUT_CREDIT_SPREAD


def test_long_call_prefers_bull_call_spread_when_enabled(monkeypatch):
    monkeypatch.setenv("OPTIONS_DEFINED_RISK_ONLY", "true")
    monkeypatch.setenv("OPTIONS_PREFER_SPREAD_OVER_SINGLE_LEG", "true")
    out = normalize_structure_for_execution(OptionsStructure.LONG_CALL)
    assert out == OptionsStructure.BULL_CALL_SPREAD


def test_long_call_kept_when_spread_preference_off(monkeypatch):
    monkeypatch.setenv("OPTIONS_DEFINED_RISK_ONLY", "true")
    monkeypatch.setenv("OPTIONS_PREFER_SPREAD_OVER_SINGLE_LEG", "false")
    out = normalize_structure_for_execution(OptionsStructure.LONG_CALL)
    assert out == OptionsStructure.LONG_CALL


def test_filter_removes_short_put_from_allowed(monkeypatch):
    monkeypatch.setenv("OPTIONS_DEFINED_RISK_ONLY", "true")
    filtered = filter_allowed_structure_values(("long_call", "short_put", "put_credit_spread"))
    assert "short_put" not in filtered
    assert "put_credit_spread" in filtered


def test_is_defined_risk_rejects_naked_short():
    assert is_defined_risk_structure(OptionsStructure.SHORT_PUT) is False
    assert is_defined_risk_structure(OptionsStructure.BULL_CALL_SPREAD) is True


def test_options_defined_risk_only_defaults_true(monkeypatch):
    monkeypatch.delenv("OPTIONS_DEFINED_RISK_ONLY", raising=False)
    assert options_defined_risk_only() is True
