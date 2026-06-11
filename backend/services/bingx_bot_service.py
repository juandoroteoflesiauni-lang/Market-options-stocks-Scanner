from backend.services.bot.bingx_bot_execution_mixin import BingXBotExecutionMixin
from backend.services.bot.bingx_bot_exits_mixin import BingXBotExitsMixin
from backend.services.bot.bingx_bot_filter_mixin import BingXBotFilterMixin
from backend.services.bot.bingx_bot_risk_mixin import BingXBotRiskMixin
from backend.services.bot.bingx_bot_scanner_mixin import BingXBotScannerMixin
from backend.services.bot.bingx_bot_types import *


class BingXBotService(
    BingXBotScannerMixin,
    BingXBotFilterMixin,
    BingXBotRiskMixin,
    BingXBotExecutionMixin,
    BingXBotExitsMixin,
):
    """Top-level orchestrator for the BingX micro-account bot."""

    def __init__(
        self,
        client: BingXClient | None = None,
        *,
        universe: Iterable[str] | None = None,
        risk_policy: BingXRiskPolicy | None = None,
        meta_learner_provider: MetaLearnerProvider | None = None,
        scan_interval: VALID_KLINE_INTERVAL = DEFAULT_SCAN_INTERVAL,
        klines_per_symbol: int = DEFAULT_KLINES_PER_SYMBOL,
        volume_z_threshold: float = DEFAULT_VOLUME_Z_THRESHOLD,
        heuristic_prob_floor: float = DEFAULT_HEURISTIC_PROB_FLOOR,
        scanner_service: ScannerConfirmationService | None = None,
        scanner_min_score: float = DEFAULT_SCANNER_MIN_SCORE,
        horizon: str = DEFAULT_HORIZON,
        universe_service: BingXUniverseService | None = None,
        account_service: BingXAccountService | None = None,
        ws_hub: BingXWebSocketHub | None = None,
        execution_quality_policy: ExecutionQualityPolicy | None = None,
        risk_desk: BingXRiskDesk | None = None,
        risk_desk_policy: BingXRiskDeskPolicy | None = None,
        options_snapshot_fn: Callable[..., Awaitable[Any]] | None = None,
        venue_technical_fn: Callable[..., Awaitable[dict[str, Any]]] | None = None,
        fmp_client: Any | None = None,
        massive_client: Any | None = None,
        alpaca_client: Any | None = None,
    ) -> None:
        self._owns_client: bool = client is None
        self._client: BingXClient = client or BingXClient(dry_run=True)
        self._universe: tuple[str, ...] = _synthetic_stock_symbols(
            tuple(universe) if universe else DEFAULT_UNIVERSE
        )
        self._risk_policy: BingXRiskPolicy = risk_policy or BingXRiskPolicy()
        self._meta_provider: MetaLearnerProvider | None = meta_learner_provider
        self._scan_interval: VALID_KLINE_INTERVAL = scan_interval
        self._klines_per_symbol: int = max(DEFAULT_MIN_BARS_FOR_SIGNAL, int(klines_per_symbol))
        self._volume_z_threshold: float = float(volume_z_threshold)
        self._heuristic_prob_floor: float = float(heuristic_prob_floor)
        self._scanner: ScannerConfirmationService = scanner_service or MarketScannerService()
        self._scanner_min_score: float = float(scanner_min_score)
        self._horizon: str = horizon
        self._universe_service = universe_service or BingXUniverseService(client=self._client)
        self._account_service = account_service or BingXAccountService(
            client=self._client,
            fmp_client=fmp_client,
        )
        self._ws_hub = ws_hub or BingXWebSocketHub()
        self._exec_quality_policy: ExecutionQualityPolicy = (
            execution_quality_policy or ExecutionQualityPolicy()
        )
        desk_policy = risk_desk_policy or BingXRiskDeskPolicy.from_env()
        if (
            risk_desk_policy is None
            and self.trading_environment == "prod-vst"
            and desk_policy.no_trade_when_provider_degraded
        ):
            desk_policy = replace(desk_policy, no_trade_when_provider_degraded=False)
        self._risk_desk: BingXRiskDesk = risk_desk or BingXRiskDesk(policy=desk_policy)
        # Execution-spam protection: in-memory cooldown cache (symbol → last execution timestamp)
        self._last_execution: dict[str, datetime] = {}
        self._conviction_scores: dict[str, float] = {}
        self._exit_reasons: dict[str, list[str]] = {}
        self._parametric_exit_state: dict[str, _ParametricExitState] = {}
        self._options_snapshot_fn = options_snapshot_fn
        self._venue_technical_fn = venue_technical_fn
        self._fmp_client = fmp_client
        self._massive_client = massive_client
        self._alpaca_client = alpaca_client
        # Initialize Trade Journal table on first instantiation
        try:
            from pathlib import Path

            db_path = Path("data/quantum_analyzer.duckdb")
            init_trade_journal_table(db_path)
        except Exception as e:
            logger.error("trade_journal.init_failed error=%s", e)

    @property
    def dry_run(self) -> bool:
        return self._client.dry_run

    @property
    def trading_environment(self) -> str:
        return getattr(self._client, "trading_environment", "paper")

    @property
    def universe(self) -> tuple[str, ...]:
        return self._universe

    @property
    def risk_policy(self) -> BingXRiskPolicy:
        return self._risk_policy

    async def aclose(self) -> None:
        if self._owns_client:
            await self._client.aclose()

    # ── Public API ────────────────────────────────────────────────────────────
    def status(self) -> dict[str, Any]:
        """Return a static snapshot of bot configuration — safe in any state."""
        return {
            "service": "bingx_bot",
            "dry_run": self.dry_run,
            "trading_environment": self.trading_environment,
            "universe": list(self._universe),
            "scan_interval": self._scan_interval,
            "horizon": self._horizon,
            "risk_policy": asdict(self._risk_policy),
            "execution_quality_policy": asdict(self._exec_quality_policy),
            "filter": {
                "volume_z_threshold": self._volume_z_threshold,
                "heuristic_prob_floor": self._heuristic_prob_floor,
                "scanner_min_score": self._scanner_min_score,
                "scanner_timeframes": list(SCANNER_CONFIRMATION_TIMEFRAMES),
                "scanner_modules": list(SCANNER_CONFIRMATION_MODULES),
                "meta_learner_attached": self._meta_provider is not None,
            },
            "reason_codes": [
                REASON_INSUFFICIENT_BARS,
                REASON_NO_VOLUME_SPIKE,
                REASON_FLAT_RANGE,
                REASON_META_BLOCK,
                REASON_META_LOW_PROB,
                REASON_HEURISTIC_LOW_PROB,
                REASON_RISK_BUDGET_EXHAUSTED,
                REASON_LEVERAGE_CAP,
                REASON_NO_VENUE_PRICE,
                REASON_L2_UNAVAILABLE,
                REASON_L2_SPREAD_TOO_WIDE,
                REASON_L2_DEPTH_TOO_THIN,
                REASON_L2_IMBALANCE_EXTREME,
                REASON_SCANNER_UNAVAILABLE,
                REASON_SCANNER_SCORE_TOO_LOW,
                REASON_SCANNER_TREND_MISALIGNED,
                REASON_SCANNER_INTRADAY_NOT_ALIGNED,
                REASON_SCANNER_DAILY_OPPOSES,
                REASON_SCANNER_PHASE_B_MISSING,
                REASON_SCANNER_VETO_PRESENT,
                REASON_SCANNER_CONFIDENCE_TOO_LOW,
            ],
        }

    async def fetch_klines_for_analysis(
        self,
        symbol: str,
        interval: VALID_KLINE_INTERVAL = "5m",
        limit: int = DEFAULT_KLINES_PER_SYMBOL,
    ) -> list[BingXKline]:
        """Fetch raw klines for the analysis drawer — routes spot vs perp."""
        return await self._fetch_klines_prefer_perp(symbol, interval=interval, limit=limit)

    async def build_analysis_snapshot(
        self,
        symbol: str,
        *,
        interval: VALID_KLINE_INTERVAL = "5m",
        limit: int = 200,
    ) -> dict[str, Any]:
        """Build the canonical drawer payload from ``BingXCandidateAnalysis``."""
        analysis = await build_candidate_analysis(
            symbol,
            bingx_client=self._client,
            fmp_client=self._fmp_client,
            massive_client=self._massive_client,
            alpaca_client=self._alpaca_client,
            ws_hub=self._ws_hub,
            options_snapshot_fn=self._options_snapshot_fn,
            venue_technical_fn=self._venue_technical_fn,
            kline_interval=interval,
            kline_limit=limit,
        )
        return _analysis_snapshot_from_candidate(analysis, interval=interval)

    async def scan(
        self,
        symbols: Iterable[str] | None = None,
        customization: ScannerCustomization | None = None,
    ) -> list[BingXSignal]:
        """Fetch K-lines for the universe and build per-symbol VSA signals.

        L2 depth analysis is attached for synthetic stock perpetuals only —
        crypto and unsupported instruments carry ``lob_analysis=None``. The L2
        fetch happens in parallel with the kline fetch so it adds no latency on
        warm caches.
        """
        target = _synthetic_stock_symbols(tuple(symbols)) if symbols else self._universe
        if not target:
            return []
        snapshots_task = self._snapshot_symbols(target)
        lob_analyses_task = self._lob_analyses_for_symbols(target)
        snapshots, lob_analyses = await asyncio.gather(snapshots_task, lob_analyses_task)
        return [self._snapshot_to_signal(snap, lob_analyses.get(snap.symbol)) for snap in snapshots]

    async def filter_signals(
        self,
        signals: Iterable[BingXSignal],
        *,
        use_scanner_confirmation: bool = True,
        customization: ScannerCustomization | None = None,
    ) -> list[FilterDecision]:
        """Run local/meta gate, optionally adding expensive scanner confirmation."""
        signals_tuple = tuple(signals)
        scanner_rows = (
            await self._scan_confirmation_rows(signals_tuple, customization)
            if use_scanner_confirmation
            else None
        )
        return [await self._evaluate_signal(sig, scanner_rows) for sig in signals_tuple]

    def kill_switch(self, *, reason: str = "operator") -> dict[str, Any]:
        """Engage the Risk Desk kill switch — permanently blocks new orders this session."""
        return self._risk_desk.kill_switch(reason=reason)

    @property
    def risk_desk(self) -> BingXRiskDesk:
        return self._risk_desk

    def build_order_plans(
        self,
        signals: Iterable[BingXSignal],
        decisions: Iterable[FilterDecision],
    ) -> list[BingXOrderPlan]:
        """Pair each signal with its filter decision and apply risk sizing."""
        decisions_by_symbol = {d.symbol: d for d in decisions}
        plans: list[BingXOrderPlan] = []
        for sig in signals:
            decision = decisions_by_symbol.get(sig.symbol)
            plans.append(self._size_signal(sig, decision))
        return plans

    async def _fetch_realized_pnl(self, symbol: str, venue_order_id: str) -> float:
        """Fetch actual realized PnL for a specific order from BingX trade fills."""
        if getattr(self._client, "dry_run", True):
            return 0.0

        for attempt in range(3):
            try:
                fills = await self._client.fetch_trade_history_perp(symbol, limit=20)
                total_pnl = 0.0
                found = False
                for fill in fills:
                    if str(fill.get("orderId")) == str(venue_order_id):
                        pnl_val = fill.get("realizedProfit") or fill.get("realizedPnl") or 0.0
                        total_pnl += float(pnl_val)
                        found = True
                if found:
                    logger.info(
                        "bingx_bot.reconciled_pnl symbol=%s order_id=%s realized_pnl=%.4f attempt=%d",
                        symbol,
                        venue_order_id,
                        total_pnl,
                        attempt + 1,
                    )
                    return total_pnl
                await asyncio.sleep(0.5)
            except Exception as exc:
                logger.warning(
                    "bingx_bot.pnl_fetch_failed symbol=%s order_id=%s attempt=%d error=%s",
                    symbol,
                    venue_order_id,
                    attempt + 1,
                    exc,
                )
                await asyncio.sleep(0.5)
        logger.info(
            "bingx_bot.pnl_reconciliation_zero symbol=%s order_id=%s (no matching fills with PnL found)",
            symbol,
            venue_order_id,
        )
        return 0.0

    async def _log_trade_execution_to_journal(
        self,
        response: BingXOrderResponse,
        decision: RiskDeskDecision,
        realized_pnl: float,
        analysis: BingXCandidateAnalysis | None,
        engine_decision: BingXDecision | None,
        cycle_id: str | None,
    ) -> None:
        """Log successful trade execution to Trade Journal (Caja Negra).

        Captures complete execution state at the moment of fill:
        - Order response (price, qty, venue_order_id)
        - Risk desk decision (authorized, reason_codes)
        - Institutional research snapshot (3 desks frozen)
        - Engine decision scores and reasoning

        This audit trail is persisted to:
        1. DuckDB trade_journal table (primary)
        2. JSONL backup file (compliance retention)
        """
        try:
            symbol = response.symbol
            cycle_id = cycle_id or "unknown"

            # Extract institutional research snapshot from analysis
            inst_research = {}
            if analysis and analysis.institutional_research:
                # Serialize InstitutionalResearchSnapshot
                ir = analysis.institutional_research
                inst_research = {
                    "predictive_desk": (
                        {
                            "direction": ir.predictive.directional_bias if ir.predictive else None,
                            "probability": (
                                ir.predictive.probability_long if ir.predictive else None
                            ),
                            "confidence": ir.predictive.confidence if ir.predictive else None,
                            "trend_strength": (
                                ir.predictive.trend_strength if ir.predictive else None
                            ),
                        }
                        if ir.predictive
                        else None
                    ),
                    "options_gex_desk": (
                        {
                            "gamma_flip_level": (
                                ir.options_gex.gamma_flip_level if ir.options_gex else None
                            ),
                            "is_gamma_negative_regime": (
                                ir.options_gex.is_gamma_negative_regime if ir.options_gex else None
                            ),
                            "tail_risk_severity": (
                                ir.options_gex.tail_risk_severity if ir.options_gex else None
                            ),
                        }
                        if ir.options_gex
                        else None
                    ),
                    "technical_desk": (
                        {
                            "trend_direction": (
                                ir.technical.trend_direction if ir.technical else None
                            ),
                            "smc_bias": ir.technical.smc_bias if ir.technical else None,
                            "technical_quality_score": (
                                ir.technical.technical_quality_score if ir.technical else None
                            ),
                        }
                        if ir.technical
                        else None
                    ),
                }

            # Extract engine decision scoring
            engine_decision_payload = {}
            if engine_decision:
                engine_decision_payload = {
                    "decision": engine_decision.decision,
                    "direction": engine_decision.direction,
                    "confidence": engine_decision.confidence,
                    "score_total": engine_decision.score_total,
                    "module_scores": {
                        "venue": engine_decision.module_scores.venue,
                        "technical": engine_decision.module_scores.technical,
                        "options": engine_decision.module_scores.options,
                        "predictive": engine_decision.module_scores.predictive,
                        "l2": engine_decision.module_scores.l2,
                        "risk": engine_decision.module_scores.risk,
                    },
                    "reason_codes": engine_decision.reason_codes,
                }

            # Create trade journal entry
            entry = TradeJournalEntry(
                execution_timestamp=_utc_iso_now(),
                symbol=symbol,
                side=response.side.upper(),
                quantity=response.requested_qty or 0.0,
                notional_usdt=decision.intent.notional_usdt,
                entry_price=response.price or 0.0,
                decision_score=engine_decision.score_total if engine_decision else 0.0,
                reason_codes=decision.reason_codes,
                venue_order_id=response.venue_order_id,
                realized_pnl=realized_pnl,
                institutional_research_snapshot=inst_research,
                engine_decision_payload=engine_decision_payload,
                dry_run=self.dry_run,
                cycle_id=cycle_id,
            )

            # Persist to DuckDB
            from pathlib import Path

            db_path = Path("data/quantum_analyzer.duckdb")
            persist_trade_execution(entry, db_path)

            # Backup to JSONL
            persist_trade_execution_jsonl(entry, Path("backend/logs/trades"))

            logger.info(
                "trade_journal.execution_logged symbol=%s pnl=%.4f cycle_id=%s venue_order_id=%s",
                symbol,
                realized_pnl,
                cycle_id,
                response.venue_order_id,
            )
        except Exception as e:
            logger.error(
                "trade_journal.execution_log_failed symbol=%s error=%s",
                response.symbol,
                e,
            )

    async def _candidate_analyses_for_symbols(
        self,
        symbols: tuple[str, ...],
    ) -> list[BingXCandidateAnalysis]:
        symbols = _synthetic_stock_symbols(symbols)
        if not symbols:
            return []
        tasks = [
            build_candidate_analysis(
                sym,
                bingx_client=self._client,
                fmp_client=self._fmp_client,
                massive_client=self._massive_client,
                alpaca_client=self._alpaca_client,
                ws_hub=self._ws_hub,
                options_snapshot_fn=self._options_snapshot_fn,
                venue_technical_fn=self._venue_technical_fn,
                kline_interval=self._scan_interval,
                kline_limit=self._klines_per_symbol,
            )
            for sym in symbols
        ]
        results = await asyncio.gather(*tasks, return_exceptions=True)
        analyses: list[BingXCandidateAnalysis] = []
        for sym, result in zip(symbols, results, strict=True):
            if isinstance(result, BaseException):
                logger.warning(
                    "bingx_bot.candidate_analysis_failed symbol=%s error=%s", sym, result
                )
                analyses.append(_empty_candidate_analysis(sym, reason=_error_reason(result)))
                continue
            analyses.append(result)
        return analyses

    def _hydrate_analysis_value_area(
        self, analysis: BingXCandidateAnalysis
    ) -> BingXCandidateAnalysis:
        """Inject GEX snapshot VAL/VAH into the venue technical payload for zone logic."""
        val, vah = _extract_value_area_from_options_metrics(analysis.options.metrics)
        if val is None or vah is None:
            ohlcv_df = _ohlcv_dataframe_for_value_area(analysis)
            if ohlcv_df is not None:
                val, vah = _compute_value_area_from_ohlcv(ohlcv_df)
        if val is None or vah is None:
            return analysis
        return _inject_value_area_into_analysis(analysis, val=val, vah=vah)

    def resolve_price_zone(self, current_spot: float, analysis: BingXCandidateAnalysis) -> str:
        if current_spot is None or current_spot <= 0:
            return "NEUTRAL"

        val, vah = _extract_value_area_from_venue_technical(analysis)
        hydrated = analysis
        if val is None or vah is None:
            hydrated = self._hydrate_analysis_value_area(analysis)
            val, vah = _extract_value_area_from_venue_technical(hydrated)
        if val is None or vah is None:
            val, vah = _extract_value_area_from_options_metrics(hydrated.options.metrics)
        if val is None or vah is None:
            val, vah = _proxy_value_area_from_options_gex(hydrated)
        if val is None and vah is None:
            return "NEUTRAL"
        if val is not None and (vah is None or vah <= val):
            if current_spot <= val:
                return "ACUMULACION"
            return "NEUTRAL"
        if vah is not None and val is None:
            if current_spot >= vah:
                return "DISTRIBUCION"
            return "NEUTRAL"
        assert val is not None and vah is not None

        venue_tech = hydrated.technical.venue_technical if hydrated.technical else None
        payload = venue_tech.get("payload") if isinstance(venue_tech, dict) else {}
        if not isinstance(payload, dict):
            payload = {}

        ms = payload.get("market_structure")
        active_pools = []
        if isinstance(ms, dict):
            active_pools = ms.get("active_pools") or []

        swing_lows = []
        swing_highs = []
        if isinstance(active_pools, list):
            for pool in active_pools:
                if isinstance(pool, dict) and pool.get("is_swept") is not True:
                    ptype = pool.get("type")
                    plevel = pool.get("price_level")
                    if plevel is not None:
                        try:
                            plevel = float(plevel)
                            if ptype == "SwingLow":
                                swing_lows.append(plevel)
                            elif ptype == "SwingHigh":
                                swing_highs.append(plevel)
                        except (ValueError, TypeError):
                            continue

        soporte_inf = min(swing_lows) if swing_lows else val
        resistencia_sup = max(swing_highs) if swing_highs else vah

        if current_spot <= val or current_spot <= soporte_inf:
            return "ACUMULACION"
        elif current_spot >= vah or current_spot >= resistencia_sup:
            return "DISTRIBUCION"
        else:
            return "NEUTRAL"

    def check_order_flow_absorption(self, direction: str, analysis: BingXCandidateAnalysis) -> bool:
        """Check if Order Flow Delta/L2 confirms absorption in the specified direction.
        - LONG: order_flow_delta delta_bias is BULLISH, or L2 imbalance_rho > 0.
        - SHORT: order_flow_delta delta_bias is BEARISH, or L2 imbalance_rho < 0.
        """
        venue_tech = analysis.technical.venue_technical if analysis.technical else None
        if not isinstance(venue_tech, dict):
            return False
        payload = venue_tech.get("payload")
        if not isinstance(payload, dict):
            return False

        of_delta = payload.get("order_flow_delta")
        of_bias = None
        if isinstance(of_delta, dict) and of_delta.get("ok"):
            of_bias = of_delta.get("delta_bias")

        lob = payload.get("lob_dynamics")
        imbalance_rho = 0.0
        if isinstance(lob, dict) and lob.get("ok"):
            result = lob.get("result")
            if isinstance(result, dict):
                imbalance_rho = result.get("imbalance_rho") or 0.0

        if direction == "LONG":
            return of_bias == "BULLISH" or imbalance_rho > 0.0
        elif direction == "SHORT":
            return of_bias == "BEARISH" or imbalance_rho < 0.0
        return False

    def check_order_flow_pressure(self, direction: str, analysis: BingXCandidateAnalysis) -> bool:
        """Check if Order Flow Delta/L2 confirms pressure opposing the position:
        - For LONG: Sell pressure (vendedora masiva) -> delta_bias is BEARISH, or imbalance_rho < 0.
        - For SHORT: Buy pressure (compradora masiva) -> delta_bias is BULLISH, or imbalance_rho > 0.
        """
        venue_tech = analysis.technical.venue_technical if analysis.technical else None
        if not isinstance(venue_tech, dict):
            return False
        payload = venue_tech.get("payload")
        if not isinstance(payload, dict):
            return False

        of_delta = payload.get("order_flow_delta")
        of_bias = None
        if isinstance(of_delta, dict) and of_delta.get("ok"):
            of_bias = of_delta.get("delta_bias")

        lob = payload.get("lob_dynamics")
        imbalance_rho = 0.0
        if isinstance(lob, dict) and lob.get("ok"):
            result = lob.get("result")
            if isinstance(result, dict):
                imbalance_rho = result.get("imbalance_rho") or 0.0

        if direction == "LONG":
            return of_bias == "BEARISH" or imbalance_rho < 0.0
        elif direction == "SHORT":
            return of_bias == "BULLISH" or imbalance_rho > 0.0
        return False

    async def run_cycle(
        self,
        symbols: Iterable[str] | None = None,
        customization: ScannerCustomization | None = None,
    ) -> BingXCycleResult:
        """Run the production Analysis -> Decision -> Risk -> Execute pipeline once."""
        started_at = _utc_iso_now()
        cycle_id = _new_execution_cycle_id()
        target = _synthetic_stock_symbols(tuple(symbols)) if symbols else self._universe

        # Fetch dynamic notional based on real available balance (1% per trade)
        dynamic_notional = await self._get_dynamic_notional()

        # Include open-position symbols so institutional GEX is loaded before exits.
        target = await self._cycle_target_with_open_positions(target)

        analyses = tuple(
            self._hydrate_analysis_value_area(a)
            for a in await self._candidate_analyses_for_symbols(target)
        )

        # ── Explicit L2 order-book snapshot for cycle diagnostics ───────────
        # Fetches raw depth in parallel alongside the analyses to provide a
        # fresh L2 heartbeat independent of the per-analysis l2 block builder.
        l2_health: dict[str, dict[str, Any]] = {}
        l2_tasks = [self._fetch_l2_order_book_for_cycle(sym) for sym in target]
        l2_results = await asyncio.gather(*l2_tasks, return_exceptions=True)
        for sym, l2_result in zip(target, l2_results, strict=True):
            if isinstance(l2_result, BaseException):
                l2_health[sym] = {"ok": False, "error": str(l2_result)}
                logger.warning("bingx_bot.cycle_l2_failed symbol=%s error=%s", sym, l2_result)
            else:
                l2_health[sym] = l2_result
                has_bids = bool(l2_result.get("parsed_bids"))
                has_asks = bool(l2_result.get("parsed_asks"))
                if has_bids and has_asks:
                    logger.info(
                        "bingx_bot.cycle_l2_ok symbol=%s bids=%d asks=%d source=%s",
                        sym,
                        len(l2_result["parsed_bids"]),
                        len(l2_result["parsed_asks"]),
                        l2_result.get("source"),
                    )
                else:
                    logger.warning(
                        "bingx_bot.cycle_l2_empty symbol=%s source=%s",
                        sym,
                        l2_result.get("source"),
                    )

        engine_decisions = tuple(self.decide_candidates(analyses))

        # Exits run after DIAGNOSTICO INSTITUCIONAL (inside decide) so gamma_flip,
        # confluence and shadow_delta come from the same cycle snapshot.
        cycle_analyses_by_symbol = {a.venue_symbol: a for a in analyses}
        exit_executions = await self.evaluate_dynamic_exits(cycle_analyses=cycle_analyses_by_symbol)

        snapshots = tuple(
            _snapshot_from_candidate_analysis(analysis, self._scan_interval)
            for analysis in analyses
        )
        signals = tuple(
            _signal_from_engine_decision(analysis, decision, snapshot, self._horizon)
            for analysis, decision, snapshot in zip(
                analyses, engine_decisions, snapshots, strict=True
            )
        )
        decisions = tuple(_filter_decision_from_engine_decision(d) for d in engine_decisions)
        plans = tuple(
            _order_plan_from_engine_decision(analysis, decision, self._risk_policy)
            for analysis, decision in zip(analyses, engine_decisions, strict=True)
        )
        await self._hydrate_risk_state_from_account()
        order_intents = tuple(
            intent
            for analysis, decision in zip(analyses, engine_decisions, strict=True)
            if (
                intent := self._order_intent_from_decision(
                    analysis,
                    decision,
                    cycle_id=cycle_id,
                    dynamic_notional_override=dynamic_notional,
                )
            )
            is not None
        )
        contract_metadata = await self._contract_metadata_for_intents(order_intents)
        risk_decisions = tuple(
            self.authorize_intents(order_intents, contract_metadata=contract_metadata)
        )
        executions = tuple(
            await self.execute_risk_decisions(
                risk_decisions,
                analyses=analyses,
                engine_decisions=engine_decisions,
                cycle_id=cycle_id,
            )
        )
        all_executions = tuple(exit_executions) + executions
        blocked_reasons = _blocked_reasons(
            engine_decisions=engine_decisions,
            risk_decisions=risk_decisions,
            analyses=analyses,
        )
        finished_at = _utc_iso_now()

        # Audit: capture cycle-level error and execution summary (fire-and-forget)
        try:
            from backend.audit.hooks import audit_error

            for analysis, _decision in zip(analyses, engine_decisions, strict=False):
                sym = getattr(analysis, "venue_symbol", "UNKNOWN")
                errs = getattr(analysis, "errors", {})
                if errs:
                    for source, msg in errs.items():
                        try:
                            asyncio.get_event_loop().create_task(
                                audit_error(
                                    module="bingx",
                                    error_type=f"analysis_{source}",
                                    message=f"{sym}: {msg}",
                                    severity="warning",
                                    context={"symbol": sym, "source": source},
                                )
                            )
                        except RuntimeError:
                            pass
        except Exception:
            pass

        return BingXCycleResult(
            started_at=started_at,
            finished_at=finished_at,
            universe=target,
            snapshots=tuple(snapshots),
            signals=signals,
            decisions=decisions,
            plans=plans,
            executions=all_executions,
            dry_run=self.dry_run,
            trading_environment=self.trading_environment,
            analyses=analyses,
            engine_decisions=engine_decisions,
            order_intents=order_intents,
            risk_decisions=risk_decisions,
            blocked_reasons=blocked_reasons,
            l2_snapshots=l2_health,
        )

    async def get_account_state(self) -> dict[str, Any]:
        """Return full account state: balances, positions, orders and risk metrics."""
        state = await self._account_service.get_account_state()
        state_dict = state.to_dict()
        for pos in state_dict.get("open_positions", []):
            symbol = pos.get("symbol")
            pos["conviction_score"] = self._conviction_scores.get(symbol, 1.0)
            pos["exit_reasons"] = self._exit_reasons.get(symbol, [])
        return state_dict

    async def _hydrate_risk_state_from_account(self) -> None:
        """Best-effort sync of Risk Desk exposure from the current BingX account state."""
        try:
            state = await self.get_account_state()
        except Exception as exc:
            logger.debug("bingx_bot.risk_hydration_skipped error=%s", exc)
            return
        positions = state.get("open_positions")
        if not isinstance(positions, list):
            return
        exposures: dict[str, float] = {}
        for position in positions:
            if not isinstance(position, dict):
                continue
            symbol = str(position.get("symbol") or "").strip()
            if not symbol:
                continue
            notional = _position_notional_usdt(position)
            if notional > 0:
                exposures[symbol] = notional
        self._risk_desk.state.open_positions = exposures
        pnl_today = _float_or_none(state.get("realized_pnl_today_usdt"))
        if pnl_today is not None:
            self._risk_desk.state.realized_pnl_today = pnl_today
        avail_margin = _float_or_none(state.get("available_margin_usdt"))
        if avail_margin is not None:
            self._risk_desk.state.available_margin_usdt = avail_margin

    async def get_universe(self, *, refresh: bool = False) -> list[dict[str, Any]]:
        """Return current filtered universe with liquidity metrics."""
        instruments = (
            await self._universe_service.refresh()
            if refresh
            else await self._universe_service.get_cached_or_discover()
        )
        if instruments:
            self._universe = tuple(item.symbol for item in instruments)
        return [item.to_dict() for item in instruments]

    async def refresh_universe(self) -> list[dict[str, Any]]:
        """Force a fresh contract/ticker/OI scan and update the active universe."""
        return await self.get_universe(refresh=True)

    async def get_funding_rate(self, symbol: str) -> dict[str, Any]:
        """Return current funding payload plus the display symbol requested by the UI."""
        payload = await self._client.fetch_funding_rate(symbol)
        return {"symbol": symbol, **payload}

    async def set_leverage(
        self,
        symbol: str,
        leverage: int,
        *,
        side: str = "BOTH",
    ) -> dict[str, Any]:
        return await self._client.set_leverage_perp(symbol, leverage, side=side)

    async def set_margin_type(self, symbol: str, margin_type: str) -> dict[str, Any]:
        return await self._client.set_margin_type_perp(symbol, margin_type)

    async def cancel_all_orders(self, symbol: str | None = None) -> dict[str, Any]:
        return await self._client.cancel_all_orders_perp(symbol)

    async def close_all_positions(
        self,
        *,
        cancel_orders: bool = True,
        confirm: bool = False,
    ) -> dict[str, Any]:
        """Emergency control used by the router kill-switch endpoint."""
        if cancel_orders:
            cancel_result = await self._client.cancel_all_orders_perp()
        else:
            cancel_result = {"skipped": True}
        close_result = await self._client.close_all_positions(confirm=confirm)
        return {
            "dry_run": self.dry_run,
            "cancel_orders": cancel_result,
            "close_positions": close_result,
            "closed": bool(close_result.get("closed", True)),
        }

    async def latest_tick_snapshot(self, symbol: str) -> dict[str, Any]:
        """Return a small recent-trade snapshot for REST consumers."""
        try:
            trades = await self._client.fetch_recent_trades_perp(symbol, limit=10)
            order_book = await self._client.fetch_order_book_perp(symbol, limit=20)
        except Exception as exc:
            logger.debug("bingx_bot.tick_perp_fallback symbol=%s error=%s", symbol, exc)
            trades = await self._client.fetch_recent_trades(symbol, limit=10)
            order_book = await self._client.fetch_order_book(symbol, limit=20, force_spot=True)
        latest = trades[-1] if trades else {}
        return {
            "symbol": symbol,
            "latest_trade": latest,
            "trades": trades,
            "order_book": order_book,
            "captured_at": _utc_iso_now(),
        }

    async def stream_micro_bars(self, symbol: str) -> Any:
        """Return the WebSocket hub async iterator for 1-second micro-bars."""
        return self._ws_hub.stream_micro_bars(symbol)

    # ── Internal: Scan ────────────────────────────────────────────────────────

    # ── Public: L2 analysis (single symbol) ───────────────────────────────────
    async def l2_analysis_for_symbol(self, symbol: str) -> LOBDynamicsAnalysis | None:
        """Public delegator returning the L2 analysis for one BingX symbol.

        Returns ``None`` for non-equity instruments (the L2 pipeline is wired
        only for ``stock_perp`` / ``stock_index_perp``); returns a
        :class:`LOBDynamicsAnalysis` for equity perps regardless of fetch
        outcome (``ok=False`` carries the adapter reason). Routers must not
        reach into the private ``_lob_analysis_for_symbol`` helper.
        """
        return await self._lob_analysis_for_symbol(symbol)

    # ── Internal: L2 analysis ─────────────────────────────────────────────────
    async def _lob_analyses_for_symbols(
        self,
        symbols: tuple[str, ...],
    ) -> dict[str, LOBDynamicsAnalysis | None]:
        """Fetch L2 (depth) analyses in parallel for BingX L2-capable symbols.

        Unsupported instruments map to ``None`` so consumers can distinguish
        *no-L2-needed* from a failed fetch (which surfaces as
        ``LOBDynamicsAnalysis(ok=False, error=...)`` via the bridge).
        """
        tasks = [self._lob_analysis_for_symbol(sym) for sym in symbols]
        results = await asyncio.gather(*tasks, return_exceptions=True)
        out: dict[str, LOBDynamicsAnalysis | None] = {}
        for sym, result in zip(symbols, results, strict=True):
            if isinstance(result, BaseException):
                logger.warning("bingx_bot.lob_analysis_failed symbol=%s error=%s", sym, result)
                out[sym] = None
                continue
            out[sym] = result
        return out

    async def _lob_analysis_for_symbol(self, symbol: str) -> LOBDynamicsAnalysis | None:
        """Run L2 analysis for BingX venue symbols with supported depth."""
        market_type = self._resolve_market_type(symbol)
        if market_type not in {"stock_perp", "stock_index_perp"}:
            return None
        analysis = await analyze_bingx_l2(
            self._client,
            symbol,
            market_type=market_type,
            limit=20,
        )
        if not analysis.ok:
            logger.warning(
                "bingx_bot.lob_unavailable symbol=%s reason=%s",
                symbol,
                analysis.error,
            )
        return analysis

    async def _fetch_l2_order_book_for_cycle(self, symbol: str) -> dict[str, Any]:
        """Fetch a fresh L2 order-book snapshot for cycle diagnostics.

        Returns the structured ``fetch_order_book`` dict (with parsed bids/asks)
        or an error dict.  Never raises — all failures surface as
        ``{"ok": False, "error": ...}``.
        """
        try:
            return await self._client.fetch_order_book(symbol, limit=20)
        except Exception as exc:
            return {"ok": False, "error": str(exc)}

    def _resolve_market_type(self, symbol: str) -> str | None:
        """Resolve the BingX market_type for ``symbol``.

        Prefers the cached universe (so the policy classification matches what
        ``BingXUniverseService`` already exposes), falling back to
        :func:`classify_instrument` for symbols outside the cache. Returns
        ``None`` only when both paths fail to classify the symbol.
        """
        cached = getattr(self._universe_service, "_cached", None)
        if cached:
            for instrument in cached:
                if instrument.symbol == symbol:
                    return instrument.market_type
        root = underlying_from_bingx_symbol(symbol).upper()
        if not root:
            return None
        return classify_underlying(symbol)

    # ── Internal: Filter ──────────────────────────────────────────────────────

    # ── Internal: Risk + sizing ───────────────────────────────────────────────


