"""Automated Unit & Integration Tests for FastAPI Presentation Layer (src/api/).

Uses FastAPI TestClient and dependency_overrides with InMemoryStorage and test doubles.
Zero external network I/O or production database calls.
"""

from __future__ import annotations

from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi.testclient import TestClient

from src.api.app import app
from src.api.deps import (
    get_health_repository,
    get_ingestion_service,
    get_job_repository,
    get_run_repository,
)
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
from src.domain.ingestion import IngestionResult, IngestionStats
from src.domain.job import JobRecord
from src.domain.source import SourceInfo
from src.services.orchestrator import IngestionService
from src.storage.base import SourceHealthRecord
from src.storage.memory import InMemoryStorage


def _create_sample_job(canonical_id: str) -> JobRecord:
    return JobRecord(
        canonical_id=canonical_id,
        source_name="weworkremotely",
        source_url=f"https://weworkremotely.com/jobs/{canonical_id}",
        title="Backend Engineer",
        company="Acme Corp",
        location="Remote",
        description="Job description",
        employment_type=EmploymentType.FULL_TIME,
        published_at=datetime(2026, 8, 18, 10, 0, 0, tzinfo=timezone.utc),
    )


@pytest.fixture
def storage():
    return InMemoryStorage()


@pytest.fixture
def client(storage):
    # Dependency overrides for isolated API testing
    app.dependency_overrides[get_job_repository] = lambda: storage.jobs
    app.dependency_overrides[get_run_repository] = lambda: storage.runs
    app.dependency_overrides[get_health_repository] = lambda: storage.health

    test_client = TestClient(app)
    yield test_client
    app.dependency_overrides.clear()


class TestOperationalEndpoints:
    def test_health_liveness_check(self, client):
        response = client.get("/health")
        assert response.status_code == 200
        assert response.json() == {"status": "ok"}


class TestIngestEndpoint:
    def test_ingest_success_flow(self, client):
        mock_service = MagicMock(spec=IngestionService)
        job = _create_sample_job("wwr_api_01")
        started = datetime(2026, 8, 18, 10, 0, 0, tzinfo=timezone.utc)
        completed = datetime(2026, 8, 18, 10, 0, 1, tzinfo=timezone.utc)

        result = IngestionResult(
            status=IngestionRunStatus.SUCCESS,
            records=[job],
            stats=IngestionStats(
                source_name="weworkremotely",
                started_at=started,
                completed_at=completed,
                duration_ms=1000,
                records_received=1,
                records_accepted=1,
                status=IngestionRunStatus.SUCCESS,
            ),
            errors=[],
            source_info=SourceInfo(
                source_name="weworkremotely",
                source_type=SourceType.RSS,
                endpoint="https://weworkremotely.com/remote-jobs.rss",
                retrieval_timestamp=started,
                health_status=SourceHealthStatus.HEALTHY,
            ),
        )
        mock_service.ingest = AsyncMock(return_value=result)
        app.dependency_overrides[get_ingestion_service] = lambda: mock_service

        response = client.post("/api/v1/ingest", json={"source_name": "weworkremotely"})

        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "success"
        assert data["records_count"] == 1
        assert data["records"][0]["canonical_id"] == "wwr_api_01"

    def test_ingest_unsupported_source_returns_400(self, client):
        mock_service = MagicMock(spec=IngestionService)
        started = datetime.now(timezone.utc)
        result = IngestionResult(
            status=IngestionRunStatus.FAILED,
            records=[],
            stats=IngestionStats(
                source_name="unregistered",
                started_at=started,
                completed_at=started,
                duration_ms=0,
                status=IngestionRunStatus.FAILED,
            ),
            errors=[
                IngestionError(
                    error_type=IngestionErrorType.VALIDATION_ERROR,
                    scope=ErrorScope.RUN,
                    message="Unsupported ingestion source: 'unregistered'",
                )
            ],
            source_info=SourceInfo(
                source_name="unregistered",
                source_type=SourceType.SANDBOX,
                endpoint="unregistered",
                retrieval_timestamp=started,
            ),
        )
        mock_service.ingest = AsyncMock(return_value=result)
        app.dependency_overrides[get_ingestion_service] = lambda: mock_service

        response = client.post("/api/v1/ingest", json={"source_name": "unregistered"})

        assert response.status_code == 400
        assert "Unsupported ingestion source" in response.json()["detail"]

    def test_ingest_upstream_failure_returns_502(self, client):
        mock_service = MagicMock(spec=IngestionService)
        started = datetime.now(timezone.utc)
        result = IngestionResult(
            status=IngestionRunStatus.FAILED,
            records=[],
            stats=IngestionStats(
                source_name="weworkremotely",
                started_at=started,
                completed_at=started,
                duration_ms=0,
                status=IngestionRunStatus.FAILED,
            ),
            errors=[
                IngestionError(
                    error_type=IngestionErrorType.TIMEOUT_ERROR,
                    scope=ErrorScope.RUN,
                    message="Connection timeout",
                )
            ],
            source_info=SourceInfo(
                source_name="weworkremotely",
                source_type=SourceType.RSS,
                endpoint="https://weworkremotely.com/remote-jobs.rss",
                retrieval_timestamp=started,
            ),
        )
        mock_service.ingest = AsyncMock(return_value=result)
        app.dependency_overrides[get_ingestion_service] = lambda: mock_service

        response = client.post("/api/v1/ingest", json={"source_name": "weworkremotely"})

        assert response.status_code == 502
        assert "Connection timeout" in response.json()["detail"]

    def test_ingest_oversized_limit_rejected_by_pydantic_422(self, client):
        response = client.post("/api/v1/ingest", json={"source_name": "weworkremotely", "limit": 9999})
        assert response.status_code == 422


