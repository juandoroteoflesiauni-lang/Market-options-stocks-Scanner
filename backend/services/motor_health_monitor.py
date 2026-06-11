"""motor_health_monitor.py
==========================
Circuit-breaker layer for every motor in the probabilistic pipeline.

Pattern: CLOSED → OPEN → HALF_OPEN → CLOSED

  CLOSED    : normal operation, all calls go through.
  OPEN      : motor failing, calls are rejected fast (no latency impact).
              Stays OPEN for `recovery_timeout` seconds.
  HALF_OPEN : after recovery_timeout, allow probe calls. After
              `success_threshold` consecutive successes the breaker closes.
              Any failure in HALF_OPEN reopens immediately.

Public API
----------
- CircuitState                          enum
- MotorHealth                           dataclass
- MotorCircuitBreaker                   class
- with_circuit_breaker(motor_name, breaker) decorator
- check_system_health(breaker)          alerting helper
"""

from __future__ import annotations

import functools
import threading
import time
from collections import deque
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from concurrent.futures import TimeoutError as FuturesTimeoutError
from dataclasses import dataclass, field
from enum import Enum
from typing import Any

from backend.config.logger_setup import get_logger

logger = get_logger(__name__)


# ---------------------------------------------------------------------------
# CircuitState
# ---------------------------------------------------------------------------


class CircuitState(str, Enum):
    CLOSED = "CLOSED"
    OPEN = "OPEN"
    HALF_OPEN = "HALF_OPEN"


# ---------------------------------------------------------------------------
# Defaults / constants
# ---------------------------------------------------------------------------

_DEFAULT_FAILURE_THRESHOLD = 3
_DEFAULT_RECOVERY_TIMEOUT = 60  # seconds
_DEFAULT_SUCCESS_THRESHOLD = 2
_DEFAULT_CALL_TIMEOUT_S = 2.0
_ROLLING_WINDOW_S = 5 * 60  # 5 min for error_rate_5min

CRITICAL_MOTORS: tuple[str, ...] = ("gamma_flip", "tail_risk")
_OPEN_MOTORS_ALERT_THRESHOLD = 3


# ---------------------------------------------------------------------------
# MotorHealth
# ---------------------------------------------------------------------------


@dataclass
class MotorHealth:
    motor_name: str
    state: CircuitState = CircuitState.CLOSED
    failure_count: int = 0
    success_count: int = 0
    last_failure_ts: float | None = None
    last_success_ts: float | None = None
    avg_latency_ms: float = 0.0
    error_rate_5min: float = 0.0
    last_cached_value: Any | None = None
    last_cached_at: float | None = None


# ---------------------------------------------------------------------------
# Internal per-motor state container (richer than MotorHealth — keeps history).
# ---------------------------------------------------------------------------


@dataclass
class _MotorState:
    motor_name: str
    state: CircuitState = CircuitState.CLOSED
    consecutive_failures: int = 0
    consecutive_half_open_wins: int = 0
    failure_count: int = 0
    success_count: int = 0
    last_failure_ts: float | None = None
    last_success_ts: float | None = None
    opened_at: float | None = None
    latencies_ms: deque[float] = field(default_factory=lambda: deque(maxlen=200))
    # rolling event log for error_rate_5min: (ts, ok_bool)
    events: deque[tuple[float, bool]] = field(default_factory=lambda: deque(maxlen=10_000))
    last_cached_value: Any | None = None
    last_cached_at: float | None = None


# ---------------------------------------------------------------------------
# MotorCircuitBreaker
# ---------------------------------------------------------------------------


