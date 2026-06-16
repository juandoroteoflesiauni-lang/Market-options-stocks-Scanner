from __future__ import annotations
"""Semantic evidence compilers for AI-ready engine outputs.

The classes in this module sit between deterministic market engines and LLM
agents. They preserve decision evidence and strip raw series so the LLM
interprets verified signals instead of rediscovering math from JSON blobs.
"""


import hashlib
import json
import time
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Final, Protocol

RAW_DENYLIST: Final[tuple[str, ...]] = (
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
    "returns",
    "paths",
    "active_zones",
    "depth",
)
_TOKEN_CHARS: Final[int] = 4


def compact_value_is_empty(value: object, *, treat_false_as_empty: bool = False) -> bool:
    """True if value is a blank placeholder. Avoids `x in (..., [])` with numpy arrays (ambiguous bool)."""
    if value is None:
        return True
    if treat_false_as_empty and value is False:
        return True
    if isinstance(value, str) and value == "":
        return True
    if isinstance(value, list | tuple | set) and len(value) == 0:
        return True
    if isinstance(value, dict) and len(value) == 0:
        return True
    try:
        import numpy as np

        if isinstance(value, np.ndarray):
            return value.size == 0
    except Exception:
        pass
    return False


@dataclass(frozen=True)
class EngineEvidenceProfile:
    engine_name: str
    domain: str
    compiler_name: str
    critical_fields: tuple[str, ...]
    raw_denylist: tuple[str, ...] = RAW_DENYLIST
    confidence_fields: tuple[str, ...] = ("confidence", "quality_score", "score")
    risk_fields: tuple[str, ...] = ("risk", "cvar", "var", "jump", "veto", "drawdown", "tail")
    invalidation_fields: tuple[str, ...] = ("invalid", "veto", "below", "breach", "regime")


@dataclass(frozen=True)
class EngineEvidenceCard:
    domain: str
    title: str
    facts: dict[str, object] = field(default_factory=dict)
    risks: list[str] = field(default_factory=list)
    invalidations: list[str] = field(default_factory=list)
    missing_data: list[str] = field(default_factory=list)
    confidence: float | None = None
    source_engines: list[str] = field(default_factory=list)

    def to_prompt_dict(self: EngineEvidenceCard) -> dict[str, object]:
        payload: dict[str, object] = {
            "domain": self.domain,
            "title": self.title,
            "facts": self.facts,
            "risks": self.risks,
            "invalidations": self.invalidations,
            "missing_data": self.missing_data,
            "confidence": self.confidence,
            "source_engines": self.source_engines,
        }
        return {key: value for key, value in payload.items() if not compact_value_is_empty(value)}


@dataclass(frozen=True)
class EngineEvidencePack:
    domain: str
    symbol: str | None
    cards: list[EngineEvidenceCard]
    signal_score: float
    has_critical_risk: bool = False
    metadata: dict[str, object] = field(default_factory=dict)

    def to_prompt_dict(self: EngineEvidencePack) -> dict[str, object]:
        payload: dict[str, object] = {
            "domain": self.domain,
            "symbol": self.symbol,
            "signal_score": round(self.signal_score, 3),
            "has_critical_risk": self.has_critical_risk,
            "metadata": self.metadata,
            "cards": [card.to_prompt_dict() for card in self.cards],
        }
        return _strip_raw_mapping(payload)

    def to_prompt_json(self: EngineEvidencePack, max_chars: int = 2_500) -> str:
        text = _json_dumps(self.to_prompt_dict())
        if len(text) <= max_chars:
            return text
        slim = {
            "domain": self.domain,
            "symbol": self.symbol,
            "signal_score": round(self.signal_score, 3),
            "has_critical_risk": self.has_critical_risk,
            "cards": [
                {
                    "title": card.title,
                    "facts": _limit_mapping(card.facts, 8),
                    "risks": card.risks[:4],
                    "invalidations": card.invalidations[:3],
                    "confidence": card.confidence,
                    "source_engines": card.source_engines[:6],
                }
                for card in self.cards[:4]
            ],
            "truncated": True,
        }
        text = _json_dumps(_strip_raw_keys(slim))
        if len(text) <= max_chars:
            return text
        return text[:max_chars]


class EngineEvidenceCompiler(Protocol):
    domain: str

    def compile(
        self: EngineEvidenceCompiler,
        symbol: str,
        payload: dict[str, object],
        *,
        agent_name: str | None = None,
    ) -> EngineEvidencePack: ...


