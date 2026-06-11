"""AI prompt helpers for probabilistic price targets."""

from __future__ import annotations

import os

from backend.services.ai_ready_payload import AIReadyPayloadEngine


def build_price_targets_evidence_prompt(
    symbol: str,
    current_price: float,
    horizons_data: list[dict[str, object]],
    engine_snapshot: dict[str, object],
) -> str:
    """Build a compact prompt from deterministic price-target evidence."""
    pack = AIReadyPayloadEngine().build_engine_pack(
        "price_targets",
        symbol,
        {
            "symbol": symbol,
            "current_price": current_price,
            "horizons": horizons_data,
            "engine_snapshot": engine_snapshot,
        },
    )
    max_chars = _int_env("PRICE_TARGETS_AI_MAX_PROMPT_CHARS", 1_600, minimum=800)
    return (
        f"SINTESIS QUANT - {symbol} @ ${current_price:.2f}\n"
        "Usa SOLO este evidence pack; no recalcules Monte Carlo, EVT, Heston ni MJD. "
        "Explica convergencia/divergencia de motores, riesgos e invalidaciones. "
        "Maximo 140 palabras, bullets concretos, espanol institucional.\n\n"
        f"PRICE_TARGETS_EVIDENCE_PACK:\n{pack.to_prompt_json(max_chars=max_chars)}"
    )


def _int_env(name: str, default: int, minimum: int) -> int:
    raw = os.environ.get(name, "").strip()
    if not raw:
        return default
    try:
        return max(minimum, int(raw))
    except ValueError:
        return default
