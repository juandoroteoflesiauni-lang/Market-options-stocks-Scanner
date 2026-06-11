"""Orquestador principal de Phase C — Derivatives Engine.

Integra los motores de backend.quant_engine para análisis institucional:
  - OptionsEngine (GEX/VEX/CEX, Max Pain, Squeeze)
  - GammaFlipEngine (gamma flip point, régimen de volatilidad)
  - DeltaExposureEngine (exposición delta MM, gamma trap)
  - OptionsFlowSignalEngine (flujo institucional)
  - ZeroDayEngine (0DTE: pinning, cascades)
  - ShadowDeltaEngine (shadow delta, position sizing)
  - DeltaWeightedFlow_Engine (capitulación)

Selecciona los Top 5 contratos basándose en un score compuesto multi-motor.
Scoring delegado a backend.phases.phase_c.scoring.
"""

from __future__ import annotations

import logging
from typing import Any

import numpy as np

from backend.config.phase_thresholds import get_active_weights
from backend.models.enriched_snapshot import EnrichedSnapshot
from backend.models.option_contract import OptionChainSnapshot, OptionContract, TopOptionSelection
from backend.models.result import Result
from backend.phases.phase_c.data_adapter import OptionsDataAdapter
from backend.phases.phase_c.engine_models import QuantEngineResults
from backend.phases.phase_c.greeks_calculator import GreeksCalculator
from backend.phases.phase_c.scoring import (
    classify_regime,
    compute_confidence,
    compute_engine_scores,
    score_contract,
)

logger = logging.getLogger(__name__)


