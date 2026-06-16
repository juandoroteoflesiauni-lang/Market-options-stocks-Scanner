from __future__ import annotations
from typing import Any
"""
backend/domain/state.py
════════════════════════════════════════════════════════════════════════════════
QuantumState — Canonical global pipeline state object.
SystemState  — Global system, provider and market status (Sector: DATA).
Result models for each engine — pure output contracts.

SYSTEM MANDATE: LONG-ONLY. No short signals. Signal ∈ {LONG, CASH, WATCH}.
════════════════════════════════════════════════════════════════════════════════
"""


from datetime import UTC, datetime
from enum import Enum

from pydantic import BaseModel, ConfigDict, Field
from quantumbeta.domain.models import CEDEARRate, CMExtension, EntropyScore, FractalSignal
from quantumbeta.domain.options_models import OptionsResult
from quantumbeta.domain.probabilistic_models import ProbabilisticResult

# ─────────────────────────────────────────────────────────────────────────────
# Canonical Enumerations
# ─────────────────────────────────────────────────────────────────────────────


class SignalDirection(str, Enum):
    LONG = "LONG"
    CASH = "CASH"
    WATCH = "WATCH"  # High score but active veto — monitor


class CurveRegime(str, Enum):
    RISK_ON = "RISK-ON"
    RISK_OFF = "RISK-OFF"
    NEUTRAL = "NEUTRAL"


class MarkovRegime(str, Enum):
    BULL_LOW_VOL = "BULL_LOW_VOL"
    BEAR_HIGH_VOL = "BEAR_HIGH_VOL"
    DISTRIBUTION = "DISTRIBUTION"
    SHOCK = "SHOCK"
    UNKNOWN = "UNKNOWN"


class RiskRegime(str, Enum):
    RISK_ON = "RISK_ON"
    RISK_OFF = "RISK_OFF"
    NEUTRAL = "NEUTRAL"
    SHOCK = "SHOCK"


class SectorTilt(str, Enum):
    TECNOLOGIA = "Tecnología"
    DEFENSIVOS = "Defensivos"
    BANCOS = "Bancos"
    ENERGIA = "Energía"
    SALUD = "Salud"
    CONSUMO = "Consumo Discrecional"
    MATERIALES = "Materiales"
    INMOBILIARIO = "Inmobiliario"
    UTILIDADES = "Utilidades"
    NEUTRO = "Neutro"


class CreditRegime(str, Enum):
    NORMAL = "NORMAL"
    STRESSED = "STRESSED"
    DISTRESSED = "DISTRESSED"


# ── Data Sector Enumerations ─────────────────────────────────────────────────


class MarketSession(str, Enum):
    PRE_MARKET = "PRE_MARKET"
    REGULAR = "REGULAR"
    AFTER_HOURS = "AFTER_HOURS"
    CLOSED = "CLOSED"


class ProviderStatus(str, Enum):
    UP = "UP"
    DOWN = "DOWN"
    DEGRADED = "DEGRADED"
    UNKNOWN = "UNKNOWN"


class FXType(str, Enum):
    OFICIAL = "OFICIAL"
    BLUE = "BLUE"
    CCL = "CCL"
    MEP = "MEP"


# ─────────────────────────────────────────────────────────────────────────────
# Data Sector Models (Capa 1)
# ─────────────────────────────────────────────────────────────────────────────


class MarketState(BaseModel):
    """Real-time market status (Sector: DATA)."""

    model_config = ConfigDict(frozen=True)

    is_open: bool = False
    current_session: MarketSession = MarketSession.CLOSED
    next_open: datetime | None = None
    next_close: datetime | None = None
    timezone: str = "America/New_York"


class ProviderState(BaseModel):
    """Health status for a specific data provider."""

    model_config = ConfigDict(frozen=True)

    provider_name: str
    status: ProviderStatus = ProviderStatus.UNKNOWN
    availability: float = 0.0  # 0.0 - 1.0 (uptime)
    last_fetch: datetime | None = None
    error_count: int = 0
    latency_ms: float = 0.0


