"""Unit tests for InMemoryStorage and its repositories (src/storage/memory.py).

Verifies in-memory persistence contracts, idempotency, list ordering,
telemetry retention, and snapshot reference handling.
"""

from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal

import pytest

from src.domain.enums import (
    EmploymentType,
    IngestionRunStatus,
    JobStatus,
    SourceHealthStatus,
    SourceType,
)
from src.domain.ingestion import IngestionStats
from src.domain.job import JobRecord, SalaryInfo
from src.domain.source import SourceInfo
from src.storage.base import IngestionSnapshotRecord, SourceHealthRecord
from src.storage.memory import InMemoryStorage

pytestmark = pytest.mark.asyncio


def _create_sample_job(
    canonical_id: str,
    title: str = "Backend Engineer",
    company: str = "Acme Corp",
    published_at: datetime | None = None,
    source_name: str = "weworkremotely",
) -> JobRecord:
    return JobRecord(
        canonical_id=canonical_id,
        source_name=source_name,
        source_url=f"https://weworkremotely.com/jobs/{canonical_id}",
        title=title,
        company=company,
        location="Remote",
        description="Job description text",
        employment_type=EmploymentType.FULL_TIME,
        published_at=published_at or datetime(2026, 8, 18, 10, 0, 0, tzinfo=timezone.utc),
    )


class TestInMemoryJobRepository:
    async def test_save_and_retrieve_job(self):
        storage = InMemoryStorage()
        job = _create_sample_job("wwr_job_001")

        result = await storage.jobs.save_jobs([job])
        assert result.is_success
        assert result.persisted_count == 1

        retrieved = await storage.jobs.get_job_by_canonical_id("wwr_job_001")
        assert retrieved is not None
        assert retrieved.canonical_id == "wwr_job_001"
        assert retrieved.title == "Backend Engineer"

    async def test_idempotent_upsert_same_canonical_id(self):
        storage = InMemoryStorage()
        job1 = _create_sample_job("wwr_job_001", title="Backend Engineer v1")
        job2 = _create_sample_job("wwr_job_001", title="Backend Engineer v2")

        await storage.jobs.save_jobs([job1])
        await storage.jobs.save_jobs([job2])

        all_jobs = await storage.jobs.list_jobs()
        assert len(all_jobs) == 1
        assert all_jobs[0].title == "Backend Engineer v2"

    async def test_get_jobs_by_canonical_ids(self):
        storage = InMemoryStorage()
        j1 = _create_sample_job("wwr_job_001")
        j2 = _create_sample_job("wwr_job_002")
        j3 = _create_sample_job("wwr_job_003")

        await storage.jobs.save_jobs([j1, j2, j3])

        subset = await storage.jobs.get_jobs_by_canonical_ids(["wwr_job_001", "wwr_job_003", "nonexistent"])
        assert len(subset) == 2
        ids = {j.canonical_id for j in subset}
        assert ids == {"wwr_job_001", "wwr_job_003"}

    async def test_list_jobs_sorting_and_pagination(self):
        storage = InMemoryStorage()
        t1 = datetime(2026, 8, 18, 9, 0, 0, tzinfo=timezone.utc)
        t2 = datetime(2026, 8, 18, 10, 0, 0, tzinfo=timezone.utc)
        t3 = datetime(2026, 8, 18, 11, 0, 0, tzinfo=timezone.utc)

        j1 = _create_sample_job("wwr_1", published_at=t1)
        j2 = _create_sample_job("wwr_2", published_at=t2)
        j3 = _create_sample_job("wwr_3", published_at=t3)

        await storage.jobs.save_jobs([j1, j2, j3])

        # Default sorted by published_at DESC
        listed = await storage.jobs.list_jobs(limit=2, offset=0)
        assert len(listed) == 2
        assert listed[0].canonical_id == "wwr_3"
        assert listed[1].canonical_id == "wwr_2"

        offset_page = await storage.jobs.list_jobs(limit=2, offset=2)
        assert len(offset_page) == 1
        assert offset_page[0].canonical_id == "wwr_1"

    async def test_list_jobs_filter_by_source(self):
        storage = InMemoryStorage()
        j1 = _create_sample_job("wwr_1", source_name="weworkremotely")
        j2 = _create_sample_job("remoteok_1", source_name="remoteok")

        await storage.jobs.save_jobs([j1, j2])

        wwr_jobs = await storage.jobs.list_jobs(source_name="weworkremotely")
        assert len(wwr_jobs) == 1
        assert wwr_jobs[0].source_name == "weworkremotely"


class TestInMemoryIngestionRunRepository:
    async def test_save_and_get_run(self):
        storage = InMemoryStorage()
        started = datetime(2026, 8, 18, 10, 0, 0, tzinfo=timezone.utc)
        completed = datetime(2026, 8, 18, 10, 0, 2, tzinfo=timezone.utc)

        stats = IngestionStats(
            source_name="weworkremotely",
            started_at=started,
            completed_at=completed,
            duration_ms=2000,
            records_received=50,
            records_accepted=50,
            status=IngestionRunStatus.SUCCESS,
        )

        source_info = SourceInfo(
            source_name="weworkremotely",
            source_type=SourceType.RSS,
            endpoint="https://weworkremotely.com/remote-jobs.rss",
            retrieval_timestamp=started,
            health_status=SourceHealthStatus.HEALTHY,
        )

        run_id = await storage.runs.save_ingestion_run(stats, source_info, errors=[], run_id="run_custom_001")
        assert run_id == "run_custom_001"

        run_record = await storage.runs.get_ingestion_run("run_custom_001")
        assert run_record is not None
        assert run_record["source_name"] == "weworkremotely"
        assert run_record["records_accepted"] == 50

        latest = await storage.runs.get_latest_run("weworkremotely")
        assert latest is not None
        assert latest["run_id"] == "run_custom_001"


class TestInMemorySourceHealthRepository:
    async def test_save_and_get_health(self):
        storage = InMemoryStorage()
        ts = datetime(2026, 8, 18, 10, 0, 0, tzinfo=timezone.utc)

        health = SourceHealthRecord(
            source_name="weworkremotely",
            health_status=SourceHealthStatus.HEALTHY,
            endpoint="https://weworkremotely.com/remote-jobs.rss",
            last_success_at=ts,
            consecutive_failures=0,
        )

        await storage.health.save_health(health)
        retrieved = await storage.health.get_health("weworkremotely")
        assert retrieved is not None
        assert retrieved.health_status == SourceHealthStatus.HEALTHY
        assert retrieved.last_success_at == ts


class TestInMemorySnapshotRepository:
    async def test_save_and_get_snapshot(self):
        storage = InMemoryStorage()
        ts = datetime(2026, 8, 18, 10, 0, 0, tzinfo=timezone.utc)

        snapshot = IngestionSnapshotRecord(
            source_name="weworkremotely",
            run_id="run_001",
            canonical_ids=["wwr_1", "wwr_2"],
            job_count=2,
            snapshot_timestamp=ts,
        )

        await storage.snapshots.save_snapshot(snapshot)
        retrieved = await storage.snapshots.get_latest_snapshot("weworkremotely")
        assert retrieved is not None
        assert retrieved.run_id == "run_001"
        assert retrieved.canonical_ids == ["wwr_1", "wwr_2"]
        assert retrieved.job_count == 2
