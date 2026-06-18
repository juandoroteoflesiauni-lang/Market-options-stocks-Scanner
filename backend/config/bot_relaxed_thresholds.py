"""Umbrales relajados para más operaciones en bots Alpaca/BingX/Options. # [PD-8][TH]

Decisiones operativas (auditoría 2026-06-17):
- Modo verificación: maximizar nº de trades intraday para recopilar datos; libro
  plano al cierre (EOD flatten + entry cutoff 15:30 ET).
- BingX: one-way — LONG en sesgo bull, SHORT en sesgo bear (ambas direcciones).
- Azure agentic committee: apagado (AI_AGENTIC_COMMITTEE_MODE=off).
"""

from __future__ import annotations

import os

from backend.config.dual_bot_core_universe import dual_bot_core_env_flags
from backend.config.execution_policy import execution_phase_b_env_flags
from backend.config.profit_calibration import profit_calibration_env_flags

# Alpaca decision engine
# [Fase 5 P0/P1 2026-06-17] Endurecidos para que los gates de verification tengan
# edge mínimo (memo §2/H4): con prob=0.50+0.45·score el floor 0.35 nunca vinculaba.
ALPACA_RELAXED_MIN_VOLUME_Z: float = 0.50  # antes 0.30
ALPACA_RELAXED_MIN_CLOSE_POSITION: float = 0.45  # antes 0.35
ALPACA_RELAXED_PROB_FLOOR: float = 0.45  # antes 0.35
ALPACA_RELAXED_SIZE_DOWN_BAND: float = 0.15
ALPACA_RELAXED_R2_MIN_SCORE: float = 40.0  # antes 32.0
ALPACA_RELAXED_R2_GATE_VETO: float = 0.10  # antes 0.05
ALPACA_RELAXED_R2_CONFLUENCE_MIN: int = 2  # antes 1 (nunca dirección de 1 solo motor)

# BingX decision engine
BINGX_RELAXED_MIN_DECISION_SCORE: float = 0.40  # antes 0.30
BINGX_RELAXED_MIN_PREDICTIVE_CONF: float = 0.40  # antes 0.35

# Predictive gate — reactivado en modo verificación (F9), umbral bajo
ALPACA_PREDICTIVE_GATE_DISABLED: bool = False

# Meta-learner — no promover modelos sintéticos al router live (F6)
META_LEARNER_PROMOTE_SYNTHETIC: bool = False
BINGX_RELAXED_SIZE_DOWN_BAND: float = 0.18

# BingX execution — one-way: LONG bull / SHORT bear; reduceOnly on all closes (F4)
BINGX_OMIT_REDUCE_ONLY: bool = False

# Alpaca EOD — libro limpio al cierre; evita bleed overnight (F3)
ALPACA_EOD_FLATTEN_ENABLED: bool = True
ALPACA_EOD_ENTRY_CUTOFF_DISABLED: bool = False
ALPACA_EOD_ENTRY_CUTOFF_ET: str = "15:30"
ALPACA_EOD_FLATTEN_START_ET: str = "15:45"

# Audit DuckDB — payloads compactos + retención (F5)
AUDIT_COMPACT_PAYLOAD: bool = True
AUDIT_RETAIN_MAX_CYCLES: int = 1500

