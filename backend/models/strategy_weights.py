from __future__ import annotations
"""Modelo de pesos estratégicos para el Asymmetric Data Funnel de 4 fases.

Cada fase del funnel (A → B → C → D) tiene un peso de contribución al score
final compuesto, más pesos internos por motor/indicador dentro de la fase.

Arquitectura:
  Phase A (Data Ingestion)    10% — gate de validación
  Phase B (Microstructure)    25% — OFI + SMC + VPIN
  Phase C (Derivatives)       45% — 8 motores quant + métricas de contrato
  Phase D (Execution)         20% — señales en tiempo real
                              ────
                              100%

Los pesos son inyectados vía API y persistidos en memoria. El engine de régimen
(RegimeWeightingEngine) puede modular estos pesos dinámicamente según el
régimen de mercado detectado (VIX + SPY MA50/MA200).
"""


from typing import ClassVar

from pydantic import BaseModel, ConfigDict, Field, model_validator


class PhaseAWeights(BaseModel):
    """Pesos Phase A — Data Ingestion & Global Filter (gate).

    Los primeros 4 campos controlan la validación de datos crudos.
    Los campos filter_* controlan los 6 filtros técnicos clásicos
    que corren sobre OHLCV cuando está disponible.
    """

    model_config = ConfigDict(frozen=True)

    # Peso de Phase A en el score compuesto total del funnel
    phase_weight: float = 0.10

    # Validación de datos (Phase A base)
    validation_strictness: float = Field(default=0.85, ge=0.0, le=1.0)
    min_price: float = Field(default=0.50, ge=0.0)
    min_volume: int = Field(default=10_000, ge=0)
    max_spread_pct: float = Field(default=0.20, ge=0.0, le=1.0)

    # Estrategia de obtención de datos
    preferred_source: str = Field(default="fmp", pattern=r"^(fmp|alpaca|polygon|combined)$")

    # ── Filtro 1: EMA Cluster Alignment (20%) ──────────────────────────────
    ema_cluster_min_score: float = Field(default=50.0, ge=0.0, le=100.0)
    ema_cluster_periods: tuple[int, int, int, int] = (9, 21, 50, 200)
    ema_cluster_min_aligned: int = Field(default=3, ge=2, le=4)

    # ── Filtro 2: ATR Volatility Gate (20%) ────────────────────────────────
    atr_gate_min_score: float = Field(default=50.0, ge=0.0, le=100.0)
    min_atr_pct: float = Field(default=0.003, ge=0.0, le=0.10)
    max_atr_pct: float = Field(default=0.05, ge=0.001, le=0.50)
    atr_period: int = Field(default=14, ge=5, le=100)

    # ── Filtro 3: RSI Extreme Filter (15%) ─────────────────────────────────
    rsi_extreme_min_score: float = Field(default=50.0, ge=0.0, le=100.0)
    rsi_oversold_threshold: float = Field(default=15.0, ge=0.0, le=50.0)
    rsi_overbought_threshold: float = Field(default=85.0, ge=50.0, le=100.0)
    rsi_period: int = Field(default=14, ge=5, le=50)

    # ── Filtro 4: VWAP Distance Z-Score (15%) ──────────────────────────────
    vwap_zscore_min_score: float = Field(default=50.0, ge=0.0, le=100.0)
    vwap_max_zscore: float = Field(default=3.0, ge=0.5, le=10.0)

    # ── Filtro 5: Shannon Entropy (15%) ────────────────────────────────────
    entropy_min_score: float = Field(default=50.0, ge=0.0, le=100.0)
    max_entropy: float = Field(default=3.5, ge=1.0, le=5.0)
    entropy_window: int = Field(default=20, ge=10, le=100)
    entropy_bins: int = Field(default=10, ge=5, le=50)

    # ── Filtro 6: SuperTrend Regime (15%) ──────────────────────────────────
    supertrend_min_score: float = Field(default=50.0, ge=0.0, le=100.0)
    supertrend_period: int = Field(default=10, ge=5, le=50)
    supertrend_multiplier: float = Field(default=3.0, ge=1.0, le=10.0)
    supertrend_max_changes: int = Field(default=2, ge=0, le=5)


