"""Orquestador del bot Alpaca Paper (100% nativo, LONG-only). # [PD-3][IM][TH]

Flujo: Universo Extendido → embudo cuantitativo (Vol/ATR + RS + MACD) →
análisis técnico de las 50-70 seleccionadas → motor de decisión nativo →
risk desk nativo (1x cash + ATR) → ejecución bracket en Paper. Sin acoplar
modelos, decisión ni riesgo de BingX.
"""

from __future__ import annotations

import statistics
from collections.abc import Iterable
from datetime import UTC, datetime
from uuid import uuid4

from backend.config.alpaca_priority_route import (
    ALPACA_ROUTE1_WATCHLIST,
    scan_pool_excluding_route1,
)
from backend.config.alpaca_options_route_config import (
    alpaca_options_enabled,
    alpaca_options_priority_over_equity,
)
from backend.config.alpaca_eod_config import is_eod_entry_cutoff
from backend.config.logger_setup import get_logger
from backend.domain.alpaca_models import (
    AlpacaCandidateAnalysis,
    AlpacaDecision,
    EquityCycleResult,
    EquityOrderIntent,
    EquityRiskDecision,
)
from backend.domain.volatility import compute_atr, compute_macd, compute_relative_strength
from backend.layer_1_data.datos.alpaca_client import AlpacaClient
from backend.services.alpaca_decision_engine import AlpacaDecisionConfig, decide
from backend.services.alpaca_dual_route_service import (
    build_route1_batch,
    build_route2_batch,
    select_route2_symbols,
)
from backend.services.alpaca_market_hours import AlpacaMarketHoursGuard
from backend.services.alpaca_risk_desk import AlpacaRiskDesk, AlpacaRiskPolicy
from backend.services.alpaca_universe_funnel import FunnelConfig, SymbolBars, run_funnel
from backend.services.equity_l2_gate_service import apply_equity_l2_gate
from backend.services.bot.alpaca_bot_execution_mixin import AlpacaBotExecutionMixin
from backend.services.bot.alpaca_bot_exits_mixin import AlpacaBotExitsMixin
from backend.services.bot.alpaca_eod_flatten_mixin import AlpacaEodFlattenMixin
from backend.services.bot.alpaca_options_cycle_mixin import AlpacaOptionsCycleMixin
from backend.services.bot.alpaca_bot_scanner_mixin import AlpacaBotScannerMixin
from backend.services.bot.alpaca_bot_types import (
    BENCHMARK_SYMBOL,
    DEFAULT_FUNNEL_MIN_ATR_PCT_5M,
    DEFAULT_FUNNEL_MIN_AVG_VOLUME_5M,
    DEFAULT_FUNNEL_TOP_N,
    DEFAULT_GATHER_CONCURRENCY,
    DEFAULT_KLINES_PER_SYMBOL,
    DEFAULT_MIN_BARS_FOR_SIGNAL,
    DEFAULT_PREFILTER_POOL_SIZE,
    DEFAULT_SCAN_INTERVAL,
    DEFAULT_VOLUME_Z_THRESHOLD,
    RANGE_LOOKBACK_BARS,
    REDUCED_UNIVERSE,
    _ParametricExitState,
)

logger = get_logger(__name__)


def utc_iso_now() -> str:
    return datetime.now(UTC).isoformat()


def _volume_z_score(volumes: tuple[float, ...]) -> float | None:
    if len(volumes) < 3:
        return None
    history = volumes[:-1]
    mean = statistics.mean(history)
    std = statistics.stdev(history)
    if std <= 0:
        return 0.0
    return (volumes[-1] - mean) / std


def _close_position_in_range(closes: tuple[float, ...]) -> float | None:
    recent = closes[-RANGE_LOOKBACK_BARS:]
    if not recent:
        return None
    high, low = max(recent), min(recent)
    span = high - low
    return (recent[-1] - low) / span if span > 0 else 0.5


def analysis_from_bars(bars: SymbolBars) -> AlpacaCandidateAnalysis:
    """Construye el análisis técnico nativo a partir de las velas. # [TH]"""
    closes = bars.closes
    if len(closes) < DEFAULT_MIN_BARS_FOR_SIGNAL:
        return AlpacaCandidateAnalysis(symbol=bars.symbol, timestamp=utc_iso_now())
    macd = compute_macd(closes)
    return AlpacaCandidateAnalysis(
        symbol=bars.symbol,
        timestamp=utc_iso_now(),
        latest_close=closes[-1],
        atr=compute_atr(bars.highs, bars.lows, closes),
        macd_histogram=macd.histogram if macd else None,
        volume_z_score=_volume_z_score(bars.volumes),
        close_position_in_range=_close_position_in_range(closes),
        technical_ok=True,
    )


