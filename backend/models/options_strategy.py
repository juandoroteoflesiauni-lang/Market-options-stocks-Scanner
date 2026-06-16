"""Contratos del pipeline Options Strategy (Fase 1). # [PD-2][PD-4][TH][IM]"""

from __future__ import annotations

from datetime import UTC, date, datetime
from decimal import Decimal
from enum import StrEnum
from typing import Any, Literal
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from backend.config.alpaca_priority_route import is_route1_symbol
from backend.domain.alpaca_options_models import OptionsConfluence, Route1OptionsSnapshotContext
from backend.models.market_snapshot import MarketSnapshot

AlpacaOptionsRoute = Literal["priority", "scan"]
StructureProfile = Literal["full", "r2_basic"]

BreakoutState = Literal["compressed", "arming", "confirmed", "failed", "unknown"]
DealerRegime = Literal["supportive", "suppressive", "pinning", "unstable", "unknown"]
IvState = Literal["cheap", "fair", "rich", "extreme", "unknown"]
OutcomeStatus = Literal["win", "loss", "breakeven", "expired", "stopped", "open"]
RegimeClass = Literal[
    "trend", "mean_reversion", "volatile", "event", "dislocated", "unknown"
]
TradeDirection = Literal["bullish", "bearish", "neutral"]


class StrategyDecision(StrEnum):
    """Decisión operativa del módulo."""

    EXECUTE = "EXECUTE"
    NO_TRADE = "NO_TRADE"
    REDUCE = "REDUCE"
    EXIT = "EXIT"


class OptionsStructure(StrEnum):
    """Estructuras soportadas (MVP + R2 básicas)."""

    LONG_CALL = "long_call"
    LONG_PUT = "long_put"
    CALL_DEBIT_SPREAD = "call_debit_spread"
    PUT_DEBIT_SPREAD = "put_debit_spread"
    SHORT_PUT = "short_put"
    PUT_CREDIT_SPREAD = "put_credit_spread"
    CALL_CREDIT_SPREAD = "call_credit_spread"
    BULL_CALL_SPREAD = "bull_call_spread"
    CALL_BUTTERFLY = "call_butterfly"
    NO_TRADE = "no_trade"


class R1EnrichmentContext(BaseModel):
    """Enriquecimiento R1: L2 BingX, barras 5m, motores híbridos y puente predictivo."""

    model_config = ConfigDict(frozen=True)

    hybrid_confluence: OptionsConfluence | None = None
    hybrid_signal_count: int = Field(default=0, ge=0)
    l2_microstructure: dict[str, Any] = Field(default_factory=dict)
    l2_ok: bool = False
    intraday_bars_5m: tuple[dict[str, Any], ...] = ()
    predictive_meta: dict[str, Any] = Field(default_factory=dict)
    sources: dict[str, str] = Field(default_factory=dict)


class OptionsStrategyInput(BaseModel):
    """Entrada al pipeline: spot + contexto R1 de opciones."""

    model_config = ConfigDict(frozen=True)

    symbol: str
    as_of: datetime
    market_snapshot: MarketSnapshot | None = None
    options_context: Route1OptionsSnapshotContext | None = None
    r1_enrichment: R1EnrichmentContext | None = None
    route: AlpacaOptionsRoute = "priority"

    @field_validator("symbol")
    @classmethod
    def symbol_uppercase_nonempty(cls, value: str) -> str:
        sym = value.upper().strip()
        if not sym:
            raise ValueError("symbol must not be empty")
        return sym

    @model_validator(mode="after")
    def _route1_guard_when_priority(self) -> OptionsStrategyInput:
        if self.route == "priority" and not is_route1_symbol(self.symbol):
            raise ValueError(f"symbol_not_in_route1_universe: {self.symbol}")
        return self

    @field_validator("as_of")
    @classmethod
    def as_of_must_be_utc_aware(cls, value: datetime) -> datetime:
        if value.tzinfo is None:
            raise ValueError("as_of must be timezone-aware (UTC)")
        return value


