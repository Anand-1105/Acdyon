"""API Package for Acdyon Ingestion Subsystem.

Exports the FastAPI application instance.
"""

from src.api.app import app, create_app

__all__ = ["app", "create_app"]
