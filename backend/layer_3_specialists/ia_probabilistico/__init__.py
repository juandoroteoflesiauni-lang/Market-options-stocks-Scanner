"""
Sector IA / Probabilístico
════════════════════════════════════════════════════════════════════════════════
Specialized layer for Multimodal AI, Tail Risk, and Probabilistic Modeling.
"""

from __future__ import annotations

from . import engines as _engines
from .domain import *  # noqa: F403

_DOMAIN_EXPORTS = {name for name in globals() if not name.startswith("_") and name != "annotations"}

__all__ = sorted(_DOMAIN_EXPORTS)


def __getattr__(name: str) -> object:
    if name in _engines.__all__:
        value = getattr(_engines, name)
        globals()[name] = value
        return value
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


def __dir__() -> list[str]:
    return sorted(set(globals()) | set(_engines.__all__))