class BaseEvidenceCompiler:
    domain = "generic"
    source_engines: tuple[str, ...] = ()

    def compile(
        self: BaseEvidenceCompiler,
        symbol: str,
        payload: dict[str, object],
        *,
        agent_name: str | None = None,
    ) -> EngineEvidencePack:
        facts = self._facts(payload, agent_name=agent_name)
        risks = self._risks(facts, payload)
        invalidations = self._invalidations(facts, payload)
        missing = [key for key, value in facts.items() if compact_value_is_empty(value)]
        confidence = _best_confidence(facts)
        signal_score = _signal_score(facts, risks, invalidations, confidence)
        has_critical = self._has_critical_risk(facts, risks, invalidations)
        return EngineEvidencePack(
            domain=self.domain,
            symbol=symbol.upper().strip() or None,
            cards=[
                EngineEvidenceCard(
                    domain=self.domain,
                    title=f"{self.domain}_engine_evidence",
                    facts={
                        key: value
                        for key, value in facts.items()
                        if not compact_value_is_empty(value)
                    },
                    risks=risks,
                    invalidations=invalidations,
                    missing_data=missing[:6],
                    confidence=confidence,
                    source_engines=list(self.source_engines),
                )
            ],
            signal_score=signal_score,
            has_critical_risk=has_critical,
            metadata={"compiler": type(self).__name__},
        )

    def _facts(
        self: BaseEvidenceCompiler,
        payload: dict[str, object],
        *,
        agent_name: str | None = None,
    ) -> dict[str, object]:
        _ = agent_name
        return _compact_mapping(payload, hints=())

    def _risks(
        self: BaseEvidenceCompiler, facts: dict[str, object], payload: dict[str, object]
    ) -> list[str]:
        _ = payload
        return _generic_risks(facts)

    def _invalidations(
        self: BaseEvidenceCompiler, facts: dict[str, object], payload: dict[str, object]
    ) -> list[str]:
        _ = payload
        return _generic_invalidations(facts)

    def _has_critical_risk(
        self: BaseEvidenceCompiler,
        facts: dict[str, object],
        risks: list[str],
        invalidations: list[str],
    ) -> bool:
        if risks or invalidations:
            return any(
                "critical" in item or "veto" in item or "high" in item
                for item in risks + invalidations
            )
        return False


class TechnicalEvidenceCompiler(BaseEvidenceCompiler):
    domain = "technical"
    source_engines: tuple[str, ...] = (
        "smc",
        "fractal",
        "vwap",
        "vsa",
        "fvg",
        "volume_profile",
        "candle_geometry",
        "market_structure",
        "ofi",
        "order_flow_delta",
        "lob",
        "hmm",
        "tpo",
        "volume_nodes",
    )

    def _facts(
        self: TechnicalEvidenceCompiler,
        payload: dict[str, object],
        *,
        agent_name: str | None = None,
    ) -> dict[str, object]:
        _ = agent_name
        facts = _pick(
            payload,
            (
                "symbol",
                "timeframe",
                "last_date",
                "last_close",
                "support_20d",
                "resistance_20d",
                "trend_regime",
            ),
        )
        facts.update(
            _prefix(
                _as_dict(payload.get("indicators")),
                "indicator",
                ("rsi", "atr", "ema21", "vwap", "sma20"),
            )
        )
        smc = _as_dict(payload.get("smc"))
        facts["smc_bias"] = smc.get("bias") or smc.get("sesgo")
        facts["smc_score"] = smc.get("composite_score") or smc.get("score")
        fractal = _as_dict(payload.get("fractal"))
        facts["fractal_trend"] = fractal.get("trend") or fractal.get("sesgo")
        facts["fractal_confidence"] = fractal.get("confidence")
        engines = _as_dict(payload.get("engines"))
        for engine_name in self.source_engines:
            engine_payload = _as_dict(engines.get(engine_name) or payload.get(engine_name))
            facts.update(_prefixed_decision_fields(engine_name, engine_payload))
        return _strip_empty(_strip_raw_mapping(facts))

    def _risks(
        self: TechnicalEvidenceCompiler, facts: dict[str, object], payload: dict[str, object]
    ) -> list[str]:
        _ = payload
        risks = _generic_risks(facts)
        rsi = _safe_float(facts.get("indicator_rsi"))
        if rsi >= 70:
            risks.append("rsi_overbought")
        if 0 < rsi <= 30:
            risks.append("rsi_oversold")
        if _safe_float(facts.get("fvg_active_count")) > 0:
            risks.append("active_fvg_zones")
        if facts.get("vwap_above_vwap") is False:
            risks.append("below_vwap")
        return _dedupe(risks)[:8]

    def _invalidations(
        self: TechnicalEvidenceCompiler, facts: dict[str, object], payload: dict[str, object]
    ) -> list[str]:
        _ = payload
        invalidations = _generic_invalidations(facts)
        if str(facts.get("smc_bias", "")).upper() in {"BEARISH", "BAJISTA"}:
            invalidations.append("smc_bearish_bias")
        return _dedupe(invalidations)[:6]


class OptionsEvidenceCompiler(BaseEvidenceCompiler):
    domain = "options"
    source_engines: tuple[str, ...] = (
        "options_snapshot",
        "gex",
        "iv_surface",
        "dex",
        "gamma_flip",
        "vol_term",
        "skew",
        "expected_move",
        "squeeze",
        "flow",
        "payoff",
    )

    def _facts(
        self: OptionsEvidenceCompiler,
        payload: dict[str, object],
        *,
        agent_name: str | None = None,
    ) -> dict[str, object]:
        _ = agent_name
        hints = (
            "symbol",
            "spot",
            "gex",
            "gamma",
            "iv",
            "skew",
            "wall",
            "strike",
            "delta",
            "vega",
            "theta",
            "max_pain",
            "pin",
            "flow",
            "squeeze",
            "expected_move",
        )
        return _compact_mapping(payload, hints=hints, limit=32)

    def _risks(
        self: OptionsEvidenceCompiler, facts: dict[str, object], payload: dict[str, object]
    ) -> list[str]:
        _ = payload
        risks = _generic_risks(facts)
        if str(facts.get("gamma_flip_regime", "")).upper() in {
            "NEGATIVE",
            "BEARISH",
            "SHORT_GAMMA",
        }:
            risks.append("negative_gamma_regime")
        if _safe_float(facts.get("iv_rank")) >= 80:
            risks.append("iv_rank_high")
        return _dedupe(risks)[:8]


