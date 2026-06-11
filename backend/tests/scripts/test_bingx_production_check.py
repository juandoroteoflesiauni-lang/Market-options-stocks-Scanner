"""Tests for bingx_production_check individual checks and run_checks orchestrator."""

from __future__ import annotations

import asyncio
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from backend.scripts.bingx_production_check import (
    CheckResult,
    _parse_args,
    check_dry_run_cycle,
    check_fmp_probe,
    check_l2_probe,
    check_live_readiness_gates,
    check_providers,
    check_recent_cycles,
    check_universe,
    run_checks,
)

# ── Helpers ────────────────────────────────────────────────────────────────────


def _make_instrument(
    market_type: str = "stock_perp",
    execution_allowed: bool = True,
    symbol: str = "AAPL-USDT",
) -> dict[str, Any]:
    return {
        "symbol": symbol,
        "market_type": market_type,
        "execution_allowed": execution_allowed,
    }


def _make_snapshot(bars: int = 50) -> MagicMock:
    snap = MagicMock()
    snap.bars = bars
    return snap


def _make_cycle_result(snapshots_with_bars: int = 1) -> MagicMock:
    result = MagicMock()
    result.snapshots = [_make_snapshot(50 if i < snapshots_with_bars else 0) for i in range(3)]
    result.universe = ("BTC-USDT", "AAPL-USDT")
    result.signals = []
    result.decisions = []
    result.plans = []
    result.executions = []
    return result


def _mock_service(
    *,
    dry_run: bool = True,
    run_cycle_result: MagicMock | None = None,
    universe: list[dict[str, Any]] | None = None,
    l2_ok: bool = True,
) -> MagicMock:
    svc = MagicMock()
    svc.dry_run = dry_run
    svc.run_cycle = AsyncMock(return_value=run_cycle_result or _make_cycle_result())
    svc.get_universe = AsyncMock(return_value=universe or [_make_instrument()])

    l2_analysis = MagicMock()
    l2_analysis.ok = l2_ok
    l2_analysis.error = None
    svc.l2_analysis_for_symbol = AsyncMock(return_value=l2_analysis)
    return svc


# ── check_providers ────────────────────────────────────────────────────────────


def test_check_providers_all_present() -> None:
    env = {
        "BINGX_API_KEY": "x",
        "FMP_API_KEY": "y",
        "GEMINI_API_KEY": "z",
        "MASSIVE_KEY_OPTIONS_PRIMARY": "w",
    }
    results = check_providers(env)
    assert all(r.status == "PASS" for r in results)


def test_check_providers_bingx_missing_is_fail() -> None:
    env = {"FMP_API_KEY": "y", "GEMINI_API_KEY": "z"}
    results = {r.name: r for r in check_providers(env)}
    assert results["provider.bingx_api_key"].status == "FAIL"
    assert results["provider.fmp_api_key"].status == "PASS"


def test_check_providers_fmp_missing_is_fail() -> None:
    env = {"BINGX_API_KEY": "x", "GEMINI_API_KEY": "z"}
    results = {r.name: r for r in check_providers(env)}
    assert results["provider.fmp_api_key"].status == "FAIL"


def test_check_providers_accepts_repo_fmp_key_alias() -> None:
    env = {"BINGX_API_KEY": "x", "FMP_KEY_QUOTES": "y", "GEMINI_API_KEY": "z"}
    results = {r.name: r for r in check_providers(env)}
    assert results["provider.fmp_api_key"].status == "PASS"


def test_check_providers_gemini_missing_is_warn_not_fail() -> None:
    env = {"BINGX_API_KEY": "x", "FMP_API_KEY": "y"}
    results = {r.name: r for r in check_providers(env)}
    assert results["provider.gemini_api_key"].status == "WARN"
    # Missing Gemini alone does not cause a FAIL
    assert results["provider.bingx_api_key"].status == "PASS"
    assert results["provider.fmp_api_key"].status == "PASS"


def test_check_providers_options_missing_is_fail() -> None:
    env = {"BINGX_API_KEY": "x", "FMP_API_KEY": "y", "GEMINI_API_KEY": "z"}
    results = {r.name: r for r in check_providers(env)}
    assert results["provider.options_credentials"].status == "FAIL"


def test_check_providers_never_expose_key_values() -> None:
    secret = "supersecretkey12345"
    env = {"BINGX_API_KEY": secret, "FMP_API_KEY": "another_secret"}
    results = check_providers(env)
    combined = " ".join(r.detail for r in results)
    assert secret not in combined
    assert "another_secret" not in combined


