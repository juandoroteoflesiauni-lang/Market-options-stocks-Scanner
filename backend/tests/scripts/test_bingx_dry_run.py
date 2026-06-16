from __future__ import annotations
"""Unit tests for bingx_dry_run._summarize() and --persist flag."""


from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

from backend.scripts.bingx_dry_run import _parse_args, _summarize
from backend.services.bingx_audit_store import BingXAuditStore
from backend.services.bingx_bot_service import BingXCycleResult, BingXMarketSnapshot


def _make_snapshot() -> BingXMarketSnapshot:
    return BingXMarketSnapshot(
        symbol="BTC-USDT",
        interval="5m",
        bars=50,
        latest_close=100.0,
        last_volume=500.0,
        volume_mean=400.0,
        volume_std=50.0,
        volume_z_score=2.1,
        close_position_in_range=0.7,
        range_pct=0.3,
        captured_at="2026-05-20T00:00:00Z",
    )


def _make_result() -> BingXCycleResult:
    return BingXCycleResult(
        started_at="2026-05-20T00:00:00Z",
        finished_at="2026-05-20T00:01:00Z",
        universe=("BTC-USDT", "GOOGL-USDT", "AAPL-USDT"),
        snapshots=(_make_snapshot(),),
        signals=(),
        decisions=(),
        plans=(),
        executions=(),
        dry_run=True,
    )


_INSTRUMENTS = [
    {
        "market_type": "crypto_standard",
        "execution_allowed": True,
        "massive_available": False,
        "fmp_symbol": None,
    },
    {
        "market_type": "stock_perp",
        "execution_allowed": True,
        "massive_available": True,
        "fmp_symbol": "GOOGL",
    },
    {
        "market_type": "stock_perp",
        "execution_allowed": False,
        "massive_available": False,
        "fmp_symbol": "AAPL",
    },
]


def test_summarize_includes_instrument_type_counts() -> None:
    summary = _summarize(_make_result(), _INSTRUMENTS)

    assert summary["stock_perp_count"] == 2
    assert summary["stock_index_perp_count"] == 0
    assert summary["crypto_count"] == 1
    assert summary["execution_allowed_count"] == 2
    # l2_active: stock_perp with execution_allowed=True (GOOGL)
    assert summary["l2_active_count"] == 1
    # l2_pending: stock_perp with execution_allowed=False (AAPL)
    assert summary["l2_pending_count"] == 1


def test_summarize_providers_are_bool_and_never_expose_values() -> None:
    with patch.dict(
        "os.environ",
        {"BINGX_API_KEY": "secret123", "FMP_API_KEY": "fmpsecret"},
        clear=False,
    ):
        summary = _summarize(_make_result(), _INSTRUMENTS)

    providers = summary["providers"]
    assert providers["bingx_api_key"] is True
    assert providers["fmp_api_key"] is True
    # The raw key value must never appear in the summary.
    assert "secret123" not in str(summary)
    assert "fmpsecret" not in str(summary)
    for v in providers.values():
        assert isinstance(v, bool)


def test_summarize_with_empty_instruments() -> None:
    summary = _summarize(_make_result(), [])
    assert summary["stock_perp_count"] == 0
    assert summary["stock_index_perp_count"] == 0
    assert summary["crypto_count"] == 0
    assert summary["l2_active_count"] == 0
    assert summary["l2_pending_count"] == 0
    assert summary["execution_allowed_count"] == 0
    assert "providers" in summary


# ── --persist flag ─────────────────────────────────────────────────────────────


def test_parse_args_persist_default_none() -> None:
    args = _parse_args([])
    assert args.persist is None


def test_parse_args_persist_accepts_path() -> None:
    args = _parse_args(["--persist", "/tmp/audit.duckdb"])
    assert args.persist == "/tmp/audit.duckdb"


def test_persist_writes_cycle_to_duckdb(tmp_path: Path) -> None:
    """--persist creates a DuckDB file and stores the cycle audit record."""
    from backend.scripts.bingx_dry_run import _run

    db_path = str(tmp_path / "audit.duckdb")
    args = _parse_args(["--persist", db_path])

    # Build a realistic mock cycle result
    mock_result = MagicMock()
    mock_result.started_at = "2026-05-21T00:00:00Z"
    mock_result.finished_at = "2026-05-21T00:01:00Z"
    mock_result.dry_run = True
    mock_result.trading_environment = "paper"
    mock_result.universe = ("BTC-USDT",)
    mock_result.snapshots = (_make_snapshot(),)
    mock_result.signals = ()
    mock_result.decisions = ()
    mock_result.plans = ()
    mock_result.executions = ()
    mock_result.to_dict.return_value = {
        "started_at": "2026-05-21T00:00:00Z",
        "finished_at": "2026-05-21T00:01:00Z",
        "dry_run": True,
        "universe": ["BTC-USDT"],
        "snapshots": [],
        "signals": [],
        "decisions": [],
        "plans": [],
        "executions": [],
    }

    mock_service = MagicMock()
    mock_service.run_cycle = AsyncMock(return_value=mock_result)
    mock_service.get_universe = AsyncMock(
        return_value=[{"market_type": "crypto_standard", "execution_allowed": True}]
    )

    mock_client = MagicMock()
    mock_client.aclose = AsyncMock()

    with (
        patch("backend.scripts.bingx_dry_run.BingXClient", return_value=mock_client),
        patch("backend.scripts.bingx_dry_run.BingXBotService", return_value=mock_service),
    ):
        import asyncio

        exit_code = asyncio.run(_run(args))

    assert exit_code == 0
    store = BingXAuditStore(db_path)
    assert store.count() == 1
    cycles = store.list_cycles()
    assert cycles[0]["dry_run"] is True
    assert cycles[0]["universe"] == ["BTC-USDT"]


def test_persist_skipped_when_flag_absent(tmp_path: Path) -> None:
    """Without --persist, no DuckDB file is created."""
    from backend.scripts.bingx_dry_run import _run

    args = _parse_args([])
    db_path = tmp_path / "should_not_exist.duckdb"

    mock_result = MagicMock()
    mock_result.started_at = "2026-05-21T00:00:00Z"
    mock_result.finished_at = "2026-05-21T00:01:00Z"
    mock_result.dry_run = True
    mock_result.trading_environment = "paper"
    mock_result.universe = ("BTC-USDT",)
    mock_result.snapshots = (_make_snapshot(),)
    mock_result.signals = ()
    mock_result.decisions = ()
    mock_result.plans = ()
    mock_result.executions = ()
    mock_result.to_dict.return_value = {
        "started_at": "2026-05-21T00:00:00Z",
        "finished_at": "2026-05-21T00:01:00Z",
        "dry_run": True,
        "universe": ["BTC-USDT"],
        "snapshots": [],
        "signals": [],
        "decisions": [],
        "plans": [],
        "executions": [],
    }

    mock_service = MagicMock()
    mock_service.run_cycle = AsyncMock(return_value=mock_result)
    mock_service.get_universe = AsyncMock(return_value=[])

    mock_client = MagicMock()
    mock_client.aclose = AsyncMock()

    with (
        patch("backend.scripts.bingx_dry_run.BingXClient", return_value=mock_client),
        patch("backend.scripts.bingx_dry_run.BingXBotService", return_value=mock_service),
    ):
        import asyncio

        asyncio.run(_run(args))

    assert not db_path.exists()