# Verificación — sizing discovery (más P&L visible en paper/VST)
VERIFICATION_ALPACA_NOTIONAL_USD: float = 2_000.0
VERIFICATION_BINGX_NOTIONAL_USDT: float = 500.0
VERIFICATION_EXECUTION_COOLDOWN_MINUTES: float = 3.0
# [Fase 5 P1 2026-06-17] Caps risk desk BingX específicos de verification (H5):
# deshacen el inflado ~2500x del default compartido. Solo aplican a verification
# (override en apply_verification_session_env, no en profit).
VERIFICATION_RISK_MAX_DAILY_LOSS_USDT: float = 2_000.0  # 2% de ~100k (antes 5000)
VERIFICATION_RISK_MAX_POSITION_NOTIONAL_USDT: float = 8_000.0  # antes 75000
VERIFICATION_RISK_MAX_SYMBOL_EXPOSURE_USDT: float = 1_500.0  # antes 15000
VERIFICATION_RISK_COOLDOWN_AFTER_LOSS_MINUTES: float = 5.0  # antes 2
OPTIONS_CONTRACT_QTY: int = 2
OPTIONS_PREMIUM_SCALE_MULT: float = 1.5
ALPACA_OPTIONS_R1_MAX_PER_CYCLE: int = 6
ALPACA_OPTIONS_R2_MAX_PER_CYCLE: int = 5

# Sizing boost: +3% sobre multiplicadores actuales; alta prob -> 10-15% del buying power
ROUTE_SIZING_BOOST_FACTOR: float = 1.03
ALPACA_ROUTE1_NOTIONAL_MULT_BOOSTED: float = round(1.5 * ROUTE_SIZING_BOOST_FACTOR, 4)
ALPACA_ROUTE2_NOTIONAL_MULT_BOOSTED: float = round(1.0 * ROUTE_SIZING_BOOST_FACTOR, 4)
ALPACA_BUYING_POWER_PCT_BOOSTED: float = round(0.05 * ROUTE_SIZING_BOOST_FACTOR, 4)
ALPACA_HIGH_PROB_THRESHOLD: float = 0.85
ALPACA_HIGH_PROB_BUYING_POWER_PCT_MIN: float = 0.10
ALPACA_HIGH_PROB_BUYING_POWER_PCT_MAX: float = 0.15
ALPACA_OPTIONS_R1_RISK_MULT_BOOSTED: float = round(1.5 * ROUTE_SIZING_BOOST_FACTOR, 4)
ALPACA_OPTIONS_R2_RISK_MULT_BOOSTED: float = round(0.85 * ROUTE_SIZING_BOOST_FACTOR, 4)
ALPACA_OPTIONS_R2_MIN_GLOBAL_CONFIDENCE: float = 0.28
ALPACA_OPTIONS_R2_MIN_PROBE_SCORE: float = 0.45

# Options Strategy vetos
OPTIONS_RELAXED_MIN_GLOBAL_CONFIDENCE: float = 0.52
OPTIONS_RELAXED_MIN_CHAIN_LIQUIDITY: float = 0.15
OPTIONS_RELAXED_TAIL_RISK_THRESHOLD: float = 0.92
OPTIONS_RELAXED_FLOW_TOXIC_CONVICTION: float = 0.10
OPTIONS_RELAXED_FLOW_TOXIC_DISPERSION: float = 0.82
OPTIONS_RELAXED_GAMMA_PRESSURE: float = 0.88

# Options Strategy universo R1 (11 tickers)
OPTIONS_ROUTE1_DTE_MIN: int = 3
OPTIONS_ROUTE1_DTE_MAX: int = 45
OPTIONS_ROUTE1_MIN_OPEN_INTEREST: int = 100
OPTIONS_ROUTE1_MIN_DAILY_VOLUME: int = 25
OPTIONS_CHAIN_HYDRATION_MIN_LEGS: int = 4

# Risk-adjusted confidence sizing (Kelly + regime + heat)
OPTIONS_KELLY_FRACTION: float = 0.5
OPTIONS_KELLY_MAX_FRACTION: float = 0.25
OPTIONS_CONFIDENCE_SIZE_FLOOR: float = 0.35
OPTIONS_DISPERSION_PENALTY: float = 0.35
OPTIONS_MAX_RISK_BUDGET_PCT: float = 1.875
OPTIONS_MAX_PORTFOLIO_HEAT_PCT: float = 12.0
OPTIONS_MAX_SECTOR_HEAT_PCT: float = 5.0
OPTIONS_CORRELATION_PENALTY_PER_SYMBOL: float = 0.15
OPTIONS_CORRELATION_SIZE_FLOOR: float = 0.55
SIZING_VIX_ELEVATED: float = 25.0
SIZING_VIX_HIGH: float = 30.0
SIZING_REGIME_SCALAR_HIGH: float = 0.4
SIZING_REGIME_SCALAR_ELEVATED: float = 0.65
SIZING_REGIME_SCALAR_NORMAL: float = 1.0
SIZING_ATR_TO_VIX_SCALE: float = 5.0
ALPACA_CONFIDENCE_SIZE_FLOOR: float = 0.4
BINGX_TRADE_SIZE_PCT: float = 0.01
BINGX_VIX_PROXY: float = 20.0

