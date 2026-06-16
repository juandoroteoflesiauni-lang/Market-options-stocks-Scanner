"""Servicio feed L2 BingX: REST bootstrap + stream WS/diff (Fase 2). # [PD-3][TH]"""

from __future__ import annotations

import asyncio
import os
import time
from dataclasses import dataclass, field
from typing import Any

from backend.config.equity_l2_watchlist import (
    EQUITY_L2_POLL_INTERVAL_S,
    EQUITY_L2_WATCHLIST,
)
from backend.config.logger_setup import get_logger
from backend.layer_1_data.datos.bingx_trade_adapter import BingXMicrostructureBundle
from backend.layer_1_data.datos.bingx_ws_hub import BingXWebSocketHub
from backend.layer_1_data.datos.equity_l2_depth_diff import (
    diff_order_books_to_events,
    order_book_to_lob_snapshot,
)
from backend.layer_1_data.datos.equity_l2_stream import StreamMode, run_symbol_stream
from backend.layer_1_data.datos.equity_l2_watchlist_hub import (
    fetch_equity_l2_microstructure,
    fetch_watchlist_microstructure,
)
from backend.quant_engine.engines.technical.lob_dynamics_engine import LOBDynamicsEngine
from backend.quant_engine.engines.technical.ofi_engine import L1Snapshot, OFIEngine
from backend.services.scanner_symbol_routing import normalize_scanner_symbol

logger = get_logger(__name__)

_service: EquityL2FeedService | None = None


def equity_l2_feed_enabled() -> bool:
    raw = os.getenv("EQUITY_L2_FEED_ENABLED", "true").strip().lower()
    return raw in {"1", "true", "yes", "on"}


def get_equity_l2_feed() -> EquityL2FeedService:
    global _service
    if _service is None:
        _service = EquityL2FeedService()
    return _service


def configure_equity_l2_feed(service: EquityL2FeedService) -> None:
    global _service
    _service = service


@dataclass
class _SymbolRuntime:
    stream_mode: StreamMode = "rest_diff"
    prev_order_book: dict[str, Any] | None = None
    ofi_engine: OFIEngine = field(default_factory=OFIEngine)
    lob_engine: LOBDynamicsEngine = field(default_factory=LOBDynamicsEngine)
    lob_result: dict[str, Any] | None = None
    passive_ofd: dict[str, Any] | None = None
    depth_updates: int = 0
    lob_events: int = 0
    last_depth_at: float | None = None
    last_depth_source: str | None = None


def _l1_from_order_book(order_book: dict[str, Any]) -> L1Snapshot | None:
    bids = order_book.get("parsed_bids") or []
    asks = order_book.get("parsed_asks") or []
    if not bids or not asks:
        return None
    best_bid = max(bids, key=lambda row: row[0])
    best_ask = min(asks, key=lambda row: row[0])
    ts_ms = int(order_book.get("timestamp_ms") or time.time() * 1000)
    return L1Snapshot(
        timestamp=ts_ms / 1000.0,
        best_bid_price=float(best_bid[0]),
        best_bid_size=float(best_bid[1]),
        best_ask_price=float(best_ask[0]),
        best_ask_size=float(best_ask[1]),
    )


