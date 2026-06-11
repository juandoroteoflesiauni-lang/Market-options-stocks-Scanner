"""BingX Audit Store — cycle-level persistence for audit, debug and analytics.

Persists every bot cycle to DuckDB so that:
- Operations can reconstruct exactly what the bot decided and why.
- ML training pipelines can replay ``candidate_analysis`` → ``decision`` pairs.
- Post-trade analytics can correlate ``order_intents`` with ``exchange_responses``.

Design notes
------------
* Injectable ``db_path`` — defaults to ``":memory:"`` so unit tests need no file.
* One connection per call (opened and closed inside each method).  DuckDB
  supports multiple concurrent readers but only one writer; opening per-call
  avoids holding a write lock between FastAPI requests.
* The ``payload`` column holds the complete audit record as a JSON string.
  This future-proofs the schema: new fields are added to the JSON without
  ALTER TABLE migrations.
* Secrets are never stored.  The caller is responsible for not including
  raw credential values in the data passed to ``persist()``.
"""

from __future__ import annotations

import json
import uuid
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import duckdb

from backend.config.logger_setup import get_logger

logger = get_logger(__name__)

# Table name is stable — do not rename without a migration.
_TABLE = "bingx_audit_cycles"

_DDL = f"""
CREATE TABLE IF NOT EXISTS {_TABLE} (
    cycle_id    VARCHAR PRIMARY KEY,
    started_at  VARCHAR NOT NULL,
    finished_at VARCHAR NOT NULL,
    dry_run     BOOLEAN NOT NULL,
    universe    VARCHAR NOT NULL,
    payload     VARCHAR NOT NULL,
    created_at  VARCHAR NOT NULL
)
"""


# ─── Audit entry ─────────────────────────────────────────────────────────────
@dataclass
class BingXAuditEntry:
    """Complete record of one bot cycle.

    ``cycle_id`` is auto-generated when empty (empty string).
    All optional blocks default to ``None`` — the caller populates whatever
    stages were executed.  Fields that are ``None`` are omitted from the
    persisted JSON to keep rows compact.
    """

    started_at: str
    finished_at: str
    dry_run: bool
    universe: list[str]
    # Core cycle output (from BingXCycleResult)
    snapshots: list[dict[str, Any]] = field(default_factory=list)
    signals: list[dict[str, Any]] = field(default_factory=list)
    decisions: list[dict[str, Any]] = field(default_factory=list)
    plans: list[dict[str, Any]] = field(default_factory=list)
    executions: list[dict[str, Any]] = field(default_factory=list)
    # Extended blocks (populated when available)
    candidate_analyses: list[dict[str, Any]] | None = None
    engine_decisions: list[dict[str, Any]] | None = None
    risk_decisions: list[dict[str, Any]] | None = None
    order_intents: list[dict[str, Any]] | None = None
    exchange_responses: list[dict[str, Any]] | None = None
    # Generated fields
    cycle_id: str = field(default_factory=lambda: _new_cycle_id())

    @classmethod
    def from_cycle_result(cls: type[BingXAuditEntry], result: Any) -> BingXAuditEntry:
        """Build an entry from a ``BingXCycleResult`` instance."""
        d = result.to_dict()
        return cls(
            started_at=d["started_at"],
            finished_at=d["finished_at"],
            dry_run=d["dry_run"],
            universe=list(d.get("universe", [])),
            snapshots=d.get("snapshots", []),
            signals=d.get("signals", []),
            decisions=d.get("decisions", []),
            plans=d.get("plans", []),
            executions=d.get("executions", []),
            candidate_analyses=d.get("candidate_analyses") or d.get("analyses"),
            engine_decisions=d.get("engine_decisions"),
            risk_decisions=d.get("risk_decisions"),
            order_intents=d.get("order_intents"),
            exchange_responses=d.get("executions"),
        )

    def to_payload(self) -> dict[str, Any]:
        """Return the complete audit dict, omitting None-valued optional blocks."""
        payload: dict[str, Any] = {
            "cycle_id": self.cycle_id,
            "started_at": self.started_at,
            "finished_at": self.finished_at,
            "dry_run": self.dry_run,
            "universe": self.universe,
            "snapshots": self.snapshots,
            "signals": self.signals,
            "decisions": self.decisions,
            "plans": self.plans,
            "executions": self.executions,
        }
        for block in (
            "candidate_analyses",
            "engine_decisions",
            "risk_decisions",
            "order_intents",
            "exchange_responses",
        ):
            value = getattr(self, block)
            if value is not None:
                payload[block] = value
        return payload


