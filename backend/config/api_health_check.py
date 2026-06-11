"""Diagnostico asincorno de salud para claves API de QuantumAnalyzer.

Ejecuta pings HTTP ultraligeros sobre:
- GitHub Models (proveedor principal)
- Gemini (GEMINI_API_KEY_* o FALLBACK_API_KEYS)
- Azure OpenAI (AZURE_OPENAI_* si estan definidos)
- Ollama local (resiliencia on-prem)
"""

from __future__ import annotations

import asyncio
import os
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Literal
from urllib.parse import urlparse

import httpx
from dotenv import load_dotenv

if __package__ is None or __package__ == "":
    project_root = Path(__file__).resolve().parent.parent
    if str(project_root) not in sys.path:
        sys.path.insert(0, str(project_root))

from config.logger_setup import get_logger
from config.settings import Config, load_settings

logger = get_logger(__name__)

StatusLabel = Literal["SUCCESS", "CRITICAL", "WARNING", "ERROR"]


@dataclass(frozen=True)
class HealthResult:
    """Resultado de diagnostico para una clave API."""

    provider: str
    key_alias: str
    status_code: int
    label: StatusLabel
    detail: str
    is_operational: bool


def _mask_key(key: str) -> str:
    """Enmascara una clave para no exponer secretos en logs."""
    if len(key) <= 8:
        return "***"
    return f"{key[:4]}...{key[-4:]}"


def _classify_status(
    provider: str,
    key_alias: str,
    status_code: int,
    detail: str,
) -> HealthResult:
    """Normaliza codigos HTTP en etiquetas operativas de seguridad."""
    if status_code == 200:
        logger.info("[%s] SUCCESS | %s | HTTP %s | %s", provider, key_alias, status_code, detail)
        return HealthResult(
            provider=provider,
            key_alias=key_alias,
            status_code=status_code,
            label="SUCCESS",
            detail=detail,
            is_operational=True,
        )

    if status_code == 401:
        logger.critical(
            "[%s] CRITICAL: Clave Invalida/Revocada | %s | HTTP %s | %s",
            provider,
            key_alias,
            status_code,
            detail,
        )
        return HealthResult(
            provider=provider,
            key_alias=key_alias,
            status_code=status_code,
            label="CRITICAL",
            detail="Clave Invalida/Revocada",
            is_operational=False,
        )

    if status_code == 429:
        logger.warning(
            "[%s] WARNING: Rate Limit / Cuota Excedida | %s | HTTP %s | %s",
            provider,
            key_alias,
            status_code,
            detail,
        )
        return HealthResult(
            provider=provider,
            key_alias=key_alias,
            status_code=status_code,
            label="WARNING",
            detail="Rate Limit / Cuota Excedida",
            is_operational=False,
        )

    logger.error("[%s] ERROR | %s | HTTP %s | %s", provider, key_alias, status_code, detail)
    return HealthResult(
        provider=provider,
        key_alias=key_alias,
        status_code=status_code,
        label="ERROR",
        detail=detail,
        is_operational=False,
    )


async def _ping_github_models(
    client: httpx.AsyncClient,
    settings: Config,
) -> HealthResult:
    """Valida token principal contra GitHub Models con payload minimo."""
    url = "https://models.inference.ai.azure.com/chat/completions"
    payload = {
        "model": "gpt-4o-mini",
        "messages": [
            {"role": "system", "content": "Respond ONLY with OK."},
            {"role": "user", "content": "OK"},
        ],
        "max_tokens": 1,
        "temperature": 0,
    }
    headers = {
        "Authorization": f"Bearer {settings.github_model_token}",
        "Content-Type": "application/json",
    }
    key_alias = _mask_key(settings.github_model_token)

    try:
        response = await client.post(url, headers=headers, json=payload)
    except httpx.HTTPError as error:
        logger.error("[GITHUB_MODELS] ERROR | %s | Network failure: %s", key_alias, error)
        return HealthResult(
            provider="GITHUB_MODELS",
            key_alias=key_alias,
            status_code=0,
            label="ERROR",
            detail=f"Network failure: {error}",
            is_operational=False,
        )

    detail = response.text[:200]
    return _classify_status("GITHUB_MODELS", key_alias, response.status_code, detail)


