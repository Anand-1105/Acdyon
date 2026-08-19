"""Canonical Ingestion Execution Contracts.

This module defines the request, execution statistics, and aggregate result contracts
governing any execution of the ingestion subsystem.
"""

from datetime import datetime, timezone
from typing import Any, List, Optional

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from src.domain.enums import IngestionRunStatus
from src.domain.errors import IngestionError
from src.domain.job import JobRecord
from src.domain.source import SourceInfo


def _ensure_utc(dt: Optional[datetime]) -> Optional[datetime]:
    """Ensure a datetime object is timezone-aware and normalized to UTC."""
    if dt is None:
        return None
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


class IngestionRequest(BaseModel):
    """Canonical request parameter model for initiating an ingestion task.

    This represents the generic request contract. Individual source adapters
    may apply these parameters natively (e.g. via API query params) or as
    post-fetch filters where the source protocol (e.g. basic RSS) does not support them natively.
    """

    model_config = ConfigDict(frozen=True, extra="forbid", str_strip_whitespace=True)

    source_name: Optional[str] = Field(
        default=None,
        max_length=64,
        description="Target source identifier to invoke. If omitted, default configured source is used.",
    )
    search_term: Optional[str] = Field(
        default=None,
        max_length=256,
        description="Keyword or search query filter.",
    )
    location: Optional[str] = Field(
        default=None,
        max_length=256,
        description="Location or geographic filter.",
    )
    category: Optional[str] = Field(
        default=None,
        max_length=128,
        description="Job category or domain tag (e.g. 'programming', 'devops').",
    )
    limit: Optional[int] = Field(
        default=None,
        ge=1,
        le=1000,
        description="Maximum number of job records to retrieve and normalize.",
    )
    since: Optional[datetime] = Field(
        default=None,
        description="Time boundary; only ingest postings published on or after this timestamp.",
    )
    metadata: dict[str, Any] = Field(
        default_factory=dict,
        description="Source-specific or execution-context options (e.g., custom feed URLs).",
    )

    @field_validator("since", mode="before")
    @classmethod
    def normalize_since(cls, v: Any) -> Any:
        if isinstance(v, datetime):
            return _ensure_utc(v)
        if isinstance(v, str):
            return _ensure_utc(datetime.fromisoformat(v))
        return v


class IngestionStats(BaseModel):
    """Operational metrics and execution summary for a single ingestion run.

    Provides complete observability for dashboards and logging without
    leaking UI or transport details.
    """

    model_config = ConfigDict(frozen=True, extra="forbid", str_strip_whitespace=True)

    source_name: str = Field(
        ...,
        min_length=1,
        max_length=64,
        description="Source targeted by this execution.",
    )
    started_at: datetime = Field(
        ...,
        description="Timezone-aware UTC timestamp when ingestion started.",
    )
    completed_at: datetime = Field(
        ...,
        description="Timezone-aware UTC timestamp when ingestion concluded.",
    )
    duration_ms: int = Field(
        ...,
        ge=0,
        description="Total duration of the ingestion run in milliseconds.",
    )
    records_received: int = Field(
        default=0,
        ge=0,
        description="Total raw records retrieved from source.",
    )
    records_accepted: int = Field(
        default=0,
        ge=0,
        description="Total records that passed validation and normalization.",
    )
    records_rejected: int = Field(
        default=0,
        ge=0,
        description="Total records rejected due to schema/validation failures.",
    )
    duplicates_detected: int = Field(
        default=0,
        ge=0,
        description="Total records identified as duplicates during deduplication.",
    )
    retries: int = Field(
        default=0,
        ge=0,
        description="Count of network or transient retry attempts made.",
    )
    failed_requests: int = Field(
        default=0,
        ge=0,
        description="Count of individual HTTP/network requests that failed.",
    )
    status: IngestionRunStatus = Field(
        ...,
        description="Final execution status of the run.",
    )

    @field_validator("started_at", "completed_at", mode="before")
    @classmethod
    def normalize_timestamps(cls, v: Any) -> Any:
        if isinstance(v, datetime):
            return _ensure_utc(v)
        if isinstance(v, str):
            return _ensure_utc(datetime.fromisoformat(v))
        return v

    @model_validator(mode="after")
    def validate_timestamps_and_metrics(self) -> "IngestionStats":
        if self.completed_at < self.started_at:
            raise ValueError("completed_at cannot be earlier than started_at")
        return self


class IngestionResult(BaseModel):
    """Aggregate result returned by the ingestion subsystem to callers.

    Decouples ingestion execution from persistence (e.g. database/Supabase)
    or presentation (e.g. FastAPI/UI) layers.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    status: IngestionRunStatus = Field(
        ...,
        description="Overall ingestion execution outcome (SUCCESS, PARTIAL_SUCCESS, FAILED).",
    )
    records: List[JobRecord] = Field(
        default_factory=list,
        description="List of canonical job records produced by the ingestion run.",
    )
    stats: IngestionStats = Field(
        ...,
        description="Execution statistics and operational metrics.",
    )
    errors: List[IngestionError] = Field(
        default_factory=list,
        description="List of structured errors and warnings encountered during the run.",
    )
    source_info: SourceInfo = Field(
        ...,
        description="Metadata describing the source queried during this run.",
    )

    @property
    def is_success(self) -> bool:
        """Returns True if the ingestion completed with full success."""
        return self.status == IngestionRunStatus.SUCCESS

    @property
    def is_partial_success(self) -> bool:
        """Returns True if the ingestion completed partially with non-fatal errors."""
        return self.status == IngestionRunStatus.PARTIAL_SUCCESS

    @property
    def is_failure(self) -> bool:
        """Returns True if the ingestion run failed completely."""
        return self.status == IngestionRunStatus.FAILED

    @property
    def total_jobs(self) -> int:
        """Returns the total number of normalized canonical records produced."""
        return len(self.records)

    @property
    def has_errors(self) -> bool:
        """Returns True if one or more structured errors were recorded."""
        return len(self.errors) > 0