class PhaseBWeights(BaseModel):
    """Pesos Phase B — Microstructure Analysis."""

    model_config = ConfigDict(frozen=True)

    phase_weight: float = 0.25

    # Pesos internos de indicadores (∑ = 1.0)
    ofi_weight: float = Field(default=0.45, ge=0.0, le=1.0)
    smc_weight: float = Field(default=0.35, ge=0.0, le=1.0)
    vpin_weight: float = Field(default=0.20, ge=0.0, le=1.0)

    # Parámetros de sensibilidad
    ofi_sensitivity: float = Field(default=1.0, ge=0.1, le=5.0)
    smc_lookback_periods: int = Field(default=20, ge=5, le=100)
    vpin_buckets: int = Field(default=50, ge=10, le=200)

    @model_validator(mode="after")
    def _weights_sum_to_one(self) -> PhaseBWeights:
        total = self.ofi_weight + self.smc_weight + self.vpin_weight
        if abs(total - 1.0) > 1e-6:
            raise ValueError(f"Phase B weights must sum to 1.0, got {total}")
        return self


class PhaseCContractFilters(BaseModel):
    """Filtros de selección de contratos para Phase C."""

    model_config = ConfigDict(frozen=True)

    min_volume: int = Field(default=100, ge=0)
    min_open_interest: int = Field(default=500, ge=0)
    max_spread_pct: float = Field(default=0.15, ge=0.0, le=1.0)
    min_dte: int = Field(default=14, ge=0)
    max_dte: int = Field(default=60, ge=1)
    delta_target_call: float = Field(default=0.35, ge=0.0, le=1.0)
    delta_target_put: float = Field(default=-0.35, ge=-1.0, le=0.0)
    min_composite_score: float = Field(default=40.0, ge=0.0, le=100.0)
    iv_min: float = Field(default=0.10, ge=0.0)
    iv_max: float = Field(default=0.40, ge=0.0)
    optimal_dte: int = Field(default=35, ge=1)


class PhaseCEngineWeights(BaseModel):
    """Pesos de los 8 motores quant en Phase C (∑ = 1.0)."""

    model_config = ConfigDict(frozen=True)

    gex_score: float = Field(default=0.20, ge=0.0, le=1.0)
    gamma_flip: float = Field(default=0.12, ge=0.0, le=1.0)
    dex_exposure: float = Field(default=0.15, ge=0.0, le=1.0)
    flow_signal: float = Field(default=0.12, ge=0.0, le=1.0)
    zero_day: float = Field(default=0.10, ge=0.0, le=1.0)
    shadow_delta: float = Field(default=0.10, ge=0.0, le=1.0)
    delta_flow: float = Field(default=0.08, ge=0.0, le=1.0)
    phase_b_momentum: float = Field(default=0.13, ge=0.0, le=1.0)

    @model_validator(mode="after")
    def _weights_sum_to_one(self) -> PhaseCEngineWeights:
        total = (
            self.gex_score
            + self.gamma_flip
            + self.dex_exposure
            + self.flow_signal
            + self.zero_day
            + self.shadow_delta
            + self.delta_flow
            + self.phase_b_momentum
        )
        if abs(total - 1.0) > 1e-6:
            raise ValueError(f"Phase C engine weights must sum to 1.0, got {total}")
        return self

    def to_dict(self) -> dict[str, float]:
        return {
            "gex_score": self.gex_score,
            "gamma_flip": self.gamma_flip,
            "dex_exposure": self.dex_exposure,
            "flow_signal": self.flow_signal,
            "zero_day": self.zero_day,
            "shadow_delta": self.shadow_delta,
            "delta_flow": self.delta_flow,
            "phase_b_momentum": self.phase_b_momentum,
        }


class PhaseCContractScoreWeights(BaseModel):
    """Pesos del score compuesto por contrato (∑ = 1.0)."""

    model_config = ConfigDict(frozen=True)

    basic_metrics: float = Field(default=0.40, ge=0.0, le=1.0)
    engine_average: float = Field(default=0.60, ge=0.0, le=1.0)

    # Sub-pesos dentro de basic_metrics (∑ = 1.0)
    liquidity: float = Field(default=0.375, ge=0.0, le=1.0)  # 0.15 / 0.40
    delta: float = Field(default=0.250, ge=0.0, le=1.0)  # 0.10 / 0.40
    iv: float = Field(default=0.200, ge=0.0, le=1.0)  # 0.08 / 0.40
    dte: float = Field(default=0.175, ge=0.0, le=1.0)  # 0.07 / 0.40

    @model_validator(mode="after")
    def _weights_sum_to_one(self) -> PhaseCContractScoreWeights:
        total = self.basic_metrics + self.engine_average
        if abs(total - 1.0) > 1e-6:
            raise ValueError(f"Phase C contract score weights must sum to 1.0, got {total}")
        sub = self.liquidity + self.delta + self.iv + self.dte
        if abs(sub - 1.0) > 1e-6:
            raise ValueError(f"Phase C basic metrics sub-weights must sum to 1.0, got {sub}")
        return self


