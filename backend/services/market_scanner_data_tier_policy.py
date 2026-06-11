"""Data-tier policy: scale effective weights by indicator audit status (real/partial/proxy)."""

from __future__ import annotations

import os
from typing import Any

from backend.config.logger_setup import get_logger
from backend.domain.market_scanner_models import ScannerIndicatorDefinition, ScannerIndicatorStatus
from backend.services.market_scanner_indicator_catalog import INDICATOR_AUDIT_STATUS
from backend.services.scanner_indicator_tier_registry import resolve_indicator_tier

logger = get_logger(__name__)

_SCAN_TOKEN = "global"

DATA_TIER_MULTIPLIERS: dict[ScannerIndicatorStatus, float] = {
    "real": 1.0,
    "partial": 0.85,
    "proxy": 0.55,
    "not_connected": 0.0,
}

DEFAULT_PROXY_MULTIPLIER = 0.55
DEFAULT_PARTIAL_MULTIPLIER = 0.85


def data_tier_policy_enabled() -> bool:
    raw = os.getenv("SCANNER_DATA_TIER_POLICY", "true").strip().lower()
    return raw in {"1", "true", "yes", "on"}


def indicator_data_tier(key: str, *, scan_token: str = _SCAN_TOKEN) -> ScannerIndicatorStatus:
    catalog = INDICATOR_AUDIT_STATUS.get(key)
    catalog_tier = catalog[0] if catalog else "proxy"
    tier, _source = resolve_indicator_tier(key, catalog_tier, scan_token=scan_token)
    return tier


def indicator_data_tier_with_source(
    key: str,
    *,
    scan_token: str = _SCAN_TOKEN,
) -> tuple[ScannerIndicatorStatus, str | None]:
    catalog = INDICATOR_AUDIT_STATUS.get(key)
    catalog_tier = catalog[0] if catalog else "proxy"
    tier, source = resolve_indicator_tier(key, catalog_tier, scan_token=scan_token)
    return tier, source


def tier_multiplier(status: ScannerIndicatorStatus) -> float:
    return DATA_TIER_MULTIPLIERS.get(status, DEFAULT_PROXY_MULTIPLIER)


def apply_data_tier_to_effective_weights(
    effective_weights: dict[str, dict[str, float]],
    indicators: list[ScannerIndicatorDefinition] | None = None,
) -> tuple[dict[str, dict[str, float]], dict[str, Any]]:
    """Down-weight proxy/partial indicators when institutional data-tier policy is on."""
    if not data_tier_policy_enabled():
        return effective_weights, {"enabled": False}

    scaled: dict[str, dict[str, float]] = {}
    by_tier: dict[str, int] = {"real": 0, "partial": 0, "proxy": 0, "not_connected": 0}
    adjusted_keys: list[str] = []

    for key, tf_map in effective_weights.items():
        status = indicator_data_tier(key)
        by_tier[status] = by_tier.get(status, 0) + 1
        mult = tier_multiplier(status)
        if mult <= 0:
            continue
        new_map = {tf: round(w * mult, 4) for tf, w in tf_map.items() if w > 0}
        if new_map:
            scaled[key] = new_map
        if mult < 1.0:
            adjusted_keys.append(key)

    audit: dict[str, Any] = {
        "enabled": True,
        "multipliers": dict(DATA_TIER_MULTIPLIERS),
        "indicators_by_tier": by_tier,
        "downweighted_indicator_count": len(adjusted_keys),
        "downweighted_indicators": sorted(adjusted_keys)[:24],
    }
    if adjusted_keys:
        logger.debug(
            "market_scanner.data_tier_policy applied downweights count=%s",
            len(adjusted_keys),
        )
    return scaled, audit


def summarize_module_data_tiers(
    module_signals: dict[str, Any],
    indicators: list[ScannerIndicatorDefinition],
) -> dict[str, Any]:
    """Per-module proxy/real mix from enabled catalog indicators (desk transparency)."""
    by_module: dict[str, dict[str, int]] = {}
    for ind in indicators:
        if not ind.default_enabled and ind.key not in INDICATOR_AUDIT_STATUS:
            continue
        mod = str(ind.module)
        tier = indicator_data_tier(ind.key)
        bucket = by_module.setdefault(mod, {"real": 0, "partial": 0, "proxy": 0})
        bucket[tier] = bucket.get(tier, 0) + 1
    return {
        "catalog_mix_by_module": by_module,
        "modules_synthesized": sorted(module_signals.keys()) if module_signals else [],
    }
