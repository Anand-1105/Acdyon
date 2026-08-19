"""HTTP response container and status classification.

This module is deliberately free of job-domain knowledge.
It understands HTTP — status codes, headers, byte payloads — and nothing else.

Design principles:
- FetchResponse is a plain dataclass carrying exactly what the transport obtained.
- HttpStatusClass is a semantic grouping used by the retry policy and callers.
- classify_status is a pure function with no side effects.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Optional


class HttpStatusClass(str, Enum):
    """Semantic classification of an HTTP response status code.

    Callers and the retry policy use this enum rather than raw integers.
    This avoids scattering magic number comparisons across the codebase.
    """

    SUCCESS = "success"                        # 2xx — usable response
    RATE_LIMITED = "rate_limited"              # 429 — back off and respect Retry-After
    TRANSIENT_SERVER_ERROR = "transient_server_error"  # 502, 503, 504 — may recover
    CLIENT_ERROR = "client_error"              # 4xx (non-429) — do not retry; fix config
    SERVER_ERROR = "server_error"              # 5xx (non-502/503/504) — usually not retryable
    REDIRECT = "redirect"                      # 3xx when redirects are not followed
    UNEXPECTED = "unexpected"                  # anything else


# Explicit sets of status codes for each class.
# Kept as frozensets so membership tests are O(1).
_TRANSIENT_SERVER_CODES: frozenset[int] = frozenset({502, 503, 504})
_RETRYABLE_CODES: frozenset[int] = frozenset({429, 502, 503, 504})


def classify_status(status_code: int) -> HttpStatusClass:
    """Map a raw HTTP integer status code to a semantic HttpStatusClass.

    Args:
        status_code: The integer HTTP status code (e.g. 200, 404, 503).

    Returns:
        The corresponding HttpStatusClass member.

    Examples:
        >>> classify_status(200)
        <HttpStatusClass.SUCCESS: 'success'>
        >>> classify_status(429)
        <HttpStatusClass.RATE_LIMITED: 'rate_limited'>
        >>> classify_status(503)
        <HttpStatusClass.TRANSIENT_SERVER_ERROR: 'transient_server_error'>
    """
    if 200 <= status_code < 300:
        return HttpStatusClass.SUCCESS
    if 300 <= status_code < 400:
        return HttpStatusClass.REDIRECT
    if status_code == 429:
        return HttpStatusClass.RATE_LIMITED
    if status_code in _TRANSIENT_SERVER_CODES:
        return HttpStatusClass.TRANSIENT_SERVER_ERROR
    if 400 <= status_code < 500:
        return HttpStatusClass.CLIENT_ERROR
    if 500 <= status_code < 600:
        return HttpStatusClass.SERVER_ERROR
    return HttpStatusClass.UNEXPECTED


def is_retryable_status(status_code: int) -> bool:
    """Return True if the HTTP status code indicates a potentially transient failure.

    Only 429 and a subset of 5xx codes are retryable.
    4xx (non-429) and other 5xx codes indicate permanent or non-transient problems.
    """
    return status_code in _RETRYABLE_CODES


@dataclass(frozen=True)
class FetchResponse:
    """Immutable container for a completed HTTP response.

    Produced by AsyncHttpTransport after a successful low-level round-trip.
    The transport caller decides how to parse or interpret the body.

    Attributes:
        status_code:    Integer HTTP status code.
        status_class:   Semantic classification of the status code.
        body:           Raw response body bytes. Bounded by ResponseLimitConfig.
        headers:        Response headers as a plain dict (lowercased keys).
        elapsed_ms:     Wall-clock time for the complete request in milliseconds.
        url:            Final URL after any redirects (or the requested URL).
        attempt_number: Which attempt produced this response (1-based).
        retry_after_seconds: Value parsed from the Retry-After header, if present.
    """

    status_code: int
    status_class: HttpStatusClass
    body: bytes
    headers: dict[str, str] = field(default_factory=dict)
    elapsed_ms: float = 0.0
    url: str = ""
    attempt_number: int = 1
    retry_after_seconds: Optional[float] = None

    @property
    def is_success(self) -> bool:
        """True when status_class is SUCCESS."""
        return self.status_class == HttpStatusClass.SUCCESS

    @property
    def text(self) -> str:
        """Decode body as UTF-8, replacing undecodable bytes rather than raising."""
        return self.body.decode("utf-8", errors="replace")

    @property
    def content_length(self) -> int:
        """Number of bytes in the response body."""
        return len(self.body)
