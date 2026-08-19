"""Domain-to-Database Mapping and Serialization Functions.

Provides pure, bidirectional translation between canonical domain models and
database storage representations without leaking database types into the domain.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any, Optional

from src.domain.enums import EmploymentType, JobStatus, SourceHealthStatus
from src.domain.errors import IngestionError
from src.domain.ingestion import IngestionStats
from src.domain.job import JobRecord, SalaryInfo
from src.domain.source import SourceInfo
from src.storage.base import IngestionSnapshotRecord, SourceHealthRecord


def _format_datetime(dt: Optional[datetime]) -> Optional[str]:
    """Format datetime to ISO 8601 string in UTC for database storage."""
    if dt is None:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    else:
        dt = dt.astimezone(timezone.utc)
    return dt.isoformat()


def _parse_datetime(val: Any) -> Optional[datetime]:
    """Parse an ISO 8601 string or datetime object into a UTC timezone-aware datetime."""
    if val is None:
        return None
    if isinstance(val, datetime):
        if val.tzinfo is None:
            return val.replace(tzinfo=timezone.utc)
        return val.astimezone(timezone.utc)
    if isinstance(val, str):
        dt = datetime.fromisoformat(val.replace("Z", "+00:00"))
        if dt.tzinfo is None:
            return dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(timezone.utc)
    return None


def job_to_row(job: JobRecord) -> dict[str, Any]:
    """Convert a canonical JobRecord domain model into a database row dictionary."""
    salary_dict = None
    if job.salary is not None:
        salary_dict = {
            "currency": job.salary.currency,
            "min_amount": str(job.salary.min_amount) if job.salary.min_amount is not None else None,
            "max_amount": str(job.salary.max_amount) if job.salary.max_amount is not None else None,
            "interval": job.salary.interval,
            "raw_text": job.salary.raw_text,
        }

    return {
        "canonical_id": job.canonical_id,
        "source_name": job.source_name,
        "source_id": job.source_id,
        "source_url": job.source_url,
        "title": job.title,
        "company": job.company,
        "location": job.location,
        "description": job.description,
        "employment_type": job.employment_type.value,
        "salary": salary_dict,
        "requirements": list(job.requirements),
        "published_at": _format_datetime(job.published_at),
        "ingested_at": _format_datetime(job.ingested_at),
        "status": job.status.value,
        "metadata": dict(job.metadata),
    }


def row_to_job(row: dict[str, Any]) -> JobRecord:
    """Convert a database row dictionary into a canonical JobRecord domain model."""
    raw_salary = row.get("salary")
    salary_obj = None
    if raw_salary and isinstance(raw_salary, dict):
        min_amt = Decimal(str(raw_salary["min_amount"])) if raw_salary.get("min_amount") is not None else None
        max_amt = Decimal(str(raw_salary["max_amount"])) if raw_salary.get("max_amount") is not None else None
        salary_obj = SalaryInfo(
            currency=raw_salary.get("currency"),
            min_amount=min_amt,
            max_amount=max_amt,
            interval=raw_salary.get("interval"),
            raw_text=raw_salary.get("raw_text"),
        )

    raw_emp = row.get("employment_type", "unknown")
    try:
        emp_type = EmploymentType(raw_emp)
    except ValueError:
        emp_type = EmploymentType.UNKNOWN

    raw_status = row.get("status", "active")
    try:
        status = JobStatus(raw_status)
    except ValueError:
        status = JobStatus.ACTIVE

    raw_reqs = row.get("requirements")
    requirements = list(raw_reqs) if isinstance(raw_reqs, (list, tuple)) else []

    raw_metadata = row.get("metadata")
    metadata = dict(raw_metadata) if isinstance(raw_metadata, dict) else {}

    return JobRecord(
        canonical_id=row["canonical_id"],
        source_name=row["source_name"],
        source_id=row.get("source_id"),
        source_url=row["source_url"],
        title=row["title"],
        company=row["company"],
        location=row.get("location", "Remote"),
        description=row["description"],
        employment_type=emp_type,
        salary=salary_obj,
        requirements=requirements,
        published_at=_parse_datetime(row["published_at"]) or datetime.now(timezone.utc),
        ingested_at=_parse_datetime(row["ingested_at"]) or datetime.now(timezone.utc),
        status=status,
        metadata=metadata,
    )


def ingestion_run_to_row(
    run_id: str,
    stats: IngestionStats,
    source_info: SourceInfo,
    errors: list[IngestionError],
) -> dict[str, Any]:
    """Convert ingestion run telemetry and metadata into a database row dictionary."""
    serialized_errors = [
        {
            "error_type": err.error_type.value,
            "scope": err.scope.value,
            "message": err.message,
            "retryable": err.retryable,
            "record_id": err.record_id,
            "timestamp": _format_datetime(err.timestamp),
            "details": err.details,
        }
        for err in errors
    ]

    serialized_source_info = {
        "source_name": source_info.source_name,
        "source_type": source_info.source_type.value,
        "endpoint": source_info.endpoint,
        "retrieval_timestamp": _format_datetime(source_info.retrieval_timestamp),
        "attribution": source_info.attribution,
        "health_status": source_info.health_status.value,
        "metadata": source_info.metadata,
    }

    return {
        "run_id": run_id,
        "source_name": stats.source_name,
        "status": stats.status.value,
        "started_at": _format_datetime(stats.started_at),
        "completed_at": _format_datetime(stats.completed_at),
        "duration_ms": stats.duration_ms,
        "records_received": stats.records_received,
        "records_accepted": stats.records_accepted,
        "records_rejected": stats.records_rejected,
        "duplicates_detected": stats.duplicates_detected,
        "retries": stats.retries,
        "failed_requests": stats.failed_requests,
        "errors": serialized_errors,
        "source_info": serialized_source_info,
        "created_at": _format_datetime(datetime.now(timezone.utc)),
    }


def source_health_to_row(health: SourceHealthRecord) -> dict[str, Any]:
    """Convert SourceHealthRecord into a database row dictionary."""
    return {
        "source_name": health.source_name,
        "health_status": health.health_status.value,
        "endpoint": health.endpoint,
        "last_success_at": _format_datetime(health.last_success_at),
        "last_failure_at": _format_datetime(health.last_failure_at),
        "consecutive_failures": health.consecutive_failures,
        "last_error_details": health.last_error_details or {},
        "updated_at": _format_datetime(health.updated_at or datetime.now(timezone.utc)),
    }


def row_to_source_health(row: dict[str, Any]) -> SourceHealthRecord:
    """Convert database row dictionary into SourceHealthRecord."""
    raw_status = row.get("health_status", "unknown")
    try:
        status = SourceHealthStatus(raw_status)
    except ValueError:
        status = SourceHealthStatus.UNKNOWN

    raw_err = row.get("last_error_details")
    err_details = dict(raw_err) if isinstance(raw_err, dict) else None

    return SourceHealthRecord(
        source_name=row["source_name"],
        health_status=status,
        endpoint=row.get("endpoint", ""),
        last_success_at=_parse_datetime(row.get("last_success_at")),
        last_failure_at=_parse_datetime(row.get("last_failure_at")),
        consecutive_failures=int(row.get("consecutive_failures", 0)),
        last_error_details=err_details,
        updated_at=_parse_datetime(row.get("updated_at")),
    )


def snapshot_to_row(snapshot: IngestionSnapshotRecord) -> dict[str, Any]:
    """Convert IngestionSnapshotRecord into a database row dictionary."""
    return {
        "source_name": snapshot.source_name,
        "run_id": snapshot.run_id,
        "canonical_ids": list(snapshot.canonical_ids),
        "job_count": snapshot.job_count,
        "snapshot_timestamp": _format_datetime(snapshot.snapshot_timestamp),
        "created_at": _format_datetime(snapshot.created_at or datetime.now(timezone.utc)),
    }


def row_to_snapshot(row: dict[str, Any]) -> IngestionSnapshotRecord:
    """Convert database row dictionary into IngestionSnapshotRecord."""
    raw_ids = row.get("canonical_ids")
    canonical_ids = list(raw_ids) if isinstance(raw_ids, (list, tuple)) else []
    return IngestionSnapshotRecord(
        source_name=row["source_name"],
        run_id=row["run_id"],
        canonical_ids=canonical_ids,
        job_count=int(row.get("job_count", len(canonical_ids))),
        snapshot_timestamp=_parse_datetime(row["snapshot_timestamp"]) or datetime.now(timezone.utc),
        created_at=_parse_datetime(row.get("created_at")),
    )
