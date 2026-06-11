"""Memory Pool para arrays NumPy - Optimización de memoria en HFT.

Este módulo implementa un pool de memoria para reutilizar arrays NumPy,
reduciendo la presión sobre el garbage collector y mejorando la latencia
en sistemas de alta frecuencia.

Problema que resuelve:
- En HFT, se crean/destruyen miles de arrays por segundo
- El garbage collector de Python causa picos de latencia
- La fragmentación de memoria degrada performance con el tiempo

Solución:
- Pool de arrays pre-asignados
- Reutilización de memoria (sin alloc/dealloc)
- Reducción de GC pressure
"""

from __future__ import annotations

import logging
from collections import deque

import numpy as np

logger = logging.getLogger(__name__)


class NumpyMemoryPool:
    """Pool de memoria para arrays NumPy.

    Reutiliza arrays pre-asignados para evitar alloc/dealloc constante.
    Ideal para cálculos repetidos de indicadores técnicos en HFT.

    Atributos:
        max_size: Máxima cantidad de arrays por tipo (shape, dtype)
        stats: Estadísticas de uso del pool

    Ejemplo:
    ```python
    pool = NumpyMemoryPool(max_size=100)

    # Adquirir array (reutiliza si hay disponible)
    arr = pool.acquire(shape=(320,), dtype=np.float64)

    # Usar array
    arr[:] = calculation_result

    # Liberar (en realidad lo devuelve al pool para reuso)
    pool.release(arr)

    # Estadísticas
    print(pool.stats)  # {'hits': 95, 'misses': 5, 'allocs': 5}
    ```
    """

    def __init__(self, max_size: int = 100) -> None:
        """Inicializa el pool de memoria.

        Args:
            max_size: Máxima cantidad de arrays por tipo en el pool.
                     Valores altos = más reutilización, más memoria.
                     Valores bajos = menos memoria, menos reutilización.
                     Default: 100 (balanceado para HFT)
        """
        self.max_size = max_size

        # Pool de arrays: clave=(shape, dtype_str) -> deque de arrays
        self._pools: dict[tuple, deque] = {}

        # Contadores de uso
        self._stats = {
            "hits": 0,  # Arrays reutilizados del pool
            "misses": 0,  # Arrays nuevos (pool vacío)
            "allocs": 0,  # Total allocaciones
            "reuses": 0,  # Total reutilizaciones
            "releases": 0,  # Total releases
        }

        logger.info(f"Created NumpyMemoryPool with max_size={max_size}")

    @property
    def stats(self) -> dict[str, int]:
        """Estadísticas de uso del pool."""
        return self._stats.copy()

    @property
    def hit_rate(self) -> float:
        """Porcentaje de aciertos (reutilización)."""
        total = self._stats["hits"] + self._stats["misses"]
        if total == 0:
            return 0.0
        return self._stats["hits"] / total

    def _make_key(self, shape: tuple, dtype: np.dtype) -> tuple:
        """Crea clave hashable para el pool."""
        return (shape, str(dtype))

    def acquire(
        self,
        shape: tuple[int, ...],
        dtype: np.dtype = np.float64,
        zeros: bool = False,
    ) -> np.ndarray:
        """Adquiere un array del pool (o crea uno nuevo).

        Args:
            shape: Forma del array (ej: (320,) o (100, 10))
            dtype: Tipo de dato (default: np.float64)
            zeros: Si True, inicializa en ceros. Si False, datos arbitrarios.

        Returns:
            np.ndarray listo para usar

        Ejemplo:
        ```python
        arr = pool.acquire((320,), np.float64)
        arr[:] = calculate_vwap(h, lo, c, v)
        ```
        """
        key = self._make_key(shape, dtype)

        # Buscar en el pool
        if self._pools.get(key):
            arr = self._pools[key].pop()
            self._stats["hits"] += 1
            self._stats["reuses"] += 1

            if zeros:
                arr[:] = 0.0

            return arr

        # Pool vacío, crear nuevo array
        self._stats["misses"] += 1
        self._stats["allocs"] += 1

        if zeros:
            arr = np.zeros(shape, dtype=dtype)
        else:
            arr = np.empty(shape, dtype=dtype)

        logger.debug(f"Allocated new array: shape={shape}, dtype={dtype}")
        return arr

    def release(self, arr: np.ndarray, shape: tuple | None = None) -> None:
        """Devuelve un array al pool para reutilización.

        Args:
            arr: Array a liberar
            shape: Shape opcional si difiere de arr.shape

        Nota:
            El array se marca como disponible para futuros acquires.
            No usar el array después de liberarlo (puede ser reutilizado).

        Ejemplo:
        ```python
        arr = pool.acquire((320,), np.float64)
        result = calculate(arr)
        pool.release(arr)  # Devolver al pool
        ```
        """
        key = self._make_key(shape or arr.shape, arr.dtype)

        if key not in self._pools:
            self._pools[key] = deque(maxlen=self.max_size)

        pool_queue = self._pools[key]

        # Solo agregar si el pool no está lleno
        if len(pool_queue) < self.max_size:
            pool_queue.append(arr)
            self._stats["releases"] += 1
            logger.debug(f"Released array to pool: shape={arr.shape}, dtype={arr.dtype}")
        else:
            # Pool lleno, el GC limpiará el array
            logger.debug("Pool full, array will be garbage collected")

    def acquire_batch(
        self,
        shapes: list[tuple[int, ...]],
        dtype: np.dtype = np.float64,
        zeros: bool = False,
    ) -> list[np.ndarray]:
        """Adquiere múltiples arrays del pool.

        Args:
            shapes: Lista de shapes para adquirir
            dtype: Tipo de dato común
            zeros: Inicializar en ceros

        Returns:
            Lista de arrays

        Ejemplo:
        ```python
        arrays = pool.acquire_batch(
            [(320,), (320,), (320,)],  # 3 arrays para h, lo, c
            dtype=np.float64
        )
        h, lo, c = arrays
        ```
        """
        return [self.acquire(shape, dtype, zeros) for shape in shapes]

    def release_batch(self, arrays: list[np.ndarray]) -> None:
        """Devuelve múltiples arrays al pool.

        Args:
            arrays: Lista de arrays a liberar

        Ejemplo:
        ```python
        pool.release_batch([h, lo, c, v])
        ```
        """
        for arr in arrays:
            self.release(arr)

    def clear(self) -> None:
        """Limpia todos los arrays del pool.

        Útil para testing o cuando se sabe que no se necesitarán más.
        """
        self._pools.clear()
        logger.info("Cleared NumpyMemoryPool")

    def __len__(self) -> int:
        """Cantidad total de arrays en el pool."""
        return sum(len(pool) for pool in self._pools.values())

    def __repr__(self) -> str:
        return (
            f"NumpyMemoryPool(max_size={self.max_size}, "
            f"total_arrays={len(self)}, "
            f"hit_rate={self.hit_rate:.2%})"
        )


