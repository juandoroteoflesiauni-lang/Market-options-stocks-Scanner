from __future__ import annotations
from typing import TYPE_CHECKING, Any

import os

from backend.services.bot.bingx_bot_types import *
from backend.layer_1_data.datos.bingx_client import BingXPerpOrderRequest, BingXOrderRequest
from backend.services.bingx_symbol_linker import display_name_from_bingx_symbol

"""Mixin class for BingX Bot Execution."""

import asyncio
from datetime import UTC, datetime
from collections.abc import Iterable

from backend.config.logger_setup import get_logger

logger = get_logger(__name__)


def _float_or_none(value: object) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from backend.services.bingx_bot_service import *


class BingXBotExecutionMixin:
    pass

    async def execute_plans(self, plans: Iterable[BingXOrderPlan]) -> list[BingXOrderResponse]:
        """Send authorized plans to BingX (intercepted when dry_run=True).

        Routes synthetic stock perpetuals to ``place_order_perp``; regular
        crypto spot orders use ``place_order``. Perp quantity is derived from
        ``notional_usdt / reference_price`` (1-contract-per-share model).
        """
        out: list[BingXOrderResponse] = []
        for plan in plans:
            if not plan.authorized:
                logger.info(
                    "bingx_bot.execute_skipped symbol=%s reasons=%s",
                    plan.symbol,
                    ",".join(plan.reason_codes),
                )
                continue
            if is_perp_symbol(plan.symbol):
                qty = (
                    round(plan.notional_usdt / plan.reference_price, 6)
                    if plan.reference_price
                    else None
                )
                response = await self._client.place_order_perp(
                    BingXPerpOrderRequest(
                        symbol=plan.symbol,
                        side=plan.side,
                        position_side="LONG" if plan.side == "BUY" else "SHORT",
                        order_type="MARKET",
                        quantity=qty,
                    )
                )
            else:
                response = await self._client.place_order(
                    BingXOrderRequest(
                        symbol=plan.symbol,
                        side=plan.side,
                        order_type="MARKET",
                        quote_order_qty=plan.notional_usdt,
                    )
                )
            out.append(response)
        return out

    async def _place_scale_out_orders(
        self,
        intent: OrderIntent,
        quantity: float,
        entry_price: float | None,
        analysis: BingXCandidateAnalysis | None,
        contract_metadata: dict[str, Any] | None,
    ) -> None:
        if not analysis or not quantity:
            return

        from backend.services.bingx_bot_service import _reference_price_from_analysis

        entry = entry_price or _reference_price_from_analysis(analysis)
        if not entry:
            return

        is_long = intent.position_side == "LONG"

        # Absurdly far safety levels as net protection
        if is_long:
            sl_price = entry * 0.5
            tp_price = entry * 6.0
        else:
            sl_price = entry * 1.5
            tp_price = entry * 0.1

        meta = contract_metadata.get(intent.venue_symbol) if contract_metadata else None
        step_size = meta.step_size if meta and hasattr(meta, "step_size") else 0.0001

        async def place_tgt(qty_ratio: float, tgt_price: float, is_stop: bool = False) -> None:
            raw_qty = quantity * qty_ratio
            if step_size > 0:
                import math

                places = max(0, int(-math.log10(step_size))) if step_size < 1 else 0
                tgt_qty = round(raw_qty, places)
            else:
                tgt_qty = round(raw_qty, 2)

            if tgt_qty <= 0.0:
                return

            close_side = "SELL" if is_long else "BUY"
            req = BingXPerpOrderRequest(
                symbol=intent.venue_symbol,
                side=close_side,
                position_side=intent.position_side,
                order_type="STOP_MARKET" if is_stop else "TAKE_PROFIT_MARKET",
                quantity=tgt_qty,
                stop_price=round(tgt_price, 4),
                reduce_only=True,
            )
            await self._client.place_order_perp(req)

        await place_tgt(1.0, sl_price, is_stop=True)
        await place_tgt(1.0, tp_price, is_stop=False)

    async def execute_risk_decisions(
        self,
        decisions: Iterable[RiskDeskDecision],
        analyses: Iterable[BingXCandidateAnalysis] | None = None,
        engine_decisions: Iterable[BingXDecision] | None = None,
        cycle_id: str | None = None,
        contract_metadata: dict[str, Any] | None = None,
    ) -> list[BingXOrderResponse]:
        """Execute only Risk Desk-authorized intents.

        Args:
            decisions: Risk desk decisions (authorized/rejected intents).
            analyses: BingXCandidateAnalysis per symbol (for audit trail).
            engine_decisions: BingXDecision per symbol (for audit trail).
            cycle_id: Cycle identifier for linking trades to analysis cycle.

        When analyses and engine_decisions are provided, successful fills
        are logged to Trade Journal (Caja Negra) with complete institutional
        research snapshot and decision reasoning.
        """
        # Build lookup maps for audit enrichment
        analysis_map: dict[str, BingXCandidateAnalysis] = {}
        decision_map: dict[str, BingXDecision] = {}
        if analyses:
            analysis_map = {a.venue_symbol: a for a in analyses}
        if engine_decisions:
            decision_map = {d.symbol: d for d in engine_decisions}

        out: list[BingXOrderResponse] = []
        for decision in decisions:
            if not decision.authorized:
                logger.info(
                    "bingx_bot.risk_execute_skipped symbol=%s reasons=%s",
                    decision.intent.venue_symbol,
                    ",".join(decision.reason_codes),
                )
                continue
            intent = decision.intent
            symbol = intent.venue_symbol
            side = intent.side

            # ── Execution-spam filter: position awareness ──────────────────
            open_positions = self._risk_desk.state.open_positions
            existing_notional = open_positions.get(symbol, 0.0)
            if existing_notional > 0.0:
                logger.info(
                    "bingx_bot.exec_spam_block symbol=%s side=%s reason=position_already_open "
                    "existing_notional=%.2f",
                    symbol,
                    side,
                    existing_notional,
                )
                continue

            # ── Execution-spam filter: cooldown cache ──────────────────────
            last_time = self._last_execution.get(symbol)
            if last_time is not None:
                elapsed = (datetime.now(UTC) - last_time).total_seconds() / 60.0
                if elapsed < EXECUTION_COOLDOWN_MINUTES:
                    logger.info(
                        "bingx_bot.exec_spam_block symbol=%s side=%s reason=execution_cooldown "
                        "elapsed=%.1fmin cooldown=%.1fmin",
                        symbol,
                        side,
                        elapsed,
                        EXECUTION_COOLDOWN_MINUTES,
                    )
                    continue

            intent = decision.intent
            quantity = decision.adjusted_quantity or intent.quantity

            # ── Check if Execution Slivering (TWAP) is needed ────────────────
            is_slivering_needed = False
            analysis = analysis_map.get(symbol)
            lob_analysis = (
                getattr(analysis.l2, "lob_analysis", None) if (analysis and analysis.l2) else None
            )

            if lob_analysis and isinstance(lob_analysis, dict) and lob_analysis.get("ok"):
                twap_enabled = os.getenv("BINGX_TWAP_SLIVERING_ENABLED", "true").lower() not in {
                    "0",
                    "false",
                    "no",
                    "off",
                }
                spread = _float_or_none(lob_analysis.get("spread"))
                mid = _float_or_none(lob_analysis.get("mid_price"))
                bid_depth = _float_or_none(lob_analysis.get("bid_depth")) or 0.0
                ask_depth = _float_or_none(lob_analysis.get("ask_depth")) or 0.0
                hhi = _float_or_none(lob_analysis.get("hhi_concentration"))

                spread_pct = (spread / mid * 100.0) if (spread and mid) else 0.0
                total_depth = bid_depth + ask_depth

                # Slivering triggers: slightly wide spread, moderate/thin depth, or high HHI concentration
                if twap_enabled and (
                    spread_pct > 0.03 or total_depth < 30000.0 or (hhi is not None and hhi > 0.20)
                ):
                    is_slivering_needed = True
                    logger.info(
                        "bingx_bot.execution_slivering_triggered symbol=%s reason=lob_dynamics "
                        "spread_pct=%.4f%% depth=%.2f HHI=%s",
                        symbol,
                        spread_pct,
                        total_depth,
                        f"{hhi:.4f}" if hhi is not None else "N/A",
                    )

            if is_slivering_needed and not intent.reduce_only:
                num_slivers = 4
                sliver_qty = quantity / num_slivers
                logger.info(
                    "bingx_bot.twap_started symbol=%s total_qty=%.6f sliver_qty=%.6f slivers=4",
                    symbol,
                    quantity,
                    sliver_qty,
                )
                response = await self._client.place_order_perp(
                    BingXPerpOrderRequest(
                        symbol=intent.venue_symbol,
                        side=intent.side,

                        position_side=intent.position_side,

                        order_type=intent.entry_type,

                        quantity=sliver_qty,
                        price=decision.adjusted_entry_price,
                        client_order_id=intent.client_order_id,
                        reduce_only=intent.reduce_only,
                    )
                )
                if response.ok:
                    await self._place_scale_out_orders(
                        intent,
                        sliver_qty,
                        decision.adjusted_entry_price,
                        analysis,
                        contract_metadata,
                    )
                    # Spawn background task for the remaining 3 slivers
                    asyncio.create_task(
                        self._run_adaptive_twap_execution(
                            symbol=intent.venue_symbol,
                            side=intent.side,
                            position_side=intent.position_side,
                            order_type=intent.entry_type,
                            total_qty=quantity,
                            sliver_qty=sliver_qty,
                            remaining_slivers=3,
                            interval_seconds=30.0,
                            price=decision.adjusted_entry_price,
                            reduce_only=intent.reduce_only,
                            intent=intent,
                            analysis=analysis,
                            contract_metadata=contract_metadata,
                        )
                    )
            else:
                response = await self._client.place_order_perp(
                    BingXPerpOrderRequest(
                        symbol=intent.venue_symbol,
                        side=intent.side,

                        position_side=intent.position_side,

                        order_type=intent.entry_type,

                        quantity=quantity,
                        price=decision.adjusted_entry_price,
                        client_order_id=intent.client_order_id,
                        reduce_only=intent.reduce_only,
                    )
                )
                if response.ok:
                    # scale-out Multi-TP post-fill
                    await self._place_scale_out_orders(
                        intent,
                        quantity,
                        decision.adjusted_entry_price,
                        analysis_map.get(intent.venue_symbol),
                        contract_metadata,
                    )

            if response.ok and (response.venue_order_id or response.dry_run):
                realized_pnl = 0.0
                if response.venue_order_id:
                    realized_pnl = await self._fetch_realized_pnl(
                        intent.venue_symbol, response.venue_order_id
                    )
                self._risk_desk.record_fill(decision, realized_pnl=realized_pnl)
                # Update cooldown cache for execution-spam protection
                self._last_execution[intent.venue_symbol] = datetime.now(UTC)

                await self._log_trade_execution_to_journal(
                    response=response,
                    decision=decision,
                    realized_pnl=realized_pnl,
                    analysis=analysis_map.get(intent.venue_symbol),
                    engine_decision=decision_map.get(intent.venue_symbol),
                    cycle_id=cycle_id,
                )
            elif response.ok and not response.venue_order_id:
                logger.warning(
                    "bingx_bot.fill_skipped_no_order_id symbol=%s dry_run=%s",
                    intent.venue_symbol,
                    response.dry_run,
                )
            out.append(response)
        return out

    async def _run_adaptive_twap_execution(
        self,
        symbol: str,
        side: str,
        position_side: str,
        order_type: str,
        total_qty: float,
        sliver_qty: float,
        remaining_slivers: int,
        interval_seconds: float,
        price: float | None,
        reduce_only: bool,
        intent: OrderIntent,
        analysis: BingXCandidateAnalysis | None,
        contract_metadata: dict[str, Any] | None,
    ) -> None:
        logger.info(
            "bingx_bot.twap_started_bg symbol=%s side=%s remaining_slivers=%d",
            symbol,
            side,
            remaining_slivers,
        )
        for i in range(remaining_slivers):
            await asyncio.sleep(interval_seconds)

            # Fetch fresh L2 snapshot before each sub-order for adaptivity
            # If spread exceeds 0.05%, sleep for 5 more seconds and retry once
            try:
                ob = await self._client.fetch_order_book(symbol, limit=10)
                bids = ob.get("bids") or []
                asks = ob.get("asks") or []
                if bids and asks:
                    best_bid = float(
                        bids[0][0] if isinstance(bids[0], list | tuple) else bids[0].get("price")
                    )
                    best_ask = float(
                        asks[0][0] if isinstance(asks[0], list | tuple) else asks[0].get("price")
                    )
                    spread_pct = (best_ask - best_bid) / ((best_bid + best_ask) / 2.0) * 100.0
                    if spread_pct > 0.05:
                        logger.warning(
                            "bingx_bot.twap_spread_high symbol=%s spread=%.4f%% retrying in 5s...",
                            symbol,
                            spread_pct,
                        )
                        await asyncio.sleep(5.0)
            except Exception as exc:
                logger.debug("bingx_bot.twap_adaptive_check_failed symbol=%s error=%s", symbol, exc)

            try:
                sub_resp = await self._client.place_order_perp(
                    BingXPerpOrderRequest(
                        symbol=symbol,
                        side=side,

                        position_side=position_side,

                        order_type=order_type,

                        quantity=sliver_qty,
                        price=price,
                        client_order_id=f"{intent.client_order_id}_s{i+2}",
                        reduce_only=reduce_only,
                    )
                )
                logger.info(
                    "bingx_bot.twap_sliver_placed symbol=%s sliver=%d/%d qty=%.6f response=%s",
                    symbol,
                    i + 2,
                    remaining_slivers + 1,
                    sliver_qty,
                    sub_resp.ok,
                )
                if sub_resp.ok:
                    await self._place_scale_out_orders(
                        intent,
                        sliver_qty,
                        price,
                        analysis,
                        contract_metadata,
                    )
            except Exception as exc:
                logger.error("bingx_bot.twap_sliver_failed symbol=%s error=%s", symbol, exc)

    async def _contract_metadata_for_intents(
        self,
        intents: Iterable[OrderIntent],
    ) -> dict[str, Any]:
        out: dict[str, Any] = {}
        fetcher = getattr(self._client, "fetch_contract_metadata", None)
        if fetcher is None:
            return out
        for intent in intents:
            lookup = display_name_from_bingx_symbol(intent.venue_symbol)
            try:
                raw = fetcher(lookup)
                meta = await raw if asyncio.iscoroutine(raw) else raw
            except Exception as exc:
                logger.warning(
                    "bingx_bot.contract_metadata_unavailable symbol=%s lookup=%s error=%s",
                    intent.venue_symbol,
                    lookup,
                    exc,
                )
                continue
            out[intent.venue_symbol] = meta
            out[lookup] = meta
        return out

    def _bingx_notional_scalars(
        self,
        analysis: BingXCandidateAnalysis,
        decision: BingXDecision,
        reference_price: float,
    ) -> float:
        """Confidence + volatility regime multiplier for BingX notional."""
        from backend.services.options_strategy.sizing_engine import (
            atr_pct_to_vix_proxy,
            equity_confidence_multiplier,
            volatility_regime_scalar,
        )

        conf_mult = equity_confidence_multiplier(
            score=decision.score_total,
            probability=decision.confidence,
        )
        metrics = analysis.technical.metrics or {}
        atr_raw = metrics.get("atr") or metrics.get("ATR")
        atr = _float_or_none(atr_raw)
        if atr is not None and reference_price > 0:
            vix_proxy = atr_pct_to_vix_proxy(atr, reference_price)
        else:
            vix_proxy = float(os.getenv("BINGX_VIX_PROXY", "20.0"))
        regime_mult = volatility_regime_scalar(vix_proxy)
        return conf_mult * regime_mult

    async def _get_dynamic_notional(self) -> float:
        """Fetch real available balance and assign 1% per trade.

        This replaces static notional sizing with dynamic capital allocation.
        Risk Desk guardrails (max_position_notional, max_symbol_exposure, etc.)
        bound exposure downstream — no additional cap is applied here.
        A floor of $1.00 prevents orders below BingX minimum notional.
        If balance fetch fails, falls back to static risk policy notional.
        """
        dynamic_floor_usdt = 1.0
        if os.getenv("BINGX_USE_STATIC_NOTIONAL", "").lower() in {"1", "true", "yes"}:
            return self._risk_policy.effective_notional()
        try:
            balance_data = await self._client.fetch_perp_balance()
            if not balance_data:
                logger.warning("bingx_bot.dynamic_notional balance_data empty, using static")
                return self._risk_policy.effective_notional()

            # Extract USDT balance — handle nested and flat response shapes
            balance_nested = balance_data.get("balance") or {}
            available_usdt = (
                balance_data.get("availableBalance")
                or balance_data.get("available_balance")
                or balance_data.get("availableMargin")
                or balance_nested.get("availableBalance")
                or balance_nested.get("available_balance")
                or balance_nested.get("availableMargin")
            )
            if available_usdt is None or float(available_usdt) <= 0:
                logger.warning(
                    "bingx_bot.dynamic_notional no available balance: %s, using static",
                    balance_data,
                )
                return self._risk_policy.effective_notional()

            # Assign configurable % of available balance per trade
            trade_pct = float(os.getenv("BINGX_TRADE_SIZE_PCT", "0.01"))
            dynamic_notional = float(available_usdt) * trade_pct
            # Floor to prevent orders below exchange minimum notional
            floored_notional = max(dynamic_notional, dynamic_floor_usdt)

            logger.info(
                "bingx_bot.dynamic_notional available=%s, pct=%s, notional=%s",
                available_usdt,
                trade_pct,
                floored_notional,
            )
            return floored_notional
        except Exception as exc:
            logger.warning("bingx_bot.dynamic_notional fetch failed: %s, using static", exc)
            return self._risk_policy.effective_notional()

    def _order_intent_from_decision(
        self,
        analysis: BingXCandidateAnalysis,
        decision: BingXDecision,
        *,
        cycle_id: str,
        dynamic_notional_override: float | None = None,
    ) -> OrderIntent | None:
        if decision.decision not in {"ALLOW", "SIZE_DOWN"}:
            return None
        if decision.direction not in {"LONG", "SHORT"}:
            return None
        if analysis.market_type not in {"stock_perp", "stock_index_perp"}:
            return None

        from backend.services.bingx_bot_service import (
            _reference_price_from_analysis,
            _bracket_prices,
            _client_order_id,
            _spread_fraction_from_analysis,
            _provider_health_for_execution,
        )

        reference_price = _reference_price_from_analysis(analysis)
        if reference_price is None or reference_price <= 0:
            return None

        # Resolve price zone rigidly
        price_zone = self.resolve_price_zone(reference_price, analysis)
        existing_exposure = self._risk_desk.state.symbol_exposure(analysis.venue_symbol)
        is_new_position = existing_exposure == 0.0

        if price_zone != "UNKNOWN" and os.getenv("BINGX_ZONE_VETO_ENABLED", "true").lower() not in {
            "0",
            "false",
            "no",
            "off",
        }:
            # Neutral zone opening veto
            if is_new_position and price_zone == "NEUTRAL":
                logger.info(
                    "bingx_bot.zone_block symbol=%s zone=%s reason=neutral_zone_no_scratch_trade",
                    analysis.venue_symbol,
                    price_zone,
                )
                return None

            # Mutual exclusion zone veto
            if price_zone == "ACUMULACION" and decision.direction == "SHORT":
                logger.info(
                    "bingx_bot.zone_block symbol=%s zone=%s reason=accumulation_veto_shorts",
                    analysis.venue_symbol,
                    price_zone,
                )
                return None
            if price_zone == "DISTRIBUCION" and decision.direction == "LONG":
                logger.info(
                    "bingx_bot.zone_block symbol=%s zone=%s reason=distribution_veto_longs",
                    analysis.venue_symbol,
                    price_zone,
                )
                return None

            # Pyramiding (Scale-In Dinámico) checks for existing positions
            if not is_new_position:
                # Must be in correct zone
                if decision.direction == "LONG" and price_zone != "ACUMULACION":
                    logger.info(
                        "bingx_bot.pyramiding_blocked symbol=%s zone=%s reason=long_pyramiding_only_in_accumulation",
                        analysis.venue_symbol,
                        price_zone,
                    )
                    return None
                if decision.direction == "SHORT" and price_zone != "DISTRIBUCION":
                    logger.info(
                        "bingx_bot.pyramiding_blocked symbol=%s zone=%s reason=short_pyramiding_only_in_distribution",
                        analysis.venue_symbol,
                        price_zone,
                    )
                    return None
                # Must have score >= 0.65
                if decision.score_total < 0.65:
                    logger.info(
                        "bingx_bot.pyramiding_blocked symbol=%s score=%.4f reason=score_too_low",
                        analysis.venue_symbol,
                        decision.score_total,
                    )
                    return None

                # Calculate adaptive size based on proximity to support/resistance and order flow delta/L2 absorption
                capital = self._risk_policy.equity_usdt

                # Retrieve technical data
                venue_tech = analysis.technical.venue_technical if analysis.technical else None
                payload = venue_tech.get("payload") if isinstance(venue_tech, dict) else {}
                vp = payload.get("volume_profile") if isinstance(payload, dict) else {}
                val = vp.get("val") if isinstance(vp, dict) else None
                vah = vp.get("vah") if isinstance(vp, dict) else None

                ms = payload.get("market_structure") if isinstance(payload, dict) else {}
                active_pools = ms.get("active_pools") if isinstance(ms, dict) else []

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

                # Calculate ratio and absorption
                absorption_confirmed = self.check_order_flow_absorption(
                    decision.direction, analysis
                )

                if decision.direction == "LONG":
                    support_base = (
                        min(val, soporte_inf)
                        if (val is not None and soporte_inf is not None)
                        else reference_price
                    )
                    support_top = val if val is not None else reference_price
                    zone_width = support_top - support_base
                    if zone_width <= 0:
                        zone_width = 0.01 * support_top
                    ratio = (support_top - reference_price) / zone_width
                    ratio = max(0.0, min(1.0, ratio))
                else:
                    resistance_base = (
                        max(vah, resistencia_sup)
                        if (vah is not None and resistencia_sup is not None)
                        else reference_price
                    )
                    resistance_top = vah if vah is not None else reference_price
                    zone_width = resistance_base - resistance_top
                    if zone_width <= 0:
                        zone_width = 0.01 * resistance_top
                    ratio = (reference_price - resistance_top) / zone_width
                    ratio = max(0.0, min(1.0, ratio))

                if ratio >= 0.5 and absorption_confirmed:
                    size_pct = 0.05
                elif ratio >= 0.5 or absorption_confirmed:
                    size_pct = 0.04
                else:
                    size_pct = 0.02

                notional = size_pct * capital * self._bingx_notional_scalars(
                    analysis, decision, reference_price
                )
            else:
                # Use dynamic notional if provided, otherwise fall back to static
                if dynamic_notional_override is not None:
                    base_notional = dynamic_notional_override
                else:
                    base_notional = self._risk_policy.effective_notional()

                size_multiplier = 0.5 if decision.decision == "SIZE_DOWN" else 1.0
                sizing_scalar = self._bingx_notional_scalars(analysis, decision, reference_price)
                notional = base_notional * size_multiplier * sizing_scalar
        else:
            # UNKNOWN price zone - fall back to standard behavior
            if dynamic_notional_override is not None:
                base_notional = dynamic_notional_override
            else:
                base_notional = self._risk_policy.effective_notional()

            size_multiplier = 0.5 if decision.decision == "SIZE_DOWN" else 1.0
            sizing_scalar = self._bingx_notional_scalars(analysis, decision, reference_price)
            notional = base_notional * size_multiplier * sizing_scalar

        if notional <= 0:
            return None

        side = "BUY" if decision.direction == "LONG" else "SELL"
        # Round quantity to 2 decimal places max for precision compliance
        quantity = round(notional / reference_price, 2)

        if price_zone != "UNKNOWN":
            stop_loss = None
            take_profit = None
        else:
            stop_loss, take_profit = _bracket_prices(
                reference_price,
                decision.direction,
                self._risk_policy,
            )

        return OrderIntent(
            venue_symbol=analysis.venue_symbol,
            side=side,
            position_side=decision.direction,
            quantity=quantity,
            leverage=int(round(min(self._risk_policy.leverage, self._risk_policy.max_leverage))),
            entry_type="MARKET",
            stop_loss=stop_loss,
            take_profit=take_profit,
            client_order_id=_client_order_id(cycle_id, analysis.venue_symbol),
            reduce_only=False,
            cycle_id=cycle_id,
            notional_usdt=notional,
            spread_pct=_spread_fraction_from_analysis(analysis),
            l2_quality_score=analysis.l2.quality_score,
            provider_health=_provider_health_for_execution(analysis),
            market_type=analysis.market_type,
            requires_l2=(
                analysis.market_type in {"stock_perp", "stock_index_perp"}
                and os.getenv("BINGX_RISK_REQUIRES_L2", "true").lower()
                not in {"0", "false", "no", "off"}
            ),
            price_zone=price_zone,
        )
