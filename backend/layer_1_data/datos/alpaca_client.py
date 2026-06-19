from __future__ import annotations

import os
import uuid
from dataclasses import dataclass, field
from typing import Any, Literal

import httpx

from backend.config.logger_setup import get_logger

logger = get_logger(__name__)


def normalize_alpaca_occ_symbol(symbol: str) -> str:
    """Quita prefijo Polygon ``O:`` para órdenes Alpaca OCC."""
    s = symbol.strip().upper()
    if s.startswith("O:"):
        return s[2:]
    return s


@dataclass(frozen=True)
class AlpacaOptionsLegRequest:
    """Una pata OCC para órdenes de opciones Alpaca."""

    symbol: str
    side: Literal["buy", "sell"]
    ratio_qty: int = 1


@dataclass(frozen=True)
class AlpacaOptionsOrderRequest:
    """Orden de opciones simple o multi-leg (``mleg``)."""

    underlying: str
    legs: tuple[AlpacaOptionsLegRequest, ...]
    order_type: Literal["market", "limit"] = "limit"
    time_in_force: Literal["day", "gtc", "ioc", "fok"] = "day"
    qty: int = 1
    limit_price: float | None = None
    client_order_id: str | None = None


@dataclass(frozen=True)
class AlpacaOrderRequest:
    symbol: str
    side: Literal["buy", "sell"]
    type: Literal["market", "limit", "stop", "stop_limit"] = "market"
    time_in_force: Literal["day", "gtc", "opg", "ioc", "fok"] = "day"
    qty: float | None = None
    notional: float | None = None
    limit_price: float | None = None
    stop_price: float | None = None
    client_order_id: str | None = None
    take_profit: dict[str, float] | None = None
    stop_loss: dict[str, float] | None = None
    advanced_instructions: dict[str, Any] | None = None


@dataclass(frozen=True)
class AlpacaOrderResponse:
    ok: bool
    dry_run: bool
    symbol: str
    side: str
    order_type: str
    requested_qty: float | None
    price: float | None
    venue_order_id: str | None
    client_order_id: str | None
    raw: dict[str, Any] = field(default_factory=dict)
    error: str | None = None


