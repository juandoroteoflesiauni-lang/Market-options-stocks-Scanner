from __future__ import annotations
from typing import Literal, Any
"""BingX production readiness check.

Runs in-process checks against the live configuration to determine
whether the BingX Bot is ready for a human go/no-go decision on live
trading. Never activates live mode.

Checks performed:
  1. Provider key presence (BINGX_API_KEY, FMP_API_KEY, GEMINI_API_KEY, options)
  2. Dry-run cycle (Scan → Filter → Risk → Execute with dry_run=True)
  3. Universe composition (total count, L2 active/pending)
  4. L2 probe (sample equity perps for actual depth reachability)
  5. FMP probe (equity TA snapshot reachability)
  6. Recent audit cycles (if --db-path is provided)
  7. Live safety gate (confirm live mode is not accidentally armed)

Exit codes:
    0 — all blocking checks pass; ready for human go/no-go review
    2 — one or more FAIL conditions; details in log output

Usage:
    python backend/scripts/bingx_production_check.py
    python backend/scripts/bingx_production_check.py --db-path /data/audit.duckdb
    python backend/scripts/bingx_production_check.py --symbols AAPL-USDT GOOGL-USDT
"""


import argparse
import asyncio
import inspect
import os
import sys
from dataclasses import dataclass
from pathlib import Path

if __package__ in {None, ""}:  # pragma: no cover — script execution shim
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

try:  # pragma: no cover — optional dev dependency
    from dotenv import load_dotenv

    load_dotenv()
except ImportError:  # pragma: no cover
    pass

from backend.config.logger_setup import get_logger
from backend.layer_1_data.datos.bingx_client import BingXClient
from backend.services.bingx_bot_service import DEFAULT_UNIVERSE, BingXBotService

logger = get_logger(__name__)

_OPTIONS_CREDENTIAL_ENVS: tuple[str, ...] = (
    "MASSIVE_KEY_OPTIONS_PRIMARY",
    "MASSIVE_KEY_OPTIONS_SECONDARY",
    "MASSIVE_KEY_OPTIONS",
    "FINNHUB_API_KEY",
)
_FMP_CREDENTIAL_ENVS: tuple[str, ...] = (
    "FMP_API_KEY",
    "FMP_KEY_QUOTES",
    "FMP_KEY_STATEMENTS",
    "FMP_KEY_ANALYST",
    "FMP_KEY_TECHNICAL",
    "FMP_KEY_NEWS",
    "FMP_KEY_SCREENING",
)
_L2_SAMPLE: int = 5
_L2_TIMEOUT_S: float = 3.0
_REMOTE_TIMEOUT_S: float = 5.0


# ── Result type ───────────────────────────────────────────────────────────────


@dataclass
class CheckResult:
    """Outcome of one production readiness check."""

    name: str
    status: Literal["PASS", "WARN", "FAIL"]
    detail: str


# ── Check 1: provider key presence ───────────────────────────────────────────


def check_providers(env: dict[str, str] | None = None) -> list[CheckResult]:
    """Verify provider key presence. Values are never read — only bool presence."""
    g = env.get if env is not None else os.environ.get
    results: list[CheckResult] = []

    bingx_ok = bool(g("BINGX_API_KEY"))
    fmp_ok = any(bool(g(k)) for k in _FMP_CREDENTIAL_ENVS)
    gemini_ok = bool(g("GEMINI_API_KEY"))
    options_ok = any(bool(g(k)) for k in _OPTIONS_CREDENTIAL_ENVS)

    results.append(
        CheckResult(
            "provider.bingx_api_key",
            "PASS" if bingx_ok else "FAIL",
            "present" if bingx_ok else "missing — account and position access unavailable",
        )
    )
    results.append(
        CheckResult(
            "provider.fmp_api_key",
            "PASS" if fmp_ok else "FAIL",
            "present" if fmp_ok else "missing — stock-perp TA/probabilistic engine degraded",
        )
    )
    results.append(
        CheckResult(
            "provider.gemini_api_key",
            "PASS" if gemini_ok else "WARN",
            "present" if gemini_ok else "absent — GEX/options engine degraded (non-blocking)",
        )
    )
    results.append(
        CheckResult(
            "provider.options_credentials",
            "PASS" if options_ok else "FAIL",
            "present" if options_ok else "absent — options pipeline skipped (non-blocking)",
        )
    )
    return results


