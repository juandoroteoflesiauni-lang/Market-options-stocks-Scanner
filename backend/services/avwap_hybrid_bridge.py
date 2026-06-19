"""Bridge AVWAP motors M13-M18 into BingX technical consensus payload."""

from __future__ import annotations

import os
from contextlib import suppress
from typing import Any

from backend.config.logger_setup import get_logger

logger = get_logger(__name__)

_MOTOR_KEYS = {
    13: "avwap_m13",
    14: "avwap_m14",
    15: "avwap_m15",
    16: "avwap_m16",
    17: "avwap_m17",
    18: "avwap_m18",
}


def _signal_to_block(signal: Any) -> dict[str, Any]:
    direction = str(getattr(signal, "direction", "FLAT")).upper()
    decision = (
        "LONG"
        if direction in ("LONG", "BULLISH", "BUY")
        else ("SHORT" if direction in ("SHORT", "BEARISH", "SELL") else "FLAT")
    )
    strength = float(getattr(signal, "strength", 0.0) or 0.0)
    return {
        "ok": decision != "FLAT",
        "decision": decision,
        "score": round(strength, 4),
        "rationale": str(getattr(signal, "rationale", "") or ""),
    }


def _neutral_blocks() -> dict[str, dict[str, Any]]:
    return {key: {"ok": False, "decision": "FLAT", "score": 0.0} for key in _MOTOR_KEYS.values()}


async def fetch_avwap_hybrid_blocks(
    underlying_symbol: str,
    *,
    close: float,
    volume: float,
) -> dict[str, dict[str, Any]]:
    """Run AVWAPEngine tick update and map signals to consensus blocks."""
    api_key = os.getenv("FMP_API_KEY", "").strip()
    if not api_key or close <= 0:
        return _neutral_blocks()

    try:
        from backend.quant_engine.engines.technical.avwap_hybrid.avwap_engine import AVWAPEngine
    except ImportError as exc:
        logger.warning("avwap_hybrid_bridge.import_failed error=%s", str(exc)[:120])
        return _neutral_blocks()

    engine = AVWAPEngine(fmp_api_key=api_key)
    blocks = _neutral_blocks()
    try:
        await engine.initialize_for_symbol(underlying_symbol)
        signals = await engine.update_tick(underlying_symbol, close, volume)
        latest: dict[int, Any] = {}
        for sig in signals:
            motor_id = int(getattr(sig, "motor_id", 0) or 0)
            if motor_id in _MOTOR_KEYS:
                latest[motor_id] = sig
        for motor_id, key in _MOTOR_KEYS.items():
            if motor_id in latest:
                blocks[key] = _signal_to_block(latest[motor_id])
        active = sum(1 for b in blocks.values() if b.get("ok"))
        logger.info(
            "avwap_hybrid_bridge.attached symbol=%s active=%d/6",
            underlying_symbol,
            active,
        )
    except Exception as exc:
        logger.warning(
            "avwap_hybrid_bridge.failed symbol=%s error=%s",
            underlying_symbol,
            str(exc)[:180],
        )
    finally:
        with suppress(Exception):
            await engine.close()
    return blocks
