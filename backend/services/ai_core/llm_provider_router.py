"""Runtime-mutable LLM provider router with per-provider circuit breakers. # [TH]"""

from __future__ import annotations

import logging
import os
from collections.abc import AsyncIterator

from backend.hub.circuit_breaker import CircuitBreaker
from backend.services.ai_core.providers.azure_openai import AzureOpenAIProvider
from backend.services.ai_core.providers.base import (
    LLMProvider,
    LLMRequest,
    ProviderFailure,
    ProviderName,
)
from backend.services.ai_core.providers.claude import ClaudeProvider
from backend.services.ai_core.providers.gemini import GeminiProvider
from backend.services.ai_core.providers.github_models import GitHubModelsProvider
from backend.services.ai_core.providers.groq import GroqProvider

logger = logging.getLogger(__name__)


class AllProvidersFailedError(RuntimeError):
    """Raised when every provider in the priority chain fails."""


def _parse_priority(env: dict[str, str]) -> list[ProviderName]:
    raw = (env.get("LLM_PROVIDER_PRIORITY", "") or "").strip()
    if not raw:
        return [
            ProviderName.GITHUB_MODELS,
            ProviderName.GEMINI,
            ProviderName.AZURE_OPENAI,
            ProviderName.CLAUDE,
            ProviderName.GROQ,
        ]
    order: list[ProviderName] = []
    for part in raw.split(","):
        name = part.strip().lower()
        try:
            order.append(ProviderName(name))
        except ValueError:
            logger.warning("llm_provider_router.unknown_provider name=%s", name)
    return order or [ProviderName.GEMINI]


def build_provider_registry(env: dict[str, str]) -> dict[ProviderName, LLMProvider]:
    """Construct all provider adapters from environment."""
    return {
        ProviderName.GITHUB_MODELS: GitHubModelsProvider(env),
        ProviderName.GEMINI: GeminiProvider(env),
        ProviderName.AZURE_OPENAI: AzureOpenAIProvider(env),
        ProviderName.CLAUDE: ClaudeProvider(env),
        ProviderName.GROQ: GroqProvider(env),
    }


class LLMProviderRouter:
    """Hot-swappable provider priority with circuit breaker gating."""

    def __init__(
        self,
        providers: dict[ProviderName, LLMProvider],
        *,
        priority: list[ProviderName] | None = None,
    ) -> None:
        self._providers = providers
        self._priority = list(priority or list(ProviderName))
        self._breakers = {
            name: CircuitBreaker(provider_name=name.value) for name in self._providers
        }

    @classmethod
    def from_env(cls, env: dict[str, str] | None = None) -> LLMProviderRouter:
        merged = {**os.environ, **(env or {})}
        registry = build_provider_registry(merged)
        return cls(registry, priority=_parse_priority(merged))

    def set_priority(self, order: list[ProviderName]) -> None:
        """Runtime-mutable provider order (hot-swap without restart)."""
        self._priority = list(order)

    def priority(self) -> list[ProviderName]:
        return list(self._priority)

    def _attempt_order(self, preferred: ProviderName) -> list[ProviderName]:
        seen: set[ProviderName] = set()
        ordered: list[ProviderName] = []
        for name in [preferred, *self._priority]:
            if name in seen:
                continue
            seen.add(name)
            ordered.append(name)
        return ordered

    async def generate(self, preferred: ProviderName, request: LLMRequest) -> str:
        """Generate with fallback across healthy providers."""
        errors: list[str] = []
        for name in self._attempt_order(preferred):
            provider = self._providers.get(name)
            if provider is None or not provider.is_available():
                continue
            breaker = self._breakers[name]
            if not breaker.can_execute():
                errors.append(f"{name.value}: circuit_open")
                continue
            try:
                result = await provider.generate(request)
                breaker.record_success()
                return result
            except ProviderFailure as exc:
                breaker.record_failure()
                errors.append(f"{name.value}: {exc}")
                if exc.rate_limit or exc.timeout:
                    logger.warning(
                        "llm_provider_router.fallback from=%s reason=%s",
                        name.value,
                        exc,
                    )
                    continue
                raise
        raise AllProvidersFailedError("; ".join(errors) or "no providers available")

    async def stream(self, preferred: ProviderName, request: LLMRequest) -> AsyncIterator[str]:
        """Stream chunks; falls back to single generate chunk per provider."""
        errors: list[str] = []
        for name in self._attempt_order(preferred):
            provider = self._providers.get(name)
            if provider is None or not provider.is_available():
                continue
            breaker = self._breakers[name]
            if not breaker.can_execute():
                errors.append(f"{name.value}: circuit_open")
                continue
            try:
                async for chunk in provider.stream(request):
                    yield chunk
                breaker.record_success()
                return
            except ProviderFailure as exc:
                breaker.record_failure()
                errors.append(f"{name.value}: {exc}")
                if exc.rate_limit or exc.timeout:
                    continue
                raise
        raise AllProvidersFailedError("; ".join(errors) or "no providers available")


__all__ = [
    "AllProvidersFailedError",
    "LLMProviderRouter",
    "build_provider_registry",
]