class EquityL2FeedService:
    """Watchlist L2: bootstrap REST + stream depth (WS o REST diff)."""

    def __init__(self, client: Any | None = None) -> None:
        self._client = client
        self._hub = BingXWebSocketHub()
        self._cache: dict[str, dict[str, Any]] = {}
        self._runtime: dict[str, _SymbolRuntime] = {}
        self._running = False
        self._stream_tasks: list[asyncio.Task[None]] = []
        self._bootstrap_task: asyncio.Task[None] | None = None

    def get_microstructure(self, symbol: str) -> dict[str, Any] | None:
        root = normalize_scanner_symbol(symbol)
        return self._cache.get(root)

    def set_stream_mode(self, root: str, mode: StreamMode) -> None:
        self._runtime.setdefault(normalize_scanner_symbol(root), _SymbolRuntime()).stream_mode = mode

    def apply_depth_update(self, root: str, order_book: dict[str, Any], *, source: str) -> None:
        """Aplica depth stream/diff y actualiza OFI + LOB pasivo en cache."""
        key = normalize_scanner_symbol(root)
        runtime = self._runtime.setdefault(key, _SymbolRuntime())
        events = diff_order_books_to_events(runtime.prev_order_book, order_book)
        runtime.prev_order_book = dict(order_book)
        runtime.depth_updates += 1
        runtime.last_depth_at = time.time()
        runtime.last_depth_source = source

        snapshot = order_book_to_lob_snapshot(order_book)
        if snapshot is not None and not events:
            lob_frame = runtime.lob_engine.process_snapshot(snapshot)
        else:
            lob_frame = None
            for event in events:
                lob_frame = runtime.lob_engine.process_event(event)
                runtime.lob_events += 1
            if lob_frame is None and snapshot is not None:
                lob_frame = runtime.lob_engine.process_snapshot(snapshot)

        if lob_frame is not None:
            runtime.lob_result = {
                "imbalance_rho": lob_frame.imbalance_rho,
                "ctr_bid": lob_frame.ctr_bid,
                "ctr_ask": lob_frame.ctr_ask,
                "spoofing_state": lob_frame.spoofing_state.name,
                "timestamp": lob_frame.timestamp,
            }
            runtime.passive_ofd = {
                "ok": True,
                "passive_cancel_pressure_bid": lob_frame.ctr_bid,
                "passive_cancel_pressure_ask": lob_frame.ctr_ask,
                "spoofing_state": lob_frame.spoofing_state.name,
                "lob_event_count": runtime.lob_events,
                "source": source,
            }

        l1 = _l1_from_order_book(order_book)
        ofi_payload: dict[str, Any] | None = None
        if l1 is not None:
            ofi_result = runtime.ofi_engine.update(l1)
            ofi_payload = {
                "ok": True,
                "regime": ofi_result.regime.value,
                "latest_raw_ofi": ofi_result.raw_ofi,
                "latest_accumulated_ofi": ofi_result.accumulated_ofi,
                "latest_delta_bid": ofi_result.delta_bid,
                "latest_delta_ask": ofi_result.delta_ask,
                "window_tick_count": ofi_result.window_tick_count,
                "source": source,
            }

        cached = self._cache.get(key)
        if cached is None:
            cached = {
                "symbol": key,
                "venue_symbol": order_book.get("symbol") or key,
                "ok": True,
                "reason": "stream_only",
            }
            self._cache[key] = cached
        cached["order_book"] = order_book
        cached["depth_source"] = source
        cached["stream_mode"] = runtime.stream_mode
        cached["last_depth_at"] = runtime.last_depth_at
        if ofi_payload:
            cached["ofi"] = ofi_payload
        if runtime.lob_result:
            cached["lob_stream"] = runtime.lob_result
        if runtime.passive_ofd:
            cached["passive_order_flow"] = runtime.passive_ofd

    def snapshot_status(self) -> dict[str, Any]:
        symbols: dict[str, Any] = {}
        for root in EQUITY_L2_WATCHLIST:
            key = normalize_scanner_symbol(root)
            rt = self._runtime.get(key)
            cached = self._cache.get(key)
            symbols[key] = {
                "ok": bool(cached and cached.get("ok")),
                "stream_mode": rt.stream_mode if rt else "rest_diff",
                "depth_updates": rt.depth_updates if rt else 0,
                "lob_events": rt.lob_events if rt else 0,
                "last_depth_at": rt.last_depth_at if rt else None,
                "last_depth_source": rt.last_depth_source if rt else None,
            }
        return {
            "enabled": equity_l2_feed_enabled(),
            "running": self._running,
            "phase": "v2_ws_rest_hybrid",
            "watchlist": list(EQUITY_L2_WATCHLIST),
            "cached_symbols": sorted(self._cache.keys()),
            "ok_count": sum(1 for v in self._cache.values() if v.get("ok")),
            "symbols": symbols,
        }

    async def refresh_symbol(self, root: str) -> dict[str, Any]:
        client = await self._ensure_client()
        bundle = await fetch_equity_l2_microstructure(client, root)
        payload = self._enrich_bundle(bundle)
        key = normalize_scanner_symbol(root)
        self._cache[key] = payload
        if bundle.order_book:
            self.apply_depth_update(key, bundle.order_book, source="bingx_rest_bootstrap")
        return payload

    async def refresh_all(self) -> dict[str, Any]:
        client = await self._ensure_client()
        bundles = await fetch_watchlist_microstructure(client)
        ok = 0
        for root, bundle in bundles.items():
            payload = self._enrich_bundle(bundle)
            self._cache[root] = payload
            if bundle.order_book:
                self.apply_depth_update(root, bundle.order_book, source="bingx_rest_bootstrap")
            if payload.get("ok"):
                ok += 1
        logger.info("equity_l2_feed.refreshed ok=%d total=%d", ok, len(bundles))
        return {"refreshed": len(bundles), "ok": ok}

    async def run_forever(self, poll_interval_s: int = EQUITY_L2_POLL_INTERVAL_S) -> None:
        self._running = True
        client = await self._ensure_client()
        for root in EQUITY_L2_WATCHLIST:
            self._stream_tasks.append(
                asyncio.create_task(
                    run_symbol_stream(self, root, client=client, hub=self._hub),
                    name=f"equity-l2-stream-{root}",
                )
            )
        logger.info(
            "equity_l2_feed.started bootstrap_s=%d stream_workers=%d",
            poll_interval_s,
            len(self._stream_tasks),
        )
        try:
            while self._running:
                try:
                    await self.refresh_all()
                except Exception as exc:
                    logger.error("equity_l2_feed.refresh_failed %s", exc, exc_info=True)
                await asyncio.sleep(max(1, poll_interval_s))
        finally:
            self._running = False
            for task in self._stream_tasks:
                task.cancel()
            if self._stream_tasks:
                await asyncio.gather(*self._stream_tasks, return_exceptions=True)
            self._stream_tasks.clear()
            logger.info("equity_l2_feed.stopped")

    async def stop(self) -> None:
        self._running = False
        if self._bootstrap_task is not None and not self._bootstrap_task.done():
            self._bootstrap_task.cancel()
            try:
                await self._bootstrap_task
            except asyncio.CancelledError:
                pass
            self._bootstrap_task = None
        for task in self._stream_tasks:
            if not task.done():
                task.cancel()
        if self._stream_tasks:
            await asyncio.gather(*self._stream_tasks, return_exceptions=True)
        self._stream_tasks.clear()

    def start_background(
        self, poll_interval_s: int = EQUITY_L2_POLL_INTERVAL_S
    ) -> asyncio.Task[None]:
        self._bootstrap_task = asyncio.create_task(self.run_forever(poll_interval_s))
        return self._bootstrap_task

    def _enrich_bundle(self, bundle: BingXMicrostructureBundle) -> dict[str, Any]:
        payload = bundle.to_dict()
        if not bundle.ok or not bundle.order_book:
            return payload
        root = normalize_scanner_symbol(bundle.symbol)
        runtime = self._runtime.setdefault(root, _SymbolRuntime())
        l1 = _l1_from_order_book(bundle.order_book)
        if l1 is None:
            return payload
        ofi_result = runtime.ofi_engine.update(l1)
        payload["ofi"] = {
            "ok": True,
            "regime": ofi_result.regime.value,
            "latest_raw_ofi": ofi_result.raw_ofi,
            "latest_accumulated_ofi": ofi_result.accumulated_ofi,
            "latest_delta_bid": ofi_result.delta_bid,
            "latest_delta_ask": ofi_result.delta_ask,
            "window_tick_count": ofi_result.window_tick_count,
            "source": "bingx_rest_bootstrap",
        }
        payload["stream_mode"] = runtime.stream_mode
        return payload

    async def _ensure_client(self) -> Any:
        if self._client is not None:
            return self._client
        from backend.layer_1_data.datos.bingx_client import BingXClient

        self._client = BingXClient(dry_run=True)
        return self._client


