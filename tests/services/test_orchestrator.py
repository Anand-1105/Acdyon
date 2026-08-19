"""Unit tests for IngestionService orchestrator (src/services/orchestrator.py).

Tests end-to-end orchestration workflows using InMemoryStorage and mock adapters:
- Full success lifecycle
- Partial success with isolated invalid records
- Unsupported source validation
- Feed-level failures and snapshot preservation
- Rate limiting and transport errors
- Empty feed semantics
- Batch deduplication
- Persistence failure isolation
- Telemetry failure isolation
- Async cancellation propagation
- Source health degradation and recovery
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from typing import List
from unittest.mock import AsyncMock, MagicMock

import pytest

from src.adapters.base import BaseSourceAdapter, ParsedBatch
from src.domain.enums import (
    EmploymentType,
    ErrorScope,
    IngestionErrorType,
    IngestionRunStatus,
    JobStatus,
    SourceHealthStatus,
    SourceType,
)
from src.domain.errors import IngestionError
from src.domain.ingestion import IngestionRequest
from src.domain.job import JobRecord
from src.domain.source import SourceInfo
from src.services.orchestrator import IngestionService
from src.services.registry import SourceAdapterRegistry
from src.storage.base import IngestionSnapshotRecord, RepositoryWriteResult, SourceHealthRecord
from src.storage.memory import InMemoryStorage

pytestmark = pytest.mark.asyncio


def _create_job(canonical_id: str, title: str = "Engineer") -> JobRecord:
    return JobRecord(
        canonical_id=canonical_id,
        source_name="mock_source",
        source_url=f"https://example.com/jobs/{canonical_id}",
        title=title,
        company="Mock Company",
        location="Remote",
        description="Job description text",
        employment_type=EmploymentType.FULL_TIME,
        published_at=datetime(2026, 8, 18, 10, 0, 0, tzinfo=timezone.utc),
    )


def _mock_adapter(
    source_name: str = "mock_source",
    records: List[JobRecord] | None = None,
    errors: List[IngestionError] | None = None,
    raw_count: int | None = None,
) -> BaseSourceAdapter:
    recs = records or []
    errs = errors or []
    count = raw_count if raw_count is not None else len(recs) + len(errs)

    source_info = SourceInfo(
        source_name=source_name,
        source_type=SourceType.RSS,
        endpoint=f"https://example.com/{source_name}.rss",
        retrieval_timestamp=datetime.now(timezone.utc),
        health_status=SourceHealthStatus.HEALTHY,
    )

    batch = ParsedBatch(
        records=recs,
        errors=errs,
        raw_count=count,
        source_info=source_info,
    )

    adapter = MagicMock(spec=BaseSourceAdapter)
    adapter.source_name = source_name
    adapter.fetch_and_parse = AsyncMock(return_value=batch)
    return adapter


class TestIngestionServiceLifecycle:
    async def test_full_success_workflow(self):
        storage = InMemoryStorage()
        j1 = _create_job("mock_1", "Backend Dev")
        j2 = _create_job("mock_2", "Frontend Dev")

        adapter = _mock_adapter("mock_source", records=[j1, j2])
        registry = SourceAdapterRegistry()
        registry.register(adapter)

        service = IngestionService(
            job_repo=storage.jobs,
            run_repo=storage.runs,
            health_repo=storage.health,
            snapshot_repo=storage.snapshots,
            registry=registry,
        )

        result = await service.ingest(IngestionRequest(source_name="mock_source"))

        assert result.status == IngestionRunStatus.SUCCESS
        assert len(result.records) == 2
        assert result.stats.records_accepted == 2
        assert result.stats.records_rejected == 0
        assert result.stats.duplicates_detected == 0

        # Verify persisted jobs
        persisted = await storage.jobs.list_jobs(source_name="mock_source")
        assert len(persisted) == 2

        # Verify run telemetry
        latest_run = await storage.runs.get_latest_run("mock_source")
        assert latest_run is not None
        assert latest_run["status"] == "success"

        # Verify snapshot created
        snapshot = await storage.snapshots.get_latest_snapshot("mock_source")
        assert snapshot is not None
        assert snapshot.job_count == 2
        assert snapshot.canonical_ids == ["mock_1", "mock_2"]

        # Verify source health is healthy
        health = await storage.health.get_health("mock_source")
        assert health is not None
        assert health.health_status == SourceHealthStatus.HEALTHY
        assert health.consecutive_failures == 0

    async def test_partial_success_isolates_record_failures(self):
        storage = InMemoryStorage()
        j1 = _create_job("mock_valid_1")
        record_error = IngestionError(
            error_type=IngestionErrorType.INVALID_RECORD_ERROR,
            scope=ErrorScope.RECORD,
            message="Malformed pubDate in item",
            record_id="broken_item_01",
        )

        adapter = _mock_adapter("mock_source", records=[j1], errors=[record_error], raw_count=2)
        registry = SourceAdapterRegistry()
        registry.register(adapter)

        service = IngestionService(
            job_repo=storage.jobs,
            run_repo=storage.runs,
            health_repo=storage.health,
            snapshot_repo=storage.snapshots,
            registry=registry,
        )

        result = await service.ingest(IngestionRequest(source_name="mock_source"))

        assert result.status == IngestionRunStatus.PARTIAL_SUCCESS
        assert len(result.records) == 1
        assert result.stats.records_received == 2
        assert result.stats.records_accepted == 1
        assert result.stats.records_rejected == 1
        assert len(result.errors) == 1

        # Valid job must still be stored in the database
        persisted = await storage.jobs.list_jobs(source_name="mock_source")
        assert len(persisted) == 1

        # Snapshot references valid job
        snapshot = await storage.snapshots.get_latest_snapshot("mock_source")
        assert snapshot is not None
        assert snapshot.canonical_ids == ["mock_valid_1"]

        # Health is degraded due to record errors
        health = await storage.health.get_health("mock_source")
        assert health is not None
        assert health.health_status == SourceHealthStatus.DEGRADED

    async def test_unsupported_source_fails_fast(self):
        storage = InMemoryStorage()
        service = IngestionService(
            job_repo=storage.jobs,
            run_repo=storage.runs,
            health_repo=storage.health,
            snapshot_repo=storage.snapshots,
            registry=SourceAdapterRegistry(),  # empty registry
        )

        result = await service.ingest(IngestionRequest(source_name="unregistered_source"))

        assert result.status == IngestionRunStatus.FAILED
        assert len(result.records) == 0
        assert len(result.errors) == 1
        assert result.errors[0].error_type == IngestionErrorType.VALIDATION_ERROR

    async def test_batch_deduplication(self):
        storage = InMemoryStorage()
        j1 = _create_job("mock_duplicate_id", "Backend v1")
        j2 = _create_job("mock_duplicate_id", "Backend v2")
        j3 = _create_job("mock_unique_id", "Frontend")

        adapter = _mock_adapter("mock_source", records=[j1, j2, j3], raw_count=3)
        registry = SourceAdapterRegistry()
        registry.register(adapter)

        service = IngestionService(
            job_repo=storage.jobs,
            run_repo=storage.runs,
            health_repo=storage.health,
            snapshot_repo=storage.snapshots,
            registry=registry,
        )

        result = await service.ingest(IngestionRequest(source_name="mock_source"))

        assert result.status == IngestionRunStatus.SUCCESS
        assert len(result.records) == 2  # j1 and j3
        assert result.stats.duplicates_detected == 1
        assert result.stats.records_accepted == 2

    async def test_empty_feed_success_and_preserves_prior_snapshot(self):
        storage = InMemoryStorage()

        # Pre-seed previous snapshot
        prev_ts = datetime(2026, 8, 18, 8, 0, 0, tzinfo=timezone.utc)
        prev_snapshot = IngestionSnapshotRecord(
            source_name="mock_source",
            run_id="run_old_001",
            canonical_ids=["mock_prev_1", "mock_prev_2"],
            job_count=2,
            snapshot_timestamp=prev_ts,
        )
        await storage.snapshots.save_snapshot(prev_snapshot)

        adapter = _mock_adapter("mock_source", records=[], raw_count=0)
        registry = SourceAdapterRegistry()
        registry.register(adapter)

        service = IngestionService(
            job_repo=storage.jobs,
            run_repo=storage.runs,
            health_repo=storage.health,
            snapshot_repo=storage.snapshots,
            registry=registry,
        )

        result = await service.ingest(IngestionRequest(source_name="mock_source"))

        assert result.status == IngestionRunStatus.SUCCESS
        assert len(result.records) == 0
        assert result.stats.records_received == 0
        assert result.stats.records_accepted == 0

        # Prior snapshot must NOT be erased by an empty feed
        snapshot = await storage.snapshots.get_latest_snapshot("mock_source")
        assert snapshot is not None
        assert snapshot.run_id == "run_old_001"
        assert snapshot.job_count == 2

    async def test_feed_level_failure_tracks_unreachable(self):
        storage = InMemoryStorage()
        feed_error = IngestionError(
            error_type=IngestionErrorType.TIMEOUT_ERROR,
            scope=ErrorScope.RUN,
            message="Gateway timeout",
        )

        adapter = _mock_adapter("mock_source", records=[], errors=[feed_error], raw_count=0)
        registry = SourceAdapterRegistry()
        registry.register(adapter)

        service = IngestionService(
            job_repo=storage.jobs,
            run_repo=storage.runs,
            health_repo=storage.health,
            snapshot_repo=storage.snapshots,
            registry=registry,
        )

        # 3 consecutive failures
        await service.ingest(IngestionRequest(source_name="mock_source"))
        await service.ingest(IngestionRequest(source_name="mock_source"))
        res3 = await service.ingest(IngestionRequest(source_name="mock_source"))

        assert res3.status == IngestionRunStatus.FAILED

        health = await storage.health.get_health("mock_source")
        assert health is not None
        assert health.consecutive_failures == 3
        assert health.health_status == SourceHealthStatus.UNREACHABLE

    async def test_persistence_failure_marks_run_failed(self):
        storage = InMemoryStorage()
        j1 = _create_job("mock_job_001")

        # Mock job repository that fails on write
        failing_job_repo = MagicMock()
        failing_job_repo.save_jobs = AsyncMock(
            return_value=RepositoryWriteResult(
                persisted_count=0,
                errors=[
                    IngestionError(
                        error_type=IngestionErrorType.PERSISTENCE_ERROR,
                        scope=ErrorScope.RUN,
                        message="Disk full",
                    )
                ],
            )
        )

        adapter = _mock_adapter("mock_source", records=[j1])
        registry = SourceAdapterRegistry()
        registry.register(adapter)

        service = IngestionService(
            job_repo=failing_job_repo,
            run_repo=storage.runs,
            health_repo=storage.health,
            snapshot_repo=storage.snapshots,
            registry=registry,
        )

        result = await service.ingest(IngestionRequest(source_name="mock_source"))

        assert result.status == IngestionRunStatus.FAILED
        assert len(result.records) == 0
        assert any(e.error_type == IngestionErrorType.PERSISTENCE_ERROR for e in result.errors)

    async def test_telemetry_failure_does_not_rollback_persisted_jobs(self):
        storage = InMemoryStorage()
        j1 = _create_job("mock_job_001")

        # Mock run repo that throws on save
        failing_run_repo = MagicMock()
        failing_run_repo.save_ingestion_run = AsyncMock(side_effect=RuntimeError("Telemetry DB down"))

        adapter = _mock_adapter("mock_source", records=[j1])
        registry = SourceAdapterRegistry()
        registry.register(adapter)

        service = IngestionService(
            job_repo=storage.jobs,
            run_repo=failing_run_repo,
            health_repo=storage.health,
            snapshot_repo=storage.snapshots,
            registry=registry,
        )

        result = await service.ingest(IngestionRequest(source_name="mock_source"))

        # Jobs remain stored
        persisted = await storage.jobs.list_jobs(source_name="mock_source")
        assert len(persisted) == 1
        assert result.stats.records_accepted == 1

    async def test_cancellation_propagates(self):
        storage = InMemoryStorage()
        adapter = MagicMock(spec=BaseSourceAdapter)
        adapter.source_name = "mock_source"
        adapter.fetch_and_parse = AsyncMock(side_effect=asyncio.CancelledError())

        registry = SourceAdapterRegistry()
        registry.register(adapter)

        service = IngestionService(
            job_repo=storage.jobs,
            run_repo=storage.runs,
            health_repo=storage.health,
            snapshot_repo=storage.snapshots,
            registry=registry,
        )

        with pytest.raises(asyncio.CancelledError):
            await service.ingest(IngestionRequest(source_name="mock_source"))

    async def test_source_health_recovery(self):
        storage = InMemoryStorage()

        # Seed degraded health
        await storage.health.save_health(
            SourceHealthRecord(
                source_name="mock_source",
                health_status=SourceHealthStatus.DEGRADED,
                endpoint="https://example.com",
                consecutive_failures=2,
            )
        )

        # Successful subsequent run
        j1 = _create_job("mock_job_001")
        adapter = _mock_adapter("mock_source", records=[j1])
        registry = SourceAdapterRegistry()
        registry.register(adapter)

        service = IngestionService(
            job_repo=storage.jobs,
            run_repo=storage.runs,
            health_repo=storage.health,
            snapshot_repo=storage.snapshots,
            registry=registry,
        )

        result = await service.ingest(IngestionRequest(source_name="mock_source"))
        assert result.status == IngestionRunStatus.SUCCESS

        health = await storage.health.get_health("mock_source")
        assert health is not None
        assert health.health_status == SourceHealthStatus.HEALTHY
        assert health.consecutive_failures == 0