class DerivativesEngine:
    """Orquestador principal de Phase C — análisis de derivados institucional.

    Procesa los Top 20 candidatos de Phase B, descarga sus cadenas de
    opciones, ejecuta análisis multi-motor con los engines de backend.quant_engine
    y selecciona los Top 5 contratos. Scoring delegado al módulo scoring.
    """

    def __init__(
        self,
        hub: Any,
        config: dict[str, Any] | None = None,
    ) -> None:
        self._hub = hub
        self._config = config or {}
        self._greeks_calc = GreeksCalculator()
        self._adapter = OptionsDataAdapter()

    async def process_top_candidates(
        self,
        candidates: list[EnrichedSnapshot],
    ) -> Result[list[TopOptionSelection]]:
        """Procesa los candidatos de Phase B y selecciona los Top 5 contratos."""
        if not candidates:
            return Result.failure(reason="No candidates provided from Phase B")

        logger.info("Phase C: Processing %d candidates from Phase B", len(candidates))

        all_chains: list[tuple[EnrichedSnapshot, OptionChainSnapshot]] = []

        for candidate in candidates:
            chain_result = await self._hub.get_options_chain(candidate.ticker)
            if chain_result.is_failure:
                logger.warning(
                    "Phase C: Failed to fetch options for %s: %s",
                    candidate.ticker,
                    chain_result.reason,
                )
                continue

            chain = chain_result.unwrap()
            if chain.has_data:
                all_chains.append((candidate, chain))

        if not all_chains:
            return Result.failure(reason="No options data available for any candidate")

        logger.info("Phase C: Got options data for %d tickers", len(all_chains))

        selections: list[TopOptionSelection] = []

        for candidate, chain in all_chains:
            selection = self._analyze_and_select(candidate, chain)
            if selection.has_selection:
                selections.append(selection)

        selections.sort(key=lambda s: s.confidence, reverse=True)
        cw = get_active_weights().phase_c
        top_selections = selections[: cw.top_n_tickers]

        logger.info(
            "Phase C: Selected %d top option contracts",
            sum(s.count for s in top_selections),
        )
        return Result.success(top_selections)

    def _analyze_and_select(
        self,
        candidate: EnrichedSnapshot,
        chain: OptionChainSnapshot,
    ) -> TopOptionSelection:
        """Ejecuta todos los motores y selecciona los mejores contratos."""
        cw = get_active_weights().phase_c
        cf = cw.contract_filters
        spot = float(chain.spot_price)
        r = 0.05
        tte = self._adapter.compute_tte(chain)
        atm_iv = self._adapter.compute_atm_iv(chain)

        engine_results = self._run_quant_engines(chain, spot, r, tte, atm_iv)
        engine_scores = compute_engine_scores(engine_results, chain, candidate)

        scored_contracts: list[tuple[OptionContract, float]] = []
        for contract in chain.contracts:
            score = score_contract(contract, chain.spot_price, candidate, engine_scores)
            if score >= cf.min_composite_score:
                scored_contracts.append((contract, score))

        scored_contracts.sort(key=lambda x: x[1], reverse=True)
        top_contracts = scored_contracts[: cw.top_n_contracts]

        return TopOptionSelection(
            ticker=candidate.ticker,
            selected_contracts=[c[0] for c in top_contracts],
            selection_criteria={
                "min_volume": cf.min_volume,
                "min_open_interest": cf.min_open_interest,
                "max_spread_pct": cf.max_spread_pct,
                "delta_target": cf.delta_target_call,
            },
            engine_scores=engine_scores,
            regime=classify_regime(engine_scores),
            confidence=compute_confidence(engine_scores, top_contracts),
        )

    def _run_quant_engines(
        self,
        chain: OptionChainSnapshot,
        spot: float,
        r: float,
        tte: float,
        atm_iv: float,
    ) -> QuantEngineResults:
        """Ejecuta todos los motores de backend.quant_engine en la cadena."""
        results = QuantEngineResults()
        results.options_result = self._run_options_engine(chain, spot, tte, atm_iv, r)
        results.gamma_flip_report = self._run_gamma_flip_engine(chain, spot, tte, r, atm_iv)
        results.dex_report = self._run_dex_engine(chain, spot)
        results.flow_signal = self._run_flow_engine(chain)
        results.zero_day_report = self._run_zero_day_engine(chain, spot, r)
        results.shadow_delta_report = self._run_shadow_delta_engine(chain, spot, tte, r)
        results.delta_flow_snapshot = self._run_delta_flow_engine(chain)
        return results

    def _run_options_engine(
        self,
        chain: Any,
        spot: float,
        tte: float,
        atm_iv: float,
        r: float,
    ) -> Any:
        try:
            from backend.quant_engine.engines.options.options import OptionsEngine

            strikes, call_oi, put_oi, call_iv, put_iv = self._adapter.to_options_engine_arrays(
                chain
            )
            if len(strikes) == 0:
                return None

            return OptionsEngine.analyze_chain(
                ticker=chain.ticker,
                spot=spot,
                strikes=strikes,
                call_oi=call_oi,
                put_oi=put_oi,
                call_iv=call_iv,
                put_iv=put_iv,
                tte=tte,
                atm_iv=atm_iv,
                r=r,
                populate_higher_greeks=False,
            )
        except Exception as e:
            logger.debug("OptionsEngine failed: %s", e)
            return None

    def _run_gamma_flip_engine(
        self,
        chain: Any,
        spot: float,
        tte: float,
        r: float,
        atm_iv: float,
    ) -> Any:
        try:
            from backend.quant_engine.engines.options.gamma_flip import GammaFlipEngine

            chain_data = self._adapter.to_chain_data_gex(chain)
            if len(chain_data) == 0:
                return None

            engine = GammaFlipEngine()
            result = engine.analyze_gamma_flip(
                chain_data=chain_data,
                spot_price=spot,
                tte=tte,
                rate=r,
                sigma=atm_iv,
            )
            return result.unwrap() if result.is_success else None
        except Exception as e:
            logger.debug("GammaFlipEngine failed: %s", e)
            return None

    def _run_dex_engine(self, chain: Any, spot: float) -> Any:
        try:
            from backend.quant_engine.engines.options.dex import DeltaExposureEngine

            chain_data = self._adapter.to_chain_data_dex(chain)
            if len(chain_data) == 0:
                return None

            engine = DeltaExposureEngine()
            result = engine.analyze(
                ticker=chain.ticker,
                spot_price=spot,
                adtv=None,
                chain_data=chain_data,
            )
            return result.unwrap() if result.is_success else None
        except Exception as e:
            logger.debug("DeltaExposureEngine failed: %s", e)
            return None

    def _run_flow_engine(self, chain: Any) -> Any:
        try:
            from backend.quant_engine.engines.options.options_flow import OptionsFlowSignalEngine

            rows = self._adapter.to_flow_rows(chain)
            if not rows:
                return None

            engine = OptionsFlowSignalEngine()
            return engine.analyze(rows)
        except Exception as e:
            logger.debug("OptionsFlowSignalEngine failed: %s", e)
            return None

    def _run_zero_day_engine(self, chain: Any, spot: float, r: float) -> Any:
        try:
            from backend.quant_engine.engines.options.zero_day import analyze_zero_day

            chain_data = self._adapter.to_chain_data_zero_day(chain)
            if len(chain_data) == 0:
                return None

            min_dte = min(c.dte for c in chain.contracts) if chain.contracts else 30
            minutes_to_close = min_dte * 390.0

            result = analyze_zero_day(
                chain_data=chain_data,
                spot=spot,
                r=r,
                minutes_to_close=minutes_to_close,
            )
            return result.unwrap() if result.is_success else None
        except Exception as e:
            logger.debug("ZeroDayEngine failed: %s", e)
            return None

    def _run_shadow_delta_engine(self, chain: Any, spot: float, tte: float, r: float) -> Any:
        try:
            from backend.quant_engine.engines.options.shadow_delta import ShadowDeltaEngine

            chain_data = self._adapter.to_chain_data_shadow_delta(chain)
            if len(chain_data) == 0:
                return None

            engine = ShadowDeltaEngine()
            result = engine.analyze_shadow_delta(
                chain_data=chain_data,
                spot_price=spot,
                tte=tte,
                rate=r,
            )
            return result.unwrap() if result.is_success else None
        except Exception as e:
            logger.debug("ShadowDeltaEngine failed: %s", e)
            return None

    def _run_delta_flow_engine(self, chain: Any) -> Any:
        try:
            from backend.quant_engine.engines.options.delta_weighted_flow import (
                DeltaWeightedFlow_Engine,
            )

            chain_data = self._adapter.to_chain_data_delta_flow(chain)
            if len(chain_data) == 0:
                return None

            engine = DeltaWeightedFlow_Engine()
            ratio_history = np.array([], dtype=np.float64)
            result = engine.analyze_flow(
                chain_data=chain_data,
                ratio_history=ratio_history,
                was_in_exhaustion=False,
            )
            return result.unwrap() if result.is_success else None
        except Exception as e:
            logger.debug("DeltaWeightedFlow_Engine failed: %s", e)
            return None
