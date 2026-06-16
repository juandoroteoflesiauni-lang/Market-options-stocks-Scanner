"""Diagnóstico del feed L2 equity (watchlist BingX)."""

from __future__ import annotations

from fastapi import APIRouter, HTTPException

from backend.domain.alpaca_models import AlpacaDecision
from backend.layer_1_data.datos.equity_l2_watchlist_hub import is_watchlist_symbol
from backend.services.equity_l2_feed_service import (
    equity_l2_feed_enabled,
    get_equity_l2_feed,
)
from backend.services.equity_l2_gate_service import evaluate_equity_l2_gate
from backend.services.scanner_symbol_routing import normalize_scanner_symbol

router = APIRouter(prefix="/api/v1/equity-l2", tags=["equity-l2"])


@router.get("/status")
async def get_equity_l2_status() -> dict[str, object]:
    """Estado agregado del feed L2 (modo stream, cache, counters)."""
    feed = get_equity_l2_feed()
    return feed.snapshot_status()


@router.get("/gate/{symbol}")
async def preview_equity_l2_gate(symbol: str) -> dict[str, object]:
    """Simula el gate L2 sobre un ALLOW sintético (diagnóstico)."""
    root = normalize_scanner_symbol(symbol)
    if not is_watchlist_symbol(root):
        raise HTTPException(status_code=404, detail=f"{root} not in equity L2 watchlist")
    micro = get_equity_l2_feed().get_microstructure(root)
    baseline = AlpacaDecision(
        symbol=root,
        decision="ALLOW",
        direction="LONG",
        score=0.75,
        probability=0.7,
        reason_codes=(),
    )
    gated, meta = evaluate_equity_l2_gate(baseline, micro)
    return {
        "symbol": root,
        "baseline_decision": baseline.decision,
        "gated_decision": gated.decision,
        "reason_codes": list(gated.reason_codes),
        "meta": meta,
        "has_microstructure": micro is not None,
    }


@router.get("/{symbol}")
async def get_equity_l2_symbol(symbol: str) -> dict[str, object]:
    """Microestructura cacheada para un root de la watchlist."""
    root = normalize_scanner_symbol(symbol)
    if not is_watchlist_symbol(root):
        raise HTTPException(status_code=404, detail=f"{root} not in equity L2 watchlist")
    micro = get_equity_l2_feed().get_microstructure(root)
    if micro is None:
        raise HTTPException(status_code=404, detail=f"No L2 cache for {root}")
    return {
        "enabled": equity_l2_feed_enabled(),
        "symbol": root,
        "microstructure": micro,
    }
