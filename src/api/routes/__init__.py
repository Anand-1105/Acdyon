"""API Routes Package.

Exposes router modules for health, ingestion, jobs, runs, and source health.
"""

from src.api.routes.health import router as health_router
from src.api.routes.ingest import router as ingest_router
from src.api.routes.jobs import router as jobs_router
from src.api.routes.logs import router as logs_router
from src.api.routes.runs import router as runs_router
from src.api.routes.source_health import router as source_health_router

__all__ = [
    "health_router",
    "ingest_router",
    "jobs_router",
    "logs_router",
    "runs_router",
    "source_health_router",
]
