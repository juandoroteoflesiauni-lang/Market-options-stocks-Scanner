from __future__ import annotations
from typing import Any
"""BingX Bot API — micro-account orchestration endpoints.

Endpoints are intentionally minimal: ``/status`` exposes configuration,
``/scan`` runs a Scan -> Filter pass without sending orders, and ``/trade``
runs the full Scan -> Filter -> Risk -> Execute pipeline. Execution defers to
``BingXBotService`` which itself respects ``BingXClient.dry_run``. Live trading
must be enabled explicitly per request (``allow_live=true``) and is rejected
unless the underlying client was constructed with ``dry_run=False``.
"""


import asyncio
import inspect
import json
import math as _math
import os
from collections.abc import AsyncIterator
from datetime import UTC, datetime
from time import monotonic

import numpy as _np
import pandas as _pd

try:
    import pandas_ta as _ta
except ImportError:
    _ta = None  # type: ignore[assignment]

from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

from backend.config.logger_setup import get_logger
from backend.config.settings import load_settings
from backend.domain.market_scanner_models import ScannerCustomization
from backend.services.bingx_exchange_derivatives_bridge import build_exchange_derivatives_bridge
from backend.services.bingx_market_data_router import build_market_data_route
from backend.services.bingx_options_bridge import (
    BingXOptionsMetrics,
    build_options_bridge,
    resolve_options_symbol,
)
from backend.services.bingx_symbol_linker import classify_underlying, underlying_from_bingx_symbol
from backend.services.bingx_technical_bridge import build_venue_technical
from backend.services.equity_ta_snapshot_service import (
    EquityTASnapshotService,
    equity_probabilistic_summary,
)

router = APIRouter(prefix="/api/v1/bingx-bot", tags=["bingx-bot"])
logger = get_logger(__name__)
_BINGX_BOT_ALLOWED_MARKET_TYPES = {"stock_perp", "stock_index_perp"}

# Module-level service is created lazily. Live mode requires replacing this
# instance from app startup with a credentialed client.
_service: Any | None = None


def _error_detail(exc: BaseException) -> str:
    """Return a stable non-empty error detail for API responses."""
    message = str(exc).strip()
    if message:
        return message
    return exc.__class__.__name__


def _service_trading_environment(service: Any) -> str:
    return str(getattr(service, "trading_environment", "paper") or "paper")


def _service_is_vst(service: Any) -> bool:
    return _service_trading_environment(service) == "prod-vst"


def get_service() -> Any:
    """Dependency hook so tests/integrations can swap the service instance.

    ``venue_technical_fn`` is wired to
    ``build_technical_terminal_payload_from_candles`` so the bot can consume
    the full 16-engine Technical Consensus that is already cached in the
    ``GeneratedCandleStore`` from the live WebSocket feed, instead of
    degrading to ``status=unavailable`` on every analysis cycle.
    """
    global _service
    if _service is None:
        from backend.services.bingx_bot_service import BingXBotService
        from backend.services.technical_terminal_payload import (
            build_technical_terminal_payload_from_candles,
        )

        _service = BingXBotService(
            venue_technical_fn=build_technical_terminal_payload_from_candles,
        )
    return _service


def configure_service(service: Any) -> None:
    """Replace the module-level service (used at app startup or in tests)."""
    global _service
    _service = service


# ── Healthcheck result cache (for live-mode gate) ─────────────────────────────
# Stores the monotonic timestamp, ok-flag and summary of the last deep probe.
# Only probe=true results are cached; the cheap fast mode does not update this.
_hc_cache: dict[str, Any] = {
    "ok": False,
    "cached_at": 0.0,
    "failures": [],
    "l2_active_count": 0,
    "l2_failed_count": 0,
    "l2_sample_size": 0,
    "options_status": "unknown",
    "fmp_status": "unknown",
}


def _hc_cache_fresh() -> bool:
    """True when a successful healthcheck result is within the configured TTL."""
    try:
        ttl = load_settings().bingx_bot_live_healthcheck_ttl_s
    except Exception:
        ttl = 300
    age = monotonic() - _hc_cache["cached_at"]
    return _hc_cache["ok"] and age <= ttl


def _hc_cache_update(ok: bool, summary: dict[str, Any] | None = None) -> None:
    _hc_cache["ok"] = ok
    _hc_cache["cached_at"] = monotonic()
    if summary:
        _hc_cache.update(summary)


# ── Audit store (cycle-level persistence) ─────────────────────────────────────
# Defaults to in-memory; api_server.py can replace with a file-backed store
# by calling configure_audit_store() at startup.
_audit_store: Any | None = None


def configure_audit_store(store: Any) -> None:
    """Replace the module-level audit store (used at app startup or in tests)."""
    global _audit_store
    _audit_store = store


def get_audit_store() -> Any:
    global _audit_store
    if _audit_store is None:
        from backend.services.bingx_audit_store import BingXAuditStore

        _audit_store = BingXAuditStore(":memory:")
    return _audit_store


# ── Scheduler (optional — configured at app startup) ──────────────────────────
_scheduler: Any | None = None


def configure_scheduler(scheduler: Any) -> None:
    """Attach a scheduler instance (called at app startup or in tests)."""
    global _scheduler
    _scheduler = scheduler


def get_scheduler() -> Any | None:
    return _scheduler


def _risk_desk_ready(service: Any) -> bool:
    desk = getattr(service, "risk_desk", None)
    state = getattr(desk, "state", None)
    engaged = getattr(state, "kill_switch_engaged", None)
    return engaged is False


def _has_canonical_analysis_builder(service: Any) -> bool:
    return hasattr(type(service), "build_analysis_snapshot") or (
        "build_analysis_snapshot" in getattr(service, "__dict__", {})
    )


class BingXScanRequest(BaseModel):
    symbols: list[str] | None = Field(
        default=None,
        description="Optional override of the default universe.",
    )
    scanner_confirmation: bool = Field(
        default=False,
        description=(
            "When true, run expensive MarketScanner/Funding/Options confirmation. "
            "The cockpit scan defaults to the lightweight BingX-only VSA pass; "
            "trade execution always uses strict confirmation."
        ),
    )
    customization: ScannerCustomization | None = Field(
        default=None,
        description="Optional scanner customization (weight matrix, modules, timeframe).",
    )


class BingXTradeRequest(BaseModel):
    symbols: list[str] | None = None
    allow_live: bool = Field(
        default=False,
        description=(
            "Safety toggle. When False (default), execution is always intercepted, "
            "even if the underlying client is configured for live trading."
        ),
    )
    customization: ScannerCustomization | None = Field(
        default=None,
        description="Optional scanner customization forwarded to the full trade cycle.",
    )


class BingXLeverageRequest(BaseModel):
    symbol: str = Field(..., min_length=1)
    leverage: int = Field(..., ge=1, le=125)
    side: str = "BOTH"


class BingXMarginTypeRequest(BaseModel):
    symbol: str = Field(..., min_length=1)
    margin_type: str = Field(..., min_length=1)


class BingXKillSwitchRequest(BaseModel):
    confirm: bool = Field(...)
    cancel_orders: bool = True
    reason: str = Field(default="operator", min_length=1)


class BingXCancelAllRequest(BaseModel):
    symbol: str | None = None


@router.get("/status")
async def get_status() -> dict[str, Any]:
    """Return current bot configuration, universe, and active reason codes."""
    return get_service().status()


@router.get("/cycles")
async def list_cycles(
    limit: int = Query(50, ge=1, le=500, description="Maximum cycles to return (newest first)."),
) -> dict[str, Any]:
    """Return the most recent audit cycles, newest first (no full payloads)."""
    cycles = get_audit_store().list_cycles(limit=limit)
    return {"cycles": cycles, "count": len(cycles)}


@router.get("/operations")
async def list_operations(
    limit: int = Query(
        100,
        ge=1,
        le=500,
        description="Maximum audit operation rows to return (newest first).",
    ),
) -> dict[str, Any]:
    """Return flattened operation ledger rows for paper/live learning."""
    operations = get_audit_store().list_operations(limit=limit)
    return {"operations": operations, "count": len(operations)}


@router.get("/cycles/{cycle_id}")
async def get_cycle(cycle_id: str) -> dict[str, Any]:
    """Return the full audit payload for a single cycle."""
    payload = get_audit_store().get_cycle(cycle_id)
    if payload is None:
        raise HTTPException(status_code=404, detail=f"cycle not found: {cycle_id}")
    return payload


