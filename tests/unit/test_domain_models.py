"""Unit tests for Canonical Domain Contracts.

Tests coverage:
- Valid JobRecord creation and defaults
- Required field enforcement
- Invalid field types, lengths, and URLs
- Timezone normalization (naive -> UTC)
- SalaryInfo range validation
- Immutability / frozen model enforcement
- SourceInfo creation and validation
- IngestionRequest parameter constraints
- IngestionStats timing and counter validation
- IngestionError classification, scope, and secret redaction
- IngestionResult full-success, partial-success, and failure representations
"""

from datetime import datetime, timedelta, timezone
from decimal import Decimal
import pytest
from pydantic import ValidationError

from src.domain import (
    EmploymentType,
    ErrorScope,
    IngestionError,
    IngestionErrorType,
    IngestionRequest,
    IngestionResult,
    IngestionStats,
    JobRecord,
    JobStatus,
    SalaryInfo,
    SourceHealthStatus,
    SourceInfo,
    SourceType,
    IngestionRunStatus,
)


class TestJobRecord:
    """Test suite for JobRecord and SalaryInfo canonical domain models."""

    def test_valid_job_record_creation(self):
        published = datetime.now(timezone.utc) - timedelta(hours=2)
        job = JobRecord(
            canonical_id="wwr_1234567890abcdef",
            source_name="weworkremotely",
            source_id="guid-9876",
            source_url="https://weworkremotely.com/jobs/senior-backend-engineer",
            title="Senior Backend Engineer",
            company="Acme Corp",
            location="Remote (US/EU)",
            description="<p>We are seeking a senior backend engineer...</p>",
            employment_type=EmploymentType.FULL_TIME,
            salary=SalaryInfo(
                currency="USD",
                min_amount=Decimal("130000"),
                max_amount=Decimal("160000"),
                interval="yearly",
                raw_text="$130,000 - $160,000 / year",
            ),
            requirements=["Python", "FastAPI", "PostgreSQL"],
            published_at=published,
            status=JobStatus.ACTIVE,
            metadata={"category": "Back-End Programming"},
        )

        assert job.canonical_id == "wwr_1234567890abcdef"
        assert job.source_name == "weworkremotely"
        assert job.source_id == "guid-9876"
        assert job.title == "Senior Backend Engineer"
        assert job.company == "Acme Corp"
        assert job.location == "Remote (US/EU)"
        assert job.employment_type == EmploymentType.FULL_TIME
        assert job.salary is not None
        assert job.salary.min_amount == Decimal("130000")
        assert job.published_at == published
        assert job.ingested_at.tzinfo == timezone.utc
        assert job.status == JobStatus.ACTIVE
        assert job.requirements == ["Python", "FastAPI", "PostgreSQL"]
        assert job.metadata["category"] == "Back-End Programming"

    def test_minimal_valid_job_record_with_defaults(self):
        job = JobRecord(
            canonical_id="generic_abc123",
            source_name="generic_source",
            source_url="https://example.com/jobs/123",
            title="Staff Engineer",
            company="Example Inc",
            description="Job description details...",
            published_at=datetime(2026, 8, 1, 12, 0, 0, tzinfo=timezone.utc),
        )

        assert job.location == "Remote"
        assert job.employment_type == EmploymentType.UNKNOWN
        assert job.salary is None
        assert job.requirements == []
        assert job.status == JobStatus.ACTIVE
        assert job.source_id is None
        assert isinstance(job.ingested_at, datetime)

    def test_missing_required_fields_raises_validation_error(self):
        with pytest.raises(ValidationError) as exc_info:
            JobRecord(
                canonical_id="wwr_123",
                source_name="weworkremotely",
                # missing source_url, title, company, description, published_at
            )
        errors = exc_info.value.errors()
        missing_fields = {e["loc"][0] for e in errors}
        assert "source_url" in missing_fields
        assert "title" in missing_fields
        assert "company" in missing_fields
        assert "description" in missing_fields
        assert "published_at" in missing_fields

    def test_invalid_url_scheme(self):
        with pytest.raises(ValidationError) as exc_info:
            JobRecord(
                canonical_id="wwr_123",
                source_name="weworkremotely",
                source_url="ftp://invalid-scheme.com/job",
                title="Engineer",
                company="Acme",
                description="Details",
                published_at=datetime.now(timezone.utc),
            )
        assert "source_url must have 'http' or 'https' scheme" in str(exc_info.value)

    def test_url_missing_host(self):
        with pytest.raises(ValidationError) as exc_info:
            JobRecord(
                canonical_id="wwr_123",
                source_name="weworkremotely",
                source_url="https:///only-path",
                title="Engineer",
                company="Acme",
                description="Details",
                published_at=datetime.now(timezone.utc),
            )
        assert "must include a valid host/domain" in str(exc_info.value)

    def test_naive_datetime_normalized_to_utc(self):
        naive_dt = datetime(2026, 8, 1, 10, 30, 0)
        job = JobRecord(
            canonical_id="wwr_123",
            source_name="weworkremotely",
            source_url="https://example.com/job/1",
            title="Engineer",
            company="Acme",
            description="Details",
            published_at=naive_dt,
        )
        assert job.published_at.tzinfo == timezone.utc
        assert job.published_at.year == 2026

    def test_oversized_text_fields(self):
        with pytest.raises(ValidationError) as exc_info:
            JobRecord(
                canonical_id="wwr_123",
                source_name="weworkremotely",
                source_url="https://example.com/job/1",
                title="X" * 513,  # Max length 512
                company="Acme",
                description="Details",
                published_at=datetime.now(timezone.utc),
            )
        assert "title" in str(exc_info.value)

    def test_frozen_immutability(self):
        job = JobRecord(
            canonical_id="wwr_123",
            source_name="weworkremotely",
            source_url="https://example.com/job/1",
            title="Engineer",
            company="Acme",
            description="Details",
            published_at=datetime.now(timezone.utc),
        )
        with pytest.raises(ValidationError):
            job.title = "Modified Title"  # type: ignore

    def test_salary_info_validations(self):
        # Valid salary
        salary = SalaryInfo(
            currency="USD",
            min_amount=Decimal("100000"),
            max_amount=Decimal("140000"),
            interval="yearly",
        )
        assert salary.min_amount == Decimal("100000")

        # Invalid salary: min > max
        with pytest.raises(ValidationError) as exc_info:
            SalaryInfo(
                min_amount=Decimal("150000"),
                max_amount=Decimal("100000"),
            )
        assert "min_amount (150000) cannot exceed max_amount (100000)" in str(exc_info.value)

        # Invalid negative amount
        with pytest.raises(ValidationError):
            SalaryInfo(min_amount=Decimal("-500"))