# ── Helpers ──────────────────────────────────────────────────────────────────
def _evaluate_l2_execution_quality(
    lob_analysis: LOBDynamicsAnalysis | None,
    policy: ExecutionQualityPolicy,
    *,
    is_stock_perp: bool,
) -> tuple[bool, tuple[str, ...]]:
    """Pre-trade L2 execution-quality gate.

    Returns ``(execution_allowed, reason_codes)``. The gate is block-only:

    * Crypto / non-stock-perp symbols always pass (``True, ()``) — the L2
      bridge does not attach an analysis for them, so there is nothing to
      gate on. Crypto execution quality is enforced by the venue itself.
    * Stock perps with a missing or ``ok=False`` analysis fail closed with
      ``REASON_L2_UNAVAILABLE``. Survival-first: never authorize when L2 is
      unknown — fabricating a "good enough" book is the failure mode this
      gate exists to prevent.
    * Stock perps with a valid analysis are evaluated against the policy:
      spread (as a percent of mid when available, absolute otherwise), bid
      and ask depth floors, and optional imbalance ceiling. Multiple reasons
      can fire simultaneously — all are returned so the UI can surface every
      violation.
    """
    if not is_stock_perp:
        return True, ()

    if lob_analysis is None or not lob_analysis.ok:
        return False, (REASON_L2_UNAVAILABLE,)

    reasons: list[str] = []

    # Spread: prefer relative (% of mid). Fall back to absolute spread treated
    # as percent when mid is unknown — same conservative fallback the bridge
    # uses for the data-quality score, so the two heuristics agree.
    spread = lob_analysis.spread
    if spread is not None:
        mid = lob_analysis.mid_price
        spread_pct = (
            (spread / mid * 100.0) if (mid is not None and mid > 0.0) else max(0.0, float(spread))
        )
        if spread_pct > policy.max_spread_pct:
            reasons.append(REASON_L2_SPREAD_TOO_WIDE)

    # Depth floors — apply independently per side so a one-sided book still
    # fires the reason on the offending side rather than only one global code.
    if lob_analysis.bid_depth is not None and lob_analysis.bid_depth < policy.min_bid_depth_usdt:
        reasons.append(REASON_L2_DEPTH_TOO_THIN)
    elif lob_analysis.ask_depth is not None and lob_analysis.ask_depth < policy.min_ask_depth_usdt:
        # `elif` keeps the reason set deduplicated — a single thin-depth code
        # is enough for the UI; per-side details live in the analysis payload.
        reasons.append(REASON_L2_DEPTH_TOO_THIN)

    # Imbalance — optional. Reads imbalance_rho from the LOB result (range
    # [-1, +1]); absolute value compared against the configured ceiling.
    if (
        policy.max_imbalance_abs is not None
        and lob_analysis.result is not None
        and abs(float(lob_analysis.result.imbalance_rho)) > float(policy.max_imbalance_abs)
    ):
        reasons.append(REASON_L2_IMBALANCE_EXTREME)

    if reasons:
        return False, tuple(reasons)
    return True, ()