async def _ping_gemini_key(
    client: httpx.AsyncClient,
    api_key: str,
    index: int,
) -> HealthResult:
    """Valida una clave fallback contra Gemini con request ultraligero."""
    payload = {
        "contents": [{"parts": [{"text": "OK"}]}],
        "generationConfig": {
            "maxOutputTokens": 1,
            "temperature": 0,
        },
    }
    key_alias = f"fallback_{index}_{_mask_key(api_key)}"
    candidate_models = ("gemini-2.0-flash", "gemini-1.5-flash", "gemini-1.5-flash-latest")

    last_result: HealthResult | None = None
    for model_name in candidate_models:
        url = (
            "https://generativelanguage.googleapis.com/v1beta/models/"
            f"{model_name}:generateContent?key={api_key}"
        )
        try:
            response = await client.post(url, json=payload)
        except httpx.HTTPError as error:
            logger.error("[GEMINI] ERROR | %s | Network failure: %s", key_alias, error)
            return HealthResult(
                provider="GEMINI",
                key_alias=key_alias,
                status_code=0,
                label="ERROR",
                detail=f"Network failure: {error}",
                is_operational=False,
            )

        detail = response.text[:200]
        if response.status_code == 404:
            last_result = _classify_status("GEMINI", key_alias, response.status_code, detail)
            continue
        return _classify_status("GEMINI", key_alias, response.status_code, detail)

    if last_result is not None:
        return last_result

    return HealthResult(
        provider="GEMINI",
        key_alias=key_alias,
        status_code=0,
        label="ERROR",
        detail="No se pudo validar clave Gemini por error inesperado.",
        is_operational=False,
    )


def _azure_should_use_foundry_v1(endpoint: str) -> bool:
    """Misma heuristica que AgentManager (Foundry project URL o flag explicito)."""
    flag = os.environ.get("AZURE_OPENAI_USE_FOUNDRY_V1", "").strip().lower()
    if flag in ("1", "true", "yes"):
        return True
    if flag in ("0", "false", "no"):
        return False
    parsed = urlparse(endpoint)
    path = parsed.path or ""
    return "services.ai.azure.com" in (parsed.netloc or "") and "/api/projects" in path


def _gemini_keys_from_env() -> list[str]:
    """Claves Gemini dedicadas (prioridad sobre FALLBACK_API_KEYS)."""
    keys: list[str] = []
    for name in ("GEMINI_API_KEY", "GEMINI_API_KEY_1", "GEMINI_API_KEY_2", "GEMINI_API_KEY_3"):
        value = os.environ.get(name, "").strip()
        if value:
            keys.append(value)
    return keys


async def _ping_azure_openai(client: httpx.AsyncClient) -> HealthResult:
    """Ping minimo a Azure OpenAI Chat Completions (mismo contrato que AgentManager)."""
    endpoint = os.environ.get("AZURE_OPENAI_ENDPOINT", "").strip().rstrip("/")
    key = os.environ.get("AZURE_OPENAI_KEY", "").strip()
    deployment = os.environ.get("AZURE_OPENAI_DEPLOYMENT", "").strip()
    api_version = os.environ.get("AZURE_OPENAI_API_VERSION", "2024-12-01-preview").strip()
    key_alias = f"azure_{_mask_key(key) if key else 'none'}"
    if not (endpoint and key and deployment):
        logger.info(
            "[AZURE_OPENAI] SKIP | %s | Variables incompletas (opcional si no usas agentes Azure).",
            key_alias,
        )
        return HealthResult(
            provider="AZURE_OPENAI",
            key_alias=key_alias,
            status_code=0,
            label="WARNING",
            detail="AZURE_OPENAI_* no configurado.",
            is_operational=False,
        )

    if _azure_should_use_foundry_v1(endpoint):
        parsed = urlparse(endpoint)
        origin = f"{parsed.scheme}://{parsed.netloc}"
        url = f"{origin}/openai/v1/chat/completions"
        payload = {
            "model": deployment,
            "messages": [
                {"role": "system", "content": "Respond ONLY with OK."},
                {"role": "user", "content": "OK"},
            ],
            "max_tokens": 1,
            "temperature": 0,
        }
    else:
        url = (
            f"{endpoint}/openai/deployments/{deployment}/chat/completions"
            f"?api-version={api_version}"
        )
        payload = {
            "messages": [
                {"role": "system", "content": "Respond ONLY with OK."},
                {"role": "user", "content": "OK"},
            ],
            "max_tokens": 1,
            "temperature": 0,
        }
    headers = {"api-key": key, "Content-Type": "application/json"}
    try:
        response = await client.post(url, headers=headers, json=payload)
    except httpx.HTTPError as error:
        logger.error("[AZURE_OPENAI] ERROR | %s | Network failure: %s", key_alias, error)
        return HealthResult(
            provider="AZURE_OPENAI",
            key_alias=key_alias,
            status_code=0,
            label="ERROR",
            detail=f"Network failure: {error}",
            is_operational=False,
        )

    detail = response.text[:200]
    return _classify_status("AZURE_OPENAI", key_alias, response.status_code, detail)


