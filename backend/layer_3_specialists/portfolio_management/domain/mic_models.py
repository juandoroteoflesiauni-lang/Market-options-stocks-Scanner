from __future__ import annotations

from enum import Enum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, model_validator

# MIGRATION: Dependencia de dominio entre especialistas
from ...tecnico.fractal_models import EntropyScore, FractalSignal

# ─────────────────────────────────────────────────────────────────────────────
# ENUMS
# ─────────────────────────────────────────────────────────────────────────────


class SignalDirection(str, Enum):
    LONG = "LONG"
    SHORT = "SHORT"
    CASH = "CASH"


class DirectionalBias(str, Enum):
    BULLISH = "BULLISH"
    BULLISH_WATCH = "BULLISH_WATCH"
    NEUTRAL = "NEUTRAL"
    CASH = "CASH"
    BEARISH_WATCH = "BEARISH_WATCH"
    BEARISH = "BEARISH"


class WyckoffFase(str, Enum):
    MARKUP = "MARKUP"
    ACUMULACION = "ACUMULACIÓN"
    RANGO = "RANGO"
    DISTRIBUCION = "DISTRIBUCIÓN"
    MARKDOWN = "MARKDOWN"


class VSALabel(str, Enum):
    STOPPING_VOLUME = "STOPPING_VOLUME"
    NO_SUPPLY = "NO_SUPPLY"
    EFFORT_VS_RESULT = "EFFORT_VS_RESULT"
    NEUTRAL = "NEUTRAL"
    NO_DEMAND = "NO_DEMAND"
    CLIMAX_BUY = "CLIMAX_BUY"
    CLIMAX_SELL = "CLIMAX_SELL"


class VetoCode(str, Enum):
    PRE_FILTRO_BINARY_EVENT = "PRE_FILTRO_BINARY_EVENT"
    PRE_FILTRO_LIQUIDITY_DRAIN = "PRE_FILTRO_LIQUIDITY_DRAIN"
    VETO_1_ALLIGATOR_DORMIDO = "VETO_1_ALLIGATOR_DORMIDO"
    VETO_2_CALLWALL = "VETO_2_CALLWALL"
    VETO_3_MARKOV_SHOCK = "VETO_3_MARKOV_SHOCK"
    VETO_4_EMBI_ARG = "VETO_4_EMBI_ARG"
    VETO_5_KELLY_NEGATIVO = "VETO_5_KELLY_NEGATIVO"
    VETO_6_FORENSIC = "VETO_6_FORENSIC"
    INTERCEPCION_DIRECCIONAL = "INTERCEPCION_DIRECCIONAL_SMC"
    VETO_BLANDO_FEAR_GREED = "VETO_BLANDO_FEAR_GREED"
    VETO_7_ENTROPY_GATE = "VETO_7_ENTROPY_GATE"
    VETO_9_CM_SYMMETRY = "VETO_9_CM_SYMMETRY"


# ─────────────────────────────────────────────────────────────────────────────
# INPUT MODELS (SIGNALS)
# ─────────────────────────────────────────────────────────────────────────────


class SMCSignal(BaseModel):
    model_config = ConfigDict(frozen=True)
    sesgo_scoring: DirectionalBias = DirectionalBias.NEUTRAL
    modelo_ict: int | None = None
    aggregate_confidence: float = Field(default=0.0, ge=0.0, le=1.0)
    has_active_ob_bullish: bool = False
    has_unmitigated_fvg: bool = False
    ob_all_invalidated: bool = True


class GEXSignal(BaseModel):
    model_config = ConfigDict(frozen=True)
    pcr_oi: float = Field(default=1.0)
    gex_regime: str = Field(default="NEUTRAL")
    call_wall: float | None = None
    put_wall: float | None = None
    spot_price: float | None = None
    options_cash_override: bool = False


class MacroSignal(BaseModel):
    model_config = ConfigDict(frozen=True)
    regime_score: float = Field(default=0.0, ge=-1.0, le=1.0)
    drain_severity: float = Field(default=0.0, ge=0.0, le=1.0)
    vix_actual: float = Field(default=0.0)
    fear_greed_index: float = Field(default=50.0)
    liquidity_drain: bool = False
    embi_arg_pb: float = Field(default=0.0)


