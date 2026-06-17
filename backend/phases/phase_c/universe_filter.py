"""Phase C option universe filter — zero-network pre-scoring veto. # [PD-3][TH][IM]"""

from __future__ import annotations

import logging

from backend.models.option_contract import OptionChainSnapshot, OptionContract
from backend.models.strategy_weights import PhaseCContractFilters
from backend.phases.phase_c.data_adapter import OptionsDataAdapter

logger = logging.getLogger(__name__)


class OptionUniverseFilter:
    """LEAN-style pre-filter: SD strike band, DTE window, OI/volume hard vetoes."""

    @staticmethod
    def filter_chain(
        chain: OptionChainSnapshot,
        filters: PhaseCContractFilters,
        *,
        atm_iv: float | None = None,
    ) -> OptionChainSnapshot:
        """Return new frozen chain with contracts passing universe rules."""
        if not chain.contracts:
            return chain

        spot = float(chain.spot_price)
        iv = atm_iv if atm_iv is not None else OptionsDataAdapter.compute_atm_iv(chain)
        iv = max(iv, 1e-4)
        tte_years = max(OptionsDataAdapter.compute_tte(chain), 1.0 / 365.0)

        kept: list[OptionContract] = []
        for contract in chain.contracts:
            if not OptionUniverseFilter._passes_hard_vetoes(contract, filters):
                continue
            if not OptionUniverseFilter._passes_dte(contract, filters):
                continue
            if filters.use_sd_strikes and not OptionUniverseFilter._passes_sd_band(
                contract, spot, iv, tte_years, filters
            ):
                continue
            kept.append(contract)

        if filters.use_sd_strikes and filters.max_strikes_each_side > 0:
            kept = OptionUniverseFilter._cap_strikes_each_side(
                kept, spot, filters.max_strikes_each_side
            )

        return OptionChainSnapshot(
            ticker=chain.ticker,
            spot_price=chain.spot_price,
            contracts=kept,
            total_call_volume=chain.total_call_volume,
            total_put_volume=chain.total_put_volume,
            total_call_oi=chain.total_call_oi,
            total_put_oi=chain.total_put_oi,
            put_call_ratio_volume=chain.put_call_ratio_volume,
            put_call_ratio_oi=chain.put_call_ratio_oi,
            fetch_timestamp=chain.fetch_timestamp,
        )

    @staticmethod
    def _passes_hard_vetoes(contract: OptionContract, filters: PhaseCContractFilters) -> bool:
        if contract.volume < filters.min_volume:
            return False
        if contract.open_interest < filters.min_open_interest:
            return False
        if contract.spread_pct > filters.max_spread_pct:
            return False
        return not (contract.bid <= 0 and contract.ask <= 0)

    @staticmethod
    def _passes_dte(contract: OptionContract, filters: PhaseCContractFilters) -> bool:
        return filters.min_dte <= contract.dte <= filters.max_dte

    @staticmethod
    def _passes_sd_band(
        contract: OptionContract,
        spot: float,
        iv: float,
        tte_years: float,
        filters: PhaseCContractFilters,
    ) -> bool:
        strike = float(contract.strike)
        sd_move = spot * iv * (tte_years**0.5) * filters.strike_sd_range
        low = spot - sd_move
        high = spot + sd_move
        return low <= strike <= high

    @staticmethod
    def _cap_strikes_each_side(
        contracts: list[OptionContract],
        spot: float,
        max_each_side: int,
    ) -> list[OptionContract]:
        calls = sorted(
            [c for c in contracts if c.is_call],
            key=lambda c: abs(float(c.strike) - spot),
        )[:max_each_side]
        puts = sorted(
            [c for c in contracts if c.is_put],
            key=lambda c: abs(float(c.strike) - spot),
        )[:max_each_side]
        allowed = {c.contract_symbol for c in calls + puts}
        return [c for c in contracts if c.contract_symbol in allowed]


def enrich_contract_american_greeks(
    contract: OptionContract,
    *,
    spot: float,
    risk_free_rate: float,
    dividend_yield: float,
    greeks_calc: object,
) -> OptionContract:
    """Re-enrich contract Greeks with American model when enabled."""
    from backend.phases.phase_c.greeks_calculator import GreeksCalculator

    calc = greeks_calc if isinstance(greeks_calc, GreeksCalculator) else GreeksCalculator()
    tte_years = max(contract.dte / 365.0, 1.0 / 365.0)
    result = calc.calculate(
        spot=spot,
        strike=float(contract.strike),
        tte_years=tte_years,
        risk_free_rate=risk_free_rate,
        iv=contract.implied_volatility,
        option_type=contract.option_type,
        model="american",
        dividend_yield=dividend_yield,
    )
    if result.is_failure:
        return contract
    g = result.unwrap()
    return contract.model_copy(
        update={
            "delta": g.delta,
            "gamma": g.gamma,
            "theta": g.theta,
            "vega": g.vega,
            "rho": g.rho,
            "vanna": g.vanna,
            "charm": g.charm,
        }
    )


__all__ = ["OptionUniverseFilter", "enrich_contract_american_greeks"]
