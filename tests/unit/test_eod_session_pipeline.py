"""Tests EOD session pipeline helpers. # [PD-6]"""

from __future__ import annotations

from backend.services.eod_session_pipeline import eod_calibration_enabled, eod_meta_learner_enabled


def test_eod_flags_default_enabled() -> None:
    assert eod_calibration_enabled() is True
    assert eod_meta_learner_enabled() is True
