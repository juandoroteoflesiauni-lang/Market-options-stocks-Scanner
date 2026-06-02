import time
from datetime import datetime, timezone
from decimal import Decimal

from backend.models.market_snapshot import DataLineage, MarketSnapshot


class MassiveNormalizer:
    """Transforms Massive API raw response into a canonical MarketSnapshot."""

    PROVIDER_NAME: str = "massive"

    def normalize(self, raw: dict, ingestion_start_ns: int) -> MarketSnapshot:
        """Converts a Massive websocket ticker response to MarketSnapshot."""
        ingestion_latency_ms = (time.time_ns() - ingestion_start_ns) // 1_000_000

        return MarketSnapshot(
            ticker=raw["sym"].upper(),
            exchange=raw.get("x", "UNKNOWN"),
            price=Decimal(str(raw["p"])),
            volume=int(raw.get("v", 0)),
            exchange_timestamp=datetime.fromtimestamp(
                raw["t"] / 1000.0, tz=timezone.utc
            ),
            data_lineage=DataLineage(
                source=self.PROVIDER_NAME,
                ingestion_latency_ms=ingestion_latency_ms,
                raw_field_count=len(raw),
            ),
        )
