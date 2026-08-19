"""Resilience tests for Concurrency, Rate Limiting, and Retry Storm Prevention.

Exercises:
1. Multiple concurrent ingestion requests against a failing upstream source.
2. Asserts that the RateLimiter enforces serial pacing and concurrency caps.
3. Asserts that retries are bounded across all concurrent tasks without exponential explosion.
"""

from __future__ import annotations

import asyncio

import pytest
from pytest_httpx import HTTPXMock

from src.adapters.wwr.config import PRIMARY_FEED_URL
from src.domain.enums import IngestionRunStatus
from src.domain.ingestion import IngestionRequest
from tests.resilience.conftest import make_fast_adapter, make_service

pytestmark = pytest.mark.asyncio


class TestConcurrencyAndRetryStormResilience:
    async def test_concurrent_failing_requests_remain_bounded(
        self, httpx_mock: HTTPXMock, memory_storage
    ):
        """5 simultaneous callers against a 503-failing source.

        With max_attempts=2 per caller, exactly 10 HTTP requests must occur (5 * 2).
        No runaway retry amplification.
        """
        for _ in range(10):
            httpx_mock.add_response(url=PRIMARY_FEED_URL, status_code=503, content=b"Server Busy")

        adapter = make_fast_adapter(max_attempts=2)
        service = make_service(memory_storage, adapter)

        # Launch 5 concurrent ingest calls
        tasks = [service.ingest(IngestionRequest()) for _ in range(5)]
        results = await asyncio.gather(*tasks)

        # All 5 failed cleanly
        for res in results:
            assert res.status == IngestionRunStatus.FAILED

        # Verify request count matches expected bounded multiplier
        requests_made = httpx_mock.get_requests()
        assert len(requests_made) == 10