def _normalize_bingx_symbol_for_scanner(symbol: str) -> str:
    base = underlying_from_bingx_symbol(symbol)
    return base


def _is_synthetic_stock_symbol(symbol: str) -> bool:
    # BingX VST returns raw API symbols (NCSK*2USD-USDT) on open positions.
    if is_ncsk_vst_stock_perp_symbol(symbol):
        return True
    return classify_underlying(symbol) in {"stock_perp", "stock_index_perp"}


def _synthetic_stock_symbols(symbols: Iterable[str]) -> tuple[str, ...]:
    out: list[str] = []
    seen: set[str] = set()
    for raw_symbol in symbols:
        symbol = str(raw_symbol or "").strip().upper().replace("/", "-")
        if not symbol or symbol in seen:
            continue
        if not _is_synthetic_stock_symbol(symbol):
            logger.info("bingx_bot.symbol_excluded_non_synthetic symbol=%s", symbol)
            continue
        seen.add(symbol)
        out.append(symbol)
    return tuple(out)


def _build_scanner_confirmation_request(
    symbols: Iterable[str],
    customization: ScannerCustomization | None = None,
) -> MarketScannerRequest:
    normalized_symbols: list[str] = []
    seen: set[str] = set()
    for symbol in symbols:
        scanner_symbol = _normalize_bingx_symbol_for_scanner(symbol)
        if scanner_symbol and scanner_symbol not in seen:
            seen.add(scanner_symbol)
            normalized_symbols.append(scanner_symbol)

    effective_customization = customization or ScannerCustomization(
        enabled_modules=list(SCANNER_CONFIRMATION_MODULES),
        module_synthesis_limit=min(max(1, len(normalized_symbols)), 100),
        primary_timeframe="15m",
    )

    return MarketScannerRequest(
        universe="custom",
        symbols=normalized_symbols,
        timeframes=list(SCANNER_CONFIRMATION_TIMEFRAMES),
        direction="both",
        max_rows=max(1, len(normalized_symbols)),
        include_deep_metrics=True,
        include_funding_gate=True,
        filters=MarketScannerFilters(
            min_price=0.0,
            min_volume=0.0,
            min_relative_volume=0.0,
            min_score=0.0,
            allow_reversal=True,
            include_vetoed=True,
        ),
        customization=effective_customization,
    )


