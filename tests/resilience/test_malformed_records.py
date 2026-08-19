"""Resilience tests for Malformed Individual Records and Partial Success.

Exercises the full pipeline:
HTTPXMock (200 with mixed valid/invalid items) -> WWRRSSParser -> IngestionService -> InMemoryStorage

Verifies:
1. Single invalid record is quarantined while valid sibling records are successfully parsed and persisted.
2. Run status becomes PARTIAL_SUCCESS with record-level error details.
3. Systemic/widespread record failure (all items invalid) is classified as a feed failure (FAILED status).
"""

from __future__ import annotations

import pytest
from pytest_httpx import HTTPXMock

from src.adapters.wwr.config import PRIMARY_FEED_URL
from src.domain.enums import IngestionErrorType, IngestionRunStatus, SourceHealthStatus
from src.domain.ingestion import IngestionRequest
from tests.resilience.conftest import load_fixture, make_fast_adapter, make_service

pytestmark = pytest.mark.asyncio


class TestMalformedRecordsResilience:
    async def test_isolated_invalid_record_yields_partial_success(
        self, httpx_mock: HTTPXMock, memory_storage
    ):
        """Feed with 2 valid jobs and 1 broken job item (unparseable pubDate)."""
        xml_content = b"""<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0">
  <channel>
    <title>We Work Remotely</title>
    <link>https://weworkremotely.com</link>
    <description>Jobs</description>
    <item>
      <title>Company A: Valid Engineer 1</title>
      <link>https://weworkremotely.com/remote-jobs/company-a-valid-engineer-1</link>
      <guid>https://weworkremotely.com/remote-jobs/company-a-valid-engineer-1</guid>
      <pubDate>Tue, 18 Aug 2026 12:00:00 +0000</pubDate>
      <description>Good job description</description>
    </item>
    <item>
      <title>Company B: Broken Date Job</title>
      <link>https://weworkremotely.com/remote-jobs/company-b-broken-date</link>
      <guid>https://weworkremotely.com/remote-jobs/company-b-broken-date</guid>
      <pubDate>INVALID_DATE_FORMAT_CANNOT_PARSE</pubDate>
      <description>Has broken date</description>
    </item>
    <item>
      <title>Company C: Valid Engineer 2</title>
      <link>https://weworkremotely.com/remote-jobs/company-c-valid-engineer-2</link>
      <guid>https://weworkremotely.com/remote-jobs/company-c-valid-engineer-2</guid>
      <pubDate>Tue, 18 Aug 2026 13:00:00 +0000</pubDate>
      <description>Another good job</description>
    </item>
  </channel>
</rss>"""
        httpx_mock.add_response(url=PRIMARY_FEED_URL, status_code=200, content=xml_content)

        adapter = make_fast_adapter(max_attempts=1)
        service = make_service(memory_storage, adapter)

        result = await service.ingest(IngestionRequest())

        # Verify Result
        assert result.status == IngestionRunStatus.PARTIAL_SUCCESS
        assert len(result.records) == 2
        assert result.stats.records_received == 3
        assert result.stats.records_accepted == 2
        assert result.stats.records_rejected == 1
        assert len(result.errors) == 1
        assert result.errors[0].error_type == IngestionErrorType.INVALID_RECORD_ERROR

        # Verify Persistence (2 valid jobs stored)
        persisted = await memory_storage.jobs.list_jobs(source_name="weworkremotely")
        assert len(persisted) == 2

        # Verify Snapshot (captures the 2 valid jobs)
        snapshot = await memory_storage.snapshots.get_latest_snapshot("weworkremotely")
        assert snapshot is not None
        assert snapshot.job_count == 2

    async def test_systemic_invalid_records_classified_as_run_failure(
        self, httpx_mock: HTTPXMock, memory_storage
    ):
        """Feed where 100% of items fail validation -> treated as a feed failure (FAILED status)."""
        xml_content = b"""<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0">
  <channel>
    <title>We Work Remotely</title>
    <link>https://weworkremotely.com</link>
    <description>Jobs</description>
    <item>
      <title>Company A: Broken Item 1</title>
      <link>https://weworkremotely.com/remote-jobs/broken-1</link>
      <pubDate>INVALID_DATE_1</pubDate>
    </item>
    <item>
      <title>Company B: Broken Item 2</title>
      <link>https://weworkremotely.com/remote-jobs/broken-2</link>
      <pubDate>INVALID_DATE_2</pubDate>
    </item>
  </channel>
</rss>"""
        httpx_mock.add_response(url=PRIMARY_FEED_URL, status_code=200, content=xml_content)

        adapter = make_fast_adapter(max_attempts=1)
        service = make_service(memory_storage, adapter)

        result = await service.ingest(IngestionRequest())

        # When 0 valid records exist and errors > 0, orchestrator marks run FAILED
        assert result.status == IngestionRunStatus.FAILED
        assert len(result.records) == 0
        assert len(result.errors) == 2
        assert result.stats.records_accepted == 0
        assert result.stats.records_rejected == 2
