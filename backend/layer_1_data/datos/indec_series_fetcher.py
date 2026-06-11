"""Conector de series económicas argentinas desde datos.gob.ar/INDEC."""

from __future__ import annotations

import datetime as _dt
import json
import logging
import urllib.parse
import urllib.request
from typing import Final

import pandas as pd

logger = logging.getLogger("backend.layer_1_data.datos.indec_series_fetcher")

INDEC_SERIES_INFLACION: Final[str] = "148.3_INIVELNAL_DICI_M_26"
INDEC_SERIES_TIPO_CAMBIO: Final[str] = "92.2_TIPO_CAMBIION_0_0_21_24"
INDEC_SERIES_RESERVAS: Final[str] = "174.1_RRVAS_IDOS_0_0_36"
INDEC_SERIES_BASE_MONETARIA: Final[str] = "331.1_SALDO_BASERIA__15"
INDEC_SERIES_EMAE: Final[str] = "143.3_NO_PR_2004_A_21"
INDEC_SERIES_DESEMPLEO: Final[str] = "45.2_ECTDT_0_T_33"

_BASE_URL: Final[str] = "https://apis.datos.gob.ar/series/api"
_TIMEOUT: Final[int] = 15
_DEFAULT_LIMIT: Final[int] = 1000
_HEADERS: Final[dict[str, str]] = {
    "User-Agent": "QuantumAnalyzer/1.0",
    "Accept": "application/json",
}


