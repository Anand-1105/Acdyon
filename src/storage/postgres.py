"""PostgreSQL / Supabase Storage Implementation.

Provides concrete implementations of the abstract storage repositories using
the Supabase / PostgREST client to perform atomic batch upserts and telemetry writes.
"""

from __future__ import annotations

import asyncio
import uuid
from typing import Any, List, Optional, Sequence

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
from src.storage.config import StorageConfig
from src.storage.errors import error_from_storage_exception
from src.storage.mapping import (
    ingestion_run_to_row,
    job_to_row,
    row_to_job,
    row_to_snapshot,
    row_to_source_health,
    snapshot_to_row,
    source_health_to_row,
)


class PostgresJobRepository(BaseJobRepository):
    """Supabase/PostgreSQL implementation of BaseJobRepository."""

    def __init__(self, client: Any) -> None:
        """Initialize with a Supabase or PostgREST client instance."""
        self._client = client

    async def save_jobs(self, jobs: Sequence[JobRecord]) -> RepositoryWriteResult:
        """Atomically upsert a batch of jobs in PostgreSQL/Supabase."""
        if not jobs:
            return RepositoryWriteResult(persisted_count=0, errors=[])

        rows = [job_to_row(job) for job in jobs]

        try:
            # PostgREST upsert with on_conflict on canonical_id
            def _do_upsert():
                return self._client.table("jobs").upsert(rows, on_conflict="canonical_id").execute()

            await asyncio.to_thread(_do_upsert)
            return RepositoryWriteResult(persisted_count=len(jobs), errors=[])
        except Exception as exc:
            err = error_from_storage_exception(exc, operation="save_jobs", details={"job_count": len(jobs)})
            return RepositoryWriteResult(persisted_count=0, errors=[err])

    async def get_job_by_canonical_id(self, canonical_id: str) -> Optional[JobRecord]:
        """Retrieve a job by canonical_id."""
        try:
            def _do_get():
                return self._client.table("jobs").select("*").eq("canonical_id", canonical_id).limit(1).execute()

            response = await asyncio.to_thread(_do_get)
            if response.data and len(response.data) > 0:
                return row_to_job(response.data[0])
            return None
        except Exception as exc:
            error_from_storage_exception(exc, operation="get_job_by_canonical_id", details={"canonical_id": canonical_id})
            return None

    async def get_jobs_by_canonical_ids(self, canonical_ids: Sequence[str]) -> List[JobRecord]:
        """Retrieve multiple jobs by canonical_ids."""
        if not canonical_ids:
            return []

        try:
            def _do_get_many():
                return self._client.table("jobs").select("*").in_("canonical_id", list(canonical_ids)).execute()

            response = await asyncio.to_thread(_do_get_many)
            return [row_to_job(r) for r in (response.data or [])]
        except Exception as exc:
            error_from_storage_exception(exc, operation="get_jobs_by_canonical_ids")
            return []

    async def list_jobs(
        self,
        source_name: Optional[str] = None,
        limit: int = 50,
        offset: int = 0,
    ) -> List[JobRecord]:
        """List jobs with pagination and optional source filtering."""
        try:
            def _do_list():
                query = self._client.table("jobs").select("*").order("published_at", desc=True)
                if source_name:
                    query = query.eq("source_name", source_name)
                return query.range(offset, offset + limit - 1).execute()

            response = await asyncio.to_thread(_do_list)
            return [row_to_job(r) for r in (response.data or [])]
        except Exception as exc:
            error_from_storage_exception(exc, operation="list_jobs")
            return []

    async def count_jobs(self, source_name: Optional[str] = None) -> int:
        """Count total jobs with optional source filtering."""
        try:
            def _do_count():
                query = self._client.table("jobs").select("canonical_id", count="exact", head=True)
                if source_name:
                    query = query.eq("source_name", source_name)
                return query.execute()

            response = await asyncio.to_thread(_do_count)
            return response.count if response.count is not None else 0
        except Exception as exc:
            error_from_storage_exception(exc, operation="count_jobs")
            return 0


