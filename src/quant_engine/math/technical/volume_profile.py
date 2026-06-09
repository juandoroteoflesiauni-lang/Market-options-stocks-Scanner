"""Núcleo matemático de Volume Profile — Sector Técnico.

Histograma de alta fidelidad sobre pares (precio, volumen) para detección de
POC, Value Area (VAH/VAL), High Volume Nodes (HVN) y Low Volume Nodes (LVN).

Restricciones:
- Exclusivamente numpy.  Sin pandas, pydantic, logging ni capas de dominio.
- Toda división regularizada con _EPS para evitar divisiones por cero.
- API orientada a arrays: recibe ndarray, devuelve dataclasses ligeros.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np

_EPS: float = 1e-12


class VolumeProfileMath:
    """Librería matemática sin estado para el cálculo de Perfiles de Volumen."""

    @staticmethod
    def compute_profile(
        price: np.ndarray,
        volume: np.ndarray,
        bins: int = 50,
        value_area_pct: float = 0.70
    ) -> tuple[float, float, float, np.ndarray, np.ndarray]:
        """
        Calcula el Punto de Control (POC) y el Área de Valor (VAH/VAL).
        
        Args:
            price: np.ndarray con la serie de precios (típicamente (H+L+C)/3).
            volume: np.ndarray con los volúmenes correspondientes.
            bins: Número de particiones para el histograma de precios.
            value_area_pct: Porcentaje del volumen total para el área de valor.
            
        Returns:
            Tupla conteniendo: (POC, VAH, VAL, bin_centers, hist_volumes)
        """
        p = np.ascontiguousarray(price, dtype=np.float64)
        v = np.ascontiguousarray(volume, dtype=np.float64)
        
        min_p = np.nanmin(p)
        max_p = np.nanmax(p)
        
        if np.isnan(min_p) or np.isnan(max_p) or min_p == max_p or len(p) == 0:
            return np.nan, np.nan, np.nan, np.array([]), np.array([])

        # 1. Construir Histograma Ponderado por Volumen
        hist, bin_edges = np.histogram(p, bins=bins, weights=v)
        bin_centers = (bin_edges[:-1] + bin_edges[1:]) / 2.0

        # 2. Identificar el Point of Control (POC)
        poc_idx = int(np.argmax(hist))
        poc = float(bin_centers[poc_idx])

        # 3. Calcular Value Area (VAH / VAL) expandiendo desde el POC
        total_vol = np.sum(hist)
        va_target = total_vol * value_area_pct

        va_vol = float(hist[poc_idx])
        lower_idx = poc_idx
        upper_idx = poc_idx

        # Bucle de expansión bidireccional buscando los nodos de mayor volumen
        while va_vol < va_target and (lower_idx > 0 or upper_idx < bins - 1):
            lower_vol = hist[lower_idx - 1] if lower_idx > 0 else 0.0
            upper_vol = hist[upper_idx + 1] if upper_idx < bins - 1 else 0.0

            if lower_vol >= upper_vol and lower_idx > 0:
                va_vol += lower_vol
                lower_idx -= 1
            elif upper_vol > lower_vol and upper_idx < bins - 1:
                va_vol += upper_vol
                upper_idx += 1
            elif lower_idx > 0:
                va_vol += lower_vol
                lower_idx -= 1
            elif upper_idx < bins - 1:
                va_vol += upper_vol
                upper_idx += 1
            else:
                break

        val = float(bin_centers[lower_idx])
        vah = float(bin_centers[upper_idx])

        return poc, vah, val, bin_centers, hist

    @staticmethod
    def identify_volume_nodes(
        bin_centers: np.ndarray, 
        hist: np.ndarray, 
        prominence_factor: float = 1.5
    ) -> tuple[np.ndarray, np.ndarray]:
        """
        Identifica High Volume Nodes (HVN) y Low Volume Nodes (LVN).
        """
        if len(hist) < 3:
            return np.array([]), np.array([])
            
        mean_vol = np.mean(hist)
        
        # Un HVN simple es un pico local mayor al promedio * prominence_factor
        hvn_mask = (hist > np.roll(hist, 1)) & (hist > np.roll(hist, -1)) & (hist > mean_vol * prominence_factor)
        hvn_mask[0] = hvn_mask[-1] = False  # Ignorar bordes
        
        # Un LVN es un valle local menor al promedio / prominence_factor
        lvn_mask = (hist < np.roll(hist, 1)) & (hist < np.roll(hist, -1)) & (hist < mean_vol / prominence_factor)
        lvn_mask[0] = lvn_mask[-1] = False  # Ignorar bordes
        
        return bin_centers[hvn_mask], bin_centers[lvn_mask]


# Parámetros por defecto
_DEFAULT_BINS: int = 100
_VALUE_AREA_PCT: float = 0.70       # 70 % del volumen total → Value Area
_MIN_OBSERVATIONS: int = 5          # Mínimo de barras para un perfil significativo
_HVN_PERCENTILE: float = 70.0       # Umbral percentil para High Volume Nodes
_LVN_PERCENTILE: float = 30.0       # Umbral percentil para Low Volume Nodes


# ─────────────────────────────────────────────────────────────────────────────
# §1  RESULT TYPES (dataclasses ligeras — sin Pydantic)
# ─────────────────────────────────────────────────────────────────────────────


@dataclass(frozen=True, slots=True)
class VolumeNode:
    """Un nivel de precio en el histograma de volumen."""

    price: float
    volume: float
    node_type: str  # "POC" | "HVN" | "LVN"


@dataclass(frozen=True, slots=True)
class VolumeProfileReport:
    """Resultado completo del análisis de Perfil de Volumen."""

    symbol: str
    poc: float
    vah: float
    val: float
    profile: tuple[VolumeNode, ...]
    hvn_levels: tuple[float, ...]
    lvn_levels: tuple[float, ...]
    nodes_found: int


@dataclass(frozen=True, slots=True)
class AnalysisResult:
    """Contenedor Result<T, E> para la API del motor."""

    _report: VolumeProfileReport | None
    _reason: str | None
    is_success: bool

    @property
    def is_failure(self) -> bool:
        return not self.is_success

    @property
    def reason(self) -> str:
        return self._reason or ""

    def unwrap(self) -> VolumeProfileReport:
        if self._report is None:
            raise RuntimeError(f"AnalysisResult is a failure: {self._reason}")
        return self._report

    @classmethod
    def ok(cls, report: VolumeProfileReport) -> AnalysisResult:
        return cls(_report=report, _reason=None, is_success=True)

    @classmethod
    def fail(cls, reason: str) -> AnalysisResult:
        return cls(_report=None, _reason=reason, is_success=False)


# ─────────────────────────────────────────────────────────────────────────────
# §2  VolumeProfileEngine
# ─────────────────────────────────────────────────────────────────────────────


class VolumeProfileEngine:
    """Motor matemático puro de Volume Profile.

    Método principal
    ----------------
    analyze(symbol, price_volume_data, bins) -> AnalysisResult
        price_volume_data : ndarray de shape (n, 2) — columnas [price, volume].
    """

    def analyze(
        self,
        symbol: str,
        price_volume_data: np.ndarray,
        bins: int = _DEFAULT_BINS,
        value_area_pct: float = _VALUE_AREA_PCT,
    ) -> AnalysisResult:
        """Valida datos y calcula el perfil de volumen completo."""
        # Validación de entrada
        err = self._validate(price_volume_data)
        if err:
            return AnalysisResult.fail(err)

        prices = price_volume_data[:, 0].astype(np.float64)
        volumes = price_volume_data[:, 1].astype(np.float64)

        # Caso degenerado: pocas muestras — devuelve informe neutro
        if len(prices) < _MIN_OBSERVATIONS:
            neutral = VolumeProfileReport(
                symbol=symbol,
                poc=0.0,
                vah=0.0,
                val=0.0,
                profile=(),
                hvn_levels=(),
                lvn_levels=(),
                nodes_found=0,
            )
            return AnalysisResult.ok(neutral)

        poc, vah, val, hist_centers, hist_volumes = _compute_profile(
            prices, volumes, bins, value_area_pct
        )
        nodes, hvn_levels, lvn_levels = _classify_nodes(hist_centers, hist_volumes, poc)

        report = VolumeProfileReport(
            symbol=symbol,
            poc=poc,
            vah=vah,
            val=val,
            profile=nodes,
            hvn_levels=hvn_levels,
            lvn_levels=lvn_levels,
            nodes_found=len(hvn_levels) + len(lvn_levels),
        )
        return AnalysisResult.ok(report)

    @staticmethod
    def _validate(data: np.ndarray) -> str | None:
        """Retorna mensaje de error o None si los datos son válidos."""
        if not isinstance(data, np.ndarray) or data.size == 0:
            return "price_volume_data is empty"
        if data.ndim != 2 or data.shape[1] != 2:
            return "must be a 2D array of shape (n, 2) with columns [price, volume]"
        if np.any(np.isnan(data)):
            return "price_volume_data contains NaN values"
        if np.any(data[:, 1] < 0):
            return "volume must be non-negative"
        return None


# ─────────────────────────────────────────────────────────────────────────────
# §3  PRIVATE MATH KERNELS
# ─────────────────────────────────────────────────────────────────────────────


def _compute_profile(
    prices: np.ndarray,
    volumes: np.ndarray,
    bins: int,
    value_area_pct: float,
) -> tuple[float, float, float, np.ndarray, np.ndarray]:
    """Histograma de volumen → POC, VAH, VAL, centros, volúmenes.

    Caso especial: precio constante → bin único con todo el volumen.
    """
    price_min = float(np.min(prices))
    price_max = float(np.max(prices))

    if price_max == price_min:
        poc = price_min
        centers = np.array([poc])
        hist = np.array([float(np.sum(volumes))])
        return (poc, poc, poc, centers, hist)

    edges = np.linspace(price_min, price_max, bins + 1)
    centers = (edges[:-1] + edges[1:]) / 2.0

    # Asigna cada precio al bin más cercano
    bin_indices = np.searchsorted(edges[1:], prices, side="left")
    bin_indices = np.clip(bin_indices, 0, bins - 1)

    hist = np.zeros(bins, dtype=np.float64)
    np.add.at(hist, bin_indices, volumes)

    # POC
    poc_idx = int(np.argmax(hist))
    poc = float(centers[poc_idx])

    # Value Area (expansión greedy desde el POC)
    target = float(np.sum(hist)) * value_area_pct
    accumulated = hist[poc_idx]
    lo_cursor = poc_idx
    hi_cursor = poc_idx

    while accumulated < target and (lo_cursor > 0 or hi_cursor < bins - 1):
        can_low = lo_cursor > 0
        can_high = hi_cursor < bins - 1
        if can_low and can_high:
            if hist[lo_cursor - 1] >= hist[hi_cursor + 1]:
                lo_cursor -= 1
                accumulated += hist[lo_cursor]
            else:
                hi_cursor += 1
                accumulated += hist[hi_cursor]
        elif can_low:
            lo_cursor -= 1
            accumulated += hist[lo_cursor]
        else:
            hi_cursor += 1
            accumulated += hist[hi_cursor]

    vah = float(centers[hi_cursor])
    val = float(centers[lo_cursor])
    return (poc, vah, val, centers, hist)


def _classify_nodes(
    centers: np.ndarray,
    hist: np.ndarray,
    poc: float,
) -> tuple[tuple[VolumeNode, ...], tuple[float, ...], tuple[float, ...]]:
    """Clasifica cada bin como POC, HVN o LVN según percentiles."""
    if len(hist) == 0:
        return ((), (), ())

    # Caso degenerado: un solo bin
    if len(hist) == 1:
        node = VolumeNode(price=float(centers[0]), volume=float(hist[0]), node_type="POC")
        return ((node,), (), ())

    hvn_thr = float(np.percentile(hist, _HVN_PERCENTILE))
    lvn_thr = float(np.percentile(hist, _LVN_PERCENTILE))

    nodes: list[VolumeNode] = []
    hvn_levels: list[float] = []
    lvn_levels: list[float] = []

    for price, vol in zip(centers, hist):
        p = float(price)
        v = float(vol)
        if p == poc:
            node_type = "POC"
            hvn_levels.append(p)
        elif v >= hvn_thr:
            node_type = "HVN"
            hvn_levels.append(p)
        elif v <= lvn_thr:
            node_type = "LVN"
            lvn_levels.append(p)
        else:
            node_type = "NORMAL"
        nodes.append(VolumeNode(price=p, volume=v, node_type=node_type))

    return (tuple(nodes), tuple(hvn_levels), tuple(lvn_levels))


# ─────────────────────────────────────────────────────────────────────────────
# §4  ANCHORED VWAP (función pura)
# ─────────────────────────────────────────────────────────────────────────────


def compute_anchored_vwap(
    high: np.ndarray,
    low: np.ndarray,
    close: np.ndarray,
    volume: np.ndarray,
    anchor_index: int = 0,
) -> np.ndarray:
    """Calcula el Anchored VWAP desde un índice de anclaje hasta el final.

    AVWAP_t = Σ(TP_i × V_i) / Σ(V_i)   para i in [anchor_index, t]

    Returns
    -------
    avwap : ndarray de shape (n,) con np.nan para posiciones anteriores al anclaje.
    """
    high = np.asarray(high, dtype=np.float64)
    low = np.asarray(low, dtype=np.float64)
    close = np.asarray(close, dtype=np.float64)
    volume = np.asarray(volume, dtype=np.float64)

    n = len(close)
    result = np.full(n, np.nan, dtype=np.float64)
    anchor_index = max(0, min(anchor_index, n - 1))

    tp = (high + low + close) / 3.0
    pv = tp * volume

    cum_pv = np.cumsum(pv[anchor_index:])
    cum_vol = np.cumsum(volume[anchor_index:])
    result[anchor_index:] = cum_pv / (cum_vol + _EPS)
    return result
