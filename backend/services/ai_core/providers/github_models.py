"""GitHub Models provider adapter."""

from __future__ import annotations

import asyncio
import logging
from typing import Any

import httpx

from backend.services.ai_core.providers.base import (
    BaseLLMProvider,
    LLMRequest,
    ProviderFailure,
    ProviderName,
)

logger = logging.getLogger(__name__)


class GitHubModelsProvider(BaseLLMProvider):
    """GitHub Models chat completions with token pinning."""

    def __init__(self, env: dict[str, str]) -> None:
        self._env = env

    @property
    def name(self) -> ProviderName:
        return ProviderName.GITHUB_MODELS

    def is_available(self) -> bool:
        return bool(self._bearer_tokens())

    def _bearer_tokens(self) -> list[str]:
        tokens: list[str] = []
        primary = self._env.get("GITHUB_MODEL_TOKEN", "").strip()
        if primary:
            tokens.append(primary)
        for i in range(2, 11):
            key = self._env.get(f"GITHUB_MODEL_TOKEN_{i}", "").strip()
            if key and key not in tokens:
                tokens.append(key)
        return tokens

    def _tokens_for_request(self, request: LLMRequest) -> list[str]:
        all_tokens = self._bearer_tokens()
        if not all_tokens:
            raise ProviderFailure("GITHUB_MODEL_TOKEN not configured")
        idx = request.token_index % len(all_tokens)
        pinned = all_tokens[idx]
        fallback = [t for i, t in enumerate(all_tokens) if i != idx]
        return [pinned, *fallback]

    async def generate(self, request: LLMRequest) -> str:
        ordered_tokens = self._tokens_for_request(request)
        url = "https://models.inference.ai.azure.com/chat/completions"
        max_retries = 3
        base_delay = 2.0

        async with httpx.AsyncClient() as client:
            last_error: BaseException | None = None
            for bearer in ordered_tokens:
                headers = {
                    "Authorization": f"Bearer {bearer}",
                    "Content-Type": "application/json",
                }
                current_model = request.model
                for attempt in range(max_retries):
                    payload: dict[str, Any] = {
                        "model": current_model,
                        "messages": [
                            {"role": "system", "content": request.system_prompt},
                            {"role": "user", "content": request.user_prompt},
                        ],
                        "temperature": 0.2,
                        "max_tokens": 800,
                    }
                    try:
                        response = await client.post(
                            url, headers=headers, json=payload, timeout=45.0
                        )
                        response.raise_for_status()
                        data = response.json()
                        choices = data.get("choices", [])
                        if not isinstance(choices, list) or not choices:
                            raise ProviderFailure("GitHub Models empty choices")
                        content = choices[0].get("message", {}).get("content")
                        if not isinstance(content, str) or not content.strip():
                            raise ProviderFailure("GitHub Models empty content")
                        return content.strip()
                    except httpx.HTTPStatusError as exc:
                        last_error = exc
                        code = exc.response.status_code
                        if code == 429:
                            if attempt < max_retries - 1:
                                if (
                                    attempt == max_retries - 2
                                    and current_model != "DeepSeek-V3-0324"
                                ):
                                    current_model = "DeepSeek-V3-0324"
                                await asyncio.sleep(base_delay * (2**attempt))
                                continue
                            break
                        if code == 401:
                            break
                        raise ProviderFailure(str(exc), rate_limit=code == 429) from exc
                    except httpx.RequestError as exc:
                        last_error = exc
                        if attempt < max_retries - 1:
                            await asyncio.sleep(base_delay * (2**attempt))
                            continue
                        raise ProviderFailure(str(exc), timeout=True) from exc
            if last_error:
                raise ProviderFailure(str(last_error), rate_limit=True) from last_error
            raise ProviderFailure("GitHub Models failed after token rotation", rate_limit=True)


__all__ = ["GitHubModelsProvider"]
