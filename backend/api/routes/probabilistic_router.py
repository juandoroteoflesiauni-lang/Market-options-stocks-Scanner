from typing import Any
"""
backend/routers/probabilistic_router.py
════════════════════════════════════════════════════════════════════════════════
API Router for Probabilistic and AI-Driven Analysis.
════════════════════════════════════════════════════════════════════════════════
"""

import asyncio
import math
import os
import sqlite3
from datetime import datetime, timedelta
from pathlib import Path

import joblib
import numpy as np
import pandas as pd
from fastapi import APIRouter, HTTPException
from scipy.stats import norm

from ..config.logger_setup import get_logger
from ..domain.probabilistic_models import AdaptiveState as AdaptiveStateModel
from ..domain.probabilistic_models import (
    COR3MSummary,
    CorrelationEntry,
    CrossAssetSummary,
    DeltaFlowSummary,
    DEXStrikeEntry,
    DEXSummary,
    EventRiskSummary,
    ExpectedMoveEntry,
    ExpectedMoveSummary,
    GammaFlipOIByStrike,
    GammaFlipProfilePoint,
    GammaFlipResponse,
    JumpRisk,
    MarkovRegimeSummary,
    PredictiveOptions2Bundle,
    ProbabilisticResult,
    RegimeStateEntry,
    ShadowDeltaResponse,
    ShadowDeltaRow,
    ShadowDeltaStressSummary,
    SkewFatTailsSummary,
    SkewPointEntry,
    SkewProfileEntry,
    SpeedByStrikeEntry,
    SpeedDecaySeries,
    SpeedInstabilityResponse,
    SpeedInstabilitySummary,
    SpeedInstabilityZoneEntry,
    SpeedProfilePoint,
    SpeedScatterPoint,
    SpeedTrapRow,
    SqueezeSummary,
    StrikeDynamicsEntry,
    TailRiskAlertBlock,
    TailRiskCurvaturePoint,
    TailRiskObservedPoint,
    TailRiskReversalBlock,
    TailRiskSmileMetricsBlock,
    TailRiskSmileResponse,
    TailRiskSplinePoint,
    UpcomingCatalystEntry,
    VolatilitySkewCurvaturePoint,
    VolatilitySkewFittedPoint,
    VolatilitySkewMarketPoint,
    VolatilitySkewMetricsBlock,
    VolatilitySkewResponse,
    VolatilitySkewScenarioEntry,
    VolatilitySurfaceSummary,
    VolTermStrikeEntry,
    VolTermSummary,
    VolumeNodeEntry,
    VolumeOISummary,
    VolumeProfileSummary,
    ZeroDayAlertEntry,
    ZeroDayGammaWallResponse,
    ZeroDayGexBar,
    ZeroDayPinPoint,
    ZeroDayZoneSpan,
    ZommaAnalysisResponse,
    ZommaTopStrikeEntry,
    ZommaVolCrushBuckets,
    ZommaVolCrushPair,
)
from ..domain.probabilistic_models import TailRisk as TailRiskModel
from ..domain.thesis_v2 import AIThesisResponse
from ..layer_1_data.backfill_ohlcv_v3 import get_predictive_backfill_status
from ..layer_1_data.datos.predictive_storage import PredictiveStorage
from ..layer_1_data.fetchers.data912_fetcher import Data912Fetcher
from ..layer_1_data.fetchers.fmp_client import FMPClient
from ..quant_engine.engines.predictive.catalyst_nlp import CatalystNLPEngine
from ..quant_engine.engines.predictive.cnn_fear_greed import (
    get_alternative_source,
    get_cnn_fetcher,
)
from ..quant_engine.engines.technical.cor3m import COR3M_Signal_Engine
from ..quant_engine.math.predictive.correlation_analyzer import (
    get_correlation_analyzer,
)
from ..quant_engine.math.predictive.cross_asset import (
    REFERENCE_ASSETS,
    CrossAssetEngine,
)
from ..quant_engine.engines.options.delta_weighted_flow import (
    DeltaWeightedFlow_Engine,
)
from ..quant_engine.engines.options.dex import DeltaExposureEngine
from ..quant_engine.math.options.expected_move import ExpectedMoveEngine
from ..quant_engine.math.predictive.factor_calibration import (
    get_calibration_engine,
)
from ..quant_engine.engines.predictive.fear_greed import FearGreedEngine
from ..quant_engine.engines.predictive.fear_greed_storage import get_fg_storage
from ..quant_engine.math.predictive.feedback_calibration import FeedbackCalibration
from ..quant_engine.engines.predictive.market_data_fetcher import MarketDataFetcher
from ..quant_engine.math.predictive.markov_regime import MarkovRegimeEngine
from ..quant_engine.engines.predictive.ml_optimizer import get_ml_optimizer
from ..quant_engine.math.technical.matrix_ops import (
    apply_macro_anchoring,
    calculate_kelly_sizing,
    calibrate_heston_vov,
    compute_etv,
    estimate_mjd_params,
    estimate_payoff_ratio,
    fit_gpd,
    project_trajectories,
    run_particle_filter,
)
from ..quant_engine.engines.predictive.regime_weights import get_regime_engine
from ..quant_engine.engines.predictive.sentiment import SentimentEngine
from ..quant_engine.math.options.skew_fattails_engine import SkewFatTailsEngine
from ..quant_engine.engines.technical.squeeze_ignition import (
    OptionChainData,
    SqueezeIgnitionEngine,
    SqueezeState,
    UnderlyingData,
)
from ..quant_engine.engines.options.options import (
    VolatilityTermStructureEngine,
)
from ..quant_engine.math.options.volatility_surface import (
    VolatilitySurfaceEngine,
)
from ..quant_engine.engines.technical.volume_oi_engine import OptionsMarketAnalyzer
from ..quant_engine.engines.technical.volume_profile_engine import (
    VolumeProfileEngine,
)
from ..services.ai_ready_payload import AIReadyPayloadEngine
from ..services.llm_call_policy import should_call_optional_ai
from ..services.notification_service import notification_service
from ..services.prediction_logger import PredictionLogger
from ..services.predictive_engine_audit import (
    build_engine_coverage,
    generate_predictive_audit_report,
    summarize_engine_coverage,
)
from ..services.price_targets_evidence import build_price_targets_evidence_prompt
from ..services.thesis_assembler import (
    assemble_thesis_v2_with_snapshot,
    build_institutional_report,
    legacy_thesis_sentence,
)

logger = get_logger(__name__)

META_LEARNER_PATH = "backend/models/meta_learner.joblib"
PROJECT_ROOT = Path(__file__).resolve().parents[2]
INSTITUTIONAL_PREDICTIONS_DB = PROJECT_ROOT / "backend" / "data" / "predictions.db"
_meta_learner_instance: Any | None = None
_prediction_logger = PredictionLogger()


def get_or_load_meta_learner(force_reload: bool = False) -> Any | None:
    global _meta_learner_instance
    if _meta_learner_instance is not None and not force_reload:
        return _meta_learner_instance
    if force_reload:
        _meta_learner_instance = None
    if os.path.exists(META_LEARNER_PATH):
        try:
            _meta_learner_instance = joblib.load(META_LEARNER_PATH)
            logger.info("Meta-learner cargado desde disco (force_reload=%s)", force_reload)
        except Exception as e:
            logger.warning("No se pudo cargar meta-learner: %s", e)
    return _meta_learner_instance


router = APIRouter(prefix="/api/v1/probabilistic", tags=["probabilistic"])
_fmp_client: FMPClient | None = None
_predictive_engine: Any | None = None
_sentiment_engine: SentimentEngine | None = None
_predictive_storage: PredictiveStorage | None = None
_feedback_engine: FeedbackCalibration | None = None
_cross_asset_engine: CrossAssetEngine | None = None
_catalyst_engine: CatalystNLPEngine | None = None
_volume_profile_engine: VolumeProfileEngine | None = None
_fear_greed_engine: FearGreedEngine | None = None
_fear_greed_storage: Any | None = None
_market_data_fetcher: MarketDataFetcher | None = None
_cnn_fetcher: Any | None = None
_alternative_fg_source: Any | None = None
_calibration_engine: Any | None = None
_ml_optimizer: Any | None = None
_correlation_analyzer: Any | None = None
_regime_engine: Any | None = None
_vol_surface_engine: VolatilitySurfaceEngine | None = None
_markov_engine: MarkovRegimeEngine | None = None


def _get_fmp_client() -> FMPClient:
    global _fmp_client
    if _fmp_client is None:
        _fmp_client = FMPClient()
    return _fmp_client


def _create_multimodal_predictive_engine() -> Any:
    try:
        from ..quant_engine.engines.predictive.multimodal_predictive import (
            MultimodalPredictiveEngine,
        )
    except ModuleNotFoundError as exc:
        if exc.name != "torch":
            raise
        missing_dependency = exc.name

        class MissingMultimodalPredictiveEngine:
            """Placeholder used when the optional torch stack is not installed."""

            def __init__(self) -> None:
                self.missing_dependency = missing_dependency

            def run_fusion_inference(self, *args: object, **kwargs: object) -> object:
                raise RuntimeError(
                    "MultimodalPredictiveEngine requires optional dependency 'torch'."
                )

        return MissingMultimodalPredictiveEngine()

    return MultimodalPredictiveEngine()


def _get_predictive_engine() -> Any:
    global _predictive_engine
    if _predictive_engine is None:
        _predictive_engine = _create_multimodal_predictive_engine()
    return _predictive_engine


def _get_sentiment_engine() -> SentimentEngine:
    global _sentiment_engine
    if _sentiment_engine is None:
        _sentiment_engine = SentimentEngine()
    return _sentiment_engine


def _get_predictive_storage() -> PredictiveStorage:
    global _predictive_storage
    if _predictive_storage is None:
        _predictive_storage = PredictiveStorage()
    return _predictive_storage


def _get_feedback_engine() -> FeedbackCalibration:
    global _feedback_engine
    if _feedback_engine is None:
        _feedback_engine = FeedbackCalibration(_get_predictive_storage())
    return _feedback_engine


def _get_cross_asset_engine() -> CrossAssetEngine:
    global _cross_asset_engine
    if _cross_asset_engine is None:
        _cross_asset_engine = CrossAssetEngine()
    return _cross_asset_engine


def _get_catalyst_engine() -> CatalystNLPEngine:
    global _catalyst_engine
    if _catalyst_engine is None:
        _catalyst_engine = CatalystNLPEngine()
    return _catalyst_engine


def _get_volume_profile_engine() -> VolumeProfileEngine:
    global _volume_profile_engine
    if _volume_profile_engine is None:
        _volume_profile_engine = VolumeProfileEngine()
    return _volume_profile_engine


def _get_fear_greed_engine() -> FearGreedEngine:
    global _fear_greed_engine
    if _fear_greed_engine is None:
        _fear_greed_engine = FearGreedEngine()
    return _fear_greed_engine


def _get_fear_greed_storage() -> Any:
    global _fear_greed_storage
    if _fear_greed_storage is None:
        _fear_greed_storage = get_fg_storage()
    return _fear_greed_storage


def _get_market_data_fetcher() -> MarketDataFetcher:
    global _market_data_fetcher
    if _market_data_fetcher is None:
        _market_data_fetcher = MarketDataFetcher(_get_fmp_client())
    return _market_data_fetcher


def _get_cnn_fetcher() -> Any:
    global _cnn_fetcher
    if _cnn_fetcher is None:
        _cnn_fetcher = get_cnn_fetcher()
    return _cnn_fetcher


def _get_alternative_fg_source() -> Any:
    global _alternative_fg_source
    if _alternative_fg_source is None:
        _alternative_fg_source = get_alternative_source(_get_fmp_client())
    return _alternative_fg_source


def _get_calibration_engine() -> Any:
    global _calibration_engine
    if _calibration_engine is None:
        _calibration_engine = get_calibration_engine()
    return _calibration_engine


def _get_ml_optimizer() -> Any:
    global _ml_optimizer
    if _ml_optimizer is None:
        _ml_optimizer = get_ml_optimizer()
    return _ml_optimizer


def _get_correlation_analyzer() -> Any:
    global _correlation_analyzer
    if _correlation_analyzer is None:
        _correlation_analyzer = get_correlation_analyzer(_get_fmp_client())
    return _correlation_analyzer


def _get_regime_engine() -> Any:
    global _regime_engine
    if _regime_engine is None:
        _regime_engine = get_regime_engine()
    return _regime_engine


def _get_vol_surface_engine() -> VolatilitySurfaceEngine:
    global _vol_surface_engine
    if _vol_surface_engine is None:
        _vol_surface_engine = VolatilitySurfaceEngine()
    return _vol_surface_engine


def _get_markov_engine() -> MarkovRegimeEngine:
    global _markov_engine
    if _markov_engine is None:
        _markov_engine = MarkovRegimeEngine()
    return _markov_engine


def _expand_chain_volume_oi_rows(
    sym: str, chain: list[Any], prev_oi_map: dict[tuple, int]
) -> list[dict]:
    """One row per option leg (call/put) for Agarwal volume–OI engine and OI snapshots."""
    rows: list[dict] = []
    for r in chain:
        exp = str(r.expiration)
        strike = float(r.strike)
        for opt_type, vol, oi in (
            ("call", int(r.call_volume or 0), int(r.call_oi or 0)),
            ("put", int(r.put_volume or 0), int(r.put_oi or 0)),
        ):
            key = (strike, opt_type, exp)
            prev_oi = prev_oi_map.get(key, oi)
            rows.append(
                {
                    "ticker": sym,
                    "expiration": exp,
                    "strike": strike,
                    "option_type": opt_type,
                    "volume": vol,
                    "open_interest": oi,
                    "previous_open_interest": prev_oi,
                }
            )
    return rows


def _expand_chain_dex_rows(sym: str, chain: list[Any], spot: float) -> list[dict]:
    """One row per leg with OI and delta for DeltaExposureEngine.
    Approximates delta if the data provider does not supply it."""
    rows: list[dict] = []
    for r in chain:
        strike = float(r.strike)

        # Pseudo-delta approximation
        moneyness = (strike - spot) / spot
        approx_delta_call = max(0.02, min(0.98, 0.5 - moneyness * 3.5))
        approx_delta_put = -(1 - approx_delta_call)

        coi = int(r.call_oi or 0)
        c_delta = float(r.call_delta) if r.call_delta is not None else approx_delta_call
        if coi > 0:
            rows.append(
                {
                    "ticker": sym,
                    "strike": strike,
                    "option_type": "call",
                    "delta": c_delta,
                    "open_interest": coi,
                    "spot_price": float(spot),
                }
            )

        poi = int(r.put_oi or 0)
        p_delta = float(r.put_delta) if r.put_delta is not None else approx_delta_put
        if poi > 0:
            rows.append(
                {
                    "ticker": sym,
                    "strike": strike,
                    "option_type": "put",
                    "delta": p_delta,
                    "open_interest": poi,
                    "spot_price": float(spot),
                }
            )
    return rows


def _build_gamma_flip_chain_dataframe(chain: list[Any], spot: float) -> pd.DataFrame:
    """Long-format chain (one row per call/put leg) for GammaFlipEngine."""
    rows: list[dict] = []
    for r in chain:
        coi = int(r.call_oi or 0)
        poi = int(r.put_oi or 0)
        if coi > 0 and r.call_gamma is not None:
            rows.append(
                {
                    "strike": float(r.strike),
                    "option_type": "call",
                    "gamma": float(r.call_gamma),
                    "open_interest": coi,
                    "current_spot": float(spot),
                }
            )
        if poi > 0 and r.put_gamma is not None:
            rows.append(
                {
                    "strike": float(r.strike),
                    "option_type": "put",
                    "gamma": float(r.put_gamma),
                    "open_interest": poi,
                    "current_spot": float(spot),
                }
            )
    return pd.DataFrame(rows)


def _downsample_gamma_profile(
    prices: np.ndarray[Any, Any], gammas: np.ndarray[Any, Any], max_points: int = 280
) -> tuple[list[float], list[float]]:
    n = len(prices)
    if n <= max_points:
        return prices.astype(float).tolist(), gammas.astype(float).tolist()
    idx = np.linspace(0, n - 1, num=max_points, dtype=int)
    return prices[idx].astype(float).tolist(), gammas[idx].astype(float).tolist()


def _build_shadow_delta_portfolio_df(
    chain: list[Any], spot: float, dte_years: float, r: float
) -> pd.DataFrame:
    """Long-format rows (CALL/PUT per strike) for ShadowDeltaEngine from OptionStrikeRow chain."""
    rows: list[dict] = []
    for row in chain:
        strike = float(row.strike)
        coi = float(row.call_oi or 0)
        poi = float(row.put_oi or 0)
        civ = row.call_iv
        piv = row.put_iv
        if coi > 0 and civ is not None and float(civ) > 1e-6:
            rows.append(
                {
                    "strike": strike,
                    "option_type": "CALL",
                    "iv": float(civ),
                    "open_interest": int(coi),
                    "quantity": float(coi),
                    "expiry": float(dte_years),
                    "r": float(r),
                }
            )
        if poi > 0 and piv is not None and float(piv) > 1e-6:
            rows.append(
                {
                    "strike": strike,
                    "option_type": "PUT",
                    "iv": float(piv),
                    "open_interest": int(poi),
                    "quantity": float(poi),
                    "expiry": float(dte_years),
                    "r": float(r),
                }
            )
    if not rows:
        return pd.DataFrame()
    return pd.DataFrame(rows).sort_values("strike").reset_index(drop=True)


def _gamma_flip_response_from_snap(sym: str, opt_snap: Any) -> GammaFlipResponse:
    """Build gamma-flip payload from an existing options snapshot (no extra HTTP)."""
    from ..quant_engine.engines.options.gamma_flip import GammaFlipEngine

    if not opt_snap.ok or not opt_snap.chain:
        return GammaFlipResponse(
            ticker=sym,
            ok=False,
            error=opt_snap.error or "Option chain unavailable",
            spot=float(opt_snap.spot or 0.0),
            as_of=opt_snap.as_of,
        )

    spot = float(opt_snap.spot or 0.0)
    if spot <= 0:
        return GammaFlipResponse(
            ticker=sym,
            ok=False,
            error="Invalid underlying spot for gamma profile.",
            as_of=opt_snap.as_of,
        )

    df_legs = _build_gamma_flip_chain_dataframe(opt_snap.chain, spot)
    if df_legs.empty or len(df_legs) < 4:
        return GammaFlipResponse(
            ticker=sym,
            ok=False,
            error="Insufficient option data (need open interest and gamma per leg).",
            spot=spot,
            as_of=opt_snap.as_of,
        )

    iv_atm = 0.22
    if opt_snap.iv_surface is not None and opt_snap.iv_surface.atm_iv is not None:
        iv_atm = float(opt_snap.iv_surface.atm_iv)

    dte_years = 30.0 / 365.0
    if opt_snap.iv_surface is not None and opt_snap.iv_surface.surface:
        dte_years = max(1.0 / 365.0, float(opt_snap.iv_surface.surface[0].dte) / 365.0)

    engine = GammaFlipEngine(
        df_legs,
        contract_size=100,
        T=float(dte_years),
        r=0.04,
        sigma=float(max(0.08, min(iv_atm, 1.5))),
        range_pct=0.18,
        n_points=140,
    )
    flip = engine.find_flip_point()
    regime_info = engine.volatility_regime()
    sens = engine.sensitivity_put_oi(0.10)
    prices, gammas = engine.gamma_profile()
    px, gy = _downsample_gamma_profile(prices, gammas)

    profile = [GammaFlipProfilePoint(price=p, net_gamma=g) for p, g in zip(px, gy, strict=False)]
    oi_by_strike = sorted(
        [
            GammaFlipOIByStrike(
                strike=float(r.strike),
                call_oi=float(r.call_oi or 0),
                put_oi=float(r.put_oi or 0),
            )
            for r in opt_snap.chain
        ],
        key=lambda x: x.strike,
    )

    zgl = opt_snap.gex_levels.zero_gamma_level if opt_snap.gex_levels else None
    fs = sens.get("flip_shocked")
    flip_shock = float(fs) if fs is not None else None

    return GammaFlipResponse(
        ticker=sym,
        ok=True,
        as_of=opt_snap.as_of,
        spot=spot,
        flip_point=float(flip) if flip is not None else None,
        flip_put_shock_10pct=flip_shock,
        regime=str(regime_info.get("regime", "UNKNOWN")),
        distance_pct=(
            float(regime_info["distance_pct"])
            if regime_info.get("distance_pct") is not None
            else None
        ),
        current_net_gamma=float(regime_info.get("current_gamma", 0.0)),
        interpretation=str(regime_info.get("interpretation", "")),
        gex_zero_gamma_level=float(zgl) if zgl is not None else None,
        profile=profile,
        oi_by_strike=oi_by_strike,
    )


