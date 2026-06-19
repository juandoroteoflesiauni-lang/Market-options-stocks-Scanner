"""Google Gemini provider adapter."""

from __future__ import annotations

import asyncio

import httpx

from backend.services.ai_core.providers.base import (
    BaseLLMProvider,
    LLMRequest,
    ProviderFailure,
    ProviderName,
)


class GeminiProvider(BaseLLMProvider):
    """Gemini generateContent API."""

    def __init__(self, env: dict[str, str]) -> None:
        self._env = env

    @property
    def name(self) -> ProviderName:
        return ProviderName.GEMINI

    def is_available(self) -> bool:
        return bool(self._api_keys())

    def _api_keys(self) -> list[str]:
        keys: list[str] = []
        primary = self._env.get("GEMINI_API_KEY", "").strip()
        if primary:
            keys.append(primary)
        for i in range(1, 10):
            key = self._env.get(f"GEMINI_API_KEY_{i}", "").strip()
            if key and key not in keys:
                keys.append(key)
        return keys

    def _resolve_model(self, request: LLMRequest) -> str:
        env_model = self._env.get("GEMINI_AGENT_MODEL", "").strip()
        return env_model or request.model

    @staticmethod
    def _text_from_response(data: dict[str, object]) -> str:
        feedback = data.get("promptFeedback")
        if isinstance(feedback, dict) and feedback.get("blockReason"):
            raise ProviderFailure(f"Gemini blocked: {feedback.get('blockReason')}")
        candidates = data.get("candidates")
        if not isinstance(candidates, list) or not candidates:
            raise ProviderFailure("Gemini empty candidates")
        content = candidates[0].get("content") if isinstance(candidates[0], dict) else None
        parts = content.get("parts") if isinstance(content, dict) else None
        if not isinstance(parts, list):
            raise ProviderFailure("Gemini invalid parts")
        text = "".join(
            p.get("text", "")
            for p in parts
            if isinstance(p, dict) and isinstance(p.get("text"), str)
        )
        if not text.strip():
            raise ProviderFailure("Gemini empty response")
        return text.strip()

    async def generate(self, request: LLMRequest) -> str:
        keys = self._api_keys()
        if not keys:
            raise ProviderFailure("GEMINI_API_KEY not configured")
        model = self._resolve_model(request)
        max_retries = 3
        base_delay = 2.0

        async with httpx.AsyncClient() as client:
            last_error: BaseException | None = None
            for api_key in keys:
                url = (
                    "https://generativelanguage.googleapis.com/v1beta/models/"
                    f"{model}:generateContent?key={api_key}"
                )
                payload = {
                    "systemInstruction": {"parts": [{"text": request.system_prompt}]},
                    "contents": [{"role": "user", "parts": [{"text": request.user_prompt}]}],
                    "generationConfig": {"temperature": 0.2, "maxOutputTokens": 800},
                }
                for attempt in range(max_retries):
                    try:
                        response = await client.post(url, json=payload, timeout=90.0)
                        response.raise_for_status()
                        data = response.json()
                        if not isinstance(data, dict):
                            raise ProviderFailure("Gemini invalid JSON")
                        return self._text_from_response(data)
                    except httpx.HTTPStatusError as exc:
                        last_error = exc
                        code = exc.response.status_code
                        if code == 404:
                            raise ProviderFailure(f"Gemini model not found: {model}") from exc
                        if code in (401, 403) and api_key != keys[-1]:
                            break
                        if code == 429 and attempt < max_retries - 1:
                            await asyncio.sleep(base_delay * (2**attempt))
                            continue
                        if code == 429 and api_key != keys[-1]:
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
            raise ProviderFailure("Gemini failed after retries", rate_limit=True)


__all__ = ["GeminiProvider"]
