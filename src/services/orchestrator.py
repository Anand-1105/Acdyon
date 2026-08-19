"""Ingestion Orchestration Service.

Coordinates the end-to-end job ingestion lifecycle:
1. Request validation
2. Source adapter resolution via SourceAdapterRegistry
3. Feed retrieval and normalization via BaseSourceAdapter
4. Batch-level in-memory deduplication
5. Status evaluation (SUCCESS, PARTIAL_SUCCESS, FAILED)
6. Ordered persistence (Jobs -> Run Telemetry -> Snapshot -> Source Health)
7. Final IngestionResult construction

Zero coupling to FastAPI, HTML/XML parsing, HTTP sockets, or raw SQL.
"""

from __future__ import annotations

import logging
import uuid
from datetime import datetime, timezone
from typing import List, Optional, Set

from src.adapters.base import ParsedBatch
from src.domain.enums import (
    ErrorScope,
    IngestionErrorType,
    IngestionRunStatus,
    SourceHealthStatus,
    SourceType,
)
from src.domain.errors import IngestionError
from src.domain.ingestion import IngestionRequest, IngestionResult, IngestionStats
from src.domain.job import JobRecord
from src.domain.source import SourceInfo
from src.services.registry import SourceAdapterRegistry
from src.storage.base import (
    BaseIngestionRunRepository,
    BaseJobRepository,
    BaseSnapshotRepository,
    BaseSourceHealthRepository,
    IngestionSnapshotRecord,
    SourceHealthRecord,
)

logger = logging.getLogger(__name__)


