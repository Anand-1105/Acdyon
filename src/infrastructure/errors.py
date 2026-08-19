"""Transport-to-domain error bridge.

This module provides factory functions that translate transport-level failures
(TransportError, ResponseTooLargeError, HTTP status codes) into canonical
IngestionError domain objects.

Why a separate module?
- The transport layer (http/client.py) must not import domain models.
- The domain layer must not import infrastructure.
- This bridge lives in infrastructure and depends on both — cleanly.

Rule: No credential, header value, or raw response body is allowed into
the IngestionError details. Use only safe diagnostic metadata.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Optional

from src.domain.enums import ErrorScope, IngestionErrorType
from src.domain.errors import IngestionError
from src.infrastructure.http.client import ResponseTooLargeError, TransportError, _safe_url_for_logging
from src.infrastructure.http.response import FetchResponse, HttpStatusClass


def error_from_transport_exception(
    exc: TransportError,
    source_name: str,
    attempt_number: int,
) -> IngestionError:
    """Convert a TransportError into a canonical IngestionError.

    Maps retryable transport exceptions to TIMEOUT_ERROR or NETWORK_TRANSPORT_ERROR.
    Maps ResponseTooLargeError to MALFORMED_RESPONSE_ERROR.

    Args:
        exc:            The TransportError raised by AsyncHttpTransport.
        source_name:    The source label for diagnostic context.
        attempt_number: Which attempt produced this error.

    Returns:
        A sanitized, classified IngestionError ready for telemetry.
    """
    if isinstance(exc, ResponseTooLargeError):
        error_type = IngestionErrorType.MALFORMED_RESPONSE_ERROR
    elif exc.retryable:
        # Distinguish timeout vs other network failures via the exception cause type.
        from httpx import TimeoutException  # local import keeps infra isolated
        error_type = (
            IngestionErrorType.TIMEOUT_ERROR
            if isinstance(exc.cause, TimeoutException)
            else IngestionErrorType.NETWORK_TRANSPORT_ERROR
        )
    else:
        error_type = IngestionErrorType.NETWORK_TRANSPORT_ERROR

    details: dict = {
        "source_name": source_name,
        "attempt_number": attempt_number,
        "url": _safe_url_for_logging(exc.url),   # strip query params; never include raw URL
        "error_class": type(exc).__name__,
    }
    if isinstance(exc, ResponseTooLargeError):
        details["limit_bytes"] = exc.limit_bytes
        details["actual_bytes"] = exc.actual_bytes

    return IngestionError(
        error_type=error_type,
        scope=ErrorScope.REQUEST,
        message=str(exc)[:1024],
        details=details,
        retryable=exc.retryable,
        timestamp=datetime.now(timezone.utc),
    )


def error_from_http_status(
    response: FetchResponse,
    source_name: str,
) -> IngestionError:
    """Translate a non-success FetchResponse into a canonical IngestionError.

    Args:
        response:    The FetchResponse with a non-2xx status.
        source_name: The source label for diagnostic context.

    Returns:
        A sanitized, classified IngestionError.
    """
    status_to_type: dict[HttpStatusClass, tuple[IngestionErrorType, bool]] = {
        HttpStatusClass.RATE_LIMITED:           (IngestionErrorType.RATE_LIMIT_ERROR,         True),
        HttpStatusClass.TRANSIENT_SERVER_ERROR:  (IngestionErrorType.SOURCE_SERVER_ERROR,       True),
        HttpStatusClass.SERVER_ERROR:            (IngestionErrorType.SOURCE_SERVER_ERROR,       False),
        HttpStatusClass.CLIENT_ERROR:            (IngestionErrorType.NETWORK_TRANSPORT_ERROR,   False),
        HttpStatusClass.REDIRECT:                (IngestionErrorType.NETWORK_TRANSPORT_ERROR,   False),
        HttpStatusClass.UNEXPECTED:              (IngestionErrorType.INTERNAL_ERROR,            False),
    }

    error_type, retryable = status_to_type.get(
        response.status_class,
        (IngestionErrorType.INTERNAL_ERROR, False),
    )

    details: dict = {
        "source_name": source_name,
        "status_code": response.status_code,
        "status_class": response.status_class.value,
        "elapsed_ms": round(response.elapsed_ms, 1),
        "attempt_number": response.attempt_number,
        "url": response.url,
    }
    if response.retry_after_seconds is not None:
        details["retry_after_seconds"] = response.retry_after_seconds

    message = (
        f"HTTP {response.status_code} received from source '{source_name}' "
        f"(attempt {response.attempt_number}, "
        f"elapsed {response.elapsed_ms:.0f}ms)"
    )

    return IngestionError(
        error_type=error_type,
        scope=ErrorScope.REQUEST,
        message=message[:1024],
        details=details,
        retryable=retryable,
        timestamp=datetime.now(timezone.utc),
    )


def error_internal(
    message: str,
    source_name: str,
    details: Optional[dict] = None,
) -> IngestionError:
    """Create an INTERNAL_ERROR IngestionError for unexpected failures.

    Args:
        message:     Human-readable description (max 1024 chars).
        source_name: The source label for diagnostic context.
        details:     Optional additional safe diagnostic data.

    Returns:
        A non-retryable, run-scoped IngestionError.
    """
    return IngestionError(
        error_type=IngestionErrorType.INTERNAL_ERROR,
        scope=ErrorScope.RUN,
        message=message[:1024],
        details={"source_name": source_name, **(details or {})},
        retryable=False,
        timestamp=datetime.now(timezone.utc),
    )