class ProbabilisticEvidenceCompiler(BaseEvidenceCompiler):
    domain = "probabilistic"
    source_engines: tuple[str, ...] = (
        "evt_cvar",
        "mjd_jumps",
        "heston",
        "particle_filter",
        "markov",
        "cor3m",
        "fear_greed",
        "factor_calibration",
        "multimodal_predictive",
        "cross_asset",
    )

    def _facts(
        self: ProbabilisticEvidenceCompiler,
        payload: dict[str, object],
        *,
        agent_name: str | None = None,
    ) -> dict[str, object]:
        _ = agent_name
        hints = (
            "symbol",
            "cvar",
            "var",
            "kelly",
            "gate",
            "veto",
            "jump",
            "regime",
            "markov",
            "heston",
            "vov",
            "fear",
            "greed",
            "cor3m",
            "win_prob",
            "tail",
            "squeeze",
            "gamma_flip",
            "vol_term",
            "pillar",
        )
        return _compact_mapping(payload, hints=hints, limit=34)

    def _risks(
        self: ProbabilisticEvidenceCompiler, facts: dict[str, object], payload: dict[str, object]
    ) -> list[str]:
        _ = payload
        risks = _generic_risks(facts)
        if _safe_float(facts.get("cvar_99")) >= 0.05:
            risks.append("cvar_99_high")
        if facts.get("gate_veto") is True:
            risks.append("gate_veto_active")
        jump_prob = max(
            _safe_float(facts.get("jump_probability")),
            _safe_float(facts.get("jump_prob")),
            _safe_float(facts.get("engine_jumps_prob")),
        )
        if jump_prob >= 0.12:
            risks.append("jump_probability_high")
        return _dedupe(risks)[:10]

    def _invalidations(
        self: ProbabilisticEvidenceCompiler, facts: dict[str, object], payload: dict[str, object]
    ) -> list[str]:
        _ = payload
        invalidations = _generic_invalidations(facts)
        if facts.get("gate_veto") is True:
            invalidations.append("risk_gate_veto")
        return _dedupe(invalidations)[:8]

    def _has_critical_risk(
        self: ProbabilisticEvidenceCompiler,
        facts: dict[str, object],
        risks: list[str],
        invalidations: list[str],
    ) -> bool:
        _ = invalidations
        return (
            bool(risks)
            or facts.get("gate_veto") is True
            or _safe_float(facts.get("cvar_99")) >= 0.05
        )


class FundamentalEvidenceCompiler(BaseEvidenceCompiler):
    domain = "fundamental"
    source_engines: tuple[str, ...] = (
        "profile",
        "ratios",
        "statements",
        "valuation",
        "forensic_flags",
        "insider",
        "catalyst_nlp",
        "transcript",
    )

    def _facts(
        self: FundamentalEvidenceCompiler,
        payload: dict[str, object],
        *,
        agent_name: str | None = None,
    ) -> dict[str, object]:
        _ = agent_name
        hints = (
            "symbol",
            "sector",
            "industry",
            "revenue",
            "income",
            "eps",
            "margin",
            "debt",
            "cash",
            "liquidity",
            "valuation",
            "pe",
            "pb",
            "roe",
            "roa",
            "altman",
            "piotroski",
            "beneish",
            "target",
            "tone",
            "transcript",
        )
        return _compact_mapping(payload, hints=hints, limit=34)


class MacroArgentinaEvidenceCompiler(BaseEvidenceCompiler):
    domain = "macro_argentina"
    source_engines: tuple[str, ...] = (
        "fred",
        "bcra_fx",
        "riesgo_pais",
        "merval_usd",
        "inflation",
        "regulatory_scanner",
    )

    def _facts(
        self: MacroArgentinaEvidenceCompiler,
        payload: dict[str, object],
        *,
        agent_name: str | None = None,
    ) -> dict[str, object]:
        _ = agent_name
        hints = (
            "fred",
            "rate",
            "yield",
            "inflation",
            "cpi",
            "unemployment",
            "bcra",
            "ccl",
            "mep",
            "blue",
            "riesgo",
            "pais",
            "merval",
            "regulatory",
            "event",
        )
        return _compact_mapping(payload, hints=hints, limit=30)


