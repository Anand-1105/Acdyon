"""Retry and exponential-backoff policy for the ingestion transport layer.

This module is source-independent. It knows:
- Which HTTP status codes are retryable.
- How to compute a bounded, jittered backoff delay.
- How to respect a Retry-After header within safe bounds.
- When to stop retrying.

This module does NOT know:
- What a job is.
- What WWR is.
- How to parse XML.
- How to write to a database.

Design decisions:
- Sleep is injected via a callable so tests can verify behavior without real waits.
- RetryState is a mutable accumulator — callers update it after each attempt.
- RetryPolicy is stateless and immutable (frozen dataclass).
- CancelledError is ALWAYS re-raised; it must never be treated as a retryable error.
"""

from __future__ import annotations

import asyncio
import math
import random
from dataclasses import dataclass, field
from enum import Enum
from typing import Awaitable, Callable, Optional

from src.infrastructure.config import RetryConfig
from src.infrastructure.http.response import HttpStatusClass, is_retryable_status


# ---------------------------------------------------------------------------
# Non-retryable HTTP status codes — fail immediately regardless of policy
# ---------------------------------------------------------------------------
_NON_RETRYABLE_4XX: frozenset[int] = frozenset(
    {400, 401, 403, 404, 405, 406, 407, 409, 410, 422, 451}
)


class RetryDecision(str, Enum):
    """What the retry policy recommends after evaluating a failure."""

    RETRY = "retry"         # Attempt again after the computed backoff delay.
    STOP = "stop"           # Do not retry; propagate the failure.
    CANCEL = "cancel"       # Request was cancelled; propagate immediately.


@dataclass
class RetryOutcome:
    """Result of a single retry-policy evaluation.

    Attributes:
        decision:              What the caller should do next.
        delay_seconds:         How long to wait before the next attempt.
                               0.0 when decision is STOP or CANCEL.
        used_retry_after:      True when the delay was taken from the server's
                               Retry-After header rather than computed locally.
        attempt_number:        The attempt number that produced this outcome.
    """

    decision: RetryDecision
    delay_seconds: float
    used_retry_after: bool
    attempt_number: int


@dataclass
class RetryState:
    """Mutable accumulator tracking retry progress across multiple attempts.

    One RetryState is created at the start of a single logical operation
    (e.g., fetching one source feed). It is updated after each attempt.

    Attributes:
        attempts_made:   Number of attempts that have completed (including the first).
        total_delay_seconds: Sum of all inter-attempt delays so far.
        used_retry_after:    Whether any delay was driven by a Retry-After header.
    """

    attempts_made: int = 0
    total_delay_seconds: float = 0.0
    used_retry_after: bool = False

    def record_attempt(self, delay: float, used_retry_after: bool) -> None:
        """Record a completed attempt and the delay that followed it."""
        self.attempts_made += 1
        self.total_delay_seconds += delay
        if used_retry_after:
            self.used_retry_after = True


# Type alias: the sleep callable signature expected by RetryPolicy.
# Using Callable[..., Awaitable[None]] instead of Coroutine so tests can inject
# synchronous stubs wrapped in an async no-op.
SleepCallable = Callable[[float], Awaitable[None]]


async def _default_sleep(seconds: float) -> None:
    """Default sleep implementation; uses asyncio.sleep in production."""
    await asyncio.sleep(seconds)


