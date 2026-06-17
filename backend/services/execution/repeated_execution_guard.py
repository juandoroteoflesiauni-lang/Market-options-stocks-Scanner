"""Límite de ejecuciones repetidas por símbolo/sesión (estándar FIA). # [TH]"""

from __future__ import annotations

from datetime import UTC, datetime

REASON_REPEATED_EXECUTION = "execution_repeated_limit_exceeded"


class SessionRepeatedExecutionGuard:
    """Singleton: cuenta entradas exitosas por símbolo en la sesión UTC."""

    _instance: SessionRepeatedExecutionGuard | None = None

    def __init__(self) -> None:
        self._counts: dict[str, int] = {}
        self._session_date: str | None = None

    @classmethod
    def instance(cls) -> SessionRepeatedExecutionGuard:
        if cls._instance is None:
            cls._instance = cls()
        return cls._instance

    @classmethod
    def reset_instance(cls) -> None:
        cls._instance = None

    def _roll_session_if_needed(self) -> None:
        today = datetime.now(UTC).strftime("%Y-%m-%d")
        if self._session_date != today:
            self._counts.clear()
            self._session_date = today

    def can_execute_entry(self, symbol: str, *, max_per_symbol: int, enabled: bool = True) -> bool:
        """True si el símbolo no superó el límite de entradas en la sesión."""
        if not enabled or max_per_symbol <= 0:
            return True
        self._roll_session_if_needed()
        return self._counts.get(symbol.upper(), 0) < max_per_symbol

    def record_entry_fill(self, symbol: str) -> int:
        """Incrementa contador tras fill de entrada confirmado."""
        self._roll_session_if_needed()
        key = symbol.upper()
        self._counts[key] = self._counts.get(key, 0) + 1
        return self._counts[key]

    def entry_count(self, symbol: str) -> int:
        self._roll_session_if_needed()
        return self._counts.get(symbol.upper(), 0)


__all__ = ["REASON_REPEATED_EXECUTION", "SessionRepeatedExecutionGuard"]
