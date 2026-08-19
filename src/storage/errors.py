"""Database Error Translation Bridge.

Converts storage layer and Supabase/PostgreSQL exceptions into structured
canonical IngestionError objects without leaking connection strings or credentials.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any, Optional

from src.domain.enums import ErrorScope, IngestionErrorType
from src.domain.errors import IngestionError

logger = logging.getLogger(__name__)


def error_from_storage_exception(
    exc: Exception,
    operation: str = "storage_operation",
    details: Optional[dict[str, Any]] = None,
) -> IngestionError:
    """Translate an arbitrary database or storage exception into a canonical IngestionError."""
    err_msg = str(exc)
    logger.error("Persistence error during %s: %s", operation, err_msg)

    err_details = dict(details or {})
    err_details["operation"] = operation
    err_details["exception_class"] = exc.__class__.__name__

    return IngestionError(
        error_type=IngestionErrorType.PERSISTENCE_ERROR,
        scope=ErrorScope.RUN,
        message=f"Database persistence failure during {operation}: {err_msg}"[:1024],
        retryable=False,
        details=err_details,
        timestamp=datetime.now(timezone.utc),
    )
