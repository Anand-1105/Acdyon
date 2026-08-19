"""Typed configuration dataclasses for ingestion infrastructure primitives.

All configuration objects use dataclasses with explicit field validation.
They have no dependency on FastAPI, Supabase, domain models, or source adapters.

Design principles:
- Every numeric threshold is named and documented (no magic numbers in transport code).
- Defaults are safe for a polite public-feed ingestion use case.
- Source-specific limits live in the adapter/caller, not in these classes.
- Impossible values are rejected at construction time.
"""

from dataclasses import dataclass, field
from typing import Optional


@dataclass(frozen=True)
class TimeoutConfig:
    """Explicit per-phase timeout policy for outbound HTTP requests.

    Attributes:
        connect_seconds:   Maximum seconds allowed to establish a TCP connection.
        read_seconds:      Maximum seconds allowed to receive the full response body
                           after the connection is open.
        pool_seconds:      Maximum seconds to wait for a connection slot from the pool.
                           Uses connect_seconds when not specified.

    Why three separate timeouts?
    Network problems manifest differently:
    - DNS/TCP failures → connection_timeout
    - Slow server writing → read_timeout
    - Pool exhaustion under load → pool_timeout
    Collapsing them into one value hides the actual failure mode.
    """

    connect_seconds: float = 5.0
    read_seconds: float = 10.0
    pool_seconds: Optional[float] = None  # defaults to connect_seconds when None

    def __post_init__(self) -> None:
        if self.connect_seconds <= 0:
            raise ValueError(f"connect_seconds must be > 0, got {self.connect_seconds}")
        if self.read_seconds <= 0:
            raise ValueError(f"read_seconds must be > 0, got {self.read_seconds}")
        if self.pool_seconds is not None and self.pool_seconds <= 0:
            raise ValueError(f"pool_seconds must be > 0 when set, got {self.pool_seconds}")

    @property
    def effective_pool_seconds(self) -> float:
        """Pool wait timeout; falls back to connect_seconds when unset."""
        return self.pool_seconds if self.pool_seconds is not None else self.connect_seconds


@dataclass(frozen=True)
class ResponseLimitConfig:
    """Safety limits applied to raw HTTP responses before any parsing.

    Attributes:
        max_bytes:  Maximum acceptable response body size in bytes.
                    Responses larger than this are rejected without parsing.
                    Default: 10 MB — generous for any RSS feed, firm against abuse.
    """

    max_bytes: int = 10 * 1024 * 1024  # 10 MB

    def __post_init__(self) -> None:
        if self.max_bytes <= 0:
            raise ValueError(f"max_bytes must be > 0, got {self.max_bytes}")
        if self.max_bytes > 100 * 1024 * 1024:
            raise ValueError(
                f"max_bytes exceeds safety ceiling of 100 MB, got {self.max_bytes}. "
                "Increase the ceiling explicitly if required."
            )


@dataclass(frozen=True)
class RetryConfig:
    """Policy governing retry attempts on transient failures.

    Attributes:
        max_attempts:       Total attempts, including the first (non-retry) attempt.
                            A value of 1 disables all retries.
        base_backoff_seconds:  Base delay for exponential backoff.
        max_backoff_seconds:   Ceiling for computed exponential backoff before jitter.
        max_retry_after_seconds: Safety ceiling for Retry-After header values.
                                 A server cannot force a sleep longer than this.
        jitter_factor:      Multiplier applied to a random [0, 1) sample, then added
                            to the computed backoff. Prevents synchronized retry storms.
                            Range: [0.0, 1.0]. 0.0 disables jitter.
    """

    max_attempts: int = 3
    base_backoff_seconds: float = 1.0
    max_backoff_seconds: float = 30.0
    max_retry_after_seconds: float = 60.0
    jitter_factor: float = 0.5

    def __post_init__(self) -> None:
        if self.max_attempts < 1:
            raise ValueError(f"max_attempts must be >= 1, got {self.max_attempts}")
        if self.base_backoff_seconds <= 0:
            raise ValueError(f"base_backoff_seconds must be > 0, got {self.base_backoff_seconds}")
        if self.max_backoff_seconds < self.base_backoff_seconds:
            raise ValueError(
                f"max_backoff_seconds ({self.max_backoff_seconds}) must be >= "
                f"base_backoff_seconds ({self.base_backoff_seconds})"
            )
        if self.max_retry_after_seconds <= 0:
            raise ValueError(
                f"max_retry_after_seconds must be > 0, got {self.max_retry_after_seconds}"
            )
        if not (0.0 <= self.jitter_factor <= 1.0):
            raise ValueError(
                f"jitter_factor must be in [0.0, 1.0], got {self.jitter_factor}"
            )

    @property
    def max_retries(self) -> int:
        """Number of retry attempts after the initial failure (max_attempts - 1)."""
        return self.max_attempts - 1


@dataclass(frozen=True)
class RateLimitConfig:
    """Pacing policy for outbound requests to a single source domain.

    This governs normal steady-state pacing only.
    Retry backoff is a separate concern handled by RetryConfig.

    Attributes:
        min_interval_seconds:  Minimum elapsed time between consecutive requests
                               to the same source. Enforced per logical source name,
                               not globally across all sources.
        max_concurrent:        Maximum number of simultaneous in-flight requests
                               allowed for this source. Prevents accidental burst
                               from multiple async callers.

    Note: Actual WWR-specific values are set by the WWR adapter, not here.
    This class defines the structure; the caller supplies the numbers.
    """

    min_interval_seconds: float = 1.0
    max_concurrent: int = 1

    def __post_init__(self) -> None:
        if self.min_interval_seconds < 0:
            raise ValueError(
                f"min_interval_seconds must be >= 0, got {self.min_interval_seconds}"
            )
        if self.max_concurrent < 1:
            raise ValueError(f"max_concurrent must be >= 1, got {self.max_concurrent}")


@dataclass(frozen=True)
class HttpTransportConfig:
    """Aggregate configuration for the HTTP transport layer.

    Composes timeout, response-limit, and user-agent settings into
    a single object suitable for passing to the transport constructor.

    Attributes:
        timeout:          Per-phase timeout policy.
        response_limit:   Response body size protection.
        user_agent:       Transparent User-Agent header identifying this client.
        follow_redirects: Whether the transport follows HTTP redirects automatically.
        max_redirects:    Safety cap on redirect hops.
    """

    timeout: TimeoutConfig = field(default_factory=TimeoutConfig)
    response_limit: ResponseLimitConfig = field(default_factory=ResponseLimitConfig)
    user_agent: str = "Acdyon-JobIngest/1.0 (Assessment Evaluation)"
    follow_redirects: bool = True
    max_redirects: int = 5

    def __post_init__(self) -> None:
        if not self.user_agent.strip():
            raise ValueError("user_agent must not be blank")
        if self.max_redirects < 0:
            raise ValueError(f"max_redirects must be >= 0, got {self.max_redirects}")
