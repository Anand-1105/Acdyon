"""Canonical Source Information Domain Model.

This module defines metadata describing any external or sandbox source that
produces job listings, maintaining generic attributes for feed endpoints,
retrieval timestamps, attribution, and operational health.
"""

from datetime import datetime, timezone
from typing import Any, Optional

from pydantic import BaseModel, ConfigDict, Field, field_validator

from src.domain.enums import SourceHealthStatus, SourceType


class SourceInfo(BaseModel):
    """Canonical metadata describing an ingestion source.

    Field Ownership & Provenance:
    - Configuration-derived: source_name, source_type, endpoint, attribution.
    - Runtime-derived: retrieval_timestamp, health_status, metadata.
    """

    model_config = ConfigDict(frozen=True, extra="forbid", str_strip_whitespace=True)

    source_name: str = Field(
        ...,
        min_length=1,
        max_length=64,
        description="Unique identifier for the source (e.g., 'weworkremotely', 'remoteok').",
    )
    source_type: SourceType = Field(
        ...,
        description="Protocol or interface category of the source.",
    )
    endpoint: str = Field(
        ...,
        min_length=1,
        max_length=2048,
        description="URL, feed URI, or sandbox descriptor for the source endpoint.",
    )
    retrieval_timestamp: datetime = Field(
        default_factory=lambda: datetime.now(timezone.utc),
        description="Timezone-aware UTC timestamp when the source was queried.",
    )
    attribution: Optional[str] = Field(
        default=None,
        max_length=512,
        description="Optional copyright or attribution notice required by source terms.",
    )
    health_status: SourceHealthStatus = Field(
        default=SourceHealthStatus.UNKNOWN,
        description="Current operational health assessment of the source.",
    )
    metadata: dict[str, Any] = Field(
        default_factory=dict,
        description="Optional feed-level metadata (e.g., ETag, Last-Modified header, channel title).",
    )

    @field_validator("retrieval_timestamp", mode="before")
    @classmethod
    def normalize_retrieval_timestamp(cls, v: Any) -> Any:
        if isinstance(v, datetime):
            if v.tzinfo is None:
                return v.replace(tzinfo=timezone.utc)
            return v.astimezone(timezone.utc)
        if isinstance(v, str):
            dt = datetime.fromisoformat(v)
            if dt.tzinfo is None:
                return dt.replace(tzinfo=timezone.utc)
            return dt.astimezone(timezone.utc)
        return v