class PhaseCWeights(BaseModel):
    """Pesos Phase C — Derivatives & Options Analysis."""

    model_config = ConfigDict(frozen=True)

    phase_weight: float = 0.45

    engine_weights: PhaseCEngineWeights = Field(default_factory=PhaseCEngineWeights)
    contract_score_weights: PhaseCContractScoreWeights = Field(
        default_factory=PhaseCContractScoreWeights
    )
    contract_filters: PhaseCContractFilters = Field(default_factory=PhaseCContractFilters)

    top_n_tickers: int = Field(default=5, ge=1, le=20)
    top_n_contracts: int = Field(default=5, ge=1, le=20)


class PhaseDWeights(BaseModel):
    """Pesos Phase D — Real-Time Execution Signals."""

    model_config = ConfigDict(frozen=True)

    phase_weight: float = 0.20

    # Pesos de indicadores de tick (∑ = 1.0)
    momentum_weight: float = Field(default=0.35, ge=0.0, le=1.0)
    volatility_weight: float = Field(default=0.25, ge=0.0, le=1.0)
    volume_spike_weight: float = Field(default=0.20, ge=0.0, le=1.0)
    vwap_weight: float = Field(default=0.10, ge=0.0, le=1.0)
    phase_c_confluence_weight: float = Field(default=0.10, ge=0.0, le=1.0)

    # Umbrales de emisión
    entry_momentum_threshold: float = Field(default=0.003, ge=0.0, le=1.0)
    exit_momentum_threshold: float = Field(default=-0.002, ge=-1.0, le=0.0)
    volume_spike_multiplier: float = Field(default=2.5, ge=1.0, le=10.0)
    min_confidence: float = Field(default=0.60, ge=0.0, le=1.0)
    cooldown_seconds: int = Field(default=30, ge=0, le=300)
    min_ticks_for_signal: int = Field(default=10, ge=1, le=100)

    # Riesgo
    stop_loss_pct: float = Field(default=0.02, ge=0.001, le=0.10)
    take_profit_pct: float = Field(default=0.04, ge=0.001, le=0.20)

    # Ventanas de análisis
    momentum_window: int = Field(default=20, ge=2, le=200)
    volatility_window: int = Field(default=30, ge=2, le=200)

    @model_validator(mode="after")
    def _weights_sum_to_one(self) -> PhaseDWeights:
        total = (
            self.momentum_weight
            + self.volatility_weight
            + self.volume_spike_weight
            + self.vwap_weight
            + self.phase_c_confluence_weight
        )
        if abs(total - 1.0) > 1e-6:
            raise ValueError(f"Phase D weights must sum to 1.0, got {total}")
        return self


