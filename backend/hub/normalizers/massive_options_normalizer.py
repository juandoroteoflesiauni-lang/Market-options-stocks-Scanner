"""Normalizador de opciones para la API de Massive.

Transforma las respuestas raw de Massive en objetos OptionContract y
OptionChainSnapshot válidos para el pipeline de Phase C.
"""

from __future__ import annotations

import time
from datetime import UTC, date, datetime
from decimal import Decimal
from typing import Any

from backend.models.market_snapshot import DataLineage
from backend.models.option_contract import OptionChainSnapshot, OptionContract


class MassiveOptionsNormalizer:
    """Normaliza datos de opciones de Massive API a modelos del dominio."""

    PROVIDER_NAME: str = "massive_options"

    def normalize_chain(
        self,
        ticker: str,
        spot_price: float,
        raw_contracts: list[dict[str, Any]],
        ingestion_start_ns: int,
    ) -> OptionChainSnapshot:
        """Normaliza una cadena completa de opciones de Massive.

        Args:
            ticker: Símbolo del underlying.
            spot_price: Precio actual del underlying.
            raw_contracts: Lista de contratos raw de Massive API.
            ingestion_start_ns: Timestamp de inicio de ingesta en nanosegundos.

        Returns:
            OptionChainSnapshot normalizado con todos los contratos válidos.
        """
        ingestion_latency_ms = (time.time_ns() - ingestion_start_ns) // 1_000_000
        contracts: list[OptionContract] = []
        total_call_vol = 0
        total_put_vol = 0
        total_call_oi = 0
        total_put_oi = 0

        for raw in raw_contracts:
            try:
                contract = self._normalize_single_contract(
                    ticker=ticker,
                    raw=raw,
                    ingestion_latency_ms=ingestion_latency_ms,
                )
                contracts.append(contract)

                if contract.is_call:
                    total_call_vol += contract.volume
                    total_call_oi += contract.open_interest
                else:
                    total_put_vol += contract.volume
                    total_put_oi += contract.open_interest
            except (ValueError, KeyError, TypeError):
                continue

        pc_ratio_vol = total_put_vol / max(total_call_vol, 1)
        pc_ratio_oi = total_put_oi / max(total_call_oi, 1)

        return OptionChainSnapshot(
            ticker=ticker.upper(),
            spot_price=Decimal(str(spot_price)),
            contracts=contracts,
            total_call_volume=total_call_vol,
            total_put_volume=total_put_vol,
            total_call_oi=total_call_oi,
            total_put_oi=total_put_oi,
            put_call_ratio_volume=pc_ratio_vol,
            put_call_ratio_oi=pc_ratio_oi,
            fetch_timestamp=datetime.now(UTC),
        )

    def _normalize_single_contract(
        self,
        ticker: str,
        raw: dict[str, Any],
        ingestion_latency_ms: int,
    ) -> OptionContract:
        """Normaliza un contrato individual de Massive.

        Massive API devuelve opciones con este esquema aproximado:
        {
            "symbol": "AAPL240119C00150000",
            "strike": 150.0,
            "expiry": "2024-01-19",
            "option_type": "call" o "put",
            "bid": 2.50,
            "ask": 2.60,
            "last": 2.55,
            "volume": 1000,
            "open_interest": 5000,
            "implied_volatility": 0.25,
            "delta": 0.55,
            "gamma": 0.02,
            "theta": -0.05,
            "vega": 0.15,
            "rho": 0.03
        }
        """
        contract_symbol = str(raw.get("symbol", "")).upper()
        if not contract_symbol:
            contract_symbol = self._build_symbol(ticker, raw)

        strike = Decimal(str(raw.get("strike", 0)))
        expiry = self._parse_expiry(raw.get("expiry", ""))
        option_type = self._normalize_option_type(raw.get("option_type", ""))

        bid = Decimal(str(raw.get("bid", 0)))
        ask = Decimal(str(raw.get("ask", 0)))
        last_price = Decimal(str(raw.get("last", 0)))
        volume = int(raw.get("volume", 0))
        open_interest = int(raw.get("open_interest", 0))
        iv = float(raw.get("implied_volatility", 0))

        delta = float(raw.get("delta", 0))
        gamma = float(raw.get("gamma", 0))
        theta = float(raw.get("theta", 0))
        vega = float(raw.get("vega", 0))
        rho = float(raw.get("rho", 0))
        vanna = float(raw.get("vanna", 0))
        charm = float(raw.get("charm", 0))

        mid_price = (bid + ask) / Decimal("2") if bid > 0 and ask > 0 else last_price
        spread = ask - bid if ask > bid else Decimal("0")
        spread_pct = float(spread / mid_price) if mid_price > 0 else 0.0
        dte = self._calculate_dte(expiry)

        return OptionContract(
            underlying_ticker=ticker,
            contract_symbol=contract_symbol,
            strike=strike,
            expiry=expiry,
            option_type=option_type,
            bid=bid,
            ask=ask,
            last_price=last_price,
            volume=volume,
            open_interest=open_interest,
            implied_volatility=iv,
            delta=delta,
            gamma=gamma,
            theta=theta,
            vega=vega,
            rho=rho,
            vanna=vanna,
            charm=charm,
            mid_price=mid_price,
            spread=spread,
            spread_pct=spread_pct,
            moneyness=0.0,
            dte=dte,
            data_lineage=DataLineage(
                source=self.PROVIDER_NAME,
                ingestion_latency_ms=ingestion_latency_ms,
                raw_field_count=len(raw),
            ),
        )

    @staticmethod
    def _normalize_option_type(raw_type: str) -> str:
        normalized = raw_type.strip().upper()
        if normalized in ("C", "CALL", "CALLS"):
            return "CALL"
        if normalized in ("P", "PUT", "PUTS"):
            return "PUT"
        raise ValueError(f"Invalid option_type: {raw_type}")

    @staticmethod
    def _parse_expiry(raw_expiry: str) -> date:
        formats = ["%Y-%m-%d", "%Y%m%d", "%m/%d/%Y"]
        for fmt in formats:
            try:
                return datetime.strptime(raw_expiry.strip(), fmt).date()
            except ValueError:
                continue
        raise ValueError(f"Cannot parse expiry date: {raw_expiry}")

    @staticmethod
    def _calculate_dte(expiry: date) -> int:
        today = datetime.now(UTC).date()
        return max((expiry - today).days, 0)

    @staticmethod
    def _build_symbol(ticker: str, raw: dict[str, Any]) -> str:
        expiry = str(raw.get("expiry", "")).replace("-", "")
        strike = str(raw.get("strike", "")).replace(".", "")
        opt_type = str(raw.get("option_type", "C"))[0].upper()
        return f"{ticker.upper()}{expiry}{opt_type}{strike.zfill(8)}"
