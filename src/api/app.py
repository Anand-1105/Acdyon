"""FastAPI Application Factory and Configuration.

Constructs the FastAPI application instance, configures CORS middleware,
registers exception handlers, and mounts API routers and static files.
"""

from __future__ import annotations

import os
from typing import List
from pathlib import Path

from fastapi import FastAPI, Request, Response, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from starlette.exceptions import HTTPException as StarletteHTTPException

from src.api.routes import (
    health_router,
    ingest_router,
    jobs_router,
    logs_router,
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

    static_dir = Path(__file__).parent.parent / "static"

    # CORS Middleware Configuration
    origins = _get_cors_origins()
    app.add_middleware(
        CORSMiddleware,
        allow_origins=origins,
        allow_credentials=True,
        allow_methods=["GET", "POST", "OPTIONS"],
        allow_headers=["*"],
    )

    # Custom HTTP Exception Handler (404, 400, etc.)
    @app.exception_handler(StarletteHTTPException)
    async def http_exception_handler(request: Request, exc: StarletteHTTPException) -> Response:
        # For API requests, always return JSON errors
        if request.url.path.startswith("/api/"):
            detail_msg = exc.detail if exc.detail != "Not Found" else f"API endpoint '{request.url.path}' not found."
            return JSONResponse(
                status_code=exc.status_code,
                content={"detail": detail_msg},
            )

        # For browser/website requests returning 404, render the branded 404.html page
        if exc.status_code == status.HTTP_404_NOT_FOUND:
            not_found_file = static_dir / "404.html"
            if not_found_file.exists():
                return HTMLResponse(
                    content=not_found_file.read_text(encoding="utf-8"),
                    status_code=status.HTTP_404_NOT_FOUND,
                )
            return HTMLResponse(
                content="<h1>404 Not Found</h1><p>The requested page could not be found.</p>",
                status_code=status.HTTP_404_NOT_FOUND,
            )

        return HTMLResponse(
            content=f"<h1>{exc.status_code} Error</h1><p>{exc.detail}</p>",
            status_code=exc.status_code,
        )

    # Global Safe Exception Handler for unhandled server errors (500)
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
    app.include_router(logs_router)
    app.include_router(runs_router)
    app.include_router(source_health_router)

    # Explicit Dashboard Route
    @app.get("/", response_class=HTMLResponse, include_in_schema=False)
    async def serve_dashboard() -> HTMLResponse:
        index_file = static_dir / "index.html"
        if index_file.exists():
            return HTMLResponse(content=index_file.read_text(encoding="utf-8"))
        return HTMLResponse("<h1>Acdyon Dashboard</h1>")

    # Mount static assets (CSS, JS, config.js, 404.html) without html=True fallback
    if static_dir.exists():
        app.mount("/", StaticFiles(directory=str(static_dir), html=False), name="static")

    return app


app = create_app()