def _shadow_delta_response_from_snap(
    sym: str,
    opt_snap: Any,
    *,
    prebuilt_portfolio_df: pd.DataFrame | None = None,
    spot_known: float | None = None,
    dte_years_known: float | None = None,
) -> ShadowDeltaResponse:
    """Shadow delta (skew-adjusted delta) from the same snapshot."""
    from ..quant_engine.engines.options.shadow_delta import (
        ShadowDeltaEngine,
    )

    try:
        if not opt_snap.ok or not opt_snap.chain:
            return ShadowDeltaResponse(
                ticker=sym,
                ok=False,
                error=opt_snap.error or "Option chain unavailable",
                spot=float(opt_snap.spot or 0.0),
                as_of=opt_snap.as_of,
            )

        spot = float(spot_known if spot_known is not None else (opt_snap.spot or 0.0))
        if spot <= 0:
            return ShadowDeltaResponse(
                ticker=sym,
                ok=False,
                error="Invalid underlying spot.",
                as_of=opt_snap.as_of,
            )

        if prebuilt_portfolio_df is not None and dte_years_known is not None:
            dte_years = float(dte_years_known)
            df_sd = prebuilt_portfolio_df
        else:
            dte_years = 30.0 / 365.0
            if opt_snap.iv_surface is not None and opt_snap.iv_surface.surface:
                dte_years = max(1.0 / 365.0, float(opt_snap.iv_surface.surface[0].dte) / 365.0)

            df_sd = _build_shadow_delta_portfolio_df(opt_snap.chain, spot, dte_years, 0.04)
        if df_sd.empty or len(df_sd) < 4:
            return ShadowDeltaResponse(
                ticker=sym,
                ok=False,
                error="Insufficient IV/OI data for shadow delta.",
                spot=spot,
                as_of=opt_snap.as_of,
            )

        eng = ShadowDeltaEngine(
            df_sd,
            spot_price=spot,
            default_expiry=float(dte_years),
            risk_free_rate=0.04,
            skew_window=2,
            regularize_skew=True,
            skew_cap=0.05,
            contract_size=100,
        )
        summary = eng.portfolio_summary()
        if len(summary) > 180:
            step = max(1, len(summary) // 180)
            summary = summary.iloc[::step].copy()

        row_models: list[ShadowDeltaRow] = []
        for _, r in summary.iterrows():
            dgp = r["delta_gap_pct"]
            try:
                dgp_f = float(dgp)
                if math.isnan(dgp_f):
                    dgp_f = 0.0
            except (TypeError, ValueError):
                dgp_f = 0.0
            row_models.append(
                ShadowDeltaRow(
                    strike=float(r["strike"]),
                    option_type=str(r["option_type"]),
                    iv=float(r["iv"]),
                    bs_delta=float(r["bs_delta"]),
                    shadow_delta=float(r["shadow_delta"]),
                    delta_gap=float(r["delta_gap"]),
                    delta_gap_pct=dgp_f,
                    skew_slope=float(r["skew_slope"]),
                    open_interest=float(r.get("open_interest", 0) or 0),
                )
            )

        net = eng.net_portfolio_delta()
        stress_df = eng.stress_test(-0.05)
        stress = ShadowDeltaStressSummary(
            shock_pct=-5.0,
            mean_abs_delta_error=float(stress_df["delta_error_naive"].abs().mean()),
            max_abs_delta_error=float(stress_df["delta_error_naive"].abs().max()),
            n_pct_error_over_5=int((stress_df["pct_error"].abs() > 5).sum()),
            n_legs=int(len(stress_df)),
        )

        return ShadowDeltaResponse(
            ticker=sym,
            ok=True,
            as_of=opt_snap.as_of,
            spot=spot,
            net_bs_delta=float(net["net_bs_delta"]),
            net_shadow_delta=float(net["net_shadow_delta"]),
            total_delta_gap=float(net["total_delta_gap"]),
            hedge_shares_needed=float(net["hedge_shares_needed"]),
            n_legs=int(net["n_options"]),
            rows=row_models,
            stress=stress,
        )
    except Exception as e:
        logger.warning("Shadow delta failed for %s: %s", sym, e)
        return ShadowDeltaResponse(ticker=sym, ok=False, error=str(e))


def _shared_predictive_portfolio_df(
    opt_snap: Any,
) -> tuple[float | None, float | None, pd.DataFrame | None]:
    """Spot, DTE (years), OI-weighted IV legs for shadow/zomma (single chain pass)."""
    if not opt_snap.ok or not opt_snap.chain:
        return None, None, None
    spot = float(opt_snap.spot or 0.0)
    if spot <= 0:
        return None, None, None
    dte_years = 30.0 / 365.0
    if opt_snap.iv_surface is not None and opt_snap.iv_surface.surface:
        dte_years = max(1.0 / 365.0, float(opt_snap.iv_surface.surface[0].dte) / 365.0)
    df_sd = _build_shadow_delta_portfolio_df(opt_snap.chain, spot, dte_years, 0.04)
    if df_sd.empty or len(df_sd) < 4:
        return spot, dte_years, None
    return spot, dte_years, df_sd


def _zomma_response_from_portfolio(
    sym: str, opt_snap: Any, df_sd: pd.DataFrame, spot: float
) -> ZommaAnalysisResponse:
    from ..quant_engine.math.options.zomma_engine import compute_zomma_bundle

    try:
        raw = compute_zomma_bundle(
            df_sd,
            spot,
            contract_size=100,
            vol_crush_pct=0.20,
            spot_range_pct=0.18,
            n_spot=46,
            n_iv=34,
            max_legs=160,
        )
        if not raw.get("ok"):
            return ZommaAnalysisResponse(
                ticker=sym,
                ok=False,
                error=str(raw.get("error", "zomma")),
                spot=spot,
                as_of=opt_snap.as_of,
            )
        gc = raw["gamma_vol_crush"]
        buckets = ZommaVolCrushBuckets(
            atm_zomma_negative=ZommaVolCrushPair(**gc["atm_zomma_neg"]),
            otm_zomma_positive=ZommaVolCrushPair(**gc["otm_zomma_pos"]),
        )
        top = [ZommaTopStrikeEntry(**x) for x in raw["top_strikes"]]
        return ZommaAnalysisResponse(
            ticker=sym,
            ok=True,
            as_of=opt_snap.as_of,
            spot=spot,
            current_iv=float(raw["current_iv"]),
            post_crush_iv=float(raw["post_crush_iv"]),
            vol_crush_pct=float(raw["vol_crush_pct"]),
            heatmap_spot_axis=[float(x) for x in raw["spot_axis"]],
            heatmap_iv_axis=[float(x) for x in raw["iv_axis"]],
            heatmap_z=raw["heatmap_z"],
            gamma_vol_crush=buckets,
            top_strikes=top,
        )
    except Exception as e:
        logger.warning("Zomma analysis failed for %s: %s", sym, e)
        return ZommaAnalysisResponse(
            ticker=sym,
            ok=False,
            error=str(e),
            spot=spot,
            as_of=getattr(opt_snap, "as_of", None),
        )


def _speed_instability_from_portfolio(
    sym: str, opt_snap: Any, df_sd: pd.DataFrame, spot: float
) -> SpeedInstabilityResponse:
    from ..quant_engine.engines.predictive.speed_instability_engine import (
        compute_speed_instability_payload,
    )

    try:
        raw = compute_speed_instability_payload(df_sd, float(spot), r=0.04)
        if not raw.get("ok"):
            return SpeedInstabilityResponse(
                ticker=sym,
                ok=False,
                error=str(raw.get("error", "speed")),
                spot=float(spot),
                as_of=opt_snap.as_of,
            )
        zones = [SpeedInstabilityZoneEntry.model_validate(z) for z in raw["zones"]]
        profile = [SpeedProfilePoint.model_validate(p) for p in raw["profile"]]
        sbs = [SpeedByStrikeEntry.model_validate(r) for r in raw["speed_by_strike"]]
        decay = [SpeedDecaySeries.model_validate(d) for d in raw["speed_decay"]]
        sc = [SpeedScatterPoint.model_validate(s) for s in raw["scatter"]]
        traps = [SpeedTrapRow.model_validate(t) for t in raw["gamma_traps"]]
        summ = SpeedInstabilitySummary.model_validate(raw["summary"])
        return SpeedInstabilityResponse(
            ticker=sym,
            ok=True,
            as_of=opt_snap.as_of,
            spot=float(raw["spot"]),
            summary=summ,
            zones=zones,
            profile=profile,
            speed_by_strike=sbs,
            speed_decay=decay,
            scatter=sc,
            gamma_traps=traps,
        )
    except Exception as e:
        logger.warning("Speed instability failed for %s: %s", sym, e)
        return SpeedInstabilityResponse(
            ticker=sym,
            ok=False,
            error=str(e),
            spot=float(spot),
            as_of=getattr(opt_snap, "as_of", None),
        )


def _zero_day_gamma_wall_from_snap(
    sym: str, opt_snap: Any, spot: float, dte_years: float
) -> ZeroDayGammaWallResponse:
    from ..quant_engine.engines.predictive.zero_day_engine import (
        compute_zero_day_payload,
    )

    try:
        if not opt_snap.chain:
            return ZeroDayGammaWallResponse(
                ticker=sym,
                ok=False,
                error="empty_chain",
                spot=float(spot),
                as_of=getattr(opt_snap, "as_of", None),
            )
        exp = opt_snap.expiries[0] if getattr(opt_snap, "expiries", None) else None
        raw = compute_zero_day_payload(
            opt_snap.chain,
            float(spot),
            float(dte_years),
            r=0.04,
            contract_multiplier=100,
            expiry_hint=exp,
            as_of_iso=getattr(opt_snap, "as_of", None),
        )
        if not raw.get("ok"):
            return ZeroDayGammaWallResponse(
                ticker=sym,
                ok=False,
                error=str(raw.get("error", "zero_day")),
                spot=float(spot),
                as_of=opt_snap.as_of,
            )
        zone_raw = raw.get("zone") or {}
        zone = (
            ZeroDayZoneSpan(
                x0=float(zone_raw["x0"]),
                x1=float(zone_raw["x1"]),
                kind=str(zone_raw.get("kind", "")),
            )
            if zone_raw
            else None
        )
        bars = [ZeroDayGexBar.model_validate(x) for x in raw["gex_bars"]]
        pins = [ZeroDayPinPoint.model_validate(x) for x in raw["pin_curve"]]
        alerts = [ZeroDayAlertEntry.model_validate(x) for x in raw["alerts"]]
        return ZeroDayGammaWallResponse(
            ticker=sym,
            ok=True,
            as_of=opt_snap.as_of,
            spot=float(raw["spot"]),
            minutes_to_close=float(raw["minutes_to_close"]),
            gamma_flip=float(raw["gamma_flip"]),
            call_wall=float(raw["call_wall"]),
            put_wall=float(raw["put_wall"]),
            total_gex_bn=float(raw["total_gex_bn"]),
            vanna_pressure_bn=float(raw["vanna_pressure_bn"]),
            charm_decay_mm=float(raw["charm_decay_mm"]),
            imbalance_ratio=raw.get("imbalance_ratio"),
            pinning_strike=float(raw["pinning_strike"]),
            pinning_prob=float(raw["pinning_prob"]),
            zone=zone,
            gex_bars=bars,
            pin_curve=pins,
            alerts=alerts,
        )
    except Exception as e:
        logger.warning("Zero-day gamma wall failed for %s: %s", sym, e)
        return ZeroDayGammaWallResponse(
            ticker=sym,
            ok=False,
            error=str(e),
            spot=float(spot),
            as_of=getattr(opt_snap, "as_of", None),
        )


def _tail_risk_smile_from_portfolio(
    sym: str, opt_snap: Any, df_sd: pd.DataFrame, spot: float, dte_years: float
) -> TailRiskSmileResponse:
    from ..quant_engine.engines.predictive.tail_risk_engine import (
        compute_tail_risk_payload,
    )

    try:
        raw = compute_tail_risk_payload(
            df_sd, float(spot), float(dte_years), r=0.04, as_of=getattr(opt_snap, "as_of", None)
        )
        if not raw.get("ok"):
            return TailRiskSmileResponse(
                ticker=sym,
                ok=False,
                error=str(raw.get("error", "tail_risk")),
                spot=float(spot),
                as_of=opt_snap.as_of,
            )
        obs = [TailRiskObservedPoint.model_validate(x) for x in raw["observed"]]
        spl = [TailRiskSplinePoint.model_validate(x) for x in raw["smile_spline"]]
        cur = [TailRiskCurvaturePoint.model_validate(x) for x in raw["curvature"]]
        return TailRiskSmileResponse(
            ticker=sym,
            ok=True,
            as_of=opt_snap.as_of,
            spot=float(raw["spot"]),
            metrics=TailRiskSmileMetricsBlock.model_validate(raw["metrics"]),
            alert=TailRiskAlertBlock.model_validate(raw["alert"]),
            risk_reversal=TailRiskReversalBlock.model_validate(raw["risk_reversal"]),
            observed=obs,
            smile_spline=spl,
            curvature=cur,
        )
    except Exception as e:
        logger.warning("Tail risk smile failed for %s: %s", sym, e)
        return TailRiskSmileResponse(
            ticker=sym,
            ok=False,
            error=str(e),
            spot=float(spot),
            as_of=getattr(opt_snap, "as_of", None),
        )


def _volatility_skew_from_portfolio(
    sym: str, opt_snap: Any, df_sd: pd.DataFrame, spot: float, dte_years: float
) -> VolatilitySkewResponse:
    from ..quant_engine.engines.predictive.volatility_skew_engine import (
        compute_volatility_skew_payload,
    )

    try:
        raw = compute_volatility_skew_payload(df_sd, float(spot), float(dte_years), r=0.04)
        if not raw.get("ok"):
            return VolatilitySkewResponse(
                ticker=sym,
                ok=False,
                error=str(raw.get("error", "skew")),
                spot=float(spot),
                as_of=opt_snap.as_of,
            )
        m = VolatilitySkewMetricsBlock.model_validate(raw["metrics"])
        mp = [VolatilitySkewMarketPoint.model_validate(x) for x in raw["market_points"]]
        fc = [VolatilitySkewFittedPoint.model_validate(x) for x in raw["fitted_curve"]]
        cv = [VolatilitySkewCurvaturePoint.model_validate(x) for x in raw["curvature"]]
        scn = [VolatilitySkewScenarioEntry.model_validate(x) for x in raw["scenarios"]]
        return VolatilitySkewResponse(
            ticker=sym,
            ok=True,
            as_of=opt_snap.as_of,
            spot=float(raw["spot"]),
            fit_model=str(raw.get("fit_model", "polynomial")),
            metrics=m,
            market_points=mp,
            fitted_curve=fc,
            curvature=cv,
            scenarios=scn,
        )
    except Exception as e:
        logger.warning("Volatility skew failed for %s: %s", sym, e)
        return VolatilitySkewResponse(
            ticker=sym,
            ok=False,
            error=str(e),
            spot=float(spot),
            as_of=getattr(opt_snap, "as_of", None),
        )


def _extract_term_structure_data(surface: list[Any], spot: float) -> list[dict]:
    """Extracts ATM IV per expiration date to build the Term Structure using the full IV Surface."""
    from datetime import datetime

    rows = []

    # Group by expiration
    exp_map = {}
    for r in surface:
        exp = str(r.expiration)
        if exp not in exp_map:
            exp_map[exp] = []
        exp_map[exp].append(r)

    today = datetime.now()

    for exp, options in exp_map.items():
        # Find closest strike to spot (ATM)
        closest = min(options, key=lambda x: abs(float(x.strike) - spot))

        # Approximate DTE
        try:
            exp_date = datetime.strptime(exp, "%Y-%m-%d")
            dte = (exp_date - today).days
            if dte <= 0:
                continue  # Skip 0DTE or expired options
        except:
            continue

        # Extract IV (average call/put if both exist, else fallback)
        civ = float(closest.call_iv) if closest.call_iv is not None else 0.0
        piv = float(closest.put_iv) if closest.put_iv is not None else 0.0

        iv_atm = 0.0
        if civ > 0 and piv > 0:
            iv_atm = (civ + piv) / 2.0
        elif civ > 0:
            iv_atm = civ
        elif piv > 0:
            iv_atm = piv

        if iv_atm > 0:
            rows.append({"snapshot_date": today.strftime("%Y-%m-%d"), "dte": dte, "iv_atm": iv_atm})

    return rows


def _build_df(ohlcv_raw: list[Any]) -> pd.DataFrame:
    """Build a clean DataFrame from FMP price objects."""
    df = pd.DataFrame([p.__dict__ for p in ohlcv_raw])
    if "adjClose" in df.columns:
        df["close"] = df["adjClose"].fillna(df["close"])
    df = df.rename(
        columns={"Open": "open", "High": "high", "Low": "low", "Close": "close", "Volume": "volume"}
    )
    for col in ["open", "high", "low", "close", "volume"]:
        if col in df.columns:
            df[col] = pd.to_numeric(df[col], errors="coerce")
    if "date" in df.columns:
        df = df.sort_values("date", ascending=True).reset_index(drop=True)
    return df.dropna(subset=["close"])


def _safe_float(value: object) -> float | None:
    try:
        out = float(value)  # type: ignore[arg-type]
        return out if math.isfinite(out) else None
    except (TypeError, ValueError):
        return None


def _normalize_ai_signal(raw_prob: float) -> float:
    """
    Normalizes a 3-class probability (where 0.33 is neutral)
    into a binary win probability (where 0.50 is neutral).
    """
    # 0.33 anchor -> 0.50
    if raw_prob >= 0.33:
        # Scale [0.33, 1.0] -> [0.5, 1.0]
        return 0.5 + 0.5 * (raw_prob - 0.33) / 0.67
    else:
        # Scale [0.0, 0.33] -> [0.0, 0.5]
        return 0.5 * (raw_prob / 0.33)


@router.get("/analysis/{symbol}", response_model=ProbabilisticResult)
async def get_probabilistic_analysis(
    symbol: str, vix_override: float | None = None, us10y_override: float | None = None
) -> ProbabilisticResult:
    """
    Comprehensive probabilistic analysis: EVT Tail Risk, Markov Regimes, and Kelly Sizing.
    """
    try:
        sym = symbol.upper().strip()
        # PHASE 2: Local Argentina Awareness
        is_argentina = sym.endswith(".BA")
        local_vol_metrics = None

        if is_argentina:
            logger.info(f"Local AR analysis detected for {sym}. Fetching Data912 context...")
            d912 = Data912Fetcher()
            # Determine instrument type (heuristic: Cedears vs Stocks)
            inst_type = "cedears" if len(sym) > 6 else "stocks"
            local_ohlcv = await d912.get_historical_ohlcv(inst_type, sym.replace(".BA", ""))
            local_vol_metrics = await d912.get_eod_volatilities(sym.replace(".BA", ""))

            if local_ohlcv:
                # Convert Data912HistoricalPoint to FMP-like object for _build_df
                class FmpLikeBar:
                    def __init__(self, p):
                        self.date = p.date
                        self.open = p.open
                        self.high = p.high
                        self.low = p.low
                        self.close = p.close
                        self.volume = p.volume
                        self.adjClose = p.close

                ohlcv_raw = [FmpLikeBar(p) for p in local_ohlcv]
            else:
                # Fallback to FMP if local fails
                date_from = (datetime.now() - timedelta(days=365)).strftime("%Y-%m-%d")
                ohlcv_raw = await _get_fmp_client().get_historical_prices(sym, date_from=date_from)
        else:
            date_from = (datetime.now() - timedelta(days=365)).strftime("%Y-%m-%d")
            ohlcv_raw = await _get_fmp_client().get_historical_prices(sym, date_from=date_from)

        if not ohlcv_raw:
            if not _get_fmp_client()._is_active():
                raise HTTPException(
                    status_code=503,
                    detail="FMPClient inactivo: no hay claves API configuradas. Verificá el archivo .env (FMP_KEY_QUOTES, etc.).",
                )
            raise HTTPException(
                status_code=404,
                detail=f"Sin datos históricos para {sym}. Verificá que el símbolo sea válido.",
            )

        df = _build_df(ohlcv_raw)
        returns = df["close"].pct_change().dropna().values
        current_spot = float(df["close"].iloc[-1])
        extra_meta: dict[str, Any] = {}

        # 1. Math Kernels
        tail_res_res = fit_gpd(returns)
        tail_res = tail_res_res.unwrap() if tail_res_res.is_success and tail_res_res.value else None
        
        state_res_res = run_particle_filter(df)
        state_res = state_res_res.unwrap() if state_res_res.is_success and state_res_res.value else None

        mjd_params_res = estimate_mjd_params(returns)
        mjd_params = mjd_params_res.unwrap() if mjd_params_res.is_success and mjd_params_res.value else {"jump_intensity": 0.0, "mu_j": 0.0, "sigma_j": 0.0, "jump_prob": 0.0}
        
        jump_res = JumpRisk(
            intensity=mjd_params.get("jump_intensity", 0.0),
            mu_j=mjd_params.get("mu_j", 0.0),
            sigma_j=mjd_params.get("sigma_j", 0.0),
            probability=mjd_params.get("jump_prob", 0.0),
        )

        # PHASE 0: Parallel Data Ingestion (Macro + Sentiment + GEX + Catalyst)

        # Define tasks that don't depend on each other
        macro_tasks = {
            "vix": _get_fmp_client().get_quote("^VIX"),
            "tnx": _get_fmp_client().get_quote("^TNX"),
            "social": _get_fmp_client().get_social_sentiment(sym, limit=5),
            "catalyst": (
                _get_catalyst_engine().analyze(sym, _get_fmp_client()) if not is_argentina else None
            ),
            "iv_history": (
                _get_fmp_client().get_options_iv_history(sym) if not is_argentina else None
            ),
        }

        task_names = list(macro_tasks.keys())
        task_coros = list(macro_tasks.values())

        # Execute concurrently with a safety timeout
        try:
            task_results = await asyncio.wait_for(
                asyncio.gather(*[c for c in task_coros if c is not None], return_exceptions=True),
                timeout=15.0,
            )
        except TimeoutError:
            logger.warning(
                f"Parallel ingestion for {sym} timed out after 15s. Using partial results."
            )
            task_results = [Exception("Timeout") for _ in range(len(task_coros))]

        # Map results back
        results_map = {}
        idx = 0
        for name in task_names:
            if macro_tasks[name] is not None:
                results_map[name] = task_results[idx]
                idx += 1
            else:
                results_map[name] = None

        # Process Macro
        vix_data = results_map.get("vix")
        vix_val = (
            vix_override
            if vix_override is not None
            else (vix_data.price if (vix_data and not isinstance(vix_data, Exception)) else 20.0)
        )

        us10y_data = results_map.get("tnx")
        us10y_val = (
            us10y_override
            if us10y_override is not None
            else (
                (us10y_data.price / 10.0)
                if (us10y_data and not isinstance(us10y_data, Exception))
                else 4.2
            )
        )

        # Process Sentiment
        social_data = results_map.get("social")
        sent_sig = (
            _get_sentiment_engine().analyze_social(social_data, sym)
            if not isinstance(social_data, Exception)
            else None
        )
        sent_score = sent_sig.sentiment_score if sent_sig else 0.5

        # Catalyst & IV results will be used later
        nlp_profile = results_map.get("catalyst")
        iv_history = results_map.get("iv_history")

        # PHASE 1: Real-time GEX Integration
        gex_data = {}
        opt_snap = None
        try:
            from .options_router import options_snapshot_service

            opt_snap = await options_snapshot_service(sym, expiry=None, r=0.04)
            if opt_snap.ok:
                gex_data = {
                    "total_gex": opt_snap.engine_signal.get("total_gex", 0.0),
                    "net_vanna_flow": opt_snap.engine_signal.get("total_vex", 0.0),
                    "dealer_bias": opt_snap.gex_levels.dealer_bias,
                    "squeeze_prob": opt_snap.gex_levels.squeeze_probability,
                }
        except Exception as e:
            logger.warning(f"GEX data fetch failed for {sym}: {e}")

        fusion = _get_predictive_engine().run_fusion_inference(
            symbol=sym, ohlcv_df=df, sentiment_score=sent_score, gex_data=gex_data
        )
        ai_conviction_raw = fusion.get("conviction", 0.33)

        # Normalize: ensure untrainted 1/3 split doesn't kill the signal
        ai_win_prob = _normalize_ai_signal(ai_conviction_raw)

        # Fused Win Prob = AI Direction (70%) + Market Regime (30%)
        pr_ordered = state_res.pr_ordered if state_res else 0.5
        win_prob = (0.7 * ai_win_prob) + (0.3 * pr_ordered)

        # 3. Dynamic Payoff & Sizing
        payoff_b_res = estimate_payoff_ratio(returns)
        payoff_b = payoff_b_res.unwrap() if payoff_b_res.is_success and payoff_b_res.value else 1.0
        
        _kelly_res = calculate_kelly_sizing(win_prob, payoff_b)
        _kelly = _kelly_res.unwrap() if _kelly_res.is_success and _kelly_res.value else None

        # Dynamic VoV Proxy: use rolling 30d std as IV proxy if actual IV is not available
        rolling_std = pd.Series(returns).rolling(30).std().fillna(np.std(returns)).values * np.sqrt(
            252.0
        )
        vov_res = calibrate_heston_vov(returns, rolling_std)
        vov = vov_res.unwrap() if vov_res.is_success and vov_res.value else 0.0
        
        tail_cvar = tail_res.cvar_99 if tail_res else 0.0
        etv = compute_etv(win_prob, payoff_b, jump_res.probability, tail_cvar)

        # Apply Anchoring
        adj_win_prob, adj_var = apply_macro_anchoring(
            vix_val, us10y_val, ai_conviction_raw, tail_res.var_99 if tail_res else 0.0
        )

        # PHASE 4: Cross-Asset Correlation (skip for AR locals to avoid FMP issues)
        cross_asset_summary: CrossAssetSummary | None = None
        if not is_argentina:
            try:
                date_from_ca = (datetime.now() - timedelta(days=300)).strftime("%Y-%m-%d")

                # Parallel Reference Fetches
                ref_tickers = [t for t in REFERENCE_ASSETS if t != sym]
                ref_tasks = [
                    _get_fmp_client().get_historical_prices(t, date_from=date_from_ca)
                    for t in ref_tickers
                ]
                ref_results = await asyncio.gather(*ref_tasks, return_exceptions=True)

                ref_prices: dict[str, list] = {}
                for t, res in zip(ref_tickers, ref_results, strict=False):
                    if res and not isinstance(res, Exception):
                        ref_prices[t] = [p.close for p in res if p.close]

                target_price_list = df["close"].dropna().tolist()
                ca_report = _get_cross_asset_engine().analyze(sym, target_price_list, ref_prices)

                # Convert dataclass → Pydantic for serialisation
                cross_asset_summary = CrossAssetSummary(
                    strongest_link=ca_report.strongest_link,
                    max_decoupling=ca_report.max_decoupling,
                    decoupling_alert=ca_report.decoupling_alert,
                    systematic_risk=ca_report.systematic_risk,
                    idiosyncratic_risk=ca_report.idiosyncratic_risk,
                    regime_label=ca_report.regime_label,
                    correlations=[CorrelationEntry(**p.__dict__) for p in ca_report.correlations],
                )
                logger.info(
                    f"Cross-asset analysis for {sym}: regime={ca_report.regime_label}, "
                    f"decoupling_alert={ca_report.decoupling_alert}"
                )
            except Exception as e:
                logger.warning(f"Cross-asset analysis failed for {sym}: {e}")

        # PHASE 5: Catalyst NLP (Event Risk Scoring)
        event_risk_summary: EventRiskSummary | None = None
        catalyst_jump_adj = 1.0
        fear_greed_result = None
        if not is_argentina:  # transcripts only available for US tickers
            try:
                nlp_profile = await _get_catalyst_engine().analyze(sym, _get_fmp_client())
                catalyst_jump_adj = nlp_profile.jump_intensity_adj

                event_risk_summary = EventRiskSummary(
                    event_risk_score=nlp_profile.event_risk_score,
                    tone=nlp_profile.tone,
                    tone_confidence=nlp_profile.tone_confidence,
                    jump_intensity_adj=nlp_profile.jump_intensity_adj,
                    transcript_summary=nlp_profile.transcript_summary,
                    bullish_hits=nlp_profile.bullish_hits,
                    bearish_hits=nlp_profile.bearish_hits,
                    alarming_hits=nlp_profile.alarming_hits,
                    news_count=nlp_profile.news_count,
                    news_sentiment=nlp_profile.news_sentiment,
                    upcoming_catalysts=[
                        UpcomingCatalystEntry(
                            event_type=c.event_type,
                            date=c.date,
                            days_until=c.days_until,
                            label=c.label,
                        )
                        for c in nlp_profile.upcoming_catalysts
                    ],
                    last_eps_surprise=nlp_profile.last_eps_surprise,
                    avg_eps_surprise=nlp_profile.avg_eps_surprise,
                )
                logger.info(
                    f"Catalyst NLP for {sym}: tone={nlp_profile.tone}, "
                    f"event_risk={nlp_profile.event_risk_score:.2f}, "
                    f"jump_adj={nlp_profile.jump_intensity_adj:.2f}"
                )

                # Compute Multi-Factor Fear & Greed Index
                try:
                    # Fetch real-time market data from FMP
                    market_data = await _get_market_data_fetcher().fetch_fear_greed_data()

                    fear_greed_result = await _get_fear_greed_engine().compute(
                        symbol=sym,
                        market_data=market_data,
                        event_risk_score=nlp_profile.event_risk_score,
                    )
                    logger.info(
                        f"Fear & Greed for {sym}: score={fear_greed_result.score:.1f}, "
                        f"label={fear_greed_result.label}, quality={fear_greed_result.data_quality}"
                    )

                    # Save to history for backtesting
                    try:
                        _get_fear_greed_storage().save(
                            symbol=sym,
                            score=fear_greed_result.score,
                            label=fear_greed_result.label,
                            data_quality=fear_greed_result.data_quality,
                            factors=fear_greed_result.factors,
                            event_risk_score=nlp_profile.event_risk_score,
                        )
                    except Exception as storage_error:
                        logger.debug(f"Failed to save FG history: {storage_error}")
                except Exception as fg_error:
                    logger.warning(f"Fear & Greed calculation failed for {sym}: {fg_error}")
                    fear_greed_result = None
            except Exception as e:
                logger.warning(f"Catalyst NLP failed for {sym}: {e}")

        # Apply catalyst jump adjustment to MJD parameters
        # This widens the jump distribution before building the result
        adjusted_jump_intensity = jump_res.intensity * catalyst_jump_adj
        adjusted_jump_res = JumpRisk(
            intensity=adjusted_jump_intensity,
            mu_j=jump_res.mu_j,
            sigma_j=jump_res.sigma_j,
            probability=min(1.0, jump_res.probability * catalyst_jump_adj),
        )

        # PHASE 6: Volume Profile (Liquidity Walls)
        vp_summary: VolumeProfileSummary | None = None
        try:
            vp_report = _get_volume_profile_engine().analyze(sym, df)
            vp_summary = VolumeProfileSummary(
                poc=vp_report.poc,
                vah=vp_report.vah,
                val=vp_report.val,
                hvn_levels=vp_report.hvn_levels,
                lvn_levels=vp_report.lvn_levels,
                nodes=[
                    VolumeNodeEntry(price=n.price, volume_pct=n.volume_pct, node_type=n.node_type)
                    for n in vp_report.profile
                ],
            )
            logger.info(
                f"Volume profile analyzed for {sym}: POC={vp_report.poc:.2f}, Nodes={vp_report.nodes_found}"
            )
        except Exception as e:
            logger.warning(f"Volume profile analysis failed for {sym}: {e}")

        # PHASE 7: Volatility Surface (Skew & Fear)
        vol_summary: VolatilitySurfaceSummary | None = None
        if iv_history and not isinstance(iv_history, Exception):
            try:
                vol_report = _get_vol_surface_engine().analyze(sym, iv_history)
                vol_summary = VolatilitySurfaceSummary(
                    current_skew=vol_report.current_skew,
                    skew_percentile=vol_report.skew_percentile,
                    fear_regime=vol_report.fear_regime,
                    put_call_iv_ratio=vol_report.put_call_iv_ratio,
                    risk_signal=vol_report.risk_signal,
                    historical_skew=[
                        SkewPointEntry(date=s.date, put_iv=s.put_iv, call_iv=s.call_iv, skew=s.skew)
                        for s in vol_report.historical_skew
                    ],
                )
                logger.info(f"Volatility skew analyzed for {sym}: regime={vol_report.fear_regime}")
            except Exception as e:
                logger.warning(f"Volatility surface analysis failed for {sym}: {e}")

        # PHASE 8: Markov Regime Switching
        markov_summary: MarkovRegimeSummary | None = None
        try:
            m_report = _get_markov_engine().analyze(sym, df)
            markov_summary = MarkovRegimeSummary(
                current_state=m_report.current_state,
                state_confidence=m_report.state_confidence,
                transition_risk=m_report.transition_risk,
                expected_days_in_state=m_report.expected_days_in_state,
                regime_signal=m_report.regime_signal,
                states=[
                    RegimeStateEntry(index=s.index, label=s.label, probability=s.probability)
                    for s in m_report.states
                ],
            )
            logger.info(
                f"Markov regime identified for {sym}: {m_report.current_state} (Conf: {m_report.state_confidence})"
            )
        except Exception as e:
            logger.warning(f"Markov regime analysis failed for {sym}: {e}")

        # PHASE 9: Expected Move (1-sigma ranges)
        em_summary: ExpectedMoveSummary | None = None
        try:
            # Determine IV to use
            if (
                not is_argentina
                and iv_history
                and not isinstance(iv_history, Exception)
                and len(iv_history) > 0
            ):
                latest_iv = iv_history[0]
                iv_val = (
                    (latest_iv.putIv + latest_iv.callIv) / 2.0
                    if latest_iv.callIv is not None
                    else (latest_iv.putIv or 0.20)
                )
            elif is_argentina and local_vol_metrics and local_vol_metrics.implied_vol:
                iv_val = local_vol_metrics.implied_vol
            else:
                # Fallback to 30d rolling vol (annualised)
                iv_val = (
                    float(np.std(returns[-30:]) * np.sqrt(252.0)) if len(returns) >= 30 else 0.25
                )

            horizons = []
            for dte in [7, 30, 45, 90]:
                em_res = ExpectedMoveEngine.calculate(spot=current_spot, iv=iv_val, dte=dte)
                horizons.append(
                    ExpectedMoveEntry(
                        timeframe=f"{dte}D",
                        dte=dte,
                        expected_move=em_res.expected_move,
                        upper_bound=em_res.upper_bound,
                        lower_bound=em_res.lower_bound,
                        iv=iv_val,
                    )
                )

            em_summary = ExpectedMoveSummary(spot=current_spot, horizons=horizons)
            logger.info(f"Expected move calculated for {sym} using IV={iv_val:.2%}")
        except Exception as e:
            logger.warning(f"Expected move calculation failed for {sym}: {e}")

        # PHASE 10: Skew and Fat Tails (Jarrow-Rudd)
        skew_res: SkewFatTailsSummary | None = None
        if not is_argentina:
            try:
                # We need a chain with at least 3 strikes
                if opt_snap and opt_snap.ok and len(opt_snap.chain) >= 3:
                    chain_rows = []
                    for r in opt_snap.chain:
                        chain_rows.append(
                            {
                                "strike": r.strike,
                                "iv_call": r.call_iv or 0.0,
                                "iv_put": r.put_iv or 0.0,
                            }
                        )
                    chain_df = pd.DataFrame(chain_rows)

                    # Get DTE from surface (default 30d)
                    dte_years = 30 / 365.0
                    if opt_snap.iv_surface is not None and opt_snap.iv_surface.surface:
                        dte_years = opt_snap.iv_surface.surface[0].dte / 365.0

                    skew_engine = SkewFatTailsEngine(
                        spot_price=current_spot,
                        risk_free_rate=0.04,
                        time_to_expiry=max(0.01, dte_years),
                    )
                    analysis = skew_engine.analyze(chain_df)

                    skew_res = SkewFatTailsSummary(
                        spot_price=analysis.spot_price,
                        atm_iv=analysis.atm_iv,
                        implied_skewness=analysis.implied_skewness,
                        tail_risk_factor=analysis.tail_risk_factor,
                        put_call_iv_spread=analysis.put_call_iv_spread,
                        risk_flag=analysis.risk_flag.value,
                        risk_score=analysis.risk_score,
                        flag_rationale=analysis.flag_rationale,
                        profile=[
                            SkewProfileEntry(
                                strike=row["strike"],
                                iv_call=row["iv_call"],
                                iv_put=row["iv_put"],
                                skew_spread=row["iv_put"] - row["iv_call"],
                                moneyness=row["moneyness"],
                            )
                            for _, row in analysis.skew_profile.iterrows()
                        ],
                    )
                    logger.info(
                        f"Skew/FatTails analysis completed for {sym}: flag={skew_res.risk_flag}"
                    )
            except Exception as e:
                logger.warning(f"Skew/FatTails analysis failed for {sym}: {e}")

        # PHASE 11: Delta-Weighted Flow (Capitulation Detection)
        dflow_res: DeltaFlowSummary | None = None
        if not is_argentina:
            try:
                if opt_snap and opt_snap.ok and opt_snap.chain:
                    flow_rows = []
                    for r in opt_snap.chain:
                        if r.call_volume and r.call_volume > 0:
                            flow_rows.append(
                                {
                                    "strike": r.strike,
                                    "type": "call",
                                    "volume": r.call_volume,
                                    "mark_price": r.call_last or 0.0,
                                    "delta": r.call_delta or 0.5,
                                }
                            )
                        if r.put_volume and r.put_volume > 0:
                            flow_rows.append(
                                {
                                    "strike": r.strike,
                                    "type": "put",
                                    "volume": r.put_volume,
                                    "mark_price": r.put_last or 0.0,
                                    "delta": r.put_delta or -0.5,
                                }
                            )

                    if flow_rows:
                        flow_df = pd.DataFrame(flow_rows)
                        flow_engine = DeltaWeightedFlow_Engine()

                        # Load History for Z-Score
                        history_ratios = _get_predictive_storage().get_recent_pc_ratios(
                            sym, limit=20
                        )
                        for ratio in history_ratios:
                            flow_engine._ratio_history.append(ratio)

                        snapshot = flow_engine.process_snapshot(flow_df)
                        dflow_res = DeltaFlowSummary(
                            total_call_flow=snapshot.total_call_flow,
                            total_put_flow=snapshot.total_put_flow,
                            pc_flow_ratio=snapshot.pc_flow_ratio,
                            z_score=snapshot.z_score,
                            signal=snapshot.signal.name,
                            rolling_mean=snapshot.rolling_mean,
                            rolling_std=snapshot.rolling_std,
                        )
                        logger.info(
                            f"Delta Flow analyzed for {sym}: signal={dflow_res.signal}, Z={dflow_res.z_score}"
                        )
            except Exception as e:
                logger.warning(f"Delta Flow analysis failed for {sym}: {e}")

        # PHASE 12: COR3M (Systemic Correlation Risk)
        cor3m_res: COR3MSummary | None = None
        try:
            # Fetch COR3M (CBOE 3-Month Implied Correlation)
            date_from_cor = (datetime.now() - timedelta(days=500)).strftime("%Y-%m-%d")
            # Try COR3M first, then ^COR3M
            cor_data_raw = await _get_fmp_client().get_historical_prices(
                "COR3M", date_from=date_from_cor
            )
            if not cor_data_raw:
                cor_data_raw = await _get_fmp_client().get_historical_prices(
                    "^COR3M", date_from=date_from_cor
                )

            if cor_data_raw:
                cor_df = pd.DataFrame([p.__dict__ for p in cor_data_raw])
                if not cor_df.empty and "close" in cor_df.columns:
                    cor_df = cor_df.sort_values("date").reset_index(drop=True)
                    cor_engine = COR3M_Signal_Engine()
                    # Run engine over historical series
                    cor_results = cor_engine.run(cor_df["close"])

                    if not cor_results.empty:
                        last_bar = cor_results.iloc[-1]
                        ms = last_bar["market_state"]
                        market_state_str = (
                            ms if isinstance(ms, str) else str(getattr(ms, "name", ms))
                        )
                        sig = last_bar["signal"]
                        signal_str = (
                            sig if isinstance(sig, str) else str(getattr(sig, "value", sig))
                        )
                        cor3m_res = COR3MSummary(
                            cor3m_value=float(last_bar["cor3m"]),
                            percentile_rank=float(last_bar["percentile_rank"]),
                            market_state=market_state_str,
                            signal=signal_str,
                            bars_since_panic=int(last_bar["bars_since_panic"]),
                            note=str(last_bar["note"]),
                        )
                        logger.info(
                            f"COR3M analyzed: State={cor3m_res.market_state}, Rank={cor3m_res.percentile_rank:.1%}"
                        )
        except Exception as e:
            logger.warning(f"COR3M analysis failed: {e}")

        # Default placeholder to ensure UI visibility
        if not cor3m_res:
            cor3m_res = COR3MSummary(
                cor3m_value=0.0,
                percentile_rank=0.5,
                market_state="NORMAL",
                signal="NEUTRAL",
                bars_since_panic=0,
                note="Data unavailable for COR3M index.",
            )

        # PHASE 13: Squeeze Ignition Detection
        squeeze_res: SqueezeSummary | None = None
        if not is_argentina:
            try:
                # 1. Underlying Data (needs Short Interest)
                si_data = await _get_fmp_client().get_short_interest(sym)
                si_ratio = 0.0
                dtc = 0.0
                if si_data and len(si_data) > 0:
                    si_ratio = (
                        (si_data[0].shortInterest / si_data[0].float) * 100.0
                        if si_data[0].float
                        else 0.0
                    )
                    dtc = si_data[0].daysToCover or 0.0

                vol_tail = (
                    df["volume"].tail(20) if "volume" in df.columns else pd.Series(dtype=float)
                )
                vol_sma_20 = float(vol_tail.mean()) if len(vol_tail) > 0 else 0.0
                prev_close = float(df["close"].iloc[-2]) if len(df) > 1 else current_spot
                last_vol = float(df["volume"].iloc[-1]) if "volume" in df.columns else 0.0

                underlying_prep = UnderlyingData(
                    ticker=sym,
                    spot_price=current_spot,
                    prev_spot_price=prev_close,
                    volume=last_vol,
                    volume_sma_20=vol_sma_20,
                    short_interest_ratio=si_ratio,
                    days_to_cover=dtc,
                )

                # 2. Options Data
                call_vol = 0.0
                call_oi = 0.0
                pcr_vol = 0.0
                if opt_snap and opt_snap.ok:
                    put_v = 0.0
                    for r in opt_snap.chain:
                        call_vol += float(r.call_volume or 0)
                        call_oi += float(r.call_oi or 0)
                        put_v += float(r.put_volume or 0)
                    if call_vol > 0:
                        pcr_vol = put_v / call_vol

                options_prep = OptionChainData(
                    call_volume=call_vol,
                    call_volume_sma_20=call_vol,  # Simplified
                    call_open_interest=call_oi,
                    put_call_ratio_volume=pcr_vol,
                    dealer_net_gamma=fusion.get("total_gex", 0.0),
                    call_wall_level=fusion.get("call_wall", current_spot * 1.05),
                    gamma_zero_level=fusion.get("gamma_zero", current_spot),
                )

                # 3. Init Engine & Load State
                sq_engine = SqueezeIgnitionEngine(ticker=sym, verbose=False)
                prev_sq = _get_predictive_storage().get_last_squeeze_state(sym)
                if prev_sq:
                    try:
                        sq_engine._state = SqueezeState[prev_sq["state"]]
                        sq_engine._cooling_counter = prev_sq.get("cooling_count", 0)
                        sq_engine._ignition_price = prev_sq.get("ignition_price")
                    except:
                        pass

                signal = sq_engine.evaluate(underlying_prep, options_prep)

                squeeze_res = SqueezeSummary(
                    state=signal.state.name,
                    vulnerability_score=signal.squeeze_vulnerability_score,
                    signal_type=signal.signal_type.name,
                    trigger_reasons=signal.trigger_reasons,
                    spot_price=signal.spot_price,
                    call_wall_level=signal.call_wall_level,
                    suggested_entry=signal.suggested_entry,
                    take_profit_levels=signal.take_profit_levels,
                    notes=signal.notes,
                )

                # Update extra_meta for persistence
                extra_meta["squeeze_state"] = signal.state.name
                extra_meta["squeeze_cooling_count"] = sq_engine._cooling_counter
                extra_meta["squeeze_ignition_price"] = sq_engine._ignition_price

                logger.info(
                    f"Squeeze analyzed for {sym}: state={squeeze_res.state}, score={squeeze_res.vulnerability_score}"
                )
            except Exception as e:
                logger.warning(f"Squeeze analysis failed for {sym}: {e}")

        # PHASE 14: Volume/OI Dynamics (Agarwal framework)
        volume_oi_res: VolumeOISummary | None = None
        if not is_argentina:
            try:
                if opt_snap and opt_snap.ok:
                    prev_oi_map = _get_predictive_storage().get_last_oi_snapshot(sym)
                    chain_rows = _expand_chain_volume_oi_rows(sym, opt_snap.chain, prev_oi_map)

                    if chain_rows:
                        chain_df = pd.DataFrame(chain_rows)
                        voi_analyzer = OptionsMarketAnalyzer()
                        analysis_df = voi_analyzer.analyze_volume_oi_dynamics(chain_df)

                        total_vol = analysis_df["volume"].sum()
                        if total_vol > 0:
                            entry_vol = analysis_df[
                                analysis_df["signal_type"] == "New Position / Institutional Entry"
                            ]["volume"].sum()
                            spec_vol = analysis_df[
                                analysis_df["signal_type"] == "Day Trading / Speculation"
                            ]["volume"].sum()
                            liq_vol = analysis_df[
                                analysis_df["signal_type"] == "Profit Taking / Closing"
                            ]["volume"].sum()

                            top_df = analysis_df.sort_values("volume", ascending=False).head(12)
                            top_entries = []
                            for _, row in top_df.iterrows():
                                top_entries.append(
                                    StrikeDynamicsEntry(
                                        strike=float(row["strike"]),
                                        option_type=str(row["option_type"]),
                                        volume=int(row["volume"]),
                                        net_oi_change=int(row["net_oi_change"]),
                                        signal_type=str(row["signal_type"]),
                                        volume_oi_ratio=float(
                                            row["volume_oi_ratio"]
                                            if not pd.isna(row["volume_oi_ratio"])
                                            else 0.0
                                        ),
                                    )
                                )

                            volume_oi_res = VolumeOISummary(
                                institutional_entry_pct=float(entry_vol / total_vol),
                                speculation_pct=float(spec_vol / total_vol),
                                liquidation_pct=float(liq_vol / total_vol),
                                top_dynamics=top_entries,
                                note=f"Analyzed {len(analysis_df)} strikes. Dominant: {analysis_df['signal_type'].mode()[0]}",
                            )
                            # Save snapshot for next analysis (OI only per leg)
                            oi_snapshot = [
                                {
                                    "strike": c["strike"],
                                    "option_type": c["option_type"],
                                    "expiration": c["expiration"],
                                    "open_interest": int(c["open_interest"] or 0),
                                }
                                for c in chain_rows
                                if int(c.get("open_interest") or 0) > 0
                            ]
                            if oi_snapshot:
                                _get_predictive_storage().save_option_oi_snapshot(sym, oi_snapshot)
            except Exception as e:
                logger.warning(f"Volume/OI analysis failed for {sym}: {e}")

        # Default placeholder to ensure UI visibility for US stocks
        if not volume_oi_res and not is_argentina:
            volume_oi_res = VolumeOISummary(
                institutional_entry_pct=0.0,
                speculation_pct=1.0,
                liquidation_pct=0.0,
                note="Awaiting historical OI data to compute dynamics.",
            )

        # Default placeholder to ensure UI visibility for US stocks
        if not squeeze_res and not is_argentina:
            squeeze_res = SqueezeSummary(
                state="MONITORING",
                vulnerability_score=0.0,
                signal_type="NONE",
                trigger_reasons=[],
                spot_price=current_spot,
                call_wall_level=current_spot * 1.05,
                notes="Short interest or option-flow data unavailable for squeeze detection.",
            )

        # PHASE 15: Delta Exposure (DEX)
        dex_res: DEXSummary | None = None
        if not is_argentina:
            try:
                if opt_snap and opt_snap.ok:
                    chain_rows = _expand_chain_dex_rows(sym, opt_snap.chain, current_spot)

                    if chain_rows:
                        dex_df = pd.DataFrame(chain_rows)
                        # Remove rows with missing delta if any
                        dex_df = dex_df[dex_df["delta"] != 0.0]
                        if not dex_df.empty:
                            dex_engine = DeltaExposureEngine(dex_df)
                            dex_calc = dex_engine.compute(sym)

                            profile_by_strike = [
                                DEXStrikeEntry(strike=float(row.strike), dex_net=float(row.dex_net))
                                for _, row in dex_calc.dex_by_strike.iterrows()
                            ]

                            cum_profile = []
                            # Approximate cumulative profile (descending strike to spot area)
                            # Actually, we can just use the dex_profile which has dex_net per pct.
                            # But since we just want a cumulative chart by strike, we can cumulatively sum the bars.
                            sorted_bars = dex_calc.dex_by_strike.sort_values(
                                "strike", ascending=True
                            )
                            cum_val = 0.0
                            for _, row in sorted_bars.iterrows():
                                cum_val += float(row.dex_net)
                                cum_profile.append(
                                    DEXStrikeEntry(strike=float(row.strike), dex_net=cum_val)
                                )

                            gamma_flip = float(fusion.get("gamma_zero", current_spot))

                            dex_res = DEXSummary(
                                total_dex_nominal=float(dex_calc.dex_total_nominal),
                                dex_calls=float(dex_calc.dex_calls),
                                dex_puts=float(dex_calc.dex_puts),
                                gamma_flip_level=gamma_flip,
                                profile_by_strike=profile_by_strike,
                                cumulative_profile=cum_profile,
                                note=f"Total DEX: ${dex_calc.dex_total_nominal:,.0f}",
                            )
            except Exception as e:
                logger.warning(f"DEX analysis failed for {sym}: {e}")

        # Default placeholder for UI visibility
        if not dex_res and not is_argentina:
            dex_res = DEXSummary(
                total_dex_nominal=0.0,
                dex_calls=0.0,
                dex_puts=0.0,
                gamma_flip_level=current_spot,
                note="Awaiting complete option chain with greeks to compute DEX.",
            )

        # PHASE 16: Volatility Term Structure
        vol_term_res: VolTermSummary | None = None
        if not is_argentina:
            try:
                if (
                    opt_snap
                    and opt_snap.ok
                    and opt_snap.iv_surface is not None
                    and opt_snap.iv_surface.surface
                ):
                    ts_rows = _extract_term_structure_data(
                        opt_snap.iv_surface.surface, current_spot
                    )
                    if (
                        ts_rows and len(ts_rows) >= 2
                    ):  # need at least a couple points to interpolate
                        ts_df = pd.DataFrame(ts_rows)
                        vol_engine = VolatilityTermStructureEngine()
                        vol_engine.load_option_chain(ts_df)
                        vol_engine.build_term_structure()
                        vol_engine.compute_metrics()

                        alerts = vol_engine.generate_alerts()

                        # Extract the interpolated curve
                        latest_ts = vol_engine._term_structure.iloc[-1]
                        curve_data = []
                        for tenor in vol_engine.standard_tenors:
                            col_name = f"iv_{tenor}d"
                            if col_name in latest_ts:
                                curve_data.append(
                                    VolTermStrikeEntry(
                                        tenor_days=tenor, iv=float(latest_ts[col_name])
                                    )
                                )

                        vol_term_res = VolTermSummary(
                            regime=str(alerts["regime"]),
                            inversion_alert=bool(alerts["inversion_alert"]),
                            slope_bps=float(alerts["slope_bps"]),
                            ratio=float(alerts["ratio"]),
                            flat_warning=bool(alerts["flat_warning"]),
                            curve=curve_data,
                            summary_msg=str(alerts["summary_msg"]),
                        )
            except Exception as e:
                logger.warning(f"Vol Term Structure analysis failed for {sym}: {e}")

        # Default placeholder
        if not vol_term_res and not is_argentina:
            vol_term_res = VolTermSummary(
                regime="AWAITING DATA",
                inversion_alert=False,
                slope_bps=0.0,
                ratio=1.0,
                flat_warning=False,
                summary_msg="Awaiting sufficient option chain data to build term structure.",
            )

        result = ProbabilisticResult(
            ticker=sym,
            tail=TailRiskModel(
                shape=float(tail_res.shape),
                scale=float(tail_res.scale),
                threshold=float(tail_res.threshold),
                var_99=float(tail_res.var_99),
                cvar_99=float(tail_res.cvar_99),
            ),
            jump=adjusted_jump_res,
            state=AdaptiveStateModel(
                pr_ordered=float(state_res.pr_ordered),
                trend_strength=float(state_res.trend_strength),
            ),
            vov=vov,
            etv=etv,
            kelly_prob=adj_win_prob,
            is_ordered_gate=state_res.pr_ordered > 0.55,
            is_jump_gate=adjusted_jump_res.probability < 0.05,
            gex_gating_safe=fusion.get("fusion_metadata", {}).get("gex_gating_safe", True),
            dealer_bias=gex_data.get("dealer_bias", "NEUTRAL"),
            is_local_ar=is_argentina,
            vix=vix_val,
            us10y=us10y_val,
            cross_asset=cross_asset_summary,
            event_risk=event_risk_summary,
            volume_profile=vp_summary,
            volatility_surface=vol_summary,
            markov_regime=markov_summary,
            expected_move=em_summary,
            skew_fat_tails=skew_res,
            delta_flow=dflow_res,
            cor3m=cor3m_res,
            squeeze_ignition=squeeze_res,
            volume_oi_dynamics=volume_oi_res,
            dex_exposure=dex_res,
            vol_term_structure=vol_term_res,
            gate_veto=not (
                state_res.pr_ordered > 0.55
                and adjusted_jump_res.probability < 0.05
                and fusion.get("fusion_metadata", {}).get("gex_gating_safe", True)
                and vix_val < 35.0
                and (skew_res.risk_flag != "RISK_AVOID" if skew_res else True)
            ),
        )

        # PERSISTENCE: Save result for lookback/backtesting
        # Include current price in the raw metadata for the next cycle
        current_price = float(df["close"].iloc[-1])
        result_dict = result.model_dump()
        result_dict["context_price"] = current_price
        _get_predictive_storage().save_analysis(
            result,
            extra_metadata={**extra_meta, "context_price": current_price},
        )

        # REAL-TIME ALERTS: Trigger notification if VETO is active
        if result.gate_veto:
            reason = (
                "Regime Chaotic"
                if not result.is_ordered_gate
                else (
                    "High Jump Probability"
                    if not result.is_jump_gate
                    else (
                        "GEX Unstable"
                        if not result.gex_gating_safe
                        else "Extreme Macro Panic" if result.vix > 35 else "Institutional Risk"
                    )
                )
            )

            await notification_service.notify_veto(
                symbol=sym,
                reason=reason,
                metrics={
                    "Pr(Ordered)": f"{(result.state.pr_ordered * 100):.1f}%",
                    "Jump Prob": f"{(result.jump.probability * 100):.1f}%",
                    "VIX": f"{result.vix:.2f}",
                    "Dealer Bias": result.dealer_bias,
                    "GEX Safe": "YES" if result.gex_gating_safe else "NO",
                    "VaR 99%": f"{(result.tail.var_99 * 100):.2f}%",
                },
            )

        return result
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e)) from e


