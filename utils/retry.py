"""Exponential backoff retry helper.

Provides a decorator that retries a callable with exponential backoff plus
jitter, and also a small helper to compute retry delays. Used primarily for
the Gemini API calls (FR-08 / NFR Reliability).
"""

import functools
import random
import time
from typing import Callable, Optional, Tuple, TypeVar

T = TypeVar("T")


def sleep_with_jitter(base_seconds: float, attempt: int, cap: float = 30.0) -> None:
    """Sleep with exponential backoff and jitter for the given attempt.

    ``attempt`` is 0-indexed, so the first retry waits ``base * 2**0``
    seconds, the second waits ``base * 2**1``, and so on.
    """
    delay = min(cap, base_seconds * (2 ** attempt))
    jitter = random.uniform(0, delay * 0.3)
    time.sleep(delay + jitter)


def retry(
    exceptions: Tuple[type[Exception], ...] = (Exception,),
    tries: int = 3,
    base_delay: float = 1.0,
    cap: float = 30.0,
    logger: Optional[object] = None,
) -> Callable[[Callable[..., T]], Callable[..., T]]:
    """Decorator that retries a function on the given exceptions.

    Args:
        exceptions: exception types that trigger a retry.
        tries: total number of attempts (including the first).
        base_delay: base delay in seconds for the first backoff.
        cap: maximum delay in seconds.
        logger: optional logger used to emit WARNING records on retries.
    """
    if tries < 1:
        raise ValueError("tries must be >= 1")

    def decorator(func: Callable[..., T]) -> Callable[..., T]:
        @functools.wraps(func)
        def wrapper(*args, **kwargs) -> T:
            last_exc: Exception | None = None
            for attempt in range(tries):
                try:
                    return func(*args, **kwargs)
                except exceptions as exc:
                    last_exc = exc
                    if attempt == tries - 1:
                        break
                    if logger is not None:
                        logger.warning(
                            "Retrying %s after error: %s (attempt %d/%d)",
                            func.__name__,
                            exc,
                            attempt + 2,
                            tries,
                        )
                    sleep_with_jitter(base_delay, attempt, cap)
            assert last_exc is not None
            raise last_exc

        return wrapper

    return decorator
