"""Bridge between agentic gate and execution mixins. # [TH]"""

from __future__ import annotations

import asyncio
import logging
import os
from collections.abc import Iterable
from dataclasses import replace
from functools import lru_cache
from typing import Literal

from backend.domain.alpaca_models import EquityRiskDecision
from backend.services.agentic_data_facade import AgenticDataFacade
from backend.services.agentic_trade_gate import AgenticGateOutcome, AgenticTradeGate
from backend.services.bingx_risk_desk import RiskDeskDecision

logger = logging.getLogger(__name__)

_DEFAULT_SIGNAL_SCORE = 0.7


@lru_cache(maxsize=1)
def get_agentic_trade_gate() -> AgenticTradeGate | None:
    """Lazy singleton gate; disabled when committee mode is off."""
    mode = (os.environ.get("AI_AGENTIC_COMMITTEE_MODE", "auto") or "").strip().lower()
    if mode == "off":
        return None
    try:
        from backend.layer_1_data.fetchers.fmp_client import FMPClient

        facade = AgenticDataFacade(FMPClient())
        return AgenticTradeGate(data_facade=facade)
    except Exception as exc:
        logger.warning("agentic_execution_bridge.gate_init_failed error=%s", exc)
        return None


async def _audit_outcome(
    outcome: AgenticGateOutcome,
    *,
    module: Literal["alpaca", "bingx"],
    symbol: str,
    contract_symbol: str,
    gate: AgenticTradeGate,
) -> None:
    try:
        from backend.audit.hooks import audit_agentic_decision

        event = gate.build_audit_event(
            outcome,
            module=module,
            symbol=symbol,
            contract_symbol=contract_symbol,
        )
        _ = asyncio.create_task(audit_agentic_decision(event=event))  # noqa: RUF006
    except Exception:
        pass


async def _audit_agentic_passthrough(
    *,
    module: Literal["alpaca", "bingx"],
    symbols: list[str],
) -> None:
    """Registra passthrough cuando el comité agentic está apagado (F10)."""
    if not symbols:
        return
    try:
        from backend.audit.hooks import audit_agentic_decision
        from backend.audit.structured_logger import get_correlation_id

        await audit_agentic_decision(
            event={
                "module": module,
                "symbol": "BATCH",
                "contract_symbol": "BATCH",
                "correlation_id": get_correlation_id() or "",
                "final_decision": "PASSTHROUGH_COMMITTEE_OFF",
                "quant_default_used": True,
                "count": len(symbols),
                "symbols": symbols[:25],
                "committee_mode": os.environ.get("AI_AGENTIC_COMMITTEE_MODE", "off"),
            }
        )
    except Exception as exc:
        logger.debug("agentic.passthrough_audit_failed module=%s error=%s", module, exc)


async def apply_agentic_gate_to_equity_decisions(
    decisions: Iterable[EquityRiskDecision],
    *,
    signal_scores: dict[str, float] | None = None,
    gate: AgenticTradeGate | None = None,
) -> list[EquityRiskDecision]:
    """Filter/scale Alpaca equity decisions through agentic committee."""
    resolved_gate = gate or get_agentic_trade_gate()
    if resolved_gate is None:
        authorized = [
            d.intent.symbol
            for d in decisions
            if d.authorized and (d.adjusted_quantity or d.intent.quantity) > 0
        ]
        if authorized:
            await _audit_agentic_passthrough(module="alpaca", symbols=authorized)
        return list(decisions)

    scores = signal_scores or {}
    out: list[EquityRiskDecision] = []
    for decision in decisions:
        if not decision.authorized:
            out.append(decision)
            continue
        symbol = decision.intent.symbol
        score = scores.get(symbol, _DEFAULT_SIGNAL_SCORE)
        outcome = await resolved_gate.evaluate_trade(
            module="alpaca",
            symbol=symbol,
            contract_symbol=symbol,
            signal_score=score,
        )
        await _audit_outcome(
            outcome,
            module="alpaca",
            symbol=symbol,
            contract_symbol=symbol,
            gate=resolved_gate,
        )
        if not outcome.allow_execute:
            logger.info("agentic.gate_blocked module=alpaca symbol=%s", symbol)
            continue
        qty = decision.adjusted_quantity or decision.intent.quantity
        scaled = resolved_gate.apply_size_modifier(qty, outcome.size_modifier)
        if scaled <= 0:
            continue
        out.append(decision.model_copy(update={"adjusted_quantity": scaled}))
    return out


async def apply_agentic_gate_to_bingx_decisions(
    decisions: Iterable[RiskDeskDecision],
    *,
    signal_scores: dict[str, float] | None = None,
    gate: AgenticTradeGate | None = None,
) -> list[RiskDeskDecision]:
    """Filter/scale BingX risk desk decisions through agentic committee."""
    resolved_gate = gate or get_agentic_trade_gate()
    if resolved_gate is None:
        authorized = [
            d.intent.venue_symbol
            for d in decisions
            if d.authorized and (d.adjusted_quantity or d.intent.quantity) > 0
        ]
        if authorized:
            await _audit_agentic_passthrough(module="bingx", symbols=authorized)
        return list(decisions)

    scores = signal_scores or {}
    out: list[RiskDeskDecision] = []
    for decision in decisions:
        if not decision.authorized:
            out.append(decision)
            continue
        symbol = decision.intent.venue_symbol
        score = scores.get(symbol, _DEFAULT_SIGNAL_SCORE)
        outcome = await resolved_gate.evaluate_trade(
            module="bingx",
            symbol=symbol,
            contract_symbol=symbol,
            signal_score=score,
        )
        await _audit_outcome(
            outcome,
            module="bingx",
            symbol=symbol,
            contract_symbol=symbol,
            gate=resolved_gate,
        )
        if not outcome.allow_execute:
            logger.info("agentic.gate_blocked module=bingx symbol=%s", symbol)
            continue
        qty = decision.adjusted_quantity or decision.intent.quantity
        scaled = resolved_gate.apply_size_modifier(qty, outcome.size_modifier)
        if scaled <= 0:
            logger.info(
                "agentic.gate_zero_qty module=bingx symbol=%s qty=%s modifier=%s",
                symbol,
                qty,
                outcome.size_modifier,
            )
            continue
        out.append(replace(decision, adjusted_quantity=float(scaled)))
    return out


async def should_halt_scanner_from_macro() -> bool:
    """Return True when macro agent requests scanner halt."""
    from backend.services.agentic_macro_state import get_agentic_macro_state

    gate = get_agentic_trade_gate()
    if gate is not None:
        await gate.refresh_macro_risk()
    state = get_agentic_macro_state()
    return state.halt_scanner or state.severity == "CRITICAL"


def macro_stop_loss_multiplier() -> float:
    """Return active macro stop-loss multiplier for risk desks."""
    from backend.services.agentic_macro_state import get_agentic_macro_state

    return get_agentic_macro_state().stop_loss_multiplier


__all__ = [
    "apply_agentic_gate_to_bingx_decisions",
    "apply_agentic_gate_to_equity_decisions",
    "get_agentic_trade_gate",
    "macro_stop_loss_multiplier",
    "should_halt_scanner_from_macro",
]
