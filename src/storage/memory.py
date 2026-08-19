"""In-Memory Storage Implementation for Testing and Local Development.

Provides thread-safe and deterministic in-memory implementations of the
abstract storage contracts without requiring external network or database dependencies.
"""

from __future__ import annotations

import asyncio
import uuid
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Sequence

from src.domain.errors import IngestionError
from src.domain.ingestion import IngestionStats
from src.domain.job import JobRecord
from src.domain.source import SourceInfo
from src.storage.base import (
    BaseIngestionRunRepository,
    BaseJobRepository,
    BaseSnapshotRepository,
    BaseSourceHealthRepository,
    IngestionSnapshotRecord,
    RepositoryWriteResult,
    SourceHealthRecord,
)


class InMemoryJobRepository(BaseJobRepository):
    """In-memory implementation of BaseJobRepository."""

    def __init__(self) -> None:
        self._jobs: Dict[str, JobRecord] = {}
        self._lock = asyncio.Lock()

    async def save_jobs(self, jobs: Sequence[JobRecord]) -> RepositoryWriteResult:
        """Upsert jobs in memory by canonical_id."""
        async with self._lock:
            for job in jobs:
                self._jobs[job.canonical_id] = job
            return RepositoryWriteResult(persisted_count=len(jobs), errors=[])

    async def get_job_by_canonical_id(self, canonical_id: str) -> Optional[JobRecord]:
        """Retrieve a job by its canonical_id."""
        async with self._lock:
            return self._jobs.get(canonical_id)

    async def get_jobs_by_canonical_ids(self, canonical_ids: Sequence[str]) -> List[JobRecord]:
        """Retrieve multiple jobs by canonical_ids."""
        async with self._lock:
            return [self._jobs[cid] for cid in canonical_ids if cid in self._jobs]

    async def list_jobs(
        self,
        source_name: Optional[str] = None,
        limit: int = 50,
        offset: int = 0,
    ) -> List[JobRecord]:
        """List jobs sorted by published_at descending."""
        async with self._lock:
            all_jobs = list(self._jobs.values())
            if source_name:
                all_jobs = [j for j in all_jobs if j.source_name == source_name]
            all_jobs.sort(key=lambda j: j.published_at, reverse=True)
            return all_jobs[offset : offset + limit]

    async def count_jobs(self, source_name: Optional[str] = None) -> int:
        async with self._lock:
            if not source_name:
                return len(self._jobs)
            return sum(1 for j in self._jobs.values() if j.source_name == source_name)

    async def clear(self) -> None:
        """Clear all stored jobs."""
        async with self._lock:
            self._jobs.clear()


class InMemoryIngestionRunRepository(BaseIngestionRunRepository):
    """In-memory implementation of BaseIngestionRunRepository."""

    def __init__(self) -> None:
        self._runs: Dict[str, dict[str, Any]] = {}
        self._lock = asyncio.Lock()

    async def save_ingestion_run(
        self,
        stats: IngestionStats,
        source_info: SourceInfo,
        errors: Sequence[IngestionError],
        run_id: Optional[str] = None,
    ) -> str:
        actual_run_id = run_id or f"run_{uuid.uuid4().hex[:12]}"
        record = {
            "run_id": actual_run_id,
            "source_name": stats.source_name,
            "status": stats.status.value,
            "started_at": stats.started_at,
            "completed_at": stats.completed_at,
            "duration_ms": stats.duration_ms,
            "records_received": stats.records_received,
            "records_accepted": stats.records_accepted,
            "records_rejected": stats.records_rejected,
            "duplicates_detected": stats.duplicates_detected,
            "retries": stats.retries,
            "failed_requests": stats.failed_requests,
            "errors": [err.model_dump() for err in errors],
            "source_info": source_info.model_dump(),
            "created_at": datetime.now(timezone.utc),
        }
        async with self._lock:
            self._runs[actual_run_id] = record
        return actual_run_id

    async def get_ingestion_run(self, run_id: str) -> Optional[dict[str, Any]]:
        async with self._lock:
            return self._runs.get(run_id)

    async def get_latest_run(self, source_name: str) -> Optional[dict[str, Any]]:
        async with self._lock:
            matching = [r for r in self._runs.values() if r["source_name"] == source_name]
            if not matching:
                return None
            matching.sort(key=lambda r: r["created_at"], reverse=True)
            return matching[0]

    async def list_runs(
        self,
        source_name: Optional[str] = None,
        limit: int = 50,
        offset: int = 0,
    ) -> List[dict[str, Any]]:
        async with self._lock:
            runs = list(self._runs.values())
            if source_name:
                runs = [r for r in runs if r["source_name"] == source_name.lower()]
            runs.sort(key=lambda r: r.get("started_at") or r["created_at"], reverse=True)
            return runs[offset : offset + limit]

    async def clear(self) -> None:
        async with self._lock:
            self._runs.clear()


class InMemorySourceHealthRepository(BaseSourceHealthRepository):
    """In-memory implementation of BaseSourceHealthRepository."""

    def __init__(self) -> None:
        self._health: Dict[str, SourceHealthRecord] = {}
        self._lock = asyncio.Lock()

    async def save_health(self, health: SourceHealthRecord) -> None:
        async with self._lock:
            self._health[health.source_name] = health

    async def get_health(self, source_name: str) -> Optional[SourceHealthRecord]:
        async with self._lock:
            return self._health.get(source_name)

    async def clear(self) -> None:
        async with self._lock:
            self._health.clear()


class InMemorySnapshotRepository(BaseSnapshotRepository):
    """In-memory implementation of BaseSnapshotRepository."""

    def __init__(self) -> None:
        self._snapshots: Dict[str, IngestionSnapshotRecord] = {}
        self._lock = asyncio.Lock()

    async def save_snapshot(self, snapshot: IngestionSnapshotRecord) -> None:
        async with self._lock:
            self._snapshots[snapshot.source_name] = snapshot

    async def get_latest_snapshot(self, source_name: str) -> Optional[IngestionSnapshotRecord]:
        async with self._lock:
            return self._snapshots.get(source_name)

    async def clear(self) -> None:
        async with self._lock:
            self._snapshots.clear()


class InMemoryStorage:
    """Unified in-memory storage holding all repositories."""

    def __init__(self) -> None:
        self.jobs = InMemoryJobRepository()
        self.runs = InMemoryIngestionRunRepository()
        self.health = InMemorySourceHealthRepository()
        self.snapshots = InMemorySnapshotRepository()

    async def clear_all(self) -> None:
        await self.jobs.clear()
        await self.runs.clear()
        await self.health.clear()
        await self.snapshots.clear()
