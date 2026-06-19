"""Adaptador Alpaca para ``OptionsExecutionPayload`` (Fase 6). # [PD-3][TH]"""

from __future__ import annotations

import os
from datetime import UTC, datetime
from decimal import Decimal

from backend.config.logger_setup import get_logger
from backend.config.options_defined_risk import is_defined_risk_structure
from backend.layer_1_data.datos.alpaca_client import (
    AlpacaClient,
    AlpacaOptionsLegRequest,
    AlpacaOptionsOrderRequest,
    normalize_alpaca_occ_symbol,
)
from backend.models.options_strategy import (
    OptionsExecutionPayload,
    OptionsExecutionResult,
    StrategyDecision,
)

logger = get_logger(__name__)

_OPTION_CONTRACT_MULTIPLIER = Decimal("100")


def _options_contract_qty() -> int:
    try:
        return max(1, min(int(os.getenv("OPTIONS_CONTRACT_QTY", "2")), 10))
    except ValueError:
        return 2


def _limit_price_from_payload(payload: OptionsExecutionPayload) -> float | None:
    if payload.order_type != "limit":
        return None
    if payload.limit_price_per_contract is not None:
        return float(payload.limit_price_per_contract)
    per_contract = payload.max_premium_usd / _OPTION_CONTRACT_MULTIPLIER
    return float(per_contract.quantize(Decimal("0.01")))


def build_alpaca_options_order(
    payload: OptionsExecutionPayload,
) -> AlpacaOptionsOrderRequest:
    """Traduce ``OptionsExecutionPayload`` a orden Alpaca OCC/mleg."""
    if not payload.legs:
        raise ValueError("execution payload has no legs")
    legs = tuple(
        AlpacaOptionsLegRequest(
            symbol=normalize_alpaca_occ_symbol(leg.contract_symbol),
            side=leg.side,
            ratio_qty=leg.ratio,
        )
        for leg in payload.legs
    )
    return AlpacaOptionsOrderRequest(
        underlying=payload.symbol,
        legs=legs,
        order_type=payload.order_type,
        time_in_force=payload.time_in_force,
        qty=_options_contract_qty(),
        limit_price=_limit_price_from_payload(payload),
        client_order_id=payload.client_order_id,
    )


def _reject_uncovered_legs(payload: OptionsExecutionPayload) -> str | None:
    """Bloquea órdenes de una sola pata vendida (causa 403 en Alpaca paper)."""
    if len(payload.legs) == 1 and payload.legs[0].side == "sell":
        return "uncovered_short_option_leg"
    if not is_defined_risk_structure(payload.recommended_structure):
        return "structure_not_defined_risk"
    return None


class AlpacaOptionsExecutor:
    """Envía órdenes de opciones a Alpaca paper/live con dry-run seguro."""

    @classmethod
    async def execute(
        cls,
        payload: OptionsExecutionPayload,
        client: AlpacaClient,
    ) -> OptionsExecutionResult:
        """Ejecuta o simula la orden según ``payload.dry_run`` y el cliente."""
        if payload.decision != StrategyDecision.EXECUTE:
            return OptionsExecutionResult(
                client_order_id=payload.client_order_id,
                underlying=payload.symbol,
                structure=payload.recommended_structure,
                ok=False,
                dry_run=client.dry_run or payload.dry_run,
                submitted_at=datetime.now(tz=UTC),
                error="decision_not_execute",
                reason_codes=("execution_skipped_not_execute",),
            )

        if not payload.legs:
            return OptionsExecutionResult(
                client_order_id=payload.client_order_id,
                underlying=payload.symbol,
                structure=payload.recommended_structure,
                ok=False,
                dry_run=client.dry_run or payload.dry_run,
                submitted_at=datetime.now(tz=UTC),
                error="missing_legs",
                reason_codes=("execution_skipped_missing_legs",),
            )

        uncovered = _reject_uncovered_legs(payload)
        if uncovered:
            logger.warning(
                "alpaca_options_executor.blocked underlying=%s structure=%s reason=%s",
                payload.symbol,
                payload.recommended_structure.value,
                uncovered,
            )
            return OptionsExecutionResult(
                client_order_id=payload.client_order_id,
                underlying=payload.symbol,
                structure=payload.recommended_structure,
                ok=False,
                dry_run=client.dry_run or payload.dry_run,
                submitted_at=datetime.now(tz=UTC),
                error=uncovered,
                reason_codes=(uncovered,),
            )

        order = build_alpaca_options_order(payload)
        effective_dry_run = payload.dry_run or client.dry_run
        if effective_dry_run:
            logger.info(
                "alpaca_options_executor.dry_run underlying=%s structure=%s legs=%s",
                payload.symbol,
                payload.recommended_structure.value,
                len(order.legs),
            )
            return OptionsExecutionResult(
                client_order_id=order.client_order_id or payload.client_order_id,
                underlying=payload.symbol,
                structure=payload.recommended_structure,
                ok=True,
                dry_run=True,
                submitted_at=datetime.now(tz=UTC),
                limit_price=order.limit_price,
                reason_codes=("execution_dry_run",),
                raw={
                    "intercepted": True,
                    "order_class": "mleg" if len(order.legs) > 1 else "simple",
                },
            )

        response = await client.place_options_order(order)
        return OptionsExecutionResult(
            client_order_id=response.client_order_id or payload.client_order_id,
            underlying=payload.symbol,
            structure=payload.recommended_structure,
            ok=response.ok,
            dry_run=response.dry_run,
            submitted_at=datetime.now(tz=UTC),
            venue_order_id=response.venue_order_id,
            limit_price=order.limit_price,
            error=response.error,
            reason_codes=("execution_submitted",) if response.ok else ("execution_failed",),
            raw=response.raw,
        )

    @classmethod
    async def cancel(
        cls,
        venue_order_id: str,
        client: AlpacaClient,
    ) -> bool:
        """Cancela una orden Alpaca por ``venue_order_id``."""
        return await client.cancel_order(venue_order_id)


__all__ = ["AlpacaOptionsExecutor", "build_alpaca_options_order"]
