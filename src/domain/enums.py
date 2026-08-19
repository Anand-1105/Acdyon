"""Domain Enumerations for the Canonical Job Ingestion System.

This module defines all categorical state values used across canonical contracts,
explicitly separating job lifecycle status, ingestion execution status, source health,
employment types, and structured error types.
"""

from enum import Enum


class JobStatus(str, Enum):
    """Lifecycle and operational status of an individual job posting."""

    ACTIVE = "active"
    EXPIRED = "expired"
    ARCHIVED = "archived"
    UNKNOWN = "unknown"


class EmploymentType(str, Enum):
    """Normalized employment arrangement categories."""

    FULL_TIME = "full_time"
    PART_TIME = "part_time"
    CONTRACT = "contract"
    INTERNSHIP = "internship"
    FREELANCE = "freelance"
    TEMPORARY = "temporary"
    OTHER = "other"
    UNKNOWN = "unknown"


class SourceType(str, Enum):
    """Type of data feed or protocol used by the ingestion source."""

    RSS = "rss"
    API = "api"
    WEB = "web"
    SANDBOX = "sandbox"


class SourceHealthStatus(str, Enum):
    """Operational health and availability state of an ingestion source."""

    HEALTHY = "healthy"
    DEGRADED = "degraded"
    UNREACHABLE = "unreachable"
    UNKNOWN = "unknown"


class IngestionRunStatus(str, Enum):
    """Execution status of an overall ingestion run or task."""

    SUCCESS = "success"
    PARTIAL_SUCCESS = "partial_success"
    FAILED = "failed"


class IngestionErrorType(str, Enum):
    """Classification of errors encountered during ingestion execution."""

    VALIDATION_ERROR = "validation_error"
    RATE_LIMIT_ERROR = "rate_limit_error"
    NETWORK_TRANSPORT_ERROR = "network_transport_error"
    TIMEOUT_ERROR = "timeout_error"
    SOURCE_SERVER_ERROR = "source_server_error"
    MALFORMED_RESPONSE_ERROR = "malformed_response_error"
    INVALID_RECORD_ERROR = "invalid_record_error"
    PERSISTENCE_ERROR = "persistence_error"
    INTERNAL_ERROR = "internal_error"


class ErrorScope(str, Enum):
    """Granularity of the failure impact."""

    RUN = "run"          # The entire ingestion execution failed
    REQUEST = "request"  # A specific HTTP/network request failed
    RECORD = "record"    # A single record failed validation or parsing
