"""Jobs Endpoint Router.

Exposes GET /api/v1/jobs for listing canonical jobs and GET /api/v1/jobs/{canonical_id}.
"""

from __future__ import annotations

from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query, status

from src.api.deps import get_job_repository
from src.api.models import JobResponseModel, SalaryResponseModel
from src.storage.base import BaseJobRepository

router = APIRouter(prefix="/api/v1/jobs", tags=["Jobs"])


def _map_job(job) -> JobResponseModel:
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


@router.get("", response_model=List[JobResponseModel])
async def list_jobs(
    source_name: Optional[str] = Query(None, max_length=64),
    limit: int = Query(50, ge=1, le=1000),
    offset: int = Query(0, ge=0),
    repo: BaseJobRepository = Depends(get_job_repository),
) -> List[JobResponseModel]:
    """Retrieve persisted canonical job records with optional source filtering and pagination."""
    jobs = await repo.list_jobs(source_name=source_name, limit=limit, offset=offset)
    return [_map_job(j) for j in jobs]


@router.get("/count", response_model=dict)
async def count_jobs(
    source_name: Optional[str] = Query(None, max_length=64),
    repo: BaseJobRepository = Depends(get_job_repository),
) -> dict:
    """Retrieve total count of persisted job records."""
    total = await repo.count_jobs(source_name=source_name)
    return {"total": total}


@router.get("/{canonical_id}", response_model=JobResponseModel)
async def get_job(
    canonical_id: str,
    repo: BaseJobRepository = Depends(get_job_repository),
) -> JobResponseModel:
    """Retrieve a single canonical job record by its canonical_id."""
    job = await repo.get_job_by_canonical_id(canonical_id)
    if job is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Job record with canonical_id '{canonical_id}' not found.",
        )
    return _map_job(job)