class CacheState(BaseModel):
    """Internal cache metrics."""

    model_config = ConfigDict(frozen=True)

    hits: int = 0
    misses: int = 0
    evictions: int = 0
    size_mb: float = 0.0


class SystemState(BaseModel):
    """Canonical global system status (Sector: DATA)."""

    model_config = ConfigDict(frozen=True)

    market: MarketState
    providers: dict[str, ProviderState] = Field(default_factory=dict)
    cache: CacheState
    system_load: float = 0.0
    uptime_seconds: int = 0
    last_update: datetime = Field(default_factory=lambda: datetime.now(UTC))


# ─────────────────────────────────────────────────────────────────────────────
# Engine Result Models (Capa 3)
# ─────────────────────────────────────────────────────────────────────────────


class MacroResult(BaseModel):
    """MacroEngine result — Yield Curve + Macro Fundamentals."""

    model_config = ConfigDict(frozen=True)

    curve_regime: CurveRegime | None = None
    t10y2y: float | None = None  # spread in bps
    t10y2y_prev: float | None = None
    fed_funds_rate: float | None = None
    cpi_yoy: float | None = None
    unemployment: float | None = None
    source: str = "UNAVAILABLE"
    as_of: str = "N/A"
    error: str | None = None
    macro_score: float = 0.0  # 0.0–2.0 for MIC
    vix_actual: float | None = None
    vix_prev: float | None = None


class SMCResult(BaseModel):
    """SMCEngine result — Smart Money Concepts."""

    model_config = ConfigDict(frozen=True)

    bias: str = "CASH"  # "LONG" | "CASH"
    confidence: float = 0.0  # 0.0–1.0
    structure: str = "BEARISH"  # MarketStructure value
    active_ict_model: str = "NONE"  # ICTModel value
    wyckoff_accumulation: bool | None = None
    ob_count_active: int = 0  # Active unmitigated Order Blocks
    fvg_count_active: int = 0  # Unfilled FVGs
    choch_count: int = 0
    key_levels: dict[str, float] = Field(default_factory=dict)
    smc_score: float = 0.0  # 0.0–2.5 for MIC


class GEXResult(BaseModel):
    """GEXEngine result — Gamma Exposure + Options Flow."""

    model_config = ConfigDict(frozen=True)

    total_gex: float = 0.0
    call_gex: float = 0.0
    put_gex: float = 0.0
    dealer_bias: str = "NEUTRAL"
    zero_gamma_level: float | None = None
    volatility_magnet: float | None = None  # Max Pain
    call_wall: float | None = None
    put_wall: float | None = None
    vanna_walls: list[float] = Field(default_factory=list)
    net_vanna_flow: float = 0.0
    near_term_vanna: float = 0.0
    expiry_pressure: str = "NEUTRAL"  # BUY/SELL/NEUTRAL
    dominant_strike: float | None = None
    has_options_data: bool = False
    gex_score: float = 0.0  # 0.0–2.0 for MIC
    vanna_flip_active: bool = False  # Triggered by VIX crush
    magnetic_walls: list[float] = Field(default_factory=list)
    volatility_trigger: float | None = None
    dix_value: float = 0.0  # Dark Pool Index [0-100]
    dix_bullish_signal: bool = False  # DIX >= 45%
    is_0dte_dominant: bool = False  # 0DTE GEX > 50% Total GEX
    hiro_net_delta: float = 0.0  # Dealer net delta (HIRO proxy)
    hiro_bias: str = "NEUTRAL"  # BULLISH | BEARISH | NEUTRAL


