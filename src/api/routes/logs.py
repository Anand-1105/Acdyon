"""Operational Ingestion Logs Endpoint Router.

Exposes GET /api/v1/logs for historical ingestion run inspection.
"""

from __future__ import annotations

from typing import List, Optional

from fastapi import APIRouter, Depends, Query

from src.api.deps import get_run_repository
from src.api.models import IngestionRunResponseModel
from src.storage.base import BaseIngestionRunRepository

router = APIRouter(prefix="/api/v1/logs", tags=["Logs"])


@router.get("", response_model=List[IngestionRunResponseModel])
async def list_logs(
    source_name: Optional[str] = Query(None, description="Filter by source name"),
    limit: int = Query(50, ge=1, le=100, description="Max historical log records to return"),
    offset: int = Query(0, ge=0, description="Pagination offset"),
    repo: BaseIngestionRunRepository = Depends(get_run_repository),
) -> List[IngestionRunResponseModel]:
    """Retrieve chronologically ordered historical ingestion run logs (newest first)."""
    runs = await repo.list_runs(
        source_name=source_name.strip().lower() if source_name else None,
        limit=limit,
        offset=offset,
    )
    return [IngestionRunResponseModel(**r) for r in runs]
