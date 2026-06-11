from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import Enum
from typing import Any, Final

import numpy as np
import pandas as pd

# MIGRATION: Dependencia cruzada institucional
try:
    from backend.layer_3_specialists.opciones_gex.options_models import ExposureRegime
except ImportError:

    class FallbackExposureRegime(str, Enum):
        NEUTRAL = "NEUTRAL"
        BULLISH = "BULLISH"
        BEARISH = "BEARISH"
        SHOCK = "SHOCK"

    ExposureRegime = FallbackExposureRegime

try:
    from scipy.cluster.hierarchy import linkage, to_tree
    from scipy.spatial.distance import squareform

    _SCIPY_AVAILABLE = True
except ImportError:
    _SCIPY_AVAILABLE = False

logger = logging.getLogger(__name__)

# ─────────────────────────────────────────────────────────────────────────────
# MIGRATION : Constantes Institucionales (Sector Portfolio)
# ─────────────────────────────────────────────────────────────────────────────

TRADING_DAYS: int = 252
ATR_PERIOD: int = 14
ATR_SL_MULTIPLIER_SWING: float = 2.0
ATR_SL_MULTIPLIER_SCALP: float = 1.25
ATR_CONSERVATIVE_FACTOR: float = 1.25

ATR_MULT_BY_TIMEFRAME: dict[str, tuple[float, float]] = {
    "15m": (1.0, 1.5),
    "1H": (1.0, 1.5),
    "4H": (2.0, 3.0),
    "1D": (2.0, 3.0),
}

MAX_POSITION_SIZE: float = 0.20
MIN_ROWS_REQUIRED: int = 30
RISK_FREE_ANNUAL: float = 0.05
VETO_KELLY_MIN: float = 0.0
RR_NORMAL_MIN: float = 1.5
RR_CONSERVATIVE_MIN: float = 2.0

# Altman Z-Score Coefficients (Original)
ALTMAN_ORIGINAL_COEFS: Final[dict[str, float]] = {
    "X1": 1.2,  # Working Capital / Total Assets
    "X2": 1.4,  # Retained Earnings / Total Assets
    "X3": 3.3,  # EBIT / Total Assets
    "X4": 0.6,  # Market Cap / Total Liabilities
    "X5": 1.0,  # Sales / Total Assets
}

FUNDAMENTAL_RISK_DEFAULT_CLIP_QUANTILE: float = 0.01
FUNDAMENTAL_RISK_DEFAULT_YEO_JOHNSON_CLIPPING: bool = True
FUNDAMENTAL_RISK_DISTRESS_THRESHOLD: float = 1.81
FUNDAMENTAL_RISK_SAFE_THRESHOLD: float = 2.99

# ─────────────────────────────────────────────────────────────────────────────
# DOMAIN MODELS
# ─────────────────────────────────────────────────────────────────────────────


@dataclass(frozen=True, slots=True)
class RiskMetrics:
    var_95_pct: float
    var_99_pct: float
    vol_annual_pct: float
    sharpe_ratio: float
    max_drawdown_pct: float
    sl_price: float | None
    sl_pct: float | None
    tp1_price: float | None
    tp2_price: float | None
    atr: float | None
    current_price: float
    timeframe: str = "1D"
    computed_at: datetime = field(default_factory=lambda: datetime.now(tz=UTC))


@dataclass(frozen=True, slots=True)
class KellySizingResult:
    kelly_fraction: float
    half_kelly: float
    suggested_exposure_pct: float
    suggested_exposure_usd: float
    is_tradeable: bool
    edge: float
    breakeven_prob: float
    win_prob: float
    reward_risk_ratio: float
    account_size: float
    vanna_penalty: float = 1.0
    gex_penalty: float = 1.0
    friction_penalty: float = 1.0


@dataclass(frozen=True, slots=True)
class HRPResult:
    weights: pd.Series
    n_assets: int
    computed_at: datetime = field(default_factory=lambda: datetime.now(tz=UTC))