class PortfolioRiskEvidenceCompiler(BaseEvidenceCompiler):
    domain = "portfolio_risk"
    source_engines: tuple[str, ...] = (
        "kelly",
        "drawdown",
        "portfolio_optimization",
        "hrp",
        "execution",
        "outcome_records",
    )

    def _facts(
        self: PortfolioRiskEvidenceCompiler,
        payload: dict[str, object],
        *,
        agent_name: str | None = None,
    ) -> dict[str, object]:
        _ = agent_name
        hints = (
            "kelly",
            "drawdown",
            "allocation",
            "weight",
            "var",
            "cvar",
            "risk",
            "execution",
            "slippage",
            "outcome",
            "sharpe",
            "volatility",
            "turnover",
        )
        return _compact_mapping(payload, hints=hints, limit=30)


class PriceTargetsEvidenceCompiler(ProbabilisticEvidenceCompiler):
    domain = "price_targets"
    source_engines: tuple[str, ...] = (
        "price_targets",
        "evt_cvar",
        "mjd_jumps",
        "heston",
        "markov",
    )

    def _facts(
        self: PriceTargetsEvidenceCompiler,
        payload: dict[str, object],
        *,
        agent_name: str | None = None,
    ) -> dict[str, object]:
        _ = agent_name
        facts = _pick(payload, ("symbol", "current_price", "spot"))
        horizons = payload.get("horizons") or payload.get("horizons_data")
        if isinstance(horizons, list):
            facts["horizons"] = [
                _pick(
                    _as_dict(item), ("days", "label", "p25", "p50", "p75", "expected_pct", "bias")
                )
                for item in horizons[:9]
                if isinstance(item, dict)
            ]
        snapshot = _as_dict(payload.get("engine_snapshot"))
        facts.update(_flatten_snapshot(snapshot, prefix="engine", limit=18))
        return _strip_empty(_strip_raw_mapping(facts))


class GGALRoleEvidenceCompiler(OptionsEvidenceCompiler):
    domain = "ggal_options"
    source_engines: tuple[str, ...] = (
        "ggal_underlying",
        "ggal_surface",
        "ggal_dealer_positioning",
        "ggal_flow",
        "ggal_risk_gate",
    )

    def _facts(
        self: GGALRoleEvidenceCompiler,
        payload: dict[str, object],
        *,
        agent_name: str | None = None,
    ) -> dict[str, object]:
        role_hints = {
            "gex_analyst": ("gex", "gamma", "wall", "flip", "dealer", "strike"),
            "iv_surface_analyst": ("iv", "surface", "skew", "term", "vol"),
            "flow_analyst": ("flow", "volume", "open_interest", "ratio"),
            "smc_overlay": ("smc", "underlying", "support", "resistance", "trend"),
            "ccl_monitor": ("ccl", "fx", "adr", "ratio", "underlying"),
            "sentiment_analyst": ("sentiment", "news", "risk", "regime"),
            "forensic_analyst": ("roe", "bank", "fundamental", "risk", "credit"),
        }
        hints = role_hints.get(agent_name or "", ()) + ("symbol", "status", "limitation")
        return _compact_mapping(payload, hints=hints, limit=28)


class TranscriptIntelligenceEvidenceCompiler(BaseEvidenceCompiler):
    domain = "transcript_intelligence"
    source_engines: tuple[str, ...] = ("fmp_transcript", "transcript_nlp", "catalyst_nlp")
    alert_terms: tuple[str, ...] = (
        "material weakness",
        "going concern",
        "debt covenant",
        "default risk",
        "restatement",
        "fraud",
        "investigation",
        "margin compression",
        "guidance lowered",
    )
    evasive_terms: tuple[str, ...] = (
        "no comment",
        "cannot comment",
        "can't comment",
        "we do not disclose",
        "under review",
        "too early",
    )

    def _facts(
        self: TranscriptIntelligenceEvidenceCompiler,
        payload: dict[str, object],
        *,
        agent_name: str | None = None,
    ) -> dict[str, object]:
        _ = agent_name
        text = _squash_text(
            str(payload.get("content") or payload.get("transcript") or payload.get("text") or "")
        )
        lower = text.lower()
        alerts = [term for term in self.alert_terms if term in lower]
        evasive = [term for term in self.evasive_terms if term in lower]
        facts = _pick(payload, ("symbol", "year", "quarter", "date", "available"))
        facts.update(
            {
                "tone": _transcript_tone(lower, alerts),
                "alert_terms": alerts[:6],
                "evasiveness_terms": evasive[:5],
                "themes": _transcript_themes(lower),
                "quotes": _extract_relevant_sentences(
                    text, (*self.alert_terms, *self.evasive_terms)
                ),
                "raw_chars": len(text),
            }
        )
        return _strip_empty(_strip_raw_mapping(facts))

    def _risks(
        self: TranscriptIntelligenceEvidenceCompiler,
        facts: dict[str, object],
        payload: dict[str, object],
    ) -> list[str]:
        _ = payload
        risks = [f"alert:{term}" for term in _as_str_list(facts.get("alert_terms"))]
        risks.extend(f"evasiveness:{term}" for term in _as_str_list(facts.get("evasiveness_terms")))
        return _dedupe(risks)[:8]

    def _invalidations(
        self: TranscriptIntelligenceEvidenceCompiler,
        facts: dict[str, object],
        payload: dict[str, object],
    ) -> list[str]:
        _ = payload
        invalidations = []
        if facts.get("tone") in {"ALERT", "BEARISH"}:
            invalidations.append("management_transcript_risk")
        return invalidations

    def _has_critical_risk(
        self: TranscriptIntelligenceEvidenceCompiler,
        facts: dict[str, object],
        risks: list[str],
        invalidations: list[str],
    ) -> bool:
        _ = invalidations
        return bool(risks) or facts.get("tone") == "ALERT"