def _row_to_dict(row: object) -> dict[str, Any] | None:
    if isinstance(row, dict):
        return row
    model_dump = getattr(row, "model_dump", None)
    if callable(model_dump):
        dumped = model_dump(mode="python")
        return dumped if isinstance(dumped, dict) else None
    return None


def _funding_gate_decision(row: dict[str, Any]) -> tuple[str, tuple[str, ...]]:
    evidence = row.get("funding_gate_evidence")
    if isinstance(evidence, dict):
        suitability = evidence.get("suitability") or evidence.get("funding_suitability")
        reasons = _reason_tuple(
            evidence.get("reason_codes")
            or evidence.get("funding_reason_codes")
            or evidence.get("reasons")
        )
    else:
        suitability = row.get("funding_suitability")
        reasons = _reason_tuple(row.get("funding_reason_codes"))

    normalized = str(suitability or "informational_only").strip().lower()
    if normalized not in {
        "allow",
        "size_down",
        "block",
        "insufficient_data",
        "informational_only",
    }:
        normalized = "informational_only"
    return normalized, reasons


def _reason_tuple(value: object) -> tuple[str, ...]:
    if value is None:
        return ()
    if isinstance(value, str):
        cleaned = value.strip()
        return (cleaned,) if cleaned else ()
    if isinstance(value, list | tuple | set):
        return _dedupe_reason_codes(str(item).strip() for item in value if str(item).strip())
    return ()


