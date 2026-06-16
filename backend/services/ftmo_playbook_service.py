from __future__ import annotations
from typing import Any
"""Read-only FTMO funding playbook service.

The playbook is an operational layer above Funding Lab. It records manual
account state and trade intentions, but it never talks to a broker and never
imports the BingX Bot.
"""


import inspect
import json
import sqlite3
from datetime import UTC, datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from backend.config.logger_setup import get_logger
from backend.services.ftmo_playbook_audit_service import FTMOPlaybookAuditService
from backend.services.funding_lab_service import DEFAULT_PREDICTIONS_DB, FundingLabService

logger = get_logger(__name__)

PLAYBOOK_READY = "PLAYBOOK_READY"
PLAYBOOK_BLOCK = "PLAYBOOK_BLOCK"
PLAYBOOK_REDUCE_SIZE = "PLAYBOOK_REDUCE_SIZE"
PLAYBOOK_OBSERVE = "PLAYBOOK_OBSERVE"
PLAYBOOK_LOCKED_DAY = "PLAYBOOK_LOCKED_DAY"

FTMO_PROFILE_ID = "ftmo_2_step_standard"
FTMO_TIMEZONE = "Europe/Prague"
DEFAULT_INITIAL_CAPITAL = 100_000.0
DEFAULT_RISK_PER_TRADE_PCT = 0.50
DAILY_LOSS_LIMIT_PCT = 5.0
MAX_LOSS_LIMIT_PCT = 10.0
CHALLENGE_PROFIT_TARGET_PCT = 10.0
VERIFICATION_PROFIT_TARGET_PCT = 5.0
MIN_TRADING_DAYS = 4
CONSISTENCY_WARNING_RATIO = 0.35
CONSISTENCY_BLOCK_RATIO = 0.50


def default_playbook_state() -> dict[str, Any]:
    now = datetime.now(tz=UTC).isoformat()
    return {
        "profile_id": FTMO_PROFILE_ID,
        "phase": "challenge",
        "initial_capital": DEFAULT_INITIAL_CAPITAL,
        "current_equity": DEFAULT_INITIAL_CAPITAL,
        "start_of_day_balance": DEFAULT_INITIAL_CAPITAL,
        "realized_daily_pnl": 0.0,
        "unrealized_pnl": 0.0,
        "commissions": 0.0,
        "swaps": 0.0,
        "risk_budget_per_trade_pct": DEFAULT_RISK_PER_TRADE_PCT,
        "trade_history": [],
        "updated_at": now,
    }


