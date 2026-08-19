"""FastAPI Application Factory and Configuration.

Constructs the FastAPI application instance, configures CORS middleware,
registers exception handlers, and mounts API routers.
"""

from __future__ import annotations

import os
from typing import List

from pathlib import Path

from fastapi import FastAPI, Request, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles

from src.api.routes import (
    health_router,
    ingest_router,
    jobs_router,
    runs_router,
    source_health_router,
)


def _get_cors_origins() -> List[str]:
    """Retrieve allowed CORS origins from environment, defaulting to safe local origins."""
    env_origins = os.getenv("CORS_ORIGINS", "")
    if env_origins.strip():
        return [o.strip() for o in env_origins.split(",") if o.strip()]
    return ["http://localhost:3000", "http://localhost:5173", "http://127.0.0.1:3000", "http://127.0.0.1:5173"]


def create_app() -> FastAPI:
    """Construct and configure the FastAPI application instance."""
    app = FastAPI(
        title="Acdyon Job Ingestion Subsystem API",
        description="Canonical REST API for job feed ingestion, source health, and canonical job retrieval.",
        version="1.0.0",
        docs_url="/docs",
        redoc_url="/redoc",
    )

    # CORS Middleware Configuration
    origins = _get_cors_origins()
    app.add_middleware(
        CORSMiddleware,
        allow_origins=origins,
        allow_credentials=True,
        allow_methods=["GET", "POST", "OPTIONS"],
        allow_headers=["*"],
    )

    # Global Safe Exception Handler
    @app.exception_handler(Exception)
    async def global_exception_handler(request: Request, exc: Exception) -> JSONResponse:
        return JSONResponse(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            content={
                "error": "Internal Error",
                "message": "An unexpected error occurred during request processing.",
            },
        )

    # Register API Routers
    app.include_router(health_router)
    app.include_router(ingest_router)
    app.include_router(jobs_router)
    app.include_router(runs_router)
    app.include_router(source_health_router)

    # Mount static dashboard UI files
    static_dir = Path(__file__).parent.parent / "static"
    if static_dir.exists():
        app.mount("/", StaticFiles(directory=str(static_dir), html=True), name="static")

    return app


app = create_app()
