"""Domain contracts for executable strategy candidates and options payoff inputs."""

from __future__ import annotations

import re
from collections.abc import Iterator
from datetime import date
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

_SENSITIVE_TEXT_RE = re.compile(
    r"(?i)((api[_-]?key|password|token|secret)\s*[:=]\s*[^,\s]+|sk_(live|test)_[a-z0-9]+)"
)


def _iter_strings(value: object, path: str = "$") -> Iterator[tuple[str, str]]:
    if isinstance(value, str):
        yield path, value
        return
    if isinstance(value, BaseModel):
        for key, child in value.__dict__.items():
            yield from _iter_strings(child, f"{path}.{key}")
        return
    if isinstance(value, dict):
        for key, child in value.items():
            yield from _iter_strings(key, f"{path}.<key>")
            yield from _iter_strings(child, f"{path}.{key}")
        return
    if isinstance(value, list | tuple | set | frozenset):
        for index, child in enumerate(value):
            yield from _iter_strings(child, f"{path}[{index}]")


class _SafeBaseModel(BaseModel):
    """Base contract that rejects secret-like text anywhere in the model tree."""

    model_config = ConfigDict(extra="forbid", frozen=False)

    @model_validator(mode="after")
    def _reject_secret_like_text(self: _SafeBaseModel) -> _SafeBaseModel:
        for path, text in _iter_strings(self):
            if _SENSITIVE_TEXT_RE.search(text):
                raise ValueError(f"secret-like text is not allowed at {path}")
        return self


class StrategyCandidate(_SafeBaseModel):
    """Executable strategy candidate derived from, but separated from, a thesis."""

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


class OptionLeg(_SafeBaseModel):
    """One options leg in a multi-leg strategy."""

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


class OptionStrategy(_SafeBaseModel):
    """A complete options strategy, including all legs and the underlying context."""

    underlying: str = Field(..., min_length=1)
    spot: float = Field(..., gt=0.0)
    legs: list[OptionLeg] = Field(..., min_length=1)
    underlying_quantity: float = 0.0
    created_from_thesis_snapshot_id: str | None = None
    limitations: list[str] = Field(default_factory=list)


class OptionPayoffScenario(_SafeBaseModel):
    """Scenario grid and market assumptions for repricing an option strategy."""

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


class PayoffCurve(_SafeBaseModel):
    """Computed payoff curve and aggregate risk measures for a strategy."""

    points: list[dict[str, float]] = Field(default_factory=list)
    max_profit: float | None = None
    max_loss: float | None = None
    break_evens: list[float] = Field(default_factory=list)
    net_delta: float = 0.0
    net_gamma: float = 0.0
    net_theta: float = 0.0
    net_vega: float = 0.0
    limitations: list[str] = Field(default_factory=list)


class PortfolioFitBlock(_SafeBaseModel):
    """Portfolio-level fit for one or more strategy candidates."""

    suggested_weights: dict[str, float] = Field(default_factory=dict)
    risk_parity_weights: dict[str, float] = Field(default_factory=dict)
    component_var: dict[str, float] = Field(default_factory=dict)
    risk_contribution: dict[str, float] = Field(default_factory=dict)
    diversification_ratio: float | None = Field(default=None, ge=0.0)
    gross_exposure: float | None = Field(default=None, ge=0.0)
    net_exposure: float | None = None
    portfolio_confidence: float = Field(0.0, ge=0.0, le=1.0)
    limitations: list[str] = Field(default_factory=list)
