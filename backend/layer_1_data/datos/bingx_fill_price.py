"""BingX fill price and timestamp normalization. # [PD-3][TH]"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

_FILL_PRICE_KEYS = ("avgPrice", "avgFillPrice", "price")


def resolve_fill_price_from_row(row: dict[str, Any] | None) -> float | None:
    """Return the first strictly positive fill price from a BingX API row.

    BingX MARKET orders often set ``price`` to ``"0"`` / ``"0.0"`` (truthy in
    Python) while the real execution price lives in ``avgPrice`` or ``avgFillPrice``.
    """
    if not row:
        return None
    for key in _FILL_PRICE_KEYS:
        raw = row.get(key)
        if raw in (None, ""):
            continue
        try:
            value = float(raw)
        except (TypeError, ValueError):
            continue
        if value > 0.0:
            return value
    return None


def parse_bingx_executed_at_utc(row: dict[str, Any]) -> datetime | None:
    """Normalize BingX fill/order timestamps to UTC ``datetime``."""
    filled = row.get("filledTime") or row.get("filled_time")
    if isinstance(filled, str) and filled.strip():
        try:
            return datetime.fromisoformat(filled.replace("Z", "+00:00")).astimezone(UTC)
        except ValueError:
            pass

    ts = row.get("time") or row.get("updateTime") or row.get("createTime") or row.get("timestamp")
    if isinstance(ts, int | float) and ts > 1_000_000_000_000:
        return datetime.fromtimestamp(ts / 1000, tz=UTC)
    if isinstance(ts, str) and ts.strip():
        try:
            return datetime.fromisoformat(ts.replace("Z", "+00:00")).astimezone(UTC)
        except ValueError:
            return None
    return None
