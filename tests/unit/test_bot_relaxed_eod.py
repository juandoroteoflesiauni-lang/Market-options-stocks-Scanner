"""Tests EOD Alpaca flags in relaxed/verification env. # [PD-6][TH]"""

from __future__ import annotations

import os
from datetime import UTC, datetime

import pytest

from backend.config import bot_relaxed_thresholds as relaxed
from backend.config.alpaca_eod_config import (
    alpaca_eod_entry_cutoff_disabled,
    alpaca_eod_flatten_enabled,
    is_eod_entry_cutoff,
    is_eod_flatten_window,
)


def test_verification_session_enables_eod_flatten_and_entry_cutoff(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    for key in (
        "ALPACA_EOD_FLATTEN_ENABLED",
        "ALPACA_EOD_ENTRY_CUTOFF_DISABLED",
        "ALPACA_EOD_ENTRY_CUTOFF_ET",
        "ALPACA_EOD_FLATTEN_START_ET",
    ):
        monkeypatch.delenv(key, raising=False)
    relaxed.apply_verification_session_env(execute_orders=False)
    assert os.environ["ALPACA_EOD_FLATTEN_ENABLED"] == "true"
    assert os.environ["ALPACA_EOD_ENTRY_CUTOFF_DISABLED"] == "false"
    assert alpaca_eod_flatten_enabled() is True
    assert alpaca_eod_entry_cutoff_disabled() is False


def test_entry_cutoff_active_after_1530_et_weekday(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ALPACA_EOD_ENTRY_CUTOFF_DISABLED", "false")
    monkeypatch.setenv("ALPACA_EOD_ENTRY_CUTOFF_ET", "15:30")
    # Monday 2026-06-15 19:35 UTC = 15:35 ET (EDT)
    monday = datetime(2026, 6, 15, 19, 35, tzinfo=UTC)
    assert is_eod_entry_cutoff(now=monday) is True


def test_entry_cutoff_inactive_before_1530_et_weekday(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ALPACA_EOD_ENTRY_CUTOFF_DISABLED", "false")
    monkeypatch.setenv("ALPACA_EOD_ENTRY_CUTOFF_ET", "15:30")
    monday = datetime(2026, 6, 15, 18, 0, tzinfo=UTC)  # 14:00 ET
    assert is_eod_entry_cutoff(now=monday) is False


def test_flatten_window_1545_to_1600_et(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ALPACA_EOD_FLATTEN_START_ET", "15:45")
    in_window = datetime(2026, 6, 15, 19, 50, tzinfo=UTC)  # 15:50 ET
    before = datetime(2026, 6, 15, 19, 30, tzinfo=UTC)  # 15:30 ET
    assert is_eod_flatten_window(now=in_window) is True
    assert is_eod_flatten_window(now=before) is False
