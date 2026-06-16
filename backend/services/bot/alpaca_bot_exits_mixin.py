"""Mixin de salidas dinámicas del bot Alpaca (LONG-only). # [IM][TH]

Las posiciones llevan bracket TP/SL en el servidor de Alpaca. Este mixin
añade salidas discrecionales: cierre por ruptura de soporte (leído del
``technical_payload`` nativo) y toma parcial paramétrica por ganancia.
"""

from __future__ import annotations

import math
from typing import Any

from backend.config.logger_setup import get_logger
from backend.layer_1_data.datos.alpaca_client import AlpacaClient, AlpacaOrderRequest
from backend.services.bot.alpaca_bot_types import (
    PARAMETRIC_HALF_EXIT_RATIO,
    PARAMETRIC_TP_TRIGGER_PCT,
    _ParametricExitState,
)

logger = get_logger(__name__)


def _float_or_none(value: object) -> float | None:
    try:
        return float(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return None


class AlpacaBotExitsMixin:
    """Evalúa y ejecuta salidas sobre posiciones LONG abiertas."""

    _client: AlpacaClient
    _parametric_exit_state: dict[str, _ParametricExitState]

    @staticmethod
    def _long_pnl_pct(entry_price: float, spot_price: float) -> float | None:
        if entry_price <= 0 or spot_price <= 0:
            return None
        return ((spot_price - entry_price) / entry_price) * 100.0

    def _parametric_state_for(self, symbol: str, position_size: float) -> _ParametricExitState:
        state = self._parametric_exit_state.get(symbol)
        if state is None or state.initial_size <= 0:
            state = _ParametricExitState(initial_size=abs(position_size))
            self._parametric_exit_state[symbol] = state
        return state

    async def _place_reduce_market(self, symbol: str, quantity: float, reason: str) -> Any | None:
        qty = math.floor(quantity)
        if qty <= 0:
            return None
        logger.warning("alpaca_bot.exit_reduce symbol=%s qty=%d reason=%s", symbol, qty, reason)
        return await self._client.place_order(
            AlpacaOrderRequest(
                symbol=symbol, side="sell", type="market", time_in_force="day", qty=float(qty)
            )
        )

    async def evaluate_dynamic_exits(self) -> list[Any]:
        try:
            open_positions = await self._client.fetch_positions()
        except Exception as exc:
            logger.error("alpaca_bot.exits_account_failed error=%s", exc)
            return []
        executions: list[Any] = []
        for pos in open_positions:
            resp = await self._evaluate_single_exit(pos)
            if resp is not None:
                executions.append(resp)
        return executions

    async def _evaluate_single_exit(self, pos: dict[str, Any]) -> Any | None:
        symbol = str(pos.get("symbol", ""))
        position_size = abs(_float_or_none(pos.get("qty")) or 0.0)
        if not symbol or position_size <= 0:
            return None
        entry_price = _float_or_none(pos.get("avg_entry_price")) or 0.0
        spot = _float_or_none(pos.get("current_price")) or 0.0
        pnl_pct = self._long_pnl_pct(entry_price, spot)
        if pnl_pct is None or pnl_pct < PARAMETRIC_TP_TRIGGER_PCT:
            return None
        state = self._parametric_state_for(symbol, position_size)
        if state.half_tp_done:
            return None
        state.half_tp_done = True
        half_qty = position_size * PARAMETRIC_HALF_EXIT_RATIO

        from backend.audit.process_recorder import record_trade_result
        pnl_usd = half_qty * spot - half_qty * entry_price
        try:
            await record_trade_result(
                module="alpaca",
                symbol=symbol,
                pnl_pct=pnl_pct,
                pnl_usd=pnl_usd,
                exit_reason="parametric_half_tp",
            )
        except Exception as exc:
            logger.error("alpaca_bot.audit_trade_result_failed symbol=%s error=%s", symbol, exc)

        return await self._place_reduce_market(symbol, half_qty, "parametric_half_tp")