class MarkovResult(BaseModel):
    """MarkovEngine result — Hidden Markov Model Regimes."""

    model_config = ConfigDict(frozen=True)

    current_regime: str | None = None
    regime_probability: float | None = None
    fit_method: str = "none"  # "hmm" | "heuristic" | "none"
    model_score: float | None = None  # log-likelihood
    transition_matrix: list[list[float]] | None = None
    regime_durations: dict[str, float] = Field(default_factory=dict)
    regime_returns: dict[str, float] = Field(default_factory=dict)
    hmm_confidence: float = 0.0  # 0–100%
    kelly_macro: float = 1.0  # Kelly multiplier


class ForensicResult(BaseModel):
    """ForensicEngine result — Forensic Accounting."""

    model_config = ConfigDict(frozen=True)

    beneish_m: float | None = None
    beneish_verdict: str | None = None
    z_score: float | None = None  # Altman Z
    z_zone: str | None = None
    f_score: int | None = None  # Piotroski F (0-9)
    f_assessment: str | None = None
    is_distressed: bool = False
    composite_verdict: str = "N/A"
    red_flags: list[str] = Field(default_factory=list)
    green_flags: list[str] = Field(default_factory=list)
    forensic_score: float = 0.0  # 0.0–0.5 for MIC


class ValuationResult(BaseModel):
    """ValuationEngine result — DCF Fair Value."""

    model_config = ConfigDict(frozen=True)

    fair_value: float = 0.0
    current_price: float = 0.0
    margin_of_safety: float = 0.0  # positive = undervalued
    upside_pct: float = 0.0
    bear_value: float = 0.0
    base_value: float = 0.0
    bull_value: float = 0.0
    terminal_value_pv: float = 0.0
    pv_fcfs: list[float] = Field(default_factory=list)
    wacc: float = 0.0
    verdict: str = "N/A"
    ev_ebitda: float | None = None
    p_fcf: float | None = None
    p_e: float | None = None


class VSAResult(BaseModel):
    """VSAEngine result — Volume Spread Analysis."""

    model_config = ConfigDict(frozen=True)

    stopping_volume: bool = False
    no_supply: bool = False
    no_demand: bool = False
    selling_climax: bool = False
    any_signal: bool = False
    vsa_score: float = 0.0  # 0.0–2.0 for MIC
    rvol: float = 1.0  # Relative Volume
    vol_velocity: float = 0.0  # Volume Acceleration
    buy_absorption: bool = False  # Absorption at bottom wick
    sell_absorption: bool = False  # Absorption at top wick
    effort_result_ratio: float = 0.0  # Move / Volume
    relative_position: float = 0.0  # Close Location % (Paper A_index)
    last_relative_position: float = 0.0  # Alias for A_index to match Confluence
    last_mfi_kinetic: float | None = None  # Money Flow Index (Kinetic)
    adv: float = 0.0  # Accumulation/Distribution Volume
    weis_wave_peak: bool = False  # Wave volume exhaustion signal
    vfi_value: float = 0.0  # Volume Flow Indicator
    vfi_slope: float = 0.0  # Momentum of volume flow
    is_forecast_climax: bool = False  # Predictive climax for live bars
    footprint_support: float | None = None
    footprint_resistance: float | None = None
    cvd_last: float = 0.0
    cvd_slope: float = 0.0


class SentimentResult(BaseModel):
    """SentimentEngine result — News & Social Sentiment."""

    model_config = ConfigDict(frozen=True)

    score: float = 0.0  # -1.0 (bearish) to 1.0 (bullish)
    consensus: str = "NEUTRAL"  # BULLISH, BEARISH, NEUTRAL
    confidence: float = 0.0  # 0.0–1.0
    sentiment_score: float = 0.0  # 0.0-1.0 for MIC (normalised)
    news_count: int = 0
    top_themes: list[str] = Field(default_factory=list)


class AlligatorResult(BaseModel):
    """AlligatorEngine result — Williams Alligator."""

    model_config = ConfigDict(frozen=True)

    jaw: float | None = None
    teeth: float | None = None
    lips: float | None = None
    is_sleeping: bool = True  # Sleeping alligator = no trend
    is_bullish: bool = False  # Lips > Teeth > Jaw
    is_bearish: bool = False
    alligator_score: float = 0.0  # 0.0–1.0 for MIC