# ── Check 2: dry-run cycle ────────────────────────────────────────────────────


async def check_dry_run_cycle(service: Any) -> CheckResult:
    """Run one full Scan → Filter → Risk → Execute cycle with dry_run=True."""
    try:
        result = await service.run_cycle()
    except Exception as exc:
        return CheckResult("dry_run_cycle", "FAIL", f"cycle_exception: {exc!s:.200}")
    snapshots_with_bars = sum(1 for s in result.snapshots if s.bars > 0)
    if snapshots_with_bars == 0:
        return CheckResult(
            "dry_run_cycle",
            "FAIL",
            "no_snapshots_returned — network or venue issue",
        )
    return CheckResult(
        "dry_run_cycle",
        "PASS",
        f"snapshots_with_bars={snapshots_with_bars} universe_size={len(result.universe)}",
    )


# ── Check 3: universe composition ────────────────────────────────────────────


def check_universe(instruments: list[dict[str, Any]]) -> CheckResult:
    """Evaluate universe composition and L2 policy counts."""
    equity_types = {"stock_perp", "stock_index_perp"}
    total = len(instruments)
    equity = [i for i in instruments if i.get("market_type") in equity_types]
    l2_active = sum(1 for i in equity if i.get("execution_allowed"))
    l2_pending = sum(1 for i in equity if not i.get("execution_allowed"))

    if total == 0:
        return CheckResult(
            "universe",
            "FAIL",
            "universe_empty — universe refresh may have failed",
        )
    if l2_active == 0:
        return CheckResult(
            "universe",
            "FAIL",
            f"total={total} l2_active=0 l2_pending={l2_pending} — all equity perps in pending state",
        )
    return CheckResult(
        "universe",
        "PASS",
        f"total={total} l2_active={l2_active} l2_pending={l2_pending}",
    )


# ── Check 4: L2 probe ─────────────────────────────────────────────────────────


async def check_l2_probe(
    service: Any,
    equity_symbols: list[str],
    *,
    timeout_s: float = _L2_TIMEOUT_S,
) -> CheckResult:
    """Sample L2 depth for equity perps and report active/failed counts."""
    sample = equity_symbols[:_L2_SAMPLE]
    if not sample:
        return CheckResult(
            "l2_probe",
            "FAIL",
            "no_equity_symbols — L2 probe skipped",
        )

    active = 0
    failures: list[str] = []
    for sym in sample:
        try:
            raw = service.l2_analysis_for_symbol(sym)
            if inspect.isawaitable(raw):
                analysis = await asyncio.wait_for(raw, timeout=timeout_s)
            else:
                analysis = raw
            if analysis is not None and getattr(analysis, "ok", False):
                active += 1
            else:
                reason = (
                    getattr(analysis, "error", None) if analysis is not None else "l2_not_wired"
                )
                failures.append(f"{sym}:{reason or 'l2_unavailable'}")
        except TimeoutError:
            failures.append(f"{sym}:timeout")
        except Exception as exc:
            failures.append(f"{sym}:{str(exc)[:60]}")

    if active == 0:
        return CheckResult(
            "l2_probe",
            "FAIL",
            f"no_active_l2 sample={len(sample)} failures=[{', '.join(failures)}]",
        )
    if failures:
        return CheckResult(
            "l2_probe",
            "FAIL",
            f"partial active={active}/{len(sample)} failures=[{', '.join(failures)}]",
        )
    return CheckResult("l2_probe", "PASS", f"active={active}/{len(sample)}")


# ── Check 5: FMP probe ────────────────────────────────────────────────────────


