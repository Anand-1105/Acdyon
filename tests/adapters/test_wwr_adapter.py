"""Tests for WWRSourceAdapter (src/adapters/wwr/adapter.py).

Covers:
- Successful fetch + parse returns a ParsedBatch with records.
- HTTP 429 with Retry-After triggers retry via RetryPolicy.
- HTTP 503 triggers retry.
- All retries exhausted → failed ParsedBatch (no exception raised).
- TransportError → failed ParsedBatch.
- Malformed XML → failed ParsedBatch (is_feed_error handled correctly).
- Unknown category → failed ParsedBatch immediately (no HTTP request).
- Known category → correct feed URL requested.
- Oversized response → failed ParsedBatch (handled by transport layer).
- Empty feed → ParsedBatch with zero records, zero errors.
- Source info is correctly populated on success.
- Source info has DEGRADED health on transport failure.
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import List
from unittest.mock import AsyncMock, patch

import httpx
import pytest
from pytest_httpx import HTTPXMock

from src.adapters.wwr.adapter import WWRSourceAdapter
from src.adapters.wwr.config import PRIMARY_FEED_URL, CATEGORY_FEED_URLS
from src.domain.enums import IngestionErrorType, SourceHealthStatus
from src.domain.ingestion import IngestionRequest
from src.infrastructure.config import (
    HttpTransportConfig,
    RateLimitConfig,
    ResponseLimitConfig,
    RetryConfig,
    TimeoutConfig,
)

pytestmark = pytest.mark.asyncio

FIXTURE_DIR = Path(__file__).parent.parent / "fixtures"


def _load(filename: str) -> bytes:
    return (FIXTURE_DIR / filename).read_bytes()


def _no_pacing_adapter(max_attempts: int = 1, **kwargs) -> WWRSourceAdapter:
    """Build an adapter with pacing and retries disabled for unit tests."""
    # Zero interval so tests don't actually wait.
    rate_cfg = RateLimitConfig(min_interval_seconds=0.0, max_concurrent=10)
    retry_cfg = RetryConfig(max_attempts=max_attempts, jitter_factor=0.0, base_backoff_seconds=0.001)

    sleep_calls: List[float] = []

    async def stub_sleep(s: float) -> None:
        sleep_calls.append(s)

    adapter = WWRSourceAdapter(
        rate_limit_config=rate_cfg,
        retry_config=retry_cfg,
        _sleep_fn=stub_sleep,
        _limiter_sleep_fn=stub_sleep,
        **kwargs,
    )
    adapter._sleep_calls = sleep_calls  # expose for assertions
    return adapter


# ─────────────────────────────────────────────────────────────────────────────
# Successful fetch
# ─────────────────────────────────────────────────────────────────────────────

class TestSuccessfulFetch:
    async def test_success_returns_records(self, httpx_mock: HTTPXMock):
        xml = _load("wwr_valid.xml")
        httpx_mock.add_response(url=PRIMARY_FEED_URL, status_code=200, content=xml)

        adapter = _no_pacing_adapter()
        batch = await adapter.fetch_and_parse(IngestionRequest())

        assert len(batch.records) == 3
        assert len(batch.errors) == 0
        assert batch.raw_count == 3

    async def test_source_info_is_healthy(self, httpx_mock: HTTPXMock):
        xml = _load("wwr_valid.xml")
        httpx_mock.add_response(url=PRIMARY_FEED_URL, status_code=200, content=xml)

        adapter = _no_pacing_adapter()
        batch = await adapter.fetch_and_parse(IngestionRequest())

        assert batch.source_info.health_status == SourceHealthStatus.HEALTHY
        assert batch.source_info.endpoint == PRIMARY_FEED_URL
        assert "weworkremotely" in batch.source_info.source_name

    async def test_source_info_channel_title_populated(self, httpx_mock: HTTPXMock):
        xml = _load("wwr_valid.xml")
        httpx_mock.add_response(url=PRIMARY_FEED_URL, status_code=200, content=xml)

        adapter = _no_pacing_adapter()
        batch = await adapter.fetch_and_parse(IngestionRequest())

        assert "We Work Remotely" in batch.source_info.metadata.get("channel_title", "")

    async def test_empty_feed_returns_zero_records(self, httpx_mock: HTTPXMock):
        xml = _load("wwr_empty.xml")
        httpx_mock.add_response(url=PRIMARY_FEED_URL, status_code=200, content=xml)

        adapter = _no_pacing_adapter()
        batch = await adapter.fetch_and_parse(IngestionRequest())

        assert len(batch.records) == 0
        assert len(batch.errors) == 0
        assert batch.raw_count == 0
        assert batch.source_info.health_status == SourceHealthStatus.HEALTHY

    async def test_category_feed_url_used(self, httpx_mock: HTTPXMock):
        xml = _load("wwr_valid.xml")
        programming_url = CATEGORY_FEED_URLS["programming"]
        httpx_mock.add_response(url=programming_url, status_code=200, content=xml)

        adapter = _no_pacing_adapter()
        batch = await adapter.fetch_and_parse(IngestionRequest(category="programming"))

        assert batch.source_info.endpoint == programming_url
        assert len(batch.records) == 3

    async def test_records_have_correct_source_name(self, httpx_mock: HTTPXMock):
        xml = _load("wwr_valid.xml")
        httpx_mock.add_response(url=PRIMARY_FEED_URL, status_code=200, content=xml)

        adapter = _no_pacing_adapter()
        batch = await adapter.fetch_and_parse(IngestionRequest())

        for record in batch.records:
            assert record.source_name == "weworkremotely"


# ─────────────────────────────────────────────────────────────────────────────
# HTTP error handling
# ─────────────────────────────────────────────────────────────────────────────

class TestHttpErrors:
    async def test_http_429_with_one_retry_succeeds(self, httpx_mock: HTTPXMock):
        xml = _load("wwr_valid.xml")
        # First request: 429; second: 200
        httpx_mock.add_response(
            url=PRIMARY_FEED_URL, status_code=429,
            headers={"Retry-After": "0"}, content=b"slow down",
        )
        httpx_mock.add_response(url=PRIMARY_FEED_URL, status_code=200, content=xml)

        adapter = _no_pacing_adapter(max_attempts=2)
        batch = await adapter.fetch_and_parse(IngestionRequest())

        assert len(batch.records) == 3
        assert batch.source_info.health_status == SourceHealthStatus.HEALTHY

    async def test_http_503_with_one_retry_succeeds(self, httpx_mock: HTTPXMock):
        xml = _load("wwr_valid.xml")
        httpx_mock.add_response(url=PRIMARY_FEED_URL, status_code=503, content=b"")
        httpx_mock.add_response(url=PRIMARY_FEED_URL, status_code=200, content=xml)

        adapter = _no_pacing_adapter(max_attempts=2)
        batch = await adapter.fetch_and_parse(IngestionRequest())

        assert len(batch.records) == 3

    async def test_all_retries_exhausted_returns_failed_batch(self, httpx_mock: HTTPXMock):
        httpx_mock.add_response(url=PRIMARY_FEED_URL, status_code=503, content=b"")
        httpx_mock.add_response(url=PRIMARY_FEED_URL, status_code=503, content=b"")
        httpx_mock.add_response(url=PRIMARY_FEED_URL, status_code=503, content=b"")

        adapter = _no_pacing_adapter(max_attempts=3)
        batch = await adapter.fetch_and_parse(IngestionRequest())

        assert len(batch.records) == 0
        assert len(batch.errors) == 1
        err = batch.errors[0]
        assert err.error_type == IngestionErrorType.SOURCE_SERVER_ERROR
        assert batch.source_info.health_status == SourceHealthStatus.DEGRADED

    async def test_http_404_stops_without_retry(self, httpx_mock: HTTPXMock):
        httpx_mock.add_response(url=PRIMARY_FEED_URL, status_code=404, content=b"not found")

        adapter = _no_pacing_adapter(max_attempts=3)
        batch = await adapter.fetch_and_parse(IngestionRequest())

        # Only one request should have been made (no retry on 404)
        assert len(httpx_mock.get_requests()) == 1
        assert len(batch.records) == 0
        assert batch.source_info.health_status == SourceHealthStatus.DEGRADED

    async def test_http_400_not_retried(self, httpx_mock: HTTPXMock):
        httpx_mock.add_response(url=PRIMARY_FEED_URL, status_code=400, content=b"bad request")

        adapter = _no_pacing_adapter(max_attempts=3)
        batch = await adapter.fetch_and_parse(IngestionRequest())

        assert len(httpx_mock.get_requests()) == 1
        assert len(batch.records) == 0


# ─────────────────────────────────────────────────────────────────────────────
# Transport errors
# ─────────────────────────────────────────────────────────────────────────────

class TestTransportErrors:
    async def test_timeout_returns_failed_batch(self, httpx_mock: HTTPXMock):
        httpx_mock.add_exception(httpx.TimeoutException("timed out"), url=PRIMARY_FEED_URL)

        adapter = _no_pacing_adapter(max_attempts=1)
        batch = await adapter.fetch_and_parse(IngestionRequest())

        assert len(batch.records) == 0
        assert len(batch.errors) == 1
        assert batch.errors[0].error_type == IngestionErrorType.TIMEOUT_ERROR
        assert batch.source_info.health_status == SourceHealthStatus.DEGRADED

    async def test_connect_error_returns_failed_batch(self, httpx_mock: HTTPXMock):
        httpx_mock.add_exception(httpx.ConnectError("refused"), url=PRIMARY_FEED_URL)

        adapter = _no_pacing_adapter(max_attempts=1)
        batch = await adapter.fetch_and_parse(IngestionRequest())

        assert len(batch.records) == 0
        assert len(batch.errors) == 1
        assert batch.errors[0].error_type == IngestionErrorType.NETWORK_TRANSPORT_ERROR

    async def test_transport_error_retried_then_succeeds(self, httpx_mock: HTTPXMock):
        xml = _load("wwr_valid.xml")
        httpx_mock.add_exception(httpx.ConnectError("refused"), url=PRIMARY_FEED_URL)
        httpx_mock.add_response(url=PRIMARY_FEED_URL, status_code=200, content=xml)

        adapter = _no_pacing_adapter(max_attempts=2)
        batch = await adapter.fetch_and_parse(IngestionRequest())

        assert len(batch.records) == 3

    async def test_oversized_response_returns_failed_batch(self, httpx_mock: HTTPXMock):
        small_limit_cfg = HttpTransportConfig(
            response_limit=ResponseLimitConfig(max_bytes=10),
        )
        httpx_mock.add_response(
            url=PRIMARY_FEED_URL, status_code=200, content=b"X" * 100
        )

        rate_cfg = RateLimitConfig(min_interval_seconds=0.0)
        retry_cfg = RetryConfig(max_attempts=1, jitter_factor=0.0, base_backoff_seconds=0.001)
        adapter = WWRSourceAdapter(
            http_config=small_limit_cfg,
            rate_limit_config=rate_cfg,
            retry_config=retry_cfg,
        )
        batch = await adapter.fetch_and_parse(IngestionRequest())

        assert len(batch.records) == 0
        assert len(batch.errors) == 1
        assert batch.errors[0].error_type == IngestionErrorType.MALFORMED_RESPONSE_ERROR


# ─────────────────────────────────────────────────────────────────────────────
# Malformed XML feed
# ─────────────────────────────────────────────────────────────────────────────

class TestMalformedFeedResponse:
    async def test_malformed_xml_returns_feed_level_error(self, httpx_mock: HTTPXMock):
        xml = _load("wwr_malformed.xml")
        httpx_mock.add_response(url=PRIMARY_FEED_URL, status_code=200, content=xml)

        adapter = _no_pacing_adapter()
        batch = await adapter.fetch_and_parse(IngestionRequest())

        assert len(batch.records) == 0
        assert len(batch.errors) == 1
        assert batch.errors[0].error_type == IngestionErrorType.MALFORMED_RESPONSE_ERROR
        assert batch.source_info.health_status == SourceHealthStatus.DEGRADED


# ─────────────────────────────────────────────────────────────────────────────
# SSRF / URL registry
# ─────────────────────────────────────────────────────────────────────────────

class TestUrlRegistry:
    async def test_unknown_category_returns_error_no_http_request(self, httpx_mock: HTTPXMock):
        adapter = _no_pacing_adapter()
        batch = await adapter.fetch_and_parse(IngestionRequest(category="definitely_not_real"))

        # No HTTP requests should have been made
        assert len(httpx_mock.get_requests()) == 0
        assert len(batch.records) == 0
        assert len(batch.errors) == 1
        assert batch.errors[0].error_type == IngestionErrorType.INTERNAL_ERROR

    async def test_no_category_uses_primary_feed(self, httpx_mock: HTTPXMock):
        xml = _load("wwr_valid.xml")
        httpx_mock.add_response(url=PRIMARY_FEED_URL, status_code=200, content=xml)

        adapter = _no_pacing_adapter()
        await adapter.fetch_and_parse(IngestionRequest())

        requests = httpx_mock.get_requests()
        assert len(requests) == 1
        assert str(requests[0].url) == PRIMARY_FEED_URL


# ─────────────────────────────────────────────────────────────────────────────
# Source name
# ─────────────────────────────────────────────────────────────────────────────

class TestAdapterMetadata:
    async def test_source_name_property(self):
        adapter = WWRSourceAdapter()
        assert adapter.source_name == "weworkremotely"
