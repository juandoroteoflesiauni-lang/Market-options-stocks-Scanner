"""Base agent with structured JSON invocation. # [TH][IM]"""

from __future__ import annotations

import json
import logging
import re
import time
from typing import TypeVar

from pydantic import BaseModel, ValidationError

from backend.services.ai_core.agent_manager import AgentManager

logger = logging.getLogger(__name__)

BaseModelT = TypeVar("BaseModelT", bound=BaseModel)

_JSON_FENCE_RE = re.compile(r"```(?:json)?\s*([\s\S]*?)\s*```", re.IGNORECASE)


class AgentDegraded(Exception):  # noqa: N818
    """Raised when agent invocation fails and caller should use quant fallback."""

    def __init__(self, reason: str) -> None:
        self.reason = reason
        super().__init__(reason)


def extract_json_payload(raw: str) -> dict[str, object]:
    """Extract JSON object from raw LLM text, stripping markdown fences."""
    text = raw.strip()
    match = _JSON_FENCE_RE.search(text)
    if match:
        text = match.group(1).strip()
    start = text.find("{")
    end = text.rfind("}")
    if start == -1 or end == -1 or end <= start:
        raise json.JSONDecodeError("No JSON object found", text, 0)
    return json.loads(text[start : end + 1])


class BaseAgent:
    """Thin domain agent: owns prompt + schema; delegates transport to AgentManager."""

    def __init__(
        self,
        agent_manager: AgentManager,
        *,
        agent_name: str,
        timeout_s: float = 3.0,
    ) -> None:
        self._manager = agent_manager
        self._agent_name = agent_name
        self._timeout_s = timeout_s

    async def _invoke_structured(
        self,
        *,
        user: str,
        schema: type[BaseModelT],
        json_hint: str,
    ) -> BaseModelT:
        """Call agent_manager, parse JSON, validate against schema."""
        import asyncio

        system_suffix = f"\n\nRespond ONLY with valid JSON matching this schema hint:\n{json_hint}"
        config = self._manager.agent_configs.get(self._agent_name)
        if config is None:
            raise AgentDegraded(f"Unknown agent: {self._agent_name}")

        enriched_user = f"{user}{system_suffix}"
        start = time.perf_counter()
        try:
            raw = await asyncio.wait_for(
                self._manager.invoke_agent(self._agent_name, enriched_user),
                timeout=self._timeout_s,
            )
            payload = extract_json_payload(raw)
            return schema.model_validate(payload)
        except (TimeoutError, json.JSONDecodeError, ValidationError, AgentDegraded) as exc:
            raise AgentDegraded(str(exc)) from exc
        except Exception as exc:
            logger.warning(
                "agent.invoke_failed agent=%s error=%s latency_ms=%.1f",
                self._agent_name,
                exc,
                (time.perf_counter() - start) * 1000.0,
            )
            raise AgentDegraded(str(exc)) from exc

    def _envelope(
        self,
        *,
        latency_ms: float,
        degraded: bool = False,
        fallback_reason: str | None = None,
    ) -> dict[str, object]:
        config = self._manager.agent_configs[self._agent_name]
        return {
            "agent_name": self._agent_name,
            "model": config.model,
            "provider": config.provider,
            "latency_ms": latency_ms,
            "degraded": degraded,
            "fallback_reason": fallback_reason,
        }


__all__ = ["AgentDegraded", "BaseAgent", "extract_json_payload"]
