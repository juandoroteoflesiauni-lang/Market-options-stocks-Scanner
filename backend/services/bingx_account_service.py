from __future__ import annotations
from typing import Protocol, Any
"""BingX account-state aggregation for the bot service and router."""


import asyncio
from collections.abc import Awaitable
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime

from backend.config.logger_setup import get_logger
from backend.layer_1_data.datos.bingx_client import BingXClient, is_perp_symbol

logger = get_logger(__name__)


class FMPQuoteClient(Protocol):
    def get_quote(self, symbol: str) -> Awaitable[object | None]: ...


@dataclass(frozen=True)
class BingXOpenOrder:
    symbol: str
    venue_order_id: str | None
    side: str | None
    price: float | None
    quantity: float | None
    status: str | None
    raw: dict[str, Any]

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class BingXPositionSnapshot:
    symbol: str
    side: str
    size: float
    entry_price: float
    mark_price: float
    unrealized_pnl: float
    leverage: int
    liquidation_price: float | None
    margin_type: str
    fmp_quote: dict[str, Any] | None
    funding_rate: float | None
    conviction_score: float | None = None
    exit_reasons: list[str] = field(default_factory=list)
    current_price: float | None = None
    pnl_real_apalancado: float | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class BingXAccountState:
    total_equity_usdt: float
    available_margin_usdt: float
    used_margin_usdt: float
    unrealized_pnl_usdt: float
    realized_pnl_today_usdt: float
    open_positions: list[BingXPositionSnapshot]
    position_count: int
    open_orders: list[BingXOpenOrder]
    margin_ratio: float | None
    largest_position_pct: float | None
    dry_run: bool
    captured_at: str

    def to_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["open_positions"] = [pos.to_dict() for pos in self.open_positions]
        payload["open_orders"] = [order.to_dict() for order in self.open_orders]
        return payload