@router.get("/history/{symbol}")
async def get_analysis_history(symbol: str, limit: int = 50) -> list[dict]:
    """Retrieve historical probabilistic analyses for lookback."""
    try:
        return _get_predictive_storage().get_history(symbol, limit)
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e)) from e


@router.get("/gamma-flip/{symbol}", response_model=GammaFlipResponse)
async def get_gamma_flip(symbol: str) -> GammaFlipResponse:
    """
    Dealer net-gamma profile vs hypothetical spot, flip point, and OI by strike
    (Predictive Options 2). Uses the same option snapshot as the options terminal.
    """
    sym = symbol.upper().strip()
    if sym.endswith(".BA"):
        return GammaFlipResponse(
            ticker=sym,
            ok=False,
            error="Gamma flip analysis is not available for .BA local symbols.",
        )
    try:
        from .options_router import options_snapshot_service

        opt_snap = await options_snapshot_service(sym, expiry=None, r=0.04)
        return _gamma_flip_response_from_snap(sym, opt_snap)
    except Exception as e:
        logger.warning("Gamma flip failed for %s: %s", sym, e)
        return GammaFlipResponse(
            ticker=sym,
            ok=False,
            error=str(e),
        )


@router.get("/predictive-options-2/{symbol}", response_model=PredictiveOptions2Bundle)
async def get_predictive_options_2(symbol: str) -> PredictiveOptions2Bundle:
    """
    Single chain fetch: Gamma Flip + Shadow Delta (Predictive Options 2 tab).
    """
    sym = symbol.upper().strip()
    if sym.endswith(".BA"):
        msg = "Analysis not available for .BA local symbols."
        zbad = ZommaAnalysisResponse(ticker=sym, ok=False, error=msg)
        sbad = SpeedInstabilityResponse(ticker=sym, ok=False, error=msg)
        vbad = VolatilitySkewResponse(ticker=sym, ok=False, error=msg)
        trbad = TailRiskSmileResponse(ticker=sym, ok=False, error=msg)
        zdbad = ZeroDayGammaWallResponse(ticker=sym, ok=False, error=msg)
        return PredictiveOptions2Bundle(
            gamma_flip=GammaFlipResponse(ticker=sym, ok=False, error=msg),
            shadow_delta=ShadowDeltaResponse(ticker=sym, ok=False, error=msg),
            zomma=zbad,
            speed_instability=sbad,
            volatility_skew=vbad,
            tail_risk_smile=trbad,
            zero_day_gamma_wall=zdbad,
        )
    try:
        from .options_router import options_snapshot_service

        opt_snap = await options_snapshot_service(sym, expiry=None, r=0.04)
        gf = _gamma_flip_response_from_snap(sym, opt_snap)
        spot_c, dte_c, df_sd = _shared_predictive_portfolio_df(opt_snap)
        if df_sd is None:
            sd = _shadow_delta_response_from_snap(sym, opt_snap)
            zm = ZommaAnalysisResponse(
                ticker=sym,
                ok=False,
                error="Insufficient IV/OI data for portfolio greeks.",
                spot=float(spot_c or opt_snap.spot or 0.0),
                as_of=opt_snap.as_of,
            )
            sp = SpeedInstabilityResponse(
                ticker=sym,
                ok=False,
                error="Insufficient IV/OI data for portfolio greeks.",
                spot=float(spot_c or opt_snap.spot or 0.0),
                as_of=opt_snap.as_of,
            )
            vk = VolatilitySkewResponse(
                ticker=sym,
                ok=False,
                error="Insufficient IV/OI data for portfolio greeks.",
                spot=float(spot_c or opt_snap.spot or 0.0),
                as_of=opt_snap.as_of,
            )
            tr = TailRiskSmileResponse(
                ticker=sym,
                ok=False,
                error="Insufficient IV/OI data for portfolio greeks.",
                spot=float(spot_c or opt_snap.spot or 0.0),
                as_of=opt_snap.as_of,
            )
        else:
            sd = _shadow_delta_response_from_snap(
                sym,
                opt_snap,
                prebuilt_portfolio_df=df_sd,
                spot_known=spot_c,
                dte_years_known=dte_c,
            )
            zm = _zomma_response_from_portfolio(sym, opt_snap, df_sd, float(spot_c))
            sp = _speed_instability_from_portfolio(sym, opt_snap, df_sd, float(spot_c))
            vk = _volatility_skew_from_portfolio(sym, opt_snap, df_sd, float(spot_c), float(dte_c))
            tr = _tail_risk_smile_from_portfolio(sym, opt_snap, df_sd, float(spot_c), float(dte_c))
        spot_z = float(spot_c or getattr(opt_snap, "spot", None) or 0.0)
        dte_z = float(dte_c) if dte_c is not None else 30.0 / 365.0
        zd = _zero_day_gamma_wall_from_snap(sym, opt_snap, spot_z, dte_z)
        return PredictiveOptions2Bundle(
            gamma_flip=gf,
            shadow_delta=sd,
            zomma=zm,
            speed_instability=sp,
            volatility_skew=vk,
            tail_risk_smile=tr,
            zero_day_gamma_wall=zd,
        )
    except Exception as e:
        logger.warning("predictive-options-2 failed for %s: %s", sym, e)
        err = str(e)
        zerr = ZommaAnalysisResponse(ticker=sym, ok=False, error=err)
        sperr = SpeedInstabilityResponse(ticker=sym, ok=False, error=err)
        vkerr = VolatilitySkewResponse(ticker=sym, ok=False, error=err)
        trerr = TailRiskSmileResponse(ticker=sym, ok=False, error=err)
        zderr = ZeroDayGammaWallResponse(ticker=sym, ok=False, error=err)
        return PredictiveOptions2Bundle(
            gamma_flip=GammaFlipResponse(ticker=sym, ok=False, error=err),
            shadow_delta=ShadowDeltaResponse(ticker=sym, ok=False, error=err),
            zomma=zerr,
            speed_instability=sperr,
            volatility_skew=vkerr,
            tail_risk_smile=trerr,
            zero_day_gamma_wall=zderr,
        )


