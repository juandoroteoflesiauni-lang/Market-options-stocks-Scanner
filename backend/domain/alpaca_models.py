"""Modelos nativos del dominio Alpaca (equities). # [PD-2][IM][TH]

Reemplazan las estructuras acopladas de BingX (perpetuos). Semántica de
acciones: solo LONG, sin apalancamiento, sin ``position_side``/``notional_usdt``.
Todos los modelos son Pydantic v2 ``frozen=True`` (inmutables).
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from backend.domain.alpaca_options_models import OptionsConfluence

# ─── Aliases de tipos estables ────────────────────────────────────────────────
Suitability = Literal["ALLOW", "SIZE_DOWN", "BLOCK", "INSUFFICIENT_DATA"]
EquityDirection = Literal["LONG", "FLAT"]
AlpacaRoute = Literal["priority", "scan"]
R2ConfluenceTier = Literal["NONE", "S1", "S2", "S3"]


class AlpacaCandidateAnalysis(BaseModel):
    """Análisis técnico consolidado de una acción candidata."""

    model_config = ConfigDict(frozen=True)

    symbol: str
    timestamp: str
    market_type: Literal["stock"] = "stock"
    latest_close: float | None = None
    atr: float | None = None
    macd_histogram: float | None = None
    relative_strength: float | None = None
    volume_z_score: float | None = None
    close_position_in_range: float | None = None
    technical_ok: bool = False
    technical_payload: dict[str, Any] = Field(default_factory=dict)
    route: AlpacaRoute = "scan"
    r2_technical_score: dict[str, Any] = Field(default_factory=dict)
    r2_confluence_tier: R2ConfluenceTier = "NONE"
    options_confluence: OptionsConfluence | None = None


class AlpacaDecision(BaseModel):
    """Veredicto del motor de decisión nativo (LONG-only)."""

    model_config = ConfigDict(frozen=True)

    symbol: str
    decision: Suitability
    direction: EquityDirection
    score: float
    probability: float | None = None
    reason_codes: tuple[str, ...] = ()
    route: AlpacaRoute = "scan"


class EquityOrderIntent(BaseModel):
    """Intención de orden de acciones: LONG, contado, cantidad entera."""

    model_config = ConfigDict(frozen=True)

    symbol: str
    side: Literal["BUY"] = "BUY"
    quantity: int = Field(gt=0)
    entry_type: Literal["MARKET", "LIMIT"] = "MARKET"
    reference_price: float = Field(gt=0)
    stop_loss: float | None = None
    take_profit: float | None = None
    notional_usd: float = Field(ge=0)
    client_order_id: str
    cycle_id: str | None = None
    reason_codes: tuple[str, ...] = ()
    route: AlpacaRoute = "scan"


class EquityRiskDecision(BaseModel):
    """Resolución del risk desk para una intención de orden."""

    model_config = ConfigDict(frozen=True)

    authorized: bool
    intent: EquityOrderIntent
    idempotency_key: str
    reason_codes: tuple[str, ...] = ()
    adjusted_quantity: int | None = None
    already_seen: bool = False


class EquityCycleResult(BaseModel):
    """Resultado completo de un ciclo del bot Alpaca."""

    model_config = ConfigDict(frozen=True)

    started_at: str
    finished_at: str
    universe: tuple[str, ...]
    prefiltered: tuple[str, ...]
    route1_symbols: tuple[str, ...] = ()
    route2_symbols: tuple[str, ...] = ()
    analyses: tuple[AlpacaCandidateAnalysis, ...] = ()
    decisions: tuple[AlpacaDecision, ...] = ()
    order_intents: tuple[EquityOrderIntent, ...] = ()
    risk_decisions: tuple[EquityRiskDecision, ...] = ()
    executions: tuple[dict[str, Any], ...] = ()
    options_entries: tuple[dict[str, Any], ...] = ()
    options_executed_symbols: tuple[str, ...] = ()
    options_reserved_premium_usd: float = 0.0
    eod_flatten: dict[str, Any] = Field(default_factory=dict)
    dry_run: bool = True
    trading_environment: str = "paper"
    blocked_reasons: dict[str, list[str]] = Field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        """Serialización JSON-safe para la capa API."""
        return self.model_dump(mode="json")
