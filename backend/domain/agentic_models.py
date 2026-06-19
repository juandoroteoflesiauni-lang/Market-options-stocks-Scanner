"""Pydantic models for agentic trade decisions. # [IM][TH]"""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from enum import StrEnum
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

Decision = Literal["EXECUTE", "PASS"]
Severity = Literal["NONE", "LOW", "MEDIUM", "HIGH", "CRITICAL"]
AgentProvider = Literal["github_models", "gemini", "azure_openai", "claude", "groq"]


class AgentEnvelope(BaseModel):
    """Common metadata wrapper for every agent output (audit-ready)."""

    model_config = ConfigDict(frozen=True)

    agent_name: str
    model: str
    provider: AgentProvider
    latency_ms: float = Field(ge=0.0)
    degraded: bool = False
    fallback_reason: str | None = None


class MacroRiskAssessment(BaseModel):
    """Macro risk assessment from FMP-driven agent."""

    model_config = ConfigDict(frozen=True)

    severity: Severity
    imminent_event: str | None = None
    minutes_to_event: int | None = Field(default=None, ge=0)
    halt_scanner: bool = False
    stop_loss_multiplier: float = Field(default=1.0, ge=0.1, le=3.0)
    rationale: str = Field(max_length=600)


class MacroRiskResult(BaseModel):
    model_config = ConfigDict(frozen=True)

    assessment: MacroRiskAssessment
    envelope: AgentEnvelope


class OptionsContractContext(BaseModel):
    """Compact options context for agent prompts (derived from OptionContract)."""

    model_config = ConfigDict(frozen=True)

    contract_symbol: str
    underlying_ticker: str
    option_type: Literal["CALL", "PUT"]
    strike: str
    implied_volatility: float = Field(ge=0.0)
    delta: float = Field(ge=-1.0, le=1.0)
    gamma: float = Field(ge=0.0)
    open_interest: int = Field(ge=0)
    volume: int = Field(ge=0)
    composite_score: float = Field(default=0.0, ge=0.0, le=100.0)


class OptionsAnalystAssessment(BaseModel):
    model_config = ConfigDict(frozen=True)

    contract_symbol: str
    iv_rank_eval: Literal["CHEAP", "FAIR", "RICH"]
    gamma_squeeze_risk: Severity
    liquidity_ok: bool
    directional_bias: Literal["BULLISH", "BEARISH", "NEUTRAL"]
    confidence_score: int = Field(ge=0, le=100)
    rationale: str = Field(max_length=600)


class OptionsAnalystResult(BaseModel):
    model_config = ConfigDict(frozen=True)

    assessment: OptionsAnalystAssessment
    envelope: AgentEnvelope


class ResearcherArgument(BaseModel):
    model_config = ConfigDict(frozen=True)

    stance: Literal["BULLISH", "BEARISH"]
    thesis: str = Field(max_length=800)
    key_risks: list[str] = Field(default_factory=list, max_length=5)


class TraderVerdict(BaseModel):
    """Strictly-typed final contract required by execution mixins."""

    model_config = ConfigDict(frozen=True)

    decision: Decision
    confidence_score: int = Field(ge=0, le=100)
    recommended_position_size_modifier: float = Field(ge=0.0, le=1.0)
    rationale: str = Field(max_length=800)


class ExecutionCommitteeResult(BaseModel):
    model_config = ConfigDict(frozen=True)

    contract_symbol: str
    bull: ResearcherArgument
    bear: ResearcherArgument
    verdict: TraderVerdict
    envelope: AgentEnvelope


class MacroDataSnapshot(BaseModel):
    """Bundled macro inputs for the risk manager agent."""

    model_config = ConfigDict(frozen=True)

    calendar_events: tuple[dict[str, str | None], ...] = ()
    treasury_yields: tuple[dict[str, str | float | None], ...] = ()
    inflation_indicators: tuple[dict[str, str | float | None], ...] = ()
    fetched_at: datetime


class AgenticTradeDecisionEvent(BaseModel):
    """Persistent decision log payload for DuckDB AuditComplexStore."""

    model_config = ConfigDict(frozen=True)

    correlation_id: str
    module: Literal["alpaca", "bingx"]
    symbol: str
    contract_symbol: str
    created_at: datetime
    macro_risk: MacroRiskResult | None = None
    options_analysis: OptionsAnalystResult | None = None
    committee: ExecutionCommitteeResult | None = None
    final_decision: Decision
    quant_default_used: bool = False


class CachedContextEntry(BaseModel):
    """Immutable cached LLM/macro context bucket entry."""

    model_config = ConfigDict(frozen=True)

    payload: dict[str, Any]
    source: str
    created_at: datetime
    cost_saved: Decimal = Field(default=Decimal("0"))


class StreamEventType(StrEnum):
    AGENT_STARTED = "agent_started"
    CHUNK = "chunk"
    AGENT_COMPLETED = "agent_completed"
    CONSENSUS = "consensus"
    ERROR = "error"
    DONE = "done"


class AgentStreamEvent(BaseModel):
    """SSE frame for live agent orchestration."""

    model_config = ConfigDict(frozen=True)

    event_type: StreamEventType
    agent: str
    data: str
    seq: int = Field(ge=0)
    ts: datetime


class TradeRationaleReport(BaseModel):
    """Institutional trade rationale combining quant record + LLM consensus."""

    model_config = ConfigDict(frozen=True)

    decision_id: str
    module: str
    symbol: str
    contract_symbol: str
    final_decision: str
    quant_default_used: bool
    technical_summary: dict[str, Any] = Field(default_factory=dict)
    consensus_text: str = ""
    consensus_available: bool = True
    created_at: datetime


__all__ = [
    "AgentEnvelope",
    "AgentProvider",
    "AgentStreamEvent",
    "AgenticTradeDecisionEvent",
    "CachedContextEntry",
    "Decision",
    "ExecutionCommitteeResult",
    "MacroDataSnapshot",
    "MacroRiskAssessment",
    "MacroRiskResult",
    "OptionsAnalystAssessment",
    "OptionsAnalystResult",
    "OptionsContractContext",
    "ResearcherArgument",
    "Severity",
    "StreamEventType",
    "TradeRationaleReport",
    "TraderVerdict",
]