class TestJobsEndpoints:
    @pytest.mark.asyncio
    async def test_list_jobs_and_get_by_id(self, client, storage):
        j1 = _create_sample_job("wwr_job_001")
        j2 = _create_sample_job("wwr_job_002")
        await storage.jobs.save_jobs([j1, j2])

        # List jobs
        res = client.get("/api/v1/jobs")
        assert res.status_code == 200
        data = res.json()
        assert len(data) == 2

        # Get existing single job
        res_single = client.get("/api/v1/jobs/wwr_job_001")
        assert res_single.status_code == 200
        assert res_single.json()["canonical_id"] == "wwr_job_001"

        # Get non-existing single job -> 404
        res_404 = client.get("/api/v1/jobs/nonexistent_id")
        assert res_404.status_code == 404


class TestSourceHealthEndpoint:
    @pytest.mark.asyncio
    async def test_get_source_health(self, client, storage):
        health = SourceHealthRecord(
            source_name="weworkremotely",
            health_status=SourceHealthStatus.HEALTHY,
            endpoint="https://weworkremotely.com/remote-jobs.rss",
            consecutive_failures=0,
        )
        await storage.health.save_health(health)

        # Existing source health -> 200 OK
        res = client.get("/api/v1/health/weworkremotely")
        assert res.status_code == 200
        data = res.json()
        assert data["source_name"] == "weworkremotely"
        assert data["health_status"] == "healthy"

        # Non-existing source health -> 404 Not Found
        res_404 = client.get("/api/v1/health/unknown_source")
        assert res_404.status_code == 404


class TestDependencyInjectionLifecycle:
    def test_shared_registry_and_rate_limiter_instance_reused(self):
        from src.api.deps import get_source_adapter_registry, get_ingestion_service, get_job_repository, get_run_repository, get_health_repository, get_snapshot_repository
        from src.storage.memory import InMemoryStorage

        reg1 = get_source_adapter_registry()
        reg2 = get_source_adapter_registry()
        assert reg1 is reg2

        adapter1 = reg1.get("weworkremotely")
        adapter2 = reg2.get("weworkremotely")
        assert adapter1 is adapter2
        assert adapter1._limiter is adapter2._limiter

        # Ingestion service injected with registry retains the same adapter & limiter
        storage = InMemoryStorage()
        svc1 = get_ingestion_service(
            job_repo=storage.jobs,
            run_repo=storage.runs,
            health_repo=storage.health,
            snapshot_repo=storage.snapshots,
            registry=reg1,
        )
        svc2 = get_ingestion_service(
            job_repo=storage.jobs,
            run_repo=storage.runs,
            health_repo=storage.health,
            snapshot_repo=storage.snapshots,
            registry=reg2,
        )
        assert svc1._registry.get("weworkremotely") is svc2._registry.get("weworkremotely")
