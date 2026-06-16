"""Orquestacion modular de agentes LLM para QuantumAnalyzer.

Transporte LLM delegado a ``LLMProviderRouter`` (GitHub, Gemini, Azure, Claude, Groq).
"""

from __future__ import annotations

import asyncio
import os
from collections.abc import AsyncIterator, Awaitable, Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Literal

from backend.domain.agentic_models import AgentStreamEvent, StreamEventType
from backend.services.ai_core.llm_provider_router import AllProvidersFailedError, LLMProviderRouter
from backend.services.ai_core.providers.base import LLMRequest, ProviderName

LlmCallable = Callable[[str, str, str, str], Awaitable[str]]
AgentProvider = Literal["github_models", "gemini", "azure_openai"]


def _load_env_file(env_path: Path) -> dict[str, str]:
    """Carga variables .env sin dependencias externas."""
    parsed: dict[str, str] = {}
    if not env_path.exists():
        return parsed

    for raw_line in env_path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        parsed[key.strip()] = value.strip().strip('"').strip("'")
    return parsed


@dataclass(frozen=True)
class AgentConfig:
    name: str
    model: str
    provider: AgentProvider
    system_prompt: str
    token_index: int = 0


class AgentManager:
    """Administra agentes especialistas para producir un veredicto institucional."""

    def __init__(
        self,
        env_path: str = ".env",
        llm_callable: LlmCallable | None = None,
    ) -> None:
        self.env_path = Path(env_path)
        file_values = _load_env_file(self.env_path)
        self._env = {**file_values, **os.environ}
        self._custom_llm = llm_callable is not None
        self.llm_callable = llm_callable or self._default_llm_callable
        self.agent_configs = self._build_agent_configs()
        self._router = LLMProviderRouter.from_env(self._env)

    @staticmethod
    def _build_agent_configs() -> dict[str, AgentConfig]:
        """Define prompts y proveedores por agente."""
        return {
            "forensic": AgentConfig(
                name="forensic",
                model="DeepSeek-V3-0324",
                provider="github_models",
                token_index=0,
                system_prompt=(
                    "Eres un Auditor Forense institucional. Detecta anomalias contables, "
                    "riesgo de restatement y deterioro de calidad de earnings."
                ),
            ),
            "sentiment": AgentConfig(
                name="sentiment",
                model="grok-3",
                provider="github_models",
                token_index=1,
                system_prompt=(
                    "Eres un Analista Macroeconómico Algorítmico. Evalua narrativa, regimen "
                    "risk-on/risk-off y catalizadores macro de corto plazo."
                ),
            ),
            "orchestrator": AgentConfig(
                name="orchestrator",
                model="gpt-5-chat",
                provider="github_models",
                token_index=2,
                system_prompt=(
                    "Eres Portfolio Manager Jefe. Consolida forense, microestructura y sentimiento "
                    "en un veredicto unico con sesgo, conviccion, invalidaciones y riesgo."
                ),
            ),
            "microstructure": AgentConfig(
                name="microstructure",
                model="gemini-2.0-flash",
                provider="gemini",
                system_prompt=(
                    "Eres especialista en Microestructura de Mercado y SMC. Evalua order flow, "
                    "liquidez, absorcion, FVG y sesgo institucional."
                ),
            ),
            "options_gex": AgentConfig(
                name="options_gex",
                model="gemini-2.0-flash",
                provider="gemini",
                system_prompt=(
                    "Eres especialista en Derivados y Gamma Exposure (GEX). "
                    "Evalua cadenas de opciones, max pain, pin risk y zonas de dealer hedging."
                ),
            ),
            "technical": AgentConfig(
                name="technical",
                model="gemini-2.0-flash",
                provider="gemini",
                system_prompt=(
                    "Eres un Analista Técnico Cuantitativo. Identifica bloques de ordenes (OB), "
                    "BOS/CHOCH y estructura de mercado."
                ),
            ),
            "transcript_analyst": AgentConfig(
                name="transcript_analyst",
                model="gemini-2.0-flash",
                provider="gemini",
                system_prompt=(
                    "Eres un Analista de Equity Research experto en detección de patrones "
                    "de lenguaje. Analiza transcripts de earnings calls para detectar: "
                    "cambios de tono respecto a periodos previos, evasivas en el Q&A, "
                    "y temas recurrentes (Nube de palabras). Reporta con rigor institucional."
                ),
            ),
            "macro_micro": AgentConfig(
                name="macro_micro",
                model="azure-deployment",
                provider="azure_openai",
                system_prompt=(
                    "Eres un Analista Funcional de Mercado. Analiza calendarios economicos, "
                    "correlaciones intermercado y flujos de liquidez institucional."
                ),
            ),
            "argentina": AgentConfig(
                name="argentina",
                model="azure-deployment",
                provider="azure_openai",
                system_prompt=(
                    "Eres especialista en Mercado Argentino. Analiza Merval, Bonos soberanos, "
                    "Riesgo Pais y brecha cambiaria (CCL/MEP)."
                ),
            ),
        }

    async def _default_llm_callable(
        self,
        model: str,
        api_key: str,
        system_prompt: str,
        user_prompt: str,
    ) -> str:
        """Compatibilidad con firma legacy de ``llm_callable``."""
        _ = api_key
        request = LLMRequest(
            agent_name="custom",
            model=model,
            system_prompt=system_prompt,
            user_prompt=user_prompt,
            token_index=0,
        )
        return await self._router.generate(ProviderName.GITHUB_MODELS, request)

    async def invoke_agent(self, agent_name: str, user_prompt: str) -> str:
        """API publica para ensambladores (thesis por dominio, herramientas externas)."""
        return await self._run_agent(agent_name, user_prompt)

    async def _run_agent(self, agent_name: str, payload: str) -> str:
        """Ejecuta un agente via LLMProviderRouter con fallback hot-swap."""
        config = self.agent_configs[agent_name]
        if self._custom_llm:
            token = (
                self._env.get("GITHUB_MODEL_TOKEN", "").strip()
                if config.provider == "github_models"
                else "LOCAL_LLM"
            )
            return await self.llm_callable(config.model, token, config.system_prompt, payload)
        request = LLMRequest(
            agent_name=config.name,
            model=config.model,
            system_prompt=config.system_prompt,
            user_prompt=payload,
            token_index=config.token_index,
        )
        return await self._router.generate(ProviderName(config.provider), request)

    async def orquestar_analisis(self, contexto_mercado: str) -> dict[str, str]:
        """Coordina analisis asincronico y devuelve veredicto final."""
        task_options = asyncio.create_task(
            self._run_agent(
                "options_gex",
                f"Lee el contexto de mercado para un analisis GEX:\n{contexto_mercado}",
            )
        )
        task_tech = asyncio.create_task(
            self._run_agent(
                "technical",
                f"Realiza analisis tecnico cuantitativo del contexto:\n{contexto_mercado}",
            )
        )
        task_macro = asyncio.create_task(
            self._run_agent(
                "macro_micro", f"Sintetiza la macro de este contexto:\n{contexto_mercado}"
            )
        )
        task_arg = asyncio.create_task(
            self._run_agent(
                "argentina",
                f"Refleja impactos en el mercado local (CCL, riesgo pais):\n{contexto_mercado}",
            )
        )

        forensic_out = await self._run_agent(
            "forensic", f"Contexto de mercado y emisor:\n{contexto_mercado}"
        )
        micro_out = await self._run_agent(
            "microstructure",
            (
                "Contexto de mercado para lectura de microestructura:\n"
                f"{contexto_mercado}\n\nHallazgos Forensic previos:\n{forensic_out}"
            ),
        )
        sentiment_out = await self._run_agent(
            "sentiment",
            (
                "Contexto para lectura de sentimiento institucional:\n"
                f"{contexto_mercado}\n\nHallazgos Forensic:\n{forensic_out}\n\n"
                f"Hallazgos Microstructure:\n{micro_out}"
            ),
        )

        options_out, tech_out, macro_out, arg_out = await asyncio.gather(
            task_options, task_tech, task_macro, task_arg
        )

        orchestrator_payload = (
            "Consolida un veredicto de riesgo y sesgo operativo.\n\n"
            f"Entrada base:\n{contexto_mercado}\n\nForensic:\n{forensic_out}\n\n"
            f"Microstructure:\n{micro_out}\n\nOptions GEX:\n{options_out}\n\n"
            f"Technical:\n{tech_out}\n\nMacro/Micro:\n{macro_out}\n\n"
            f"Argentina:\n{arg_out}\n\nSentiment:\n{sentiment_out}\n\n"
            "Devuelve: sesgo, nivel de conviccion, riesgos clave, "
            "invalidaciones y recomendacion tactica."
        )
        orchestrator_out = await self._run_agent("orchestrator", orchestrator_payload)

        return {
            "forensic": forensic_out,
            "microstructure": micro_out,
            "options_gex": options_out,
            "technical": tech_out,
            "macro_micro": macro_out,
            "argentina": arg_out,
            "sentiment": sentiment_out,
            "orchestrator": orchestrator_out,
        }

    async def orquestar_analisis_stream(
        self,
        contexto_mercado: str,
    ) -> AsyncIterator[AgentStreamEvent]:
        """SSE-friendly async generator with per-agent error isolation."""
        seq = 0
        outputs: dict[str, str] = {}

        async def _emit(event_type: StreamEventType, agent: str, data: str) -> AgentStreamEvent:
            nonlocal seq
            event = AgentStreamEvent(
                event_type=event_type,
                agent=agent,
                data=data,
                seq=seq,
                ts=datetime.now(tz=UTC),
            )
            seq += 1
            return event

        async def _run_streamed(agent_name: str, prompt: str) -> AsyncIterator[AgentStreamEvent]:
            config = self.agent_configs[agent_name]
            yield await _emit(StreamEventType.AGENT_STARTED, agent_name, "")
            request = LLMRequest(
                agent_name=config.name,
                model=config.model,
                system_prompt=config.system_prompt,
                user_prompt=prompt,
                token_index=config.token_index,
            )
            chunks: list[str] = []
            try:
                async for chunk in self._router.stream(ProviderName(config.provider), request):
                    chunks.append(chunk)
                    yield await _emit(StreamEventType.CHUNK, agent_name, chunk)
                text = "".join(chunks).strip()
                outputs[agent_name] = text
                yield await _emit(StreamEventType.AGENT_COMPLETED, agent_name, text)
            except (AllProvidersFailedError, Exception) as exc:
                msg = str(exc)
                outputs[agent_name] = f"[error] {msg}"
                yield await _emit(StreamEventType.ERROR, agent_name, msg)

        try:
            async for event in _run_streamed(
                "forensic", f"Contexto de mercado y emisor:\n{contexto_mercado}"
            ):
                yield event
            forensic_out = outputs.get("forensic", "")

            async for event in _run_streamed(
                "microstructure",
                f"Contexto microestructura:\n{contexto_mercado}\n\nForensic:\n{forensic_out}",
            ):
                yield event
            micro_out = outputs.get("microstructure", "")

            parallel_agents = (
                ("options_gex", f"Analisis GEX:\n{contexto_mercado}"),
                ("technical", f"Analisis tecnico:\n{contexto_mercado}"),
                ("macro_micro", f"Macro:\n{contexto_mercado}"),
                ("argentina", f"Argentina:\n{contexto_mercado}"),
            )
            for agent_name, prompt in parallel_agents:
                async for event in _run_streamed(agent_name, prompt):
                    yield event

            async for event in _run_streamed(
                "sentiment",
                (
                    f"Sentimiento:\n{contexto_mercado}\n\nForensic:\n{forensic_out}\n\n"
                    f"Micro:\n{micro_out}"
                ),
            ):
                yield event

            consensus_payload = (
                "Consolida veredicto institucional.\n"
                f"Contexto:\n{contexto_mercado}\n\nOutputs:\n{outputs}"
            )
            async for event in _run_streamed("orchestrator", consensus_payload):
                yield event
            consensus = outputs.get("orchestrator", "")
            yield await _emit(StreamEventType.CONSENSUS, "orchestrator", consensus)

            try:
                from backend.audit.hooks import audit_agentic_decision
                from backend.domain.agentic_models import AgenticTradeDecisionEvent

                event = AgenticTradeDecisionEvent(
                    correlation_id="stream",
                    module="alpaca",
                    symbol="_STREAM",
                    contract_symbol="_STREAM",
                    created_at=datetime.now(tz=UTC),
                    final_decision="EXECUTE" if consensus else "PASS",
                    quant_default_used=False,
                )
                await audit_agentic_decision(event=event)
            except Exception:
                pass

            yield await _emit(StreamEventType.DONE, "orchestrator", consensus)
        except asyncio.CancelledError:
            yield await _emit(StreamEventType.DONE, "system", "cancelled")
            raise


__all__ = ["AgentConfig", "AgentManager", "AgentProvider", "LlmCallable"]
