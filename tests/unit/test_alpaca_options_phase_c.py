"""Tests Fase C — butterfly/bull call R1 + flatten EOD. # [PD-6][TH]"""

from __future__ import annotations

from datetime import UTC, datetime, time, timedelta

import pytest

from backend.config.alpaca_eod_config import (
    is_eod_entry_cutoff,
    is_eod_flatten_window,
)
from backend.models.options_strategy import OptionsStructure
from backend.services.options_strategy.contract_selector import ContractSelector
from backend.services.options_strategy.playbook_matcher import PlaybookMatcher
from tests.unit.test_options_strategy_phase3 import _make_googl_input


def test_eod_entry_cutoff_after_1530_et(monkeypatch):
    monkeypatch.setenv("ALPACA_EOD_ENTRY_CUTOFF_ET", "15:30")
    # 2026-06-13 is Saturday — weekday check returns True for cutoff
    saturday = datetime(2026, 6, 13, 19, 0, tzinfo=UTC)
    assert is_eod_entry_cutoff(now=saturday) is True


def test_eod_flatten_window_1545_et(monkeypatch):
    monkeypatch.setenv("ALPACA_EOD_FLATTEN_START_ET", "15:45")
    # Monday 2026-06-15 19:50 UTC = 15:50 ET (EDT)
    monday = datetime(2026, 6, 15, 19, 50, tzinfo=UTC)
    assert is_eod_flatten_window(now=monday) is True


def test_contract_selector_call_butterfly_three_legs():
    inp = _make_googl_input()
    legs = ContractSelector.select(inp, OptionsStructure.CALL_BUTTERFLY)
    assert len(legs) == 3
    short_legs = [leg for leg in legs if leg.side == "short"]
    long_legs = [leg for leg in legs if leg.side == "long"]
    assert len(short_legs) == 1
    assert short_legs[0].ratio == 2
    assert len(long_legs) == 2
    assert long_legs[0].strike < short_legs[0].strike < long_legs[1].strike


def test_contract_selector_bull_call_spread_matches_debit():
    inp = _make_googl_input()
    bull = ContractSelector.select(inp, OptionsStructure.BULL_CALL_SPREAD)
    debit = ContractSelector.select(inp, OptionsStructure.CALL_DEBIT_SPREAD)
    assert len(bull) == len(debit) == 2
    assert bull[0].strike == debit[0].strike
    assert bull[1].strike == debit[1].strike


def test_playbook_matcher_pinning_butterfly():
    from backend.config.options_strategy_loader import get_options_strategy_config
    from backend.models.options_strategy import (
        NormalizedFeatures,
        OptionsStrategyCandidate,
        StructureSelection,
    )
    from backend.services.options_strategy.input_builder import build_strategy_input

    inp = build_strategy_input("GOOGL", as_of=datetime(2026, 6, 13, 15, 0, tzinfo=UTC))
    features = NormalizedFeatures(
        symbol="GOOGL",
        as_of=inp.as_of,
        dealer_regime="pinning",
        gamma_pressure_score=0.7,
        chain_liquidity_score=0.6,
        global_bias=0.05,
        global_confidence=0.7,
    )
    candidate = OptionsStrategyCandidate(
        symbol="GOOGL",
        as_of=inp.as_of,
        selection=StructureSelection(
            symbol="GOOGL",
            as_of=inp.as_of,
            structure=OptionsStructure.CALL_BUTTERFLY,
            direction="neutral",
            confidence=0.6,
        ),
        legs=(),
    )
    match = PlaybookMatcher.match(inp, features, candidate, config=get_options_strategy_config())
    assert match.playbook_family == "pinning_butterfly"
