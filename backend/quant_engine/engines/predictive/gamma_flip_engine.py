"""
DEPRECATED — use gamma_exposure_engine.get_gamma_exposure() for new code.
GammaFlipEngine and related helpers remain here for backward compatibility
with probabilistic_router.py and any existing consumers.
=============================================================================
GammaFlipEngine — Market Microstructure · Dealer Gamma Exposure Analysis
=============================================================================

CONCEPTUAL FOUNDATION
─────────────────────
Market Makers (MM) operan en una posición estructuralmente corta de opciones:
venden volatilidad implícita a los participantes minoristas e institucionales
que buscan cobertura o especulación direccional.

GAMMA POSITIVA (MM net long gamma):
  • El MM recibió más calls que puts (en OI ponderado por gamma).
  • Para cubrirse en delta: compra cuando el precio BAJA, vende cuando SUBE.
  • Acción: el MM es un AMORTIGUADOR del mercado → precio en rango, baja vol.
  • Los movimientos del spot se atenúan, porque el hedging del MM va en contra
    de la dirección del movimiento (comportamiento "contrarian").

GAMMA NEGATIVA (MM net short gamma):
  • El MM tiene más puts vendidos al descubierto que calls.
  • Para cubrirse en delta: VENDE cuando el precio BAJA, compra cuando SUBE.
  • Acción: el MM es un AMPLIFICADOR del mercado → tendencias violentas, alta vol.
  • El hedging va EN LA MISMA DIRECCIÓN del movimiento → feedback loop positivo.

GAMMA FLIP POINT:
  El precio exacto donde la Gamma Neta del sistema = 0.
  Por debajo de este nivel: el mercado pasa de amortiguado a acelerado.
  Ruptura del flip → el hedging del MM retroalimenta la caída → "air pocket".
=============================================================================
"""

from __future__ import annotations

import math
import warnings
from collections.abc import Sequence
from typing import Any, cast

import numpy as np
import numpy.typing as npt
import pandas as pd  # type: ignore[import-untyped]
from scipy.optimize import brentq  # type: ignore[import-untyped]

warnings.filterwarnings("ignore")

FloatArray = npt.NDArray[np.float64]


def _std_norm_pdf(x: float) -> float:
    """Standard normal PDF (scalar), avoids untyped scipy.stats."""
    return math.exp(-0.5 * x * x) / math.sqrt(2.0 * math.pi)


# ─────────────────────────────────────────────────────────────────────────────
#  HELPERS: Black-Scholes Greeks
# ─────────────────────────────────────────────────────────────────────────────


def bs_gamma(S: float, K: float, T: float, r: float, sigma: float) -> float:
    """
    Calcula la Gamma de Black-Scholes para call y put (idéntica por paridad).

    Gamma = N'(d1) / (S · σ · √T)

    donde N'(·) es la densidad normal estándar.
    Si T ≤ 0 o sigma ≤ 0, devuelve 0 para evitar divisiones por cero.
    """
    if T <= 0 or sigma <= 0 or S <= 0:
        return 0.0
    d1 = (math.log(S / K) + (r + 0.5 * sigma**2) * T) / (sigma * math.sqrt(T))
    return float(_std_norm_pdf(d1) / (S * sigma * math.sqrt(T)))


def recalculate_gamma(
    df: pd.DataFrame, spot: float, T: float = 30 / 365, r: float = 0.05, sigma: float = 0.20
) -> pd.DataFrame:
    """
    Re-calcula la gamma de cada strike usando BS dado un spot hipotético.
    Útil para el barrido de precios en la Gamma Profile Curve.
    """
    df = df.copy()
    df["gamma"] = df["strike"].apply(lambda K: bs_gamma(spot, K, T, r, sigma))
    return df


# ─────────────────────────────────────────────────────────────────────────────
#  CLASE PRINCIPAL
# ─────────────────────────────────────────────────────────────────────────────