# ─────────────────────────────────────────────────────────────────────────────
# Pool Global para Indicadores Técnicos
# ─────────────────────────────────────────────────────────────────────────────

# Pool optimizado para cálculos técnicos típicos (320 días)
_technical_pool: NumpyMemoryPool | None = None


def get_technical_pool(max_size: int = 100) -> NumpyMemoryPool:
    """Obtiene o crea el pool global para indicadores técnicos.

    Args:
        max_size: Máximo tamaño del pool (default: 100)

    Returns:
        NumpyMemoryPool global
    """
    global _technical_pool
    if _technical_pool is None:
        _technical_pool = NumpyMemoryPool(max_size=max_size)
    return _technical_pool


def reset_technical_pool() -> None:
    """Resetea el pool global (útil para testing)."""
    global _technical_pool
    if _technical_pool is not None:
        _technical_pool.clear()
    _technical_pool = None


# ─────────────────────────────────────────────────────────────────────────────
# Helpers para Cálculos Técnicos
# ─────────────────────────────────────────────────────────────────────────────


def allocate_technical_arrays(
    bars: int = 320,
    dtype: np.dtype = np.float64,
    pool: NumpyMemoryPool | None = None,
) -> dict[str, np.ndarray]:
    """Asigna arrays para cálculos técnicos usando el pool.

    Args:
        bars: Cantidad de barras (ej: 320 días)
        dtype: Tipo de dato (default: float64)
        pool: Pool opcional (default: global)

    Returns:
        Diccionario con arrays: h, lo, c, v, vwap, sma20, sma50, sma200, ema21, avwap

    Ejemplo:
    ```python
    arrays = allocate_technical_arrays(320)

    # Llenar con datos reales
    arrays['h'][:] = high_prices
    arrays['lo'][:] = low_prices
    arrays['c'][:] = close_prices
    arrays['v'][:] = volume

    # Calcular
    arrays['vwap'][:] = TechnicalMath.vwap(
        arrays['h'], arrays['lo'], arrays['c'], arrays['v']
    )

    # Liberar
    release_technical_arrays(arrays)
    ```
    """
    target_pool = pool or get_technical_pool()

    return {
        "h": target_pool.acquire((bars,), dtype),
        "lo": target_pool.acquire((bars,), dtype),
        "c": target_pool.acquire((bars,), dtype),
        "v": target_pool.acquire((bars,), dtype),
        "vwap": target_pool.acquire((bars,), dtype),
        "sma20": target_pool.acquire((bars,), dtype),
        "sma50": target_pool.acquire((bars,), dtype),
        "sma200": target_pool.acquire((bars,), dtype),
        "ema21": target_pool.acquire((bars,), dtype),
        "avwap": target_pool.acquire((bars,), dtype),
    }


def release_technical_arrays(
    arrays: dict[str, np.ndarray],
    pool: NumpyMemoryPool | None = None,
) -> None:
    """Devuelve arrays técnicos al pool.

    Args:
        arrays: Diccionario de arrays a liberar
        pool: Pool opcional (default: global)
    """
    target_pool = pool or get_technical_pool()
    for arr in arrays.values():
        target_pool.release(arr)


# Context manager para uso seguro
class TechnicalArraysContext:
    """Context manager para arrays técnicos con release automático.

    Ejemplo:
    ```python
    with TechnicalArraysContext(320) as arrays:
        arrays['h'][:] = high_prices
        result = calculate_indicators(arrays)
    # Arrays liberados automáticamente
    ```
    """

    def __init__(
        self,
        bars: int = 320,
        dtype: np.dtype = np.float64,
        pool: NumpyMemoryPool | None = None,
    ):
        self.bars = bars
        self.dtype = dtype
        self.pool = pool or get_technical_pool()
        self.arrays: dict[str, np.ndarray] | None = None

    def __enter__(self) -> dict[str, np.ndarray]:
        self.arrays = allocate_technical_arrays(self.bars, self.dtype, self.pool)
        return self.arrays

    def __exit__(self, exc_type, exc_val, exc_tb):
        if self.arrays:
            release_technical_arrays(self.arrays, self.pool)
        return False

    async def __aenter__(self) -> dict[str, np.ndarray]:
        return self.__enter__()

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        self.__exit__(exc_type, exc_val, exc_tb)
