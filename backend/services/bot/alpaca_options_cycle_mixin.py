"""Mixin: ciclo de opciones Alpaca integrado en R1/R2 del bot dual. # [PD-3][TH]"""

from __future__ import annotations

import os
from datetime import UTC, datetime
from typing import Any

from backend.config.alpaca_options_route_config import (
    AlpacaOptionsRoute,
    alpaca_options_enabled,
    alpaca_options_priority_over_equity,
    get_options_config_for_route,
)
from backend.config.logger_setup import get_logger
from backend.domain.alpaca_models import AlpacaDecision
from backend.layer_1_data.datos.alpaca_client import AlpacaClient
from backend.models.options_strategy import RiskSessionState, StrategyDecision
from backend.services.options_strategy.input_builder import (
    build_strategy_input,
    clear_strategy_input_cache,
)
from backend.services.options_strategy.pipeline import OptionsStrategyPipeline
from backend.services.options_strategy.signal_loop import SignalLoopEntry

logger = get_logger(__name__)

_MAX_R1_OPTIONS_PER_CYCLE = int(os.getenv("ALPACA_OPTIONS_R1_MAX_PER_CYCLE", "6"))
_MAX_R2_OPTIONS_PER_CYCLE = int(os.getenv("ALPACA_OPTIONS_R2_MAX_PER_CYCLE", "3"))


def _eligible_equity_decisions(
    decisions: list[AlpacaDecision],
    route: AlpacaOptionsRoute,
) -> list[AlpacaDecision]:
    """Candidatos con señal alcista permitida para intentar opciones."""
    out: list[AlpacaDecision] = []
    for decision in decisions:
        if decision.route != route:
            continue
        if decision.decision not in {"ALLOW", "SIZE_DOWN"}:
            continue
        if decision.direction != "LONG":
            continue
        out.append(decision)
    out.sort(key=lambda d: d.score, reverse=True)
    return out


def _entry_from_execution(
    symbol: str,
    audit_id: str,
    log_decision: Any,
    *,
    executed: bool,
    execution_ok: bool | None,
) -> SignalLoopEntry:
    return SignalLoopEntry(
        symbol=symbol,
        audit_id=audit_id,
        decision=str(log_decision.decision),
        structure=str(log_decision.recommended_structure),
        direction=str(log_decision.direction),
        confidence=log_decision.confidence,
        playbook_family=log_decision.playbook_family,
        veto=log_decision.veto_triggered,
        reason_codes=log_decision.reason_codes,
        executed=executed,
        execution_ok=execution_ok,
    )


