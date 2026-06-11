"""Deterministic AI-ready evidence packs and token budgeting.

This module prepares compact, typed payloads for LLM agents. LLMs should
interpret these packs instead of discovering signals from raw text or large
frontend/API payloads.
"""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass, field
from typing import Final

from backend.config.logger_setup import get_logger
from backend.domain.thesis_v2 import ThesisBlock
from backend.services.engine_evidence import (
    EngineEvidenceCompiler,
    FinalReportEvidenceAssembler,
    FundamentalEvidenceCompiler,
    GGALRoleEvidenceCompiler,
    MacroArgentinaEvidenceCompiler,
    MesaDineroEvidenceCompiler,
    OptionsEvidenceCompiler,
    PortfolioRiskEvidenceCompiler,
    PriceTargetsEvidenceCompiler,
    ProbabilisticEvidenceCompiler,
    TechnicalEvidenceCompiler,
    TranscriptIntelligenceEvidenceCompiler,
    compact_value_is_empty,
)

logger = get_logger(__name__)

_RAW_KEYS: Final[set[str]] = {
    "raw",
    "history",
    "ohlcv",
    "candles",
    "overlays",
    "dataframe",
    "prices",
    "chain",
    "bid_depth",
    "ask_depth",
}
_BULLISH_TERMS: Final[tuple[str, ...]] = (
    "record",
    "accelerat",
    "beat",
    "outperform",
    "strong demand",
    "margin expansion",
    "guidance raised",
    "robust growth",
    "confident",
)
_BEARISH_TERMS: Final[tuple[str, ...]] = (
    "headwind",
    "challeng",
    "uncertain",
    "slowing",
    "miss",
    "pressure",
    "margin compression",
    "guidance lowered",
    "softening",
)
_ALERT_TERMS: Final[tuple[str, ...]] = (
    "material weakness",
    "going concern",
    "liquidity crisis",
    "debt covenant",
    "default risk",
    "restatement",
    "fraud",
    "sec investigation",
)
_EVASIVE_TERMS: Final[tuple[str, ...]] = (
    "no comment",
    "can't comment",
    "cannot comment",
    "not going to comment",
    "under review",
    "too early",
    "we do not disclose",
)
_THEME_TERMS: Final[dict[str, tuple[str, ...]]] = {
    "guidance": ("guidance", "outlook", "forecast"),
    "capex": ("capex", "capital expenditure", "investment plan"),
    "margin": ("margin", "gross profit", "operating leverage"),
    "liquidity": ("liquidity", "cash flow", "debt", "covenant"),
    "controls": ("material weakness", "internal control", "restatement"),
}
_TOKEN_CHARS: Final[int] = 4