class RiskResult(BaseModel):
    """RiskEngine result — Kelly + VaR."""

    model_config = ConfigDict(frozen=True)

    kelly_pct: float = 0.0
    half_kelly_pct: float = 0.0
    suggested_exposure: float = 0.0
    kelly_fraction: float = 0.0
    edge: float = 0.0
    win_prob: float = 0.0
    reward_risk_ratio: float = 0.0
    cap_was_applied: bool = False
    is_tradeable: bool = False
    stop_loss: float | None = None
    tp1: float | None = None
    tp2: float | None = None
    var_95: float = 0.0
    portfolio_vol: float = 0.0


class AIResult(BaseModel):
    """AI Engine result — Gemini Multimodal Fusion + ToT/LATS."""

    model_config = ConfigDict(frozen=True)

    texto: str = ""
    model_used: str = "N/A"
    prompt_tokens: int = 0
    latency_ms: float = 0.0
    error: str | None = None

    # Advanced Prompt Framework Outputs
    expert_briefs: dict[str, str] = Field(default_factory=dict)  # Persona-specific summaries
    thought_tree: str | None = None  # Full reasoning trace for ToT/LATS
    framework_used: str = "LINEAR"  # LINEAR, ToT, LATS
    audit_score: float = 0.0  # Internal critique score (0-1)


class MonteCarloResult(BaseModel):
    """OptionsRiskCalculator.monte_carlo_risk result."""

    model_config = ConfigDict(frozen=True)

    expected_return_pct: float = 0.0
    expected_pnl_usd: float = 0.0
    var_usd: float = 0.0
    cvar_usd: float = 0.0
    probability_profit: float = 0.0
    avg_path_drawdown_pct: float = 0.0
    worst_path_drawdown_pct: float = 0.0
    stress_var_usd: float = 0.0
    stress_cvar_usd: float = 0.0
    risk_regime: str = "UNKNOWN"
    kill_switch: bool = False
    risk_budget_pct: float = 0.0
    recommended_position_usd: float = 0.0


class QuantumAlphaResult(BaseModel):
    """QuantumAlphaEngine (LSTM) predictive result."""

    model_config = ConfigDict(frozen=True)

    direction_prob: float = 0.5  # 0.0-1.0 (prob of UP)
    signal: str = "WATCH"  # LONG | CASH | WATCH
    confidence: float = 0.0
    horizon_days: int = 6
    is_valid: bool = False
    inference_latency_ms: float = 0.0


class ImpliedPDFResult(BaseModel):
    """VolatilitySurfaceMath.bl_pdf result — Risk-neutral distribution."""

    model_config = ConfigDict(frozen=True)

    strikes: list[float] = Field(default_factory=list)
    density: list[float] = Field(default_factory=list)
    risk_neutral_mean: float = 0.0
    risk_neutral_std: float = 0.0
    skewness: float = 0.0
    excess_kurtosis: float = 0.0
    tail_regime: str = "SYMMETRIC"


class CerebroResult(BaseModel):
    """
    Cerebro Matematico result — Combined Quant-AI Oracle.
    """

    model_config = ConfigDict(frozen=True)

    ticker: str
    timestamp: str
    cm_score: float = 0.0  # 0.0–1.0
    cm_extension: CMExtension | None = None
    regime_summary: str = "NEUTRAL"
    is_ordered: bool = False
    is_convergent: bool = False
    veto_active: bool = False
    veto_reason: str = ""
    ok: bool = True


class CreditRiskResult(BaseModel):
    """CreditRiskEngine result — Yield Spread Solvency Analysis."""

    model_config = ConfigDict(frozen=True)

    has_credit_data: bool = False
    current_spread_bps: float = 0.0
    spread_z_score: float = 0.0
    credit_regime: CreditRegime = CreditRegime.NORMAL
    credit_veto: bool = False


