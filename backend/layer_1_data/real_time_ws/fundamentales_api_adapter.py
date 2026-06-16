from __future__ import annotations
"""Bindings de APIs para inyección de datos del sector Fundamentales."""


from dataclasses import dataclass
from typing import Final

try:
    from config.settings import Config, load_settings
except ModuleNotFoundError:  # pragma: no cover - compatibilidad de import por paquete.
    from backend.config.settings import Config, load_settings


@dataclass(frozen=True)
class ApiSourceBinding:
    provider: str
    env_key: str
    config_attr: str
    priority: int
    purpose: str
    realtime: bool


@dataclass(frozen=True)
class ResolvedApiSourceBinding:
    provider: str
    env_key: str
    priority: int
    purpose: str
    realtime: bool
    active: bool


_FUNDAMENTALES_MODEL_BINDINGS: Final[dict[str, tuple[ApiSourceBinding, ...]]] = {
    "fundamental_models": (
        ApiSourceBinding(
            provider="FMP",
            env_key="FMP_KEY_STATEMENTS",
            config_attr="fmp_key_statements",
            priority=1,
            purpose="financial_statements",
            realtime=False,
        ),
        ApiSourceBinding(
            provider="MASSIVE",
            env_key="MASSIVE_KEY_FINANCIALS",
            config_attr="massive_key_financials",
            priority=2,
            purpose="financials_fallback",
            realtime=True,
        ),
    ),
    "forensic_models": (
        ApiSourceBinding(
            provider="FMP",
            env_key="FMP_KEY_STATEMENTS",
            config_attr="fmp_key_statements",
            priority=1,
            purpose="ratios_and_statements",
            realtime=False,
        ),
        ApiSourceBinding(
            provider="SEC_API",
            env_key="SEC_API_KEY",
            config_attr="sec_api_key",
            priority=2,
            purpose="filings_and_insider_events",
            realtime=False,
        ),
        ApiSourceBinding(
            provider="FMP",
            env_key="FMP_KEY_FILINGS",
            config_attr="fmp_key_filings",
            priority=3,
            purpose="filings_fallback",
            realtime=False,
        ),
    ),
    "credit_models": (
        ApiSourceBinding(
            provider="MASSIVE",
            env_key="MASSIVE_KEY_DISTRESS",
            config_attr="massive_key_distress",
            priority=1,
            purpose="distress_and_credit_stress",
            realtime=True,
        ),
        ApiSourceBinding(
            provider="MASSIVE",
            env_key="MASSIVE_KEY_WS_QUOTES",
            config_attr="massive_key_ws_quotes",
            priority=2,
            purpose="live_spreads_quotes",
            realtime=True,
        ),
    ),
    "event_models": (
        ApiSourceBinding(
            provider="FMP",
            env_key="FMP_KEY_NEWS",
            config_attr="fmp_key_news",
            priority=1,
            purpose="news_events",
            realtime=True,
        ),
        ApiSourceBinding(
            provider="FMP",
            env_key="FMP_KEY_CALENDARS",
            config_attr="fmp_key_calendars",
            priority=2,
            purpose="earnings_macro_calendars",
            realtime=True,
        ),
        ApiSourceBinding(
            provider="FRED",
            env_key="FRED_API_KEY",
            config_attr="fred_api_key",
            priority=3,
            purpose="macro_context",
            realtime=False,
        ),
    ),
    "regulatory_models": (
        ApiSourceBinding(
            provider="SEC_API",
            env_key="SEC_API_KEY",
            config_attr="sec_api_key",
            priority=1,
            purpose="regulatory_filings",
            realtime=False,
        ),
        ApiSourceBinding(
            provider="FMP",
            env_key="FMP_KEY_FILINGS",
            config_attr="fmp_key_filings",
            priority=2,
            purpose="filings_backup",
            realtime=False,
        ),
        ApiSourceBinding(
            provider="FMP",
            env_key="FMP_KEY_NEWS",
            config_attr="fmp_key_news",
            priority=3,
            purpose="regulatory_news_context",
            realtime=True,
        ),
    ),
    "flow_models": (
        ApiSourceBinding(
            provider="FRED",
            env_key="FRED_API_KEY",
            config_attr="fred_api_key",
            priority=1,
            purpose="walcl_wtregen_rrpontsyd_nfci",
            realtime=False,
        ),
        ApiSourceBinding(
            provider="MASSIVE",
            env_key="MASSIVE_KEY_MACRO",
            config_attr="massive_key_macro",
            priority=2,
            purpose="macro_liquidity_context",
            realtime=True,
        ),
        ApiSourceBinding(
            provider="MASSIVE",
            env_key="MASSIVE_KEY_WS_TRADES",
            config_attr="massive_key_ws_trades",
            priority=3,
            purpose="flow_realtime",
            realtime=True,
        ),
    ),
}


def get_fundamentales_api_bindings() -> dict[str, tuple[ApiSourceBinding, ...]]:
    """Retorna el mapeo estático modelo → prioridades de APIs."""

    return {
        model_name: tuple(bindings)
        for model_name, bindings in _FUNDAMENTALES_MODEL_BINDINGS.items()
    }


def resolve_fundamentales_api_bindings(
    settings: Config | None = None,
) -> dict[str, tuple[ResolvedApiSourceBinding, ...]]:
    """Resuelve el estado activo de cada binding usando configuración tipada."""

    resolved_settings = settings
    if resolved_settings is None:
        try:
            resolved_settings = load_settings()
        except SystemExit:
            resolved_settings = None

    resolved: dict[str, tuple[ResolvedApiSourceBinding, ...]] = {}
    for model_name, bindings in _FUNDAMENTALES_MODEL_BINDINGS.items():
        ordered_bindings = sorted(bindings, key=lambda item: item.priority)
        resolved[model_name] = tuple(
            ResolvedApiSourceBinding(
                provider=item.provider,
                env_key=item.env_key,
                priority=item.priority,
                purpose=item.purpose,
                realtime=item.realtime,
                active=_is_binding_active(resolved_settings, item.config_attr),
            )
            for item in ordered_bindings
        )
    return resolved


def _is_binding_active(settings: Config | None, config_attr: str) -> bool:
    if settings is None:
        return False
    value = getattr(settings, config_attr, None)
    return isinstance(value, str) and bool(value.strip())


__all__ = [
    "ApiSourceBinding",
    "ResolvedApiSourceBinding",
    "get_fundamentales_api_bindings",
    "resolve_fundamentales_api_bindings",
]