class AlpacaBotService(
    AlpacaBotScannerMixin,
    AlpacaBotExecutionMixin,
    AlpacaBotExitsMixin,
    AlpacaOptionsCycleMixin,
    AlpacaEodFlattenMixin,
):
    """Top-level orchestrator for the Alpaca Paper bot (native, LONG-only)."""

    def __init__(
        self,
        client: AlpacaClient | None = None,
        *,
        universe: Iterable[str] | None = None,
        risk_policy: AlpacaRiskPolicy | None = None,
        decision_config: AlpacaDecisionConfig | None = None,
        funnel_config: FunnelConfig | None = None,
        scan_interval: str = DEFAULT_SCAN_INTERVAL,
        klines_per_symbol: int = DEFAULT_KLINES_PER_SYMBOL,
        volume_z_threshold: float = DEFAULT_VOLUME_Z_THRESHOLD,
        risk_desk: AlpacaRiskDesk | None = None,
        market_hours_guard: AlpacaMarketHoursGuard | None = None,
        trading_mode: str = "paper",
        prefilter_pool_size: int = DEFAULT_PREFILTER_POOL_SIZE,
        gather_concurrency: int = DEFAULT_GATHER_CONCURRENCY,
    ) -> None:
        self._owns_client: bool = client is None
        self._client = client or AlpacaClient()
        self._universe = tuple(universe) if universe else REDUCED_UNIVERSE
        self._risk_policy = risk_policy or AlpacaRiskPolicy()
        self._decision_config = decision_config or AlpacaDecisionConfig()
        self._funnel_config = funnel_config or FunnelConfig(
            top_n=DEFAULT_FUNNEL_TOP_N,
            min_avg_volume=DEFAULT_FUNNEL_MIN_AVG_VOLUME_5M,
            min_atr_pct=DEFAULT_FUNNEL_MIN_ATR_PCT_5M,
        )
        self._scan_interval = scan_interval
        self._klines_per_symbol = klines_per_symbol
        self._volume_z_threshold = volume_z_threshold
        self._risk_desk = risk_desk or AlpacaRiskDesk(policy=self._risk_policy)
        self._market_hours = market_hours_guard or AlpacaMarketHoursGuard(self._client)
        self._trading_mode = trading_mode.strip().lower()
        self._prefilter_pool_size = prefilter_pool_size
        self._gather_concurrency = gather_concurrency
        self._last_execution: dict[str, datetime] = {}
        self._parametric_exit_state: dict[str, _ParametricExitState] = {}
        self._eod_flatten_date: str | None = None

    @property
    def dry_run(self) -> bool:
        return self._client.dry_run

    @property
    def trading_mode(self) -> str:
        return self._trading_mode

    @property
    def is_live(self) -> bool:
        return self._trading_mode == "live"

    async def aclose(self) -> None:
        if self._owns_client:
            await self._client.aclose()

    def _full_universe(self) -> tuple[str, ...]:
        from backend.services.alpaca_universe_fetcher import ALPACA_EXTENDED_CACHE

        extended = tuple(ALPACA_EXTENDED_CACHE) or self._universe
        return extended

    def _working_pool(self, full: tuple[str, ...]) -> tuple[str, ...]:
        seen: set[str] = set()
        pool: list[str] = []
        for sym in (*REDUCED_UNIVERSE, BENCHMARK_SYMBOL):
            if sym not in seen:
                seen.add(sym)
                pool.append(sym)
        for sym in full:
            if len(pool) >= self._prefilter_pool_size:
                break
            if sym not in seen:
                seen.add(sym)
                pool.append(sym)
        return tuple(pool)

    async def _buying_power(self) -> float | None:
        balance = await self._client.fetch_account_balance()
        value = balance.get("buying_power") or balance.get("equity")
        try:
            return float(value) if value is not None else None
        except (TypeError, ValueError):
            return None

    def _build_decisions(
        self, selected: list[str], bars_map: dict[str, SymbolBars], benchmark: tuple[float, ...]
    ) -> tuple[list[AlpacaCandidateAnalysis], list[AlpacaDecision]]:
        analyses: list[AlpacaCandidateAnalysis] = []
        decisions: list[AlpacaDecision] = []
        for symbol in selected:
            bars = bars_map.get(symbol)
            if bars is None:
                continue
            analysis = analysis_from_bars(bars)
            rs = compute_relative_strength(bars.closes, benchmark)
            analysis = analysis.model_copy(update={"relative_strength": rs})
            analyses.append(analysis)
            decisions.append(apply_equity_l2_gate(decide(analysis, self._decision_config)))
        return analyses, decisions

    async def _authorize_decisions(
        self,
        analyses: list[AlpacaCandidateAnalysis],
        decisions: list[AlpacaDecision],
        *,
        cycle_id: str,
        buying_power: float | None,
    ) -> tuple[list[EquityOrderIntent], list[EquityRiskDecision]]:
        analysis_map = {a.symbol: a for a in analyses}
        intents: list[EquityOrderIntent] = []
        risk_decisions: list[EquityRiskDecision] = []
        for decision in decisions:
            analysis = analysis_map.get(decision.symbol)
            if analysis is None:
                continue
            intent = self._risk_desk.build_intent(
                decision,
                analysis,
                cycle_id=cycle_id,
                buying_power=buying_power,
                route=decision.route,
            )
            if intent is None:
                continue
            intents.append(intent)
            risk_decisions.append(self._risk_desk.authorize_intent(intent))
        return intents, risk_decisions

    async def _build_technical_payload(
        self, symbol: str, klines: list[dict], l1_only: bool
    ) -> dict:
        from backend.services.technical_terminal_payload import (
            build_technical_terminal_payload_from_candles,
            technical_flags_l1_only,
        )

        flags = technical_flags_l1_only() if l1_only else None
        return await build_technical_terminal_payload_from_candles(
            symbol,
            klines,
            timeframe=self._scan_interval,
            engine_flags=flags,
        )

    def _dual_route_symbols(self, full_universe: tuple[str, ...]) -> tuple[str, ...]:
        scan_pool = scan_pool_excluding_route1(
            full_universe,
            pool_size=self._prefilter_pool_size,
            benchmark=BENCHMARK_SYMBOL,
        )
        ordered = (*ALPACA_ROUTE1_WATCHLIST, BENCHMARK_SYMBOL, *scan_pool)
        seen: set[str] = set()
        out: list[str] = []
        for sym in ordered:
            if sym not in seen:
                seen.add(sym)
                out.append(sym)
        return tuple(out)

    async def run_cycle(self, *, cycle_mode: str = "slow") -> EquityCycleResult:
        """Run one bot cycle. ``fast`` = exits/EOD only; ``slow`` = full R1/R2 scan."""
        if cycle_mode == "fast":
            return await self._run_fast_cycle()
        return await self._run_slow_cycle()

    async def _run_fast_cycle(self) -> EquityCycleResult:
        """Monitor loop: EOD flatten + dynamic exits on open positions."""
        started_at = utc_iso_now()
        full_universe = self._full_universe()
        if not await self._market_hours.is_market_open():
            return EquityCycleResult(
                started_at=started_at,
                finished_at=utc_iso_now(),
                universe=full_universe,
                prefiltered=(),
                dry_run=self.dry_run,
                trading_environment=self._trading_mode,
                blocked_reasons={"_market": ["market_closed"]},
            )

        eod_flatten = await self.maybe_eod_flatten()
        if eod_flatten is not None:
            return EquityCycleResult(
                started_at=started_at,
                finished_at=utc_iso_now(),
                universe=full_universe,
                prefiltered=(),
                dry_run=self.dry_run,
                trading_environment=self._trading_mode,
                blocked_reasons={"eod": ["flatten_window"]},
                eod_flatten=eod_flatten,
            )

        executions: list = []
        try:
            executions = await self.evaluate_dynamic_exits()
        except Exception as exc:
            logger.error("alpaca_bot.fast_exits_failed %s", exc)

        logger.info("alpaca_bot.fast_cycle_finished exits=%d", len(executions))
        return EquityCycleResult(
            started_at=started_at,
            finished_at=utc_iso_now(),
            universe=full_universe,
            prefiltered=(),
            dry_run=self.dry_run,
            trading_environment=self._trading_mode,
            executions=tuple(e.raw if hasattr(e, "raw") else {} for e in executions),
            blocked_reasons={"_cycle": ["fast_monitor_only"]},
        )

    async def _run_slow_cycle(self) -> EquityCycleResult:
        started_at = utc_iso_now()
        full_universe = self._full_universe()
        symbols_to_fetch = self._dual_route_symbols(full_universe)
        if not await self._market_hours.is_market_open():
            logger.info(
                "alpaca_bot.cycle_skipped market_closed universe=%d symbols=%d",
                len(full_universe),
                len(symbols_to_fetch),
            )
            return EquityCycleResult(
                started_at=started_at,
                finished_at=utc_iso_now(),
                universe=full_universe,
                prefiltered=(),
                dry_run=self.dry_run,
                trading_environment=self._trading_mode,
                blocked_reasons={"_market": ["market_closed"]},
            )

        eod_flatten = await self.maybe_eod_flatten()
        if eod_flatten is not None:
            logger.warning("alpaca_bot.eod_flatten_cycle result=%s", eod_flatten)
            return EquityCycleResult(
                started_at=started_at,
                finished_at=utc_iso_now(),
                universe=full_universe,
                prefiltered=(),
                dry_run=self.dry_run,
                trading_environment=self._trading_mode,
                blocked_reasons={"eod": ["flatten_window"]},
                eod_flatten=eod_flatten,
            )

        allow_new_entries = not is_eod_entry_cutoff()
        bars_map, klines_map = await self._gather_bars_and_klines(symbols_to_fetch)
        benchmark = bars_map.get(BENCHMARK_SYMBOL)
        benchmark_closes = benchmark.closes if benchmark else ()
        scan_pool = scan_pool_excluding_route1(
            full_universe,
            pool_size=self._prefilter_pool_size,
            benchmark=BENCHMARK_SYMBOL,
        )
        scan_bars = [bars_map[s] for s in scan_pool if s in bars_map]
        route2_selected = select_route2_symbols(
            scan_bars, benchmark_closes, self._funnel_config
        )

        technical_builder = self._build_technical_payload
        r1_analyses, r1_decisions = await build_route1_batch(
            bars_map,
            klines_map,
            benchmark_closes,
            decision_config=self._decision_config,
            analysis_builder=analysis_from_bars,
            technical_builder=technical_builder,
            concurrency=self._gather_concurrency,
        )
        r2_analyses, r2_decisions = await build_route2_batch(
            route2_selected,
            bars_map,
            klines_map,
            benchmark_closes,
            decision_config=self._decision_config,
            analysis_builder=analysis_from_bars,
            technical_builder=technical_builder,
            concurrency=self._gather_concurrency,
        )

        analyses = r1_analyses + r2_analyses
        decisions = r1_decisions + r2_decisions
        cycle_id = uuid4().hex[:8]
        buying_power = await self._buying_power()

        r1_intents, r1_risk = await self._authorize_decisions(
            r1_analyses, r1_decisions, cycle_id=cycle_id, buying_power=buying_power
        )
        r1_reserved = sum(
            i.notional_usd for i, rd in zip(r1_intents, r1_risk, strict=False) if rd.authorized
        )
        adjusted_bp = (
            max(0.0, buying_power - r1_reserved) if buying_power is not None else None
        )
        r2_intents, r2_risk = await self._authorize_decisions(
            r2_analyses, r2_decisions, cycle_id=cycle_id, buying_power=adjusted_bp
        )

        options_entries: tuple[dict, ...] = ()
        options_executed: frozenset[str] = frozenset()
        options_reserved = 0.0
        if alpaca_options_enabled() and allow_new_entries:
            options_entries, options_executed, options_reserved = (
                await self.run_integrated_options_cycle(
                    r1_decisions=list(r1_decisions),
                    r2_decisions=list(r2_decisions),
                    r2_symbols=tuple(route2_selected),
                    execute=not self.dry_run,
                )
            )

        skip_equity_symbols = (
            options_executed
            if options_executed and alpaca_options_priority_over_equity()
            else frozenset()
        )

        intents = r1_intents + r2_intents
        risk_decisions = r1_risk + r2_risk
        executions: list = []
        if allow_new_entries:
            executions = await self.execute_risk_decisions(
                risk_decisions,
                skip_symbols=skip_equity_symbols,
            )
        elif is_eod_entry_cutoff():
            logger.info("alpaca_bot.eod_entry_cutoff skipping new equity/options entries")
        try:
            await self.evaluate_dynamic_exits()
        except Exception as exc:
            logger.error("alpaca_bot.exits_failed %s", exc)

        logger.info(
            "alpaca_bot.cycle_finished route1=%d route2=%d equity_executions=%d "
            "options_entries=%d options_executed=%d",
            len(ALPACA_ROUTE1_WATCHLIST),
            len(route2_selected),
            len(executions),
            len(options_entries),
            len(options_executed),
        )
        return EquityCycleResult(
            started_at=started_at,
            finished_at=utc_iso_now(),
            universe=full_universe,
            prefiltered=tuple(route2_selected),
            route1_symbols=ALPACA_ROUTE1_WATCHLIST,
            route2_symbols=tuple(route2_selected),
            analyses=tuple(analyses),
            decisions=tuple(decisions),
            order_intents=tuple(intents),
            risk_decisions=tuple(risk_decisions),
            executions=tuple(e.raw if hasattr(e, "raw") else {} for e in executions),
            options_entries=tuple(options_entries),
            options_executed_symbols=tuple(sorted(options_executed)),
            options_reserved_premium_usd=options_reserved,
            dry_run=self.dry_run,
            trading_environment=self._trading_mode,
            blocked_reasons=(
                {"eod": ["entry_cutoff"]} if not allow_new_entries else {}
            ),
        )