def build_terminal_l2_blocks(symbol: str, flags: dict[str, bool]) -> dict[str, Any]:
    from backend.layer_1_data.datos.equity_l2_watchlist_hub import is_watchlist_symbol
    from backend.quant_engine.engines.technical.lob_dynamics_engine import (
        unavailable_lob_dynamics_payload,
    )
    from backend.services.bingx_l2_integration import order_book_dict_to_lob_analysis

    root = normalize_scanner_symbol(symbol)
    if not equity_l2_feed_enabled() or not is_watchlist_symbol(root):
        return {}

    micro = get_equity_l2_feed().get_microstructure(root)
    if not micro or not micro.get("ok"):
        return {}

    depth_source = str(micro.get("depth_source") or micro.get("stream_mode") or "bingx_watchlist")
    blocks: dict[str, Any] = {
        "meta": {
            "equity_l2_source": "bingx_watchlist_feed_v2",
            "stream_mode": micro.get("stream_mode"),
            "depth_source": depth_source,
        }
    }

    if flags.get("enable_lob_dynamics") and micro.get("order_book"):
        analysis = order_book_dict_to_lob_analysis(
            micro["order_book"],
            symbol=str(micro.get("venue_symbol") or root),
            market_type="stock_perp",
        )
        stream_lob = micro.get("lob_stream") or {}
        blocks["lob_dynamics"] = analysis.model_dump(mode="json") | {
            "enabled": True,
            "source": depth_source,
            "stream_ctr_bid": stream_lob.get("ctr_bid"),
            "stream_ctr_ask": stream_lob.get("ctr_ask"),
            "stream_spoofing_state": stream_lob.get("spoofing_state"),
        }
    elif flags.get("enable_lob_dynamics"):
        blocks["lob_dynamics"] = unavailable_lob_dynamics_payload() | {
            "enabled": True,
            "error": "order_book missing in equity L2 feed",
        }

    if flags.get("enable_order_flow_delta"):
        passive = micro.get("passive_order_flow")
        executed_cvd = micro.get("cvd")
        if passive or executed_cvd is not None:
            blocks["order_flow_delta"] = {
                "enabled": True,
                "ok": True,
                "latest_cvd": float(executed_cvd) if executed_cvd is not None else None,
                "delta_bias": _cvd_bias(executed_cvd),
                "passive_cancel_pressure_bid": (passive or {}).get("passive_cancel_pressure_bid"),
                "passive_cancel_pressure_ask": (passive or {}).get("passive_cancel_pressure_ask"),
                "spoofing_state": (passive or {}).get("spoofing_state"),
                "source": (passive or {}).get("source") or "bingx_trade_tape",
            }

    if micro.get("ofi"):
        blocks["ofi"] = dict(micro["ofi"]) | {"enabled": True}

    return blocks


def _cvd_bias(cvd: object) -> str:
    if cvd is None:
        return "Neutral"
    value = float(cvd)
    if value > 0:
        return "Bullish"
    if value < 0:
        return "Bearish"
    return "Neutral"


__all__ = [
    "EquityL2FeedService",
    "build_terminal_l2_blocks",
    "configure_equity_l2_feed",
    "equity_l2_feed_enabled",
    "get_equity_l2_feed",
]