# ── check_dry_run_cycle ────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_dry_run_cycle_pass_when_snapshots_returned() -> None:
    svc = _mock_service(run_cycle_result=_make_cycle_result(snapshots_with_bars=2))
    result = await check_dry_run_cycle(svc)
    assert result.status == "PASS"
    assert "snapshots_with_bars=2" in result.detail


@pytest.mark.asyncio
async def test_dry_run_cycle_fail_when_no_snapshots() -> None:
    svc = _mock_service(run_cycle_result=_make_cycle_result(snapshots_with_bars=0))
    result = await check_dry_run_cycle(svc)
    assert result.status == "FAIL"
    assert "no_snapshots" in result.detail


@pytest.mark.asyncio
async def test_dry_run_cycle_fail_on_exception() -> None:
    svc = MagicMock()
    svc.run_cycle = AsyncMock(side_effect=RuntimeError("upstream_error"))
    result = await check_dry_run_cycle(svc)
    assert result.status == "FAIL"
    assert "cycle_exception" in result.detail
    assert "upstream_error" in result.detail


# ── check_universe ────────────────────────────────────────────────────────────


def test_check_universe_pass_when_all_l2_active() -> None:
    instruments = [
        _make_instrument("stock_perp", True, "AAPL-USDT"),
        _make_instrument("crypto_standard", True, "BTC-USDT"),
    ]
    result = check_universe(instruments)
    assert result.status == "PASS"
    assert "l2_active=1" in result.detail


def test_check_universe_fail_when_empty() -> None:
    result = check_universe([])
    assert result.status == "FAIL"
    assert "universe_empty" in result.detail


def test_check_universe_fail_when_all_equity_pending() -> None:
    instruments = [
        _make_instrument("stock_perp", False, "AAPL-USDT"),
        _make_instrument("stock_perp", False, "GOOGL-USDT"),
    ]
    result = check_universe(instruments)
    assert result.status == "FAIL"
    assert "l2_active=0" in result.detail


def test_check_universe_pass_with_mixed_pending() -> None:
    instruments = [
        _make_instrument("stock_perp", True, "AAPL-USDT"),
        _make_instrument("stock_perp", False, "GOOGL-USDT"),
    ]
    result = check_universe(instruments)
    assert result.status == "PASS"
    assert "l2_active=1" in result.detail
    assert "l2_pending=1" in result.detail


# ── check_l2_probe ────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_l2_probe_pass_all_active() -> None:
    svc = _mock_service(l2_ok=True)
    result = await check_l2_probe(svc, ["AAPL-USDT", "GOOGL-USDT"])
    assert result.status == "PASS"
    assert "active=2/2" in result.detail


@pytest.mark.asyncio
async def test_l2_probe_fail_when_none_active() -> None:
    svc = _mock_service(l2_ok=False)
    result = await check_l2_probe(svc, ["AAPL-USDT"])
    assert result.status == "FAIL"
    assert "no_active_l2" in result.detail


@pytest.mark.asyncio
async def test_l2_probe_fail_when_partial_active() -> None:
    call_count = [0]

    async def _l2(sym: str) -> MagicMock:
        call_count[0] += 1
        m = MagicMock()
        m.ok = call_count[0] == 1  # first call succeeds, rest fail
        m.error = None
        return m

    svc = MagicMock()
    svc.l2_analysis_for_symbol = _l2
    result = await check_l2_probe(svc, ["AAPL-USDT", "GOOGL-USDT"])
    assert result.status == "FAIL"
    assert "partial" in result.detail


@pytest.mark.asyncio
async def test_l2_probe_fail_when_no_equity_symbols() -> None:
    svc = _mock_service()
    result = await check_l2_probe(svc, [])
    assert result.status == "FAIL"
    assert "skipped" in result.detail


@pytest.mark.asyncio
async def test_l2_probe_handles_timeout() -> None:
    async def _slow(_sym: str) -> None:
        await asyncio.sleep(999)

    svc = MagicMock()
    svc.l2_analysis_for_symbol = _slow
    result = await check_l2_probe(svc, ["AAPL-USDT"], timeout_s=0.01)
    assert result.status == "FAIL"
    assert "timeout" in result.detail


@pytest.mark.asyncio
async def test_l2_probe_handles_exception() -> None:
    svc = MagicMock()
    svc.l2_analysis_for_symbol = AsyncMock(side_effect=RuntimeError("l2_error"))
    result = await check_l2_probe(svc, ["AAPL-USDT"])
    assert result.status == "FAIL"
    assert "l2_error" in result.detail


