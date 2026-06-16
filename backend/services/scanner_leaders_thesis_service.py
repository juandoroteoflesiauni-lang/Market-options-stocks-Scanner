from __future__ import annotations
from typing import Any
"""Selective Layer-4 thesis for scanner leaders: concise LLM brief + deterministic fallback."""


import asyncio
import inspect
import json
import os

from backend.config.logger_setup import get_logger
from backend.domain.market_scanner_models import ScannerLeadersThesisResponse
from backend.services.scanner_external_contracts import ResearchBriefResult

logger = get_logger(__name__)

_FOCUS_TIMEOUT_S = 72.0
_FINROBOT_TIMEOUT_S = 45.0
_MAX_WORDS_BRIEF = 260
_TRUTHY_ENV = {"1", "true", "yes", "on"}


def _clip_words(text: str, max_words: int = _MAX_WORDS_BRIEF) -> str:
    words = text.split()
    if len(words) <= max_words:
        return text
    return " ".join(words[:max_words]) + "\n\n*(Texto truncado al límite de brevedad.)*"


def _env_timeout_seconds(name: str, default: float) -> float:
    raw = os.getenv(name, "").strip()
    if not raw:
        return default
    try:
        value = float(raw)
    except ValueError:
        logger.warning("scanner.leaders_thesis_invalid_timeout env=%s value=%s", name, raw[:40])
        return default
    return max(0.001, min(value, default))


def _select_research_engine() -> str:
    raw = os.getenv("SCANNER_RESEARCH_ENGINE", "").strip().lower()
    if raw in {"focused", "full_agents", "finrobot"}:
        return raw
    if raw:
        logger.warning("scanner.leaders_thesis_unknown_engine engine=%s fallback=focused", raw[:80])
    legacy_full_agents = (
        os.getenv("SCANNER_LEADERS_THESIS_AGENTS", "").strip().lower() in _TRUTHY_ENV
    )
    return "full_agents" if legacy_full_agents else "focused"


def _deterministic_narrative(
    symbols: list[str],
    summaries: list[dict[str, Any]],
    *,
    universe: str | None,
    regime: dict[str, Any] | None,
    compact: bool,
) -> str:
    """Desk-style digest from scanner rows (no LLM)."""
    lines: list[str] = [
        "## Resumen desk (determinístico)",
        "",
        f"**Universo:** {universe or '—'} · **Líderes:** {', '.join(symbols)}",
        "",
    ]
    if regime and regime.get("status") == "ok":
        lines.extend(
            [
                "### Contexto transversal",
                f"- Tono: **{regime.get('tone')}** · score medio {regime.get('mean_scanner_score')} · "
                f"bull {regime.get('bullish_share')} / bear {regime.get('bearish_share')}",
                "",
            ]
        )

    cap = 4 if compact else 10
    for block in summaries[:cap]:
        sym = block.get("symbol") or "—"
        score = block.get("scanner_score")
        grade = block.get("setup_grade")
        direction = block.get("direction")
        reasons = block.get("reasons") or []
        warnings = block.get("warnings") or []
        vetoes = block.get("vetoes") or []
        lines.append(f"### {sym} — {score} pts · {grade} · {direction}")
        if reasons:
            lines.append("- " + " · ".join(str(r) for r in reasons[:4]))
        if warnings:
            lines.append(f"- *Alertas:* {' · '.join(str(w) for w in warnings[:3])}")
        if vetoes:
            lines.append(f"- *Vetos:* {' · '.join(str(v) for v in vetoes[:2])}")
        lines.append("")

    if not compact:
        lines.append(
            "_Modo sin LLM: configurá `GITHUB_MODEL_TOKEN` / `GEMINI_API_KEY` / `AZURE_OPENAI_*` "
            "para generar la **tesis breve IA** automáticamente. "
            "Modo multi-agente pesado: `SCANNER_LEADERS_THESIS_AGENTS=true`._"
        )
    return "\n".join(lines).strip()