# Bot loop — dual-interval (Wall Street style: fast monitor + slow scan)
DEFAULT_BOT_CYCLE_INTERVAL_S: int = 240
BOT_DUAL_LOOP_ENABLED: bool = True
BOT_FAST_CYCLE_INTERVAL_S: int = 75
BOT_SLOW_CYCLE_INTERVAL_S: int = 240

# Cuentas paper/demo objetivo
ALPACA_PAPER_EQUITY_USD: float = 100_000.0
BINGX_DEMO_EQUITY_USDT: float = 99_998.8102
ALPACA_NOTIONAL_PER_TRADE_USD: float = 2_500.0
BINGX_NOTIONAL_PER_TRADE_USDT: float = 2_000.0


def apply_paper_demo_account_env(*, execute_orders: bool = True) -> None:
    """Configura Alpaca paper 100k + BingX VST demo ~100k USDT y risk desk acorde."""
    apply_relaxed_bot_env(execute_orders=execute_orders)
    forced: dict[str, str] = {
        "ALPACA_TRADING_MODE": "paper",
        "ALPACA_NOTIONAL_PER_TRADE_USD": str(ALPACA_NOTIONAL_PER_TRADE_USD),
        "ALPACA_BUYING_POWER_PCT": "0.025",
        "ALPACA_MAX_OPEN_POSITIONS": "10",
        "BINGX_BOT_TRADING_ENV": "prod-vst",
        "BINGX_EQUITY_USDT": str(BINGX_DEMO_EQUITY_USDT),
        "BINGX_NOTIONAL_PER_TRADE_USDT": str(BINGX_NOTIONAL_PER_TRADE_USDT),
        "RISK_MAX_POSITION_NOTIONAL_USDT": "75000",
        "RISK_MAX_SYMBOL_EXPOSURE_USDT": "15000",
        "RISK_MAX_OPEN_POSITIONS": "10",
        "RISK_MAX_DAILY_LOSS_USDT": "5000",
        "RISK_MIN_L2_QUALITY_SCORE": "0",
        "BINGX_RISK_REQUIRES_L2": "false",
        "BINGX_ZONE_VETO_ENABLED": "false",
        "BINGX_NEUTRAL_ZONE_BLOCK": "false",
        "BINGX_EXEC_QUALITY_ENABLED": "false",
    }
    if execute_orders:
        forced["ALPACA_DRY_RUN"] = "false"
        forced["BINGX_DRY_RUN"] = "false"
        forced["BINGX_BOT_ENABLE_LIVE"] = "true"
    _force_env(forced)


def _force_env(updates: dict[str, str]) -> None:
    """Sobrescribe variables de entorno (no setdefault)."""
    for key, value in updates.items():
        os.environ[key] = value


def _alpaca_eod_env_flags() -> dict[str, str]:
    """Flags EOD Alpaca: cutoff entradas + flatten libro (F3)."""
    return {
        "ALPACA_EOD_FLATTEN_ENABLED": str(ALPACA_EOD_FLATTEN_ENABLED).lower(),
        "ALPACA_EOD_ENTRY_CUTOFF_DISABLED": str(ALPACA_EOD_ENTRY_CUTOFF_DISABLED).lower(),
        "ALPACA_EOD_ENTRY_CUTOFF_ET": ALPACA_EOD_ENTRY_CUTOFF_ET,
        "ALPACA_EOD_FLATTEN_START_ET": ALPACA_EOD_FLATTEN_START_ET,
    }


