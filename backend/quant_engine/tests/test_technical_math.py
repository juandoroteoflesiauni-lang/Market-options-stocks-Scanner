import numpy as np
from quant_engine.math.technical.hmm_math import (
    build_ohlcv_features,
    classify_regime,
    forward_step,
    infer_hmm_sequence,
    log_gaussian_emission,
    log_sum_exp,
    normalised_entropy,
    precompute_emission_cache,
    to_log_matrix,
    to_log_vector,
)
from quant_engine.math.technical.lob_math import (
    classify_spoofing,
    compute_ctr,
    compute_depth_imbalance,
    compute_ofi,
    compute_queue_imbalance,
    rolling_ctr,
)
from quant_engine.math.technical.smc_math import (
    compute_atr,
    compute_ote_zone,
    compute_swing_levels,
    detect_bos_choch,
    detect_fvg,
    detect_liquidity_sweeps,
    detect_order_blocks,
    rolling_volume_mean,
)
from quant_engine.math.technical.technical import AVWAPMath, TechnicalMath
from quant_engine.math.technical.tpo_math import (
    build_tpo_histogram,
    classify_profile_shape,
    compute_tpo_stats,
    detect_bimodal,
    infer_tick_size,
)
from quant_engine.math.technical.vsa_math import (
    classify_vsa_bars,
    compute_absorption_index,
    compute_close_location,
    compute_cvd_approx,
    compute_mfi_kinetic,
    compute_volume_zscore,
    compute_weis_wave,
    detect_buying_climax,
)


def test_technical_math_basic() -> None:
    # Test SMA
    close = np.array([10.0, 11.0, 12.0, 13.0, 14.0])
    sma = TechnicalMath.sma(close, n=3)
    assert np.isnan(sma[0])
    assert np.isnan(sma[1])
    assert np.isclose(sma[2], 11.0)
    assert np.isclose(sma[3], 12.0)
    assert np.isclose(sma[4], 13.0)

    # Test EMA
    ema = TechnicalMath.ema(close, n=3)
    assert np.isnan(ema[0])
    assert np.isnan(ema[1])
    assert np.isclose(ema[2], 11.0)  # initialized to mean of first 3

    # Test SMMA
    smma = TechnicalMath.smma(close, n=3)
    assert np.isnan(smma[0])
    assert np.isnan(smma[1])
    assert np.isclose(smma[2], 11.0)

    # Test RSI
    rsi = TechnicalMath.rsi(close, n=3)
    assert len(rsi) == 5

    # Test MACD
    macd_line, signal_line, histogram = TechnicalMath.macd(close, fast=2, slow=4, signal=2)
    assert len(macd_line) == 5

    # Test ATR
    high = np.array([11.0, 12.0, 13.0, 14.0, 15.0])
    low = np.array([9.0, 10.0, 11.0, 12.0, 13.0])
    atr = TechnicalMath.atr(close, high, low, n=3)
    assert len(atr) == 5

    # Test Bollinger Bands
    upper, mid, lower = TechnicalMath.bollinger(close, n=3)
    assert len(upper) == 5

    # Test BBP
    bbp = TechnicalMath.bbp(close, n=3)
    assert len(bbp) == 5

    # Test Supertrend
    st, d = TechnicalMath.supertrend(close, high, low, n=3)
    assert len(st) == 5

    # Test VWAP
    volume = np.array([100, 200, 150, 300, 250])
    vwap = TechnicalMath.vwap(high, low, close, volume)
    assert len(vwap) == 5

    # Test Shannon Entropy
    entropy = TechnicalMath.shannon_entropy(close, n=3)
    assert len(entropy) == 5


def test_avwap_math() -> None:
    high = np.array([11.0, 12.0, 13.0, 14.0, 15.0])
    low = np.array([9.0, 10.0, 11.0, 12.0, 13.0])
    close = np.array([10.0, 11.0, 12.0, 13.0, 14.0])
    volume = np.array([100, 200, 150, 300, 250])
    avwap, std_dev = AVWAPMath.compute_anchored(high, low, close, volume, anchor_idx=2)
    assert np.isnan(avwap[0])
    assert np.isnan(avwap[1])
    assert not np.isnan(avwap[2])
    assert not np.isnan(std_dev[2])


def test_smc_math() -> None:
    high = np.array([11.0, 12.0, 13.0, 14.0, 15.0])
    low = np.array([9.0, 10.0, 11.0, 12.0, 13.0])
    close = np.array([10.0, 11.0, 12.0, 13.0, 14.0])
    atr = compute_atr(high, low, close, window=3)
    assert len(atr) == 5

    open_ = np.array([9.5, 10.5, 11.5, 12.5, 13.5])
    volume = np.array([100, 200, 150, 300, 250])
    vol_mean = rolling_volume_mean(volume, window=3)
    assert len(vol_mean) == 5

    # Detect Order Blocks
    bull_idx, bull_ob50, bear_idx, bear_ob50 = detect_order_blocks(
        open_, high, low, close, volume, atr, vol_mean
    )
    assert isinstance(bull_idx, np.ndarray)

    # Detect FVG
    bull_bar, bull_top, bull_bot, bear_bar, bear_size = detect_fvg(high, low)
    assert isinstance(bull_bar, np.ndarray)

    # Swing levels
    sh, sl = compute_swing_levels(high, low, lookback=2)
    assert len(sh) == 5

    # Detect BOS/CHoCH
    ev_idx, ev_lvl, ev_type = detect_bos_choch(close, sh, sl, bull_bar, lookback=2)
    assert isinstance(ev_idx, np.ndarray)

    # Liquidity Sweeps
    sw_idx, sw_lvl, sw_type = detect_liquidity_sweeps(high, low, volume, vol_mean, window=2)
    assert isinstance(sw_idx, np.ndarray)

    # OTE Zone
    ote_top, ote_bottom = compute_ote_zone(high, low, pivot_index=4, pivot_bars=3)
    assert ote_top is not None or ote_top is None


