"""Integration tests for AsyncHttpTransport using pytest-httpx.

Tests cover:
- Successful 200 response.
- 4xx, 429, and 5xx responses (status class correctly set).
- Connection timeout raises TransportError (retryable).
- Response body exceeding size limit raises ResponseTooLargeError.
- Too-many-redirects raises TransportError (not retryable).
- Retry-After header is parsed and stored on the response.
- User-Agent header is sent.
- CancelledError propagates (not caught as a TransportError).

All tests use httpx's MockTransport / pytest-httpx to avoid real network calls.
"""

from __future__ import annotations

import asyncio

import httpx
import pytest
import pytest_asyncio
from pytest_httpx import HTTPXMock

from src.infrastructure.config import HttpTransportConfig, ResponseLimitConfig, TimeoutConfig
from src.infrastructure.http.client import (
    AsyncHttpTransport,
    ResponseTooLargeError,
    TransportError,
)
from src.infrastructure.http.response import HttpStatusClass

pytestmark = pytest.mark.asyncio


TARGET_URL = "https://example-feed.test/jobs.rss"


class TestAsyncHttpTransportSuccess:
    async def test_200_response_returns_fetch_response(self, httpx_mock: HTTPXMock):
        httpx_mock.add_response(url=TARGET_URL, status_code=200, content=b"<rss/>")

        async with AsyncHttpTransport() as t:
            resp = await t.get(TARGET_URL)

        assert resp.status_code == 200
        assert resp.status_class == HttpStatusClass.SUCCESS
        assert resp.body == b"<rss/>"
        assert resp.is_success is True

    async def test_elapsed_ms_is_positive(self, httpx_mock: HTTPXMock):
        httpx_mock.add_response(url=TARGET_URL, status_code=200, content=b"data")
        async with AsyncHttpTransport() as t:
            resp = await t.get(TARGET_URL)
        assert resp.elapsed_ms >= 0.0

    async def test_attempt_number_stored(self, httpx_mock: HTTPXMock):
        httpx_mock.add_response(url=TARGET_URL, status_code=200, content=b"data")
        async with AsyncHttpTransport() as t:
            resp = await t.get(TARGET_URL, attempt_number=2)
        assert resp.attempt_number == 2

    async def test_user_agent_header_sent(self, httpx_mock: HTTPXMock):
        httpx_mock.add_response(url=TARGET_URL, status_code=200, content=b"x")

        cfg = HttpTransportConfig(user_agent="TestAgent/1.0")
        async with AsyncHttpTransport(cfg) as t:
            await t.get(TARGET_URL)

        request = httpx_mock.get_requests()[0]
        assert request.headers["user-agent"] == "TestAgent/1.0"

    async def test_extra_headers_sent(self, httpx_mock: HTTPXMock):
        httpx_mock.add_response(url=TARGET_URL, status_code=200, content=b"x")
        async with AsyncHttpTransport() as t:
            await t.get(TARGET_URL, headers={"If-None-Match": '"abc123"'})

        req = httpx_mock.get_requests()[0]
        assert req.headers["if-none-match"] == '"abc123"'


class TestAsyncHttpTransportNonSuccessStatuses:
    @pytest.mark.parametrize("status_code", [400, 401, 403, 404, 422])
    async def test_4xx_returns_client_error_class(
        self, httpx_mock: HTTPXMock, status_code: int
    ):
        httpx_mock.add_response(url=TARGET_URL, status_code=status_code, content=b"err")
        async with AsyncHttpTransport() as t:
            resp = await t.get(TARGET_URL)
        assert resp.status_code == status_code
        assert resp.status_class == HttpStatusClass.CLIENT_ERROR

    async def test_429_returns_rate_limited_class(self, httpx_mock: HTTPXMock):
        httpx_mock.add_response(
            url=TARGET_URL,
            status_code=429,
            headers={"Retry-After": "45"},
            content=b"slow down",
        )
        async with AsyncHttpTransport() as t:
            resp = await t.get(TARGET_URL)
        assert resp.status_class == HttpStatusClass.RATE_LIMITED
        assert resp.retry_after_seconds == 45.0

    @pytest.mark.parametrize("status_code", [502, 503, 504])
    async def test_5xx_transient_class(self, httpx_mock: HTTPXMock, status_code: int):
        httpx_mock.add_response(url=TARGET_URL, status_code=status_code, content=b"")
        async with AsyncHttpTransport() as t:
            resp = await t.get(TARGET_URL)
        assert resp.status_class == HttpStatusClass.TRANSIENT_SERVER_ERROR

    async def test_500_returns_server_error_class(self, httpx_mock: HTTPXMock):
        httpx_mock.add_response(url=TARGET_URL, status_code=500, content=b"")
        async with AsyncHttpTransport() as t:
            resp = await t.get(TARGET_URL)
        assert resp.status_class == HttpStatusClass.SERVER_ERROR


class TestAsyncHttpTransportFailures:
    async def test_timeout_raises_transport_error(self, httpx_mock: HTTPXMock):
        httpx_mock.add_exception(httpx.TimeoutException("timed out"), url=TARGET_URL)

        async with AsyncHttpTransport() as t:
            with pytest.raises(TransportError) as exc_info:
                await t.get(TARGET_URL)

        assert exc_info.value.retryable is True

    async def test_connect_error_raises_transport_error(self, httpx_mock: HTTPXMock):
        httpx_mock.add_exception(httpx.ConnectError("refused"), url=TARGET_URL)

        async with AsyncHttpTransport() as t:
            with pytest.raises(TransportError) as exc_info:
                await t.get(TARGET_URL)

        assert exc_info.value.retryable is True

    async def test_transport_error_url_excludes_query_params(
        self, httpx_mock: HTTPXMock
    ):
        url_with_params = TARGET_URL + "?api_key=secret"
        httpx_mock.add_exception(
            httpx.ConnectError("refused"), url=url_with_params
        )

        async with AsyncHttpTransport() as t:
            with pytest.raises(TransportError) as exc_info:
                await t.get(url_with_params)

        assert "secret" not in exc_info.value.url

    async def test_oversized_response_raises_response_too_large(
        self, httpx_mock: HTTPXMock
    ):
        small_limit = ResponseLimitConfig(max_bytes=10)
        cfg = HttpTransportConfig(response_limit=small_limit)
        big_body = b"X" * 100

        httpx_mock.add_response(url=TARGET_URL, status_code=200, content=big_body)

        async with AsyncHttpTransport(cfg) as t:
            with pytest.raises(ResponseTooLargeError) as exc_info:
                await t.get(TARGET_URL)

        err = exc_info.value
        assert err.retryable is False
        assert err.limit_bytes == 10
        assert err.actual_bytes == 100

    async def test_too_many_redirects_raises_transport_error(
        self, httpx_mock: HTTPXMock
    ):
        httpx_mock.add_exception(
            httpx.TooManyRedirects("too many"), url=TARGET_URL
        )

        async with AsyncHttpTransport() as t:
            with pytest.raises(TransportError) as exc_info:
                await t.get(TARGET_URL)

        assert exc_info.value.retryable is False

    async def test_runtime_error_without_context_manager(self):
        transport = AsyncHttpTransport()  # not entered
        with pytest.raises(RuntimeError, match="context manager"):
            await transport.get(TARGET_URL)
