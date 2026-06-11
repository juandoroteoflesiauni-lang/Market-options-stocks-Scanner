"""Motor de Análisis de Volumen y Perfilado de Precios — Sector Técnico.

Proporciona herramientas de Volume Profile (POC, VAH/VAL), CVD (Cumulative Volume Delta)
y Delta Volume Profile para validación de Order Blocks y sesgo institucional.
"""

from __future__ import annotations

from enum import Enum
from typing import Any

import numpy as np
import numpy.typing as npt
import pandas as pd
from pydantic import BaseModel, ConfigDict

_FloatArr = npt.NDArray[np.float64]

# ── Constants ─────────────────────────────────────────────────────────────────
_DEFAULT_N_BINS = 100
_DEFAULT_VA_PCT = 0.70
_DEFAULT_HVN_THRESHOLD = 0.70
_DEFAULT_LVN_THRESHOLD = 0.25
_MIN_ROWS = 5


# ─────────────────────────────────────────────────────────────────────────────
# §1  ENUMERATIONS
# ─────────────────────────────────────────────────────────────────────────────


class NodeType(str, Enum):
    """Tipo de nodo de volumen."""

    HVN = "HVN"  # High-Volume Node (Imán de precio / Soporte-Resistencia)
    LVN = "LVN"  # Low-Volume Node (Mercado fino / Cruce rápido)


class CVDMode(str, Enum):
    """Modo de cálculo de CVD."""

    TICK_EXACT = "TICK_EXACT"  # Requiere ask_volume / bid_volume
    MIDPOINT_PROXY = "MIDPOINT_PROXY"  # Proxy basado en cierre vs midpoint


class DeltaBias(str, Enum):
    """Sesgo direccional derivado del Delta Volume Profile."""

    BULLISH = "BULLISH"
    BEARISH = "BEARISH"
    NEUTRAL = "NEUTRAL"


# ─────────────────────────────────────────────────────────────────────────────
# §2  DOMAIN MODELS (Pydantic V2)
# ─────────────────────────────────────────────────────────────────────────────


class VolumeNode(BaseModel):
    """Nodo individual de precio clasificado en el perfil."""

    model_config = ConfigDict(frozen=True, extra="ignore")

    price: float
    volume: float
    volume_pct: float
    node_type: NodeType
    bin_index: int


class VolumeProfileResult(BaseModel):
    """Resultado completo de un Volume Profile."""

    model_config = ConfigDict(frozen=True, extra="ignore", arbitrary_types_allowed=True)

    poc: float
    poc_volume: float
    value_area_high: float
    value_area_low: float
    value_area_pct: float
    hvn_nodes: tuple[VolumeNode, ...]
    lvn_nodes: tuple[VolumeNode, ...]
    price_levels: Any  # npt.NDArray[np.float64]
    volume_at_price: Any  # npt.NDArray[np.float64]
    total_volume: float
    n_bins: int
    price_min: float
    price_max: float
    error: str | None = None

    @property
    def success(self) -> bool:
        return self.error is None

    @property
    def value_area_range(self) -> float:
        return self.value_area_high - self.value_area_low


class CVDResult(BaseModel):
    """Resultado de Cumulative Volume Delta."""

    model_config = ConfigDict(frozen=True, extra="ignore", arbitrary_types_allowed=True)

    series: Any  # npt.NDArray[np.float64]
    delta_series: Any  # npt.NDArray[np.float64]
    mode: CVDMode
    final_value: float
    max_value: float
    min_value: float

    @property
    def net_bias(self) -> str:
        if self.final_value > 0:
            return "BUY"
        if self.final_value < 0:
            return "SELL"
        return "NEUTRAL"


class DeltaVolumeNode(BaseModel):
    """Nodo de bin individual en el Delta Volume Profile."""

    model_config = ConfigDict(frozen=True, extra="ignore")

    price: float
    bull_volume: float
    bear_volume: float
    net_delta: float
    delta_bias: DeltaBias
    bin_index: int


