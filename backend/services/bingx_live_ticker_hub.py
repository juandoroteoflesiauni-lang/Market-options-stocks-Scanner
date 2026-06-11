"""BingX live ticker hub — fan-out venue WebSocket ticks to dashboard clients.

Maintains in-memory account + position state seeded from REST, then mutates on:
* Public ``{symbol}@ticker`` mark/last price frames (Layer 1 BingXWebSocketHub).
* Private account stream (``BingXAccountWebSocket``) balance / position events.

Broadcast payloads mirror ``monitoring_service`` mirror fields so the React
dashboard can render tick-by-tick without waiting for the bot analytic cycle.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import os
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

from fastapi import WebSocket

from backend.config.logger_setup import get_logger
from backend.config.settings import load_settings
from backend.layer_1_data.datos.bingx_account_ws import BingXAccountWebSocket
from backend.layer_1_data.datos.bingx_client import BingXClient
from backend.layer_1_data.datos.bingx_ws_hub import BingXWebSocketHub
from backend.services.bingx_account_service import BingXAccountService
from backend.services.monitoring_service import (
    TelemetryMirrorAccount,
    TelemetryMirrorPosition,
    _load_zone_hints_from_journal,
    _normalize_position_side,
)

logger = get_logger(__name__)

_ACCOUNT_REST_REFRESH_S = 3.0
_MARKET_CHANNEL_SUFFIX = "ticker"


@dataclass
class _PositionState:
    symbol: str
    side: str = "UNKNOWN"
    size: float = 0.0
    entry_price: float = 0.0
    current_spot: float = 0.0
    leverage: int = 1
    unrealized_pnl: float = 0.0
    current_zone: str = "NEUTRAL"

    def pnl_real_apalancado(self) -> float | None:
        if self.entry_price <= 0 or self.current_spot <= 0:
            return self.unrealized_pnl if self.unrealized_pnl else None
        unleveraged = (
            ((self.current_spot - self.entry_price) / self.entry_price) * 100.0
            if self.side == "LONG"
            else ((self.entry_price - self.current_spot) / self.entry_price) * 100.0
        )
        return unleveraged * self.leverage


@dataclass
class _LiveTickerState:
    total_equity: float = 0.0
    available_margin: float = 0.0
    used_margin: float = 0.0
    unrealized_pnl_usdt: float = 0.0
    positions: dict[str, _PositionState] = field(default_factory=dict)
    zone_hints: dict[str, str] = field(default_factory=dict)
    venue_connected: bool = False
    last_tick_at: str | None = None


def _utc_iso_now() -> str:
    return datetime.now(tz=UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def _first_float(row: dict[str, Any], *keys: str, default: float = 0.0) -> float:
    for key in keys:
        raw = row.get(key)
        if raw is None:
            continue
        with contextlib.suppress(TypeError, ValueError):
            return float(raw)
    return default


def _extract_ticker_price(payload: dict[str, Any]) -> tuple[str, float] | None:
    """Parse a BingX public ticker frame into (symbol, price)."""
    data = payload.get("data", payload)
    rows: list[dict[str, Any]] = []
    if isinstance(data, dict):
        rows = [data]
    elif isinstance(data, list):
        rows = [r for r in data if isinstance(r, dict)]
    for row in rows:
        symbol = str(row.get("s") or row.get("symbol") or "").strip()
        price = _first_float(
            row,
            "markPrice",
            "mark_price",
            "p",
            "c",
            "lastPrice",
            "last",
            "price",
            default=-1.0,
        )
        if symbol and price > 0:
            return symbol, price
    data_type = str(payload.get("dataType") or "")
    if "@" in data_type:
        symbol = data_type.split("@", 1)[0].strip()
        price = _first_float(payload, "markPrice", "c", "p", "lastPrice", default=-1.0)
        if symbol and price > 0:
            return symbol, price
    return None


def _apply_account_update(state: _LiveTickerState, payload: dict[str, Any]) -> bool:
    """Apply Binance-style ACCOUNT_UPDATE fields; return True if state changed."""
    event = str(payload.get("e") or payload.get("eventType") or "").upper()
    if event and event not in {"ACCOUNT_UPDATE", "ORDER_TRADE_UPDATE"}:
        return False

    account_block = payload.get("a")
    if not isinstance(account_block, dict):
        account_block = payload

    changed = False
    balances = account_block.get("B")
    if isinstance(balances, list):
        for bal in balances:
            if not isinstance(bal, dict):
                continue
            asset = str(bal.get("a") or bal.get("asset") or "").upper()
            if asset and asset not in {"USDT", "VST", "USD"}:
                continue
            equity = _first_float(
                bal,
                "wb",
                "cw",
                "balance",
                "equity",
                "walletBalance",
            )
            available = _first_float(
                bal,
                "cw",
                "availableMargin",
                "availableBalance",
                "free",
            )
            if equity > 0:
                state.total_equity = equity
                changed = True
            if available >= 0:
                state.available_margin = available
                changed = True

    positions = account_block.get("P")
    if isinstance(positions, list):
        for row in positions:
            if not isinstance(row, dict):
                continue
            symbol = str(row.get("s") or row.get("symbol") or "").strip()
            if not symbol:
                continue
            size = _first_float(row, "pa", "positionAmt", "positionAmount", "size")
            if abs(size) < 1e-12:
                if symbol in state.positions:
                    del state.positions[symbol]
                    changed = True
                continue
            side = _normalize_position_side(
                str(row.get("ps") or row.get("positionSide") or row.get("side") or ""),
                size,
            )
            entry = _first_float(row, "ep", "entryPrice", "avgPrice")
            mark = _first_float(row, "mp", "markPrice", "lastPrice")
            leverage = int(_first_float(row, "l", "leverage", default=1.0) or 1)
            upnl = _first_float(row, "up", "unrealizedProfit", "unrealizedPnl")
            zone = state.zone_hints.get(symbol, "NEUTRAL")
            pos = state.positions.get(symbol) or _PositionState(symbol=symbol, current_zone=zone)
            pos.side = side
            pos.size = size
            pos.entry_price = entry or pos.entry_price
            pos.current_spot = mark or pos.current_spot
            pos.leverage = max(1, leverage)
            pos.unrealized_pnl = upnl
            pos.current_zone = zone
            state.positions[symbol] = pos
            changed = True

    if state.total_equity > 0 and state.used_margin == 0.0:
        notionals = sum(abs(p.size) * p.current_spot for p in state.positions.values())
        state.used_margin = max(0.0, state.total_equity - state.available_margin)
        if notionals > 0 and state.used_margin <= 0:
            state.used_margin = notionals / max(
                1, sum(p.leverage for p in state.positions.values())
            )
    return changed


def _state_to_payload(state: _LiveTickerState, *, event: str = "tick") -> dict[str, Any]:
    mirror_account = TelemetryMirrorAccount(
        total_equity=round(state.total_equity, 4),
        available_margin=round(state.available_margin, 4),
        used_margin=round(state.used_margin, 4),
    )
    mirror_positions = tuple(
        TelemetryMirrorPosition(
            symbol=p.symbol,
            side=p.side,
            entry_price=round(p.entry_price, 6),
            current_spot=round(p.current_spot, 6),
            leverage=p.leverage,
            pnl_real_apalancado=(
                round(pnl, 4) if (pnl := p.pnl_real_apalancado()) is not None else None
            ),
            current_zone=p.current_zone,
        )
        for p in state.positions.values()
    )
    return {
        "type": event,
        "captured_at": _utc_iso_now(),
        "venue_connected": state.venue_connected,
        "account": mirror_account.to_dict(),
        "positions": [row.to_dict() for row in mirror_positions],
    }


class BingXLiveTickerHub:
    """Singleton fan-out bridge: BingX WS → FastAPI dashboard WebSockets."""

    def __init__(
        self,
        *,
        client: BingXClient,
        symbols: list[str] | None = None,
        market_hub: BingXWebSocketHub | None = None,
    ) -> None:
        self._client = client
        self._symbols = symbols or _default_stream_symbols()
        self._market_hub = market_hub or BingXWebSocketHub()
        self._account_ws = BingXAccountWebSocket(client)
        self._state = _LiveTickerState(zone_hints=_load_zone_hints_from_journal())
        self._clients: set[WebSocket] = set()
        self._lock = asyncio.Lock()
        self._started = False
        self._tasks: list[asyncio.Task[None]] = []
        self._market_tasks: dict[str, asyncio.Task[None]] = {}
        self._account_service = BingXAccountService(client=client)

    @property
    def symbols(self) -> tuple[str, ...]:
        return tuple(self._symbols)

    @property
    def client_count(self) -> int:
        return len(self._clients)

    async def ensure_started(self) -> None:
        async with self._lock:
            if self._started:
                return
            if getattr(self._client, "dry_run", True) and not _env_venue_live():
                logger.info("bingx_live_ticker_hub.skipped dry_run=True no venue credentials mode")
                self._started = True
                return
            await self._seed_from_rest()
            for symbol in self._symbols:
                self._ensure_market_task(symbol)
            self._tasks.append(asyncio.create_task(self._account_loop(), name="bingx-account-ws"))
            self._tasks.append(
                asyncio.create_task(self._rest_refresh_loop(), name="bingx-account-rest")
            )
            self._started = True
            self._state.venue_connected = True
            logger.info(
                "bingx_live_ticker_hub.started symbols=%s dry_run=%s",
                len(self._symbols),
                getattr(self._client, "dry_run", True),
            )

    async def shutdown(self) -> None:
        async with self._lock:
            for task in [*self._tasks, *self._market_tasks.values()]:
                task.cancel()
            all_tasks = [*self._tasks, *self._market_tasks.values()]
            if all_tasks:
                await asyncio.gather(*all_tasks, return_exceptions=True)
            self._tasks.clear()
            self._market_tasks.clear()
            self._started = False
            self._clients.clear()

    async def register(self, websocket: WebSocket) -> None:
        await self.ensure_started()
        async with self._lock:
            self._clients.add(websocket)
        await websocket.send_text(json.dumps(_state_to_payload(self._state, event="snapshot")))

    async def unregister(self, websocket: WebSocket) -> None:
        async with self._lock:
            self._clients.discard(websocket)

    def _ensure_market_task(self, symbol: str) -> None:
        sym = symbol.strip()
        if not sym or sym in self._market_tasks:
            return
        self._market_tasks[sym] = asyncio.create_task(
            self._market_loop(sym),
            name=f"bingx-ticker-{sym}",
        )

    async def _seed_from_rest(self) -> None:
        try:
            account = await self._account_service.get_account_state()
        except Exception as exc:
            logger.warning("bingx_live_ticker_hub.seed_failed error=%s", exc)
            return
        self._state.total_equity = account.total_equity_usdt
        self._state.available_margin = account.available_margin_usdt
        self._state.used_margin = account.used_margin_usdt
        self._state.unrealized_pnl_usdt = account.unrealized_pnl_usdt
        self._state.positions.clear()
        for pos in account.open_positions:
            zone = self._state.zone_hints.get(pos.symbol, "NEUTRAL")
            self._state.positions[pos.symbol] = _PositionState(
                symbol=pos.symbol,
                side=_normalize_position_side(pos.side, pos.size),
                size=pos.size,
                entry_price=pos.entry_price,
                current_spot=pos.current_price or pos.mark_price,
                leverage=max(1, int(pos.leverage)),
                unrealized_pnl=pos.unrealized_pnl,
                current_zone=zone,
            )
            self._ensure_market_task(pos.symbol)

    async def _rest_refresh_loop(self) -> None:
        while True:
            try:
                await asyncio.sleep(_ACCOUNT_REST_REFRESH_S)
                prev = (
                    self._state.total_equity,
                    self._state.available_margin,
                    tuple(sorted(self._state.positions.keys())),
                )
                await self._seed_from_rest()
                now = (
                    self._state.total_equity,
                    self._state.available_margin,
                    tuple(sorted(self._state.positions.keys())),
                )
                if now != prev:
                    await self._broadcast()
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                logger.debug("bingx_live_ticker_hub.rest_refresh error=%s", exc)

    async def _market_loop(self, symbol: str) -> None:
        while True:
            try:
                async for frame in self._market_hub.stream_channel(
                    symbol,
                    _MARKET_CHANNEL_SUFFIX,
                    max_messages=None,
                ):
                    parsed = _extract_ticker_price(frame)
                    if parsed is None:
                        continue
                    sym, price = parsed
                    pos = self._state.positions.get(sym)
                    if pos is None:
                        continue
                    if abs(pos.current_spot - price) < 1e-9:
                        continue
                    pos.current_spot = price
                    self._state.last_tick_at = _utc_iso_now()
                    await self._broadcast()
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                logger.warning(
                    "bingx_live_ticker_hub.market_loop_retry symbol=%s error=%s",
                    symbol,
                    exc,
                )
                await asyncio.sleep(2.0)

    async def _account_loop(self) -> None:
        while True:
            try:
                async for event in self._account_ws.stream_events():
                    if not _apply_account_update(self._state, event):
                        continue
                    for sym in self._state.positions:
                        self._ensure_market_task(sym)
                    self._state.last_tick_at = _utc_iso_now()
                    await self._broadcast()
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                logger.warning("bingx_live_ticker_hub.account_loop_retry error=%s", exc)
                await asyncio.sleep(3.0)

    async def _broadcast(self) -> None:
        message = json.dumps(_state_to_payload(self._state, event="tick"))
        dead: list[WebSocket] = []
        async with self._lock:
            clients = list(self._clients)
        for ws in clients:
            try:
                await ws.send_text(message)
            except Exception:
                dead.append(ws)
        if dead:
            async with self._lock:
                for ws in dead:
                    self._clients.discard(ws)


def _default_stream_symbols() -> list[str]:
    with contextlib.suppress(Exception):
        cfg = load_settings()
        allowlist = sorted(cfg.get_bingx_live_allowlist())
        if allowlist:
            return allowlist
        priority = str(getattr(cfg, "bingx_bot_priority_stocks", "") or "")
        symbols = [s.strip() for s in priority.split(",") if s.strip()]
        if symbols:
            return symbols
    env = os.getenv(
        "BINGX_BOT_PRIORITY_STOCKS",
        "AMZN-USDT,AAPL-USDT,TSLA-USDT,GOOGL-USDT,META-USDT,MSFT-USDT,NVDA-USDT,PLTR-USDT",
    )
    return [s.strip() for s in env.split(",") if s.strip()]


def _env_venue_live() -> bool:
    dry = os.getenv("BINGX_DRY_RUN", "true").strip().lower()
    if dry in {"0", "false", "no", "live"}:
        return True
    trading = os.getenv("BINGX_BOT_TRADING_ENV", "").strip().lower()
    return trading == "prod-vst"


_hub: BingXLiveTickerHub | None = None


def configure_live_ticker_hub(
    *,
    client: BingXClient,
    symbols: list[str] | None = None,
) -> BingXLiveTickerHub:
    """Bind the process-wide hub to the API lifespan BingX client."""
    global _hub
    _hub = BingXLiveTickerHub(client=client, symbols=symbols)
    return _hub


def get_live_ticker_hub() -> BingXLiveTickerHub:
    if _hub is None:
        raise RuntimeError(
            "BingX live ticker hub is not configured — start api_server lifespan first"
        )
    return _hub


__all__ = [
    "BingXLiveTickerHub",
    "configure_live_ticker_hub",
    "get_live_ticker_hub",
]