# ─────────────────────────────────────────────────────────────────────────────
# PRICE TARGETS BANNER — Precios esperados con IA a horizontes múltiples
# ─────────────────────────────────────────────────────────────────────────────

_PRICE_TARGETS_CACHE: dict[str, dict] = {}
_PRICE_TARGETS_HORIZONS = [1, 2, 3, 5, 7, 10, 14, 20, 30]


def _determine_bias(expected_pct: float, jump_prob: float, regime_ordered: float) -> str:
    """Determina sesgo direccional para un horizonte dado."""
    if jump_prob > 0.12:
        return "HIGH_RISK"
    if regime_ordered < 0.4:
        return "NEUTRAL"
    if expected_pct > 0.005:
        return "BULLISH"
    if expected_pct < -0.005:
        return "BEARISH"
    return "NEUTRAL"


def _build_price_targets_prompt(
    sym: str,
    current_price: float,
    horizons_data: list[dict],
    engine_snapshot: dict,
) -> str:
    """Construye el prompt institucional para el agente microstructure."""
    return build_price_targets_evidence_prompt(
        sym,
        current_price,
        horizons_data,
        engine_snapshot,
    )


@router.get("/price-targets/{symbol}")
async def get_price_targets(symbol: str, include_ai: bool = False) -> dict[str, Any]:
    """
    Motor de Proyección Universal: Integra EVT, MJD, Heston, Markov, Particle Filter,
    GEX/DEX, Squeeze, Sentiment y VolTerm para generar targets probabilísticos.
    """
    try:
        sym = symbol.upper().strip()
        is_argentina = sym.endswith(".BA")

        # ── 0. Cache hit ───────────────────────────────────────────────────────
        cache_key = f"{sym}_universal_{include_ai}"
        cache_entry = _PRICE_TARGETS_CACHE.get(cache_key)
        if cache_entry:
            import time as _time

            if _time.time() - cache_entry.get("_cached_at", 0) < 480:
                payload = dict(cache_entry)
                payload.pop("_cached_at", None)
                return payload

        # ── 1. Recolección de Datos Base ───────────────────────────────────────
        date_from = (datetime.now() - timedelta(days=365)).strftime("%Y-%m-%d")

        tasks = [_get_fmp_client().get_historical_prices(sym, date_from=date_from)]
        if not is_argentina:
            from .options_router import options_snapshot_service

            tasks.append(options_snapshot_service(sym, expiry=None, r=0.04))
            tasks.append(_get_fmp_client().get_stock_news(sym, limit=15))

        results = await asyncio.gather(*tasks, return_exceptions=True)
        ohlcv_raw = results[0] if not isinstance(results[0], Exception) else []
        options_snapshot = (
            results[1] if len(results) > 1 and not isinstance(results[1], Exception) else None
        )
        _news_raw = results[2] if len(results) > 2 and not isinstance(results[2], Exception) else []

        if not ohlcv_raw:
            raise HTTPException(status_code=404, detail=f"Sin datos para {sym}")

        df = _build_df(ohlcv_raw)
        returns = df["close"].pct_change().dropna().values
        current_price = float(df["close"].iloc[-1])

        # ── 2. Ejecución Paralela de Motores Probabilísticos ──────────────────
        # Definimos los motores que no dependen de I/O externo adicional
        async def run_engines():
            # Cálculos matemáticos locales (rápidos)
            tail_task = asyncio.to_thread(fit_gpd, returns)
            mjd_task = asyncio.to_thread(estimate_mjd_params, returns)
            pf_task = asyncio.to_thread(run_particle_filter, df)

            # Motores con lógica específica
            results = await asyncio.gather(
                tail_task,
                mjd_task,
                pf_task,
                (
                    _get_markov_engine().analyze_async(sym, df)
                    if hasattr(_get_markov_engine(), "analyze_async")
                    else asyncio.to_thread(_get_markov_engine().analyze, sym, df)
                ),
                return_exceptions=True,
            )
            return results

        eng_results = await run_engines()
        
        tail_res_res = eng_results[0]
        tail_res = tail_res_res.unwrap() if not isinstance(tail_res_res, Exception) and hasattr(tail_res_res, "is_success") and tail_res_res.is_success else None
        
        mjd_params_res = eng_results[1]
        mjd_params = mjd_params_res.unwrap() if not isinstance(mjd_params_res, Exception) and hasattr(mjd_params_res, "is_success") and mjd_params_res.is_success else {"jump_intensity": 0.0, "mu_j": 0.0, "sigma_j": 0.0, "jump_prob": 0.0}
        
        pf_res_res = eng_results[2]
        pf_res = pf_res_res.unwrap() if not isinstance(pf_res_res, Exception) and hasattr(pf_res_res, "is_success") and pf_res_res.is_success else None
        
        markov_res = eng_results[3]

        # ── 3. Motores de Estructura y Flujo (Opciones/Squeeze) ───────────────
        # Estos motores alimentan el 'sesgo' (bias) de los targets
        squeeze_prob = 0.05
        _gex_signal = "NEUTRAL"
        vol_regime = "NORMAL"

        if options_snapshot and getattr(options_snapshot, "ok", False):
            try:
                option_rows = list(getattr(options_snapshot, "chain", []) or [])
                # Squeeze Ignition
                if option_rows:
                    call_vol = sum(float(r.call_volume or 0.0) for r in option_rows)
                    put_vol = sum(float(r.put_volume or 0.0) for r in option_rows)
                    call_oi = sum(float(r.call_oi or 0.0) for r in option_rows)
                    net_gamma = sum(float(r.net_gex or 0.0) for r in option_rows)
                    call_wall = max(option_rows, key=lambda r: float(r.call_oi or 0.0)).strike
                    pcr_vol = put_vol / call_vol if call_vol > 0 else 0.0
                    volume_series = pd.to_numeric(
                        df.get("volume", pd.Series(dtype=float)),
                        errors="coerce",
                    ).dropna()
                    close_series = pd.to_numeric(
                        df.get("close", pd.Series(dtype=float)),
                        errors="coerce",
                    ).dropna()
                    current_volume = float(volume_series.iloc[-1]) if len(volume_series) else 0.0
                    volume_sma = (
                        float(volume_series.tail(20).mean())
                        if len(volume_series)
                        else max(current_volume, 1.0)
                    )
                    prev_price = (
                        float(close_series.iloc[-2]) if len(close_series) >= 2 else current_price
                    )
                    sq_eng = SqueezeIgnitionEngine(ticker=sym, verbose=False)
                    sq_res = sq_eng.evaluate(
                        UnderlyingData(
                            ticker=sym,
                            spot_price=current_price,
                            prev_spot_price=prev_price,
                            volume=current_volume,
                            volume_sma_20=volume_sma if volume_sma > 0 else 1.0,
                            short_interest_ratio=0.0,
                            days_to_cover=0.0,
                        ),
                        OptionChainData(
                            call_volume=call_vol,
                            call_volume_sma_20=max(call_vol, 1.0),
                            call_open_interest=call_oi,
                            put_call_ratio_volume=pcr_vol,
                            dealer_net_gamma=net_gamma,
                            call_wall_level=float(call_wall),
                            gamma_zero_level=current_price,
                        ),
                    )
                    squeeze_prob = max(
                        0.05,
                        min(1.0, float(sq_res.squeeze_vulnerability_score) / 100.0),
                    )

                # Vol Term Structure
                term_rows: list[dict[str, Any]] = []
                by_exp: dict[str, list[Any]] = {}
                for row in option_rows:
                    exp = str(getattr(row, "expiration", "") or "")[:10]
                    if exp:
                        by_exp.setdefault(exp, []).append(row)
                for exp, options in by_exp.items():
                    try:
                        dte_days = max(
                            1,
                            (datetime.strptime(exp[:10], "%Y-%m-%d") - datetime.now()).days,
                        )
                    except ValueError:
                        continue
                    candidates = []
                    for opt in options:
                        strike = _safe_float(getattr(opt, "strike", None))
                        civ = _safe_float(getattr(opt, "call_iv", None))
                        piv = _safe_float(getattr(opt, "put_iv", None))
                        ivs = [iv for iv in (civ, piv) if iv is not None and iv > 0]
                        if strike is not None and strike > 0 and ivs:
                            candidates.append(
                                (abs(strike - current_price), float(sum(ivs) / len(ivs)))
                            )
                    if candidates:
                        candidates.sort(key=lambda item: item[0])
                        term_rows.append(
                            {
                                "snapshot_date": datetime.now().strftime("%Y-%m-%d"),
                                "dte": float(dte_days),
                                "iv_atm": candidates[0][1],
                            }
                        )
                if len(term_rows) >= 2:
                    vt_eng = VolatilityTermStructureEngine()
                    vt_eng.load_option_chain(pd.DataFrame(term_rows))
                    vt_eng.build_term_structure()
                    vt_eng.compute_metrics()
                    vt_alerts = vt_eng.generate_alerts()
                    vol_regime = (
                        "BACKWARDATION"
                        if bool(vt_alerts.get("inversion_alert", False))
                        else "CONTANGO"
                    )
            except Exception as e:
                logger.warning("Motores secundarios fallaron para %s: %s", sym, e)

        # ── 4. Monte Carlo Enriquecido ────────────────────────────────────────
        # Usamos MJD + Heston + Ajuste de Sesgo por Motores
        rolling_std = pd.Series(returns).rolling(30).std().fillna(np.std(returns)).values * np.sqrt(
            252.0
        )
        vov_res = calibrate_heston_vov(returns, rolling_std)
        vov = vov_res.unwrap() if vov_res.is_success and vov_res.value else 0.0

        paths = project_trajectories(
            current_price=current_price,
            returns=returns,
            mjd_params=mjd_params,
            vov=vov,
            horizon_days=30,
            n_paths=1000,
        )

        # ── 5. Construcción de Horizontes y Snapshot Universal ────────────────
        horizons_data = []
        for days in _PRICE_TARGETS_HORIZONS:
            col = paths[:, days]
            p50 = float(np.percentile(col, 50))
            expected_pct = (p50 - current_price) / current_price

            # El BIAS ahora es una síntesis de TODOS los motores
            bias = _determine_bias(
                expected_pct, mjd_params.get("jump_prob", 0.0), pf_res.pr_ordered
            )
            # Ajuste dinámico por Squeeze o Vol
            if squeeze_prob > 0.7:
                bias = "HIGH_RISK"
            if vol_regime == "BACKWARDATION":
                bias = "BEARISH"

            horizons_data.append(
                {
                    "days": days,
                    "label": f"{days}D",
                    "p10": round(float(np.percentile(col, 10)), 2),
                    "p25": round(float(np.percentile(col, 25)), 2),
                    "p50": round(p50, 2),
                    "p75": round(float(np.percentile(col, 75)), 2),
                    "p90": round(float(np.percentile(col, 90)), 2),
                    "expected_pct": round(expected_pct * 100, 3),
                    "bias": bias,
                }
            )

        engine_snapshot = {
            "evt_var_99": round(float(getattr(tail_res, "var_99", 0.0)), 4),
            "evt_cvar_99": round(float(getattr(tail_res, "cvar_99", 0.0)), 4),
            "evt_shape": round(float(getattr(tail_res, "shape", 0.0)), 4),
            "jump_probability": round(float(mjd_params.get("jump_prob", 0.0)), 4),
            "jump_intensity": round(float(mjd_params.get("jump_intensity", 0.0)), 4),
            "jump_mu": round(float(mjd_params.get("jump_mu", 0.0)), 4),
            "jump_sigma": round(float(mjd_params.get("jump_sigma", 0.0)), 4),
            "regime_ordered_prob": round(float(getattr(pf_res, "pr_ordered", 0.5)), 4),
            "trend_strength": round(float(getattr(pf_res, "trend_strength", 0.5)), 4),
            "vov": round(float(vov), 4),
            "markov_state": str(getattr(markov_res, "current_state", "UNKNOWN")),
            "vix": 20.0,
            "us10y": 0.0,
        }

        # ── 6. Narrativa IA de Síntesis (Optimizada) ──────────────────────────
        ai_narrative = "Narrativa no solicitada."
        ai_agent = "none"

        if include_ai:
            ai_agent = "microstructure_synthesizer"
            # Legacy placeholder retained only until the evidence prompt is built below.
            prompt = (
                f"SÍNTESIS QUANT - {sym} @ ${current_price}\n"
                "EVIDENCE_PACK_ONLY\n"
                "TARGETS_REDACTED\n"
                f"TAREA: Como sintetizador, explica la convergencia/divergencia de estos motores. "
                f"Sé extremadamente directo. Usa bullets. Máximo 150 palabras."
            )

            evidence_pack = AIReadyPayloadEngine().build_engine_pack(
                "price_targets",
                sym,
                {
                    "current_price": current_price,
                    "horizons": horizons_data,
                    "engine_snapshot": engine_snapshot,
                },
            )
            policy = should_call_optional_ai(
                feature="price_targets_ai",
                signal_score=evidence_pack.signal_score,
                has_critical_risk=evidence_pack.has_critical_risk,
            )
            if not policy.call:
                logger.info(
                    "price_targets.optional_ai_skipped symbol=%s reason=%s signal_score=%.3f",
                    sym,
                    policy.reason,
                    policy.signal_score,
                )
                result = {
                    "symbol": sym,
                    "current_price": current_price,
                    "horizons": horizons_data,
                    "engine_snapshot": engine_snapshot,
                    "ai_narrative": ai_narrative,
                    "ai_agent": ai_agent,
                    "as_of": datetime.now().isoformat(),
                }
                import time as _time

                _PRICE_TARGETS_CACHE[cache_key] = {**result, "_cached_at": _time.time()}
                return result

            from ..services.ai_core.agent_manager import AgentManager

            am = AgentManager()
            prompt = _build_price_targets_prompt(sym, current_price, horizons_data, engine_snapshot)
            try:
                ai_narrative = await am.invoke_agent("microstructure", prompt)
            finally:
                await am.aclose()

        result = {
            "symbol": sym,
            "current_price": current_price,
            "horizons": horizons_data,
            "engine_snapshot": engine_snapshot,
            "ai_narrative": ai_narrative,
            "ai_agent": ai_agent,
            "as_of": datetime.now().isoformat(),
        }

        import time as _time

        _PRICE_TARGETS_CACHE[cache_key] = {**result, "_cached_at": _time.time()}
        return result

    except Exception as e:
        logger.exception("Error universal en price-targets para %s", symbol)
        raise HTTPException(status_code=500, detail=str(e))

    except HTTPException:
        raise


