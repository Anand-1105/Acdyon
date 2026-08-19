"""Unit tests for RateLimiter.

Uses injected monotonic clock and sleep functions to verify interval enforcement
and concurrency guarantees without real waiting.

Coverage:
- Sequential requests respect min_interval_seconds.
- First request never waits.
- Zero interval means no pacing.
- Concurrent requests are serialized by the interval lock.
- max_concurrent limits in-flight requests.
- Cancellation while waiting releases the semaphore.
- Independent limiters for different source names do not interfere.
"""

from __future__ import annotations

import asyncio
from typing import List

import pytest

from src.infrastructure.config import RateLimitConfig
from src.infrastructure.reliability.limiter import RateLimiter


class FakeClock:
    """Monotonic clock stub with manual time advance."""

    def __init__(self, initial: float = 0.0):
        self.now = initial

    def __call__(self) -> float:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += seconds


class RecordingSleepStub:
    """Async sleep stub that records calls and optionally advances a FakeClock."""

    def __init__(self, clock: FakeClock):
        self._clock = clock
        self.calls: List[float] = []

    async def __call__(self, seconds: float) -> None:
        self.calls.append(seconds)
        self._clock.advance(seconds)


@pytest.mark.asyncio
class TestRateLimiterInterval:
    async def test_first_request_does_not_wait(self):
        clock = FakeClock(initial=0.0)
        sleep = RecordingSleepStub(clock)
        cfg = RateLimitConfig(min_interval_seconds=3.0, max_concurrent=1)
        limiter = RateLimiter(cfg, source_name="src", _clock_fn=clock, _sleep_fn=sleep)

        async with limiter:
            pass  # first request — should not sleep

        assert sleep.calls == []

    async def test_second_request_waits_full_interval_when_no_time_elapsed(self):
        clock = FakeClock(initial=0.0)
        sleep = RecordingSleepStub(clock)
        cfg = RateLimitConfig(min_interval_seconds=3.0, max_concurrent=1)
        limiter = RateLimiter(cfg, source_name="src", _clock_fn=clock, _sleep_fn=sleep)

        async with limiter:
            pass
        # Time does not advance between the two calls (no real I/O in tests)
        async with limiter:
            pass

        # The second request should have slept ~3 seconds
        assert len(sleep.calls) == 1
        assert sleep.calls[0] == pytest.approx(3.0)

    async def test_no_wait_when_interval_already_elapsed(self):
        clock = FakeClock(initial=0.0)
        sleep = RecordingSleepStub(clock)
        cfg = RateLimitConfig(min_interval_seconds=3.0, max_concurrent=1)
        limiter = RateLimiter(cfg, source_name="src", _clock_fn=clock, _sleep_fn=sleep)

        async with limiter:
            pass
        clock.advance(10.0)  # simulate 10s of real elapsed time
        async with limiter:
            pass

        assert sleep.calls == []  # no wait needed

    async def test_partial_interval_elapsed_waits_remainder(self):
        clock = FakeClock(initial=0.0)
        sleep = RecordingSleepStub(clock)
        cfg = RateLimitConfig(min_interval_seconds=5.0, max_concurrent=1)
        limiter = RateLimiter(cfg, source_name="src", _clock_fn=clock, _sleep_fn=sleep)

        async with limiter:
            pass
        clock.advance(2.0)  # only 2s elapsed of required 5s
        async with limiter:
            pass

        assert len(sleep.calls) == 1
        assert sleep.calls[0] == pytest.approx(3.0)

    async def test_zero_interval_never_sleeps(self):
        clock = FakeClock(initial=0.0)
        sleep = RecordingSleepStub(clock)
        cfg = RateLimitConfig(min_interval_seconds=0.0, max_concurrent=1)
        limiter = RateLimiter(cfg, source_name="src", _clock_fn=clock, _sleep_fn=sleep)

        async with limiter:
            pass
        async with limiter:
            pass

        assert sleep.calls == []


