"""Runtime indicator tier overrides when real tape/L2/options data is available."""

from __future__ import annotations

import threading
from typing import Any

from backend.domain.market_scanner_models import ScannerIndicatorSource, ScannerIndicatorStatus

_lock = threading.Lock()
# scan_id or "global" -> indicator_key -> (tier, source)
_overrides: dict[str, dict[str, tuple[ScannerIndicatorStatus, ScannerIndicatorSource]]] = {}


def clear_tier_overrides(scan_token: str = "global") -> None:
    with _lock:
        _overrides.pop(scan_token, None)


def register_indicator_tier(
    indicator_key: str,
    tier: ScannerIndicatorStatus,
    source: ScannerIndicatorSource,
    *,
    scan_token: str = "global",
) -> None:
    with _lock:
        bucket = _overrides.setdefault(scan_token, {})
        bucket[indicator_key] = (tier, source)


def register_microstructure_tiers(
    *,
    vpin_ok: bool,
    order_flow_ok: bool,
    volume_profile_ok: bool,
    scan_token: str = "global",
) -> dict[str, Any]:
    """Promote VPIN / OFD / volume profile when BingX real data succeeded."""
    audit: dict[str, Any] = {"promoted": []}
    if vpin_ok:
        register_indicator_tier("vpin", "real", "bingx_trade", scan_token=scan_token)
        audit["promoted"].append("vpin:real:bingx_trade")
    if order_flow_ok:
        register_indicator_tier("order_flow_delta", "real", "bingx_trade", scan_token=scan_token)
        audit["promoted"].append("order_flow_delta:real:bingx_trade")
    if volume_profile_ok:
        register_indicator_tier(
            "volume_profile",
            "partial",
            "bingx_l2",
            scan_token=scan_token,
        )
        audit["promoted"].append("volume_profile:partial:bingx_l2")
    return audit


def register_gex_tier(
    source: ScannerIndicatorSource,
    *,
    tier: ScannerIndicatorStatus = "real",
    scan_token: str = "global",
) -> None:
    for key in ("net_gex", "dealer_bias", "gamma_flip", "squeeze_probability", "flow_signal"):
        register_indicator_tier(key, tier, source, scan_token=scan_token)


def resolve_indicator_tier(
    indicator_key: str,
    catalog_tier: ScannerIndicatorStatus,
    *,
    scan_token: str = "global",
) -> tuple[ScannerIndicatorStatus, ScannerIndicatorSource | None]:
    with _lock:
        entry = _overrides.get(scan_token, {}).get(indicator_key)
    if entry:
        return entry[0], entry[1]
    return catalog_tier, None


def build_source_attribution(
    scan_token: str = "global",
) -> dict[str, dict[str, str]]:
    """Per-indicator {tier, source} for API transparency."""
    with _lock:
        bucket = dict(_overrides.get(scan_token, {}))
    out: dict[str, dict[str, str]] = {}
    for key, (tier, source) in bucket.items():
        out[key] = {"tier": tier, "source": source}
    return out