class BingXAccountService:
    def __init__(
        self,
        *,
        client: BingXClient,
        fmp_client: FMPQuoteClient | None = None,
    ) -> None:
        self._client = client
        self._fmp = fmp_client

    async def _fetch_daily_realized_pnl(self, raw_positions: list[dict[str, Any]]) -> float:
        """Fetch perpetual trade fills for today across configured symbols and active positions."""
        if getattr(self._client, "dry_run", True):
            return 0.0

        import time

        start_of_day_ms = int((time.time() // 86400) * 86400 * 1000)

        try:
            from backend.config.settings import load_settings

            settings = load_settings()
            allowlist_str = settings.bingx_bot_live_symbol_allowlist or ""
            symbols = [s.strip() for s in allowlist_str.split(",") if s.strip()]
            if not symbols:
                priority_str = getattr(settings, "bingx_bot_priority_stocks", "AAPL-USDT,MSFT-USDT")
                symbols = [s.strip() for s in priority_str.split(",") if s.strip()]
        except Exception:
            symbols = ["AAPL-USDT", "MSFT-USDT"]

        current_symbols = set()
        for pos in raw_positions:
            if isinstance(pos, dict):
                sym = pos.get("symbol")
                if sym:
                    current_symbols.add(sym)

        query_symbols = set(symbols) | current_symbols
        total_pnl = 0.0
        tasks = []
        for symbol in query_symbols:
            tasks.append(
                self._client.fetch_trade_history_perp(
                    symbol, limit=100, start_time_ms=start_of_day_ms
                )
            )

        results = await asyncio.gather(*tasks, return_exceptions=True)
        import contextlib

        for _symbol, result in zip(query_symbols, results, strict=False):
            if isinstance(result, list):
                for fill in result:
                    pnl_val = fill.get("realizedProfit") or fill.get("realizedPnl") or 0.0
                    with contextlib.suppress(ValueError, TypeError):
                        total_pnl += float(pnl_val)
        logger.debug("bingx_account.reconciled_daily_pnl total_pnl=%.4f", total_pnl)
        return total_pnl

    async def get_account_state(self) -> BingXAccountState:
        balance, spot_balance, raw_positions, perp_orders, spot_orders = await asyncio.gather(
            self._client.fetch_perp_balance(),
            self._client.fetch_account_balance(),
            self._client.fetch_perp_positions(),
            self._client.fetch_open_orders_perp(),
            self._client.fetch_open_orders_spot(),
        )
        balance_row = _balance_row(balance)
        equity = _first_float(balance_row, "equity", "balance", "totalEquity", "accountEquity")
        if equity <= 0:
            equity = _spot_usdt_equity(spot_balance)
        available = _first_float(balance_row, "availableMargin", "availableBalance", "free")
        used = _first_float(balance_row, "usedMargin", "marginUsed")
        unrealized = _first_float(balance_row, "unrealizedProfit", "unrealizedPnl")
        positions = [await self._position_snapshot(row) for row in raw_positions]
        orders = [_order_snapshot(row) for row in [*perp_orders, *spot_orders]]
        margin_ratio = round(used / equity, 6) if equity > 0 else None
        notionals = [abs(pos.size) * pos.mark_price for pos in positions if pos.mark_price > 0]
        largest_position_pct = (
            round(max(notionals) / equity, 6) if equity > 0 and notionals else None
        )
        realized_pnl_today = await self._fetch_daily_realized_pnl(raw_positions)
        return BingXAccountState(
            total_equity_usdt=equity,
            available_margin_usdt=available,
            used_margin_usdt=used,
            unrealized_pnl_usdt=unrealized,
            realized_pnl_today_usdt=realized_pnl_today,
            open_positions=positions,
            position_count=len(positions),
            open_orders=orders,
            margin_ratio=margin_ratio,
            largest_position_pct=largest_position_pct,
            dry_run=bool(getattr(self._client, "dry_run", True)),
            captured_at=_utc_iso_now(),
        )

    async def get_position_enriched(self, symbol: str) -> BingXPositionSnapshot | None:
        positions = await self._client.fetch_perp_positions(symbol)
        for row in positions:
            if str(row.get("symbol") or "").upper() == symbol.upper():
                return await self._position_snapshot(row)
        return None

    async def _position_snapshot(self, row: dict[str, Any]) -> BingXPositionSnapshot:
        symbol = str(row.get("symbol") or "")
        side = str(row.get("positionSide") or row.get("side") or "BOTH").upper()
        size = _first_float(row, "positionAmt", "positionAmount", "quantity", "size")
        entry = _first_float(row, "entryPrice", "avgPrice")
        mark = _first_float(row, "markPrice", "lastPrice")
        current = _nullable_float(row, "currentPrice", "current_price") or mark
        unrealized = _first_float(row, "unrealizedProfit", "unrealizedPnl")
        leverage = int(_first_float(row, "leverage") or 1)
        liquidation = _nullable_float(row, "liquidationPrice", "liqPrice")
        margin_type = str(row.get("marginType") or row.get("marginMode") or "UNKNOWN").upper()
        fmp_quote = await self._fmp_quote(symbol)
        funding = await self._funding_rate(symbol)

        # Compute pnl_real_apalancado using current_price (or mark_price) and entry_price
        pnl_real_apalancado = None
        if entry > 0 and current > 0:
            unleveraged_pnl = (
                ((current - entry) / entry) * 100.0
                if side == "LONG"
                else ((entry - current) / entry) * 100.0
            )
            pnl_real_apalancado = unleveraged_pnl * leverage

        return BingXPositionSnapshot(
            symbol=symbol,
            side=side,
            size=size,
            entry_price=entry,
            mark_price=mark,
            unrealized_pnl=unrealized,
            leverage=leverage,
            liquidation_price=liquidation,
            margin_type=margin_type,
            fmp_quote=fmp_quote,
            funding_rate=funding,
            current_price=current,
            pnl_real_apalancado=pnl_real_apalancado,
        )

    async def _fmp_quote(self, symbol: str) -> dict[str, Any] | None:
        if self._fmp is None or not is_perp_symbol(symbol):
            return None
        root = symbol.split("-")[0].split("/")[0].upper()
        try:
            quote = await self._fmp.get_quote(root)
        except Exception as exc:
            logger.debug("bingx_account.fmp_quote_failed symbol=%s error=%s", symbol, exc)
            return None
        return _object_to_dict(quote)

    async def _funding_rate(self, symbol: str) -> float | None:
        try:
            payload = await self._client.fetch_funding_rate(symbol)
        except Exception as exc:
            logger.debug("bingx_account.funding_failed symbol=%s error=%s", symbol, exc)
            return None
        return _nullable_float(payload, "lastFundingRate", "fundingRate")


def _balance_row(payload: dict[str, Any]) -> dict[str, Any]:
    balance = payload.get("balance")
    if isinstance(balance, dict):
        return balance
    data = payload.get("data")
    if isinstance(data, dict):
        nested = data.get("balance")
        if isinstance(nested, dict):
            return nested
        return data
    return payload


def _spot_usdt_equity(payload: dict[str, Any]) -> float:
    balances = payload.get("balances") or payload.get("items")
    if not isinstance(balances, list):
        return 0.0
    total = 0.0
    for row in balances:
        if not isinstance(row, dict):
            continue
        if str(row.get("asset") or "").upper() != "USDT":
            continue
        total += _first_float(row, "free", "available") + _first_float(row, "locked", "freeze")
    return total


def _order_snapshot(row: dict[str, Any]) -> BingXOpenOrder:
    return BingXOpenOrder(
        symbol=str(row.get("symbol") or ""),
        venue_order_id=str(row.get("orderId") or row.get("orderID") or "") or None,
        side=str(row.get("side") or "") or None,
        price=_nullable_float(row, "price"),
        quantity=_nullable_float(row, "quantity", "origQty"),
        status=str(row.get("status") or "") or None,
        raw=dict(row),
    )


def _first_float(payload: dict[str, Any], *keys: str) -> float:
    value = _nullable_float(payload, *keys)
    return value if value is not None else 0.0


def _nullable_float(payload: dict[str, Any], *keys: str) -> float | None:
    for key in keys:
        value = payload.get(key)
        if value in (None, ""):
            continue
        try:
            return float(value)  # type: ignore[arg-type]
        except (TypeError, ValueError):
            continue
    return None


def _object_to_dict(value: object | None) -> dict[str, Any] | None:
    if value is None:
        return None
    if isinstance(value, dict):
        return dict(value)
    model_dump = getattr(value, "model_dump", None)
    if callable(model_dump):
        dumped = model_dump(mode="python")
        return dumped if isinstance(dumped, dict) else None
    return {
        key: raw
        for key in ("symbol", "price", "name", "changesPercentage", "change")
        if (raw := getattr(value, key, None)) is not None
    }


def _utc_iso_now() -> str:
    return datetime.now(tz=UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
