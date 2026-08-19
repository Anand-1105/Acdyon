"""Canonical Structured Ingestion Errors.

This module defines the structured error model used across the ingestion boundary.
It guarantees that failures are classified, actionable, and sanitized against
accidental leakage of credentials, bearer tokens, or sensitive network headers.
"""

from datetime import datetime, timezone
import re
from typing import Any, Optional

from pydantic import BaseModel, ConfigDict, Field, field_validator

from src.domain.enums import ErrorScope, IngestionErrorType

SENSITIVE_KEY_PATTERN = re.compile(
    r"(auth|password|secret|token|apikey|api_key|bearer|cookie|credential|private)",
    re.IGNORECASE,
)


def _sanitize_details(data: Any, depth: int = 0) -> Any:
    """Recursively sanitize dictionary details to remove sensitive values and truncate long blobs."""
    if depth > 4:
        return "[TRUNCATED_NESTING]"
    if isinstance(data, dict):
        sanitized = {}
        for k, v in data.items():
            if SENSITIVE_KEY_PATTERN.search(str(k)):
                sanitized[str(k)] = "[REDACTED]"
            else:
                sanitized[str(k)] = _sanitize_details(v, depth + 1)
        return sanitized
    if isinstance(data, list):
        return [_sanitize_details(item, depth + 1) for item in data[:20]]
    if isinstance(data, str) and len(data) > 1000:
        return data[:1000] + "... [TRUNCATED]"
    return data


class IngestionError(BaseModel):
    """Canonical representation of an ingestion failure or warning.

    Field Ownership & Provenance:
    - Runtime-derived by adapter, parser, or ingestion pipeline upon encountering a failure.
    """

    model_config = ConfigDict(frozen=True, extra="forbid", str_strip_whitespace=True)

    error_type: IngestionErrorType = Field(
        ...,
        description="Structured classification of the failure.",
    )
    scope: ErrorScope = Field(
        ...,
        description="Granularity of the failure (RUN, REQUEST, or RECORD).",
    )
    message: str = Field(
        ...,
        min_length=1,
        max_length=1024,
        description="Sanitized human-readable error description.",
    )
    details: dict[str, Any] = Field(
        default_factory=dict,
        description="Safe diagnostic context with credentials and secrets automatically stripped.",
    )
    record_id: Optional[str] = Field(
        default=None,
        max_length=256,
        description="Source or canonical record identifier if the error pertains to a specific job.",
    )
    retryable: bool = Field(
        default=False,
        description="Indicates whether the operation might succeed on a subsequent attempt.",
    )
    timestamp: datetime = Field(
        default_factory=lambda: datetime.now(timezone.utc),
        description="Timezone-aware UTC timestamp when the error occurred.",
    )

    @field_validator("details", mode="before")
    @classmethod
    def sanitize_details_payload(cls, v: Any) -> Any:
        if isinstance(v, dict):
            return _sanitize_details(v)
        return v

    @field_validator("timestamp", mode="before")
    @classmethod
    def normalize_timestamp(cls, v: Any) -> Any:
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
