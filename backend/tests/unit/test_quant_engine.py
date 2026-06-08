import math
import numpy as np
import pytest

from src.quant_engine.math.options.bsm import BlackScholesPricer, OptionType
from src.quant_engine.math.options.derivatives import VolatilitySurfaceMath, SVIParameters, GEXMath
from src.quant_engine.math.options.iv_primitives import atm_iv_from_chain
from src.quant_engine.math.options.gamma_flip_probability import estimate_gamma_flip_probability
from src.quant_engine.domain.options.options_models import OptionsResult, DealerExposures
from src.quant_engine.domain.confluence.confluence_models import GEXLevels
from src.quant_engine.engines.options.options import OptionsEngine
from src.quant_engine.engines.options.options_flow import OptionsFlowSignalEngine
from src.quant_engine.engines.options.strategy_payoff import StrategyPayoffEngine
from src.quant_engine.engines.confluence.options_confluence import OptionsConfluenceEngine


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
    params = SVIParameters(
        tte=0.1,
        a=0.04,
        b=0.1,
        rho=-0.3,
        m=0.0,
        sigma=0.1,
        forward=100.0
    )
    
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
        r=0.05
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
        put_wall=99.0,
        zero_gamma_level=97.0,
        volatility_magnet=98.5,
        max_pain=100.0
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
        {"strike": 105.0, "option_type": "call", "side": "ask", "volume": 1000, "oi": 100, "price": 1.50},
        {"strike": 95.0, "option_type": "put", "side": "bid", "volume": 500, "oi": 200, "price": 0.80}
    ]
    
    sig = engine.analyze(rows)
    assert sig.total_volume == 1500
    assert sig.total_premium > 0.0
    assert sig.institutional_signal in ("call_pressure", "bullish_flow", "ambiguous", "put_pressure", "bearish_flow", "balanced")


def test_matrix_ops_and_filters():
    from src.quant_engine.math.technical.matrix_ops import ParticleFilter, safe_divide, estimate_mjd_params

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
    from src.quant_engine.engines.technical.cor3m import COR3M_Signal_Engine, EngineConfig
    from src.quant_engine.engines.technical.vsa_forecast import VSAForecastEngine
    
    # Test COR3M Engine instantiation and analysis
    cfg = EngineConfig(percentile_window=10, panic_threshold=0.90, signal_threshold=0.85, memory_window=3)
    cor = COR3M_Signal_Engine(config=cfg)
    history = np.array([10.0] * 15, dtype=np.float64)
    res = cor.analyze_current_state(history)
    assert res.is_success
    
    # Test VSA Forecast Engine
    vsa = VSAForecastEngine(vol_avg_period=20)
    ohlcv = np.random.rand(35, 5) + 1.0
    res_vfi = vsa.calculate_vfi(ohlcv, period=14)
    assert res_vfi.is_success