class DeltaVolumeResult(BaseModel):
    """Resultado de Delta Volume Profile."""

    model_config = ConfigDict(frozen=True, extra="ignore")

    nodes: tuple[DeltaVolumeNode, ...]
    poc_price: float
    poc_delta_bias: DeltaBias
    delta_skew: float
    total_bull: float
    total_bear: float
    n_bins: int
    price_min: float
    price_max: float
    ok: bool = True
    error: str | None = None


class OBDeltaValidation(BaseModel):
    """Validación de un Order Block mediante Delta Volume Profile."""

    model_config = ConfigDict(frozen=True, extra="ignore")

    ob_low: float
    ob_high: float
    delta_bias: DeltaBias
    is_valid: bool
    is_weak: bool
    bull_pct: float
    recommendation: str = ""


# ─────────────────────────────────────────────────────────────────────────────
# §3  VolumeAnalytics — Motor de Lógica Pura
# ─────────────────────────────────────────────────────────────────────────────


class VolumeAnalytics:
    """Utilidades estáticas de análisis de volumen."""

    @staticmethod
    def compute_profile(
        df: pd.DataFrame,
        n_bins: int = _DEFAULT_N_BINS,
        value_area_pct: float = _DEFAULT_VA_PCT,
        hvn_threshold_pct: float = _DEFAULT_HVN_THRESHOLD,
        lvn_threshold_pct: float = _DEFAULT_LVN_THRESHOLD,
    ) -> VolumeProfileResult:
        """Construye un Volume Profile optimizado O(n)."""
        try:
            df = VolumeAnalytics._validate(df)
        except ValueError as e:
            _empty = np.zeros(1)
            return VolumeProfileResult(
                poc=0.0,
                poc_volume=0.0,
                value_area_high=0.0,
                value_area_low=0.0,
                value_area_pct=0.0,
                hvn_nodes=(),
                lvn_nodes=(),
                price_levels=_empty,
                volume_at_price=_empty,
                total_volume=0.0,
                n_bins=n_bins,
                price_min=0.0,
                price_max=0.0,
                error=str(e),
            )

        highs, lows, vols = df["high"].to_numpy(), df["low"].to_numpy(), df["volume"].to_numpy()
        p_min, p_max = float(lows.min()), float(highs.max())

        if p_max - p_min < 1e-10:
            mid = (p_min + p_max) / 2.0
            return VolumeProfileResult(
                poc=mid,
                poc_volume=float(vols.sum()),
                value_area_high=mid,
                value_area_low=mid,
                value_area_pct=1.0,
                hvn_nodes=(),
                lvn_nodes=(),
                price_levels=np.array([mid]),
                volume_at_price=np.array([vols.sum()]),
                total_volume=float(vols.sum()),
                n_bins=1,
                price_min=p_min,
                price_max=p_max,
            )

        bin_centers, vol_at_price = VolumeAnalytics._build_histogram(
            highs, lows, vols, p_min, p_max, n_bins
        )
        total_vol = float(vol_at_price.sum())

        if total_vol < 1e-10:
            return VolumeProfileResult(
                poc=bin_centers[0],
                poc_volume=0.0,
                value_area_high=bin_centers[-1],
                value_area_low=bin_centers[0],
                value_area_pct=0.0,
                hvn_nodes=(),
                lvn_nodes=(),
                price_levels=bin_centers,
                volume_at_price=np.zeros(n_bins),
                total_volume=0.0,
                n_bins=n_bins,
                price_min=p_min,
                price_max=p_max,
                error="Zero total volume",
            )

        poc_idx = int(np.argmax(vol_at_price))
        va_high, va_low, va_actual_pct = VolumeAnalytics._expand_value_area(
            bin_centers, vol_at_price, poc_idx, total_vol * value_area_pct
        )

        max_vol = float(vol_at_price.max())
        hvn = VolumeAnalytics._classify_nodes(
            bin_centers, vol_at_price, max_vol, hvn_threshold_pct, NodeType.HVN
        )
        lvn = VolumeAnalytics._classify_nodes(
            bin_centers, vol_at_price, max_vol, lvn_threshold_pct, NodeType.LVN
        )

        return VolumeProfileResult(
            poc=float(bin_centers[poc_idx]),
            poc_volume=float(vol_at_price[poc_idx]),
            value_area_high=va_high,
            value_area_low=va_low,
            value_area_pct=va_actual_pct,
            hvn_nodes=tuple(hvn),
            lvn_nodes=tuple(lvn),
            price_levels=bin_centers,
            volume_at_price=vol_at_price,
            total_volume=total_vol,
            n_bins=n_bins,
            price_min=p_min,
            price_max=p_max,
        )

    @staticmethod
    def compute_cvd(df: pd.DataFrame) -> CVDResult:
        """Cálculo de Cumulative Volume Delta."""
        try:
            df = VolumeAnalytics._validate(df, require_close=True)
        except ValueError:
            return CVDResult(
                series=np.zeros(1),
                delta_series=np.zeros(1),
                mode=CVDMode.MIDPOINT_PROXY,
                final_value=0.0,
                max_value=0.0,
                min_value=0.0,
            )

        vols = df["volume"].to_numpy()
        if "ask_volume" in df.columns and "bid_volume" in df.columns:
            delta, mode = (
                df["ask_volume"].to_numpy() - df["bid_volume"].to_numpy(),
                CVDMode.TICK_EXACT,
            )
        else:
            mid = (df["high"].to_numpy() + df["low"].to_numpy()) / 2.0
            delta, mode = np.sign(df["close"].to_numpy() - mid) * vols, CVDMode.MIDPOINT_PROXY

        cum = np.cumsum(delta)
        return CVDResult(
            series=cum,
            delta_series=delta,
            mode=mode,
            final_value=float(cum[-1]),
            max_value=float(cum.max()),
            min_value=float(cum.min()),
        )

    @staticmethod
    def analyze(df: pd.DataFrame, **kwargs) -> tuple[VolumeProfileResult, CVDResult]:
        return VolumeAnalytics.compute_profile(df, **kwargs), VolumeAnalytics.compute_cvd(df)

    # ── Private Implementation ──
    @staticmethod
    def _build_histogram(highs, lows, volumes, p_min, p_max, n_bins):
        bsize = (p_max - p_min) / n_bins
        bin_edges = np.linspace(p_min, p_max, n_bins + 1)
        bin_centers = (bin_edges[:-1] + bin_edges[1:]) / 2.0
        diff = np.zeros(n_bins + 1)
        lo_idx = np.clip(((lows - p_min) / bsize).astype(np.int64), 0, n_bins - 1)
        hi_idx = np.clip(((highs - p_min) / bsize).astype(np.int64), 0, n_bins - 1)
        spanned = np.maximum(hi_idx - lo_idx + 1, 1.0)
        v_per_bin = volumes / spanned
        np.add.at(diff, lo_idx, v_per_bin)
        np.add.at(diff, np.minimum(hi_idx + 1, n_bins), -v_per_bin)
        return bin_centers, np.maximum(np.cumsum(diff)[:n_bins], 0.0)

    @staticmethod
    def _expand_value_area(bin_centers, vol_at_price, poc_idx, target):
        lo, hi = poc_idx, poc_idx
        acc, total = float(vol_at_price[poc_idx]), float(vol_at_price.sum())
        if total < 1e-10:
            return float(bin_centers[poc_idx]), float(bin_centers[poc_idx]), 0.0
        while acc < target:
            up, down = hi + 1 < len(bin_centers), lo - 1 >= 0
            if not up and not down:
                break
            if up and down:
                if vol_at_price[hi + 1] >= vol_at_price[lo - 1]:
                    hi += 1
                    acc += vol_at_price[hi]
                else:
                    lo -= 1
                    acc += vol_at_price[lo]
            elif up:
                hi += 1
                acc += vol_at_price[hi]
            else:
                lo -= 1
                acc += vol_at_price[lo]
        return float(bin_centers[hi]), float(bin_centers[lo]), round(acc / total, 4)

    @staticmethod
    def _classify_nodes(bin_centers, vol_at_price, max_vol, thr_pct, ntype):
        if max_vol < 1e-10:
            return []
        total, thr = vol_at_price.sum() or 1.0, max_vol * thr_pct
        nodes = []
        for i, (p, v) in enumerate(zip(bin_centers, vol_at_price, strict=False)):
            if (ntype == NodeType.HVN and v >= thr) or (ntype == NodeType.LVN and v <= thr):
                nodes.append(
                    VolumeNode(
                        price=float(p),
                        volume=float(v),
                        volume_pct=round(v / total, 6),
                        node_type=ntype,
                        bin_index=i,
                    )
                )
        return nodes

    @staticmethod
    def _validate(df: pd.DataFrame, require_close: bool = False) -> pd.DataFrame:
        df = df.copy()
        df.columns = [c.lower() for c in df.columns]
        req = ["high", "low", "volume"]
        if require_close:
            req.append("close")
        if not set(req).issubset(df.columns):
            raise ValueError(f"Missing columns: {set(req) - set(df.columns)}")
        df = df.dropna(subset=req)
        if len(df) < _MIN_ROWS:
            raise ValueError(f"Insufficient data ({len(df)})")
        return df


