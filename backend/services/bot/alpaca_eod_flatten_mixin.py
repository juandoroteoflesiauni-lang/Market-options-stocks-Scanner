"""Mixin EOD: cierra equity + opciones antes del cierre de mercado. # [PD-3][TH]"""

from __future__ import annotations

from typing import Any

from backend.config.alpaca_eod_config import (
    alpaca_eod_flatten_enabled,
    is_eod_flatten_window,
    trading_date_et_key,
)
from backend.config.logger_setup import get_logger
from backend.layer_1_data.datos.alpaca_client import AlpacaClient

logger = get_logger(__name__)


class AlpacaEodFlattenMixin:
    """Cierra el libro una vez por día en la ventana EOD."""

    _client: AlpacaClient
    _eod_flatten_date: str | None = None

    async def maybe_eod_flatten(self) -> dict[str, Any] | None:
        """Ejecuta flatten si estamos en ventana EOD y aún no se hizo hoy."""
        if not alpaca_eod_flatten_enabled() or not is_eod_flatten_window():
            return None

        today = trading_date_et_key()
        if self._eod_flatten_date == today:
            return {
                "flattened": False,
                "reason": "already_flattened_today",
                "trading_date": today,
            }

        result = await self._flatten_entire_book()
        self._eod_flatten_date = today
        logger.warning(
            "alpaca_bot.eod_flatten_complete trading_date=%s closed=%d dry_run=%s",
            today,
            result.get("closed_count", 0),
            self._client.dry_run,
        )

        # Trigger ML retrain in background
        import asyncio

        self._ml_retrain_task = asyncio.create_task(self._retrain_ml_model())

        return result

    async def _retrain_ml_model(self) -> None:
        """Re-entrena el modelo de Machine Learning en background sin bloquear el bot."""
        import asyncio
        import sys

        from backend.scripts.train_ml_model import main as train_main

        def _run_train() -> None:
            old_argv = sys.argv
            try:
                sys.argv = ["train_ml_model.py"]
                train_main()
            finally:
                sys.argv = old_argv

        try:
            logger.info("alpaca_bot.ml_retrain_starting")
            await asyncio.to_thread(_run_train)
            logger.info("alpaca_bot.ml_retrain_success")
        except SystemExit as exc:
            logger.error("alpaca_bot.ml_retrain_failed exit_code=%s", exc.code)
        except Exception as exc:
            logger.error("alpaca_bot.ml_retrain_failed error=%s", exc)

    async def _flatten_entire_book(self) -> dict[str, Any]:
        """Cierra todas las posiciones abiertas (acciones + opciones OCC)."""
        positions = await self._client.fetch_positions()

        from backend.audit.process_recorder import record_trade_result

        for pos in positions:
            try:
                symbol = str(pos.get("symbol", ""))
                entry_price = float(pos.get("avg_entry_price") or 0.0)
                spot = float(pos.get("current_price") or 0.0)
                qty = abs(float(pos.get("qty") or 0.0))
                if entry_price > 0 and spot > 0 and qty > 0:
                    pnl_pct = ((spot - entry_price) / entry_price) * 100.0
                    pnl_usd = (spot - entry_price) * qty
                    await record_trade_result(
                        module="alpaca",
                        symbol=symbol,
                        pnl_pct=pnl_pct,
                        pnl_usd=pnl_usd,
                        exit_reason="eod_flatten",
                    )
            except Exception:
                pass

        if self._client.dry_run:
            return {
                "flattened": True,
                "dry_run": True,
                "closed_count": len(positions),
                "positions": [str(p.get("symbol", "")) for p in positions],
                "method": "dry_run_simulated",
            }

        closed = await self._client.close_all_positions(cancel_orders=True)
        return {
            "flattened": True,
            "dry_run": False,
            "closed_count": len(closed),
            "positions": closed,
            "method": "close_all_positions",
        }


__all__ = ["AlpacaEodFlattenMixin"]