class TestSourceInfo:
    """Test suite for SourceInfo metadata model."""

    def test_valid_source_info(self):
        source = SourceInfo(
            source_name="weworkremotely",
            source_type=SourceType.RSS,
            endpoint="https://weworkremotely.com/remote-jobs.rss",
            attribution="Data provided by We Work Remotely public feed",
            health_status=SourceHealthStatus.HEALTHY,
            metadata={"feed_etag": '"abc-123"'},
        )
        assert source.source_name == "weworkremotely"
        assert source.source_type == SourceType.RSS
        assert source.endpoint == "https://weworkremotely.com/remote-jobs.rss"
        assert source.health_status == SourceHealthStatus.HEALTHY
        assert source.retrieval_timestamp.tzinfo == timezone.utc

    def test_missing_source_endpoint(self):
        with pytest.raises(ValidationError):
            SourceInfo(
                source_name="weworkremotely",
                source_type=SourceType.RSS,
                # missing endpoint
            )


class TestIngestionRequest:
    """Test suite for IngestionRequest parameter model."""

    def test_valid_request_with_filters(self):
        req = IngestionRequest(
            source_name="weworkremotely",
            search_term="Python",
            location="Remote",
            category="programming",
            limit=50,
            since=datetime(2026, 8, 1, 0, 0, tzinfo=timezone.utc),
        )
        assert req.search_term == "Python"
        assert req.limit == 50
        assert req.category == "programming"

    def test_limit_bounds_validation(self):
        with pytest.raises(ValidationError):
            IngestionRequest(limit=0)  # ge=1

        with pytest.raises(ValidationError):
            IngestionRequest(limit=1001)  # le=1000


class TestIngestionStats:
    """Test suite for IngestionStats metrics model."""

    def test_valid_stats(self):
        t0 = datetime(2026, 8, 18, 10, 0, 0, tzinfo=timezone.utc)
        t1 = datetime(2026, 8, 18, 10, 0, 5, tzinfo=timezone.utc)
        stats = IngestionStats(
            source_name="weworkremotely",
            started_at=t0,
            completed_at=t1,
            duration_ms=5000,
            records_received=100,
            records_accepted=95,
            records_rejected=5,
            duplicates_detected=12,
            retries=1,
            failed_requests=0,
            status=IngestionRunStatus.SUCCESS,
        )
        assert stats.records_received == 100
        assert stats.duration_ms == 5000
        assert stats.status == IngestionRunStatus.SUCCESS

    def test_invalid_completed_before_started(self):
        t0 = datetime(2026, 8, 18, 10, 0, 5, tzinfo=timezone.utc)
        t1 = datetime(2026, 8, 18, 10, 0, 0, tzinfo=timezone.utc)
        with pytest.raises(ValidationError) as exc_info:
            IngestionStats(
                source_name="weworkremotely",
                started_at=t0,
                completed_at=t1,
                duration_ms=0,
                status=IngestionRunStatus.FAILED,
            )
        assert "completed_at cannot be earlier than started_at" in str(exc_info.value)


