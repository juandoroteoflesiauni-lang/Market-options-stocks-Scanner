"""Utilidades para backend - Async executors, memory pool, helpers, etc."""

from .async_executor import (
    CpuBoundExecutor,
    get_executor,
    run_cpu_bound,
    run_multiple_cpu_bound,
    shutdown_executor,
)
from .numpy_pool import (
    NumpyMemoryPool,
    TechnicalArraysContext,
    allocate_technical_arrays,
    get_technical_pool,
    release_technical_arrays,
    reset_technical_pool,
)

__all__ = [
    # Async Executor
    "run_cpu_bound",
    "run_multiple_cpu_bound",
    "get_executor",
    "shutdown_executor",
    "CpuBoundExecutor",
    # Memory Pool
    "NumpyMemoryPool",
    "get_technical_pool",
    "reset_technical_pool",
    "allocate_technical_arrays",
    "release_technical_arrays",
    "TechnicalArraysContext",
]
