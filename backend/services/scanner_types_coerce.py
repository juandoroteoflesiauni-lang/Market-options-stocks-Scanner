"""Coerce numpy / odd scalars to JSON-safe Python types for Pydantic scanner models."""

from __future__ import annotations

from typing import Any

import numpy as np


def json_scalar(value: object) -> float | str | bool | None:
    """Return a value safe for Pydantic ``float | str | bool | None`` union fields."""
    if value is None:
        return None
    if isinstance(value, np.generic):
        return json_scalar(value.item())
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, str):
        return value
    return str(value)


def scrub_metrics_dict(metrics: dict[str, Any]) -> dict[str, float | str | bool | None]:
    """Normalize scanner timeframe metrics (avoids np.bool_ / np.float64 Pydantic warnings)."""
    out: dict[str, float | str | bool | None] = {}
    for key, raw in metrics.items():
        out[str(key)] = json_scalar(raw)
    return out