class MorningBriefResult(BaseModel):
    """MorningBriefingEngine result — Macro & News Narrative."""

    model_config = ConfigDict(frozen=True)

    risk_regime: RiskRegime = RiskRegime.NEUTRAL
    conviction_score: float = 0.0
    key_drivers: list[str] = Field(default_factory=list)
    sector_tilt: SectorTilt = SectorTilt.NEUTRO
    generated_at: str = ""
    is_fallback: bool = False


class VolumeProfileResult(BaseModel):
    """VolumeProfileEngine result — POC, VAH/VAL, AVWAP."""

    model_config = ConfigDict(frozen=True)

    ok: bool = False
    poc: float = 0.0
    vah: float = 0.0
    val: float = 0.0
    avwap: float = 0.0
    is_above_avwap: bool = False
    is_above_poc: bool = False
    volume_bias: str = "NEUTRAL"
    anchor_date: str = ""


class RegulatoryVetoResult(BaseModel):
    """RegulatoryScannerEngine result — SEC/DOJ Risk Scan."""

    model_config = ConfigDict(frozen=True)

    absolute_veto: bool = False
    severity_level: str = "NONE"  # EXISTENTIAL | HIGH | LOW | NONE
    action_directive: str = "CLEAR"  # LIQUIDATE | REDUCE | CLEAR
    matched_keywords: list[str] = Field(default_factory=list)
    source: str = "UNKNOWN"
    scan_timestamp: float = 0.0


class PortfolioStats(BaseModel):
    """Stats for a specific optimized portfolio."""

    model_config = ConfigDict(frozen=True)

    weights: dict[str, float] = Field(default_factory=dict)
    expected_return: float = 0.0
    volatility: float = 0.0
    sharpe_ratio: float = 0.0


class PortfolioResult(BaseModel):
    """PortfolioEngine result — CAPM & Markowitz Optimization."""

    model_config = ConfigDict(frozen=True)

    min_variance_portfolio: PortfolioStats | None = None
    tangency_portfolio: PortfolioStats | None = None
    is_valid_optimization: bool = False
    warnings: list[str] = Field(default_factory=list)


# ─────────────────────────────────────────────────────────────────────────────
# Veto / Bonus System
# ─────────────────────────────────────────────────────────────────────────────


class VetoCode(str, Enum):
    CALLWALL_VETO = "CALLWALL_VETO"  # price >= call_wall × 0.995
    MARKOV_SHOCK_VETO = "MARKOV_SHOCK_VETO"  # shock regime detected
    ALLIGATOR_DORMIDO = "ALLIGATOR_DORMIDO"  # no confirmed trend
    EMBI_ARG_VETO = "EMBI_ARG_VETO"  # EMBI Argentina > 1200
    FORENSIC_DISTRESS = "FORENSIC_DISTRESS"  # is_distressed = True
    CURVE_INVERTED = "CURVE_INVERTED"  # yield curve RISK-OFF
    DIRECTIONAL_DIVERGENCE = "INTERCEPCION_DIRECCIONAL_SMC"  # Cerebro LONG vs MIC CASH divergence
    ENTROPY_GATE_VETO = "VETO_7_ENTROPY_GATE"  # Shannon Entropy complexity filter
    PROBABILISTIC_GATE_VETO = "VETO_8_PROBABILISTIC_GATE"  # GPD Jump / PF Ordered filter
    CM_SYMMETRY_VETO = "VETO_9_CM_SYMMETRY"  # Cerebro Matematico symmetry failure
    CREDIT_VETO = "CREDIT_VETO"  # Bond spread distressed
    REGULATORY_VETO = "REGULATORY_VETO"  # SEC Existential threat


class BonificacionCode(str, Enum):
    PASO_A = "PASO_A"  # Wyckoff ACCUMULATION + OB aligned (+0.5 pts)
    PASO_B = "PASO_B"  # Vanna Flow positive + Liquidity Sweep (×1.5)


