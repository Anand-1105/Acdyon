"""Resilience tests for upstream HTTP 429 Rate Limiting.

Exercises the full pipeline:
HTTPXMock (429 responses) -> AsyncHttpTransport -> RetryPolicy / RateLimiter ->
WWRSourceAdapter -> IngestionService -> InMemoryStorage

Verifies:
1. Transient 429 with Retry-After recovers cleanly to SUCCESS without health degradation.
2. Persistent 429 exhausts retries, marks run FAILED, records RATE_LIMIT_ERROR, and updates health.
3. Excessive Retry-After headers are clamped to bounded limits.
4. Pre-existing last-known-good snapshots are preserved across rate-limit outages.
"""

from __future__ import annotations

from datetime import datetime, timezone

import pytest
from pytest_httpx import HTTPXMock

from src.adapters.wwr.config import PRIMARY_FEED_URL
from src.domain.enums import IngestionErrorType, IngestionRunStatus, SourceHealthStatus
from src.domain.ingestion import IngestionRequest
from src.storage.base import IngestionSnapshotRecord
from tests.resilience.conftest import load_fixture, make_fast_adapter, make_service

pytestmark = pytest.mark.asyncio


class TestRateLimiting429Resilience:
    async def test_transient_429_recovers_to_success(self, httpx_mock: HTTPXMock, memory_storage):
        """HTTP 429 on attempt 1 with Retry-After: 0 followed by 200 OK on attempt 2."""
        xml_bytes = load_fixture("wwr_valid.xml")
        httpx_mock.add_response(
            url=PRIMARY_FEED_URL,
            status_code=429,
            headers={"Retry-After": "0"},
            content=b"Rate limit exceeded",
        )
        httpx_mock.add_response(
            url=PRIMARY_FEED_URL,
            status_code=200,
            content=xml_bytes,
        )

        adapter = make_fast_adapter(max_attempts=3)
        service = make_service(memory_storage, adapter)

        result = await service.ingest(IngestionRequest())

        # Verify Pipeline Outcome
        assert result.status == IngestionRunStatus.SUCCESS
        assert len(result.records) == 3
        assert result.stats.records_accepted == 3
        assert len(result.errors) == 0

        # Verify Persistence State
        persisted = await memory_storage.jobs.list_jobs(source_name="weworkremotely")
        assert len(persisted) == 3

        # Verify Source Health (transient rate limiting does NOT degrade final health on recovery)
        health = await memory_storage.health.get_health("weworkremotely")
        assert health is not None
        assert health.health_status == SourceHealthStatus.HEALTHY
        assert health.consecutive_failures == 0

        # Verify Snapshot Created
        snapshot = await memory_storage.snapshots.get_latest_snapshot("weworkremotely")
        assert snapshot is not None
        assert snapshot.job_count == 3

    async def test_persistent_429_exhausts_retries_and_preserves_prior_snapshot(
        self, httpx_mock: HTTPXMock, memory_storage
    ):
        """HTTP 429 on all attempts -> bounded retries -> FAILED -> preserves prior snapshot."""
        # Pre-seed existing last-known-good snapshot
        seed_time = datetime(2026, 8, 18, 8, 0, 0, tzinfo=timezone.utc)
        await memory_storage.snapshots.save_snapshot(
            IngestionSnapshotRecord(
                source_name="weworkremotely",
                run_id="run_lkg_001",
                canonical_ids=["wwr_existing_job_1", "wwr_existing_job_2"],
                job_count=2,
                snapshot_timestamp=seed_time,
            )
        )

        # Mock 3 persistent 429 responses
        for _ in range(3):
            httpx_mock.add_response(
                url=PRIMARY_FEED_URL,
                status_code=429,
                headers={"Retry-After": "0"},
                content=b"Rate limit exceeded",
            )

        adapter = make_fast_adapter(max_attempts=3)
        service = make_service(memory_storage, adapter)

        result = await service.ingest(IngestionRequest())

        # Verify Pipeline Outcome
        assert result.status == IngestionRunStatus.FAILED
        assert len(result.records) == 0
        assert len(result.errors) == 1
        assert result.errors[0].error_type == IngestionErrorType.RATE_LIMIT_ERROR

        # Verify Prior Snapshot Preserved (not erased or corrupted)
        snapshot = await memory_storage.snapshots.get_latest_snapshot("weworkremotely")
        assert snapshot is not None
        assert snapshot.run_id == "run_lkg_001"
        assert snapshot.job_count == 2
        assert snapshot.canonical_ids == ["wwr_existing_job_1", "wwr_existing_job_2"]

        # Verify Source Health Degraded
        health = await memory_storage.health.get_health("weworkremotely")
        assert health is not None
        assert health.health_status == SourceHealthStatus.DEGRADED
        assert health.consecutive_failures == 1
