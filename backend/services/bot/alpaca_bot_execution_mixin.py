"""Mixin de ejecución del bot Alpaca (bracket orders nativos). # [IM][TH]"""

from __future__ import annotations

from collections.abc import Iterable
from datetime import UTC, datetime

from backend.config.logger_setup import get_logger
from backend.domain.alpaca_models import EquityRiskDecision
from backend.layer_1_data.datos.alpaca_client import (
    AlpacaClient,
    AlpacaOrderRequest,
    AlpacaOrderResponse,
)
from backend.services.alpaca_risk_desk import AlpacaRiskDesk
from backend.services.bot.alpaca_bot_types import EXECUTION_COOLDOWN_MINUTES

logger = get_logger(__name__)


class AlpacaBotExecutionMixin:
    """Ejecuta decisiones de riesgo autorizadas como bracket orders LONG."""

    _client: AlpacaClient
    _risk_desk: AlpacaRiskDesk
    _last_execution: dict[str, datetime]

    def _build_bracket_request(self, decision: EquityRiskDecision) -> AlpacaOrderRequest:
        intent = decision.intent
        quantity = decision.adjusted_quantity or intent.quantity
        take_profit = {"limit_price": round(intent.take_profit, 2)} if intent.take_profit else None
        stop_loss = {"stop_price": round(intent.stop_loss, 2)} if intent.stop_loss else None
        return AlpacaOrderRequest(
            symbol=intent.symbol,
            side="buy",
            type="market",
            time_in_force="day",
            qty=float(quantity),
            client_order_id=intent.client_order_id,
            take_profit=take_profit,
            stop_loss=stop_loss,
        )

    def _is_on_cooldown(self, symbol: str) -> bool:
        last_time = self._last_execution.get(symbol)
        if last_time is None:
            return False
        elapsed_min = (datetime.now(UTC) - last_time).total_seconds() / 60.0
        return elapsed_min < EXECUTION_COOLDOWN_MINUTES

    async def execute_risk_decisions(
        self,
        decisions: Iterable[EquityRiskDecision],
        *,
        skip_symbols: frozenset[str] | None = None,
        signal_scores: dict[str, float] | None = None,
    ) -> list[AlpacaOrderResponse]:
        from backend.services.agentic_execution_bridge import apply_agentic_gate_to_equity_decisions

        gated = await apply_agentic_gate_to_equity_decisions(
            decisions,
            signal_scores=signal_scores,
        )
        skip = skip_symbols or frozenset()
        ordered = sorted(
            gated,
            key=lambda d: (0 if d.intent.route == "priority" else 1, d.intent.symbol),
        )
        out: list[AlpacaOrderResponse] = []
        for decision in ordered:
            symbol = decision.intent.symbol
            if symbol.upper() in skip:
                logger.info(
                    "alpaca_bot.equity_skipped_options_priority symbol=%s",
                    symbol,
                )
                continue
            if not decision.authorized:
                logger.info(
                    "alpaca_bot.risk_execute_skipped symbol=%s reasons=%s",
                    symbol,
                    ",".join(decision.reason_codes),
                )
                continue
            if self._is_on_cooldown(symbol):
                logger.info("alpaca_bot.exec_cooldown symbol=%s", symbol)
                continue
            response = await self._client.place_order(self._build_bracket_request(decision))
            if response.ok:
                self._risk_desk.record_fill(decision)
                self._last_execution[symbol] = datetime.now(UTC)
            out.append(response)
        return out
