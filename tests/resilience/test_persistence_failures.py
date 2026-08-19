"""Resilience tests for Persistence Layer Failures and Telemetry Isolation.

Exercises:
1. Job Repository Write Failure: Source fetch succeeds, but DB job write fails ->
   run is marked FAILED, PERSISTENCE_ERROR is returned, no false success is reported.
2. Telemetry Write Failure Isolation: Jobs are successfully written to DB, but
   telemetry / snapshot / health write throws -> jobs remain safely persisted (non-destructive).
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest
from pytest_httpx import HTTPXMock

from src.adapters.wwr.config import PRIMARY_FEED_URL
from src.domain.enums import ErrorScope, IngestionErrorType, IngestionRunStatus
from src.domain.errors import IngestionError
from src.domain.ingestion import IngestionRequest
from src.services.orchestrator import IngestionService
from src.services.registry import SourceAdapterRegistry
from src.storage.base import RepositoryWriteResult
from tests.resilience.conftest import load_fixture, make_fast_adapter, make_service

pytestmark = pytest.mark.asyncio


class TestPersistenceFailuresResilience:
    async def test_job_persistence_failure_marks_run_failed(
        self, httpx_mock: HTTPXMock, memory_storage
    ):
        """Source fetch succeeds, but job repo write fails -> overall FAILED."""
        xml_bytes = load_fixture("wwr_valid.xml")
        httpx_mock.add_response(url=PRIMARY_FEED_URL, status_code=200, content=xml_bytes)

        adapter = make_fast_adapter(max_attempts=1)

        # Mock failing job repo
        failing_job_repo = MagicMock()
        failing_job_repo.save_jobs = AsyncMock(
            return_value=RepositoryWriteResult(
                persisted_count=0,
                errors=[
                    IngestionError(
                        error_type=IngestionErrorType.PERSISTENCE_ERROR,
                        scope=ErrorScope.RUN,
                        message="Database transaction rolled back: deadlock",
                    )
                ],
            )
        )

        registry = SourceAdapterRegistry()
        registry.register(adapter)
        service = IngestionService(
            job_repo=failing_job_repo,
            run_repo=memory_storage.runs,
            health_repo=memory_storage.health,
            snapshot_repo=memory_storage.snapshots,
            registry=registry,
        )

        result = await service.ingest(IngestionRequest())

        assert result.status == IngestionRunStatus.FAILED
        assert len(result.records) == 0
        assert any(e.error_type == IngestionErrorType.PERSISTENCE_ERROR for e in result.errors)

    async def test_telemetry_failure_does_not_destroy_persisted_jobs(
        self, httpx_mock: HTTPXMock, memory_storage
    ):
        """Jobs successfully persist to storage, but run telemetry write throws."""
        xml_bytes = load_fixture("wwr_valid.xml")
        httpx_mock.add_response(url=PRIMARY_FEED_URL, status_code=200, content=xml_bytes)

        adapter = make_fast_adapter(max_attempts=1)

        # Mock failing run repo
        failing_run_repo = MagicMock()
        failing_run_repo.save_ingestion_run = AsyncMock(side_effect=RuntimeError("Telemetry DB connection dropped"))

        registry = SourceAdapterRegistry()
        registry.register(adapter)
        service = IngestionService(
            job_repo=memory_storage.jobs,
            run_repo=failing_run_repo,
            health_repo=memory_storage.health,
            snapshot_repo=memory_storage.snapshots,
            registry=registry,
        )

        result = await service.ingest(IngestionRequest())

        # Jobs remain intact in repository
        persisted = await memory_storage.jobs.list_jobs(source_name="weworkremotely")
        assert len(persisted) == 3
        assert result.stats.records_accepted == 3

    async def test_snapshot_failure_does_not_destroy_persisted_jobs(
        self, httpx_mock: HTTPXMock, memory_storage
    ):
        """Jobs persist, but snapshot repo write throws."""
        xml_bytes = load_fixture("wwr_valid.xml")
        httpx_mock.add_response(url=PRIMARY_FEED_URL, status_code=200, content=xml_bytes)

        adapter = make_fast_adapter(max_attempts=1)

        failing_snap_repo = MagicMock()
        failing_snap_repo.save_snapshot = AsyncMock(side_effect=RuntimeError("Snapshot store unavailable"))

        registry = SourceAdapterRegistry()
        registry.register(adapter)
        service = IngestionService(
            job_repo=memory_storage.jobs,
            run_repo=memory_storage.runs,
            health_repo=memory_storage.health,
            snapshot_repo=failing_snap_repo,
            registry=registry,
        )

        result = await service.ingest(IngestionRequest())

        persisted = await memory_storage.jobs.list_jobs(source_name="weworkremotely")
        assert len(persisted) == 3
        assert result.stats.records_accepted == 3

    async def test_persistence_failure_preserves_last_success_at_and_does_not_mark_unreachable(
        self, httpx_mock: HTTPXMock, memory_storage
    ):
        """When source fetch succeeds but persistence fails:
        1. last_success_at is NOT advanced.
        2. source is NOT marked UNREACHABLE.
        3. result status is FAILED.
        4. subsequent successful run restores last_success_at and HEALTHY status.
        """
        from datetime import datetime, timezone
        from src.storage.base import SourceHealthRecord
        from src.domain.enums import SourceHealthStatus

        # Seed previous health
        prior_ts = datetime(2026, 8, 18, 8, 0, 0, tzinfo=timezone.utc)
        await memory_storage.health.save_health(
            SourceHealthRecord(
                source_name="weworkremotely",
                health_status=SourceHealthStatus.HEALTHY,
                endpoint="https://weworkremotely.com/remote-jobs.rss",
                last_success_at=prior_ts,
                consecutive_failures=0,
            )
        )

        xml_bytes = load_fixture("wwr_valid.xml")
        httpx_mock.add_response(url=PRIMARY_FEED_URL, status_code=200, content=xml_bytes)

        adapter = make_fast_adapter(max_attempts=1)

        failing_job_repo = MagicMock()
        failing_job_repo.save_jobs = AsyncMock(
            return_value=RepositoryWriteResult(
                persisted_count=0,
                errors=[
                    IngestionError(
                        error_type=IngestionErrorType.PERSISTENCE_ERROR,
                        scope=ErrorScope.RUN,
                        message="Disk full",
                    )
                ],
            )
        )

        registry = SourceAdapterRegistry()
        registry.register(adapter)
        service = IngestionService(
            job_repo=failing_job_repo,
            run_repo=memory_storage.runs,
            health_repo=memory_storage.health,
            snapshot_repo=memory_storage.snapshots,
            registry=registry,
        )

        # Run 1: Persistence failure
        result = await service.ingest(IngestionRequest())

        assert result.status == IngestionRunStatus.FAILED
        assert any(e.error_type == IngestionErrorType.PERSISTENCE_ERROR for e in result.errors)

        health1 = await memory_storage.health.get_health("weworkremotely")
        assert health1 is not None
        # Must NOT be UNREACHABLE (source responded 200 OK)
        assert health1.health_status == SourceHealthStatus.HEALTHY
        assert health1.consecutive_failures == 0
        # Must NOT advance last_success_at
        assert health1.last_success_at == prior_ts
        assert "persistence_error" in (health1.last_error_details or {})

        # Run 2: Subsequent successful recovery run with working repository
        httpx_mock.add_response(url=PRIMARY_FEED_URL, status_code=200, content=xml_bytes)
        recovery_service = IngestionService(
            job_repo=memory_storage.jobs,
            run_repo=memory_storage.runs,
            health_repo=memory_storage.health,
            snapshot_repo=memory_storage.snapshots,
            registry=registry,
        )

        res2 = await recovery_service.ingest(IngestionRequest())
        assert res2.status == IngestionRunStatus.SUCCESS
        assert len(res2.records) == 3

        health2 = await memory_storage.health.get_health("weworkremotely")
        assert health2 is not None
        assert health2.health_status == SourceHealthStatus.HEALTHY
        assert health2.consecutive_failures == 0
        # Now last_success_at is updated to the new run's timestamp
        assert health2.last_success_at is not None
        assert health2.last_success_at > prior_ts