class GammaFlipEngine:
    """
    Motor de análisis de exposición gamma de Market Makers.

    Parámetros
    ──────────
    df          : DataFrame con columnas:
                    - strike        (float) precio de ejercicio
                    - option_type   (str)   'call' | 'put'
                    - gamma         (float) gamma por contrato (BS)
                    - open_interest (int)   contratos abiertos
                    - current_spot  (float) spot actual del subyacente
    contract_size : acciones/unidades por contrato (default 100)
    T             : tiempo a vencimiento en años para re-pricing (default 30/365)
    r             : tasa libre de riesgo (default 0.05)
    sigma         : volatilidad implícita para re-pricing (default 0.20)
    range_pct     : rango del barrido de precios como fracción del spot (default 0.15)
    n_points      : puntos en la curva de gamma (default 500)
    """

    def __init__(
        self,
        df: pd.DataFrame,
        contract_size: int = 100,
        T: float = 30 / 365,
        r: float = 0.05,
        sigma: float = 0.20,
        range_pct: float = 0.15,
        n_points: int = 500,
    ):
        self._validate_input(df)
        self.df = df.copy()
        self.contract_size = contract_size
        self.T = T
        self.r = r
        self.sigma = sigma
        self.range_pct = range_pct
        self.n_points = n_points

        # Spot leído del DataFrame (puede ser columna o campo único)
        self.spot = float(df["current_spot"].iloc[0])

        # Rango de precios para el barrido
        self.price_range = np.linspace(
            self.spot * (1 - range_pct),
            self.spot * (1 + range_pct),
            n_points,
        )

        # Resultados cacheados
        self._gamma_profile: FloatArray | None = None
        self._flip_point: float | None = None

    # ─────────────────────────────────────────────────────────────────────────
    #  VALIDACIÓN
    # ─────────────────────────────────────────────────────────────────────────

    @staticmethod
    def _validate_input(df: pd.DataFrame) -> None:
        required = {"strike", "option_type", "gamma", "open_interest", "current_spot"}
        missing = required - set(df.columns)
        if missing:
            raise ValueError(f"Columnas faltantes en el DataFrame: {missing}")
        valid_types = {"call", "put"}
        found = set(df["option_type"].str.lower().unique())
        if not found.issubset(valid_types):
            raise ValueError(f"option_type debe ser 'call' o 'put'. Encontrado: {found}")

    # ─────────────────────────────────────────────────────────────────────────
    #  CÁLCULO DE GAMMA NETA PARA UN NIVEL DE PRECIO
    # ─────────────────────────────────────────────────────────────────────────

    def _net_gamma_at_price(self, price: float, df: pd.DataFrame | None = None) -> float:
        """
        Gamma Neta del sistema = Σ(Call_γ · Call_OI) − Σ(Put_γ · Put_OI)
        escalada por contract_size.

        La lógica MM-short implica que:
          - MM vendió calls → delta hedge: corto subyacente → gamma positiva desde la perspectiva del sistema
          - MM vendió puts  → delta hedge: largo subyacente → gamma negativa desde la perspectiva del sistema

        Net Gamma > 0 → MM amortigua; Net Gamma < 0 → MM amplifica.
        """
        if df is None:
            df = self.df

        # Re-calcula gamma de cada strike dado el precio hipotético
        df_repriced = recalculate_gamma(df, price, self.T, self.r, self.sigma)

        calls = df_repriced[df_repriced["option_type"].str.lower() == "call"]
        puts = df_repriced[df_repriced["option_type"].str.lower() == "put"]

        call_gamma_total = (calls["gamma"] * calls["open_interest"]).sum()
        put_gamma_total = (puts["gamma"] * puts["open_interest"]).sum()

        return float((call_gamma_total - put_gamma_total) * self.contract_size)

    # ─────────────────────────────────────────────────────────────────────────
    #  GAMMA PROFILE CURVE
    # ─────────────────────────────────────────────────────────────────────────

    def gamma_profile(self, df: pd.DataFrame | None = None) -> tuple[FloatArray, FloatArray]:
        """
        Genera la curva de Gamma Neta para cada precio en price_range.

        Retorna
        ───────
        prices  : array de niveles de precio
        gammas  : array de gamma neta en cada nivel
        """
        gammas = np.asarray(
            [self._net_gamma_at_price(p, df) for p in self.price_range],
            dtype=np.float64,
        )
        if df is None:
            self._gamma_profile = gammas
        prices_arr = np.asarray(self.price_range, dtype=np.float64)
        return prices_arr, gammas

    # ─────────────────────────────────────────────────────────────────────────
    #  BÚSQUEDA DEL FLIP POINT (Zero-Crossing)
    # ─────────────────────────────────────────────────────────────────────────

    def find_flip_point(self, df: pd.DataFrame | None = None) -> float | None:
        """
        Localiza el precio exacto donde la Gamma Neta cruza cero usando:
          1. Brentq (raíz exacta) si detecta un cambio de signo en el intervalo.
          2. Interpolación lineal como fallback.

        Retorna None si no se detecta cruce en el rango configurado.
        """
        prices, gammas = self.gamma_profile(df)

        # Busca cambios de signo
        sign_changes = np.where(np.diff(np.sign(gammas)))[0]

        if len(sign_changes) == 0:
            warnings.warn(
                "No zero-crossing for net gamma in the scanned price range; "
                "try increasing range_pct or check chain data.",
                UserWarning,
                stacklevel=2,
            )
            return None

        # Tomamos el primer cruce (el más relevante en la práctica)
        idx = sign_changes[0]
        p_lo, p_hi = prices[idx], prices[idx + 1]

        try:
            # Brentq requiere que f(a) y f(b) tengan signos opuestos
            flip = brentq(
                lambda p: self._net_gamma_at_price(p, df), p_lo, p_hi, xtol=1e-6, maxiter=200
            )
        except ValueError:
            # Fallback: interpolación lineal
            g_lo, g_hi = gammas[idx], gammas[idx + 1]
            flip = p_lo - g_lo * (p_hi - p_lo) / (g_hi - g_lo)

        if df is None:
            self._flip_point = float(flip)

        return float(flip)

    # ─────────────────────────────────────────────────────────────────────────
    #  VOLATILITY SWITCH ALERT
    # ─────────────────────────────────────────────────────────────────────────

    def volatility_regime(self) -> dict[str, Any]:
        """
        Determina si el precio actual está en Zona de Gamma Positiva o Negativa.

        Retorna un diccionario con:
          - regime        : 'GAMMA_POSITIVE' | 'GAMMA_NEGATIVE' | 'AT_FLIP'
          - current_gamma : gamma neta al spot actual
          - flip_point    : nivel del flip (o None)
          - distance_pct  : distancia del spot al flip en %
          - interpretation: texto descriptivo
        """
        flip = self._flip_point if self._flip_point is not None else self.find_flip_point()
        current_gamma = self._net_gamma_at_price(self.spot)

        tolerance = self.spot * 0.002  # ±0.2% = "en el flip"

        if flip is not None and abs(self.spot - flip) < tolerance:
            regime = "AT_FLIP"
            interpretation = (
                "🔴 ALERTA CRÍTICA: El precio está en el Gamma Flip Point. "
                "Movimientos explosivos probables en cualquier dirección."
            )
        elif current_gamma > 0:
            regime = "GAMMA_POSITIVE"
            interpretation = (
                "🟢 ZONA DE BAJA VOLATILIDAD: MM actúa como amortiguador. "
                "El hedging del dealer compra caídas y vende alzas → precio en rango."
            )
        else:
            regime = "GAMMA_NEGATIVE"
            interpretation = (
                "🔴 ZONA DE ALTA VOLATILIDAD: MM actúa como amplificador. "
                "El hedging del dealer vende en caídas y compra en alzas → feedback loop."
            )

        distance_pct = (self.spot - flip) / flip * 100 if flip is not None else None

        return {
            "regime": regime,
            "current_gamma": current_gamma,
            "flip_point": flip,
            "distance_pct": distance_pct,
            "interpretation": interpretation,
        }

    # ─────────────────────────────────────────────────────────────────────────
    #  SENSITIVITY ANALYSIS: Impacto de un shock en el OI de Puts
    # ─────────────────────────────────────────────────────────────────────────

    def sensitivity_put_oi(self, shock_pct: float = 0.10) -> dict[str, Any]:
        """
        Analiza cuánto se desplaza el Flip Point si el OI de Puts
        aumenta un `shock_pct` (default 10%).

        Razonamiento:
          Más OI en puts → más gamma negativa de puts → la curva se desplaza hacia abajo
          → el cruce de cero ocurre a un precio más ALTO (el mercado se vuelve
            estructuralmente más frágil a niveles de precio superiores).

        Retorna
        ───────
        dict con flip_original, flip_shocked, desplazamiento absoluto y porcentual.
        """
        # Flip sin shock
        flip_original = self._flip_point if self._flip_point is not None else self.find_flip_point()

        # DataFrame con puts escalados
        df_shocked = self.df.copy()
        mask_puts = df_shocked["option_type"].str.lower() == "put"
        df_shocked.loc[mask_puts, "open_interest"] = (
            df_shocked.loc[mask_puts, "open_interest"] * (1 + shock_pct)
        ).astype(int)

        flip_shocked = self.find_flip_point(df=df_shocked)

        delta_abs: float | None = None
        delta_pct: float | None = None
        if flip_shocked is not None and flip_original is not None:
            delta_abs = float(flip_shocked - flip_original)
            if flip_original != 0:
                delta_pct = float(delta_abs / flip_original * 100.0)

        if delta_abs is not None and delta_pct is not None:
            direction = "hacia arriba" if delta_abs > 0 else "hacia abajo"
            interp = (
                f"Un aumento del {shock_pct * 100:.0f}% en el OI de Puts desplaza el Flip Point "
                f"{direction} en {abs(delta_abs):.2f} pts ({abs(delta_pct):.3f}%), "
                f"aumentando la fragilidad estructural del mercado."
            )
        elif delta_abs is not None:
            interp = (
                f"Un aumento del {shock_pct * 100:.0f}% en el OI de Puts desplaza el Flip Point "
                f"{'hacia arriba' if delta_abs > 0 else 'hacia abajo'} en {abs(delta_abs):.2f} pts."
            )
        else:
            interp = "No se pudo calcular el desplazamiento."

        return {
            "shock_pct_applied": shock_pct * 100,
            "flip_original": flip_original,
            "flip_shocked": flip_shocked,
            "delta_absolute": delta_abs,
            "delta_percent": delta_pct,
            "interpretation": interp,
        }

    # ─────────────────────────────────────────────────────────────────────────
    #  GRÁFICO PRINCIPAL
    # ─────────────────────────────────────────────────────────────────────────

    def plot(self, figsize: tuple[float, float] = (14.0, 9.0), save_path: str | None = None) -> Any:
        """
        Genera el gráfico de la Curva de Gamma con anotaciones.

        Paneles:
          - Superior: Gamma Profile Curve con flip point y spot marcados.
          - Inferior: Open Interest distribution (calls vs puts) por strike.

        Requiere el paquete opcional ``matplotlib`` (solo para este método).
        """
        try:
            import matplotlib.pyplot as plt
            import matplotlib.ticker as mticker
            from matplotlib.gridspec import GridSpec
        except ImportError as e:
            raise ImportError(
                "matplotlib is required for GammaFlipEngine.plot(). "
                "Install with: pip install matplotlib"
            ) from e

        # --- Cálculos necesarios ---
        prices, gammas = self.gamma_profile()
        flip = self._flip_point if self._flip_point is not None else self.find_flip_point()
        regime_info = self.volatility_regime()
        sensitivity = self.sensitivity_put_oi()

        # --- Paleta y estilo ---
        BG = "#0d1117"
        PANEL_BG = "#161b22"
        GREEN = "#39d353"
        RED = "#f85149"
        YELLOW = "#d29922"
        BLUE = "#58a6ff"
        GRAY = "#8b949e"
        WHITE = "#e6edf3"

        plt.rcParams.update(
            {
                "font.family": "monospace",
                "axes.facecolor": PANEL_BG,
                "figure.facecolor": BG,
                "axes.edgecolor": "#30363d",
                "axes.labelcolor": GRAY,
                "xtick.color": GRAY,
                "ytick.color": GRAY,
                "grid.color": "#21262d",
                "grid.linestyle": "--",
                "grid.linewidth": 0.6,
                "text.color": WHITE,
            }
        )

        fig = plt.figure(figsize=figsize)
        gs = GridSpec(2, 1, height_ratios=[3, 1.2], hspace=0.04, figure=fig)
        ax1 = fig.add_subplot(gs[0])
        ax2 = fig.add_subplot(gs[1], sharex=ax1)

        # ── Panel superior: Gamma Profile ─────────────────────────────────

        # Relleno de zonas
        pos_mask = cast(Sequence[bool] | None, (gammas >= 0).tolist())
        neg_mask = cast(Sequence[bool] | None, (gammas < 0).tolist())
        ax1.fill_between(
            prices,
            gammas,
            0,
            where=pos_mask,
            alpha=0.25,
            color=GREEN,
            label="Gamma Positiva (MM amortigua)",
        )
        ax1.fill_between(
            prices,
            gammas,
            0,
            where=neg_mask,
            alpha=0.25,
            color=RED,
            label="Gamma Negativa (MM amplifica)",
        )

        # Curva principal
        ax1.plot(prices, gammas, color=BLUE, linewidth=2.2, zorder=5)

        # Línea cero
        ax1.axhline(0, color="#30363d", linewidth=1.0, zorder=3)

        # Flip Point
        if flip:
            ax1.axvline(
                flip,
                color=YELLOW,
                linewidth=1.8,
                linestyle="--",
                zorder=6,
                label=f"Gamma Flip Point: {flip:,.2f}",
            )
            ax1.annotate(
                f"  FLIP\n  {flip:,.2f}",
                xy=(flip, 0),
                xytext=(flip + self.spot * 0.01, max(gammas) * 0.55),
                color=YELLOW,
                fontsize=9,
                fontweight="bold",
                arrowprops=dict(arrowstyle="->", color=YELLOW, lw=1.2),
            )

            # Flecha de aceleración hacia abajo del flip
            ax1.annotate(
                "",
                xy=(flip - self.spot * 0.05, min(gammas) * 0.85),
                xytext=(flip - self.spot * 0.01, min(gammas) * 0.4),
                arrowprops=dict(arrowstyle="-|>", color=RED, lw=1.5, mutation_scale=15),
            )
            ax1.text(
                flip - self.spot * 0.08,
                min(gammas) * 0.65,
                "⚡ Aceleración\n   del precio",
                color=RED,
                fontsize=7.5,
                ha="center",
            )

        # Spot actual
        current_gamma_val = self._net_gamma_at_price(self.spot)
        spot_color = GREEN if current_gamma_val >= 0 else RED
        ax1.axvline(
            self.spot,
            color=spot_color,
            linewidth=1.4,
            linestyle=":",
            zorder=6,
            label=f"Spot Actual: {self.spot:,.2f}",
        )
        ax1.scatter([self.spot], [current_gamma_val], color=spot_color, s=90, zorder=7)

        # Flip shocked
        flip_shocked = sensitivity.get("flip_shocked")
        if flip_shocked:
            ax1.axvline(
                flip_shocked,
                color="#a371f7",
                linewidth=1.2,
                linestyle=(0, (3, 5, 1, 5)),
                zorder=6,
                label=f"Flip +10% Put OI: {flip_shocked:,.2f}",
            )

        # Texto del régimen
        regime_color = GREEN if regime_info["regime"] == "GAMMA_POSITIVE" else RED
        ax1.text(
            0.02,
            0.97,
            f"Régimen: {regime_info['regime']}",
            transform=ax1.transAxes,
            fontsize=10,
            color=regime_color,
            va="top",
            bbox=dict(
                boxstyle="round,pad=0.4", facecolor="#161b22", edgecolor=regime_color, alpha=0.9
            ),
        )

        # Distancia al flip
        if regime_info["distance_pct"] is not None:
            ax1.text(
                0.02,
                0.87,
                f"Distancia al Flip: {regime_info['distance_pct']:+.2f}%",
                transform=ax1.transAxes,
                fontsize=8.5,
                color=GRAY,
                va="top",
            )

        ax1.set_ylabel("Gamma Neta del Sistema (MM-adjusted)", fontsize=9)
        ax1.set_title(
            "GAMMA FLIP ENGINE  ·  Dealer Gamma Exposure Profile",
            fontsize=13,
            color=WHITE,
            pad=12,
            fontweight="bold",
        )
        ax1.legend(
            fontsize=8, loc="upper right", facecolor=PANEL_BG, edgecolor="#30363d", labelcolor=WHITE
        )
        ax1.grid(True)
        ax1.yaxis.set_major_formatter(
            mticker.FuncFormatter(lambda x, _: f"{x/1e6:.1f}M" if abs(x) >= 1e6 else f"{x:,.0f}")
        )
        plt.setp(ax1.get_xticklabels(), visible=False)

        # ── Panel inferior: OI por Strike ────────────────────────────────

        calls_df = self.df[self.df["option_type"].str.lower() == "call"]
        puts_df = self.df[self.df["option_type"].str.lower() == "put"]

        # Solo strikes dentro del rango visible
        lo, hi = prices[0], prices[-1]
        calls_vis = calls_df[(calls_df["strike"] >= lo) & (calls_df["strike"] <= hi)]
        puts_vis = puts_df[(puts_df["strike"] >= lo) & (puts_df["strike"] <= hi)]

        bar_width = (prices[-1] - prices[0]) / (len(self.df["strike"].unique()) * 2.5)

        ax2.bar(
            calls_vis["strike"],
            calls_vis["open_interest"],
            width=bar_width,
            color=GREEN,
            alpha=0.7,
            label="Call OI",
        )
        ax2.bar(
            puts_vis["strike"],
            -puts_vis["open_interest"],
            width=bar_width,
            color=RED,
            alpha=0.7,
            label="Put OI",
        )
        ax2.axhline(0, color="#30363d", linewidth=0.8)

        if flip:
            ax2.axvline(flip, color=YELLOW, linewidth=1.8, linestyle="--")
        ax2.axvline(self.spot, color=spot_color, linewidth=1.4, linestyle=":")

        ax2.set_xlabel("Precio del Subyacente / Strike", fontsize=9)
        ax2.set_ylabel("Open Interest", fontsize=9)
        ax2.legend(
            fontsize=8, loc="upper right", facecolor=PANEL_BG, edgecolor="#30363d", labelcolor=WHITE
        )
        ax2.grid(True, axis="y")
        ax2.yaxis.set_major_formatter(mticker.FuncFormatter(lambda x, _: f"{abs(x)/1e3:.0f}k"))

        # ── Nota explicativa ──────────────────────────────────────────────

        note = (
            "⚡  Por qué el precio se acelera al romper el Flip hacia abajo:\n"
            "   El MM pasa de comprar caídas (gamma+) a VENDER caídas (gamma−).\n"
            "   Su delta-hedge refuerza el movimiento → bucle de retroalimentación positiva.\n"
            "   Sin amortiguador, la liquidez se evapora → caída violenta ('air pocket')."
        )
        fig.text(
            0.01, 0.01, note, fontsize=7.5, color=GRAY, verticalalignment="bottom", style="italic"
        )

        plt.tight_layout(rect=(0.0, 0.07, 1.0, 1.0))

        if save_path:
            plt.savefig(save_path, dpi=150, bbox_inches="tight", facecolor=BG)
            print(f"✅  Gráfico guardado en: {save_path}")
        else:
            plt.savefig(
                "/mnt/user-data/outputs/gamma_flip_chart.png",
                dpi=150,
                bbox_inches="tight",
                facecolor=BG,
            )
            print("✅  Gráfico guardado en /mnt/user-data/outputs/gamma_flip_chart.png")

        plt.show()
        return fig

    # ─────────────────────────────────────────────────────────────────────────
    #  REPORTE COMPLETO
    # ─────────────────────────────────────────────────────────────────────────

    def report(self) -> pd.DataFrame:
        """
        Genera un DataFrame-resumen con todos los KPIs del análisis.
        """
        regime = self.volatility_regime()
        sens = self.sensitivity_put_oi()

        data = {
            "KPI": [
                "Spot Actual",
                "Gamma Flip Point",
                "Distancia Spot→Flip (%)",
                "Gamma Neta @ Spot",
                "Régimen de Volatilidad",
                "Flip +10% OI Puts",
                "Desplazamiento Flip (pts)",
                "Desplazamiento Flip (%)",
            ],
            "Valor": [
                f"{self.spot:,.4f}",
                f"{regime['flip_point']:,.4f}" if regime["flip_point"] else "N/A",
                f"{regime['distance_pct']:+.4f}%" if regime["distance_pct"] else "N/A",
                f"{regime['current_gamma']:,.2f}",
                regime["regime"],
                f"{sens['flip_shocked']:,.4f}" if sens["flip_shocked"] else "N/A",
                f"{sens['delta_absolute']:+.4f}" if sens["delta_absolute"] else "N/A",
                f"{sens['delta_percent']:+.4f}%" if sens["delta_percent"] else "N/A",
            ],
        }
        df_report = pd.DataFrame(data)
        print("\n" + "═" * 60)
        print("  GAMMA FLIP ENGINE  ─  REPORTE DE EXPOSICIÓN")
        print("═" * 60)
        print(df_report.to_string(index=False))
        print("─" * 60)
        print(regime["interpretation"])
        print("─" * 60)
        print(sens["interpretation"])
        print("═" * 60 + "\n")
        return df_report


