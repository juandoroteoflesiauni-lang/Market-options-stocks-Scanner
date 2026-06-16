from __future__ import annotations
from typing import Any
"""API endpoints para gestión dinámica de pesos estratégicos del funnel.

Permite consultar, actualizar y resetear los StrategyWeights en caliente.
Todos los endpoints afectan el comportamiento del pipeline en la siguiente
iteración del funnel (no requiere reinicio).
"""



from fastapi import APIRouter, HTTPException

from backend.config.phase_thresholds import get_active_weights, reset_to_defaults, update_weight
from backend.models.strategy_weights import StrategyWeights

router = APIRouter(prefix="/api/strategy", tags=["strategy"])


@router.get("/weights")
async def get_weights() -> dict[str, Any]:
    """Retorna la configuración completa de pesos activos."""
    w = get_active_weights()
    return {
        "regime_adaptation_enabled": w.regime_adaptation_enabled,
        "weights": w.to_flat_dict(),
    }


@router.patch("/weights/{path:path}")
async def patch_weight(path: str, value: float) -> dict[str, Any]:
    """Actualiza un peso individual por ruta punto-separada.

    Ejemplos:
      PATCH /api/strategy/weights/phase_c.gex_score  (body: {"value": 0.25})
      PATCH /api/strategy/weights/phase_b.ofi_weight  (body: {"value": 0.50})
      PATCH /api/strategy/weights/phase_d.entry_momentum_threshold  (body: {"value": 0.005})
    """
    ok = update_weight(path, value)
    if not ok:
        raise HTTPException(
            status_code=404,
            detail=f"Weight path '{path}' not found. "
            f"Use GET /api/strategy/weights to see all valid paths.",
        )
    return {"updated": path, "value": value, "active_weights": get_active_weights().to_flat_dict()}


@router.put("/weights", status_code=200)
async def put_weights(payload: dict[str, Any]) -> dict[str, Any]:
    """Reemplaza la configuración completa de pesos (merge parcial).

    Envía solo los campos que deseas modificar en formato anidado:
      {
        "phase_c": {
          "engine_weights": {"gex_score": 0.25, "gamma_flip": 0.10},
          "contract_filters": {"min_volume": 200, "min_composite_score": 50}
        },
        "phase_d": {"entry_momentum_threshold": 0.005}
      }
    """
    try:
        current = get_active_weights()
        merged = _deep_merge(current, payload)
        new_weights = StrategyWeights(**merged)
        # Persistir vía phase_thresholds
        from backend.config.phase_thresholds import set_active_weights as _set

        _set(new_weights)
        return {"status": "ok", "active_weights": get_active_weights().to_flat_dict()}
    except Exception as e:
        raise HTTPException(status_code=422, detail=str(e)) from e


@router.post("/weights/reset")
async def reset_weights() -> dict[str, Any]:
    """Resetea todos los pesos a los valores por defecto."""
    reset_to_defaults()
    return {"status": "reset", "active_weights": get_active_weights().to_flat_dict()}


def _deep_merge(current: StrategyWeights, updates: dict[str, Any]) -> dict[str, Any]:
    """Merge recursivo de updates en la estructura StrategyWeights."""
    import copy

    base = copy.deepcopy(current.model_dump())

    def _merge_dict(d: dict[str, Any], u: dict[str, Any]) -> dict[str, Any]:
        for key, val in u.items():
            if key in d and isinstance(d[key], dict) and isinstance(val, dict):
                _merge_dict(d[key], val)
            else:
                d[key] = val
        return d

    return _merge_dict(base, updates)
