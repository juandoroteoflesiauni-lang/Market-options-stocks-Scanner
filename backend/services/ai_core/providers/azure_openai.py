"""Azure OpenAI provider adapter."""

from __future__ import annotations

import asyncio
from typing import Any
from urllib.parse import urlparse

import httpx

from backend.services.ai_core.providers.base import (
    BaseLLMProvider,
    LLMRequest,
    ProviderFailure,
    ProviderName,
)


class AzureOpenAIProvider(BaseLLMProvider):
    """Azure OpenAI / Foundry chat completions."""

    def __init__(self, env: dict[str, str]) -> None:
        self._env = env
        self._endpoint = env.get("AZURE_OPENAI_ENDPOINT", "").strip().rstrip("/")
        self._api_key = env.get("AZURE_OPENAI_KEY", "").strip()
        self._api_version = env.get("AZURE_OPENAI_API_VERSION", "2024-12-01-preview").strip()

    @property
    def name(self) -> ProviderName:
        return ProviderName.AZURE_OPENAI

    def is_available(self) -> bool:
        return bool(self._endpoint and self._api_key and self._deployment())

    def _deployment(self) -> str:
        return (self._env.get("AZURE_OPENAI_DEPLOYMENT", "") or "").strip()

    def _uses_foundry_v1(self) -> bool:
        flag = (self._env.get("AZURE_OPENAI_USE_FOUNDRY_V1", "") or "").strip().lower()
        if flag in ("1", "true", "yes"):
            return True
        if flag in ("0", "false", "no"):
            return False
        if not self._endpoint:
            return False
        parsed = urlparse(self._endpoint)
        path = parsed.path or ""
        return "services.ai.azure.com" in (parsed.netloc or "") and "/api/projects" in path

    def _url_and_payload(self, deployment: str, request: LLMRequest) -> tuple[str, dict[str, Any]]:
        messages = [
            {"role": "system", "content": request.system_prompt},
            {"role": "user", "content": request.user_prompt},
        ]
        if self._uses_foundry_v1():
            parsed = urlparse(self._endpoint)
            origin = f"{parsed.scheme}://{parsed.netloc}"
            url = f"{origin}/openai/v1/chat/completions"
            payload: dict[str, Any] = {
                "model": deployment,
                "messages": messages,
                "temperature": 0.2,
                "max_tokens": 800,
            }
            return url, payload
        url = (
            f"{self._endpoint}/openai/deployments/{deployment}/chat/completions"
            f"?api-version={self._api_version}"
        )
        payload = {"messages": messages, "temperature": 0.2, "max_tokens": 800}
        return url, payload

    async def generate(self, request: LLMRequest) -> str:
        deployment = self._deployment()
        if not deployment:
            raise ProviderFailure("AZURE_OPENAI_DEPLOYMENT not configured")
        if not self._endpoint or not self._api_key:
            raise ProviderFailure("Azure OpenAI endpoint/key missing")

        url, payload = self._url_and_payload(deployment, request)
        headers = {"api-key": self._api_key, "Content-Type": "application/json"}
        max_retries = 3
        base_delay = 2.0

        async with httpx.AsyncClient() as client:
            last_error: BaseException | None = None
            for attempt in range(max_retries):
                try:
                    response = await client.post(url, headers=headers, json=payload, timeout=90.0)
                    response.raise_for_status()
                    data = response.json()
                    choices = data.get("choices")
                    if not isinstance(choices, list) or not choices:
                        raise ProviderFailure("Azure OpenAI empty choices")
                    content = choices[0].get("message", {}).get("content")
                    if not isinstance(content, str) or not content.strip():
                        raise ProviderFailure("Azure OpenAI empty content")
                    return content.strip()
                except httpx.HTTPStatusError as exc:
                    last_error = exc
                    code = exc.response.status_code
                    if code == 429 and attempt < max_retries - 1:
                        await asyncio.sleep(base_delay * (2**attempt))
                        continue
                    raise ProviderFailure(str(exc), rate_limit=code == 429) from exc
                except httpx.RequestError as exc:
                    last_error = exc
                    if attempt < max_retries - 1:
                        await asyncio.sleep(base_delay * (2**attempt))
                        continue
                    raise ProviderFailure(str(exc), timeout=True) from exc
            if last_error:
                raise ProviderFailure(str(last_error), rate_limit=True) from last_error
            raise ProviderFailure("Azure OpenAI failed after retries")


__all__ = ["AzureOpenAIProvider"]