class TechnicalLayerOutput(BaseModel):
    """Salida parcial de la capa técnica (Fase 2)."""

    model_config = ConfigDict(frozen=True)

    symbol: str
    as_of: datetime
    technical_direction_bias: float = Field(default=0.0, ge=-1.0, le=1.0)
    trend_quality_score: float = Field(default=0.0, ge=0.0, le=1.0)
    breakout_state: BreakoutState = "unknown"
    liquidity_location_score: float = Field(default=0.0, ge=0.0, le=1.0)
    reversal_risk_score: float = Field(default=0.0, ge=0.0, le=1.0)
    structure_alignment_score: float = Field(default=0.0, ge=0.0, le=1.0)
    l2_microstructure_score: float = Field(default=0.0, ge=0.0, le=1.0)
    engine_scores: dict[str, float] = Field(default_factory=dict)
    insufficient_data: bool = False


class PredictiveLayerOutput(BaseModel):
    """Salida parcial de la capa predictiva (Fase 2)."""

    model_config = ConfigDict(frozen=True)

    symbol: str
    as_of: datetime
    predictive_direction_bias: float = Field(default=0.0, ge=-1.0, le=1.0)
    regime_class: RegimeClass = "unknown"
    expected_move_pct: float = Field(default=0.0, ge=0.0)
    expected_move_confidence: float = Field(default=0.0, ge=0.0, le=1.0)
    left_tail_risk_score: float = Field(default=0.0, ge=0.0, le=1.0)
    right_tail_risk_score: float = Field(default=0.0, ge=0.0, le=1.0)
    macro_alignment_score: float = Field(default=0.0, ge=-1.0, le=1.0)
    forecast_dispersion_score: float = Field(default=0.0, ge=0.0, le=1.0)
    engine_scores: dict[str, float] = Field(default_factory=dict)
    insufficient_data: bool = False


class OptionsLayerOutput(BaseModel):
    """Salida parcial de la capa de opciones (Fase 3)."""

    model_config = ConfigDict(frozen=True)

    symbol: str
    as_of: datetime
    options_direction_bias: float = Field(default=0.0, ge=-1.0, le=1.0)
    dealer_regime: DealerRegime = "unknown"
    gamma_pressure_score: float = Field(default=0.0, ge=0.0, le=1.0)
    iv_state: IvState = "unknown"
    flow_conviction_score: float = Field(default=0.0, ge=0.0, le=1.0)
    chain_liquidity_score: float = Field(default=0.0, ge=0.0, le=1.0)
    structure_preference: OptionsStructure = OptionsStructure.NO_TRADE
    hybrid_confluence_score: float = Field(default=0.0, ge=0.0, le=1.0)
    engine_scores: dict[str, float] = Field(default_factory=dict)
    insufficient_data: bool = False


class SelectedOptionContract(BaseModel):
    """Contrato seleccionado para una pata de la estructura."""

    model_config = ConfigDict(frozen=True)

    underlying: str
    expiry: date
    strike: float = Field(gt=0.0)
    right: Literal["call", "put"]
    side: Literal["long", "short"]
    delta: float | None = None
    open_interest: int = Field(default=0, ge=0)
    mark: float | None = Field(default=None, ge=0.0)
    iv: float | None = Field(default=None, ge=0.0)
    dte: int = Field(default=0, ge=0)
    contract_symbol: str | None = None
    ratio: int = Field(default=1, ge=1)


class StructureSelection(BaseModel):
    """Estructura MVP elegida antes de ejecución."""

    model_config = ConfigDict(frozen=True)

    symbol: str
    as_of: datetime
    structure: OptionsStructure
    direction: TradeDirection
    reason_codes: tuple[str, ...] = ()
    confidence: float = Field(default=0.0, ge=0.0, le=1.0)


class OptionsStrategyCandidate(BaseModel):
    """Candidato completo: estructura + contratos + métricas de payoff."""

    model_config = ConfigDict(frozen=True)

    symbol: str
    as_of: datetime
    selection: StructureSelection
    legs: tuple[SelectedOptionContract, ...] = ()
    max_profit: float | None = None
    max_loss: float | None = None
    break_evens: tuple[float, ...] = ()
    limitations: tuple[str, ...] = ()