class MotorCircuitBreaker:
    """
    Per-motor circuit breaker registry. Threadsafe via per-motor lock.
    """

    def __init__(
        self,
        failure_threshold: int = _DEFAULT_FAILURE_THRESHOLD,
        recovery_timeout: int = _DEFAULT_RECOVERY_TIMEOUT,
        success_threshold: int = _DEFAULT_SUCCESS_THRESHOLD,
    ) -> None:
        self.failure_threshold = int(failure_threshold)
        self.recovery_timeout = int(recovery_timeout)
        self.success_threshold = int(success_threshold)

        self._motors: dict[str, _MotorState] = {}
        self._lock = threading.RLock()

    # ── State helpers ───────────────────────────────────────────────────────

    def _state_for(self, motor_name: str) -> _MotorState:
        with self._lock:
            st = self._motors.get(motor_name)
            if st is None:
                st = _MotorState(motor_name=motor_name)
                self._motors[motor_name] = st
            return st

    def _maybe_transition_to_half_open(self, st: _MotorState) -> None:
        """If OPEN and recovery_timeout elapsed → HALF_OPEN."""
        if st.state == CircuitState.OPEN and st.opened_at is not None:
            if time.time() - st.opened_at >= self.recovery_timeout:
                st.state = CircuitState.HALF_OPEN
                st.consecutive_half_open_wins = 0
                logger.info("circuit_breaker.half_open motor=%s", st.motor_name)

    def _trim_events(self, st: _MotorState, now: float) -> None:
        """Drop events older than the rolling window."""
        cutoff = now - _ROLLING_WINDOW_S
        while st.events and st.events[0][0] < cutoff:
            st.events.popleft()

    def _compute_health_snapshot(self, st: _MotorState) -> MotorHealth:
        now = time.time()
        self._trim_events(st, now)
        total = len(st.events)
        errors = sum(1 for _, ok in st.events if not ok)
        error_rate = (errors / total) if total > 0 else 0.0
        avg_lat = float(sum(st.latencies_ms) / len(st.latencies_ms)) if st.latencies_ms else 0.0
        return MotorHealth(
            motor_name=st.motor_name,
            state=st.state,
            failure_count=st.failure_count,
            success_count=st.success_count,
            last_failure_ts=st.last_failure_ts,
            last_success_ts=st.last_success_ts,
            avg_latency_ms=avg_lat,
            error_rate_5min=error_rate,
            last_cached_value=st.last_cached_value,
            last_cached_at=st.last_cached_at,
        )

    # ── Public bookkeeping API ──────────────────────────────────────────────

    def record_success(self, motor_name: str, latency_ms: float) -> None:
        with self._lock:
            st = self._state_for(motor_name)
            now = time.time()
            st.success_count += 1
            st.last_success_ts = now
            st.consecutive_failures = 0
            st.latencies_ms.append(float(latency_ms))
            st.events.append((now, True))

            if st.state == CircuitState.HALF_OPEN:
                st.consecutive_half_open_wins += 1
                if st.consecutive_half_open_wins >= self.success_threshold:
                    st.state = CircuitState.CLOSED
                    st.consecutive_half_open_wins = 0
                    st.opened_at = None
                    logger.info("circuit_breaker.closed motor=%s", motor_name)
            elif st.state == CircuitState.OPEN:
                # Manual record_success while OPEN moves us to HALF_OPEN territory
                st.state = CircuitState.HALF_OPEN
                st.consecutive_half_open_wins = 1

    def record_failure(self, motor_name: str, error: str | Exception) -> None:
        with self._lock:
            st = self._state_for(motor_name)
            now = time.time()
            st.failure_count += 1
            st.last_failure_ts = now
            st.consecutive_failures += 1
            st.events.append((now, False))

            if st.state == CircuitState.HALF_OPEN:
                # Any failure in HALF_OPEN reopens the breaker immediately.
                st.state = CircuitState.OPEN
                st.opened_at = now
                st.consecutive_half_open_wins = 0
                logger.warning("circuit_breaker.reopened motor=%s error=%s", motor_name, error)
            elif st.state == CircuitState.CLOSED:
                if st.consecutive_failures >= self.failure_threshold:
                    st.state = CircuitState.OPEN
                    st.opened_at = now
                    logger.warning("circuit_breaker.opened motor=%s error=%s", motor_name, error)

    def get_health(self, motor_name: str) -> MotorHealth:
        with self._lock:
            st = self._state_for(motor_name)
            self._maybe_transition_to_half_open(st)
            return self._compute_health_snapshot(st)

    def get_all_health(self) -> dict[str, MotorHealth]:
        with self._lock:
            for st in self._motors.values():
                self._maybe_transition_to_half_open(st)
            return {name: self._compute_health_snapshot(st) for name, st in self._motors.items()}

    def reset(self, motor_name: str) -> None:
        with self._lock:
            self._motors[motor_name] = _MotorState(motor_name=motor_name)

    def reset_all(self) -> None:
        with self._lock:
            self._motors.clear()

    def cache_last_value(self, motor_name: str, value: Any) -> None:
        """Stash a successful return so callers can use it while OPEN."""
        with self._lock:
            st = self._state_for(motor_name)
            st.last_cached_value = value
            st.last_cached_at = time.time()

    # ── Call orchestration ──────────────────────────────────────────────────

    def is_callable(self, motor_name: str) -> bool:
        """True if the breaker permits a call (CLOSED or HALF_OPEN probe)."""
        with self._lock:
            st = self._state_for(motor_name)
            self._maybe_transition_to_half_open(st)
            return st.state != CircuitState.OPEN

    def call(
        self,
        motor_fn: Callable[..., Any],
        *args: Any,
        motor_name: str | None = None,
        timeout: float = _DEFAULT_CALL_TIMEOUT_S,
        **kwargs: Any,
    ) -> Any:
        """
        Execute `motor_fn(*args, **kwargs)` under the breaker.

        Behaviour:
          OPEN      → raises CircuitBreakerOpenError without calling motor_fn.
          CLOSED    → calls; records success/failure; opens after threshold.
          HALF_OPEN → calls; on success counts toward closing, on failure reopens.

        `timeout` triggers a hard cancellation (treated as failure).
        """
        name = motor_name or getattr(motor_fn, "__name__", "anonymous_motor")

        if not self.is_callable(name):
            raise CircuitBreakerOpenError(f"motor '{name}' circuit is OPEN — call rejected")

        t0 = time.perf_counter()
        try:
            with ThreadPoolExecutor(max_workers=1) as ex:
                future = ex.submit(motor_fn, *args, **kwargs)
                try:
                    result = future.result(timeout=timeout)
                except FuturesTimeoutError as exc:
                    future.cancel()
                    elapsed_ms = (time.perf_counter() - t0) * 1000
                    self.record_failure(name, f"timeout>{timeout:.1f}s")
                    raise MotorCallTimeout(
                        f"motor '{name}' timed out after {timeout:.1f}s"
                    ) from exc
        except CircuitBreakerOpenError:
            raise
        except MotorCallTimeout:
            raise
        except Exception as exc:
            elapsed_ms = (time.perf_counter() - t0) * 1000
            self.record_failure(name, exc)
            raise

        elapsed_ms = (time.perf_counter() - t0) * 1000
        self.record_success(name, elapsed_ms)
        if result is not None:
            self.cache_last_value(name, result)
        return result


