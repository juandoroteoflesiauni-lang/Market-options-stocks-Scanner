"""Event-sourced immutable journal for Alpaca R1/R2 decisions. # [PD-3][IM][TH]"""

from __future__ import annotations

import hashlib
import json
import threading
from datetime import UTC, datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from backend.config.logger_setup import get_logger

logger = get_logger(__name__)

EventType = Literal[
    "risk_decision",
    "pre_trade_gate",
    "order_sent",
    "fill",
    "r1_signal",
    "r2_score",
    "cycle_start",
    "cycle_end",
]


class AlpacaEvent(BaseModel):
    """Immutable event with monotonic sequence number."""

    model_config = ConfigDict(frozen=True)

    seq: int
    event_type: EventType
    timestamp: str
    cycle_id: str
    symbol: str = ""
    payload: dict[str, Any] = Field(default_factory=dict)
    state_hash: str = ""


class AlpacaEventJournal:
    """Single-writer event journal with deterministic state hashing."""

    _instance: AlpacaEventJournal | None = None
    _lock = threading.Lock()

    def __init__(self) -> None:
        self._seq = 0
        self._events: list[AlpacaEvent] = []
        self._state_digest = hashlib.sha256(b"alpaca_journal_init").hexdigest()

    @classmethod
    def instance(cls) -> AlpacaEventJournal:
        if cls._instance is None:
            with cls._lock:
                if cls._instance is None:
                    cls._instance = cls()
        return cls._instance

    @classmethod
    def reset_instance(cls) -> None:
        with cls._lock:
            cls._instance = None

    @property
    def state_hash(self) -> str:
        return self._state_digest

    @property
    def event_count(self) -> int:
        return len(self._events)

    def append(
        self,
        event_type: EventType,
        *,
        cycle_id: str,
        symbol: str = "",
        payload: dict[str, Any] | None = None,
    ) -> AlpacaEvent:
        """Append immutable event; updates rolling state hash."""
        with self._lock:
            self._seq += 1
            body = payload or {}
            canonical = json.dumps(body, sort_keys=True, default=str)
            self._state_digest = hashlib.sha256(
                f"{self._state_digest}:{self._seq}:{event_type}:{canonical}".encode()
            ).hexdigest()
            event = AlpacaEvent(
                seq=self._seq,
                event_type=event_type,
                timestamp=datetime.now(UTC).isoformat(),
                cycle_id=cycle_id,
                symbol=symbol,
                payload=body,
                state_hash=self._state_digest,
            )
            self._events.append(event)
            return event

    def events_for_cycle(self, cycle_id: str) -> tuple[AlpacaEvent, ...]:
        return tuple(e for e in self._events if e.cycle_id == cycle_id)

    def replay_verify(self, other_hash: str) -> bool:
        """Verify state hash matches expected (deterministic replay check)."""
        return self._state_digest == other_hash

    def export_glass_box(self, cycle_id: str | None = None) -> list[dict[str, Any]]:
        """Export audit trail for regulatory / EOD review."""
        events = self._events if cycle_id is None else list(self.events_for_cycle(cycle_id))
        return [e.model_dump(mode="json") for e in events]

    def clear(self) -> None:
        with self._lock:
            self._seq = 0
            self._events.clear()
            self._state_digest = hashlib.sha256(b"alpaca_journal_init").hexdigest()


__all__ = ["AlpacaEvent", "AlpacaEventJournal", "EventType"]
