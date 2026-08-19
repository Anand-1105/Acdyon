"""API Request and Response Models for Presentation Layer.

Provides HTTP-safe request wrappers and response DTOs for API consumers,
decoupling raw internal domain and persistence representations from client payloads.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, List, Optional

from pydantic import BaseModel, ConfigDict, Field

from src.domain.enums import (
    EmploymentType,
    IngestionRunStatus,
    JobStatus,
    SourceHealthStatus,
    SourceType,
)


class IngestRequestModel(BaseModel):
    """HTTP request model for triggering an ingestion run."""

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    source_name: Optional[str] = Field(
        default="weworkremotely",
        max_length=64,
        description="Target source identifier to ingest.",
    )
    category: Optional[str] = Field(
        default=None,
        max_length=128,
        description="Optional category or domain tag.",
    )
    limit: Optional[int] = Field(
        default=None,
        ge=1,
        le=1000,
        description="Maximum number of records to process.",
    )


class SalaryResponseModel(BaseModel):
    """HTTP response model for salary information."""

    currency: Optional[str] = None
    min_amount: Optional[str] = None
    max_amount: Optional[str] = None
    interval: Optional[str] = None
    raw_text: Optional[str] = None


class JobResponseModel(BaseModel):
    """HTTP response model for canonical job records."""

    canonical_id: str
    source_name: str
    source_id: Optional[str] = None
    source_url: str
    title: str
    company: str
    location: str
    description: str
    employment_type: EmploymentType
    salary: Optional[SalaryResponseModel] = None
    requirements: List[str] = Field(default_factory=list)
    published_at: datetime
    ingested_at: datetime
    status: JobStatus
    metadata: dict[str, Any] = Field(default_factory=dict)


class IngestionStatsResponseModel(BaseModel):
    """HTTP response model for ingestion metrics."""

    source_name: str
    started_at: datetime
    completed_at: datetime
    duration_ms: int
    records_received: int
    records_accepted: int
    records_rejected: int
    duplicates_detected: int
    retries: int
    failed_requests: int
    status: IngestionRunStatus


class SourceInfoResponseModel(BaseModel):
    """HTTP response model for source provider information."""

    source_name: str
    source_type: SourceType
    endpoint: str
    retrieval_timestamp: datetime
    attribution: Optional[str] = None
    health_status: SourceHealthStatus
    metadata: dict[str, Any] = Field(default_factory=dict)


class IngestionErrorResponseModel(BaseModel):
    """HTTP response model for structured errors."""

    error_type: str
    scope: str
    message: str
    retryable: bool
    record_id: Optional[str] = None
    timestamp: datetime
    details: dict[str, Any] = Field(default_factory=dict)


class IngestResponseModel(BaseModel):
    """HTTP response model returned by POST /api/v1/ingest."""

    status: IngestionRunStatus
    records_count: int
    records: List[JobResponseModel]
    stats: IngestionStatsResponseModel
    errors: List[IngestionErrorResponseModel]
    source_info: SourceInfoResponseModel


class SourceHealthResponseModel(BaseModel):
    """HTTP response model for GET /api/v1/health/{source_name}."""

    source_name: str
    health_status: SourceHealthStatus
    endpoint: str
    last_success_at: Optional[datetime] = None
    last_failure_at: Optional[datetime] = None
    consecutive_failures: int
    last_error_details: Optional[dict[str, Any]] = None
    updated_at: Optional[datetime] = None


class IngestionRunResponseModel(BaseModel):
    """HTTP response model for GET /api/v1/runs/{run_id}."""

    run_id: str
    source_name: str
    status: str
    started_at: datetime
    completed_at: datetime
    duration_ms: int
    records_received: int
    records_accepted: int
    records_rejected: int
    duplicates_detected: int
    errors: List[dict[str, Any]] = Field(default_factory=list)
    source_info: dict[str, Any] = Field(default_factory=dict)


class ErrorDetailResponseModel(BaseModel):
    """Generic API error response model for HTTP error statuses."""

    error: str
    message: str
    details: Optional[dict[str, Any]] = None