@router.get("/trajectories/{symbol}")
async def get_price_trajectories(symbol: str, horizon: int = 30) -> dict[str, Any]:
    """Monte Carlo price trajectories using MJD + Heston stochastic volatility."""
    try:
        sym = symbol.upper().strip()
        _is_argentina = sym.endswith(".BA")

        date_from = (datetime.now() - timedelta(days=200)).strftime("%Y-%m-%d")
        ohlcv_raw = await _get_fmp_client().get_historical_prices(sym, date_from=date_from)
        if not ohlcv_raw:
            if not _get_fmp_client()._is_active():
                raise HTTPException(
                    status_code=503,
                    detail="FMPClient inactivo: no hay claves API configuradas. Verificá el archivo .env.",
                )
            raise HTTPException(status_code=404, detail=f"Sin datos históricos para {sym}.")

        df = _build_df(ohlcv_raw)
        returns = df["close"].pct_change().dropna().values
        current_price = float(df["close"].iloc[-1])

        mjd_params_res = estimate_mjd_params(returns)
        mjd_params = mjd_params_res.unwrap() if mjd_params_res.is_success and mjd_params_res.value else {"jump_intensity": 0.0, "mu_j": 0.0, "sigma_j": 0.0, "jump_prob": 0.0}
        rolling_std = pd.Series(returns).rolling(30).std().fillna(np.std(returns)).values * np.sqrt(
            252.0
        )
        vov_res = calibrate_heston_vov(returns, rolling_std)
        vov = vov_res.unwrap() if vov_res.is_success and vov_res.value else 0.0

        # PHASE 4: Feedback Calibration (Self-Correction)
        history = _get_predictive_storage().get_history(sym, limit=5)
        feedback = _get_feedback_engine().calculate_model_error(history, current_price)

        # Adapt parameters based on realized performance
        adj_mjd = _get_feedback_engine().adapt_parameters(mjd_params, feedback)
        # Adapt vov if we are seeing higher than expected errors
        vov *= feedback.get("error_factor", 1.0)

        paths = project_trajectories(
            current_price=current_price,
            returns=returns,
            mjd_params=adj_mjd,
            vov=vov,
            horizon_days=horizon,
            n_paths=500,
        )

        fan_data = {f"p{p}": np.percentile(paths, p, axis=0).tolist() for p in [10, 25, 50, 75, 90]}
        dates = [
            (datetime.now() + timedelta(days=i)).strftime("%Y-%m-%d") for i in range(horizon + 1)
        ]

        return {
            "symbol": sym,
            "currentPrice": current_price,
            "trajectories": fan_data,
            "timestamps": dates,
        }
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e)) from e


@router.get("/thesis/{symbol}", response_model=AIThesisResponse)
async def get_ai_thesis(symbol: str, include_snapshot: bool = False) -> AIThesisResponse:
    """AI Thesis: legacy fields + ThesisV2 (bloques auditables)."""
    try:
        sym = symbol.upper().strip()
        date_from = (datetime.now() - timedelta(days=200)).strftime("%Y-%m-%d")
        ohlcv_raw = await _get_fmp_client().get_historical_prices(sym, date_from=date_from)
        if not ohlcv_raw:
            if not _get_fmp_client()._is_active():
                raise HTTPException(
                    status_code=503,
                    detail="FMPClient inactivo: no hay claves API configuradas. Verificá el archivo .env.",
                )
            raise HTTPException(status_code=404, detail=f"Sin datos históricos para {sym}.")

        df = _build_df(ohlcv_raw)
        social_data = await _get_fmp_client().get_social_sentiment(sym, limit=10)
        sentiment_signal = _get_sentiment_engine().analyze_social(social_data, sym)
        sent_score = sentiment_signal.sentiment_score if sentiment_signal else 0.5

        fusion_res = _get_predictive_engine().run_fusion_inference(
            symbol=sym,
            ohlcv_df=df,
            sentiment_score=sent_score,
            gex_data={},
        )
        conviction_score = float(
            fusion_res.get("conviction", fusion_res.get("fusion_conviction", 0.5))
        )
        bias = fusion_res.get(
            "bias",
            (
                "BULLISH"
                if conviction_score > 0.6
                else "BEARISH" if conviction_score < 0.4 else "NEUTRAL"
            ),
        )
        fusion_meta = fusion_res.get("fusion_metadata", fusion_res)

        assembly = await assemble_thesis_v2_with_snapshot(
            sym,
            df,
            _get_fmp_client(),
            _get_predictive_engine(),
            _get_sentiment_engine(),
            fusion_res=fusion_res,
            sentiment_score=float(sent_score),
            include_snapshot=include_snapshot,
            horizon="swing",
            market="US",
            snapshot_inputs={
                "config": {
                    "endpoint": "/api/v1/probabilistic/thesis/{symbol}",
                    "include_snapshot": include_snapshot,
                    "lookback_days": 200,
                },
                "data": {
                    "symbol": sym,
                    "ohlcv_rows": int(len(df)),
                    "fusion_keys": (
                        sorted(fusion_res.keys()) if isinstance(fusion_res, dict) else []
                    ),
                },
            },
        )
        snapshot_payload = None
        if include_snapshot:
            thesis_v2, multimodal, _domain_narratives, snapshot = assembly
            if snapshot is not None and hasattr(snapshot, "model_dump"):
                snapshot_payload = snapshot.model_dump(mode="json")
        else:
            thesis_v2, multimodal, _domain_narratives = assembly

        fallback = legacy_thesis_sentence(sym, str(bias), fusion_meta, thesis_v2)
        thesis = multimodal if isinstance(multimodal, str) and multimodal.strip() else fallback
        institutional_report = build_institutional_report(
            sym,
            thesis_v2,
            bias=str(bias),
            conviction=conviction_score,
            horizon="swing",
        )
        fusion_metadata_payload = (
            fusion_meta if isinstance(fusion_meta, dict) else {"raw": fusion_meta}
        )
        if snapshot_payload is not None:
            fusion_metadata_payload = {**fusion_metadata_payload, "snapshot": snapshot_payload}

        return AIThesisResponse(
            symbol=sym,
            bias=str(bias),
            conviction=conviction_score,
            sentiment=sentiment_signal.model_dump() if sentiment_signal else None,
            fusion_metadata=fusion_metadata_payload,
            thesis=thesis,
            timestamp=datetime.now().isoformat(),
            thesis_v2=thesis_v2,
            institutional_multimodal_thesis=multimodal,
            institutional_report=institutional_report,
        )
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e)) from e


from ..quant_engine.engines.predictive.portfolio_optimizer import (
    BlackLittermanOptimizer,
    calculate_covariance,
)


@router.post("/portfolio/optimize")
async def optimize_portfolio(symbols: list[str]) -> dict[str, Any]:
    """
    Optimizes a portfolio of assets using Black-Litterman with Predictive views.
    """
    try:
        returns_map = {}
        views = []
        confidences = []

        # 1. Fetch data and views for each ticker
        for sym in symbols:
            # We reuse the existing logic but simplified
            date_from = (datetime.now() - timedelta(days=250)).strftime("%Y-%m-%d")
            ohlcv = await _get_fmp_client().get_historical_prices(sym, date_from=date_from)
            if not ohlcv:
                continue

            df = _build_df(ohlcv)
            rets = df["close"].pct_change().dropna().values
            returns_map[sym] = rets

            # Get Predictive View (conviction)
            # For speed, we run a simplified version of the analysis
            analysis = await get_probabilistic_analysis(sym)
            views.append(analysis.etv)  # Use Expected Trade Value as return view
            confidences.append(analysis.state.pr_ordered)

        if not returns_map:
            raise HTTPException(status_code=400, detail="No valid data for provided symbols")

        # 2. Covariance Calculation
        tickers, cov = calculate_covariance(returns_map)

        # 3. Prior (Market Equilibrium) - assume equal weight prior
        prior = np.ones(len(tickers)) * 0.05  # 5% expected annual return as neutral prior

        # 4. Run Optimizer
        optimizer = BlackLittermanOptimizer()
        result = optimizer.optimize(
            tickers=tickers,
            cov_matrix=cov,
            prior_returns=prior,
            views=np.array(views),
            confidences=np.array(confidences),
        )

        return result
    except Exception as e:
        logger.error(f"Portfolio optimization error: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/cross-asset/{symbol}")
async def get_cross_asset_analysis(symbol: str) -> dict[str, Any]:
    """
    Standalone cross-asset correlation endpoint.
    Returns pairwise rolling & long-term correlations, decoupling scores,
    and a regime label (IDIOSYNCRATIC / SYSTEMATIC / MODERATE_COUPLING).
    """
    try:
        sym = symbol.upper().strip()
        date_from = (datetime.now() - timedelta(days=300)).strftime("%Y-%m-%d")

        # Fetch target
        target_ohlcv = await _get_fmp_client().get_historical_prices(sym, date_from=date_from)
        if not target_ohlcv:
            raise HTTPException(status_code=404, detail=f"No price data for {sym}")

        target_prices = [p.close for p in target_ohlcv if p.close]

        # Fetch all reference assets concurrently
        import asyncio

        ref_tasks = {
            ref: _get_fmp_client().get_historical_prices(ref, date_from=date_from)
            for ref in REFERENCE_ASSETS
            if ref != sym
        }
        ref_results = await asyncio.gather(*ref_tasks.values(), return_exceptions=True)
        target_prices_np = np.array(target_prices)
        ref_tickers_list = []
        ref_prices_list = []
        for ref_ticker, res in zip(ref_tasks.keys(), ref_results, strict=False):
            if isinstance(res, list) and res:
                prices = [p.close for p in res if p.close]
                if len(prices) >= len(target_prices):
                    ref_tickers_list.append(ref_ticker)
                    ref_prices_list.append(np.array(prices[-len(target_prices):]))

        if not ref_tickers_list:
            raise HTTPException(status_code=500, detail="No reference prices available")

        ref_prices_np = np.column_stack(ref_prices_list)

        ca_report_res = _get_cross_asset_engine().analyze(sym, target_prices_np, ref_prices_np, ref_tickers_list)
        if not ca_report_res.is_success or not ca_report_res.value:
            raise HTTPException(status_code=500, detail=f"CrossAsset analysis failed: {ca_report_res.error}")
        ca_report = ca_report_res.unwrap()

        return {
            "symbol": ca_report.symbol,
            "regime_label": ca_report.regime_label,
            "strongest_link": ca_report.strongest_link,
            "max_decoupling": ca_report.max_decoupling,
            "decoupling_alert": ca_report.decoupling_alert,
            "systematic_risk": ca_report.systematic_risk,
            "idiosyncratic_risk": ca_report.idiosyncratic_risk,
            "correlations": [
                {
                    "ticker": p.reference_ticker,
                    "label": p.label,
                    "rolling_corr": p.rolling_corr,
                    "long_corr": p.long_corr,
                    "decoupling": p.decoupling_score,
                    "is_decoupled": p.is_decoupled,
                    "direction": p.direction,
                }
                for p in ca_report.correlations
            ],
        }
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e)) from e


@router.get("/catalyst/{symbol}")
async def get_catalyst_analysis(symbol: str) -> dict[str, Any]:
    """
    Standalone catalyst NLP analysis endpoint.
    Scans transcripts, news, and upcoming earnings dates to quantify event risk.
    """
    try:
        sym = symbol.upper().strip()
        profile = await _get_catalyst_engine().analyze(sym, _get_fmp_client())

        # Compute Multi-Factor Fear & Greed Index
        fear_greed_result = None
        try:
            market_data = {
                "spx_price": None,
                "spx_ma125": None,
                "vix_current": None,
                "vix_ma50": None,
                "nyse_highs_pct": None,
                "put_call_ratio": None,
                "credit_spread": None,
            }

            fg_result = await _get_fear_greed_engine().compute(
                symbol=sym,
                market_data=market_data,
                event_risk_score=profile.event_risk_score,
            )
            fear_greed_result = {
                "score": fg_result.score,
                "label": fg_result.label,
                "data_quality": fg_result.data_quality,
                "factors": fg_result.factors,
            }
        except Exception as fg_error:
            logger.warning(f"Fear & Greed calculation failed for {sym}: {fg_error}")

        result = {
            "symbol": profile.symbol,
            "event_risk_score": profile.event_risk_score,
            "tone": profile.tone,
            "tone_confidence": profile.tone_confidence,
            "jump_intensity_adj": profile.jump_intensity_adj,
            "transcript_summary": profile.transcript_summary,
            "bullish_hits": profile.bullish_hits,
            "bearish_hits": profile.bearish_hits,
            "alarming_hits": profile.alarming_hits,
            "news_count": profile.news_count,
            "news_sentiment": profile.news_sentiment,
            "upcoming_catalysts": [
                {
                    "event_type": c.event_type,
                    "date": c.date,
                    "days_until": c.days_until,
                    "label": c.label,
                }
                for c in profile.upcoming_catalysts
            ],
            "last_eps_surprise": profile.last_eps_surprise,
            "avg_eps_surprise": profile.avg_eps_surprise,
        }

        # Add fear_greed_result if available
        if fear_greed_result:
            result["fear_greed_result"] = fear_greed_result

        return result
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e)) from e


@router.get("/fear-greed/history/{symbol}")
async def get_fear_greed_history(
    symbol: str,
    days: int = 30,
    limit: int | None = None,
) -> dict[str, Any]:
    """
    Get historical Fear & Greed scores for a symbol.

    Args:
        symbol: Symbol to query
        days: Number of days of history
        limit: Maximum number of records

    Returns:
        Historical FG readings with factor breakdown
    """
    try:
        sym = symbol.upper().strip()
        history = _get_fear_greed_storage().get_history(sym, days=days, limit=limit)

        return {
            "symbol": sym,
            "days": days,
            "count": len(history),
            "history": [
                {
                    "timestamp": h.timestamp.isoformat(),
                    "score": h.score,
                    "label": h.label,
                    "data_quality": h.data_quality,
                    "factors": h.factors,
                    "event_risk_score": h.event_risk_score,
                }
                for h in history
            ],
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e)) from e


@router.get("/fear-greed/stats/{symbol}")
async def get_fear_greed_statistics(
    symbol: str,
    days: int = 30,
) -> dict[str, Any]:
    """
    Get statistical summary of Fear & Greed scores.

    Args:
        symbol: Symbol to analyze
        days: Number of days of history

    Returns:
        Statistical summary (mean, min, max, count)
    """
    try:
        sym = symbol.upper().strip()
        stats = _get_fear_greed_storage().get_statistics(sym, days=days)

        return {
            "symbol": sym,
            "days": days,
            **stats,
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e)) from e


@router.get("/fear-greed/calibration")
async def get_fear_greed_calibration(
    days: int = 90,
    method: str = "pca",
) -> dict[str, Any]:
    """
    Get factor calibration analysis.

    Args:
        days: Days of historical data to use
        method: Calibration method ("pca", "optimization", "equal")

    Returns:
        Calibration report with recommended weights
    """
    try:
        history = _get_fear_greed_storage().get_history("SPY", days=days)

        if len(history) < 10:
            return {
                "error": f"Insufficient data ({len(history)} readings, need 10+)",
                "days_requested": days,
            }

        for h in history:
            _get_calibration_engine().add_observation(factors=h.factors, target=h.score)

        report = _get_calibration_engine().get_calibration_report()
        report["method_used"] = method

        if method == "pca":
            report["recommended_weights"] = report["pca_weights"]
        elif method == "optimization":
            report["recommended_weights"] = report["optimized_weights"]
        else:
            report["recommended_weights"] = report["equal_weights"]

        return report
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e)) from e


@router.get("/fear-greed/compare-cnn")
async def compare_with_cnn(
    symbol: str = "SPY",
    days: int = 30,
) -> dict[str, Any]:
    """
    Compare our Fear & Greed with CNN (or alternative source).
    """
    try:
        sym = symbol.upper()
        our_history = _get_fear_greed_storage().get_history(sym, days=days)

        if not our_history:
            return {"error": "No historical data available", "symbol": sym}

        cnn_data = await _get_cnn_fetcher().fetch_current()

        if not cnn_data:
            alt_fg = _get_alternative_fg_source().calculate_approximate_fg()
            comparison = {
                "cnn_available": False,
                "alternative_available": True,
                "alternative_score": alt_fg["score"],
                "alternative_label": alt_fg["label"],
            }
        else:
            comparison = {
                "cnn_available": True,
                "cnn_score": cnn_data.get("score"),
                "cnn_label": cnn_data.get("label"),
            }

        latest = our_history[0] if our_history else None
        if latest is not None:
            comparison["our_score"] = latest.score
            comparison["our_label"] = latest.label
            comparison["difference"] = latest.score - comparison.get("cnn_score", latest.score)
            comparison["agreement"] = (
                "high"
                if abs(comparison["difference"]) < 5
                else "medium" if abs(comparison["difference"]) < 10 else "low"
            )

        stats = _get_fear_greed_storage().get_statistics(sym, days=days)
        comparison["our_statistics"] = stats

        return comparison
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e)) from e


@router.get("/news/{symbol}")
async def get_symbol_news(symbol: str, limit: int = 12) -> dict[str, Any]:
    """
    Lightweight news feed for dashboard/editorial surfaces.
    Uses the configured FMP client (no mock fallback).
    """
    try:
        sym = symbol.upper().strip()
        lim = max(1, min(int(limit), 40))
        items = await _get_fmp_client().get_stock_news(sym, limit=lim)
        return {
            "symbol": sym,
            "count": len(items),
            "items": [
                {
                    "symbol": n.symbol,
                    "published_date": n.publishedDate,
                    "title": n.title,
                    "source": n.site,
                    "url": n.url,
                    "summary": n.text,
                }
                for n in items
                if n.title
            ],
        }
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e)) from e


