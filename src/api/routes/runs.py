"""Ingestion Runs Telemetry Endpoint Router.

Exposes GET /api/v1/runs/{run_id} and GET /api/v1/runs/latest/{source_name}.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, status

from src.api.deps import get_run_repository
from src.api.models import IngestionRunResponseModel
from src.storage.base import BaseIngestionRunRepository

router = APIRouter(prefix="/api/v1/runs", tags=["Telemetry"])


@router.get("/{run_id}", response_model=IngestionRunResponseModel)
async def get_run(
    run_id: str,
    repo: BaseIngestionRunRepository = Depends(get_run_repository),
) -> IngestionRunResponseModel:
    """Retrieve operational telemetry and metrics for a specific ingestion run."""
    run_data = await repo.get_ingestion_run(run_id)
    if run_data is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Ingestion run '{run_id}' not found.",
        )
    return IngestionRunResponseModel(**run_data)


@router.get("/latest/{source_name}", response_model=IngestionRunResponseModel)
async def get_latest_run(
    source_name: str,
    repo: BaseIngestionRunRepository = Depends(get_run_repository),
) -> IngestionRunResponseModel:
    """Retrieve operational telemetry for the most recent run of a source."""
    run_data = await repo.get_latest_run(source_name.strip().lower())
    if run_data is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"No run history found for source '{source_name}'.",
        )
    return IngestionRunResponseModel(**run_data)
