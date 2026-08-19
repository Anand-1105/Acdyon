"""Source adapter base contract.

Defines the minimal abstract interface that all source adapters must implement.
The ingestion orchestrator depends only on this interface, not on any
specific adapter implementation.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import List

from src.domain.errors import IngestionError
from src.domain.ingestion import IngestionRequest
from src.domain.job import JobRecord
from src.domain.source import SourceInfo


@dataclass
class ParsedBatch:
    """The output contract of every source adapter's fetch_and_parse call.

    Attributes:
        records:         Canonical job records that passed validation.
        errors:          Structured errors encountered during fetch or parsing.
        raw_count:       Total items seen in the source payload (before filtering).
        source_info:     Metadata describing the source used for this fetch.
    """

    records: List[JobRecord] = field(default_factory=list)
    errors: List[IngestionError] = field(default_factory=list)
    raw_count: int = 0
    source_info: SourceInfo = field(default=None)  # type: ignore[assignment]


class BaseSourceAdapter(ABC):
    """Abstract base for all external job-feed source adapters.

    Implementors must:
    - Return a ParsedBatch from fetch_and_parse().
    - Use the shared HTTP transport, retry policy, and rate limiter.
    - Never write to persistence.
    - Never import FastAPI or database drivers.
    """

    @abstractmethod
    async def fetch_and_parse(self, request: IngestionRequest) -> ParsedBatch:
        """Fetch the source feed and return a normalized batch of canonical records.

        Args:
            request: The canonical ingestion parameters. Source-specific options
                     may be embedded in request.metadata.

        Returns:
            ParsedBatch containing records, errors, raw count, and source info.
        """
        ...

    @property
    @abstractmethod
    def source_name(self) -> str:
        """Stable identifier for this source, e.g. 'weworkremotely'."""
        ...
