"""LLM provider protocol and shared types. # [TH]"""

from __future__ import annotations

from collections.abc import AsyncIterator
from dataclasses import dataclass
from enum import StrEnum
from typing import Protocol


class ProviderName(StrEnum):
    GITHUB_MODELS = "github_models"
    GEMINI = "gemini"
    AZURE_OPENAI = "azure_openai"
    CLAUDE = "claude"
    GROQ = "groq"


@dataclass(frozen=True)
class LLMRequest:
    """Normalized provider request."""

    agent_name: str
    model: str
    system_prompt: str
    user_prompt: str
    token_index: int = 0


class ProviderFailure(Exception):  # noqa: N818
    """Typed provider error for router fallback decisions."""

    def __init__(
        self,
        message: str,
        *,
        rate_limit: bool = False,
        timeout: bool = False,
    ) -> None:
        super().__init__(message)
        self.rate_limit = rate_limit
        self.timeout = timeout


class LLMProvider(Protocol):
    """Async LLM adapter."""

    @property
    def name(self) -> ProviderName: ...

    def is_available(self) -> bool: ...

    async def generate(self, request: LLMRequest) -> str: ...

    async def stream(self, request: LLMRequest) -> AsyncIterator[str]:
        """Default: single-chunk stream from generate()."""
        ...


class BaseLLMProvider:
    """Helper base with default stream implementation."""

    async def stream(self, request: LLMRequest) -> AsyncIterator[str]:
        yield await self.generate(request)


__all__ = [
    "BaseLLMProvider",
    "LLMProvider",
    "LLMRequest",
    "ProviderFailure",
    "ProviderName",
]
