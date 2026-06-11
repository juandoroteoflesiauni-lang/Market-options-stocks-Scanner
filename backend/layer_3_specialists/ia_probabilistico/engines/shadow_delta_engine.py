from typing import Any, cast

"""
╔══════════════════════════════════════════════════════════════════════════════╗
║              SHADOW DELTA ENGINE — High-Frequency Options Analytics          ║
║                                                                              ║
║  Basado en la expansión de primer orden del delta ajustado por Skew IV:      ║
║                                                                              ║
║      Δ_shadow = Δ_BS + Vanna × (∂σ/∂S)                                      ║
║                                                                              ║
║  Donde (∂σ/∂S) es la pendiente local de la volatilidad implícita respecto   ║
║  al precio del subyacente — capturando el efecto de "sticky moneyness".      ║
║                                                                              ║
║  Referencia teórica: Poulsen (2008) — "Four Things You Might Not Know        ║
║  About the Black-Scholes Formula". La Vanna es el vínculo entre el           ║
║  mundo BS-clásico y la realidad del skew: dVega/dS = dDelta/dVol.            ║
╚══════════════════════════════════════════════════════════════════════════════╝
"""

import warnings

import numpy as np
import pandas as pd  # type: ignore[import-untyped]
from scipy.stats import norm  # type: ignore[import-untyped]

warnings.filterwarnings("ignore")


# ─────────────────────────────────────────────────────────────────────────────
# UTILIDADES BLACK-SCHOLES
# ─────────────────────────────────────────────────────────────────────────────


def _d1(S: float, K: float, T: float, r: float, sigma: float) -> float:
    """Calcula d1 de Black-Scholes."""
    return cast(float, (np.log(S / K) + (r + 0.5 * sigma**2) * T) / (sigma * np.sqrt(T)))


def _d2(S: float, K: float, T: float, r: float, sigma: float) -> float:
    """Calcula d2 de Black-Scholes."""
    return cast(float, _d1(S, K, T, r, sigma) - sigma * np.sqrt(T))


def bs_delta(S: float, K: float, T: float, r: float, sigma: float, option_type: str) -> float:
    """
    Delta estándar de Black-Scholes.

    - Calls: N(d1)       → ∈ (0, 1)
    - Puts:  N(d1) - 1   → ∈ (-1, 0)
    """
    d1 = _d1(S, K, T, r, sigma)
    if option_type.upper() == "CALL":
        return cast(float, norm.cdf(d1))
    elif option_type.upper() == "PUT":
        return cast(float, norm.cdf(d1)) - 1.0
    else:
        raise ValueError(f"option_type debe ser 'CALL' o 'PUT', recibido: {option_type}")


def bs_vanna(S: float, K: float, T: float, r: float, sigma: float) -> float:
    """
    Vanna = ∂²V / (∂S ∂σ) = ∂Delta/∂σ = ∂Vega/∂S

    En Black-Scholes: Vanna = -φ(d1) × d2 / σ

    Interpretación de riesgo:
    - Para Puts OTM (d2 > 0), Vanna < 0 → cuando el precio cae (skew sube),
      el delta del put se vuelve MÁS negativo que lo que BS predice.
    - Esto explica por qué el Shadow Delta de Puts OTM es más negativo que
      el delta clásico: el mercado "sabe" que la vol se dispara cuando el
      subyacente cae, amplificando la sensibilidad.
    """
    d1 = _d1(S, K, T, r, sigma)
    d2 = d1 - sigma * np.sqrt(T)
    return cast(float, -norm.pdf(d1) * d2 / sigma)


def bs_vega(S: float, K: float, T: float, r: float, sigma: float) -> float:
    """Vega = S × φ(d1) × √T"""
    d1 = _d1(S, K, T, r, sigma)
    return cast(float, S * norm.pdf(d1) * np.sqrt(T))


# ─────────────────────────────────────────────────────────────────────────────
# MOTOR PRINCIPAL
# ─────────────────────────────────────────────────────────────────────────────


