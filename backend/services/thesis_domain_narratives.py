"""Narrativas institucionales por dominio + síntesis multimodal (orquestador).

Requiere THESIS_ENABLE_AGENTS=1 y claves LLM según AgentManager.
Mapeo agente → dominio: options_gex (opciones), technical (técnico), forensic (fundamental),
microstructure (probabilístico), orchestrator (tesis multimodal final).
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass

from backend.domain.thesis_v2 import ThesisBlock
from backend.layer_4_orchestration.ai_core.agent_manager import AgentManager


def _risk_free_rate() -> float:
    raw = (os.environ.get("THESIS_RISK_FREE_RATE", "") or "").strip()
    if raw:
        try:
            return float(raw)
        except ValueError:
            pass
    return 0.04


def _json_chunk(obj: object, max_len: int = 14_000) -> str:
    try:
        s = json.dumps(obj, default=str, ensure_ascii=False)
    except TypeError:
        s = str(obj)
    if len(s) <= max_len:
        return s
    return s[: max_len - 3] + "…"


async def _safe_narrative(
    manager: AgentManager,
    agent_name: str,
    user_prompt: str,
) -> tuple[str | None, str | None]:
    try:
        text = await manager.invoke_agent(agent_name, user_prompt)
        return (text.strip() or None, None)
    except Exception as e:
        return (None, f"{agent_name}: {e!s}")


@dataclass(frozen=True)
class DomainNarratives:
    opciones: str | None
    tecnico: str | None
    fundamental: str | None
    probabilistico: str | None
    multimodal: str | None
    errors: list[str]


def _prompt_options(sym: str, metrics_payload: str) -> str:
    return (
        "[INSTITUTIONAL DESK — OPTIONS / GEX / VOL]\n"
        f"Symbol: {sym}\n"
        "Below is JSON from our internal options snapshot (chain-derived GEX, IV surface, "
        "engine signal, confluence). Write 2–4 paragraphs of institutional-grade prose: "
        "dealer gamma / pinning, key strikes, IV vs realized if present, tactical implications. "
        "Do not invent figures; state if coverage is thin.\n\n"
        f"DATA:\n{metrics_payload}"
    )


def _prompt_technical(sym: str, metrics_payload: str) -> str:
    return (
        "[INSTITUTIONAL DESK — TECHNICAL / PRICE ACTION]\n"
        f"Symbol: {sym}\n"
        "Below are price-derived metrics (OHLCV history). Produce 2–3 paragraphs: trend/vol "
        "regime, risk of mean reversion vs continuation, levels implied by the data.\n\n"
        f"DATA:\n{metrics_payload}"
    )


def _prompt_forensic_fundamental(sym: str, metrics_payload: str) -> str:
    return (
        "[INSTITUTIONAL DESK — FUNDAMENTALS / QUALITY]\n"
        f"Symbol: {sym}\n"
        "Below is JSON with keys such as `fundamental` (profile + TTM ratios + enrichment), "
        "`macro_context`, and `transcript_context`. If `fundamental.financial_scores` or nested "
        "entries include Altman Z (`altmanZScore`) or Piotroski (`piotroskiScore`), cite them "
        "explicitly with interpretation (distress vs quality). If absent, say data is missing.\n"
        "Write 2–4 paragraphs in forensic institutional style: quality of earnings, leverage, "
        "valuation hooks, red flags if any. Do not invent figures.\n\n"
        f"DATA:\n{metrics_payload}"
    )


def _prompt_micro_probabilistic(sym: str, metrics_payload: str) -> str:
    return (
        "[INSTITUTIONAL DESK — QUANT RISK / PROBABILISTIC]\n"
        f"Symbol: {sym}\n"
        "Below are EVT tail metrics, jump intensity, regime probability, Kelly fractions, "
        "gates. Interpret for a risk committee: tail risk, sizing discipline, invalidations.\n\n"
        f"DATA:\n{metrics_payload}"
    )


def _prompt_orchestrator(sym: str, pack: dict[str, str | None]) -> str:
    parts = [
        "You are the head of research. Synthesize ONE unified institutional multimodal "
        f"investment thesis for {sym} using ONLY the specialist narratives below. "
        "Structure: (1) Executive view (2) Positioning & derivatives (3) Fundamentals "
        "(4) Quant risk (5) Tactical conclusion. Flag contradictions. 700–1100 words.\n",
    ]
    for label, text in pack.items():
        parts.append(f"\n=== {label.upper()} ===\n{(text or '[not available]')}\n")
    return "\n".join(parts)


async def run_domain_narratives_and_multimodal(
    sym: str,
    opciones: ThesisBlock,
    tecnico: ThesisBlock,
    fundamental: ThesisBlock,
    probabilistico: ThesisBlock,
    manager: AgentManager | None = None,
    macro_context: object | None = None,
    transcript_context: object | None = None,
    quant_metrics: object | None = None,
    sentiment_context: object | None = None,
) -> DomainNarratives:
    """Genera narrativas por dominio y síntesis orquestada."""
    manager = manager or AgentManager()
    errs: list[str] = []

    opt_p = _prompt_options(sym, _json_chunk(opciones.metrics))
    tech_p = _prompt_technical(sym, _json_chunk(tecnico.metrics))
    fund_payload = {
        "fundamental": fundamental.metrics,
        "macro_context": macro_context,
        "transcript_context": transcript_context,
    }
    prob_payload = {
        "probabilistic": probabilistico.metrics,
        "quant_metrics": quant_metrics,
        "sentiment_context": sentiment_context,
    }
    fund_p = _prompt_forensic_fundamental(sym, _json_chunk(fund_payload))
    prob_p = _prompt_micro_probabilistic(sym, _json_chunk(prob_payload))

    r_opt, e1 = await _safe_narrative(manager, "options_gex", opt_p)
    r_tech, e2 = await _safe_narrative(manager, "technical", tech_p)
    r_fund, e3 = await _safe_narrative(manager, "forensic", fund_p)
    r_prob, e4 = await _safe_narrative(manager, "microstructure", prob_p)

    for e in (e1, e2, e3, e4):
        if e:
            errs.append(e)

    pack = {
        "options": r_opt,
        "technical": r_tech,
        "fundamental": r_fund,
        "probabilistic": r_prob,
    }
    multimodal: str | None = None
    if any(pack.values()):
        orch_p = _prompt_orchestrator(sym, pack)
        multimodal, e5 = await _safe_narrative(manager, "orchestrator", orch_p)
        if e5:
            errs.append(e5)
    else:
        errs.append("No domain narratives produced; orchestrator skipped.")

    return DomainNarratives(
        opciones=r_opt,
        tecnico=r_tech,
        fundamental=r_fund,
        probabilistico=r_prob,
        multimodal=multimodal,
        errors=errs,
    )


def get_risk_free_for_options_snapshot() -> float:
    """Tasa libre de riesgo anualizada para `options_snapshot_service`."""
    return _risk_free_rate()
