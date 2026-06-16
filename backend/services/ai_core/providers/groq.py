"""Groq OpenAI-compatible provider."""

from __future__ import annotations

import asyncio

import httpx

from backend.services.ai_core.providers.base import (
    BaseLLMProvider,
    LLMRequest,
    ProviderFailure,
    ProviderName,
)


class GroqProvider(BaseLLMProvider):
    """Groq chat completions (OpenAI-compatible)."""

    def __init__(self, env: dict[str, str]) -> None:
        self._env = env
        self._api_key = env.get("GROQ_API_KEY", "").strip()
        self._default_model = env.get("GROQ_MODEL", "llama-3.3-70b-versatile").strip()

    @property
    def name(self) -> ProviderName:
        return ProviderName.GROQ

    def is_available(self) -> bool:
        return bool(self._api_key)

    async def generate(self, request: LLMRequest) -> str:
        if not self._api_key:
            raise ProviderFailure("GROQ_API_KEY not configured")
        model = request.model
        if model in {"azure-deployment", "gemini-2.0-flash"}:
            model = self._default_model
        url = "https://api.groq.com/openai/v1/chat/completions"
        headers = {
            "Authorization": f"Bearer {self._api_key}",
            "Content-Type": "application/json",
        }
        payload = {
            "model": model,
            "messages": [
                {"role": "system", "content": request.system_prompt},
                {"role": "user", "content": request.user_prompt},
            ],
            "temperature": 0.2,
            "max_tokens": 800,
        }
        max_retries = 3
        base_delay = 2.0

        async with httpx.AsyncClient() as client:
            last_error: BaseException | None = None
            for attempt in range(max_retries):
                try:
                    response = await client.post(url, headers=headers, json=payload, timeout=60.0)
                    response.raise_for_status()
                    data = response.json()
                    choices = data.get("choices")
                    if not isinstance(choices, list) or not choices:
                        raise ProviderFailure("Groq empty choices")
                    content = choices[0].get("message", {}).get("content")
                    if not isinstance(content, str) or not content.strip():
                        raise ProviderFailure("Groq empty content")
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
            raise ProviderFailure("Groq failed after retries")


__all__ = ["GroqProvider"]
