"""Servicios de datos e ingesta de la Capa 1."""

try:
    from layer_1_data.datos.db_manager import DuckDBManager
    from layer_1_data.datos.finnhub_fetcher import FinnhubFetcher
    from layer_1_data.datos.indec_series_fetcher import INDECSeriesFetcher
    from layer_1_data.datos.sec_fetcher import SECFetcher
    from layer_1_data.fetchers.fmp_client import FMPClient
except ModuleNotFoundError:  # pragma: no cover - compatibilidad por paquete.
    from backend.layer_1_data.datos.db_manager import DuckDBManager
    from backend.layer_1_data.datos.finnhub_fetcher import FinnhubFetcher
    from backend.layer_1_data.datos.indec_series_fetcher import INDECSeriesFetcher
    from backend.layer_1_data.datos.sec_fetcher import SECFetcher
    from backend.layer_1_data.fetchers.fmp_client import FMPClient

__all__ = [
    "DuckDBManager",
    "FMPClient",
    "FinnhubFetcher",
    "INDECSeriesFetcher",
    "SECFetcher",
]
