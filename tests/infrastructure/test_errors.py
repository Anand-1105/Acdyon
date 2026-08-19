"""Unit tests for the transport-to-domain error bridge (infrastructure/errors.py).

Verifies that:
- TransportErrors map to correct IngestionErrorType values.
- ResponseTooLargeError maps to MALFORMED_RESPONSE_ERROR.
- HTTP status errors map correctly based on HttpStatusClass.
- Sensitive fields are never present in error details.
- Internal errors are run-scoped.
"""

import pytest

from src.domain.enums import ErrorScope, IngestionErrorType
from src.infrastructure.errors import (
    error_from_http_status,
    error_from_transport_exception,
    error_internal,
)
from src.infrastructure.http.client import ResponseTooLargeError, TransportError
from src.infrastructure.http.response import FetchResponse, HttpStatusClass, classify_status


class TestErrorFromTransportException:
    def test_timeout_error_mapped_correctly(self):
        import httpx
        cause = httpx.TimeoutException("timed out")
        exc = TransportError("timed out connecting", retryable=True, cause=cause, url="https://x.test/feed")
        err = error_from_transport_exception(exc, source_name="testsrc", attempt_number=1)

        assert err.error_type == IngestionErrorType.TIMEOUT_ERROR
        assert err.scope == ErrorScope.REQUEST
        assert err.retryable is True
        assert "testsrc" in err.details.get("source_name", "")
        assert err.details["attempt_number"] == 1

    def test_connect_error_mapped_to_network_transport_error(self):
        import httpx
        cause = httpx.ConnectError("refused")
        exc = TransportError("connection refused", retryable=True, cause=cause, url="https://x.test/feed")
        err = error_from_transport_exception(exc, source_name="testsrc", attempt_number=2)

        assert err.error_type == IngestionErrorType.NETWORK_TRANSPORT_ERROR
        assert err.retryable is True

    def test_non_retryable_transport_error(self):
        exc = TransportError("bad redirect", retryable=False, url="https://x.test/feed")
        err = error_from_transport_exception(exc, source_name="testsrc", attempt_number=1)
        assert err.error_type == IngestionErrorType.NETWORK_TRANSPORT_ERROR
        assert err.retryable is False

    def test_response_too_large_mapped_to_malformed(self):
        exc = ResponseTooLargeError(url="https://x.test", limit_bytes=100, actual_bytes=9999)
        err = error_from_transport_exception(exc, source_name="testsrc", attempt_number=1)

        assert err.error_type == IngestionErrorType.MALFORMED_RESPONSE_ERROR
        assert err.retryable is False
        assert err.details["limit_bytes"] == 100
        assert err.details["actual_bytes"] == 9999

    def test_no_credentials_in_details(self):
        exc = TransportError("fail", retryable=False, url="https://x.test/feed?token=secret")
        err = error_from_transport_exception(exc, source_name="testsrc", attempt_number=1)
        # URL passed to TransportError is already stripped by client.py — verify no raw URL leaks
        details_str = str(err.details)
        assert "secret" not in details_str


class TestErrorFromHttpStatus:
    def _make_response(self, status_code: int, retry_after: float = None) -> FetchResponse:
        return FetchResponse(
            status_code=status_code,
            status_class=classify_status(status_code),
            body=b"",
            retry_after_seconds=retry_after,
            attempt_number=1,
            elapsed_ms=100.0,
            url="https://x.test/feed",
        )

    def test_429_is_rate_limit_error_and_retryable(self):
        err = error_from_http_status(self._make_response(429, retry_after=30.0), "testsrc")
        assert err.error_type == IngestionErrorType.RATE_LIMIT_ERROR
        assert err.retryable is True
        assert err.details["retry_after_seconds"] == 30.0

    def test_503_is_source_server_error_and_retryable(self):
        err = error_from_http_status(self._make_response(503), "testsrc")
        assert err.error_type == IngestionErrorType.SOURCE_SERVER_ERROR
        assert err.retryable is True

    def test_500_is_source_server_error_not_retryable(self):
        err = error_from_http_status(self._make_response(500), "testsrc")
        assert err.error_type == IngestionErrorType.SOURCE_SERVER_ERROR
        assert err.retryable is False

    def test_404_is_network_transport_error_not_retryable(self):
        err = error_from_http_status(self._make_response(404), "testsrc")
        assert err.error_type == IngestionErrorType.NETWORK_TRANSPORT_ERROR
        assert err.retryable is False

    def test_scope_is_request(self):
        err = error_from_http_status(self._make_response(502), "testsrc")
        assert err.scope == ErrorScope.REQUEST

    def test_source_name_in_message(self):
        err = error_from_http_status(self._make_response(429), "weworkremotely")
        assert "weworkremotely" in err.message


class TestErrorInternal:
    def test_internal_error_is_run_scoped(self):
        err = error_internal("Something went wrong", source_name="testsrc")
        assert err.error_type == IngestionErrorType.INTERNAL_ERROR
        assert err.scope == ErrorScope.RUN
        assert err.retryable is False

    def test_details_merged(self):
        err = error_internal("oops", source_name="s", details={"extra": "info"})
        assert err.details["extra"] == "info"
        assert err.details["source_name"] == "s"
