from __future__ import annotations
"""
backend/layer_1_data/engines/morning_briefing.py
════════════════════════════════════════════════════════════════════════════════
Morning Briefing Engine — Institutional Prompt Generation & Parsing (Sector: DATA).
════════════════════════════════════════════════════════════════════════════════
"""


import json
import textwrap
from datetime import UTC, date, datetime
from typing import Annotated, Final

from pydantic import BaseModel, ConfigDict, Field, field_validator

from backend.domain.morning_briefing_models import MacroSnapshot, NewsEvent, RiskRegime, SectorTilt

# ─────────────────────────────────────────────────────────────────────────────
# ENUMS
# ─────────────────────────────────────────────────────────────────────────────


# ─────────────────────────────────────────────────────────────────────────────
# THRESHOLDS
# ─────────────────────────────────────────────────────────────────────────────

_VIX_FEAR_THRESHOLD: Final[float] = 25.0
_VIX_PANIC_THRESHOLD: Final[float] = 40.0
_HY_STRESS_THRESHOLD: Final[float] = 500.0  # bps
_MAX_NEWS_IN_PROMPT: Final[int] = 12

_CURVE_DEEP_INVERSION: Final[float] = -1.50
_CURVE_HINT_THRESHOLD: Final[float] = -0.50
_HY_HINT_THRESHOLD: Final[float] = 500.0
_NEWS_NEGATIVE_SENT: Final[float] = -0.30
_NEWS_IMPACT_MIN: Final[float] = 0.60
_NEWS_NEGATIVE_COUNT: Final[int] = 3
_FUTURES_RED_THRESHOLD: Final[float] = -1.50
_SENTIMENT_POS_THRESH: Final[float] = 0.15
_SENTIMENT_NEG_THRESH: Final[float] = -0.15


# ─────────────────────────────────────────────────────────────────────────────
# OUTPUT MODEL
# ─────────────────────────────────────────────────────────────────────────────


class LocalMorningBriefResult(BaseModel):
    """Immutable result from the MorningBriefingEngine."""

    model_config = ConfigDict(frozen=True)

    risk_regime: RiskRegime
    conviction_score: Annotated[float, Field(ge=0.0, le=1.0)]
    key_drivers: Annotated[list[str], Field(min_length=3, max_length=3)]
    sector_tilt: SectorTilt
    generated_at: datetime
    llm_raw_response: str | None = Field(default=None, exclude=True)
    is_fallback: bool = Field(default=False)

    @field_validator("key_drivers")
    @classmethod
    def _cap_driver_length(cls, v: list[str]) -> list[str]:
        return [d[:117] + "..." if len(d) > 120 else d for d in v]

    @field_validator("conviction_score", mode="before")
    @classmethod
    def _round_conviction(cls, v: float | str | int) -> float:
        return round(float(v), 2)

    @classmethod
    def neutral_fallback(cls, raw: str | None = None) -> LocalMorningBriefResult:
        return cls(
            risk_regime=RiskRegime.NEUTRAL,
            conviction_score=0.0,
            key_drivers=[
                "Insufficient data for regime determination",
                "Invalid or malformed LLM response",
                "Operate with reduced size until next evaluation",
            ],
            sector_tilt=SectorTilt.NEUTRO,
            generated_at=datetime.now(UTC),
            llm_raw_response=raw,
            is_fallback=True,
        )


# ─────────────────────────────────────────────────────────────────────────────
# ASSEMBLER
# ─────────────────────────────────────────────────────────────────────────────

_LLM_JSON_SCHEMA: str = json.dumps(
    {
        "risk_regime": "RISK_ON | RISK_OFF | NEUTRAL | SHOCK",
        "conviction_score": "<float 0.0–1.0>",
        "key_drivers": ["<catalyzed 1>", "<catalyzed 2>", "<catalyzed 3>"],
        "sector_tilt": "Tecnología | Defensivos | Bancos | Energía | Salud | ...",
        "generated_at": "<ISO-8601 UTC datetime>",
    },
    indent=2,
    ensure_ascii=False,
)

_SYSTEM_PREAMBLE: str = (
    textwrap.dedent(
        """\
    Eres el Chief Macro Strategist de un hedge fund cuantitativo.
    Sintetiza el entorno global pre-mercado y emite un dictamen de Apetito de Riesgo.
    REGLAS: Responde ÚNICAMENTE con JSON. Exactamente 3 key_drivers.
"""
    )
    + _LLM_JSON_SCHEMA
)


