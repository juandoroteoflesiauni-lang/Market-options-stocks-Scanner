from __future__ import annotations

from backend.layer_1_data.datos.bingx_client import BingXPerpOrderRequest
from backend.services.bot.bingx_bot_types import *

"""Mixin class for BingX Bot Exits."""

import math
from collections.abc import Mapping
from datetime import UTC, datetime
from typing import Literal

from backend.config.bingx_exit_bucket_policy import (
    EARLY_SL_LEVERAGED_PCT,
    BingXConfluenceCacheEntry,
    adapt_thresholds_for_leverage,
    confluence_broken_for_tp,
    confluence_is_healthy,
    confluence_is_weakened,
    leveraged_pnl_pct,
    resolve_exit_bucket,
)
from backend.config.logger_setup import get_logger
from backend.services.bingx_gex_wall_stop import resolve_wall_stop
from backend.services.bingx_symbol_linker import underlying_from_bingx_symbol

logger = get_logger(__name__)


def _float_or_none(value: object) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from backend.services.bingx_bot_service import *


class BingXBotExitsMixin:
    pass

    async def monitor_exits(self) -> list[BingXOrderResponse]:
        """Backward-compatible alias for the parametric exit engine."""
        return await self.evaluate_dynamic_exits()

    async def _resolve_exit_reference_spot(
        self,
        symbol: str,
        analysis: BingXCandidateAnalysis,
    ) -> float | None:
        """Massive/Polygon spot first, then venue kline / FMP underlying quote."""
        underlying = underlying_from_bingx_symbol(symbol)
        if self._massive_client is not None:
            fetch_price = getattr(self._massive_client, "get_equity_last_price", None)
            if callable(fetch_price):
                try:
                    massive_spot = await fetch_price(underlying)
                except Exception as exc:
                    logger.debug(
                        "bingx_bot.exit_spot_massive_failed symbol=%s error=%s", symbol, exc
                    )
                    massive_spot = None
                if massive_spot is not None and massive_spot > 0:
                    return massive_spot

        if analysis.venue.klines:
            venue_spot = _float_or_none(analysis.venue.klines[-1].get("close"))
            if venue_spot is not None and venue_spot > 0:
                return venue_spot

        if isinstance(analysis.underlying.quote, dict):
            underlying_spot = _float_or_none(analysis.underlying.quote.get("price"))
            if underlying_spot is not None and underlying_spot > 0:
                return underlying_spot

        return None

    @staticmethod
    def _extract_options_exit_signals(
        analysis: BingXCandidateAnalysis,
    ) -> tuple[
        float | None,
        float | None,
        str | None,
        float | None,
        float | None,
        float | None,
        float | None,
    ]:
        options_metrics = analysis.options.metrics or {}
        inner_metrics = (
            options_metrics.get("metrics")
            if isinstance(options_metrics.get("metrics"), dict)
            else options_metrics
        )
        if not isinstance(inner_metrics, dict):
            inner_metrics = {}

        gamma_flip = _float_or_none(
            inner_metrics.get("gamma_flip") or inner_metrics.get("zero_gamma")
        )
        confluence_score = _float_or_none(inner_metrics.get("confluence_score"))
        confluence_signal = inner_metrics.get("confluence_signal")
        shadow_delta = _float_or_none(inner_metrics.get("shadow_delta_imbalance"))
        call_wall = _float_or_none(inner_metrics.get("call_wall"))
        put_wall = _float_or_none(inner_metrics.get("put_wall"))
        net_gex_total = _float_or_none(inner_metrics.get("net_gex_total"))

        # Same bundle injected after institutional snapshot (used by decide() logging).
        options_bundle = analysis.options.predictive_report
        if options_bundle is not None:
            if gamma_flip is None:
                gamma_flip = _float_or_none(options_bundle.gamma_flip_level)
            if shadow_delta is None:
                shadow_delta = _float_or_none(options_bundle.shadow_delta_imbalance)

        ir = analysis.institutional_research
        if ir is not None and ir.options_gex.desk_status.is_available:
            if gamma_flip is None:
                gamma_flip = _float_or_none(ir.options_gex.gamma_flip_level)
            if shadow_delta is None:
                shadow_delta = _float_or_none(ir.options_gex.shadow_delta_imbalance)
            bundle = ir.options_gex.predictive_report
            if bundle is not None:
                if gamma_flip is None:
                    gamma_flip = _float_or_none(bundle.gamma_flip_level)
                if shadow_delta is None:
                    shadow_delta = _float_or_none(bundle.shadow_delta_imbalance)

        return (
            confluence_score,
            gamma_flip,
            (str(confluence_signal) if confluence_signal is not None else None),
            shadow_delta,
            call_wall,
            put_wall,
            net_gex_total,
        )

    @staticmethod
    def _compute_unrealized_pnl_pct(
        *,
        side: str,
        entry_price: float,
        spot_price: float,
    ) -> float | None:
        if entry_price <= 0 or spot_price <= 0:
            return None
        side_norm = side.upper()
        if side_norm == "LONG":
            return ((spot_price - entry_price) / entry_price) * 100.0
        if side_norm == "SHORT":
            return ((entry_price - spot_price) / entry_price) * 100.0
        return None

    async def _round_position_qty(self, symbol: str, raw_qty: float) -> float:
        if raw_qty <= 0:
            return 0.0
        step_size = 0.0001
        min_qty = 0.0
        try:
            meta = await self._client.fetch_contract_metadata(symbol)
            step_size = float(meta.step_size)
            min_qty = float(meta.min_qty)
        except Exception:
            pass
        if step_size > 0:
            places = max(0, int(-math.log10(step_size))) if step_size < 1 else 0
            qty = round(raw_qty, places)
        else:
            qty = round(raw_qty, 4)
        if min_qty > 0 and 0 < qty < min_qty:
            return 0.0
        return qty

    async def _place_reduce_market(
        self,
        *,
        symbol: str,
        position_side: str,
        quantity: float,
        reason: str,
        pnl_pct: float | None = None,
        pnl_usd: float | None = None,
    ) -> BingXOrderResponse | None:
        qty = await self._round_position_qty(symbol, quantity)
        if qty <= 0:
            logger.info(
                "bingx_bot.evaluate_dynamic_exits skip_reduce symbol=%s reason=%s qty_rounded_zero",
                symbol,
                reason,
            )
            return None
        close_side = "SELL" if position_side.upper() == "LONG" else "BUY"
        logger.warning(
            "bingx_bot.evaluate_dynamic_exits REDUCE symbol=%s side=%s qty=%.6f reason=%s",
            symbol,
            position_side,
            qty,
            reason,
        )
        resp = await self._client.place_order_perp(
            BingXPerpOrderRequest(
                symbol=symbol,
                side=close_side,
                position_side=position_side,
                order_type="MARKET",
                quantity=qty,
                reduce_only=True,
            )
        )
        if not resp.ok and resp.error and "Hedge mode" in resp.error:
            logger.warning(
                "bingx_bot.evaluate_dynamic_exits hedge_retry symbol=%s reason=%s",
                symbol,
                reason,
            )
            resp = await self._client.place_order_perp(
                BingXPerpOrderRequest(
                    symbol=symbol,
                    side=close_side,
                    position_side=position_side,
                    order_type="MARKET",
                    quantity=qty,
                    reduce_only=False,
                )
            )
        if resp.ok and pnl_pct is not None and pnl_usd is not None:
            from backend.audit.process_recorder import record_trade_result

            try:
                await record_trade_result(
                    module="bingx",
                    symbol=symbol,
                    pnl_pct=pnl_pct,
                    pnl_usd=pnl_usd,
                    exit_reason=reason,
                )
            except Exception as exc:
                logger.error("bingx_bot.audit_trade_result_failed symbol=%s error=%s", symbol, exc)
        return resp

    async def _place_full_close(
        self,
        *,
        symbol: str,
        position_side: str,
        quantity: float,
        reason: str,
        pnl_pct: float | None = None,
        pnl_usd: float | None = None,
    ) -> BingXOrderResponse | None:
        response = await self._place_reduce_market(
            symbol=symbol,
            position_side=position_side,
            quantity=abs(quantity),
            reason=reason,
            pnl_pct=pnl_pct,
            pnl_usd=pnl_usd,
        )
        if response is not None and response.ok:
            self._clear_exit_tracking(symbol)
        return response

    async def _apply_gex_wall_exit(
        self,
        *,
        symbol: str,
        side: str,
        cycle_mode: str,
        current_spot: float,
        call_wall: float | None,
        put_wall: float | None,
        zero_gamma: float | None,
        net_gex_total: float | None,
        position_size: float,
        entry_price: float,
        pnl_pct: float | None,
        executions: list[BingXOrderResponse],
    ) -> float:
        """Motor ④ GEX wall stop on the slow cycle — returns updated size.

        Single source of truth via ``resolve_wall_stop`` (same core as
        ``decide()``). Wall breached → full close; proximity to the directional
        wall → 20% trim. Slow-cycle only; never raises; returns the (possibly
        reduced) size.
        """
        if cycle_mode != "slow":
            return position_size

        wall = resolve_wall_stop(
            direction=side,
            spot=current_spot,
            call_wall=call_wall,
            put_wall=put_wall,
            zero_gamma=zero_gamma,
            net_gex_total=net_gex_total or 0.0,
        )

        if wall.invalidates_direction:
            logger.warning(
                "bingx_bot.gex_wall_invalidation symbol=%s side=%s spot=%.4f wall=%.4f",
                symbol,
                side,
                current_spot,
                wall.wall_price or 0.0,
            )
            pnl_usd = (pnl_pct / 100.0) * (entry_price * position_size) if pnl_pct else None
            resp = await self._place_full_close(
                symbol=symbol,
                position_side=side,
                quantity=position_size,
                reason="gex_wall_invalidation",
                pnl_pct=pnl_pct,
                pnl_usd=pnl_usd,
            )
            if resp is not None and resp.ok:
                executions.append(resp)
                return 0.0
            return position_size

        if not (wall.active and wall.stop_price is not None and position_size > 0):
            return position_size

        # Blend with the stop computed at entry (decide() may have used a
        # slightly different spot): pick the more conservative level. The stored
        # map is optional / forward-compatible.
        effective_stop = wall.stop_price
        stored = getattr(self, "_gex_wall_stop_prices", {}).get(symbol)
        is_long = side.upper() == "LONG"
        if stored is not None:
            effective_stop = max(effective_stop, stored) if is_long else min(effective_stop, stored)

        triggered = current_spot <= effective_stop if is_long else current_spot >= effective_stop
        if not triggered:
            return position_size

        trim_qty = position_size * 0.20
        logger.info(
            "bingx_bot.gex_wall_proximity_close symbol=%s side=%s spot=%.4f stop=%.4f "
            "wall=%.4f erosion=%.3f",
            symbol,
            side,
            current_spot,
            effective_stop,
            wall.wall_price or 0.0,
            wall.wall_erosion_score,
        )
        trim_usd = (pnl_pct / 100.0) * (entry_price * trim_qty) if pnl_pct else None
        resp = await self._place_reduce_market(
            symbol=symbol,
            position_side=side,
            quantity=trim_qty,
            reason="gex_wall_proximity_close",
            pnl_pct=pnl_pct,
            pnl_usd=trim_usd,
        )
        if resp is not None and resp.ok:
            executions.append(resp)
            return position_size - trim_qty
        return position_size

    def _clear_exit_tracking(self, symbol: str) -> None:
        self._conviction_scores.pop(symbol, None)
        self._exit_reasons.pop(symbol, None)
        self._parametric_exit_state.pop(symbol, None)

    def _leveraged_state_for(
        self,
        symbol: str,
        position_size: float,
        entry_confluence: float | None,
    ) -> _LeveragedExitState:
        state = self._parametric_exit_state.get(symbol)
        if state is None or state.initial_size <= 0:
            state = _LeveragedExitState(
                initial_size=abs(position_size),
                entry_confluence_score=entry_confluence,
            )
            self._parametric_exit_state[symbol] = state
        elif state.entry_confluence_score is None and entry_confluence is not None:
            state.entry_confluence_score = entry_confluence
        return state

    def _parametric_state_for(self, symbol: str, position_size: float) -> _LeveragedExitState:
        return self._leveraged_state_for(symbol, position_size, None)

    def _extract_institutional_risk_flags(
        self,
        analysis: BingXCandidateAnalysis | None,
    ) -> tuple[bool, str]:
        if analysis is None or analysis.institutional_research is None:
            return False, "LOW"
        gex = analysis.institutional_research.options_gex
        return bool(gex.speed_instability_warning), str(gex.tail_risk_severity or "LOW")

    def _build_confluence_cache_entry(
        self,
        symbol: str,
        analysis: BingXCandidateAnalysis,
    ) -> BingXConfluenceCacheEntry:
        score, gamma_flip, signal, _, _, _, _ = self._extract_options_exit_signals(analysis)
        speed_instability, tail_risk = self._extract_institutional_risk_flags(analysis)
        return BingXConfluenceCacheEntry(
            symbol=symbol,
            underlying=underlying_from_bingx_symbol(symbol),
            confluence_score=score,
            confluence_signal=signal,
            gamma_flip=gamma_flip,
            speed_instability=speed_instability,
            tail_risk_severity=tail_risk,
            updated_at_iso=datetime.now(UTC).isoformat(),
        )

    def _update_confluence_cache_from_analyses(
        self,
        analyses: tuple[BingXCandidateAnalysis, ...] | list[BingXCandidateAnalysis],
    ) -> None:
        for analysis in analyses:
            symbol = analysis.venue_symbol
            if not symbol:
                continue
            self._confluence_cache[symbol] = self._build_confluence_cache_entry(symbol, analysis)

    def _cached_confluence(self, symbol: str) -> BingXConfluenceCacheEntry | None:
        entry = self._confluence_cache.get(symbol)
        return entry if isinstance(entry, BingXConfluenceCacheEntry) else None

    async def _evaluate_leveraged_exit_ladder(
        self,
        *,
        symbol: str,
        pos_side: str,
        position_size: float,
        entry_price: float,
        pnl_pct: float | None,
        pnl_leveraged: float | None,
        leverage: float,
        spot: float | None,
        cache_entry: BingXConfluenceCacheEntry | None,
        entry_confluence: float | None,
    ) -> tuple[list[BingXOrderResponse], float]:
        """Escalera TP1/TP2/SL defensivo en % PnL apalancado."""
        executions: list[BingXOrderResponse] = []
        if pnl_leveraged is None or position_size <= 0:
            return executions, position_size

        pnl_lev = round(pnl_leveraged, 2)
        root = underlying_from_bingx_symbol(symbol)
        thresholds = adapt_thresholds_for_leverage(resolve_exit_bucket(root), leverage)
        side_norm = pos_side.upper()
        if side_norm not in ("LONG", "SHORT"):
            return executions, position_size

        state = self._leveraged_state_for(symbol, position_size, entry_confluence)
        remaining = position_size
        weakened = confluence_is_weakened(
            cache_entry,
            side=side_norm,  # type: ignore[arg-type]
            spot=spot,
            entry_score_at_open=state.entry_confluence_score,
        )
        healthy = confluence_is_healthy(
            cache_entry,
            side=side_norm,  # type: ignore[arg-type]
            spot=spot,
        )
        broken_tp = confluence_broken_for_tp(
            cache_entry,
            side=side_norm,  # type: ignore[arg-type]
            spot=spot,
        )

        sl_trigger = (
            pnl_lev <= EARLY_SL_LEVERAGED_PCT
            if weakened
            else pnl_lev <= thresholds.sl_def_leveraged_pct
        )
        if not state.sl_def_done and weakened and sl_trigger:
            trim_qty = remaining * thresholds.sl_def_trim_ratio
            trim_usd = (pnl_pct / 100.0) * (entry_price * trim_qty) if pnl_pct else None
            resp = await self._place_reduce_market(
                symbol=symbol,
                position_side=pos_side,
                quantity=trim_qty,
                reason="leveraged_sl_def_confluence_weakened",
                pnl_pct=pnl_pct,
                pnl_usd=trim_usd,
            )
            if resp is not None and resp.ok:
                executions.append(resp)
                remaining -= trim_qty
                state.sl_def_done = True
            elif resp is not None:
                logger.error(
                    "bingx_bot.leveraged_sl_failed symbol=%s error=%s",
                    symbol,
                    resp.error,
                )

        if not state.tp1_done and pnl_lev >= thresholds.tp1_leveraged_pct and not broken_tp:
            trim_qty = remaining * thresholds.tp1_trim_ratio
            trim_usd = (pnl_pct / 100.0) * (entry_price * trim_qty) if pnl_pct else None
            resp = await self._place_reduce_market(
                symbol=symbol,
                position_side=pos_side,
                quantity=trim_qty,
                reason=f"leveraged_tp1_{thresholds.bucket}",
                pnl_pct=pnl_pct,
                pnl_usd=trim_usd,
            )
            if resp is not None and resp.ok:
                executions.append(resp)
                remaining -= trim_qty
                state.tp1_done = True
            elif resp is not None:
                logger.error(
                    "bingx_bot.leveraged_tp1_failed symbol=%s error=%s",
                    symbol,
                    resp.error,
                )

        if (
            not state.tp2_done
            and state.tp1_done
            and pnl_lev >= thresholds.tp2_leveraged_pct
            and healthy
        ):
            trim_qty = remaining * thresholds.tp2_trim_ratio
            trim_usd = (pnl_pct / 100.0) * (entry_price * trim_qty) if pnl_pct else None
            resp = await self._place_reduce_market(
                symbol=symbol,
                position_side=pos_side,
                quantity=trim_qty,
                reason=f"leveraged_tp2_{thresholds.bucket}",
                pnl_pct=pnl_pct,
                pnl_usd=trim_usd,
            )
            if resp is not None and resp.ok:
                executions.append(resp)
                remaining -= trim_qty
                state.tp2_done = True
            elif resp is not None:
                logger.error(
                    "bingx_bot.leveraged_tp2_failed symbol=%s error=%s",
                    symbol,
                    resp.error,
                )

        return executions, remaining

    async def _execute_fade_and_flip_short(
        self,
        *,
        symbol: str,
        analysis: BingXCandidateAnalysis,
        spot_price: float,
        remaining_size: float,
        pnl_pct: float | None = None,
        pnl_usd: float | None = None,
    ) -> list[BingXOrderResponse]:
        executions: list[BingXOrderResponse] = []
        close_resp = await self._place_full_close(
            symbol=symbol,
            position_side="LONG",
            quantity=remaining_size,
            reason="fade_and_flip_close_long",
            pnl_pct=pnl_pct,
            pnl_usd=pnl_usd,
        )
        if close_resp is not None:
            executions.append(close_resp)
        if close_resp is None or not close_resp.ok:
            return executions

        try:
            await self.set_leverage(symbol, PARAMETRIC_FLIP_LEVERAGE, side="SHORT")
            await self.set_margin_type(symbol, PARAMETRIC_FLIP_MARGIN_TYPE)
        except Exception as exc:
            logger.warning(
                "bingx_bot.evaluate_dynamic_exits flip_leverage_failed symbol=%s error=%s",
                symbol,
                exc,
            )

        notional = await self._get_dynamic_notional()
        qty = notional / spot_price if spot_price > 0 else None
        qty = await self._round_position_qty(symbol, float(qty or 0.0))
        if qty <= 0:
            return executions

        logger.warning(
            "bingx_bot.evaluate_dynamic_exits FLIP_SHORT symbol=%s qty=%.6f notional=%.2f",
            symbol,
            qty,
            notional,
        )
        flip_resp = await self._client.place_order_perp(
            BingXPerpOrderRequest(
                symbol=symbol,
                side="SELL",
                position_side="SHORT",
                order_type="MARKET",
                quantity=qty,
            )
        )
        executions.append(flip_resp)
        if flip_resp.ok:
            self._last_execution[symbol] = datetime.now(UTC)
        return executions

    async def _open_position_underlying_roots(self) -> frozenset[str]:
        """Roots subyacentes con posición abierta (tier quant completo)."""
        try:
            account_state = await self._account_service.get_account_state()
        except Exception as exc:
            logger.debug("bingx_bot.open_roots_skipped error=%s", exc)
            return frozenset()
        roots = {
            underlying_from_bingx_symbol(pos.symbol)
            for pos in account_state.open_positions
            if pos.symbol
        }
        return frozenset(root for root in roots if root)

    async def _cycle_target_with_open_positions(self, target: tuple[str, ...]) -> tuple[str, ...]:
        """Union scan universe with symbols that currently have open positions."""
        try:
            account_state = await self._account_service.get_account_state()
            open_symbols = tuple(pos.symbol for pos in account_state.open_positions if pos.symbol)
        except Exception as exc:
            logger.debug("bingx_bot.cycle_open_positions_union_skipped error=%s", exc)
            return target
        if not open_symbols:
            return target
        from backend.services.bingx_bot_service import _synthetic_stock_symbols

        merged = _synthetic_stock_symbols((*target, *open_symbols))
        extra = sorted(set(merged) - set(target))
        if extra:
            logger.info(
                "bingx_bot.cycle_universe_expanded base=%d total=%d open_only=%s",
                len(target),
                len(merged),
                extra,
            )
        return merged

    async def evaluate_dynamic_exits(
        self,
        *,
        cycle_analyses: Mapping[str, BingXCandidateAnalysis] | None = None,
        cycle_mode: Literal["fast", "slow"] = "slow",
    ) -> list[BingXOrderResponse]:
        """Escalera apalancada (fast/slow) + salidas estructurales solo en slow."""
        logger.info(
            "bingx_bot.evaluate_dynamic_exits started cycle_mode=%s cycle_analyses=%s",
            cycle_mode,
            "yes" if cycle_analyses else "no",
        )
        try:
            account_state = await self._account_service.get_account_state()
            open_positions = account_state.open_positions
        except Exception as exc:
            logger.error("bingx_bot.evaluate_dynamic_exits account_failed error=%s", exc)
            return []

        if not open_positions:
            logger.debug("bingx_bot.evaluate_dynamic_exits no open positions")
            return []

        executions: list[BingXOrderResponse] = []
        open_symbols = {pos.symbol for pos in open_positions}
        for tracked in list(self._parametric_exit_state):
            if tracked not in open_symbols:
                self._parametric_exit_state.pop(tracked, None)

        for pos in open_positions:
            symbol = pos.symbol
            position_size = abs(pos.size)
            if position_size <= 0:
                continue

            analysis: BingXCandidateAnalysis | None = None
            if cycle_analyses is not None:
                analysis = cycle_analyses.get(symbol)
            if analysis is None and cycle_mode == "slow":
                from backend.config.shared_options_tier_policy import is_full_quant_tier
                from backend.services.bingx_bot_service import build_candidate_analysis

                open_roots = await self._open_position_underlying_roots()
                try:
                    analysis = await build_candidate_analysis(
                        symbol,
                        bingx_client=self._client,
                        fmp_client=self._fmp_client,
                        massive_client=self._massive_client,
                        alpaca_client=self._alpaca_client,
                        ws_hub=self._ws_hub,
                        options_snapshot_fn=self._options_snapshot_fn,
                        venue_technical_fn=self._venue_technical_fn,
                        dark_pool_fn=self._dark_pool_fn,
                        kline_interval=self._scan_interval,
                        kline_limit=self._klines_per_symbol,
                        full_quant_tier=is_full_quant_tier(
                            symbol,
                            open_position_roots=open_roots,
                        ),
                    )
                    self._confluence_cache[symbol] = self._build_confluence_cache_entry(
                        symbol, analysis
                    )
                except Exception as exc:
                    logger.error(
                        "bingx_bot.evaluate_dynamic_exits analysis_failed symbol=%s error=%s",
                        symbol,
                        exc,
                    )
                    analysis = None
            elif analysis is not None and cycle_mode == "slow":
                self._confluence_cache[symbol] = self._build_confluence_cache_entry(
                    symbol, analysis
                )
                logger.debug(
                    "bingx_bot.evaluate_dynamic_exits reusing_cycle_analysis symbol=%s",
                    symbol,
                )

            cache_entry = self._cached_confluence(symbol)

            # Resolve current spot from the position's dynamic payload (truth of the Exchange)
            current_spot = (
                pos.current_price
                if (hasattr(pos, "current_price") and pos.current_price is not None)
                else pos.mark_price
            )

            pnl_pct = None
            pnl_usd = None
            if current_spot is not None and pos.entry_price > 0:
                pnl_pct = self._compute_unrealized_pnl_pct(
                    side=pos.side,
                    entry_price=pos.entry_price,
                    spot_price=current_spot,
                )
                if pnl_pct is not None:
                    pnl_usd = (pnl_pct / 100.0) * (pos.entry_price * position_size)

            # ── Structural Stop Loss (slow only) ─────────────────────────────
            if cycle_mode == "slow" and current_spot is not None and analysis is not None:
                # Find support/resistance limits
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

                if pos.side == "LONG":
                    support_limit = (
                        min(val, soporte_inf)
                        if (val is not None and soporte_inf is not None)
                        else None
                    )
                    if (
                        support_limit is not None
                        and current_spot < support_limit
                        and self.check_order_flow_pressure("LONG", analysis)
                    ):
                        logger.warning(
                            "bingx_bot.structural_sl_triggered symbol=%s reason=support_zone_broken",
                            symbol,
                        )
                        close_resp = await self._place_full_close(
                            symbol=symbol,
                            position_side=pos.side,
                            quantity=position_size,
                            reason="support_zone_broken",
                            pnl_pct=pnl_pct,
                            pnl_usd=pnl_usd,
                        )
                        if close_resp is not None:
                            executions.append(close_resp)
                        continue
                elif pos.side == "SHORT":
                    resistance_limit = (
                        max(vah, resistencia_sup)
                        if (vah is not None and resistencia_sup is not None)
                        else None
                    )
                    if (
                        resistance_limit is not None
                        and current_spot > resistance_limit
                        and self.check_order_flow_pressure("SHORT", analysis)
                    ):
                        logger.warning(
                            "bingx_bot.structural_sl_triggered symbol=%s reason=resistance_zone_broken",
                            symbol,
                        )
                        close_resp = await self._place_full_close(
                            symbol=symbol,
                            position_side=pos.side,
                            quantity=position_size,
                            reason="resistance_zone_broken",
                            pnl_pct=pnl_pct,
                            pnl_usd=pnl_usd,
                        )
                        if close_resp is not None:
                            executions.append(close_resp)
                        continue

            (
                confluence_score,
                gamma_flip,
                confluence_signal,
                shadow_delta,
                call_wall,
                put_wall,
                net_gex_total,
            ) = (
                self._extract_options_exit_signals(analysis)
                if analysis is not None
                else (
                    cache_entry.confluence_score if cache_entry else None,
                    None,
                    None,
                    None,
                    None,
                    None,
                    None,
                )
            )
            if analysis is None and cache_entry is not None:
                gamma_flip = cache_entry.gamma_flip
                confluence_signal = cache_entry.confluence_signal

            conv_score = confluence_score if confluence_score is not None else 0.5
            reasons: list[str] = []
            gamma_contradicts = False
            if (
                current_spot is not None
                and gamma_flip is not None
                and (
                    (pos.side == "LONG" and current_spot < gamma_flip)
                    or (pos.side == "SHORT" and current_spot > gamma_flip)
                )
            ):
                gamma_contradicts = True
            if gamma_contradicts:
                conv_score = max(0.0, conv_score - 0.5)
                reasons.append("gamma_flip_regime_flipped")

            signal_opposes = False
            if confluence_signal is not None:
                conf_sig = confluence_signal.upper().strip()
                if (pos.side == "LONG" and conf_sig in ("BEARISH", "SHORT", "SELL")) or (
                    pos.side == "SHORT" and conf_sig in ("BULLISH", "LONG", "BUY")
                ):
                    signal_opposes = True
            if signal_opposes:
                conv_score = max(0.0, conv_score - 0.4)
                reasons.append("confluence_signal_contradicts")
            if (
                confluence_score is not None
                and confluence_score < PARAMETRIC_FLIP_CONFLUENCE_CEILING
            ):
                reasons.append("confluence_score_too_low")

            self._conviction_scores[symbol] = round(conv_score, 4)
            self._exit_reasons[symbol] = reasons

            # ── GEX Wall Stop (Motor ④, slow only) ───────────────────────────
            if current_spot is not None:
                position_size = await self._apply_gex_wall_exit(
                    symbol=symbol,
                    side=pos.side,
                    cycle_mode=cycle_mode,
                    current_spot=current_spot,
                    call_wall=call_wall,
                    put_wall=put_wall,
                    zero_gamma=gamma_flip,
                    net_gex_total=net_gex_total,
                    position_size=position_size,
                    entry_price=pos.entry_price,
                    pnl_pct=pnl_pct,
                    executions=executions,
                )

            # ── Shadow Delta Reversal Hedge (slow only) ──────────────────────
            if (
                cycle_mode == "slow"
                and current_spot is not None
                and pnl_pct is not None
                and pnl_pct >= 1.0
                and (
                    (pos.side == "LONG" and shadow_delta is not None and shadow_delta < -0.50)
                    or (pos.side == "SHORT" and shadow_delta is not None and shadow_delta > 0.50)
                )
            ):
                trim_qty = position_size * 0.30
                logger.info(
                    "bingx_bot.shadow_delta_reversal_hedge symbol=%s side=%s spot=%.4f pnl=%.2f%% shadow_delta=%.2f",
                    symbol,
                    pos.side,
                    current_spot,
                    pnl_pct,
                    shadow_delta,
                )
                trim_usd = (pnl_pct / 100.0) * (pos.entry_price * trim_qty) if pnl_pct else None
                resp = await self._place_reduce_market(
                    symbol=symbol,
                    position_side=pos.side,
                    quantity=trim_qty,
                    reason="shadow_delta_reversal_hedge",
                    pnl_pct=pnl_pct,
                    pnl_usd=trim_usd,
                )
                if resp is not None:
                    executions.append(resp)
                    position_size -= trim_qty

            leverage_factor = float(
                pos.leverage if (hasattr(pos, "leverage") and pos.leverage) else 5.0
            )
            pnl_real_apalancado = leveraged_pnl_pct(
                side=pos.side,  # type: ignore[arg-type]
                entry_price=pos.entry_price,
                mark_price=current_spot or pos.mark_price or 0.0,
                leverage=leverage_factor,
            )
            if pnl_real_apalancado is None and pnl_pct is not None:
                pnl_real_apalancado = pnl_pct * leverage_factor

            logger.info(
                "bingx_bot.evaluate_dynamic_exits symbol=%s side=%s entry=%s current_spot=%s "
                "pnl_pct=%s pnl_real_apalancado=%s gamma_flip=%s confluence=%s shadow_delta=%s "
                "cycle_mode=%s cache=%s",
                symbol,
                pos.side,
                pos.entry_price,
                current_spot,
                pnl_pct,
                pnl_real_apalancado,
                gamma_flip,
                confluence_score,
                shadow_delta,
                cycle_mode,
                "yes" if cache_entry else "no",
            )

            # Fade-and-flip (slow only)
            if (
                cycle_mode == "slow"
                and pos.side == "LONG"
                and pnl_pct is not None
                and pnl_pct >= PARAMETRIC_PROFIT_ZONE_MIN_PCT
                and confluence_score is not None
                and confluence_score < PARAMETRIC_FLIP_CONFLUENCE_CEILING
                and current_spot is not None
                and gamma_flip is not None
                and current_spot < gamma_flip
                and shadow_delta is not None
                and shadow_delta < 0.0
            ):
                flip_execs = await self._execute_fade_and_flip_short(
                    symbol=symbol,
                    analysis=analysis,
                    spot_price=current_spot,
                    remaining_size=position_size,
                    pnl_pct=pnl_pct,
                    pnl_usd=pnl_usd,
                )
                executions.extend(flip_execs)
                continue

            ladder_execs, position_size = await self._evaluate_leveraged_exit_ladder(
                symbol=symbol,
                pos_side=pos.side,
                position_size=position_size,
                entry_price=pos.entry_price,
                pnl_pct=pnl_pct,
                pnl_leveraged=pnl_real_apalancado,
                leverage=leverage_factor,
                spot=current_spot,
                cache_entry=cache_entry,
                entry_confluence=confluence_score,
            )
            executions.extend(ladder_execs)

        return executions