@pytest.mark.asyncio
class TestRateLimiterConcurrency:
    async def test_max_concurrent_1_serializes_concurrent_callers(self):
        """With max_concurrent=1, two simultaneous callers must queue."""
        results: List[str] = []
        cfg = RateLimitConfig(min_interval_seconds=0.0, max_concurrent=1)
        limiter = RateLimiter(cfg, source_name="src")

        async def caller(name: str, delay: float):
            async with limiter:
                results.append(f"start:{name}")
                await asyncio.sleep(delay)
                results.append(f"end:{name}")

        # Run both concurrently; they should NOT interleave (one runs to completion first)
        await asyncio.gather(
            caller("A", 0.01),
            caller("B", 0.01),
        )

        # Each caller must have an uninterrupted start→end block
        a_start = results.index("start:A")
        a_end = results.index("end:A")
        b_start = results.index("start:B")
        b_end = results.index("end:B")
        # Either A runs fully before B starts, or B runs fully before A starts
        assert (a_end < b_start) or (b_end < a_start)

    async def test_max_concurrent_2_allows_two_simultaneous(self):
        """With max_concurrent=2, two concurrent callers can enter simultaneously."""
        concurrency_counter = [0]
        max_observed = [0]
        cfg = RateLimitConfig(min_interval_seconds=0.0, max_concurrent=2)
        limiter = RateLimiter(cfg, source_name="src")

        async def caller():
            async with limiter:
                concurrency_counter[0] += 1
                max_observed[0] = max(max_observed[0], concurrency_counter[0])
                await asyncio.sleep(0.02)
                concurrency_counter[0] -= 1

        await asyncio.gather(*[caller() for _ in range(4)])
        # At some point two callers were simultaneously inside
        assert max_observed[0] >= 2

    async def test_cancellation_releases_semaphore(self):
        """If a caller is cancelled while waiting, the semaphore must be released
        so subsequent callers are not permanently blocked."""
        cfg = RateLimitConfig(min_interval_seconds=5.0, max_concurrent=1)
        # Real limiter with real sleeps so cancellation actually interrupts the wait
        limiter = RateLimiter(cfg, source_name="src")

        # First caller acquires the limiter
        acquired = asyncio.Event()
        release_signal = asyncio.Event()

        async def first_caller():
            async with limiter:
                acquired.set()
                await release_signal.wait()

        task1 = asyncio.create_task(first_caller())
        await acquired.wait()  # ensure first caller is inside

        # Second caller tries to acquire with a pacing sleep — cancel it
        async def second_caller():
            async with limiter:
                pass

        task2 = asyncio.create_task(second_caller())
        await asyncio.sleep(0)  # let task2 start and block on semaphore
        task2.cancel()
        try:
            await task2
        except asyncio.CancelledError:
            pass

        # Let first caller finish
        release_signal.set()
        await task1

        # Now a third caller should not deadlock
        async def third_caller():
            async with limiter:
                pass

        # Allow time for pacing if needed
        await asyncio.wait_for(third_caller(), timeout=10.0)


@pytest.mark.asyncio
class TestRateLimiterIsolation:
    async def test_independent_sources_do_not_share_state(self):
        """Two limiters for different sources must not affect each other."""
        clock_a = FakeClock(initial=0.0)
        sleep_a = RecordingSleepStub(clock_a)
        clock_b = FakeClock(initial=0.0)
        sleep_b = RecordingSleepStub(clock_b)

        cfg = RateLimitConfig(min_interval_seconds=5.0, max_concurrent=1)
        limiter_a = RateLimiter(cfg, source_name="source_a", _clock_fn=clock_a, _sleep_fn=sleep_a)
        limiter_b = RateLimiter(cfg, source_name="source_b", _clock_fn=clock_b, _sleep_fn=sleep_b)

        async with limiter_a:
            pass
        async with limiter_a:
            pass  # limiter_a second request sleeps

        async with limiter_b:
            pass  # limiter_b first request — should NOT sleep

        assert len(sleep_b.calls) == 0, "limiter_b was affected by limiter_a state"

    async def test_source_name_stored(self):
        cfg = RateLimitConfig()
        limiter = RateLimiter(cfg, source_name="weworkremotely")
        assert limiter.source_name == "weworkremotely"
        assert limiter.min_interval_seconds == cfg.min_interval_seconds
