"""Domain contracts for executable strategy candidates and options payoff inputs."""

from __future__ import annotations

from datetime import date
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


class StrategyCandidate(BaseModel):
    """Executable strategy candidate derived from, but separated from, a thesis."""

    model_config = ConfigDict(frozen=True)

    strategy_id: str = Field(..., min_length=1)
    symbol: str = Field(..., min_length=1)
    source_thesis_snapshot_id: str = Field(..., min_length=1)
    direction: Literal["long", "short", "neutral", "pair", "options"]
    horizon: str = Field(..., min_length=1)
    entry_plan: dict[str, Any] = Field(default_factory=dict)
    invalidation: dict[str, Any] = Field(default_factory=dict)
    targets: list[dict[str, Any]] = Field(default_factory=list)
    sizing: dict[str, Any] = Field(default_factory=dict)
    constraints: dict[str, Any] = Field(default_factory=dict)
    confidence: float = Field(0.0, ge=0.0, le=1.0)
    limitations: list[str] = Field(default_factory=list)


class OptionLeg(BaseModel):
    """One options leg in a multi-leg strategy."""

    model_config = ConfigDict(frozen=True)

    symbol: str = Field(..., min_length=1)
    expiry: date
    strike: float = Field(..., gt=0.0)
    right: Literal["call", "put"]
    side: Literal["long", "short"]
    quantity: int = Field(..., gt=0)
    entry_price: float | None = Field(default=None, ge=0.0)
    iv: float | None = Field(default=None, ge=0.0)
    delta: float | None = None
    gamma: float | None = None
    theta: float | None = None
    vega: float | None = None
    multiplier: float = Field(100.0, gt=0.0)
    limitations: list[str] = Field(default_factory=list)


class OptionStrategy(BaseModel):
    """A complete options strategy, including all legs and the underlying context."""

    model_config = ConfigDict(frozen=True)

    underlying: str = Field(..., min_length=1)
    spot: float = Field(..., gt=0.0)
    legs: list[OptionLeg] = Field(..., min_length=1)
    underlying_quantity: float = 0.0
    created_from_thesis_snapshot_id: str | None = None
    limitations: list[str] = Field(default_factory=list)


class OptionPayoffScenario(BaseModel):
    """Scenario grid and market assumptions for repricing an option strategy."""

    model_config = ConfigDict(frozen=True)

    valuation_date: date | None = None
    spot_min: float = Field(..., gt=0.0)
    spot_max: float = Field(..., gt=0.0)
    steps: int = Field(100, ge=2)
    iv_shift: float = 0.0
    dte_shift_days: int = 0
    risk_free_rate: float
    dividend_yield: float = 0.0
    limitations: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def _check_spot_range(self: OptionPayoffScenario) -> OptionPayoffScenario:
        if self.spot_max <= self.spot_min:
            raise ValueError("spot_max must be greater than spot_min")
        return self


class PayoffCurve(BaseModel):
    """Computed payoff curve and aggregate risk measures for a strategy."""

    model_config = ConfigDict(frozen=True)

    points: list[dict[str, float]] = Field(default_factory=list)
    max_profit: float | None = None
    max_loss: float | None = None
    break_evens: list[float] = Field(default_factory=list)
    net_delta: float = 0.0
    net_gamma: float = 0.0
    net_theta: float = 0.0
    net_vega: float = 0.0
    limitations: list[str] = Field(default_factory=list)


class PortfolioFitBlock(BaseModel):
    """Portfolio-level fit for one or more strategy candidates."""

    model_config = ConfigDict(frozen=True)

    suggested_weights: dict[str, float] = Field(default_factory=dict)
    risk_parity_weights: dict[str, float] = Field(default_factory=dict)
    component_var: dict[str, float] = Field(default_factory=dict)
    risk_contribution: dict[str, float] = Field(default_factory=dict)
    diversification_ratio: float | None = Field(default=None, ge=0.0)
    gross_exposure: float | None = Field(default=None, ge=0.0)
    net_exposure: float | None = None
    portfolio_confidence: float = Field(0.0, ge=0.0, le=1.0)
    limitations: list[str] = Field(default_factory=list)