async def _fetch_ollama_tags(client: httpx.AsyncClient, base_url: str) -> set[str]:
    """Obtiene catalogo de modelos locales disponibles en Ollama."""
    try:
        response = await client.get(f"{base_url}/api/tags")
    except httpx.HTTPError as error:
        logger.error("[OLLAMA] ERROR | tags_lookup | Network failure: %s", error)
        return set()

    if response.status_code != 200:
        logger.error(
            "[OLLAMA] ERROR | tags_lookup | HTTP %s | %s",
            response.status_code,
            response.text[:200],
        )
        return set()

    payload = response.json()
    models_raw = payload.get("models", [])
    if not isinstance(models_raw, list):
        return set()

    names: set[str] = set()
    for model_entry in models_raw:
        if isinstance(model_entry, dict):
            model_name = model_entry.get("name")
            if isinstance(model_name, str) and model_name.strip():
                names.add(model_name.strip())
    return names


async def _ping_ollama_model(
    client: httpx.AsyncClient,
    base_url: str,
    model_name: str,
    available_models: set[str],
) -> HealthResult:
    """Ejecuta ping local sobre modelo Ollama con salida minima."""
    key_alias = f"local_{model_name}"
    available_by_base = {name.split(":", 1)[0] for name in available_models}
    model_base = model_name.split(":", 1)[0]
    if model_name not in available_models and model_base not in available_by_base:
        logger.warning("[OLLAMA] WARNING | %s | Modelo no instalado localmente.", key_alias)
        return HealthResult(
            provider="OLLAMA",
            key_alias=key_alias,
            status_code=404,
            label="WARNING",
            detail="Modelo no instalado localmente.",
            is_operational=False,
        )

    payload = {
        "model": model_name,
        "prompt": "OK",
        "stream": False,
        "options": {"num_predict": 1, "temperature": 0},
    }
    try:
        response = await client.post(f"{base_url}/api/generate", json=payload)
    except httpx.HTTPError as error:
        logger.error("[OLLAMA] ERROR | %s | Network failure: %s", key_alias, error)
        return HealthResult(
            provider="OLLAMA",
            key_alias=key_alias,
            status_code=0,
            label="ERROR",
            detail=f"Network failure: {error}",
            is_operational=False,
        )

    detail = response.text[:200]
    return _classify_status("OLLAMA", key_alias, response.status_code, detail)


async def run_api_health_check() -> int:
    """Ejecuta diagnostico completo y devuelve codigo de salida de proceso."""
    load_dotenv()
    settings = load_settings()

    timeout = httpx.Timeout(30.0, connect=8.0)
    async with httpx.AsyncClient(timeout=timeout) as client:
        github_task = _ping_github_models(client, settings)
        gemini_key_list = _gemini_keys_from_env() or settings.get_fallback_api_keys()
        gemini_tasks = [
            _ping_gemini_key(client, key, index + 1) for index, key in enumerate(gemini_key_list)
        ]
        azure_task = _ping_azure_openai(client)
        ollama_available = await _fetch_ollama_tags(client, settings.ollama_base_url)
        ollama_tasks = [
            _ping_ollama_model(
                client=client,
                base_url=settings.ollama_base_url,
                model_name=model_name,
                available_models=ollama_available,
            )
            for model_name in settings.get_ollama_models()
        ]

        github_result = await github_task
        azure_result = await azure_task
        gemini_results = await asyncio.gather(*gemini_tasks)
        ollama_results = await asyncio.gather(*ollama_tasks)

    all_results = [github_result, azure_result, *gemini_results, *ollama_results]
    operational_count = sum(1 for result in all_results if result.is_operational)
    failed_count = len(all_results) - operational_count
    gemini_ok = any(result.is_operational and result.provider == "GEMINI" for result in all_results)
    ollama_ok = any(result.is_operational and result.provider == "OLLAMA" for result in all_results)
    azure_ok = any(
        result.is_operational and result.provider == "AZURE_OPENAI" for result in all_results
    )

    system_ready = github_result.is_operational and (gemini_ok or ollama_ok or azure_ok)
    final_state = "READY" if system_ready else "BLOCKED"

    logger.info("===== API HEALTH CHECK SUMMARY =====")
    logger.info("Operativas: %s", operational_count)
    logger.info("Fallidas: %s", failed_count)
    logger.info("Gemini operativo: %s", gemini_ok)
    logger.info("Azure OpenAI operativo: %s", azure_ok)
    logger.info("Backup Ollama operativo: %s", ollama_ok)
    logger.info("Estado del sistema: %s", final_state)
    logger.info("====================================")

    return 0 if system_ready else 1


async def _amain() -> None:
    exit_code = await run_api_health_check()
    if exit_code != 0:
        raise SystemExit(exit_code)


if __name__ == "__main__":
    asyncio.run(_amain())
