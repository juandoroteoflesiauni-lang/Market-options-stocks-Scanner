"""Tests for shared options snapshot resolver. # [PD-6][TH]"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest

from backend.services.shared_options_snapshot_resolver import (
    shared_options_snapshot_service,
    snapshot_from_route1_sqlite,
)


def test_snapshot_from_route1_sqlite_miss() -> None:
    with patch(
        "backend.services.shared_options_snapshot_resolver.load_route1_options_context",
        return_value=None,
    ):
        assert snapshot_from_route1_sqlite("ZZZZ") is None


def test_snapshot_from_route1_sqlite_hit() -> None:
    ctx = SimpleNamespace(
        available=True,
        as_of="2099-01-01T12:00:00+00:00",
        snapshot={"ok": True, "spot": 100.0, "chain": []},
    )
    with patch(
        "backend.services.shared_options_snapshot_resolver.load_route1_options_context",
        return_value=ctx,
    ):
        snap = snapshot_from_route1_sqlite("AAPL")
    assert snap is not None
    assert snap.ok is True
    assert snap.spot == 100.0


@pytest.mark.asyncio
async def test_shared_options_snapshot_service_falls_back_live() -> None:
    live = SimpleNamespace(ok=True, spot=50.0)
    with patch(
        "backend.services.shared_options_snapshot_resolver.snapshot_from_route1_sqlite",
        return_value=None,
    ), patch(
        "backend.api.routes.options_router.options_snapshot_service",
        new_callable=AsyncMock,
        return_value=live,
    ):
        result = await shared_options_snapshot_service("AAPL", None, 0.04)
    assert result is live
