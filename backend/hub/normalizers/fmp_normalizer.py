import time
from datetime import datetime, timezone
from decimal import Decimal

from backend.models.market_snapshot import DataLineage, MarketSnapshot


class FmpNormalizer:
    """Transforms FMP API raw response into a canonical MarketSnapshot."""

    PROVIDER_NAME: str = "fmp"

    def normalize(self, raw: dict, ingestion_start_ns: int) -> MarketSnapshot:
        """Converts an FMP ticker response to MarketSnapshot.
        
        Args:
            raw: The raw dict from FMP REST API.
            ingestion_start_ns: nanosecond timestamp when the fetch started.
        
        Returns:
            A validated, frozen MarketSnapshot.
        """
        ingestion_latency_ms = (time.time_ns() - ingestion_start_ns) // 1_000_000

        return MarketSnapshot(
            ticker=raw["symbol"].upper(),
            exchange=raw.get("exchange", "UNKNOWN"),
            price=Decimal(str(raw["price"])),
            volume=int(raw["volume"]),
            exchange_timestamp=datetime.fromtimestamp(
                raw["timestamp"], tz=timezone.utc
            ),
            data_lineage=DataLineage(
                source=self.PROVIDER_NAME,
                ingestion_latency_ms=ingestion_latency_ms,
                raw_field_count=len(raw),
            ),
        )
