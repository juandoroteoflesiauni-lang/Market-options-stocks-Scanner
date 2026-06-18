"""Tests política salidas BingX apalancada. # [PD-6][TH]"""

from __future__ import annotations

from backend.config.bingx_exit_bucket_policy import (
    EARLY_SL_LEVERAGED_PCT,
    BingXConfluenceCacheEntry,
    adapt_thresholds_for_leverage,
    confluence_broken_for_tp,
    confluence_is_healthy,
    confluence_is_weakened,
    leveraged_pnl_pct,
    resolve_exit_bucket,
)


def _entry(
    *,
    score: float | None = 0.65,
    signal: str | None = "BUY",
    gamma_flip: float | None = 55.0,
) -> BingXConfluenceCacheEntry:
    return BingXConfluenceCacheEntry(
        symbol="INTC-USDT",
        underlying="INTC",
        confluence_score=score,
        confluence_signal=signal,
        gamma_flip=gamma_flip,
        speed_instability=False,
        tail_risk_severity="LOW",
        updated_at_iso="2026-06-18T12:00:00Z",
    )


def test_leveraged_pnl_matches_ui_five_x() -> None:
    pnl = leveraged_pnl_pct(
        side="LONG",
        entry_price=131.88,
        mark_price=133.28,
        leverage=5.0,
    )
    assert pnl is not None
    assert 5.0 <= pnl <= 5.5


def test_intc_bucket_semis() -> None:
    bucket = resolve_exit_bucket("INTC")
    assert bucket.bucket == "SEMIS"
    assert bucket.tp1_leveraged_pct == 5.0
    assert bucket.tp1_trim_ratio == 0.30


def test_healthy_confluence_long_above_gamma() -> None:
    entry = _entry()
    assert confluence_is_healthy(entry, side="LONG", spot=132.0) is True


def test_weakened_when_below_gamma_flip() -> None:
    entry = _entry()
    assert confluence_is_weakened(entry, side="LONG", spot=50.0) is True


def test_no_sl_when_healthy_but_drawdown() -> None:
    entry = _entry()
    assert confluence_is_weakened(entry, side="LONG", spot=132.0) is False


def test_early_sl_when_weakened_at_minus_three() -> None:
    entry = _entry(score=0.28, signal="WAIT")
    assert confluence_is_weakened(entry, side="LONG", spot=50.0) is True
    assert EARLY_SL_LEVERAGED_PCT == -3.0


def test_tp1_blocked_when_confluence_broken() -> None:
    entry = _entry(signal="SELL")
    assert confluence_broken_for_tp(entry, side="LONG", spot=140.0) is True


def test_tp2_requires_healthy() -> None:
    entry = _entry()
    assert confluence_is_healthy(entry, side="LONG", spot=132.0) is True


def test_high_leverage_increases_tp1_trim_slightly() -> None:
    base = resolve_exit_bucket("TSLA")
    adapted = adapt_thresholds_for_leverage(base, 10.0)
    assert adapted.tp1_trim_ratio > base.tp1_trim_ratio
