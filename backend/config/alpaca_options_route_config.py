"""Perfiles de Options Strategy por ruta Alpaca (R1 sofisticada / R2 básica). # [PD-8][TH][IM]"""

from __future__ import annotations

import os
from typing import Literal

from backend.config.options_strategy_loader import (
    OmniEngineConfig,
    OptionsStrategyConfigBundle,
    OptionsUniverseConfig,
    PlaybookConfig,
    PlaybooksConfig,
    RiskRulesConfig,
    get_options_strategy_config,
)
from backend.models.options_strategy import OptionsStructure

AlpacaOptionsRoute = Literal["priority", "scan"]

_R1_RISK_MULT_ENV = "ALPACA_OPTIONS_R1_RISK_MULT"
_R2_RISK_MULT_ENV = "ALPACA_OPTIONS_R2_RISK_MULT"
_R2_MIN_CONF_ENV = "ALPACA_OPTIONS_R2_MIN_GLOBAL_CONFIDENCE"
_OPTIONS_MIN_CONF_ENV = "OPTIONS_STRATEGY_MIN_GLOBAL_CONFIDENCE"

_R2_BASIC_STRUCTURES: tuple[str, ...] = (
    OptionsStructure.LONG_CALL,
    OptionsStructure.LONG_PUT,
    OptionsStructure.SHORT_PUT,
    OptionsStructure.PUT_CREDIT_SPREAD,
)


def alpaca_options_enabled() -> bool:
    """True si el bot Alpaca debe ejecutar opciones en cada ciclo."""
    return os.getenv("ALPACA_OPTIONS_ENABLED", "true").strip().lower() in {
        "1",
        "true",
        "yes",
        "on",
    }


def alpaca_options_priority_over_equity() -> bool:
    """Si hay ejecución de opciones exitosa, omitir equity en el mismo símbolo/ciclo."""
    return os.getenv("ALPACA_OPTIONS_PRIORITY_EQUITY", "true").strip().lower() in {
        "1",
        "true",
        "yes",
        "on",
    }


def _env_float(name: str, default: float) -> float:
    raw = os.getenv(name, "").strip()
    if not raw:
        return default
    try:
        return float(raw)
    except ValueError:
        return default


def _env_int(name: str, default: int) -> int:
    raw = os.getenv(name, "").strip()
    if not raw:
        return default
    try:
        return int(raw)
    except ValueError:
        return default


def _route1_lenient_enabled() -> bool:
    return os.getenv("OPTIONS_ROUTE1_LENIENT", "true").strip().lower() in {
        "1",
        "true",
        "yes",
        "on",
    }


def _route1_universe_overrides(universe: OptionsUniverseConfig) -> OptionsUniverseConfig:
    """Ventana DTE y liquidez más amplia para los 11 tickers R1."""
    if not _route1_lenient_enabled():
        return universe
    return universe.model_copy(
        update={
            "dte_min": _env_int("OPTIONS_UNIVERSE_DTE_MIN", min(universe.dte_min, 3)),
            "dte_max": _env_int("OPTIONS_UNIVERSE_DTE_MAX", max(universe.dte_max, 45)),
            "min_open_interest": _env_int(
                "OPTIONS_MIN_OPEN_INTEREST",
                min(universe.min_open_interest, 100),
            ),
            "min_daily_volume": _env_int(
                "OPTIONS_MIN_DAILY_VOLUME",
                min(universe.min_daily_volume, 25),
            ),
        }
    )


def _route1_catchall_playbook(base: PlaybooksConfig) -> PlaybooksConfig:
    """Playbook de respaldo para señales direccionales sin match estricto."""
    if not _route1_lenient_enabled():
        return base
    updated = dict(base.playbooks)
    updated["route1_directional"] = PlaybookConfig(
        enabled=True,
        allowed_structures=(
            OptionsStructure.LONG_CALL.value,
            OptionsStructure.LONG_PUT.value,
            OptionsStructure.CALL_DEBIT_SPREAD.value,
            OptionsStructure.PUT_DEBIT_SPREAD.value,
            OptionsStructure.BULL_CALL_SPREAD.value,
            OptionsStructure.CALL_BUTTERFLY.value,
        ),
        min_trend_quality=0.28,
        min_predictive_bias=0.05,
        min_options_bias=0.05,
    )
    return PlaybooksConfig(playbooks=updated)


def _scale_risk(base: RiskRulesConfig, multiplier: float) -> RiskRulesConfig:
    mult = max(0.05, min(multiplier, 2.0))
    return base.model_copy(
        update={
            "max_risk_per_trade_pct": round(base.max_risk_per_trade_pct * mult, 4),
            "max_same_direction_exposure_pct": round(
                base.max_same_direction_exposure_pct * mult, 4
            ),
        }
    )


