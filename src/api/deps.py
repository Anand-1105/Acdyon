"""FastAPI Dependency Injection Providers.

Provides overridable dependency factories for repositories, services, and configuration,
allowing test suites to replace production databases with in-memory test doubles.
"""

from __future__ import annotations

import logging
from typing import Generator

from fastapi import Depends

from src.services.orchestrator import IngestionService
from src.services.registry import SourceAdapterRegistry
from src.storage.base import (
    BaseIngestionRunRepository,
    BaseJobRepository,
    BaseSnapshotRepository,
    BaseSourceHealthRepository,
)
from src.storage.config import StorageConfig
from src.storage.memory import InMemoryStorage
from src.storage.postgres import PostgresStorage

logger = logging.getLogger(__name__)

# Global singleton storage instance for production runtime
_global_storage: InMemoryStorage | PostgresStorage | None = None
_global_registry: SourceAdapterRegistry | None = None


def get_storage_config() -> StorageConfig:
    """Retrieve StorageConfig from environment."""
    return StorageConfig.from_env()


def get_source_adapter_registry() -> SourceAdapterRegistry:
    """Dependency provider for shared application-lifetime SourceAdapterRegistry."""
    global _global_registry
    if _global_registry is None:
        _global_registry = SourceAdapterRegistry.default_registry()
    return _global_registry


def get_storage(
    config: StorageConfig = Depends(get_storage_config),
) -> InMemoryStorage | PostgresStorage:
    """Dependency provider for storage subsystem."""
    global _global_storage

    if _global_storage is not None:
        return _global_storage

    if config.is_configured:
        try:
            from supabase import create_client

            client = create_client(config.supabase_url, config.supabase_key)
            _global_storage = PostgresStorage(client)
            logger.info("Initialized PostgresStorage backend")
            return _global_storage
        except Exception as exc:
            logger.warning("Failed to initialize Supabase client: %s. Falling back to InMemoryStorage.", exc)

    _global_storage = InMemoryStorage()
    logger.info("Initialized InMemoryStorage backend")
    return _global_storage


def get_job_repository(
    storage: InMemoryStorage | PostgresStorage = Depends(get_storage),
) -> BaseJobRepository:
    """Dependency provider for BaseJobRepository."""
    return storage.jobs


def get_run_repository(
    storage: InMemoryStorage | PostgresStorage = Depends(get_storage),
) -> BaseIngestionRunRepository:
    """Dependency provider for BaseIngestionRunRepository."""
    return storage.runs


def get_health_repository(
    storage: InMemoryStorage | PostgresStorage = Depends(get_storage),
) -> BaseSourceHealthRepository:
    """Dependency provider for BaseSourceHealthRepository."""
    return storage.health


def get_snapshot_repository(
    storage: InMemoryStorage | PostgresStorage = Depends(get_storage),
) -> BaseSnapshotRepository:
    """Dependency provider for BaseSnapshotRepository."""
    return storage.snapshots


def get_ingestion_service(
    job_repo: BaseJobRepository = Depends(get_job_repository),
    run_repo: BaseIngestionRunRepository = Depends(get_run_repository),
    health_repo: BaseSourceHealthRepository = Depends(get_health_repository),
    snapshot_repo: BaseSnapshotRepository = Depends(get_snapshot_repository),
    registry: SourceAdapterRegistry = Depends(get_source_adapter_registry),
) -> IngestionService:
    """Dependency provider for IngestionService orchestrator."""
    return IngestionService(
        job_repo=job_repo,
        run_repo=run_repo,
        health_repo=health_repo,
        snapshot_repo=snapshot_repo,
        registry=registry,
    )
