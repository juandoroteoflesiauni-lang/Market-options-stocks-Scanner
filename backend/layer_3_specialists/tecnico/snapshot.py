"""Módulo de restauración de snapshots para fluejos de datos técnicos.

Este módulo implementa la lógica de reposicionamiento (fast-forward) de flujos de datos
(DataPipes) para restaurar estados capturados previamente.
"""

from __future__ import annotations

from torch.utils.data.datapipes._hook_iterator import _SnapshotState
from torch.utils.data.datapipes.datapipe import IterDataPipe
from torch.utils.data.graph_settings import apply_random_seed


def _simple_graph_snapshot_restoration(datapipe: IterDataPipe, n_iterations: int, rng=None) -> None:
    """
    Restaura un snapshot en el grafo de DataPipes mediante fast-forward de n_iterations.

    Args:
        datapipe: IterDataPipe a avanzar.
        n_iterations: Número de iteraciones para adelantar el flujo.
        rng: Generador de números aleatorios opcional para mantener determinismo.
    """
    if datapipe._snapshot_state == _SnapshotState.Restored:
        raise RuntimeError("La restauración de snapshot ya ha sido aplicada a este grafo.")

    # Aseguramos que el DataPipe esté en su estado inicial antes del fast-forward
    datapipe.reset()

    # MIGRATION: Aplicar semilla aleatoria para consistencia en la restauración
    apply_random_seed(datapipe, rng)

    remainder = n_iterations
    it = iter(datapipe)

    while remainder > 0:
        try:
            next(it)
            remainder -= 1
        except StopIteration as e:
            raise RuntimeError(
                f"La restauración por {n_iterations} iteraciones excede "
                "el número de muestras disponibles en el DataPipe."
            ) from e

    datapipe._fast_forward_iterator = it

    # Marcamos el estado como restaurado para evitar re-restauraciones en la misma sesión
    datapipe._snapshot_state = _SnapshotState.Restored


# ─────────────────────────────────────────────────────────
# MIGRATION AUDIT — SECTOR: TÉCNICO
# Archivo      : snapshot.py
# Sub-capa     : Engine / Utility
# Eliminado    : TODO Caveats, Referencias a ReadingService, Comentarios de debug
# Preservado   : Lógica de restauración de grafo (_SnapshotState)
# Pendientes   : Abstracción de tipos Torch hacia snapshot_models.py
# ─────────────────────────────────────────────────────────
