"""Resilience tests for Valid Empty RSS Feeds.

Exercises the full pipeline:
HTTPXMock (200 with valid empty RSS) -> AsyncHttpTransport -> WWRRSSParser ->
WWRSourceAdapter -> IngestionService -> InMemoryStorage

Verifies:
1. Valid empty feed is treated as a SUCCESS run with 0 records and 0 errors.
2. Source health remains HEALTHY (empty feed is not an outage).
3. Pre-existing last-known-good snapshot is NOT erased or overwritten.
"""

from __future__ import annotations

from datetime import datetime, timezone

import pytest
from pytest_httpx import HTTPXMock

from src.adapters.wwr.config import PRIMARY_FEED_URL
from src.domain.enums import IngestionRunStatus, SourceHealthStatus
from src.domain.ingestion import IngestionRequest
from src.storage.base import IngestionSnapshotRecord
from tests.resilience.conftest import load_fixture, make_fast_adapter, make_service

pytestmark = pytest.mark.asyncio


class TestEmptyFeedResilience:
    async def test_empty_feed_yields_success_and_preserves_prior_snapshot(
        self, httpx_mock: HTTPXMock, memory_storage
    ):
        """Valid empty RSS with channel headers but zero job items."""
        seed_time = datetime(2026, 8, 18, 8, 0, 0, tzinfo=timezone.utc)
        await memory_storage.snapshots.save_snapshot(
            IngestionSnapshotRecord(
                source_name="weworkremotely",
                run_id="run_lkg_empty_test",
                canonical_ids=["wwr_seed_item_1", "wwr_seed_item_2"],
                job_count=2,
                snapshot_timestamp=seed_time,
            )
        )

        empty_xml = load_fixture("wwr_empty.xml")
        httpx_mock.add_response(url=PRIMARY_FEED_URL, status_code=200, content=empty_xml)

        adapter = make_fast_adapter(max_attempts=1)
        service = make_service(memory_storage, adapter)

        result = await service.ingest(IngestionRequest())

        # Verify Pipeline Outcome
        assert result.status == IngestionRunStatus.SUCCESS
        assert len(result.records) == 0
        assert result.stats.records_received == 0
        assert result.stats.records_accepted == 0
        assert len(result.errors) == 0

        # Verify Source Health (empty is healthy)
        health = await memory_storage.health.get_health("weworkremotely")
        assert health is not None
        assert health.health_status == SourceHealthStatus.HEALTHY
        assert health.consecutive_failures == 0

        # Verify Prior Snapshot Preserved (not erased by empty run)
        snapshot = await memory_storage.snapshots.get_latest_snapshot("weworkremotely")
        assert snapshot is not None
        assert snapshot.run_id == "run_lkg_empty_test"
        assert snapshot.job_count == 2