class StrategyWeights(BaseModel):
    """Modelo maestro de pesos estratégicos del funnel completo.

    Controla cómo cada fase, motor e indicador contribuye al score compuesto
    final. Persistido en memoria y modulable vía API REST + RegimeWeightingEngine.
    """

    model_config = ConfigDict(frozen=True)

    phase_a: PhaseAWeights = Field(default_factory=PhaseAWeights)
    phase_b: PhaseBWeights = Field(default_factory=PhaseBWeights)
    phase_c: PhaseCWeights = Field(default_factory=PhaseCWeights)
    phase_d: PhaseDWeights = Field(default_factory=PhaseDWeights)

    # Modulación por régimen de mercado
    regime_adaptation_enabled: bool = Field(default=True)

    @model_validator(mode="after")
    def _phase_weights_sum_to_one(self) -> StrategyWeights:
        total = (
            self.phase_a.phase_weight
            + self.phase_b.phase_weight
            + self.phase_c.phase_weight
            + self.phase_d.phase_weight
        )
        if abs(total - 1.0) > 1e-6:
            raise ValueError(
                f"Phase weights must sum to 1.0, got {total}. "
                f"A={self.phase_a.phase_weight} "
                f"B={self.phase_b.phase_weight} "
                f"C={self.phase_c.phase_weight} "
                f"D={self.phase_d.phase_weight}"
            )
        return self

    # Pesos por defecto (instancia canónica)
    DEFAULT: ClassVar[StrategyWeights]

    def to_flat_dict(self) -> dict[str, float]:
        """Aplana todos los pesos a un dict<string, float> para serialización."""
        return {
            # Phase A - base
            "phase_a.phase_weight": self.phase_a.phase_weight,
            "phase_a.validation_strictness": self.phase_a.validation_strictness,
            "phase_a.min_price": self.phase_a.min_price,
            "phase_a.min_volume": float(self.phase_a.min_volume),
            "phase_a.max_spread_pct": self.phase_a.max_spread_pct,
            # Phase A - EMA Cluster
            "phase_a.ema_cluster_min_score": self.phase_a.ema_cluster_min_score,
            "phase_a.ema_cluster_min_aligned": float(self.phase_a.ema_cluster_min_aligned),
            # Phase A - ATR Gate
            "phase_a.atr_gate_min_score": self.phase_a.atr_gate_min_score,
            "phase_a.min_atr_pct": self.phase_a.min_atr_pct,
            "phase_a.max_atr_pct": self.phase_a.max_atr_pct,
            # Phase A - RSI Extreme
            "phase_a.rsi_extreme_min_score": self.phase_a.rsi_extreme_min_score,
            "phase_a.rsi_oversold_threshold": self.phase_a.rsi_oversold_threshold,
            "phase_a.rsi_overbought_threshold": self.phase_a.rsi_overbought_threshold,
            # Phase A - VWAP Z-Score
            "phase_a.vwap_zscore_min_score": self.phase_a.vwap_zscore_min_score,
            "phase_a.vwap_max_zscore": self.phase_a.vwap_max_zscore,
            # Phase A - Entropy
            "phase_a.entropy_min_score": self.phase_a.entropy_min_score,
            "phase_a.max_entropy": self.phase_a.max_entropy,
            # Phase A - SuperTrend
            "phase_a.supertrend_min_score": self.phase_a.supertrend_min_score,
            "phase_a.supertrend_period": float(self.phase_a.supertrend_period),
            "phase_a.supertrend_multiplier": self.phase_a.supertrend_multiplier,
            "phase_a.supertrend_max_changes": float(self.phase_a.supertrend_max_changes),
            # Phase B
            "phase_b.phase_weight": self.phase_b.phase_weight,
            "phase_b.ofi_weight": self.phase_b.ofi_weight,
            "phase_b.smc_weight": self.phase_b.smc_weight,
            "phase_b.vpin_weight": self.phase_b.vpin_weight,
            "phase_b.ofi_sensitivity": self.phase_b.ofi_sensitivity,
            "phase_b.smc_lookback_periods": float(self.phase_b.smc_lookback_periods),
            "phase_b.vpin_buckets": float(self.phase_b.vpin_buckets),
            # Phase C - engine weights
            "phase_c.phase_weight": self.phase_c.phase_weight,
            **{f"phase_c.{k}": v for k, v in self.phase_c.engine_weights.to_dict().items()},
            "phase_c.basic_metrics_weight": self.phase_c.contract_score_weights.basic_metrics,
            "phase_c.engine_average_weight": self.phase_c.contract_score_weights.engine_average,
            "phase_c.top_n_tickers": float(self.phase_c.top_n_tickers),
            "phase_c.top_n_contracts": float(self.phase_c.top_n_contracts),
            # Phase C - contract filters
            "phase_c.min_volume": float(self.phase_c.contract_filters.min_volume),
            "phase_c.min_open_interest": float(self.phase_c.contract_filters.min_open_interest),
            "phase_c.max_spread_pct": self.phase_c.contract_filters.max_spread_pct,
            "phase_c.min_dte": float(self.phase_c.contract_filters.min_dte),
            "phase_c.max_dte": float(self.phase_c.contract_filters.max_dte),
            "phase_c.delta_target_call": self.phase_c.contract_filters.delta_target_call,
            "phase_c.min_composite_score": self.phase_c.contract_filters.min_composite_score,
            "phase_c.iv_min": self.phase_c.contract_filters.iv_min,
            "phase_c.iv_max": self.phase_c.contract_filters.iv_max,
            # Phase D
            "phase_d.phase_weight": self.phase_d.phase_weight,
            "phase_d.momentum_weight": self.phase_d.momentum_weight,
            "phase_d.volatility_weight": self.phase_d.volatility_weight,
            "phase_d.volume_spike_weight": self.phase_d.volume_spike_weight,
            "phase_d.vwap_weight": self.phase_d.vwap_weight,
            "phase_d.phase_c_confluence_weight": self.phase_d.phase_c_confluence_weight,
            "phase_d.entry_momentum_threshold": self.phase_d.entry_momentum_threshold,
            "phase_d.volume_spike_multiplier": self.phase_d.volume_spike_multiplier,
            "phase_d.min_confidence": self.phase_d.min_confidence,
            "phase_d.cooldown_seconds": float(self.phase_d.cooldown_seconds),
            "phase_d.stop_loss_pct": self.phase_d.stop_loss_pct,
            "phase_d.take_profit_pct": self.phase_d.take_profit_pct,
            "phase_d.momentum_window": float(self.phase_d.momentum_window),
            "phase_d.volatility_window": float(self.phase_d.volatility_window),
        }


StrategyWeights.DEFAULT = StrategyWeights()
