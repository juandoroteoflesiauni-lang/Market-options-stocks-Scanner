from decimal import Decimal

from pydantic import BaseModel, ConfigDict, Field


class GlobalContextSnapshot(BaseModel):
    """Immutable data model for global macro context and regime factors."""

    model_config = ConfigDict(frozen=True)

    vix_level: Decimal = Field(default=Decimal("0.0"))
    spy_trend: str = Field(default="NEUTRAL")
    qqq_trend: str = Field(default="NEUTRAL")
    spy_eem_trend: str = Field(default="NEUTRAL")
    qqq_iwm_trend: str = Field(default="NEUTRAL")
    fear_greed_index: int | None = Field(default=None)
    market_regime: str = Field(default="NORMAL")
    macro_conflict_score: Decimal = Field(default=Decimal("0.0"))
    regime_factor: Decimal = Field(default=Decimal("1.0"))
    global_factor: Decimal = Field(default=Decimal("1.0"))
    is_valid: bool = Field(default=True)
