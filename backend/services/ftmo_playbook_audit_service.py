from __future__ import annotations
from typing import Any
"""Tamper-evident audit ledger for the FTMO playbook.

The audit service is local and read-only toward brokers. It records the manual
playbook evidence needed to reconstruct operational decisions.
"""


import hashlib
import json
import sqlite3
from collections import Counter
from datetime import UTC, datetime
from pathlib import Path

from backend.config.logger_setup import get_logger
from backend.services.funding_lab_service import DEFAULT_PREDICTIONS_DB

logger = get_logger(__name__)

AUDIT_REPORT_DIR = Path("backend/reports/funding-lab/playbook-audit")
REDACTED = "[REDACTED]"
SENSITIVE_KEYS = {
    "api_key",
    "apikey",
    "secret",
    "token",
    "password",
    "cookie",
    "authorization",
    "auth",
}


class FTMOPlaybookAuditService:
    """Append-only audit events with a simple hash chain."""

    def __init__(self, *, predictions_db: str | Path = DEFAULT_PREDICTIONS_DB) -> None:
        self.predictions_db = Path(predictions_db)

    def ensure_schema(self) -> None:
        self.predictions_db.parent.mkdir(parents=True, exist_ok=True)
        con = self._connect()
        try:
            con.executescript(
                """
                CREATE TABLE IF NOT EXISTS ftmo_playbook_audit_events (
                    sequence_id INTEGER PRIMARY KEY AUTOINCREMENT,
                    event_id TEXT NOT NULL UNIQUE,
                    event_type TEXT NOT NULL,
                    occurred_at TEXT NOT NULL,
                    source TEXT NOT NULL,
                    operator_id TEXT,
                    symbol TEXT,
                    intent_id TEXT,
                    journal_id TEXT,
                    parent_event_id TEXT,
                    payload_json TEXT NOT NULL,
                    payload_hash TEXT NOT NULL,
                    previous_hash TEXT,
                    event_hash TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_ftmo_playbook_audit_occurred_at
                    ON ftmo_playbook_audit_events(occurred_at);
                CREATE INDEX IF NOT EXISTS idx_ftmo_playbook_audit_symbol
                    ON ftmo_playbook_audit_events(symbol);
                CREATE INDEX IF NOT EXISTS idx_ftmo_playbook_audit_type
                    ON ftmo_playbook_audit_events(event_type);
                """
            )
            con.commit()
        finally:
            con.close()

    def payload_hash(self, payload: Any) -> str:
        return _sha256(_canonical_json(_redact(payload)))

    def record_audit_event(
        self,
        *,
        event_type: str,
        payload: dict[str, Any],
        symbol: str | None = None,
        intent_id: str | None = None,
        journal_id: str | None = None,
        parent_event_id: str | None = None,
        source: str = "playbook",
        operator_id: str = "manual",
    ) -> dict[str, Any]:
        self.ensure_schema()
        occurred_at = datetime.now(tz=UTC).isoformat()
        payload_json = _redact(payload)
        payload_hash = self.payload_hash(payload_json)
        previous_hash = self._latest_event_hash()
        event_id = _id("ftmo-audit")
        event_hash = self._event_hash(
            event_id=event_id,
            event_type=event_type,
            occurred_at=occurred_at,
            source=source,
            operator_id=operator_id,
            symbol=symbol,
            intent_id=intent_id,
            journal_id=journal_id,
            parent_event_id=parent_event_id,
            payload_hash=payload_hash,
            previous_hash=previous_hash,
        )
        con = self._connect()
        try:
            con.execute(
                """
                INSERT INTO ftmo_playbook_audit_events (
                    event_id, event_type, occurred_at, source, operator_id, symbol,
                    intent_id, journal_id, parent_event_id, payload_json, payload_hash,
                    previous_hash, event_hash
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    event_id,
                    event_type,
                    occurred_at,
                    source,
                    operator_id,
                    symbol,
                    intent_id,
                    journal_id,
                    parent_event_id,
                    _canonical_json(payload_json),
                    payload_hash,
                    previous_hash,
                    event_hash,
                ),
            )
            con.commit()
        finally:
            con.close()
        return {
            "event_id": event_id,
            "event_type": event_type,
            "occurred_at": occurred_at,
            "source": source,
            "operator_id": operator_id,
            "symbol": symbol,
            "intent_id": intent_id,
            "journal_id": journal_id,
            "parent_event_id": parent_event_id,
            "payload_json": payload_json,
            "payload_hash": payload_hash,
            "previous_hash": previous_hash,
            "event_hash": event_hash,
        }

    def validate_audit_chain(self) -> dict[str, Any]:
        self.ensure_schema()
        previous_hash: str | None = None
        invalid: list[str] = []
        events = self._load_events()
        for event in events:
            expected_payload_hash = self.payload_hash(event["payload_json"])
            expected_event_hash = self._event_hash(
                event_id=event["event_id"],
                event_type=event["event_type"],
                occurred_at=event["occurred_at"],
                source=event["source"],
                operator_id=event.get("operator_id"),
                symbol=event.get("symbol"),
                intent_id=event.get("intent_id"),
                journal_id=event.get("journal_id"),
                parent_event_id=event.get("parent_event_id"),
                payload_hash=event["payload_hash"],
                previous_hash=event.get("previous_hash"),
            )
            if event.get("previous_hash") != previous_hash:
                invalid.append(event["event_id"])
            if event["payload_hash"] != expected_payload_hash:
                invalid.append(event["event_id"])
            if event["event_hash"] != expected_event_hash:
                invalid.append(event["event_id"])
            previous_hash = event["event_hash"]
        invalid = _dedupe(invalid)
        return {
            "ok": not invalid,
            "checked_events": len(events),
            "first_invalid_event_id": invalid[0] if invalid else None,
            "invalid_event_ids": invalid,
        }

    def build_audit_report(
        self,
        *,
        date: str | None = None,
        symbol: str | None = None,
        event_type: str | None = None,
    ) -> dict[str, Any]:
        self.ensure_schema()
        events = self._load_events(date=date, symbol=symbol, event_type=event_type)
        event_counts = dict(Counter(str(event["event_type"]) for event in events))
        reconciliation_counts = dict(
            Counter(
                str(event["payload_json"].get("reconciliation_status"))
                for event in events
                if event["payload_json"].get("reconciliation_status")
            )
        )
        return {
            "ok": True,
            "date": date,
            "symbol": symbol,
            "event_type": event_type,
            "summary": {
                "total_events": len(events),
                "event_type_counts": event_counts,
                "reconciliation_counts": reconciliation_counts,
            },
            "hash_chain": self.validate_audit_chain(),
            "events": events,
            "generated_at": datetime.now(tz=UTC).isoformat(),
        }

    def render_markdown_report(self, report: dict[str, Any]) -> str:
        chain = report.get("hash_chain") if isinstance(report.get("hash_chain"), dict) else {}
        lines = [
            "# FTMO Playbook Audit Report",
            "",
            f"- Generated at: {report.get('generated_at', '--')}",
            f"- Date: {report.get('date') or 'all'}",
            f"- Symbol: {report.get('symbol') or 'all'}",
            f"- Event type: {report.get('event_type') or 'all'}",
            f"- Hash chain: {'OK' if chain.get('ok') else 'BROKEN'}",
            f"- Events: {report.get('summary', {}).get('total_events', 0)}",
            "",
            "## Events",
        ]
        for event in report.get("events") or []:
            lines.append(
                "- "
                f"{event.get('occurred_at')} | {event.get('event_type')} | "
                f"{event.get('symbol') or '--'} | {event.get('event_id')}"
            )
        return "\n".join(lines) + "\n"

    def _load_events(
        self,
        *,
        date: str | None = None,
        symbol: str | None = None,
        event_type: str | None = None,
    ) -> list[dict[str, Any]]:
        clauses: list[str] = []
        params: list[Any] = []
        if date:
            clauses.append("substr(occurred_at, 1, 10) = ?")
            params.append(date)
        if symbol:
            clauses.append("symbol = ?")
            params.append(symbol.upper())
        if event_type:
            clauses.append("event_type = ?")
            params.append(event_type)
        where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
        con = self._connect()
        try:
            rows = con.execute(
                f"""
                SELECT sequence_id, event_id, event_type, occurred_at, source,
                       operator_id, symbol, intent_id, journal_id, parent_event_id,
                       payload_json, payload_hash, previous_hash, event_hash
                FROM ftmo_playbook_audit_events
                {where}
                ORDER BY sequence_id ASC
                """,
                params,
            ).fetchall()
        finally:
            con.close()
        return [
            {
                "sequence_id": int(row[0]),
                "event_id": str(row[1]),
                "event_type": str(row[2]),
                "occurred_at": str(row[3]),
                "source": str(row[4]),
                "operator_id": row[5],
                "symbol": row[6],
                "intent_id": row[7],
                "journal_id": row[8],
                "parent_event_id": row[9],
                "payload_json": json.loads(str(row[10])),
                "payload_hash": str(row[11]),
                "previous_hash": row[12],
                "event_hash": str(row[13]),
            }
            for row in rows
        ]

    def _latest_event_hash(self) -> str | None:
        con = self._connect()
        try:
            row = con.execute(
                """
                SELECT event_hash FROM ftmo_playbook_audit_events
                ORDER BY sequence_id DESC LIMIT 1
                """
            ).fetchone()
            return str(row[0]) if row else None
        finally:
            con.close()

    def _event_hash(
        self,
        *,
        event_id: str,
        event_type: str,
        occurred_at: str,
        source: str,
        operator_id: str | None,
        symbol: str | None,
        intent_id: str | None,
        journal_id: str | None,
        parent_event_id: str | None,
        payload_hash: str,
        previous_hash: str | None,
    ) -> str:
        return _sha256(
            _canonical_json(
                {
                    "event_id": event_id,
                    "event_type": event_type,
                    "occurred_at": occurred_at,
                    "source": source,
                    "operator_id": operator_id,
                    "symbol": symbol,
                    "intent_id": intent_id,
                    "journal_id": journal_id,
                    "parent_event_id": parent_event_id,
                    "payload_hash": payload_hash,
                    "previous_hash": previous_hash,
                }
            )
        )

    def _connect(self) -> sqlite3.Connection:
        return sqlite3.connect(self.predictions_db)


def _redact(value: Any) -> Any:
    if isinstance(value, dict):
        redacted: dict[str, Any] = {}
        for key, item in value.items():
            normalized = str(key).lower().replace("-", "_")
            redacted[str(key)] = REDACTED if normalized in SENSITIVE_KEYS else _redact(item)
        return redacted
    if isinstance(value, list):
        return [_redact(item) for item in value]
    return value


def _canonical_json(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), default=str)


def _sha256(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


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