class MesaDineroEvidenceCompiler(BaseEvidenceCompiler):
    domain = "mesa_dinero"
    source_engines: tuple[str, ...] = ("thesis_v2", "risk_assessment", "mesa_stream")

    def _facts(
        self: MesaDineroEvidenceCompiler,
        payload: dict[str, object],
        *,
        agent_name: str | None = None,
    ) -> dict[str, object]:
        _ = agent_name
        facts = _pick(
            payload,
            (
                "symbol",
                "bias",
                "conviction",
                "multimodal_synthesis",
                "tactical_recommendation",
            ),
        )
        facts.update(
            _prefix(
                _as_dict(payload.get("risk_assessment")),
                "risk",
                ("cvar_99", "tail_risk", "jump_risk", "gate_veto", "kelly_fraction", "etv"),
            )
        )
        facts["invalidations"] = _compact_value(payload.get("invalidations") or [])
        return _strip_empty(_strip_raw_mapping(facts))


class FinalReportEvidenceAssembler(BaseEvidenceCompiler):
    domain = "final_report"
    source_engines: tuple[str, ...] = (
        "evidence_registry",
        "domain_narratives",
        "risk_gate",
        "final_report",
    )

    def _facts(
        self: FinalReportEvidenceAssembler,
        payload: dict[str, object],
        *,
        agent_name: str | None = None,
    ) -> dict[str, object]:
        _ = agent_name
        hints = (
            "symbol",
            "executive",
            "bias",
            "conviction",
            "risk",
            "invalidation",
            "limitation",
            "sizing",
            "recommendation",
            "confidence",
            "conflict",
        )
        return _compact_mapping(payload, hints=hints, limit=36)


class EngineEvidenceRegistry:
    def __init__(self: EngineEvidenceRegistry, profiles: tuple[EngineEvidenceProfile, ...]) -> None:
        self.profiles = profiles

    @classmethod
    def default(cls: type[EngineEvidenceRegistry]) -> EngineEvidenceRegistry:
        specs = {
            "technical": (
                "smc",
                "fractal",
                "vwap",
                "vsa",
                "fvg",
                "volume_profile",
                "candle_geometry",
                "market_structure",
                "ofi",
                "order_flow_delta",
                "lob",
                "hmm",
                "tpo",
                "volume_nodes",
            ),
            "options": (
                "options_snapshot",
                "gex",
                "iv_surface",
                "dex",
                "gamma_flip",
                "vol_term",
                "skew",
                "expected_move",
                "squeeze",
                "flow",
                "payoff",
            ),
            "probabilistic": (
                "evt_cvar",
                "mjd_jumps",
                "heston",
                "particle_filter",
                "markov",
                "cor3m",
                "fear_greed",
                "factor_calibration",
                "multimodal_predictive",
                "cross_asset",
            ),
            "fundamental": (
                "profile",
                "ratios",
                "statements",
                "valuation",
                "forensic_flags",
                "insider",
                "catalyst_nlp",
                "transcript",
            ),
            "macro_argentina": (
                "fred",
                "bcra_fx",
                "riesgo_pais",
                "merval_usd",
                "inflation",
                "regulatory_scanner",
            ),
            "portfolio_risk": (
                "kelly",
                "drawdown",
                "portfolio_optimization",
                "hrp",
                "execution",
                "outcome_records",
            ),
        }
        profiles: list[EngineEvidenceProfile] = []
        for domain, engine_names in specs.items():
            for engine_name in engine_names:
                profiles.append(
                    EngineEvidenceProfile(
                        engine_name=engine_name,
                        domain=domain,
                        compiler_name=_compiler_name_for_domain(domain),
                        critical_fields=_critical_fields_for_domain(domain),
                    )
                )
        return cls(tuple(profiles))

    def by_domain(self: EngineEvidenceRegistry, domain: str) -> tuple[EngineEvidenceProfile, ...]:
        return tuple(profile for profile in self.profiles if profile.domain == domain)


class LLMCallDecision(StrEnum):
    CALL = "CALL"
    SKIP_LOW_SIGNAL = "SKIP_LOW_SIGNAL"
    SKIP_BUDGET = "SKIP_BUDGET"
    DEDUPE_HIT = "DEDUPE_HIT"


@dataclass(frozen=True)
class LLMCallPlan:
    decision: LLMCallDecision
    agent_name: str
    provider: str
    model: str
    input_est_tokens: int
    max_output_tokens: int
    payload_hash: str
    dedupe_key: str
    signal_score: float
    has_critical_risk: bool
    skipped_reason: str | None = None
    cached_output: str | None = None
    saved_est_tokens: int = 0


@dataclass
class EphemeralLLMDedupeEntry:
    output: str
    expires_at: float
    est_tokens: int