class IngestionService:
    """Central orchestration service coordinating end-to-end ingestion runs."""

    def __init__(
        self,
        job_repo: BaseJobRepository,
        run_repo: BaseIngestionRunRepository,
        health_repo: BaseSourceHealthRepository,
        snapshot_repo: BaseSnapshotRepository,
        registry: Optional[SourceAdapterRegistry] = None,
    ) -> None:
        self._job_repo = job_repo
        self._run_repo = run_repo
        self._health_repo = health_repo
        self._snapshot_repo = snapshot_repo
        self._registry = registry or SourceAdapterRegistry.default_registry()

    async def ingest(self, request: Optional[IngestionRequest] = None) -> IngestionResult:
        """Execute one ingestion run according to the canonical request.

        Args:
            request: Canonical parameters. If None, default IngestionRequest() is used.

        Returns:
            IngestionResult containing canonical records, operational stats,
            structured errors, and source metadata.
        """
        started_at = datetime.now(timezone.utc)
        req = request or IngestionRequest()
        source_name = (req.source_name or "weworkremotely").strip().lower()

        logger.info("Starting ingestion run: source=%s", source_name)

        # -------------------------------------------------------------------
        # Step 1: Validate Source Selection
        # -------------------------------------------------------------------
        adapter = self._registry.get(source_name)
        if adapter is None:
            completed_at = datetime.now(timezone.utc)
            duration_ms = max(0, int((completed_at - started_at).total_seconds() * 1000))
            error = IngestionError(
                error_type=IngestionErrorType.VALIDATION_ERROR,
                scope=ErrorScope.RUN,
                message=f"Unsupported ingestion source: '{source_name}'. Registered sources: {self._registry.list_sources()}",
                retryable=False,
                timestamp=completed_at,
            )
            stats = IngestionStats(
                source_name=source_name,
                started_at=started_at,
                completed_at=completed_at,
                duration_ms=duration_ms,
                records_received=0,
                records_accepted=0,
                records_rejected=0,
                status=IngestionRunStatus.FAILED,
            )
            placeholder_source_info = SourceInfo(
                source_name=source_name,
                source_type=SourceType.SANDBOX,
                endpoint="unregistered",
                retrieval_timestamp=completed_at,
                health_status=SourceHealthStatus.UNKNOWN,
            )
            return IngestionResult(
                status=IngestionRunStatus.FAILED,
                records=[],
                stats=stats,
                errors=[error],
                source_info=placeholder_source_info,
            )

        # -------------------------------------------------------------------
        # Step 2: Execute Source Adapter
        # -------------------------------------------------------------------
        batch: ParsedBatch = await adapter.fetch_and_parse(req)
        completed_at = datetime.now(timezone.utc)
        duration_ms = max(0, int((completed_at - started_at).total_seconds() * 1000))

        all_errors: List[IngestionError] = list(batch.errors)

        # Evaluate if the source fetch/parse failed at the feed level
        is_source_failed = any(e.scope == ErrorScope.RUN for e in batch.errors) or (
            len(batch.records) == 0 and len(batch.errors) > 0
        )

        # -------------------------------------------------------------------
        # Step 3: In-Memory Batch Deduplication
        # -------------------------------------------------------------------
        seen_ids: Set[str] = set()
        unique_records: List[JobRecord] = []
        duplicate_count = 0

        for job in batch.records:
            if job.canonical_id in seen_ids:
                duplicate_count += 1
            else:
                seen_ids.add(job.canonical_id)
                unique_records.append(job)

        # Apply request limit filter if specified
        if req.limit is not None and req.limit > 0:
            unique_records = unique_records[: req.limit]

        # -------------------------------------------------------------------
        # Step 4: Status Evaluation
        # -------------------------------------------------------------------
        if is_source_failed:
            status = IngestionRunStatus.FAILED
        elif len(all_errors) > 0:
            status = IngestionRunStatus.PARTIAL_SUCCESS
        else:
            status = IngestionRunStatus.SUCCESS

        # -------------------------------------------------------------------
        # Step 5: Ordered Persistence
        # -------------------------------------------------------------------
        persisted_jobs: List[JobRecord] = []

        # A. Job Records Persistence (correctness-critical)
        if unique_records and status != IngestionRunStatus.FAILED:
            write_res = await self._job_repo.save_jobs(unique_records)
            if not write_res.is_success:
                all_errors.extend(write_res.errors)
                status = IngestionRunStatus.FAILED
                persisted_jobs = []
            else:
                persisted_jobs = unique_records
        else:
            persisted_jobs = []

        # B. Run Telemetry Persistence (operational)
        records_rejected = len([e for e in all_errors if e.scope == ErrorScope.RECORD])
        stats = IngestionStats(
            source_name=source_name,
            started_at=started_at,
            completed_at=completed_at,
            duration_ms=duration_ms,
            records_received=batch.raw_count,
            records_accepted=len(persisted_jobs),
            records_rejected=records_rejected,
            duplicates_detected=duplicate_count,
            retries=0,
            failed_requests=0,
            status=status,
        )

        run_id = f"run_{uuid.uuid4().hex[:12]}"
        try:
            run_id = await self._run_repo.save_ingestion_run(
                stats=stats,
                source_info=batch.source_info,
                errors=all_errors,
                run_id=run_id,
            )
        except Exception as exc:
            logger.warning("Telemetry save failed for run %s: %s", run_id, exc)

        # C. Snapshot Persistence (reference state for last-known-good)
        if status in (IngestionRunStatus.SUCCESS, IngestionRunStatus.PARTIAL_SUCCESS) and len(persisted_jobs) > 0:
            try:
                snapshot = IngestionSnapshotRecord(
                    source_name=source_name,
                    run_id=run_id,
                    canonical_ids=[j.canonical_id for j in persisted_jobs],
                    job_count=len(persisted_jobs),
                    snapshot_timestamp=completed_at,
                )
                await self._snapshot_repo.save_snapshot(snapshot)
            except Exception as exc:
                logger.warning("Snapshot save failed for source %s: %s", source_name, exc)

        # D. Source Health State Update
        try:
            prev_health = await self._health_repo.get_health(source_name)
        except Exception:
            prev_health = None

        if is_source_failed:
            consec = (prev_health.consecutive_failures if prev_health else 0) + 1
            health_status = SourceHealthStatus.UNREACHABLE if consec >= 3 else SourceHealthStatus.DEGRADED
            last_success_at = prev_health.last_success_at if prev_health else None
            last_failure_at = completed_at
            last_error_details = {"message": all_errors[0].message if all_errors else "Source failure"}
        elif status == IngestionRunStatus.FAILED:
            # Upstream source succeeded, but downstream persistence failed.
            # Preserve prior last_success_at without marking the external source unreachable.
            health_status = SourceHealthStatus.HEALTHY
            consec = 0
            last_success_at = prev_health.last_success_at if prev_health else None
            last_failure_at = prev_health.last_failure_at if prev_health else None
            last_error_details = {"persistence_error": all_errors[0].message if all_errors else "Persistence failure"}
        elif status == IngestionRunStatus.PARTIAL_SUCCESS:
            health_status = SourceHealthStatus.DEGRADED
            consec = 0
            last_success_at = completed_at
            last_failure_at = prev_health.last_failure_at if prev_health else None
            last_error_details = {"record_errors": records_rejected}
        else:
            health_status = SourceHealthStatus.HEALTHY
            consec = 0
            last_success_at = completed_at
            last_failure_at = prev_health.last_failure_at if prev_health else None
            last_error_details = None

        try:
            health_record = SourceHealthRecord(
                source_name=source_name,
                health_status=health_status,
                endpoint=batch.source_info.endpoint if batch.source_info else "unknown",
                last_success_at=last_success_at,
                last_failure_at=last_failure_at,
                consecutive_failures=consec,
                last_error_details=last_error_details,
                updated_at=completed_at,
            )
            await self._health_repo.save_health(health_record)
        except Exception as exc:
            logger.warning("Source health save failed for source %s: %s", source_name, exc)

        # -------------------------------------------------------------------
        # Step 6: Construct Final IngestionResult
        # -------------------------------------------------------------------
        return IngestionResult(
            status=status,
            records=persisted_jobs,
            stats=stats,
            errors=all_errors,
            source_info=batch.source_info,
        )
