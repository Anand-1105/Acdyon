"""Resilience tests for Upstream Schema Drift & Structural Changes.

Exercises the full pipeline:
HTTPXMock (200 OK with structurally altered/incomplete XML) -> WWRRSSParser ->
IngestionService -> InMemoryStorage

Verifies:
1. Missing required semantic elements (e.g. missing <title>, empty title, missing link/guid) are safely rejected.
2. The parser never fabricates fake default data (e.g. no fake "Untitled" or placeholder URLs).
3. Valid sibling records in the drifted feed remain usable.
4. Structured INVALID_RECORD_ERROR diagnostics are recorded.
"""

from __future__ import annotations

import pytest
from pytest_httpx import HTTPXMock

from src.adapters.wwr.config import PRIMARY_FEED_URL
from src.domain.enums import IngestionErrorType, IngestionRunStatus
from src.domain.ingestion import IngestionRequest
from tests.resilience.conftest import make_fast_adapter, make_service

pytestmark = pytest.mark.asyncio


class TestSchemaDriftResilience:
    async def test_missing_title_element_rejected_without_fabrication(
        self, httpx_mock: HTTPXMock, memory_storage
    ):
        """Item is missing <title> element entirely."""
        xml_content = b"""<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0">
  <channel>
    <title>We Work Remotely</title>
    <link>https://weworkremotely.com</link>
    <item>
      <!-- <title> missing -->
      <link>https://weworkremotely.com/remote-jobs/no-title-job</link>
      <pubDate>Tue, 18 Aug 2026 12:00:00 +0000</pubDate>
      <description>Description without title</description>
    </item>
    <item>
      <title>Good Company: Good Title</title>
      <link>https://weworkremotely.com/remote-jobs/good-job</link>
      <pubDate>Tue, 18 Aug 2026 12:00:00 +0000</pubDate>
      <description>Good description</description>
    </item>
  </channel>
</rss>"""
        httpx_mock.add_response(url=PRIMARY_FEED_URL, status_code=200, content=xml_content)

        adapter = make_fast_adapter(max_attempts=1)
        service = make_service(memory_storage, adapter)

        result = await service.ingest(IngestionRequest())

        assert result.status == IngestionRunStatus.PARTIAL_SUCCESS
        assert len(result.records) == 1
        assert result.records[0].title == "Good Title"
        assert len(result.errors) == 1
        assert result.errors[0].error_type == IngestionErrorType.INVALID_RECORD_ERROR
        assert "title" in result.errors[0].message.lower()

    async def test_missing_link_and_guid_rejected_without_fabrication(
        self, httpx_mock: HTTPXMock, memory_storage
    ):
        """Item has title but no <link> or <guid> URL."""
        xml_content = b"""<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0">
  <channel>
    <title>We Work Remotely</title>
    <link>https://weworkremotely.com</link>
    <item>
      <title>Company: Job With No Link</title>
      <!-- link and guid both missing -->
      <pubDate>Tue, 18 Aug 2026 12:00:00 +0000</pubDate>
      <description>No URL available</description>
    </item>
    <item>
      <title>Company: Valid Job</title>
      <link>https://weworkremotely.com/remote-jobs/valid-job-url</link>
      <pubDate>Tue, 18 Aug 2026 12:00:00 +0000</pubDate>
      <description>Has URL</description>
    </item>
  </channel>
</rss>"""
        httpx_mock.add_response(url=PRIMARY_FEED_URL, status_code=200, content=xml_content)

        adapter = make_fast_adapter(max_attempts=1)
        service = make_service(memory_storage, adapter)

        result = await service.ingest(IngestionRequest())

        assert result.status == IngestionRunStatus.PARTIAL_SUCCESS
        assert len(result.records) == 1
        assert result.records[0].source_url == "https://weworkremotely.com/remote-jobs/valid-job-url"
        assert len(result.errors) == 1
        assert result.errors[0].error_type == IngestionErrorType.INVALID_RECORD_ERROR

    async def test_empty_string_fields_rejected_safely(
        self, httpx_mock: HTTPXMock, memory_storage
    ):
        """Item has whitespace-only <title> and whitespace-only <link>."""
        xml_content = b"""<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0">
  <channel>
    <title>We Work Remotely</title>
    <link>https://weworkremotely.com</link>
    <item>
      <title>   </title>
      <link>   </link>
      <pubDate>Tue, 18 Aug 2026 12:00:00 +0000</pubDate>
    </item>
  </channel>
</rss>"""
        httpx_mock.add_response(url=PRIMARY_FEED_URL, status_code=200, content=xml_content)

        adapter = make_fast_adapter(max_attempts=1)
        service = make_service(memory_storage, adapter)

        result = await service.ingest(IngestionRequest())

        assert result.status == IngestionRunStatus.FAILED
        assert len(result.records) == 0
        assert len(result.errors) == 1
        assert result.errors[0].error_type == IngestionErrorType.INVALID_RECORD_ERROR