class AlpacaOptionsCycleMixin:
    """Ejecuta Options Strategy antes del equity cuando está habilitado."""

    _client: AlpacaClient

    async def run_integrated_options_cycle(
        self,
        *,
        r1_decisions: list[AlpacaDecision],
        r2_decisions: list[AlpacaDecision],
        r2_symbols: tuple[str, ...],
        execute: bool,
    ) -> tuple[tuple[dict[str, Any], ...], frozenset[str], float]:
        """Corre opciones R1→R2. Retorna entradas, símbolos con fill OK y premium reservado."""
        if not alpaca_options_enabled():
            return (), frozenset(), 0.0

        clear_strategy_input_cache()
        moment = datetime.now(tz=UTC)
        session = RiskSessionState()
        entries: list[dict[str, Any]] = []
        executed_symbols: set[str] = set()
        reserved_premium = 0.0

        r1_candidates = _eligible_equity_decisions(r1_decisions, "priority")[
            :_MAX_R1_OPTIONS_PER_CYCLE
        ]
        r2_candidates = _eligible_equity_decisions(r2_decisions, "scan")[:_MAX_R2_OPTIONS_PER_CYCLE]

        for route, candidates, extra_symbols in (
            ("priority", r1_candidates, ()),
            ("scan", r2_candidates, r2_symbols),
        ):
            config = get_options_config_for_route(
                route,  # type: ignore[arg-type]
                r2_symbols=extra_symbols if route == "scan" else (),
            )
            for decision in candidates:
                symbol = decision.symbol.upper()
                try:
                    from backend.services.agentic_execution_bridge import get_agentic_trade_gate

                    gate = get_agentic_trade_gate()
                    if gate is not None and execute:
                        outcome = await gate.evaluate_trade(
                            module="alpaca",
                            symbol=symbol,
                            contract_symbol=symbol,
                            signal_score=float(decision.score),
                        )
                        from backend.audit.hooks import audit_agentic_decision

                        event = gate.build_audit_event(
                            outcome,
                            module="alpaca",
                            symbol=symbol,
                            contract_symbol=symbol,
                        )
                        await audit_agentic_decision(event=event)
                        if not outcome.allow_execute:
                            entries.append(
                                {
                                    "symbol": symbol,
                                    "route": route,
                                    "decision": StrategyDecision.NO_TRADE,
                                    "reason": "agentic_committee_pass",
                                    "executed": False,
                                }
                            )
                            continue
                    include_r1 = route == "priority"
                    inp = build_strategy_input(
                        symbol,
                        as_of=moment,
                        include_r1_enrichment=include_r1,
                        route=route,  # type: ignore[arg-type]
                    )
                    result = await OptionsStrategyPipeline.run(
                        inp,
                        config=config,
                        session=session,
                        persist=True,
                        execute=execute and not self._client.dry_run,
                        client=self._client,
                    )
                    log = result.audit_log
                    playbook = log.playbook_decision
                    exec_ok: bool | None = None
                    if result.execution is not None:
                        exec_ok = result.execution.ok
                        if exec_ok:
                            executed_symbols.add(symbol)
                            premium = (
                                float(log.execution_payload.max_premium_usd)
                                if (log.execution_payload is not None)
                                else 0.0
                            )
                            reserved_premium += premium
                            risk_pct = (
                                log.execution_payload.risk_budget_pct
                                if log.execution_payload is not None
                                else playbook.risk_budget_pct
                            )
                            from backend.services.options_strategy.portfolio_heat import (
                                symbol_sector,
                            )

                            sector = symbol_sector(symbol)
                            sector_map = dict(session.sector_risk_budget_pct)
                            if sector is not None:
                                sector_map[sector] = sector_map.get(sector, 0.0) + risk_pct
                            session = session.model_copy(
                                update={
                                    "open_positions": session.open_positions + 1,
                                    "open_symbols": tuple(
                                        sorted(set(session.open_symbols) | {symbol.upper()})
                                    ),
                                    "total_risk_budget_pct": session.total_risk_budget_pct
                                    + risk_pct,
                                    "sector_risk_budget_pct": sector_map,
                                }
                            )
                    entry = _entry_from_execution(
                        symbol,
                        log.audit_id,
                        playbook,
                        executed=result.execution is not None,
                        execution_ok=exec_ok,
                    )
                    entries.append({**entry.as_dict(), "route": route})
                    logger.info(
                        "alpaca_bot.options_route symbol=%s route=%s decision=%s "
                        "structure=%s executed=%s ok=%s",
                        symbol,
                        route,
                        playbook.decision,
                        playbook.recommended_structure,
                        result.execution is not None,
                        exec_ok,
                    )
                except Exception as exc:
                    logger.warning(
                        "alpaca_bot.options_failed symbol=%s route=%s error=%s",
                        symbol,
                        route,
                        exc,
                    )
                    entries.append(
                        {
                            "symbol": symbol,
                            "route": route,
                            "decision": StrategyDecision.NO_TRADE,
                            "error": str(exc),
                            "executed": False,
                        }
                    )

        if entries:
            logger.info(
                "alpaca_bot.options_cycle_complete entries=%d executed_symbols=%d "
                "reserved_premium=%.2f priority_equity=%s",
                len(entries),
                len(executed_symbols),
                reserved_premium,
                alpaca_options_priority_over_equity(),
            )
        return tuple(entries), frozenset(executed_symbols), reserved_premium


__all__ = ["AlpacaOptionsCycleMixin"]