class ShadowDeltaEngine:
    """
    Motor de cálculo del Shadow Delta para carteras de opciones.

    Integra el efecto del Skew de Volatilidad Implícita en el cálculo del
    delta efectivo, usando la expansión:

        Δ_shadow = Δ_BS + Vanna × (∂σ/∂S)

    Parámetros del DataFrame de entrada (portfolio_df):
    ─────────────────────────────────────────────────
    strike        : float  — Precio de ejercicio de la opción
    option_type   : str    — 'CALL' o 'PUT'
    delta         : float  — Delta de BS proporcionado externamente (o se recalcula)
    vanna         : float  — Vanna de BS proporcionado externamente (o se recalcula)
    iv            : float  — Volatilidad Implícita (decimal, ej: 0.25 = 25%)
    spot_price    : float  — Precio spot del subyacente
    open_interest : int    — Interés abierto (para ponderación de posiciones)
    expiry        : float  — (Opcional) Tiempo a vencimiento en años
    quantity      : float  — (Opcional) Número de contratos (+ largo, - corto)
    r             : float  — (Opcional) Tasa libre de riesgo (default: 0.05)
    """

    def __init__(
        self,
        portfolio_df: pd.DataFrame,
        spot_price: float | None = None,
        default_expiry: float = 0.25,  # 3 meses por defecto
        risk_free_rate: float = 0.05,
        skew_window: int = 2,  # nº de strikes vecinos para estimar pendiente
        regularize_skew: bool = True,  # truncar pendientes extremas
        skew_cap: float = 0.05,  # cap en |∂σ/∂S| (5% por punto)
        contract_size: int = 100,  # acciones por contrato
    ):
        self.df = portfolio_df.copy()
        self.r = risk_free_rate
        self.T_default = default_expiry
        self.skew_window = skew_window
        self.regularize = regularize_skew
        self.skew_cap = skew_cap
        self.contract_size = contract_size

        # Spot price: puede venir en columna o como parámetro
        if spot_price is not None:
            self.spot = spot_price
        elif "spot_price" in self.df.columns:
            self.spot = float(self.df["spot_price"].iloc[0])
        else:
            raise ValueError("Debe proporcionar spot_price como parámetro o columna.")

        self._validate_and_prepare()
        self._compute_skew_slope()
        self._compute_shadow_delta()

    # ── Validación ────────────────────────────────────────────────────────────

    def _validate_and_prepare(self) -> None:
        required = {"strike", "option_type", "iv"}
        missing = required - set(self.df.columns)
        if missing:
            raise ValueError(f"Columnas faltantes en el DataFrame: {missing}")

        # Normalizar tipos
        self.df["option_type"] = self.df["option_type"].str.upper()
        self.df["strike"] = self.df["strike"].astype(float)
        self.df["iv"] = self.df["iv"].astype(float)

        # Defaults opcionales
        if "expiry" not in self.df.columns:
            self.df["expiry"] = self.T_default
        if "quantity" not in self.df.columns:
            self.df["quantity"] = 1.0
        if "open_interest" not in self.df.columns:
            self.df["open_interest"] = 1
        if "r" not in self.df.columns:
            self.df["r"] = self.r

        # Recalcular delta y vanna desde BS (más preciso que usar los dados)
        self.df["bs_delta"] = self.df.apply(
            lambda row: bs_delta(
                self.spot, row["strike"], row["expiry"], row["r"], row["iv"], row["option_type"]
            ),
            axis=1,
        )
        self.df["vanna"] = self.df.apply(
            lambda row: bs_vanna(self.spot, row["strike"], row["expiry"], row["r"], row["iv"]),
            axis=1,
        )
        self.df["vega"] = self.df.apply(
            lambda row: bs_vega(self.spot, row["strike"], row["expiry"], row["r"], row["iv"]),
            axis=1,
        )
        self.df = self.df.sort_values("strike").reset_index(drop=True)

    # ── Skew Slope ────────────────────────────────────────────────────────────

    def _compute_skew_slope(self) -> None:
        """
        Estima la pendiente local del IV Skew: ∂σ/∂S ≈ ∂σ/∂K × (∂K/∂S)

        En mercados de renta variable el skew es empíricamente negativo:
        cuando S cae, la IV sube → ∂σ/∂S < 0.

        Usamos diferencias finitas centradas entre strikes vecinos,
        re-expresadas en términos del precio spot para obtener la derivada
        respecto a S (sticky-strike vs sticky-moneyness).
        """
        n = len(self.df)
        skew_slopes = np.zeros(n)
        w = self.skew_window

        for i in range(n):
            # Vecinos disponibles
            lo = max(0, i - w)
            hi = min(n - 1, i + w)

            if hi > lo:  # al menos 2 puntos
                dIV = self.df["iv"].iloc[hi] - self.df["iv"].iloc[lo]
                dK = self.df["strike"].iloc[hi] - self.df["strike"].iloc[lo]
                # ∂σ/∂K — pendiente en espacio de strikes
                dIV_dK = dIV / dK if dK != 0 else 0.0

                # Conversión a ∂σ/∂S: bajo sticky-moneyness ∂σ/∂S ≈ ∂σ/∂K
                # (el moneyness K/S se preserva con los movimientos de S)
                skew_slopes[i] = dIV_dK
            else:
                skew_slopes[i] = 0.0

        if self.regularize:
            # Trucamos pendientes extremas (ver sección de regularización en
            # Poulsen 2008: sin regularización los hedges explotan)
            skew_slopes = cast(
                np.ndarray[Any, np.dtype[Any]], np.clip(skew_slopes, -self.skew_cap, self.skew_cap)
            )

        self.df["skew_slope"] = skew_slopes

    # ── Shadow Delta ──────────────────────────────────────────────────────────

    def _compute_shadow_delta(self) -> None:
        """
        Δ_shadow = Δ_BS + Vanna × (∂σ/∂S)

        Por qué los Puts OTM tienen Shadow Delta mucho más negativo:
        ─────────────────────────────────────────────────────────────
        1. Put OTM → strike < spot → d2 > 0 → Vanna = -φ(d1)·d2/σ < 0
        2. Skew empírico: ∂σ/∂S < 0 (la vol sube cuando el precio cae)
        3. Producto Vanna × (∂σ/∂S) = (negativo) × (negativo) = POSITIVO...
           PERO para puts el delta ya es negativo, y la corrección hace
           que el delta efectivo sea AÚN MÁS negativo (más sensible).
        4. Intuición: el mercado "sabe" que si S cae, la IV explota,
           lo que amplifica la sensibilidad del put —el trader que ignora
           esto está sistemáticamente sub-cubierto.
        """
        self.df["shadow_delta"] = self.df["bs_delta"] + self.df["vanna"] * self.df["skew_slope"]

        # Delta Gap (diferencia absoluta y porcentual)
        self.df["delta_gap"] = self.df["shadow_delta"] - self.df["bs_delta"]
        self.df["delta_gap_pct"] = np.where(
            self.df["bs_delta"].abs() > 1e-6,
            (self.df["delta_gap"] / self.df["bs_delta"].abs()) * 100,
            0.0,
        )

    # ── Reportes ──────────────────────────────────────────────────────────────

    def portfolio_summary(self) -> pd.DataFrame:
        """
        Retorna DataFrame con métricas por opción más resumen de cartera.

        Columnas clave:
        - bs_delta      : Delta teórico BS
        - shadow_delta  : Delta ajustado por skew
        - delta_gap     : Diferencia (shadow - BS)
        - delta_gap_pct : Diferencia porcentual respecto al delta BS
        - hedge_adj     : Acciones adicionales para ser Shadow-Delta neutral
        """
        cols = [
            "strike",
            "option_type",
            "iv",
            "expiry",
            "bs_delta",
            "shadow_delta",
            "delta_gap",
            "delta_gap_pct",
            "vanna",
            "skew_slope",
            "open_interest",
            "quantity",
        ]
        available_cols = [c for c in cols if c in self.df.columns]
        summary = self.df[available_cols].copy()

        # Hedge adjustment: acciones adicionales para Shadow-Delta neutral
        # (positivo = comprar acciones, negativo = vender)
        summary["hedge_adj_shares"] = (
            -summary["delta_gap"] * summary["quantity"] * self.contract_size
        )
        return summary

    def net_portfolio_delta(self) -> dict[str, Any]:
        """
        Calcula el delta neto de la cartera (ponderado por cantidad y contratos).
        """
        df = self.df
        net_bs = (df["bs_delta"] * df["quantity"] * self.contract_size).sum()
        net_shadow = (df["shadow_delta"] * df["quantity"] * self.contract_size).sum()
        return {
            "net_bs_delta": round(net_bs, 4),
            "net_shadow_delta": round(net_shadow, 4),
            "total_delta_gap": round(net_shadow - net_bs, 4),
            "hedge_shares_needed": round(-(net_shadow - net_bs), 4),
            "spot_price": self.spot,
            "n_options": len(df),
        }

    # ── Stress Test ───────────────────────────────────────────────────────────

    def stress_test(self, shock_pct: float = -0.05) -> pd.DataFrame:
        """
        Simula un shock en el precio del subyacente y compara:
        - Delta "naive" BS (ignora el efecto Vanna en el nuevo spot)
        - Delta ajustado que incorpora el desplazamiento del skew

        Parámetros:
            shock_pct : fracción del shock (default -0.05 = caída del 5%)

        Retorna:
            DataFrame con métricas pre/post shock y la desviación del delta.
        """
        shocked_spot = self.spot * (1 + shock_pct)
        rows = []

        for _, row in self.df.iterrows():
            # BS delta en el spot shockeado (sin ajustar IV)
            delta_shocked_naive = bs_delta(
                shocked_spot, row["strike"], row["expiry"], row["r"], row["iv"], row["option_type"]
            )

            # IV ajustada por skew: la vol cambia con el precio
            # ΔIV ≈ (∂σ/∂S) × ΔS
            delta_S = shocked_spot - self.spot
            iv_adjusted = max(row["iv"] + row["skew_slope"] * delta_S, 0.001)

            # Vanna ajustada para el nuevo spot e IV
            vanna_adjusted = bs_vanna(
                shocked_spot, row["strike"], row["expiry"], row["r"], iv_adjusted
            )

            # Delta con IV ajustada (modelo más realista)
            delta_shocked_adjusted = (
                bs_delta(
                    shocked_spot,
                    row["strike"],
                    row["expiry"],
                    row["r"],
                    iv_adjusted,
                    row["option_type"],
                )
                + vanna_adjusted * row["skew_slope"]
            )

            rows.append(
                {
                    "strike": row["strike"],
                    "option_type": row["option_type"],
                    "bs_delta_pre": row["bs_delta"],
                    "shadow_delta_pre": row["shadow_delta"],
                    "bs_delta_post": delta_shocked_naive,
                    "shadow_delta_post": delta_shocked_adjusted,
                    "iv_pre": row["iv"],
                    "iv_post": iv_adjusted,
                    "delta_error_naive": delta_shocked_naive - delta_shocked_adjusted,
                    "pct_error": (
                        (delta_shocked_naive - delta_shocked_adjusted)
                        / max(abs(delta_shocked_adjusted), 1e-6)
                        * 100
                    ),
                }
            )

        stress_df = pd.DataFrame(rows)
        return stress_df

    # ── Visualización ─────────────────────────────────────────────────────────

    def plot_delta_curves(
        self,
        show_oi_bubble: bool = True,
        title: str = "Shadow Delta vs. Delta BS — Efecto del IV Skew",
    ) -> Any:
        """
        Visualización interactiva con Plotly que muestra:
        1. Panel superior: Curvas de Delta BS vs Shadow Delta por strike
        2. Panel central : Delta Gap (diferencia)
        3. Panel inferior: Skew Slope estimado

        Requiere ``plotly`` (import diferido; no necesario para la API REST).
        """
        try:
            import plotly.graph_objects as go  # type: ignore[import-untyped]
            from plotly.subplots import make_subplots  # type: ignore[import-untyped]
        except ImportError as e:
            raise ImportError(
                "plotly is required for ShadowDeltaEngine.plot_delta_curves(). "
                "Install with: pip install plotly"
            ) from e

        df = self.df.copy()
        calls = df[df["option_type"] == "CALL"]
        puts = df[df["option_type"] == "PUT"]

        fig = make_subplots(
            rows=3,
            cols=1,
            shared_xaxes=True,
            row_heights=[0.50, 0.25, 0.25],
            vertical_spacing=0.06,
            subplot_titles=[
                "📊 Delta Teórico BS vs. Shadow Delta",
                "⚡ Delta Gap (Shadow − BS)",
                "📐 Skew Slope Local  ∂σ/∂S",
            ],
        )

        # Línea de spot
        for row_n in [1, 2, 3]:
            fig.add_vline(
                x=self.spot,
                line_dash="dot",
                line_color="#f59e0b",
                line_width=1.5,
                row=row_n,
                col=1,
                annotation_text="Spot" if row_n == 1 else "",
                annotation_font_color="#f59e0b",
            )

        # ── Calls ──────────────────────────────────────────────────────────
        for typ_df, label in [(calls, "CALL"), (puts, "PUT")]:
            color_bs = "#38bdf8" if label == "CALL" else "#f472b6"
            color_shadow = "#0ea5e9" if label == "CALL" else "#ec4899"
            dash_bs = "solid"
            dash_shadow = "dot"

            oi_size = (
                10 + 30 * (typ_df["open_interest"] / df["open_interest"].max())
                if show_oi_bubble
                else 8
            )

            # BS Delta
            fig.add_trace(
                go.Scatter(
                    x=typ_df["strike"],
                    y=typ_df["bs_delta"],
                    mode="lines+markers",
                    name=f"Δ_BS ({label})",
                    line=dict(color=color_bs, width=2, dash=dash_bs),
                    marker=dict(size=6, color=color_bs),
                    hovertemplate=(
                        "<b>Strike</b>: %{x:.1f}<br>" "<b>Δ_BS</b>: %{y:.4f}<extra></extra>"
                    ),
                ),
                row=1,
                col=1,
            )

            # Shadow Delta
            fig.add_trace(
                go.Scatter(
                    x=typ_df["strike"],
                    y=typ_df["shadow_delta"],
                    mode="lines+markers",
                    name=f"Δ_shadow ({label})",
                    line=dict(color=color_shadow, width=2.5, dash=dash_shadow),
                    marker=dict(
                        size=oi_size if show_oi_bubble else 8,
                        color=color_shadow,
                        opacity=0.7,
                        line=dict(color="white", width=1),
                    ),
                    hovertemplate=(
                        "<b>Strike</b>: %{x:.1f}<br>"
                        "<b>Δ_shadow</b>: %{y:.4f}<br>"
                        "<b>Gap%</b>: " + typ_df["delta_gap_pct"].round(1).astype(str) + "%"
                        "<extra></extra>"
                    ),
                ),
                row=1,
                col=1,
            )

            # Delta Gap
            fig.add_trace(
                go.Bar(
                    x=typ_df["strike"],
                    y=typ_df["delta_gap"],
                    name=f"Gap ({label})",
                    marker_color=["#22c55e" if v >= 0 else "#ef4444" for v in typ_df["delta_gap"]],
                    opacity=0.7,
                    showlegend=False,
                    hovertemplate="<b>Strike</b>: %{x:.1f}<br><b>Gap</b>: %{y:.4f}<extra></extra>",
                ),
                row=2,
                col=1,
            )

        # Skew Slope (total)
        fig.add_trace(
            go.Scatter(
                x=df["strike"],
                y=df["skew_slope"],
                mode="lines+markers",
                name="Skew Slope",
                line=dict(color="#a78bfa", width=2),
                marker=dict(size=5, color="#a78bfa"),
                fill="tozeroy",
                fillcolor="rgba(167,139,250,0.15)",
                hovertemplate="<b>Strike</b>: %{x:.1f}<br><b>∂σ/∂S</b>: %{y:.5f}<extra></extra>",
            ),
            row=3,
            col=1,
        )

        # Línea cero en gap
        fig.add_hline(y=0, row=2, col=1, line_color="rgba(255,255,255,0.2)", line_width=1)
        fig.add_hline(y=0, row=3, col=1, line_color="rgba(255,255,255,0.2)", line_width=1)

        fig.update_layout(
            title=dict(text=title, font=dict(size=18, color="#f8fafc"), x=0.5),
            template="plotly_dark",
            paper_bgcolor="#0f172a",
            plot_bgcolor="#0f172a",
            font=dict(family="JetBrains Mono, monospace", color="#94a3b8"),
            legend=dict(
                bgcolor="rgba(15,23,42,0.8)",
                bordercolor="#334155",
                borderwidth=1,
                font=dict(size=11),
            ),
            height=750,
            margin=dict(l=60, r=40, t=80, b=60),
        )
        fig.update_xaxes(
            title_text="Strike Price",
            row=3,
            col=1,
            gridcolor="#1e293b",
            zeroline=False,
        )
        for r in [1, 2, 3]:
            fig.update_xaxes(gridcolor="#1e293b", zeroline=False, row=r, col=1)
            fig.update_yaxes(gridcolor="#1e293b", zeroline=False, row=r, col=1)

        fig.update_yaxes(title_text="Delta", row=1, col=1)
        fig.update_yaxes(title_text="Δ Gap", row=2, col=1)
        fig.update_yaxes(title_text="∂σ/∂S", row=3, col=1)

        return fig

    def print_report(self) -> None:
        """Imprime un reporte de consola formateado."""
        net = self.net_portfolio_delta()
        summary = self.portfolio_summary()

        print("\n" + "═" * 70)
        print("  SHADOW DELTA ENGINE — REPORTE DE CARTERA")
        print("═" * 70)
        print(f"  Spot Price          : {net['spot_price']:.2f}")
        print(f"  Opciones en cartera : {net['n_options']}")
        print(f"  Delta Neto BS       : {net['net_bs_delta']:+.4f} acciones")
        print(f"  Shadow Delta Neto   : {net['net_shadow_delta']:+.4f} acciones")
        print(f"  Delta Gap Total     : {net['total_delta_gap']:+.4f} acciones")
        print(f"  ➤ Ajuste de Hedge   : {net['hedge_shares_needed']:+.1f} acciones")
        print("─" * 70)
        print("\n  TOP 5 opciones con mayor Delta Gap (%):\n")
        top5 = summary.nlargest(5, "delta_gap_pct")[
            [
                "strike",
                "option_type",
                "bs_delta",
                "shadow_delta",
                "delta_gap_pct",
                "hedge_adj_shares",
            ]
        ]
        print(top5.to_string(index=False, float_format=lambda x: f"{x:+.4f}"))
        print("\n" + "═" * 70 + "\n")


