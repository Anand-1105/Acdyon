"""Canonical JobRecord Domain Model.

This module defines the normalized representation of a single job posting within
the ingestion subsystem. All external source formats (WWR RSS, APIs, etc.)
must be transformed into this canonical format prior to validation and downstream processing.
"""

from datetime import datetime, timezone
from decimal import Decimal
from typing import Any, List, Optional
from urllib.parse import urlparse

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from src.domain.enums import EmploymentType, JobStatus


def _ensure_utc(dt: Optional[datetime]) -> Optional[datetime]:
    """Ensure a datetime object is timezone-aware and normalized to UTC."""
    if dt is None:
        return None
    if dt.tzinfo is None:
        # Interpret naive datetime as UTC
        return dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


class SalaryInfo(BaseModel):
    """Canonical representation of salary or compensation range."""

    model_config = ConfigDict(frozen=True, extra="forbid", str_strip_whitespace=True)

    currency: Optional[str] = Field(
        default=None,
        max_length=10,
        description="ISO 4217 currency code (e.g., 'USD', 'EUR') or symbol if unstandardized.",
    )
    min_amount: Optional[Decimal] = Field(
        default=None,
        ge=0,
        description="Lower bound of compensation range.",
    )
    max_amount: Optional[Decimal] = Field(
        default=None,
        ge=0,
        description="Upper bound of compensation range.",
    )
    interval: Optional[str] = Field(
        default=None,
        max_length=32,
        description="Payment interval (e.g., 'yearly', 'monthly', 'hourly').",
    )
    raw_text: Optional[str] = Field(
        default=None,
        max_length=256,
        description="Original unparsed compensation string from source.",
    )

    @model_validator(mode="after")
    def validate_range(self) -> "SalaryInfo":
        if (
            self.min_amount is not None
            and self.max_amount is not None
            and self.min_amount > self.max_amount
        ):
            raise ValueError(
                f"min_amount ({self.min_amount}) cannot exceed max_amount ({self.max_amount})"
            )
        return self


class JobRecord(BaseModel):
    """Canonical representation of a normalized job posting.

    Field Ownership & Provenance:
    - Source-derived: source_id, source_url, title, company, location, description,
      employment_type, salary, requirements, published_at.
    - System-derived: canonical_id, source_name, ingested_at, status, metadata.
    """

    model_config = ConfigDict(frozen=True, extra="forbid", str_strip_whitespace=True)

    # Identifiers
    canonical_id: str = Field(
        ...,
        min_length=1,
        max_length=128,
        description="Deterministic unique canonical identifier across all sources.",
    )
    source_name: str = Field(
        ...,
        min_length=1,
        max_length=64,
        description="Identifier of the origin source (e.g., 'weworkremotely').",
    )
    source_id: Optional[str] = Field(
        default=None,
        max_length=256,
        description="Source-specific record identifier (e.g., RSS GUID, remote primary key).",
    )
    source_url: str = Field(
        ...,
        min_length=1,
        max_length=2048,
        description="Direct URL to original job posting on the source platform.",
    )

    # Core Job Details
    title: str = Field(
        ...,
        min_length=1,
        max_length=512,
        description="Normalized job title.",
    )
    company: str = Field(
        ...,
        min_length=1,
        max_length=256,
        description="Normalized hiring organization or company name.",
    )
    location: str = Field(
        default="Remote",
        min_length=1,
        max_length=256,
        description="Normalized geographical location or remote policy.",
    )
    description: str = Field(
        ...,
        min_length=1,
        max_length=100_000,
        description="Full text or sanitized HTML/Markdown description of the role.",
    )

    # Structured Classifications
    employment_type: EmploymentType = Field(
        default=EmploymentType.UNKNOWN,
        description="Categorized employment arrangement.",
    )
    salary: Optional[SalaryInfo] = Field(
        default=None,
        description="Structured compensation information if available.",
    )
    requirements: List[str] = Field(
        default_factory=list,
        max_length=100,
        description="List of extracted or source-provided skill/requirement tags.",
    )

    # Timestamps
    published_at: datetime = Field(
        ...,
        description="Timezone-aware timestamp indicating when the job was published at the source.",
    )
    ingested_at: datetime = Field(
        default_factory=lambda: datetime.now(timezone.utc),
        description="Timezone-aware timestamp indicating when the job was processed by this system.",
    )

    # Operational Status & Metadata
    status: JobStatus = Field(
        default=JobStatus.ACTIVE,
        description="Current operational lifecycle status of the job posting.",
    )
    metadata: dict[str, Any] = Field(
        default_factory=dict,
        description="Generic, non-lossy dictionary for source-specific extensions or debugging data.",
    )

    @field_validator("source_url")
    @classmethod
    def validate_url_scheme(cls, v: str) -> str:
        parsed = urlparse(v)
        if parsed.scheme not in ("http", "https"):
            raise ValueError(f"source_url must have 'http' or 'https' scheme, got: '{parsed.scheme}'")
        if not parsed.netloc:
            raise ValueError(f"source_url must include a valid host/domain, got: '{v}'")
        return v

    @field_validator("published_at", "ingested_at", mode="before")
    @classmethod
    def normalize_datetimes(cls, v: Any) -> Any:
        if isinstance(v, datetime):
            return _ensure_utc(v)
        if isinstance(v, str):
            dt = datetime.fromisoformat(v)
            return _ensure_utc(dt)
        return v
