"""Canonical data-provider policy for FTMO Funding Lab.

This module is deliberately read-only and contains no provider IO. It is the
single source of truth for which feeds authorize production readiness, which
feeds are validation-only, and which feeds are contextual.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any

PRIMARY = "primary"
VALIDATION = "validation"
CONTEXT = "context"
COMPARISON = "comparison"
CRYPTO_OPTIONS_CONTEXT = "crypto_options_context"

DEFAULT_FRESHNESS_HOURS = 24
FTMO_POLICY_SYMBOLS = (
    "GOOGL",
    "AAPL",
    "TSLA",
    "XAUUSD",
    "XAGUSD",
    "US100.CASH",
    "BTC/USDT",
)


@dataclass(frozen=True)
class FtmoDataProviderPolicy:
    canonical_symbol: str
    primary_provider: str
    primary_symbols: tuple[str, ...]
    validation_provider: str | None = None
    validation_symbols: tuple[str, ...] = ()
    context_provider: str | None = None
    context_symbols: tuple[str, ...] = ()
    comparison_provider: str | None = None
    comparison_symbols: tuple[str, ...] = ()
    crypto_options_context_provider: str | None = None
    crypto_options_context_symbols: tuple[str, ...] = ()
    crypto_options_context_required: bool = False
    required_roles: tuple[str, ...] = (PRIMARY,)
    context_required: bool = False
    freshness_hours: int = DEFAULT_FRESHNESS_HOURS

    def to_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        for key in (
            "primary_symbols",
            "validation_symbols",
            "context_symbols",
            "comparison_symbols",
            "crypto_options_context_symbols",
            "required_roles",
        ):
            payload[key] = list(payload[key])
        payload["roles"] = self.roles_payload()
        return payload

    def roles_payload(self) -> dict[str, dict[str, Any]]:
        roles: dict[str, dict[str, Any]] = {
            PRIMARY: {
                "usage_role": PRIMARY,
                "provider": self.primary_provider,
                "symbols": list(self.primary_symbols),
                "required": PRIMARY in self.required_roles,
                "freshness_hours": self.freshness_hours,
            }
        }
        if self.validation_provider:
            roles[VALIDATION] = {
                "usage_role": VALIDATION,
                "provider": self.validation_provider,
                "symbols": list(self.validation_symbols),
                "required": VALIDATION in self.required_roles,
                "freshness_hours": self.freshness_hours,
            }
        if self.context_provider:
            roles[CONTEXT] = {
                "usage_role": CONTEXT,
                "provider": self.context_provider,
                "symbols": list(self.context_symbols),
                "required": self.context_required,
                "freshness_hours": self.freshness_hours,
            }
        if self.comparison_provider:
            roles[COMPARISON] = {
                "usage_role": COMPARISON,
                "provider": self.comparison_provider,
                "symbols": list(self.comparison_symbols),
                "required": COMPARISON in self.required_roles,
                "freshness_hours": self.freshness_hours,
            }
        if self.crypto_options_context_provider:
            roles[CRYPTO_OPTIONS_CONTEXT] = {
                "usage_role": CRYPTO_OPTIONS_CONTEXT,
                "provider": self.crypto_options_context_provider,
                "symbols": list(self.crypto_options_context_symbols),
                "required": self.crypto_options_context_required
                or CRYPTO_OPTIONS_CONTEXT in self.required_roles,
                "freshness_hours": self.freshness_hours,
            }
        return roles


_POLICIES: dict[str, FtmoDataProviderPolicy] = {
    "GOOGL": FtmoDataProviderPolicy(
        canonical_symbol="GOOGL",
        primary_provider="fmp_massive_polygon",
        primary_symbols=("GOOGL",),
        validation_provider="bingx_market",
        validation_symbols=("GOOGL-USDT", "NCSKGOOGL2USD-USDT"),
        required_roles=(PRIMARY, VALIDATION),
    ),
    "AAPL": FtmoDataProviderPolicy(
        canonical_symbol="AAPL",
        primary_provider="fmp_massive_polygon",
        primary_symbols=("AAPL",),
        validation_provider="bingx_market",
        validation_symbols=("AAPL-USDT", "NCSKAAPL2USD-USDT"),
        required_roles=(PRIMARY, VALIDATION),
    ),
    "TSLA": FtmoDataProviderPolicy(
        canonical_symbol="TSLA",
        primary_provider="fmp_massive_polygon",
        primary_symbols=("TSLA",),
        validation_provider="bingx_market",
        validation_symbols=("TSLA-USDT", "NCSKTSLA2USD-USDT"),
        required_roles=(PRIMARY, VALIDATION),
    ),
    "XAUUSD": FtmoDataProviderPolicy(
        canonical_symbol="XAUUSD",
        primary_provider="bingx_market",
        primary_symbols=("GOLD(XAU)-USDT", "NCCOGOLD2USD-USDT"),
        context_provider="fmp_massive_polygon",
        context_symbols=("GC=F", "GLD"),
        crypto_options_context_provider="crypto_options_multi",
        crypto_options_context_symbols=("BTC/USDT",),
        required_roles=(PRIMARY,),
        context_required=True,
    ),
    "XAGUSD": FtmoDataProviderPolicy(
        canonical_symbol="XAGUSD",
        primary_provider="bingx_market",
        primary_symbols=("SILVER(XAG)-USDT", "NCCOXAG2USD-USDT"),
        context_provider="fmp_massive_polygon",
        context_symbols=("SI=F", "SLV"),
        crypto_options_context_provider="crypto_options_multi",
        crypto_options_context_symbols=("BTC/USDT",),
        required_roles=(PRIMARY,),
        context_required=True,
    ),
    "US100.CASH": FtmoDataProviderPolicy(
        canonical_symbol="US100.CASH",
        primary_provider="bingx_market",
        primary_symbols=(
            "NASDAQ100(7*24)-USDT",
            "NCSI724NASDAQ1002USD-USDT",
            "NASDAQ100-USDT",
            "NCSINASDAQ1002USD-USDT",
        ),
        context_provider="fmp_massive_polygon",
        context_symbols=("QQQ",),
        crypto_options_context_provider="crypto_options_multi",
        crypto_options_context_symbols=("BTC/USDT",),
        required_roles=(PRIMARY,),
        context_required=True,
    ),
    "BTC/USDT": FtmoDataProviderPolicy(
        canonical_symbol="BTC/USDT",
        primary_provider="bingx_market",
        primary_symbols=("BTC-USDT",),
        comparison_provider="binance_public",
        comparison_symbols=("BTCUSDT",),
        crypto_options_context_provider="crypto_options_multi",
        crypto_options_context_symbols=("BTC/USDT",),
        crypto_options_context_required=True,
        required_roles=(PRIMARY, CRYPTO_OPTIONS_CONTEXT),
    ),
}


def funding_lab_data_provider_policy() -> list[FtmoDataProviderPolicy]:
    return [_POLICIES[symbol] for symbol in FTMO_POLICY_SYMBOLS]


def provider_policy_for_symbol(symbol: str) -> FtmoDataProviderPolicy:
    key = str(symbol).strip().upper()
    if key in {"BTCUSDT", "BTCUSD", "BTC-USD"}:
        key = "BTC/USDT"
    if key == "US100CASH":
        key = "US100.CASH"
    try:
        return _POLICIES[key]
    except KeyError as exc:
        raise ValueError(f"{symbol} is not part of the ftmo provider policy") from exc


def data_provider_policy_payload() -> dict[str, dict[str, Any]]:
    return {
        policy.canonical_symbol: policy.to_dict() for policy in funding_lab_data_provider_policy()
    }