def merge_layer_features(
    technical: TechnicalLayerOutput,
    predictive: PredictiveLayerOutput,
) -> NormalizedFeatures:
    """Combina salidas de capas 2 y 3 parciales en ``NormalizedFeatures``."""
    if technical.symbol != predictive.symbol:
        raise ValueError("technical and predictive symbols must match")
    return NormalizedFeatures(
        symbol=technical.symbol,
        as_of=technical.as_of,
        technical_direction_bias=technical.technical_direction_bias,
        predictive_direction_bias=predictive.predictive_direction_bias,
        trend_quality_score=technical.trend_quality_score,
        breakout_state=technical.breakout_state,
        liquidity_location_score=technical.liquidity_location_score,
        reversal_risk_score=technical.reversal_risk_score,
        structure_alignment_score=technical.structure_alignment_score,
        l2_microstructure_score=technical.l2_microstructure_score,
        regime_class=predictive.regime_class,
        expected_move_pct=predictive.expected_move_pct,
        expected_move_confidence=predictive.expected_move_confidence,
        left_tail_risk_score=predictive.left_tail_risk_score,
        right_tail_risk_score=predictive.right_tail_risk_score,
        macro_alignment_score=predictive.macro_alignment_score,
        forecast_dispersion_score=predictive.forecast_dispersion_score,
    )


def merge_all_layer_features(
    technical: TechnicalLayerOutput,
    predictive: PredictiveLayerOutput,
    options: OptionsLayerOutput,
) -> NormalizedFeatures:
    """Combina las tres capas parciales en ``NormalizedFeatures``."""
    base = merge_layer_features(technical, predictive)
    if (
        technical.symbol != predictive.symbol
        or technical.symbol != options.symbol
    ):
        raise ValueError("all layer symbols must match")
    return base.model_copy(
        update={
            "options_direction_bias": options.options_direction_bias,
            "dealer_regime": options.dealer_regime,
            "gamma_pressure_score": options.gamma_pressure_score,
            "iv_state": options.iv_state,
            "flow_conviction_score": options.flow_conviction_score,
            "chain_liquidity_score": options.chain_liquidity_score,
            "structure_preference": options.structure_preference,
            "hybrid_confluence_score": options.hybrid_confluence_score,
            "l2_microstructure_score": technical.l2_microstructure_score,
        }
    )


class NormalizedFeatures(BaseModel):
    """Salidas normalizadas compartidas entre capas técnicas, predictivas y de opciones."""

    model_config = ConfigDict(frozen=True)

    symbol: str
    as_of: datetime
    technical_direction_bias: float = Field(default=0.0, ge=-1.0, le=1.0)
    predictive_direction_bias: float = Field(default=0.0, ge=-1.0, le=1.0)
    options_direction_bias: float = Field(default=0.0, ge=-1.0, le=1.0)
    trend_quality_score: float = Field(default=0.0, ge=0.0, le=1.0)
    breakout_state: BreakoutState = "unknown"
    liquidity_location_score: float = Field(default=0.0, ge=0.0, le=1.0)
    reversal_risk_score: float = Field(default=0.0, ge=0.0, le=1.0)
    structure_alignment_score: float = Field(default=0.0, ge=0.0, le=1.0)
    regime_class: RegimeClass = "unknown"
    expected_move_pct: float = Field(default=0.0, ge=0.0)
    expected_move_confidence: float = Field(default=0.0, ge=0.0, le=1.0)
    left_tail_risk_score: float = Field(default=0.0, ge=0.0, le=1.0)
    right_tail_risk_score: float = Field(default=0.0, ge=0.0, le=1.0)
    macro_alignment_score: float = Field(default=0.0, ge=-1.0, le=1.0)
    forecast_dispersion_score: float = Field(default=0.0, ge=0.0, le=1.0)
    dealer_regime: DealerRegime = "unknown"
    gamma_pressure_score: float = Field(default=0.0, ge=0.0, le=1.0)
    iv_state: IvState = "unknown"
    flow_conviction_score: float = Field(default=0.0, ge=0.0, le=1.0)
    chain_liquidity_score: float = Field(default=0.0, ge=0.0, le=1.0)
    structure_preference: OptionsStructure = OptionsStructure.NO_TRADE
    hybrid_confluence_score: float = Field(default=0.0, ge=0.0, le=1.0)
    l2_microstructure_score: float = Field(default=0.0, ge=0.0, le=1.0)
    global_bias: float = Field(default=0.0, ge=-1.0, le=1.0)
    global_confidence: float = Field(default=0.0, ge=0.0, le=1.0)

    @field_validator("symbol")
    @classmethod
    def symbol_uppercase(cls, value: str) -> str:
        return value.upper().strip()