def _r2_playbooks(base: PlaybooksConfig) -> PlaybooksConfig:
    """Playbook R2: long call/put, short put y vertical credit (put)."""
    trend = base.playbooks.get("trend_continuation")
    if trend is None:
        playbooks = {
            "trend_continuation": PlaybookConfig(
                enabled=True,
                allowed_structures=_R2_BASIC_STRUCTURES,  # type: ignore[arg-type]
                min_trend_quality=0.35,
            )
        }
    else:
        playbooks = {
            "trend_continuation": trend.model_copy(
                update={
                    "enabled": True,
                    "allowed_structures": _R2_BASIC_STRUCTURES,  # type: ignore[arg-type]
                    "min_trend_quality": 0.35,
                    "min_predictive_bias": None,
                    "min_options_bias": None,
                    "require_gamma_level": False,
                }
            )
        }
    playbooks["route2_directional"] = PlaybookConfig(
        enabled=True,
        allowed_structures=_R2_BASIC_STRUCTURES,  # type: ignore[arg-type]
        min_trend_quality=0.28,
        min_predictive_bias=None,
        min_options_bias=None,
    )
    return PlaybooksConfig(playbooks=playbooks)


def _relaxed_r1_playbooks(base: PlaybooksConfig) -> PlaybooksConfig:
    """Umbrales de playbook más bajos en sesión de verificación."""
    updated: dict[str, PlaybookConfig] = {}
    for name, pb in base.playbooks.items():
        if not pb.enabled:
            continue
        updates: dict[str, object] = {}
        if pb.min_trend_quality is not None:
            updates["min_trend_quality"] = 0.35
        if pb.min_predictive_bias is not None:
            updates["min_predictive_bias"] = 0.08
        if pb.min_options_bias is not None:
            updates["min_options_bias"] = 0.08
        if name == "gamma_wall_rejection":
            updates["require_gamma_level"] = False
        updated[name] = pb.model_copy(update=updates)
    return PlaybooksConfig(playbooks=updated)


def _relaxed_options_min_confidence(default: float) -> float:
    if os.getenv("OPTIONS_STRATEGY_RELAXED_VETOS", "").strip().lower() not in {
        "1",
        "true",
        "yes",
        "on",
    }:
        return default
    return _env_float(_OPTIONS_MIN_CONF_ENV, 0.18)


def get_options_config_for_route(
    route: AlpacaOptionsRoute,
    *,
    r2_symbols: tuple[str, ...] = (),
) -> OptionsStrategyConfigBundle:
    """Devuelve configuración Options Strategy ajustada a R1 (priority) o R2 (scan)."""
    base = get_options_strategy_config()

    if route == "priority":
        risk_mult = _env_float(_R1_RISK_MULT_ENV, 1.0)
        min_conf = _relaxed_options_min_confidence(base.omni_engine.min_global_confidence)
        playbooks = _route1_catchall_playbook(base.playbooks)
        if os.getenv("OPTIONS_STRATEGY_RELAXED_VETOS", "").strip().lower() in {
            "1",
            "true",
            "yes",
            "on",
        }:
            playbooks = _relaxed_r1_playbooks(playbooks)
        omni = base.omni_engine.model_copy(
            update={"min_global_confidence": min_conf},
        )
        return base.model_copy(
            update={
                "omni_engine": omni,
                "universe": _route1_universe_overrides(base.universe),
                "playbooks": playbooks,
                "risk": _scale_risk(base.risk, risk_mult),
                "structure_profile": "full",
            },
        )

    r2_min_conf = _env_float(
        _R2_MIN_CONF_ENV,
        0.35
        if os.getenv("OPTIONS_STRATEGY_RELAXED_VETOS", "").strip().lower()
        in {"1", "true", "yes", "on"}
        else 0.48,
    )
    risk_mult = _env_float(_R2_RISK_MULT_ENV, 0.85)
    omni = OmniEngineConfig(
        enabled_layers=("technical",),
        fusion_mode=base.omni_engine.fusion_mode,
        min_global_confidence=r2_min_conf,
        weights={"technical": 1.0},
        disagreement_penalty=0.05,
        veto_rules=tuple(
            rule
            for rule in base.omni_engine.veto_rules
            if rule not in {"symbol_not_in_route1_universe", "chain_liquidity_poor"}
        ),
    )
    universe = _route1_universe_overrides(
        base.universe.model_copy(
            update={
                "enforce_route1_only": False,
            }
        )
    )
    resolved = tuple(dict.fromkeys(sym.upper() for sym in r2_symbols))
    risk = _scale_risk(base.risk, risk_mult).model_copy(
        update={"max_open_positions": max(base.risk.max_open_positions, 8)}
    )

    return base.model_copy(
        update={
            "omni_engine": omni,
            "universe": universe,
            "playbooks": _r2_playbooks(base.playbooks),
            "risk": risk,
            "resolved_symbols": resolved,
            "structure_profile": "r2_basic",
        }
    )


__all__ = [
    "AlpacaOptionsRoute",
    "alpaca_options_enabled",
    "alpaca_options_priority_over_equity",
    "get_options_config_for_route",
]