class LLMCallPlanner:
    _SHARED_DEDUPE: dict[str, EphemeralLLMDedupeEntry] = {}

    def __init__(
        self: LLMCallPlanner,
        *,
        enabled: bool = True,
        min_signal_score: float = 0.25,
        max_input_tokens: int = 6_000,
        max_total_tokens: int = 24_000,
        dedupe_enabled: bool = False,
        shared_dedupe: bool = False,
        dedupe_ttl_seconds: int = 300,
        dedupe_max_entries: int = 256,
    ) -> None:
        self.enabled = enabled
        self.min_signal_score = min_signal_score
        self.max_input_tokens = max(100, max_input_tokens)
        self.max_total_tokens = max(1_000, max_total_tokens)
        self.dedupe_enabled = dedupe_enabled
        self.shared_dedupe = shared_dedupe
        self.dedupe_ttl_seconds = max(1, dedupe_ttl_seconds)
        self.dedupe_max_entries = max(1, dedupe_max_entries)
        self._dedupe: dict[str, EphemeralLLMDedupeEntry] = (
            self._SHARED_DEDUPE if shared_dedupe else {}
        )

    def plan(
        self: LLMCallPlanner,
        *,
        agent_name: str,
        provider: str,
        model: str,
        payload: str,
        max_output_tokens: int,
        signal_score: float,
        has_critical_risk: bool,
    ) -> LLMCallPlan:
        input_tokens = estimate_tokens(payload)
        total_tokens = input_tokens + max_output_tokens
        payload_hash = hash_payload(payload)
        dedupe_key = hash_payload(
            f"{agent_name}|{provider}|{model}|{payload_hash}|{max_output_tokens}"
        )
        if not self.enabled:
            return self._plan(
                LLMCallDecision.CALL,
                agent_name,
                provider,
                model,
                input_tokens,
                max_output_tokens,
                payload_hash,
                dedupe_key,
                signal_score,
                has_critical_risk,
            )
        self._prune_dedupe()
        if self.dedupe_enabled and dedupe_key in self._dedupe:
            entry = self._dedupe[dedupe_key]
            return self._plan(
                LLMCallDecision.DEDUPE_HIT,
                agent_name,
                provider,
                model,
                input_tokens,
                max_output_tokens,
                payload_hash,
                dedupe_key,
                signal_score,
                has_critical_risk,
                cached_output=entry.output,
                saved_est_tokens=entry.est_tokens,
            )
        if input_tokens > self.max_input_tokens or total_tokens > self.max_total_tokens:
            return self._plan(
                LLMCallDecision.SKIP_BUDGET,
                agent_name,
                provider,
                model,
                input_tokens,
                max_output_tokens,
                payload_hash,
                dedupe_key,
                signal_score,
                has_critical_risk,
                skipped_reason="budget_exceeded",
                saved_est_tokens=total_tokens,
            )
        if (
            signal_score < self.min_signal_score
            and not has_critical_risk
            and agent_name != "orchestrator"
            and not agent_name.endswith("_orchestrator")
        ):
            return self._plan(
                LLMCallDecision.SKIP_LOW_SIGNAL,
                agent_name,
                provider,
                model,
                input_tokens,
                max_output_tokens,
                payload_hash,
                dedupe_key,
                signal_score,
                has_critical_risk,
                skipped_reason="low_signal",
                saved_est_tokens=total_tokens,
            )
        return self._plan(
            LLMCallDecision.CALL,
            agent_name,
            provider,
            model,
            input_tokens,
            max_output_tokens,
            payload_hash,
            dedupe_key,
            signal_score,
            has_critical_risk,
        )

    def remember(self: LLMCallPlanner, plan: LLMCallPlan, output: str) -> None:
        if not self.dedupe_enabled or plan.decision != LLMCallDecision.CALL:
            return
        self._prune_dedupe()
        if len(self._dedupe) >= self.dedupe_max_entries:
            oldest = min(self._dedupe, key=lambda key: self._dedupe[key].expires_at)
            self._dedupe.pop(oldest, None)
        self._dedupe[plan.dedupe_key] = EphemeralLLMDedupeEntry(
            output=output,
            expires_at=time.monotonic() + self.dedupe_ttl_seconds,
            est_tokens=plan.input_est_tokens + plan.max_output_tokens,
        )

    def _prune_dedupe(self: LLMCallPlanner) -> None:
        now = time.monotonic()
        expired = [key for key, entry in self._dedupe.items() if entry.expires_at <= now]
        for key in expired:
            self._dedupe.pop(key, None)

    @staticmethod
    def _plan(
        decision: LLMCallDecision,
        agent_name: str,
        provider: str,
        model: str,
        input_est_tokens: int,
        max_output_tokens: int,
        payload_hash: str,
        dedupe_key: str,
        signal_score: float,
        has_critical_risk: bool,
        *,
        skipped_reason: str | None = None,
        cached_output: str | None = None,
        saved_est_tokens: int = 0,
    ) -> LLMCallPlan:
        return LLMCallPlan(
            decision=decision,
            agent_name=agent_name,
            provider=provider,
            model=model,
            input_est_tokens=input_est_tokens,
            max_output_tokens=max_output_tokens,
            payload_hash=payload_hash,
            dedupe_key=dedupe_key,
            signal_score=signal_score,
            has_critical_risk=has_critical_risk,
            skipped_reason=skipped_reason,
            cached_output=cached_output,
            saved_est_tokens=saved_est_tokens,
        )


