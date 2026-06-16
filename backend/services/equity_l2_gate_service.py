"""Gate L2 BingX → decisiones Alpaca (watchlist equities). # [PD-3][TH][IM]"""

from __future__ import annotations

import os
import time
from typing import Any

from pydantic import BaseModel, ConfigDict

from backend.config.logger_setup import get_logger
from backend.domain.alpaca_models import AlpacaDecision
from backend.layer_1_data.datos.equity_l2_watchlist_hub import is_watchlist_symbol
from backend.quant_engine.engines.technical.ofi_engine import OFIRegime
from backend.services.equity_l2_feed_service import equity_l2_feed_enabled, get_equity_l2_feed

logger = get_logger(__name__)

REASON_L2_NO_DATA = "equity_l2_no_data"
REASON_L2_STALE = "equity_l2_stale"
REASON_L2_SPOOFING_BID = "equity_l2_bid_spoofing"
REASON_L2_SPOOFING_ASK = "equity_l2_ask_spoofing"
REASON_L2_BEARISH_OFI = "equity_l2_bearish_ofi"
REASON_L2_PASSIVE_CANCEL_BID = "equity_l2_passive_cancel_bid_pressure"


class EquityL2GateConfig(BaseModel):
    """Umbrales del gate microestructura para entradas LONG."""

    model_config = ConfigDict(frozen=True)

    enabled: bool = True
    max_depth_age_s: float = 30.0
    ctr_block_threshold: float = 8.0
    block_on_missing_data: bool = False
    size_down_on_missing_data: bool = True

    @classmethod
    def from_env(cls) -> EquityL2GateConfig:
        enabled_raw = os.getenv("EQUITY_L2_GATE_ENABLED", "true").strip().lower()
        return cls(
            enabled=enabled_raw in {"1", "true", "yes", "on"},
            max_depth_age_s=float(os.getenv("EQUITY_L2_GATE_MAX_DEPTH_AGE_S", "30")),
            ctr_block_threshold=float(os.getenv("EQUITY_L2_GATE_CTR_BLOCK", "8.0")),
            block_on_missing_data=os.getenv("EQUITY_L2_GATE_BLOCK_MISSING", "false").lower()
            in {"1", "true", "yes"},
            size_down_on_missing_data=os.getenv("EQUITY_L2_GATE_SIZE_DOWN_MISSING", "true").lower()
            in {"1", "true", "yes"},
        )


def _is_finite_ctr(value: object) -> float | None:
    if value is None:
        return None
    try:
        out = float(value)
    except (TypeError, ValueError):
        return None
    if out != out or out < 0:  # NaN
        return None
    return out


def _depth_age_s(micro: dict[str, Any]) -> float | None:
    rt = micro.get("last_depth_at")
    if rt is None:
        return None
    try:
        return max(0.0, time.time() - float(rt))
    except (TypeError, ValueError):
        return None


def evaluate_equity_l2_gate(
    decision: AlpacaDecision,
    micro: dict[str, Any] | None,
    *,
    config: EquityL2GateConfig | None = None,
) -> tuple[AlpacaDecision, dict[str, Any]]:
    """Aplica gate L2 sobre un veredicto Alpaca (solo watchlist, LONG)."""
    cfg = config or EquityL2GateConfig.from_env()
    meta: dict[str, Any] = {
        "applied": False,
        "symbol": decision.symbol,
        "original_decision": decision.decision,
    }
    if not cfg.enabled or not equity_l2_feed_enabled():
        meta["skipped"] = "gate_disabled"
        return decision, meta
    if not is_watchlist_symbol(decision.symbol):
        meta["skipped"] = "not_in_watchlist"
        return decision, meta
    if decision.decision in {"BLOCK", "INSUFFICIENT_DATA"}:
        meta["skipped"] = "already_blocked"
        return decision, meta

    meta["applied"] = True
    reasons = list(decision.reason_codes)

    if not micro or not micro.get("ok"):
        if cfg.block_on_missing_data:
            return _downgrade(decision, "BLOCK", REASON_L2_NO_DATA, reasons, meta), meta
        if cfg.size_down_on_missing_data and decision.decision == "ALLOW":
            return _downgrade(decision, "SIZE_DOWN", REASON_L2_NO_DATA, reasons, meta), meta
        meta["verdict"] = "pass_missing_data"
        return decision, meta

    age = _depth_age_s(micro)
    meta["depth_age_s"] = age
    if age is not None and age > cfg.max_depth_age_s:
        reasons.append(REASON_L2_STALE)
        if decision.decision == "ALLOW":
            return _downgrade(decision, "SIZE_DOWN", REASON_L2_STALE, reasons, meta), meta

    ofi = micro.get("ofi") or {}
    regime = str(ofi.get("regime") or "")
    meta["ofi_regime"] = regime
    if regime == OFIRegime.STRONG_DISTRIBUTION.value:
        reasons.append(REASON_L2_BEARISH_OFI)
        return _downgrade(decision, "BLOCK", REASON_L2_BEARISH_OFI, reasons, meta), meta

    lob = micro.get("lob_stream") or {}
    spoofing = str(lob.get("spoofing_state") or "NORMAL")
    meta["spoofing_state"] = spoofing
    if spoofing == "BID_SPOOFING":
        reasons.append(REASON_L2_SPOOFING_BID)
        return _downgrade(decision, "BLOCK", REASON_L2_SPOOFING_BID, reasons, meta), meta
    if spoofing == "ASK_SPOOFING" and decision.decision == "ALLOW":
        reasons.append(REASON_L2_SPOOFING_ASK)
        return _downgrade(decision, "SIZE_DOWN", REASON_L2_SPOOFING_ASK, reasons, meta), meta

    passive = micro.get("passive_order_flow") or {}
    ctr_bid = _is_finite_ctr(passive.get("passive_cancel_pressure_bid") or lob.get("ctr_bid"))
    meta["ctr_bid"] = ctr_bid
    if ctr_bid is not None and ctr_bid >= cfg.ctr_block_threshold:
        reasons.append(REASON_L2_PASSIVE_CANCEL_BID)
        if decision.decision == "ALLOW":
            return (
                _downgrade(decision, "SIZE_DOWN", REASON_L2_PASSIVE_CANCEL_BID, reasons, meta),
                meta,
            )

    meta["verdict"] = "pass"
    return decision, meta


def apply_equity_l2_gate(decision: AlpacaDecision) -> AlpacaDecision:
    """Convenience: lee cache del feed y aplica gate."""
    micro = get_equity_l2_feed().get_microstructure(decision.symbol)
    gated, _meta = evaluate_equity_l2_gate(decision, micro)
    return gated


def _downgrade(
    decision: AlpacaDecision,
    new_decision: str,
    reason: str,
    reasons: list[str],
    meta: dict[str, Any],
) -> AlpacaDecision:
    if reason not in reasons:
        reasons.append(reason)
    meta["verdict"] = new_decision
    meta["reason"] = reason
    direction = "LONG" if new_decision != "BLOCK" else "FLAT"
    logger.info(
        "equity_l2_gate.downgrade symbol=%s %s→%s reason=%s",
        decision.symbol,
        decision.decision,
        new_decision,
        reason,
    )
    return decision.model_copy(
        update={
            "decision": new_decision,
            "direction": direction,
            "reason_codes": tuple(reasons),
        }
    )


__all__ = [
    "EquityL2GateConfig",
    "REASON_L2_BEARISH_OFI",
    "REASON_L2_NO_DATA",
    "REASON_L2_SPOOFING_BID",
    "apply_equity_l2_gate",
    "evaluate_equity_l2_gate",
]
