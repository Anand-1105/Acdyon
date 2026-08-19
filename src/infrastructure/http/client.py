"""Async HTTP transport layer.

Responsibility: Execute outbound HTTP GET requests and return a FetchResponse.

This module knows about:
- TCP connections, timeouts, and connection pooling.
- Response size limits.
- Normalizing transport-level exceptions into TransportError.
- Redirect following.
- Header hygiene (User-Agent, safe request headers).

This module does NOT know about:
- What a job is.
- RSS or XML structure.
- Domain validation.
- Retry decisions (that belongs to RetryPolicy).
- Rate limiting (that belongs to RateLimiter).
- Any specific source such as WWR.

Lifecycle:
    AsyncHttpTransport is designed to be used as an async context manager.
    A single HTTPX AsyncClient is created on __aenter__ and cleanly closed on __aexit__.
    This ensures connection pool reuse within an ingestion run and safe cleanup.

    async with AsyncHttpTransport(config) as transport:
        response = await transport.get(url, headers={...})
"""

from __future__ import annotations

import time
from types import TracebackType
from typing import Optional, Type

import httpx

from src.infrastructure.config import HttpTransportConfig, TimeoutConfig
from src.infrastructure.http.response import (
    FetchResponse,
    HttpStatusClass,
    classify_status,
)


class TransportError(Exception):
    """Raised by AsyncHttpTransport for connection and protocol-level failures.

    This is a transport-level exception; it is NOT a domain IngestionError.
    The retry policy and adapter layer convert this into domain errors when needed.

    Attributes:
        message:      Human-readable description (no credentials).
        retryable:    Whether the caller should consider retrying.
        cause:        The underlying exception for debug context, if available.
        url:          The requested URL (without query parameters or auth).
    """

    def __init__(
        self,
        message: str,
        retryable: bool = False,
        cause: Optional[BaseException] = None,
        url: str = "",
    ) -> None:
        super().__init__(message)
        self.retryable = retryable
        self.cause = cause
        self.url = url

    def __repr__(self) -> str:
        return (
            f"TransportError(message={str(self)!r}, "
            f"retryable={self.retryable}, url={self.url!r})"
        )


class ResponseTooLargeError(TransportError):
    """Raised when the response body exceeds the configured size limit.

    Never retried — the same request will produce the same oversized response.
    """

    def __init__(self, url: str, limit_bytes: int, actual_bytes: int) -> None:
        super().__init__(
            message=(
                f"Response body ({actual_bytes:,} bytes) exceeds the "
                f"configured limit of {limit_bytes:,} bytes"
            ),
            retryable=False,
            url=url,
        )
        self.limit_bytes = limit_bytes
        self.actual_bytes = actual_bytes


def _build_httpx_timeout(config: TimeoutConfig) -> httpx.Timeout:
    """Translate our TimeoutConfig into an httpx.Timeout object."""
    return httpx.Timeout(
        connect=config.connect_seconds,
        read=config.read_seconds,
        write=config.connect_seconds,   # reuse connect for write; we only GET
        pool=config.effective_pool_seconds,
    )


def _safe_url_for_logging(url: str) -> str:
    """Strip query parameters from a URL before including it in any log or error.

    This prevents accidentally logging API keys or tracking tokens that sometimes
    appear in query strings.
    """
    try:
        parsed = httpx.URL(url)
        return str(parsed.copy_with(query=b""))
    except Exception:
        return "[unparseable url]"


def _parse_retry_after(headers: dict[str, str]) -> Optional[float]:
    """Safely extract a Retry-After delay in seconds from response headers.

    Handles both numeric (seconds) and HTTP-date formats.
    Returns None when the header is absent or unparseable.
    """
    raw = headers.get("retry-after", "").strip()
    if not raw:
        return None
    # Numeric form: "Retry-After: 30"
    try:
        return float(raw)
    except ValueError:
        pass
    # HTTP-date form: "Retry-After: Wed, 21 Oct 2025 07:28:00 GMT"
    try:
        import email.utils
        parsed_time = email.utils.parsedate_to_datetime(raw)
        delay = (parsed_time - parsed_time.now(tz=parsed_time.tzinfo)).total_seconds()
        return max(0.0, delay)
    except Exception:
        return None