@dataclass(frozen=True)
class EvidenceCard:
    """Compact domain evidence for one agent or analysis block."""

    domain: str
    title: str
    facts: dict[str, object] = field(default_factory=dict)
    risks: list[str] = field(default_factory=list)
    invalidations: list[str] = field(default_factory=list)
    missing_data: list[str] = field(default_factory=list)
    confidence: float | None = None
    quotes: list[str] = field(default_factory=list)
    source_engines: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class AIReadyPayload:
    """Serializable payload intended to be consumed by an LLM prompt."""

    domain: str
    symbol: str | None
    cards: list[EvidenceCard]
    metadata: dict[str, object] = field(default_factory=dict)

    @property
    def signal_score(self: AIReadyPayload) -> float:
        value = self.metadata.get("signal_score")
        try:
            return float(value)  # type: ignore[arg-type]
        except (TypeError, ValueError):
            return 0.0

    @property
    def has_critical_risk(self: AIReadyPayload) -> bool:
        return bool(self.metadata.get("has_critical_risk"))

    def to_dict(self: AIReadyPayload) -> dict[str, object]:
        return {
            "domain": self.domain,
            "symbol": self.symbol,
            "metadata": self.metadata,
            "cards": [
                {
                    "domain": card.domain,
                    "title": card.title,
                    "facts": card.facts,
                    "risks": card.risks,
                    "invalidations": card.invalidations,
                    "missing_data": card.missing_data,
                    "confidence": card.confidence,
                    "quotes": card.quotes,
                    "source_engines": card.source_engines,
                }
                for card in self.cards
            ],
        }

    def to_prompt_json(self: AIReadyPayload, max_chars: int = 2_500) -> str:
        payload = self.to_dict()
        text = _json_dumps(payload)
        if len(text) <= max_chars:
            return text

        slim_cards: list[dict[str, object]] = []
        for card in self.cards[:4]:
            slim_cards.append(
                {
                    "domain": card.domain,
                    "title": card.title,
                    "facts": _limit_mapping(card.facts, 12),
                    "risks": card.risks[:5],
                    "invalidations": card.invalidations[:4],
                    "missing_data": card.missing_data[:4],
                    "confidence": card.confidence,
                    "quotes": [_truncate_text(quote, 180) for quote in card.quotes[:3]],
                    "source_engines": card.source_engines[:8],
                }
            )
        text = _json_dumps(
            {
                "domain": self.domain,
                "symbol": self.symbol,
                "metadata": _limit_mapping(self.metadata, 8),
                "cards": slim_cards,
            }
        )
        if len(text) <= max_chars:
            return text

        minimal = {
            "domain": self.domain,
            "symbol": self.symbol,
            "cards": [
                {
                    "domain": card.domain,
                    "title": card.title,
                    "facts": _limit_mapping(card.facts, 6),
                    "risks": card.risks[:3],
                    "invalidations": card.invalidations[:2],
                    "confidence": card.confidence,
                    "source_engines": card.source_engines[:6],
                }
                for card in self.cards[:3]
            ],
            "truncated": True,
        }
        text = _json_dumps(minimal)
        if len(text) <= max_chars:
            return text
        return _json_dumps(
            {
                "domain": self.domain,
                "symbol": self.symbol,
                "cards": [
                    {
                        "title": card.title,
                        "facts": _limit_mapping(card.facts, 3),
                        "risks": card.risks[:2],
                        "source_engines": card.source_engines[:4],
                    }
                    for card in self.cards[:2]
                ],
                "truncated": True,
            }
        )[:max_chars]


@dataclass(frozen=True)
class TokenBudgetReport:
    """Budget evaluation for one LLM invocation."""

    agent_name: str
    provider: str
    input_est_tokens: int
    max_output_tokens: int
    total_est_tokens: int
    payload_hash: str
    skipped: bool = False
    skipped_reason: str | None = None


class LLMTokenBudgetEngine:
    """Applies per-agent input budgets before LLM calls."""

    def __init__(
        self: LLMTokenBudgetEngine,
        *,
        enabled: bool = True,
        max_input_tokens_per_agent: int = 6_000,
        max_total_tokens_per_thesis: int = 24_000,
    ) -> None:
        self.enabled = enabled
        self.max_input_tokens_per_agent = max(100, max_input_tokens_per_agent)
        self.max_total_tokens_per_thesis = max(1_000, max_total_tokens_per_thesis)

    def check(
        self: LLMTokenBudgetEngine,
        *,
        agent_name: str,
        provider: str,
        payload: str,
        max_output_tokens: int,
    ) -> TokenBudgetReport:
        input_tokens = estimate_tokens(payload)
        total_tokens = input_tokens + max_output_tokens
        skipped = False
        reason: str | None = None
        if self.enabled and input_tokens > self.max_input_tokens_per_agent:
            skipped = True
            reason = "input_budget_exceeded"
        elif self.enabled and total_tokens > self.max_total_tokens_per_thesis:
            skipped = True
            reason = "total_budget_exceeded"
        return TokenBudgetReport(
            agent_name=agent_name,
            provider=provider,
            input_est_tokens=input_tokens,
            max_output_tokens=max_output_tokens,
            total_est_tokens=total_tokens,
            payload_hash=hash_payload(payload),
            skipped=skipped,
            skipped_reason=reason,
        )


