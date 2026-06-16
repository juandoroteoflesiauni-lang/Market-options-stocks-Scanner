"""Loader de configuración del módulo Options Strategy (Fase 1). # [PD-8][TH][IM]"""

from __future__ import annotations

import os
from functools import lru_cache
from pathlib import Path
from typing import Any, Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from backend.config.alpaca_priority_route import (
    ALPACA_ROUTE1_WATCHLIST,
    is_route1_symbol,
)
from backend.config.logger_setup import get_logger
from backend.models.options_strategy import StructureProfile

logger = get_logger(__name__)

_CONFIG_DIR = Path(__file__).parent
_DEFAULT_OMNI = _CONFIG_DIR / "omni_engine.yaml"
_DEFAULT_UNIVERSE = _CONFIG_DIR / "options_universe.yaml"
_DEFAULT_PLAYBOOKS = _CONFIG_DIR / "playbooks.yaml"
_DEFAULT_RISK = _CONFIG_DIR / "risk_rules.yaml"
_DEFAULT_CALIBRATED = _CONFIG_DIR / "omni_engine_calibrated.yaml"

UniverseSource = Literal["alpaca_route1"]
FusionMode = Literal["weighted_hierarchical", "equal_weight"]
MvpStructure = Literal[
    "long_call",
    "long_put",
    "call_debit_spread",
    "put_debit_spread",
    "short_put",
    "put_credit_spread",
    "call_credit_spread",
    "bull_call_spread",
    "call_butterfly",
]


class OmniEngineConfig(BaseModel):
    """Parámetros globales de fusión y capas habilitadas."""

    model_config = ConfigDict(frozen=True)

    enabled_layers: tuple[str, ...] = ("technical", "predictive", "options")
    fusion_mode: FusionMode = "weighted_hierarchical"
    min_global_confidence: float = Field(default=0.68, ge=0.0, le=1.0)
    weights: dict[str, float] = Field(default_factory=dict)
    disagreement_penalty: float = Field(default=0.15, ge=0.0, le=1.0)
    veto_rules: tuple[str, ...] = ()

    @model_validator(mode="after")
    def _weights_sum_sane(self) -> OmniEngineConfig:
        if not self.weights:
            return self
        total = sum(self.weights.values())
        if total <= 0:
            raise ValueError("omni_engine weights must sum to a positive value")
        return self


class OptionsUniverseConfig(BaseModel):
    """Universo operativo — resuelto contra Ruta 1 por defecto."""

    model_config = ConfigDict(frozen=True)

    source: UniverseSource = "alpaca_route1"
    enforce_route1_only: bool = True
    dte_min: int = Field(default=7, ge=1)
    dte_max: int = Field(default=21, ge=1)
    min_open_interest: int = Field(default=500, ge=0)
    min_daily_volume: int = Field(default=100, ge=0)
    allowed_order_types: tuple[str, ...] = ("limit",)
    trade_sessions: tuple[str, ...] = ("regular",)

    @model_validator(mode="after")
    def _dte_range_valid(self) -> OptionsUniverseConfig:
        if self.dte_max < self.dte_min:
            raise ValueError("dte_max must be >= dte_min")
        return self


class PlaybookConfig(BaseModel):
    """Reglas de un playbook individual."""

    model_config = ConfigDict(frozen=True, extra="allow")

    enabled: bool = True
    allowed_structures: tuple[MvpStructure, ...] = ()
    min_trend_quality: float | None = Field(default=None, ge=0.0, le=1.0)
    min_predictive_bias: float | None = Field(default=None, ge=-1.0, le=1.0)
    min_options_bias: float | None = Field(default=None, ge=-1.0, le=1.0)
    require_gamma_level: bool = False
    max_dte: int | None = Field(default=None, ge=1)
    require_breakout_state: str | None = None


class PlaybooksConfig(BaseModel):
    """Catálogo de playbooks del módulo."""

    model_config = ConfigDict(frozen=True)

    playbooks: dict[str, PlaybookConfig] = Field(default_factory=dict)

    def enabled_playbooks(self) -> dict[str, PlaybookConfig]:
        return {name: cfg for name, cfg in self.playbooks.items() if cfg.enabled}


class RiskRulesConfig(BaseModel):
    """Guardrails de riesgo del módulo."""

    model_config = ConfigDict(frozen=True)

    max_risk_per_trade_pct: float = Field(default=0.75, ge=0.0)
    max_daily_loss_pct: float = Field(default=2.0, ge=0.0)
    max_open_positions: int = Field(default=4, ge=1)
    max_same_direction_exposure_pct: float = Field(default=2.5, ge=0.0)
    min_chain_liquidity_score: float = Field(default=0.60, ge=0.0, le=1.0)
    max_bid_ask_spread_pct: float = Field(default=8.0, ge=0.0)
    cooldown_after_loss_minutes: int = Field(default=45, ge=0)
    max_premium_loss_pct: float = Field(default=50.0, ge=0.0)
    min_dte_time_stop: int = Field(default=3, ge=0)
    thesis_bias_flip_threshold: float = Field(default=0.25, ge=0.0)