class VSASignal(BaseModel):
    model_config = ConfigDict(frozen=True)
    señal_dominante: VSALabel = VSALabel.NEUTRAL
    a_index_zscore: float = Field(default=0.0)
    mfi_3: float = Field(default=50.0)


class AlligatorSignal(BaseModel):
    model_config = ConfigDict(frozen=True)
    wyckoff_fase: WyckoffFase = WyckoffFase.RANGO
    jaw_lips_spread_pct: float = Field(default=0.01)
    ob_bullish_activo: bool = False


class ForensicSignal(BaseModel):
    model_config = ConfigDict(frozen=True)
    beneish_m: float | None = None
    altman_zone: str | None = None
    altman_z: float | None = None
    piotroski_score: int | None = None
    piotroski_reliable: bool = False
    is_distressed: bool = False
    bonificacion_paso_b: bool = False
    economic_spread: float | None = None


class RiskSignal(BaseModel):
    model_config = ConfigDict(frozen=True)
    kelly_fraction: float = Field(default=0.0)
    position_size: float = Field(default=0.0, ge=0.0)


class MarkovSignal(BaseModel):
    model_config = ConfigDict(frozen=True)
    kelly_macro_multiplier: float = Field(default=1.0, gt=0.0)
    hmm_confidence_pct: float = Field(default=100.0, ge=0.0, le=100.0)


class NewsSignal(BaseModel):
    model_config = ConfigDict(frozen=True)
    binary_event_warning: bool = False
    max_categoria: str = "DESCONOCIDA"


class SentimentSignal(BaseModel):
    model_config = ConfigDict(frozen=True)
    score: float = Field(default=0.0, ge=0.0, le=1.0)
    confidence: float = Field(default=0.0, ge=0.0, le=1.0)
    consensus: str = "NEUTRAL"


class OptionsSignal(BaseModel):
    model_config = ConfigDict(frozen=True)
    vex_score: float = Field(default=0.0, ge=0.0, le=10.0)
    cex_score: float = Field(default=0.0, ge=0.0, le=10.0)
    gex_regime: str = "NEUTRAL"
    vanna_sweep_probability: float = 0.0


class AggregatedSignals(BaseModel):
    model_config = ConfigDict(arbitrary_types_allowed=True)

    ticker: str
    smc: SMCSignal | None = None
    gex: GEXSignal | None = None
    macro: MacroSignal | None = None
    vsa: VSASignal | None = None
    alligator: AlligatorSignal | None = None
    forensic: ForensicSignal | None = None
    risk: RiskSignal | None = None
    markov: MarkovSignal | None = None
    options: OptionsSignal | None = None
    news: NewsSignal | None = None
    sentiment: SentimentSignal | None = None
    fractal: FractalSignal | None = None
    entropy: EntropyScore | None = None
    cerebro: Any | None = None
    modo_conservador: bool = False
    modo_conservador_factor: float = Field(default=1.0, gt=0.0, le=1.0)


# ─────────────────────────────────────────────────────────────────────────────
# OUTPUT MODELS (DECISION)
# ─────────────────────────────────────────────────────────────────────────────


class ComponentScore(BaseModel):
    model_config = ConfigDict(frozen=True)
    raw_sub_score: float
    weight: float
    contribution: float
    detail: str


class MICDecision(BaseModel):
    model_config = ConfigDict(frozen=True)

    ticker: str
    signal: SignalDirection
    size_multiplier: float
    score_bruto: float
    score_final: float
    kelly_macro: float
    vetos_activos: list[str] = Field(default_factory=list)
    component_scores: dict[str, ComponentScore] = Field(default_factory=dict)
    bonificaciones_aplicadas: list[str] = Field(default_factory=list)
    triple_confluencia_activa: bool = False
    modo_conservador: bool = False
    size_label: str = ""

    @model_validator(mode="after")
    def validate_long_only(self) -> MICDecision:
        allowed = {SignalDirection.LONG, SignalDirection.SHORT, SignalDirection.CASH}
        if self.signal not in allowed:
            raise ValueError(
                f"PRIMERA LEY FÍSICA VIOLADA: signal='{self.signal}' is not LONG-ONLY."
            )
        return self


AggregatedSignals.model_rebuild()
