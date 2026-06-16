"""Tests perfil laxo R1 — 11 tickers operables con opciones. # [PD-6]"""

from __future__ import annotations

import os
from datetime import UTC, datetime

import pytest

from backend.config.alpaca_options_route_config import get_options_config_for_route
from backend.config.options_strategy_loader import get_options_strategy_config
from backend.services.options_strategy._chain import leg_is_tradeable
from backend.services.options_strategy.contract_selector import ContractSelector


@pytest.fixture(autouse=True)
def _clear_config_cache() -> None:
    get_options_strategy_config.cache_clear()
    yield
    get_options_strategy_config.cache_clear()


def test_leg_is_tradeable_accepts_volume_without_oi() -> None:
    row = {
        "call_oi": 0,
        "call_volume": 40,
        "call_delta": 0.35,
        "call_bid": 1.0,
        "call_ask": 1.2,
    }
    assert leg_is_tradeable(row, prefix="call", min_daily_volume=25)


def test_leg_is_tradeable_rejects_thin_leg() -> None:
    row = {
        "call_oi": 0,
        "call_volume": 2,
        "call_delta": 0.35,
        "call_bid": 1.0,
        "call_ask": 1.2,
    }
    assert not leg_is_tradeable(row, prefix="call", min_daily_volume=25)


def test_route1_priority_config_widens_universe(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("OPTIONS_ROUTE1_LENIENT", "true")
    cfg = get_options_config_for_route("priority")
    assert cfg.universe.dte_min <= 3
    assert cfg.universe.dte_max >= 45
    assert cfg.universe.min_open_interest <= 100
    assert "route1_directional" in cfg.playbooks.playbooks


def test_contract_selector_picks_volume_only_leg() -> None:
    from datetime import timedelta

    from backend.models.options_strategy import OptionsStrategyInput, OptionsStructure
    from backend.domain.alpaca_options_models import Route1OptionsSnapshotContext

    as_of = datetime.now(tz=UTC)
    expiry = (as_of.date() + timedelta(days=14)).isoformat()
    chain = [
        {
            "strike": 50.0,
            "expiration": expiry,
            "call_oi": 0,
            "call_volume": 80,
            "call_delta": 0.38,
            "call_bid": 2.0,
            "call_ask": 2.2,
            "put_oi": 0,
            "put_volume": 0,
        }
    ]
    ctx = Route1OptionsSnapshotContext(
        symbol="IREN",
        as_of=as_of.isoformat(),
        available=True,
        snapshot={"chain": chain, "spot": 50.0},
    )
    inp = OptionsStrategyInput(
        symbol="IREN",
        as_of=as_of,
        options_context=ctx,
    )
    cfg = get_options_config_for_route("priority")
    legs = ContractSelector.select(
        inp,
        OptionsStructure.LONG_CALL,
        config=cfg,
    )
    assert len(legs) == 1
