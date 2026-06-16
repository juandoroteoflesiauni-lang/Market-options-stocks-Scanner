"""Broker reconciliator for synchronizing live account state. # [PD-3][TH]"""

from __future__ import annotations

import asyncio
from decimal import Decimal
from typing import Any

from backend.config.logger_setup import get_logger
from backend.domain.builder_models import BuilderAccountState
from backend.layer_1_data.datos.alpaca_client import AlpacaClient
from backend.services.builder_state_store import BuilderStateStore

logger = get_logger(__name__)


class BrokerStateReconciliator:
    """Synchronizes live Alpaca account balance and positions to the funding state store."""

    def __init__(self, store: BuilderStateStore) -> None:
        self.store = store

    async def reconcile(
        self,
        account_id: str = "default",
        *,
        client: AlpacaClient,
    ) -> BuilderAccountState:
        """Fetch balance and positions from Alpaca and update BuilderAccountState.
        
        Applies a fallback rule to cached state if the broker connection fails or returns empty data.
        """
        cached_state = self.store.load_state(account_id)
        
        try:
            # 1. Fetch from Alpaca API
            balance = await client.fetch_account_balance()
            if not balance or "equity" not in balance:
                logger.warning(
                    "broker_reconciliator.reconcile_failed account_id=%s using_cached_fallback",
                    account_id
                )
                return cached_state

            # 2. Extract metrics
            equity = Decimal(str(balance.get("equity", 100000.0)))
            last_equity = Decimal(str(balance.get("last_equity", equity)))
            
            unrealized_pnl = Decimal("0")
            positions = await client.fetch_positions()
            for pos in positions:
                unrealized_pnl += Decimal(str(pos.get("unrealized_pl", 0.0)))

            # Realized daily PnL is total daily PnL minus unrealized daily PnL
            total_daily_pnl = equity - last_equity
            realized_daily_pnl = total_daily_pnl - unrealized_pnl

            # 3. Update HWM (High Watermark)
            current_hwm = cached_state.high_watermark_balance
            new_hwm = current_hwm
            if current_hwm is None or equity > current_hwm:
                new_hwm = equity

            # 4. Save and return updated state
            updated_state = cached_state.model_copy(
                update={
                    "current_equity": equity,
                    "realized_daily_pnl": realized_daily_pnl,
                    "unrealized_pnl": unrealized_pnl,
                    "high_watermark_balance": new_hwm,
                }
            )
            self.store.save_state(updated_state)
            logger.info(
                "broker_reconciliator.reconciled account_id=%s equity=%s unrealized_pnl=%s realized_daily_pnl=%s",
                account_id,
                equity,
                unrealized_pnl,
                realized_daily_pnl,
            )
            return updated_state

        except Exception as exc:
            logger.error(
                "broker_reconciliator.reconcile_exception account_id=%s error=%s using_cached_fallback",
                account_id,
                exc,
            )
            return cached_state
