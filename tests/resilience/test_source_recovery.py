"""Resilience tests for Source Health State Machine & Outage Recovery.

Exercises the full lifecycle transitions:
HEALTHY -> DEGRADED (1-2 failures) -> UNREACHABLE (>=3 failures) -> RECOVERY -> HEALTHY

Verifies:
1. Consecutive failure counter accurately tracks repeated outages.
2. Health status transitions through DEGRADED to UNREACHABLE.
3. Pre-existing last-known-good snapshot remains frozen throughout the outage.
4. Subsequent successful ingestion resets consecutive failures to 0, marks source HEALTHY,
   and updates the snapshot with fresh job references.
"""

from __future__ import annotations

import pytest
from pytest_httpx import HTTPXMock

from src.adapters.wwr.config import PRIMARY_FEED_URL
from src.domain.enums import IngestionRunStatus, SourceHealthStatus
from src.domain.ingestion import IngestionRequest
from tests.resilience.conftest import load_fixture, make_fast_adapter, make_service

pytestmark = pytest.mark.asyncio


class TestSourceRecoveryResilience:
    async def test_full_health_lifecycle_and_recovery(
        self, httpx_mock: HTTPXMock, memory_storage
    ):
        xml_initial = load_fixture("wwr_valid.xml")
        xml_fresh = b"""<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0">
  <channel>
    <title>We Work Remotely</title>
    <link>https://weworkremotely.com</link>
    <item>
      <title>Company Fresh: New Senior Engineer</title>
      <link>https://weworkremotely.com/remote-jobs/new-senior-eng</link>
      <guid>https://weworkremotely.com/remote-jobs/new-senior-eng</guid>
      <pubDate>Tue, 18 Aug 2026 15:00:00 +0000</pubDate>
      <description>Fresh job post</description>
    </item>
  </channel>
</rss>"""

        adapter = make_fast_adapter(max_attempts=1)
        service = make_service(memory_storage, adapter)

        # -------------------------------------------------------------
        # Phase 1: Initial Successful Ingestion (HEALTHY)
        # -------------------------------------------------------------
        httpx_mock.add_response(url=PRIMARY_FEED_URL, status_code=200, content=xml_initial)
        res1 = await service.ingest(IngestionRequest())

        assert res1.status == IngestionRunStatus.SUCCESS
        health1 = await memory_storage.health.get_health("weworkremotely")
        assert health1.health_status == SourceHealthStatus.HEALTHY
        assert health1.consecutive_failures == 0

        snapshot1 = await memory_storage.snapshots.get_latest_snapshot("weworkremotely")
        assert snapshot1.job_count == 3
        initial_snapshot_ids = snapshot1.canonical_ids

        # -------------------------------------------------------------
        # Phase 2: Outage 1 (DEGRADED, consecutive_failures = 1)
        # -------------------------------------------------------------
        httpx_mock.add_response(url=PRIMARY_FEED_URL, status_code=503, content=b"Server error")
        res2 = await service.ingest(IngestionRequest())

        assert res2.status == IngestionRunStatus.FAILED
        health2 = await memory_storage.health.get_health("weworkremotely")
        assert health2.health_status == SourceHealthStatus.DEGRADED
        assert health2.consecutive_failures == 1

        # Snapshot remains untouched
        snap_during_outage_1 = await memory_storage.snapshots.get_latest_snapshot("weworkremotely")
        assert snap_during_outage_1.canonical_ids == initial_snapshot_ids

        # -------------------------------------------------------------
        # Phase 3: Outage 2 (DEGRADED, consecutive_failures = 2)
        # -------------------------------------------------------------
        httpx_mock.add_response(url=PRIMARY_FEED_URL, status_code=503, content=b"Server error")
        res3 = await service.ingest(IngestionRequest())

        assert res3.status == IngestionRunStatus.FAILED
        health3 = await memory_storage.health.get_health("weworkremotely")
        assert health3.health_status == SourceHealthStatus.DEGRADED
        assert health3.consecutive_failures == 2

        # -------------------------------------------------------------
        # Phase 4: Outage 3 (UNREACHABLE, consecutive_failures = 3)
        # -------------------------------------------------------------
        httpx_mock.add_response(url=PRIMARY_FEED_URL, status_code=503, content=b"Server error")
        res4 = await service.ingest(IngestionRequest())

        assert res4.status == IngestionRunStatus.FAILED
        health4 = await memory_storage.health.get_health("weworkremotely")
        assert health4.health_status == SourceHealthStatus.UNREACHABLE
        assert health4.consecutive_failures == 3

        # Snapshot is STILL intact
        snap_during_outage_3 = await memory_storage.snapshots.get_latest_snapshot("weworkremotely")
        assert snap_during_outage_3.canonical_ids == initial_snapshot_ids

        # -------------------------------------------------------------
        # Phase 5: Recovery (HEALTHY, consecutive_failures = 0, new snapshot)
        # -------------------------------------------------------------
        httpx_mock.add_response(url=PRIMARY_FEED_URL, status_code=200, content=xml_fresh)
        res5 = await service.ingest(IngestionRequest())

        assert res5.status == IngestionRunStatus.SUCCESS
        assert len(res5.records) == 1

        # Health is restored to HEALTHY and consecutive_failures reset
        health5 = await memory_storage.health.get_health("weworkremotely")
        assert health5.health_status == SourceHealthStatus.HEALTHY
        assert health5.consecutive_failures == 0
        assert health5.last_success_at is not None

        # Snapshot is updated with the new job
        snapshot5 = await memory_storage.snapshots.get_latest_snapshot("weworkremotely")
        assert snapshot5.job_count == 1
        assert snapshot5.canonical_ids != initial_snapshot_ids