# ── check_fmp_probe ───────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_fmp_probe_warn_when_no_key() -> None:
    result = await check_fmp_probe(env={})
    assert result.status == "WARN"
    assert "skipped" in result.detail


_FMP_SVC_PATH = "backend.services.equity_ta_snapshot_service.EquityTASnapshotService"


@pytest.mark.asyncio
async def test_fmp_probe_pass_when_ok() -> None:
    mock_snapshot = {"ok": True}
    with patch(_FMP_SVC_PATH) as MockSvc:
        instance = MagicMock()
        instance.snapshot = AsyncMock(return_value=mock_snapshot)
        MockSvc.return_value = instance
        result = await check_fmp_probe(env={"FMP_API_KEY": "key"})
    assert result.status == "PASS"
    assert "fmp_reachable" in result.detail


@pytest.mark.asyncio
async def test_fmp_probe_fail_on_timeout() -> None:
    async def _slow() -> dict[str, Any]:
        await asyncio.sleep(999)
        return {}

    with patch(_FMP_SVC_PATH) as MockSvc:
        instance = MagicMock()
        instance.snapshot = _slow
        MockSvc.return_value = instance
        result = await check_fmp_probe(env={"FMP_API_KEY": "key"}, timeout_s=0.01)
    assert result.status == "FAIL"
    assert "timeout" in result.detail


@pytest.mark.asyncio
async def test_fmp_probe_fail_when_snapshot_not_ok() -> None:
    mock_snapshot = {"ok": False, "reason": "fmp_unauthorized"}
    with patch(_FMP_SVC_PATH) as MockSvc:
        instance = MagicMock()
        instance.snapshot = AsyncMock(return_value=mock_snapshot)
        MockSvc.return_value = instance
        result = await check_fmp_probe(env={"FMP_API_KEY": "key"})
    assert result.status == "FAIL"
    assert "fmp_unauthorized" in result.detail


# ── check_recent_cycles ───────────────────────────────────────────────────────


def test_recent_cycles_warn_when_no_store() -> None:
    result = check_recent_cycles(None)
    assert result.status == "WARN"
    assert "--db-path" in result.detail


def test_recent_cycles_warn_when_store_empty() -> None:
    store = MagicMock()
    store.count.return_value = 0
    result = check_recent_cycles(store)
    assert result.status == "WARN"
    assert "empty" in result.detail


def test_recent_cycles_pass_when_cycles_found() -> None:
    store = MagicMock()
    store.count.return_value = 5
    store.list_cycles.return_value = [{"started_at": "2026-05-21T10:00:00Z"}]
    result = check_recent_cycles(store)
    assert result.status == "PASS"
    assert "cycles_found=5" in result.detail


def test_recent_cycles_warn_on_store_error() -> None:
    store = MagicMock()
    store.count.side_effect = RuntimeError("db_locked")
    result = check_recent_cycles(store)
    assert result.status == "WARN"
    assert "audit_store_error" in result.detail


# ── check_live_readiness_gates ────────────────────────────────────────────────


def test_live_gates_pass_when_dry_run_and_not_armed() -> None:
    svc = MagicMock()
    svc.dry_run = True
    result = check_live_readiness_gates(svc, env={})
    assert result.status == "PASS"
    assert "dry_run=True" in result.detail


def test_live_gates_warn_when_service_not_dry_run() -> None:
    svc = MagicMock()
    svc.dry_run = False
    result = check_live_readiness_gates(svc, env={})
    assert result.status == "WARN"
    assert "live client active" in result.detail


def test_live_gates_warn_when_enable_live_true() -> None:
    svc = MagicMock()
    svc.dry_run = True
    result = check_live_readiness_gates(svc, env={"BINGX_BOT_ENABLE_LIVE": "true"})
    assert result.status == "WARN"
    assert "live mode armed" in result.detail


@pytest.mark.parametrize("value", ["1", "true", "yes", "TRUE", "YES"])
def test_live_gates_warn_for_all_truthy_enable_live_values(value: str) -> None:
    svc = MagicMock()
    svc.dry_run = True
    result = check_live_readiness_gates(svc, env={"BINGX_BOT_ENABLE_LIVE": value})
    assert result.status == "WARN"


# ── run_checks orchestrator ───────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_run_checks_exit_0_when_all_pass() -> None:
    svc = _mock_service()
    env = {
        "BINGX_API_KEY": "k",
        "FMP_API_KEY": "k",
        "GEMINI_API_KEY": "k",
        "MASSIVE_KEY_OPTIONS": "k",
    }
    with patch(
        "backend.scripts.bingx_production_check.check_fmp_probe",
        AsyncMock(return_value=CheckResult("fmp_probe", "PASS", "ok")),
    ):
        results, exit_code = await run_checks(service=svc, env=env)
    assert exit_code == 0
    fail_results = [r for r in results if r.status == "FAIL"]
    assert fail_results == []


