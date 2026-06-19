"""Unit tests for Motor ⑬ — Bayesian Kelly sizer."""

from __future__ import annotations

from pathlib import Path

import duckdb

from backend.config.profit_calibration import ProfitCalibrationPolicy
from backend.services.calibration.bayesian_kelly_sizer import (
    bayesian_kelly_scalar,
    compute_bayesian_kelly_fraction,
)


def _make_journal(path: Path, trades: list[tuple[str, float]]) -> None:
    """Create a minimal DuckDB trade_journal with (route, realized_pnl) rows."""
    conn = duckdb.connect(str(path))
    try:
        conn.execute(
            "CREATE TABLE trade_journal ("
            "execution_timestamp VARCHAR, symbol VARCHAR, route VARCHAR, realized_pnl DOUBLE)"
        )
        for i, (route, pnl) in enumerate(trades):
            conn.execute(
                "INSERT INTO trade_journal VALUES (?, ?, ?, ?)",
                [f"2026-06-18T00:{i // 60:02d}:{i % 60:02d}", "AAPL-USDT", route, pnl],
            )
    finally:
        conn.close()


def _profit_policy(
    path: Path, *, kelly_enabled: bool = True, mode: str = "profit"
) -> ProfitCalibrationPolicy:
    return ProfitCalibrationPolicy(
        session_mode=mode,  # type: ignore[arg-type]
        kelly_enabled=kelly_enabled,
        journal_db_path=str(path),
    )


# ── compute_bayesian_kelly_fraction ──────────────────────────────────────────


def test_compute_fraction_positive_edge_is_positive() -> None:
    # ARRANGE — 9 wins of +10, 6 losses of -5 (n=15 ≥ min_sample).
    pnls = [10.0] * 9 + [-5.0] * 6
    # ACT
    fraction = compute_bayesian_kelly_fraction(pnls, min_sample=12)
    # ASSERT
    assert 0.0 < fraction <= 1.0


def test_compute_fraction_only_wins_is_zero() -> None:
    pnls = [10.0] * 12
    assert compute_bayesian_kelly_fraction(pnls, min_sample=12) == 0.0


def test_compute_fraction_only_losses_is_zero() -> None:
    pnls = [-5.0] * 12
    assert compute_bayesian_kelly_fraction(pnls, min_sample=12) == 0.0


def test_compute_fraction_below_min_sample_is_zero() -> None:
    pnls = [10.0, -5.0, 10.0]
    assert compute_bayesian_kelly_fraction(pnls, min_sample=12) == 0.0


def test_half_kelly_halves_fraction() -> None:
    pnls = [10.0] * 9 + [-5.0] * 6
    full = compute_bayesian_kelly_fraction(pnls, half_kelly=False, min_sample=12)
    half = compute_bayesian_kelly_fraction(pnls, half_kelly=True, min_sample=12)
    assert half == full * 0.5


# ── bayesian_kelly_scalar (journal-driven) ───────────────────────────────────


def test_scalar_below_min_sample_is_neutral(tmp_path: Path) -> None:
    path = tmp_path / "journal.duckdb"
    _make_journal(path, [("BINGX", 10.0), ("BINGX", -5.0), ("BINGX", 10.0)])
    scalar = bayesian_kelly_scalar(route="BINGX", policy=_profit_policy(path))
    assert scalar == 1.0


def test_scalar_with_positive_edge_journal_in_range(tmp_path: Path) -> None:
    path = tmp_path / "journal.duckdb"
    trades = [("BINGX", 10.0)] * 9 + [("BINGX", -5.0)] * 6
    _make_journal(path, trades)
    scalar = bayesian_kelly_scalar(route="BINGX", policy=_profit_policy(path))
    assert 0.0 < scalar <= 1.0


def test_scalar_kelly_disabled_is_neutral(tmp_path: Path) -> None:
    path = tmp_path / "journal.duckdb"
    trades = [("BINGX", 10.0)] * 9 + [("BINGX", -5.0)] * 6
    _make_journal(path, trades)
    scalar = bayesian_kelly_scalar(route="BINGX", policy=_profit_policy(path, kelly_enabled=False))
    assert scalar == 1.0


def test_scalar_verification_mode_is_neutral(tmp_path: Path) -> None:
    path = tmp_path / "journal.duckdb"
    trades = [("BINGX", 10.0)] * 9 + [("BINGX", -5.0)] * 6
    _make_journal(path, trades)
    scalar = bayesian_kelly_scalar(route="BINGX", policy=_profit_policy(path, mode="verification"))
    assert scalar == 1.0


def test_scalar_missing_journal_is_neutral(tmp_path: Path) -> None:
    path = tmp_path / "does_not_exist.duckdb"
    scalar = bayesian_kelly_scalar(route="BINGX", policy=_profit_policy(path))
    assert scalar == 1.0
