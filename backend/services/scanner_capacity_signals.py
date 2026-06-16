from __future__ import annotations
from typing import Any
"""Fase 3: per-row capacity / liquidity signals (no extra market IO in v1)."""


import os

from backend.config.logger_setup import get_logger
from backend.domain.market_scanner_models import (
    VETO_ILLIQUID,
    MarketScannerRow,
    ScannerCapacitySignals,
    ScannerLiquidityTier,
)

logger = get_logger(__name__)

WARN_LOW_LIQUIDITY = "low_liquidity"
WARN_HIGH_SHORT_INTEREST = "high_short_interest"
WARN_CAPACITY_UNKNOWN = "capacity_unknown"


def capacity_enabled() -> bool:
    raw = os.getenv("SCANNER_CAPACITY_SIGNALS", "true").strip().lower()
    return raw in {"1", "true", "yes", "on"}


def _metric_float(signal: Any, key: str) -> float | None:
    if signal is None:
        return None
    metrics = getattr(signal, "metrics", None) or {}
    if not isinstance(metrics, dict):
        return None
    val = metrics.get(key)
    if isinstance(val, int | float):
        return float(val)
    return None


def _relative_volume_from_row(row: MarketScannerRow) -> float | None:
    for sig in row.signals.values():
        if not getattr(sig, "ok", False):
            continue
        rvol = _metric_float(sig, "relative_volume")
        if rvol is not None:
            return rvol
    audit = row.score_audit or {}
    for block in (audit.get("phase_a"), audit.get("metrics")):
        if isinstance(block, dict):
            rv = block.get("relative_volume")
            if isinstance(rv, int | float):
                return float(rv)
    return None


def _liquidity_tier(rvol: float | None) -> ScannerLiquidityTier:
    if rvol is None:
        return "unknown"
    if rvol >= 1.5:
        return "high"
    if rvol >= 0.5:
        return "normal"
    return "low"


def _rvol_score(rvol: float | None) -> float:
    if rvol is None:
        return 50.0
    if rvol >= 2.0:
        return 95.0
    if rvol >= 1.2:
        return 75.0
    if rvol >= 0.8:
        return 60.0
    if rvol >= 0.5:
        return 45.0
    return 25.0


def _adv_proxy_usd(row: MarketScannerRow, rvol: float | None) -> float | None:
    price = row.price
    if price is None or price <= 0:
        return None
    spark = row.sparkline
    if len(spark) < 2:
        return None
    # Proxy: mean absolute bar change * price as notional activity scale
    deltas = [abs(spark[i] - spark[i - 1]) for i in range(1, len(spark))]
    if not deltas:
        return None
    mean_delta = sum(deltas) / len(deltas)
    base_notional = mean_delta * price * 1_000_000
    if rvol is not None:
        base_notional *= max(0.2, rvol)
    return round(base_notional, 2)


def _adv_score(adv_usd: float | None) -> float:
    if adv_usd is None:
        return 50.0
    if adv_usd >= 50_000_000:
        return 95.0
    if adv_usd >= 10_000_000:
        return 80.0
    if adv_usd >= 1_000_000:
        return 60.0
    if adv_usd >= 100_000:
        return 40.0
    return 20.0


def _fundamentals_payload(row: MarketScannerRow) -> dict[str, Any]:
    mod = row.module_signals.get("fundamentals")
    if mod is None:
        return {}
    payload = getattr(mod, "payload", None) or {}
    if isinstance(payload, dict):
        return payload
    return {}


def _short_interest_pct(row: MarketScannerRow) -> float | None:
    payload = _fundamentals_payload(row)
    fund = payload.get("fundamentals") or payload.get("short_interest") or payload
    if not isinstance(fund, dict):
        fund = payload
    for key in ("short_interest_ratio", "shortPercentOfFloat", "short_interest_pct"):
        val = fund.get(key) if isinstance(fund, dict) else None
        if isinstance(val, int | float):
            pct = float(val)
            if pct <= 1.0:
                pct *= 100.0
            return min(100.0, max(0.0, pct))
    audit = row.score_audit or {}
    si = audit.get("short_interest_pct") or audit.get("fmp_short_interest")
    if isinstance(si, int | float):
        pct = float(si)
        if pct <= 1.0:
            pct *= 100.0
        return min(100.0, max(0.0, pct))
    return None


def _institutional_ownership_pct(row: MarketScannerRow) -> float | None:
    audit = row.score_audit or {}
    inst = audit.get("institutional_ownership_pct") or audit.get("fmp_institutional_holders")
    if isinstance(inst, int | float):
        pct = float(inst)
        if pct > 100 and pct > 1e6:
            return None
        if pct <= 1.0:
            pct *= 100.0
        return min(100.0, max(0.0, pct))
    return None


def _short_interest_penalty(si_pct: float | None) -> float:
    if si_pct is None:
        return 0.0
    if si_pct >= 25:
        return 80.0
    if si_pct >= 15:
        return 50.0
    if si_pct >= 10:
        return 25.0
    return 0.0


def compute_capacity_signals(row: MarketScannerRow) -> ScannerCapacitySignals:
    """Build capacity signals from fields already on the row."""
    warnings: list[str] = []
    rvol = _relative_volume_from_row(row)
    tier = _liquidity_tier(rvol)
    adv_usd = _adv_proxy_usd(row, rvol)
    si_pct = _short_interest_pct(row)
    inst_pct = _institutional_ownership_pct(row)

    if rvol is None and adv_usd is None:
        warnings.append(WARN_CAPACITY_UNKNOWN)
    if tier == "low" or VETO_ILLIQUID in row.vetoes:
        warnings.append(WARN_LOW_LIQUIDITY)
    if si_pct is not None and si_pct >= 15:
        warnings.append(WARN_HIGH_SHORT_INTEREST)

    rvol_s = _rvol_score(rvol)
    adv_s = _adv_score(adv_usd)
    si_pen = _short_interest_penalty(si_pct)
    capacity_raw = 0.45 * rvol_s + 0.35 * adv_s + 0.20 * (100.0 - si_pen)
    capacity_score = max(0.0, min(100.0, round(capacity_raw, 2)))

    if VETO_ILLIQUID in row.vetoes:
        capacity_score = min(capacity_score, 25.0)

    return ScannerCapacitySignals(
        capacity_score=capacity_score,
        relative_volume=round(rvol, 4) if rvol is not None else None,
        liquidity_tier=tier,
        estimated_adv_usd=adv_usd,
        short_interest_pct=round(si_pct, 2) if si_pct is not None else None,
        institutional_ownership_pct=round(inst_pct, 2) if inst_pct is not None else None,
        production_size_hint=None,
        warnings=warnings,
    )


def attach_capacity_signals(rows: list[MarketScannerRow]) -> None:
    for row in rows:
        row.capacity_signals = compute_capacity_signals(row)
    logger.info("Attached capacity signals to %d rows", len(rows))


def enrich_capacity_with_production_hint(rows: list[MarketScannerRow]) -> None:
    """Copy production_size_multiplier after risk stack (read-only hint)."""
    for row in rows:
        if row.capacity_signals is None:
            row.capacity_signals = compute_capacity_signals(row)
        if row.production_size_multiplier is not None:
            row.capacity_signals.production_size_hint = row.production_size_multiplier