# ─────────────────────────────────────────────────────────────────────────────
# LAYER A — StatisticalRiskCalculator
# ─────────────────────────────────────────────────────────────────────────────


class StatisticalRiskCalculator:
    """Pure-math statistical risk calculator. All methods are static."""

    @staticmethod
    def compute(
        close: pd.Series,
        atr_series: pd.Series | None = None,
        high: pd.Series | None = None,
        low: pd.Series | None = None,
        timeframe: str = "1D",
        conservative_mode: bool = False,
        rr_min: float | None = None,
    ) -> RiskMetrics:
        close = StatisticalRiskCalculator._validate_close(close)
        returns = close.pct_change().dropna()
        ret_arr = returns.to_numpy(dtype=np.float64)
        close_arr = close.to_numpy(dtype=np.float64)
        current = float(close_arr[-1])

        var_95_pct = float(abs(np.percentile(ret_arr, 5))) * 100.0
        var_99_pct = float(abs(np.percentile(ret_arr, 1))) * 100.0

        annual_factor = StatisticalRiskCalculator._annual_factor(timeframe)
        vol_daily = float(np.std(ret_arr, ddof=1))
        vol_annual_pct = vol_daily * np.sqrt(annual_factor) * 100.0

        rf_daily = RISK_FREE_ANNUAL / annual_factor
        excess = ret_arr - rf_daily
        sigma_exc = float(np.std(excess, ddof=1))
        sharpe = (
            float(np.mean(excess)) / sigma_exc * np.sqrt(annual_factor)
            if sigma_exc > 1e-12
            else 0.0
        )

        rolling_max = np.maximum.accumulate(close_arr)
        safe_max = np.where(rolling_max > 0, rolling_max, np.nan)
        drawdowns = (close_arr - rolling_max) / safe_max * 100.0
        max_dd_pct = float(np.nanmin(drawdowns))

        atr_val = StatisticalRiskCalculator._resolve_atr(close, atr_series, high, low)
        sl_price = tp1_price = tp2_price = sl_pct_val = None

        if atr_val is not None and atr_val > 0 and current > 0:
            mult = StatisticalRiskCalculator._atr_multiplier(timeframe, conservative_mode)
            rr = (
                rr_min
                if rr_min is not None
                else (RR_CONSERVATIVE_MIN if conservative_mode else RR_NORMAL_MIN)
            )
            sl_dist = atr_val * mult
            sl_price = round(current - sl_dist, 6)
            sl_pct_val = round(sl_dist / current * 100.0, 4)
            tp1_price = round(current + rr * sl_dist, 6)
            tp2_price = round(current + rr * 2.0 * sl_dist, 6)

        return RiskMetrics(
            var_95_pct=round(var_95_pct, 4),
            var_99_pct=round(var_99_pct, 4),
            vol_annual_pct=round(vol_annual_pct, 4),
            sharpe_ratio=round(sharpe, 4),
            max_drawdown_pct=round(max_dd_pct, 4),
            sl_price=sl_price,
            sl_pct=sl_pct_val,
            tp1_price=tp1_price,
            tp2_price=tp2_price,
            atr=round(atr_val, 6) if atr_val else None,
            current_price=current,
            timeframe=timeframe,
        )

    @staticmethod
    def _validate_close(close: pd.Series) -> pd.Series:
        if not isinstance(close, pd.Series):
            close = pd.Series(close, dtype=np.float64)
        close = close.dropna().reset_index(drop=True).astype(np.float64)
        if len(close) < MIN_ROWS_REQUIRED:
            raise ValueError(
                f"Close series has {len(close)} rows; minimum required is {MIN_ROWS_REQUIRED}."
            )
        return close

    @staticmethod
    def _annual_factor(timeframe: str) -> int:
        mapping = {"15m": 252 * 26, "1H": 252 * 7, "4H": 252 * 2, "1D": TRADING_DAYS}
        return mapping.get(timeframe, TRADING_DAYS)

    @staticmethod
    def _atr_multiplier(timeframe: str, conservative_mode: bool) -> float:
        lo, hi = ATR_MULT_BY_TIMEFRAME.get(timeframe, (1.5, 2.5))
        mult = (lo + hi) / 2.0
        if conservative_mode:
            mult *= ATR_CONSERVATIVE_FACTOR
        return mult

    @staticmethod
    def _resolve_atr(
        close: pd.Series,
        atr_series: pd.Series | None,
        high: pd.Series | None,
        low: pd.Series | None,
    ) -> float | None:
        if atr_series is not None and not atr_series.empty:
            last = atr_series.dropna()
            if not last.empty:
                val = float(last.iloc[-1])
                if np.isfinite(val) and val > 0:
                    return val

        if high is not None and low is not None:
            try:
                prev_close = close.shift(1)
                tr = pd.concat(
                    [
                        high - low,
                        (high - prev_close).abs(),
                        (low - prev_close).abs(),
                    ],
                    axis=1,
                ).max(axis=1)
                # Exponential Moving Average for ATR
                atr_full = tr.ewm(span=ATR_PERIOD, adjust=False).mean().dropna()
                if not atr_full.empty:
                    val = float(atr_full.iloc[-1])
                    if np.isfinite(val) and val > 0:
                        return val
            except Exception:
                pass

        try:
            tr_proxy = close.diff().abs()
            atr_proxy = tr_proxy.rolling(ATR_PERIOD).mean().dropna()
            if not atr_proxy.empty:
                val = float(atr_proxy.iloc[-1])
                if np.isfinite(val) and val > 0:
                    return val
        except Exception:
            pass

        return None

    @staticmethod
    def var_95(returns: np.ndarray) -> float:
        """Historical VaR 95% (positive value = loss magnitude)."""
        return float(abs(np.percentile(returns, 5)))

    @staticmethod
    def cvar_95(returns: np.ndarray) -> float:
        """Conditional VaR 95% (expected shortfall)."""
        tail = returns[returns <= np.percentile(returns, 5)]
        return float(abs(np.mean(tail))) if len(tail) > 0 else 0.0

    @staticmethod
    def max_drawdown(equity_curve: np.ndarray) -> float:
        """Maximum drawdown as negative percentage."""
        rolling_max = np.maximum.accumulate(equity_curve)
        safe_max = np.where(rolling_max > 0, rolling_max, np.nan)
        dd = (equity_curve - rolling_max) / safe_max * 100.0
        return float(np.nanmin(dd))


