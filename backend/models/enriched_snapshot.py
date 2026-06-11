from pydantic import ConfigDict, Field

from backend.models.market_snapshot import MarketSnapshot


class EnrichedSnapshot(MarketSnapshot):
    """Derived model containing microstructure enrichment (Phase B output)."""

    model_config = ConfigDict(frozen=True)

    ofi_score: float = Field(default=0.0, description="Order Flow Imbalance score")
    smc_direction: str | None = Field(
        default=None, description="BULL, BEAR or None from SMC engine"
    )
    smc_weight: float = Field(default=0.0, description="Weight/Confidence of the SMC signal")
