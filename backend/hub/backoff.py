from typing import TypeVar, Any
import asyncio
import logging
import random
from collections.abc import Callable, Coroutine
from functools import wraps

logger = logging.getLogger(__name__)

T = TypeVar("T")


def exponential_backoff(
    max_retries: int = 3,
    base_delay_seconds: float = 1.0,
    max_delay_seconds: float = 30.0,
    jitter: bool = True,
) -> Callable[[Callable[..., Coroutine[Any, Any, T]]], Callable[..., Coroutine[Any, Any, T]]]:
    """Decorator to apply exponential backoff to async functions.

    Args:
        max_retries: Maximum number of retry attempts.
        base_delay_seconds: Base multiplier for exponential delay.
        max_delay_seconds: Cap on maximum wait time between retries.
        jitter: If True, adds randomized jitter to prevent thundering herd.
    """

    def decorator(
        func: Callable[..., Coroutine[Any, Any, T]]
    ) -> Callable[..., Coroutine[Any, Any, T]]:
        @wraps(func)
        async def wrapper(*args: Any, **kwargs: Any) -> T:
            for attempt in range(max_retries + 1):
                try:
                    return await func(*args, **kwargs)
                except Exception as exc:
                    if attempt == max_retries:
                        logger.error("Max retries exhausted for %s", func.__name__, exc_info=True)
                        raise

                    delay = min(base_delay_seconds * (2**attempt), max_delay_seconds)
                    if jitter:
                        delay = delay * random.uniform(0.5, 1.5)

                    logger.warning(
                        "Attempt %d/%d failed for %s. Retrying in %.2fs. Error: %s",
                        attempt + 1,
                        max_retries,
                        func.__name__,
                        delay,
                        str(exc),
                    )
                    await asyncio.sleep(delay)
            raise RuntimeError("Unreachable")

        return wrapper

    return decorator