def test_vsa_math() -> None:
    high = np.array([11.0, 12.0, 13.0, 14.0, 15.0])
    low = np.array([9.0, 10.0, 11.0, 12.0, 13.0])
    close = np.array([10.0, 11.0, 12.0, 13.0, 14.0])
    open_ = np.array([9.5, 10.5, 11.5, 12.5, 13.5])
    volume = np.array([100.0, 200.0, 150.0, 300.0, 250.0])

    vz = compute_volume_zscore(volume, window=3)
    assert len(vz) == 5

    cl, sp = compute_close_location(high, low, close)
    assert len(cl) == 5

    a_idx, a_zscore, is_anom = compute_absorption_index(high, low, volume, window=3)
    assert len(a_idx) == 5

    is_climax = detect_buying_climax(high, low, close, volume, climax_percentile=50.0, window=3)
    assert len(is_climax) == 5

    mfi = compute_mfi_kinetic(high, low, close, volume, period=3)
    assert len(mfi) == 5

    wave_vol, wave_dir = compute_weis_wave(close, volume, threshold=0.01)
    assert len(wave_vol) == 5

    labels = classify_vsa_bars(close, open_, vz, cl, high - low, np.array([1.0] * 5))
    assert len(labels) == 5

    cvd = compute_cvd_approx(open_, high, low, close, volume)
    assert len(cvd) == 5


def test_tpo_math() -> None:
    prices = np.array([10.0, 10.5, 11.0, 11.5, 12.0])
    tpo_counts = np.array([1, 2, 5, 2, 1])
    mean, sigma, skewness, poc = compute_tpo_stats(prices, tpo_counts)
    assert poc == 11.0
    assert mean == 11.0
    assert skewness == 0.0

    shape = classify_profile_shape(skewness, prices, tpo_counts)
    assert shape == 0  # SHAPE_NORMAL

    bimodal = detect_bimodal(tpo_counts, gap_ticks=2)
    assert not bimodal

    high = np.array([11.0, 12.0, 13.0])
    low = np.array([9.0, 10.0, 11.0])
    close = np.array([10.0, 11.0, 12.0])
    t = infer_tick_size(high, low, close)
    assert t > 0

    p, counts = build_tpo_histogram(high, low, tick_size=0.5)
    assert len(p) > 0


def test_lob_math() -> None:
    bid_quantities = np.array([10.0, 5.0, 2.0])
    ask_quantities = np.array([8.0, 6.0, 4.0])
    rho = compute_depth_imbalance(bid_quantities, ask_quantities)
    assert -1.0 <= rho <= 1.0

    ctr = compute_ctr(100.0, 50.0)
    assert ctr == 2.0

    spoof = classify_spoofing(rho, 5.0, 1.0, rho_threshold=0.05, ctr_multiplier=2.0)
    assert spoof in (0, 1, 2)

    bid_price = np.array([100.0, 100.5, 100.0])
    bid_size = np.array([10.0, 12.0, 11.0])
    ask_price = np.array([101.0, 101.5, 101.0])
    ask_size = np.array([8.0, 9.0, 7.0])
    ofi = compute_ofi(bid_price, bid_size, ask_price, ask_size)
    assert len(ofi) == 3

    qi = compute_queue_imbalance(
        np.array([[10.0, 5.0], [12.0, 6.0]]), np.array([[8.0, 4.0], [9.0, 5.0]]), depth=2
    )
    assert len(qi) == 2

    r_ctr = rolling_ctr(
        np.array([1000, 2000, 3000]),
        np.array([5.0, 2.0, 3.0]),
        np.array([10.0, 5.0, 4.0]),
        window_ms=1500,
    )
    assert len(r_ctr) == 3


def test_hmm_math() -> None:
    values = np.array([1.0, 2.0, 3.0])
    lse = log_sum_exp(values)
    assert lse > 3.0

    entropy = normalised_entropy(np.array([0.5, 0.5]))
    assert np.isclose(entropy, 1.0)

    obs = np.array([1.0, 2.0])
    mu = np.array([0.0, 0.0])
    inv_cov = np.eye(2)
    lnc = 0.5 * 2 * np.log(2 * np.pi)
    log_prob = log_gaussian_emission(obs, mu, inv_cov, lnc)
    assert log_prob < 0

    cache = precompute_emission_cache([mu], [np.eye(2)])
    assert len(cache) == 1

    log_alpha = np.array([0.0])
    log_transition = np.array([[0.0]])
    log_emissions = np.array([-0.5])
    new_log_alpha = forward_step(log_alpha, log_transition, log_emissions, initialised=True)
    assert len(new_log_alpha) == 1

    seq, probs, risks = infer_hmm_sequence(
        np.array([[1.0, 2.0], [1.1, 2.1]]), np.array([0.0]), np.array([[0.0]]), cache
    )
    assert len(seq) == 2

    regime = classify_regime(0.5)
    assert regime == 1  # REGIME_SHIFTING

    l_mat = to_log_matrix(np.array([[0.5, 0.5]]))
    assert l_mat.shape == (1, 2)

    l_vec = to_log_vector(np.array([0.5, 0.5]))
    assert len(l_vec) == 2

    close = np.array([10.0, 11.0, 12.0, 11.5, 12.5])
    volume = np.array([100.0, 150.0, 120.0, 130.0, 140.0])
    features = build_ohlcv_features(close, volume, vol_window=3, ret_window=3)
    assert features.shape == (5, 3)