def estimate_tokens(text: str) -> int:
    return max(1, (len(text) + _TOKEN_CHARS - 1) // _TOKEN_CHARS)


def hash_payload(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8", errors="ignore")).hexdigest()[:16]


def _compiler_name_for_domain(domain: str) -> str:
    return {
        "technical": "TechnicalEvidenceCompiler",
        "options": "OptionsEvidenceCompiler",
        "probabilistic": "ProbabilisticEvidenceCompiler",
        "fundamental": "FundamentalEvidenceCompiler",
        "macro_argentina": "MacroArgentinaEvidenceCompiler",
        "portfolio_risk": "PortfolioRiskEvidenceCompiler",
        "transcript_intelligence": "TranscriptIntelligenceEvidenceCompiler",
        "mesa_dinero": "MesaDineroEvidenceCompiler",
        "final_report": "FinalReportEvidenceAssembler",
    }.get(domain, "BaseEvidenceCompiler")


def _critical_fields_for_domain(domain: str) -> tuple[str, ...]:
    return {
        "technical": ("last_close", "smc_bias", "support", "resistance"),
        "options": ("spot", "gex", "iv", "gamma_flip", "wall"),
        "probabilistic": ("cvar_99", "var_99", "kelly", "gate_veto", "jump"),
        "fundamental": ("revenue", "debt", "cash", "valuation", "forensic"),
        "macro_argentina": ("rate", "inflation", "ccl", "riesgo_pais", "merval_usd"),
        "portfolio_risk": ("kelly", "drawdown", "allocation", "execution"),
        "transcript_intelligence": ("tone", "alert_terms", "evasiveness_terms", "quotes"),
        "mesa_dinero": ("bias", "conviction", "risk", "invalidations"),
        "final_report": ("executive_bias", "invalidations", "limitations", "confidence"),
    }.get(domain, ("symbol",))


def _json_dumps(value: object) -> str:
    return json.dumps(value, default=str, ensure_ascii=False, separators=(",", ":"))


def _as_dict(value: object) -> dict[str, object]:
    return value if isinstance(value, dict) else {}


def _pick(source: dict[str, object], keys: tuple[str, ...]) -> dict[str, object]:
    return {key: source.get(key) for key in keys if not compact_value_is_empty(source.get(key))}


def _prefix(source: dict[str, object], prefix: str, keys: tuple[str, ...]) -> dict[str, object]:
    return {
        f"{prefix}_{key}": source.get(key)
        for key in keys
        if not compact_value_is_empty(source.get(key))
    }


def _prefixed_decision_fields(prefix: str, source: dict[str, object]) -> dict[str, object]:
    hints = (
        "bias",
        "score",
        "confidence",
        "trend",
        "signal",
        "poc",
        "vah",
        "val",
        "vwap",
        "above",
        "zscore",
        "active_count",
        "bullish",
        "bearish",
        "support",
        "resistance",
        "regime",
        "state",
        "delta",
        "imbalance",
    )
    compact = _compact_mapping(source, hints=hints, limit=8)
    return {f"{prefix}_{key}": value for key, value in compact.items()}


def _compact_mapping(
    source: dict[str, object],
    *,
    hints: tuple[str, ...],
    limit: int = 24,
) -> dict[str, object]:
    compact: dict[str, object] = {}
    for key, value in source.items():
        key_text = str(key).lower()
        if key_text in RAW_DENYLIST or key_text.startswith("_"):
            continue
        if (
            hints
            and not any(hint in key_text for hint in hints)
            and not isinstance(value, bool | int | float)
        ):
            if isinstance(value, dict):
                nested = _compact_mapping(value, hints=hints, limit=6)
                if nested:
                    compact[str(key)] = nested
            continue
        compact[str(key)] = _compact_value(value)
        if len(compact) >= limit:
            compact["_truncated_keys"] = True
            break
    return _strip_empty(_strip_raw_mapping(compact))


def _compact_value(value: object, *, depth: int = 0) -> object:
    if value is None or isinstance(value, bool | int | float):
        return value
    if isinstance(value, str):
        cleaned = " ".join(value.split())
        return cleaned if len(cleaned) <= 360 else cleaned[:357] + "..."
    if isinstance(value, dict):
        if depth >= 3:
            return {"_keys_count": len(value)}
        return _compact_mapping(
            {str(key): item for key, item in value.items()},
            hints=(),
            limit=10,
        )
    if isinstance(value, list | tuple | set):
        items = list(value)
        if all(isinstance(item, bool | int | float | str) for item in items[:10]):
            return [_compact_value(item, depth=depth + 1) for item in items[:5]]
        return {"items_count": len(items)}
    return str(value)[:240]


def _strip_raw_keys(value: object) -> object:
    if isinstance(value, dict):
        cleaned: dict[str, object] = {}
        for key, item in value.items():
            key_text = str(key).lower()
            if key_text in RAW_DENYLIST:
                continue
            cleaned[str(key)] = _strip_raw_keys(item)
        return cleaned
    if isinstance(value, list):
        return [_strip_raw_keys(item) for item in value[:24]]
    return value


def _strip_raw_mapping(value: dict[str, object]) -> dict[str, object]:
    stripped = _strip_raw_keys(value)
    return stripped if isinstance(stripped, dict) else {}


def _strip_empty(value: dict[str, object]) -> dict[str, object]:
    return {key: item for key, item in value.items() if not compact_value_is_empty(item)}


def _limit_mapping(value: dict[str, object], limit: int) -> dict[str, object]:
    out: dict[str, object] = {}
    for key, item in value.items():
        if compact_value_is_empty(item):
            continue
        out[key] = _compact_value(item)
        if len(out) >= limit:
            out["_truncated_keys"] = True
            break
    return _strip_raw_mapping(out) if isinstance(out, dict) else {}


def _flatten_snapshot(source: dict[str, object], *, prefix: str, limit: int) -> dict[str, object]:
    out: dict[str, object] = {}
    for key, value in source.items():
        key_text = str(key).lower()
        if key_text in RAW_DENYLIST:
            continue
        if isinstance(value, dict):
            for nested_key, nested_value in value.items():
                nested_text = str(nested_key).lower()
                if nested_text in RAW_DENYLIST:
                    continue
                out[f"{prefix}_{key}_{nested_key}"] = _compact_value(nested_value)
                if len(out) >= limit:
                    return out
        else:
            out[f"{prefix}_{key}"] = _compact_value(value)
        if len(out) >= limit:
            break
    return _strip_empty(out)


def _best_confidence(facts: dict[str, object]) -> float | None:
    for key in ("confidence", "quality_score", "score", "smc_score", "fractal_confidence"):
        score = _safe_float(facts.get(key))
        if score:
            return max(0.0, min(1.0, score))
    if facts:
        return 0.5
    return None


def _signal_score(
    facts: dict[str, object],
    risks: list[str],
    invalidations: list[str],
    confidence: float | None,
) -> float:
    fact_score = min(
        0.55,
        len([value for value in facts.values() if not compact_value_is_empty(value)]) * 0.035,
    )
    risk_score = min(0.25, (len(risks) + len(invalidations)) * 0.06)
    confidence_score = 0.2 * (confidence if confidence is not None else 0.25)
    return max(0.0, min(1.0, fact_score + risk_score + confidence_score))


def _generic_risks(facts: dict[str, object]) -> list[str]:
    risks: list[str] = []
    for key, value in facts.items():
        lowered = key.lower()
        if any(
            marker in lowered for marker in ("risk", "cvar", "var", "jump", "tail", "veto")
        ) and not compact_value_is_empty(value, treat_false_as_empty=True):
            risks.append(key)
    return risks[:8]


def _generic_invalidations(facts: dict[str, object]) -> list[str]:
    invalidations: list[str] = []
    for key, value in facts.items():
        lowered = key.lower()
        if any(
            marker in lowered for marker in ("invalid", "veto", "breach")
        ) and not compact_value_is_empty(value, treat_false_as_empty=True):
            invalidations.append(key)
    return invalidations[:8]


def _dedupe(values: list[str]) -> list[str]:
    return list(dict.fromkeys(values))


def _safe_float(value: object) -> float:
    if not isinstance(value, str | int | float):
        return 0.0
    try:
        return float(value)
    except (TypeError, ValueError):
        return 0.0


def _squash_text(value: str) -> str:
    return " ".join(value.split())


def _transcript_tone(lower_text: str, alerts: list[str]) -> str:
    if alerts:
        return "ALERT"
    bullish = sum(1 for term in ("beat", "strong demand", "raised", "growth") if term in lower_text)
    bearish = sum(
        1
        for term in ("lowered", "compression", "headwind", "uncertain", "pressure")
        if term in lower_text
    )
    if bullish > bearish:
        return "BULLISH"
    if bearish > bullish:
        return "BEARISH"
    return "NEUTRAL"


def _transcript_themes(lower_text: str) -> list[str]:
    themes = {
        "guidance": ("guidance", "outlook", "forecast"),
        "margin": ("margin", "gross profit", "operating leverage"),
        "liquidity": ("liquidity", "cash flow", "debt", "covenant"),
        "controls": ("material weakness", "internal control", "restatement"),
        "capex": ("capex", "capital expenditure", "investment"),
    }
    return [name for name, terms in themes.items() if any(term in lower_text for term in terms)]


def _extract_relevant_sentences(text: str, terms: tuple[str, ...], limit: int = 3) -> list[str]:
    quotes: list[str] = []
    lowered_text = text.lower()
    for term in terms:
        idx = lowered_text.find(term)
        if idx < 0:
            continue
        start = idx
        end = min(len(text), idx + len(term) + 120)
        quotes.append(text[start:end].strip()[:220])
        if len(quotes) >= limit:
            break
    return quotes


def _as_str_list(value: object) -> list[str]:
    if not isinstance(value, list):
        return []
    return [str(item) for item in value if not compact_value_is_empty(item)]
