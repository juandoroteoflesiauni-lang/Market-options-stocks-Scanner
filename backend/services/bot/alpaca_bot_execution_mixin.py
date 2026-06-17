"""Mixin de ejecución del bot Alpaca (bracket orders + Elite DMA/VWAP/TWAP). # [IM][TH]"""

from __future__ import annotations

from collections.abc import Iterable
from datetime import UTC, datetime
from typing import Any

from backend.config.alpaca_institutional_config import AlpacaEliteOrderConfig
from backend.config.logger_setup import get_logger
from backend.domain.alpaca_models import EquityRiskDecision
from backend.layer_1_data.datos.alpaca_client import (
    AlpacaClient,
    AlpacaOrderRequest,
    AlpacaOrderResponse,
)
from backend.services.alpaca_pre_trade_risk_gate import PreTradeRiskGate
from backend.services.alpaca_risk_desk import AlpacaRiskDesk
from backend.services.bot.alpaca_bot_types import EXECUTION_COOLDOWN_MINUTES

logger = get_logger(__name__)


class AlpacaBotExecutionMixin:
    """Ejecuta decisiones de riesgo autorizadas como bracket orders LONG."""

    _client: AlpacaClient
    _risk_desk: AlpacaRiskDesk
    _last_execution: dict[str, datetime]

    def _elite_config(self) -> AlpacaEliteOrderConfig:
        return AlpacaEliteOrderConfig.from_env()

    def _build_advanced_instructions(
        self,
        decision: EquityRiskDecision,
    ) -> dict[str, Any] | None:
        """Construye advanced_instructions para Alpaca Elite si aplica."""
        cfg = self._elite_config()
        if not cfg.enabled:
            return None
        intent = decision.intent
        notional = (decision.adjusted_quantity or intent.quantity) * intent.reference_price
        if notional < cfg.min_notional_for_elite_usd:
            return None
        if cfg.algorithm == "DMA":
            payload: dict[str, Any] = {
                "algorithm": "DMA",
                "destination": cfg.destination,
            }
            if cfg.display_qty is not None:
                payload["display_qty"] = cfg.display_qty
            return payload
        payload = {"algorithm": cfg.algorithm}
        if cfg.start_time_iso:
            payload["start_time"] = cfg.start_time_iso
        if cfg.end_time_iso:
            payload["end_time"] = cfg.end_time_iso
        return payload

    def _build_bracket_request(self, decision: EquityRiskDecision) -> AlpacaOrderRequest:
        intent = decision.intent
        quantity = decision.adjusted_quantity or intent.quantity
        take_profit = {"limit_price": round(intent.take_profit, 2)} if intent.take_profit else None
        stop_loss = {"stop_price": round(intent.stop_loss, 2)} if intent.stop_loss else None
        advanced = self._build_advanced_instructions(decision)
        return AlpacaOrderRequest(
            symbol=intent.symbol,
            side="buy",
            type="market",
            time_in_force="day",
            qty=float(quantity),
            client_order_id=intent.client_order_id,
            take_profit=take_profit,
            stop_loss=stop_loss,
            advanced_instructions=advanced,
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
        gate = PreTradeRiskGate.instance()
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
            verdict = gate.evaluate(
                decision,
                open_position_count=len(self._risk_desk.open_positions),
            )
            decision = gate.apply_to_decision(decision, verdict)
            if not decision.authorized:
                logger.info(
                    "alpaca_bot.pre_trade_blocked symbol=%s reasons=%s bur=%.3f zone=%s",
                    symbol,
                    ",".join(decision.reason_codes),
                    verdict.bur,
                    verdict.buffer_zone,
                )
                continue
            if self._is_on_cooldown(symbol):
                logger.info("alpaca_bot.exec_cooldown symbol=%s", symbol)
                continue
            request = self._build_bracket_request(decision)
            from decimal import Decimal

            from backend.services.telemetry.fill_slippage_telemetry import (
                log_fill_slippage_telemetry,
            )

            telemetry = log_fill_slippage_telemetry(
                module="alpaca_equity",
                symbol=symbol,
                side="buy",
                quantity=Decimal(str(request.qty or 0)),
                limit_or_market_price=Decimal(str(decision.intent.reference_price)),
                order_payload=(request.model_dump() if hasattr(request, "model_dump") else None),
            )
            if telemetry.get("bur") is not None:
                gate.update_bur(float(telemetry["bur"]))
            response = await self._client.place_order(request)
            if response.ok:
                gate.record_order_sent()
                self._risk_desk.record_fill(decision)
                self._last_execution[symbol] = datetime.now(UTC)
            out.append(response)
        return out
