import numpy as np
import pytest

from backend.quant_engine.domain.confluence.confluence_models import GEXLevels
from backend.quant_engine.engines.confluence.options_confluence import OptionsConfluenceEngine
from backend.quant_engine.engines.options.options import OptionsEngine
from backend.quant_engine.engines.options.options_flow import OptionsFlowSignalEngine
from backend.quant_engine.math.options.bsm import BlackScholesPricer, OptionType
from backend.quant_engine.math.options.derivatives import SVIParameters, VolatilitySurfaceMath
from backend.quant_engine.math.options.gamma_flip_probability import estimate_gamma_flip_probability


def test_black_scholes_pricer():
    # Test Black-Scholes pricing and Greeks calculation
    S, K, T, r, sigma = 100.0, 100.0, 1.0, 0.05, 0.20
    price_c = BlackScholesPricer.price(S, K, T, r, sigma, OptionType.CALL)
    price_p = BlackScholesPricer.price(S, K, T, r, sigma, OptionType.PUT)

    assert price_c > 0.0
    assert price_p > 0.0
    assert abs(price_c - 10.45) < 0.05

    greeks = BlackScholesPricer.greeks(S, K, T, r, sigma, OptionType.CALL)
    assert "delta" in greeks
    assert "gamma" in greeks
    assert "vanna" in greeks
    assert "charm" in greeks


def test_gamma_flip_probability():
    # Spot = 100, ZGL = 95, IV = 0.20, DTE = 30
    prob = estimate_gamma_flip_probability(100.0, 95.0, 0.20, 30.0, 0.05)
    assert 0.0 <= prob <= 1.0

    # Already crossed or extremely close
    assert estimate_gamma_flip_probability(100.0, 100.0, 0.20, 30.0, 0.05) == 1.0

    # Distance is huge, prob should be lower
    prob_close = estimate_gamma_flip_probability(100.0, 98.0, 0.20, 30.0, 0.05)
    prob_far = estimate_gamma_flip_probability(100.0, 90.0, 0.20, 30.0, 0.05)
    assert prob_close > prob_far


def test_svi_and_bl_pdf():
    # SVI slices parameters
    params = SVIParameters(tte=0.1, a=0.04, b=0.1, rho=-0.3, m=0.0, sigma=0.1, forward=100.0)

    # Verify SVI slice conversion
    vols = VolatilitySurfaceMath.svi_to_vol_slice(np.array([90.0, 100.0, 110.0]), params)
    assert len(vols) == 3
    assert np.all(vols > 0)

    # Breeden-Litzenberger PDF
    pdf_res = VolatilitySurfaceMath.bl_pdf(params, r=0.05, n_points=50)
    assert len(pdf_res.density) == 50
    assert len(pdf_res.cdf) == 50
    assert pdf_res.risk_neutral_mean > 0.0
    assert pdf_res.risk_neutral_std > 0.0


def test_options_engine_analysis():
    strikes = np.array([95.0, 100.0, 105.0])
    call_oi = np.array([100.0, 500.0, 200.0])
    put_oi = np.array([300.0, 400.0, 100.0])
    call_iv = np.array([0.22, 0.20, 0.18])
    put_iv = np.array([0.24, 0.21, 0.19])

    res = OptionsEngine.analyze_chain(
        ticker="AAPL",
        spot=100.0,
        strikes=strikes,
        call_oi=call_oi,
        put_oi=put_oi,
        call_iv=call_iv,
        put_iv=put_iv,
        tte=0.1,
        atm_iv=0.20,
        r=0.05,
    )

    assert res.ok
    assert res.ticker == "AAPL"
    assert res.exposures.total_gex is not None
    assert res.exposures.total_vex is not None
    assert res.exposures.total_cex is not None
    assert res.options_mic_score >= 0.0


def test_options_confluence_engine():
    # Mock SMC result object
    class SMCResultMock:
        def __init__(self):
            self.bias = "LONG"
            self.liquidity_sweeps = ["daily_low"]
            self.active_ict_model = "LIQUIDITY_SWEEP"
            self.fvg_zones = [{"top": 99.0, "bottom": 98.0}]
            self.fvg_count_active = 1

    smc = SMCResultMock()
    gex_levels = GEXLevels(
        put_wall=99.0, zero_gamma_level=97.0, volatility_magnet=98.5, max_pain=100.0
    )

    res = OptionsConfluenceEngine.validate(smc, gex_levels, spot=99.2)
    assert res.confluence_score > 0.0
    assert res.is_ob_validated
    assert res.is_sweep_confirmed
    assert res.is_magnet_active
    assert "OB-WALL_SUPPORT" in res.summary


