import time
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any

from backend.models.market_snapshot import DataLineage, MarketSnapshot


class FmpNormalizer:
    """Transforms FMP API raw response into a canonical MarketSnapshot."""

    PROVIDER_NAME: str = "fmp"

    def normalize(self, raw: dict[str, Any], ingestion_start_ns: int) -> MarketSnapshot:
        """Converts an FMP ticker response to MarketSnapshot.

        Args:
            raw: The raw dict from FMP REST API.
            ingestion_start_ns: nanosecond timestamp when the fetch started.

        Returns:
            A validated, frozen MarketSnapshot.
        """
        ingestion_latency_ms = (time.time_ns() - ingestion_start_ns) // 1_000_000

        raw_change = raw.get("changesPercentage")
        daily_change_pct = float(raw_change) if isinstance(raw_change, int | float) else 0.0

        raw_avg_vol = raw.get("avgVolume")
        avg_volume = (
            int(raw_avg_vol) if isinstance(raw_avg_vol, int | float) and raw_avg_vol > 0 else 0
        )

        return MarketSnapshot(
            ticker=raw["symbol"].upper(),
            exchange=raw.get("exchange", "UNKNOWN"),
            price=Decimal(str(raw["price"])),
            volume=int(raw["volume"]),
            exchange_timestamp=datetime.fromtimestamp(raw["timestamp"], tz=UTC),
            data_lineage=DataLineage(
                source=self.PROVIDER_NAME,
                ingestion_latency_ms=ingestion_latency_ms,
                raw_field_count=len(raw),
            ),
            daily_change_pct=daily_change_pct,
            avg_volume=avg_volume,
        )