def _build_focused_user_prompt(
    symbols: list[str],
    summaries: list[dict[str, Any]],
    *,
    universe: str | None,
    regime: dict[str, Any] | None,
) -> str:
    payload: dict[str, Any] = {
        "symbols": symbols,
        "universe": universe,
        "universe_regime": regime,
        "leaders": summaries[:8],
    }
    blob = json.dumps(payload, ensure_ascii=False, default=str)[:14_000]
    return (
        "Rol: sos **PM institucional** (mesa de renta variable). "
        "Recibís un JSON con líderes del **Market Scanner** (score compuesto, notas, módulos técnico/probabilístico/GEX).\n\n"
        "**Tarea:** escribí una **tesis de asignación breve** en español, tono profesional, "
        f"**máximo {_MAX_WORDS_BRIEF} palabras**. "
        "Explicá de forma sintética **por qué tiene sentido destinar capital de trabajo al basket** "
        "(convergencia de señales, diversificación implícita, coherencia direccional, lectura de riesgo transversal). "
        "Incluí **una** subsección breve de riesgos/vetos si el JSON los trae.\n\n"
        "**Restricciones:**\n"
        "- No es un informe equity completo ni reemplaza un módulo de tesis larga; evitá listados exhaustivos.\n"
        "- No inventes catalizadores ni cifras que no estén en el JSON.\n"
        "- Markdown: `## Tesis líderes (scanner)`, párrafo principal, `### Riesgos a vigilar` con viñetas cortas si aplica.\n\n"
        "JSON:\n"
        f"{blob}"
    )


async def _try_focused_llm_brief(user_prompt: str) -> tuple[str, str] | None:
    """Single-agent call: orchestrator (GitHub) → macro_micro (Azure) → technical (Gemini)."""
    from backend.services.ai_core.agent_manager import AgentManager

    manager = AgentManager()
    order = ("orchestrator", "macro_micro", "technical")
    last_err = ""
    for agent in order:
        try:
            raw = await asyncio.wait_for(
                manager.invoke_agent(agent, user_prompt),
                timeout=_FOCUS_TIMEOUT_S,
            )
            text = (raw or "").strip()
            if text:
                return agent, _clip_words(text)
        except TimeoutError:
            last_err = f"{agent}:timeout"
            logger.warning("scanner.leaders_thesis_focused_timeout agent=%s", agent)
        except Exception as exc:
            last_err = f"{agent}:{str(exc)[:120]}"
            logger.debug(
                "scanner.leaders_thesis_focused_skip agent=%s err=%s", agent, str(exc)[:160]
            )
            continue
    logger.warning("scanner.leaders_thesis_focused_failed last=%s", last_err)
    return None


def _render_finrobot_orchestrator(result: ResearchBriefResult) -> str:
    sections: list[str] = []
    title = result.title.strip() or "FinRobot leaders research"
    sections.append(f"## {title}")
    summary = result.summary.strip()
    if summary:
        sections.append(summary)
    if result.key_points:
        sections.append("### Puntos de research")
        sections.extend(f"- {point}" for point in result.key_points[:6])
    if result.risks:
        sections.append("### Riesgos a vigilar")
        sections.extend(f"- {risk}" for risk in result.risks[:4])
    sections.append(
        "_Research solamente: no autoriza trades, sizing, entradas/salidas ni saltea el funding gate._"
    )
    return _clip_words("\n\n".join(sections), max_words=420)


def _finrobot_agent_summaries(result: ResearchBriefResult) -> dict[str, str]:
    return {
        "engine": result.engine,
        "adapter_mode": result.mode,
        "status": result.status,
        "reason": result.reason,
        "confidence": f"{result.confidence:.3f}",
        "data_quality_score": f"{result.data_quality_score:.3f}",
        "symbols": ", ".join(result.symbols),
        "citations_count": str(len(result.citations)),
    }


async def _invoke_finrobot_research(
    symbols: list[str],
    row_summaries: list[dict[str, Any]],
    *,
    universe: str | None,
    regime_summary: dict[str, Any] | None,
) -> ResearchBriefResult:
    from backend.services.finrobot_scanner_research import run_finrobot_leaders_research

    if inspect.iscoroutinefunction(run_finrobot_leaders_research):
        return await run_finrobot_leaders_research(
            symbols,
            row_summaries,
            universe=universe,
            regime_summary=regime_summary,
        )
    return await asyncio.to_thread(
        run_finrobot_leaders_research,
        symbols,
        row_summaries,
        universe=universe,
        regime_summary=regime_summary,
    )