# ── Scheduler endpoints ────────────────────────────────────────────────────────


@router.get("/scheduler/status")
async def get_scheduler_status() -> dict[str, Any]:
    """Return the current scheduler state and cycle metrics.

    Returns ``{"state": "not_configured"}`` when no scheduler has been
    attached (e.g. plain API server without the paper daemon).
    """
    sched = get_scheduler()
    if sched is None:
        return {"state": "not_configured"}
    return sched.status()


@router.post("/scheduler/start")
async def post_scheduler_start() -> dict[str, Any]:
    """Start the paper-trading scheduler loop.

    409 when no scheduler is configured; idempotent when already running.
    """
    sched = get_scheduler()
    if sched is None:
        raise HTTPException(
            status_code=409,
            detail="scheduler_not_configured: call configure_scheduler() at startup",
        )
    await sched.start()
    return sched.status()


@router.post("/scheduler/stop")
async def post_scheduler_stop() -> dict[str, Any]:
    """Stop the scheduler loop gracefully.

    409 when no scheduler is configured; idempotent when already stopped.
    """
    sched = get_scheduler()
    if sched is None:
        raise HTTPException(
            status_code=409,
            detail="scheduler_not_configured: call configure_scheduler() at startup",
        )
    await sched.stop()
    return sched.status()


# ── Healthcheck probe configuration ───────────────────────────────────────────
# These constants are intentionally narrow: the healthcheck is a preflight,
# not a live data path. Bumping the upper bounds would let probe=true become
# a self-DoS vector against upstream providers.
_PROBE_L2_SAMPLE_DEFAULT = 5
_PROBE_L2_SAMPLE_MIN = 1
_PROBE_L2_SAMPLE_MAX = 20
_PROBE_L2_TIMEOUT_DEFAULT_S = 3.0
_PROBE_L2_TIMEOUT_MIN_S = 0.5
_PROBE_L2_TIMEOUT_MAX_S = 15.0
_PROBE_REMOTE_TIMEOUT_DEFAULT_S = 5.0
_PROBE_REMOTE_TIMEOUT_MIN_S = 1.0
_PROBE_REMOTE_TIMEOUT_MAX_S = 20.0
_PROBE_FMP_TICKER = "SPY"
_PROBE_OPTIONS_TICKER = "GOOGL"
_FMP_CREDENTIAL_ENV_VARS = (
    "FMP_API_KEY",
    "FMP_KEY_QUOTES",
    "FMP_KEY_STATEMENTS",
    "FMP_KEY_ANALYST",
    "FMP_KEY_TECHNICAL",
    "FMP_KEY_NEWS",
    "FMP_KEY_SCREENING",
)
_OPTIONS_CREDENTIAL_ENV_VARS = (
    "MASSIVE_KEY_OPTIONS_PRIMARY",
    "MASSIVE_KEY_OPTIONS_SECONDARY",
    "MASSIVE_KEY_OPTIONS",
    "FINNHUB_API_KEY",
)


def _probe_env_int(name: str, default: int, *, min_val: int, max_val: int) -> int:
    raw = os.getenv(name)
    if raw is None or raw.strip() == "":
        return default
    try:
        value = int(raw)
    except (TypeError, ValueError):
        return default
    return max(min_val, min(max_val, value))


def _probe_env_float(name: str, default: float, *, min_val: float, max_val: float) -> float:
    raw = os.getenv(name)
    if raw is None or raw.strip() == "":
        return default
    try:
        value = float(raw)
    except (TypeError, ValueError):
        return default
    return max(min_val, min(max_val, value))


def _has_options_credentials() -> bool:
    return any(os.getenv(name) for name in _OPTIONS_CREDENTIAL_ENV_VARS)


def _has_fmp_credentials() -> bool:
    return any(os.getenv(name) for name in _FMP_CREDENTIAL_ENV_VARS)


def _live_allowlist_symbols() -> set[str]:
    try:
        return set(load_settings().get_bingx_live_allowlist())
    except Exception:
        return set()


def _equity_symbol_pool(instruments: list[dict[str, Any]]) -> list[str]:
    """Equity-perp symbols, prioritized by live allowlist and major stocks."""
    equity_types = {"stock_perp", "stock_index_perp"}
    pool: list[str] = []
    for inst in instruments:
        if inst.get("market_type") not in equity_types:
            continue
        symbol = inst.get("symbol")
        if isinstance(symbol, str) and symbol:
            pool.append(symbol)
    allowlist = _live_allowlist_symbols()
    priority_roots = (
        "GOOGL",
        "AAPL",
        "MSFT",
        "NVDA",
        "META",
        "AMZN",
        "SPX",
        "NDX",
        "QQQ",
        "DIA",
    )
    ranked: list[str] = []
    for symbol in pool:
        if symbol in allowlist and symbol not in ranked:
            ranked.append(symbol)
    for root in priority_roots:
        for symbol in pool:
            if symbol.upper().startswith(root) and symbol not in ranked:
                ranked.append(symbol)
    for symbol in pool:
        if symbol not in ranked:
            ranked.append(symbol)
    return ranked


def _l2_symbol_pool(instruments: list[dict[str, Any]]) -> list[str]:
    """BingX synthetic-stock symbols whose market type supports venue L2 depth."""
    return _equity_symbol_pool(instruments)


def _required_equity_probe_symbols(equity_symbols: list[str]) -> list[str]:
    allowlist = _live_allowlist_symbols()
    if not allowlist:
        return []
    return [symbol for symbol in equity_symbols if symbol in allowlist]


def _required_l2_probe_symbols(l2_symbols: list[str]) -> list[str]:
    allowlist = _live_allowlist_symbols()
    if not allowlist:
        return []
    return [symbol for symbol in l2_symbols if symbol in allowlist]


def _probe_sample_symbols(
    l2_symbols: list[str],
    *,
    required_symbols: list[str] | None = None,
) -> list[str]:
    if required_symbols:
        return list(required_symbols)
    sample_size = _probe_env_int(
        "BINGX_HEALTHCHECK_L2_SAMPLE",
        _PROBE_L2_SAMPLE_DEFAULT,
        min_val=_PROBE_L2_SAMPLE_MIN,
        max_val=_PROBE_L2_SAMPLE_MAX,
    )
    return l2_symbols[:sample_size]


async def _probe_l2_for_symbol(
    service: Any, symbol: str, timeout_s: float
) -> tuple[str, str | None]:
    """Probe a single symbol's L2 wiring. Returns ``(symbol, reason_or_None)``.

    ``reason=None`` means ``ok=True`` from the L2 pipeline; any non-None reason
    is a stable, truncated string suitable for surfacing in the response.
    """
    try:
        raw = service.l2_analysis_for_symbol(symbol)
        if not inspect.isawaitable(raw):
            return symbol, "l2_not_wired"
        analysis = await asyncio.wait_for(raw, timeout=timeout_s)
    except TimeoutError:
        return symbol, "timeout"
    except Exception as exc:
        return symbol, _error_detail(exc)[:120]
    if analysis is None:
        return symbol, "l2_not_wired"
    if getattr(analysis, "ok", False):
        return symbol, None
    return symbol, (str(getattr(analysis, "error", None) or "l2_unavailable"))[:120]


async def _probe_l2(sample: list[str]) -> dict[str, Any]:
    per_symbol_timeout = _probe_env_float(
        "BINGX_HEALTHCHECK_L2_TIMEOUT_S",
        _PROBE_L2_TIMEOUT_DEFAULT_S,
        min_val=_PROBE_L2_TIMEOUT_MIN_S,
        max_val=_PROBE_L2_TIMEOUT_MAX_S,
    )
    if not sample:
        return {
            "sample_size": 0,
            "symbols_sampled": [],
            "active_count": 0,
            "failed_count": 0,
            "failures": [],
        }
    service = get_service()
    outcomes = await asyncio.gather(
        *(_probe_l2_for_symbol(service, sym, per_symbol_timeout) for sym in sample)
    )
    failures = [{"symbol": sym, "reason": reason} for sym, reason in outcomes if reason is not None]
    active_count = sum(1 for _, reason in outcomes if reason is None)
    return {
        "sample_size": len(sample),
        "symbols_sampled": list(sample),
        "active_count": active_count,
        "failed_count": len(failures),
        "failures": failures,
    }


