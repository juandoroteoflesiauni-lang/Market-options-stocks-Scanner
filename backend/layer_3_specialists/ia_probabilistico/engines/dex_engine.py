"""
DEPRECATED — use gamma_exposure_engine.get_gamma_exposure() for new code.
DeltaExposureEngine and DEXResult remain here for backward compatibility
with probabilistic_router.py and any existing consumers.
╔══════════════════════════════════════════════════════════════════════════╗
║              DELTA EXPOSURE ENGINE  (DEX)                               ║
║  Market Microstructure — MM Hedging Pressure Calculator                 ║
║                                                                          ║
║  Supuesto central: los Market Makers (MM) están SIEMPRE en el lado       ║
║  OPUESTO al Open Interest del cliente.                                   ║
║    · Cuando un cliente compra una CALL  → el MM está SHORT la call       ║
║      → el MM necesita COMPRAR acciones para cubrirse (Delta > 0)        ║
║    · Cuando un cliente compra una PUT   → el MM está SHORT la put        ║
║      → el MM necesita VENDER acciones para cubrirse (Delta < 0)         ║
╚══════════════════════════════════════════════════════════════════════════╝

¿Por qué un DEX altamente NEGATIVO actúa como "acelerador" de caídas?
──────────────────────────────────────────────────────────────────────
Cuando el DEX agregado en una zona de precio es muy negativo, significa que
los MM tienen un exceso de delta negativa que necesitan cubrir. A medida que
el spot cae:
  1. El Delta de las puts se vuelve más negativo (más in-the-money).
  2. Los MM deben VENDER MÁS acciones para mantener su cobertura (delta-neutral).
  3. Esa venta forzada empuja el precio aún más abajo → más puts se activan
     → más venta programática → efecto "cascada" o "gamma trap".

En cambio, un DEX altamente POSITIVO actúa como "imán" y frena las caídas:
los MM compran acciones al bajar el spot, amortiguando el movimiento.
"""

from __future__ import annotations

import warnings
from dataclasses import dataclass
from typing import Any

import pandas as pd  # type: ignore[import-not-found, import-untyped]

# ──────────────────────────────────────────────────────────────────────────────
#  Tipos auxiliares
# ──────────────────────────────────────────────────────────────────────────────

REQUIRED_COLUMNS = {"ticker", "strike", "option_type", "delta", "open_interest", "spot_price"}
CALL_TYPE = "call"
PUT_TYPE = "put"


# ──────────────────────────────────────────────────────────────────────────────
#  Dataclass de resultado para mantener la API limpia
# ──────────────────────────────────────────────────────────────────────────────


@dataclass
class DEXResult:
    """Contenedor inmutable con todos los resultados calculados para un ticker."""

    ticker: str
    spot_price: float
    dex_total_nominal: float  # DEX nocional total en USD
    dex_calls: float  # Contribución de las calls
    dex_puts: float  # Contribución de las puts
    dex_by_strike: pd.DataFrame  # DEX desagregado por strike
    dex_profile: pd.DataFrame  # DEX unitario (por 1 % de movimiento)
    dex_as_pct_adtv: float | None = None  # DEX / ADTV (si se provee ADTV)
    adtv: float | None = None

    def summary(self) -> str:
        lines = [
            f"\n{'─'*60}",
            f"  DEX SUMMARY  ·  {self.ticker}  ·  Spot = ${self.spot_price:,.2f}",
            f"{'─'*60}",
            f"  DEX Total Nocional   : ${self.dex_total_nominal:>15,.0f}",
            f"  DEX (Calls)          : ${self.dex_calls:>15,.0f}",
            f"  DEX (Puts)           : ${self.dex_puts:>15,.0f}",
        ]
        if self.dex_as_pct_adtv is not None:
            lines.append(f"  DEX como % del ADTV  : {self.dex_as_pct_adtv:>14.2f}%")
            lines.append(f"  ADTV                 : ${self.adtv:>15,.0f}")
        lines.append(f"{'─'*60}\n")
        return "\n".join(lines)


# ──────────────────────────────────────────────────────────────────────────────
#  Motor principal
# ──────────────────────────────────────────────────────────────────────────────


