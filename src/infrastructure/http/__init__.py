"""HTTP sub-package: transport client, response wrapper, and status classification.

Public surface:
- FetchResponse       Raw response container (status, headers, body bytes, elapsed).
- HttpStatusClass     Enum for categorized HTTP outcomes.
- classify_status     Maps an integer HTTP status to an HttpStatusClass.
- AsyncHttpTransport  Context-managed async HTTP client for source adapters.
"""

from src.infrastructure.http.response import FetchResponse, HttpStatusClass, classify_status
from src.infrastructure.http.client import AsyncHttpTransport

__all__ = [
    "FetchResponse",
    "HttpStatusClass",
    "classify_status",
    "AsyncHttpTransport",
]