# ─────────────────────────────────────────────────────────────────────────────
# LAYER B — KellySizer
# ─────────────────────────────────────────────────────────────────────────────


class KellySizer:
    """Institutional Kelly Criterion with VETO_5 rule. All methods are static."""

    @staticmethod
    def compute(
        win_prob: float,
        reward_risk_ratio: float,
        account_size: float = 10_000.0,
        max_position_size: float = MAX_POSITION_SIZE,
        gex_regime: ExposureRegime = ExposureRegime.NEUTRAL,
        vanna_sensitivity: float = 0.0,
        implied_liquidity: float = 1.0,
    ) -> KellySizingResult:
        p = float(win_prob)
        b = float(reward_risk_ratio)
        q = 1.0 - p
        cap = float(max_position_size)

        # 1. Base Kelly
        K = (p - (q / b)) if b > 1e-12 else -1.0

        # 2. Institutional Penalties
        gex_penalty = 1.0
        if gex_regime == ExposureRegime.BEARISH:  # Short Gamma Proxy
            gex_penalty = 0.25
        elif gex_regime == ExposureRegime.SHOCK:
            gex_penalty = 0.10

        vanna_penalty = 1.0
        if abs(vanna_sensitivity) > 1e6:  # Institutional notional threshold
            vanna_penalty = 0.5

        friction_penalty = max(0.1, implied_liquidity)

        # Formula: Final_K = Half-Kelly * GEX_Penalty * Vanna_Penalty * Friction
        applied_K = (K / 2.0) * gex_penalty * vanna_penalty * friction_penalty

        edge = K * b if b > 1e-12 else 0.0
        be = 1.0 / (1.0 + b) if b > 1e-12 else 1.0
        is_tradeable = (K > VETO_KELLY_MIN) and (0.0 < p < 1.0) and (b > 1e-12)

        if not is_tradeable:
            suggested_frac = 0.0
        else:
            suggested_frac = min(applied_K, cap)

        return KellySizingResult(
            kelly_fraction=round(K, 6),
            half_kelly=round(K / 2.0, 6),
            suggested_exposure_pct=round(suggested_frac * 100.0, 4),
            suggested_exposure_usd=round(suggested_frac * account_size, 2),
            is_tradeable=is_tradeable,
            edge=round(edge, 6),
            breakeven_prob=round(be, 6),
            win_prob=p,
            reward_risk_ratio=b,
            account_size=account_size,
            vanna_penalty=vanna_penalty,
            gex_penalty=gex_penalty,
            friction_penalty=friction_penalty,
        )