@pytest.mark.asyncio
async def test_run_checks_exit_2_when_dry_run_cycle_fails() -> None:
    svc = _mock_service(run_cycle_result=_make_cycle_result(snapshots_with_bars=0))
    env = {"BINGX_API_KEY": "k", "FMP_API_KEY": "k", "GEMINI_API_KEY": "k"}
    with patch(
        "backend.scripts.bingx_production_check.check_fmp_probe",
        AsyncMock(return_value=CheckResult("fmp_probe", "PASS", "ok")),
    ):
        results, exit_code = await run_checks(service=svc, env=env)
    assert exit_code == 2
    fail_names = {r.name for r in results if r.status == "FAIL"}
    assert "dry_run_cycle" in fail_names


@pytest.mark.asyncio
async def test_run_checks_exit_2_when_bingx_key_missing() -> None:
    svc = _mock_service()
    env = {"FMP_API_KEY": "k"}
    with patch(
        "backend.scripts.bingx_production_check.check_fmp_probe",
        AsyncMock(return_value=CheckResult("fmp_probe", "PASS", "ok")),
    ):
        results, exit_code = await run_checks(service=svc, env=env)
    assert exit_code == 2
    fail_names = {r.name for r in results if r.status == "FAIL"}
    assert "provider.bingx_api_key" in fail_names


@pytest.mark.asyncio
async def test_run_checks_exit_0_when_only_warns() -> None:
    """WARN results must not trigger exit code 2."""
    svc = _mock_service()
    env = {"BINGX_API_KEY": "k", "FMP_API_KEY": "k", "MASSIVE_KEY_OPTIONS": "k"}
    with patch(
        "backend.scripts.bingx_production_check.check_fmp_probe",
        AsyncMock(return_value=CheckResult("fmp_probe", "PASS", "ok")),
    ):
        results, exit_code = await run_checks(service=svc, env=env)
    assert exit_code == 0
    assert any(r.status == "WARN" for r in results)


@pytest.mark.asyncio
async def test_run_checks_includes_all_check_names() -> None:
    svc = _mock_service()
    env = {"BINGX_API_KEY": "k", "FMP_API_KEY": "k", "MASSIVE_KEY_OPTIONS": "k"}
    with patch(
        "backend.scripts.bingx_production_check.check_fmp_probe",
        AsyncMock(return_value=CheckResult("fmp_probe", "WARN", "skipped")),
    ):
        results, _ = await run_checks(service=svc, env=env)
    names = {r.name for r in results}
    assert "dry_run_cycle" in names
    assert "universe" in names
    assert "l2_probe" in names
    assert "fmp_probe" in names
    assert "recent_cycles" in names
    assert "live_readiness_gates" in names


@pytest.mark.asyncio
async def test_run_checks_universe_fail_when_service_raises() -> None:
    svc = MagicMock()
    svc.dry_run = True
    svc.run_cycle = AsyncMock(return_value=_make_cycle_result())
    svc.get_universe = AsyncMock(side_effect=RuntimeError("universe_down"))
    env = {"BINGX_API_KEY": "k", "FMP_API_KEY": "k"}
    with patch(
        "backend.scripts.bingx_production_check.check_fmp_probe",
        AsyncMock(return_value=CheckResult("fmp_probe", "WARN", "skipped")),
    ):
        results, exit_code = await run_checks(service=svc, env=env)
    assert exit_code == 2
    fail_names = {r.name for r in results if r.status == "FAIL"}
    assert "universe" in fail_names


# ── _parse_args ───────────────────────────────────────────────────────────────


def test_parse_args_defaults() -> None:
    args = _parse_args([])
    assert args.symbols is None
    assert args.db_path is None
    assert args.timeout == 15.0


def test_parse_args_symbols() -> None:
    args = _parse_args(["--symbols", "BTC-USDT", "AAPL-USDT"])
    assert args.symbols == ["BTC-USDT", "AAPL-USDT"]


def test_parse_args_db_path() -> None:
    args = _parse_args(["--db-path", "/tmp/audit.duckdb"])
    assert args.db_path == "/tmp/audit.duckdb"


def test_parse_args_timeout() -> None:
    args = _parse_args(["--timeout", "30"])
    assert args.timeout == 30.0
