"""Ingestion Endpoint Router.

Exposes POST /api/v1/ingest to trigger job ingestion runs via IngestionService.
"""

from __future__ import annotations

import logging
from typing import List

from fastapi import APIRouter, Depends, HTTPException, status

from src.api.deps import get_ingestion_service
from src.api.models import (
    IngestRequestModel,
    IngestResponseModel,
    IngestionErrorResponseModel,
    IngestionStatsResponseModel,
    JobResponseModel,
    SalaryResponseModel,
    SourceInfoResponseModel,
)
from src.domain.enums import IngestionErrorType, IngestionRunStatus
from src.domain.ingestion import IngestionRequest, IngestionResult
from src.services.orchestrator import IngestionService

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1", tags=["Ingestion"])


def _map_job_response(job) -> JobResponseModel:
    salary_model = None
    if job.salary:
        salary_model = SalaryResponseModel(
            currency=job.salary.currency,
            min_amount=str(job.salary.min_amount) if job.salary.min_amount is not None else None,
            max_amount=str(job.salary.max_amount) if job.salary.max_amount is not None else None,
            interval=job.salary.interval,
            raw_text=job.salary.raw_text,
        )
    return JobResponseModel(
        canonical_id=job.canonical_id,
        source_name=job.source_name,
        source_id=job.source_id,
        source_url=job.source_url,
        title=job.title,
        company=job.company,
        location=job.location,
        description=job.description,
        employment_type=job.employment_type,
        salary=salary_model,
        requirements=list(job.requirements),
        published_at=job.published_at,
        ingested_at=job.ingested_at,
        status=job.status,
        metadata=job.metadata,
    )


@router.post("/ingest", response_model=IngestResponseModel)
async def trigger_ingestion(
    payload: IngestRequestModel,
    service: IngestionService = Depends(get_ingestion_service),
) -> IngestResponseModel:
    """Trigger a job ingestion run for the specified source and category."""
    domain_req = IngestionRequest(
        source_name=payload.source_name,
        category=payload.category,
        limit=payload.limit,
    )

    result: IngestionResult = await service.ingest(domain_req)

    # Map failures to explicit HTTP error statuses
    if result.is_failure:
        first_err = result.errors[0] if result.errors else None
        err_type = first_err.error_type if first_err else IngestionErrorType.INTERNAL_ERROR
        msg = first_err.message if first_err else "Ingestion run failed"

        if err_type == IngestionErrorType.VALIDATION_ERROR:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=msg)
        if err_type in (
            IngestionErrorType.TIMEOUT_ERROR,
            IngestionErrorType.RATE_LIMIT_ERROR,
            IngestionErrorType.NETWORK_TRANSPORT_ERROR,
            IngestionErrorType.SOURCE_SERVER_ERROR,
            IngestionErrorType.MALFORMED_RESPONSE_ERROR,
        ):
            raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail=msg)
        if err_type == IngestionErrorType.PERSISTENCE_ERROR:
            raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=msg)
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=msg)

    # Success or Partial Success -> 200 OK
    job_models = [_map_job_response(j) for j in result.records]
    stats_model = IngestionStatsResponseModel(
        source_name=result.stats.source_name,
        started_at=result.stats.started_at,
        completed_at=result.stats.completed_at,
        duration_ms=result.stats.duration_ms,
        records_received=result.stats.records_received,
        records_accepted=result.stats.records_accepted,
        records_rejected=result.stats.records_rejected,
        duplicates_detected=result.stats.duplicates_detected,
        retries=result.stats.retries,
        failed_requests=result.stats.failed_requests,
        status=result.stats.status,
    )
    error_models = [
        IngestionErrorResponseModel(
            error_type=e.error_type.value,
            scope=e.scope.value,
            message=e.message,
            retryable=e.retryable,
            record_id=e.record_id,
            timestamp=e.timestamp,
            details=e.details,
        )
        for e in result.errors
    ]
    source_info_model = SourceInfoResponseModel(
        source_name=result.source_info.source_name,
        source_type=result.source_info.source_type,
        endpoint=result.source_info.endpoint,
        retrieval_timestamp=result.source_info.retrieval_timestamp,
        attribution=result.source_info.attribution,
        health_status=result.source_info.health_status,
        metadata=result.source_info.metadata,
    )

    return IngestResponseModel(
        status=result.status,
        records_count=len(job_models),
        records=job_models,
        stats=stats_model,
        errors=error_models,
        source_info=source_info_model,
    )
