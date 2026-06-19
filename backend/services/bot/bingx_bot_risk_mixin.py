"""Mixin class for BingX Bot Risk."""

from __future__ import annotations

import os
from collections.abc import Iterable
from typing import TYPE_CHECKING, Any

from backend.config.logger_setup import get_logger
from backend.services.bingx_decision_engine import BingXDecisionConfig, decide
from backend.services.bot.bingx_bot_types import *

logger = get_logger(__name__)


if TYPE_CHECKING:
    from backend.services.bingx_bot_service import *


class BingXBotRiskMixin:
    pass

    def decide_candidates(
        self,
        analyses: Iterable[BingXCandidateAnalysis],
        *,
        mode: ExecutionMode | None = None,
        config: BingXDecisionConfig | None = None,
    ) -> list[BingXDecision]:
        """Run the multi-module decision engine over a batch of analyses.

        The decision engine is *complementary* to ``filter_signals``: the
        latter consults the meta-learner / heuristic on a per-``BingXSignal``
        basis, while this path inspects the *full* analysis contract
        (venue + technical + options + predictive + L2) and applies
        institutional gates (L2 live gate, predictive floor, options
        contradiction, score band → SIZE_DOWN/ALLOW/BLOCK).

        ``mode`` defaults to ``"live"`` when the underlying client is live
        and ``"dry_run"`` otherwise — this keeps the L2 live-mode BLOCK in
        sync with the executor's actual posture without callers having to
        re-derive it.
        """
        from backend.services.agentic_macro_state import get_agentic_macro_state

        macro_state = get_agentic_macro_state()
        if macro_state.halt_scanner or macro_state.severity == "CRITICAL":
            logger.warning("bingx_bot.macro_halt_scanner severity=%s", macro_state.severity)
            return []
        try:
            import asyncio as _aio

            from backend.services.agentic_execution_bridge import get_agentic_trade_gate

            gate = get_agentic_trade_gate()
            if gate is not None:
                _aio.get_event_loop().create_task(gate.refresh_macro_risk())
        except Exception:
            pass
        resolved_mode: ExecutionMode = mode or ("dry_run" if self.dry_run else "live")
        resolved_config = config or BingXDecisionConfig.from_env()
        decisions = [
            decide(analysis, mode=resolved_mode, config=resolved_config) for analysis in analyses
        ]
        # Audit: capture decision snapshots (fire-and-forget)
        try:
            import asyncio as _aio
            import contextlib as _ctx

            from backend.audit.hooks import audit_bingx_decision

            _analyses_list = list(analyses) if not isinstance(analyses, list) else analyses
            for analysis, decision in zip(_analyses_list, decisions, strict=False):
                with _ctx.suppress(RuntimeError):
                    _aio.get_event_loop().create_task(
                        audit_bingx_decision(analysis=analysis, decision=decision)
                    )
        except Exception:
            pass
        return decisions

    def authorize_intents(
        self,
        intents: Iterable[OrderIntent],
        *,
        contract_metadata: dict[str, Any] | None = None,
    ) -> list[RiskDeskDecision]:
        """Run all intents through the Risk Desk guardrails.

        ``contract_metadata`` is a mapping of venue_symbol → BingXContractMetadata
        (or any object with ``quantity_precision``, ``price_precision``,
        ``min_qty``, ``min_notional`` attributes).  When ``None``, precision
        validation is skipped and quantities pass through unmodified.
        """
        meta = contract_metadata or {}
        return [
            self._risk_desk.authorize_intent(
                intent, contract_metadata=meta.get(intent.venue_symbol)
            )
            for intent in intents
        ]

    def _size_signal(
        self,
        signal: BingXSignal,
        decision: FilterDecision | None,
    ) -> BingXOrderPlan:
        reasons: list[str] = []
        if decision is None or decision.suitability in ("BLOCK", "INSUFFICIENT_DATA"):
            reasons.extend(decision.reason_codes if decision else (REASON_INSUFFICIENT_BARS,))
            return BingXOrderPlan(
                symbol=signal.symbol,
                side="BUY" if signal.direction == "LONG" else "SELL",
                notional_usdt=0.0,
                leverage=self._risk_policy.leverage,
                quantity=None,
                reference_price=signal.snapshot.latest_close,
                reason_codes=tuple(reasons),
                authorized=False,
            )

        if self._risk_policy.leverage > self._risk_policy.max_leverage:
            reasons.append(REASON_LEVERAGE_CAP)
            return BingXOrderPlan(
                symbol=signal.symbol,
                side="BUY" if signal.direction == "LONG" else "SELL",
                notional_usdt=0.0,
                leverage=self._risk_policy.leverage,
                quantity=None,
                reference_price=signal.snapshot.latest_close,
                reason_codes=tuple(reasons),
                authorized=False,
            )

        base_notional = self._risk_policy.effective_notional()
        if base_notional <= 0:
            reasons.append(REASON_RISK_BUDGET_EXHAUSTED)
            return BingXOrderPlan(
                symbol=signal.symbol,
                side="BUY" if signal.direction == "LONG" else "SELL",
                notional_usdt=0.0,
                leverage=self._risk_policy.leverage,
                quantity=None,
                reference_price=signal.snapshot.latest_close,
                reason_codes=tuple(reasons),
                authorized=False,
            )

        # SIZE_DOWN halves the notional — never goes back to full size.
        notional = base_notional * (0.5 if decision.suitability == "SIZE_DOWN" else 1.0)
        price = signal.snapshot.latest_close
        if price is None or price <= 0:
            reasons.append(REASON_NO_VENUE_PRICE)
            return BingXOrderPlan(
                symbol=signal.symbol,
                side="BUY" if signal.direction == "LONG" else "SELL",
                notional_usdt=0.0,
                leverage=self._risk_policy.leverage,
                quantity=None,
                reference_price=price,
                reason_codes=tuple(reasons),
                authorized=False,
            )

        # L2 execution-quality gate — only active for synthetic stock perps.
        # Crypto symbols pass through unchanged. The gate is *block-only*: it
        # never authorizes sizing, it only vetoes candidates whose book is too
        # thin, too wide or too one-sided for safe entry.
        market_type = self._resolve_market_type(signal.symbol)
        is_stock_perp = market_type in {"stock_perp", "stock_index_perp"}

        exec_quality_on = os.getenv("BINGX_EXEC_QUALITY_ENABLED", "true").lower() not in {
            "0",
            "false",
            "no",
            "off",
        }
        if exec_quality_on:
            from backend.services.bingx_bot_service import _evaluate_l2_execution_quality

            exec_allowed, l2_reasons = _evaluate_l2_execution_quality(
                signal.lob_analysis,
                self._exec_quality_policy,
                is_stock_perp=is_stock_perp,
            )
            if not exec_allowed:
                reasons.extend(l2_reasons)
                return BingXOrderPlan(
                    symbol=signal.symbol,
                    side="BUY" if signal.direction == "LONG" else "SELL",
                    notional_usdt=0.0,
                    leverage=self._risk_policy.leverage,
                    quantity=None,
                    reference_price=price,
                    reason_codes=tuple(reasons),
                    authorized=False,
                )

        quantity = round((notional * self._risk_policy.leverage) / price, 8)
        return BingXOrderPlan(
            symbol=signal.symbol,
            side="BUY" if signal.direction == "LONG" else "SELL",
            notional_usdt=round(notional, 4),
            leverage=self._risk_policy.leverage,
            quantity=quantity,
            reference_price=price,
            reason_codes=tuple(reasons),
            authorized=True,
        )
