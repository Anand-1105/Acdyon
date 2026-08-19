"""Source-aware async rate limiter.

Responsibility: Prevent outbound request bursts by enforcing a configurable minimum
interval between consecutive requests to the same source, and by limiting concurrent
in-flight requests per source.

Design principles:
- One RateLimiter instance per source (keyed by source_name outside this class).
  Do NOT create a single global instance shared across all sources.
- Monotonic clock (time.monotonic) — immune to wall-clock jumps.
- asyncio.Lock serializes access to the timestamp state; it is process-local.
- asyncio.Semaphore enforces the concurrency ceiling.
- CancelledError propagates correctly — the semaphore release is in a finally block.
- No global mutable state within this module.

Why separate from RetryPolicy?
- Pacing governs normal steady-state spacing between successful requests.
- Retry backoff governs how long to wait after a failure.
- Combining them would make it impossible to enforce both independently.

Usage:
    config = RateLimitConfig(min_interval_seconds=3.0, max_concurrent=1)
    limiter = RateLimiter(config, source_name="weworkremotely")

    async with limiter:
        response = await transport.get(url)

Testability:
    Inject a mock clock via the `_clock_fn` constructor parameter.
    Inject a mock sleep via the `_sleep_fn` parameter.
    This allows tests to verify interval enforcement without real sleeps.

Future scalability note:
    This implementation provides process-local concurrency safety only.
    A distributed deployment would require a shared counter (e.g. Redis INCR).
    That complexity is not required for the current single-process assessment scope.
"""

from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass, field
from typing import Awaitable, Callable, Optional

from src.infrastructure.config import RateLimitConfig


# Type aliases for injectable functions used in testing.
ClockCallable = Callable[[], float]
SleepCallable = Callable[[float], Awaitable[None]]


async def _default_sleep(seconds: float) -> None:
    await asyncio.sleep(seconds)


def _default_clock() -> float:
    return time.monotonic()


class RateLimiter:
    """Async context-manager rate limiter for a single named source.

    Enforces:
    1. Minimum interval between consecutive requests (steady-state pacing).
    2. Maximum number of simultaneously in-flight requests (concurrency cap).

    Both are configurable per source via RateLimitConfig.

    Example:
        limiter = RateLimiter(
            config=RateLimitConfig(min_interval_seconds=3.0, max_concurrent=1),
            source_name="weworkremotely",
        )

        async with limiter:
            response = await transport.get(url)
    """

    def __init__(
        self,
        config: RateLimitConfig,
        source_name: str,
        *,
        _clock_fn: ClockCallable = _default_clock,
        _sleep_fn: SleepCallable = _default_sleep,
    ) -> None:
        self._config = config
        self._source_name = source_name
        self._clock = _clock_fn
        self._sleep = _sleep_fn

        # _last_request_time tracks when the most-recent request *started*.
        # It is updated under _interval_lock before the actual HTTP call so that
        # a concurrent caller that obtains the lock next will wait appropriately.
        self._last_request_time: Optional[float] = None
        self._interval_lock = asyncio.Lock()
        self._semaphore = asyncio.Semaphore(config.max_concurrent)

    @property
    def source_name(self) -> str:
        """The source this limiter was created for."""
        return self._source_name

    @property
    def min_interval_seconds(self) -> float:
        """The configured minimum request interval for this source."""
        return self._config.min_interval_seconds

    async def __aenter__(self) -> "RateLimiter":
        """Acquire both the interval gate and the concurrency slot."""
        # Step 1: Concurrency gate — blocks if max_concurrent are in flight.
        await self._semaphore.acquire()
        try:
            # Step 2: Interval gate — wait until min_interval has elapsed since
            # the last request. The lock ensures only one caller computes/waits
            # at a time, preventing a burst from multiple simultaneous callers.
            async with self._interval_lock:
                await self._enforce_interval()
        except BaseException:
            # If the interval wait is cancelled or raises, release the semaphore
            # so another caller is not permanently blocked.
            self._semaphore.release()
            raise
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb) -> None:  # type: ignore[override]
        """Release the concurrency slot unconditionally."""
        self._semaphore.release()

    # -----------------------------------------------------------------------
    # Internal helpers
    # -----------------------------------------------------------------------

    async def _enforce_interval(self) -> None:
        """Wait until the minimum interval since the last request has elapsed.

        Called under _interval_lock so at most one caller computes the wait at a time.
        Records the new last-request timestamp *before* releasing the lock so the next
        queued caller will see the updated timestamp and compute its own correct wait.
        """
        import random
        now = self._clock()
        interval = self._config.min_interval_seconds

        # Add a randomized delay (jitter) of up to 50% of the pacing interval
        # to ensure request timing behavior appears non-linear / natural.
        # We only apply jitter when running in production (default functions are active)
        # to preserve test determinism.
        is_test = (self._clock is not _default_clock) or (self._sleep is not _default_sleep)
        jitter = random.uniform(0.0, interval * 0.5) if (interval > 0 and not is_test) else 0.0
        target_interval = interval + jitter

        if self._last_request_time is not None and target_interval > 0:
            elapsed = now - self._last_request_time
            remaining = target_interval - elapsed
            if remaining > 0:
                await self._sleep(remaining)
                now = self._clock()

        # Record the time at which we are *about* to make the next request.
        self._last_request_time = now