def test_options_flow_signal_engine():
    engine = OptionsFlowSignalEngine()
    rows = [
        {
            "strike": 105.0,
            "option_type": "call",
            "side": "ask",
            "volume": 1000,
            "oi": 100,
            "price": 1.50,
        },
        {
            "strike": 95.0,
            "option_type": "put",
            "side": "bid",
            "volume": 500,
            "oi": 200,
            "price": 0.80,
        },
    ]

    sig = engine.analyze(rows)
    assert sig.total_volume == 1500
    assert sig.total_premium > 0.0
    assert sig.institutional_signal in (
        "call_pressure",
        "bullish_flow",
        "ambiguous",
        "put_pressure",
        "bearish_flow",
        "balanced",
    )


def test_matrix_ops_and_filters():
    from backend.quant_engine.math.technical.matrix_ops import (
        ParticleFilter,
        estimate_mjd_params,
        safe_divide,
    )

    # Test safe divide
    res = safe_divide(10.0, 2.0)
    assert res.is_success
    assert res.unwrap() == 5.0
    assert safe_divide(10.0, 0.0).is_failure

    # Test particle filter instantiation and update
    pf = ParticleFilter(n_particles=100)
    pf.update(0.01, 1.2)
    state = pf.get_state()
    assert 0.0 <= state.pr_ordered <= 1.0
    assert 0.0 <= state.trend_strength <= 1.0

    # Test Merton Jump-Diffusion param estimation
    returns = np.random.normal(0, 0.01, 100)
    mjd = estimate_mjd_params(returns)
    assert mjd.is_success
    params = mjd.unwrap()
    assert "jump_intensity" in params
    assert "mu_j" in params


def test_microstructure_engines():
    from backend.quant_engine.engines.technical.cor3m import COR3M_Signal_Engine, EngineConfig
    from backend.quant_engine.engines.technical.vsa_forecast import VSAForecastEngine

    # Test COR3M Engine instantiation and analysis
    cfg = EngineConfig(
        percentile_window=10, panic_threshold=0.90, signal_threshold=0.85, memory_window=3
    )
    cor = COR3M_Signal_Engine(config=cfg)
    history = np.array([10.0] * 15, dtype=np.float64)
    res = cor.analyze_current_state(history)
    assert res.is_success

    # Test VSA Forecast Engine
    vsa = VSAForecastEngine(vol_avg_period=20)
    ohlcv = np.random.rand(35, 5) + 1.0
    res_vfi = vsa.calculate_vfi(ohlcv, period=14)
    assert res_vfi.is_success


def test_predictive_engines():
    import os
    import tempfile

    import torch

    from backend.quant_engine.engines.predictive.multimodal_predictive import (
        MultimodalPredictiveEngine,
    )
    from backend.quant_engine.engines.predictive.quantum_alpha import (
        MultimodalModelConfig,
        QuantumAlphaEngine,
        QuantumAlphaLSTM,
    )

    config = MultimodalModelConfig(
        hidden_channels=8, event_dim=5, n_layers=1, n_classes=3, dropout=0.0
    )

    # Save a temporary weights file for QuantumAlphaLSTM
    temp_model = QuantumAlphaLSTM(config)
    with tempfile.NamedTemporaryFile(suffix=".pth", delete=False) as tmp:
        weights_path = tmp.name

    try:
        torch.save(temp_model.state_dict(), weights_path)

        # Initialize engines
        alpha_engine = QuantumAlphaEngine(weights_path=weights_path, config=config)
        predictive_engine = MultimodalPredictiveEngine(weights_path=weights_path, config=config)

        # Test analyze
        ohlcv = np.random.rand(25, 5)
        res = alpha_engine.analyze(ticker="AAPL", ohlcv=ohlcv)
        assert res.is_success

        # Test batch prep
        fund_data = np.random.rand(25, 5)
        news_data = np.random.rand(25, 3)
        res_batch = predictive_engine.prepare_batch(
            fund_data=fund_data, news_data=news_data, ticker="AAPL", config=config
        )
        assert res_batch.is_success
    finally:
        if os.path.exists(weights_path):
            os.remove(weights_path)


def test_expected_move_engine():
    from backend.quant_engine.math.options.expected_move import (
        ExpectedMoveEngine,
        ExpectedMoveResult,
    )

    # Test valid calculations
    res = ExpectedMoveEngine.calculate(spot=100.0, iv=0.20, dte=30)
    assert res.is_success

    report = res.unwrap()
    assert isinstance(report, ExpectedMoveResult)
    assert report.spot == 100.0
    assert report.iv == 0.20
    assert report.dte == 30
    assert report.expected_move > 0.0
    assert report.upper_bound > 100.0
    assert report.lower_bound < 100.0

    summary = report.get_summary()
    assert summary["spot"] == 100.0

    # Test invalid inputs
    res_err = ExpectedMoveEngine.calculate(spot=-5.0, iv=0.20, dte=30)
    assert res_err.is_failure