class OptionsStrategyConfigBundle(BaseModel):
    """Configuración completa cargada desde YAML."""

    model_config = ConfigDict(frozen=True)

    omni_engine: OmniEngineConfig
    universe: OptionsUniverseConfig
    playbooks: PlaybooksConfig
    risk: RiskRulesConfig
    resolved_symbols: tuple[str, ...] = ()
    structure_profile: StructureProfile = "full"

    @field_validator("resolved_symbols")
    @classmethod
    def _symbols_uppercase(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        return tuple(sym.upper().strip() for sym in value)


def resolve_universe_symbols(universe: OptionsUniverseConfig) -> tuple[str, ...]:
    """Resuelve la lista canónica de subyacentes según ``universe.source``."""
    if universe.source == "alpaca_route1":
        return ALPACA_ROUTE1_WATCHLIST
    raise ValueError(f"unsupported universe source: {universe.source}")


def assert_symbol_in_universe(
    symbol: str,
    *,
    universe: OptionsUniverseConfig,
    resolved_symbols: tuple[str, ...],
) -> str:
    """Valida que el símbolo pertenezca al universo R1 cuando está enforced."""
    sym = symbol.upper().strip()
    if universe.enforce_route1_only and not is_route1_symbol(sym):
        raise ValueError(f"symbol_not_in_route1_universe: {sym}")
    if sym not in resolved_symbols:
        raise ValueError(f"symbol_not_in_resolved_universe: {sym}")
    return sym


def _load_yaml(path: Path) -> dict[str, Any]:
    if not path.exists():
        raise FileNotFoundError(f"options strategy config not found: {path}")
    with open(path, encoding="utf-8") as handle:
        return yaml.safe_load(handle) or {}


def _parse_playbooks(raw: dict[str, Any]) -> PlaybooksConfig:
    entries = raw.get("playbooks") or {}
    parsed: dict[str, PlaybookConfig] = {}
    for name, payload in entries.items():
        if not isinstance(payload, dict):
            continue
        structures = tuple(payload.get("allowed_structures") or ())
        parsed[name] = PlaybookConfig.model_validate(
            {**payload, "allowed_structures": structures}
        )
    return PlaybooksConfig(playbooks=parsed)


def load_options_strategy_config(
    *,
    omni_path: Path | str | None = None,
    universe_path: Path | str | None = None,
    playbooks_path: Path | str | None = None,
    risk_path: Path | str | None = None,
) -> OptionsStrategyConfigBundle:
    """Carga y valida la configuración del módulo Options Strategy."""
    omni_raw = _load_yaml(Path(omni_path or _DEFAULT_OMNI))
    universe_raw = _load_yaml(Path(universe_path or _DEFAULT_UNIVERSE))
    playbooks_raw = _load_yaml(Path(playbooks_path or _DEFAULT_PLAYBOOKS))
    risk_raw = _load_yaml(Path(risk_path or _DEFAULT_RISK))

    omni = OmniEngineConfig.model_validate(omni_raw.get("omni_engine") or omni_raw)
    universe = OptionsUniverseConfig.model_validate(universe_raw.get("universe") or universe_raw)
    playbooks = _parse_playbooks(playbooks_raw)
    risk = RiskRulesConfig.model_validate(risk_raw.get("risk") or risk_raw)
    resolved = resolve_universe_symbols(universe)

    return OptionsStrategyConfigBundle(
        omni_engine=omni,
        universe=universe,
        playbooks=playbooks,
        risk=risk,
        resolved_symbols=resolved,
    )


def _truthy_env(name: str) -> bool:
    return os.getenv(name, "").strip().lower() in {"1", "true", "yes", "on"}


def _maybe_apply_calibrated(
    base: OptionsStrategyConfigBundle,
) -> OptionsStrategyConfigBundle:
    """Fusiona pesos/umbrales calibrados si ``OPTIONS_STRATEGY_USE_CALIBRATED`` está activo."""
    if not _truthy_env("OPTIONS_STRATEGY_USE_CALIBRATED"):
        return base
    calibrated_path = Path(
        os.getenv("OPTIONS_STRATEGY_CALIBRATED_PATH") or _DEFAULT_CALIBRATED
    )
    if not calibrated_path.exists():
        logger.warning(
            "options_strategy.calibrated_config_missing path=%s falling_back_to_base",
            calibrated_path,
        )
        return base
    # Import perezoso: evita ciclo loader <-> calibration_loop.
    from backend.services.options_strategy.calibration_loop import (
        load_calibrated_config_bundle,
    )

    merged = load_calibrated_config_bundle(calibrated_path, base_config=base)
    logger.info("options_strategy.calibrated_config_applied path=%s", calibrated_path)
    return merged


@lru_cache(maxsize=1)
def get_options_strategy_config() -> OptionsStrategyConfigBundle:
    """Configuración cacheada; override vía ``OPTIONS_STRATEGY_CONFIG_DIR``.

    Si ``OPTIONS_STRATEGY_USE_CALIBRATED`` está activo, fusiona los pesos y
    umbrales del YAML calibrado (Fase 7) sobre la configuración base.
    """
    override_dir = os.getenv("OPTIONS_STRATEGY_CONFIG_DIR")
    if not override_dir:
        base = load_options_strategy_config()
    else:
        root = Path(override_dir)
        base = load_options_strategy_config(
            omni_path=root / "omni_engine.yaml",
            universe_path=root / "options_universe.yaml",
            playbooks_path=root / "playbooks.yaml",
            risk_path=root / "risk_rules.yaml",
        )
    return _maybe_apply_calibrated(base)


__all__ = [
    "OmniEngineConfig",
    "OptionsStrategyConfigBundle",
    "OptionsUniverseConfig",
    "PlaybookConfig",
    "PlaybooksConfig",
    "RiskRulesConfig",
    "assert_symbol_in_universe",
    "get_options_strategy_config",
    "load_options_strategy_config",
    "resolve_universe_symbols",
]
