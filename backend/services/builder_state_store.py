"""SQLite persistence for Builder account state and payout cycles."""

from __future__ import annotations

import json
import sqlite3
import uuid
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path

from backend.domain.builder_models import (
    BuilderAccountState,
    BuilderDailyPnl,
    BuilderPayoutCycleRecord,
    mffu_builder_50k_profile,
)
from backend.services.builder_state_machine import default_builder_account_state
from backend.services.funding_lab_service import DEFAULT_PREDICTIONS_DB


class BuilderStateStore:
    """Persist Builder phases and payout cycles in predictions.db."""

    def __init__(self, predictions_db: str | Path = DEFAULT_PREDICTIONS_DB) -> None:
        self.predictions_db = Path(predictions_db)

    def ensure_schema(self) -> None:
        """Create Builder tables idempotently."""
        self.predictions_db.parent.mkdir(parents=True, exist_ok=True)
        connection = self._connect()
        try:
            connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS builder_state (
                    account_id TEXT PRIMARY KEY,
                    payload_json TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS builder_payout_cycles (
                    cycle_id TEXT PRIMARY KEY,
                    account_id TEXT NOT NULL,
                    cycle_number INTEGER NOT NULL,
                    status TEXT NOT NULL,
                    buffer_target TEXT NOT NULL,
                    buffer_progress TEXT NOT NULL DEFAULT '0',
                    qualified_days_count INTEGER NOT NULL DEFAULT 0,
                    withdrawable_amount TEXT NOT NULL DEFAULT '0',
                    payload_json TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_builder_payout_cycles_account
                    ON builder_payout_cycles(account_id);
                CREATE TABLE IF NOT EXISTS builder_daily_pnl (
                    account_id TEXT NOT NULL,
                    trade_date TEXT NOT NULL,
                    pnl TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    PRIMARY KEY (account_id, trade_date)
                );
                CREATE INDEX IF NOT EXISTS idx_builder_daily_pnl_account
                    ON builder_daily_pnl(account_id);
                """
            )
            connection.commit()
        finally:
            connection.close()

    # Class-level cache for fast, non-blocking reads and shared access across instances
    _state_cache: dict[str, BuilderAccountState] = {}

    def load_state(self, account_id: str = "default") -> BuilderAccountState:
        """Load Builder account state, seeding defaults when missing (uses cache)."""
        if account_id in self._state_cache:
            return self._state_cache[account_id]

        state = self._load_state_from_db(account_id)
        self._state_cache[account_id] = state
        return state

    def _load_state_from_db(self, account_id: str) -> BuilderAccountState:
        self.ensure_schema()
        connection = self._connect()
        try:
            row = connection.execute(
                "SELECT payload_json FROM builder_state WHERE account_id = ?",
                (account_id,),
            ).fetchone()
            if row is None:
                seeded = default_builder_account_state(account_id=account_id)
                self._save_state_to_db(seeded, connection=connection)
                return seeded
            payload = json.loads(str(row[0]))
            if not isinstance(payload, dict):
                payload = {}
            payload["account_id"] = account_id
            return BuilderAccountState.model_validate(payload)
        finally:
            connection.close()

    def save_state(
        self,
        state: BuilderAccountState,
        *,
        connection: sqlite3.Connection | None = None,
    ) -> None:
        """Upsert the Builder account state payload (cached, async DB write)."""
        # Update cache immediately
        self._state_cache[state.account_id] = state

        # If a connection is passed, we write to db synchronously.
        # Otherwise, if there is an active event loop, we write asynchronously in the background.
        if connection is not None:
            self._save_state_to_db(state, connection=connection)
        else:
            try:
                import asyncio
                loop = asyncio.get_running_loop()
                loop.run_in_executor(None, self._save_state_to_db, state)
            except RuntimeError:
                # Fallback to synchronous database write when outside an async loop
                self._save_state_to_db(state)

    def _save_state_to_db(
        self,
        state: BuilderAccountState,
        *,
        connection: sqlite3.Connection | None = None,
    ) -> None:
        self.ensure_schema()
        updated_at = datetime.now(tz=UTC).isoformat()
        owns_connection = connection is None
        connection = connection or self._connect()
        try:
            connection.execute(
                """
                INSERT INTO builder_state (account_id, payload_json, updated_at)
                VALUES (?, ?, ?)
                ON CONFLICT(account_id) DO UPDATE SET
                    payload_json = excluded.payload_json,
                    updated_at = excluded.updated_at
                """,
                (
                    state.account_id,
                    state.model_dump_json(),
                    updated_at,
                ),
            )
            connection.commit()
        finally:
            if owns_connection:
                connection.close()

    def list_payout_cycles(self, account_id: str = "default") -> list[BuilderPayoutCycleRecord]:
        """Return payout cycles for an account ordered by cycle number."""
        self.ensure_schema()
        connection = self._connect()
        try:
            rows = connection.execute(
                """
                SELECT cycle_id, account_id, cycle_number, status,
                       buffer_target, buffer_progress, qualified_days_count,
                       withdrawable_amount
                FROM builder_payout_cycles
                WHERE account_id = ?
                ORDER BY cycle_number ASC
                """,
                (account_id,),
            ).fetchall()
            return [_row_to_payout_cycle(row) for row in rows]
        finally:
            connection.close()

    def save_payout_cycle(
        self,
        cycle: BuilderPayoutCycleRecord,
        *,
        connection: sqlite3.Connection | None = None,
    ) -> BuilderPayoutCycleRecord:
        """Insert or update a payout cycle row."""
        self.ensure_schema()
        now = datetime.now(tz=UTC).isoformat()
        owns_connection = connection is None
        connection = connection or self._connect()
        try:
            payload = cycle.model_dump(mode="json")
            connection.execute(
                """
                INSERT INTO builder_payout_cycles (
                    cycle_id, account_id, cycle_number, status,
                    buffer_target, buffer_progress, qualified_days_count,
                    withdrawable_amount, payload_json, created_at, updated_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(cycle_id) DO UPDATE SET
                    status = excluded.status,
                    buffer_target = excluded.buffer_target,
                    buffer_progress = excluded.buffer_progress,
                    qualified_days_count = excluded.qualified_days_count,
                    withdrawable_amount = excluded.withdrawable_amount,
                    payload_json = excluded.payload_json,
                    updated_at = excluded.updated_at
                """,
                (
                    cycle.cycle_id,
                    cycle.account_id,
                    cycle.cycle_number,
                    cycle.status,
                    str(cycle.buffer_target),
                    str(cycle.buffer_progress),
                    cycle.qualified_days_count,
                    str(cycle.withdrawable_amount),
                    json.dumps(payload, sort_keys=True),
                    now,
                    now,
                ),
            )
            connection.commit()
        finally:
            if owns_connection:
                connection.close()
        return cycle

    def create_payout_cycle(
        self,
        account_id: str = "default",
        *,
        buffer_target: str | None = None,
    ) -> BuilderPayoutCycleRecord:
        """Create the next open payout cycle for an account."""
        profile = mffu_builder_50k_profile()
        existing = self.list_payout_cycles(account_id)
        cycle_number = len(existing) + 1
        cycle = BuilderPayoutCycleRecord(
            cycle_id=str(uuid.uuid4()),
            account_id=account_id,
            cycle_number=cycle_number,
            buffer_target=buffer_target or profile.payout_buffer,
        )
        return self.save_payout_cycle(cycle)

    def record_daily_pnl(
        self,
        trade_date: str,
        pnl: Decimal,
        *,
        account_id: str = "default",
    ) -> BuilderDailyPnl:
        """Upsert a single trading-day PnL entry (one row per calendar day)."""
        self.ensure_schema()
        now = datetime.now(tz=UTC).isoformat()
        connection = self._connect()
        try:
            connection.execute(
                """
                INSERT INTO builder_daily_pnl (account_id, trade_date, pnl, updated_at)
                VALUES (?, ?, ?, ?)
                ON CONFLICT(account_id, trade_date) DO UPDATE SET
                    pnl = excluded.pnl,
                    updated_at = excluded.updated_at
                """,
                (account_id, trade_date, str(pnl), now),
            )
            connection.commit()
        finally:
            connection.close()
        return BuilderDailyPnl(date=trade_date, pnl=pnl)

    def list_daily_pnls(self, account_id: str = "default") -> list[BuilderDailyPnl]:
        """Return persisted daily PnL entries ordered by date ascending."""
        self.ensure_schema()
        connection = self._connect()
        try:
            rows = connection.execute(
                """
                SELECT trade_date, pnl
                FROM builder_daily_pnl
                WHERE account_id = ?
                ORDER BY trade_date ASC
                """,
                (account_id,),
            ).fetchall()
            return [
                BuilderDailyPnl(date=str(row["trade_date"]), pnl=Decimal(str(row["pnl"])))
                for row in rows
            ]
        finally:
            connection.close()

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(str(self.predictions_db))
        connection.row_factory = sqlite3.Row
        return connection


def _row_to_payout_cycle(row: sqlite3.Row) -> BuilderPayoutCycleRecord:
    return BuilderPayoutCycleRecord(
        cycle_id=str(row["cycle_id"]),
        account_id=str(row["account_id"]),
        cycle_number=int(row["cycle_number"]),
        status=row["status"],  # type: ignore[arg-type]
        buffer_target=Decimal(str(row["buffer_target"])),
        buffer_progress=Decimal(str(row["buffer_progress"])),
        qualified_days_count=int(row["qualified_days_count"]),
        withdrawable_amount=Decimal(str(row["withdrawable_amount"])),
    )
