from __future__ import annotations
from typing import Any
"""Local paper-trading validation for FTMO Funding Lab.

The simulator consumes Playbook-approved trade intents and persisted provider
bars. It never connects to a broker and never imports the BingX Bot.
"""


import json
import sqlite3
from collections import Counter
from datetime import UTC, datetime, timedelta
from pathlib import Path

from backend.config.logger_setup import get_logger
from backend.services.ftmo_playbook_audit_service import FTMOPlaybookAuditService
from backend.services.ftmo_playbook_service import (
    DAILY_LOSS_LIMIT_PCT,
    DEFAULT_INITIAL_CAPITAL,
    MAX_LOSS_LIMIT_PCT,
    PLAYBOOK_READY,
    FTMOPlaybookService,
)
from backend.services.funding_lab_service import DEFAULT_PREDICTIONS_DB

logger = get_logger(__name__)

SIM_OBSERVE = "SIM_OBSERVE"
SIM_READY = "SIM_READY"
SIM_BLOCKED = "SIM_BLOCKED"
SIM_OPEN = "SIM_OPEN"
SIM_CLOSED = "SIM_CLOSED"
SIM_VALIDATED = "SIM_VALIDATED"
SIM_FAILED = "SIM_FAILED"

SIM_MIN_CLOSED_TRADES = 20
SIM_MIN_CALENDAR_DAYS = 10
SIM_MIN_TRADING_DAYS = 4
SIM_MIN_PROFIT_FACTOR = 1.15
SIM_MAX_DRAWDOWN_PCT = 4.0
SIM_MAX_DAILY_USAGE_PCT = 80.0
SIM_MAX_LOSS_USAGE_PCT = 80.0

SIM_REPORT_DIR = Path("backend/reports/funding-lab/simulation")