class INDECSeriesFetcher:
    """
    Conector para la API de series de tiempo del INDEC / datos.gob.ar.

    Stateless. Sin I/O fuera de las llamadas HTTP.
    Todos los métodos retornan None ante cualquier error (fail-graceful).

    Punto de integración en el pipeline:
        · Fase 1 (Macro Engine) — indicadores macro argentinos como complemento
          al MacroEngine basado en FRED (indicadores US).
        · Datos de contexto para el engine de análisis CEDEAR/CCL.
    """

    # ─────────────────────────────────────────────────────────────────────────
    # Métodos especializados por serie
    # ─────────────────────────────────────────────────────────────────────────

    def get_inflacion(
        self,
        start_date: str | None = None,
        end_date: str | None = None,
    ) -> pd.DataFrame | None:
        """
        IPC Nacional General — variación mensual del nivel general de precios.

        Returns:
            DataFrame con columnas [fecha, inflacion_ipc] o None.
        """
        return self._fetch_named(
            INDEC_SERIES_INFLACION,
            col_name="inflacion_ipc",
            start_date=start_date,
            end_date=end_date,
        )

    def get_emae(
        self,
        start_date: str | None = None,
        end_date: str | None = None,
    ) -> pd.DataFrame | None:
        """
        EMAE (Estimador Mensual de Actividad Económica) — proxy del PIB mensual
        argentino. Base 2004 = 100.

        Returns:
            DataFrame con columnas [fecha, emae] o None.
        """
        return self._fetch_named(
            INDEC_SERIES_EMAE,
            col_name="emae",
            start_date=start_date,
            end_date=end_date,
        )

    def get_tipo_cambio_oficial(
        self,
        start_date: str | None = None,
        end_date: str | None = None,
    ) -> pd.DataFrame | None:
        """
        Tipo de cambio ARS/USD oficial (referencia BCRA).

        Returns:
            DataFrame con columnas [fecha, tipo_cambio_oficial] o None.
        """
        return self._fetch_named(
            INDEC_SERIES_TIPO_CAMBIO,
            col_name="tipo_cambio_oficial",
            start_date=start_date,
            end_date=end_date,
        )

    def get_reservas_internacionales(
        self,
        start_date: str | None = None,
        end_date: str | None = None,
    ) -> pd.DataFrame | None:
        """
        Reservas internacionales del BCRA en millones de dólares.

        Returns:
            DataFrame con columnas [fecha, reservas_usd_mm] o None.
        """
        return self._fetch_named(
            INDEC_SERIES_RESERVAS,
            col_name="reservas_usd_mm",
            start_date=start_date,
            end_date=end_date,
        )

    def get_base_monetaria(
        self,
        start_date: str | None = None,
        end_date: str | None = None,
    ) -> pd.DataFrame | None:
        """
        Base monetaria argentina en millones de pesos.

        Returns:
            DataFrame con columnas [fecha, base_monetaria_mm] o None.
        """
        return self._fetch_named(
            INDEC_SERIES_BASE_MONETARIA,
            col_name="base_monetaria_mm",
            start_date=start_date,
            end_date=end_date,
        )

    def get_desempleo(
        self,
        start_date: str | None = None,
        end_date: str | None = None,
    ) -> pd.DataFrame | None:
        """
        Tasa de desempleo argentina (EPH, trimestral, %).

        Returns:
            DataFrame con columnas [fecha, desempleo_pct] o None.
        """
        return self._fetch_named(
            INDEC_SERIES_DESEMPLEO,
            col_name="desempleo_pct",
            start_date=start_date,
            end_date=end_date,
        )

    # ─────────────────────────────────────────────────────────────────────────
    # API genérica
    # ─────────────────────────────────────────────────────────────────────────

    def fetch_series(
        self,
        series_ids: list[str],
        start_date: str | None = None,
        end_date: str | None = None,
        collapse: str | None = None,
        representation_mode: str | None = None,
        limit: int = _DEFAULT_LIMIT,
    ) -> pd.DataFrame | None:
        """
        Fetch genérico de una o varias series de tiempo del catálogo INDEC.

        Args:
            series_ids:          Lista de IDs de series (ej: ["148.3_INIVELNAL_DICI_M_26"]).
            start_date:          Fecha inicio ISO "YYYY-MM-DD" (opcional).
            end_date:            Fecha fin ISO "YYYY-MM-DD" (opcional).
            collapse:            Frecuencia de agregación: "month", "quarter", "year" (opcional).
            representation_mode: "percent_change" para variación porcentual (opcional).
            limit:               Máximo de registros por serie (default: 1000).

        Returns:
            DataFrame con columna 'fecha' + una columna por cada series_id,
            ordenado por fecha ascendente. None si ocurre un error.
        """
        try:
            if not series_ids:
                return None

            params: dict[str, str] = {
                "ids": ",".join(series_ids),
                "format": "json",
                "limit": str(limit),
                "metadata": "full",
            }
            if start_date:
                params["start_date"] = start_date
            if end_date:
                params["end_date"] = end_date
            if collapse:
                params["collapse"] = collapse
            if representation_mode:
                params["representation_mode"] = representation_mode

            url = _BASE_URL + "/series?" + urllib.parse.urlencode(params)
            data = self._get(url)
            if not isinstance(data, dict):
                return None

            return self._parse_series_response(data)
        except Exception:
            return None

    # ─────────────────────────────────────────────────
    # MIGRATION AUDIT — SECTOR: FUNDAMENTALES
    # Archivo: indec_series_fetcher.py
    # Eliminado: import de constantes del sistema anterior y encabezado de procedencia previa
    # Preservado: métodos públicos, contratos DataFrame y parseo del endpoint /series y /search
    # Pendientes: ninguno
    # ─────────────────────────────────────────────────

    def search(self, query: str, limit: int = 10) -> list[dict] | None:
        """
        Busca series en el catálogo INDEC / datos.gob.ar por texto libre.

        Args:
            query: Texto de búsqueda en español (ej: "inflación mensual").
            limit: Máximo de resultados devueltos.

        Returns:
            Lista de dicts con claves: id, title, description, units,
            frequency, dataset_title, source. None si ocurre un error.
        """
        try:
            params = urllib.parse.urlencode({"q": query, "limit": str(limit)})
            url = f"{_BASE_URL}/search?{params}"
            data = self._get(url)
            if not isinstance(data, dict):
                return None
            results = data.get("data", [])
            if not isinstance(results, list):
                return None
            return results if results else None
        except Exception:
            return None

    # ─────────────────────────────────────────────────────────────────────────
    # Helpers internos
    # ─────────────────────────────────────────────────────────────────────────

    def _fetch_named(
        self,
        series_id: str,
        col_name: str,
        start_date: str | None,
        end_date: str | None,
    ) -> pd.DataFrame | None:
        """Fetch una serie única y renombra la columna de valor a col_name."""
        try:
            df = self.fetch_series([series_id], start_date=start_date, end_date=end_date)
            if df is None or df.empty:
                return None
            if series_id in df.columns:
                df = df.rename(columns={series_id: col_name})
            return df
        except Exception:
            return None

    def _get(self, url: str) -> dict | list | None:
        """Realiza GET y retorna JSON parseado, o None ante cualquier error."""
        try:
            req = urllib.request.Request(url, headers=_HEADERS)
            with urllib.request.urlopen(req, timeout=_TIMEOUT) as resp:
                raw = resp.read().decode("utf-8")
                return json.loads(raw)
        except Exception as exc:
            logger.debug("INDECSeriesFetcher._get falló para %s: %s", url, exc)
            return None

    @staticmethod
    def _parse_series_response(data: dict) -> pd.DataFrame | None:
        """
        Parsea la respuesta del endpoint /series en formato wide DataFrame.

        La API retorna:
          meta: descriptores de columnas (el primero siempre es el índice temporal)
          data: lista de filas [fecha_str, val1, val2, ...]
        """
        try:
            meta: list = data.get("meta", [])
            rows: list = data.get("data", [])

            if not rows:
                return None

            col_names: list[str] = []
            for m in meta:
                field_info = m.get("field", {}) if isinstance(m, dict) else {}
                fid = field_info.get("id", "")
                col_names.append(str(fid))

            records: list[dict] = []
            for row in rows:
                if not isinstance(row, list) or not row:
                    continue
                try:
                    fecha = _dt.date.fromisoformat(str(row[0])[:10])
                    record: dict = {"fecha": fecha}
                    for i, val in enumerate(row[1:], start=1):
                        if i < len(col_names):
                            record[col_names[i]] = float(val) if val is not None else None
                    records.append(record)
                except Exception:
                    continue

            if not records:
                return None

            return pd.DataFrame(records).sort_values("fecha").reset_index(drop=True)
        except Exception:
            return None
