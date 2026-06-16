"""Unit tests for AgenticDataFacade."""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal

import pytest

from backend.domain.agentic_models import MacroDataSnapshot
from backend.domain.fmp_models import FMPEconomicCalendarItem, FMPEconomicIndicator, FMPTreasuryRate
from backend.hub.circuit_breaker import CircuitBreaker, CircuitState
from backend.models.market_snapshot import DataLineage
from backend.models.option_contract import OptionChainSnapshot, OptionContract
from backend.models.result import Result
from backend.services.agentic_data_facade import AgenticDataFacade, contract_context_from_option


class _FakeFMP:
    async def get_economic_calendar(
        self, date_from: str, date_to: str
    ) -> list[FMPEconomicCalendarItem]:
        _ = (date_from, date_to)
        return [FMPEconomicCalendarItem(date="2026-06-16", event="CPI", impact="High")]

    async def get_treasury_rates(self, from_date: str, to_date: str) -> list[FMPTreasuryRate]:
        _ = (from_date, to_date)
        return [FMPTreasuryRate(date="2026-06-16", year10=4.3)]

    async def get_economic_indicator(self, name: str) -> list[FMPEconomicIndicator]:
        return [FMPEconomicIndicator(date="2026-06-16", value=3.1, name=name)]


class _FakeHub:
    async def get_options_chain(self, ticker: str) -> Result[OptionChainSnapshot]:
        _ = ticker
        contract = OptionContract(
            underlying_ticker="AAPL",
            contract_symbol="AAPL240119C00150000",
            strike=Decimal("150"),
            expiry=datetime(2026, 7, 19).date(),
            option_type="CALL",
            bid=Decimal("2.5"),
            ask=Decimal("2.6"),
            volume=1000,
            open_interest=5000,
            implied_volatility=0.25,
            delta=0.55,
            gamma=0.02,
            theta=-0.05,
            vega=0.15,
            rho=0.03,
            composite_score=88.0,
            data_lineage=DataLineage(
                source="test",
                ingestion_latency_ms=1,
                raw_field_count=1,
            ),
        )
        chain = OptionChainSnapshot(
            ticker="AAPL",
            spot_price=Decimal("150"),
            contracts=[contract],
        )
        return Result.success(chain)


@pytest.mark.asyncio
async def test_fetch_macro_snapshot_success() -> None:
    facade = AgenticDataFacade(_FakeFMP())
    result = await facade.fetch_macro_snapshot(horizon_days=3)
    assert result.is_success
    snapshot = result.unwrap()
    assert isinstance(snapshot, MacroDataSnapshot)
    assert len(snapshot.calendar_events) == 1


@pytest.mark.asyncio
async def test_fetch_macro_snapshot_circuit_open() -> None:
    import time

    breaker = CircuitBreaker(provider_name="fmp")
    breaker.state = CircuitState.OPEN
    breaker._last_failure_time = time.time()
    facade = AgenticDataFacade(_FakeFMP(), circuit_breaker=breaker)
    result = await facade.fetch_macro_snapshot()
    assert result.is_failure
    assert "circuit" in result.reason.lower()


@pytest.mark.asyncio
async def test_get_options_chain_delegates_to_hub() -> None:
    facade = AgenticDataFacade(_FakeFMP(), options_hub=_FakeHub())
    result = await facade.get_options_chain("AAPL")
    assert result.is_success
    contexts = facade.top_contracts(result.unwrap(), limit=1)
    assert contexts[0].contract_symbol == "AAPL240119C00150000"


def test_contract_context_from_option() -> None:
    contract = OptionContract(
        underlying_ticker="AAPL",
        contract_symbol="AAPL240119C00150000",
        strike=Decimal("150"),
        expiry=datetime(2026, 7, 19).date(),
        option_type="CALL",
        bid=Decimal("2.5"),
        ask=Decimal("2.6"),
        volume=1000,
        open_interest=5000,
        implied_volatility=0.25,
        delta=0.55,
        gamma=0.02,
        theta=-0.05,
        vega=0.15,
        rho=0.03,
        composite_score=90.0,
        data_lineage=DataLineage(
            source="test",
            ingestion_latency_ms=1,
            raw_field_count=1,
        ),
    )
    ctx = contract_context_from_option(contract)
    assert ctx.delta == 0.55
    assert ctx.composite_score == 90.0
