"""Orquestacion modular de agentes LLM para QuantumAnalyzer.

Usa:
- GitHub Models (agentes premium definidos por estrategia)
- Google Gemini (antes Ollama: microestructura, GEX, tecnico)
- Azure OpenAI (antes Ollama: macro/micro, Argentina)
- Framework asincronico nativo (asyncio / httpx.AsyncClient)
"""

from __future__ import annotations

import asyncio
import os
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal
from urllib.parse import urlparse

import httpx

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
    token_index: int = 0  # Índice del token "pinned" en el pool de GitHub (0-based)


class AgentManager:
    """Administra agentes especialistas para producir un veredicto institucional."""

    def __init__(
        self: AgentManager,
        env_path: str = ".env",
        llm_callable: LlmCallable | None = None,
    ) -> None:
        self.env_path = Path(env_path)
        file_values = _load_env_file(self.env_path)
        self._env = {**file_values, **os.environ}
        self.github_token = self._primary_github_model_token()
        self._gemini_api_keys = self._collect_gemini_api_keys()
        self._azure_endpoint = self._env.get("AZURE_OPENAI_ENDPOINT", "").strip().rstrip("/")
        self._azure_api_key = self._env.get("AZURE_OPENAI_KEY", "").strip()
        self._azure_api_version = self._env.get(
            "AZURE_OPENAI_API_VERSION", "2024-12-01-preview"
        ).strip()
        self._custom_llm = llm_callable is not None
        self.llm_callable = llm_callable or self._default_llm_callable
        self.agent_configs = self._build_agent_configs()

    def _github_model_bearer_tokens(self: AgentManager) -> list[str]:
        """Lee todos los tokens GITHUB_MODEL_TOKEN hasta _10 en orden de declaracion."""
        tokens: list[str] = []
        primary = self._env.get("GITHUB_MODEL_TOKEN", "").strip()
        if primary:
            tokens.append(primary)
        for i in range(2, 11):  # TOKEN_2 .. TOKEN_10
            k = self._env.get(f"GITHUB_MODEL_TOKEN_{i}", "").strip()
            if k and k not in tokens:
                tokens.append(k)
        return tokens

    def _primary_github_model_token(self: AgentManager) -> str:
        """Primer token de la lista (compatibilidad con comprobaciones de presencia)."""
        tokens = self._github_model_bearer_tokens()
        return tokens[0] if tokens else ""

    def _github_tokens_for_agent(self: AgentManager, config: AgentConfig) -> list[str]:
        """Devuelve [token_pinned, ...fallback_pool] para el agente dado.

        El token en posicion `config.token_index` se usa primero (pinned).
        Los tokens restantes se agregan en orden como pool de fallback,
        garantizando que ningun otro agente compita por el mismo token principal.
        """
        all_tokens = self._github_model_bearer_tokens()
        if not all_tokens:
            raise RuntimeError(
                "No hay GITHUB_MODEL_TOKEN configurado. Define al menos GITHUB_MODEL_TOKEN en .env."
            )
        idx = config.token_index % len(all_tokens)
        pinned = all_tokens[idx]
        fallback = [t for i, t in enumerate(all_tokens) if i != idx]
        return [pinned] + fallback

    def _collect_gemini_api_keys(self: AgentManager) -> list[str]:
        """Claves Gemini (GEMINI_API_KEY y GEMINI_API_KEY_N)."""
        keys: list[str] = []
        primary = self._env.get("GEMINI_API_KEY", "").strip()
        if primary:
            keys.append(primary)
        for i in range(1, 10):
            k = self._env.get(f"GEMINI_API_KEY_{i}", "").strip()
            if k and k not in keys:
                keys.append(k)
        return keys

    def _default_azure_deployment(self: AgentManager) -> str:
        return (self._env.get("AZURE_OPENAI_DEPLOYMENT", "") or "").strip()

    def _azure_uses_foundry_v1(self: AgentManager) -> bool:
        """Azure AI Foundry: /openai/v1/chat/completions + campo model (no api-version en query)."""
        flag = (self._env.get("AZURE_OPENAI_USE_FOUNDRY_V1", "") or "").strip().lower()
        if flag in ("1", "true", "yes"):
            return True
        if flag in ("0", "false", "no"):
            return False
        ep = self._azure_endpoint
        if not ep:
            return False
        parsed = urlparse(ep)
        path = parsed.path or ""
        return "services.ai.azure.com" in (parsed.netloc or "") and "/api/projects" in path

    def _azure_chat_url_and_payload(
        self: AgentManager,
        deployment: str,
        config: AgentConfig,
        user_prompt: str,
    ) -> tuple[str, dict[str, Any]]:
        messages = [
            {"role": "system", "content": config.system_prompt},
            {"role": "user", "content": user_prompt},
        ]
        if self._azure_uses_foundry_v1():
            parsed = urlparse(self._azure_endpoint)
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
            f"{self._azure_endpoint}/openai/deployments/{deployment}/chat/completions"
            f"?api-version={self._azure_api_version}"
        )
        payload = {
            "messages": messages,
            "temperature": 0.2,
            "max_tokens": 800,
        }
        return url, payload

    @staticmethod
    def _build_agent_configs() -> dict[str, AgentConfig]:
        """Define prompts y proveedores por agente.

        Key Pinning (GitHub Models):
          token_index=0 → GITHUB_MODEL_TOKEN   → forensic
          token_index=1 → GITHUB_MODEL_TOKEN_2 → sentiment
          token_index=2 → GITHUB_MODEL_TOKEN_3 → orchestrator
          tokens 3..5   → Fallback pool (TOKEN_4, TOKEN_5, TOKEN_6)

        Gemini y Azure no usan token_index.
        """
        return {
            # ── GitHub Models: token fijo por agente ──────────────────────────
            "forensic": AgentConfig(
                name="forensic",
                model="DeepSeek-V3-0324",
                provider="github_models",
                token_index=0,  # TOKEN_1 exclusivo
                system_prompt=(
                    "Eres un Auditor Forense institucional. Detecta anomalias contables, "
                    "riesgo de restatement y deterioro de calidad de earnings."
                ),
            ),
            "sentiment": AgentConfig(
                name="sentiment",
                model="grok-3",
                provider="github_models",
                token_index=1,  # TOKEN_2 exclusivo
                system_prompt=(
                    "Eres un Analista Macroeconómico Algorítmico. Evalua narrativa, regimen "
                    "risk-on/risk-off y catalizadores macro de corto plazo."
                ),
            ),
            "orchestrator": AgentConfig(
                name="orchestrator",
                model="gpt-5-chat",
                provider="github_models",
                token_index=2,  # TOKEN_3 exclusivo
                system_prompt=(
                    "Eres Portfolio Manager Jefe. Consolida forense, microestructura y sentimiento "
                    "en un veredicto unico con sesgo, conviccion, invalidaciones y riesgo."
                ),
            ),
            # ── Google Gemini: pool de 5 llaves, sin token_index ─────────────
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
                    "Eres especialista en Derivados y Gamma Exposure (GEX). Evalua cadenas de opciones, "
                    "max pain, pin risk y zonas de dealer hedging."
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
                    "Eres un Analista de Equity Research experto en detección de patrones de lenguaje. "
                    "Analiza transcripts de earnings calls para detectar: cambios de tono respecto a periodos previos, "
                    "evasivas en el Q&A, y temas recurrentes (Nube de palabras). Reporta con rigor institucional."
                ),
            ),
            # ── Azure OpenAI: sin token_index ────────────────────────────────
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

    async def _call_github_models(
        self: AgentManager,
        config: AgentConfig,
        user_prompt: str,
    ) -> str:
        """Llama a GitHub Models usando el token pinned del agente + fallback pool.

        El token en `config.token_index` se usa en primer lugar (pinning).
        Si falla con 429/401, se rota por el fallback pool (tokens restantes).
        Todos los cambios de token y modelo se registran en los logs.
        """
        all_tokens = self._github_model_bearer_tokens()
        if not all_tokens:
            raise RuntimeError(
                "No hay GITHUB_MODEL_TOKEN configurado. Define al menos GITHUB_MODEL_TOKEN en .env."
            )

        # Orden: [token_pinned, ...fallback_pool]
        ordered_tokens = self._github_tokens_for_agent(config)

        # Calcular el número de token para los logs (1-based)
        def _token_label(bearer: str) -> str:
            try:
                idx = all_tokens.index(bearer)
                return f"TOKEN_{idx + 1}"
            except ValueError:
                return "TOKEN_?"

        url = "https://models.inference.ai.azure.com/chat/completions"
        max_retries = 3
        base_delay = 2.0

        import logging as _logging

        _log = _logging.getLogger("agent_manager.github")

        async with httpx.AsyncClient() as client:
            last_error: BaseException | None = None
            for bearer in ordered_tokens:
                token_label = _token_label(bearer)
                headers = {
                    "Authorization": f"Bearer {bearer}",
                    "Content-Type": "application/json",
                }
                current_model = config.model
                for attempt in range(max_retries):
                    _log.info(
                        "[%s] [%s] modelo=%s intento=%d/%d",
                        config.name.upper(),
                        token_label,
                        current_model,
                        attempt + 1,
                        max_retries,
                    )
                    payload = {
                        "model": current_model,
                        "messages": [
                            {"role": "system", "content": config.system_prompt},
                            {"role": "user", "content": user_prompt},
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
                            raise RuntimeError("GitHub Models devolvio respuesta sin choices.")
                        content = choices[0].get("message", {}).get("content")
                        if not isinstance(content, str) or not content.strip():
                            raise RuntimeError("GitHub Models devolvio respuesta vacia.")
                        _log.info(
                            "[%s] [%s] OK — modelo=%s",
                            config.name.upper(),
                            token_label,
                            current_model,
                        )
                        return content.strip()
                    except (httpx.HTTPStatusError, httpx.RequestError) as e:
                        last_error = e
                        is_rate_limit = (
                            isinstance(e, httpx.HTTPStatusError) and e.response.status_code == 429
                        )
                        is_unauthorized = (
                            isinstance(e, httpx.HTTPStatusError) and e.response.status_code == 401
                        )
                        is_timeout = isinstance(e, httpx.RequestError)

                        if is_rate_limit:
                            _log.warning(
                                "[%s] [%s] 429 rate limit — modelo=%s intento=%d.",
                                config.name.upper(),
                                token_label,
                                current_model,
                                attempt + 1,
                            )
                            if attempt < max_retries - 1:
                                # En último intento del token, degradar modelo a DeepSeek como fallback
                                if (
                                    attempt == max_retries - 2
                                    and current_model != "DeepSeek-V3-0324"
                                ):
                                    _log.warning(
                                        "[%s] [%s] Degradando a DeepSeek-V3-0324.",
                                        config.name.upper(),
                                        token_label,
                                    )
                                    current_model = "DeepSeek-V3-0324"
                                await asyncio.sleep(base_delay * (2**attempt))
                                continue
                            _log.warning(
                                "[%s] [%s] Agotados reintentos — rotando al siguiente token.",
                                config.name.upper(),
                                token_label,
                            )
                            break  # Probar siguiente token del pool

                        if is_unauthorized:
                            _log.warning(
                                "[%s] [%s] 401 no autorizado — rotando al siguiente token.",
                                config.name.upper(),
                                token_label,
                            )
                            break  # Probar siguiente token del pool

                        if is_timeout:
                            _log.warning(
                                "[%s] [%s] Timeout — intento=%d.",
                                config.name.upper(),
                                token_label,
                                attempt + 1,
                            )
                            if attempt < max_retries - 1:
                                await asyncio.sleep(base_delay * (2**attempt))
                                continue
                            break

                        raise  # Errores no recuperables se propagan inmediatamente

            if last_error:
                raise last_error
            raise RuntimeError(
                f"[{config.name.upper()}] GitHub Models fallo tras probar {len(ordered_tokens)} token(s)."
            )

    def _resolve_gemini_model(self: AgentManager, config: AgentConfig) -> str:
        env_model = self._env.get("GEMINI_AGENT_MODEL", "").strip()
        return env_model or config.model

    @staticmethod
    def _text_from_gemini_response(data: dict[str, Any]) -> str:
        feedback = data.get("promptFeedback")
        if isinstance(feedback, dict) and feedback.get("blockReason"):
            raise RuntimeError(f"Gemini bloqueo la respuesta: {feedback.get('blockReason')}")
        candidates = data.get("candidates")
        if not isinstance(candidates, list) or not candidates:
            raise RuntimeError("Gemini devolvio respuesta sin candidates.")
        content = candidates[0].get("content") if isinstance(candidates[0], dict) else None
        parts = content.get("parts") if isinstance(content, dict) else None
        if not isinstance(parts, list):
            raise RuntimeError("Gemini devolvio content.parts invalido.")
        text = "".join(
            p.get("text", "")
            for p in parts
            if isinstance(p, dict) and isinstance(p.get("text"), str)
        )
        if not text.strip():
            raise RuntimeError("Gemini devolvio respuesta vacia.")
        return text.strip()

    async def _call_gemini(
        self: AgentManager,
        config: AgentConfig,
        user_prompt: str,
    ) -> str:
        keys = self._gemini_api_keys
        if not keys:
            raise RuntimeError("Falta GEMINI_API_KEY o GEMINI_API_KEY_1 para agentes Gemini.")

        model = self._resolve_gemini_model(config)
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
                    "systemInstruction": {"parts": [{"text": config.system_prompt}]},
                    "contents": [{"role": "user", "parts": [{"text": user_prompt}]}],
                    "generationConfig": {
                        "temperature": 0.2,
                        "maxOutputTokens": 800,
                    },
                }
                for attempt in range(max_retries):
                    try:
                        response = await client.post(url, json=payload, timeout=90.0)
                        response.raise_for_status()
                        data = response.json()
                        if not isinstance(data, dict):
                            raise RuntimeError("Gemini devolvio JSON invalido.")
                        return self._text_from_gemini_response(data)
                    except httpx.HTTPStatusError as e:
                        last_error = e
                        code = e.response.status_code
                        if code == 404:
                            raise RuntimeError(
                                f"Modelo Gemini no encontrado ({model}). Revisa GEMINI_AGENT_MODEL."
                            ) from e
                        if code in (401, 403) and api_key != keys[-1]:
                            break
                        if code == 429 and attempt < max_retries - 1:
                            await asyncio.sleep(base_delay * (2**attempt))
                            continue
                        if code == 429 and api_key != keys[-1]:
                            break
                        raise
                    except httpx.RequestError as e:
                        last_error = e
                        if attempt < max_retries - 1:
                            await asyncio.sleep(base_delay * (2**attempt))
                            continue
                        raise
            if last_error:
                raise last_error
            raise RuntimeError("Gemini fallo tras reintentos.")

    async def _call_azure_openai(
        self: AgentManager,
        config: AgentConfig,
        user_prompt: str,
    ) -> str:
        deployment = self._default_azure_deployment()
        if not deployment:
            raise RuntimeError("Falta AZURE_OPENAI_DEPLOYMENT en .env para agentes Azure OpenAI.")
        if not self._azure_endpoint or not self._azure_api_key:
            raise RuntimeError("Faltan AZURE_OPENAI_ENDPOINT o AZURE_OPENAI_KEY.")

        url, payload = self._azure_chat_url_and_payload(deployment, config, user_prompt)
        headers = {
            "api-key": self._azure_api_key,
            "Content-Type": "application/json",
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
                    choices = data.get("choices")
                    if not isinstance(choices, list) or not choices:
                        raise RuntimeError("Azure OpenAI devolvio respuesta sin choices.")
                    content = choices[0].get("message", {}).get("content")
                    if not isinstance(content, str) or not content.strip():
                        raise RuntimeError("Azure OpenAI devolvio respuesta vacia.")
                    return content.strip()
                except (httpx.HTTPStatusError, httpx.RequestError) as e:
                    last_error = e
                    is_rate_limit = (
                        isinstance(e, httpx.HTTPStatusError) and e.response.status_code == 429
                    )
                    is_timeout = isinstance(e, httpx.RequestError)
                    if (is_rate_limit or is_timeout) and attempt < max_retries - 1:
                        await asyncio.sleep(base_delay * (2**attempt))
                        continue
                    raise
            if last_error:
                raise last_error
            raise RuntimeError("Azure OpenAI fallo tras reintentos.")

    async def _default_llm_callable(
        self: AgentManager,
        model: str,
        api_key: str,
        system_prompt: str,
        user_prompt: str,
    ) -> str:
        """Mantiene compatibilidad con la firma de llamada por defecto (custom llm_callable)."""
        # Crea un AgentConfig temporal para reutilizar la logica de pinning con token_index=0
        _tmp_config = AgentConfig(
            name="custom",
            model=model,
            provider="github_models",
            system_prompt=system_prompt,
            token_index=0,
        )
        return await self._call_github_models(_tmp_config, user_prompt)

    async def invoke_agent(self: AgentManager, agent_name: str, user_prompt: str) -> str:
        """API publica para ensambladores (thesis por dominio, herramientas externas)."""
        return await self._run_agent(agent_name, user_prompt)

    async def _run_agent(self: AgentManager, agent_name: str, payload: str) -> str:
        """Ejecuta un agente segun proveedor asignado.

        Para GitHub Models usa Key Pinning: cada agente tiene su token fijo
        (config.token_index) y el resto actua como fallback pool.
        """
        config = self.agent_configs[agent_name]
        if self._custom_llm:
            # Compatibilidad: llm_callable externo usa la firma antigua
            token = self.github_token if config.provider == "github_models" else "LOCAL_LLM"
            return await self.llm_callable(config.model, token, config.system_prompt, payload)
        if config.provider == "github_models":
            # Key Pinning: usar la nueva implementacion con token asignado
            return await self._call_github_models(config, payload)
        if config.provider == "gemini":
            return await self._call_gemini(config, payload)
        if config.provider == "azure_openai":
            return await self._call_azure_openai(config, payload)
        raise RuntimeError(f"Proveedor no soportado: {config.provider}")

    async def orquestar_analisis(self: AgentManager, contexto_mercado: str) -> dict[str, str]:
        """Coordina analisis asincronico y devuelve veredicto final."""

        # 1. Agentes independientes que no requieren contexto de otros agentes
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

        # 2. Cadena de dependencias: Forensic -> Microstructure -> Sentiment
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

        # Recolectamos los outputs de los agentes independientes disparados al unísono
        options_out, tech_out, macro_out, arg_out = await asyncio.gather(
            task_options, task_tech, task_macro, task_arg
        )

        # 3. Orquestador final agrupa todos los outputs
        orchestrator_payload = (
            "Consolida un veredicto de riesgo y sesgo operativo.\n\n"
            f"Entrada base:\n{contexto_mercado}\n\nForensic:\n{forensic_out}\n\n"
            f"Microstructure:\n{micro_out}\n\nOptions GEX:\n{options_out}\n\n"
            f"Technical:\n{tech_out}\n\nMacro/Micro:\n{macro_out}\n\n"
            f"Argentina:\n{arg_out}\n\nSentiment:\n{sentiment_out}\n\n"
            "Devuelve: sesgo, nivel de conviccion, riesgos clave, invalidaciones y recomendacion tactica."
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