class AIReadyPayloadEngine:
    """Factory for compact LLM evidence packs."""

    def build_engine_pack(
        self: AIReadyPayloadEngine,
        domain: str,
        symbol: str,
        payload: dict[str, object],
        *,
        agent_name: str | None = None,
    ) -> AIReadyPayload:
        compiler = _compiler_for_domain(domain)
        evidence = compiler.compile(symbol, payload, agent_name=agent_name)
        return AIReadyPayload(
            domain=evidence.domain,
            symbol=evidence.symbol,
            cards=[
                EvidenceCard(
                    domain=card.domain,
                    title=card.title,
                    facts=card.facts,
                    risks=card.risks,
                    invalidations=card.invalidations,
                    missing_data=card.missing_data,
                    confidence=card.confidence,
                    source_engines=card.source_engines,
                )
                for card in evidence.cards
            ],
            metadata={
                **evidence.metadata,
                "signal_score": evidence.signal_score,
                "has_critical_risk": evidence.has_critical_risk,
            },
        )

    def build_context_pack(
        self: AIReadyPayloadEngine,
        agent_name: str,
        symbol: str,
        context: dict[str, object],
    ) -> AIReadyPayload:
        domain = {
            "macro_micro": "macro_argentina",
            "argentina": "macro_argentina",
            "sentiment": "fundamental",
            "transcript_analyst": "fundamental",
        }.get(agent_name, "probabilistic")
        return self.build_engine_pack(domain, symbol, context, agent_name=agent_name)

    def build_ggal_role_pack(
        self: AIReadyPayloadEngine,
        agent_name: str,
        symbol: str,
        snapshot_payload: dict[str, object],
    ) -> AIReadyPayload:
        return self.build_engine_pack(
            "ggal_options",
            symbol,
            snapshot_payload,
            agent_name=agent_name,
        )

    def build_transcript_intelligence_pack(
        self: AIReadyPayloadEngine,
        symbol: str,
        transcript_payload: dict[str, object],
    ) -> AIReadyPayload:
        return self.build_engine_pack(
            "transcript_intelligence",
            symbol,
            transcript_payload,
            agent_name="transcript_analyst",
        )

    def build_transcript_pack(
        self: AIReadyPayloadEngine, symbol: str, transcript: str
    ) -> AIReadyPayload:
        text = " ".join(str(transcript).split())
        lower = text.lower()
        bull = _count_terms(lower, _BULLISH_TERMS)
        bear = _count_terms(lower, _BEARISH_TERMS)
        alerts = _matched_terms(lower, _ALERT_TERMS)
        evasive_hits = _matched_terms(lower, _EVASIVE_TERMS)
        themes = _matched_themes(lower)
        tone = "BULLISH" if bull > bear else "BEARISH" if bear > bull else "NEUTRAL"
        if alerts:
            tone = "ALERT"
        quotes = _extract_quotes(text, (*_BULLISH_TERMS, *_BEARISH_TERMS, *_ALERT_TERMS), limit=3)
        facts: dict[str, object] = {
            "tone": tone,
            "bullish_hits": bull,
            "bearish_hits": bear,
            "evasiveness_hits": len(evasive_hits),
            "themes": themes[:6],
            "char_count": len(text),
        }
        risks = alerts + [f"evasiveness:{term}" for term in evasive_hits[:3]]
        return AIReadyPayload(
            domain="transcript",
            symbol=symbol.upper().strip(),
            cards=[
                EvidenceCard(
                    domain="transcript",
                    title="earnings_call_evidence",
                    facts=facts,
                    risks=risks[:8],
                    invalidations=["management_tone_shift"] if alerts or evasive_hits else [],
                    confidence=min(0.9, 0.35 + 0.08 * (bull + bear + len(alerts))),
                    quotes=quotes,
                )
            ],
            metadata={"source": "TranscriptEvidenceEngine", "raw_chars": len(transcript)},
        )

    def build_technical_pack(
        self: AIReadyPayloadEngine, payload: dict[str, object]
    ) -> AIReadyPayload:
        facts: dict[str, object] = {
            "symbol": payload.get("symbol"),
            "timeframe": payload.get("timeframe"),
            "last_date": payload.get("last_date"),
            "last_close": payload.get("last_close"),
        }
        indicators = _as_dict(payload.get("indicators"))
        facts.update(_prefixed(indicators, "indicator", ("rsi", "atr", "ema21", "vwap", "sma20")))
        smc = _as_dict(payload.get("smc"))
        fractal = _as_dict(payload.get("fractal"))
        facts["smc_bias"] = smc.get("sesgo") or smc.get("bias")
        facts["smc_score"] = smc.get("composite_score")
        facts["fractal_trend"] = fractal.get("trend") or fractal.get("sesgo")
        facts["fractal_confidence"] = fractal.get("confidence")

        engine_facts = self._technical_engine_facts(_as_dict(payload.get("engines")))
        facts.update(engine_facts)
        signals = _compact_sequence(payload.get("signals"), limit=8)
        risks = _technical_risks(facts)
        candles = payload.get("candles")
        return AIReadyPayload(
            domain="technical",
            symbol=str(payload.get("symbol") or "").upper() or None,
            cards=[
                EvidenceCard(
                    domain="technical",
                    title="technical_evidence",
                    facts={key: value for key, value in facts.items() if value is not None},
                    risks=risks,
                    invalidations=_technical_invalidations(facts),
                    confidence=_safe_float(facts.get("smc_score")),
                    quotes=[_json_dumps(item) for item in signals[:3]],
                )
            ],
            metadata={
                "source": "TechnicalEvidenceEngine",
                "source_series": {
                    "price_bars": len(candles) if isinstance(candles, list) else 0,
                },
                "signals": signals,
            },
        )

    def build_thesis_pack(
        self: AIReadyPayloadEngine, agent_name: str, block: ThesisBlock
    ) -> AIReadyPayload:
        metrics = block.metrics if isinstance(block.metrics, dict) else {}
        compact = _drop_raw_count_keys(self.compact_for_agent(agent_name, metrics))
        risks = [
            key
            for key, value in compact.items()
            if _is_risk_key(key) and not compact_value_is_empty(value, treat_false_as_empty=True)
        ][:8]
        invalidations: list[str] = []
        if compact.get("gate_veto") is True:
            invalidations.append("gate_veto_active")
        if _safe_float(compact.get("cvar_99")) > 0.05:
            invalidations.append("cvar_99_above_5pct")
        missing_data = [
            key
            for key, value in compact.items()
            if isinstance(value, str) and value.upper() in {"N/A", "NO DISPONIBLE", "UNAVAILABLE"}
        ][:6]
        return AIReadyPayload(
            domain="thesis",
            symbol=str(compact.get("symbol") or "").upper() or None,
            cards=[
                EvidenceCard(
                    domain=agent_name,
                    title=f"{agent_name}_evidence",
                    facts=compact,
                    risks=risks,
                    invalidations=invalidations,
                    missing_data=missing_data,
                    confidence=block.confidence,
                )
            ],
            metadata={"source": block.source, "agent": agent_name},
        )

    def compact_for_agent(
        self: AIReadyPayloadEngine, agent_name: str, obj: object
    ) -> dict[str, object]:
        if not isinstance(obj, dict):
            return {"value": _compact_value(obj)}
        hints = _agent_hints(agent_name)
        compact: dict[str, object] = {}
        for key, value in obj.items():
            key_text = str(key).lower()
            if key_text.startswith("_") and key_text != "_error":
                continue
            if key_text in _RAW_KEYS:
                if isinstance(value, list):
                    compact[f"{key_text}_count"] = len(value)
                continue
            if any(hint in key_text for hint in hints) or isinstance(value, bool | int | float):
                compact[str(key)] = _compact_value(value)
            if len(compact) >= 18:
                compact["_truncated_keys"] = True
                break
        if compact:
            return compact
        fallback = _compact_value(obj)
        return fallback if isinstance(fallback, dict) else {"value": fallback}

    def _technical_engine_facts(
        self: AIReadyPayloadEngine, engines: dict[str, object]
    ) -> dict[str, object]:
        facts: dict[str, object] = {}
        volume_profile = _as_dict(engines.get("volume_profile"))
        facts.update(
            _prefixed(volume_profile, "volume_profile", ("poc", "vah", "val", "volume_bias"))
        )
        fvg = _as_dict(engines.get("fvg") or engines.get("fair_value_gap"))
        facts.update(
            _prefixed(fvg, "fvg", ("active_count", "bullish_active_count", "bearish_active_count"))
        )
        vwap = _as_dict(engines.get("vwap_advanced"))
        facts.update(_prefixed(vwap, "vwap", ("price_vs_vwap", "price_zscore", "above_vwap")))
        return facts


