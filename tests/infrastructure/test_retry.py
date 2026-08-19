"""Unit tests for RetryPolicy.

All tests are synchronous/deterministic — no real sleeps.
The sleep function is injected so tests only verify call arguments.

Test coverage:
- Immediate success (no retry needed).
- Retryable status followed by success.
- Retryable status exhausting all attempts.
- Non-retryable 4xx status codes.
- CancelledError propagates as CANCEL decision.
- Non-retryable TransportError stops immediately.
- Retryable TransportError triggers retry.
- Retry-After header respected within ceiling.
- Retry-After over ceiling is clamped.
- Invalid Retry-After falls back to computed backoff.
- Exponential backoff increases with each attempt.
- Jitter stays within expected bounds.
- Zero jitter produces deterministic output.
"""

from __future__ import annotations

import asyncio
from typing import List
from unittest.mock import AsyncMock

import pytest

from src.infrastructure.config import RetryConfig
from src.infrastructure.http.client import ResponseTooLargeError, TransportError
from src.infrastructure.http.response import FetchResponse, HttpStatusClass
from src.infrastructure.reliability.retry import (
    RetryDecision,
    RetryPolicy,
    RetryState,
)


def _no_jitter_policy(max_attempts: int = 3, **kwargs) -> RetryPolicy:
    """RetryPolicy with jitter disabled for deterministic backoff tests."""
    cfg = RetryConfig(max_attempts=max_attempts, jitter_factor=0.0, **kwargs)
    sleep_mock = AsyncMock()
    return RetryPolicy(config=cfg, _sleep_fn=sleep_mock)


class TestRetryDecisionsOnHttpStatus:
    def test_non_retryable_200_status(self):
        # 200 is a success — should not even be evaluated as a retry,
        # but verify classify path: 200 is not in retryable codes.
        policy = _no_jitter_policy()
        state = RetryState()
        outcome = policy.evaluate(state, status_code=200)
        assert outcome.decision == RetryDecision.STOP

    @pytest.mark.parametrize("code", [400, 401, 403, 404, 422])
    def test_non_retryable_4xx_stops_immediately(self, code: int):
        policy = _no_jitter_policy()
        state = RetryState()
        outcome = policy.evaluate(state, status_code=code)
        assert outcome.decision == RetryDecision.STOP

    @pytest.mark.parametrize("code", [429, 502, 503, 504])
    def test_retryable_status_with_budget_returns_retry(self, code: int):
        policy = _no_jitter_policy(max_attempts=3)
        state = RetryState()  # 0 attempts made → budget remains
        outcome = policy.evaluate(state, status_code=code)
        assert outcome.decision == RetryDecision.RETRY

    @pytest.mark.parametrize("code", [429, 502, 503, 504])
    def test_retryable_status_exhausted_budget_stops(self, code: int):
        policy = _no_jitter_policy(max_attempts=2)
        state = RetryState(attempts_made=2)  # already used all attempts
        outcome = policy.evaluate(state, status_code=code)
        assert outcome.decision == RetryDecision.STOP

    def test_attempt_number_recorded_in_outcome(self):
        policy = _no_jitter_policy()
        state = RetryState(attempts_made=1)
        outcome = policy.evaluate(state, status_code=429)
        assert outcome.attempt_number == 2  # 1 made + 1 = current

    def test_500_is_not_retryable(self):
        policy = _no_jitter_policy()
        state = RetryState()
        outcome = policy.evaluate(state, status_code=500)
        assert outcome.decision == RetryDecision.STOP


class TestRetryDecisionsOnException:
    def test_cancelled_error_returns_cancel(self):
        policy = _no_jitter_policy()
        state = RetryState()
        outcome = policy.evaluate_exception(state, asyncio.CancelledError())
        assert outcome.decision == RetryDecision.CANCEL

    def test_retryable_transport_error_returns_retry(self):
        policy = _no_jitter_policy()
        state = RetryState()
        exc = TransportError("timeout", retryable=True)
        outcome = policy.evaluate_exception(state, exc)
        assert outcome.decision == RetryDecision.RETRY

    def test_non_retryable_transport_error_stops(self):
        policy = _no_jitter_policy()
        state = RetryState()
        exc = TransportError("bad redirect", retryable=False)
        outcome = policy.evaluate_exception(state, exc)
        assert outcome.decision == RetryDecision.STOP

    def test_response_too_large_stops_immediately(self):
        policy = _no_jitter_policy()
        state = RetryState()
        exc = ResponseTooLargeError(url="https://x.test", limit_bytes=10, actual_bytes=100)
        outcome = policy.evaluate_exception(state, exc)
        assert outcome.decision == RetryDecision.STOP

    def test_generic_exception_stops(self):
        policy = _no_jitter_policy()
        state = RetryState()
        outcome = policy.evaluate_exception(state, ValueError("unexpected"))
        assert outcome.decision == RetryDecision.STOP


