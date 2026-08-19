"""Resilience tests for Malformed XML / RSS Feed Payloads.

Exercises the full pipeline:
HTTPXMock (200 with corrupted XML) -> AsyncHttpTransport -> WWRRSSParser (defusedxml) ->
WWRSourceAdapter -> IngestionService -> InMemoryStorage

Verifies:
1. Syntactically invalid XML is safely caught by defusedxml without crashing.
2. Malformed feeds produce MALFORMED_RESPONSE_ERROR at the feed level.
3. No corrupted or partial job records are persisted.
4. Pre-existing last-known-good snapshots are preserved.
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


class TestMalformedXmlResilience:
    async def test_corrupted_xml_produces_malformed_error_and_preserves_snapshot(
        self, httpx_mock: HTTPXMock, memory_storage
    ):
        """HTTP 200 with invalid unclosed XML tags."""
        seed_time = datetime(2026, 8, 18, 8, 0, 0, tzinfo=timezone.utc)
        await memory_storage.snapshots.save_snapshot(
            IngestionSnapshotRecord(
                source_name="weworkremotely",
                run_id="run_lkg_xml",
                canonical_ids=["wwr_seed_valid_job"],
                job_count=1,
                snapshot_timestamp=seed_time,
            )
        )

        malformed_xml = load_fixture("wwr_malformed.xml")
        httpx_mock.add_response(url=PRIMARY_FEED_URL, status_code=200, content=malformed_xml)

        adapter = make_fast_adapter(max_attempts=1)
        service = make_service(memory_storage, adapter)

        result = await service.ingest(IngestionRequest())

        assert result.status == IngestionRunStatus.FAILED
        assert len(result.records) == 0
        assert len(result.errors) == 1
        assert result.errors[0].error_type == IngestionErrorType.MALFORMED_RESPONSE_ERROR

        # Zero corrupt jobs persisted
        persisted = await memory_storage.jobs.list_jobs(source_name="weworkremotely")
        assert len(persisted) == 0

        # Snapshot is preserved
        snapshot = await memory_storage.snapshots.get_latest_snapshot("weworkremotely")
        assert snapshot is not None
        assert snapshot.run_id == "run_lkg_xml"
        assert snapshot.job_count == 1

    async def test_truncated_xml_bytes_handled_safely(
        self, httpx_mock: HTTPXMock, memory_storage
    ):
        """HTTP 200 with truncated XML stream (cut off mid-tag)."""
        truncated_xml = b"<?xml version='1.0'?><rss version='2.0'><channel><title>We Work Remotely</title><item><title>Engine"
        httpx_mock.add_response(url=PRIMARY_FEED_URL, status_code=200, content=truncated_xml)

        adapter = make_fast_adapter(max_attempts=1)
        service = make_service(memory_storage, adapter)

        result = await service.ingest(IngestionRequest())

        assert result.status == IngestionRunStatus.FAILED
        assert len(result.records) == 0
        assert len(result.errors) == 1
        assert result.errors[0].error_type == IngestionErrorType.MALFORMED_RESPONSE_ERROR