def test_markov_regime_engine():
    import pandas as pd

    from backend.quant_engine.math.predictive.markov_regime import MarkovRegimeEngine, MarkovReport

    # Create synthetic price series
    prices = [100.0 * (1.01**i) for i in range(100)]
    df = pd.DataFrame({"close": prices})

    engine = MarkovRegimeEngine()
    res = engine.analyze(symbol="AAPL", df=df)
    assert res.is_success

    report = res.unwrap()
    assert isinstance(report, MarkovReport)
    assert report.symbol == "AAPL"
    assert len(report.states) == 3
    assert 0.0 <= report.state_confidence <= 1.0
    assert report.regime_signal in ("CRITICAL", "SHIFTING", "STABLE")


def test_ensemble_meta_learner_predictor():
    from backend.quant_engine.engines.predictive.meta_learner import (
        CalibratorBundle,
        EnsembleMetaLearnerPredictor,
    )

    # Mock model
    class MockModel:
        def __init__(self):
            self.classes_ = np.array([0, 1, 2])

        def predict_proba(self, x):
            n = x.shape[0]
            res = np.zeros((n, 3))
            res[:, 0] = 0.1
            res[:, 1] = 0.2
            res[:, 2] = 0.7
            return res

    model = MockModel()
    feature_names = [
        "price__return_5d",
        "price__return_20d",
        "price__rsi_14_normalized",
        "price__price_vs_ma20",
    ]

    # 1. Sin calibrador
    predictor = EnsembleMetaLearnerPredictor(model, feature_names)
    assert predictor.is_fitted

    # Input dict
    x_dict = {
        "price__return_5d": 0.01,
        "price__return_20d": 0.02,
        "price__rsi_14_normalized": 0.5,
        "price__price_vs_ma20": 0.01,
    }

    res = predictor.predict_proba(x_dict)
    assert res.is_success
    probs = res.unwrap()
    assert "DOWN" in probs
    assert "NEUTRAL" in probs
    assert "UP" in probs
    assert pytest.approx(sum(probs.values())) == 1.0

    # Input numpy array
    x_arr = np.array([0.01, 0.02, 0.5, 0.01])
    res_arr = predictor.predict_proba(x_arr)
    assert res_arr.is_success

    # 2. Con calibrador mock
    class MockCalibrator:
        def __init__(self):
            self.classes_ = np.array([0, 1, 2])

        def predict_proba(self, x):
            return x

    cal = CalibratorBundle(calibrator=MockCalibrator(), is_fitted=True)
    predictor_cal = EnsembleMetaLearnerPredictor(model, feature_names, calibrator=cal)
    res_cal = predictor_cal.predict_proba(x_dict)
    assert res_cal.is_success


def test_vol_term_structure_engine():
    from backend.quant_engine.math.options.vol_term_structure import (
        VolatilityTermStructureEngine,
        VolTermStructureReport,
        analyze_vol_term_structure,
    )

    # 1. Test normal contango (IV sube con el tiempo)
    atm_curve_normal = np.array(
        [
            [15.0, 0.18],
            [30.0, 0.20],
            [60.0, 0.22],
            [90.0, 0.24],
            [180.0, 0.26],
        ]
    )

    engine = VolatilityTermStructureEngine(short_tenor=30, long_tenor=90)
    res = engine.analyze(atm_curve_normal)
    assert res.is_success

    report = res.unwrap()
    assert isinstance(report, VolTermStructureReport)
    assert report.short_tenor == 30
    assert report.long_tenor == 90
    assert report.iv_short == 0.20
    assert report.iv_long == 0.24
    assert report.ratio < 1.0
    assert report.slope > 0.0
    assert "NORMAL" in report.regime
    assert report.inversion_alert is False

    # 2. Test inversion / backwardation
    atm_curve_inverted = np.array(
        [
            [15.0, 0.45],
            [30.0, 0.40],
            [60.0, 0.35],
            [90.0, 0.30],
            [180.0, 0.25],
        ]
    )
    res_inv = analyze_vol_term_structure(atm_curve_inverted, short_tenor=30, long_tenor=90)
    assert res_inv.is_success
    report_inv = res_inv.unwrap()
    assert report_inv.ratio > 1.0
    assert report_inv.slope < 0.0
    assert "PANIC" in report_inv.regime
    assert report_inv.inversion_alert is True

    # 3. Test validation errors
    res_err = engine.analyze(np.empty((0, 2)))
    assert res_err.is_failure
