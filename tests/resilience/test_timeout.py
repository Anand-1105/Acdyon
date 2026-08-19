"""Resilience tests for Transport Timeouts and Cancellation.

Exercises the full pipeline:
HTTPXMock (TimeoutException) -> AsyncHttpTransport -> RetryPolicy ->
WWRSourceAdapter -> IngestionService -> InMemoryStorage

Verifies:
1. Transient timeout with retry succeeds without false error.
2. Persistent timeout fails gracefully, records TIMEOUT_ERROR, and preserves prior state.
3. Async cancellation is never masked as a retryable timeout.
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from unittest.mock import AsyncMock, patch

import httpx
import pytest
from pytest_httpx import HTTPXMock

from src.adapters.wwr.config import PRIMARY_FEED_URL
from src.domain.enums import IngestionErrorType, IngestionRunStatus, SourceHealthStatus
from src.domain.ingestion import IngestionRequest
from src.storage.base import IngestionSnapshotRecord
from tests.resilience.conftest import load_fixture, make_fast_adapter, make_service

pytestmark = pytest.mark.asyncio


class TestTimeoutResilience:
    async def test_transient_timeout_recovers_to_success(
        self, httpx_mock: HTTPXMock, memory_storage
    ):
        """Attempt 1: TimeoutException -> Attempt 2: 200 OK."""
        xml_bytes = load_fixture("wwr_valid.xml")
        httpx_mock.add_exception(httpx.TimeoutException("Read timeout"), url=PRIMARY_FEED_URL)
        httpx_mock.add_response(url=PRIMARY_FEED_URL, status_code=200, content=xml_bytes)

        adapter = make_fast_adapter(max_attempts=2)
        service = make_service(memory_storage, adapter)

        result = await service.ingest(IngestionRequest())

        assert result.status == IngestionRunStatus.SUCCESS
        assert len(result.records) == 3
        assert result.stats.records_accepted == 3

    async def test_persistent_timeout_fails_and_preserves_snapshot(
        self, httpx_mock: HTTPXMock, memory_storage
    ):
        """Persistent timeout across all attempts -> FAILED run -> preserves prior snapshot."""
        seed_time = datetime(2026, 8, 18, 8, 0, 0, tzinfo=timezone.utc)
        await memory_storage.snapshots.save_snapshot(
            IngestionSnapshotRecord(
                source_name="weworkremotely",
                run_id="run_lkg_timeout",
                canonical_ids=["wwr_seed_to_1"],
                job_count=1,
                snapshot_timestamp=seed_time,
            )
        )

        for _ in range(2):
            httpx_mock.add_exception(httpx.TimeoutException("Connection timed out"), url=PRIMARY_FEED_URL)

        adapter = make_fast_adapter(max_attempts=2)
        service = make_service(memory_storage, adapter)

        result = await service.ingest(IngestionRequest())

        assert result.status == IngestionRunStatus.FAILED
        assert len(result.records) == 0
        assert len(result.errors) == 1
        assert result.errors[0].error_type == IngestionErrorType.TIMEOUT_ERROR

        # Snapshot preserved
        snapshot = await memory_storage.snapshots.get_latest_snapshot("weworkremotely")
        assert snapshot is not None
        assert snapshot.run_id == "run_lkg_timeout"

    async def test_async_cancellation_propagates_immediately(
        self, memory_storage
    ):
        """Cancellation during execution raises CancelledError and is not swallowed."""
        adapter = make_fast_adapter()
        with patch.object(adapter, "fetch_and_parse", side_effect=asyncio.CancelledError()):
            service = make_service(memory_storage, adapter)
            with pytest.raises(asyncio.CancelledError):
                await service.ingest(IngestionRequest())