def _remote_probe_timeout() -> float:
    return _probe_env_float(
        "BINGX_HEALTHCHECK_PROBE_TIMEOUT_S",
        _PROBE_REMOTE_TIMEOUT_DEFAULT_S,
        min_val=_PROBE_REMOTE_TIMEOUT_MIN_S,
        max_val=_PROBE_REMOTE_TIMEOUT_MAX_S,
    )


async def _probe_fmp() -> dict[str, Any]:
    """Simple FMP reachability probe via the equity TA snapshot service.

    Skipped when ``FMP_API_KEY`` is absent (presence-only check — the key
    itself never appears in the response).
    """
    if not _has_fmp_credentials():
        return {
            "status": "skipped",
            "ticker": _PROBE_FMP_TICKER,
            "reason": "no_api_key",
            "latency_ms": None,
        }
    timeout_s = _remote_probe_timeout()
    started = monotonic()
    try:
        snapshot = await asyncio.wait_for(
            EquityTASnapshotService(_PROBE_FMP_TICKER).snapshot(),
            timeout=timeout_s,
        )
    except TimeoutError:
        return {
            "status": "failed",
            "ticker": _PROBE_FMP_TICKER,
            "reason": "timeout",
            "latency_ms": int((monotonic() - started) * 1000),
        }
    except Exception as exc:
        return {
            "status": "failed",
            "ticker": _PROBE_FMP_TICKER,
            "reason": _error_detail(exc)[:120],
            "latency_ms": int((monotonic() - started) * 1000),
        }
    elapsed_ms = int((monotonic() - started) * 1000)
    if snapshot.get("ok"):
        return {
            "status": "ok",
            "ticker": _PROBE_FMP_TICKER,
            "reason": None,
            "latency_ms": elapsed_ms,
        }
    return {
        "status": "failed",
        "ticker": _PROBE_FMP_TICKER,
        "reason": str(snapshot.get("reason", "snapshot_not_ok"))[:120],
        "latency_ms": elapsed_ms,
    }


async def _probe_options(equity_symbols: list[str]) -> dict[str, Any]:
    """Options pipeline probe via ``options_snapshot_service``.

    Skipped when no Massive/Finnhub-class credential is present (presence-only
    check; secrets are never read back into the response).
    """
    option_tickers = _options_probe_tickers(equity_symbols)
    primary_ticker = option_tickers[0] if option_tickers else _PROBE_OPTIONS_TICKER
    if not _has_options_credentials():
        return {
            "status": "skipped",
            "ticker": primary_ticker,
            "tickers": option_tickers,
            "reason": "no_api_key",
            "latency_ms": None,
            "checked_count": 0,
            "failed_count": 0,
            "failures": [],
        }
    if not option_tickers:
        return {
            "status": "skipped",
            "ticker": primary_ticker,
            "tickers": [],
            "reason": "no_equity_symbols",
            "latency_ms": None,
            "checked_count": 0,
            "failed_count": 0,
            "failures": [],
        }
    timeout_s = _remote_probe_timeout()
    started = monotonic()
    try:
        from backend.api.routes.options_router import options_snapshot_service

        outcomes = await asyncio.gather(
            *(
                asyncio.wait_for(
                    options_snapshot_service(ticker, None, 0.04),
                    timeout=timeout_s,
                )
                for ticker in option_tickers
            ),
            return_exceptions=True,
        )
    except Exception as exc:
        return {
            "status": "failed",
            "ticker": primary_ticker,
            "tickers": option_tickers,
            "reason": _error_detail(exc)[:120],
            "latency_ms": int((monotonic() - started) * 1000),
            "checked_count": len(option_tickers),
            "failed_count": len(option_tickers),
            "failures": [
                {"ticker": ticker, "reason": _error_detail(exc)[:120]} for ticker in option_tickers
            ],
        }
    elapsed_ms = int((monotonic() - started) * 1000)
    failures: list[dict[str, str]] = []
    for ticker, outcome in zip(option_tickers, outcomes, strict=True):
        if isinstance(outcome, TimeoutError):
            failures.append({"ticker": ticker, "reason": "timeout"})
            continue
        if isinstance(outcome, BaseException):
            failures.append({"ticker": ticker, "reason": _error_detail(outcome)[:120]})
            continue
        if not bool(getattr(outcome, "ok", False)):
            failures.append({"ticker": ticker, "reason": "snapshot_not_ok"})
    ok = not failures
    return {
        "status": "ok" if ok else "failed",
        "ticker": primary_ticker,
        "tickers": option_tickers,
        "reason": None if ok else failures[0]["reason"],
        "latency_ms": elapsed_ms,
        "checked_count": len(option_tickers),
        "failed_count": len(failures),
        "failures": failures,
    }


def _options_probe_tickers(equity_symbols: list[str]) -> list[str]:
    tickers: list[str] = []
    for symbol in equity_symbols:
        market_type = classify_underlying(symbol)
        options_symbol, _proxy, reason = resolve_options_symbol(symbol, market_type)
        if reason is not None or not options_symbol:
            continue
        if options_symbol not in tickers:
            tickers.append(options_symbol)
    return tickers


@router.get("/healthcheck")
async def get_healthcheck(
    probe: bool = Query(
        False,
        description=(
            "When true, run live probes (L2 sampling, FMP, options) with "
            "per-probe timeouts. Default false keeps the endpoint config-only."
        ),
    ),
) -> dict[str, Any]:
    """Universe composition + provider availability with optional live probes.

    ``probe=false`` (default): config-only counts, no upstream calls.
    ``probe=true``: runs sampled L2 + FMP + options probes. Each probe is
    timeout-bounded and never raises; credentials are reported as presence
    booleans only — values are never returned.
    """
    try:
        instruments = await _maybe_await(get_service().get_universe())
    except Exception as exc:
        logger.warning("bingx_bot.healthcheck_universe_failed error=%s", exc)
        instruments = []

    equity_types = {"stock_perp", "stock_index_perp"}
    equity = [i for i in instruments if i.get("market_type") in equity_types]

    body: dict[str, Any] = {
        "service": "bingx_bot",
        "dry_run": get_service().dry_run,
        "trading_environment": _service_trading_environment(get_service()),
        "universe_count": len(instruments),
        "stock_perp_count": sum(1 for i in instruments if i.get("market_type") == "stock_perp"),
        "stock_index_perp_count": sum(
            1 for i in instruments if i.get("market_type") == "stock_index_perp"
        ),
        "crypto_count": sum(1 for i in instruments if i.get("market_type") == "crypto_standard"),
        # ``l2_active_count`` remains a *policy-derived* count (execution_allowed
        # flag from the universe). Probe-mode adds ``l2_probe_*`` keys with the
        # outcome of actual L2 fetches.
        "l2_active_count": sum(1 for i in equity if i.get("execution_allowed")),
        "l2_pending_count": sum(1 for i in equity if not i.get("execution_allowed")),
        "options_available_count": sum(1 for i in instruments if i.get("massive_available")),
        "predictive_available_count": sum(1 for i in instruments if i.get("fmp_symbol")),
        "execution_allowed_count": sum(1 for i in instruments if i.get("execution_allowed")),
        "providers": {
            "bingx_api_key": bool(os.getenv("BINGX_API_KEY")),
            "fmp_api_key": _has_fmp_credentials(),
            "gemini_api_key": bool(os.getenv("GEMINI_API_KEY")),
            "options_credentials": _has_options_credentials(),
        },
        "probe_mode": False,
    }

    if not probe:
        return body

    # ── Deep probe mode ──────────────────────────────────────────────────────
    # Run probes concurrently so the total latency is bounded by the slowest
    # remote, not by the sum. Each probe is internally timeout-bounded.
    equity_symbols = _equity_symbol_pool(instruments)
    required_equity_symbols = _required_equity_probe_symbols(equity_symbols)
    l2_symbols = _l2_symbol_pool(instruments)
    required_l2_symbols = _required_l2_probe_symbols(l2_symbols)
    probe_symbols = _probe_sample_symbols(
        l2_symbols,
        required_symbols=required_l2_symbols,
    )
    l2_result, fmp_result, options_result = await asyncio.gather(
        _probe_l2(probe_symbols),
        _probe_fmp(),
        _probe_options(required_equity_symbols or equity_symbols),
    )

    l2_ok = l2_result["sample_size"] > 0 and l2_result["active_count"] == l2_result["sample_size"]
    fmp_ok = fmp_result.get("status") == "ok"
    options_ok = options_result.get("status") == "ok"
    probe_ok = l2_ok and fmp_ok and options_ok
    _hc_cache_update(
        probe_ok,
        {
            "failures": list(l2_result["failures"]),
            "l2_active_count": l2_result["active_count"],
            "l2_failed_count": l2_result["failed_count"],
            "l2_sample_size": l2_result["sample_size"],
            "options_status": options_result.get("status"),
            "fmp_status": fmp_result.get("status"),
        },
    )

    # ── Automated reconnection on probe failure ────────────────────────────────
    # When the deep probe returns a failure, the underlying HTTP/TCP connection
    # pool may be corrupted (e.g. stale keepalive sockets, closed remote).
    # Calling aclose() drops all idle connections; the lazy _ensure_client()
    # method in BingXClient recreates a fresh pool on the very next request.
    # This is a no-op if probe_ok is True, so there is zero overhead on the
    # happy path.
    if not probe_ok:
        try:
            await get_service().aclose()
            logger.warning(
                "bingx_bot.healthcheck_probe_failed_reconnect "
                "l2_ok=%s fmp_ok=%s options_ok=%s — connection pool reset triggered",
                l2_ok,
                fmp_ok,
                options_ok,
            )
        except Exception as _reconnect_exc:
            logger.warning("bingx_bot.healthcheck_reconnect_error error=%s", _reconnect_exc)

    body.update(
        {
            "probe_mode": True,
            "probe_ok": probe_ok,
            "l2_probe_sample_size": l2_result["sample_size"],
            "l2_probe_symbols_sampled": l2_result["symbols_sampled"],
            "l2_probe_active_count": l2_result["active_count"],
            "l2_probe_failed_count": l2_result["failed_count"],
            "l2_probe_failures": l2_result["failures"],
            "fmp_probe": fmp_result,
            "options_probe": options_result,
        }
    )
    return body


