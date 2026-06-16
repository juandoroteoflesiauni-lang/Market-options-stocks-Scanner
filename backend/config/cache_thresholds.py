"""Cache TTL and bucket thresholds for LLM context memoization. # [PD-8]"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field


class CacheThresholds(BaseModel):
    """Centralized cache timing — no magic numbers in services."""

    model_config = ConfigDict(frozen=True)

    llm_context_bucket_seconds: int = Field(default=300, ge=60, le=3600)
    llm_context_ttl_seconds: int = Field(default=300, ge=60, le=3600)
    llm_context_redis_ttl_seconds: int = Field(default=300, ge=60, le=3600)


DEFAULT_CACHE_THRESHOLDS = CacheThresholds()

__all__ = ["DEFAULT_CACHE_THRESHOLDS", "CacheThresholds"]
