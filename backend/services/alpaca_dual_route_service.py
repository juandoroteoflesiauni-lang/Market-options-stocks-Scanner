"""Orquestación dual-route: R1 prioritaria + R2 scan dinámico. # [PD-3][TH][IM]"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from typing import Any

from backend.config.alpaca_priority_route import (
    ROUTE2_FUNNEL_TOP_N,
    is_route1_symbol,
    resolve_route1_watchlist,
)
from backend.config.logger_setup import get_logger
from backend.domain.alpaca_models import AlpacaCandidateAnalysis, AlpacaDecision, AlpacaRoute
from backend.domain.volatility import compute_relative_strength
from backend.services.alpaca_decision_engine import AlpacaDecisionConfig, decide
from backend.services.alpaca_r1_options_confluence import (
    OptionsConfluenceScorer,
    apply_equity_options_confluence_gate,
)
from backend.services.alpaca_r1_options_context import (
    clear_route1_options_cache,
    fetch_route1_options_bundle,
)
from backend.services.alpaca_r1_options_replay import AlpacaR1OptionsReplay
from backend.services.alpaca_r2_technical_scoring import enrich_route2_analysis
from backend.services.alpaca_route1_context_service import (
    apply_predictive_gate,
    fetch_route1_predictive_meta,
)
from backend.services.alpaca_universe_funnel import FunnelConfig, SymbolBars, run_funnel
from backend.services.equity_l2_gate_service import apply_equity_l2_gate
from backend.services.equity_options_gate_service import apply_equity_options_gate

logger = get_logger(__name__)

KlinesFetcher = Callable[[str], Awaitable[list[dict[str, Any]]]]
TechnicalBuilder = Callable[
    [str, list[dict[str, Any]], bool],
    Awaitable[dict[str, Any]],
]


def bars_to_analysis(
    bars: SymbolBars,
    benchmark: tuple[float, ...],
    *,
    route: AlpacaRoute,
    analysis_builder: Callable[[SymbolBars], AlpacaCandidateAnalysis],
) -> AlpacaCandidateAnalysis:
    """Análisis base + RS + etiqueta de ruta."""
    analysis = analysis_builder(bars)
    rs = compute_relative_strength(bars.closes, benchmark)
    return analysis.model_copy(update={"relative_strength": rs, "route": route})


async def build_route1_pair(
    symbol: str,
    bars: SymbolBars,
    klines: list[dict[str, Any]],
    benchmark: tuple[float, ...],
    *,
    decision_config: AlpacaDecisionConfig,
    analysis_builder: Callable[[SymbolBars], AlpacaCandidateAnalysis],
    technical_builder: TechnicalBuilder,
) -> tuple[AlpacaCandidateAnalysis, AlpacaDecision]:
    """R1: técnico completo + opciones + predictivo + gate L2."""
    analysis = bars_to_analysis(
        bars, benchmark, route="priority", analysis_builder=analysis_builder
    )
    try:
        payload = await technical_builder(symbol, klines, False)
        analysis = analysis.model_copy(update={"technical_payload": payload})
    except Exception as exc:
        logger.warning("dual_route.r1_technical_failed symbol=%s error=%s", symbol, exc)

    options_bundle = await fetch_route1_options_bundle(symbol)
    options_report = options_bundle.report
    predictive_meta = await fetch_route1_predictive_meta(symbol)

    signals = AlpacaR1OptionsReplay.run(klines, options_bundle.context)
    confluence = OptionsConfluenceScorer.score(signals)
    if confluence is not None:
        analysis = analysis.model_copy(update={"options_confluence": confluence})

    decision = decide(analysis, decision_config)
    decision = decision.model_copy(update={"route": "priority"})
    decision = apply_equity_l2_gate(decision)
    decision = apply_equity_options_gate(decision, options_report)
    decision = apply_equity_options_confluence_gate(decision, confluence)
    decision = apply_predictive_gate(decision, predictive_meta)
    return analysis, decision


async def build_route2_pair(
    symbol: str,
    bars: SymbolBars,
    klines: list[dict[str, Any]],
    benchmark: tuple[float, ...],
    *,
    decision_config: AlpacaDecisionConfig,
    analysis_builder: Callable[[SymbolBars], AlpacaCandidateAnalysis],
    technical_builder: TechnicalBuilder,
) -> tuple[AlpacaCandidateAnalysis, AlpacaDecision]:
    """R2: embudo clásico ya aplicado; técnico L1-only; scoring multi-motor."""
    analysis = bars_to_analysis(bars, benchmark, route="scan", analysis_builder=analysis_builder)
    try:
        payload = await technical_builder(symbol, klines, True)
        analysis = analysis.model_copy(update={"technical_payload": payload})
    except Exception as exc:
        logger.warning("dual_route.r2_technical_failed symbol=%s error=%s", symbol, exc)

    analysis = enrich_route2_analysis(analysis)
    decision = decide(analysis, decision_config)
    return analysis, decision.model_copy(update={"route": "scan"})


async def build_route1_batch(
    bars_map: dict[str, SymbolBars],
    klines_map: dict[str, list[dict[str, Any]]],
    benchmark: tuple[float, ...],
    *,
    decision_config: AlpacaDecisionConfig,
    analysis_builder: Callable[[SymbolBars], AlpacaCandidateAnalysis],
    technical_builder: TechnicalBuilder,
    concurrency: int = 4,
) -> tuple[list[AlpacaCandidateAnalysis], list[AlpacaDecision]]:
    """Procesa los 11 tickers fijos de Ruta 1 en paralelo."""
    clear_route1_options_cache()
    sem = asyncio.Semaphore(max(1, concurrency))
    analyses: list[AlpacaCandidateAnalysis] = []
    decisions: list[AlpacaDecision] = []

    async def _one(sym: str) -> None:
        bars = bars_map.get(sym)
        if bars is None:
            return
        async with sem:
            analysis, decision = await build_route1_pair(
                sym,
                bars,
                klines_map.get(sym, []),
                benchmark,
                decision_config=decision_config,
                analysis_builder=analysis_builder,
                technical_builder=technical_builder,
            )
        analyses.append(analysis)
        decisions.append(decision)

    await asyncio.gather(*[_one(sym) for sym in resolve_route1_watchlist() if sym in bars_map])
    return analyses, decisions


async def build_route2_batch(
    selected: list[str],
    bars_map: dict[str, SymbolBars],
    klines_map: dict[str, list[dict[str, Any]]],
    benchmark: tuple[float, ...],
    *,
    decision_config: AlpacaDecisionConfig,
    analysis_builder: Callable[[SymbolBars], AlpacaCandidateAnalysis],
    technical_builder: TechnicalBuilder,
    concurrency: int = 4,
) -> tuple[list[AlpacaCandidateAnalysis], list[AlpacaDecision]]:
    """Top-N circunstancial de Ruta 2 (sin gates L2/opciones)."""
    sem = asyncio.Semaphore(max(1, concurrency))
    analyses: list[AlpacaCandidateAnalysis] = []
    decisions: list[AlpacaDecision] = []

    async def _one(sym: str) -> None:
        if is_route1_symbol(sym):
            return
        bars = bars_map.get(sym)
        if bars is None:
            return
        async with sem:
            analysis, decision = await build_route2_pair(
                sym,
                bars,
                klines_map.get(sym, []),
                benchmark,
                decision_config=decision_config,
                analysis_builder=analysis_builder,
                technical_builder=technical_builder,
            )
        analyses.append(analysis)
        decisions.append(decision)

    await asyncio.gather(*[_one(sym) for sym in selected])
    return analyses, decisions


async def build_open_position_quant_analyses(
    open_symbols: tuple[str, ...],
    bars_map: dict[str, SymbolBars],
    klines_map: dict[str, list[dict[str, Any]]],
    benchmark: tuple[float, ...],
    *,
    analysis_builder: Callable[[SymbolBars], AlpacaCandidateAnalysis],
    technical_builder: TechnicalBuilder,
    concurrency: int = 4,
) -> dict[str, AlpacaCandidateAnalysis]:
    """Quant completo (como R1) para posiciones abiertas fuera del watchlist R1."""
    from backend.config.shared_options_tier_policy import is_full_quant_tier

    sem = asyncio.Semaphore(max(1, concurrency))
    open_roots = frozenset(s.upper().strip() for s in open_symbols if s)
    targets = [
        sym.upper().strip()
        for sym in open_symbols
        if sym
        and not is_route1_symbol(sym)
        and is_full_quant_tier(sym, open_position_roots=open_roots)
        and sym.upper().strip() in bars_map
    ]
    out: dict[str, AlpacaCandidateAnalysis] = {}

    async def _one(sym: str) -> None:
        bars = bars_map.get(sym)
        if bars is None:
            return
        async with sem:
            analysis = bars_to_analysis(
                bars, benchmark, route="scan", analysis_builder=analysis_builder
            )
            try:
                payload = await technical_builder(sym, klines_map.get(sym, []), False)
                analysis = analysis.model_copy(update={"technical_payload": payload})
            except Exception as exc:
                logger.warning("dual_route.open_pos_technical_failed symbol=%s error=%s", sym, exc)

            options_bundle = await fetch_route1_options_bundle(sym)
            signals = AlpacaR1OptionsReplay.run(klines_map.get(sym, []), options_bundle.context)
            confluence = OptionsConfluenceScorer.score(signals)
            if confluence is not None:
                analysis = analysis.model_copy(update={"options_confluence": confluence})
            out[sym] = analysis

    await asyncio.gather(*[_one(sym) for sym in targets])
    return out


def select_route2_symbols(
    bars_values: list[SymbolBars],
    benchmark: tuple[float, ...],
    funnel_config: FunnelConfig | None,
) -> list[str]:
    """Embudo clásico → top 20 dinámico (excluye R1 implícitamente vía pool)."""
    cfg = funnel_config or FunnelConfig(top_n=ROUTE2_FUNNEL_TOP_N)
    cfg = cfg.model_copy(update={"top_n": ROUTE2_FUNNEL_TOP_N})
    return run_funnel(bars_values, benchmark, cfg)


__all__ = [
    "build_open_position_quant_analyses",
    "build_route1_batch",
    "build_route2_batch",
    "select_route2_symbols",
]