class PlaybookDecision(BaseModel):
    """Decisión de playbook tras fusión y vetos."""

    model_config = ConfigDict(frozen=True)

    symbol: str
    as_of: datetime
    decision: StrategyDecision
    playbook_family: str | None = None
    recommended_structure: OptionsStructure = OptionsStructure.NO_TRADE
    direction: TradeDirection = "neutral"
    confidence: float = Field(default=0.0, ge=0.0, le=1.0)
    reason_codes: tuple[str, ...] = ()
    veto_triggered: str | None = None
    execution_ready: bool = False
    risk_budget_pct: float = Field(default=0.0, ge=0.0)
    candidate_contract_policy: dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode="after")
    def _execution_ready_requires_execute(self) -> PlaybookDecision:
        if self.execution_ready and self.decision != StrategyDecision.EXECUTE:
            raise ValueError("execution_ready requires decision=EXECUTE")
        if self.decision == StrategyDecision.EXECUTE and self.veto_triggered:
            raise ValueError("EXECUTE cannot coexist with veto_triggered")
        return self


class OptionsLegSpec(BaseModel):
    """Especificación de una pata para el adaptador Alpaca."""

    model_config = ConfigDict(frozen=True)

    contract_symbol: str
    side: Literal["buy", "sell"]
    ratio: int = Field(default=1, ge=1)


class OptionsExecutionPayload(BaseModel):
    """Payload listo para dry-run / ejecución Alpaca (sin lógica broker)."""

    model_config = ConfigDict(frozen=True)

    symbol: str
    timestamp: datetime
    decision: StrategyDecision
    playbook_family: str
    recommended_structure: OptionsStructure
    direction: TradeDirection
    global_confidence: float = Field(ge=0.0, le=1.0)
    dte_target: int = Field(ge=1)
    delta_buy_target: float = Field(ge=0.0, le=1.0)
    delta_sell_target: float | None = Field(default=None, ge=0.0, le=1.0)
    max_premium_usd: Decimal = Field(gt=Decimal("0"))
    risk_budget_pct: float = Field(ge=0.0)
    veto_triggered: str | None = None
    reason_codes: tuple[str, ...] = ()
    legs: tuple[OptionsLegSpec, ...] = ()
    time_in_force: Literal["day", "gtc", "ioc", "fok"] = "day"
    order_type: Literal["limit", "market"] = "limit"
    max_slippage_pct: float = Field(default=2.0, ge=0.0)
    client_order_id: str = Field(default_factory=lambda: f"opt-{uuid4().hex[:16]}")
    dry_run: bool = True
    audit_metadata: dict[str, Any] = Field(default_factory=dict)
    route: AlpacaOptionsRoute = "priority"

    @field_validator("symbol")
    @classmethod
    def symbol_uppercase(cls, value: str) -> str:
        return value.upper().strip()

    @model_validator(mode="after")
    def _route1_guard_when_priority(self) -> OptionsExecutionPayload:
        if self.route == "priority" and not is_route1_symbol(self.symbol):
            raise ValueError(f"symbol_not_in_route1_universe: {self.symbol}")
        return self

    @model_validator(mode="after")
    def _spread_requires_two_legs(self) -> OptionsExecutionPayload:
        spread = {
            OptionsStructure.CALL_DEBIT_SPREAD,
            OptionsStructure.PUT_DEBIT_SPREAD,
            OptionsStructure.PUT_CREDIT_SPREAD,
            OptionsStructure.CALL_CREDIT_SPREAD,
            OptionsStructure.BULL_CALL_SPREAD,
        }
        if self.recommended_structure in spread and len(self.legs) < 2:
            raise ValueError("spread structures require at least two legs")
        if self.recommended_structure == OptionsStructure.CALL_BUTTERFLY and len(self.legs) < 3:
            raise ValueError("call_butterfly requires at least three legs")
        if self.recommended_structure == OptionsStructure.SHORT_PUT and len(self.legs) < 1:
            raise ValueError("short_put requires one leg")
        return self


