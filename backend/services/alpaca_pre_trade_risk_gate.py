"""Pre-trade risk gate autoritativo para Alpaca (hot-path safety). # [PD-3][TH][IM]"""

from __future__ import annotations

import time
from collections import deque

from pydantic import BaseModel, ConfigDict

from backend.config.alpaca_institutional_config import AlpacaPreTradeLimits, BufferZone
from backend.config.logger_setup import get_logger
from backend.domain.alpaca_models import EquityRiskDecision

logger = get_logger(__name__)

REASON_KILL_SWITCH = "kill_switch_active"
REASON_MAX_NOTIONAL = "max_order_notional_exceeded"
REASON_MAX_POSITION_NOTIONAL = "max_position_notional_exceeded"
REASON_ORDER_RATE_LIMIT = "order_rate_limit_exceeded"
REASON_BUR_RED_ZONE = "bur_red_zone_block"
REASON_BUR_YELLOW_SIZE_DOWN = "bur_yellow_zone_size_down"


class PreTradeRiskVerdict(BaseModel):
    """Resultado del gate pre-trade antes de enviar orden al exchange."""

    model_config = ConfigDict(frozen=True)

    allowed: bool
    adjusted_quantity: int | None = None
    reason_codes: tuple[str, ...] = ()
    buffer_zone: BufferZone = "GREEN"
    bur: float = 0.0


class PreTradeRiskGate:
    """Singleton autoritativo: rechaza órdenes antes de salir al exchange."""

    _instance: PreTradeRiskGate | None = None

    def __init__(self, limits: AlpacaPreTradeLimits | None = None) -> None:
        self._limits = limits or AlpacaPreTradeLimits.from_env()
        self._order_timestamps: deque[float] = deque()
        self._bur: float = 0.0
        self._buffer_zone: BufferZone = "GREEN"

    @classmethod
    def instance(cls) -> PreTradeRiskGate:
        if cls._instance is None:
            cls._instance = cls()
        return cls._instance

    @classmethod
    def reset_instance(cls) -> None:
        cls._instance = None

    @property
    def limits(self) -> AlpacaPreTradeLimits:
        return self._limits

    @property
    def buffer_zone(self) -> BufferZone:
        return self._buffer_zone

    @property
    def bur(self) -> float:
        return self._bur

    def update_bur(self, bur: float) -> BufferZone:
        """Actualiza BUR y zona buffer desde telemetría/analytics."""
        self._bur = max(0.0, min(1.0, bur))
        if self._bur >= self._limits.bur_red_threshold:
            self._buffer_zone = "RED"
        elif self._bur >= self._limits.bur_yellow_threshold:
            self._buffer_zone = "YELLOW"
        else:
            self._buffer_zone = "GREEN"
        return self._buffer_zone

    def _prune_order_rate(self, now: float) -> None:
        cutoff = now - 60.0
        while self._order_timestamps and self._order_timestamps[0] < cutoff:
            self._order_timestamps.popleft()

    def evaluate(
        self,
        decision: EquityRiskDecision,
        *,
        open_position_count: int = 0,
    ) -> PreTradeRiskVerdict:
        """Evalúa una decisión autorizada antes de ejecución."""
        if not decision.authorized:
            return PreTradeRiskVerdict(allowed=False, reason_codes=decision.reason_codes)

        intent = decision.intent
        qty = decision.adjusted_quantity or intent.quantity
        notional = qty * intent.reference_price
        reasons: list[str] = []

        if self._limits.kill_switch:
            logger.warning("pre_trade_risk.kill_switch symbol=%s", intent.symbol)
            return PreTradeRiskVerdict(
                allowed=False,
                reason_codes=(REASON_KILL_SWITCH,),
                buffer_zone=self._buffer_zone,
                bur=self._bur,
            )

        if self._buffer_zone == "RED":
            return PreTradeRiskVerdict(
                allowed=False,
                reason_codes=(REASON_BUR_RED_ZONE,),
                buffer_zone="RED",
                bur=self._bur,
            )

        if notional > self._limits.max_order_notional_usd:
            max_qty = int(self._limits.max_order_notional_usd / intent.reference_price)
            if max_qty <= 0:
                return PreTradeRiskVerdict(
                    allowed=False,
                    reason_codes=(REASON_MAX_NOTIONAL,),
                    buffer_zone=self._buffer_zone,
                    bur=self._bur,
                )
            qty = max_qty
            reasons.append(REASON_MAX_NOTIONAL)

        if notional > self._limits.max_position_notional_usd:
            max_qty = int(self._limits.max_position_notional_usd / intent.reference_price)
            if max_qty <= 0:
                return PreTradeRiskVerdict(
                    allowed=False,
                    reason_codes=(REASON_MAX_POSITION_NOTIONAL,),
                    buffer_zone=self._buffer_zone,
                    bur=self._bur,
                )
            qty = min(qty, max_qty)
            reasons.append(REASON_MAX_POSITION_NOTIONAL)

        if open_position_count >= self._limits.max_open_positions:
            return PreTradeRiskVerdict(
                allowed=False,
                reason_codes=("max_positions_reached",),
                buffer_zone=self._buffer_zone,
                bur=self._bur,
            )

        now = time.monotonic()
        self._prune_order_rate(now)
        if len(self._order_timestamps) >= self._limits.order_rate_limit_per_minute:
            return PreTradeRiskVerdict(
                allowed=False,
                reason_codes=(REASON_ORDER_RATE_LIMIT,),
                buffer_zone=self._buffer_zone,
                bur=self._bur,
            )

        if self._buffer_zone == "YELLOW" and qty > 1:
            qty = max(1, qty // 2)
            reasons.append(REASON_BUR_YELLOW_SIZE_DOWN)

        return PreTradeRiskVerdict(
            allowed=True,
            adjusted_quantity=qty,
            reason_codes=tuple(dict.fromkeys(reasons)),
            buffer_zone=self._buffer_zone,
            bur=self._bur,
        )

    def record_order_sent(self) -> None:
        """Registra timestamp de orden enviada para rate limiting."""
        self._order_timestamps.append(time.monotonic())

    def apply_to_decision(
        self,
        decision: EquityRiskDecision,
        verdict: PreTradeRiskVerdict,
    ) -> EquityRiskDecision:
        """Fusiona veredicto pre-trade en EquityRiskDecision."""
        if not verdict.allowed:
            return decision.model_copy(
                update={
                    "authorized": False,
                    "reason_codes": verdict.reason_codes,
                }
            )
        if verdict.adjusted_quantity is not None and verdict.adjusted_quantity != (
            decision.adjusted_quantity or decision.intent.quantity
        ):
            return decision.model_copy(update={"adjusted_quantity": verdict.adjusted_quantity})
        return decision


__all__ = [
    "REASON_BUR_RED_ZONE",
    "REASON_BUR_YELLOW_SIZE_DOWN",
    "REASON_KILL_SWITCH",
    "REASON_MAX_NOTIONAL",
    "REASON_ORDER_RATE_LIMIT",
    "PreTradeRiskGate",
    "PreTradeRiskVerdict",
]
