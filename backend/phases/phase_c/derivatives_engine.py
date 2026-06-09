"""Orquestador principal de Phase C — Derivatives Engine.

Integra los motores de src/quant_engine para análisis institucional:
- OptionsEngine (GEX/VEX/CEX, Max Pain, Squeeze)
- GammaFlipEngine (gamma flip point, régimen de volatilidad)
- DeltaExposureEngine (exposición delta MM, gamma trap)
- OptionsFlowSignalEngine (flujo institucional)
- ZeroDayEngine (0DTE: pinning, cascades)
- ShadowDeltaEngine (shadow delta, position sizing)
- DeltaWeightedFlow_Engine (capitulación)
- OptionsConfluenceEngine (confluencia SMC-Opciones)

Selecciona los Top 5 contratos basándose en un score compuesto multi-motor.
"""

from __future__ import annotations

import logging
from decimal import Decimal
from typing import Any

import numpy as np

from backend.models.enriched_snapshot import EnrichedSnapshot
from backend.models.option_contract import OptionChainSnapshot, OptionContract, TopOptionSelection
from backend.models.result import Result
from backend.phases.phase_c.data_adapter import OptionsDataAdapter
from backend.phases.phase_c.greeks_calculator import GreeksCalculator

logger = logging.getLogger(__name__)

# Pesos del score compuesto multi-motor
ENGINE_WEIGHTS: dict[str, float] = {
    "gex_score": 0.20,
    "gamma_flip": 0.12,
    "dex_exposure": 0.15,
    "flow_signal": 0.12,
    "zero_day": 0.10,
    "shadow_delta": 0.10,
    "delta_flow": 0.08,
    "phase_b_momentum": 0.13,
}

# Configuración de selección
DEFAULT_CONFIG: dict[str, Any] = {
    "min_volume": 100,
    "min_open_interest": 500,
    "max_spread_pct": 0.15,
    "min_dte": 14,
    "max_dte": 60,
    "delta_target_call": 0.35,
    "delta_target_put": -0.35,
    "top_n": 5,
    "min_composite_score": 40.0,
    "risk_free_rate": 0.05,
}


class QuantEngineResults:
    """Contenedor de resultados de los motores de src/quant_engine."""

    __slots__ = (
        "delta_flow_snapshot",
        "dex_report",
        "flow_signal",
        "gamma_flip_report",
        "options_result",
        "shadow_delta_report",
        "zero_day_report",
    )

    def __init__(self) -> None:
        self.options_result: Any = None
        self.gamma_flip_report: Any = None
        self.dex_report: Any = None
        self.flow_signal: Any = None
        self.zero_day_report: Any = None
        self.shadow_delta_report: Any = None
        self.delta_flow_snapshot: Any = None