# ---------------------------------------------------------------------------
# Custom errors
# ---------------------------------------------------------------------------


class CircuitBreakerOpenError(RuntimeError):
    """Raised when a call is attempted on a motor whose breaker is OPEN."""


class MotorCallTimeout(RuntimeError):
    """Raised when a motor call exceeds its timeout."""


# ---------------------------------------------------------------------------
# Decorator
# ---------------------------------------------------------------------------


def with_circuit_breaker(
    motor_name: str,
    breaker: MotorCircuitBreaker,
    timeout: float = _DEFAULT_CALL_TIMEOUT_S,
) -> Callable:
    """
    Wrap a motor callable so every invocation is governed by `breaker`.

    Usage:
        @with_circuit_breaker("tail_risk", breaker)
        def my_motor(...): ...
    """

    def _decorator(fn: Callable[..., Any]) -> Callable[..., Any]:
        @functools.wraps(fn)
        def _wrap(*args: Any, **kwargs: Any) -> Any:
            return breaker.call(fn, *args, motor_name=motor_name, timeout=timeout, **kwargs)

        return _wrap

    return _decorator


# ---------------------------------------------------------------------------
# System-wide health alerts
# ---------------------------------------------------------------------------


def check_system_health(breaker: MotorCircuitBreaker) -> list[str]:
    """
    Return a list of human-readable alerts about systemic motor health.

    Triggers:
      · > 3 motors simultaneously OPEN.
      · Any critical motor (gamma_flip, tail_risk) OPEN.
      · Any motor with error_rate_5min > 0.50.
    """
    alerts: list[str] = []
    health = breaker.get_all_health()

    open_motors = [name for name, h in health.items() if h.state == CircuitState.OPEN]
    if len(open_motors) > _OPEN_MOTORS_ALERT_THRESHOLD:
        alerts.append(
            f"systemic: {len(open_motors)} motors OPEN "
            f"(threshold {_OPEN_MOTORS_ALERT_THRESHOLD}): {sorted(open_motors)}"
        )

    for crit in CRITICAL_MOTORS:
        h = health.get(crit)
        if h is not None and h.state == CircuitState.OPEN:
            alerts.append(f"critical motor OPEN: {crit}")

    for name, h in health.items():
        if h.error_rate_5min > 0.50:
            alerts.append(f"high error rate: {name} = {h.error_rate_5min:.0%} (5min)")

    return alerts
