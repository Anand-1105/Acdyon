"""Abstract Persistence Contracts and Data Transfer Objects.

This module defines the repository interfaces that decouple domain and orchestration
logic from specific database engines (Supabase, PostgreSQL, or in-memory test fakes).
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, List, Optional, Sequence

from src.domain.enums import SourceHealthStatus
from src.domain.errors import IngestionError
from src.domain.ingestion import IngestionStats
from src.domain.job import JobRecord
from src.domain.source import SourceInfo


@dataclass(frozen=True)
class RepositoryWriteResult:
    """Outcome metrics of a bulk job persistence operation.

    Attributes:
        persisted_count: Total number of records successfully written or updated.
        errors: List of persistence errors encountered during write, if any.
    """

    persisted_count: int
    errors: List[IngestionError] = field(default_factory=list)

    @property
    def is_success(self) -> bool:
        return len(self.errors) == 0


@dataclass(frozen=True)
class SourceHealthRecord:
    """Persistence representation of source operational health."""

    source_name: str
    health_status: SourceHealthStatus
    endpoint: str
    last_success_at: Optional[datetime] = None
    last_failure_at: Optional[datetime] = None
    consecutive_failures: int = 0
    last_error_details: Optional[dict[str, Any]] = None
    updated_at: Optional[datetime] = None


@dataclass(frozen=True)
class IngestionSnapshotRecord:
    """Lightweight reference to a successful ingestion run's job canonical IDs."""

    source_name: str
    run_id: str
    canonical_ids: List[str]
    job_count: int
    snapshot_timestamp: datetime
    created_at: Optional[datetime] = None


class BaseJobRepository(ABC):
    """Abstract interface for storing and retrieving canonical JobRecord instances."""

    @abstractmethod
    async def save_jobs(self, jobs: Sequence[JobRecord]) -> RepositoryWriteResult:
        """Atomically upsert a batch of canonical job records on conflict on canonical_id.

        Args:
            jobs: Sequence of validated canonical JobRecord objects.

        Returns:
            RepositoryWriteResult with count of persisted records.
        """
        ...

    @abstractmethod
    async def get_job_by_canonical_id(self, canonical_id: str) -> Optional[JobRecord]:
        """Retrieve a single canonical job record by its canonical_id."""
        ...

    @abstractmethod
    async def get_jobs_by_canonical_ids(self, canonical_ids: Sequence[str]) -> List[JobRecord]:
        """Retrieve multiple canonical job records by their canonical_ids."""
        ...

    @abstractmethod
    async def list_jobs(
        self,
        source_name: Optional[str] = None,
        limit: int = 50,
        offset: int = 0,
    ) -> List[JobRecord]:
        """List canonical job records with optional source filtering and pagination."""
        ...

    @abstractmethod
    async def count_jobs(self, source_name: Optional[str] = None) -> int:
        """Count total persisted canonical job records with optional source filtering."""
        ...


class BaseIngestionRunRepository(ABC):
    """Abstract interface for storing and retrieving ingestion run telemetry."""

    @abstractmethod
    async def save_ingestion_run(
        self,
        stats: IngestionStats,
        source_info: SourceInfo,
        errors: Sequence[IngestionError],
        run_id: Optional[str] = None,
    ) -> str:
        """Persist an ingestion run summary and return the generated or provided run_id."""
        ...

    @abstractmethod
    async def get_ingestion_run(self, run_id: str) -> Optional[dict[str, Any]]:
        """Retrieve execution telemetry for a specific run_id."""
        ...

    @abstractmethod
    async def get_latest_run(self, source_name: str) -> Optional[dict[str, Any]]:
        """Retrieve telemetry for the most recent run of a given source."""
        ...

    @abstractmethod
    async def list_runs(
        self,
        source_name: Optional[str] = None,
        limit: int = 50,
        offset: int = 0,
    ) -> List[dict[str, Any]]:
        """List historical ingestion runs ordered chronologically (newest first)."""
        ...


class BaseSourceHealthRepository(ABC):
    """Abstract interface for persisting and reading source health states."""

    @abstractmethod
    async def save_health(self, health: SourceHealthRecord) -> None:
        """Persist or update operational health state for a source."""
        ...

    @abstractmethod
    async def get_health(self, source_name: str) -> Optional[SourceHealthRecord]:
        """Retrieve the current recorded health state for a source."""
        ...


class BaseSnapshotRepository(ABC):
    """Abstract interface for saving and retrieving lightweight last-known-good snapshots."""

    @abstractmethod
    async def save_snapshot(self, snapshot: IngestionSnapshotRecord) -> None:
        """Save a lightweight snapshot reference for a source."""
        ...

    @abstractmethod
    async def get_latest_snapshot(self, source_name: str) -> Optional[IngestionSnapshotRecord]:
        """Retrieve the most recent snapshot reference for a source."""
        ...