def shadow_delta_position_multiplier(
    shadow_delta: float,
    bs_delta: float,
    vanna: float,
    option_type: str,
    skew_slope: float,
    base_multiplier: float = 1.0,
    edge_threshold: float = 0.10,  # |shadow_delta - bs_delta| > 10% = edge detectado
    max_leverage: float = 1.40,  # max amplificación 40% sobre base
) -> dict[str, float | str]:
    """
    Retorna multiplicador de posición basado en Shadow Delta vs Black-Scholes.

    INPUTS:
    - shadow_delta: float (calculado de ShadowDeltaEngine)
    - bs_delta: float (delta clásico B-S)
    - vanna: float (sensibilidad delta-a-vol)
    - option_type: str ("CALL" o "PUT")
    - skew_slope: float (∂σ/∂S, pendiente local del skew IV)

    OUTPUTS dict:
    - multiplier: float ∈ [1.0, max_leverage] (amplificación de posición)
    - edge_signal: float (0-1, confianza en edge detectado)
    - reason: str

    LÓGICA:
    delta_divergence = abs(shadow_delta - bs_delta) / max(abs(bs_delta), 0.01)

    if delta_divergence > edge_threshold:
        # Edge detectado: el skew crea oportunidad institucional
        edge_strength = min(1.0, delta_divergence / 0.50)  # normalizar

        if option_type == "CALL" and shadow_delta > bs_delta:
            # Calls se vuelven más delta-positivos (alcista) → amplificar
            multiplier = 1.0 + (edge_strength * 0.40)
        elif option_type == "PUT" and shadow_delta < bs_delta:
            # Puts se vuelven más delta-negativo (bajista) → amplificar
            multiplier = 1.0 + (edge_strength * 0.40)
        else:
            multiplier = 1.0  # divergence pero direccionalmente inconsistente
    else:
        multiplier = 1.0
        edge_strength = 0.0

    # RESTRICCIÓN FTMO
    if edge_strength > 0.85 and vanna > 0.05:
        multiplier = max_leverage
    """

    delta_divergence = abs(shadow_delta - bs_delta) / max(abs(bs_delta), 0.01)

    if delta_divergence > edge_threshold:
        edge_strength = min(1.0, delta_divergence / 0.50)

        if (option_type == "CALL" and shadow_delta > bs_delta) or (option_type == "PUT" and shadow_delta < bs_delta):
            multiplier = 1.0 + (edge_strength * 0.40)
        else:
            multiplier = 1.0
    else:
        multiplier = 1.0
        edge_strength = 0.0

    if edge_strength > 0.85 and vanna > 0.05:
        multiplier = max_leverage

    multiplier = float(max(1.0, min(max_leverage, multiplier * base_multiplier)))

    reason = "Normal delta alignment"
    if multiplier > 1.0:
        reason = f"OTM {option_type.lower()}s amplified by positive skew slope"

    return {
        "multiplier": float(multiplier),
        "edge_signal": float(edge_strength),
        "reason": reason,
        "delta_divergence": float(delta_divergence),
    }


