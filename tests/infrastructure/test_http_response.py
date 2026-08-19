"""Unit tests for HTTP response container and status classification.

All tests are synchronous — no network calls, no mocking required.
"""

import pytest

from src.infrastructure.http.response import (
    FetchResponse,
    HttpStatusClass,
    classify_status,
    is_retryable_status,
)


class TestClassifyStatus:
    """Pure function: integer status → HttpStatusClass."""

    @pytest.mark.parametrize("code", [200, 201, 204, 206])
    def test_2xx_is_success(self, code: int):
        assert classify_status(code) == HttpStatusClass.SUCCESS

    @pytest.mark.parametrize("code", [301, 302, 307, 308])
    def test_3xx_is_redirect(self, code: int):
        assert classify_status(code) == HttpStatusClass.REDIRECT

    def test_429_is_rate_limited(self):
        assert classify_status(429) == HttpStatusClass.RATE_LIMITED

    @pytest.mark.parametrize("code", [502, 503, 504])
    def test_transient_server_codes(self, code: int):
        assert classify_status(code) == HttpStatusClass.TRANSIENT_SERVER_ERROR

    @pytest.mark.parametrize("code", [400, 401, 403, 404, 422])
    def test_other_4xx_is_client_error(self, code: int):
        assert classify_status(code) == HttpStatusClass.CLIENT_ERROR

    @pytest.mark.parametrize("code", [500, 501])
    def test_non_transient_5xx_is_server_error(self, code: int):
        assert classify_status(code) == HttpStatusClass.SERVER_ERROR

    def test_unusual_code_is_unexpected(self):
        assert classify_status(999) == HttpStatusClass.UNEXPECTED
        assert classify_status(100) == HttpStatusClass.UNEXPECTED

    def test_is_retryable_status_429(self):
        assert is_retryable_status(429) is True

    @pytest.mark.parametrize("code", [502, 503, 504])
    def test_is_retryable_transient(self, code: int):
        assert is_retryable_status(code) is True

    @pytest.mark.parametrize("code", [200, 400, 401, 403, 404, 500])
    def test_non_retryable_statuses(self, code: int):
        assert is_retryable_status(code) is False


class TestFetchResponse:
    """FetchResponse dataclass properties and text decoding."""

    def _make_response(self, status: int, body: bytes = b"hello") -> FetchResponse:
        return FetchResponse(
            status_code=status,
            status_class=classify_status(status),
            body=body,
            headers={"content-type": "application/rss+xml"},
            elapsed_ms=42.5,
            url="https://example.com/feed",
            attempt_number=1,
        )

    def test_is_success_true_for_200(self):
        r = self._make_response(200)
        assert r.is_success is True

    def test_is_success_false_for_404(self):
        r = self._make_response(404)
        assert r.is_success is False

    def test_text_decodes_utf8(self):
        r = self._make_response(200, body="héllo".encode("utf-8"))
        assert r.text == "héllo"

    def test_text_replaces_bad_bytes(self):
        r = self._make_response(200, body=b"\xff\xfe bad")
        # Should not raise; bad bytes are replaced
        assert isinstance(r.text, str)

    def test_content_length_property(self):
        r = self._make_response(200, body=b"abcde")
        assert r.content_length == 5

    def test_retry_after_stored(self):
        r = FetchResponse(
            status_code=429,
            status_class=HttpStatusClass.RATE_LIMITED,
            body=b"",
            retry_after_seconds=30.0,
        )
        assert r.retry_after_seconds == 30.0