class OptionsStrategyAuditLog(BaseModel):
    """Registro auditable de una decisión completa del módulo."""

    model_config = ConfigDict(frozen=True)

    audit_id: str = Field(default_factory=lambda: uuid4().hex)
    created_at: datetime = Field(default_factory=lambda: datetime.now(tz=UTC))
    input: OptionsStrategyInput
    features: NormalizedFeatures | None = None
    playbook_decision: PlaybookDecision
    execution_payload: OptionsExecutionPayload | None = None
    config_version: str = "phase1-mvp"
    pipeline_phase: str = "phase2-layers"

    @model_validator(mode="after")
    def _symbols_aligned(self) -> OptionsStrategyAuditLog:
        symbols = {
            self.input.symbol,
            self.playbook_decision.symbol,
        }
        if self.features is not None:
            symbols.add(self.features.symbol)
        if self.execution_payload is not None:
            symbols.add(self.execution_payload.symbol)
        if len(symbols) != 1:
            raise ValueError("audit log symbols must match across all stages")
        return self


class RiskSessionState(BaseModel):
    """Estado de sesión para límites de cartera del módulo."""

    model_config = ConfigDict(frozen=True)

    open_positions: int = Field(default=0, ge=0)
    daily_loss_pct: float = Field(default=0.0, ge=0.0)
    bullish_exposure_pct: float = Field(default=0.0, ge=0.0)
    bearish_exposure_pct: float = Field(default=0.0, ge=0.0)
    consecutive_losses_by_playbook: dict[str, int] = Field(default_factory=dict)
    open_symbols: tuple[str, ...] = ()
    total_risk_budget_pct: float = Field(default=0.0, ge=0.0)
    sector_risk_budget_pct: dict[str, float] = Field(default_factory=dict)


class RiskEvaluation(BaseModel):
    """Resultado de validación de riesgo pre-ejecución."""

    model_config = ConfigDict(frozen=True)

    passed: bool = True
    size_multiplier: float = Field(default=1.0, ge=0.0, le=1.0)
    adjusted_risk_budget_pct: float = Field(default=0.0, ge=0.0)
    veto_code: str | None = None
    reason_codes: tuple[str, ...] = ()


class OpenOptionsPosition(BaseModel):
    """Posición abierta mínima para evaluación de salida."""

    model_config = ConfigDict(frozen=True)

    symbol: str
    playbook_family: str
    structure: OptionsStructure
    direction: TradeDirection
    entry_premium_usd: Decimal = Field(gt=Decimal("0"))
    current_premium_usd: Decimal = Field(gt=Decimal("0"))
    dte: int = Field(ge=0)
    opened_at: datetime


class ExitEvaluation(BaseModel):
    """Recomendación de salida para una posición viva."""

    model_config = ConfigDict(frozen=True)

    symbol: str
    as_of: datetime
    decision: StrategyDecision = StrategyDecision.NO_TRADE
    reason_codes: tuple[str, ...] = ()


class OptionsExecutionResult(BaseModel):
    """Resultado de envío de orden Alpaca para opciones."""

    model_config = ConfigDict(frozen=True)

    client_order_id: str
    underlying: str
    structure: OptionsStructure
    ok: bool
    dry_run: bool
    submitted_at: datetime
    venue_order_id: str | None = None
    limit_price: float | None = Field(default=None, ge=0.0)
    error: str | None = None
    reason_codes: tuple[str, ...] = ()
    raw: dict[str, Any] = Field(default_factory=dict)


class OptionsStrategyRunResult(BaseModel):
    """Salida completa del pipeline con ejecución opcional."""

    model_config = ConfigDict(frozen=True)

    audit_log: OptionsStrategyAuditLog
    execution: OptionsExecutionResult | None = None


