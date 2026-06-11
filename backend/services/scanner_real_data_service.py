"""Orchestrate real BingX tape/L2 and options routing for Market Scanner (Point 1)."""

from __future__ import annotations

import asyncio
import os
from typing import Any

from backend.config.logger_setup import get_logger
from backend.domain.market_scanner_models import MarketScannerRow
from backend.layer_1_data.datos.bingx_client import BingXClient
from backend.layer_1_data.datos.bingx_trade_adapter import build_microstructure_bundle
from backend.layer_1_data.datos.deribit_gex_adapter import build_gex_payload_from_deribit
from backend.services.scanner_indicator_tier_registry import (
    build_source_attribution,
    clear_tier_overrides,
    register_gex_tier,
    register_microstructure_tiers,
)
from backend.services.scanner_institutional_overlay import merge_microstructure_from_bingx_bundle
from backend.services.scanner_symbol_routing import (
    bingx_venue_symbol,
    deribit_currency,
    instrument_data_class,
    normalize_scanner_symbol,
    options_chain_symbol,
)

logger = get_logger(__name__)

_SCAN_TOKEN = "global"
_DEFAULT_TRADE_LIMIT = 120
_DEFAULT_CONCURRENCY = 6


def scanner_real_data_enabled() -> bool:
    raw = os.getenv("SCANNER_REAL_DATA", "true").strip().lower()
    return raw in {"1", "true", "yes", "on"}


async def fetch_bingx_microstructure(
    root: str,
    *,
    client: BingXClient | None = None,
    trade_limit: int = _DEFAULT_TRADE_LIMIT,
) -> dict[str, Any]:
    """Fetch trade tape + L2 for one symbol root."""
    venue = bingx_venue_symbol(root)
    if not venue:
        return {"ok": False, "reason": "missing_venue_symbol"}

    api = client or BingXClient()
    data_class = instrument_data_class(root)
    market_type = (
        "stock_perp"
        if data_class == "equity"
        else "crypto_standard" if data_class == "crypto" else None
    )

    try:
        trades_coro = api.fetch_recent_trades_perp(venue, limit=trade_limit)
        depth_coro = api.fetch_order_book_perp(venue, limit=50)
        raw_trades, depth = await asyncio.gather(trades_coro, depth_coro)
    except Exception as exc:
        logger.warning(
            "scanner_real_data.bingx_fetch_failed root=%s venue=%s error=%s",
            root,
            venue,
            str(exc)[:160],
        )
        return {"ok": False, "reason": "fetch_error", "venue_symbol": venue}

    bundle = build_microstructure_bundle(
        symbol=normalize_scanner_symbol(root),
        venue_symbol=venue,
        raw_trades=raw_trades,
        depth_payload=depth,
        market_type=market_type,
    )
    return {"ok": bundle.ok, "bundle": bundle.to_dict(), "reason": bundle.reason}


def apply_microstructure_to_row(row: MarketScannerRow, bundle_dict: dict[str, Any]) -> None:
    """Patch row metrics, overlay, and tier registry from a microstructure bundle."""
    if not bundle_dict.get("ok"):
        return

    row.source_attribution = dict(row.source_attribution or {})
    row.source_attribution["vpin"] = {"tier": "real", "source": "bingx_trade"}
    row.source_attribution["order_flow_delta"] = {"tier": "real", "source": "bingx_trade"}
    row.source_attribution["volume_profile"] = {"tier": "partial", "source": "bingx_l2"}

    register_microstructure_tiers(
        vpin_ok=True,
        order_flow_ok=True,
        volume_profile_ok=True,
        scan_token=_SCAN_TOKEN,
    )

    vpin = bundle_dict.get("vpin")
    imb = bundle_dict.get("volume_imbalance")
    cvd = bundle_dict.get("cvd")

    for signal in row.signals.values():
        if not signal.ok:
            continue
        metrics = dict(signal.metrics or {})
        if vpin is not None:
            metrics["vpin"] = vpin
            metrics["vpin_real"] = vpin
            metrics["vpin_proxy"] = vpin
        if imb is not None:
            metrics["volume_imbalance"] = imb
        if cvd is not None:
            metrics["order_flow_cvd"] = cvd
            metrics["order_flow_delta_real"] = bundle_dict.get("period_delta")
        metrics["microstructure_source"] = "bingx_trade_l2_v1"
        signal.metrics = metrics

    if row.deep_metrics is None:
        row.deep_metrics = {}
    row.deep_metrics["real_microstructure"] = bundle_dict
    row.deep_metrics["volume_profile_poc"] = bundle_dict.get("poc_price")
    row.deep_metrics["volume_profile_vah"] = bundle_dict.get("vah_price")
    row.deep_metrics["volume_profile_val"] = bundle_dict.get("val_price")

    row.institutional_overlay = merge_microstructure_from_bingx_bundle(
        row.institutional_overlay,
        bundle_dict,
    )

    audit = dict(row.score_audit or {})
    audit["real_microstructure"] = {
        "source": "bingx_trade_l2_v1",
        "venue_symbol": bundle_dict.get("venue_symbol"),
        "trade_count": bundle_dict.get("trade_count"),
        "method_vpin": bundle_dict.get("method_vpin"),
    }
    row.score_audit = audit


