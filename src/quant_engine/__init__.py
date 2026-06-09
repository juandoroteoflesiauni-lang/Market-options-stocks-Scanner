from __future__ import annotations

import importlib
from typing import Any

_SUBMODULES = {
    "domain",
    "math",
    "engines",
}


def __getattr__(name: str) -> Any:
    if name in _SUBMODULES:
        return importlib.import_module(f".{name}", package=__name__)
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


def __dir__() -> list[str]:
    return sorted(list(_SUBMODULES))


__all__ = sorted(list(_SUBMODULES))
