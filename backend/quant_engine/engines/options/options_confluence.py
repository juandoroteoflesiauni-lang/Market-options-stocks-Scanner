"""Motor de Confluencia SMC-Opciones — Sector Opciones/GEX.

Valida puntos de interés (POIs) estructurales (SMC) mediante flujos mecánicos
de opciones (GEX Walls, ZGL, Max Pain).
"""

from __future__ import annotations

from .confluence_models import GEXLevels, OptionsSMCConfluenceResult


class OptionsConfluenceEngine:
    """Motor Stateless para sincronizar señales estructurales con flujos mecánicos."""

    @staticmethod
    def validate(
        smc: object,
        gex_levels: GEXLevels,
        spot: float = 0.0,
    ) -> OptionsSMCConfluenceResult:
        """
        Ejecuta el mapeo de confluencia entre los motores técnico y de derivados.

        Reglas Institucionales:
        1. OB x Gamma Wall: OB Alcista + Put Wall (Soporte) cerca del precio.
        2. Sweep x Flip: Liquidez barrida + Precio sobre el ZGL (Estabilización).
        3. FVG x Magnet: FVG activo cerca del Max Pain (Magnet de volatilidad).
        """
        if spot <= 0:
            return OptionsSMCConfluenceResult()

        # 1. OB x Gamma Wall (Validación de Soporte)
        ob_valid = False
        # Verificamos si SMC tiene sesgo alcista y el Put Wall actúa como piso
        if (
            hasattr(smc, "bias")
            and smc.bias == "LONG"
            and abs(spot - gex_levels.put_wall) / spot < 0.02
        ):
            ob_valid = True

        # 2. Sweep x Flip (Confirmación de Reversión)
        sweep_confirmed = False
        # Barrido de liquidez: lista en SMCResult o alias legacy ``active_ict_model``
        has_liq_sweep = bool(getattr(smc, "liquidity_sweeps", None)) or (
            getattr(smc, "active_ict_model", None) == "LIQUIDITY_SWEEP"
        )
        if has_liq_sweep and spot > gex_levels.zero_gamma_level:
            sweep_confirmed = True

        # 3. FVG x Magnet (Atracción de Rebalanceo)
        magnet_active = False
        # FVG activos: conteo explícito o propiedad ``fvg_count_active`` en SMCResult
        n_fvg = int(getattr(smc, "fvg_count_active", 0) or len(getattr(smc, "fvg_zones", []) or []))
        if n_fvg > 0:
            target = gex_levels.volatility_magnet or gex_levels.max_pain
            if abs(spot - target) / spot < 0.03:
                magnet_active = True

        # Cálculo de Score de Confluencia (Ponderación 40/40/20)
        score = (
            (0.4 * float(ob_valid)) + (0.4 * float(sweep_confirmed)) + (0.2 * float(magnet_active))
        )

        summary_parts: list[str] = []
        if ob_valid:
            summary_parts.append("OB-WALL_SUPPORT")
        if sweep_confirmed:
            summary_parts.append("SWEEP-FLIP_RECOVERY")
        if magnet_active:
            summary_parts.append("FVG-MAGNET_ATTR")

        return OptionsSMCConfluenceResult(
            is_ob_validated=ob_valid,
            is_sweep_confirmed=sweep_confirmed,
            is_magnet_active=magnet_active,
            confluence_score=round(score, 2),
            summary=" | ".join(summary_parts) if summary_parts else "NO CONFLUENCE",
        )


# ─────────────────────────────────────────────────────────────
# MIGRATION AUDIT — SECTOR: OPCIONES
# Archivo      : options_confluence.py
# Sub-capa     : Engine (Confluence)
# Eliminado    : Rutas relativas legacy (quantumbeta.domain).
# Preservado   : Lógica de validación cruzada OB/Wall, Sweep/ZGL, FVG/Magnet.
# Dependencias : Requiere SMCResult del especialista técnico.
# ─────────────────────────────────────────────────────────────