async def enrich_phase_b_rows_with_real_data(
    rows: list[MarketScannerRow],
    *,
    client: BingXClient | None = None,
    concurrency: int = _DEFAULT_CONCURRENCY,
) -> dict[str, Any]:
    """Batch-fetch BingX microstructure for Phase-B candidates."""
    if not scanner_real_data_enabled() or not rows:
        return {"enabled": False, "enriched": 0}

    clear_tier_overrides(_SCAN_TOKEN)
    sem = asyncio.Semaphore(max(1, concurrency))
    api = client or BingXClient()
    enriched = 0
    errors = 0

    async def _one(row: MarketScannerRow) -> None:
        nonlocal enriched, errors
        root = normalize_scanner_symbol(row.symbol)
        if instrument_data_class(root) == "other":
            return
        async with sem:
            result = await fetch_bingx_microstructure(root, client=api)
        if result.get("ok") and isinstance(result.get("bundle"), dict):
            apply_microstructure_to_row(row, result["bundle"])
            enriched += 1
        else:
            errors += 1

    await asyncio.gather(*[_one(row) for row in rows])
    summary = {
        "enabled": True,
        "enriched": enriched,
        "errors": errors,
        "source_attribution": build_source_attribution(_SCAN_TOKEN),
    }
    logger.info(
        "scanner_real_data.phase_b enriched=%s errors=%s rows=%s",
        enriched,
        errors,
        len(rows),
    )
    return summary


async def fetch_options_snapshot_routed(symbol: str) -> object | None:
    """Route options fetch: Massive for equities, Deribit for crypto."""
    root = normalize_scanner_symbol(symbol)
    currency = deribit_currency(root)

    if currency:
        payload = build_gex_payload_from_deribit(currency)
        if payload:
            register_gex_tier("deribit_options", scan_token=_SCAN_TOKEN)
            from backend.services.options_gex_feature_assembler import assemble_options_gex_features

            payload["options_gex_features"] = assemble_options_gex_features(payload)
            ff = payload["options_gex_features"]
            if isinstance(ff, dict):
                ff["provider"] = "deribit"
                ff["source_tier"] = "full_chain_gex"
                ff["data_quality_score"] = max(float(ff.get("data_quality_score") or 0.0), 0.78)
            return payload

    chain_sym = options_chain_symbol(root)
    snapshot = await _fetch_massive_options_snapshot(chain_sym)
    if snapshot is not None:
        register_gex_tier("massive_options", scan_token=_SCAN_TOKEN)
    return snapshot


async def _fetch_massive_options_snapshot(symbol: str) -> object | None:
    """Massive/Polygon options chain for equity underlyings (original ticker)."""
    try:
        from backend.layer_3_specialists.opciones_gex.chain_analytics_history import (
            OptionsChainAnalyticsHistoryStore,
        )
        from backend.routers.options_router import (
            options_chain_analytics_service,
            options_snapshot_service,
        )
        from backend.services.thesis_domain_narratives import get_risk_free_for_options_snapshot

        risk_free_rate = get_risk_free_for_options_snapshot()
        snapshot_result, analytics_result = await asyncio.gather(
            options_snapshot_service(symbol, None, risk_free_rate),
            options_chain_analytics_service(symbol, None, risk_free_rate),
            return_exceptions=True,
        )
        if isinstance(snapshot_result, Exception):
            raise snapshot_result

        if hasattr(snapshot_result, "model_dump"):
            payload = snapshot_result.model_dump(mode="json")
        elif isinstance(snapshot_result, dict):
            payload = dict(snapshot_result)
        else:
            payload = {}

        if not isinstance(analytics_result, Exception) and analytics_result is not None:
            payload["chain_analytics"] = (
                analytics_result.model_dump(mode="json")
                if hasattr(analytics_result, "model_dump")
                else analytics_result
            )
            history = OptionsChainAnalyticsHistoryStore().history_response(
                symbol, expiry=None, limit=12
            )
            payload["chain_analytics_history"] = history.model_dump(mode="json")
            from backend.services.options_gex_feature_assembler import assemble_options_gex_features

            payload["options_gex_features"] = assemble_options_gex_features(payload)
            feats = payload.get("options_gex_features")
            if isinstance(feats, dict):
                feats["provider"] = "massive"
                if feats.get("source_tier") == "full_chain_gex":
                    feats["data_quality_score"] = max(
                        float(feats.get("data_quality_score") or 0.0), 0.75
                    )
        return payload
    except Exception as exc:
        logger.warning(
            "scanner_real_data.massive_options_failed symbol=%s error=%s",
            symbol,
            str(exc)[:180],
        )
        return None


def real_data_scan_summary() -> dict[str, Any]:
    return {
        "enabled": scanner_real_data_enabled(),
        "source_attribution": build_source_attribution(_SCAN_TOKEN),
    }