def _dedupe_reason_codes(values: Iterable[str]) -> tuple[str, ...]:
    out: list[str] = []
    seen: set[str] = set()
    for value in values:
        if value in seen:
            continue
        seen.add(value)
        out.append(value)
    return tuple(out)


def _order_intent_to_dict(intent: OrderIntent) -> dict[str, Any]:
    return asdict(intent)


def _risk_decision_to_dict(decision: RiskDeskDecision) -> dict[str, Any]:
    return {
        "authorized": decision.authorized,
        "intent": _order_intent_to_dict(decision.intent),
        "idempotency_key": decision.idempotency_key,
        "reason_codes": list(decision.reason_codes),
        "adjusted_quantity": decision.adjusted_quantity,
        "adjusted_entry_price": decision.adjusted_entry_price,
        "already_seen": decision.already_seen,
    }


def _filter_decision_from_engine_decision(decision: BingXDecision) -> FilterDecision:
    return FilterDecision(
        symbol=decision.symbol,
        suitability=decision.decision,
        probability=decision.confidence,
        threshold=0.0,
        provider="bingx_decision_engine",
        reason_codes=tuple(decision.reason_codes),
    )


def _order_plan_from_engine_decision(
    analysis: BingXCandidateAnalysis,
    decision: BingXDecision,
    policy: BingXRiskPolicy,
) -> BingXOrderPlan:
    reference_price = _reference_price_from_analysis(analysis)
    authorized = (
        decision.decision in {"ALLOW", "SIZE_DOWN"}
        and decision.direction in {"LONG", "SHORT"}
        and analysis.market_type in {"stock_perp", "stock_index_perp"}
        and reference_price is not None
        and reference_price > 0
    )
    multiplier = getattr(decision, "sizing_multiplier", 1.0)
    if decision.decision == "SIZE_DOWN" and multiplier >= 1.0:
        multiplier = 0.5
    notional = policy.effective_notional() * multiplier if authorized else 0.0
    return BingXOrderPlan(
        symbol=analysis.venue_symbol,
        side="BUY" if decision.direction == "LONG" else "SELL",
        notional_usdt=notional,
        leverage=policy.leverage,
        quantity=(round(notional / reference_price, 8) if authorized and reference_price else None),
        reference_price=reference_price,
        reason_codes=tuple(decision.reason_codes),
        authorized=authorized,
    )


