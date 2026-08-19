"""Storage Package for Acdyon Job Ingestion Subsystem.

Exports abstract persistence contracts, in-memory and PostgreSQL implementations,
configuration models, and mapping utilities.
"""

from src.storage.base import (
    BaseIngestionRunRepository,
    BaseJobRepository,
    BaseSnapshotRepository,
    BaseSourceHealthRepository,
    IngestionSnapshotRecord,
    RepositoryWriteResult,
    SourceHealthRecord,
)
from src.storage.config import StorageConfig
from src.storage.errors import error_from_storage_exception
from src.storage.mapping import (
    ingestion_run_to_row,
    job_to_row,
    row_to_job,
    row_to_snapshot,
    row_to_source_health,
    snapshot_to_row,
    source_health_to_row,
)
from src.storage.memory import (
    InMemoryIngestionRunRepository,
    InMemoryJobRepository,
    InMemorySnapshotRepository,
    InMemorySourceHealthRepository,
    InMemoryStorage,
)
from src.storage.postgres import (
    PostgresIngestionRunRepository,
    PostgresJobRepository,
    PostgresSnapshotRepository,
    PostgresSourceHealthRepository,
    PostgresStorage,
)

__all__ = [
    "BaseJobRepository",
    "BaseIngestionRunRepository",
    "BaseSourceHealthRepository",
    "BaseSnapshotRepository",
    "RepositoryWriteResult",
    "SourceHealthRecord",
    "IngestionSnapshotRecord",
    "StorageConfig",
    "error_from_storage_exception",
    "job_to_row",
    "row_to_job",
    "ingestion_run_to_row",
    "source_health_to_row",
    "row_to_source_health",
    "snapshot_to_row",
    "row_to_snapshot",
    "InMemoryJobRepository",
    "InMemoryIngestionRunRepository",
    "InMemorySourceHealthRepository",
    "InMemorySnapshotRepository",
    "InMemoryStorage",
    "PostgresJobRepository",
    "PostgresIngestionRunRepository",
    "PostgresSourceHealthRepository",
    "PostgresSnapshotRepository",
    "PostgresStorage",
]
