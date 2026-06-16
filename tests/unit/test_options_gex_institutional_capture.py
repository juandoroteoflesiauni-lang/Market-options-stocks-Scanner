"""Tests del capturador institucional GEX en background."""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest

from backend.services.options_gex_institutional_capture_service import (
    OptionsGexInstitutionalCaptureService,
    options_gex_capture_enabled,
)
from backend.services.options_gex_institutional_scheduler import (
    OptionsInstitutionalScheduler,
    OptionsSchedulerJob,
)


@pytest.mark.asyncio
async def test_poll_once_captures_when_job_due() -> None:
    job = OptionsSchedulerJob(
        "test_slot",
        "unit test",
        hour=10,
        minute=0,
    )
    scheduler = OptionsInstitutionalScheduler(jobs=[job], snapshot_runner=lambda _j, _l: None)
    service = OptionsGexInstitutionalCaptureService(symbols=("AAPL", "MSFT"))
    object.__setattr__(service, "_scheduler", scheduler)
    mock_snap = AsyncMock(return_value=object())
    with patch(
        "backend.api.routes.options_router.options_snapshot_service",
        mock_snap,
    ), patch.object(scheduler, "run_due", return_value=[job]):
        await service._poll_once()
    assert mock_snap.await_count == 2
    assert service.stats().last_symbols_captured == 2
    assert service.stats().last_jobs == ("test_slot",)


def test_capture_enabled_defaults_true() -> None:
    assert options_gex_capture_enabled() is True