# ─────────────────────────────────────────────────────────────────────────────
# MIC Score Thresholds — Long-Only system
# MIC score range: 0–100
# Sniper threshold: >= 82 → LONG
# Cash threshold:   < 45  → CASH
# ─────────────────────────────────────────────────────────────────────────────

MIC_SNIPER_THRESHOLD: float = 82.0
MIC_CASH_THRESHOLD: float = 45.0


from quantumbeta.domain.credit_models import FixedIncomeResult
from quantumbeta.domain.stochastic_models import StochasticPredictiveResult
from quantumbeta.engines.arg_macro_engine import ArgMacroResult

# ─────────────────────────────────────────────────────────────────────────────
# QuantumState — Canonical pipeline state object
# ─────────────────────────────────────────────────────────────────────────────


class QuantumState(BaseModel):
    """
    Global state object that traverses the entire analysis pipeline.

    LIFECYCLE:
        1. Orchestrator instantiates QuantumState with ticker + input params.
        2. Each engine receives what it needs as arguments; Orchestrator
           writes the result into the corresponding field.
        3. No engine writes directly to state — only the Orchestrator does.
        4. At pipeline end, QuantumState contains all consolidated information
           ready for any entrypoint (CLI, FastAPI, PDF Reporter, etc.)

    SYSTEM MANDATE: LONG ONLY — No Short Selling.
        Signal can only be LONG, CASH, or WATCH.

    MIC SCORE: 0–100 range.
        Sniper threshold >= 82 → LONG signal eligible.
        Cash threshold < 45 → forced CASH.
    """

    model_config = ConfigDict(use_enum_values=True)

    # ── Input parameters ──────────────────────────────────────────────────────
    ticker: str
    period: str = "2y"
    timeframe: str = "1D"
    timestamp: str = Field(default_factory=lambda: datetime.now(UTC).isoformat())

    # ── Base data (loaded by DataLake before pipeline) ────────────────────────
    current_price: float = 0.0
    has_ohlcv: bool = False
    has_fundamentals: bool = False
    has_options: bool = False
    is_ccl_normalized: bool = False  # True if analyzed in stabilized USD CCL

    # ── Engine results (None = engine not run or failed) ─────────────────────
    macro_result: MacroResult | None = None
    smc_result: SMCResult | None = None
    gex_result: GEXResult | None = None
    volume_profile_result: VolumeProfileResult | None = None
    options_result: OptionsResult | None = None
    markov_result: MarkovResult | None = None
    forensic_result: ForensicResult | None = None
    valuation_result: ValuationResult | None = None
    vsa_result: VSAResult | None = None
    alligator_result: AlligatorResult | None = None
    risk_result: RiskResult | None = None
    ai_result: AIResult | None = None
    fixed_income_result: FixedIncomeResult | None = None
    fractal_result: FractalSignal | None = None
    entropy_result: EntropyScore | None = None
    probabilistic_result: ProbabilisticResult | None = None
    sentiment_result: SentimentResult | None = None
    cerebro_result: CerebroResult | None = None
    arg_macro_result: ArgMacroResult | None = None  # Argentine macro context (ArgMacroEngine)
    cedear_rate: CEDEARRate | None = None  # Live CCL/MEP/oficial rates (ArgMacroEngine)
    stochastic_predictive_result: StochasticPredictiveResult | None = None

    # ── Advanced Predictive / Probabilistic Extensions ──────────────────────
    monte_carlo_result: MonteCarloResult | None = None
    quantum_alpha_result: QuantumAlphaResult | None = None
    implied_pdf_result: ImpliedPDFResult | None = None

    # ── Institutional Results ────────────────────────────────────────────────
    credit_risk_result: CreditRiskResult | None = None
    morning_brief_result: MorningBriefResult | None = None
    regulatory_result: RegulatoryVetoResult | None = None
    portfolio_result: PortfolioResult | None = None

    # ── MIC Score — Institutional Confluence ─────────────────────────────────
    # Score range: 0–100. Canonical weights per engine sub-score.
    # {"gex": 0.0–2.0, "vsa": 0.0–2.0, "smc": 0.0–2.5,
    #  "alligator": 0.0–1.0, "macro": 0.0–2.0, "forensic": 0.0–0.5}
    mic_score_raw: float = 0.0  # weighted sum before bonuses
    mic_score_final: float = 0.0  # score with bonuses applied (0–100)
    mic_components: dict[str, float] = Field(default_factory=dict)
    triple_confluencia_activa: bool = False
    pillar_scores: Any = None
    pillar_mic: float = 0.0

    # ── Veto System ───────────────────────────────────────────────────────────
    vetos_activos: list[VetoCode] = Field(default_factory=list)
    bonificaciones: list[BonificacionCode] = Field(default_factory=list)
    veto_penalty: float = 0.0

    # ── Forensic probabilistic diagnostics ───────────────────────────────────
    forensic_distress_prob: float | None = None
    forensic_models_available: int = 0

    # ── Effective MIC (post-veto adjustment) ─────────────────────────────────
    mic_score_effective: float = 0.0

    # ── Final Executive Signal ────────────────────────────────────────────────
    signal: SignalDirection = SignalDirection.CASH
    signal_confidence: float = 0.0  # 0.0–1.0
    sniper_active: bool = False  # Irrefutable Long signal

    # ── Verdict Table (directly consumable by UI / CLI / PDF) ────────────────
    direction: str = "CASH"
    kelly_size_str: str = "CASH — no edge"
    stop_loss_str: str = "N/A"
    target_str: str = "N/A"
    rr_ratio_str: str = "N/A"
    fair_value_str: str = "N/A"
    confidence_str: str = "0%"

    # ── Engine errors ─────────────────────────────────────────────────────────
    engine_errors: dict[str, str] = Field(default_factory=dict)
    pipeline_duration_ms: float = 0.0

    # ── Read helpers ──────────────────────────────────────────────────────────

    def is_tradeable(self) -> bool:
        """True if signal is LONG and no active vetos."""
        return self.signal == SignalDirection.LONG and not self.vetos_activos

    def has_engine_failed(self, engine_name: str) -> bool:
        return engine_name.upper() in self.engine_errors

    def is_sniper_quality(self) -> bool:
        """True if MIC score meets sniper threshold (>= 82)."""
        return self.mic_score_final >= MIC_SNIPER_THRESHOLD

    def is_cash_forced(self) -> bool:
        """True if MIC score is below cash threshold (< 45)."""
        return self.mic_score_final < MIC_CASH_THRESHOLD

    def summary_dict(self) -> dict[str, Any]:
        """Condensed view for CLI / logging — excludes DataFrames."""
        return {
            "ticker": self.ticker,
            "timestamp": self.timestamp,
            "signal": self.signal,
            "mic_score": round(self.mic_score_final, 2),
            "mic_score_effective": round(self.mic_score_effective, 2),
            "veto_penalty": round(self.veto_penalty, 4),
            "vetos": self.vetos_activos,
            "bonificaciones": self.bonificaciones,
            "direction": self.direction,
            "kelly_size": self.kelly_size_str,
            "stop_loss": self.stop_loss_str,
            "target": self.target_str,
            "rr_ratio": self.rr_ratio_str,
            "engine_errors": self.engine_errors,
            "duration_ms": round(self.pipeline_duration_ms, 1),
        }


CerebroResult.model_rebuild()
QuantumState.model_rebuild()

# ─────────────────────────────────────────────────────────────────────
# MIGRATION AUDIT — SECTOR: DATA
# Archivo          : state.py
# Sub-capa         : Modelo
# Providers activos: N/A (Domain Models)
# Eliminado        : Referencias a Quantum Alpha V4/V6, comentarios legacy.
# Preservado       : QuantumState, Engine Results, Contratos de dominio.
# Pendientes       : Integración de SystemState en orquestador de Capa 1.
# ─────────────────────────────────────────────────────────────────────