@router.get("/volume-profile/{symbol}")
async def get_volume_profile(symbol: str) -> dict[str, Any]:
    """
    Standalone Volume Profile endpoint.
    Returns POC, VAH, VAL, and list of HVN/LVN liquidity levels.
    """
    try:
        sym = symbol.upper().strip()
        date_from = (datetime.now() - timedelta(days=250)).strftime("%Y-%m-%d")
        ohlcv = await _get_fmp_client().get_historical_prices(sym, date_from=date_from)
        if not ohlcv:
            raise HTTPException(status_code=404, detail=f"No price data for {sym}")

        df = _build_df(ohlcv)
        report = _get_volume_profile_engine().analyze(sym, df)

        return {
            "symbol": report.symbol,
            "poc": report.poc,
            "vah": report.vah,
            "val": report.val,
            "hvn_levels": report.hvn_levels,
            "lvn_levels": report.lvn_levels,
            "profile": [
                {"price": n.price, "volume_pct": n.volume_pct, "node_type": n.node_type}
                for n in report.profile
            ],
        }
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e)) from e


@router.get("/volatility-skew/{symbol}")
async def get_volatility_skew(symbol: str) -> dict[str, Any]:
    """
    Standalone Volatility Skew endpoint.
    Analyzes historical Put/Call IV dynamics to detect institutional hedging demand.
    """
    try:
        sym = symbol.upper().strip()
        iv_history = await _get_fmp_client().get_options_iv_history(sym)
        report = _get_vol_surface_engine().analyze(sym, iv_history)

        return {
            "symbol": report.symbol,
            "current_skew": report.current_skew,
            "skew_percentile": report.skew_percentile,
            "fear_regime": report.fear_regime,
            "put_call_iv_ratio": report.put_call_iv_ratio,
            "risk_signal": report.risk_signal,
            "historical_skew": [
                {"date": s.date, "put_iv": s.put_iv, "call_iv": s.call_iv, "skew": s.skew}
                for s in report.historical_skew
            ],
        }
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e)) from e


@router.get("/markov-regime/{symbol}")
async def get_markov_regime(symbol: str) -> dict[str, Any]:
    """
    Standalone Markov Regime Switching endpoint.
    Identifies if the market is in a Bull, Bear, or Chaotic state.
    """
    try:
        sym = symbol.upper().strip()
        ohlcv = await _get_fmp_client().get_historical_prices(sym)
        if not ohlcv:
            raise HTTPException(status_code=404, detail=f"No price data for {sym}")

        df = _build_df(ohlcv)
        report = _get_markov_engine().analyze(sym, df)

        return {
            "symbol": report.symbol,
            "current_state": report.current_state,
            "state_confidence": report.state_confidence,
            "transition_risk": report.transition_risk,
            "expected_days_in_state": report.expected_days_in_state,
            "regime_signal": report.regime_signal,
            "states": [
                {"index": s.index, "label": s.label, "probability": s.probability}
                for s in report.states
            ],
        }
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e)) from e


@router.get("/fear-greed/dashboard")
async def get_fear_greed_dashboard() -> dict[str, Any]:
    """Real-time Fear & Greed monitoring dashboard."""
    try:
        history = _get_fear_greed_storage().get_history("SPY", days=1)
        latest = history[0] if history else None
        stats = _get_fear_greed_storage().get_statistics("SPY", days=30)

        return {
            "timestamp": datetime.now().isoformat(),
            "current": (
                {
                    "score": latest.score if latest is not None is not None else None,
                    "label": latest.label if latest is not None is not None else None,
                    "data_quality": latest.data_quality if latest is not None is not None else None,
                }
                if latest is not None
                else None
            ),
            "statistics": stats,
            "factors": latest.factors if latest is not None else None,
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e)) from e


@router.get("/fear-greed/ml-optimize")
async def get_fear_greed_ml_optimization(
    days: int = 90,
    method: str = "auto",  # auto, ridge, random_forest
) -> dict[str, Any]:
    """
    Get ML-optimized factor weights.

    Args:
        days: Days of historical data
        method: Optimization method

    Returns:
        ML optimization results
    """
    try:
        # Get historical data
        history = _get_fear_greed_storage().get_history("SPY", days=days)

        if len(history) < 20:
            return {
                "error": f"Insufficient data ({len(history)} readings, need 20+)",
                "days_requested": days,
            }

        # Add samples to optimizer
        for h in history:
            _get_ml_optimizer().add_sample(features=h.factors, target=h.score)

        # Get optimal weights
        result = _get_ml_optimizer().get_optimal_weights(method=method)

        return {
            "method": result.method,
            "weights": result.weights,
            "score": result.score,
            "feature_importance": result.feature_importance,
            "timestamp": result.timestamp.isoformat(),
            "sample_size": len(history),
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e)) from e


@router.get("/fear-greed/analyze-correlation")
async def get_fear_greed_correlation(
    symbol: str = "SPY",
    horizon: int = 5,
) -> dict[str, Any]:
    """
    Analyze correlation between Fear & Greed and future returns.

    Args:
        symbol: Symbol to analyze
        horizon: Holding period in days

    Returns:
        Correlation analysis
    """
    try:
        # Get historical FG
        history = _get_fear_greed_storage().get_history(symbol, days=100)

        if len(history) < 30:
            return {
                "error": "Insufficient data for correlation analysis",
                "sample_size": len(history),
            }

        # Get SPY data for correlation
        spy_hist = await _get_fmp_client().get_historical_prices(
            symbol,
            date_from=(datetime.now() - timedelta(days=150)).strftime("%Y-%m-%d"),
            date_to=datetime.now().strftime("%Y-%m-%d"),
        )

        if not spy_hist:
            return {"error": "No SPY data available"}

        # Add to correlation analyzer
        for i, h in enumerate(history):
            if i < len(spy_hist):
                spy_price = spy_hist[i].close if spy_hist[i].close else 0
                _get_correlation_analyzer().add_observation(h.timestamp, h.score, spy_price)

        # Analyze
        analysis = _get_correlation_analyzer().analyze_correlation(horizon=horizon)

        if not analysis:
            return {"error": "Correlation analysis failed"}

        return {
            "symbol": symbol,
            "horizon_days": horizon,
            "fg_spy_correlation": analysis.fgspy_correlation,
            "factor_correlations": analysis.factor_correlations,
            "predictive_power": analysis.predictive_power,
            "optimal_horizon": analysis.optimal_horizon,
            "sample_size": analysis.sample_size,
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e)) from e


@router.get("/fear-greed/regime-weights")
async def get_regime_weights() -> dict[str, Any]:
    """
    Get current regime and adaptive factor weights.

    Returns:
        Current regime and recommended weights
    """
    try:
        # Get current market data
        vix_data = await _get_fmp_client().get_quote("^VIX")
        spy_data = await _get_fmp_client().get_quote("SPY")

        if not vix_data or not spy_data:
            return {"error": "Failed to fetch market data"}

        vix = vix_data.price or 20
        spy_price = spy_data.price or 100

        # Get historical for MA calculations
        spy_hist = await _get_fmp_client().get_historical_prices(
            "SPY",
            date_from=(datetime.now() - timedelta(days=250)).strftime("%Y-%m-%d"),
            date_to=datetime.now().strftime("%Y-%m-%d"),
        )

        if not spy_hist or len(spy_hist) < 200:
            return {"error": "Insufficient data for regime classification"}

        prices = [p.close for p in spy_hist if p.close]
        ma50 = sum(prices[:50]) / 50 if len(prices) >= 50 else prices[0]
        ma200 = sum(prices[:200]) / 200 if len(prices) >= 200 else prices[0]

        # Classify regime
        regime = _get_regime_engine().classify_regime(
            vix=vix,
            spy_ma50=ma50,
            spy_ma200=ma200,
            spy_price=spy_price,
        )

        # Get weights
        regime_weights = _get_regime_engine().get_regime_weights(regime)

        return {
            "regime": regime.value,
            "regime_description": regime_weights.description,
            "weights": regime_weights.weights,
            "market_context": {
                "vix": vix,
                "spy_price": spy_price,
                "spy_ma50": ma50,
                "spy_ma200": ma200,
                "trend": "uptrend" if spy_price > ma200 else "downtrend",
            },
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e)) from e


@router.get("/fear-greed/alerts")
async def get_fear_greed_alerts(threshold: float = 20.0) -> dict[str, Any]:
    """Get Fear & Greed alerts for extreme deviations."""
    try:
        alerts = []
        history = _get_fear_greed_storage().get_history("SPY", days=1)

        if not history:
            return {"alerts": [], "count": 0}

        latest = history[0]

        if latest is not None.score <= 25:
            alerts.append(
                {
                    "type": "extreme_fear",
                    "severity": "high",
                    "message": f"Fear & Greed at {latest.score:.1f} - Extreme Fear",
                    "recommendation": "Consider contrarian bullish positions",
                }
            )
        elif latest is not None.score >= 75:
            alerts.append(
                {
                    "type": "extreme_greed",
                    "severity": "high",
                    "message": f"Fear & Greed at {latest.score:.1f} - Extreme Greed",
                    "recommendation": "Consider reducing risk exposure",
                }
            )

        if len(history) > 5:
            five_days_ago = history[5]
            change = latest.score - five_days_ago.score
            if abs(change) > threshold:
                alerts.append(
                    {
                        "type": "rapid_change",
                        "severity": "medium" if abs(change) < 15 else "high",
                        "message": f"Fear & Greed changed by {change:.1f} points in 5 days",
                        "recommendation": "Monitor for regime change",
                    }
                )

        return {
            "alerts": alerts,
            "count": len(alerts),
            "timestamp": datetime.now().isoformat(),
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e)) from e


# ════════════════════════════════════════════════════════════════════════════════
# NEW PROBABILISTIC ENGINES — endpoints
# ════════════════════════════════════════════════════════════════════════════════
#
# Endpoints follow the same idiom as /gamma-flip/{symbol} and
# /volatility-skew/{symbol}: fetch the options snapshot once, hand the chain
# DataFrame to the engine, and return a typed Pydantic model. Cached in-process
# with TTLs reflecting how fast each signal decays:
#   risk-neutral-density       →  5 min  (vol smile drifts fast)
#   dealer-flow                → 10 min
#   options-flow-toxicity      →  5 min
#   macro-regime-prior         → 60 min  (macro tape is slow)
# ════════════════════════════════════════════════════════════════════════════════

import time as _time_mod  # noqa: E402

from pydantic import BaseModel, Field  # noqa: E402

# In-process TTL cache (single dict per endpoint keeps invalidation trivial).
_RND_CACHE: dict[str, dict[str, Any]] = {}
_DEALER_CACHE: dict[str, dict[str, Any]] = {}
_FLOW_TOX_CACHE: dict[str, dict[str, Any]] = {}
_MACRO_CACHE: dict[str, dict[str, Any]] = {}

_RND_TTL_S = 5 * 60
_DEALER_TTL_S = 10 * 60
_FLOW_TOX_TTL_S = 5 * 60
_MACRO_TTL_S = 60 * 60


def _cache_get(cache: dict[str, dict[str, Any]], key: str, ttl_s: int) -> dict[str, Any] | None:
    entry = cache.get(key)
    if entry is None:
        return None
    if _time_mod.time() - entry.get("_cached_at", 0) > ttl_s:
        cache.pop(key, None)
        return None
    payload = dict(entry)
    payload.pop("_cached_at", None)
    return payload


def _cache_put(cache: dict[str, dict[str, Any]], key: str, payload: dict[str, Any]) -> None:
    cache[key] = {**payload, "_cached_at": _time_mod.time()}


def _build_rnd_chain_df(chain: list[Any]) -> pd.DataFrame:
    """Wide-format frame (one row per strike) with call_price for RND."""
    rows: list[dict[str, Any]] = []

    def to_finite_float(value: Any) -> float | None:
        try:
            out = float(value)
        except (TypeError, ValueError):
            return None
        return out if math.isfinite(out) else None

    for r in chain:
        if isinstance(r, dict):
            getter = r.get
        else:

            def getter(key: str, default: Any = None, row: Any = r) -> Any:
                return getattr(row, key, default)

        try:
            strike = float(getter("strike"))
        except (TypeError, ValueError):
            continue

        bid = to_finite_float(getter("call_bid"))
        ask = to_finite_float(getter("call_ask"))
        if bid is None or ask is None:
            bid = to_finite_float(getter("bid"))
            ask = to_finite_float(getter("ask"))

        price: float | None = None
        if bid is not None and ask is not None and ask >= bid:
            price = (bid + ask) / 2.0

        if price is None or price <= 0:
            for field in (
                "call_mid",
                "mid",
                "mark",
                "call_price",
                "call_last",
                "last_price",
                "close",
                "call_vwap",
            ):
                price = to_finite_float(getter(field))
                if price is not None and price > 0:
                    break

        if price is None:
            continue
        if not math.isfinite(strike) or not math.isfinite(price) or price <= 0:
            continue

        rows.append({"strike": strike, "call_price": price})

    if not rows:
        return pd.DataFrame(columns=["strike", "call_price"])
    df = pd.DataFrame(rows).sort_values("strike").drop_duplicates("strike").reset_index(drop=True)
    return df


def _dealer_chain_columns() -> list[str]:
    return [
        "strike",
        "call_oi",
        "put_oi",
        "delta",
        "gamma",
        "vanna",
        "charm",
        "implied_vol",
        "spot_price",
    ]


def _finite_float(value: Any) -> float | None:
    try:
        out = float(value)
    except (TypeError, ValueError):
        return None
    return out if math.isfinite(out) else None


def _dealer_dte_years(expiration: Any) -> float:
    if expiration is None:
        return 30.0 / 365.0
    try:
        exp_dt = pd.Timestamp(str(expiration)[:10]).to_pydatetime()
    except (TypeError, ValueError):
        return 30.0 / 365.0
    today = datetime.now().replace(hour=0, minute=0, second=0, microsecond=0)
    days = max((exp_dt.replace(tzinfo=None) - today).days, 1)
    return float(days) / 365.0


def _bs_dealer_greeks(
    spot: float,
    strike: float,
    tte: float,
    rate: float,
    implied_vol: float | None,
) -> dict[str, float] | None:
    if spot <= 0 or strike <= 0 or tte <= 0 or implied_vol is None or implied_vol <= 0:
        return None
    sqrt_t = math.sqrt(tte)
    vol_sqrt_t = implied_vol * sqrt_t
    if vol_sqrt_t <= 0:
        return None

    d1 = (math.log(spot / strike) + (rate + 0.5 * implied_vol**2) * tte) / vol_sqrt_t
    d2 = d1 - vol_sqrt_t
    pdf_d1 = float(norm.pdf(d1))
    call_delta = float(norm.cdf(d1))
    gamma = float(pdf_d1 / (spot * implied_vol * sqrt_t))
    vanna = float(-pdf_d1 * d2 / implied_vol)
    charm = float(
        -pdf_d1
        * (2.0 * rate * tte - d2 * implied_vol * sqrt_t)
        / (2.0 * tte * implied_vol * sqrt_t)
    )
    return {
        "call_delta": call_delta,
        "put_delta": call_delta - 1.0,
        "gamma": gamma,
        "vanna": vanna,
        "charm": charm,
    }


def _build_dealer_chain_df(chain: list[Any], spot: float) -> pd.DataFrame:
    """Wide-format frame required by the dealer-flow engine."""
    rows: list[dict[str, Any]] = []
    for r in chain:
        if isinstance(r, dict):
            getter = r.get
        else:

            def getter(key: str, default: Any = None, row: Any = r) -> Any:
                return getattr(row, key, default)

        strike = _finite_float(getter("strike"))
        call_oi = _finite_float(getter("call_oi")) or 0.0
        put_oi = _finite_float(getter("put_oi")) or 0.0
        if strike is None:
            continue
        if call_oi <= 0 and put_oi <= 0:
            continue

        call_delta = _finite_float(getter("call_delta"))
        put_delta = _finite_float(getter("put_delta"))
        call_gamma = _finite_float(getter("call_gamma"))
        put_gamma = _finite_float(getter("put_gamma"))
        call_iv = _finite_float(getter("call_iv")) or _finite_float(getter("implied_vol"))
        put_iv = _finite_float(getter("put_iv")) or _finite_float(getter("implied_vol"))
        if call_iv is None and put_iv is not None:
            call_iv = put_iv
        if put_iv is None and call_iv is not None:
            put_iv = call_iv

        tte = _dealer_dte_years(getter("expiration") or getter("expiry"))
        call_bs = _bs_dealer_greeks(spot, strike, tte, 0.05, call_iv)
        put_bs = _bs_dealer_greeks(spot, strike, tte, 0.05, put_iv)

        if call_oi > 0 and call_bs is not None:
            call_delta = call_delta if call_delta is not None else call_bs["call_delta"]
            call_gamma = call_gamma if call_gamma is not None else call_bs["gamma"]
        if put_oi > 0 and put_bs is not None:
            put_delta = put_delta if put_delta is not None else put_bs["put_delta"]
            put_gamma = put_gamma if put_gamma is not None else put_bs["gamma"]

        delta_values: list[float] = []
        gamma_values: list[float] = []
        if (
            call_oi > 0
            and call_delta is not None
            and call_gamma is not None
            and math.isfinite(call_delta)
            and math.isfinite(call_gamma)
        ):
            delta_values.append(abs(call_delta))
            gamma_values.append(abs(call_gamma))
        if (
            put_oi > 0
            and put_delta is not None
            and put_gamma is not None
            and math.isfinite(put_delta)
            and math.isfinite(put_gamma)
        ):
            delta_values.append(abs(put_delta))
            gamma_values.append(abs(put_gamma))
        if not delta_values or not gamma_values:
            continue

        vanna = _finite_float(getter("vanna")) or _finite_float(getter("net_vanna"))
        charm = _finite_float(getter("charm")) or _finite_float(getter("net_charm"))
        if vanna is None:
            vanna_values = []
            if call_oi > 0 and call_bs is not None:
                vanna_values.append(call_bs["vanna"])
            if put_oi > 0 and put_bs is not None:
                vanna_values.append(put_bs["vanna"])
            vanna = float(np.mean(vanna_values)) if vanna_values else 0.0
        if charm is None:
            charm_values = []
            if call_oi > 0 and call_bs is not None:
                charm_values.append(call_bs["charm"])
            if put_oi > 0 and put_bs is not None:
                charm_values.append(put_bs["charm"])
            charm = float(np.mean(charm_values)) if charm_values else 0.0

        iv_values = [v for v in (call_iv, put_iv) if v is not None and math.isfinite(v) and v > 0]
        implied_vol = float(np.mean(iv_values)) if iv_values else 0.0

        rows.append(
            {
                "strike": strike,
                "call_oi": call_oi,
                "put_oi": put_oi,
                "delta": float(np.mean(np.abs(delta_values))),
                "gamma": float(np.mean(gamma_values)),
                "vanna": vanna,
                "charm": charm,
                "implied_vol": implied_vol,
                "spot_price": float(spot),
            }
        )

    if not rows:
        return pd.DataFrame(columns=_dealer_chain_columns())
    return pd.DataFrame(rows).sort_values("strike").reset_index(drop=True)


def _snapshot_dte_years(opt_snap: Any) -> float:
    surf = getattr(opt_snap, "iv_surface", None)
    if surf and getattr(surf, "surface", None):
        try:
            dte_days = float(surf.surface[0].dte)
            if dte_days > 0:
                return max(1.0 / 365.0, dte_days / 365.0)
        except (AttributeError, IndexError, TypeError, ValueError):
            pass
    return 30.0 / 365.0


# ────────────────────────────────────────────────────────────────────────────
# Pydantic response models
# ────────────────────────────────────────────────────────────────────────────


class RiskNeutralDensityResponse(BaseModel):
    ok: bool
    ticker: str
    spot: float | None = None
    expiry: str | None = None
    rate: float | None = None
    q_skewness: float | None = None
    q_kurtosis: float | None = None
    modal_price: float | None = None
    is_bimodal: bool | None = None
    density_strikes: list[float] | None = None
    density_values: list[float] | None = None
    error: str | None = None
    latency_ms: float | None = None


class DealerFlowResponse(BaseModel):
    ok: bool
    ticker: str
    spot: float | None = None
    expiry: str | None = None
    ndde: float | None = None
    ndde_normalized: float | None = None
    ndde_usd: float | None = None
    charm_flow_series: list[float] | None = None
    vanna_pressure: float | None = None
    pinning_strike: float | None = None
    pinning_probability: float | None = None
    gamma_wall_up: float | None = None
    gamma_wall_down: float | None = None
    dealer_directional_signal: float | None = None
    regime_context: str | None = None
    error: str | None = None
    latency_ms: float | None = None


class UARTrade(BaseModel):
    timestamp: str | None = None
    strike: float | None = None
    option_type: str | None = None
    size: float | None = None
    score: float | None = None


class OptionsFlowToxicityResponse(BaseModel):
    ok: bool
    ticker: str
    lookback_hours: int = 4
    call_flow_signal: float | None = None
    put_flow_signal: float | None = None
    net_options_flow: float | None = None
    vpin_total: float | None = None
    vpin_percentile: float | None = None
    flow_regime: str | None = None
    top_uar_trades: list[UARTrade] = Field(default_factory=list)
    error: str | None = None
    latency_ms: float | None = None


class MacroAlert(BaseModel):
    type: str
    severity: str
    message: str


class MacroRegimePriorResponse(BaseModel):
    ok: bool
    ticker: str
    macro_regime_prior: dict[str, float] | None = None
    macro_regime_dominant: str | None = None
    macro_confidence: float | None = None
    macro_alerts: list[MacroAlert] = Field(default_factory=list)
    error: str | None = None
    latency_ms: float | None = None


# ────────────────────────────────────────────────────────────────────────────
# 1. /risk-neutral-density/{symbol}
# ────────────────────────────────────────────────────────────────────────────


