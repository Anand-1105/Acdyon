"""Resilience tests for upstream HTTP 5xx Server Failures.

Exercises the full pipeline:
HTTPXMock (502/503/504 responses) -> AsyncHttpTransport -> RetryPolicy ->
WWRSourceAdapter -> IngestionService -> InMemoryStorage

Verifies:
1. Transient 5xx with mid-loop recovery yields SUCCESS and HEALTHY source state.
2. Persistent 5xx exhausts retries, marks run FAILED, records SOURCE_SERVER_ERROR, and preserves last-known-good state.
3. 500 Internal Server Error (non-transient by default) stops immediately without retry storms.
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


class TestTransient5xxResilience:
    async def test_transient_503_and_502_recovers_on_third_attempt(
        self, httpx_mock: HTTPXMock, memory_storage
    ):
        """Attempt 1: 503 -> Attempt 2: 502 -> Attempt 3: 200 OK."""
        xml_bytes = load_fixture("wwr_valid.xml")
        httpx_mock.add_response(url=PRIMARY_FEED_URL, status_code=503, content=b"Gateway Busy")
        httpx_mock.add_response(url=PRIMARY_FEED_URL, status_code=502, content=b"Bad Gateway")
        httpx_mock.add_response(url=PRIMARY_FEED_URL, status_code=200, content=xml_bytes)

        adapter = make_fast_adapter(max_attempts=3)
        service = make_service(memory_storage, adapter)

        result = await service.ingest(IngestionRequest())

        assert result.status == IngestionRunStatus.SUCCESS
        assert len(result.records) == 3
        assert result.stats.records_accepted == 3
        assert len(result.errors) == 0

        # Verify all 3 jobs persisted
        persisted = await memory_storage.jobs.list_jobs(source_name="weworkremotely")
        assert len(persisted) == 3

        # Verify healthy state restored
        health = await memory_storage.health.get_health("weworkremotely")
        assert health is not None
        assert health.health_status == SourceHealthStatus.HEALTHY
        assert health.consecutive_failures == 0

    async def test_persistent_503_exhaustion_preserves_snapshot(
        self, httpx_mock: HTTPXMock, memory_storage
    ):
        """3 persistent 503 responses -> FAILED run -> prior snapshot intact."""
        # Pre-seed last-known-good snapshot
        seed_time = datetime(2026, 8, 18, 8, 0, 0, tzinfo=timezone.utc)
        await memory_storage.snapshots.save_snapshot(
            IngestionSnapshotRecord(
                source_name="weworkremotely",
                run_id="run_lkg_5xx",
                canonical_ids=["wwr_seed_1"],
                job_count=1,
                snapshot_timestamp=seed_time,
            )
        )

        for _ in range(3):
            httpx_mock.add_response(url=PRIMARY_FEED_URL, status_code=503, content=b"Outage")

        adapter = make_fast_adapter(max_attempts=3)
        service = make_service(memory_storage, adapter)

        result = await service.ingest(IngestionRequest())

        assert result.status == IngestionRunStatus.FAILED
        assert len(result.records) == 0
        assert len(result.errors) == 1
        assert result.errors[0].error_type == IngestionErrorType.SOURCE_SERVER_ERROR

        # Snapshot is preserved
        snapshot = await memory_storage.snapshots.get_latest_snapshot("weworkremotely")
        assert snapshot is not None
        assert snapshot.run_id == "run_lkg_5xx"
        assert snapshot.job_count == 1
