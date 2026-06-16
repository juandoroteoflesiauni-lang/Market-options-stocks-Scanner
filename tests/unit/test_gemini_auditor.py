"""Tests for Gemini auditor security and missing-key handling."""

from __future__ import annotations

import os

import pandas as pd
import pytest

from backend.ml_engine import gemini_auditor


def test_analyze_trading_performance_without_api_key(monkeypatch: pytest.MonkeyPatch) -> None:
    """Must not call Gemini when GEMINI_API_KEY is unset."""
    monkeypatch.setattr(gemini_auditor, "_resolve_gemini_api_key", lambda: None)

    result = gemini_auditor.analyze_trading_performance(
        pd.DataFrame({"pnl_pct": [1.0], "target_win": [1]})
    )

    assert "GEMINI_API_KEY" in result
    assert "AIza" not in result


def test_resolve_gemini_api_key_from_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """Reads key from environment when present."""
    monkeypatch.setenv("GEMINI_API_KEY", "test-gemini-key-from-env")
    monkeypatch.setattr(
        "backend.ml_engine.gemini_auditor.load_settings",
        None,
        raising=False,
    )

    original = gemini_auditor._resolve_gemini_api_key

    def _env_only() -> str | None:
        return os.getenv("GEMINI_API_KEY", "").strip() or None

    monkeypatch.setattr(gemini_auditor, "_resolve_gemini_api_key", _env_only)
    assert gemini_auditor._resolve_gemini_api_key() == "test-gemini-key-from-env"
    monkeypatch.setattr(gemini_auditor, "_resolve_gemini_api_key", original)