def _signal_from_engine_decision(
    analysis: BingXCandidateAnalysis,
    decision: BingXDecision,
    snapshot: BingXMarketSnapshot,
    horizon: str,
) -> BingXSignal:
    direction: Literal[LONG, SHORT, FLAT]
    direction = decision.direction if decision.direction in {"LONG", "SHORT"} else "FLAT"
    return BingXSignal(
        symbol=analysis.venue_symbol,
        direction=direction,
        score=decision.score_total,
        horizon=horizon,
        reason_codes=tuple(decision.reason_codes),
        snapshot=snapshot,
        timestamp=analysis.captured_at or _utc_iso_now(),
        source="bingx_candidate_decision_engine",
    )


def _analysis_snapshot_from_candidate(
    analysis: BingXCandidateAnalysis,
    *,
    interval: str,
) -> dict[str, Any]:
    options_bridge = _options_bridge_payload(analysis)
    venue_ta = _legacy_venue_ta(analysis.venue.venue_ta)
    return {
        "venue_symbol": analysis.venue_symbol,
        "underlying_symbol": analysis.underlying_symbol,
        "market_type": analysis.market_type,
        "venue_ta": venue_ta,
        "underlying_ta": (
            analysis.technical.metrics if analysis.technical.status == "available" else None
        ),
        "options": (
            _legacy_options_from_bridge_payload(
                options_bridge,
                spot_hint=_last_close_from_venue(analysis),
            )
            if analysis.options.status == "available"
            else None
        ),
        "options_bridge": options_bridge,
        "probabilistic": _legacy_probabilistic_payload(analysis),
        "predictive_signal": analysis.predictive.signal,
        "exchange_derivatives": {
            "status": analysis.exchange_derivatives.status,
            "source": analysis.exchange_derivatives.source,
            "market_type": analysis.market_type,
            "underlying_symbol": analysis.underlying_symbol,
            "metrics": analysis.exchange_derivatives.metrics,
            "providers": list(analysis.exchange_derivatives.providers),
            "data_sources": list(analysis.exchange_derivatives.data_sources),
            "quality_score": analysis.exchange_derivatives.quality_score,
            "reason": analysis.exchange_derivatives.reason,
        },
        "lob_analysis": analysis.l2.lob_analysis,
        "lob_quality_score": analysis.l2.quality_score,
        "lob_status": _legacy_lob_status(analysis),
        "venue_technical": analysis.technical.venue_technical,
        "data_sources": list(_legacy_analysis_sources(analysis)),
        "errors": _legacy_analysis_errors(analysis),
        "candidate_analysis": analysis.to_dict(),
        "symbol": analysis.venue_symbol,
        "interval": interval,
        "klines": _legacy_kline_points(analysis),
        "ta": venue_ta,
    }


def _legacy_kline_points(analysis: BingXCandidateAnalysis) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for row in analysis.venue.klines:
        open_time_ms = _float_or_none(row.get("open_time_ms"))
        out.append(
            {
                "time": int(open_time_ms // 1000) if open_time_ms is not None else row.get("time"),
                "open": row.get("open"),
                "high": row.get("high"),
                "low": row.get("low"),
                "close": row.get("close"),
                "volume": row.get("volume"),
            }
        )
    return out


def _legacy_venue_ta(venue_ta: dict[str, Any] | None) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "rsi_14": None,
        "ema_9": None,
        "ema_21": None,
        "ema_50": None,
        "vwap": None,
        "vwap_upper_1": None,
        "vwap_lower_1": None,
        "vsa_delta": None,
        "vsa_z_score": None,
        "trend": "neutral",
    }
    if venue_ta:
        payload.update({k: v for k, v in venue_ta.items() if k in payload})
    return payload


def _options_bridge_payload(analysis: BingXCandidateAnalysis) -> dict[str, Any]:
    metrics = analysis.options.metrics
    if isinstance(metrics, dict) and "status" in metrics and "metrics" in metrics:
        return metrics
    return {
        "status": analysis.options.status,
        "source": analysis.options.source,
        "market_type": analysis.market_type,
        "underlying_symbol": analysis.underlying_symbol,
        "proxy_symbol": None,
        "options_symbol": analysis.underlying_symbol,
        "metrics": metrics if analysis.options.status == "available" else None,
        "chain_quality": {},
        "quality_score": analysis.options.quality_score,
        "reason": analysis.options.reason,
        "fetched_at": analysis.captured_at,
    }


def _legacy_options_from_bridge_payload(
    payload: dict[str, Any],
    *,
    spot_hint: float | None,
) -> dict[str, Any] | None:
    metrics = payload.get("metrics")
    if not isinstance(metrics, dict):
        return None
    spot = _positive_float(metrics.get("spot")) or spot_hint
    wall_candidates = [
        value
        for value in (
            _positive_float(metrics.get("zero_gamma")),
            _positive_float(metrics.get("call_wall")),
            _positive_float(metrics.get("put_wall")),
        )
        if value is not None
    ]
    if not wall_candidates:
        gex_wall_price = None
    elif spot is None or spot <= 0:
        gex_wall_price = wall_candidates[0]
    else:
        gex_wall_price = min(wall_candidates, key=lambda value: abs(value - spot))
    out = dict(metrics)
    out.update(
        {
            "gex_wall_price": gex_wall_price,
            "gex_wall_direction": metrics.get("wall_direction"),
            "gex_wall_distance_pct": metrics.get("wall_distance_pct"),
            "iv_percentile": _as_pct(
                _float_or_none(metrics.get("iv_percentile_cross_term"))
                or _float_or_none(metrics.get("iv_rank_hv_rolling"))
            ),
            "put_call_ratio": metrics.get("pcr_oi"),
            "delta_exposure_usd": metrics.get("total_dex"),
        }
    )
    return out