async def _try_finrobot_research(
    symbols: list[str],
    row_summaries: list[dict[str, Any]],
    *,
    universe: str | None,
    regime_summary: dict[str, Any] | None,
    fallback_narrative: str,
) -> ScannerLeadersThesisResponse | None:
    timeout_s = _env_timeout_seconds("SCANNER_FINROBOT_TIMEOUT_SECONDS", _FINROBOT_TIMEOUT_S)
    try:
        result = await asyncio.wait_for(
            _invoke_finrobot_research(
                symbols,
                row_summaries,
                universe=universe,
                regime_summary=regime_summary,
            ),
            timeout=timeout_s,
        )
    except TimeoutError:
        logger.warning("scanner.leaders_thesis_finrobot_timeout timeout_s=%s", timeout_s)
        return None
    except Exception as exc:
        logger.warning("scanner.leaders_thesis_finrobot_failed error=%s", str(exc)[:200])
        return None

    if result.status not in {"available", "partial"}:
        logger.info(
            "scanner.leaders_thesis_finrobot_unavailable status=%s reason=%s",
            result.status,
            result.reason,
        )
        return None

    orchestrator = _render_finrobot_orchestrator(result)
    if not orchestrator.strip():
        logger.info("scanner.leaders_thesis_finrobot_empty")
        return None

    return ScannerLeadersThesisResponse(
        ok=True,
        mode="finrobot",
        orchestrator=orchestrator,
        agent_summaries=_finrobot_agent_summaries(result),
        fallback_narrative=fallback_narrative,
        error=None,
    )


async def run_leaders_thesis(
    symbols: list[str],
    row_summaries: list[dict[str, Any]],
    *,
    universe: str | None = None,
    regime_summary: dict[str, Any] | None = None,
) -> ScannerLeadersThesisResponse:
    """Focused LLM brief by default, optional full agents or FinRobot research."""
    engine = _select_research_engine()
    digest_full = _deterministic_narrative(
        symbols, row_summaries, universe=universe, regime=regime_summary, compact=False
    )
    digest_compact = _deterministic_narrative(
        symbols, row_summaries, universe=universe, regime=regime_summary, compact=True
    )

    if engine == "finrobot":
        finrobot = await _try_finrobot_research(
            symbols,
            row_summaries,
            universe=universe,
            regime_summary=regime_summary,
            fallback_narrative=digest_compact,
        )
        if finrobot is not None:
            return finrobot
        logger.info("scanner.leaders_thesis_finrobot_fallback engine=focused")

    if engine == "full_agents":
        try:
            from backend.services.ai_core.agent_manager import AgentManager

            context_lines = [digest_full, "", "SYMBOLS:", ", ".join(symbols)]
            for s in row_summaries:
                context_lines.append(json.dumps(s, ensure_ascii=False)[:1200])
            context_blob = "\n".join(context_lines)[:12_000]

            manager = AgentManager()
            agents_out = await manager.orquestar_analisis(context_blob)
            orch = str(agents_out.get("orchestrator") or "")[:8000]
            summaries = {k: str(v)[:2000] for k, v in agents_out.items()}
            return ScannerLeadersThesisResponse(
                ok=True,
                mode="full_agents",
                orchestrator=orch,
                agent_summaries=summaries,
                fallback_narrative=digest_full,
                error=None,
            )
        except Exception as exc:
            logger.warning("scanner.leaders_thesis_full_agents_failed error=%s", str(exc)[:200])
            return ScannerLeadersThesisResponse(
                ok=False,
                mode="error",
                orchestrator=None,
                agent_summaries=None,
                fallback_narrative=digest_full,
                error=str(exc)[:500],
            )

    prompt = _build_focused_user_prompt(
        symbols, row_summaries, universe=universe, regime=regime_summary
    )
    focused = await _try_focused_llm_brief(prompt)
    if focused:
        agent_used, brief = focused
        return ScannerLeadersThesisResponse(
            ok=True,
            mode="focused_llm",
            orchestrator=brief,
            agent_summaries={"brief_agent": agent_used, "brief_kind": "scanner_leaders_instant"},
            fallback_narrative=digest_compact,
            error=None,
        )

    return ScannerLeadersThesisResponse(
        ok=True,
        mode="deterministic",
        orchestrator=None,
        agent_summaries=None,
        fallback_narrative=digest_full,
        error=None,
    )