# ─────────────────────────────────────────────────────────────────────────────
# DEMO — Ejemplo con cartera sintética
# ─────────────────────────────────────────────────────────────────────────────


def build_demo_portfolio(spot: float = 5000.0) -> pd.DataFrame:
    """
    Genera una cartera sintética representativa de opciones sobre índice.
    El skew de IV simula condiciones reales de mercado (puts OTM más caros).
    """
    np.random.seed(42)

    strikes = np.arange(4600, 5401, 50, dtype=float)
    n = len(strikes)
    option_types = ["PUT" if k < spot else "CALL" for k in strikes]

    # IV Skew realista: puts OTM tienen IV más alta (smirk)
    moneyness = strikes / spot
    iv_base = 0.18
    skew_coeff = -0.35  # Pendiente negativa del skew
    smile_coeff = 0.08  # Curvatura (smile)
    iv = iv_base + skew_coeff * (moneyness - 1) + smile_coeff * (moneyness - 1) ** 2
    iv = np.clip(iv, 0.08, 0.45)

    # Open interest: mayor en strikes cercanos al spot
    oi = (
        5000 * np.exp(-0.5 * ((strikes - spot) / 150) ** 2) + np.random.randint(100, 500, n)
    ).astype(int)

    return pd.DataFrame(
        {
            "strike": strikes,
            "option_type": option_types,
            "iv": iv,
            "spot_price": spot,
            "open_interest": oi,
            "quantity": np.random.choice([-10, -5, 5, 10], n),
            "expiry": 0.25,  # 3 meses
            "delta": 0.0,  # se recalcula internamente
            "vanna": 0.0,  # se recalcula internamente
            "r": 0.05,
        }
    )