class TestRetryAfterHandling:
    def test_valid_retry_after_used_when_within_ceiling(self):
        cfg = RetryConfig(max_attempts=3, max_retry_after_seconds=60.0, jitter_factor=0.0)
        policy = RetryPolicy(config=cfg)
        state = RetryState()
        outcome = policy.evaluate(state, status_code=429, retry_after_seconds=30.0)
        assert outcome.decision == RetryDecision.RETRY
        assert outcome.used_retry_after is True
        assert outcome.delay_seconds == 30.0

    def test_retry_after_clamped_to_ceiling(self):
        cfg = RetryConfig(max_attempts=3, max_retry_after_seconds=60.0, jitter_factor=0.0)
        policy = RetryPolicy(config=cfg)
        state = RetryState()
        outcome = policy.evaluate(state, status_code=429, retry_after_seconds=999.0)
        assert outcome.decision == RetryDecision.RETRY
        assert outcome.used_retry_after is True
        assert outcome.delay_seconds == 60.0

    def test_negative_retry_after_falls_back_to_backoff(self):
        cfg = RetryConfig(max_attempts=3, jitter_factor=0.0, base_backoff_seconds=1.0)
        policy = RetryPolicy(config=cfg)
        state = RetryState()
        outcome = policy.evaluate(state, status_code=429, retry_after_seconds=-5.0)
        assert outcome.used_retry_after is False

    def test_none_retry_after_uses_computed_backoff(self):
        cfg = RetryConfig(max_attempts=3, jitter_factor=0.0, base_backoff_seconds=2.0)
        policy = RetryPolicy(config=cfg)
        state = RetryState()
        outcome = policy.evaluate(state, status_code=503, retry_after_seconds=None)
        assert outcome.used_retry_after is False
        assert outcome.delay_seconds > 0


class TestBackoffComputation:
    def test_backoff_increases_with_attempts(self):
        cfg = RetryConfig(jitter_factor=0.0, base_backoff_seconds=1.0, max_backoff_seconds=30.0)
        policy = RetryPolicy(config=cfg)
        delays = [policy.compute_backoff(i) for i in range(4)]
        # With no jitter: 1, 2, 4, 8
        assert delays[0] == pytest.approx(1.0)
        assert delays[1] == pytest.approx(2.0)
        assert delays[2] == pytest.approx(4.0)
        assert delays[3] == pytest.approx(8.0)

    def test_backoff_capped_at_max(self):
        cfg = RetryConfig(
            jitter_factor=0.0,
            base_backoff_seconds=1.0,
            max_backoff_seconds=5.0,
        )
        policy = RetryPolicy(config=cfg)
        # 2^10 = 1024, capped to 5
        assert policy.compute_backoff(10) == pytest.approx(5.0)

    def test_jitter_stays_within_bounds(self):
        cfg = RetryConfig(jitter_factor=0.5, base_backoff_seconds=4.0, max_backoff_seconds=4.0)
        policy = RetryPolicy(config=cfg)
        # Run many samples; jitter should stay within [4.0, 4.0 + 0.5*4.0] = [4.0, 6.0]
        for _ in range(50):
            delay = policy.compute_backoff(0)
            assert 4.0 <= delay <= 6.0 + 1e-9

    def test_zero_jitter_is_deterministic(self):
        cfg = RetryConfig(jitter_factor=0.0, base_backoff_seconds=2.0)
        policy = RetryPolicy(config=cfg)
        d1 = policy.compute_backoff(1)
        d2 = policy.compute_backoff(1)
        assert d1 == d2 == pytest.approx(4.0)


class TestRetrySleepInjection:
    @pytest.mark.asyncio
    async def test_sleep_called_with_computed_delay(self):
        sleep_calls: List[float] = []

        async def stub_sleep(s: float) -> None:
            sleep_calls.append(s)

        cfg = RetryConfig(max_attempts=3, jitter_factor=0.0, base_backoff_seconds=1.0)
        policy = RetryPolicy(config=cfg, _sleep_fn=stub_sleep)
        state = RetryState()

        outcome = policy.evaluate(state, status_code=503)
        await policy.sleep(outcome.delay_seconds)

        assert len(sleep_calls) == 1
        assert sleep_calls[0] == pytest.approx(1.0)

    @pytest.mark.asyncio
    async def test_sleep_not_called_for_zero_delay(self):
        sleep_calls: List[float] = []

        async def stub_sleep(s: float) -> None:
            sleep_calls.append(s)

        policy = RetryPolicy(config=RetryConfig(), _sleep_fn=stub_sleep)
        await policy.sleep(0.0)
        assert sleep_calls == []
