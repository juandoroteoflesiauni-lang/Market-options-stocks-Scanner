from __future__ import annotations

from typing import Generic, TypeVar

from pydantic import BaseModel, ConfigDict

T = TypeVar("T")


class Result(BaseModel, Generic[T]):
    """Type-safe wrapper for all Hub return values.
    
    Enforces explicit error handling at every call site.
    The Hub never raises raw exceptions — it returns Result objects.
    
    Usage:
        result = await hub.fetch_snapshot("AAPL")
        if result.is_failure:
            logger.warning("Failed: %s", result.reason)
            return
        snapshot = result.unwrap()
    """
    model_config = ConfigDict(frozen=True)

    _value: T | None = None
    _reason: str | None = None
    is_success: bool

    @classmethod
    def success(cls, value: T) -> "Result[T]":
        instance = cls(is_success=True)
        object.__setattr__(instance, "_value", value)
        return instance

    @classmethod
    def failure(cls, reason: str) -> "Result[T]":
        instance = cls(is_success=False)
        object.__setattr__(instance, "_reason", reason)
        return instance

    @property
    def is_failure(self) -> bool:
        return not self.is_success

    @property
    def reason(self) -> str:
        if self._reason is None:
            raise RuntimeError("Cannot access reason on a successful Result.")
        return self._reason

    def unwrap(self) -> T:
        """Returns the value. Raises RuntimeError if called on a failure."""
        if not self.is_success or self._value is None:
            raise RuntimeError(
                f"Cannot unwrap a failed Result. Reason: {self._reason}"
            )
        return self._value
