"""Default LLM bridge for transcript analysis (Layer 4) used from application services."""

from __future__ import annotations

from backend.config.logger_setup import get_logger
from backend.layer_4_orchestration.ai_core.agent_manager import AgentManager

logger = get_logger(__name__)

_agent_manager: AgentManager | None = None


async def default_transcript_agent_invoke(agent_name: str, user_prompt: str) -> str:
    """Invoke a named agent via ``AgentManager`` (public ``invoke_agent`` API)."""
    global _agent_manager
    if _agent_manager is None:
        _agent_manager = AgentManager()
    try:
        return await _agent_manager.invoke_agent(agent_name, user_prompt)
    except Exception as exc:
        logger.error("transcript_llm_bridge.invoke_failed agent=%s err=%s", agent_name, exc)
        raise
