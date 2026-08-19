"""Unit tests for storage domain mapping and serialization (src/storage/mapping.py).

Verifies bi-directional translation between canonical domain models and
database row representations, ensuring zero data loss and exact invariant preservation.
"""

from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal

import pytest

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
from src.domain.ingestion import IngestionStats
from src.domain.job import JobRecord, SalaryInfo
from src.domain.source import SourceInfo
from src.storage.base import IngestionSnapshotRecord, SourceHealthRecord
from src.storage.mapping import (
    ingestion_run_to_row,
    job_to_row,
    row_to_job,
    row_to_snapshot,
    row_to_source_health,
    snapshot_to_row,
    source_health_to_row,
)


class TestJobMapping:
    def test_job_to_row_and_back(self):
        published = datetime(2026, 8, 18, 12, 0, 0, tzinfo=timezone.utc)
        ingested = datetime(2026, 8, 18, 12, 5, 0, tzinfo=timezone.utc)

        job = JobRecord(
            canonical_id="wwr_12345678abcdef00",
            source_name="weworkremotely",
            source_id="https://weworkremotely.com/jobs/123",
            source_url="https://weworkremotely.com/remote-jobs/acme-backend",
            title="Senior Backend Engineer",
            company="Acme Corp",
            location="Anywhere in the World",
            description="<p>Full job description</p>",
            employment_type=EmploymentType.FULL_TIME,
            salary=SalaryInfo(
                currency="USD",
                min_amount=Decimal("120000"),
                max_amount=Decimal("160000"),
                interval="yearly",
                raw_text="$120k - $160k",
            ),
            requirements=["Python", "FastAPI", "PostgreSQL"],
            published_at=published,
            ingested_at=ingested,
            status=JobStatus.ACTIVE,
            metadata={"wwr_category": "Programming", "wwr_type": "Full-Time"},
        )

        row = job_to_row(job)
        assert row["canonical_id"] == "wwr_12345678abcdef00"
        assert row["employment_type"] == "full_time"
        assert row["status"] == "active"
        assert row["salary"]["min_amount"] == "120000"
        assert row["requirements"] == ["Python", "FastAPI", "PostgreSQL"]

        restored = row_to_job(row)
        assert restored.canonical_id == job.canonical_id
        assert restored.source_name == job.source_name
        assert restored.source_id == job.source_id
        assert restored.title == job.title
        assert restored.company == job.company
        assert restored.location == job.location
        assert restored.description == job.description
        assert restored.employment_type == EmploymentType.FULL_TIME
        assert restored.status == JobStatus.ACTIVE
        assert restored.salary is not None
        assert restored.salary.min_amount == Decimal("120000")
        assert restored.salary.max_amount == Decimal("160000")
        assert restored.requirements == ["Python", "FastAPI", "PostgreSQL"]
        assert restored.published_at == published
        assert restored.ingested_at == ingested
        assert restored.metadata == {"wwr_category": "Programming", "wwr_type": "Full-Time"}

    def test_minimal_job_without_optional_fields(self):
        published = datetime(2026, 8, 18, 12, 0, 0, tzinfo=timezone.utc)
        job = JobRecord(
            canonical_id="wwr_minimal_001",
            source_name="weworkremotely",
            source_url="https://weworkremotely.com/jobs/minimal",
            title="Minimal Role",
            company="Minimal Co",
            description="Description",
            published_at=published,
        )

        row = job_to_row(job)
        assert row["salary"] is None
        assert row["requirements"] == []
        assert row["location"] == "Remote"

        restored = row_to_job(row)
        assert restored.salary is None
        assert restored.requirements == []
        assert restored.location == "Remote"
        assert restored.employment_type == EmploymentType.UNKNOWN
        assert restored.status == JobStatus.ACTIVE

    def test_unknown_enum_fallbacks(self):
        row = {
            "canonical_id": "wwr_fallback_01",
            "source_name": "weworkremotely",
            "source_url": "https://example.com/job",
            "title": "Role",
            "company": "Company",
            "location": "Remote",
            "description": "Text",
            "employment_type": "unrecognized_future_type",
            "status": "unrecognized_future_status",
            "published_at": "2026-08-18T10:00:00+00:00",
            "ingested_at": "2026-08-18T10:05:00+00:00",
        }

        job = row_to_job(row)
        assert job.employment_type == EmploymentType.UNKNOWN
        assert job.status == JobStatus.ACTIVE