def _new_cycle_id() -> str:
    ts = datetime.now(UTC).strftime("%Y%m%dT%H%M%S")
    return f"{ts}_{uuid.uuid4().hex[:8]}"


# ─── Store ────────────────────────────────────────────────────────────────────
class BingXAuditStore:
    """Thin DuckDB-backed audit store.

    Parameters
    ----------
    db_path:
        Path to the DuckDB database file, or ``":memory:"`` for tests.

    Design notes
    ------------
    * In-memory stores (``db_path=":memory:"``) keep a single persistent
      connection because each ``duckdb.connect(":memory:")`` call creates a
      fresh, isolated database — per-call connections would lose the schema.
    * File-backed stores open a fresh connection per call to avoid holding a
      write lock across requests in a multi-worker environment.
    """

    def __init__(self, db_path: str | Path = ":memory:") -> None:
        self._db_path = str(db_path)
        self._in_memory = self._db_path == ":memory:"
        if not self._in_memory:
            Path(self._db_path).parent.mkdir(parents=True, exist_ok=True)
        # Persistent connection for in-memory mode only.
        self._mem_conn: duckdb.DuckDBPyConnection | None = (
            duckdb.connect(":memory:") if self._in_memory else None
        )
        self._ensure_schema()

    @property
    def db_path(self) -> str:
        return self._db_path

    @property
    def is_persistent(self) -> bool:
        return not self._in_memory

    # ── Public API ────────────────────────────────────────────────────────────

    def persist(self, entry: BingXAuditEntry) -> str:
        """Insert ``entry`` into the store.  Returns the ``cycle_id``."""
        payload_json = json.dumps(entry.to_payload(), default=str)
        universe_json = json.dumps(entry.universe)
        created_at = datetime.now(UTC).isoformat()

        with self._connect() as con:
            con.execute(
                f"""
                INSERT OR REPLACE INTO {_TABLE}
                    (cycle_id, started_at, finished_at, dry_run,
                     universe, payload, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    entry.cycle_id,
                    entry.started_at,
                    entry.finished_at,
                    entry.dry_run,
                    universe_json,
                    payload_json,
                    created_at,
                ),
            )

        logger.info(
            "audit_store.persisted cycle_id=%s dry_run=%s",
            entry.cycle_id,
            entry.dry_run,
        )
        return entry.cycle_id

    def list_cycles(self, *, limit: int = 50) -> list[dict[str, Any]]:
        """Return the most recent ``limit`` cycles, newest first."""
        limit = max(1, min(int(limit), 500))
        with self._connect() as con:
            rows = con.execute(
                f"""
                SELECT cycle_id, started_at, finished_at, dry_run, universe, created_at
                FROM {_TABLE}
                ORDER BY started_at DESC
                LIMIT ?
                """,
                (limit,),
            ).fetchall()
        return [
            {
                "cycle_id": r[0],
                "started_at": r[1],
                "finished_at": r[2],
                "dry_run": bool(r[3]),
                "universe": json.loads(r[4]),
                "created_at": r[5],
            }
            for r in rows
        ]

    def list_operations(self, *, limit: int = 100) -> list[dict[str, Any]]:
        """Return a flattened operation ledger, newest first.

        The cycle payload remains the source of truth.  This view extracts one
        row per exchange execution; when a cycle was blocked before execution,
        it records one row per decision so paper-mode learning still captures
        why the bot did not trade.
        """
        limit = max(1, min(int(limit), 500))
        with self._connect() as con:
            rows = con.execute(
                f"""
                SELECT payload
                FROM {_TABLE}
                ORDER BY started_at DESC
                LIMIT 500
                """
            ).fetchall()

        operations: list[dict[str, Any]] = []
        for row in rows:
            payload = json.loads(row[0])
            operations.extend(_operations_from_payload(payload))
            if len(operations) >= limit:
                return operations[:limit]
        return operations[:limit]

    def get_cycle(self, cycle_id: str) -> dict[str, Any] | None:
        """Return the full audit payload for ``cycle_id``, or ``None``."""
        with self._connect() as con:
            rows = con.execute(
                f"SELECT payload FROM {_TABLE} WHERE cycle_id = ?",
                (cycle_id,),
            ).fetchall()
        if not rows:
            return None
        return json.loads(rows[0][0])

    def count(self) -> int:
        """Return the total number of persisted cycles."""
        with self._connect() as con:
            rows = con.execute(f"SELECT COUNT(*) FROM {_TABLE}").fetchall()
        return int(rows[0][0])

    # ── Private helpers ───────────────────────────────────────────────────────

    @contextmanager
    def _connect(self) -> Any:
        """Yield an active DuckDB connection.

        In-memory stores reuse the persistent ``_mem_conn`` and never close it.
        File-backed stores open and close a fresh connection on every call.
        """
        if self._in_memory:
            assert self._mem_conn is not None
            yield self._mem_conn
        else:
            con = duckdb.connect(self._db_path)
            try:
                yield con
            finally:
                con.close()

    def _ensure_schema(self) -> None:
        with self._connect() as con:
            con.execute(_DDL)


def _operations_from_payload(payload: dict[str, Any]) -> list[dict[str, Any]]:
    executions = _as_dict_list(payload.get("executions"))
    decisions = _as_dict_list(payload.get("decisions"))
    risk_decisions = _as_dict_list(payload.get("risk_decisions"))
    order_intents = _as_dict_list(payload.get("order_intents"))
    plans = _as_dict_list(payload.get("plans"))

    decisions_by_symbol = _by_symbol(decisions)
    risk_by_symbol = _by_symbol(risk_decisions)
    intents_by_symbol = _by_symbol(order_intents)
    plans_by_symbol = _by_symbol(plans)

    if executions:
        return [
            _operation_row(
                payload,
                event_type="execution",
                index=index,
                execution=execution,
                decision=decisions_by_symbol.get(_symbol_from(execution), {}),
                risk_decision=risk_by_symbol.get(_symbol_from(execution), {}),
                order_intent=intents_by_symbol.get(_symbol_from(execution), {}),
                plan=plans_by_symbol.get(_symbol_from(execution), {}),
            )
            for index, execution in enumerate(executions)
        ]

    symbols = _ordered_symbols(decisions, risk_decisions, order_intents, plans)
    return [
        _operation_row(
            payload,
            event_type="decision",
            index=index,
            execution={},
            decision=decisions_by_symbol.get(symbol, {}),
            risk_decision=risk_by_symbol.get(symbol, {}),
            order_intent=intents_by_symbol.get(symbol, {}),
            plan=plans_by_symbol.get(symbol, {}),
            fallback_symbol=symbol,
        )
        for index, symbol in enumerate(symbols)
    ]


def _operation_row(
    payload: dict[str, Any],
    *,
    event_type: str,
    index: int,
    execution: dict[str, Any],
    decision: dict[str, Any],
    risk_decision: dict[str, Any],
    order_intent: dict[str, Any],
    plan: dict[str, Any],
    fallback_symbol: str | None = None,
) -> dict[str, Any]:
    symbol = (
        _symbol_from(execution)
        or _symbol_from(order_intent)
        or _symbol_from(risk_decision)
        or _symbol_from(decision)
        or _symbol_from(plan)
        or fallback_symbol
        or "UNKNOWN"
    )
    notional = _first_number(
        execution,
        risk_decision,
        order_intent,
        plan,
        keys=("notional_usdt", "requested_quote_qty", "quote_order_qty", "quote_quantity"),
    )
    realized_pnl = _first_number(
        execution,
        risk_decision,
        keys=("realized_pnl_usdt", "realized_pnl", "pnl_usdt", "profit_loss_usdt"),
    )
    pnl_pct = None
    if realized_pnl is not None and notional and notional > 0:
        pnl_pct = round((realized_pnl / notional) * 100.0, 6)

    return {
        "operation_id": f"{payload.get('cycle_id', 'cycle')}:{symbol}:{event_type}:{index}",
        "cycle_id": payload.get("cycle_id"),
        "event_type": event_type,
        "started_at": payload.get("started_at"),
        "finished_at": payload.get("finished_at"),
        "dry_run": bool(execution.get("dry_run", payload.get("dry_run", True))),
        "symbol": symbol,
        "side": _first_text(execution, order_intent, plan, decision, keys=("side", "direction")),
        "suitability": decision.get("suitability") or decision.get("decision"),
        "probability": _first_number(decision, keys=("probability", "confidence", "score")),
        "authorized": _first_bool(risk_decision, plan, keys=("authorized", "approved", "allowed")),
        "execution_ok": _first_bool(execution, keys=("ok", "success")) if execution else None,
        "order_type": _first_text(execution, order_intent, plan, keys=("order_type", "type")),
        "quantity": _first_number(
            execution,
            order_intent,
            plan,
            keys=("quantity", "qty", "requested_qty", "base_quantity"),
        ),
        "notional_usdt": notional,
        "reference_price": _first_number(
            execution,
            order_intent,
            plan,
            keys=("reference_price", "price", "entry_price", "current_price"),
        ),
        "venue_order_id": execution.get("venue_order_id") or execution.get("order_id"),
        "client_order_id": execution.get("client_order_id") or order_intent.get("client_order_id"),
        "realized_pnl_usdt": realized_pnl,
        "pnl_pct": pnl_pct,
        "reason_codes": _reason_codes(decision, risk_decision, order_intent, plan, execution),
        "error": execution.get("error") or risk_decision.get("error") or plan.get("error"),
    }


def _as_dict_list(value: Any) -> list[dict[str, Any]]:
    if not isinstance(value, list):
        return []
    return [item for item in value if isinstance(item, dict)]


def _symbol_from(item: dict[str, Any]) -> str | None:
    value = item.get("symbol") or item.get("venue_symbol")
    return str(value) if value else None


def _by_symbol(items: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    out: dict[str, dict[str, Any]] = {}
    for item in items:
        symbol = _symbol_from(item)
        if symbol and symbol not in out:
            out[symbol] = item
    return out


def _ordered_symbols(*groups: list[dict[str, Any]]) -> list[str]:
    symbols: list[str] = []
    seen: set[str] = set()
    for group in groups:
        for item in group:
            symbol = _symbol_from(item)
            if symbol and symbol not in seen:
                seen.add(symbol)
                symbols.append(symbol)
    return symbols


def _first_number(*items: dict[str, Any], keys: tuple[str, ...]) -> float | None:
    for item in items:
        for key in keys:
            value = item.get(key)
            if value is None or value == "":
                continue
            try:
                return float(value)
            except (TypeError, ValueError):
                continue
    return None


def _first_text(*items: dict[str, Any], keys: tuple[str, ...]) -> str | None:
    for item in items:
        for key in keys:
            value = item.get(key)
            if value is not None and str(value).strip():
                return str(value)
    return None


def _first_bool(*items: dict[str, Any], keys: tuple[str, ...]) -> bool | None:
    for item in items:
        for key in keys:
            value = item.get(key)
            if isinstance(value, bool):
                return value
    return None


def _reason_codes(*items: dict[str, Any]) -> list[str]:
    out: list[str] = []
    seen: set[str] = set()
    for item in items:
        raw = item.get("reason_codes") or item.get("reasons") or item.get("motives")
        values = raw if isinstance(raw, list) else [raw] if raw else []
        for value in values:
            text = str(value).strip()
            if text and text not in seen:
                seen.add(text)
                out.append(text)
    return out
