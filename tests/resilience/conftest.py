"""Shared fixtures and test doubles for the Resilience and Failure Laboratory.

Provides zero-sleep adapters, memory storage instances, and XML test fixtures
to exercise real HTTP transport, XML parsing, retry policy, rate limiting, and persistence
boundaries under simulated adverse conditions.
"""

from __future__ import annotations

from pathlib import Path
from typing import List

import pytest

from src.adapters.wwr.adapter import WWRSourceAdapter
from src.infrastructure.config import RateLimitConfig, RetryConfig
from src.services.orchestrator import IngestionService
from src.services.registry import SourceAdapterRegistry
from src.storage.memory import InMemoryStorage

FIXTURE_DIR = Path(__file__).parent.parent / "fixtures"


def load_fixture(filename: str) -> bytes:
    """Load raw XML fixture bytes from tests/fixtures."""
    return (FIXTURE_DIR / filename).read_bytes()


def make_fast_adapter(
    max_attempts: int = 3,
    min_interval_seconds: float = 0.0,
    base_backoff_seconds: float = 0.001,
) -> WWRSourceAdapter:
    """Create a real WWRSourceAdapter with fast zero-wait timers for deterministic testing."""
    rate_cfg = RateLimitConfig(
        min_interval_seconds=min_interval_seconds,
        max_concurrent=10,
    )
    retry_cfg = RetryConfig(
        max_attempts=max_attempts,
        jitter_factor=0.0,
        base_backoff_seconds=base_backoff_seconds,
        max_backoff_seconds=0.1,
    )

    sleep_calls: List[float] = []

    async def stub_sleep(s: float) -> None:
        sleep_calls.append(s)

    adapter = WWRSourceAdapter(
        rate_limit_config=rate_cfg,
        retry_config=retry_cfg,
        _sleep_fn=stub_sleep,
        _limiter_sleep_fn=stub_sleep,
    )
    adapter._test_sleep_calls = sleep_calls
    return adapter


def make_service(
    storage: InMemoryStorage,
    adapter: WWRSourceAdapter,
) -> IngestionService:
    """Construct an IngestionService wired to the provided storage and adapter."""
    registry = SourceAdapterRegistry()
    registry.register(adapter)
    return IngestionService(
        job_repo=storage.jobs,
        run_repo=storage.runs,
        health_repo=storage.health,
        snapshot_repo=storage.snapshots,
        registry=registry,
    )


@pytest.fixture
def memory_storage() -> InMemoryStorage:
    """Provide fresh in-memory repositories for a resilience test."""
    return InMemoryStorage()