class AlpacaClient:
    """Async REST client for Alpaca with built-in dry-run safety net."""

    def __init__(
        self,
        api_key: str | None = None,
        secret_key: str | None = None,
        *,
        base_url: str = "https://paper-api.alpaca.markets",
        dry_run: bool = True,
    ) -> None:
        self._api_key = api_key or os.getenv("ALPACA_API_KEY") or ""
        self._secret_key = secret_key or os.getenv("ALPACA_SECRET_KEY") or ""
        self._base_url = os.getenv("ALPACA_TRADING_BASE_URL", base_url).rstrip("/")
        env_dry_run = os.getenv("ALPACA_DRY_RUN")
        if env_dry_run is not None:
            self._dry_run = env_dry_run.strip().lower() not in {
                "0",
                "false",
                "no",
                "live",
            }
        else:
            self._dry_run = bool(dry_run)

        self._client: httpx.AsyncClient | None = None

    @property
    def dry_run(self) -> bool:
        return self._dry_run

    async def __aenter__(self) -> AlpacaClient:
        await self._ensure_client()
        return self

    async def __aexit__(self, *_exc: object) -> None:
        await self.aclose()

    async def _ensure_client(self) -> httpx.AsyncClient:
        if self._client is None or self._client.is_closed:
            self._client = httpx.AsyncClient(
                base_url=self._base_url,
                timeout=httpx.Timeout(30.0),
                headers={
                    "APCA-API-KEY-ID": self._api_key,
                    "APCA-API-SECRET-KEY": self._secret_key,
                },
            )
        return self._client

    async def aclose(self) -> None:
        if self._client is not None and not self._client.is_closed:
            await self._client.aclose()
        self._client = None

    @staticmethod
    def _build_order_payload(order: AlpacaOrderRequest, client_order_id: str) -> dict[str, Any]:
        """Construye el payload de /v2/orders. # [TH]

        Cuando hay TP y SL válidos, emite un bracket order (``order_class``
        obligatorio en Alpaca Trading API v2). TP requiere ``limit_price`` y
        SL requiere ``stop_price``.
        """
        payload: dict[str, Any] = {
            "symbol": order.symbol,
            "side": order.side,
            "type": order.type,
            "time_in_force": order.time_in_force,
            "client_order_id": client_order_id,
        }
        if order.qty is not None:
            payload["qty"] = str(order.qty)
        elif order.notional is not None:
            payload["notional"] = str(order.notional)
        if order.limit_price is not None:
            payload["limit_price"] = str(round(order.limit_price, 4))
        if order.stop_price is not None:
            payload["stop_price"] = str(round(order.stop_price, 4))
        has_tp = bool(order.take_profit and order.take_profit.get("limit_price"))
        has_sl = bool(order.stop_loss and order.stop_loss.get("stop_price"))
        if has_tp and has_sl:
            payload["order_class"] = "bracket"
            payload["take_profit"] = order.take_profit
            payload["stop_loss"] = order.stop_loss
        if order.advanced_instructions:
            payload["advanced_instructions"] = order.advanced_instructions
        return payload

    @staticmethod
    def _build_options_order_payload(
        order: AlpacaOptionsOrderRequest,
        client_order_id: str,
    ) -> dict[str, Any]:
        """Construye payload OCC simple o ``order_class=mleg`` para spreads."""
        if not order.legs:
            raise ValueError("options order requires at least one leg")
        if len(order.legs) == 1:
            leg = order.legs[0]
            payload: dict[str, Any] = {
                "symbol": normalize_alpaca_occ_symbol(leg.symbol),
                "side": leg.side,
                "type": order.order_type,
                "time_in_force": order.time_in_force,
                "qty": str(order.qty),
                "client_order_id": client_order_id,
            }
            if order.limit_price is not None and order.order_type == "limit":
                payload["limit_price"] = str(round(order.limit_price, 4))
            return payload
        payload = {
            "order_class": "mleg",
            "type": order.order_type,
            "time_in_force": order.time_in_force,
            "qty": str(order.qty),
            "client_order_id": client_order_id,
            "legs": [
                {
                    "symbol": normalize_alpaca_occ_symbol(leg.symbol),
                    "side": leg.side,
                    "ratio_qty": str(leg.ratio_qty),
                }
                for leg in order.legs
            ],
        }
        if order.limit_price is not None and order.order_type == "limit":
            payload["limit_price"] = str(round(order.limit_price, 4))
        return payload

    async def place_options_order(
        self,
        order: AlpacaOptionsOrderRequest,
    ) -> AlpacaOrderResponse:
        """Envía orden de opciones simple o multi-leg a ``/v2/orders``."""
        client_order_id = order.client_order_id or f"opt-{uuid.uuid4().hex[:16]}"
        primary = order.legs[0].symbol if order.legs else order.underlying
        if self._dry_run:
            logger.info(
                "alpaca_client.place_options_order DRY_RUN underlying=%s legs=%s type=%s",
                order.underlying,
                len(order.legs),
                order.order_type,
            )
            return AlpacaOrderResponse(
                ok=True,
                dry_run=True,
                symbol=primary,
                side=order.legs[0].side if order.legs else "buy",
                order_type=order.order_type,
                requested_qty=float(order.qty),
                price=order.limit_price,
                venue_order_id=None,
                client_order_id=client_order_id,
                raw={"intercepted": True, "reason": "dry_run", "legs": len(order.legs)},
            )

        payload = self._build_options_order_payload(order, client_order_id)
        try:
            client = await self._ensure_client()
            response = await client.post("/v2/orders", json=payload)
            response.raise_for_status()
            data = response.json()
        except httpx.HTTPStatusError as exc:
            try:
                err_data = exc.response.json()
            except Exception:
                err_data = exc.response.text
            logger.error(
                "alpaca_client.place_options_order live_error underlying=%s status=%s body=%s",
                order.underlying,
                exc.response.status_code,
                err_data,
            )
            return AlpacaOrderResponse(
                ok=False,
                dry_run=False,
                symbol=primary,
                side=order.legs[0].side if order.legs else "buy",
                order_type=order.order_type,
                requested_qty=float(order.qty),
                price=order.limit_price,
                venue_order_id=None,
                client_order_id=client_order_id,
                raw={},
                error=str(err_data),
            )
        except Exception as exc:
            logger.error(
                "alpaca_client.place_options_order error underlying=%s exc=%s",
                order.underlying,
                exc,
            )
            return AlpacaOrderResponse(
                ok=False,
                dry_run=False,
                symbol=primary,
                side=order.legs[0].side if order.legs else "buy",
                order_type=order.order_type,
                requested_qty=float(order.qty),
                price=order.limit_price,
                venue_order_id=None,
                client_order_id=client_order_id,
                raw={},
                error=str(exc),
            )

        return AlpacaOrderResponse(
            ok=True,
            dry_run=False,
            symbol=primary,
            side=order.legs[0].side if order.legs else "buy",
            order_type=order.order_type,
            requested_qty=float(order.qty),
            price=order.limit_price,
            venue_order_id=data.get("id"),
            client_order_id=data.get("client_order_id"),
            raw=data,
        )

    async def get_clock(self) -> dict[str, Any]:
        """Devuelve el reloj de mercado de Alpaca (``/v2/clock``)."""
        if self._dry_run:
            return {"is_open": True, "dry_run": True}
        try:
            client = await self._ensure_client()
            r = await client.get("/v2/clock")
            r.raise_for_status()
            clock: dict[str, Any] = r.json()
            return clock
        except Exception as exc:
            logger.error("alpaca_client.get_clock error %s", exc)
            return {}

    async def place_order(self, order: AlpacaOrderRequest) -> AlpacaOrderResponse:
        client_order_id = order.client_order_id or f"qa-{uuid.uuid4().hex[:16]}"
        if self._dry_run:
            logger.info(
                "alpaca_client.place_order DRY_RUN symbol=%s side=%s type=%s qty=%s",
                order.symbol,
                order.side,
                order.type,
                order.qty,
            )
            return AlpacaOrderResponse(
                ok=True,
                dry_run=True,
                symbol=order.symbol,
                side=order.side,
                order_type=order.type,
                requested_qty=order.qty,
                price=order.limit_price or order.stop_price,
                venue_order_id=None,
                client_order_id=client_order_id,
                raw={"intercepted": True, "reason": "dry_run"},
            )

        payload = self._build_order_payload(order, client_order_id)

        try:
            client = await self._ensure_client()
            r = await client.post("/v2/orders", json=payload)
            r.raise_for_status()
            data = r.json()
        except httpx.HTTPStatusError as exc:
            try:
                err_data = exc.response.json()
            except Exception:
                err_data = exc.response.text
            logger.error(
                "alpaca_client.place_order live_error symbol=%s status=%s body=%s",
                order.symbol,
                exc.response.status_code,
                err_data,
            )
            return AlpacaOrderResponse(
                ok=False,
                dry_run=False,
                symbol=order.symbol,
                side=order.side,
                order_type=order.type,
                requested_qty=order.qty,
                price=order.limit_price,
                venue_order_id=None,
                client_order_id=client_order_id,
                raw={},
                error=str(err_data),
            )
        except Exception as exc:
            logger.error("alpaca_client.place_order error symbol=%s exc=%s", order.symbol, exc)
            return AlpacaOrderResponse(
                ok=False,
                dry_run=False,
                symbol=order.symbol,
                side=order.side,
                order_type=order.type,
                requested_qty=order.qty,
                price=order.limit_price,
                venue_order_id=None,
                client_order_id=client_order_id,
                raw={},
                error=str(exc),
            )

        return AlpacaOrderResponse(
            ok=True,
            dry_run=False,
            symbol=order.symbol,
            side=order.side,
            order_type=order.type,
            requested_qty=order.qty,
            price=order.limit_price,
            venue_order_id=data.get("id"),
            client_order_id=data.get("client_order_id"),
            raw=data,
        )

    async def cancel_order(self, venue_order_id: str) -> bool:
        """Cancela una orden por ID de venue."""
        if self._dry_run:
            logger.info("alpaca_client.cancel_order DRY_RUN order_id=%s", venue_order_id)
            return True
        try:
            client = await self._ensure_client()
            response = await client.delete(f"/v2/orders/{venue_order_id}")
            response.raise_for_status()
            return True
        except Exception as exc:
            logger.error("alpaca_client.cancel_order error id=%s exc=%s", venue_order_id, exc)
            return False

    async def fetch_account_balance(self) -> dict[str, Any]:
        if self._dry_run:
            return {
                "dry_run": True,
                "equity": "100000.0",
                "buying_power": "400000.0",
                "pattern_day_trader": False,
            }
        try:
            client = await self._ensure_client()
            r = await client.get("/v2/account")
            r.raise_for_status()
            balance: dict[str, Any] = r.json()
            return balance
        except Exception as exc:
            logger.error("alpaca_client.fetch_account_balance error %s", exc)
            return {}

    async def fetch_positions(self) -> list[dict[str, Any]]:
        if self._dry_run:
            return []
        try:
            client = await self._ensure_client()
            r = await client.get("/v2/positions")
            r.raise_for_status()
            positions: list[dict[str, Any]] = r.json()
            return positions
        except Exception as exc:
            logger.error("alpaca_client.fetch_positions error %s", exc)
            return []

    async def close_position(self, symbol: str, *, cancel_orders: bool = True) -> dict[str, Any]:
        """Cierra una posición por símbolo (equity o OCC)."""
        sym = symbol.strip()
        if self._dry_run:
            logger.info("alpaca_client.close_position DRY_RUN symbol=%s", sym)
            return {"symbol": sym, "dry_run": True, "ok": True}
        try:
            client = await self._ensure_client()
            params = {"cancel_orders": "true"} if cancel_orders else {}
            response = await client.delete(f"/v2/positions/{sym}", params=params)
            response.raise_for_status()
            data = response.json()
            logger.info("alpaca_client.close_position ok symbol=%s", sym)
            return {"symbol": sym, "ok": True, "raw": data}
        except Exception as exc:
            logger.error("alpaca_client.close_position error symbol=%s exc=%s", sym, exc)
            return {"symbol": sym, "ok": False, "error": str(exc)}

    async def close_all_positions(self, *, cancel_orders: bool = True) -> list[dict[str, Any]]:
        """Cierra todas las posiciones abiertas en la cuenta."""
        if self._dry_run:
            positions = await self.fetch_positions()
            logger.info("alpaca_client.close_all_positions DRY_RUN count=%d", len(positions))
            return [{"symbol": p.get("symbol"), "dry_run": True, "ok": True} for p in positions]
        try:
            client = await self._ensure_client()
            params = {"cancel_orders": "true"} if cancel_orders else {}
            response = await client.delete("/v2/positions", params=params)
            response.raise_for_status()
            data = response.json()
            if isinstance(data, list):
                return [{"symbol": item.get("symbol"), "ok": True, "raw": item} for item in data]
            return [{"ok": True, "raw": data}]
        except Exception as exc:
            logger.error("alpaca_client.close_all_positions error %s", exc)
            return [{"ok": False, "error": str(exc)}]