# ─────────────────────────────────────────────────────────────────────────────
# LAYER C — HRPPortfolioOptimizer
# ─────────────────────────────────────────────────────────────────────────────


class HRPPortfolioOptimizer:
    """Hierarchical Risk Parity portfolio optimizer."""

    @staticmethod
    def compute(returns_df: pd.DataFrame) -> HRPResult:
        if not _SCIPY_AVAILABLE:
            n = len(returns_df.columns)
            equal_w = pd.Series([1.0 / n] * n, index=returns_df.columns)
            return HRPResult(weights=equal_w, n_assets=n)

        cov = returns_df.cov()
        corr = returns_df.corr()
        dist = np.sqrt((1.0 - corr) / 2.0)
        dist = np.clip(dist, 0.0, 1.0)
        np.fill_diagonal(dist.values, 0.0)

        link = linkage(squareform(dist.values), method="single")
        sort_idx = HRPPortfolioOptimizer._quasi_diag(link, len(cov))
        sort_cols = [cov.columns[i] for i in sort_idx]
        cov_sorted = cov.loc[sort_cols, sort_cols]

        weights = HRPPortfolioOptimizer._recursive_bisection(cov_sorted)
        weights = weights.reindex(returns_df.columns).fillna(0.0)
        weights = weights / weights.sum()

        return HRPResult(weights=weights.round(6), n_assets=len(weights))

    @staticmethod
    def _quasi_diag(link: np.ndarray, n: int) -> list[int]:
        root = to_tree(link, rd=False)
        items: list[int] = []

        def _recurse(node):
            if node.is_leaf():
                items.append(node.id)
            else:
                _recurse(node.left)
                _recurse(node.right)

        _recurse(root)
        return items

    @staticmethod
    def _cluster_variance(cov: pd.DataFrame, items: list) -> float:
        sub_cov = cov.loc[items, items]
        ivp = 1.0 / np.diag(sub_cov.values)
        ivp = ivp / ivp.sum()
        return float(ivp @ sub_cov.values @ ivp)

    @staticmethod
    def _recursive_bisection(cov: pd.DataFrame) -> pd.Series:
        weights = pd.Series(1.0, index=cov.index)
        items = [list(cov.index)]

        while items:
            items = [
                i[j:k]
                for i in items
                for j, k in [(0, len(i) // 2), (len(i) // 2, len(i))]
                if len(i) > 1
            ]
            for i in range(0, len(items), 2):
                if i + 1 >= len(items):
                    break
                left, right = items[i], items[i + 1]
                v_l = HRPPortfolioOptimizer._cluster_variance(cov, left)
                v_r = HRPPortfolioOptimizer._cluster_variance(cov, right)
                alpha = 1.0 - v_l / (v_l + v_r) if (v_l + v_r) > 0 else 0.5
                weights[left] *= alpha
                weights[right] *= 1.0 - alpha

        return weights


# ─────────────────────────────────────────────────────────────────────────────
# FUNDAMENTAL RISK ENGINE — ALTMAN Z-SCORE
# ─────────────────────────────────────────────────────────────────────────────


class FundamentalRiskEngine:
    """Vectorized Altman Z-Score engine for a universe of equities."""

    @staticmethod
    def compute(
        data: list[dict[str, Any]] | pd.DataFrame,
        yeo_johnson_like_clipping: bool = FUNDAMENTAL_RISK_DEFAULT_YEO_JOHNSON_CLIPPING,
        clip_quantile: float = FUNDAMENTAL_RISK_DEFAULT_CLIP_QUANTILE,
    ) -> pd.DataFrame:
        df = data.copy() if isinstance(data, pd.DataFrame) else pd.DataFrame(data)
        if df.empty:
            return df

        num_cols = [
            "total_assets",
            "working_capital",
            "retained_earnings",
            "ebit",
            "total_liabilities",
            "market_cap",
            "sales",
            "net_income",
            "shares_outstanding",
        ]
        for col in num_cols:
            if col in df.columns:
                df[col] = pd.to_numeric(df[col], errors="coerce")
            else:
                df[col] = np.nan

        # Ratios
        df["ratio_A"] = df["working_capital"] / df["total_assets"].replace(0, np.nan)
        df["ratio_B"] = df["retained_earnings"] / df["total_assets"].replace(0, np.nan)
        df["ratio_C"] = df["ebit"] / df["total_assets"].replace(0, np.nan)
        df["ratio_D"] = df["market_cap"] / df["total_liabilities"].replace(0, np.nan)
        df["ratio_E"] = df["sales"] / df["total_assets"].replace(0, np.nan)

        if yeo_johnson_like_clipping:
            for col in ["ratio_A", "ratio_B", "ratio_C", "ratio_D", "ratio_E"]:
                lower = df[col].quantile(clip_quantile)
                upper = df[col].quantile(1.0 - clip_quantile)
                df[col] = df[col].clip(lower=lower, upper=upper)

        df["z_score"] = (
            ALTMAN_ORIGINAL_COEFS["X1"] * df["ratio_A"]
            + ALTMAN_ORIGINAL_COEFS["X2"] * df["ratio_B"]
            + ALTMAN_ORIGINAL_COEFS["X3"] * df["ratio_C"]
            + ALTMAN_ORIGINAL_COEFS["X4"] * df["ratio_D"]
            + ALTMAN_ORIGINAL_COEFS["X5"] * df["ratio_E"]
        )

        def classify(z):
            if z > FUNDAMENTAL_RISK_SAFE_THRESHOLD:
                return "Safe"
            if z < FUNDAMENTAL_RISK_DISTRESS_THRESHOLD:
                return "Distress"
            return "Grey"

        df["risk_zone"] = df["z_score"].apply(classify)
        return df


# ─────────────────────────────────────────────────────────────────────────────
# FACADE — RiskCore
# ─────────────────────────────────────────────────────────────────────────────


class RiskCore:
    """Unified risk facade wrapping all risk engines."""

    @staticmethod
    def compute_risk_metrics(close: pd.Series, **kwargs) -> RiskMetrics:
        return StatisticalRiskCalculator.compute(close, **kwargs)

    @staticmethod
    def kelly_size(win_prob: float, reward_risk_ratio: float, **kwargs) -> KellySizingResult:
        return KellySizer.compute(win_prob, reward_risk_ratio, **kwargs)

    @staticmethod
    def hrp_weights(returns_df: pd.DataFrame) -> HRPResult:
        return HRPPortfolioOptimizer.compute(returns_df)


# ────────────────────────────────────────────────────────────────────
# MIGRATION AUDIT — SECTOR: PORTAFOLIO
# Archivo         : risk.py
# Sub-capa        : Engines
# Solver/Optimizer: Kelly, HRP, Altman
# Eliminado       : Dependencias legacy de QuantumBeta V1.
# Preservado      : Todas las fórmulas matemáticas de riesgo y sizing.
# Pendientes      : Pruebas de integración con el orquestador final.
# ────────────────────────────────────────────────────────────────────