async def check_fmp_probe(
    env: dict[str, str] | None = None,
    *,
    timeout_s: float = _REMOTE_TIMEOUT_S,
) -> CheckResult:
    """Probe FMP reachability via EquityTASnapshotService('SPY')."""
    g = env.get if env is not None else os.environ.get
    if not any(g(k) for k in _FMP_CREDENTIAL_ENVS):
        return CheckResult("fmp_probe", "WARN", "FMP_API_KEY absent — probe skipped")
    try:
        from backend.services.equity_ta_snapshot_service import EquityTASnapshotService

        snapshot = await asyncio.wait_for(
            EquityTASnapshotService("SPY").snapshot(),
            timeout=timeout_s,
        )
    except TimeoutError:
        return CheckResult("fmp_probe", "FAIL", "timeout")
    except Exception as exc:
        return CheckResult("fmp_probe", "FAIL", f"probe_error: {str(exc)[:120]}")
    if snapshot.get("ok"):
        return CheckResult("fmp_probe", "PASS", "fmp_reachable")
    return CheckResult(
        "fmp_probe",
        "FAIL",
        f"probe_not_ok: {snapshot.get('reason', 'unknown')}",
    )


# ── Check 6: recent audit cycles ─────────────────────────────────────────────


def check_recent_cycles(store: Any | None) -> CheckResult:
    """Verify that recent dry-run cycles are present in the audit store."""
    if store is None:
        return CheckResult(
            "recent_cycles",
            "WARN",
            "no_audit_store — pass --db-path to enable cycle history check",
        )
    try:
        count = store.count()
    except Exception as exc:
        return CheckResult("recent_cycles", "WARN", f"audit_store_error: {exc}")
    if count == 0:
        return CheckResult(
            "recent_cycles",
            "WARN",
            "audit_store_empty — run bingx_dry_run.py --persist first",
        )
    cycles = store.list_cycles(limit=1)
    latest = cycles[0]["started_at"] if cycles else "unknown"
    return CheckResult("recent_cycles", "PASS", f"cycles_found={count} latest={latest}")


# ── Check 7: live safety gate ─────────────────────────────────────────────────


def check_live_readiness_gates(
    service: Any,
    env: dict[str, str] | None = None,
) -> CheckResult:
    """Confirm the bot is in dry-run mode and live mode is not accidentally armed.

    This check PASSES when live is safely disabled, which is the expected state
    for pre-production. A WARN indicates live is armed — verify this is intentional.
    """
    g = env.get if env is not None else os.environ.get
    enable_live_raw = (g("BINGX_BOT_ENABLE_LIVE") or "false").strip().lower()
    enable_live = enable_live_raw in {"1", "true", "yes"}
    service_dry_run = getattr(service, "dry_run", True)

    if not service_dry_run:
        return CheckResult(
            "live_readiness_gates",
            "WARN",
            "service.dry_run=False — live client active; verify this is intentional",
        )
    if enable_live:
        return CheckResult(
            "live_readiness_gates",
            "WARN",
            "BINGX_BOT_ENABLE_LIVE=true — live mode armed; verify intentional before human approval",
        )
    return CheckResult(
        "live_readiness_gates",
        "PASS",
        "dry_run=True live_not_armed — safe for pre-production operation",
    )


# ── Orchestrator ──────────────────────────────────────────────────────────────


async def _maybe_await(value: object) -> Any:
    if inspect.isawaitable(value):
        return await value
    return value


def _allowlisted_equity_symbols(equity_symbols: list[str], env: dict[str, str] | None) -> list[str]:
    g = env.get if env is not None else os.environ.get
    raw = g("BINGX_BOT_LIVE_SYMBOL_ALLOWLIST") or ""
    allowlist = {item.strip() for item in raw.split(",") if item.strip()}
    if not allowlist:
        return equity_symbols
    return [symbol for symbol in equity_symbols if symbol in allowlist]