def _legacy_probabilistic_payload(analysis: BingXCandidateAnalysis) -> dict[str, Any] | None:
    if analysis.predictive.status != "available":
        return None
    if isinstance(analysis.predictive.metrics, dict):
        return analysis.predictive.metrics
    return analysis.predictive.signal


def _legacy_lob_status(analysis: BingXCandidateAnalysis) -> str:
    if analysis.l2.status == "available":
        return "active"
    if analysis.market_type in {"stock_perp", "stock_index_perp"} and analysis.l2.lob_analysis:
        return "pending"
    return "unavailable"


def _legacy_analysis_sources(analysis: BingXCandidateAnalysis) -> tuple[str, ...]:
    sources = list(analysis.data_sources)
    if analysis.venue.status == "available":
        sources.append("venue_klines")
    if analysis.options.status == "available":
        sources.append(analysis.options.source)
    if analysis.technical.status == "available":
        sources.append("underlying_equity_ta")
    if analysis.predictive.status == "available":
        sources.append("underlying_probabilistic")
    if analysis.l2.status == "available":
        sources.append(analysis.l2.source)
    return _dedupe_reason_codes(s for s in sources if s)


def _legacy_analysis_errors(analysis: BingXCandidateAnalysis) -> dict[str, str]:
    errors: dict[str, str] = {}
    is_equity = analysis.market_type in {"stock_perp", "stock_index_perp"}
    if analysis.venue.status != "available" and analysis.venue.reason:
        errors["venue"] = f"UNAVAILABLE: {analysis.venue.reason}"
    if is_equity and analysis.options.status != "available" and analysis.options.reason:
        errors["options"] = f"UNAVAILABLE: {analysis.options.reason}"
    if is_equity and analysis.technical.status != "available" and analysis.technical.reason:
        errors["underlying_ta"] = f"UNAVAILABLE: {analysis.technical.reason}"
    if is_equity and analysis.predictive.status != "available" and analysis.predictive.reason:
        errors["probabilistic"] = f"UNAVAILABLE: {analysis.predictive.reason}"
    if is_equity and analysis.l2.status != "available" and analysis.l2.reason:
        errors["l2"] = f"UNAVAILABLE: {analysis.l2.reason}"
    if (
        analysis.exchange_derivatives.status != "available"
        and analysis.exchange_derivatives.reason
        and analysis.exchange_derivatives.reason != "exchange_derivatives_only_for_crypto"
    ):
        errors["exchange_derivatives"] = f"UNAVAILABLE: {analysis.exchange_derivatives.reason}"
    return errors


def _last_close_from_venue(analysis: BingXCandidateAnalysis) -> float | None:
    if not analysis.venue.klines:
        return None
    return _float_or_none(analysis.venue.klines[-1].get("close"))


_MIN_VALUE_AREA_BARS = 50


def _value_area_dict_val_vah(block: object) -> tuple[float | None, float | None]:
    if not isinstance(block, dict):
        return None, None
    val = _float_or_none(block.get("val"))
    vah = _float_or_none(block.get("vah"))
    if val is not None and vah is not None and val > 0 and vah > val:
        return val, vah
    if val is not None and val > 0:
        return val, None
    if vah is not None and vah > 0:
        return None, vah
    return None, None


def _extract_value_area_from_options_metrics(
    options_metrics: dict[str, Any] | None,
) -> tuple[float | None, float | None]:
    if not isinstance(options_metrics, dict):
        return None, None
    for key in ("value_area",):
        val, vah = _value_area_dict_val_vah(options_metrics.get(key))
        if val is not None or vah is not None:
            return val, vah
    chain_quality = options_metrics.get("chain_quality")
    if isinstance(chain_quality, dict):
        val, vah = _value_area_dict_val_vah(chain_quality.get("value_area"))
        if val is not None or vah is not None:
            return val, vah
    inner = options_metrics.get("metrics")
    if isinstance(inner, dict):
        val, vah = _value_area_dict_val_vah(inner.get("value_area"))
        if val is not None or vah is not None:
            return val, vah
    return None, None


def _extract_value_area_from_venue_technical(
    analysis: BingXCandidateAnalysis,
) -> tuple[float | None, float | None]:
    venue_tech = analysis.technical.venue_technical if analysis.technical else None
    if not isinstance(venue_tech, dict):
        return None, None
    payload = venue_tech.get("payload")
    if not isinstance(payload, dict):
        return None, None
    vp = payload.get("volume_profile")
    if not isinstance(vp, dict):
        return None, None
    return _value_area_dict_val_vah(vp)


def _proxy_value_area_from_options_gex(
    analysis: BingXCandidateAnalysis,
) -> tuple[float | None, float | None]:
    """Dealer walls / gamma flip as coarse bounds when VP is not yet on the payload."""
    options_metrics = analysis.options.metrics if analysis.options else None
    if not isinstance(options_metrics, dict):
        return None, None
    inner = (
        options_metrics.get("metrics")
        if isinstance(options_metrics.get("metrics"), dict)
        else options_metrics
    )
    if not isinstance(inner, dict):
        return None, None
    put_wall = _float_or_none(inner.get("put_wall"))
    call_wall = _float_or_none(inner.get("call_wall"))
    gamma_flip = _float_or_none(inner.get("gamma_flip") or inner.get("zero_gamma"))
    val = put_wall if put_wall is not None else gamma_flip
    vah = call_wall if call_wall is not None else gamma_flip
    if val is None and vah is None:
        return None, None
    if val is not None and vah is not None and val >= vah:
        mid = (val + vah) / 2.0
        val = mid * 0.995
        vah = mid * 1.005
    return val, vah


def _ohlcv_dataframe_for_value_area(analysis: BingXCandidateAnalysis) -> Any | None:
    import pandas as pd

    from backend.layer_1_data.datos.massive_equity_bars_fetcher import fetch_equity_daily_bars

    if analysis.venue.klines:
        rows: list[dict[str, float]] = []
        for bar in analysis.venue.klines:
            if not isinstance(bar, dict):
                continue
            try:
                o = float(bar.get("open") or bar.get("close") or 0)
                h = float(bar.get("high") or o)
                low = float(bar.get("low") or o)
                c = float(bar.get("close") or o)
                vol = float(bar.get("volume") or 1.0)
            except (TypeError, ValueError):
                continue
            if c > 0 and h > 0 and low > 0:
                rows.append({"open": o, "high": h, "low": low, "close": c, "volume": max(vol, 1.0)})
        if len(rows) >= _MIN_VALUE_AREA_BARS:
            return pd.DataFrame(rows)

    underlying = underlying_from_bingx_symbol(analysis.venue_symbol)
    _closes, df, _meta = fetch_equity_daily_bars(underlying)
    if df is not None and not df.empty and len(df) >= _MIN_VALUE_AREA_BARS:
        return df
    return None


def _compute_value_area_from_ohlcv(ohlcv_df: Any) -> tuple[float | None, float | None]:
    from backend.layer_3_specialists.tecnico.volume_profile import VolumeProfileEngine

    if ohlcv_df is None or getattr(ohlcv_df, "empty", True):
        return None, None
    if len(ohlcv_df) < _MIN_VALUE_AREA_BARS:
        return None, None
    vp = VolumeProfileEngine.calculate(ohlcv_df)
    if not vp.ok or float(vp.val) <= 0 or float(vp.vah) <= float(vp.val):
        return None, None
    return float(vp.val), float(vp.vah)


def _inject_value_area_into_analysis(
    analysis: BingXCandidateAnalysis,
    *,
    val: float,
    vah: float,
) -> BingXCandidateAnalysis:
    technical = analysis.technical
    venue_tech = technical.venue_technical if technical else None
    if not isinstance(venue_tech, dict):
        venue_tech = {"status": "available", "payload": {}}
    payload = dict(venue_tech.get("payload") or {})
    vp = dict(payload.get("volume_profile") or {})
    vp.update(
        {
            "ok": True,
            "val": round(val, 6),
            "vah": round(vah, 6),
            "source": str(vp.get("source") or "gex_value_area_hydration"),
        }
    )
    payload["volume_profile"] = vp
    venue_tech = {
        **venue_tech,
        "status": venue_tech.get("status") or "available",
        "payload": payload,
    }
    return replace(
        analysis,
        technical=replace(technical, venue_technical=venue_tech),
    )


