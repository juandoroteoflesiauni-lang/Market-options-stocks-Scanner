from __future__ import annotations
"""Bloque thesis Opciones / GEX desde snapshot del motor interno (options_router)."""


from backend.domain.thesis_v2 import ThesisBlock


def build_options_thesis_block_from_snapshot(symbol: str, snapshot: object | None) -> ThesisBlock:
    """Construye métricas desde `OptionsSnapshotResponse` (o vacío si no hay cadena)."""
    sym = symbol.upper().strip()
    if snapshot is None:
        return ThesisBlock(
            metrics={"symbol": sym},
            source="UNAVAILABLE",
            limitations=["Options snapshot not fetched."],
            confidence=0.0,
        )

    ok = bool(getattr(snapshot, "ok", False))
    if not ok:
        err = getattr(snapshot, "error", None) or "Option chain unavailable for thesis."
        return ThesisBlock(
            metrics={"symbol": sym, "ok": False},
            source="UNAVAILABLE",
            limitations=[str(err)],
            confidence=0.0,
        )

    data = snapshot.model_dump() if hasattr(snapshot, "model_dump") else {}
    chain = data.get("chain")
    if isinstance(chain, list) and len(chain) > 100:
        data = {**data, "chain": chain[:100], "_chain_truncated": True}

    spot = data.get("spot") or 0.0
    conf = 0.7 if isinstance(spot, int | float) and float(spot) > 0 else 0.4

    return ThesisBlock(
        metrics=data,
        source="OPTIONS_SNAPSHOT",
        limitations=[
            "Snapshot from options_snapshot_service (layer_3 opciones_gex + chain providers). "
            "Chain may be truncated in metrics payload.",
        ],
        confidence=min(1.0, conf),
    )
