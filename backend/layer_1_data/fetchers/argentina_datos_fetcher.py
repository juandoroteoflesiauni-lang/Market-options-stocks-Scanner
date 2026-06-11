"""
backend/layer_1_data/fetchers/argentina_datos_fetcher.py
════════════════════════════════════════════════════════════════════════════════
ArgentinaDatos — Public connector for Argentine financial macro data.
════════════════════════════════════════════════════════════════════════════════
"""

from __future__ import annotations

import datetime as _dt
import json
import logging
import urllib.request
from datetime import datetime
from typing import Final

import pandas as pd

from backend.domain.argentina_models import (
    ARGENTINA_DOLAR_TIPOS,
    ArgentinaDolarSnapshot,
    RiesgoPaisPoint,
)

logger = logging.getLogger("backend.layer_1_data.fetchers.argentina_datos_fetcher")

_BASE_URL: Final[str] = "https://api.argentinadatos.com/v1"
_TIMEOUT: Final[int] = 10
_HEADERS: Final[dict[str, str]] = {
    "User-Agent": "QuantumAnalyzer/1.0",
    "Accept": "application/json",
}


class ArgentinaDatosFetcher:
    """
    Optimized fetcher with caching for the ArgentinaDatos public API.
    """

    # ── Dollar Exchange Rates ────────────────────────────────────────────────

    def get_dolar_ultimo(self, casa: str = "blue") -> ArgentinaDolarSnapshot | None:
        """
        Fetch the most recent quote for a specific dollar type with caching.
        """
        try:
            casa = casa.lower().strip()
            if casa not in ARGENTINA_DOLAR_TIPOS:
                return None
            url = f"{_BASE_URL}/cotizaciones/dolares/{casa}"
            data = self._get(url)
            if not isinstance(data, list) or not data:
                return None
            return self._parse_snapshot(data[-1], casa)
        except Exception:
            return None

    def get_dolar_historico(self, casa: str = "blue") -> list[ArgentinaDolarSnapshot] | None:
        """
        Fetch the complete historical series for a specific dollar type.
        """
        try:
            casa = casa.lower().strip()
            if casa not in ARGENTINA_DOLAR_TIPOS:
                return None
            url = f"{_BASE_URL}/cotizaciones/dolares/{casa}"
            data = self._get(url)
            if not isinstance(data, list) or not data:
                return None
            records: list[ArgentinaDolarSnapshot] = []
            for item in data:
                parsed = self._parse_snapshot(item, casa)
                if parsed is not None:
                    records.append(parsed)
            return records if records else None
        except Exception:
            return None

    def get_todas_cotizaciones(self) -> list[ArgentinaDolarSnapshot] | None:
        """
        Fetch all active dollar quotes in a single call.
        """
        try:
            url = f"{_BASE_URL}/cotizaciones/dolares"
            data = self._get(url)
            if not isinstance(data, list) or not data:
                return None
            records: list[ArgentinaDolarSnapshot] = []
            for item in data:
                casa = str(item.get("casa", "")).strip().lower()
                parsed = self._parse_snapshot(item, casa)
                if parsed is not None:
                    records.append(parsed)
            return records if records else None
        except Exception:
            return None

    def get_ccl_ultimo(self) -> ArgentinaDolarSnapshot | None:
        return self.get_dolar_ultimo("contadoconliqui")

    def get_mep_ultimo(self) -> ArgentinaDolarSnapshot | None:
        return self.get_dolar_ultimo("bolsa")

    def get_blue_ultimo(self) -> ArgentinaDolarSnapshot | None:
        return self.get_dolar_ultimo("blue")

    def get_oficial_ultimo(self) -> ArgentinaDolarSnapshot | None:
        return self.get_dolar_ultimo("oficial")

    # ── Country Risk (EMBI+ Argentina) ────────────────────────────────────────

    def get_riesgo_pais_ultimo(self) -> RiesgoPaisPoint | None:
        """
        Fetch the most recent Country Risk value in bps with caching.
        """
        try:
            url = f"{_BASE_URL}/finanzas/indices/riesgo-pais/ultimo"
            data = self._get(url)
            if isinstance(data, dict) and "fecha" in data:
                return self._parse_riesgo_pais(data)
            if isinstance(data, list) and data:
                return self._parse_riesgo_pais(data[-1])
            return None
        except Exception:
            return None

    def get_riesgo_pais_historico(self) -> list[RiesgoPaisPoint] | None:
        """
        Fetch the complete historical series of Country Risk.
        """
        try:
            url = f"{_BASE_URL}/finanzas/indices/riesgo-pais"
            data = self._get(url)
            if not isinstance(data, list) or not data:
                return None
            records: list[RiesgoPaisPoint] = []
            for item in data:
                parsed = self._parse_riesgo_pais(item)
                if parsed is not None:
                    records.append(parsed)
            return records if records else None
        except Exception:
            return None

    # ── Monthly Inflation (IPC) ──────────────────────────────────────────────

    def get_inflacion(self) -> pd.DataFrame | None:
        """
        Fetch the monthly inflation index (IPC INDEC) as a DataFrame.
        """
        try:
            url = f"{_BASE_URL}/finanzas/indices/inflacion"
            data = self._get(url)
            if not isinstance(data, list) or not data:
                return None
            rows: list[dict] = []
            for item in data:
                try:
                    fecha = _dt.date.fromisoformat(str(item["fecha"])[:10])
                    valor = float(item["valor"])
                    rows.append({"fecha": fecha, "valor": valor})
                except Exception:
                    continue
            if not rows:
                return None
            return pd.DataFrame(rows).sort_values("fecha").reset_index(drop=True)
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
            logger.debug("ArgentinaDatasFetcher._get failed for %s: %s", url, exc)
            return None

    @staticmethod
    def _parse_snapshot(item: dict, casa: str) -> ArgentinaDolarSnapshot | None:
        """Parse a dollar quote item."""
        try:
            fecha = _dt.date.fromisoformat(str(item.get("fecha", ""))[:10])
            compra_raw = item.get("compra")
            venta_raw = item.get("venta")
            return ArgentinaDolarSnapshot(
                fecha=fecha,
                casa=str(item.get("casa", casa)).strip().lower(),
                compra=float(compra_raw) if compra_raw is not None else None,
                venta=float(venta_raw) if venta_raw is not None else None,
            )
        except Exception:
            return None

    @staticmethod
    def _parse_riesgo_pais(item: dict) -> RiesgoPaisPoint | None:
        """Parse a country risk item."""
        try:
            fecha = _dt.date.fromisoformat(str(item.get("fecha", ""))[:10])
            valor = float(item["valor"])
            return RiesgoPaisPoint(fecha=fecha, valor=valor, fetched_at=datetime.now(_dt.UTC))
        except Exception:
            return None


# ─────────────────────────────────────────────────────────────────────
# MIGRATION AUDIT — SECTOR: DATA
# Archivo          : argentina_datos_fetcher.py
# Sub-capa         : Fetchers
# Enfoque          : Conector para la API de ArgentinaDatos.
# Eliminado        : Comentarios legacy de V1, dependencias de constants.py.
# Preservado       : Lógica de parsing, fail-graceful, soporte DataFrame.
# ─────────────────────────────────────────────────────────────────────
