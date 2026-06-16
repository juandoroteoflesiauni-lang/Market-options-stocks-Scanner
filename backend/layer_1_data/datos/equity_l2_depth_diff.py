"""Diff consecutivos de order book → eventos LOB (add/cancel). # [PD-3][TH][IM]"""

from __future__ import annotations

from typing import Any

from backend.quant_engine.engines.technical.lob_dynamics_engine import (
    LOBEvent,
    LOBEventType,
    LOBSide,
    LOBLevel,
    LOBSnapshot,
)


def _levels_map(levels: list[tuple[float, float]]) -> dict[float, float]:
    out: dict[float, float] = {}
    for price, qty in levels:
        if price > 0 and qty >= 0:
            out[float(price)] = float(qty)
    return out


def order_book_to_lob_snapshot(order_book: dict[str, Any]) -> LOBSnapshot | None:
    """Convierte payload normalizado de depth a LOBSnapshot."""
    bids_raw = order_book.get("parsed_bids") or []
    asks_raw = order_book.get("parsed_asks") or []
    if not bids_raw or not asks_raw:
        return None
    ts = int(order_book.get("timestamp_ms") or 0)
    bids = tuple(LOBLevel(price=float(p), quantity=float(q)) for p, q in bids_raw)
    asks = tuple(LOBLevel(price=float(p), quantity=float(q)) for p, q in asks_raw)
    return LOBSnapshot(timestamp=ts, bids=bids, asks=asks)


def diff_order_books_to_events(
    previous: dict[str, Any] | None,
    current: dict[str, Any],
) -> tuple[LOBEvent, ...]:
    """Infiera eventos ADD/CANCEL comparando dos snapshots de depth."""
    if previous is None:
        return ()
    ts = int(current.get("timestamp_ms") or previous.get("timestamp_ms") or 0)
    prev_bids = _levels_map(list(previous.get("parsed_bids") or []))
    prev_asks = _levels_map(list(previous.get("parsed_asks") or []))
    curr_bids = _levels_map(list(current.get("parsed_bids") or []))
    curr_asks = _levels_map(list(current.get("parsed_asks") or []))
    events: list[LOBEvent] = []
    events.extend(_diff_side(prev_bids, curr_bids, LOBSide.BID, ts))
    events.extend(_diff_side(prev_asks, curr_asks, LOBSide.ASK, ts))
    return tuple(events)


def _diff_side(
    previous: dict[float, float],
    current: dict[float, float],
    side: LOBSide,
    timestamp: int,
) -> list[LOBEvent]:
    events: list[LOBEvent] = []
    for price in set(previous) | set(current):
        old_qty = previous.get(price, 0.0)
        new_qty = current.get(price, 0.0)
        if new_qty > old_qty:
            events.append(
                LOBEvent(
                    timestamp=timestamp,
                    type=LOBEventType.ADD,
                    side=side,
                    price=price,
                    quantity=new_qty - old_qty,
                )
            )
        elif new_qty < old_qty:
            events.append(
                LOBEvent(
                    timestamp=timestamp,
                    type=LOBEventType.CANCEL,
                    side=side,
                    price=price,
                    quantity=old_qty - new_qty,
                )
            )
    return events


__all__ = [
    "diff_order_books_to_events",
    "order_book_to_lob_snapshot",
]
