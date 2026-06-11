"""Additive thesis snapshot contracts that wrap ThesisV2 metadata."""

from __future__ import annotations

import re
from datetime import datetime
from typing import Any
from uuid import UUID

from pydantic import Field, field_validator, model_validator

from backend.domain.strategy_models import _SafeBaseModel

ENGINE_VERSION = "qa-integrations-v0"
_SHA256_RE = re.compile(r"^[a-fA-F0-9]{64}$")


class ThesisSnapshot(_SafeBaseModel):
    """Reproducible metadata snapshot for a generated thesis."""

    snapshot_id: str
    symbol: str = Field(..., min_length=1)
    created_at: datetime
    engine_version: str = ENGINE_VERSION
    config_hash: str
    data_hash: str | None = None
    code_hash: str | None = None
    horizon: str = Field(..., min_length=1)
    market: str = Field(..., min_length=1)
    inputs: dict[str, Any] = Field(default_factory=dict)
    block_sources: dict[str, str] = Field(default_factory=dict)
    block_confidences: dict[str, float] = Field(default_factory=dict)
    limitations: list[str] = Field(default_factory=list)

    @field_validator("snapshot_id")
    @classmethod
    def _validate_snapshot_id(cls: type[ThesisSnapshot], value: str) -> str:
        if _SHA256_RE.fullmatch(value):
            return value
        try:
            parsed = UUID(value)
        except ValueError as exc:
            raise ValueError("snapshot_id must be uuid4 or sha256 hex") from exc
        if parsed.version != 4:
            raise ValueError("snapshot_id must be uuid4 or sha256 hex")
        return value

    @field_validator("config_hash", "data_hash", "code_hash")
    @classmethod
    def _validate_hashes(cls: type[ThesisSnapshot], value: str | None) -> str | None:
        if value is None:
            return value
        if not _SHA256_RE.fullmatch(value):
            raise ValueError("hash fields must be 64-character sha256 hex strings")
        return value.lower()

    @model_validator(mode="after")
    def _validate_block_confidences(self: ThesisSnapshot) -> ThesisSnapshot:
        invalid = {
            name: confidence
            for name, confidence in self.block_confidences.items()
            if confidence < 0.0 or confidence > 1.0
        }
        if invalid:
            raise ValueError(f"block_confidences must be between 0 and 1: {invalid}")
        return self
