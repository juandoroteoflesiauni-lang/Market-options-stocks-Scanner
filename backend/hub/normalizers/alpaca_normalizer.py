from typing import Any
import time
from datetime import UTC, datetime
from decimal import Decimal

from backend.models.market_snapshot import DataLineage, MarketSnapshot


class AlpacaNormalizer:
    """Transforms Alpaca API raw response into a canonical MarketSnapshot."""

    PROVIDER_NAME: str = "alpaca"

    def normalize(self, raw: dict[str, Any], ingestion_start_ns: int) -> MarketSnapshot:
        """Converts an Alpaca ticker response to MarketSnapshot."""
        ingestion_latency_ms = (time.time_ns() - ingestion_start_ns) // 1_000_000

        ts = raw["timestamp"]
        if isinstance(ts, str):
            dt = datetime.fromisoformat(ts.replace("Z", "+00:00"))
        else:
            dt = datetime.fromtimestamp(ts, tz=UTC)

        return MarketSnapshot(
            ticker=raw["symbol"].upper(),
            exchange="UNKNOWN",
            price=Decimal(str(raw["close"])),
            volume=int(raw["volume"]),
            exchange_timestamp=dt,
            data_lineage=DataLineage(
                source=self.PROVIDER_NAME,
                ingestion_latency_ms=ingestion_latency_ms,
                raw_field_count=len(raw),
            ),
        )
