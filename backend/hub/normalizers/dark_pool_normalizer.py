"""Normalizer: raw dark-pool prints → ``DarkPoolSnapshot``. # [PD-2][TH]

Aggregates a trailing window of dark-pool prints into a single signed net
notional plus a directional bias and confidence. Tolerant of partial / varied
provider schemas — degrades to a NEUTRAL, zero-confidence snapshot rather than
raising.
"""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal, InvalidOperation
from typing import Any

from backend.config.dark_pool_calibration import bias_threshold_usd, confidence_ref_prints
from backend.config.logger_setup import get_logger
from backend.models.dark_pool_snapshot import DarkPoolSnapshot

logger = get_logger(__name__)

# Per-print field name candidates (providers differ).
_NOTIONAL_KEYS = ("premium", "notional", "value", "dollar_volume")
_PRICE_KEYS = ("price", "fill_price", "avg_price")
_SIZE_KEYS = ("size", "volume", "quantity", "shares")
_SIDE_KEYS = ("side", "sentiment", "direction")

_BULLISH_TOKENS = {"buy", "bull", "bullish", "long", "ask"}
_BEARISH_TOKENS = {"sell", "bear", "bearish", "short", "bid"}


def _to_decimal(value: object) -> Decimal | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        out = Decimal(str(value))
    except (InvalidOperation, ValueError, TypeError):
        return None
    return out if out.is_finite() else None


class DarkPoolNormalizer:
    """Convert a provider dark-pool payload into a frozen snapshot."""

    def normalize(
        self, raw: dict[str, Any], *, symbol: str, source: str = "unusual_whales"
    ) -> DarkPoolSnapshot:
        prints = self._extract_prints(raw)
        net_notional = Decimal("0")
        count = 0
        for row in prints:
            notional = self._print_notional(row)
            if notional is None:
                continue
            count += 1
            net_notional += notional * self._side_sign(row)

        threshold = _to_decimal(bias_threshold_usd()) or Decimal("0")
        if net_notional > threshold:
            bias = "BULLISH"
        elif net_notional < -threshold:
            bias = "BEARISH"
        else:
            bias = "NEUTRAL"

        ref = confidence_ref_prints()
        confidence = min(1.0, count / ref) if ref > 0 else 0.0

        return DarkPoolSnapshot(
            symbol=symbol.upper(),
            print_count_1h=count,
            net_notional_usd=net_notional,
            bias=bias,
            confidence=round(confidence, 4),
            fetched_at=datetime.now(UTC),
            source=source,
        )

    @staticmethod
    def _extract_prints(raw: dict[str, Any]) -> list[dict[str, Any]]:
        for key in ("data", "prints", "trades", "results"):
            value = raw.get(key)
            if isinstance(value, list):
                return [row for row in value if isinstance(row, dict)]
        return []

    @staticmethod
    def _print_notional(row: dict[str, Any]) -> Decimal | None:
        for key in _NOTIONAL_KEYS:
            notional = _to_decimal(row.get(key))
            if notional is not None:
                return abs(notional)
        price = next((p for k in _PRICE_KEYS if (p := _to_decimal(row.get(k))) is not None), None)
        size = next((s for k in _SIZE_KEYS if (s := _to_decimal(row.get(k))) is not None), None)
        if price is not None and size is not None:
            return abs(price * size)
        return None

    @staticmethod
    def _side_sign(row: dict[str, Any]) -> Decimal:
        for key in _SIDE_KEYS:
            token = str(row.get(key) or "").strip().lower()
            if token in _BULLISH_TOKENS:
                return Decimal("1")
            if token in _BEARISH_TOKENS:
                return Decimal("-1")
        return Decimal("0")


__all__ = ["DarkPoolNormalizer"]
