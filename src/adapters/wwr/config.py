"""WWR feed URL registry and adapter-level configuration.

This module defines the complete set of permitted WWR RSS feed URLs.
No URL outside this registry may be requested by the adapter.

Security contract:
- The adapter MUST NOT accept an arbitrary caller-supplied URL.
- All outbound requests must use URLs from this registry.
- Adding a new feed URL requires a code change here, not a runtime parameter.

WWR feed structure (as of 2026-08-18):
- Primary feed:   all current remote jobs across all categories
- Category feeds: scoped to a single job category

Feed URL format: https://weworkremotely.com/categories/<slug>.rss
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional

from src.infrastructure.config import (
    HttpTransportConfig,
    RateLimitConfig,
    ResponseLimitConfig,
    RetryConfig,
    TimeoutConfig,
)


# ---------------------------------------------------------------------------
# Known/permitted WWR feed endpoints
# ---------------------------------------------------------------------------

#: The primary all-jobs feed URL
PRIMARY_FEED_URL = "https://weworkremotely.com/remote-jobs.rss"

#: Mapping of short category keys to their canonical feed URLs.
#: Only these identifiers are accepted when the caller passes a category.
CATEGORY_FEED_URLS: dict[str, str] = {
    "programming": "https://weworkremotely.com/categories/remote-programming-jobs.rss",
    "devops": "https://weworkremotely.com/categories/remote-devops-sysadmin-jobs.rss",
    "design": "https://weworkremotely.com/categories/remote-design-jobs.rss",
    "management": "https://weworkremotely.com/categories/remote-management-finance-jobs.rss",
    "marketing": "https://weworkremotely.com/categories/remote-marketing-jobs.rss",
    "sales": "https://weworkremotely.com/categories/remote-sales-jobs.rss",
    "customer-support": "https://weworkremotely.com/categories/remote-customer-support-jobs.rss",
    "writing": "https://weworkremotely.com/categories/remote-writing-editing-jobs.rss",
    "product": "https://weworkremotely.com/categories/remote-product-jobs.rss",
    "data": "https://weworkremotely.com/categories/remote-data-science-jobs.rss",
    "all-other": "https://weworkremotely.com/categories/remote-all-other-jobs.rss",
}

#: Union of all permitted URLs for fast membership testing
_ALL_PERMITTED_URLS: frozenset[str] = frozenset(
    {PRIMARY_FEED_URL} | set(CATEGORY_FEED_URLS.values())
)


def resolve_feed_url(category: Optional[str] = None) -> str:
    """Return the permitted feed URL for the requested category.

    Args:
        category: A known short category key (e.g. 'programming', 'devops')
                  or None to select the primary all-jobs feed.

    Returns:
        A known WWR RSS feed URL.

    Raises:
        ValueError: If category is provided but not in the permitted registry.
    """
    if category is None or category.strip() == "":
        return PRIMARY_FEED_URL

    key = category.strip().lower()
    url = CATEGORY_FEED_URLS.get(key)
    if url is None:
        allowed = sorted(CATEGORY_FEED_URLS.keys())
        raise ValueError(
            f"Unknown WWR category: {key!r}. "
            f"Permitted categories: {allowed}. "
            "Use category=None for the primary all-jobs feed."
        )
    return url


def is_permitted_url(url: str) -> bool:
    """Return True if the URL is in the permitted WWR feed registry."""
    return url in _ALL_PERMITTED_URLS


# ---------------------------------------------------------------------------
# WWR-specific transport configuration
# ---------------------------------------------------------------------------
# These values are tuned specifically for the public WWR RSS feed:
# - 3-second minimum interval respects polite pacing and avoids Cloudflare friction.
# - max_concurrent=1: no parallelism against the same host.
# - 3 retry attempts for transient errors.

WWR_RATE_LIMIT_CONFIG = RateLimitConfig(
    min_interval_seconds=3.0,
    max_concurrent=1,
)

WWR_RETRY_CONFIG = RetryConfig(
    max_attempts=3,
    base_backoff_seconds=2.0,
    max_backoff_seconds=30.0,
    max_retry_after_seconds=60.0,
    jitter_factor=0.5,
)

WWR_HTTP_CONFIG = HttpTransportConfig(
    timeout=TimeoutConfig(connect_seconds=5.0, read_seconds=15.0),
    response_limit=ResponseLimitConfig(max_bytes=10 * 1024 * 1024),
    user_agent="Acdyon-JobIngest/1.0 (Assessment Evaluation)",
    follow_redirects=True,
    max_redirects=5,
)
