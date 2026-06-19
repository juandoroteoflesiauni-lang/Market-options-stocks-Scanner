"""Shared macro risk state updated by agentic risk manager. # [TH]"""

from __future__ import annotations

import threading
from dataclasses import dataclass
from datetime import UTC, datetime

from backend.domain.agentic_models import MacroRiskResult, Severity


@dataclass(frozen=True)
class AgenticMacroState:
    """Thread-safe snapshot of latest macro risk agent output."""

    severity: Severity = "NONE"
    halt_scanner: bool = False
    stop_loss_multiplier: float = 1.0
    degraded: bool = True
    updated_at: datetime | None = None


_lock = threading.Lock()
_state = AgenticMacroState()


def get_agentic_macro_state() -> AgenticMacroState:
    """Return the latest macro risk state."""
    with _lock:
        return _state


def update_agentic_macro_state(result: MacroRiskResult) -> AgenticMacroState:
    """Update macro state from a risk manager result."""
    global _state
    assessment = result.assessment
    new_state = AgenticMacroState(
        severity=assessment.severity,
        halt_scanner=assessment.halt_scanner,
        stop_loss_multiplier=assessment.stop_loss_multiplier,
        degraded=result.envelope.degraded,
        updated_at=datetime.now(tz=UTC),
    )
    with _lock:
        _state = new_state
    return new_state


def reset_agentic_macro_state() -> None:
    """Reset macro state (for tests)."""
    global _state
    with _lock:
        _state = AgenticMacroState()


__all__ = [
    "AgenticMacroState",
    "get_agentic_macro_state",
    "reset_agentic_macro_state",
    "update_agentic_macro_state",
]
