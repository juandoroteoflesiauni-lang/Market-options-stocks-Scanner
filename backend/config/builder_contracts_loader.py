"""Loader for MFFU Builder futures contract specifications."""

from __future__ import annotations

import os
from decimal import Decimal
from functools import lru_cache
from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel, ConfigDict, Field, field_validator

_DEFAULT_YAML = Path(__file__).parent / "builder_contracts.yaml"


class BuilderContractSpec(BaseModel):
    """Immutable futures contract specification for Builder sizing."""

    model_config = ConfigDict(frozen=True)

    symbol: str
    tick_size: Decimal
    tick_value: Decimal
    is_micro: bool = False
    mini_equivalent: Decimal = Field(default=Decimal("1.0"), gt=0)
    paired_symbol: str | None = None

    @field_validator("symbol", "paired_symbol", mode="before")
    @classmethod
    def _upper_symbol(cls, value: str | None) -> str | None:
        if value is None:
            return None
        return str(value).strip().upper()


class BuilderContractsDefaults(BaseModel):
    """Global caps shared across the Builder contract catalog."""

    model_config = ConfigDict(frozen=True)

    max_minis: int = Field(default=4, gt=0)
    max_micros: int = Field(default=40, gt=0)
    revisable: bool = True


class BuilderContractsCatalog(BaseModel):
    """Full contract catalog with alias resolution."""

    model_config = ConfigDict(frozen=True)

    defaults: BuilderContractsDefaults = Field(default_factory=BuilderContractsDefaults)
    contracts: dict[str, BuilderContractSpec]
    aliases: dict[str, str]

    def resolve(
        self,
        raw_symbol: str,
        *,
        prefer_micro: bool = False,
    ) -> BuilderContractSpec:
        """Resolve a raw symbol or alias to a canonical contract spec."""
        canonical = _normalize_alias_key(raw_symbol)
        target = self.aliases.get(canonical)
        if target is None:
            if canonical in self.contracts:
                target = canonical
            else:
                raise ValueError(f"{raw_symbol} is not part of the Builder futures universe")

        spec = self.contracts[target]
        if prefer_micro and not spec.is_micro and spec.paired_symbol:
            micro = self.contracts.get(spec.paired_symbol)
            if micro is not None and micro.is_micro:
                return micro
        return spec


def _normalize_alias_key(raw_symbol: str) -> str:
    return str(raw_symbol).strip().upper().replace(" ", "")


def _parse_contract_entry(symbol: str, payload: dict[str, Any]) -> BuilderContractSpec:
    return BuilderContractSpec(
        symbol=symbol,
        tick_size=Decimal(str(payload["tick_size"])),
        tick_value=Decimal(str(payload["tick_value"])),
        is_micro=bool(payload.get("is_micro", False)),
        mini_equivalent=Decimal(str(payload.get("mini_equivalent", "1.0"))),
        paired_symbol=payload.get("paired_symbol"),
    )


def load_builder_contracts(filepath: str | Path | None = None) -> BuilderContractsCatalog:
    """Load and validate the Builder futures contract catalog from YAML."""
    path = Path(filepath) if filepath is not None else _DEFAULT_YAML
    if not path.exists():
        raise FileNotFoundError(f"Builder contracts file not found: {path}")

    with open(path, encoding="utf-8") as handle:
        raw = yaml.safe_load(handle) or {}

    defaults = BuilderContractsDefaults.model_validate(raw.get("defaults", {}))
    contracts = {
        symbol.upper(): _parse_contract_entry(symbol.upper(), entry)
        for symbol, entry in (raw.get("contracts") or {}).items()
    }
    aliases = {
        _normalize_alias_key(alias): str(target).upper()
        for alias, target in (raw.get("aliases") or {}).items()
    }
    return BuilderContractsCatalog(defaults=defaults, contracts=contracts, aliases=aliases)


@lru_cache(maxsize=1)
def get_builder_contracts_catalog() -> BuilderContractsCatalog:
    """Return the cached default Builder contracts catalog."""
    override = os.getenv("QA_BUILDER_CONTRACTS_PATH")
    if override:
        return load_builder_contracts(override)
    return load_builder_contracts()


def resolve_builder_contract(
    raw_symbol: str,
    *,
    prefer_micro: bool = False,
    catalog: BuilderContractsCatalog | None = None,
) -> BuilderContractSpec:
    """Resolve a symbol through the default or provided catalog."""
    active = catalog or get_builder_contracts_catalog()
    return active.resolve(raw_symbol, prefer_micro=prefer_micro)