async def _maybe_await(value: object) -> Any:
    if inspect.isawaitable(value):
        return await value
    return value


@router.get("/account")
async def get_account() -> dict[str, Any]:
    """Return full account state: balances, positions and open orders."""
    try:
        return await _maybe_await(get_service().get_account_state())
    except Exception as exc:
        logger.warning("bingx_bot.account_failed error=%s", exc)
        raise HTTPException(status_code=502, detail=f"bingx_account_failed: {exc}") from exc


@router.get("/positions")
async def get_positions() -> dict[str, Any]:
    """Return open positions from the aggregated account state."""
    state = await get_account()
    return {"positions": state.get("open_positions", []), "dry_run": state.get("dry_run", True)}


@router.get("/orders")
async def get_orders() -> dict[str, Any]:
    """Return open orders from the aggregated account state."""
    state = await get_account()
    return {"orders": state.get("open_orders", []), "dry_run": state.get("dry_run", True)}


@router.get("/universe")
async def get_universe() -> dict[str, Any]:
    """Return the current liquidity-filtered universe."""
    try:
        universe = await _maybe_await(get_service().get_universe())
    except Exception as exc:
        detail = _error_detail(exc)
        logger.warning("bingx_bot.universe_failed error=%s", detail)
        return {
            "universe": [],
            "degraded": True,
            "error": f"bingx_universe_failed: {detail}",
        }
    return {"universe": universe}


@router.post("/universe/refresh")
async def post_universe_refresh() -> dict[str, Any]:
    """Force a fresh BingX contract/ticker/OI scan."""
    try:
        universe = await _maybe_await(get_service().refresh_universe())
    except Exception as exc:
        logger.warning("bingx_bot.universe_refresh_failed error=%s", exc)
        raise HTTPException(
            status_code=502,
            detail=f"bingx_universe_refresh_failed: {exc}",
        ) from exc
    return {"universe": universe, "refreshed": True}