@router.get("/risk-neutral-density/{symbol}", response_model=RiskNeutralDensityResponse)
async def get_risk_neutral_density_endpoint(
    symbol: str,
    expiry: str | None = None,
    rate: float = 0.05,
) -> RiskNeutralDensityResponse:
    """
    Risk-neutral density via Breeden-Litzenberger (q_skewness, q_kurtosis,
    modal price, bimodality). Cached 5 min.
    """
    sym = symbol.upper().strip()
    cache_key = f"{sym}:{expiry or ''}:{rate}"
    started = _time_mod.perf_counter()

    cached = _cache_get(_RND_CACHE, cache_key, _RND_TTL_S)
    if cached is not None:
        cached["latency_ms"] = (_time_mod.perf_counter() - started) * 1000
        return RiskNeutralDensityResponse(**cached)

    if sym.endswith(".BA"):
        return RiskNeutralDensityResponse(
            ok=False,
            ticker=sym,
            error="Risk-neutral density not available for .BA local symbols.",
        )

    try:
        from .options_router import options_snapshot_service

        opt_snap = await options_snapshot_service(sym, expiry=expiry, r=rate)
    except HTTPException:
        raise
    except Exception as exc:
        logger.warning("RND data fetch failed for %s: %s", sym, exc)
        raise HTTPException(status_code=503, detail=f"Data provider failed: {exc}") from exc

    if not getattr(opt_snap, "ok", False) or not getattr(opt_snap, "chain", None):
        raise HTTPException(
            status_code=404,
            detail=getattr(opt_snap, "error", "Option chain unavailable"),
        )

    spot = float(opt_snap.spot or 0.0)
    tte = _snapshot_dte_years(opt_snap)
    df = _build_rnd_chain_df(opt_snap.chain)
    if df.empty or len(df) < 5:
        return RiskNeutralDensityResponse(
            ok=False,
            ticker=sym,
            spot=spot,
            error="Insufficient call_price data for RND extraction.",
            latency_ms=(_time_mod.perf_counter() - started) * 1000,
        )

    try:
        from ..quant_engine.engines.predictive.risk_neutral_density_engine import (
            get_risk_neutral_density,
        )

        result = get_risk_neutral_density(df, spot, rate, tte)
    except Exception as exc:
        logger.warning("RND engine failed for %s: %s", sym, exc)
        return RiskNeutralDensityResponse(
            ok=False,
            ticker=sym,
            spot=spot,
            error=str(exc),
            latency_ms=(_time_mod.perf_counter() - started) * 1000,
        )

    if "error_msg" in result:
        return RiskNeutralDensityResponse(
            ok=False,
            ticker=sym,
            spot=spot,
            error=str(result["error_msg"]),
            latency_ms=(_time_mod.perf_counter() - started) * 1000,
        )

    payload = {
        "ok": True,
        "ticker": sym,
        "spot": spot,
        "expiry": expiry,
        "rate": rate,
        "q_skewness": result.get("q_skewness"),
        "q_kurtosis": result.get("q_kurtosis"),
        "modal_price": result.get("modal_price"),
        "is_bimodal": bool(result.get("is_bimodal", False)),
        "density_strikes": list(result.get("density_strikes") or result.get("rnd_strikes") or [])
        or None,
        "density_values": list(result.get("density_values") or result.get("rnd_density") or [])
        or None,
    }
    _cache_put(_RND_CACHE, cache_key, payload)
    payload["latency_ms"] = (_time_mod.perf_counter() - started) * 1000
    return RiskNeutralDensityResponse(**payload)


# ────────────────────────────────────────────────────────────────────────────
# 2. /dealer-flow/{symbol}
# ────────────────────────────────────────────────────────────────────────────


@router.get("/dealer-flow/{symbol}", response_model=DealerFlowResponse)
async def get_dealer_flow_endpoint(
    symbol: str,
    expiry: str | None = None,
) -> DealerFlowResponse:
    """
    Dealer flow dynamics (NDDE, charm flow series, vanna pressure, pinning,
    gamma walls). Cached 10 min.
    """
    sym = symbol.upper().strip()
    cache_key = f"{sym}:{expiry or ''}"
    started = _time_mod.perf_counter()

    cached = _cache_get(_DEALER_CACHE, cache_key, _DEALER_TTL_S)
    if cached is not None:
        cached["latency_ms"] = (_time_mod.perf_counter() - started) * 1000
        return DealerFlowResponse(**cached)

    if sym.endswith(".BA"):
        return DealerFlowResponse(
            ok=False,
            ticker=sym,
            error="Dealer flow analysis not available for .BA local symbols.",
        )

    try:
        from .options_router import options_snapshot_service

        opt_snap = await options_snapshot_service(sym, expiry=expiry, r=0.05)
    except HTTPException:
        raise
    except Exception as exc:
        logger.warning("Dealer-flow data fetch failed for %s: %s", sym, exc)
        raise HTTPException(status_code=503, detail=f"Data provider failed: {exc}") from exc

    if not getattr(opt_snap, "ok", False) or not getattr(opt_snap, "chain", None):
        raise HTTPException(
            status_code=404,
            detail=getattr(opt_snap, "error", "Option chain unavailable"),
        )

    spot = float(opt_snap.spot or 0.0)
    tte = _snapshot_dte_years(opt_snap)
    df = _build_dealer_chain_df(opt_snap.chain, spot)
    if df.empty:
        return DealerFlowResponse(
            ok=False,
            ticker=sym,
            spot=spot,
            error="Insufficient delta/gamma/OI data for dealer flow.",
            latency_ms=(_time_mod.perf_counter() - started) * 1000,
        )

    # VIX best-effort — fall back to neutral 18 if unavailable
    vix = 20.0
    try:
        from ..services.macro_data_provider import get_vix_spot

        vix = get_vix_spot()
        logger.info("Dealer-flow VIX spot for %s: %.2f", sym, vix)
    except Exception as exc:
        logger.warning("Dealer-flow VIX fetch failed for %s: %s", sym, exc)

    try:
        from ..quant_engine.engines.predictive.dealer_flow_dynamics_engine import (
            get_dealer_flow_dynamics,
        )

        result = get_dealer_flow_dynamics(df, spot, vix, tte, 0.05)
    except Exception as exc:
        logger.warning("Dealer flow engine failed for %s: %s", sym, exc)
        return DealerFlowResponse(
            ok=False,
            ticker=sym,
            spot=spot,
            error=str(exc),
            latency_ms=(_time_mod.perf_counter() - started) * 1000,
        )

    if "error_msg" in result:
        return DealerFlowResponse(
            ok=False,
            ticker=sym,
            spot=spot,
            error=str(result["error_msg"]),
            latency_ms=(_time_mod.perf_counter() - started) * 1000,
        )

    charm_series = result.get("charm_flow_series") or []
    if isinstance(charm_series, np.ndarray):
        charm_series = charm_series.tolist()

    payload = {
        "ok": True,
        "ticker": sym,
        "spot": spot,
        "expiry": expiry,
        "ndde": result.get("ndde"),
        "ndde_normalized": result.get("ndde_signal"),
        "ndde_usd": result.get("ndde"),
        "charm_flow_series": list(charm_series)[:7] or None,
        "vanna_pressure": result.get("vanna_pressure"),
        "pinning_strike": result.get("pinning_strike"),
        "pinning_probability": result.get("pinning_probability"),
        "gamma_wall_up": result.get("gamma_wall_up"),
        "gamma_wall_down": result.get("gamma_wall_down"),
        "dealer_directional_signal": result.get("dealer_directional_signal"),
        "regime_context": (
            "LONG_GAMMA"
            if float(result.get("dealer_directional_signal") or 0.0) > 0.2
            else (
                "SHORT_GAMMA"
                if float(result.get("dealer_directional_signal") or 0.0) < -0.2
                else "NEUTRAL_GAMMA"
            )
        ),
    }
    _cache_put(_DEALER_CACHE, cache_key, payload)
    payload["latency_ms"] = (_time_mod.perf_counter() - started) * 1000
    return DealerFlowResponse(**payload)


# ────────────────────────────────────────────────────────────────────────────
# 3. /options-flow-toxicity/{symbol}
# ────────────────────────────────────────────────────────────────────────────


@router.get("/options-flow-toxicity/{symbol}", response_model=OptionsFlowToxicityResponse)
async def get_options_flow_toxicity_endpoint(
    symbol: str,
    lookback_hours: int = 4,
) -> OptionsFlowToxicityResponse:
    """
    Options-flow VPIN / toxicity (call_flow, put_flow, net flow, regime, UAR
    trades). Cached 5 min.
    """
    sym = symbol.upper().strip()
    cache_key = f"{sym}:{lookback_hours}"
    started = _time_mod.perf_counter()

    cached = _cache_get(_FLOW_TOX_CACHE, cache_key, _FLOW_TOX_TTL_S)
    if cached is not None:
        cached["latency_ms"] = (_time_mod.perf_counter() - started) * 1000
        return OptionsFlowToxicityResponse(**cached)

    try:
        from ..services.options_trades_provider import fetch_recent_option_trades

        trades = fetch_recent_option_trades(sym, lookback_hours=lookback_hours)
    except ImportError:
        # Provider not yet wired in this env — degrade to empty trades.
        trades = pd.DataFrame()
    except HTTPException:
        raise
    except Exception as exc:
        logger.warning("Options-trades fetch failed for %s: %s", sym, exc)
        raise HTTPException(status_code=503, detail=f"Data provider failed: {exc}") from exc

    if trades is None or (isinstance(trades, pd.DataFrame) and trades.empty):
        return OptionsFlowToxicityResponse(
            ok=False,
            ticker=sym,
            lookback_hours=lookback_hours,
            error="No options trades available for the requested window.",
            latency_ms=(_time_mod.perf_counter() - started) * 1000,
        )

    try:
        from ..quant_engine.engines.predictive.options_order_flow_toxicity_engine import (
            get_options_flow_toxicity,
        )

        result = get_options_flow_toxicity(trades)
    except Exception as exc:
        logger.warning("Flow-toxicity engine failed for %s: %s", sym, exc)
        return OptionsFlowToxicityResponse(
            ok=False,
            ticker=sym,
            lookback_hours=lookback_hours,
            error=str(exc),
            latency_ms=(_time_mod.perf_counter() - started) * 1000,
        )

    if "error_msg" in result:
        return OptionsFlowToxicityResponse(
            ok=False,
            ticker=sym,
            lookback_hours=lookback_hours,
            error=str(result["error_msg"]),
            latency_ms=(_time_mod.perf_counter() - started) * 1000,
        )

    uar_raw = result.get("top_uar_trades") or []
    uar_trades: list[UARTrade] = []
    for entry in uar_raw[:10]:
        if isinstance(entry, dict):
            uar_trades.append(
                UARTrade(
                    timestamp=(
                        str(entry.get("timestamp")) if entry.get("timestamp") is not None else None
                    ),
                    strike=entry.get("strike"),
                    option_type=entry.get("option_type"),
                    size=entry.get("size"),
                    score=entry.get("score"),
                )
            )

    payload = {
        "ok": True,
        "ticker": sym,
        "lookback_hours": int(lookback_hours),
        "call_flow_signal": result.get("call_flow_signal"),
        "put_flow_signal": result.get("put_flow_signal"),
        "net_options_flow": result.get("net_options_flow"),
        "vpin_total": result.get("vpin_total"),
        "vpin_percentile": result.get("vpin_percentile"),
        "flow_regime": result.get("flow_regime"),
        "top_uar_trades": [t.model_dump() for t in uar_trades],
    }
    _cache_put(_FLOW_TOX_CACHE, cache_key, payload)
    payload["latency_ms"] = (_time_mod.perf_counter() - started) * 1000
    return OptionsFlowToxicityResponse(**payload)


# ────────────────────────────────────────────────────────────────────────────
# 4. /macro-regime-prior/{symbol}
# ────────────────────────────────────────────────────────────────────────────


@router.get("/macro-regime-prior/{symbol}", response_model=MacroRegimePriorResponse)
async def get_macro_regime_prior_endpoint(symbol: str) -> MacroRegimePriorResponse:
    """
    Macro regime prior (VIX, credit spreads, yield curve) → regime probability
    distribution + alerts. Cached 60 min — macro tape moves slowly.
    """
    sym = symbol.upper().strip()
    cache_key = sym
    started = _time_mod.perf_counter()

    cached = _cache_get(_MACRO_CACHE, cache_key, _MACRO_TTL_S)
    if cached is not None:
        cached["latency_ms"] = (_time_mod.perf_counter() - started) * 1000
        return MacroRegimePriorResponse(**cached)

    try:
        from ..services.macro_data_provider import fetch_macro_snapshot

        macro_data = await fetch_macro_snapshot()
        vix_spot = _finite_float(macro_data.get("vix_spot"))
        if vix_spot is not None:
            logger.info("Macro-regime VIX spot for %s: %.2f", sym, vix_spot)
    except ImportError:
        raise HTTPException(
            status_code=503,
            detail="Macro data provider not configured in this environment.",
        ) from None
    except HTTPException:
        raise
    except Exception as exc:
        logger.warning("Macro data fetch failed for %s: %s", sym, exc)
        raise HTTPException(status_code=503, detail=f"Data provider failed: {exc}") from exc

    try:
        from ..quant_engine.engines.predictive.macro_regime_prior_engine import (
            get_macro_regime_prior,
        )
    except ImportError as exc:
        logger.warning("macro_regime_prior_engine not available: %s", exc)
        return MacroRegimePriorResponse(
            ok=False,
            ticker=sym,
            error="macro_regime_prior_engine is not installed.",
            latency_ms=(_time_mod.perf_counter() - started) * 1000,
        )

    try:
        result = get_macro_regime_prior(macro_data)
    except Exception as exc:
        logger.warning("Macro regime engine failed for %s: %s", sym, exc)
        return MacroRegimePriorResponse(
            ok=False,
            ticker=sym,
            error=str(exc),
            latency_ms=(_time_mod.perf_counter() - started) * 1000,
        )

    if "error_msg" in result:
        return MacroRegimePriorResponse(
            ok=False,
            ticker=sym,
            error=str(result["error_msg"]),
            latency_ms=(_time_mod.perf_counter() - started) * 1000,
        )

    alerts_raw = result.get("macro_alerts") or []
    alerts = [
        MacroAlert(
            type=str(a.get("type", "info")),
            severity=str(a.get("severity", "low")),
            message=str(a.get("message", "")),
        )
        for a in alerts_raw
        if isinstance(a, dict)
    ]

    payload = {
        "ok": True,
        "ticker": sym,
        "macro_regime_prior": result.get("macro_regime_prior"),
        "macro_regime_dominant": result.get("macro_regime_dominant"),
        "macro_confidence": result.get("macro_confidence"),
        "macro_alerts": [a.model_dump() for a in alerts],
    }
    _cache_put(_MACRO_CACHE, cache_key, payload)
    payload["latency_ms"] = (_time_mod.perf_counter() - started) * 1000
    return MacroRegimePriorResponse(**payload)


# ════════════════════════════════════════════════════════════════════════════════
# META-SIGNAL — top-level consolidated endpoint
# ════════════════════════════════════════════════════════════════════════════════
#
# /meta-signal/{symbol} fans out to every motor in parallel, runs the regime
# classifier (with macro prior), fuses signals via the orchestrator, optionally
# blends in the meta-learner (when fitted), filters via signal_filter, and
# returns a single Pydantic payload that the frontend can render directly.
# ════════════════════════════════════════════════════════════════════════════════

from collections import deque  # noqa: E402

from ..services.final_signal_composer import compose_final_signal  # noqa: E402
from ..services.signal_filter import apply_signal_filters  # noqa: E402
from ..services.signal_quality import assess_signal_quality  # noqa: E402

_META_SIGNAL_CACHE: dict[str, dict[str, Any]] = {}
_META_SIGNAL_TTL_S = 15 * 60

# Bounded audit log (in-process; production should also persist to storage layer).
_META_SIGNAL_HISTORY: deque[dict[str, Any]] = deque(maxlen=500)
_META_SIGNAL_FEATURE_PREFIXES = {
    "risk_neutral_density": "rnd",
    "macro_regime_prior": "macro_regime",
}


# ── Pydantic response models ────────────────────────────────────────────────


class MetaSignalResponse(BaseModel):
    symbol: str
    timestamp: str
    direction: str
    signal: float
    confidence: float
    conviction_level: str
    p_up: float
    p_down: float
    p_neutral: float
    should_trade: bool
    position_size_pct: float
    position_size_usd: float | None = None
    kelly_fraction: float
    regime: str
    regime_probs: dict[str, float] | None = None
    conflict_score: float
    regime_alignment: bool
    conviction_drivers: list[str] = Field(default_factory=list)
    filter_reason: str | None = None
    meta_learner_used: bool = False
    meta_learner_version: str | None = None
    fusion_confidence: float = 0.0
    meta_learner_confidence: float = 0.0
    blended_confidence: float = 0.0
    shap_attribution: dict[str, Any] | None = None
    component_signals: dict[str, float] = Field(default_factory=dict)
    latency_ms: float = 0.0
    ok: bool = True
    error: str | None = None


class MetaSignalHistoryEntry(BaseModel):
    symbol: str
    timestamp: str
    direction: str
    signal: float
    confidence: float
    should_trade: bool
    position_size_pct: float


class MetaSignalHistoryResponse(BaseModel):
    symbol: str
    count: int
    entries: list[MetaSignalHistoryEntry] = Field(default_factory=list)


# ── Helpers ──────────────────────────────────────────────────────────────────


def _safe_call_signal(
    response: Any,
    *,
    signal_fields: tuple[str, ...] = ("signal", "directional_signal"),
) -> float:
    """Extract a numeric signal field from a Pydantic response or dict; 0.0 if missing."""
    if response is None:
        return 0.0
    obj: Any = response
    for fld in signal_fields:
        try:
            val = (
                getattr(obj, fld)
                if hasattr(obj, fld)
                else (obj.get(fld) if isinstance(obj, dict) else None)
            )
        except (AttributeError, TypeError):
            val = None
        if isinstance(val, (int, float)):
            return float(val)
    return 0.0


def _portfolio_context_default() -> dict[str, Any]:
    """Server has no portfolio of its own — return safe placeholder context."""
    return {
        "capital_total": 0.0,  # 0 ⇒ position_size_usd = 0
        "max_risk_per_trade_pct": 0.02,
        "current_drawdown_pct": 0.0,
        "n_open_positions": 0,
    }


def _market_context_default() -> dict[str, Any]:
    """Defaults for the filter when no live market_context provider is wired."""
    return {
        "current_vix": 18.0,
        "market_hours": "regular",
        "days_to_expiry": 30,
        "avg_volume_ratio": 1.0,
        "regime_certainty": 0.7,
    }


async def _gather_engine_outputs(
    sym: str,
    expiry: str | None,
) -> dict[str, Any]:
    """
    Fan out to every available motor endpoint in parallel.

    Returns a dict {motor_name: response_or_exception}. Each entry is either
    a Pydantic response, a dict, or an Exception (degraded-mode signalled by
    None signals downstream).
    """
    coros = [
        ("gamma_flip", get_gamma_flip(sym)),
        ("predictive_options_2", get_predictive_options_2(sym)),
        ("risk_neutral_density", get_risk_neutral_density_endpoint(sym, expiry=expiry, rate=0.05)),
        ("dealer_flow", get_dealer_flow_endpoint(sym, expiry=expiry)),
        ("options_flow_toxicity", get_options_flow_toxicity_endpoint(sym)),
        ("macro_regime_prior", get_macro_regime_prior_endpoint(sym)),
    ]
    names = [n for n, _ in coros]
    awaits = [c for _, c in coros]
    results = await asyncio.gather(*awaits, return_exceptions=True)
    return dict(zip(names, results, strict=False))


def _compute_regime(
    macro_resp: Any,
) -> dict[str, Any]:
    """
    Build the regime_result dict consumed by synthesize_fusion_signal().
    Falls back to UNKNOWN regime with uniform probs if macro engine failed.
    """
    if isinstance(macro_resp, Exception) or macro_resp is None:
        return {"regime": "UNKNOWN", "regime_probs": None}

    dominant = getattr(macro_resp, "macro_regime_dominant", None) or "UNKNOWN"
    probs = getattr(macro_resp, "macro_regime_prior", None)
    return {
        "regime": str(dominant).upper(),
        "regime_probs": probs if isinstance(probs, dict) else None,
    }


def _build_engine_outputs_for_fusion(
    motor_responses: dict[str, Any],
) -> dict[str, float]:
    """Reduce the parallel-fetched motor responses to scalar signals for the fusion layer."""
    out: dict[str, float] = {}
    mapping = {
        "gamma_flip": ("flip_signal", "directional_signal"),
        "risk_neutral_density": ("q_skewness",),
        "dealer_flow": ("dealer_directional_signal", "ndde"),
        "macro_regime_prior": ("macro_confidence",),
    }
    for motor, fields in mapping.items():
        resp = motor_responses.get(motor)
        if isinstance(resp, Exception):
            out[motor] = 0.0
            continue
        val = _safe_call_signal(resp, signal_fields=fields)
        out[motor] = float(val)
    bundle = motor_responses.get("predictive_options_2")
    if not isinstance(bundle, Exception) and bundle is not None:
        out.update(_predictive_options_bundle_signals(bundle))
    toxicity = motor_responses.get("options_flow_toxicity")
    if not isinstance(toxicity, Exception) and toxicity is not None:
        out["options_flow_toxicity"] = _safe_call_signal(
            toxicity,
            signal_fields=("toxicity_score", "directional_signal", "flow_toxicity"),
        )
    return out


def _nested_attr(obj: Any, *path: str) -> Any:
    cur = obj
    for key in path:
        if cur is None:
            return None
        cur = cur.get(key) if isinstance(cur, dict) else getattr(cur, key, None)
    return cur


def _predictive_options_bundle_signals(bundle: Any) -> dict[str, float]:
    """Derive scalar fusion signals from the richer Predictive Options 2 bundle."""
    signals: dict[str, float] = {}
    sd_gap = _safe_float(_nested_attr(bundle, "shadow_delta", "total_delta_gap"))
    if sd_gap is not None:
        signals["shadow_delta"] = float(np.clip(sd_gap / 100_000.0, -1.0, 1.0))
    speed = _safe_float(_nested_attr(bundle, "speed_instability", "summary", "instability_score"))
    if speed is not None:
        signals["speed_instability"] = float(np.clip(speed, -1.0, 1.0))
    skew_rr = _safe_float(_nested_attr(bundle, "volatility_skew", "metrics", "risk_reversal_25d"))
    if skew_rr is not None:
        signals["volatility_skew"] = float(np.clip(skew_rr * 10.0, -1.0, 1.0))
    tail_alert = str(_nested_attr(bundle, "tail_risk_smile", "alert", "level") or "").upper()
    if tail_alert:
        signals["tail_risk"] = -1.0 if tail_alert in {"HIGH", "EXTREME", "RISK_AVOID"} else 0.0
    pin_prob = _safe_float(_nested_attr(bundle, "zero_day_gamma_wall", "pinning_prob"))
    if pin_prob is not None:
        signals["zero_day_gamma_wall"] = float(np.clip(pin_prob, 0.0, 1.0))
    return signals


def _maybe_meta_learner_predict(
    component_signals: dict[str, float],
    explain: bool,
) -> tuple[dict[str, Any] | None, dict[str, Any] | None, bool, str | None]:
    """
    Best-effort call into a globally available, pre-fitted EnsembleMetaLearner.
    Returns (predict_proba_dict, shap_explanation) — both None if unavailable.
    """
    learner = get_or_load_meta_learner()
    meta_learner_version = getattr(learner, "version", None) if learner is not None else None
    if learner is None or not getattr(learner, "is_fitted", False):
        return None, None, False, meta_learner_version

    try:
        # Project component signals onto the learner's feature schema.
        feature_row = dict.fromkeys(learner.feature_names, 0.0)
        for motor, val in component_signals.items():
            feature_prefix = _META_SIGNAL_FEATURE_PREFIXES.get(motor, motor)
            for col in learner.feature_names:
                if col.startswith(f"{feature_prefix}__"):
                    feature_row[col] = val
        x_df = pd.DataFrame([feature_row])
        proba = learner.predict_proba(x_df, explain=explain)
        explanation = proba.pop("_explanation", None) if explain else None
        return proba, explanation, True, meta_learner_version
    except Exception as exc:
        logger.warning("meta-learner predict failed: %s", exc)
        return None, None, False, meta_learner_version


