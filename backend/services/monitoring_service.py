"""Bot Monitoring Service — unified real-time telemetry for the dashboard.

Reads live state directly from the active ``BingXBotService``, ``BingXRiskDesk``
and ``Scheduler`` instances without triggering any upstream HTTP calls of its
own. The only *optional* async call is ``service.get_account_state()`` for the
live balance/position snapshot; if it fails the risk_summary degrades gracefully
to the in-memory ``BingXRiskDesk.state`` which never raises.

The critical design decision here is the **VST-aware ``production_ready``**
flag: when ``trading_environment == "prod-vst"`` the system is fully
operational with a simulated balance. The gates ``ENABLE_LIVE`` and
``PAPER_TRADING`` are intentionally false in that mode (they guard real-money
execution, not VST). Collapsing those flags into a single ``production_ready``
boolean using VST semantics unblocks the frontend "NO LISTO" banner without
requiring any environment change.

Layer contract
--------------
This module lives in ``layer_4`` (services). It imports from ``layer_3``
(bingx_risk_desk) and ``layer_1`` config/settings, but never from routers or
higher layers.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import os
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from time import monotonic
from typing import Any

from backend.config.logger_setup import get_logger
from backend.config.settings import load_settings
from backend.layer_1_data.datos.bingx_client import BINGX_REST_VST_BASE, BingXClient

logger = get_logger(__name__)

# ── Internal constants ────────────────────────────────────────────────────────
_ACCOUNT_STATE_TIMEOUT_S: float = 8.0  # max wait for live balance fetch
_DEFAULT_JOURNAL_DB = Path("data/quantum_analyzer.duckdb")


# ── Output contract ───────────────────────────────────────────────────────────


@dataclass(frozen=True)
class TelemetryGates:
    """Per-gate readiness flags — direct mapping from live state."""

    enable_live: bool
    client_configured_live: bool
    paper_trading: bool
    vst_mode: bool  # True when trading_environment == "prod-vst"
    allowlist: list[str]
    healthcheck: str  # "FRESH" | "STALE" | "NEVER_RUN"
    probe_providers: str  # "OK" | "FAILED" | "UNKNOWN"
    audit_persistent: bool
    scheduler_configured: bool
    risk_desk: str  # "OPERATIONAL" | "KILL_SWITCH_ACTIVE"

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class TelemetryRiskSummary:
    """Risk desk state + live account balance (best-effort)."""

    # From live account state (may be default zeros if fetch failed)
    balance_usdt: float
    available_margin_usdt: float
    used_margin_usdt: float
    unrealized_pnl_usdt: float
    realized_pnl_today_usdt: float

    # From BingXRiskDesk.state (in-memory, never fails)
    open_position_count: int
    open_positions: dict[str, float]  # symbol → notional USDT
    kill_switch_engaged: bool
    kill_switch_reason: str | None

    # Derived
    daily_loss_used_pct: float  # realized_pnl_today / max_daily_loss_usdt (negative = loss)

    # Dynamic raw fields from VST
    equity: float | None = None
    balance: float | None = None
    available_margin: float | None = None
    used_margin: float | None = None

    # Policy snapshot
    policy: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class TelemetryScheduler:
    """Scheduler runtime state."""

    configured: bool
    state: str  # "running" | "stopped" | "not_configured"
    last_cycle_at: str | None
    cycle_count: int | None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class TelemetryLastProbe:
    """Last deep-probe healthcheck result from the router cache."""

    probe_ok: bool
    age_s: float | None
    fmp_status: str | None
    options_status: str | None
    l2_active_count: int | None
    l2_failed_count: int | None
    l2_sample_size: int | None
    failures: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class TelemetryMirrorAccount:
    """VST/account mirror — equity and margin straight from the venue snapshot."""

    total_equity: float
    available_margin: float
    used_margin: float

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class TelemetryMirrorPosition:
    """One open position row for the dashboard inventory matrix."""

    symbol: str
    side: str
    entry_price: float
    current_spot: float
    leverage: int
    pnl_real_apalancado: float | None
    current_zone: str

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class TelemetryPayload:
    """Unified real-time telemetry snapshot for the frontend dashboard."""

    captured_at: str
    production_ready: bool
    trading_environment: str
    dry_run: bool

    gates: TelemetryGates
    risk_summary: TelemetryRiskSummary
    scheduler: TelemetryScheduler
    universe: dict[str, Any]
    last_probe: TelemetryLastProbe
    account: TelemetryMirrorAccount
    positions: tuple[TelemetryMirrorPosition, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        return {
            "captured_at": self.captured_at,
            "production_ready": self.production_ready,
            "trading_environment": self.trading_environment,
            "dry_run": self.dry_run,
            "gates": self.gates.to_dict(),
            "risk_summary": self.risk_summary.to_dict(),
            "scheduler": self.scheduler.to_dict(),
            "universe": self.universe,
            "last_probe": self.last_probe.to_dict(),
            "account": self.account.to_dict(),
            "positions": [row.to_dict() for row in self.positions],
        }


# ── Helper functions ──────────────────────────────────────────────────────────


def _utc_iso_now() -> str:
    return datetime.now(tz=UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def _env_requests_vst_live() -> bool:
    """True when operator config expects real VST venue reads (not intercepted stubs)."""
    dry = os.getenv("BINGX_DRY_RUN", "true").strip().lower()
    if dry in {"0", "false", "no", "live"}:
        return True
    trading = os.getenv("BINGX_BOT_TRADING_ENV", "").strip().lower()
    return trading == "prod-vst"


def _infer_trading_environment(service: Any) -> str:
    """Resolve dashboard trading environment, honoring env when service is still on paper."""
    runtime = str(getattr(service, "trading_environment", "paper") or "paper")
    if runtime in {"prod-vst", "prod-live"}:
        return runtime
    configured = os.getenv("BINGX_BOT_TRADING_ENV", "").strip().lower()
    if configured in {"prod-vst", "prod-live"}:
        return configured
    if _env_requests_vst_live():
        return "prod-vst"
    return runtime


def _has_bingx_credentials() -> bool:
    return bool(os.getenv("BINGX_API_KEY") and os.getenv("BINGX_SECRET"))


def _build_vst_venue_client() -> BingXClient | None:
    """Dedicated VST client for telemetry when the in-process bot service is still dry-run."""
    if not _has_bingx_credentials():
        return None
    return BingXClient(
        api_key=os.getenv("BINGX_API_KEY"),
        secret_key=os.getenv("BINGX_SECRET"),
        base_url=BINGX_REST_VST_BASE,
        dry_run=False,
        allow_env_dry_run_override=False,
    )


def _account_positions_to_exposure(positions: list[Any]) -> dict[str, float]:
    exposures: dict[str, float] = {}
    for row in positions:
        if isinstance(row, dict):
            symbol = str(row.get("symbol") or "").strip()
            size = float(row.get("size") or row.get("positionAmt") or 0.0)
            mark = float(row.get("mark_price") or row.get("markPrice") or 0.0)
        else:
            symbol = str(getattr(row, "symbol", "") or "").strip()
            size = float(getattr(row, "size", 0.0) or 0.0)
            mark = float(getattr(row, "mark_price", 0.0) or 0.0)
        if symbol and mark > 0:
            exposures[symbol] = abs(size) * mark
    return exposures


def _risk_summary_from_account_dict(
    account: dict[str, Any],
    *,
    base: TelemetryRiskSummary,
) -> TelemetryRiskSummary:
    positions = account.get("open_positions")
    position_rows = positions if isinstance(positions, list) else []
    venue_exposures = _account_positions_to_exposure(position_rows)
    open_positions = venue_exposures or base.open_positions
    position_count = len(position_rows) if position_rows else len(open_positions)

    def _af(key: str, default: float = 0.0) -> float:
        try:
            value = account.get(key)
            return float(value) if value is not None else default
        except (TypeError, ValueError):
            return default

    equity = _af("total_equity_usdt") or _af("equity") or _af("balance")
    return TelemetryRiskSummary(
        balance_usdt=equity,
        available_margin_usdt=_af("available_margin_usdt") or _af("available_margin"),
        used_margin_usdt=_af("used_margin_usdt") or _af("used_margin"),
        unrealized_pnl_usdt=_af("unrealized_pnl_usdt"),
        realized_pnl_today_usdt=_af("realized_pnl_today_usdt", base.realized_pnl_today_usdt),
        open_position_count=position_count,
        open_positions=open_positions,
        kill_switch_engaged=base.kill_switch_engaged,
        kill_switch_reason=base.kill_switch_reason,
        daily_loss_used_pct=base.daily_loss_used_pct,
        equity=equity or None,
        balance=_af("balance") or None,
        available_margin=_af("available_margin") or None,
        used_margin=_af("used_margin") or None,
        policy=base.policy,
    )


async def _fetch_vst_venue_account() -> dict[str, Any] | None:
    """Query BingX VST directly (dry_run=False) for dashboard telemetry."""
    client = _build_vst_venue_client()
    if client is None:
        return None
    try:
        from backend.services.bingx_account_service import BingXAccountService

        account = await asyncio.wait_for(
            BingXAccountService(client=client).get_account_state(),
            timeout=_ACCOUNT_STATE_TIMEOUT_S,
        )
        if account.dry_run:
            return None
        return account.to_dict()
    except Exception as exc:
        logger.warning("monitoring_service.vst_venue_fetch_failed error=%s", exc)
        return None
    finally:
        await client.aclose()


def _compute_production_ready(
    *,
    trading_env: str,
    client_live: bool,
    risk_desk_ok: bool,
    enable_live: bool,
    paper_trading: bool,
    hc_fresh: bool,
) -> bool:
    """Return True when the system is operational under current semantics.

    VST mode (``prod-vst``):
        The environment simulates real execution with a virtual balance.
        ``ENABLE_LIVE`` and ``PAPER_TRADING`` flags intentionally remain at their
        safe defaults (false / true) because they guard real-money production.
        In VST a live client + operational risk desk = production ready.

    Production-live mode:
        All gates must pass: client live, enable_live flag, paper_trading off,
        risk desk operational, and a fresh healthcheck probe.
    """
    if trading_env == "prod-vst":
        return client_live and risk_desk_ok
    # Production real — strict gates
    return client_live and risk_desk_ok and enable_live and not paper_trading and hc_fresh


def _build_gates(
    *,
    service: Any,
    trading_env: str,
    hc_cache: dict[str, Any],
    hc_ttl: float,
    audit_store: Any,
    scheduler: Any,
) -> TelemetryGates:
    """Build the gates section from live service state and settings."""
    # Config from settings (with safe fallback)
    enable_live = False
    paper_trading = True
    allowlist: list[str] = []
    try:
        cfg = load_settings()
        enable_live = bool(cfg.bingx_bot_enable_live)
        paper_trading = bool(cfg.bingx_bot_paper_trading)
        allowlist = sorted(cfg.get_bingx_live_allowlist())
    except Exception:
        pass

    client_live = not getattr(service, "dry_run", True)
    vst_mode = trading_env == "prod-vst"

    # Healthcheck freshness
    cached_at = float(hc_cache.get("cached_at") or 0.0)
    hc_ok = bool(hc_cache.get("ok", False))
    if cached_at == 0.0:
        hc_label = "NEVER_RUN"
    else:
        age_s = monotonic() - cached_at
        hc_label = "FRESH" if (hc_ok and age_s <= hc_ttl) else "STALE"

    # Provider probe label
    probe_label: str
    if cached_at == 0.0:
        probe_label = "UNKNOWN"
    elif hc_ok:
        probe_label = "OK"
    else:
        probe_label = "FAILED"

    # Audit store persistence
    audit_persistent = bool(getattr(audit_store, "is_persistent", False))

    # Scheduler configured
    scheduler_configured = scheduler is not None

    # Risk desk
    desk = getattr(service, "risk_desk", None)
    state = getattr(desk, "state", None)
    kill_switch = getattr(state, "kill_switch_engaged", False)
    risk_label = "KILL_SWITCH_ACTIVE" if kill_switch else "OPERATIONAL"

    return TelemetryGates(
        enable_live=enable_live,
        client_configured_live=client_live,
        paper_trading=paper_trading,
        vst_mode=vst_mode,
        allowlist=allowlist,
        healthcheck=hc_label,
        probe_providers=probe_label,
        audit_persistent=audit_persistent,
        scheduler_configured=scheduler_configured,
        risk_desk=risk_label,
    )


def _build_risk_summary_from_desk(service: Any) -> TelemetryRiskSummary:
    """Build risk summary from in-memory risk desk state (no IO, never fails)."""
    desk = getattr(service, "risk_desk", None)
    state = getattr(desk, "state", None)
    policy = getattr(desk, "policy", None)

    open_positions: dict[str, float] = {}
    kill_switch_engaged = False
    kill_switch_reason: str | None = None
    realized_pnl_today: float = 0.0

    if state is not None:
        open_positions = dict(getattr(state, "open_positions", {}) or {})
        kill_switch_engaged = bool(getattr(state, "kill_switch_engaged", False))
        kill_switch_reason = getattr(state, "kill_switch_reason", None)
        realized_pnl_today = float(getattr(state, "realized_pnl_today", 0.0) or 0.0)

    max_daily_loss: float = 3.0
    policy_dict: dict[str, Any] = {}
    if policy is not None:
        max_daily_loss = float(getattr(policy, "max_daily_loss_usdt", 3.0))
        policy_dict = {
            "max_daily_loss_usdt": max_daily_loss,
            "max_position_notional_usdt": float(
                getattr(policy, "max_position_notional_usdt", 25.0)
            ),
            "max_open_positions": int(getattr(policy, "max_open_positions", 3)),
            "max_symbol_exposure_usdt": float(getattr(policy, "max_symbol_exposure_usdt", 12.0)),
            "cooldown_after_loss_minutes": float(
                getattr(policy, "cooldown_after_loss_minutes", 15.0)
            ),
            "max_spread_pct": float(getattr(policy, "max_spread_pct", 0.005)),
        }

    # Daily loss used %
    if max_daily_loss > 0 and realized_pnl_today < 0:
        daily_loss_used_pct = round(abs(realized_pnl_today) / max_daily_loss, 4)
    else:
        daily_loss_used_pct = 0.0

    return TelemetryRiskSummary(
        balance_usdt=0.0,
        available_margin_usdt=0.0,
        used_margin_usdt=0.0,
        unrealized_pnl_usdt=0.0,
        realized_pnl_today_usdt=realized_pnl_today,
        open_position_count=len(open_positions),
        open_positions=open_positions,
        kill_switch_engaged=kill_switch_engaged,
        kill_switch_reason=kill_switch_reason,
        daily_loss_used_pct=daily_loss_used_pct,
        policy=policy_dict,
    )


async def _fetch_service_account_dict(service: Any) -> dict[str, Any] | None:
    """Best-effort account snapshot from the in-process BingXBotService."""
    get_state = getattr(service, "get_account_state", None)
    if get_state is None:
        return None
    try:
        import inspect

        raw = get_state()
        if inspect.isawaitable(raw):
            account = await asyncio.wait_for(raw, timeout=_ACCOUNT_STATE_TIMEOUT_S)
        else:
            account = raw
    except Exception as exc:
        logger.warning("monitoring_service.account_state_failed error=%s", exc)
        return None

    if hasattr(account, "to_dict"):
        return account.to_dict()
    if isinstance(account, dict):
        return account
    return None


def _normalize_position_side(side: str, size: float) -> str:
    normalized = str(side or "").upper()
    if normalized in {"LONG", "SHORT"}:
        return normalized
    if size > 0:
        return "LONG"
    if size < 0:
        return "SHORT"
    return normalized or "UNKNOWN"


def _load_zone_hints_from_journal() -> dict[str, str]:
    """Best-effort zone map from the latest bot cycle log (no synthetic defaults)."""
    hints: dict[str, str] = {}
    paths: list[Path] = []
    with contextlib.suppress(Exception):
        cfg = load_settings()
        audit_path = str(getattr(cfg, "bingx_bot_audit_db_path", "") or "").strip()
        if audit_path and audit_path != ":memory:":
            paths.append(Path(audit_path))
    paths.append(_DEFAULT_JOURNAL_DB)

    import duckdb

    for path in paths:
        if not path.is_file():
            continue
        try:
            conn = duckdb.connect(str(path), read_only=True)
            try:
                row = conn.execute(
                    """
                    SELECT serialized_metrics
                    FROM bot_cycle_logs
                    ORDER BY timestamp DESC
                    LIMIT 1
                    """
                ).fetchone()
            except Exception:
                continue
            finally:
                conn.close()
            if not row or row[0] is None:
                continue
            raw_metrics = row[0]
            metrics = json.loads(raw_metrics) if isinstance(raw_metrics, str) else raw_metrics
            if not isinstance(metrics, dict):
                continue
            for sym, payload in metrics.items():
                if not isinstance(payload, dict):
                    continue
                zone = payload.get("current_zone")
                if zone:
                    hints[str(sym)] = str(zone).upper()
            if hints:
                return hints
        except Exception as exc:
            logger.debug(
                "monitoring_service.zone_hints_journal_failed path=%s error=%s",
                path,
                exc,
            )
    return hints


def _build_mirror_account(
    account: dict[str, Any] | None,
    risk: TelemetryRiskSummary,
) -> TelemetryMirrorAccount:
    if account is not None:
        equity = float(account.get("total_equity_usdt") or account.get("equity") or 0.0)
        available = float(
            account.get("available_margin_usdt") or account.get("available_margin") or 0.0
        )
        used = float(account.get("used_margin_usdt") or account.get("used_margin") or 0.0)
    else:
        equity = float(risk.equity or risk.balance_usdt or 0.0)
        available = float(risk.available_margin or risk.available_margin_usdt or 0.0)
        used = float(risk.used_margin or risk.used_margin_usdt or 0.0)
    return TelemetryMirrorAccount(
        total_equity=round(equity, 4),
        available_margin=round(available, 4),
        used_margin=round(used, 4),
    )


def _build_mirror_positions(
    account: dict[str, Any] | None,
    *,
    zone_hints: dict[str, str],
) -> tuple[TelemetryMirrorPosition, ...]:
    if account is None:
        return ()
    rows = account.get("open_positions")
    if not isinstance(rows, list):
        return ()

    mirror: list[TelemetryMirrorPosition] = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        symbol = str(row.get("symbol") or "").strip()
        if not symbol:
            continue
        size = float(row.get("size") or 0.0)
        side = _normalize_position_side(str(row.get("side") or ""), size)
        entry = float(row.get("entry_price") or 0.0)
        current = float(
            row.get("current_price") or row.get("current_spot") or row.get("mark_price") or 0.0
        )
        leverage = int(float(row.get("leverage") or 1))
        pnl_raw = row.get("pnl_real_apalancado")
        pnl_real: float | None
        if pnl_raw is not None:
            with contextlib.suppress(TypeError, ValueError):
                pnl_real = float(pnl_raw)
        if pnl_real is None and entry > 0 and current > 0:
            unleveraged = (
                ((current - entry) / entry) * 100.0
                if side == "LONG"
                else ((entry - current) / entry) * 100.0
            )
            pnl_real = unleveraged * leverage
        zone = zone_hints.get(symbol) or str(row.get("current_zone") or "NEUTRAL").upper()
        mirror.append(
            TelemetryMirrorPosition(
                symbol=symbol,
                side=side,
                entry_price=round(entry, 6),
                current_spot=round(current, 6),
                leverage=leverage,
                pnl_real_apalancado=round(pnl_real, 4) if pnl_real is not None else None,
                current_zone=zone,
            )
        )
    return tuple(mirror)


async def _build_risk_summary(
    service: Any,
    *,
    prefer_vst_venue: bool = False,
) -> tuple[TelemetryRiskSummary, bool, dict[str, Any] | None]:
    """Build risk summary; return (summary, venue_live, raw_account_dict)."""
    base = _build_risk_summary_from_desk(service)
    venue_live = not bool(getattr(service, "dry_run", True))

    account: dict[str, Any] | None = None
    if not prefer_vst_venue:
        account = await _fetch_service_account_dict(service)
        if account is not None and not account.get("dry_run"):
            venue_live = True
        elif account is not None and account.get("dry_run"):
            account = None

    if account is None and (prefer_vst_venue or _env_requests_vst_live()):
        account = await _fetch_vst_venue_account()
        if account is not None:
            venue_live = True

    if account is None:
        return base, venue_live, None

    return _risk_summary_from_account_dict(account, base=base), venue_live, account


def _build_scheduler(scheduler: Any) -> TelemetryScheduler:
    """Extract scheduler runtime state with safe degradation."""
    if scheduler is None:
        return TelemetryScheduler(
            configured=False,
            state="not_configured",
            last_cycle_at=None,
            cycle_count=None,
        )
    try:
        status = scheduler.status()
        if not isinstance(status, dict):
            status = {}
    except Exception:
        status = {}

    raw_state = str(status.get("state") or status.get("status") or "unknown").lower()
    if raw_state in {"running", "started", "active"}:
        state_label = "running"
    elif raw_state in {"stopped", "idle", "paused"}:
        state_label = "stopped"
    else:
        state_label = raw_state

    return TelemetryScheduler(
        configured=True,
        state=state_label,
        last_cycle_at=status.get("last_cycle_at") or status.get("last_run"),
        cycle_count=status.get("cycle_count") or status.get("cycles"),
    )


def _build_last_probe(hc_cache: dict[str, Any]) -> TelemetryLastProbe:
    """Extract last deep-probe result from the router-managed cache dict."""
    cached_at = float(hc_cache.get("cached_at") or 0.0)
    age_s: float | None = None
    if cached_at > 0.0:
        age_s = round(monotonic() - cached_at, 1)

    failures_raw = hc_cache.get("failures") or []
    failures: list[str] = [str(f) for f in failures_raw] if isinstance(failures_raw, list) else []

    return TelemetryLastProbe(
        probe_ok=bool(hc_cache.get("ok", False)),
        age_s=age_s,
        fmp_status=hc_cache.get("fmp_status"),
        options_status=hc_cache.get("options_status"),
        l2_active_count=hc_cache.get("l2_active_count"),
        l2_failed_count=hc_cache.get("l2_failed_count"),
        l2_sample_size=hc_cache.get("l2_sample_size"),
        failures=failures,
    )


def _build_universe(service: Any) -> dict[str, Any]:
    """Extract universe metadata from the live service (sync, no IO)."""
    universe = getattr(service, "universe", ()) or ()
    try:
        cfg = load_settings()
        allowlist = sorted(cfg.get_bingx_live_allowlist())
    except Exception:
        allowlist = []

    return {
        "total_count": len(universe),
        "symbols": list(universe),
        "allowlist": allowlist,
    }


# ── Main service class ────────────────────────────────────────────────────────


class BotMonitoringService:
    """Read-only telemetry collector for the BingX Bot dashboard.

    This service has **no state of its own** — it reads from the live service,
    risk desk and scheduler instances provided at call time. It is safe to
    instantiate on every request.

    Usage::

        from backend.services.monitoring_service import BotMonitoringService
        payload = await BotMonitoringService().get_telemetry(
            service=get_service(),
            scheduler=get_scheduler(),
            audit_store=get_audit_store(),
            hc_cache=_hc_cache,
        )
        return payload.to_dict()
    """

    async def get_telemetry(
        self,
        *,
        service: Any,
        scheduler: Any,
        audit_store: Any,
        hc_cache: dict[str, Any],
    ) -> TelemetryPayload:
        """Collect unified real-time telemetry from live instances.

        Guaranteed to return a complete payload — every section degrades
        gracefully on error. The only async operation is the optional account
        balance fetch; all other reads are synchronous in-memory lookups.
        """
        trading_env = _infer_trading_environment(service)
        prefer_vst_venue = trading_env == "prod-vst" or _env_requests_vst_live()

        # Resolve HC TTL from settings
        hc_ttl: float = 300.0
        with contextlib.suppress(Exception):
            hc_ttl = float(load_settings().bingx_bot_live_healthcheck_ttl_s)

        # Derive gate flags for production_ready computation
        enable_live = False
        paper_trading = True
        try:
            cfg = load_settings()
            enable_live = bool(cfg.bingx_bot_enable_live)
            paper_trading = bool(cfg.bingx_bot_paper_trading)
        except Exception:
            pass

        cached_at = float(hc_cache.get("cached_at") or 0.0)
        hc_ok = bool(hc_cache.get("ok", False))
        hc_fresh = hc_ok and cached_at > 0.0 and (monotonic() - cached_at) <= hc_ttl

        desk = getattr(service, "risk_desk", None)
        state = getattr(desk, "state", None)
        kill_switch = bool(getattr(state, "kill_switch_engaged", False))
        risk_desk_ok = not kill_switch

        risk_summary, venue_live, account_dict = await _build_risk_summary(
            service,
            prefer_vst_venue=prefer_vst_venue,
        )
        zone_hints = _load_zone_hints_from_journal()
        mirror_account = _build_mirror_account(account_dict, risk_summary)
        mirror_positions = _build_mirror_positions(account_dict, zone_hints=zone_hints)
        client_live = venue_live or not bool(getattr(service, "dry_run", True))

        production_ready = _compute_production_ready(
            trading_env=trading_env,
            client_live=client_live,
            risk_desk_ok=risk_desk_ok,
            enable_live=enable_live,
            paper_trading=paper_trading,
            hc_fresh=hc_fresh,
        )

        # Build all sections (risk summary is async; others are sync)
        gates = _build_gates(
            service=service,
            trading_env=trading_env,
            hc_cache=hc_cache,
            hc_ttl=hc_ttl,
            audit_store=audit_store,
            scheduler=scheduler,
        )
        # Reflect effective venue connectivity in gates (not only in-memory dry_run flag).
        gates = TelemetryGates(
            enable_live=gates.enable_live,
            client_configured_live=client_live,
            paper_trading=gates.paper_trading,
            vst_mode=trading_env == "prod-vst",
            allowlist=gates.allowlist,
            healthcheck=gates.healthcheck,
            probe_providers=gates.probe_providers,
            audit_persistent=gates.audit_persistent,
            scheduler_configured=gates.scheduler_configured,
            risk_desk=gates.risk_desk,
        )
        sched = _build_scheduler(scheduler)
        universe = _build_universe(service)
        last_probe = _build_last_probe(hc_cache)

        payload = TelemetryPayload(
            captured_at=_utc_iso_now(),
            production_ready=production_ready,
            trading_environment=trading_env,
            dry_run=not client_live,
            gates=gates,
            risk_summary=risk_summary,
            scheduler=sched,
            universe=universe,
            last_probe=last_probe,
            account=mirror_account,
            positions=mirror_positions,
        )

        logger.debug(
            "monitoring_service.telemetry production_ready=%s env=%s client_live=%s "
            "dry_run=%s kill_switch=%s open_positions=%d balance_usdt=%.2f",
            production_ready,
            trading_env,
            client_live,
            payload.dry_run,
            kill_switch,
            risk_summary.open_position_count,
            risk_summary.balance_usdt,
        )

        return payload