# ─────────────────────────────────────────────────────────────────────────────
# §4  DeltaVolumeProfile — Separación Direccional de Volumen
# ─────────────────────────────────────────────────────────────────────────────


class DeltaVolumeProfile:
    """Análisis estático de Delta por nivel de precio."""

    @staticmethod
    def compute(df: pd.DataFrame, n_bins: int = _DEFAULT_N_BINS) -> DeltaVolumeResult:
        """Construye el perfil de Delta Volumen."""
        try:
            df = VolumeAnalytics._validate(df, require_close=True)
            if "open" not in df.columns:
                raise ValueError("Column 'open' required")
            p_min, p_max = float(df["low"].min()), float(df["high"].max())
            if p_max - p_min < 1e-10:
                return DeltaVolumeResult(
                    nodes=(),
                    poc_price=p_min,
                    poc_delta_bias=DeltaBias.NEUTRAL,
                    delta_skew=0.0,
                    total_bull=0.0,
                    total_bear=0.0,
                    n_bins=1,
                    price_min=p_min,
                    price_max=p_max,
                )

            vols, o, c = df["volume"].to_numpy(), df["open"].to_numpy(), df["close"].to_numpy()
            is_bull, is_bear = c > o, c < o
            bull_v = np.where(is_bull, vols, np.where(~is_bear, vols * 0.5, 0.0))
            bear_v = np.where(is_bear, vols, np.where(~is_bull, vols * 0.5, 0.0))

            _, b_at_p = VolumeAnalytics._build_histogram(
                df["high"].to_numpy(), df["low"].to_numpy(), bull_v, p_min, p_max, n_bins
            )
            bin_centers, s_at_p = VolumeAnalytics._build_histogram(
                df["high"].to_numpy(), df["low"].to_numpy(), bear_v, p_min, p_max, n_bins
            )

            total_t = b_at_p + s_at_p
            poc_idx = int(np.argmax(total_t))

            nodes = []
            for i, (p, bv, sv) in enumerate(zip(bin_centers, b_at_p, s_at_p, strict=False)):
                nd = bv - sv
                bios = bv + sv
                if bios < 1e-10:
                    bias = DeltaBias.NEUTRAL
                elif nd > bios * 0.10:
                    bias = DeltaBias.BULLISH
                elif nd < -bios * 0.10:
                    bias = DeltaBias.BEARISH
                else:
                    bias = DeltaBias.NEUTRAL
                nodes.append(
                    DeltaVolumeNode(
                        price=round(float(p), 6),
                        bull_volume=round(float(bv), 4),
                        bear_volume=round(float(sv), 4),
                        net_delta=round(float(nd), 4),
                        delta_bias=bias,
                        bin_index=i,
                    )
                )

            t_vol = float(b_at_p.sum() + s_at_p.sum())
            skew = (float(b_at_p.sum()) - float(s_at_p.sum())) / max(t_vol, 1e-10)

            return DeltaVolumeResult(
                nodes=tuple(nodes),
                poc_price=float(bin_centers[poc_idx]),
                poc_delta_bias=nodes[poc_idx].delta_bias,
                delta_skew=round(skew, 6),
                total_bull=float(b_at_p.sum()),
                total_bear=float(s_at_p.sum()),
                n_bins=n_bins,
                price_min=p_min,
                price_max=p_max,
            )

        except Exception as e:
            return DeltaVolumeResult(
                nodes=(),
                poc_price=0.0,
                poc_delta_bias=DeltaBias.NEUTRAL,
                delta_skew=0.0,
                total_bull=0.0,
                total_bear=0.0,
                n_bins=n_bins,
                price_min=0.0,
                price_max=0.0,
                ok=False,
                error=str(e),
            )

    @staticmethod
    def validate_order_block(
        ob_low: float, ob_high: float, delta_res: DeltaVolumeResult, ob_dir: str = "BULLISH"
    ) -> OBDeltaValidation:
        range_nodes = [n for n in delta_res.nodes if ob_low <= n.price <= ob_high]
        if not range_nodes:
            return OBDeltaValidation(
                ob_low=ob_low,
                ob_high=ob_high,
                delta_bias=DeltaBias.NEUTRAL,
                is_valid=False,
                is_weak=True,
                bull_pct=0.5,
                recommendation="No data",
            )

        t_bull = sum(n.bull_volume for n in range_nodes)
        t_bear = sum(n.bear_volume for n in range_nodes)
        t_vol = t_bull + t_bear
        if t_vol < 1e-10:
            return OBDeltaValidation(
                ob_low=ob_low,
                ob_high=ob_high,
                delta_bias=DeltaBias.NEUTRAL,
                is_valid=False,
                is_weak=True,
                bull_pct=0.5,
                recommendation="Sin volumen en zona del OB — datos insuficientes",
            )

        bpct = t_bull / t_vol
        # Positivo = compradores dominan; negativo = vendedores dominan (simétrico bull/bear)
        delta_bias_ratio = (t_bull - t_bear) / t_vol
        bias = (
            DeltaBias.BULLISH
            if bpct > 0.55
            else (DeltaBias.BEARISH if bpct < 0.45 else DeltaBias.NEUTRAL)
        )

        direction = ob_dir.strip().upper()
        if direction == "BULLISH":
            if delta_bias_ratio >= 0.30:
                valid, weak, rec = True, False, "Bullish OB confirmed — delta absorption positiva"
            elif delta_bias_ratio >= 0.10:
                valid, weak, rec = (
                    True,
                    True,
                    "Bullish OB débil — delta positiva pero baja convicción",
                )
            else:
                valid, weak, rec = False, True, "Bullish OB invalidado — delta negativa en zona"
        elif direction == "BEARISH":
            if delta_bias_ratio <= -0.30:
                valid, weak, rec = True, False, "Bearish OB confirmed — delta absorption negativa"
            elif delta_bias_ratio <= -0.10:
                valid, weak, rec = (
                    True,
                    True,
                    "Bearish OB débil — delta negativa pero baja convicción",
                )
            else:
                valid, weak, rec = (
                    False,
                    True,
                    "Bearish OB invalidado — delta positiva en zona (compradores presentes)",
                )
        else:
            valid, weak, rec = False, True, f"Dirección OB desconocida: {ob_dir}"

        return OBDeltaValidation(
            ob_low=ob_low,
            ob_high=ob_high,
            delta_bias=bias,
            is_valid=valid,
            is_weak=weak,
            bull_pct=round(bpct, 4),
            recommendation=rec,
        )


# ─────────────────────────────────────────────────────────
# MIGRATION AUDIT — SECTOR: TÉCNICO
# Archivo      : volume.py
# Sub-capa     : Engine (Análisis de Volumen)
# Eliminado    : Referencias QuantumBeta V1. Conversión Dataclasses.
# Preservado   : Algoritmo O(n) Histogram, Value Area Greedy, Delta Profiling.
# Pendientes   : Integración con generadores de señales de Market Structure.
# ─────────────────────────────────────────────────────────
