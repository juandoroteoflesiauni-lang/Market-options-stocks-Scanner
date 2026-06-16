"""Anthropic Claude Messages API provider."""

from __future__ import annotations

import asyncio

import httpx

from backend.services.ai_core.providers.base import (
    BaseLLMProvider,
    LLMRequest,
    ProviderFailure,
    ProviderName,
)


class ClaudeProvider(BaseLLMProvider):
    """Claude via Anthropic Messages API."""

    def __init__(self, env: dict[str, str]) -> None:
        self._env = env
        self._api_key = env.get("ANTHROPIC_API_KEY", "").strip()
        self._default_model = env.get("CLAUDE_MODEL", "claude-sonnet-4-20250514").strip()

    @property
    def name(self) -> ProviderName:
        return ProviderName.CLAUDE

    def is_available(self) -> bool:
        return bool(self._api_key)

    async def generate(self, request: LLMRequest) -> str:
        if not self._api_key:
            raise ProviderFailure("ANTHROPIC_API_KEY not configured")
        model = request.model if request.model != "azure-deployment" else self._default_model
        if model == "azure-deployment":
            model = self._default_model
        url = "https://api.anthropic.com/v1/messages"
        headers = {
            "x-api-key": self._api_key,
            "anthropic-version": "2023-06-01",
            "content-type": "application/json",
        }
        payload = {
            "model": model,
            "max_tokens": 800,
            "system": request.system_prompt,
            "messages": [{"role": "user", "content": request.user_prompt}],
        }
        max_retries = 3
        base_delay = 2.0

        async with httpx.AsyncClient() as client:
            last_error: BaseException | None = None
            for attempt in range(max_retries):
                try:
                    response = await client.post(url, headers=headers, json=payload, timeout=90.0)
                    response.raise_for_status()
                    data = response.json()
                    content_blocks = data.get("content")
                    if not isinstance(content_blocks, list):
                        raise ProviderFailure("Claude invalid content")
                    text = "".join(
                        block.get("text", "")
                        for block in content_blocks
                        if isinstance(block, dict) and block.get("type") == "text"
                    )
                    if not text.strip():
                        raise ProviderFailure("Claude empty response")
                    return text.strip()
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
            raise ProviderFailure("Claude failed after retries")


__all__ = ["ClaudeProvider"]
