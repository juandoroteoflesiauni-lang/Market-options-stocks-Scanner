from __future__ import annotations
from typing import Any
"""Generate reproducible metadata snapshots for ThesisV2 outputs."""


import hashlib
import json
import re
from collections.abc import Callable
from datetime import UTC, date, datetime

from pydantic import BaseModel

from backend.domain.thesis_extensions import ENGINE_VERSION, ThesisSnapshot
from backend.domain.thesis_v2 import ThesisBlock, ThesisV2

_SENSITIVE_TEXT_RE = re.compile(
    r"(?i)((api[_-]?key|password|token|secret)\s*[:=]\s*[^,\s]+|sk_(live|test)_[a-z0-9]+)"
)
_MAX_TEXT_LEN = 512
_MAX_DEPTH = 8


class ThesisSnapshotService:
    """Builds sanitized, hash-based snapshots without storing raw LLM payloads."""

    def __init__(self: ThesisSnapshotService, now: Callable[[], datetime] | None = None) -> None:
        self._now = now or (lambda: datetime.now(UTC))

    def generate_snapshot(
        self: ThesisSnapshotService,
        thesis: ThesisV2,
        symbol: str,
        horizon: str,
        market: str,
        inputs: dict[str, Any],
    ) -> ThesisSnapshot:
        """Create a ThesisSnapshot and degrade gracefully when metadata is sparse."""
        limitations: list[str] = []
        sanitized_inputs = self._sanitize(inputs or {}, limitations)
        config_payload = (
            sanitized_inputs.get("config", {}) if isinstance(sanitized_inputs, dict) else {}
        )
        data_payload = (
            sanitized_inputs.get("data")
            if isinstance(sanitized_inputs, dict) and "data" in sanitized_inputs
            else sanitized_inputs
        )
        block_sources = self._extract_block_sources(thesis)
        block_confidences = self._extract_block_confidences(thesis)

        if not block_sources:
            limitations.append("No block sources could be extracted from ThesisV2.")
        if not block_confidences:
            limitations.append("No block confidences could be extracted from ThesisV2.")

        for name, block in self._iter_blocks(thesis).items():
            if block.limitations:
                limitations.extend(f"{name}: {item}" for item in block.limitations[:3])

        config_hash = self._compute_config_hash(config_payload)
        data_hash = self._compute_hash(data_payload) if data_payload else None
        code_hash = self._compute_hash({"engine_version": ENGINE_VERSION})
        snapshot_id = self._compute_hash(
            {
                "symbol": symbol.upper().strip(),
                "horizon": horizon,
                "market": market,
                "engine_version": ENGINE_VERSION,
                "config_hash": config_hash,
                "data_hash": data_hash,
                "block_sources": block_sources,
                "block_confidences": block_confidences,
            }
        )

        return ThesisSnapshot(
            snapshot_id=snapshot_id,
            symbol=symbol.upper().strip(),
            created_at=self._now(),
            engine_version=ENGINE_VERSION,
            config_hash=config_hash,
            data_hash=data_hash,
            code_hash=code_hash,
            horizon=horizon,
            market=market,
            inputs=sanitized_inputs if isinstance(sanitized_inputs, dict) else {},
            block_sources=block_sources,
            block_confidences=block_confidences,
            limitations=list(dict.fromkeys(limitations)),
        )

    def _compute_config_hash(self: ThesisSnapshotService, config: dict[str, Any]) -> str:
        """Return a deterministic sha256 hash for sanitized engine config."""
        return self._compute_hash(self._sanitize(config, []))

    def _extract_block_sources(self: ThesisSnapshotService, thesis: ThesisV2) -> dict[str, str]:
        """Extract each ThesisV2 block source without raising on partial data."""
        return {
            name: block.source
            for name, block in self._iter_blocks(thesis).items()
            if isinstance(block.source, str) and block.source
        }

    def _extract_block_confidences(
        self: ThesisSnapshotService, thesis: ThesisV2
    ) -> dict[str, float]:
        """Extract each ThesisV2 block confidence clamped to the contract range."""
        confidences: dict[str, float] = {}
        for name, block in self._iter_blocks(thesis).items():
            try:
                confidences[name] = max(0.0, min(1.0, float(block.confidence)))
            except (TypeError, ValueError):
                continue
        return confidences

    def _compute_hash(self: ThesisSnapshotService, payload: object) -> str:
        raw = json.dumps(
            payload,
            sort_keys=True,
            default=str,
            ensure_ascii=True,
            separators=(",", ":"),
        )
        return hashlib.sha256(raw.encode("utf-8")).hexdigest()

    def _iter_blocks(self: ThesisSnapshotService, thesis: ThesisV2) -> dict[str, ThesisBlock]:
        blocks: dict[str, ThesisBlock] = {}
        for name in (
            "opciones",
            "tecnico",
            "fundamental",
            "probabilistico",
            "agentes",
            "ejecutivo",
        ):
            block = getattr(thesis, name, None)
            if isinstance(block, ThesisBlock):
                blocks[name] = block
        return blocks

    def _sanitize(
        self: ThesisSnapshotService,
        value: object,
        limitations: list[str],
        depth: int = 0,
    ) -> object:
        if depth > _MAX_DEPTH:
            limitations.append("Input snapshot truncated because nesting was too deep.")
            return "[TRUNCATED]"
        if value is None or isinstance(value, bool | int | float):
            return value
        if isinstance(value, str):
            if _SENSITIVE_TEXT_RE.search(value):
                limitations.append("Secret-like input text was redacted before snapshot storage.")
                return "[REDACTED]"
            if len(value) > _MAX_TEXT_LEN:
                limitations.append("Long input text was truncated before snapshot storage.")
                return f"{value[:_MAX_TEXT_LEN]}...[TRUNCATED]"
            return value
        if isinstance(value, datetime | date):
            return value.isoformat()
        if isinstance(value, BaseModel):
            return self._sanitize(value.model_dump(mode="json"), limitations, depth + 1)
        if isinstance(value, dict):
            sanitized: dict[str, Any] = {}
            for key, child in value.items():
                safe_key = str(self._sanitize(str(key), limitations, depth + 1))
                sanitized[safe_key] = self._sanitize(child, limitations, depth + 1)
            return sanitized
        if isinstance(value, list | tuple | set | frozenset):
            return [self._sanitize(item, limitations, depth + 1) for item in value]
        text = str(value)
        return self._sanitize(text, limitations, depth + 1)