@dataclass(frozen=True)
class RetryPolicy:
    """Stateless, configurable retry policy with bounded exponential backoff and jitter.

    Usage:
        config = RetryConfig(max_attempts=3, base_backoff_seconds=1.0)
        policy = RetryPolicy(config)
        state = RetryState()

        while True:
            try:
                response = await transport.get(url)
                if response.is_success:
                    break
                outcome = policy.evaluate(
                    state=state,
                    status_code=response.status_code,
                    retry_after_seconds=response.retry_after_seconds,
                )
            except TransportError as exc:
                outcome = policy.evaluate_exception(state=state, exc=exc)

            if outcome.decision != RetryDecision.RETRY:
                # handle final failure
                break
            await policy.sleep(outcome.delay_seconds)
            state.record_attempt(outcome.delay_seconds, outcome.used_retry_after)

    The RetryPolicy is intentionally a plain synchronous data object.
    The `sleep_fn` dependency is set at construction for testability.
    """

    config: RetryConfig = field(default_factory=RetryConfig)
    _sleep_fn: SleepCallable = field(default=_default_sleep, compare=False, repr=False)

    # -----------------------------------------------------------------------
    # Public evaluation API
    # -----------------------------------------------------------------------

    def evaluate(
        self,
        state: RetryState,
        status_code: int,
        retry_after_seconds: Optional[float] = None,
    ) -> RetryOutcome:
        """Decide whether to retry after receiving an HTTP response.

        Args:
            state:               Current retry accumulator for this operation.
            status_code:         The HTTP status code just received.
            retry_after_seconds: Parsed Retry-After value from the response headers.

        Returns:
            RetryOutcome with the recommended decision and wait duration.
        """
        attempt = state.attempts_made + 1  # this will be the attempt we are evaluating

        # Non-retryable 4xx errors fail immediately regardless of policy.
        if status_code in _NON_RETRYABLE_4XX:
            return RetryOutcome(
                decision=RetryDecision.STOP,
                delay_seconds=0.0,
                used_retry_after=False,
                attempt_number=attempt,
            )

        if not is_retryable_status(status_code):
            return RetryOutcome(
                decision=RetryDecision.STOP,
                delay_seconds=0.0,
                used_retry_after=False,
                attempt_number=attempt,
            )

        return self._decide_retry(state, retry_after_seconds)

    def evaluate_exception(
        self,
        state: RetryState,
        exc: BaseException,
    ) -> RetryOutcome:
        """Decide whether to retry after a transport-level exception.

        CancelledError is never retried — it is propagated by returning CANCEL.

        Args:
            state: Current retry accumulator.
            exc:   The exception that was raised.

        Returns:
            RetryOutcome with the recommended decision.
        """
        attempt = state.attempts_made + 1

        # Propagate cancellation immediately — never swallow it.
        if isinstance(exc, asyncio.CancelledError):
            return RetryOutcome(
                decision=RetryDecision.CANCEL,
                delay_seconds=0.0,
                used_retry_after=False,
                attempt_number=attempt,
            )

        # Import here to avoid a circular dependency at module import time.
        from src.infrastructure.http.client import TransportError

        if isinstance(exc, TransportError) and exc.retryable:
            return self._decide_retry(state, retry_after_seconds=None)

        return RetryOutcome(
            decision=RetryDecision.STOP,
            delay_seconds=0.0,
            used_retry_after=False,
            attempt_number=attempt,
        )

    async def sleep(self, seconds: float) -> None:
        """Wait for the specified duration using the configured sleep function.

        In production this delegates to asyncio.sleep.
        In tests an injected stub can record the call without actually sleeping.
        """
        if seconds > 0:
            await self._sleep_fn(seconds)

    # -----------------------------------------------------------------------
    # Backoff calculation — pure functions, no side effects
    # -----------------------------------------------------------------------

    def compute_backoff(self, attempts_made: int) -> float:
        """Compute the backoff delay for the upcoming retry attempt.

        Uses full-jitter exponential backoff:
            base_delay = base * 2^(attempts_made)
            jitter     = random.uniform(0, jitter_factor * base_delay)
            result     = min(max_backoff, base_delay) + jitter

        The result is always clamped to [0, max_backoff + jitter_window].

        Args:
            attempts_made: Number of attempts already completed (0-based for the
                           first retry computation).

        Returns:
            Delay in seconds.
        """
        cfg = self.config
        base_delay = cfg.base_backoff_seconds * (2 ** attempts_made)
        capped_delay = min(base_delay, cfg.max_backoff_seconds)
        jitter = random.uniform(0, cfg.jitter_factor * capped_delay)  # noqa: S311
        return capped_delay + jitter

    def apply_retry_after(self, raw_seconds: Optional[float]) -> tuple[float, bool]:
        """Validate and bound a server-provided Retry-After delay.

        Args:
            raw_seconds: The raw float value from the Retry-After header,
                         or None if absent/unparseable.

        Returns:
            (delay_seconds, used_retry_after) — where used_retry_after is True
            when the server's value was usable and within bounds.
        """
        if raw_seconds is None or raw_seconds < 0:
            return 0.0, False

        ceiling = self.config.max_retry_after_seconds
        safe_value = min(raw_seconds, ceiling)
        return safe_value, True

    # -----------------------------------------------------------------------
    # Internal helpers
    # -----------------------------------------------------------------------

    def _decide_retry(
        self,
        state: RetryState,
        retry_after_seconds: Optional[float],
    ) -> RetryOutcome:
        """Core retry decision: check attempt budget, compute delay, return outcome."""
        attempt = state.attempts_made + 1

        if state.attempts_made >= self.config.max_attempts - 1:
            # Exhausted all configured attempts.
            return RetryOutcome(
                decision=RetryDecision.STOP,
                delay_seconds=0.0,
                used_retry_after=False,
                attempt_number=attempt,
            )

        # Attempt budget allows another try — compute the wait duration.
        server_delay, used_server = self.apply_retry_after(retry_after_seconds)

        if used_server:
            delay = server_delay
        else:
            delay = self.compute_backoff(state.attempts_made)

        return RetryOutcome(
            decision=RetryDecision.RETRY,
            delay_seconds=delay,
            used_retry_after=used_server,
            attempt_number=attempt,
        )
