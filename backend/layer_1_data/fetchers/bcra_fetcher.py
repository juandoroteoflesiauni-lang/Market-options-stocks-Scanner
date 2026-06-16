from __future__ import annotations
"""
backend/layer_1_data/fetchers/bcra_fetcher.py
════════════════════════════════════════════════════════════════════════════════
BCRA (Banco Central de la República Argentina) — Foreign Exchange Statistics.
════════════════════════════════════════════════════════════════════════════════
"""


import datetime as _dt
import json
import logging
import urllib.parse
import urllib.request
from datetime import datetime
from typing import Final

import pandas as pd

from backend.domain.argentina_models import BCRAExchangeRate

logger = logging.getLogger("backend.layer_1_data.fetchers.bcra_fetcher")

_BASE_URL: Final[str] = "https://api.bcra.gob.ar"
_COTIZACIONES_PATH: Final[str] = "/estadisticascambiarias/v1.0/Cotizaciones"
_TIMEOUT: Final[int] = 15
_HEADERS: Final[dict[str, str]] = {
    "User-Agent": "QuantumAnalyzer/1.0",
    "Authorization": "Bearer BCRA",
    "Accept": "application/json",
}

_USD_CODES: Final[frozenset[str]] = frozenset({"USD", "002"})


class BCRAFetcher:
    """
    Fetcher for the BCRA public API exchange rate statistics.
    Stateless and fail-graceful.
    """

    def get_cotizaciones(
        self,
        fecha_desde: str | None = None,
        fecha_hasta: str | None = None,
    ) -> list[BCRAExchangeRate] | None:
        """
        Fetch official BCRA exchange rates for all currencies.
        """
        try:
            params: dict[str, str] = {}
            if fecha_desde:
                params["fechaDesde"] = fecha_desde
            if fecha_hasta:
                params["fechaHasta"] = fecha_hasta

            url = _BASE_URL + _COTIZACIONES_PATH
            if params:
                url += "?" + urllib.parse.urlencode(params)

            data = self._get(url)
            if data is None:
                return None

            return self._parse_cotizaciones(data)
        except Exception as exc:
            logger.error("Error fetching BCRA cotizaciones: %s", exc)
            return None

    def get_cotizacion_usd(self) -> BCRAExchangeRate | None:
        """
        Fetch the most recent official USD rate from BCRA.
        """
        try:
            records = self.get_cotizaciones()
            if not records:
                return None
            for rec in reversed(records):
                if rec.codigo_moneda.upper() in _USD_CODES:
                    return rec
            return None
        except Exception:
            return None

    def get_cotizaciones_as_dataframe(
        self,
        fecha_desde: str | None = None,
        fecha_hasta: str | None = None,
    ) -> pd.DataFrame | None:
        """
        Fetch BCRA rates as a normalized pandas DataFrame.
        """
        try:
            records = self.get_cotizaciones(fecha_desde, fecha_hasta)
            if not records:
                return None

            rows = [
                {
                    "fecha": r.fecha,
                    "codigo_moneda": r.codigo_moneda,
                    "descripcion": r.descripcion,
                    "tipo_pase": r.tipo_pase,
                    "tipo_cotizacion": r.tipo_cotizacion,
                    "compra": r.compra,
                    "venta": r.venta,
                    "fetched_at": r.fetched_at,
                }
                for r in records
            ]
            return pd.DataFrame(rows)
        except Exception:
            return None

    # ── Internal Helpers ───────────────────────────────────────────────────────

    def _get(self, url: str) -> dict | list | None:
        """Execute GET request and return parsed JSON."""
        try:
            req = urllib.request.Request(url, headers=_HEADERS)
            with urllib.request.urlopen(req, timeout=_TIMEOUT) as resp:
                raw = resp.read().decode("utf-8")
                return json.loads(raw)
        except Exception as exc:
            logger.debug("BCRAFetcher._get failed for %s: %s", url, exc)
            return None

    @staticmethod
    def _parse_cotizaciones(data: dict | list) -> list[BCRAExchangeRate] | None:
        """
        Parse BCRA API response into domain models.
        """
        try:
            fetched_at = datetime.now(_dt.UTC)

            if not isinstance(data, dict):
                return None

            results = data.get("results")
            if not isinstance(results, dict):
                return None

            fecha_str: str = results.get("fecha", "")
            detalle: list = results.get("detalle", [])

            fecha_parsed: _dt.date
            try:
                fecha_parsed = _dt.date.fromisoformat(str(fecha_str)[:10])
            except (ValueError, TypeError):
                fecha_parsed = _dt.date.today()

            records: list[BCRAExchangeRate] = []
            for item in detalle:
                if not isinstance(item, dict):
                    continue
                try:
                    compra = item.get("compra")
                    venta = item.get("venta")
                    rec = BCRAExchangeRate(
                        fecha=fecha_parsed,
                        codigo_moneda=str(item.get("codigoMoneda", "")),
                        descripcion=item.get("descripcion"),
                        tipo_pase=item.get("tipoPase"),
                        tipo_cotizacion=item.get("tipoCotizacion"),
                        compra=float(compra) if compra is not None else None,
                        venta=float(venta) if venta is not None else None,
                        fetched_at=fetched_at,
                    )
                    records.append(rec)
                except Exception:
                    continue

            return records if records else None
        except Exception:
            return None


# ─────────────────────────────────────────────────────────────────────
# MIGRATION AUDIT — SECTOR: DATA
# Archivo          : bcra_fetcher.py
# Sub-capa         : Fetchers
# Enfoque          : Conector para la API cambiaria pública del BCRA.
# Eliminado        : Comentarios legacy de V1, noise en logs.
# Preservado       : Lógica de parsing y fail-graceful, Bearer BCRA token.
# Pendientes       : Ninguno.
# ─────────────────────────────────────────────────────────────────────
