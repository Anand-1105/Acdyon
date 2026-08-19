"""Source Health Endpoint Router.

Exposes GET /api/v1/health/{source_name} for inspecting provider operational health.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, status

from src.api.deps import get_health_repository
from src.api.models import SourceHealthResponseModel
from src.storage.base import BaseSourceHealthRepository

router = APIRouter(prefix="/api/v1/health", tags=["Operational"])


@router.get("/{source_name}", response_model=SourceHealthResponseModel)
async def get_source_health(
    source_name: str,
    repo: BaseSourceHealthRepository = Depends(get_health_repository),
) -> SourceHealthResponseModel:
    """Retrieve operational health telemetry for a specific ingestion source."""
    health = await repo.get_health(source_name.strip().lower())
    if health is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"No health telemetry found for source '{source_name}'.",
        )

    return SourceHealthResponseModel(
        source_name=health.source_name,
        health_status=health.health_status,
        endpoint=health.endpoint,
        last_success_at=health.last_success_at,
        last_failure_at=health.last_failure_at,
        consecutive_failures=health.consecutive_failures,
        last_error_details=health.last_error_details,
        updated_at=health.updated_at,
    )