def _float_or_none(value: object) -> float | None:
    try:
        out = float(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return None
    return out if math.isfinite(out) else None


def _position_notional_usdt(position: dict[str, Any]) -> float:
    for key in (
        "notional_usdt",
        "notional",
        "position_notional",
        "positionNotional",
        "mark_value",
        "markValue",
    ):
        value = _float_or_none(position.get(key))
        if value is not None and value != 0:
            return abs(value)
    size = _float_or_none(position.get("size"))
    mark = _float_or_none(position.get("mark_price") or position.get("markPrice"))
    if size is None or mark is None:
        return 0.0
    return abs(size * mark)


def _positive_float(value: object) -> float | None:
    out = _float_or_none(value)
    return out if out is not None and out > 0 else None


def _as_pct(value: float | None) -> float | None:
    if value is None:
        return None
    return round(value * 100.0 if 0.0 <= value <= 1.0 else value, 4)


def _snapshot_from_candidate_analysis(
    analysis: BingXCandidateAnalysis,
    interval: str,
) -> BingXMarketSnapshot:
    klines = list(analysis.venue.klines)
    latest = _reference_price_from_analysis(analysis)
    closes: tuple[float, ...] = tuple(
        float(k["close"])
        for k in klines[-50:]
        if isinstance(k, dict) and isinstance(k.get("close"), int | float)
    )
    volume_values = [
        float(k["volume"])
        for k in klines
        if isinstance(k, dict) and isinstance(k.get("volume"), int | float)
    ]
    last_volume = volume_values[-1] if volume_values else None
    volume_mean = (
        sum(volume_values[:-1]) / max(len(volume_values) - 1, 1)
        if len(volume_values) >= 2
        else None
    )
    volume_std = None
    volume_z_score = None
    if volume_mean is not None and len(volume_values) >= 2:
        variance = sum((v - volume_mean) ** 2 for v in volume_values[:-1]) / max(
            len(volume_values) - 1,
            1,
        )
        volume_std = math.sqrt(variance) if variance > 0 else 0.0
        volume_z_score = (
            (volume_values[-1] - volume_mean) / volume_std if volume_std and volume_std > 0 else 0.0
        )
    return BingXMarketSnapshot(
        symbol=analysis.venue_symbol,
        interval=interval,
        bars=len(klines),
        latest_close=latest,
        last_volume=last_volume,
        volume_mean=volume_mean,
        volume_std=volume_std,
        volume_z_score=volume_z_score,
        close_position_in_range=None,
        range_pct=None,
        captured_at=analysis.captured_at or _utc_iso_now(),
        closes_recent=closes,
    )


def _reference_price_from_analysis(analysis: BingXCandidateAnalysis) -> float | None:
    ta = analysis.venue.venue_ta or {}
    for value in (
        ta.get("last_price"),
        ta.get("close"),
        analysis.venue.klines[-1].get("close") if analysis.venue.klines else None,
    ):
        try:
            price = float(value)
        except (TypeError, ValueError):
            continue
        if math.isfinite(price) and price > 0:
            return price
    return None


def _spread_fraction_from_analysis(analysis: BingXCandidateAnalysis) -> float | None:
    lob = analysis.l2.lob_analysis or {}
    spread = lob.get("spread")
    mid_price = lob.get("mid_price")
    try:
        spread_f = float(spread)
    except (TypeError, ValueError):
        return None
    try:
        mid_f = float(mid_price)
    except (TypeError, ValueError):
        return None
    if mid_f <= 0:
        return None
    return max(0.0, spread_f / mid_f)


def _provider_health_for_execution(analysis: BingXCandidateAnalysis) -> str:
    required = [analysis.venue.status, analysis.technical.status, analysis.predictive.status]
    if analysis.market_type in {"stock_perp", "stock_index_perp"}:
        required.extend([analysis.l2.status, analysis.options.status])
    return "ok" if all(status == "available" for status in required) else "unavailable"


def _bracket_prices(
    reference_price: float,
    direction: str,
    policy: BingXRiskPolicy,
) -> tuple[float, float]:
    """Return protective stop-loss and take-profit prices for a market entry."""
    stop_pct = policy.stop_loss_pct if policy.stop_loss_pct > 0 else 0.02
    take_pct = policy.take_profit_pct if policy.take_profit_pct > 0 else 0.04
    if direction == "SHORT":
        return (
            round(reference_price * (1.0 + stop_pct), 8),
            round(reference_price * (1.0 - take_pct), 8),
        )
    return (
        round(reference_price * (1.0 - stop_pct), 8),
        round(reference_price * (1.0 + take_pct), 8),
    )


def _blocked_reasons(
    *,
    engine_decisions: tuple[BingXDecision, ...],
    risk_decisions: tuple[RiskDeskDecision, ...],
    analyses: tuple[BingXCandidateAnalysis, ...],
) -> dict[str, list[str]]:
    out: dict[str, list[str]] = {}
    intent_symbols = {d.intent.venue_symbol for d in risk_decisions}
    for analysis, decision in zip(analyses, engine_decisions, strict=True):
        reasons: list[str] = list(decision.reason_codes)
        if decision.decision in {"BLOCK", "INSUFFICIENT_DATA"}:
            reasons.append(decision.decision.lower())
        elif analysis.market_type not in {"stock_perp", "stock_index_perp"}:
            reasons.append("not_execution_market_type")
        elif decision.symbol not in intent_symbols:
            reasons.append("no_order_intent")
        if reasons:
            out[analysis.venue_symbol] = _dedupe_reason_codes(reasons)
    for risk in risk_decisions:
        if not risk.authorized:
            out[risk.intent.venue_symbol] = _dedupe_reason_codes(risk.reason_codes)
    return {symbol: list(reasons) for symbol, reasons in out.items()}


def _client_order_id(cycle_id: str, symbol: str) -> str:
    clean_symbol = "".join(ch for ch in symbol.upper() if ch.isalnum())
    return f"bingxbot_{cycle_id}_{clean_symbol}"[:64]


def _new_execution_cycle_id() -> str:
    return datetime.now(tz=UTC).strftime("%Y%m%dT%H%M%S%fZ")


def _empty_candidate_analysis(symbol: str, *, reason: str) -> BingXCandidateAnalysis:
    underlying = underlying_from_bingx_symbol(symbol)
    return BingXCandidateAnalysis(
        venue_symbol=symbol,
        underlying_symbol=underlying,
        market_type=classify_underlying(symbol),
        venue=BingXVenueBlock(symbol, "unavailable", "none", reason=reason),
        underlying=BingXUnderlyingBlock(underlying, classify_underlying(symbol), reason=reason),
        options=BingXOptionsBlock(reason=reason),
        technical=BingXTechnicalBlock(reason=reason),
        predictive=BingXPredictiveBlock(reason=reason),
        l2=BingXL2Block(reason=reason),
        errors={"candidate_analysis": reason},
        captured_at=_utc_iso_now(),
    )


def _error_reason(exc: BaseException) -> str:
    text = str(exc).strip()
    return text or exc.__class__.__name__


def _empty_snapshot(symbol: str, interval: str) -> BingXMarketSnapshot:
    return BingXMarketSnapshot(
        symbol=symbol,
        interval=interval,
        bars=0,
        latest_close=None,
        last_volume=None,
        volume_mean=None,
        volume_std=None,
        volume_z_score=None,
        close_position_in_range=None,
        range_pct=None,
        captured_at=_utc_iso_now(),
    )


def _features_from_klines(
    symbol: str, interval: str, klines: list[BingXKline]
) -> BingXMarketSnapshot:
    if not klines:
        return _empty_snapshot(symbol, interval)
    last = klines[-1]
    volumes = [k.volume for k in klines]
    if len(volumes) >= 2:
        mean_v = sum(volumes[:-1]) / max(len(volumes) - 1, 1)
        variance = sum((v - mean_v) ** 2 for v in volumes[:-1]) / max(len(volumes) - 1, 1)
        std_v = math.sqrt(variance) if variance > 0 else 0.0
        z = (last.volume - mean_v) / std_v if std_v > 0 else 0.0
    else:
        mean_v = 0.0
        std_v = 0.0
        z = 0.0
    bar_range = max(last.high - last.low, 0.0)
    close_pos = (last.close - last.low) / bar_range if bar_range > 0 else 0.5
    range_pct = (bar_range / last.close * 100.0) if last.close > 0 else 0.0
    closes_recent = tuple(k.close for k in klines[-50:])
    return BingXMarketSnapshot(
        symbol=symbol,
        interval=interval,
        bars=len(klines),
        latest_close=last.close,
        last_volume=last.volume,
        volume_mean=mean_v if len(volumes) >= 2 else None,
        volume_std=std_v if len(volumes) >= 2 else None,
        volume_z_score=z if len(volumes) >= 2 else None,
        close_position_in_range=close_pos,
        range_pct=range_pct,
        captured_at=_utc_iso_now(),
        closes_recent=closes_recent,
    )