class PostgresIngestionRunRepository(BaseIngestionRunRepository):
    """Supabase/PostgreSQL implementation of BaseIngestionRunRepository."""

    def __init__(self, client: Any) -> None:
        self._client = client

    async def save_ingestion_run(
        self,
        stats: IngestionStats,
        source_info: SourceInfo,
        errors: Sequence[IngestionError],
        run_id: Optional[str] = None,
    ) -> str:
        actual_run_id = run_id or f"run_{uuid.uuid4().hex[:12]}"
        row = ingestion_run_to_row(actual_run_id, stats, source_info, list(errors))

        try:
            def _do_save_run():
                return self._client.table("ingestion_runs").insert(row).execute()

            await asyncio.to_thread(_do_save_run)
            return actual_run_id
        except Exception as exc:
            error_from_storage_exception(exc, operation="save_ingestion_run", details={"run_id": actual_run_id})
            return actual_run_id

    async def get_ingestion_run(self, run_id: str) -> Optional[dict[str, Any]]:
        try:
            def _do_get():
                return self._client.table("ingestion_runs").select("*").eq("run_id", run_id).limit(1).execute()

            response = await asyncio.to_thread(_do_get)
            if response.data and len(response.data) > 0:
                return response.data[0]
            return None
        except Exception as exc:
            error_from_storage_exception(exc, operation="get_ingestion_run", details={"run_id": run_id})
            return None

    async def get_latest_run(self, source_name: str) -> Optional[dict[str, Any]]:
        try:
            def _do_get_latest():
                return (
                    self._client.table("ingestion_runs")
                    .select("*")
                    .eq("source_name", source_name)
                    .order("created_at", desc=True)
                    .limit(1)
                    .execute()
                )

            response = await asyncio.to_thread(_do_get_latest)
            if response.data and len(response.data) > 0:
                return response.data[0]
            return None
        except Exception as exc:
            error_from_storage_exception(exc, operation="get_latest_run", details={"source_name": source_name})
            return None

    async def list_runs(
        self,
        source_name: Optional[str] = None,
        limit: int = 50,
        offset: int = 0,
    ) -> List[dict[str, Any]]:
        try:
            def _do_list():
                query = (
                    self._client.table("ingestion_runs")
                    .select("*")
                    .order("started_at", desc=True)
                    .limit(limit)
                    .offset(offset)
                )
                if source_name:
                    query = query.eq("source_name", source_name)
                return query.execute()

            response = await asyncio.to_thread(_do_list)
            return response.data or []
        except Exception as exc:
            error_from_storage_exception(exc, operation="list_runs", details={"source_name": source_name})
            return []


class PostgresSourceHealthRepository(BaseSourceHealthRepository):
    """Supabase/PostgreSQL implementation of BaseSourceHealthRepository."""

    def __init__(self, client: Any) -> None:
        self._client = client

    async def save_health(self, health: SourceHealthRecord) -> None:
        row = source_health_to_row(health)
        try:
            def _do_save_health():
                return self._client.table("source_health").upsert(row, on_conflict="source_name").execute()

            await asyncio.to_thread(_do_save_health)
        except Exception as exc:
            error_from_storage_exception(exc, operation="save_health", details={"source_name": health.source_name})

    async def get_health(self, source_name: str) -> Optional[SourceHealthRecord]:
        try:
            def _do_get_health():
                return self._client.table("source_health").select("*").eq("source_name", source_name).limit(1).execute()

            response = await asyncio.to_thread(_do_get_health)
            if response.data and len(response.data) > 0:
                return row_to_source_health(response.data[0])
            return None
        except Exception as exc:
            error_from_storage_exception(exc, operation="get_health", details={"source_name": source_name})
            return None


class PostgresSnapshotRepository(BaseSnapshotRepository):
    """Supabase/PostgreSQL implementation of BaseSnapshotRepository."""

    def __init__(self, client: Any) -> None:
        self._client = client

    async def save_snapshot(self, snapshot: IngestionSnapshotRecord) -> None:
        row = snapshot_to_row(snapshot)
        try:
            def _do_save():
                return self._client.table("ingestion_snapshots").upsert(row, on_conflict="source_name").execute()

            await asyncio.to_thread(_do_save)
        except Exception as exc:
            error_from_storage_exception(exc, operation="save_snapshot", details={"source_name": snapshot.source_name})

    async def get_latest_snapshot(self, source_name: str) -> Optional[IngestionSnapshotRecord]:
        try:
            def _do_get():
                return self._client.table("ingestion_snapshots").select("*").eq("source_name", source_name).limit(1).execute()

            response = await asyncio.to_thread(_do_get)
            if response.data and len(response.data) > 0:
                return row_to_snapshot(response.data[0])
            return None
        except Exception as exc:
            error_from_storage_exception(exc, operation="get_latest_snapshot", details={"source_name": source_name})
            return None


class PostgresStorage:
    """Unified repository container using Supabase/PostgreSQL."""

    def __init__(self, client: Any) -> None:
        self.jobs = PostgresJobRepository(client)
        self.runs = PostgresIngestionRunRepository(client)
        self.health = PostgresSourceHealthRepository(client)
        self.snapshots = PostgresSnapshotRepository(client)