class DerivativesEngine:
    """Orquestador principal de Phase C — análisis de derivados institucional.

    Procesa los Top 20 candidatos de Phase B, descarga sus cadenas de
    opciones, ejecuta análisis multi-motor con los engines de src/quant_engine
    y selecciona los Top 5 contratos.
    """

    def __init__(
        self,
        hub: Any,
        config: dict[str, Any] | None = None,
    ) -> None:
        self._hub = hub
        self._config = {**DEFAULT_CONFIG, **(config or {})}
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
        top_selections = selections[: self._config["top_n"]]

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
        spot = float(chain.spot_price)
        r = self._config["risk_free_rate"]
        tte = self._adapter.compute_tte(chain)
        atm_iv = self._adapter.compute_atm_iv(chain)

        # 1. Ejecutar motores de src/quant_engine
        engine_results = self._run_quant_engines(chain, spot, r, tte, atm_iv)

        # 2. Computar scores de cada motor
        engine_scores = self._compute_engine_scores(engine_results, chain, candidate)

        # 3. Scoring compuesto por contrato individual
        scored_contracts: list[tuple[OptionContract, float]] = []
        for contract in chain.contracts:
            score = self._score_contract(contract, chain.spot_price, candidate, engine_scores)
            if score >= self._config["min_composite_score"]:
                scored_contracts.append((contract, score))

        scored_contracts.sort(key=lambda x: x[1], reverse=True)
        top_contracts = scored_contracts[: self._config["top_n"]]

        return TopOptionSelection(
            ticker=candidate.ticker,
            selected_contracts=[c[0] for c in top_contracts],
            selection_criteria={
                "min_volume": self._config["min_volume"],
                "min_open_interest": self._config["min_open_interest"],
                "max_spread_pct": self._config["max_spread_pct"],
                "delta_target": self._config["delta_target_call"],
            },
            engine_scores=engine_scores,
            regime=self._classify_regime(engine_scores),
            confidence=self._compute_confidence(engine_scores, top_contracts),
        )

    def _run_quant_engines(
        self,
        chain: OptionChainSnapshot,
        spot: float,
        r: float,
        tte: float,
        atm_iv: float,
    ) -> QuantEngineResults:
        """Ejecuta todos los motores de src/quant_engine en la cadena."""
        results = QuantEngineResults()

        # OptionsEngine (GEX/VEX/CEX)
        results.options_result = self._run_options_engine(chain, spot, tte, atm_iv, r)

        # GammaFlipEngine
        results.gamma_flip_report = self._run_gamma_flip_engine(chain, spot, tte, r, atm_iv)

        # DeltaExposureEngine
        results.dex_report = self._run_dex_engine(chain, spot)

        # OptionsFlowSignalEngine
        results.flow_signal = self._run_flow_engine(chain)

        # ZeroDayEngine (solo si hay contratos con DTE <= 1)
        results.zero_day_report = self._run_zero_day_engine(chain, spot, r)

        # ShadowDeltaEngine
        results.shadow_delta_report = self._run_shadow_delta_engine(chain, spot, tte, r)

        # DeltaWeightedFlow_Engine
        results.delta_flow_snapshot = self._run_delta_flow_engine(chain)

        return results

    def _run_options_engine(self, chain, spot, tte, atm_iv, r) -> Any:
        """Ejecuta OptionsEngine para GEX/VEX/CEX."""
        try:
            from src.quant_engine.engines.options.options import OptionsEngine

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

    def _run_gamma_flip_engine(self, chain, spot, tte, r, atm_iv) -> Any:
        """Ejecuta GammaFlipEngine para gamma flip point."""
        try:
            from src.quant_engine.engines.options.gamma_flip import GammaFlipEngine

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

    def _run_dex_engine(self, chain, spot) -> Any:
        """Ejecuta DeltaExposureEngine para exposición delta MM."""
        try:
            from src.quant_engine.engines.options.dex import DeltaExposureEngine

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

    def _run_flow_engine(self, chain) -> Any:
        """Ejecuta OptionsFlowSignalEngine para flujo institucional."""
        try:
            from src.quant_engine.engines.options.options_flow import OptionsFlowSignalEngine

            rows = self._adapter.to_flow_rows(chain)
            if not rows:
                return None

            engine = OptionsFlowSignalEngine()
            return engine.analyze(rows)
        except Exception as e:
            logger.debug("OptionsFlowSignalEngine failed: %s", e)
            return None

    def _run_zero_day_engine(self, chain, spot, r) -> Any:
        """Ejecuta ZeroDayEngine para análisis 0DTE."""
        try:
            from src.quant_engine.engines.options.zero_day import analyze_zero_day

            chain_data = self._adapter.to_chain_data_zero_day(chain)
            if len(chain_data) == 0:
                return None

            # Usar DTE mínimo de la cadena
            min_dte = min(c.dte for c in chain.contracts) if chain.contracts else 30
            minutes_to_close = min_dte * 390.0  # ~390 min por día de trading

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

    def _run_shadow_delta_engine(self, chain, spot, tte, r) -> Any:
        """Ejecuta ShadowDeltaEngine para shadow delta analysis."""
        try:
            from src.quant_engine.engines.options.shadow_delta import ShadowDeltaEngine

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

    def _run_delta_flow_engine(self, chain) -> Any:
        """Ejecuta DeltaWeightedFlow_Engine para capitulación."""
        try:
            from src.quant_engine.engines.options.delta_weighted_flow import (
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

    def _compute_engine_scores(
        self,
        engine_results: QuantEngineResults,
        chain: OptionChainSnapshot,
        candidate: EnrichedSnapshot,
    ) -> dict[str, float]:
        """Computa scores normalizados (0-100) de cada motor."""
        scores: dict[str, float] = {}

        # 1. GEX Score (OptionsEngine)
        scores["gex_score"] = self._gex_score(engine_results.options_result)

        # 2. Gamma Flip Score
        scores["gamma_flip"] = self._gamma_flip_score(
            engine_results.gamma_flip_report, float(chain.spot_price)
        )

        # 3. DEX Exposure Score
        scores["dex_exposure"] = self._dex_score(engine_results.dex_report)

        # 4. Flow Signal Score
        scores["flow_signal"] = self._flow_score(engine_results.flow_signal)

        # 5. Zero Day Score
        scores["zero_day"] = self._zero_day_score(engine_results.zero_day_report)

        # 6. Shadow Delta Score
        scores["shadow_delta"] = self._shadow_delta_score(engine_results.shadow_delta_report)

        # 7. Delta Flow Score
        scores["delta_flow"] = self._delta_flow_score(engine_results.delta_flow_snapshot)

        # 8. Phase B Momentum
        scores["phase_b_momentum"] = self._phase_b_momentum_score(candidate)

        return scores

    def _gex_score(self, options_result: Any) -> float:
        """Score basado en GEX/VEX/CEX del OptionsEngine."""
        if options_result is None:
            return 50.0

        mic = getattr(options_result, "options_mic_score", 0.0)
        return min(max(mic, 0.0), 100.0)

    def _gamma_flip_score(self, report: Any, spot: float) -> float:
        """Score basado en la distancia al gamma flip point."""
        if report is None:
            return 50.0

        flip_point = getattr(report, "flip_point", None)
        if flip_point is None:
            return 50.0

        distance_pct = abs(spot - flip_point) / max(spot, 1.0) * 100.0

        # Cerca del flip = alta volatilidad = oportunidad
        if distance_pct < 2.0:
            return 90.0
        elif distance_pct < 5.0:
            return 75.0
        elif distance_pct < 10.0:
            return 60.0
        else:
            return 40.0

    def _dex_score(self, dex_report: Any) -> float:
        """Score basado en exposición delta MM."""
        if dex_report is None:
            return 50.0

        dex_pct = getattr(dex_report, "dex_as_pct_adtv", None)
        if dex_pct is None:
            return 50.0

        # Alta exposición = más oportunidad de squeezes
        return min(max(dex_pct * 10, 0.0), 100.0)

    def _flow_score(self, flow_signal: Any) -> float:
        """Score basado en flujo institucional."""
        if flow_signal is None:
            return 50.0

        directional = getattr(flow_signal, "directional_score", 0.0)
        confidence = getattr(flow_signal, "confidence", 0.0)

        # Convertir [-1, 1] a [0, 100] ponderado por confianza
        base_score = (directional + 1.0) * 50.0
        return base_score * confidence + 50.0 * (1.0 - confidence)

    def _zero_day_score(self, zero_day_report: Any) -> float:
        """Score basado en análisis 0DTE."""
        if zero_day_report is None:
            return 50.0

        pin_prob = getattr(zero_day_report, "pinning_prob", 0.0)
        alerts = getattr(zero_day_report, "alerts", [])
        alert_count = len(alerts) if alerts else 0

        # Más alerts = más actividad = más oportunidad
        alert_score = min(alert_count * 10, 50.0)
        pin_score = pin_prob * 50.0

        return alert_score + pin_score

    def _shadow_delta_score(self, shadow_report: Any) -> float:
        """Score basado en shadow delta gap."""
        if shadow_report is None:
            return 50.0

        net_portfolio = getattr(shadow_report, "net_portfolio", None)
        if net_portfolio is None:
            return 50.0

        delta_gap = abs(getattr(net_portfolio, "total_delta_gap", 0.0))

        # Mayor gap = mayor divergencia = oportunidad de position sizing
        return min(delta_gap * 5.0 + 50.0, 100.0)

    def _delta_flow_score(self, delta_flow: Any) -> float:
        """Score basado en capitulación por delta flow."""
        if delta_flow is None:
            return 50.0

        z_score = getattr(delta_flow, "z_score", None)
        signal = getattr(delta_flow, "signal", None)

        if z_score is None:
            return 50.0

        if signal and hasattr(signal, "value"):
            signal_str = signal.value
        else:
            signal_str = str(signal) if signal else "NEUTRAL"

        if "EXHAUSTION" in signal_str:
            return 85.0
        elif "LONG_SETUP" in signal_str:
            return 90.0
        elif "HOLD" in signal_str:
            return 65.0
        else:
            return 50.0

    def _phase_b_momentum_score(self, candidate: EnrichedSnapshot) -> float:
        """Score de momentum basado en datos de Phase B."""
        ofi = abs(candidate.ofi_score) * 100
        smc = 50.0
        if candidate.smc_direction == "BULLISH":
            smc = 80.0
        elif candidate.smc_direction == "BEARISH":
            smc = 70.0
        return min((ofi + smc) / 2, 100.0)

    def _score_contract(
        self,
        contract: OptionContract,
        spot: Decimal,
        candidate: EnrichedSnapshot,
        engine_scores: dict[str, float],
    ) -> float:
        """Score compuesto por contrato usando scores de motores + métricas básicas."""
        score = 0.0

        # Métricas básicas del contrato (40%)
        basic_score = (
            self._liquidity_score(contract) * 0.15
            + self._delta_score(contract) * 0.10
            + self._iv_score(contract) * 0.08
            + self._dte_score(contract) * 0.07
        )
        score += basic_score

        # Scores de motores quant (60%)
        engine_avg = sum(engine_scores.values()) / max(len(engine_scores), 1)
        score += engine_avg * 0.60

        return round(min(score, 100.0), 2)

    def _liquidity_score(self, contract: OptionContract) -> float:
        volume_score = min(contract.volume / 1000, 1.0) * 40
        oi_score = min(contract.open_interest / 5000, 1.0) * 40
        spread_score = max(0, 1.0 - contract.spread_pct * 10) * 20
        return volume_score + oi_score + spread_score

    def _delta_score(self, contract: OptionContract) -> float:
        target = (
            self._config["delta_target_call"]
            if contract.is_call
            else self._config["delta_target_put"]
        )
        distance = abs(abs(contract.delta) - abs(target))
        return max(0, 100 - distance * 200)

    def _iv_score(self, contract: OptionContract) -> float:
        iv = contract.implied_volatility
        if iv < 0.10:
            return 30.0
        elif iv < 0.25:
            return 80.0
        elif iv < 0.40:
            return 60.0
        else:
            return 40.0

    def _dte_score(self, contract: OptionContract) -> float:
        dte = contract.dte
        min_dte = self._config["min_dte"]
        max_dte = self._config["max_dte"]
        if dte < min_dte or dte > max_dte:
            return 0.0
        optimal = 35
        distance = abs(dte - optimal)
        return max(0, 100 - distance * 3)

    def _classify_regime(self, engine_scores: dict[str, float]) -> str:
        avg = sum(engine_scores.values()) / max(len(engine_scores), 1)
        if avg >= 65:
            return "BULLISH"
        elif avg >= 45:
            return "NEUTRAL"
        else:
            return "BEARISH"

    def _compute_confidence(
        self,
        engine_scores: dict[str, float],
        top_contracts: list[tuple[OptionContract, float]],
    ) -> float:
        if not top_contracts:
            return 0.0

        score_avg = sum(s for _, s in top_contracts) / len(top_contracts)
        engine_avg = sum(engine_scores.values()) / max(len(engine_scores), 1)

        confidence = (score_avg * 0.5 + engine_avg * 0.5) / 100
        return round(min(max(confidence, 0.0), 1.0), 4)
