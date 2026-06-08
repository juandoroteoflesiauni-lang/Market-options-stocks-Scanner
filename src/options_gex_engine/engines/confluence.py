"""Motor de Confluencia SMC-Opciones — Sector Opciones/GEX.

Valida puntos de interés (POIs) estructurales (SMC) mediante flujos mecánicos
de opciones (GEX Walls, ZGL, Max Pain).
"""

from __future__ import annotations

from ..domain.confluence_models import GEXLevels, OptionsSMCConfluenceResult


class OptionsConfluenceEngine:
    """Motor Stateless para sincronizar señales estructurales con flujos mecánicos."""

    @staticmethod
    def validate(
        smc: object,
        gex_levels: GEXLevels | None,
        spot: float = 0.0,
    ) -> OptionsSMCConfluenceResult:
        """
        Ejecuta el mapeo de confluencia entre los motores técnico y de derivados.

        Reglas Institucionales:
        1. OB x Gamma Wall: OB Alcista + Put Wall (Soporte) cerca del precio.
        2. Sweep x Flip: Liquidez barrida + Precio sobre el ZGL (Estabilización).
        3. FVG x Magnet: FVG activo cerca del Max Pain (Magnet de volatilidad).
        """
        if spot <= 0 or gex_levels is None:
            return OptionsSMCConfluenceResult()

        # 1. OB x Gamma Wall (Validación de Soporte)
        ob_valid = False
        put_wall = getattr(gex_levels, "put_wall", None)
        if (
            hasattr(smc, "bias")
            and getattr(smc, "bias", None) == "LONG"
            and put_wall is not None
            and abs(spot - put_wall) / spot < 0.02
        ):
            ob_valid = True

        # 2. Sweep x Flip (Confirmación de Reversión)
        sweep_confirmed = False
        has_liq_sweep = bool(getattr(smc, "liquidity_sweeps", None)) or (
            getattr(smc, "active_ict_model", None) == "LIQUIDITY_SWEEP"
        )
        zgl = getattr(gex_levels, "zero_gamma_level", None)
        if has_liq_sweep and zgl is not None and spot > zgl:
            sweep_confirmed = True

        # 3. FVG x Magnet (Atracción de Rebalanceo)
        magnet_active = False
        fvg_zones = getattr(smc, "fvg_zones", None)
        fvg_list = fvg_zones if isinstance(fvg_zones, list) else []
        n_fvg = int(getattr(smc, "fvg_count_active", 0) or len(fvg_list))
        if n_fvg > 0:
            target = getattr(gex_levels, "volatility_magnet", None) or getattr(gex_levels, "max_pain", None)
            if target is not None and abs(spot - target) / spot < 0.03:
                magnet_active = True

        # Cálculo de Score de Confluencia (Ponderación 40/40/20)
        score = (0.4 * float(ob_valid)) + (0.4 * float(sweep_confirmed)) + (0.2 * float(magnet_active))

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
            confluence_score=score,
            summary=" | ".join(summary_parts) if summary_parts else "NO CONFLUENCE"
        )
