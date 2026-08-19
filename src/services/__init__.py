"""Services Package for Acdyon Ingestion Subsystem.

Exports the central IngestionService and the SourceAdapterRegistry.
"""

from src.services.orchestrator import IngestionService
from src.services.registry import SourceAdapterRegistry

__all__ = ["IngestionService", "SourceAdapterRegistry"]