@router.post("/scan")
async def post_scan(request: BingXScanRequest) -> dict[str, Any]:
    """Run scan + filter only (no orders, no risk sizing applied)."""
    service = get_service()
    started_at = datetime.now(tz=UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
    try:
        signals = await service.scan(request.symbols, request.customization)
        decisions = await service.filter_signals(
            signals,
            use_scanner_confirmation=request.scanner_confirmation,
            customization=request.customization,
        )
    except Exception as exc:
        logger.warning("bingx_bot.scan_failed error=%s", exc)
        raise HTTPException(status_code=502, detail=f"bingx_scan_failed: {exc}") from exc
    finished_at = datetime.now(tz=UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
    snapshots = [sig.snapshot.to_dict() for sig in signals]
    return {
        "service": "bingx_bot",
        "dry_run": service.dry_run,
        "started_at": started_at,
        "finished_at": finished_at,
        "snapshots": snapshots,
        "signals": [sig.to_dict() for sig in signals],
        "decisions": [dec.to_dict() for dec in decisions],
        "scanner_confirmation": request.scanner_confirmation,
    }


def _safe_float(val: object) -> float | None:
    """Return Python float or None if NaN/Inf."""
    try:
        f = float(val)  # type: ignore[arg-type]
        return f if _math.isfinite(f) else None
    except (TypeError, ValueError):
        return None


async def _options_snapshot_fetcher(sym: str, expiry: str | None, r: float) -> Any:
    """Lazy adapter passed to ``build_options_bridge``.

    The lazy import keeps this router free of an eager dependency on the
    options router at module-load time (the legacy ``options_router`` pulls in
    pandas-heavy GEX machinery).
    """
    from backend.api.routes.options_router import options_snapshot_service

    return await options_snapshot_service(sym, expiry, r)


async def _venue_technical_fetcher(
    sym: str, candles: list[dict[str, Any]], timeframe: str
) -> dict[str, Any]:
    """Lazy adapter passed to ``build_venue_technical``.

    ``build_technical_terminal_payload_from_candles`` pulls in pandas, the
    SMC/VSA/FVG/VP engine bundle and the volume node TPO engines — all of
    which are expensive to import. Deferring keeps cold-start of routers
    that never call ``/analysis`` cheap.
    """
    from backend.services.technical_terminal_payload import (
        build_technical_terminal_payload_from_candles,
    )

    return await build_technical_terminal_payload_from_candles(sym, candles, timeframe)


def _legacy_options_dict(metrics: BingXOptionsMetrics, spot_hint: float | None) -> dict[str, Any]:
    """Project bridge metrics into the legacy 6-field shape PLUS institutional
    extensions (max pain, dealer bias, vanna/VEX/CEX, IV rank, confluence)."""
    spot = metrics.spot if metrics.spot and metrics.spot > 0 else spot_hint

    wall_candidates = [
        v
        for v in (metrics.zero_gamma, metrics.call_wall, metrics.put_wall)
        if v is not None and v > 0
    ]
    if not wall_candidates:
        gex_wall_price: float | None = None
    elif spot is None or spot <= 0:
        gex_wall_price = wall_candidates[0]
    else:
        gex_wall_price = min(wall_candidates, key=lambda v: abs(v - spot))

    # ``iv_percentile`` is reported as a percentage [0, 100]. Raw fields are
    # in [0, 1]; we expand if needed so the frontend can render directly.
    def _as_pct(raw: float | None) -> float | None:
        if raw is None:
            return None
        return round(raw * 100.0 if 0.0 <= raw <= 1.0 else raw, 4)

    iv_percentile = _as_pct(metrics.iv_percentile_cross_term) or _as_pct(metrics.iv_rank_hv_rolling)

    return {
        # ── Legacy 6 fields (backward-compat with frontend BingXOptionsMetrics) ─
        "gex_wall_price": gex_wall_price,
        "gex_wall_direction": metrics.wall_direction,
        "gex_wall_distance_pct": metrics.wall_distance_pct,
        "iv_percentile": iv_percentile,
        "put_call_ratio": metrics.pcr_oi,
        "delta_exposure_usd": metrics.total_dex,
        # ── Institutional extensions ──────────────────────────────────────────
        "spot": metrics.spot,
        "call_wall": metrics.call_wall,
        "put_wall": metrics.put_wall,
        "call_wall_moderate": metrics.call_wall_moderate,
        "put_wall_moderate": metrics.put_wall_moderate,
        "zero_gamma": metrics.zero_gamma,
        "max_pain": metrics.max_pain,
        "net_gex_total": metrics.net_gex_total,
        "call_gex_total": metrics.call_gex_total,
        "put_gex_total": metrics.put_gex_total,
        "dealer_bias": metrics.dealer_bias,
        "squeeze_probability": metrics.squeeze_probability,
        "atm_iv": metrics.atm_iv,
        "iv_rank_hv_rolling": metrics.iv_rank_hv_rolling,
        "iv_rank_cross_expiry": metrics.iv_rank_cross_expiry,
        "iv_percentile_cross_term": metrics.iv_percentile_cross_term,
        "vrp": metrics.vrp,
        "pcr_volume": metrics.pcr_volume,
        "dex_flip_level": metrics.dex_flip_level,
        "total_vanna": metrics.total_vanna,
        "total_vex": metrics.total_vex,
        "total_cex": metrics.total_cex,
        "vanna_exposure_regime": metrics.vanna_exposure_regime,
        "vex_regime": metrics.vex_regime,
        "cex_regime": metrics.cex_regime,
        "confluence_score": metrics.confluence_score,
        "confluence_signal": metrics.confluence_signal,
        "confluence_confidence": metrics.confluence_confidence,
        "chain_contracts": metrics.chain_contracts,
    }


async def _fetch_options_metrics(
    symbol: str,
    spot_hint: float | None,
    *,
    market_type: str,
) -> tuple[dict[str, Any] | None, dict[str, Any], str | None]:
    """Run ``build_options_bridge`` and project the result into the response shape.

    Returns ``(legacy_options_dict_or_None, bridge_payload, error_reason_or_None)``.

    - ``legacy_options_dict_or_None``: the existing options block frontend
      consumers expect; ``None`` when no options snapshot is available.
    - ``bridge_payload``: always-present JSON-safe dump of the bridge result
      (useful for diagnostics, proxy_symbol, chain_quality, fetched_at).
    - ``error_reason_or_None``: stable code for ``errors.options``; ``None``
      for crypto/excluded (where options absence is expected) or on success.
    """
    fetcher = (
        _options_snapshot_fetcher if market_type in {"stock_perp", "stock_index_perp"} else None
    )
    bridge = await build_options_bridge(
        symbol,
        market_type=market_type,
        options_snapshot_fn=fetcher,
    )
    bridge_payload = bridge.to_dict()
    if bridge.status == "available" and bridge.metrics is not None:
        return _legacy_options_dict(bridge.metrics, spot_hint), bridge_payload, None
    return None, bridge_payload, bridge.reason


@router.get("/telemetry")
async def get_telemetry() -> dict[str, Any]:
    """Unified real-time telemetry for the frontend dashboard.

    Reads live state directly from the active ``BingXBotService``,
    ``BingXRiskDesk`` and Scheduler instances — no stale cache lookups.

    Key field:
    - ``production_ready``: VST-aware boolean. In ``prod-vst`` mode this is
      ``true`` whenever the client is live and the risk desk is operational,
      regardless of ``ENABLE_LIVE`` / ``PAPER_TRADING`` flags which guard
      real-money execution only. This is the authoritative signal the
      dashboard should use to clear the "NO LISTO" banner.

    Other fields mirror the preflight dashboard panels:
    - ``account``: ``total_equity``, ``available_margin``, ``used_margin`` (VST mirror)
    - ``positions``: open rows with ``entry_price``, ``current_spot``,
      ``pnl_real_apalancado``, ``current_zone``, ``leverage``
    - ``gates``: per-gate status (enable_live, paper_trading, healthcheck, …)
    - ``risk_summary``: live balance (VST balance ≈ 99 999 USDT), open
      positions count, kill-switch state, realized PnL today.
    - ``scheduler``: configured / running / stopped state + cycle count.
    - ``last_probe``: most recent deep-probe healthcheck result.
    """
    from backend.services.monitoring_service import BotMonitoringService

    svc = BotMonitoringService()
    try:
        payload = await svc.get_telemetry(
            service=get_service(),
            scheduler=get_scheduler(),
            audit_store=get_audit_store(),
            hc_cache=_hc_cache,
        )
        return payload.to_dict()
    except Exception as exc:
        logger.warning("bingx_bot.telemetry_failed error=%s", exc)
        raise HTTPException(status_code=502, detail=f"telemetry_failed: {exc}") from exc


@router.get("/live-readiness")
async def get_live_readiness() -> dict[str, Any]:
    """Return a structured readiness report for live trading.

    All conditions are checked without running orders. A response with
    ``ready=true`` means all configured gates would pass a ``/trade`` request
    for any allowlisted symbol at this moment.

    Fields:
    - ``ready``: True only when every enabled gate is green.
    - ``enable_live``: BINGX_BOT_ENABLE_LIVE setting value.
    - ``client_live``: actual dry_run flag of the active client.
    - ``allowlist``: symbols allowed in live mode (empty blocks unless allow_all_live=true).
    - ``healthcheck_gate``: required / fresh / age_s.
    - ``paper_trading``: BINGX_BOT_PAPER_TRADING setting value.
    - ``gates``: per-gate status dict.
    """
    service = get_service()
    try:
        cfg = load_settings()
        enable_live = cfg.bingx_bot_enable_live
        allowlist = sorted(cfg.get_bingx_live_allowlist())
        allow_all_live = cfg.bingx_bot_allow_all_live
        require_hc = cfg.bingx_bot_live_require_healthcheck
        hc_ttl = cfg.bingx_bot_live_healthcheck_ttl_s
        paper = cfg.bingx_bot_paper_trading
    except Exception:
        enable_live = False
        allowlist = []
        allow_all_live = False
        require_hc = True
        hc_ttl = 300
        paper = True

    trading_env = _service_trading_environment(service)
    client_live = not service.dry_run
    demo_vst = trading_env == "prod-vst"
    hc_age_s = monotonic() - _hc_cache["cached_at"]
    hc_fresh = _hc_cache["ok"] and hc_age_s <= hc_ttl
    hc_ok_value = _hc_cache["ok"]

    gate_enable = demo_vst or enable_live
    gate_client = client_live
    gate_hc = (not require_hc) or hc_fresh
    gate_allowlist = bool(allowlist) or allow_all_live
    gate_paper = demo_vst or not paper
    gate_audit = bool(getattr(get_audit_store(), "is_persistent", False))
    gate_scheduler = get_scheduler() is not None
    gate_provider_probe = bool(_hc_cache["ok"])
    gate_l2 = (
        int(_hc_cache.get("l2_sample_size") or 0) > 0
        and int(_hc_cache.get("l2_failed_count") or 0) == 0
        and int(_hc_cache.get("l2_active_count") or 0) == int(_hc_cache.get("l2_sample_size") or 0)
    )
    gate_options = _hc_cache.get("options_status") == "ok"
    gate_risk = _risk_desk_ready(service)

    ready = all(
        (
            gate_enable,
            gate_client,
            gate_hc,
            gate_allowlist,
            gate_paper,
            gate_audit,
            gate_scheduler,
            gate_provider_probe,
            gate_l2,
            gate_options,
            gate_risk,
        )
    )

    return {
        "ready": ready,
        "enable_live": enable_live,
        "client_live": client_live,
        "trading_environment": trading_env,
        "demo_vst": demo_vst,
        "paper_trading": paper,
        "allowlist": allowlist,
        "allow_all_live": allow_all_live,
        "healthcheck_gate": {
            "required": require_hc,
            "fresh": hc_fresh,
            "last_result_ok": hc_ok_value,
            "age_s": round(hc_age_s, 1) if _hc_cache["cached_at"] > 0 else None,
            "ttl_s": hc_ttl,
        },
        "last_probe": {
            "probe_ok": bool(_hc_cache["ok"]),
            "l2_active_count": _hc_cache.get("l2_active_count"),
            "l2_failed_count": _hc_cache.get("l2_failed_count"),
            "l2_sample_size": _hc_cache.get("l2_sample_size"),
            "options_status": _hc_cache.get("options_status"),
            "fmp_status": _hc_cache.get("fmp_status"),
            "failures": list(_hc_cache.get("failures") or []),
        },
        "gates": {
            "enable_live": gate_enable,
            "client_configured_live": gate_client,
            "healthcheck": gate_hc,
            "allowlist": gate_allowlist,
            "paper_trading": gate_paper,
            "audit_store": gate_audit,
            "scheduler": gate_scheduler,
            "provider_probe": gate_provider_probe,
            "l2": gate_l2,
            "options": gate_options,
            "risk_desk": gate_risk,
        },
    }


@router.post("/trade")
async def post_trade(request: BingXTradeRequest) -> dict[str, Any]:
    """Run the full cycle. Live execution is rejected unless explicitly allowed.

    Gate order (all must pass for live orders):
    1. ``allow_live=true`` in the request body.
    2. Service client is in live mode (``BINGX_BOT_ENABLE_LIVE=true`` at startup).
    3. Every requested symbol is in ``BINGX_BOT_LIVE_SYMBOL_ALLOWLIST`` (when set).
    4. A successful ``/healthcheck?probe=true`` was recorded recently (when
       ``BINGX_BOT_LIVE_REQUIRE_HEALTHCHECK=true``).
    """
    service = get_service()
    is_vst_demo = _service_is_vst(service)

    # ── Gate 1: allow_live toggle ─────────────────────────────────────────────
    if request.allow_live and service.dry_run:
        logger.info("bingx_bot.trade live_requested_but_client_dry_run")
        raise HTTPException(
            status_code=409,
            detail=(
                "External execution requested but BingXClient is in dry_run mode. "
                "Set BINGX_BOT_TRADING_ENV=prod-vst for demo/VST or "
                "BINGX_BOT_ENABLE_LIVE=true for production live."
            ),
        )
    if not request.allow_live and not service.dry_run:
        logger.info("bingx_bot.trade safety_block client_live=True allow_live=False")
        raise HTTPException(
            status_code=409,
            detail="Refusing to send orders: allow_live=false on a live client.",
        )

    # ── Gates 2-4 only apply when allow_live=true (i.e. live client) ─────────
    if request.allow_live and not service.dry_run and not is_vst_demo:
        try:
            cfg = load_settings()
        except Exception:
            cfg = None


        paper_trading = bool(getattr(cfg, "bingx_bot_paper_trading", True)) if cfg else True
        if paper_trading:
            logger.warning("bingx_bot.trade paper_trading_enabled")
            raise HTTPException(
                status_code=409,
                detail=(
                    "PAPER_TRADING_ENABLED: live trading requires " "BINGX_BOT_PAPER_TRADING=false."
                ),
            )

        allowlist = cfg.get_bingx_live_allowlist() if cfg else frozenset()
        allow_all_live = bool(getattr(cfg, "bingx_bot_allow_all_live", False)) if cfg else False
        if not allowlist and not allow_all_live:
            logger.warning("bingx_bot.trade allowlist_empty")
            raise HTTPException(
                status_code=403,
                detail=(
                    "LIVE_ALLOWLIST_EMPTY: live trading requires "
                    "BINGX_BOT_LIVE_SYMBOL_ALLOWLIST or BINGX_BOT_ALLOW_ALL_LIVE=true."
                ),
            )
        if allowlist:
            requested = set(request.symbols or service.universe)
            blocked = requested - allowlist
            if blocked:
                blocked_sorted = sorted(blocked)
                logger.warning("bingx_bot.trade allowlist_block symbols=%s", blocked_sorted)
                raise HTTPException(
                    status_code=403,
                    detail=(
                        f"Symbols not in live allowlist: {blocked_sorted}. "
                        "Add them to BINGX_BOT_LIVE_SYMBOL_ALLOWLIST."
                    ),
                )

        require_hc = cfg.bingx_bot_live_require_healthcheck if cfg else True
        if require_hc and not _hc_cache_fresh():
            logger.warning("bingx_bot.trade healthcheck_stale")
            raise HTTPException(
                status_code=409,
                detail=(
                    "Live trading requires a recent successful healthcheck. "
                    "Run GET /api/v1/bingx-bot/healthcheck?probe=true first."
                ),
            )

    if request.allow_live and not service.dry_run and is_vst_demo:
        allowlist = frozenset()
        allow_all_live = False
        cfg = None
        try:
            cfg = load_settings()
            allowlist = cfg.get_bingx_live_allowlist()
            allow_all_live = bool(getattr(cfg, "bingx_bot_allow_all_live", False))
        except Exception:
            pass
        if allowlist:
            requested = set(request.symbols or service.universe)
            blocked = requested - allowlist
            if blocked:
                raise HTTPException(
                    status_code=403,
                    detail=f"Symbols not in demo allowlist: {sorted(blocked)}.",
                )
        elif not allow_all_live:
            logger.warning("bingx_bot.trade vst_allowlist_empty")
            raise HTTPException(
                status_code=403,
                detail=(
                    "VST_ALLOWLIST_EMPTY: demo live requires "
                    "BINGX_BOT_LIVE_SYMBOL_ALLOWLIST or BINGX_BOT_ALLOW_ALL_LIVE=true."
                ),
            )

        # Gate 2: symbol allowlist
        allowlist = cfg.get_bingx_live_allowlist() if cfg else frozenset()
        allow_all_live = bool(getattr(cfg, "bingx_bot_allow_all_live", False)) if cfg else False
        if not allowlist and not allow_all_live:
            logger.warning("bingx_bot.trade allowlist_empty")
            raise HTTPException(
                status_code=403,
                detail=(
                    "LIVE_ALLOWLIST_EMPTY: live trading requires "
                    "BINGX_BOT_LIVE_SYMBOL_ALLOWLIST or BINGX_BOT_ALLOW_ALL_LIVE=true."
                ),
            )
        if allowlist:
            requested = set(request.symbols or service.universe)
            blocked = requested - allowlist
            if blocked:
                blocked_sorted = sorted(blocked)
                logger.warning("bingx_bot.trade allowlist_block symbols=%s", blocked_sorted)
                raise HTTPException(
                    status_code=403,
                    detail=(
                        f"Symbols not in live allowlist: {blocked_sorted}. "
                        "Add them to BINGX_BOT_LIVE_SYMBOL_ALLOWLIST."
                    ),
                )

        # Gate 3: recent healthcheck
        require_hc = (
            False if is_vst_demo else (cfg.bingx_bot_live_require_healthcheck if cfg else True)
        )
        if require_hc and not _hc_cache_fresh():
            logger.warning("bingx_bot.trade healthcheck_stale")
            raise HTTPException(
                status_code=409,
                detail=(
                    "Live trading requires a recent successful healthcheck. "
                    "Run GET /api/v1/bingx-bot/healthcheck?probe=true first."
                ),
            )

    try:
        result = await service.run_cycle(request.symbols, request.customization)
    except Exception as exc:
        logger.warning("bingx_bot.trade_failed error=%s", exc)
        raise HTTPException(status_code=502, detail=f"bingx_trade_failed: {exc}") from exc

    try:
        from backend.services.bingx_audit_store import BingXAuditEntry

        entry = BingXAuditEntry.from_cycle_result(result)
        cycle_id = get_audit_store().persist(entry)
        logger.info("bingx_bot.trade_audited cycle_id=%s", cycle_id)
    except Exception as exc:
        logger.warning("bingx_bot.trade_audit_failed error=%s", exc)

    return result.to_dict()


@router.post("/leverage")
async def post_leverage(request: BingXLeverageRequest) -> dict[str, Any]:
    """Set leverage for a BingX perpetual symbol."""
    try:
        return await _maybe_await(
            get_service().set_leverage(request.symbol, request.leverage, side=request.side)
        )
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except Exception as exc:
        logger.warning("bingx_bot.leverage_failed symbol=%s error=%s", request.symbol, exc)
        raise HTTPException(status_code=502, detail=f"bingx_leverage_failed: {exc}") from exc


@router.post("/margin-type")
async def post_margin_type(request: BingXMarginTypeRequest) -> dict[str, Any]:
    """Set margin type for a BingX perpetual symbol."""
    try:
        return await _maybe_await(
            get_service().set_margin_type(request.symbol, request.margin_type)
        )
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except Exception as exc:
        logger.warning("bingx_bot.margin_type_failed symbol=%s error=%s", request.symbol, exc)
        raise HTTPException(status_code=502, detail=f"bingx_margin_type_failed: {exc}") from exc


@router.post("/kill-switch")
async def post_kill_switch(request: BingXKillSwitchRequest) -> dict[str, Any]:
    """Emergency endpoint: cancel orders and close all positions after confirmation."""
    if not request.confirm:
        raise HTTPException(status_code=422, detail="Kill switch requires confirm=true")
    try:
        service = get_service()
        risk_result = service.kill_switch(reason=request.reason)
        close_result = await _maybe_await(
            service.close_all_positions(cancel_orders=request.cancel_orders, confirm=True)
        )
        if isinstance(close_result, dict):
            return {**close_result, "risk_desk": risk_result}
        return {"close_all_positions": close_result, "risk_desk": risk_result}
    except Exception as exc:
        logger.warning("bingx_bot.kill_switch_failed error=%s", exc)
        raise HTTPException(status_code=502, detail=f"bingx_kill_switch_failed: {exc}") from exc


@router.post("/cancel-all")
async def post_cancel_all(request: BingXCancelAllRequest) -> dict[str, Any]:
    """Cancel open perpetual orders, optionally scoped by symbol."""
    try:
        return await _maybe_await(get_service().cancel_all_orders(request.symbol))
    except Exception as exc:
        logger.warning("bingx_bot.cancel_all_failed error=%s", exc)
        raise HTTPException(status_code=502, detail=f"bingx_cancel_all_failed: {exc}") from exc


@router.get("/funding-rate/{symbol}")
async def get_funding_rate(symbol: str) -> dict[str, Any]:
    """Return current funding rate and mark/index data."""
    try:
        return await _maybe_await(get_service().get_funding_rate(symbol))
    except Exception as exc:
        logger.warning("bingx_bot.funding_rate_failed symbol=%s error=%s", symbol, exc)
        raise HTTPException(status_code=502, detail=f"bingx_funding_rate_failed: {exc}") from exc


@router.get("/tick/{symbol}")
async def get_tick_snapshot(symbol: str) -> dict[str, Any]:
    """Return latest recent-trade/order-book snapshot for a symbol."""
    try:
        return await _maybe_await(get_service().latest_tick_snapshot(symbol))
    except Exception as exc:
        logger.warning("bingx_bot.tick_failed symbol=%s error=%s", symbol, exc)
        raise HTTPException(status_code=502, detail=f"bingx_tick_failed: {exc}") from exc


@router.get("/stream/ticks/{symbol}")
async def stream_ticks(symbol: str) -> StreamingResponse:
    """SSE stream of 1-second micro-bars for one symbol."""

    async def event_generator() -> AsyncIterator[str]:
        stream = await _maybe_await(get_service().stream_micro_bars(symbol))
        async for bar in stream:
            payload = bar.to_dict() if hasattr(bar, "to_dict") else bar
            yield f"data: {json.dumps(payload)}\n\n"

    return StreamingResponse(event_generator(), media_type="text/event-stream")


@router.get("/stream/account")
async def stream_account() -> StreamingResponse:
    """SSE stream of account snapshots. Uses polling when private WS is unavailable."""

    async def event_generator() -> AsyncIterator[str]:
        while True:
            state = await _maybe_await(get_service().get_account_state())
            yield f"data: {json.dumps(state)}\n\n"
            await asyncio.sleep(60)

    return StreamingResponse(event_generator(), media_type="text/event-stream")


@router.get("/analysis/{symbol}")
async def get_analysis(symbol: str, interval: str = "5m") -> dict[str, Any]:
    """Fetch OHLCV klines + compute TA metrics for the analysis drawer."""
    service = get_service()
    valid_intervals = ("1m", "3m", "5m", "15m", "30m", "1h", "2h", "4h", "6h", "12h", "1d")
    if interval not in valid_intervals:
        raise HTTPException(status_code=422, detail=f"Invalid interval: {interval}")

    try:
        requested_market_type = classify_underlying(symbol)
    except Exception as exc:
        logger.warning(
            "bingx_bot.analysis_classify_failed symbol=%s error=%s",
            symbol,
            str(exc)[:180],
        )
        requested_market_type = "excluded"
    if requested_market_type not in _BINGX_BOT_ALLOWED_MARKET_TYPES:
        raise HTTPException(status_code=400, detail="bingx_bot_synthetic_stocks_only")

    if _has_canonical_analysis_builder(service):
        try:
            return await _maybe_await(
                service.build_analysis_snapshot(symbol, interval=interval)

            )
        except Exception as exc:
            detail = _error_detail(exc)
            logger.warning(
                "bingx_bot.analysis_failed symbol=%s interval=%s error=%s",
                symbol,
                interval,
                detail,
            )
            raise HTTPException(
                status_code=502,
                detail=f"bingx_analysis_failed: {detail}",
            ) from exc

    try:
        klines = await service.fetch_klines_for_analysis(
            symbol,
            interval=interval,
            limit=2000,

        )
    except Exception as exc:
        detail = _error_detail(exc)
        logger.warning(
            "bingx_bot.analysis_failed symbol=%s interval=%s error=%s",
            symbol,
            interval,
            detail,
        )
        raise HTTPException(
            status_code=502,
            detail=f"bingx_analysis_failed: {detail}",
        ) from exc

    kline_points = [
        {
            "time": k.open_time_ms // 1000,
            "open": k.open,
            "high": k.high,
            "low": k.low,
            "close": k.close,
            "volume": k.volume,
        }
        for k in klines
    ]

    ta_metrics: dict[str, Any] = {
        "rsi_14": None,
        "ema_9": None,
        "ema_21": None,
        "ema_50": None,
        "vwap": None,
        "vwap_upper_1": None,
        "vwap_lower_1": None,
        "vsa_delta": None,
        "vsa_z_score": None,
        "trend": "neutral",
    }

    if len(klines) >= 15 and _ta is not None:
        df = _pd.DataFrame(
            {
                "open": [k.open for k in klines],
                "high": [k.high for k in klines],
                "low": [k.low for k in klines],
                "close": [k.close for k in klines],
                "volume": [k.volume for k in klines],
            }
        ).astype(float)

        rsi = df.ta.rsi(length=14)
        if rsi is not None and not rsi.empty:
            ta_metrics["rsi_14"] = _safe_float(rsi.iloc[-1])

        ema9 = df.ta.ema(length=9)
        if ema9 is not None and not ema9.empty:
            ta_metrics["ema_9"] = _safe_float(ema9.iloc[-1])

        ema21 = df.ta.ema(length=21)
        if ema21 is not None and not ema21.empty:
            ta_metrics["ema_21"] = _safe_float(ema21.iloc[-1])

        ema50 = df.ta.ema(length=50)
        if ema50 is not None and not ema50.empty:
            ta_metrics["ema_50"] = _safe_float(ema50.iloc[-1])

        vol = df["volume"].fillna(0.0)
        if float(vol.sum()) > 0:
            vwap_arr = (df["close"] * vol).cumsum() / vol.cumsum()
            vwap_val = _safe_float(vwap_arr.iloc[-1])
            ta_metrics["vwap"] = vwap_val
            dev = df["close"] - vwap_arr
            sigma = float(dev.std())
            if vwap_val is not None:
                ta_metrics["vwap_upper_1"] = _safe_float(vwap_val + sigma)
                ta_metrics["vwap_lower_1"] = _safe_float(vwap_val - sigma)

        buy_mask = df["close"] >= df["open"]
        buy_vol = _np.where(buy_mask, df["volume"].to_numpy(), 0.0)
        sell_vol = _np.where(~buy_mask, df["volume"].to_numpy(), 0.0)
        delta_series = buy_vol - sell_vol
        delta_last20 = float(delta_series[-20:].sum())
        delta_mean = float(delta_series.mean())
        delta_std = float(delta_series.std())
        ta_metrics["vsa_delta"] = round(delta_last20, 2)
        ta_metrics["vsa_z_score"] = (
            round((delta_last20 - delta_mean) / max(delta_std, 1e-9), 4) if delta_std > 0 else None
        )

        e9 = ta_metrics["ema_9"]
        e21 = ta_metrics["ema_21"]
        if e9 is not None and e21 is not None:
            if e9 > e21:
                ta_metrics["trend"] = "bullish"
            elif e9 < e21:
                ta_metrics["trend"] = "bearish"

    # ── Routing context: venue vs underlying ──────────────────────────────────
    # Computed BEFORE the options fetch so the bridge can route stock_perp,
    # stock_index_perp (→ ETF proxy) and crypto correctly.
    venue_symbol = symbol
    underlying_symbol = underlying_from_bingx_symbol(symbol)
    try:
        market_type = classify_underlying(symbol)
    except Exception as exc:
        logger.warning(
            "bingx_bot.analysis_classify_failed symbol=%s error=%s", symbol, str(exc)[:180]
        )
        market_type = "excluded"

    market_data_route = build_market_data_route(symbol).to_dict()
    is_equity_perp = market_type in {"stock_perp", "stock_index_perp"}

    options_metrics, options_bridge_payload, options_reason = await _fetch_options_metrics(
        symbol,
        _safe_float(klines[-1].close) if klines else None,
        market_type=market_type,
    )
    exchange_derivatives_result = await build_exchange_derivatives_bridge(
        symbol,
        market_type=market_type,
    )
    exchange_derivatives_payload = exchange_derivatives_result.to_dict()

    # ── Underlying TA / Probabilistic ─────────────────────────────────────────
    # For equity perps we invoke the dedicated equity snapshot/probabilistic
    # helpers. They are *honest by default*: when the underlying data source
    # is unavailable they return ``ok=False`` with a stable reason code which
    # we surface as ``errors[field] = "UNAVAILABLE: <reason>"``. The data
    # field is only populated when ``ok=True`` — never with a fabricated /
    # placeholder metric. Risk-Desk remains the final authorizer.
    errors: dict[str, str] = {}
    underlying_ta: dict[str, Any] | None = None
    probabilistic: dict[str, Any] | None = None
    lob_analysis: dict[str, Any] | None = None
    lob_quality_score: float | None = None
    lob_status: str = "unavailable"

    if is_equity_perp:
        # Fan out TA / probabilistic / L2 in parallel — each engine degrades in
        # isolation so a single slow source can't stall the drawer load.
        ta_coro = EquityTASnapshotService(underlying_symbol).snapshot()
        prob_coro = equity_probabilistic_summary(underlying_symbol)

        async def _safe_l2() -> Any:
            # The service contract returns ``LOBDynamicsAnalysis | None`` for
            # equity perps. Coerce any non-awaitable return (e.g. test mocks
            # without an async stub) into ``None`` so it degrades cleanly
            # through the same path crypto takes — the alternative is a
            # ``TypeError`` from ``asyncio.gather``.
            raw = service.l2_analysis_for_symbol(symbol)
            if inspect.isawaitable(raw):
                return await raw
            return None

        gathered = await asyncio.gather(ta_coro, prob_coro, _safe_l2(), return_exceptions=True)
        ta_raw: Any = gathered[0]
        prob_raw: Any = gathered[1]
        l2_raw: Any = gathered[2]

        if isinstance(ta_raw, BaseException):
            logger.warning(
                "bingx_bot.underlying_ta_failed symbol=%s underlying=%s error=%s",
                symbol,
                underlying_symbol,
                str(ta_raw)[:180],
            )
            ta_result: dict[str, Any] = {
                "ok": False,
                "reason": "engine_error",
                "ticker": underlying_symbol,
            }
        else:
            ta_result = ta_raw


        if ta_result.get("ok"):
            underlying_ta = ta_result
        else:
            errors["underlying_ta"] = f"UNAVAILABLE: {ta_result.get('reason', 'engine_not_wired')}"

        if isinstance(prob_raw, BaseException):
            logger.warning(
                "bingx_bot.probabilistic_failed symbol=%s underlying=%s error=%s",
                symbol,
                underlying_symbol,
                str(prob_raw)[:180],
            )
            prob_result: dict[str, Any] = {
                "ok": False,
                "reason": "engine_error",
                "ticker": underlying_symbol,
            }
        else:
            prob_result = prob_raw


        if prob_result.get("ok"):
            probabilistic = prob_result
        else:
            errors["probabilistic"] = (
                f"UNAVAILABLE: {prob_result.get('reason', 'engine_not_wired')}"
            )

        # ── L2 / LOB dynamics ────────────────────────────────────────────────
        # ``l2_analysis_for_symbol`` returns ``None`` only for non-equity
        # instruments (we already gated on ``is_equity_perp``). For equity
        # perps it returns an ``LOBDynamicsAnalysis``; ``ok=True`` populates
        # the snapshot, ``ok=False`` carries the adapter reason.
        if isinstance(l2_raw, BaseException):
            logger.warning(
                "bingx_bot.l2_failed symbol=%s error=%s",
                symbol,
                str(l2_raw)[:180],
            )
            errors["l2"] = "UNAVAILABLE: l2_fetch_failed"
        elif l2_raw is None:
            # Defensive: equity perp without a wired L2 pipeline.
            errors["l2"] = "UNAVAILABLE: l2_not_wired"
        else:
            lob_analysis = l2_raw.model_dump(mode="python")
            lob_quality_score = l2_raw.data_quality_score
            if l2_raw.ok:
                lob_status = "active"
            else:
                lob_status = "pending"
                errors["l2"] = f"UNAVAILABLE: {l2_raw.error or 'l2_unavailable'}"

    # Options: only attempt for equity perps. If the helper signaled an error,
    # surface it; for crypto roots options stay null without an error entry.
    if options_metrics is None and is_equity_perp:
        # Equity perp but no options data: surface the bridge reason
        # (e.g. ``no_index_proxy_for_underlying``, ``snapshot_fetch_failed``)
        # rather than swallow it. ``options_unavailable`` is the catch-all
        # fallback when the bridge gave back no reason at all.
        # Crypto / excluded fall through silently — options absence is expected.
        errors["options"] = f"UNAVAILABLE: {options_reason or 'options_unavailable'}"

    if (
        exchange_derivatives_result.status != "available"
        and exchange_derivatives_result.reason
        and exchange_derivatives_result.reason != "exchange_derivatives_only_for_crypto"
    ):
        errors["exchange_derivatives"] = f"UNAVAILABLE: {exchange_derivatives_result.reason}"

    # ── Venue technical bridge (SMC/VSA/FVG/VP/OF on BingX klines) ────────────
    # Sequential after the gather because the bridge needs the resolved L2
    # snapshot to inject into ``lob_dynamics`` and the klines that were
    # already fetched above. Degrades cleanly when bars are insufficient or
    # the technical fetcher raises — no exception escapes the bridge.
    venue_technical_result = await build_venue_technical(
        symbol,
        klines,
        timeframe=interval,
        l2_snapshot=lob_analysis,
        technical_fn=_venue_technical_fetcher,
    )
    venue_technical_payload = venue_technical_result.to_dict()
    if (
        venue_technical_result.status != "available"
        and venue_technical_result.reason
        and venue_technical_result.reason not in {"no_venue_bars", "insufficient_bars_for_smc"}
    ):
        # ``no_venue_bars`` / ``insufficient_bars_for_smc`` are expected for
        # symbols mid-warmup; surfacing them as errors would page operators on
        # benign states. Other reasons (fetch_failed, payload_not_ok) are
        # genuine engine degradation and must be visible.
        errors["venue_technical"] = f"UNAVAILABLE: {venue_technical_result.reason}"

    # ── Data sources we actually populated ─────────────────────────────────────
    data_sources: list[str] = []
    if kline_points:
        data_sources.append("venue_klines")
    if options_metrics is not None:
        # The bridge tags the source as ``underlying_options`` or
        # ``index_proxy_options`` — use that so dashboards can distinguish
        # SPY → SPX-USDT routing from direct underlying queries.
        bridge_source = options_bridge_payload.get("source") or "underlying_options"
        data_sources.append(bridge_source)
    if underlying_ta is not None:
        data_sources.append("underlying_equity_ta")
    if probabilistic is not None:
        data_sources.append("underlying_probabilistic")
    if lob_status == "active":
        data_sources.append("bingx_l2_snapshot_rest")
    if venue_technical_result.status == "available":
        data_sources.append(venue_technical_result.source)
    if exchange_derivatives_result.status == "available":
        data_sources.extend(exchange_derivatives_result.data_sources)

    return {
        # Routing context
        "venue_symbol": venue_symbol,
        "underlying_symbol": underlying_symbol,
        "market_type": market_type,
        "market_data_route": market_data_route,
        # Per-engine payloads
        "venue_ta": ta_metrics,
        "underlying_ta": underlying_ta,
        "options": options_metrics,
        # Full institutional bridge result — proxy_symbol, chain_quality,
        # fetched_at, quality_score, and every metric the bridge resolved.
        # Always present (even when ``options`` is null) so the UI can show
        # *why* the options block degraded.
        "options_bridge": options_bridge_payload,
        "probabilistic": probabilistic,
        "exchange_derivatives": exchange_derivatives_payload,
        # L2 / LOB dynamics (equity perps only; null for crypto)
        "lob_analysis": lob_analysis,
        "lob_quality_score": lob_quality_score,
        "lob_status": lob_status,
        # Full SMC/VSA/FVG/VP/OF venue technical block — summary + payload +
        # technical_quality_score. ``payload`` carries the complete technical
        # terminal output with the L2 snapshot injected into ``lob_dynamics``.
        "venue_technical": venue_technical_payload,
        # Provenance
        "data_sources": data_sources,
        "errors": errors,
        # ── Backward-compatible fields (existing tests / cockpit consumers) ──
        "symbol": symbol,
        "interval": interval,
        "klines": kline_points,
        "ta": ta_metrics,
    }
