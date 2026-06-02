import time
import logging
from enum import StrEnum

logger = logging.getLogger(__name__)


class CircuitState(StrEnum):
    CLOSED = "CLOSED"
    OPEN = "OPEN"
    HALF_OPEN = "HALF_OPEN"


class CircuitBreaker:
    """Circuit Breaker to prevent cascading failures to external APIs.
    
    States:
        CLOSED: Normal operation, calls go through.
        OPEN: Failing, calls are blocked and return Result.failure().
        HALF-OPEN: Testing if provider has recovered.
    """
    
    def __init__(
        self,
        failure_threshold: int = 5,
        recovery_timeout_seconds: float = 60.0,
        provider_name: str = "unknown",
    ) -> None:
        self.failure_threshold = failure_threshold
        self.recovery_timeout_seconds = recovery_timeout_seconds
        self.provider_name = provider_name
        
        self.state = CircuitState.CLOSED
        self._failure_count = 0
        self._last_failure_time = 0.0

    def record_failure(self) -> None:
        """Record a failure and potentially trip the circuit to OPEN."""
        self._failure_count += 1
        self._last_failure_time = time.time()
        
        if self.state == CircuitState.CLOSED and self._failure_count >= self.failure_threshold:
            self.state = CircuitState.OPEN
            logger.error(
                "CircuitBreaker [%s]: OPEN - Exceeded failure threshold (%d)",
                self.provider_name,
                self.failure_threshold,
            )
        elif self.state == CircuitState.HALF_OPEN:
            self.state = CircuitState.OPEN
            logger.error(
                "CircuitBreaker [%s]: OPEN - Probe failed, circuit remains open",
                self.provider_name,
            )

    def record_success(self) -> None:
        """Record a success and potentially reset the circuit to CLOSED."""
        if self.state != CircuitState.CLOSED:
            logger.info("CircuitBreaker [%s]: CLOSED - Provider recovered", self.provider_name)
        self.state = CircuitState.CLOSED
        self._failure_count = 0
        self._last_failure_time = 0.0

    def can_execute(self) -> bool:
        """Determine whether a request should be allowed through.
        
        Returns:
            True if call is allowed (CLOSED or HALF-OPEN probe).
            False if call should be blocked (OPEN).
        """
        if self.state == CircuitState.CLOSED:
            return True
            
        if self.state == CircuitState.OPEN:
            if time.time() - self._last_failure_time >= self.recovery_timeout_seconds:
                self.state = CircuitState.HALF_OPEN
                logger.warning(
                    "CircuitBreaker [%s]: HALF_OPEN - Attempting probe",
                    self.provider_name,
                )
                return True
            return False
            
        return True