# =============================================================================
#  DEMO: Construcción de datos sintéticos y ejecución del motor
# =============================================================================


def generate_synthetic_data(
    spot: float = 5000.0,
    n_strikes: int = 30,
    range_pct: float = 0.15,
    T: float = 30 / 365,
    r: float = 0.05,
    sigma: float = 0.18,
    seed: int = 42,
) -> pd.DataFrame:
    """
    Genera un DataFrame sintético que simula una estructura de opciones
    real donde los puts tienen mayor OI que los calls en la parte baja
    (skew de volatilidad típico del S&P).
    """
    rng = np.random.default_rng(seed)

    strikes = np.linspace(spot * (1 - range_pct), spot * (1 + range_pct), n_strikes)
    rows = []

    for K in strikes:
        # Gamma BS
        g = bs_gamma(spot, K, T, r, sigma)

        # OI Calls: concentrado alrededor y por encima del spot
        call_oi = int(
            rng.integers(500, 8000) * np.exp(-0.5 * ((K - spot * 1.02) / (spot * 0.05)) ** 2)
        )

        # OI Puts: asimétrico, mayor en la parte baja (put skew)
        put_oi = int(
            rng.integers(700, 12000) * np.exp(-0.5 * ((K - spot * 0.97) / (spot * 0.04)) ** 2)
        )

        rows.append(
            {
                "strike": round(K, 2),
                "option_type": "call",
                "gamma": g,
                "open_interest": call_oi,
                "current_spot": spot,
            }
        )
        rows.append(
            {
                "strike": round(K, 2),
                "option_type": "put",
                "gamma": g,
                "open_interest": put_oi,
                "current_spot": spot,
            }
        )

    return pd.DataFrame(rows)