class TestIngestionError:
    """Test suite for IngestionError classification and security redaction."""

    def test_structured_error_creation(self):
        err = IngestionError(
            error_type=IngestionErrorType.RATE_LIMIT_ERROR,
            scope=ErrorScope.REQUEST,
            message="HTTP 429 Too Many Requests received from remote endpoint",
            details={"status_code": 429, "retry_after": 60},
            retryable=True,
        )
        assert err.error_type == IngestionErrorType.RATE_LIMIT_ERROR
        assert err.scope == ErrorScope.REQUEST
        assert err.retryable is True
        assert err.timestamp.tzinfo == timezone.utc

    def test_sensitive_headers_and_tokens_redacted_automatically(self):
        err = IngestionError(
            error_type=IngestionErrorType.NETWORK_TRANSPORT_ERROR,
            scope=ErrorScope.REQUEST,
            message="Connection failed",
            details={
                "Authorization": "Bearer secret_jwt_token_12345",
                "api_key": "live_key_xyz987",
                "normal_header": "application/rss+xml",
                "nested": {
                    "password": "db_secret_pass",
                    "safe_count": 3,
                },
            },
        )
        assert err.details["Authorization"] == "[REDACTED]"
        assert err.details["api_key"] == "[REDACTED]"
        assert err.details["normal_header"] == "application/rss+xml"
        assert err.details["nested"]["password"] == "[REDACTED]"
        assert err.details["nested"]["safe_count"] == 3


class TestIngestionResult:
    """Test suite for IngestionResult aggregation and status reporting."""

    def _sample_source_info(self) -> SourceInfo:
        return SourceInfo(
            source_name="weworkremotely",
            source_type=SourceType.RSS,
            endpoint="https://weworkremotely.com/remote-jobs.rss",
            health_status=SourceHealthStatus.HEALTHY,
        )

    def _sample_job(self, canonical_id: str) -> JobRecord:
        return JobRecord(
            canonical_id=canonical_id,
            source_name="weworkremotely",
            source_url=f"https://weworkremotely.com/jobs/{canonical_id}",
            title="Software Engineer",
            company="Tech Corp",
            description="Role details...",
            published_at=datetime.now(timezone.utc),
        )

    def test_full_success_result(self):
        source = self._sample_source_info()
        job1 = self._sample_job("job_1")
        job2 = self._sample_job("job_2")
        t0 = datetime.now(timezone.utc)
        stats = IngestionStats(
            source_name="weworkremotely",
            started_at=t0,
            completed_at=t0 + timedelta(seconds=2),
            duration_ms=2000,
            records_received=2,
            records_accepted=2,
            records_rejected=0,
            status=IngestionRunStatus.SUCCESS,
        )
        result = IngestionResult(
            status=IngestionRunStatus.SUCCESS,
            records=[job1, job2],
            stats=stats,
            errors=[],
            source_info=source,
        )

        assert result.is_success is True
        assert result.is_partial_success is False
        assert result.is_failure is False
        assert result.total_jobs == 2
        assert result.has_errors is False

    def test_partial_success_result(self):
        source = self._sample_source_info()
        job1 = self._sample_job("job_1")
        record_error = IngestionError(
            error_type=IngestionErrorType.INVALID_RECORD_ERROR,
            scope=ErrorScope.RECORD,
            message="Record item 2 missing published date",
            record_id="raw_item_2",
            retryable=False,
        )
        t0 = datetime.now(timezone.utc)
        stats = IngestionStats(
            source_name="weworkremotely",
            started_at=t0,
            completed_at=t0 + timedelta(seconds=3),
            duration_ms=3000,
            records_received=2,
            records_accepted=1,
            records_rejected=1,
            status=IngestionRunStatus.PARTIAL_SUCCESS,
        )
        result = IngestionResult(
            status=IngestionRunStatus.PARTIAL_SUCCESS,
            records=[job1],
            stats=stats,
            errors=[record_error],
            source_info=source,
        )

        assert result.is_success is False
        assert result.is_partial_success is True
        assert result.is_failure is False
        assert result.total_jobs == 1
        assert result.has_errors is True
        assert result.errors[0].scope == ErrorScope.RECORD

    def test_failure_result(self):
        source = SourceInfo(
            source_name="weworkremotely",
            source_type=SourceType.RSS,
            endpoint="https://weworkremotely.com/remote-jobs.rss",
            health_status=SourceHealthStatus.UNREACHABLE,
        )
        run_error = IngestionError(
            error_type=IngestionErrorType.NETWORK_TRANSPORT_ERROR,
            scope=ErrorScope.RUN,
            message="Connection timeout connecting to remote host",
            retryable=True,
        )
        t0 = datetime.now(timezone.utc)
        stats = IngestionStats(
            source_name="weworkremotely",
            started_at=t0,
            completed_at=t0 + timedelta(seconds=5),
            duration_ms=5000,
            records_received=0,
            records_accepted=0,
            records_rejected=0,
            failed_requests=1,
            status=IngestionRunStatus.FAILED,
        )
        result = IngestionResult(
            status=IngestionRunStatus.FAILED,
            records=[],
            stats=stats,
            errors=[run_error],
            source_info=source,
        )

        assert result.is_success is False
        assert result.is_partial_success is False
        assert result.is_failure is True
        assert result.total_jobs == 0
        assert result.has_errors is True