class DeltaExposureEngine:
    """
    Motor vectorizado para calcular el Delta Exposure (DEX) nocional de opciones,
    modelando la presión de cobertura de los Market Makers.

    Parameters
    ----------
    df : pd.DataFrame
        DataFrame con columnas obligatorias:
        ['ticker', 'strike', 'option_type', 'delta', 'open_interest', 'spot_price']
        - option_type: 'call' o 'put' (case-insensitive)
        - delta: valor en [-1, 1]. Para puts suele ser negativo.
        - open_interest: número de contratos abiertos.
        - spot_price: precio actual del subyacente.

    multiplier : int
        Número de acciones por contrato. Por defecto 100 (estándar USA).
    """

    MULTIPLIER_DEFAULT = 100

    def __init__(self, df: pd.DataFrame, multiplier: int = MULTIPLIER_DEFAULT):
        self._raw = df.copy()
        self.mult = multiplier
        self._df = self._validate_and_prepare(df)

    # ──────────────────────────────────────────────────────────────────────
    #  Validación y normalización del DataFrame
    # ──────────────────────────────────────────────────────────────────────

    def _validate_and_prepare(self, df: pd.DataFrame) -> pd.DataFrame:
        missing = REQUIRED_COLUMNS - set(df.columns)
        if missing:
            raise ValueError(f"Faltan columnas requeridas: {missing}")

        out = df.copy()
        out["option_type"] = out["option_type"].str.lower().str.strip()

        invalid_types = ~out["option_type"].isin([CALL_TYPE, PUT_TYPE])
        if invalid_types.any():
            bad = out.loc[invalid_types, "option_type"].unique().tolist()
            raise ValueError(f"option_type inválido (solo 'call'/'put'): {bad}")

        # Fuerza signos correctos de delta según tipo de opción
        # · Call: delta ∈ (0, 1]   → si viene negativo lo corregimos con advertencia
        # · Put : delta ∈ [-1, 0)  → si viene positivo lo corregimos con advertencia
        call_mask = out["option_type"] == CALL_TYPE
        put_mask = out["option_type"] == PUT_TYPE

        wrong_call = call_mask & (out["delta"] < 0)
        wrong_put = put_mask & (out["delta"] > 0)

        if wrong_call.any():
            warnings.warn(
                f"{wrong_call.sum()} call(s) con delta negativo → se toma valor absoluto.",
                UserWarning,
                stacklevel=3,
            )
            out.loc[wrong_call, "delta"] = out.loc[wrong_call, "delta"].abs()

        if wrong_put.any():
            warnings.warn(
                f"{wrong_put.sum()} put(s) con delta positivo → se invierte el signo.",
                UserWarning,
                stacklevel=3,
            )
            out.loc[wrong_put, "delta"] = -out.loc[wrong_put, "delta"].abs()

        out["open_interest"] = out["open_interest"].clip(lower=0)
        return out

    # ──────────────────────────────────────────────────────────────────────
    #  Cálculo del DEX nocional por fila
    # ──────────────────────────────────────────────────────────────────────

    def _compute_dex_column(self, df: pd.DataFrame) -> pd.Series:
        """
        DEX nocional = Delta_MM × OI × Multiplier × SpotPrice

        La perspectiva del MM es OPUESTA a la del cliente:
          · Call vendida por el MM → Delta_MM = -delta_call  (negativo)
            El MM debe comprar acciones (cobertura larga) → DEX positivo.
            CORRECCIÓN: invertimos el signo del MM para que el DEX refleje
            la presión NETA sobre el mercado:
              DEX_call = +delta_call × OI × mult × spot  (presión compradora)
          · Put  vendida por el MM → Delta_MM = +|delta_put| (positivo)
              DEX_put  = -|delta_put| × OI × mult × spot  (presión vendedora)

        En la convención más extendida (GEX/DEX estilo SpotGamma):
          DEX = delta × OI × 100 × spot  (sin invertir signo)
          donde el signo del delta ya refleja la presión del MM.
        """
        return df["delta"] * df["open_interest"] * self.mult * df["spot_price"]

    # ──────────────────────────────────────────────────────────────────────
    #  API pública
    # ──────────────────────────────────────────────────────────────────────

    def compute(self, ticker: str, adtv: float | None = None) -> DEXResult:
        """
        Calcula el DEX completo para un ticker específico.

        Parameters
        ----------
        ticker : str
            Símbolo del activo.
        adtv : float, optional
            Average Daily Trading Value en USD. Si se provee, se calcula
            DEX_as_Percentage_of_ADTV.

        Returns
        -------
        DEXResult
        """
        mask = self._df["ticker"].str.upper() == ticker.upper()
        if not mask.any():
            raise KeyError(f"Ticker '{ticker}' no encontrado en el DataFrame.")

        sub = self._df[mask].copy()

        # ── 1. DEX por fila ──────────────────────────────────────────────
        #
        # Convención de signo (perspectiva MM):
        #   CALL: el MM está short la call → para cubrir su delta corta, COMPRA acciones.
        #         DEX es NEGATIVO (el MM tiene una posición delta negativa que cubre
        #         comprando → la exposición bruta al mercado es vendedora de gamma).
        #         Según la convención SpotGamma, el DEX de la call es POSITIVO porque
        #         refleja la cantidad de acciones que el MM tiene LARGAS como cobertura.
        #
        #   PUT:  el MM está short la put → para cubrir su delta positiva, VENDE acciones.
        #         DEX es NEGATIVO → presión vendedora real sobre el spot.
        #
        # Usamos la convención práctica:
        #   DEX_raw = delta × OI × 100 × spot
        #   · Calls: delta > 0 → DEX > 0  (cobertura: el MM es largo en acciones)
        #   · Puts:  delta < 0 → DEX < 0  (cobertura: el MM es corto en acciones)
        sub["dex"] = self._compute_dex_column(sub)

        # ── 2. DEX por strike ────────────────────────────────────────────
        dex_by_strike = (
            sub.groupby(["strike", "option_type"])["dex"]
            .sum()
            .reset_index()
            .pivot(index="strike", columns="option_type", values="dex")
            .fillna(0)
            .reset_index()
        )
        # Aseguramos columnas aunque no haya calls o puts en el ticker
        for col in [CALL_TYPE, PUT_TYPE]:
            if col not in dex_by_strike.columns:
                dex_by_strike[col] = 0.0

        dex_by_strike["dex_net"] = dex_by_strike[CALL_TYPE] + dex_by_strike[PUT_TYPE]
        dex_by_strike = dex_by_strike.sort_values("strike").reset_index(drop=True)

        # ── 3. DEX Profile: DEX unitario por 1 % de movimiento del spot ──
        #
        # La idea es: si el spot se mueve un 1 %, ¿cuánto DEX adicional
        # se genera (o se pierde)? Esto requiere el Gamma, que no siempre
        # está disponible. Aproximamos con la sensibilidad del DEX al spot:
        #
        #   ΔDEX / ΔSpot ≈ delta × OI × 100
        #   DEX_Unitario (1%) = (delta × OI × 100) × (spot × 0.01)
        #                     = dex × 0.01
        #
        # Este número indica cuánto cambia el DEX por cada 1 % de movimiento.
        spot_price = sub["spot_price"].iloc[0]
        dex_profile = dex_by_strike.copy()
        dex_profile["dex_per_1pct_move"] = dex_profile["dex_net"] * 0.01
        dex_profile["dex_cumulative"] = dex_profile["dex_net"].cumsum()

        # ── 4. Totales ───────────────────────────────────────────────────
        call_sub = sub[sub["option_type"] == CALL_TYPE]
        put_sub = sub[sub["option_type"] == PUT_TYPE]
        dex_calls = call_sub["dex"].sum()
        dex_puts = put_sub["dex"].sum()
        dex_total = dex_calls + dex_puts

        # ── 5. Normalización por ADTV ────────────────────────────────────
        dex_pct_adtv = None
        if adtv is not None and adtv > 0:
            dex_pct_adtv = (abs(dex_total) / adtv) * 100

        return DEXResult(
            ticker=ticker.upper(),
            spot_price=spot_price,
            dex_total_nominal=dex_total,
            dex_calls=dex_calls,
            dex_puts=dex_puts,
            dex_by_strike=dex_by_strike,
            dex_profile=dex_profile,
            dex_as_pct_adtv=dex_pct_adtv,
            adtv=adtv,
        )

    def compute_all_tickers(self, adtv_map: dict[str, Any] | None = None) -> dict[str, DEXResult]:
        """
        Calcula el DEX para todos los tickers presentes en el DataFrame.

        Parameters
        ----------
        adtv_map : dict, optional
            Diccionario {ticker: adtv_value} con el ADTV de cada ticker.
        """
        adtv_map = adtv_map or {}
        tickers = self._df["ticker"].str.upper().unique()
        return {t: self.compute(t, adtv=adtv_map.get(t)) for t in tickers}

    # ──────────────────────────────────────────────────────────────────────
    #  Utilidades de inspección
    # ──────────────────────────────────────────────────────────────────────

    def top_strikes(self, ticker: str, n: int = 5, by: str = "dex_net") -> pd.DataFrame:
        """
        Devuelve los N strikes con mayor DEX neto absoluto.
        `by` puede ser 'dex_net', 'call', 'put'.
        """
        result = self.compute(ticker)
        df = result.dex_by_strike.copy()
        df["abs_dex_net"] = df["dex_net"].abs()
        return df.nlargest(n, "abs_dex_net").reset_index(drop=True)

    def net_gamma_flip_level(self, ticker: str) -> float | None:
        """
        Nivel de strike donde el DEX acumulado cambia de signo ('gamma flip').
        Por encima de este nivel los MM son netos compradores (amortiguador),
        por debajo son netos vendedores (acelerador).
        """
        result = self.compute(ticker)
        df = result.dex_profile.sort_values("strike")
        # Buscamos el strike donde el acumulado cruza cero
        cross = df[df["dex_cumulative"].shift(1) * df["dex_cumulative"] < 0]
        if cross.empty:
            return None
        return float(cross.iloc[0]["strike"])
