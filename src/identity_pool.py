"""Scaffold compatibility layer: identity pool & headers configuration."""
from src.infrastructure.http.client import _safe_url_for_logging
from src.domain.identity import generate_canonical_id
