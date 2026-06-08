from __future__ import annotations

import importlib
from typing import Any

_SUBMODULES = {
    "cor3m",
    "fvg_engine",
    "hmm_engine",
    "lob_engine",
    "matrix_processor",
    "microstructure_engine",
    "ofi_engine",
    "smc_engine",
    "smc_fractal_engine",
    "squeeze_ignition",
    "tpo_engine",
    "volume_node_engine",
    "volume_oi",
    "vpoc_engine",
    "vsa_engine",
    "vsa_footprint_engine",
    "vsa_forecast",
    "vwap_engine",
}


def __getattr__(name: str) -> Any:
    if name in _SUBMODULES:
        return importlib.import_module(f".{name}", package=__name__)
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


def __dir__() -> list[str]:
    return sorted(list(_SUBMODULES))


__all__ = sorted(list(_SUBMODULES))