def compute_ftmo_playbook_metrics(state: dict[str, Any]) -> dict[str, Any]:
    initial = _positive_float(state.get("initial_capital"), DEFAULT_INITIAL_CAPITAL)
    current_equity = _positive_float(state.get("current_equity"), initial)
    start_of_day = _positive_float(state.get("start_of_day_balance"), initial)
    realized = _float(state.get("realized_daily_pnl"), 0.0)
    unrealized = _float(state.get("unrealized_pnl"), 0.0)
    commissions = abs(_float(state.get("commissions"), 0.0))
    swaps = abs(_float(state.get("swaps"), 0.0))
    effective_equity = current_equity + unrealized - commissions - swaps
    daily_loss_amount = initial * DAILY_LOSS_LIMIT_PCT / 100.0
    max_loss_amount = initial * MAX_LOSS_LIMIT_PCT / 100.0
    daily_floor = start_of_day - daily_loss_amount
    max_floor = initial - max_loss_amount
    daily_pnl_used = abs(min(0.0, realized + unrealized - commissions - swaps))
    daily_used = max(0.0, start_of_day - effective_equity, daily_pnl_used)
    max_used = max(0.0, initial - effective_equity)
    remaining_daily = max(0.0, effective_equity - daily_floor)
    remaining_max = max(0.0, effective_equity - max_floor)
    daily_usage_pct = _pct(daily_used, daily_loss_amount)
    max_usage_pct = _pct(max_used, max_loss_amount)
    trade_days = _trading_days(state.get("trade_history"))
    best_day = _best_day_metrics(state.get("trade_history"))
    phase = str(state.get("phase") or "challenge").lower()
    target_pct = (
        VERIFICATION_PROFIT_TARGET_PCT if phase == "verification" else CHALLENGE_PROFIT_TARGET_PCT
    )
    profit_target_balance = initial * (1.0 + target_pct / 100.0)
    blockers: list[str] = []
    if daily_usage_pct >= 80.0:
        blockers.append("daily_loss_usage_high")
    if max_usage_pct >= 80.0:
        blockers.append("max_loss_usage_high")
    if daily_usage_pct >= 100.0:
        blockers.append("daily_loss_breach")
    if max_usage_pct >= 100.0:
        blockers.append("max_loss_breach")
    if best_day["best_day_contribution_pct"] >= CONSISTENCY_BLOCK_RATIO * 100.0:
        blockers.append("best_day_concentration")
    elif best_day["best_day_contribution_pct"] >= CONSISTENCY_WARNING_RATIO * 100.0:
        blockers.append("consistency_warning")
    day_status = (
        PLAYBOOK_LOCKED_DAY
        if any("breach" in item or item.endswith("_high") for item in blockers)
        else "OPEN"
    )
    return {
        "rules": {
            "profile_id": FTMO_PROFILE_ID,
            "timezone": FTMO_TIMEZONE,
            "daily_loss_limit_pct": DAILY_LOSS_LIMIT_PCT,
            "max_loss_limit_pct": MAX_LOSS_LIMIT_PCT,
            "challenge_profit_target_pct": CHALLENGE_PROFIT_TARGET_PCT,
            "verification_profit_target_pct": VERIFICATION_PROFIT_TARGET_PCT,
            "min_trading_days": MIN_TRADING_DAYS,
            "consistency_warning_pct": CONSISTENCY_WARNING_RATIO * 100.0,
            "consistency_block_pct": CONSISTENCY_BLOCK_RATIO * 100.0,
        },
        "reset_timezone": FTMO_TIMEZONE,
        "reset_time": "00:00 CE(S)T",
        "effective_equity": round(effective_equity, 2),
        "daily_loss_limit_amount": round(daily_loss_amount, 2),
        "daily_loss_floor": round(daily_floor, 2),
        "daily_loss_used_amount": round(daily_used, 2),
        "daily_loss_usage_pct": round(daily_usage_pct, 2),
        "remaining_daily_risk_amount": round(remaining_daily, 2),
        "remaining_daily_risk_pct": round(remaining_daily / initial * 100.0, 4),
        "max_loss_floor": round(max_floor, 2),
        "max_loss_used_amount": round(max_used, 2),
        "max_loss_usage_pct": round(max_usage_pct, 2),
        "remaining_max_risk_amount": round(remaining_max, 2),
        "remaining_max_risk_pct": round(remaining_max / initial * 100.0, 4),
        "profit_target_balance": round(profit_target_balance, 2),
        "profit_target_remaining_amount": round(
            max(0.0, profit_target_balance - effective_equity), 2
        ),
        "profit_target_progress_pct": round(
            _pct(max(0.0, effective_equity - initial), profit_target_balance - initial), 2
        ),
        "trading_days": len(trade_days),
        "min_trading_days_required": MIN_TRADING_DAYS,
        "trading_days_remaining": max(0, MIN_TRADING_DAYS - len(trade_days)),
        "best_day_contribution_pct": best_day["best_day_contribution_pct"],
        "consistency_headroom_pct": round(
            max(0.0, CONSISTENCY_BLOCK_RATIO * 100.0 - best_day["best_day_contribution_pct"]),
            4,
        ),
        "risk_budget_per_trade_pct": _float(
            state.get("risk_budget_per_trade_pct"), DEFAULT_RISK_PER_TRADE_PCT
        ),
        "day_status": day_status,
        "blockers": _dedupe(blockers),
    }


