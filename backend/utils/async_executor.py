"""Thread pool executor para operaciones CPU-bound en asyncio.

Este módulo proporciona un executor global para mover cálculos pesados
a threads separados, evitando bloquear el event loop de asyncio.

Uso en HFT:
- Mover cálculos de indicadores técnicos a threads
- Offload de análisis SMC y fractal
- Procesamiento de grandes volúmenes de datos OHLCV

Ejemplo:
```python
from backend.utils.async_executor import run_cpu_bound

async def handler():
    # Ejecutar en thread pool
    result = await run_cpu_bound(heavy_function, arg1, arg2)
    return result
```
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from functools import partial
from typing import Any, TypeVar

logger = logging.getLogger(__name__)

# ThreadPoolExecutor global para reutilizar threads
# Máximo 4 workers (ajustar según CPU cores disponibles)
_executor: ThreadPoolExecutor | None = None


def get_executor(max_workers: int = 4) -> ThreadPoolExecutor:
    """Obtiene o crea el executor global.

    Args:
        max_workers: Cantidad máxima de threads (default: 4)

    Returns:
        ThreadPoolExecutor instance (singleton)
    """
    global _executor
    if _executor is None:
        _executor = ThreadPoolExecutor(
            max_workers=max_workers, thread_name_prefix="cpu_bound_worker"
        )
        logger.info(f"Created ThreadPoolExecutor with {max_workers} workers")
    return _executor


def shutdown_executor() -> None:
    """Cierra el executor global limpiamente."""
    global _executor
    if _executor is not None:
        _executor.shutdown(wait=True)
        _executor = None
        logger.info("ThreadPoolExecutor shutdown complete")


T = TypeVar("T")


async def run_cpu_bound(
    func: Callable[..., T],
    *args: Any,
    executor: ThreadPoolExecutor | None = None,
    timeout: float | None = None,
) -> T:
    """Ejecuta una función CPU-bound en un thread pool.

    Esta función mueve cálculos pesados fuera del event loop principal,
    permitiendo que asyncio atienda otras requests concurrentes.

    Args:
        func: Función a ejecutar (debe ser thread-safe)
        *args: Argumentos posicionales para la función
        executor: ThreadPoolExecutor opcional (usa el global si None)
        timeout: Timeout en segundos (opcional). Si None, sin timeout.

    Returns:
        Resultado de la función

    Raises:
        TimeoutError: Si se excede el timeout especificado
        Exception: Cualquier error de la función original

    Ejemplo:
    ```python
    # Calcular indicadores técnicos en thread
    result = await run_cpu_bound(
        TechnicalMath.vwap,
        high, low, close, volume,
        timeout=5.0
    )

    # Analizar SMC en thread
    smc_result = await run_cpu_bound(
        SMCEngine().analyze,
        df, ticker, timeframe
    )
    ```
    """
    loop = asyncio.get_event_loop()
    target_executor = executor or get_executor()

    # Crear partial con argumentos
    if args:
        wrapped_func = partial(func, *args)
    else:
        wrapped_func = func

    try:
        # Ejecutar en thread pool
        if timeout is not None:
            # Con timeout
            result = await asyncio.wait_for(
                loop.run_in_executor(target_executor, wrapped_func), timeout=timeout
            )
        else:
            # Sin timeout
            result = await loop.run_in_executor(target_executor, wrapped_func)

        return result

    except TimeoutError:
        logger.error(f"CPU-bound operation timed out after {timeout}s: {func.__name__}")
        raise TimeoutError(f"CPU-bound operation timed out after {timeout}s")
    except Exception as e:
        logger.exception(f"Error in CPU-bound operation {func.__name__}: {e}")
        raise


async def run_multiple_cpu_bound(
    tasks: list[tuple[Callable, tuple]],
    executor: ThreadPoolExecutor | None = None,
    timeout: float | None = None,
) -> list[Any]:
    """Ejecuta múltiples operaciones CPU-bound en paralelo.

    Útil para procesar múltiples símbolos concurrentemente.

    Args:
        tasks: Lista de (func, args) para ejecutar
        executor: ThreadPoolExecutor opcional
        timeout: Timeout por tarea (opcional)

    Returns:
        Lista de resultados en el mismo orden que las tareas

    Ejemplo:
    ```python
    tasks = [
        (SMCEngine().analyze, (df1, "AAPL", "1D")),
        (SMCEngine().analyze, (df2, "GOOGL", "1D")),
        (SMCEngine().analyze, (df3, "MSFT", "1D")),
    ]
    results = await run_multiple_cpu_bound(tasks, timeout=10.0)
    ```
    """
    if not tasks:
        return []

    loop = asyncio.get_event_loop()
    target_executor = executor or get_executor()

    # Crear coroutines para cada tarea
    async def run_task(func: Callable, args: tuple) -> Any:
        return await run_cpu_bound(func, *args, executor=target_executor, timeout=timeout)

    # Ejecutar en paralelo
    coroutines = [run_task(func, args) for func, args in tasks]
    results = await asyncio.gather(*coroutines, return_exceptions=True)

    # Convertir excepciones a errors
    for i, result in enumerate(results):
        if isinstance(result, Exception):
            logger.error(f"Task {i} failed: {result}")

    return results  # type: ignore


# Context manager para shutdown automático
class CpuBoundExecutor:
    """Context manager para el executor de CPU-bound.

    Uso:
    ```python
    async with CpuBoundExecutor(max_workers=4) as executor:
        result = await run_cpu_bound(heavy_func, arg1, arg2, executor=executor)
    ```
    """

    def __init__(self, max_workers: int = 4):
        self.max_workers = max_workers
        self.executor: ThreadPoolExecutor | None = None

    async def __aenter__(self) -> ThreadPoolExecutor:
        self.executor = get_executor(self.max_workers)
        return self.executor

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        if self.executor:
            self.executor.shutdown(wait=True)
        return False
