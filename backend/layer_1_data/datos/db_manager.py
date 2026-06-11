"""Gestor central DuckDB para la Capa 1 (datos).

Este modulo expone una unica puerta de acceso a la persistencia para que las capas
superiores consuman datos de forma segura, trazable y sin abrir conexiones ad-hoc.
"""

from __future__ import annotations

from threading import Lock
from types import TracebackType
from typing import Any

import duckdb

try:
    from config.logger_setup import get_logger
    from config.settings import load_settings
except ModuleNotFoundError:  # pragma: no cover - compatibilidad por paquete.
    from backend.config.logger_setup import get_logger
    from backend.config.settings import load_settings

logger = get_logger(__name__)


class DuckDBManager:
    """Singleton de conexion DuckDB con soporte de contexto `with`.

    Reglas de uso para capas superiores:
    - Ingesta y servicios de datos deben usar este gestor, nunca `duckdb.connect(...)` directo.
    - Abrir conexion con `conectar()` o `with DuckDBManager() as db: ...`.
    - Ejecutar SQL mediante `ejecutar_query(...)`.
    - Cerrar con `desconectar()` cuando termine el proceso o job.
    """

    _instance: DuckDBManager | None = None
    _instance_lock: Lock = Lock()
    _connection: duckdb.DuckDBPyConnection | None

    def __new__(cls: type[DuckDBManager]) -> DuckDBManager:
        """Garantiza una sola instancia global del gestor."""
        if cls._instance is None:
            with cls._instance_lock:
                if cls._instance is None:
                    cls._instance = super().__new__(cls)
                    cls._instance._connection = None
        return cls._instance

    def __enter__(self: DuckDBManager) -> DuckDBManager:
        self.conectar()
        return self

    def __exit__(
        self: DuckDBManager,
        exc_type: type[BaseException] | None,
        exc_val: BaseException | None,
        exc_tb: TracebackType | None,
    ) -> None:
        self.desconectar()

    def conectar(self: DuckDBManager) -> None:
        """Abre la conexion unica a DuckDB usando `DB_PATH` de configuracion."""
        if self._connection is not None:
            logger.info("Conexion DuckDB reutilizada.")
            return

        settings = load_settings()
        from pathlib import Path

        db_path = (
            Path(settings.db_path)
            if getattr(settings, "db_path", None)
            else Path("data/quantum_analyzer.duckdb")
        )
        db_path.parent.mkdir(parents=True, exist_ok=True)

        self._connection = duckdb.connect(database=str(db_path))
        logger.info("Conexion DuckDB establecida en: %s", db_path)

    def desconectar(self: DuckDBManager) -> None:
        """Cierra la conexion activa, liberando recursos del proceso."""
        if self._connection is None:
            logger.info("No hay conexion DuckDB activa para cerrar.")
            return

        self._connection.close()
        self._connection = None
        logger.info("Conexion DuckDB cerrada correctamente.")

    def ejecutar_query(
        self: DuckDBManager,
        query: str,
        params: tuple[Any, ...] | None = None,
    ) -> list[tuple[Any, ...]]:
        """Ejecuta SQL y devuelve filas materializadas.

        Args:
            query: SQL a ejecutar.
            params: Parametros opcionales para consultas parametrizadas.

        Returns:
            Filas de resultado como lista de tuplas.

        Raises:
            RuntimeError: Si no hay conexion activa.
            duckdb.Error: Si la consulta falla.
        """
        if self._connection is None:
            logger.error("Intento de query sin conexion activa.")
            raise RuntimeError("DuckDB no esta conectado. Llama conectar() o usa contexto with.")

        try:
            cursor = (
                self._connection.execute(query, params)
                if params is not None
                else self._connection.execute(query)
            )
            result = cursor.fetchall()
            logger.info("Query ejecutada correctamente.")
            return result
        except duckdb.Error:
            logger.exception("Fallo al ejecutar query DuckDB.")
            raise
