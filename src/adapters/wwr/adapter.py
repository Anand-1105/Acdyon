"""WWR RSS Source Adapter.

Responsibility:
- Select the correct permitted WWR feed URL from the registry.
- Invoke the shared transport, rate limiter, and retry policy.
- Pass raw XML bytes to WWRRSSParser.
- Return a ParsedBatch with canonical records and structured errors.

This adapter knows:
- Which WWR feed URLs are permitted.
- How to translate IngestionRequest.category to a feed URL.
- How to compose transport, rate-limit, and retry infrastructure.
- How to bridge transport errors into domain IngestionErrors.

This adapter does NOT know:
- SQL or database schemas.
- FastAPI or HTTP request/response objects.
- Hunter enrichment.
- Any other source (LinkedIn, RemoteOK, etc.).
- How to parse RSS XML (that belongs to WWRRSSParser).

Retry loop contract:
- The adapter implements one retry loop per feed fetch.
- It delegates retry decisions to the shared RetryPolicy.
- It does NOT implement its own backoff logic.
- It delegates pacing to the shared RateLimiter.
- It does NOT add source-specific sleep() calls.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Optional

from src.adapters.base import BaseSourceAdapter, ParsedBatch
from src.adapters.wwr.config import (
    WWR_HTTP_CONFIG,
    WWR_RATE_LIMIT_CONFIG,
    WWR_RETRY_CONFIG,
    resolve_feed_url,
)
from src.adapters.wwr.parser import FeedParseResult, build_source_info, parse_feed
from src.domain.enums import IngestionErrorType, ErrorScope, SourceHealthStatus
from src.domain.errors import IngestionError
from src.domain.ingestion import IngestionRequest
from src.infrastructure.errors import error_from_http_status, error_from_transport_exception
from src.infrastructure.http.client import AsyncHttpTransport, TransportError
from src.infrastructure.reliability.limiter import RateLimiter
from src.infrastructure.reliability.retry import RetryDecision, RetryPolicy, RetryState

logger = logging.getLogger(__name__)

_SOURCE_NAME = "weworkremotely"


class WWRSourceAdapter(BaseSourceAdapter):
    """We Work Remotely RSS source adapter.

    Usage:
        adapter = WWRSourceAdapter()
        batch = await adapter.fetch_and_parse(request)

    The adapter manages its own rate limiter and retry policy instances.
    These are constructed once per adapter instance and reused across calls.
    If you want to inject custom configs (e.g. in tests), pass them to __init__.
    """

    def __init__(
        self,
        http_config=None,
        rate_limit_config=None,
        retry_config=None,
        *,
        _sleep_fn=None,       # injectable for testing (passed to RetryPolicy)
        _clock_fn=None,       # injectable for testing (passed to RateLimiter)
        _limiter_sleep_fn=None,
    ) -> None:
        self._http_config = http_config or WWR_HTTP_CONFIG
        self._rate_limit_config = rate_limit_config or WWR_RATE_LIMIT_CONFIG
        self._retry_config = retry_config or WWR_RETRY_CONFIG

        # Build the rate limiter once; it persists across calls to enforce
        # the inter-request interval correctly.
        limiter_kwargs = {}
        if _clock_fn is not None:
            limiter_kwargs["_clock_fn"] = _clock_fn
        if _limiter_sleep_fn is not None:
            limiter_kwargs["_sleep_fn"] = _limiter_sleep_fn

        self._limiter = RateLimiter(
            self._rate_limit_config,
            source_name=_SOURCE_NAME,
            **limiter_kwargs,
        )

        # Retry policy — stateless; a new RetryState is created per fetch call.
        retry_kwargs = {}
        if _sleep_fn is not None:
            retry_kwargs["_sleep_fn"] = _sleep_fn
        self._retry_policy = RetryPolicy(config=self._retry_config, **retry_kwargs)

    @property
    def source_name(self) -> str:
        return _SOURCE_NAME

    async def fetch_and_parse(self, request: IngestionRequest) -> ParsedBatch:
        """Fetch the WWR RSS feed and return a normalized ParsedBatch.

        Steps:
        1. Resolve feed URL from request.category (SSRF-safe registry lookup).
        2. Execute fetch with retry loop (delegating decisions to RetryPolicy).
        3. Each attempt goes through the RateLimiter.
        4. On success, parse XML via WWRRSSParser.
        5. Return ParsedBatch with records, errors, and source metadata.

        Feed-level failures (transport, HTTP error, XML parse failure) are
        returned as a failed ParsedBatch — not raised as exceptions — so the
        orchestrator can record telemetry and decide next steps.
        """
        retrieved_at = datetime.now(timezone.utc)

        # --- Step 1: Resolve feed URL (SSRF guard) ---
        try:
            category = request.category if request.category else None
            feed_url = resolve_feed_url(category)
        except ValueError as exc:
            return _failed_batch(
                source_name=_SOURCE_NAME,
                endpoint="unknown",
                message=str(exc),
                error_type=IngestionErrorType.INTERNAL_ERROR,
                retrieved_at=retrieved_at,
            )

        logger.info("WWR fetch: url=%s", feed_url)

        # --- Step 2: Fetch with retry loop ---
        state = RetryState()
        last_error: Optional[IngestionError] = None

        async with AsyncHttpTransport(self._http_config) as transport:
            while True:
                attempt = state.attempts_made + 1

                # Acquire rate limiter before each attempt
                async with self._limiter:
                    try:
                        response = await transport.get(
                            feed_url,
                            headers={"Accept": "application/rss+xml, application/xml, text/xml"},
                            attempt_number=attempt,
                        )
                    except TransportError as exc:
                        outcome = self._retry_policy.evaluate_exception(state, exc)
                        last_error = error_from_transport_exception(
                            exc, source_name=_SOURCE_NAME, attempt_number=attempt
                        )
                        if outcome.decision == RetryDecision.CANCEL:
                            raise
                        if outcome.decision == RetryDecision.RETRY:
                            logger.warning(
                                "WWR fetch attempt %d failed (transport): %s — retrying in %.1fs",
                                attempt, exc, outcome.delay_seconds,
                            )
                            await self._retry_policy.sleep(outcome.delay_seconds)
                            state.record_attempt(outcome.delay_seconds, outcome.used_retry_after)
                            continue
                        # STOP: exhausted or non-retryable
                        return _failed_batch_from_error(last_error, feed_url, retrieved_at)

                # We have a response — evaluate its status
                if not response.is_success:
                    outcome = self._retry_policy.evaluate(
                        state,
                        status_code=response.status_code,
                        retry_after_seconds=response.retry_after_seconds,
                    )
                    last_error = error_from_http_status(response, source_name=_SOURCE_NAME)
                    if outcome.decision == RetryDecision.RETRY:
                        logger.warning(
                            "WWR fetch attempt %d returned HTTP %d — retrying in %.1fs",
                            attempt, response.status_code, outcome.delay_seconds,
                        )
                        await self._retry_policy.sleep(outcome.delay_seconds)
                        state.record_attempt(outcome.delay_seconds, outcome.used_retry_after)
                        continue
                    # Non-retryable HTTP error
                    return _failed_batch_from_error(last_error, feed_url, retrieved_at)

                # --- Step 3: We have a successful HTTP response ---
                logger.info(
                    "WWR fetch succeeded: attempt=%d status=%d bytes=%d elapsed_ms=%.0f",
                    attempt, response.status_code, response.content_length, response.elapsed_ms,
                )
                xml_bytes = response.body
                break  # exit retry loop

        # --- Step 4: Parse the XML ---
        feed_result: FeedParseResult = parse_feed(xml_bytes, retrieved_at=retrieved_at)

        if feed_result.is_feed_error:
            assert feed_result.feed_error is not None
            source_info = build_source_info(
                endpoint=feed_url,
                retrieved_at=retrieved_at,
                health_status=SourceHealthStatus.DEGRADED,
            )
            return ParsedBatch(
                records=[],
                errors=[feed_result.feed_error],
                raw_count=0,
                source_info=source_info,
            )

        # --- Step 5: Build source metadata and return ---
        source_info = build_source_info(
            endpoint=feed_url,
            retrieved_at=retrieved_at,
            channel_title=feed_result.channel_title,
            health_status=SourceHealthStatus.HEALTHY,
        )

        logger.info(
            "WWR parse complete: raw=%d accepted=%d rejected=%d",
            feed_result.raw_count,
            len(feed_result.records),
            len(feed_result.errors),
        )

        return ParsedBatch(
            records=feed_result.records,
            errors=feed_result.errors,
            raw_count=feed_result.raw_count,
            source_info=source_info,
        )


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _failed_batch(
    source_name: str,
    endpoint: str,
    message: str,
    error_type: IngestionErrorType,
    retrieved_at: datetime,
    retryable: bool = False,
) -> ParsedBatch:
    error = IngestionError(
        error_type=error_type,
        scope=ErrorScope.RUN,
        message=message[:1024],
        retryable=retryable,
        details={"source_name": source_name},
        timestamp=retrieved_at,
    )
    source_info = build_source_info(
        endpoint=endpoint,
        retrieved_at=retrieved_at,
        health_status=SourceHealthStatus.DEGRADED,
    )
    return ParsedBatch(records=[], errors=[error], raw_count=0, source_info=source_info)


def _failed_batch_from_error(
    error: IngestionError,
    endpoint: str,
    retrieved_at: datetime,
) -> ParsedBatch:
    source_info = build_source_info(
        endpoint=endpoint,
        retrieved_at=retrieved_at,
        health_status=SourceHealthStatus.DEGRADED,
    )
    return ParsedBatch(records=[], errors=[error], raw_count=0, source_info=source_info)
