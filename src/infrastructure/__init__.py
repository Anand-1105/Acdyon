"""Infrastructure primitives for the Acdyon ingestion subsystem.

This package contains reusable, source-independent building blocks:
- config:       Typed configuration for timeouts, retry, pacing, and size limits.
- http:         Async HTTP transport, response wrapper, and status classification.
- reliability:  Retry/backoff policy and source-aware rate limiter.

None of these modules know what a job is, what WWR is, or how RSS is structured.
"""