class BriefPromptAssembler:
    """Stateless constructor for Institutional prompts."""

    @staticmethod
    def build_system_prompt() -> str:
        return _SYSTEM_PREAMBLE

    @staticmethod
    def build_user_payload(
        macro: MacroSnapshot,
        news_events: list[NewsEvent],
        briefing_date: date | None = None,
    ) -> str:
        date_str = (briefing_date or date.today()).isoformat()
        top_news = sorted(news_events, key=lambda e: e.impact_score, reverse=True)

        sections: list[str] = [
            f"Header: Morning Briefing {date_str}",
            f"VIX: {macro.vix_level:.2f} (Δ1d: {macro.vix_1d_change:+.2f})",
            f"Treasuries: 10Y {macro.us_10y_yield:.3f}% | 2Y {macro.us_2y_yield:.3f}%",
            f"Futures: SPX {macro.spx_futures_pct:+.2f}% | NDX {macro.ndx_futures_pct:+.2f}%",
            f"Credit: IG {macro.ig_spread_bps:.0f} | HY {macro.hy_spread_bps:.0f}",
            "News Section:",
            *(
                f"- {e.headline} (Impact: {e.impact_score:.2f}, Sent: {e.sentiment:.2f})"
                for e in top_news[:_MAX_NEWS_IN_PROMPT]
            ),
            "Final Instruction: Provide the risk regime dictamen in JSON format.",
        ]
        return "\n\n".join(sections)

    @staticmethod
    def assemble(
        macro: MacroSnapshot,
        news_events: list[NewsEvent],
        briefing_date: date | None = None,
    ) -> dict[str, str]:
        return {
            "system": BriefPromptAssembler.build_system_prompt(),
            "user": BriefPromptAssembler.build_user_payload(macro, news_events, briefing_date),
        }


# ─────────────────────────────────────────────────────────────────────────────
# PARSER
# ─────────────────────────────────────────────────────────────────────────────


class BriefParser:
    """Validates and parses LLM responses into a local MorningBrief result."""

    @staticmethod
    def parse(llm_response: str) -> LocalMorningBriefResult:
        try:
            cleaned = BriefParser._strip_markdown_fences(llm_response)
            raw_dict = BriefParser._safe_json_load(cleaned)
            raw_dict = BriefParser._normalize_enums(raw_dict)
            result = LocalMorningBriefResult.model_validate(raw_dict)
            return result.model_copy(update={"llm_raw_response": llm_response})
        except Exception:
            return LocalMorningBriefResult.neutral_fallback(raw=llm_response)

    @staticmethod
    def _strip_markdown_fences(text: str) -> str:
        text = text.strip()
        if text.startswith("```"):
            lines = text.splitlines()
            inner = [l for l in lines[1:] if l.strip() != "```"]
            text = "\n".join(inner).strip()
        return text

    @staticmethod
    def _safe_json_load(text: str) -> dict[str, object]:
        try:
            return json.loads(text)
        except json.JSONDecodeError:
            start, end = text.find("{"), text.rfind("}")
            if start != -1 and end != -1:
                return json.loads(text[start : end + 1])
            raise ValueError("No valid JSON found.")

    @staticmethod
    def _normalize_enums(data: dict[str, object]) -> dict[str, object]:
        if "risk_regime" in data and isinstance(data["risk_regime"], str):
            data["risk_regime"] = data["risk_regime"].upper().strip()
        if "key_drivers" in data and isinstance(data["key_drivers"], list):
            while len(data["key_drivers"]) < 3:
                data["key_drivers"].append("N/A")
            data["key_drivers"] = data["key_drivers"][:3]
        return data


# ─────────────────────────────────────────────────────────────────────────────
# FACADE
# ─────────────────────────────────────────────────────────────────────────────


class MorningBriefingEngine:
    """Main facade for the Morning Briefing Engine."""

    @staticmethod
    def build_payload(
        macro: MacroSnapshot,
        news_events: list[NewsEvent],
        briefing_date: date | None = None,
    ) -> dict[str, str]:
        return BriefPromptAssembler.assemble(macro, news_events, briefing_date)

    @staticmethod
    def parse_llm_response(llm_raw: str) -> LocalMorningBriefResult:
        return BriefParser.parse(llm_raw)


# ─────────────────────────────────────────────────────────────────────
# MIGRATION AUDIT — SECTOR: DATA
# Archivo          : morning_briefing.py
# Sub-capa         : Engines
# Enfoque          : Generación y parseo de briefings pre-mercado.
# Eliminado        : Comentarios legacy de QB V1, ruido visual excesivo.
# Preservado       : Lógica de asamblado de prompts, validación fail-graceful.
# ─────────────────────────────────────────────────────────────────────
