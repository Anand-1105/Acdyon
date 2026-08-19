"""Application Liveness Health Endpoint.

Provides a fast, zero-dependency process liveness check for load balancers and orchestrators.
"""

from __future__ import annotations

from fastapi import APIRouter

router = APIRouter()


@router.get("/health", tags=["Operational"])
async def liveness_check() -> dict[str, str]:
    """Process liveness endpoint. Returns status: ok without calling external networks."""
    return {"status": "ok"}