class OptionsTradeOutcome(BaseModel):
    """Resultado realizado de un trade ejecutado, ligado a su ``audit_id``.

    Es el feedback con el que la calibración aprende de PnL real en vez de
    solo de la decisión EXECUTE/NO_TRADE.
    """

    model_config = ConfigDict(frozen=True)

    audit_id: str
    symbol: str
    structure: OptionsStructure
    status: OutcomeStatus
    realized_pnl_usd: Decimal
    entry_premium_usd: Decimal = Field(gt=Decimal("0"))
    exit_premium_usd: Decimal | None = Field(default=None, ge=Decimal("0"))
    return_pct: float = 0.0
    opened_at: datetime | None = None
    closed_at: datetime = Field(default_factory=lambda: datetime.now(tz=UTC))
    notes: str = ""

    @field_validator("symbol")
    @classmethod
    def _symbol_route1(cls, value: str) -> str:
        sym = value.upper().strip()
        if not is_route1_symbol(sym):
            raise ValueError(f"symbol_not_in_route1_universe: {sym}")
        return sym

    @field_validator("closed_at", "opened_at")
    @classmethod
    def _utc_aware(cls, value: datetime | None) -> datetime | None:
        if value is not None and value.tzinfo is None:
            raise ValueError("timestamps must be timezone-aware (UTC)")
        return value

    def is_win(self) -> bool:
        """True si el trade cerró en ganancia (status o PnL positivo)."""
        if self.status == "win":
            return True
        if self.status in {"loss", "stopped"}:
            return False
        return self.realized_pnl_usd > Decimal("0")


class PlaybookCalibrationStats(BaseModel):
    """Estadísticas por playbook a partir del historial de auditoría."""

    model_config = ConfigDict(frozen=True)

    playbook_family: str
    total_signals: int = Field(ge=0)
    execute_count: int = Field(ge=0)
    no_trade_count: int = Field(ge=0)
    avg_confidence_on_execute: float = Field(default=0.0, ge=0.0, le=1.0)
    veto_rate: float = Field(default=0.0, ge=0.0, le=1.0)


class OptionsStrategyCalibrationReport(BaseModel):
    """Reporte offline de calibración del módulo Options Strategy."""

    model_config = ConfigDict(frozen=True)

    calibration_id: str = Field(default_factory=lambda: uuid4().hex)
    created_at: datetime = Field(default_factory=lambda: datetime.now(tz=UTC))
    observation_count: int = Field(ge=0)
    current_weights: dict[str, float] = Field(default_factory=dict)
    suggested_weights: dict[str, float] = Field(default_factory=dict)
    current_min_global_confidence: float = Field(default=0.68, ge=0.0, le=1.0)
    suggested_min_global_confidence: float = Field(default=0.68, ge=0.0, le=1.0)
    playbook_stats: tuple[PlaybookCalibrationStats, ...] = ()
    veto_counts: dict[str, int] = Field(default_factory=dict)
    execute_rate: float = Field(default=0.0, ge=0.0, le=1.0)
    factor_report: dict[str, Any] = Field(default_factory=dict)
    recommendations: tuple[str, ...] = ()
    limitations: tuple[str, ...] = ()


__all__ = [
    "ExitEvaluation",
    "NormalizedFeatures",
    "OpenOptionsPosition",
    "OptionsExecutionPayload",
    "OptionsExecutionResult",
    "OptionsLegSpec",
    "OptionsStrategyAuditLog",
    "OptionsStrategyInput",
    "OptionsLayerOutput",
    "OptionsStrategyCandidate",
    "OptionsStructure",
    "OptionsStrategyCalibrationReport",
    "OptionsStrategyRunResult",
    "OptionsTradeOutcome",
    "OutcomeStatus",
    "PlaybookCalibrationStats",
    "PlaybookDecision",
    "PredictiveLayerOutput",
    "RiskEvaluation",
    "RiskSessionState",
    "SelectedOptionContract",
    "StrategyDecision",
    "StructureSelection",
    "TechnicalLayerOutput",
    "merge_all_layer_features",
    "merge_layer_features",
]