def _blend_fusion_with_meta(
    fusion: dict[str, Any],
    meta_proba: dict[str, Any],
    meta_learner_used: bool,
) -> tuple[dict[str, Any], float, float, float]:
    """Blend fusion and meta-learner confidence before quality/filter gates."""
    fusion_confidence = float(fusion.get("confidence", 0.0) or 0.0)
    if not meta_learner_used:
        return dict(fusion), fusion_confidence, 0.0, fusion_confidence

    class_probs = {
        label: float(meta_proba.get(label, 0.0) or 0.0) for label in ("UP", "DOWN", "NEUTRAL")
    }
    meta_direction = max(class_probs, key=class_probs.get)
    meta_confidence = max(class_probs.values())

    fusion_weight = 0.25 if bool(fusion.get("suppressed", False)) else 0.50
    meta_weight = 1.0 - fusion_weight
    meta_signal = (
        meta_confidence
        if meta_direction == "UP"
        else -meta_confidence if meta_direction == "DOWN" else 0.0
    )
    blended_confidence = (fusion_weight * fusion_confidence) + (meta_weight * meta_confidence)
    blended_signal = (fusion_weight * float(fusion.get("signal", 0.0) or 0.0)) + (
        meta_weight * meta_signal
    )

    blended = {
        **fusion,
        "confidence": float(max(0.0, min(1.0, blended_confidence))),
        "signal": float(max(-1.0, min(1.0, blended_signal))),
        "direction": meta_direction,
        "regime_alignment": True,
        "suppressed": False,
        "suppression_reason": None,
    }
    return blended, fusion_confidence, meta_confidence, blended["confidence"]


def _record_history_entry(payload: MetaSignalResponse) -> None:
    """Append minimal entry to bounded in-process audit log."""
    _META_SIGNAL_HISTORY.append(
        {
            "symbol": payload.symbol,
            "timestamp": payload.timestamp,
            "direction": payload.direction,
            "signal": payload.signal,
            "confidence": payload.confidence,
            "should_trade": payload.should_trade,
            "position_size_pct": payload.position_size_pct,
        }
    )


# ── Endpoint: /meta-signal/{symbol} ──────────────────────────────────────────


@router.get("/meta-signal/{symbol}", response_model=MetaSignalResponse)
async def get_meta_signal_endpoint(
    symbol: str,
    explain: bool = False,
    expiry: str | None = None,
    forward_periods: int = 5,
) -> MetaSignalResponse:
    """
    Top-level consolidated meta-signal:
      · fans out to every motor in parallel
      · runs regime classification (macro prior → markov)
      · fuses via orchestrator
      · optionally blends with meta-learner
      · filters & sizes via signal_filter + final_signal_composer

    Cache: 15 min when explain=False, never when explain=True.
    """
    sym = symbol.upper().strip()
    cache_key = f"{sym}:{expiry or ''}:{forward_periods}"
    started = _time_mod.perf_counter()

    if not explain:
        cached = _cache_get(_META_SIGNAL_CACHE, cache_key, _META_SIGNAL_TTL_S)
        if cached is not None:
            cached["latency_ms"] = (_time_mod.perf_counter() - started) * 1000
            return MetaSignalResponse(**cached)

    try:
        # 1. Parallel motor fan-out
        motor_responses = await _gather_engine_outputs(sym, expiry)

        # 2. Regime (macro prior → orchestrator regime_result)
        regime_result = _compute_regime(motor_responses.get("macro_regime_prior"))

        # 3. Component signals reduced to scalars
        component_signals = _build_engine_outputs_for_fusion(motor_responses)

        # 4. Orchestrator fusion (synthesize_fusion_signal)
        try:
            from ..services.probabilistic_signal_fusion import synthesize_fusion_signal

            fusion = synthesize_fusion_signal(sym, component_signals, regime_result)
        except Exception as exc:
            logger.warning("Fusion engine failed for %s: %s", sym, exc)
            fusion = {
                "signal": 0.0,
                "confidence": 0.0,
                "direction": "NEUTRAL",
                "regime": regime_result.get("regime", "UNKNOWN"),
                "conflict_score": 0.0,
                "regime_alignment": False,
                "suppressed": True,
                "suppression_reason": f"fusion_failed:{exc}",
                "conviction_drivers": [],
                "motor_signals": component_signals,
                "motor_weights": {},
            }

        # 5. Meta-learner blend (if available)
        meta_proba, shap_attr, meta_learner_used, meta_learner_version = (
            _maybe_meta_learner_predict(
                component_signals,
                explain,
            )
        )
        if meta_proba is None:
            # Synthesise a meta_result from fusion signal directly.
            sig = float(fusion.get("signal", 0.0))
            p_up = max(0.0, sig) if sig > 0 else 0.0
            p_down = max(0.0, -sig) if sig < 0 else 0.0
            p_neutral = 1.0 - (p_up + p_down)
            meta_proba = {"UP": p_up, "DOWN": p_down, "NEUTRAL": max(0.0, p_neutral)}

        # 6. Quality + filter + final composition
        (
            blended_result,
            fusion_confidence,
            meta_learner_confidence,
            blended_confidence,
        ) = _blend_fusion_with_meta(fusion, meta_proba, meta_learner_used)
        quality = assess_signal_quality(blended_result)
        filter_result = apply_signal_filters(
            blended_result,
            quality,
            _market_context_default(),
        )

        # ── 4. Engine Integration (Options Toxicity & Shadow Delta) ─────────────
        # PSEUDO-CÓDIGO: En un entorno de producción, esto iría aquí (o como un step
        # async previo dentro de la fusión L3, inyectándose en blended_result).
        #
        # 4A. Options Flow Toxicity
        # trades_df = fetch_options_trades(sym, lookback_minutes=60)
        # toxicity_result = get_options_flow_toxicity(trades_df, lookback_buckets=50)
        # if "error_msg" not in toxicity_result:
        #     toxicity_mult = toxicity_position_multiplier(
        #         vpin_total=toxicity_result["vpin_total"],
        #         vpin_percentile=toxicity_result["vpin_percentile"],
        #         flow_regime=toxicity_result.get("flow_regime", "NORMAL"),
        #     )
        #     blended_result["options_flow_toxicity"] = toxicity_mult
        #
        # 4B. Shadow Delta (requiere cartera de opciones)
        # portfolio_df = fetch_portfolio_options(sym)
        # if len(portfolio_df) > 0:
        #     shadow_engine = ShadowDeltaEngine(
        #         portfolio_df,
        #         spot_price=current_spot,
        #         default_expiry=dte_years,
        #     )
        #     shadow_result = shadow_engine.calculate()
        #     shadow_mult = shadow_delta_position_multiplier(
        #         shadow_delta=shadow_result["shadow_delta_weighted"],
        #         bs_delta=shadow_result["bs_delta_weighted"],
        #         vanna=shadow_result["vanna_weighted"],
        #         option_type="CALL",  # O PUT según blended_result direction
        #         skew_slope=shadow_result["skew_slope"],
        #     )
        #     blended_result["shadow_delta"] = shadow_mult
        # ────────────────────────────────────────────────────────────────────────

        final = compose_final_signal(
            meta_result={
                **meta_proba,
                "_explanation": shap_attr,
                "expected_move": 1.5,
                "q_kurtosis": _safe_call_signal(
                    motor_responses.get("risk_neutral_density"),
                    signal_fields=("q_kurtosis",),
                ),
            },
            engine_result=blended_result,
            quality=quality,
            filter_result=filter_result,
            portfolio_context=_portfolio_context_default(),
        )

        latency_ms = (_time_mod.perf_counter() - started) * 1000

        payload_dict: dict[str, Any] = {
            "symbol": sym,
            "timestamp": final.get("timestamp", datetime.utcnow().isoformat()),
            "direction": final.get("direction", "NEUTRAL"),
            "signal": float(final.get("signal", 0.0)),
            "confidence": float(final.get("confidence", 0.0)),
            "conviction_level": str(final.get("conviction_level", "INSUFFICIENT")),
            "p_up": float(final.get("p_up", 0.0)),
            "p_down": float(final.get("p_down", 0.0)),
            "p_neutral": float(final.get("p_neutral", 0.0)),
            "should_trade": bool(final.get("should_trade", False)),
            "position_size_pct": float(final.get("position_size_pct", 0.0)),
            "position_size_usd": float(final.get("position_size_usd", 0.0)),
            "kelly_fraction": float(final.get("kelly_fraction", 0.0)),
            "regime": str(final.get("regime", "UNKNOWN")),
            "regime_probs": regime_result.get("regime_probs"),
            "conflict_score": float(final.get("conflict_score", 0.0)),
            "regime_alignment": bool(blended_result.get("regime_alignment", True)),
            "conviction_drivers": list(blended_result.get("conviction_drivers") or []),
            "filter_reason": final.get("filter_reason"),
            "meta_learner_used": meta_learner_used,
            "meta_learner_version": meta_learner_version,
            "fusion_confidence": fusion_confidence,
            "meta_learner_confidence": meta_learner_confidence,
            "blended_confidence": blended_confidence,
            "shap_attribution": shap_attr if explain else None,
            "component_signals": component_signals,
            "latency_ms": latency_ms,
            "ok": True,
            "error": None,
        }

        response = MetaSignalResponse(**payload_dict)
        _record_history_entry(response)

        spot_t0 = 0.0
        for _resp_key in ("gamma_flip", "shadow_delta", "zomma", "tail_risk"):
            _resp = motor_responses.get(_resp_key)
            if _resp is None or isinstance(_resp, Exception):
                continue
            for _attr in ("spot", "underlying_price", "spot_price"):
                _val = _resp.get(_attr) if isinstance(_resp, dict) else getattr(_resp, _attr, None)
                try:
                    _candidate = float(_val) if _val is not None else 0.0
                except (TypeError, ValueError):
                    _candidate = 0.0
                if _candidate > 0:
                    spot_t0 = _candidate
                    break
            if spot_t0 > 0:
                break

        if spot_t0 <= 0 and os.environ.get("PRICE_T0_YF_FALLBACK") == "1":
            try:
                import yfinance as _yf

                _hist = _yf.Ticker(sym).history(period="1d")
                if not _hist.empty:
                    spot_t0 = float(_hist["Close"].iloc[-1])
            except Exception as _exc:
                logger.warning("yfinance fallback for price_t0 failed (%s): %s", sym, _exc)

        try:
            prediction_id = _prediction_logger.log_prediction(
                {
                    "symbol": sym,
                    "price_t0": spot_t0 if spot_t0 > 0 else None,
                    "direction": payload_dict["direction"],
                    "signal": payload_dict.get("signal", 0.0),
                    "confidence": payload_dict.get("blended_confidence", 0.0),
                    "p_up": payload_dict.get("p_up", 0.0),
                    "p_down": payload_dict.get("p_down", 0.0),
                    "p_neutral": payload_dict.get("p_neutral", 0.0),
                    "conviction_level": payload_dict.get("conviction_level", ""),
                    "should_trade": payload_dict.get("should_trade", False),
                    "position_size_pct": payload_dict.get("position_size_pct", 0.0),
                    "regime": payload_dict.get("regime", ""),
                    "conflict_score": payload_dict.get("conflict_score", 0.0),
                    "motor_signals": payload_dict.get("component_signals", {}),
                    "shap_attribution": payload_dict.get("shap_attribution"),
                    "filter_reason": payload_dict.get("filter_reason"),
                    "meta_learner_used": payload_dict.get("meta_learner_used", False),
                }
            )
            logger.info("Prediction logged: %s for %s", prediction_id, sym)
        except Exception as exc:
            logger.warning("Failed to log prediction for %s: %s", sym, exc)

        if not explain:
            _cache_put(_META_SIGNAL_CACHE, cache_key, payload_dict)

        return response

    except HTTPException:
        raise
    except Exception as exc:
        logger.error("meta-signal failed for %s: %s", sym, exc)
        return MetaSignalResponse(
            symbol=sym,
            timestamp=datetime.utcnow().isoformat(),
            direction="NEUTRAL",
            signal=0.0,
            confidence=0.0,
            conviction_level="INSUFFICIENT",
            p_up=0.0,
            p_down=0.0,
            p_neutral=1.0,
            should_trade=False,
            position_size_pct=0.0,
            kelly_fraction=0.0,
            regime="UNKNOWN",
            conflict_score=0.0,
            regime_alignment=False,
            latency_ms=(_time_mod.perf_counter() - started) * 1000,
            ok=False,
            error=str(exc),
        )


# ── Endpoint: /meta-signal/{symbol}/history ──────────────────────────────────


@router.get("/meta-signal/{symbol}/history", response_model=MetaSignalHistoryResponse)
async def get_meta_signal_history(
    symbol: str,
    limit: int = 50,
) -> MetaSignalHistoryResponse:
    """
    Return the last N meta-signal records for `symbol` from the in-process
    audit log. limit clamped to [1, 500].
    """
    sym = symbol.upper().strip()
    n = max(1, min(500, int(limit)))

    matching = [e for e in _META_SIGNAL_HISTORY if e.get("symbol") == sym]
    matching = matching[-n:]  # most recent n
    entries = [
        MetaSignalHistoryEntry(
            symbol=e["symbol"],
            timestamp=e["timestamp"],
            direction=e["direction"],
            signal=float(e["signal"]),
            confidence=float(e["confidence"]),
            should_trade=bool(e["should_trade"]),
            position_size_pct=float(e["position_size_pct"]),
        )
        for e in matching
    ]
    return MetaSignalHistoryResponse(
        symbol=sym,
        count=len(entries),
        entries=entries,
    )


# ── Endpoint: /predictions/{symbol} ──────────────────────────────────────────


@router.get("/predictions/{symbol}")
def get_predictions(symbol: str, last_n: int = 50) -> dict[str, Any]:
    """
    Return last `last_n` logged predictions for `symbol` from the SQLite
    prediction log, including any whose forward outcomes have not yet
    been backfilled.
    """
    sym = symbol.upper().strip()
    n = max(1, min(500, int(last_n)))

    with _prediction_logger._lock, _prediction_logger._connect() as conn:
        rows = conn.execute(
            """
            SELECT p.*,
                   o.outcome_return_1d,
                   o.outcome_return_5d,
                   o.outcome_direction_correct,
                   o.outcome_logged_at
            FROM predictions p
            LEFT JOIN outcomes o ON p.prediction_id = o.prediction_id
            WHERE p.symbol = ?
            ORDER BY p.timestamp ASC
            """,
            (sym,),
        ).fetchall()

    if not rows:
        return {"symbol": sym, "total": 0, "with_outcome": 0, "predictions": []}

    records = [dict(r) for r in rows]
    with_outcome = sum(1 for r in records if r.get("outcome_direction_correct") is not None)
    tail = records[-n:]
    return {
        "symbol": sym,
        "total": len(records),
        "with_outcome": with_outcome,
        "predictions": tail,
    }


# ── Endpoint: /accumulation-stats/{symbol} ───────────────────────────────────

_ACCUM_STATS_CACHE: dict[str, dict[str, Any]] = {}
_ACCUM_STATS_TTL_S: int = 300  # 5 minutes

_RETRAIN_TARGET = 300
_MIN_OUTCOMES_FOR_ACCURACY = 20


def _institutional_accumulation_rows(symbol: str) -> list[sqlite3.Row]:
    if not INSTITUTIONAL_PREDICTIONS_DB.exists():
        return []
    sym = symbol.upper().strip()
    with sqlite3.connect(INSTITUTIONAL_PREDICTIONS_DB) as conn:
        conn.row_factory = sqlite3.Row
        return conn.execute(
            """
            SELECT p.direction, p.should_trade, p.timestamp, p.price_t0,
                   COALESCE(length(p.motor_signals), 0) > 2 AS has_motor_signals,
                   COALESCE(length(p.shap_attribution), 0) > 2 AS has_shap,
                   NULL AS outcome_return_1d,
                   o.outcome_return AS outcome_return_5d,
                   o.outcome_direction_correct
            FROM predictions p
            LEFT JOIN outcomes o
              ON p.prediction_id = o.prediction_id
             AND o.n_days = 5
            WHERE p.symbol = ?
            ORDER BY p.timestamp ASC
            """,
            (sym,),
        ).fetchall()


def _training_readiness_from_rows(
    symbol: str,
    rows: list[sqlite3.Row],
    *,
    min_samples: int,
) -> dict[str, Any]:
    complete_5d = sum(1 for r in rows if r["outcome_return_5d"] is not None)
    missing_price_t0 = sum(
        1 for r in rows if r["price_t0"] is None or float(r["price_t0"] or 0) <= 0
    )
    with_motor_signals = sum(1 for r in rows if bool(r["has_motor_signals"]))
    with_shap = sum(1 for r in rows if bool(r["has_shap"]))
    reasons: list[str] = []
    if complete_5d < min_samples:
        reasons.append("minimum_samples")
    if missing_price_t0:
        reasons.append("missing_price_t0")
    return {
        "symbol": symbol.upper().strip(),
        "primary_horizon_days": 5,
        "total_predictions": len(rows),
        "complete_1d_outcomes": 0,
        "complete_5d_outcomes": complete_5d,
        "missing_price_t0": missing_price_t0,
        "with_motor_signals": with_motor_signals,
        "with_shap_attribution": with_shap,
        "source_db": str(INSTITUTIONAL_PREDICTIONS_DB),
        "model_gate": {
            "status": "approved" if not reasons else "blocked",
            "minimum_samples": int(min_samples),
            "reasons": reasons,
        },
    }


@router.get("/accumulation-stats/{symbol}")
def get_accumulation_stats(symbol: str) -> dict[str, Any]:
    """
    Aggregate stats describing how the prediction-logging pipeline is
    accumulating training data for `symbol`. Drives the frontend Data
    Accumulation Monitor panel. Cached 5 min.
    """
    sym = symbol.upper().strip()

    cached = _cache_get(_ACCUM_STATS_CACHE, sym, _ACCUM_STATS_TTL_S)
    if cached is not None:
        return cached

    rows = _institutional_accumulation_rows(sym)
    use_institutional = bool(rows)
    if not rows:
        with _prediction_logger._lock, _prediction_logger._connect() as conn:
            rows = conn.execute(
                """
                SELECT p.direction, p.should_trade, p.timestamp,
                       o.outcome_return_1d, o.outcome_return_5d,
                       o.outcome_direction_correct
                FROM predictions p
                LEFT JOIN outcomes o ON p.prediction_id = o.prediction_id
                WHERE p.symbol = ?
                ORDER BY p.timestamp ASC
                """,
                (sym,),
            ).fetchall()

    total = len(rows)
    if total == 0:
        readiness = _prediction_logger.get_training_readiness(sym, min_samples=_RETRAIN_TARGET)
        payload = {
            "symbol": sym,
            "total_predictions": 0,
            "with_outcome": 0,
            "without_outcome": 0,
            "accuracy_1d": None,
            "accuracy_5d": None,
            "should_trade_rate": 0.0,
            "direction_distribution": {"UP": 0.0, "DOWN": 0.0, "NEUTRAL": 0.0},
            "days_until_retrain_ready": None,
            "retrain_ready": False,
            "last_prediction_ts": None,
            "meta_learner_version": "synthetic_v1",
            "training_quality": readiness,
            "model_gate": readiness["model_gate"],
            "engine_coverage_summary": summarize_engine_coverage(),
        }
        _cache_put(_ACCUM_STATS_CACHE, sym, payload)
        return payload

    with_outcome = sum(1 for r in rows if r["outcome_direction_correct"] is not None)
    without_outcome = total - with_outcome

    # Accuracy: compute per-horizon only when at least MIN outcomes for that horizon
    rows_1d = [r for r in rows if r["outcome_return_1d"] is not None]
    rows_5d = [r for r in rows if r["outcome_return_5d"] is not None]
    accuracy_1d: float | None = None
    accuracy_5d: float | None = None
    if len(rows_1d) > _MIN_OUTCOMES_FOR_ACCURACY:
        correct = sum(int(bool(r["outcome_direction_correct"])) for r in rows_1d)
        accuracy_1d = float(correct / len(rows_1d))
    if len(rows_5d) > _MIN_OUTCOMES_FOR_ACCURACY:
        correct = sum(int(bool(r["outcome_direction_correct"])) for r in rows_5d)
        accuracy_5d = float(correct / len(rows_5d))

    should_trade_rate = float(sum(int(bool(r["should_trade"])) for r in rows) / total)

    dir_counts = {"UP": 0, "DOWN": 0, "NEUTRAL": 0}
    for r in rows:
        d = str(r["direction"]).upper()
        if d in dir_counts:
            dir_counts[d] += 1
    direction_distribution = {k: float(v / total) for k, v in dir_counts.items()}

    last_ts = rows[-1]["timestamp"]
    first_ts = rows[0]["timestamp"]
    try:
        first_dt = datetime.fromisoformat(first_ts)
        last_dt = datetime.fromisoformat(last_ts)
        elapsed_days = max((last_dt - first_dt).total_seconds() / 86400.0, 1e-6)
    except Exception:
        elapsed_days = 1.0
    preds_per_day = total / elapsed_days if elapsed_days > 0 else 0.0

    remaining = max(0, _RETRAIN_TARGET - with_outcome)
    if remaining == 0:
        days_until_retrain_ready = 0
    elif preds_per_day > 0:
        days_until_retrain_ready = int(round(remaining / preds_per_day))
    else:
        days_until_retrain_ready = None

    readiness = (
        _training_readiness_from_rows(sym, rows, min_samples=_RETRAIN_TARGET)
        if use_institutional
        else _prediction_logger.get_training_readiness(sym, min_samples=_RETRAIN_TARGET)
    )
    payload = {
        "symbol": sym,
        "total_predictions": total,
        "with_outcome": with_outcome,
        "without_outcome": without_outcome,
        "accuracy_1d": accuracy_1d,
        "accuracy_5d": accuracy_5d,
        "should_trade_rate": should_trade_rate,
        "direction_distribution": direction_distribution,
        "days_until_retrain_ready": days_until_retrain_ready,
        "retrain_ready": with_outcome >= _RETRAIN_TARGET,
        "last_prediction_ts": last_ts,
        "meta_learner_version": ("real_v1" if with_outcome >= _RETRAIN_TARGET else "synthetic_v1"),
        "data_source": "institutional_backfill" if use_institutional else "prediction_logger",
        "training_quality": readiness,
        "model_gate": readiness["model_gate"],
        "engine_coverage_summary": summarize_engine_coverage(),
    }
    _cache_put(_ACCUM_STATS_CACHE, sym, payload)
    return payload


@router.get("/predictive-backfill/status")
def get_predictive_backfill_status_endpoint() -> dict[str, Any]:
    """Read-only status for OHLCV backfill, data quality and model gate."""
    return get_predictive_backfill_status()


@router.get("/engine-coverage")
def get_predictive_engine_coverage() -> dict[str, Any]:
    """Static audit of probabilistic/predictive engine wiring."""
    coverage = build_engine_coverage()
    return {
        "engine_coverage_summary": summarize_engine_coverage(coverage),
        "engine_coverage": coverage,
    }


@router.post("/predictive-audit/run")
def run_predictive_audit_report() -> dict[str, Any]:
    """Generate JSON/Markdown artifacts under artifacts/reports."""
    return generate_predictive_audit_report()
