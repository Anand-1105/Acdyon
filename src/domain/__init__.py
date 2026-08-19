"""Canonical Domain Contracts for Acdyon Ingestion System.

This package exposes the source-independent domain models, enumerations,
structured errors, and identity generation rules.
"""

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
from src.domain.identity import canonicalize_url, generate_canonical_id, normalize_string
from src.domain.ingestion import IngestionRequest, IngestionResult, IngestionStats
from src.domain.job import JobRecord, SalaryInfo
from src.domain.source import SourceInfo

__all__ = [
    # Enumerations
    "EmploymentType",
    "ErrorScope",
    "IngestionErrorType",
    "IngestionRunStatus",
    "JobStatus",
    "SourceHealthStatus",
    "SourceType",
    # Core Domain Models
    "SalaryInfo",
    "JobRecord",
    "SourceInfo",
    "IngestionRequest",
    "IngestionStats",
    "IngestionError",
    "IngestionResult",
    # Identity & Normalization Utilities
    "generate_canonical_id",
    "canonicalize_url",
    "normalize_string",
]