class FTMOSimulationService:
    """Persistent local simulator for Playbook-approved FTMO intents."""

    def __init__(
        self,
        *,
        predictions_db: str | Path = DEFAULT_PREDICTIONS_DB,
        playbook_service: FTMOPlaybookService | None = None,
    ) -> None:
        self.predictions_db = Path(predictions_db)
        self.playbook_service = playbook_service or FTMOPlaybookService(
            predictions_db=self.predictions_db
        )
        self.audit_service = FTMOPlaybookAuditService(predictions_db=self.predictions_db)

    def state(self) -> dict[str, Any]:
        self._ensure_schema()
        session = self._active_session()
        report = self.report(session_id=session["session_id"]) if session else None
        return {
            "ok": True,
            "status": report.get("validation_status") if report else SIM_OBSERVE,
            "active_session": session,
            "report": report,
        }

    def create_session(self, payload: dict[str, Any] | None = None) -> dict[str, Any]:
        self._ensure_schema()
        payload = payload or {}
        playbook_state = self.playbook_service.get_state()
        account_state = playbook_state.get("state") if isinstance(playbook_state, dict) else {}
        initial_capital = _positive_float(
            payload.get("initial_capital"),
            _positive_float(
                account_state.get("initial_capital") if isinstance(account_state, dict) else None,
                DEFAULT_INITIAL_CAPITAL,
            ),
        )
        now = datetime.now(tz=UTC).isoformat()
        session = {
            "session_id": _id("ftmo-sim-session"),
            "status": SIM_OBSERVE,
            "initial_capital": initial_capital,
            "current_equity": initial_capital,
            "started_at": now,
            "updated_at": now,
            "metadata": (
                payload.get("metadata") if isinstance(payload.get("metadata"), dict) else {}
            ),
        }
        con = self._connect()
        try:
            con.execute(
                """
                INSERT INTO ftmo_sim_sessions (
                    session_id, status, initial_capital, current_equity,
                    started_at, updated_at, payload_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    session["session_id"],
                    session["status"],
                    session["initial_capital"],
                    session["current_equity"],
                    session["started_at"],
                    session["updated_at"],
                    json.dumps(session, sort_keys=True, default=str),
                ),
            )
            con.commit()
        finally:
            con.close()
        audit = self.audit_service.record_audit_event(
            event_type="sim_session_created",
            payload=session,
            source="simulation",
        )
        session["audit_event_id"] = audit["event_id"]
        return {"ok": True, **session}

    def create_order(self, payload: dict[str, Any]) -> dict[str, Any]:
        self._ensure_schema()
        intent_id = str(payload.get("intent_id") or "")
        intent = self._load_intent(intent_id)
        if not intent:
            return _blocked(["sim_intent_missing"])
        if intent.get("decision") != PLAYBOOK_READY:
            return _blocked(["sim_intent_not_playbook_ready"])
        audit_chain = self.audit_service.validate_audit_chain()
        if not audit_chain.get("ok"):
            return _blocked(["sim_audit_chain_broken"])
        request = intent.get("request") if isinstance(intent.get("request"), dict) else {}
        symbol = str(request.get("symbol") or "").upper()
        bars = self._load_market_bars(symbol)
        if not bars:
            return _blocked(["sim_market_data_missing"])
        session = self._session_for_order(str(payload.get("session_id") or ""))
        side = str(request.get("side") or "").upper()
        entry = _float(request.get("entry"), 0.0)
        stop = _float(request.get("stop"), 0.0)
        target = _optional_float(request.get("target"))
        size_units = _positive_float(
            payload.get("size_units"), _float(intent.get("position_size_units"), 0.0)
        )
        if size_units <= 0.0:
            return _blocked(["sim_size_missing"])
        order = {
            "order_id": _id("ftmo-sim-order"),
            "session_id": session["session_id"],
            "intent_id": intent_id,
            "symbol": symbol,
            "side": side,
            "entry": entry,
            "stop": stop,
            "target": target,
            "size_units": size_units,
            "status": SIM_READY,
            "entry_fill_price": None,
            "exit_fill_price": None,
            "exit_reason": None,
            "realized_pnl": 0.0,
            "created_at": datetime.now(tz=UTC).isoformat(),
            "updated_at": datetime.now(tz=UTC).isoformat(),
        }
        simulated = self._simulate_order(order, bars)
        self._save_order(simulated)
        self._save_fills_for_order(simulated)
        self._refresh_session_equity(session["session_id"])
        order_event = self.audit_service.record_audit_event(
            event_type="sim_order_created",
            payload=simulated,
            symbol=symbol,
            intent_id=intent_id,
            source="simulation",
        )
        final_event = order_event
        if simulated["status"] == SIM_OPEN:
            final_event = self.audit_service.record_audit_event(
                event_type="sim_order_opened",
                payload=simulated,
                symbol=symbol,
                intent_id=intent_id,
                parent_event_id=order_event["event_id"],
                source="simulation",
            )
        elif simulated["status"] == SIM_CLOSED:
            final_event = self.audit_service.record_audit_event(
                event_type="sim_order_closed",
                payload=simulated,
                symbol=symbol,
                intent_id=intent_id,
                parent_event_id=order_event["event_id"],
                source="simulation",
            )
        return {
            "ok": True,
            "status": simulated["status"],
            "order": simulated,
            "session": self._load_session(session["session_id"]),
            "audit_event_id": final_event["event_id"],
        }

    def mark_to_market(self, *, session_id: str | None = None) -> dict[str, Any]:
        self._ensure_schema()
        session = self._session_for_order(session_id or "")
        open_orders = self._load_orders(session_id=session["session_id"], status=SIM_OPEN)
        updated = 0
        for order in open_orders:
            bars = self._load_market_bars(str(order["symbol"]))
            simulated = self._simulate_order(order, bars, require_entry=False)
            if simulated["status"] != order["status"]:
                self._save_order(simulated)
                self._save_fills_for_order(simulated)
                updated += 1
                self.audit_service.record_audit_event(
                    event_type="sim_mark_to_market",
                    payload=simulated,
                    symbol=str(simulated["symbol"]),
                    intent_id=str(simulated.get("intent_id") or ""),
                    source="simulation",
                )
        self._refresh_session_equity(session["session_id"])
        return {"ok": True, "session_id": session["session_id"], "updated_orders": updated}

    def report(
        self,
        *,
        session_id: str | None = None,
        date: str | None = None,
    ) -> dict[str, Any]:
        self._ensure_schema()
        session = self._load_session(session_id) if session_id else self._active_session()
        if not session:
            summary = _empty_summary()
            return {
                "ok": True,
                "session_id": None,
                "validation_status": SIM_OBSERVE,
                "blockers": [
                    "sim_min_trades_missing",
                    "sim_min_days_missing",
                    "sim_min_trading_days_missing",
                    "sim_profit_factor_low",
                ],
                "thresholds": _thresholds(),
                "summary": summary,
                "orders": [],
                "equity_curve": [],
                "daily_metrics": [],
                "generated_at": datetime.now(tz=UTC).isoformat(),
            }
        orders = self._load_orders(session_id=session["session_id"])
        if date:
            orders = [order for order in orders if str(order.get("updated_at", ""))[:10] == date]
        summary = _summary(session, orders)
        validation_status, blockers = _validation_status(
            summary, self.audit_service.validate_audit_chain()
        )
        return {
            "ok": True,
            "session_id": session["session_id"],
            "validation_status": validation_status,
            "blockers": blockers,
            "thresholds": _thresholds(),
            "summary": summary,
            "orders": orders,
            "equity_curve": self._load_equity_curve(session["session_id"]),
            "daily_metrics": self._daily_metrics(session, orders),
            "generated_at": datetime.now(tz=UTC).isoformat(),
        }

    def export_report(
        self,
        *,
        session_id: str | None = None,
        date: str | None = None,
        output_format: str = "json",
    ) -> Any:
        report = self.report(session_id=session_id, date=date)
        if output_format == "markdown":
            return _markdown_report(report)
        return report

    def record_closed_orders(
        self,
        *,
        session_id: str,
        wins: list[float],
        losses: list[float],
        start_date: str,
    ) -> None:
        self._ensure_schema()
        date_start = datetime.fromisoformat(start_date).replace(tzinfo=UTC)
        rows: list[dict[str, Any]] = []
        values = [*wins, *losses]
        for index, pnl in enumerate(values):
            stamp = (date_start + timedelta(days=index % 10, minutes=index)).isoformat()
            rows.append(
                {
                    "order_id": _id(f"ftmo-sim-seed-{index}"),
                    "session_id": session_id,
                    "intent_id": None,
                    "symbol": "AAPL",
                    "side": "LONG",
                    "entry": 100.0,
                    "stop": 99.0,
                    "target": 102.0,
                    "size_units": 100.0,
                    "status": SIM_CLOSED,
                    "entry_fill_price": 100.0,
                    "exit_fill_price": 101.0 if pnl > 0 else 99.0,
                    "exit_reason": "target" if pnl > 0 else "stop",
                    "realized_pnl": float(pnl),
                    "created_at": stamp,
                    "updated_at": stamp,
                }
            )
        for row in rows:
            self._save_order(row)
        self._refresh_session_equity(session_id)

    def _ensure_schema(self) -> None:
        self.predictions_db.parent.mkdir(parents=True, exist_ok=True)
        self.audit_service.ensure_schema()
        self.playbook_service._ensure_schema()
        con = self._connect()
        try:
            con.executescript(
                """
                CREATE TABLE IF NOT EXISTS ftmo_sim_sessions (
                    session_id TEXT PRIMARY KEY,
                    status TEXT NOT NULL,
                    initial_capital REAL NOT NULL,
                    current_equity REAL NOT NULL,
                    started_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    payload_json TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS ftmo_sim_orders (
                    order_id TEXT PRIMARY KEY,
                    session_id TEXT NOT NULL,
                    intent_id TEXT,
                    symbol TEXT NOT NULL,
                    side TEXT NOT NULL,
                    status TEXT NOT NULL,
                    payload_json TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS ftmo_sim_fills (
                    fill_id TEXT PRIMARY KEY,
                    order_id TEXT NOT NULL,
                    session_id TEXT NOT NULL,
                    symbol TEXT NOT NULL,
                    fill_type TEXT NOT NULL,
                    price REAL NOT NULL,
                    size_units REAL NOT NULL,
                    created_at TEXT NOT NULL,
                    payload_json TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS ftmo_sim_equity_curve (
                    point_id TEXT PRIMARY KEY,
                    session_id TEXT NOT NULL,
                    timestamp TEXT NOT NULL,
                    equity REAL NOT NULL,
                    realized_pnl REAL NOT NULL,
                    payload_json TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS ftmo_sim_daily_metrics (
                    metric_id TEXT PRIMARY KEY,
                    session_id TEXT NOT NULL,
                    metric_date TEXT NOT NULL,
                    payload_json TEXT NOT NULL,
                    created_at TEXT NOT NULL
                );
                """
            )
            con.commit()
        finally:
            con.close()

    def _load_intent(self, intent_id: str) -> dict[str, Any] | None:
        if not intent_id:
            return None
        con = self._connect()
        try:
            row = con.execute(
                "SELECT payload_json FROM ftmo_playbook_trade_intents WHERE intent_id = ?",
                (intent_id,),
            ).fetchone()
            return json.loads(str(row[0])) if row else None
        finally:
            con.close()

    def _session_for_order(self, session_id: str) -> dict[str, Any]:
        if session_id:
            existing = self._load_session(session_id)
            if existing:
                return existing
        active = self._active_session()
        return active or self.create_session({})

    def _active_session(self) -> dict[str, Any] | None:
        con = self._connect()
        try:
            row = con.execute(
                """
                SELECT payload_json FROM ftmo_sim_sessions
                ORDER BY started_at DESC LIMIT 1
                """
            ).fetchone()
            return json.loads(str(row[0])) if row else None
        finally:
            con.close()

    def _load_session(self, session_id: str) -> dict[str, Any] | None:
        con = self._connect()
        try:
            row = con.execute(
                "SELECT payload_json FROM ftmo_sim_sessions WHERE session_id = ?",
                (session_id,),
            ).fetchone()
            return json.loads(str(row[0])) if row else None
        finally:
            con.close()

    def _load_market_bars(self, symbol: str) -> list[dict[str, Any]]:
        con = self._connect()
        try:
            tables = {
                str(row[0])
                for row in con.execute("SELECT name FROM sqlite_master WHERE type='table'")
            }
            if "ftmo_provider_snapshots" not in tables:
                return []
            rows = con.execute(
                """
                SELECT timestamp, open, high, low, close, provider, provider_symbol, timeframe
                FROM ftmo_provider_snapshots
                WHERE canonical_symbol = ?
                ORDER BY timestamp ASC
                """,
                (symbol,),
            ).fetchall()
            return [
                {
                    "timestamp": str(row[0]),
                    "open": float(row[1]),
                    "high": float(row[2]),
                    "low": float(row[3]),
                    "close": float(row[4]),
                    "provider": str(row[5]),
                    "provider_symbol": str(row[6]),
                    "timeframe": str(row[7]),
                }
                for row in rows
            ]
        finally:
            con.close()

    def _simulate_order(
        self,
        order: dict[str, Any],
        bars: list[dict[str, Any]],
        *,
        require_entry: bool = True,
    ) -> dict[str, Any]:
        side = str(order["side"])
        entry = float(order["entry"])
        stop = float(order["stop"])
        target = _optional_float(order.get("target"))
        slippage = _slippage_pct(str(order["symbol"]))
        simulated = {**order}
        entry_filled = simulated.get("entry_fill_price") is not None
        for bar in bars:
            high = float(bar["high"])
            low = float(bar["low"])
            if not entry_filled:
                touched_entry = low <= entry <= high
                if not touched_entry:
                    continue
                simulated["entry_fill_price"] = _entry_fill(side, entry, slippage)
                simulated["entry_timestamp"] = bar["timestamp"]
                entry_filled = True
                simulated["status"] = SIM_OPEN
            if entry_filled:
                stop_hit = _stop_hit(side, stop, low, high)
                target_hit = target is not None and _target_hit(side, target, low, high)
                if stop_hit:
                    simulated["exit_fill_price"] = _exit_fill(
                        side, stop, slippage, exit_reason="stop"
                    )
                    simulated["exit_reason"] = "stop"
                elif target_hit and target is not None:
                    simulated["exit_fill_price"] = _exit_fill(
                        side, target, slippage, exit_reason="target"
                    )
                    simulated["exit_reason"] = "target"
                if simulated.get("exit_fill_price") is not None:
                    simulated["exit_timestamp"] = bar["timestamp"]
                    simulated["realized_pnl"] = round(_pnl(simulated), 2)
                    simulated["status"] = SIM_CLOSED
                    break
                if require_entry:
                    break
        simulated["updated_at"] = datetime.now(tz=UTC).isoformat()
        return simulated

    def _save_order(self, order: dict[str, Any]) -> None:
        con = self._connect()
        try:
            con.execute(
                """
                INSERT INTO ftmo_sim_orders (
                    order_id, session_id, intent_id, symbol, side, status,
                    payload_json, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(order_id) DO UPDATE SET
                    status = excluded.status,
                    payload_json = excluded.payload_json,
                    updated_at = excluded.updated_at
                """,
                (
                    order["order_id"],
                    order["session_id"],
                    order.get("intent_id"),
                    order["symbol"],
                    order["side"],
                    order["status"],
                    json.dumps(order, sort_keys=True, default=str),
                    order["created_at"],
                    order["updated_at"],
                ),
            )
            con.commit()
        finally:
            con.close()

    def _save_fills_for_order(self, order: dict[str, Any]) -> None:
        fills: list[dict[str, Any]] = []
        if order.get("entry_fill_price") is not None:
            fills.append(
                {
                    "fill_type": "entry",
                    "price": float(order["entry_fill_price"]),
                    "created_at": order.get("entry_timestamp") or order["updated_at"],
                }
            )
        if order.get("exit_fill_price") is not None:
            fills.append(
                {
                    "fill_type": str(order.get("exit_reason") or "exit"),
                    "price": float(order["exit_fill_price"]),
                    "created_at": order.get("exit_timestamp") or order["updated_at"],
                }
            )
        con = self._connect()
        try:
            for fill in fills:
                payload = {
                    "order_id": order["order_id"],
                    "session_id": order["session_id"],
                    "symbol": order["symbol"],
                    "size_units": order["size_units"],
                    **fill,
                }
                con.execute(
                    """
                    INSERT OR IGNORE INTO ftmo_sim_fills (
                        fill_id, order_id, session_id, symbol, fill_type,
                        price, size_units, created_at, payload_json
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        f"{order['order_id']}:{fill['fill_type']}",
                        order["order_id"],
                        order["session_id"],
                        order["symbol"],
                        fill["fill_type"],
                        fill["price"],
                        order["size_units"],
                        fill["created_at"],
                        json.dumps(payload, sort_keys=True, default=str),
                    ),
                )
            con.commit()
        finally:
            con.close()

    def _load_orders(
        self,
        *,
        session_id: str,
        status: str | None = None,
    ) -> list[dict[str, Any]]:
        clauses = ["session_id = ?"]
        params: list[Any] = [session_id]
        if status:
            clauses.append("status = ?")
            params.append(status)
        con = self._connect()
        try:
            rows = con.execute(
                f"""
                SELECT payload_json FROM ftmo_sim_orders
                WHERE {' AND '.join(clauses)}
                ORDER BY updated_at ASC
                """,
                params,
            ).fetchall()
            return [json.loads(str(row[0])) for row in rows]
        finally:
            con.close()

    def _refresh_session_equity(self, session_id: str) -> None:
        session = self._load_session(session_id)
        if not session:
            return
        orders = self._load_orders(session_id=session_id)
        realized = sum(float(order.get("realized_pnl") or 0.0) for order in orders)
        equity = float(session["initial_capital"]) + realized
        session["current_equity"] = round(equity, 2)
        session["updated_at"] = datetime.now(tz=UTC).isoformat()
        con = self._connect()
        try:
            con.execute(
                """
                UPDATE ftmo_sim_sessions
                SET current_equity = ?, updated_at = ?, payload_json = ?
                WHERE session_id = ?
                """,
                (
                    session["current_equity"],
                    session["updated_at"],
                    json.dumps(session, sort_keys=True, default=str),
                    session_id,
                ),
            )
            point = {
                "point_id": _id("ftmo-sim-equity"),
                "session_id": session_id,
                "timestamp": session["updated_at"],
                "equity": session["current_equity"],
                "realized_pnl": round(realized, 2),
            }
            con.execute(
                """
                INSERT INTO ftmo_sim_equity_curve (
                    point_id, session_id, timestamp, equity, realized_pnl, payload_json
                ) VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    point["point_id"],
                    session_id,
                    point["timestamp"],
                    point["equity"],
                    point["realized_pnl"],
                    json.dumps(point, sort_keys=True),
                ),
            )
            con.commit()
        finally:
            con.close()

    def _load_equity_curve(self, session_id: str) -> list[dict[str, Any]]:
        con = self._connect()
        try:
            rows = con.execute(
                """
                SELECT payload_json FROM ftmo_sim_equity_curve
                WHERE session_id = ? ORDER BY timestamp ASC
                """,
                (session_id,),
            ).fetchall()
            return [json.loads(str(row[0])) for row in rows]
        finally:
            con.close()

    def _daily_metrics(
        self, session: dict[str, Any], orders: list[dict[str, Any]]
    ) -> list[dict[str, Any]]:
        daily: dict[str, float] = {}
        for order in orders:
            if order.get("status") != SIM_CLOSED:
                continue
            key = str(order.get("updated_at") or "")[:10]
            daily[key] = daily.get(key, 0.0) + float(order.get("realized_pnl") or 0.0)
        initial = float(session["initial_capital"])
        daily_limit = initial * DAILY_LOSS_LIMIT_PCT / 100.0
        return [
            {
                "date": date,
                "realized_pnl": round(pnl, 2),
                "daily_loss_usage_pct": round(abs(min(0.0, pnl)) / daily_limit * 100.0, 2),
            }
            for date, pnl in sorted(daily.items())
        ]

    def _connect(self) -> sqlite3.Connection:
        return sqlite3.connect(self.predictions_db)


def _summary(session: dict[str, Any], orders: list[dict[str, Any]]) -> dict[str, Any]:
    closed = [order for order in orders if order.get("status") == SIM_CLOSED]
    open_orders = [order for order in orders if order.get("status") == SIM_OPEN]
    pnls = [float(order.get("realized_pnl") or 0.0) for order in closed]
    wins = [pnl for pnl in pnls if pnl > 0]
    losses = [pnl for pnl in pnls if pnl < 0]
    gross_profit = sum(wins)
    gross_loss = abs(sum(losses))
    equity = float(session["initial_capital"])
    peak = equity
    max_drawdown = 0.0
    daily: dict[str, float] = {}
    for order in closed:
        pnl = float(order.get("realized_pnl") or 0.0)
        equity += pnl
        peak = max(peak, equity)
        max_drawdown = max(max_drawdown, peak - equity)
        day = str(order.get("updated_at") or "")[:10]
        daily[day] = daily.get(day, 0.0) + pnl
    initial = float(session["initial_capital"])
    daily_limit = initial * DAILY_LOSS_LIMIT_PCT / 100.0
    max_loss_limit = initial * MAX_LOSS_LIMIT_PCT / 100.0
    max_daily_usage = max(
        [abs(min(0.0, pnl)) / daily_limit * 100.0 for pnl in daily.values()] or [0.0]
    )
    max_loss_usage = max(0.0, (initial - equity) / max_loss_limit * 100.0)
    return {
        "closed_trades": len(closed),
        "open_trades": len(open_orders),
        "calendar_days": len(daily),
        "trading_days": len([pnl for pnl in daily.values() if pnl >= 0.0]),
        "gross_profit": round(gross_profit, 2),
        "gross_loss": round(gross_loss, 2),
        "net_pnl": round(sum(pnls), 2),
        "profit_factor": round(gross_profit / gross_loss, 4) if gross_loss > 0.0 else None,
        "max_drawdown_pct": round(max_drawdown / initial * 100.0, 4),
        "max_daily_loss_usage_pct": round(max_daily_usage, 2),
        "max_loss_usage_pct": round(max_loss_usage, 2),
        "breach_counts": {
            "daily_loss": sum(1 for pnl in daily.values() if abs(min(0.0, pnl)) >= daily_limit),
            "max_loss": 1 if max_loss_usage >= 100.0 else 0,
        },
        "status_counts": dict(Counter(str(order.get("status")) for order in orders)),
    }


def _empty_summary() -> dict[str, Any]:
    return {
        "closed_trades": 0,
        "open_trades": 0,
        "calendar_days": 0,
        "trading_days": 0,
        "gross_profit": 0.0,
        "gross_loss": 0.0,
        "net_pnl": 0.0,
        "profit_factor": None,
        "max_drawdown_pct": 0.0,
        "max_daily_loss_usage_pct": 0.0,
        "max_loss_usage_pct": 0.0,
        "breach_counts": {"daily_loss": 0, "max_loss": 0},
        "status_counts": {},
    }


def _validation_status(
    summary: dict[str, Any],
    audit_chain: dict[str, Any],
) -> tuple[str, list[str]]:
    blockers: list[str] = []
    if not audit_chain.get("ok"):
        blockers.append("sim_audit_chain_broken")
    if summary["breach_counts"]["daily_loss"]:
        blockers.append("daily_loss_breach")
    if summary["breach_counts"]["max_loss"]:
        blockers.append("max_loss_breach")
    if summary["max_drawdown_pct"] > SIM_MAX_DRAWDOWN_PCT:
        blockers.append("sim_drawdown_exceeded")
    if summary["max_daily_loss_usage_pct"] >= SIM_MAX_DAILY_USAGE_PCT:
        blockers.append("sim_daily_usage_high")
    if summary["max_loss_usage_pct"] >= SIM_MAX_LOSS_USAGE_PCT:
        blockers.append("sim_max_loss_usage_high")
    if blockers:
        return SIM_FAILED, blockers
    if summary["open_trades"] > 0:
        return SIM_OPEN, []
    validation_gaps: list[str] = []
    if summary["closed_trades"] < SIM_MIN_CLOSED_TRADES:
        validation_gaps.append("sim_min_trades_missing")
    if summary["calendar_days"] < SIM_MIN_CALENDAR_DAYS:
        validation_gaps.append("sim_min_days_missing")
    if summary["trading_days"] < SIM_MIN_TRADING_DAYS:
        validation_gaps.append("sim_min_trading_days_missing")
    if (summary["profit_factor"] or 0.0) < SIM_MIN_PROFIT_FACTOR:
        validation_gaps.append("sim_profit_factor_low")
    if validation_gaps:
        return SIM_OBSERVE if summary["closed_trades"] == 0 else SIM_CLOSED, validation_gaps
    return SIM_VALIDATED, []


def _thresholds() -> dict[str, Any]:
    return {
        "min_closed_trades": SIM_MIN_CLOSED_TRADES,
        "min_calendar_days": SIM_MIN_CALENDAR_DAYS,
        "min_trading_days": SIM_MIN_TRADING_DAYS,
        "min_profit_factor": SIM_MIN_PROFIT_FACTOR,
        "max_drawdown_pct": SIM_MAX_DRAWDOWN_PCT,
        "max_daily_loss_usage_pct": SIM_MAX_DAILY_USAGE_PCT,
        "max_loss_usage_pct": SIM_MAX_LOSS_USAGE_PCT,
    }


def _markdown_report(report: dict[str, Any]) -> str:
    lines = [
        "# FTMO Simulation Report",
        "",
        f"- Session: {report.get('session_id')}",
        f"- Status: {report.get('validation_status')}",
        f"- Generated at: {report.get('generated_at')}",
        f"- Closed trades: {report.get('summary', {}).get('closed_trades', 0)}",
        f"- Profit factor: {report.get('summary', {}).get('profit_factor')}",
        f"- Net PnL: {report.get('summary', {}).get('net_pnl')}",
        "",
        "## Blockers",
    ]
    blockers = report.get("blockers") or []
    lines.extend([f"- {blocker}" for blocker in blockers] or ["- none"])
    return "\n".join(lines) + "\n"


def _blocked(blockers: list[str]) -> dict[str, Any]:
    return {"ok": False, "status": SIM_BLOCKED, "blockers": blockers}


def _entry_fill(side: str, entry: float, slippage: float) -> float:
    return round(entry * (1.0 + slippage if side == "LONG" else 1.0 - slippage), 6)


def _exit_fill(side: str, price: float, slippage: float, *, exit_reason: str) -> float:
    if exit_reason == "stop":
        return round(price * (1.0 - slippage if side == "LONG" else 1.0 + slippage), 6)
    return round(price * (1.0 - slippage if side == "LONG" else 1.0 + slippage), 6)


def _stop_hit(side: str, stop: float, low: float, high: float) -> bool:
    return low <= stop if side == "LONG" else high >= stop


def _target_hit(side: str, target: float, low: float, high: float) -> bool:
    return high >= target if side == "LONG" else low <= target


def _pnl(order: dict[str, Any]) -> float:
    entry = float(order["entry_fill_price"])
    exit_price = float(order["exit_fill_price"])
    size = float(order["size_units"])
    if order["side"] == "SHORT":
        return (entry - exit_price) * size
    return (exit_price - entry) * size


def _slippage_pct(symbol: str) -> float:
    if symbol in {"XAUUSD", "XAGUSD", "US100.CASH"}:
        return 0.0005
    return 0.0002


def _id(prefix: str) -> str:
    return f"{prefix}-{datetime.now(tz=UTC).strftime('%Y%m%dT%H%M%S%fZ')}"


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
