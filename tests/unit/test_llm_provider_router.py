"""Unit tests for LLMProviderRouter."""

from __future__ import annotations

import pytest

from backend.services.ai_core.llm_provider_router import LLMProviderRouter
from backend.services.ai_core.providers.base import LLMRequest, ProviderFailure, ProviderName
from backend.services.ai_core.providers.gemini import GeminiProvider
from backend.services.ai_core.providers.github_models import GitHubModelsProvider


class _FlakyProvider:
    def __init__(self, name: ProviderName, *, fail_rate_limit: bool = False) -> None:
        self._name = name
        self._fail = fail_rate_limit
        self.calls = 0

    @property
    def name(self) -> ProviderName:
        return self._name

    def is_available(self) -> bool:
        return True

    async def generate(self, request: LLMRequest) -> str:
        _ = request
        self.calls += 1
        if self._fail:
            raise ProviderFailure("429", rate_limit=True)
        return f"ok-{self._name.value}"


@pytest.mark.asyncio
async def test_router_primary_success() -> None:
    github = _FlakyProvider(ProviderName.GITHUB_MODELS)
    gemini = _FlakyProvider(ProviderName.GEMINI)
    router = LLMProviderRouter(
        {ProviderName.GITHUB_MODELS: github, ProviderName.GEMINI: gemini},
        priority=[ProviderName.GITHUB_MODELS, ProviderName.GEMINI],
    )
    req = LLMRequest("a", "m", "sys", "user")
    out = await router.generate(ProviderName.GITHUB_MODELS, req)
    assert out == "ok-github_models"
    assert github.calls == 1
    assert gemini.calls == 0


@pytest.mark.asyncio
async def test_router_fallback_on_rate_limit() -> None:
    github = _FlakyProvider(ProviderName.GITHUB_MODELS, fail_rate_limit=True)
    gemini = _FlakyProvider(ProviderName.GEMINI)
    router = LLMProviderRouter(
        {ProviderName.GITHUB_MODELS: github, ProviderName.GEMINI: gemini},
        priority=[ProviderName.GITHUB_MODELS, ProviderName.GEMINI],
    )
    req = LLMRequest("a", "m", "sys", "user")
    out = await router.generate(ProviderName.GITHUB_MODELS, req)
    assert out == "ok-gemini"
    assert github.calls == 1
    assert gemini.calls == 1


@pytest.mark.asyncio
async def test_set_priority_reorders() -> None:
    router = LLMProviderRouter.from_env({"LLM_PROVIDER_PRIORITY": "gemini,github_models"})
    assert router.priority()[0] == ProviderName.GEMINI
    router.set_priority([ProviderName.GITHUB_MODELS, ProviderName.GEMINI])
    assert router.priority()[0] == ProviderName.GITHUB_MODELS


def test_github_provider_unavailable_without_token() -> None:
    provider = GitHubModelsProvider({})
    assert provider.is_available() is False


def test_gemini_provider_unavailable_without_key() -> None:
    provider = GeminiProvider({})
    assert provider.is_available() is False