async def run_checks(
    *,
    service: Any,
    audit_store: Any | None = None,
    env: dict[str, str] | None = None,
) -> tuple[list[CheckResult], int]:
    """Run all production readiness checks.

    Returns ``(results, exit_code)`` where exit_code is 0 when all blocking
    (FAIL) checks pass, or 2 when at least one FAIL is present.
    Never activates live trading.
    """
    results: list[CheckResult] = []

    # ── 1. Provider key presence ──────────────────────────────────────────────
    results.extend(check_providers(env))

    # ── 2. Dry-run cycle ──────────────────────────────────────────────────────
    results.append(await check_dry_run_cycle(service))

    # ── 3 + 4. Universe composition + L2 probe ────────────────────────────────
    try:
        instruments = list(await _maybe_await(service.get_universe()))
    except Exception as exc:
        instruments = []
        results.append(CheckResult("universe", "FAIL", f"universe_fetch_failed: {exc!s:.200}"))
    else:
        results.append(check_universe(instruments))
        equity_types = {"stock_perp", "stock_index_perp"}
        equity_symbols = [
            str(i["symbol"])
            for i in instruments
            if i.get("market_type") in equity_types and i.get("symbol")
        ]
        results.append(
            await check_l2_probe(service, _allowlisted_equity_symbols(equity_symbols, env))
        )

    # ── 5. FMP probe ──────────────────────────────────────────────────────────
    results.append(await check_fmp_probe(env))

    # ── 6. Recent audit cycles ────────────────────────────────────────────────
    results.append(check_recent_cycles(audit_store))

    # ── 7. Live safety gate ───────────────────────────────────────────────────
    results.append(check_live_readiness_gates(service, env))

    exit_code = 0 if all(r.status != "FAIL" for r in results) else 2
    return results, exit_code


# ── CLI ───────────────────────────────────────────────────────────────────────


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="BingX production readiness check — never activates live trading.",
    )
    parser.add_argument(
        "--symbols",
        nargs="*",
        default=None,
        help="Override universe (e.g. --symbols BTC-USDT AAPL-USDT).",
    )
    parser.add_argument(
        "--db-path",
        metavar="PATH",
        default=None,
        help="DuckDB audit store path for cycle history check.",
    )
    parser.add_argument(
        "--timeout",
        type=float,
        default=15.0,
        help="HTTP timeout for market data calls (default: 15s).",
    )
    return parser.parse_args(argv)


async def _run(args: argparse.Namespace) -> int:
    from backend.services.bingx_audit_store import BingXAuditStore

    client = BingXClient(
        api_key=os.getenv("BINGX_API_KEY"),
        secret_key=os.getenv("BINGX_SECRET"),
        dry_run=True,
        allow_env_dry_run_override=False,
        timeout_seconds=float(args.timeout),
    )
    from backend.layer_1_data.fetchers.fmp_client import FMPClient
    from backend.layer_1_data.fetchers.massive_client import MassiveClient
    from backend.services.bingx_universe import BingXUniverseService

    fmp_client = FMPClient()
    massive_client = MassiveClient()
    service = BingXBotService(
        client=client,
        universe=tuple(args.symbols) if args.symbols else DEFAULT_UNIVERSE,
        fmp_client=fmp_client,
        massive_client=massive_client,
        universe_service=BingXUniverseService(
            client=client,
            fmp_client=fmp_client,
            massive_client=massive_client,
        ),
    )
    audit_store: BingXAuditStore | None = BingXAuditStore(args.db_path) if args.db_path else None

    try:
        results, exit_code = await run_checks(service=service, audit_store=audit_store)
    finally:
        await client.aclose()

    pass_count = sum(1 for r in results if r.status == "PASS")
    warn_count = sum(1 for r in results if r.status == "WARN")
    fail_count = sum(1 for r in results if r.status == "FAIL")

    for r in results:
        if r.status == "PASS":
            logger.info("check.%s status=PASS detail=%s", r.name, r.detail)
        elif r.status == "WARN":
            logger.warning("check.%s status=WARN detail=%s", r.name, r.detail)
        else:
            logger.error("check.%s status=FAIL detail=%s", r.name, r.detail)

    logger.info(
        "bingx_production_check.summary pass=%d warn=%d fail=%d exit_code=%d",
        pass_count,
        warn_count,
        fail_count,
        exit_code,
    )
    if exit_code == 0:
        logger.info(
            "bingx_production_check.READY — all blocking checks passed; "
            "awaiting human go/no-go decision"
        )
    else:
        logger.error(
            "bingx_production_check.NOT_READY — %d blocking condition(s) found",
            fail_count,
        )
    return exit_code


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    return asyncio.run(_run(args))


if __name__ == "__main__":  # pragma: no cover — manual entry point
    raise SystemExit(main())