def estimate_tokens(text: str) -> int:
    return max(1, (len(text) + _TOKEN_CHARS - 1) // _TOKEN_CHARS)


def hash_payload(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8", errors="ignore")).hexdigest()[:16]


def _json_dumps(value: object) -> str:
    return json.dumps(value, default=str, ensure_ascii=False, separators=(",", ":"))


def _truncate_text(text: object, max_chars: int) -> str:
    cleaned = " ".join(str(text).split())
    if len(cleaned) <= max_chars:
        return cleaned
    return cleaned[: max_chars - 3] + "..."


def _limit_mapping(value: dict[str, object], limit: int) -> dict[str, object]:
    out: dict[str, object] = {}
    for key, item in value.items():
        if compact_value_is_empty(item):
            continue
        out[key] = _compact_value(item)
        if len(out) >= limit:
            out["_truncated_keys"] = True
            break
    return out


def _compact_value(value: object, *, depth: int = 0) -> object:
    if value is None or isinstance(value, bool | int | float):
        return value
    if isinstance(value, str):
        return _truncate_text(value, 420)
    if isinstance(value, dict):
        if depth >= 3:
            return {"_keys": list(value.keys())[:6], "_truncated_depth": True}
        return _limit_mapping({str(key): item for key, item in value.items()}, 12)
    if isinstance(value, list | tuple | set):
        items = list(value)
        if not items:
            return []
        if all(isinstance(item, bool | int | float | str) for item in items):
            return [_compact_value(item, depth=depth + 1) for item in items[:6]]
        return {
            "count": len(items),
            "sample": [_compact_value(item, depth=depth + 1) for item in items[:2]],
        }
    return _truncate_text(value, 420)


def _count_terms(text: str, terms: tuple[str, ...]) -> int:
    return sum(1 for term in terms if term in text)


def _matched_terms(text: str, terms: tuple[str, ...]) -> list[str]:
    return [term for term in terms if term in text]


def _matched_themes(text: str) -> list[str]:
    return [theme for theme, terms in _THEME_TERMS.items() if any(term in text for term in terms)]


def _extract_quotes(text: str, terms: tuple[str, ...], *, limit: int) -> list[str]:
    sentences = re.split(r"(?<=[.!?])\s+", text)
    quotes: list[str] = []
    for sentence in sentences:
        lower = sentence.lower()
        if any(term in lower for term in terms):
            quotes.append(_truncate_text(sentence, 220))
        if len(quotes) >= limit:
            break
    return quotes


def _as_dict(value: object) -> dict[str, object]:
    return value if isinstance(value, dict) else {}


def _prefixed(
    source: dict[str, object],
    prefix: str,
    keys: tuple[str, ...],
) -> dict[str, object]:
    return {
        f"{prefix}_{key}": source.get(key)
        for key in keys
        if not compact_value_is_empty(source.get(key))
    }


def _compact_sequence(value: object, *, limit: int) -> list[object]:
    if not isinstance(value, list):
        return []
    return [_compact_value(item) for item in value[:limit]]


def _safe_float(value: object) -> float:
    if not isinstance(value, str | int | float):
        return 0.0
    try:
        return float(value)
    except (TypeError, ValueError):
        return 0.0


def _technical_risks(facts: dict[str, object]) -> list[str]:
    risks: list[str] = []
    rsi = _safe_float(facts.get("indicator_rsi"))
    if rsi >= 70:
        risks.append("rsi_overbought")
    if rsi and rsi <= 30:
        risks.append("rsi_oversold")
    if _safe_float(facts.get("fvg_active_count")) > 0:
        risks.append("active_fvg_zones")
    if facts.get("vwap_above_vwap") is False:
        risks.append("below_vwap")
    return risks[:6]


def _technical_invalidations(facts: dict[str, object]) -> list[str]:
    invalidations: list[str] = []
    if facts.get("smc_bias") in {"BEARISH", "bajista"}:
        invalidations.append("smc_bearish_bias")
    if _safe_float(facts.get("indicator_rsi")) <= 30:
        invalidations.append("oversold_reversal_risk")
    return invalidations[:4]


def _is_risk_key(key: str) -> bool:
    lowered = key.lower()
    return any(
        marker in lowered
        for marker in ("risk", "cvar", "var", "jump", "veto", "drawdown", "tail", "alert")
    )


def _drop_raw_count_keys(value: dict[str, object]) -> dict[str, object]:
    return {
        key: item
        for key, item in value.items()
        if key.lower() not in {f"{raw_key}_count" for raw_key in _RAW_KEYS}
    }


def _agent_hints(agent_name: str) -> tuple[str, ...]:
    hints: dict[str, tuple[str, ...]] = {
        "options_gex": ("symbol", "spot", "gex", "gamma", "iv", "wall", "strike", "delta"),
        "technical": ("symbol", "price", "trend", "bias", "rsi", "atr", "vwap", "support"),
        "forensic": ("symbol", "revenue", "earnings", "debt", "cash", "margin", "valuation"),
        "microstructure": ("symbol", "cvar", "var", "kelly", "regime", "jump", "veto", "tail"),
        "macro_micro": ("rate", "event", "inflation", "yield", "fed", "macro", "calendar"),
        "transcript_analyst": ("transcript", "tone", "theme", "evasive", "guidance", "margin"),
        "sentiment": ("sentiment", "news", "headline", "social", "buzz", "catalyst"),
        "argentina": ("ccl", "riesgo", "pais", "merval", "inflacion", "bcra", "mep"),
    }
    return hints.get(agent_name, ())


def _compiler_for_domain(domain: str) -> EngineEvidenceCompiler:
    compilers = {
        "technical": TechnicalEvidenceCompiler(),
        "options": OptionsEvidenceCompiler(),
        "probabilistic": ProbabilisticEvidenceCompiler(),
        "fundamental": FundamentalEvidenceCompiler(),
        "macro_argentina": MacroArgentinaEvidenceCompiler(),
        "portfolio_risk": PortfolioRiskEvidenceCompiler(),
        "price_targets": PriceTargetsEvidenceCompiler(),
        "ggal_options": GGALRoleEvidenceCompiler(),
        "transcript_intelligence": TranscriptIntelligenceEvidenceCompiler(),
        "mesa_dinero": MesaDineroEvidenceCompiler(),
        "final_report": FinalReportEvidenceAssembler(),
    }
    return compilers.get(domain, ProbabilisticEvidenceCompiler())