class AsyncHttpTransport:
    """Reusable async HTTP transport for outbound GET requests.

    Usage:
        async with AsyncHttpTransport(config) as transport:
            response = await transport.get("https://example.com/feed.rss")

    The transport is intentionally restricted to GET requests because the
    ingestion system only reads from external job feeds.

    It is safe to call .get() multiple times within a single context; the
    underlying HTTPX client and its connection pool are reused.
    """

    def __init__(self, config: Optional[HttpTransportConfig] = None) -> None:
        self._config = config or HttpTransportConfig()
        self._client: Optional[httpx.AsyncClient] = None

    async def __aenter__(self) -> "AsyncHttpTransport":
        # Full browser-like headers to blend request traffic naturally
        browser_headers = {
            "User-Agent": self._config.user_agent,
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,image/apng,*/*;q=0.8",
            "Accept-Language": "en-US,en;q=0.9",
            "Accept-Encoding": "gzip, deflate, br",
            "Sec-Ch-Ua": '"Not_A Brand";v="8", "Chromium";v="120", "Google Chrome";v="120"',
            "Sec-Ch-Ua-Mobile": "?0",
            "Sec-Ch-Ua-Platform": '"Windows"',
            "Sec-Fetch-Dest": "document",
            "Sec-Fetch-Mode": "navigate",
            "Sec-Fetch-Site": "none",
            "Sec-Fetch-User": "?1",
            "Upgrade-Insecure-Requests": "1"
        }
        self._client = httpx.AsyncClient(
            timeout=_build_httpx_timeout(self._config.timeout),
            follow_redirects=self._config.follow_redirects,
            max_redirects=self._config.max_redirects,
            headers=browser_headers,
        )
        return self

    async def __aexit__(
        self,
        exc_type: Optional[Type[BaseException]],
        exc_val: Optional[BaseException],
        exc_tb: Optional[TracebackType],
    ) -> None:
        if self._client is not None:
            await self._client.aclose()
            self._client = None

    async def get(
        self,
        url: str,
        headers: Optional[dict[str, str]] = None,
        attempt_number: int = 1,
    ) -> FetchResponse:
        """Execute a single HTTP GET request and return a FetchResponse.

        Args:
            url:            Target URL. Must be http or https.
            headers:        Additional request headers merged with transport defaults.
                            Must not include credentials; those belong in the adapter config.
            attempt_number: 1-based attempt counter for telemetry/logging.

        Returns:
            FetchResponse containing status, headers, body, and elapsed time.

        Raises:
            TransportError:        On network failures (DNS, connection, protocol).
            ResponseTooLargeError: When body exceeds the configured size limit.
            asyncio.CancelledError: Propagated directly — not treated as a retryable error.
        """
        if self._client is None:
            raise RuntimeError(
                "AsyncHttpTransport must be used as an async context manager. "
                "Call 'async with AsyncHttpTransport(...) as transport:' first."
            )

        safe_url = _safe_url_for_logging(url)
        start = time.monotonic()

        try:
            raw = await self._client.get(url, headers=headers or {})
        except httpx.TimeoutException as exc:
            raise TransportError(
                message=f"Request timed out: {safe_url}",
                retryable=True,
                cause=exc,
                url=safe_url,
            ) from exc
        except httpx.ConnectError as exc:
            raise TransportError(
                message=f"Connection failed: {safe_url}",
                retryable=True,
                cause=exc,
                url=safe_url,
            ) from exc
        except httpx.TooManyRedirects as exc:
            raise TransportError(
                message=f"Too many redirects for: {safe_url}",
                retryable=False,
                cause=exc,
                url=safe_url,
            ) from exc
        except httpx.HTTPStatusError as exc:
            # httpx raises this only when raise_for_status() is called; we do not call it.
            # Guard here anyway for completeness.
            raise TransportError(
                message=f"Unexpected HTTP status error: {safe_url}",
                retryable=False,
                cause=exc,
                url=safe_url,
            ) from exc
        except Exception as exc:
            raise TransportError(
                message=f"Unexpected transport error: {safe_url}",
                retryable=False,
                cause=exc,
                url=safe_url,
            ) from exc

        elapsed_ms = (time.monotonic() - start) * 1000.0

        # --- Response size protection ---
        limit = self._config.response_limit.max_bytes
        # Use content-length header for early rejection if available
        content_length_header = raw.headers.get("content-length", "")
        if content_length_header:
            try:
                declared_length = int(content_length_header)
                if declared_length > limit:
                    raise ResponseTooLargeError(
                        url=safe_url,
                        limit_bytes=limit,
                        actual_bytes=declared_length,
                    )
            except ValueError:
                pass  # malformed content-length header; fall through to body check

        # Read body (httpx buffers automatically with async client)
        body = raw.content
        if len(body) > limit:
            raise ResponseTooLargeError(
                url=safe_url,
                limit_bytes=limit,
                actual_bytes=len(body),
            )

        # Normalize headers to lowercase keys for consistent access
        response_headers = {k.lower(): v for k, v in raw.headers.items()}

        status_class = classify_status(raw.status_code)
        retry_after = _parse_retry_after(response_headers)

        return FetchResponse(
            status_code=raw.status_code,
            status_class=status_class,
            body=body,
            headers=response_headers,
            elapsed_ms=elapsed_ms,
            url=str(raw.url),
            attempt_number=attempt_number,
            retry_after_seconds=retry_after,
        )