def _audit_duckdb_env_flags() -> dict[str, str]:
    """Compactación y retención de audits DuckDB (F5)."""
    return {
        "AUDIT_COMPACT_PAYLOAD": str(AUDIT_COMPACT_PAYLOAD).lower(),
        "AUDIT_RETAIN_MAX_CYCLES": str(AUDIT_RETAIN_MAX_CYCLES),
    }


def _meta_learner_env_flags() -> dict[str, str]:
    """Promoción EOD del meta-learner (F6)."""
    return {
        "META_LEARNER_PROMOTE_SYNTHETIC": str(META_LEARNER_PROMOTE_SYNTHETIC).lower(),
    }


def _predictive_gate_env_flags() -> dict[str, str]:
    """Predictive gate relajado en verificación (F9)."""
    return {
        "ALPACA_PREDICTIVE_GATE_DISABLED": str(ALPACA_PREDICTIVE_GATE_DISABLED).lower(),
        "BINGX_MIN_PREDICTIVE_CONFIDENCE": str(BINGX_RELAXED_MIN_PREDICTIVE_CONF),
    }


def apply_profit_session_env(*, execute_orders: bool = True) -> None:
    """Modo profit: umbrales estrictos, PF rolling gate, Kelly fraccional."""
    apply_paper_demo_account_env(execute_orders=execute_orders)
    _force_env(
        {
            "EOD_CALIBRATION_ENABLED": "true",
            "EOD_META_LEARNER_ENABLED": "true",
            "AI_AGENTIC_COMMITTEE_MODE": "off",
            "AI_AGENTIC_QUANT_FALLBACK": "true",
            "BINGX_OMIT_REDUCE_ONLY": "false",
            "ALPACA_EOD_FLATTEN_ENABLED": "true",
            "BOT_DUAL_LOOP_ENABLED": "true",
            "BOT_FAST_CYCLE_INTERVAL_S": str(BOT_FAST_CYCLE_INTERVAL_S),
            "BOT_SLOW_CYCLE_INTERVAL_S": str(BOT_SLOW_CYCLE_INTERVAL_S),
            "BOT_CYCLE_INTERVAL_S": str(BOT_SLOW_CYCLE_INTERVAL_S),
            **_alpaca_eod_env_flags(),
            **_audit_duckdb_env_flags(),
            **_meta_learner_env_flags(),
            **_predictive_gate_env_flags(),
            **profit_calibration_env_flags(),
        }
    )


def apply_session_mode_env(
    mode: str,
    *,
    execute_orders: bool = True,
) -> None:
    """Aplica verificación (datos) o profit (selectividad + PF gate)."""
    if mode.strip().lower() == "profit":
        apply_profit_session_env(execute_orders=execute_orders)
    else:
        apply_verification_session_env(execute_orders=execute_orders)