class TestIngestionRunMapping:
    def test_ingestion_run_to_row(self):
        started = datetime(2026, 8, 18, 10, 0, 0, tzinfo=timezone.utc)
        completed = datetime(2026, 8, 18, 10, 0, 2, tzinfo=timezone.utc)

        stats = IngestionStats(
            source_name="weworkremotely",
            started_at=started,
            completed_at=completed,
            duration_ms=2000,
            records_received=100,
            records_accepted=98,
            records_rejected=2,
            duplicates_detected=5,
            retries=1,
            failed_requests=0,
            status=IngestionRunStatus.PARTIAL_SUCCESS,
        )

        source_info = SourceInfo(
            source_name="weworkremotely",
            source_type=SourceType.RSS,
            endpoint="https://weworkremotely.com/remote-jobs.rss",
            retrieval_timestamp=started,
            attribution="Attribution notice",
            health_status=SourceHealthStatus.HEALTHY,
        )

        errors = [
            IngestionError(
                error_type=IngestionErrorType.INVALID_RECORD_ERROR,
                scope=ErrorScope.RECORD,
                message="Missing title",
                record_id="item_01",
            )
        ]

        row = ingestion_run_to_row("run_test_123", stats, source_info, errors)
        assert row["run_id"] == "run_test_123"
        assert row["source_name"] == "weworkremotely"
        assert row["status"] == "partial_success"
        assert row["records_received"] == 100
        assert row["records_accepted"] == 98
        assert row["records_rejected"] == 2
        assert len(row["errors"]) == 1
        assert row["errors"][0]["error_type"] == "invalid_record_error"
        assert row["source_info"]["source_name"] == "weworkremotely"
        assert row["source_info"]["health_status"] == "healthy"


class TestSourceHealthMapping:
    def test_source_health_to_row_and_back(self):
        last_success = datetime(2026, 8, 18, 9, 0, 0, tzinfo=timezone.utc)
        health = SourceHealthRecord(
            source_name="weworkremotely",
            health_status=SourceHealthStatus.HEALTHY,
            endpoint="https://weworkremotely.com/remote-jobs.rss",
            last_success_at=last_success,
            last_failure_at=None,
            consecutive_failures=0,
            last_error_details=None,
            updated_at=last_success,
        )

        row = source_health_to_row(health)
        assert row["source_name"] == "weworkremotely"
        assert row["health_status"] == "healthy"
        assert row["consecutive_failures"] == 0

        restored = row_to_source_health(row)
        assert restored.source_name == health.source_name
        assert restored.health_status == SourceHealthStatus.HEALTHY
        assert restored.endpoint == health.endpoint
        assert restored.last_success_at == last_success
        assert restored.last_failure_at is None
        assert restored.consecutive_failures == 0


class TestSnapshotMapping:
    def test_snapshot_to_row_and_back(self):
        ts = datetime(2026, 8, 18, 9, 0, 0, tzinfo=timezone.utc)
        snap = IngestionSnapshotRecord(
            source_name="weworkremotely",
            run_id="run_abc123",
            canonical_ids=["wwr_1", "wwr_2", "wwr_3"],
            job_count=3,
            snapshot_timestamp=ts,
        )

        row = snapshot_to_row(snap)
        assert row["source_name"] == "weworkremotely"
        assert row["run_id"] == "run_abc123"
        assert row["canonical_ids"] == ["wwr_1", "wwr_2", "wwr_3"]
        assert row["job_count"] == 3

        restored = row_to_snapshot(row)
        assert restored.source_name == snap.source_name
        assert restored.run_id == snap.run_id
        assert restored.canonical_ids == ["wwr_1", "wwr_2", "wwr_3"]
        assert restored.job_count == 3
        assert restored.snapshot_timestamp == ts