class FTMOPlaybookService:
    """Persistent manual playbook for FTMO funding operations."""

    def __init__(
        self,
        *,
        predictions_db: str | Path = DEFAULT_PREDICTIONS_DB,
        funding_service: Any | None = None,
    ) -> None:
        self.predictions_db = Path(predictions_db)
        self.funding_service = funding_service or FundingLabService(
            predictions_db=self.predictions_db
        )
        self.audit_service = FTMOPlaybookAuditService(predictions_db=self.predictions_db)

    def get_state(self) -> dict[str, Any]:
        self._ensure_schema()
        state = self._load_state()
        status = self.funding_service.status()
        return {
            "ok": True,
            "state": state,
            "metrics": compute_ftmo_playbook_metrics(state),
            "latest_monitor_run": status.get("latest_monitor_run"),
            "operational_monitor": status.get("operational_monitor"),
        }

    def update_state(self, payload: dict[str, Any]) -> dict[str, Any]:
        self._ensure_schema()
        state = {**self._load_state(), **payload}
        state = _normalize_state(state)
        self._save_state(state)
        metrics = compute_ftmo_playbook_metrics(state)
        audit_event = self.audit_service.record_audit_event(
            event_type="state_updated",
            payload={"state": state, "metrics": metrics},
        )
        return {
            "ok": True,
            **state,
            "metrics": metrics,
            "audit_event_id": audit_event["event_id"],
        }

    async def evaluate_trade_intent(self, payload: dict[str, Any]) -> dict[str, Any]:
        self._ensure_schema()
        state = self._load_state()
        metrics = compute_ftmo_playbook_metrics(state)
        status = self.funding_service.status()
        monitor = _monitor_summary(status)
        side = str(payload.get("side") or "").upper()
        symbol = str(payload.get("symbol") or "").strip().upper()
        entry = _float(payload.get("entry"), 0.0)
        stop = _float(payload.get("stop"), 0.0)
        target = payload.get("target")
        blockers: list[str] = []
        warnings: list[str] = []
        if not monitor["ok"]:
            blockers.append("monitor_not_ok")
        if not monitor["source_ready"]:
            blockers.append("source_not_ready")
        if not monitor["analysis_ready"]:
            blockers.append("analysis_not_ready")
        if not monitor["production_ready"]:
            blockers.append("production_not_ready")
        if not monitor["trade_gate_ready"]:
            blockers.append("trade_gate_not_ready")
        if metrics["day_status"] == PLAYBOOK_LOCKED_DAY:
            blockers.extend(str(item) for item in metrics["blockers"])
        signal_check = await _maybe_await(
            self.funding_service.signal_check(
                symbol=symbol,
                entry_direction=side or None,
                account_state=_account_state_for_signal(state),
            )
        )
        survival = signal_check.get("funding_survival") if isinstance(signal_check, dict) else {}
        if not bool(signal_check.get("trade_ready")):
            blockers.append("trade_not_ready")
        if isinstance(survival, dict):
            if survival.get("status") != "SAFE":
                blockers.append("survival_not_safe")
            if _float(survival.get("score"), 0.0) < 70.0:
                blockers.append("survival_score_below_minimum")
            if _float(survival.get("recommended_risk_per_trade_pct"), 0.0) <= 0.0:
                blockers.append("survival_risk_budget_zero")
        gex_validation = _gex_validation_from_signal(signal_check)
        if gex_validation and not _gex_ready(gex_validation):
            blockers.extend(
                str(item) for item in gex_validation.get("gex_blockers") or ["gex_not_ready"]
            )
        stop_distance = _stop_distance(side=side, entry=entry, stop=stop)
        if stop_distance <= 0.0:
            blockers.append("invalid_entry_stop")
        allowed_risk_pct = _allowed_risk_pct(
            metrics, survival if isinstance(survival, dict) else {}
        )
        allowed_risk_amount = _allowed_risk_amount(metrics, allowed_risk_pct)
        requested_risk_amount = _float(payload.get("requested_risk_amount"), 0.0)
        if allowed_risk_amount <= 0.0:
            blockers.append("playbook_risk_budget_zero")
        if requested_risk_amount > allowed_risk_amount > 0.0:
            warnings.append("requested_risk_exceeds_cap")
        position_size = allowed_risk_amount / stop_distance if stop_distance > 0.0 else 0.0
        rr = _reward_risk(side=side, entry=entry, stop=stop, target=target)
        hard_blockers = _dedupe(blockers)
        if hard_blockers:
            decision = PLAYBOOK_BLOCK
        elif requested_risk_amount > allowed_risk_amount > 0.0:
            decision = PLAYBOOK_REDUCE_SIZE
        else:
            decision = PLAYBOOK_READY
        checklist = {
            "monitor_ok": monitor["ok"],
            "source_ready": monitor["source_ready"],
            "analysis_ready": monitor["analysis_ready"],
            "production_ready": monitor["production_ready"],
            "trade_gate_ready": monitor["trade_gate_ready"],
            "trade_ready": bool(signal_check.get("trade_ready")),
            "survival_safe": isinstance(survival, dict) and survival.get("status") == "SAFE",
            "risk_within_budget": allowed_risk_amount > 0.0 and not hard_blockers,
            "gex_ready": not gex_validation or _gex_ready(gex_validation),
            "playbook_ready": decision == PLAYBOOK_READY,
        }
        result = {
            "ok": True,
            "intent_id": _id("ftmo-intent"),
            "request": {
                "symbol": symbol,
                "side": side,
                "entry": entry,
                "stop": stop,
                "target": target,
            },
            "decision": decision,
            "blockers": hard_blockers,
            "warnings": _dedupe(warnings),
            "allowed_risk_pct": round(allowed_risk_pct, 4),
            "allowed_risk_amount": round(allowed_risk_amount, 2),
            "position_size_units": round(position_size, 4),
            "rr": rr,
            "account_state_used": _account_state_for_signal(state),
            "signal_check": signal_check,
            "monitor_summary": monitor,
            "checklist": checklist,
            "created_at": datetime.now(tz=UTC).isoformat(),
        }
        self._save_trade_intent(result)
        created_event = self.audit_service.record_audit_event(
            event_type="trade_intent_created",
            payload=result,
            symbol=symbol,
            intent_id=result["intent_id"],
        )
        decision_event = self.audit_service.record_audit_event(
            event_type="playbook_decision",
            payload={
                "intent_id": result["intent_id"],
                "decision": result["decision"],
                "blockers": result["blockers"],
                "warnings": result["warnings"],
                "checklist": result["checklist"],
                "allowed_risk_amount": result["allowed_risk_amount"],
            },
            symbol=symbol,
            intent_id=result["intent_id"],
            parent_event_id=created_event["event_id"],
        )
        result["audit_event_id"] = decision_event["event_id"]
        return result

    def record_journal(self, payload: dict[str, Any]) -> dict[str, Any]:
        self._ensure_schema()
        now = datetime.now(tz=UTC).isoformat()
        symbol = str(payload.get("symbol") or "").upper()
        side = str(payload.get("side") or "").upper()
        status = str(payload.get("status") or "recorded")
        intent_id = str(payload.get("intent_id") or "") or None
        entry = {
            "ok": True,
            "journal_id": _id("ftmo-journal"),
            "intent_id": intent_id,
            "symbol": symbol,
            "side": side,
            "status": status,
            "pnl": _float(payload.get("pnl", payload.get("net_pnl")), 0.0),
            "actual_entry": _optional_float(payload.get("actual_entry")),
            "actual_exit": _optional_float(payload.get("actual_exit")),
            "actual_stop": _optional_float(payload.get("actual_stop")),
            "actual_size_units": _optional_float(payload.get("actual_size_units")),
            "fees": _float(payload.get("fees"), 0.0),
            "swap": _float(payload.get("swap"), 0.0),
            "gross_pnl": _optional_float(payload.get("gross_pnl")),
            "net_pnl": _optional_float(payload.get("net_pnl")),
            "closed_at": payload.get("closed_at"),
            "reason": str(payload.get("reason") or ""),
            "notes": str(payload.get("notes") or ""),
            "created_at": now,
        }
        intent = self._load_trade_intent(intent_id) if intent_id else None
        reconciliation = self._reconcile_journal(entry, intent)
        entry.update(reconciliation)
        con = self._connect()
        try:
            con.execute(
                """
                INSERT INTO ftmo_playbook_journal
                    (journal_id, symbol, side, status, pnl, payload_json, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    entry["journal_id"],
                    entry["symbol"],
                    entry["side"],
                    entry["status"],
                    entry["pnl"],
                    json.dumps(entry, sort_keys=True),
                    now,
                ),
            )
            con.commit()
        finally:
            con.close()
        recorded_event = self.audit_service.record_audit_event(
            event_type="journal_recorded",
            payload=entry,
            symbol=symbol,
            intent_id=intent_id,
            journal_id=entry["journal_id"],
        )
        reconciled_event = self.audit_service.record_audit_event(
            event_type="journal_reconciled",
            payload={
                "journal_id": entry["journal_id"],
                "intent_id": intent_id,
                "reconciliation_status": entry["reconciliation_status"],
                "reconciliation_warnings": entry["reconciliation_warnings"],
            },
            symbol=symbol,
            intent_id=intent_id,
            journal_id=entry["journal_id"],
            parent_event_id=recorded_event["event_id"],
        )
        entry["audit_event_id"] = reconciled_event["event_id"]
        return entry

    def report(self, *, date: str | None = None) -> dict[str, Any]:
        self._ensure_schema()
        state = self._load_state()
        metrics = compute_ftmo_playbook_metrics(state)
        journal = self._load_journal(date=date)
        status = self.funding_service.status()
        audit = self.audit_report(date=date)
        return {
            "ok": True,
            "date": date or datetime.now(tz=ZoneInfo(FTMO_TIMEZONE)).date().isoformat(),
            "state": state,
            "metrics": metrics,
            "journal_count": len(journal),
            "journal": journal,
            "audit_summary": audit.get("summary"),
            "audit_hash_chain": audit.get("hash_chain"),
            "latest_monitor_run": status.get("latest_monitor_run"),
            "operational_monitor": status.get("operational_monitor"),
            "generated_at": datetime.now(tz=UTC).isoformat(),
        }

    def audit_report(
        self,
        *,
        date: str | None = None,
        symbol: str | None = None,
        event_type: str | None = None,
    ) -> dict[str, Any]:
        return self.audit_service.build_audit_report(
            date=date,
            symbol=symbol.upper() if symbol else None,
            event_type=event_type,
        )

    def audit_export(
        self,
        *,
        date: str | None = None,
        symbol: str | None = None,
        event_type: str | None = None,
        output_format: str = "json",
    ) -> Any:
        report = self.audit_report(date=date, symbol=symbol, event_type=event_type)
        if output_format == "markdown":
            return self.audit_service.render_markdown_report(report)
        return report

    def _ensure_schema(self) -> None:
        self.predictions_db.parent.mkdir(parents=True, exist_ok=True)
        con = self._connect()
        try:
            con.executescript(
                """
                CREATE TABLE IF NOT EXISTS ftmo_playbook_state (
                    state_id TEXT PRIMARY KEY,
                    payload_json TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS ftmo_playbook_trade_intents (
                    intent_id TEXT PRIMARY KEY,
                    symbol TEXT NOT NULL,
                    side TEXT NOT NULL,
                    decision TEXT NOT NULL,
                    payload_json TEXT NOT NULL,
                    created_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS ftmo_playbook_journal (
                    journal_id TEXT PRIMARY KEY,
                    symbol TEXT NOT NULL,
                    side TEXT,
                    status TEXT NOT NULL,
                    pnl REAL NOT NULL DEFAULT 0,
                    payload_json TEXT NOT NULL,
                    created_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS ftmo_playbook_daily_snapshots (
                    snapshot_date TEXT PRIMARY KEY,
                    payload_json TEXT NOT NULL,
                    created_at TEXT NOT NULL
                );
                """
            )
            con.commit()
        finally:
            con.close()
        self.audit_service.ensure_schema()

    def _load_state(self) -> dict[str, Any]:
        con = self._connect()
        try:
            row = con.execute(
                "SELECT payload_json FROM ftmo_playbook_state WHERE state_id = 'default'"
            ).fetchone()
            if not row:
                state = default_playbook_state()
                self._save_state(state, connection=con)
                return state
            payload = json.loads(str(row[0]))
            return _normalize_state(payload if isinstance(payload, dict) else {})
        finally:
            con.close()

    def _save_state(
        self,
        state: dict[str, Any],
        *,
        connection: sqlite3.Connection | None = None,
    ) -> None:
        state["updated_at"] = datetime.now(tz=UTC).isoformat()
        con = connection or self._connect()
        try:
            con.execute(
                """
                INSERT INTO ftmo_playbook_state (state_id, payload_json, updated_at)
                VALUES ('default', ?, ?)
                ON CONFLICT(state_id) DO UPDATE SET
                    payload_json = excluded.payload_json,
                    updated_at = excluded.updated_at
                """,
                (json.dumps(state, sort_keys=True), state["updated_at"]),
            )
            con.commit()
        finally:
            if connection is None:
                con.close()

    def _save_trade_intent(self, result: dict[str, Any]) -> None:
        con = self._connect()
        try:
            con.execute(
                """
                INSERT INTO ftmo_playbook_trade_intents
                    (intent_id, symbol, side, decision, payload_json, created_at)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    result["intent_id"],
                    result["request"].get("symbol", ""),
                    str(result["request"].get("side") or ""),
                    result["decision"],
                    json.dumps(result, sort_keys=True, default=str),
                    result["created_at"],
                ),
            )
            con.commit()
        finally:
            con.close()

    def _load_journal(self, *, date: str | None = None) -> list[dict[str, Any]]:
        con = self._connect()
        try:
            if date:
                rows = con.execute(
                    """
                    SELECT payload_json FROM ftmo_playbook_journal
                    WHERE substr(created_at, 1, 10) = ?
                    ORDER BY created_at ASC
                    """,
                    (date,),
                ).fetchall()
            else:
                rows = con.execute(
                    "SELECT payload_json FROM ftmo_playbook_journal ORDER BY created_at ASC"
                ).fetchall()
            return [json.loads(str(row[0])) for row in rows]
        finally:
            con.close()

    def _load_trade_intent(self, intent_id: str | None) -> dict[str, Any] | None:
        if not intent_id:
            return None
        con = self._connect()
        try:
            row = con.execute(
                """
                SELECT payload_json FROM ftmo_playbook_trade_intents
                WHERE intent_id = ?
                """,
                (intent_id,),
            ).fetchone()
            return json.loads(str(row[0])) if row else None
        finally:
            con.close()

    def _reconcile_journal(
        self,
        entry: dict[str, Any],
        intent: dict[str, Any] | None,
    ) -> dict[str, Any]:
        warnings: list[str] = []
        status = "matched"
        if not intent:
            return {
                "reconciliation_status": "unlinked",
                "reconciliation_warnings": ["journal_missing_intent"],
            }
        if entry["status"] != "executed":
            return {"reconciliation_status": "matched", "reconciliation_warnings": []}
        request = intent.get("request") if isinstance(intent.get("request"), dict) else {}
        if intent.get("decision") != PLAYBOOK_READY:
            warnings.append("executed_blocked_intent")
        if str(request.get("symbol") or "").upper() != entry["symbol"]:
            warnings.append("symbol_mismatch")
        if str(request.get("side") or "").upper() != entry["side"]:
            warnings.append("side_mismatch")
        actual_risk = _actual_risk_amount(entry)
        allowed_risk = _float(intent.get("allowed_risk_amount"), 0.0)
        if actual_risk > allowed_risk + 0.01:
            warnings.append("actual_risk_exceeded")
        if warnings:
            status = (
                "breach"
                if any(
                    item in warnings
                    for item in {
                        "executed_blocked_intent",
                        "actual_risk_exceeded",
                    }
                )
                else "warning"
            )
        return {
            "reconciliation_status": status,
            "reconciliation_warnings": _dedupe(warnings),
        }

    def _connect(self) -> sqlite3.Connection:
        return sqlite3.connect(self.predictions_db)


async def _maybe_await(value: Any) -> Any:
    if inspect.isawaitable(value):
        return await value
    return value


def _normalize_state(payload: dict[str, Any]) -> dict[str, Any]:
    state = {**default_playbook_state(), **payload}
    state["phase"] = str(state.get("phase") or "challenge").lower()
    if state["phase"] not in {"challenge", "verification", "funded"}:
        state["phase"] = "challenge"
    state["initial_capital"] = _positive_float(
        state.get("initial_capital"), DEFAULT_INITIAL_CAPITAL
    )
    state["current_equity"] = _positive_float(state.get("current_equity"), state["initial_capital"])
    state["start_of_day_balance"] = _positive_float(
        state.get("start_of_day_balance"), state["initial_capital"]
    )
    for key in ("realized_daily_pnl", "unrealized_pnl", "commissions", "swaps"):
        state[key] = _float(state.get(key), 0.0)
    state["risk_budget_per_trade_pct"] = max(
        0.0,
        min(5.0, _float(state.get("risk_budget_per_trade_pct"), DEFAULT_RISK_PER_TRADE_PCT)),
    )
    history = state.get("trade_history")
    state["trade_history"] = history if isinstance(history, list) else []
    return state


def _account_state_for_signal(state: dict[str, Any]) -> dict[str, Any]:
    return {
        "initial_capital": state["initial_capital"],
        "current_equity": state["current_equity"],
        "start_of_day_balance": state["start_of_day_balance"],
        "realized_daily_pnl": state["realized_daily_pnl"],
        "unrealized_pnl": state["unrealized_pnl"],
        "trade_history": state.get("trade_history") or [],
    }


def _monitor_summary(status: dict[str, Any]) -> dict[str, Any]:
    monitor = status.get("operational_monitor")
    if not isinstance(monitor, dict):
        production_ready = bool(status.get("production_ready_symbols")) and (
            status.get("production_ready_symbols") == status.get("total_symbols")
        )
        monitor = {
            "ok": production_ready,
            "source_ready": production_ready,
            "analysis_ready": production_ready,
            "production_ready": production_ready,
            "trade_gate_ready": True,
        }
    return {
        "ok": bool(monitor.get("ok")),
        "source_ready": bool(monitor.get("source_ready")),
        "analysis_ready": bool(monitor.get("analysis_ready")),
        "production_ready": bool(monitor.get("production_ready")),
        "trade_gate_ready": bool(monitor.get("trade_gate_ready", True)),
        "blocked_symbols": list(monitor.get("blocked_symbols") or []),
        "operational_blockers": list(monitor.get("operational_blockers") or []),
    }


def _gex_validation_from_signal(signal_check: dict[str, Any]) -> dict[str, Any] | None:
    readiness = signal_check.get("readiness")
    if not isinstance(readiness, dict):
        return None
    gex = readiness.get("gex_validation")
    return gex if isinstance(gex, dict) else None


def _gex_ready(gex: dict[str, Any]) -> bool:
    if bool(gex.get("gex_required")):
        return bool(gex.get("gex_validated")) and not gex.get("gex_blockers")
    if bool(gex.get("gex_context_required")):
        return bool(gex.get("gex_context_ready")) and not gex.get("gex_blockers")
    return True


def _allowed_risk_pct(metrics: dict[str, Any], survival: dict[str, Any]) -> float:
    return max(
        0.0,
        min(
            _float(metrics.get("risk_budget_per_trade_pct"), DEFAULT_RISK_PER_TRADE_PCT),
            _float(survival.get("recommended_risk_per_trade_pct"), 0.0),
            _float(metrics.get("remaining_daily_risk_pct"), 0.0),
            _float(metrics.get("remaining_max_risk_pct"), 0.0),
        ),
    )


def _allowed_risk_amount(metrics: dict[str, Any], allowed_risk_pct: float) -> float:
    equity = _float(metrics.get("effective_equity"), DEFAULT_INITIAL_CAPITAL)
    amount = equity * allowed_risk_pct / 100.0
    return min(
        amount,
        _float(metrics.get("remaining_daily_risk_amount"), 0.0),
        _float(metrics.get("remaining_max_risk_amount"), 0.0),
    )


def _stop_distance(*, side: str, entry: float, stop: float) -> float:
    if entry <= 0.0 or stop <= 0.0:
        return 0.0
    if side == "LONG":
        return entry - stop if stop < entry else 0.0
    if side == "SHORT":
        return stop - entry if stop > entry else 0.0
    return 0.0


def _reward_risk(*, side: str, entry: float, stop: float, target: Any) -> float | None:
    target_value = _float(target, 0.0)
    risk = _stop_distance(side=side, entry=entry, stop=stop)
    if risk <= 0.0 or target_value <= 0.0:
        return None
    reward = target_value - entry if side == "LONG" else entry - target_value
    return round(reward / risk, 4) if reward > 0.0 else None


def _actual_risk_amount(entry: dict[str, Any]) -> float:
    actual_entry = _optional_float(entry.get("actual_entry"))
    actual_stop = _optional_float(entry.get("actual_stop"))
    size = _optional_float(entry.get("actual_size_units"))
    if actual_entry is None or actual_stop is None or size is None:
        return 0.0
    return abs(actual_entry - actual_stop) * abs(size)


def _trading_days(history: Any) -> set[str]:
    days: set[str] = set()
    if not isinstance(history, list):
        return days
    for item in history:
        if isinstance(item, dict) and item.get("date"):
            days.add(str(item["date"])[:10])
    return days


def _best_day_metrics(history: Any) -> dict[str, float]:
    daily: dict[str, float] = {}
    if isinstance(history, list):
        for index, item in enumerate(history):
            if not isinstance(item, dict):
                continue
            key = str(item.get("date") or f"unknown-{index}")[:10]
            daily[key] = daily.get(key, 0.0) + _float(item.get("pnl"), 0.0)
    positive = [value for value in daily.values() if value > 0.0]
    total = sum(positive)
    best = max(positive) if positive else 0.0
    return {"best_day_contribution_pct": round(_pct(best, total), 2) if total > 0.0 else 0.0}


def _id(prefix: str) -> str:
    return f"{prefix}-{datetime.now(tz=UTC).strftime('%Y%m%dT%H%M%S%fZ')}"


def _dedupe(values: list[str]) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for value in values:
        if value and value not in seen:
            seen.add(value)
            result.append(value)
    return result


def _pct(numerator: float, denominator: float) -> float:
    return numerator / denominator * 100.0 if denominator > 0.0 else 0.0


def _positive_float(value: Any, default: float) -> float:
    parsed = _float(value, default)
    return parsed if parsed > 0.0 else default


def _float(value: Any, default: float) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _optional_float(value: Any) -> float | None:
    if value is None or value == "":
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None