def apply_verification_session_env(*, execute_orders: bool = True) -> None:
    """Modo verificación: umbrales bajos, notional pequeño, auditoría completa."""
    apply_paper_demo_account_env(execute_orders=execute_orders)
    _force_env(
        {
            "ALPACA_MIN_VOLUME_Z": str(ALPACA_RELAXED_MIN_VOLUME_Z),
            "ALPACA_MIN_CLOSE_POSITION": str(ALPACA_RELAXED_MIN_CLOSE_POSITION),
            "ALPACA_PROB_FLOOR": str(ALPACA_RELAXED_PROB_FLOOR),
            "ALPACA_SIZE_DOWN_BAND": str(ALPACA_RELAXED_SIZE_DOWN_BAND),
            "ALPACA_R2_MIN_SCORE": str(ALPACA_RELAXED_R2_MIN_SCORE),
            "ALPACA_R2_GATE_VETO_THRESHOLD": str(ALPACA_RELAXED_R2_GATE_VETO),
            "ALPACA_R2_CONFLUENCE_MIN_ENGINES": str(ALPACA_RELAXED_R2_CONFLUENCE_MIN),
            "ALPACA_R2_HMM_BULLISH_ONLY": "false",
            "ALPACA_R2_VSA_VOLUME_GATE": "false",
            "ALPACA_R2_ACCEPT_S1": "true",
            "ALPACA_VERIFICATION_RELAXED_BULLISH": "false",
            "ALPACA_PREDICTIVE_GATE_DISABLED": str(ALPACA_PREDICTIVE_GATE_DISABLED).lower(),
            "EQUITY_OPTIONS_GATE_RELAXED": "true",
            "EQUITY_L2_GATE_ENABLED": "false",
            "EQUITY_L2_GATE_BLOCK_MISSING": "false",
            "BINGX_MIN_DECISION_SCORE": str(BINGX_RELAXED_MIN_DECISION_SCORE),
            "BINGX_MIN_PREDICTIVE_CONFIDENCE": str(BINGX_RELAXED_MIN_PREDICTIVE_CONF),
            "BINGX_SKIP_OPTIONS_SNAPSHOT": "false",
            "SHARED_OPTIONS_TIER_ENABLED": "true",
            "ALPACA_OPTIONS_R2_ENABLED": "false",
            "ALPACA_OPTIONS_R2_STANDALONE": "false",
            "EOD_CALIBRATION_ENABLED": "true",
            "EOD_META_LEARNER_ENABLED": "true",
            **execution_phase_b_env_flags(),
            "AI_AGENTIC_COMMITTEE_MODE": "off",
            "AI_AGENTIC_QUANT_FALLBACK": "true",
            "MACRO_FMP_FALLBACK_ENABLED": "true",
            "BOT_EXECUTION_COOLDOWN_MINUTES": str(VERIFICATION_EXECUTION_COOLDOWN_MINUTES),
            "BOT_DUAL_LOOP_ENABLED": "true",
            "BOT_FAST_CYCLE_INTERVAL_S": str(BOT_FAST_CYCLE_INTERVAL_S),
            "BOT_SLOW_CYCLE_INTERVAL_S": str(BOT_SLOW_CYCLE_INTERVAL_S),
            "BOT_CYCLE_INTERVAL_S": str(BOT_SLOW_CYCLE_INTERVAL_S),
            "RISK_COOLDOWN_AFTER_LOSS_MINUTES": str(VERIFICATION_RISK_COOLDOWN_AFTER_LOSS_MINUTES),
            "RISK_MAX_DAILY_LOSS_USDT": str(VERIFICATION_RISK_MAX_DAILY_LOSS_USDT),
            "RISK_MAX_POSITION_NOTIONAL_USDT": str(VERIFICATION_RISK_MAX_POSITION_NOTIONAL_USDT),
            "RISK_MAX_SYMBOL_EXPOSURE_USDT": str(VERIFICATION_RISK_MAX_SYMBOL_EXPOSURE_USDT),
            "ALPACA_NOTIONAL_PER_TRADE_USD": str(VERIFICATION_ALPACA_NOTIONAL_USD),
            "BINGX_NOTIONAL_PER_TRADE_USDT": str(VERIFICATION_BINGX_NOTIONAL_USDT),
            "BINGX_USE_STATIC_NOTIONAL": "true",
            "BINGX_OMIT_REDUCE_ONLY": "false",
            "TECHNICAL_CPU_TIMEOUT_SEC": "15",
            "OPTIONS_CONTRACT_QTY": str(OPTIONS_CONTRACT_QTY),
            "OPTIONS_PREMIUM_SCALE_MULT": str(OPTIONS_PREMIUM_SCALE_MULT),
            "ALPACA_OPTIONS_R1_MAX_PER_CYCLE": str(ALPACA_OPTIONS_R1_MAX_PER_CYCLE),
            "ALPACA_OPTIONS_R2_MAX_PER_CYCLE": str(ALPACA_OPTIONS_R2_MAX_PER_CYCLE),
            "ALPACA_OPTIONS_R2_MIN_GLOBAL_CONFIDENCE": str(ALPACA_OPTIONS_R2_MIN_GLOBAL_CONFIDENCE),
            "ALPACA_OPTIONS_R2_MIN_PROBE_SCORE": str(ALPACA_OPTIONS_R2_MIN_PROBE_SCORE),
            "ALPACA_ROUTE1_NOTIONAL_MULT": str(ALPACA_ROUTE1_NOTIONAL_MULT_BOOSTED),
            "ALPACA_ROUTE2_NOTIONAL_MULT": str(ALPACA_ROUTE2_NOTIONAL_MULT_BOOSTED),
            "ALPACA_HIGH_PROB_THRESHOLD": str(ALPACA_HIGH_PROB_THRESHOLD),
            "ALPACA_HIGH_PROB_BUYING_POWER_PCT_MIN": str(ALPACA_HIGH_PROB_BUYING_POWER_PCT_MIN),
            "ALPACA_HIGH_PROB_BUYING_POWER_PCT_MAX": str(ALPACA_HIGH_PROB_BUYING_POWER_PCT_MAX),
            "OPTIONS_STRATEGY_RELAXED_VETOS": "true",
            "OPTIONS_STRATEGY_MIN_GLOBAL_CONFIDENCE": "0.18",
            "OPTIONS_RELAXED_MIN_CHAIN_LIQUIDITY": str(OPTIONS_RELAXED_MIN_CHAIN_LIQUIDITY),
            "OPTIONS_UNIVERSE_DTE_MIN": str(OPTIONS_ROUTE1_DTE_MIN),
            "OPTIONS_UNIVERSE_DTE_MAX": str(OPTIONS_ROUTE1_DTE_MAX),
            "OPTIONS_MIN_OPEN_INTEREST": str(OPTIONS_ROUTE1_MIN_OPEN_INTEREST),
            "OPTIONS_MIN_DAILY_VOLUME": str(OPTIONS_ROUTE1_MIN_DAILY_VOLUME),
            "OPTIONS_CHAIN_HYDRATION_MIN_LEGS": str(OPTIONS_CHAIN_HYDRATION_MIN_LEGS),
            "OPTIONS_ROUTE1_LENIENT": "true",
            "ALPACA_OPTIONS_ENABLED": "true",
            "ALPACA_OPTIONS_PRIORITY_EQUITY": "true",
            "ALPACA_OPTIONS_R1_RISK_MULT": str(ALPACA_OPTIONS_R1_RISK_MULT_BOOSTED),
            "ALPACA_OPTIONS_R2_RISK_MULT": str(ALPACA_OPTIONS_R2_RISK_MULT_BOOSTED),
            "ALPACA_BUYING_POWER_PCT": str(ALPACA_BUYING_POWER_PCT_BOOSTED),
            "OPTIONS_DEFINED_RISK_ONLY": "true",
            "OPTIONS_PREFER_SPREAD_OVER_SINGLE_LEG": "true",
            "OPTIONS_KELLY_FRACTION": str(OPTIONS_KELLY_FRACTION),
            "OPTIONS_KELLY_MAX_FRACTION": str(OPTIONS_KELLY_MAX_FRACTION),
            "OPTIONS_CONFIDENCE_SIZE_FLOOR": str(OPTIONS_CONFIDENCE_SIZE_FLOOR),
            "OPTIONS_DISPERSION_PENALTY": str(OPTIONS_DISPERSION_PENALTY),
            "OPTIONS_MAX_RISK_BUDGET_PCT": str(OPTIONS_MAX_RISK_BUDGET_PCT),
            "OPTIONS_MAX_PORTFOLIO_HEAT_PCT": str(OPTIONS_MAX_PORTFOLIO_HEAT_PCT),
            "OPTIONS_MAX_SECTOR_HEAT_PCT": str(OPTIONS_MAX_SECTOR_HEAT_PCT),
            "OPTIONS_CORRELATION_PENALTY_PER_SYMBOL": str(OPTIONS_CORRELATION_PENALTY_PER_SYMBOL),
            "OPTIONS_CORRELATION_SIZE_FLOOR": str(OPTIONS_CORRELATION_SIZE_FLOOR),
            "SIZING_VIX_ELEVATED": str(SIZING_VIX_ELEVATED),
            "SIZING_VIX_HIGH": str(SIZING_VIX_HIGH),
            "SIZING_REGIME_SCALAR_HIGH": str(SIZING_REGIME_SCALAR_HIGH),
            "SIZING_REGIME_SCALAR_ELEVATED": str(SIZING_REGIME_SCALAR_ELEVATED),
            "SIZING_REGIME_SCALAR_NORMAL": str(SIZING_REGIME_SCALAR_NORMAL),
            "SIZING_ATR_TO_VIX_SCALE": str(SIZING_ATR_TO_VIX_SCALE),
            "ALPACA_CONFIDENCE_SIZE_FLOOR": str(ALPACA_CONFIDENCE_SIZE_FLOOR),
            "BINGX_TRADE_SIZE_PCT": str(BINGX_TRADE_SIZE_PCT),
            "BINGX_VIX_PROXY": str(BINGX_VIX_PROXY),
            **_alpaca_eod_env_flags(),
            **_audit_duckdb_env_flags(),
            **_meta_learner_env_flags(),
            **_predictive_gate_env_flags(),
            "BOT_SESSION_MODE": "verification",
            "PROFIT_ROLLING_PF_GATE_ENABLED": "true",
            "VERIFICATION_ROLLING_PF_MIN": "0.85",
            "PROFIT_KELLY_SIZING_ENABLED": "false",
            **dual_bot_core_env_flags(),
        }
    )


