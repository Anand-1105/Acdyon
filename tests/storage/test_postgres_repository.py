"""Unit tests for PostgresStorage repositories (src/storage/postgres.py).

Tests Supabase PostgREST interactions using mock client responses, error translation,
atomic batch upserts, and snapshot reference handling.
"""

from __future__ import annotations

from datetime import datetime, timezone
from unittest.mock import MagicMock

import pytest

from src.domain.enums import (
    EmploymentType,
    IngestionErrorType,
    IngestionRunStatus,
    JobStatus,
    SourceHealthStatus,
    SourceType,
)
from src.domain.ingestion import IngestionStats
from src.domain.job import JobRecord
from src.domain.source import SourceInfo
from src.storage.base import IngestionSnapshotRecord, SourceHealthRecord
from src.storage.postgres import (
    PostgresIngestionRunRepository,
    PostgresJobRepository,
    PostgresSnapshotRepository,
    PostgresSourceHealthRepository,
    PostgresStorage,
)

pytestmark = pytest.mark.asyncio


def _create_sample_job(canonical_id: str) -> JobRecord:
    return JobRecord(
        canonical_id=canonical_id,
        source_name="weworkremotely",
        source_url=f"https://weworkremotely.com/jobs/{canonical_id}",
        title="Backend Engineer",
        company="Acme Corp",
        location="Remote",
        description="Job description text",
        employment_type=EmploymentType.FULL_TIME,
        published_at=datetime(2026, 8, 18, 10, 0, 0, tzinfo=timezone.utc),
    )


class TestPostgresJobRepository:
    async def test_save_jobs_success(self):
        mock_client = MagicMock()
        mock_table = MagicMock()
        mock_upsert = MagicMock()
        mock_response = MagicMock()

        mock_client.table.return_value = mock_table
        mock_table.upsert.return_value = mock_upsert
        mock_upsert.execute.return_value = mock_response

        repo = PostgresJobRepository(mock_client)
        job = _create_sample_job("wwr_test_01")

        result = await repo.save_jobs([job])

        assert result.is_success
        assert result.persisted_count == 1
        mock_client.table.assert_called_once_with("jobs")
        mock_table.upsert.assert_called_once()

    async def test_save_jobs_empty_batch(self):
        mock_client = MagicMock()
        repo = PostgresJobRepository(mock_client)

        result = await repo.save_jobs([])
        assert result.is_success
        assert result.persisted_count == 0
        mock_client.table.assert_not_called()

    async def test_save_jobs_database_exception_translated_to_persistence_error(self):
        mock_client = MagicMock()
        mock_table = MagicMock()
        mock_client.table.return_value = mock_table
        mock_table.upsert.side_effect = RuntimeError("Database connection lost")

        repo = PostgresJobRepository(mock_client)
        job = _create_sample_job("wwr_test_01")

        result = await repo.save_jobs([job])

        assert not result.is_success
        assert result.persisted_count == 0
        assert len(result.errors) == 1
        assert result.errors[0].error_type == IngestionErrorType.PERSISTENCE_ERROR
        assert "Database connection lost" in result.errors[0].message

    async def test_get_job_by_canonical_id(self):
        mock_client = MagicMock()
        mock_table = MagicMock()
        mock_select = MagicMock()
        mock_eq = MagicMock()
        mock_limit = MagicMock()
        mock_response = MagicMock()

        mock_response.data = [
            {
                "canonical_id": "wwr_test_01",
                "source_name": "weworkremotely",
                "source_url": "https://example.com/job",
                "title": "Engineer",
                "company": "Company",
                "location": "Remote",
                "description": "Desc",
                "employment_type": "full_time",
                "published_at": "2026-08-18T10:00:00+00:00",
                "ingested_at": "2026-08-18T10:05:00+00:00",
            }
        ]

        mock_client.table.return_value = mock_table
        mock_table.select.return_value = mock_select
        mock_select.eq.return_value = mock_eq
        mock_eq.limit.return_value = mock_limit
        mock_limit.execute.return_value = mock_response

        repo = PostgresJobRepository(mock_client)
        job = await repo.get_job_by_canonical_id("wwr_test_01")

        assert job is not None
        assert job.canonical_id == "wwr_test_01"
        assert job.title == "Engineer"


class TestPostgresIngestionRunRepository:
    async def test_save_ingestion_run(self):
        mock_client = MagicMock()
        mock_table = MagicMock()
        mock_insert = MagicMock()

        mock_client.table.return_value = mock_table
        mock_table.insert.return_value = mock_insert
        mock_insert.execute.return_value = MagicMock()

        repo = PostgresIngestionRunRepository(mock_client)

        started = datetime(2026, 8, 18, 10, 0, 0, tzinfo=timezone.utc)
        completed = datetime(2026, 8, 18, 10, 0, 2, tzinfo=timezone.utc)

        stats = IngestionStats(
            source_name="weworkremotely",
            started_at=started,
            completed_at=completed,
            duration_ms=2000,
            records_received=100,
            records_accepted=100,
            status=IngestionRunStatus.SUCCESS,
        )

        source_info = SourceInfo(
            source_name="weworkremotely",
            source_type=SourceType.RSS,
            endpoint="https://weworkremotely.com/remote-jobs.rss",
            retrieval_timestamp=started,
            health_status=SourceHealthStatus.HEALTHY,
        )

        run_id = await repo.save_ingestion_run(stats, source_info, errors=[], run_id="run_pg_001")
        assert run_id == "run_pg_001"
        mock_client.table.assert_called_once_with("ingestion_runs")


class TestPostgresSourceHealthRepository:
    async def test_save_and_get_health(self):
        mock_client = MagicMock()
        mock_table = MagicMock()
        mock_upsert = MagicMock()

        mock_client.table.return_value = mock_table
        mock_table.upsert.return_value = mock_upsert
        mock_upsert.execute.return_value = MagicMock()

        repo = PostgresSourceHealthRepository(mock_client)

        ts = datetime(2026, 8, 18, 10, 0, 0, tzinfo=timezone.utc)
        health = SourceHealthRecord(
            source_name="weworkremotely",
            health_status=SourceHealthStatus.HEALTHY,
            endpoint="https://weworkremotely.com/remote-jobs.rss",
            last_success_at=ts,
        )

        await repo.save_health(health)
        mock_client.table.assert_called_once_with("source_health")


class TestPostgresSnapshotRepository:
    async def test_save_snapshot(self):
        mock_client = MagicMock()
        mock_table = MagicMock()
        mock_upsert = MagicMock()

        mock_client.table.return_value = mock_table
        mock_table.upsert.return_value = mock_upsert
        mock_upsert.execute.return_value = MagicMock()

        repo = PostgresSnapshotRepository(mock_client)
        ts = datetime(2026, 8, 18, 10, 0, 0, tzinfo=timezone.utc)
        snap = IngestionSnapshotRecord(
            source_name="weworkremotely",
            run_id="run_001",
            canonical_ids=["wwr_1", "wwr_2"],
            job_count=2,
            snapshot_timestamp=ts,
        )

        await repo.save_snapshot(snap)
        mock_client.table.assert_called_once_with("ingestion_snapshots")