if __name__ == "__main__":
    # ── 1. Generar datos sintéticos ──────────────────────────────────────
    SPOT = 5_000.0
    df_options = generate_synthetic_data(spot=SPOT, n_strikes=40, seed=7)

    print(f"📊 DataFrame de opciones generado: {len(df_options)} filas")
    print(df_options.head(8).to_string(index=False))

    # ── 2. Instanciar el motor ───────────────────────────────────────────
    engine = GammaFlipEngine(
        df=df_options,
        contract_size=100,
        T=30 / 365,
        r=0.05,
        sigma=0.18,
        range_pct=0.15,
        n_points=600,
    )

    # ── 3. Calcular el Flip Point ────────────────────────────────────────
    flip = engine.find_flip_point()
    print(f"\n🎯 Gamma Flip Point detectado en: {flip:,.2f}" if flip else "\n⚠️ No se detectó flip.")

    # ── 4. Régimen actual ────────────────────────────────────────────────
    regime = engine.volatility_regime()
    print(f"\n{regime['interpretation']}")

    # ── 5. Análisis de sensibilidad ──────────────────────────────────────
    sens = engine.sensitivity_put_oi(shock_pct=0.10)
    print(f"\n{sens['interpretation']}")

    # ── 6. Reporte completo ──────────────────────────────────────────────
    engine.report()

    # ── 7. Gráfico ───────────────────────────────────────────────────────
    engine.plot()