def apply_relaxed_bot_env(*, execute_orders: bool = True) -> None:
    """Inyecta variables de entorno relajadas (idempotente por proceso)."""
    relaxed: dict[str, str] = {
        "ALPACA_MIN_VOLUME_Z": str(ALPACA_RELAXED_MIN_VOLUME_Z),
        "ALPACA_MIN_CLOSE_POSITION": str(ALPACA_RELAXED_MIN_CLOSE_POSITION),
        "ALPACA_PROB_FLOOR": str(ALPACA_RELAXED_PROB_FLOOR),
        "ALPACA_SIZE_DOWN_BAND": str(ALPACA_RELAXED_SIZE_DOWN_BAND),
        "ALPACA_R2_MIN_SCORE": str(ALPACA_RELAXED_R2_MIN_SCORE),
        "ALPACA_R2_GATE_VETO_THRESHOLD": str(ALPACA_RELAXED_R2_GATE_VETO),
        "ALPACA_R2_CONFLUENCE_MIN_ENGINES": str(ALPACA_RELAXED_R2_CONFLUENCE_MIN),
        "EQUITY_L2_GATE_ENABLED": "false",
        "EQUITY_L2_GATE_BLOCK_MISSING": "false",
        "BINGX_MIN_DECISION_SCORE": str(BINGX_RELAXED_MIN_DECISION_SCORE),
        "BINGX_MIN_PREDICTIVE_CONFIDENCE": str(BINGX_RELAXED_MIN_PREDICTIVE_CONF),
        "BINGX_REQUIRE_L2_FOR_EQUITY_LIVE": "false",
        "RISK_NO_TRADE_PROVIDER_DEGRADED": "false",
        "RISK_MIN_L2_QUALITY_SCORE": "0.05",
        "RISK_MAX_OPEN_POSITIONS": "8",
        "BINGX_ZONE_VETO_ENABLED": "false",
        "BINGX_NEUTRAL_ZONE_BLOCK": "false",
        "OPTIONS_STRATEGY_RELAXED_VETOS": "true",
        "OPTIONS_STRATEGY_MIN_GLOBAL_CONFIDENCE": "0.18",
        "OPTIONS_RELAXED_MIN_CHAIN_LIQUIDITY": str(OPTIONS_RELAXED_MIN_CHAIN_LIQUIDITY),
        "OPTIONS_UNIVERSE_DTE_MIN": str(OPTIONS_ROUTE1_DTE_MIN),
        "OPTIONS_UNIVERSE_DTE_MAX": str(OPTIONS_ROUTE1_DTE_MAX),
        "OPTIONS_MIN_OPEN_INTEREST": str(OPTIONS_ROUTE1_MIN_OPEN_INTEREST),
        "OPTIONS_MIN_DAILY_VOLUME": str(OPTIONS_ROUTE1_MIN_DAILY_VOLUME),
        "OPTIONS_CHAIN_HYDRATION_MIN_LEGS": str(OPTIONS_CHAIN_HYDRATION_MIN_LEGS),
        "OPTIONS_ROUTE1_LENIENT": "true",
        "ALPACA_OPTIONS_R1_MAX_PER_CYCLE": str(ALPACA_OPTIONS_R1_MAX_PER_CYCLE),
        "ALPACA_OPTIONS_R2_MAX_PER_CYCLE": str(ALPACA_OPTIONS_R2_MAX_PER_CYCLE),
        "ALPACA_OPTIONS_R2_ENABLED": "false",
        "ALPACA_OPTIONS_R2_STANDALONE": "false",
        "ALPACA_OPTIONS_R2_MIN_PROBE_SCORE": str(ALPACA_OPTIONS_R2_MIN_PROBE_SCORE),
        "ALPACA_OPTIONS_R2_MIN_GLOBAL_CONFIDENCE": str(ALPACA_OPTIONS_R2_MIN_GLOBAL_CONFIDENCE),
        "BINGX_OMIT_REDUCE_ONLY": "false",
        "ALPACA_OPTIONS_ENABLED": "true",
        "ALPACA_OPTIONS_PRIORITY_EQUITY": "true",
        "OPTIONS_DEFINED_RISK_ONLY": "true",
        "OPTIONS_PREFER_SPREAD_OVER_SINGLE_LEG": "true",
        "MACRO_FMP_FALLBACK_ENABLED": "true",
        **_alpaca_eod_env_flags(),
        **_audit_duckdb_env_flags(),
        **_meta_learner_env_flags(),
        **_predictive_gate_env_flags(),
    }
    if execute_orders:
        relaxed["ALPACA_TRADING_MODE"] = os.getenv("ALPACA_TRADING_MODE", "paper")
        relaxed["ALPACA_DRY_RUN"] = "false"
        relaxed["BINGX_DRY_RUN"] = "false"
        relaxed["BINGX_BOT_ENABLE_LIVE"] = "true"
        if not os.getenv("BINGX_BOT_TRADING_ENV"):
            relaxed["BINGX_BOT_TRADING_ENV"] = "prod-vst"
    _force_env(relaxed)


__all__ = [
    "ALPACA_NOTIONAL_PER_TRADE_USD",
    "ALPACA_PAPER_EQUITY_USD",
    "BINGX_DEMO_EQUITY_USDT",
    "BINGX_NOTIONAL_PER_TRADE_USDT",
    "DEFAULT_BOT_CYCLE_INTERVAL_S",
    "VERIFICATION_ALPACA_NOTIONAL_USD",
    "VERIFICATION_BINGX_NOTIONAL_USDT",
    "apply_paper_demo_account_env",
    "apply_profit_session_env",
    "apply_relaxed_bot_env",
    "apply_session_mode_env",
    "apply_verification_session_env",
]
