"""Canonical Identity Generation and Deduplication Rules.

This module defines deterministic identity calculation for job postings.
The identity rules follow a strict precedence hierarchy:
1. Source-provided identifier (e.g. RSS GUID, unique API record ID) if available.
2. Canonicalized source URL (normalized, lowercased host, tracking query params stripped).
3. Deterministic fallback composite key (source_name + normalized_company + normalized_title).

This guarantees that re-ingesting the exact same posting yields an identical canonical ID.
"""

import hashlib
import re
from typing import Optional
from urllib.parse import parse_qsl, urlencode, urlparse, urlunparse

# Common tracking parameters to strip when canonicalizing source URLs
TRACKING_PARAMS = {
    "utm_source",
    "utm_medium",
    "utm_campaign",
    "utm_term",
    "utm_content",
    "ref",
    "source",
    "fbclid",
    "gclid",
}


def normalize_string(val: Optional[str]) -> str:
    """Normalize a text string by trimming whitespace and collapsing multiple spaces."""
    if not val:
        return ""
    return re.sub(r"\s+", " ", val).strip()


def canonicalize_url(url: Optional[str]) -> str:
    """Normalize a URL by lowercasing scheme/host, sorting query params, and stripping tracking parameters."""
    if not url:
        return ""
    parsed = urlparse(url.strip())
    scheme = parsed.scheme.lower()
    netloc = parsed.netloc.lower()
    path = parsed.path.rstrip("/")

    # Filter out tracking query params and sort remaining params for determinism
    query_params = parse_qsl(parsed.query, keep_blank_values=True)
    clean_params = sorted([(k, v) for k, v in query_params if k.lower() not in TRACKING_PARAMS])
    query = urlencode(clean_params)

    return urlunparse((scheme, netloc, path, parsed.params, query, ""))


def generate_canonical_id(
    source_name: str,
    source_id: Optional[str] = None,
    source_url: Optional[str] = None,
    company: Optional[str] = None,
    title: Optional[str] = None,
) -> str:
    """Generate a deterministic canonical identifier for a job posting.

    Precedence:
    1. source_name + source_id
    2. source_name + canonicalized source_url
    3. source_name + normalized company + normalized title
    """
    clean_source = normalize_string(source_name).lower()
    if not clean_source:
        raise ValueError("source_name is required to generate a canonical ID")

    clean_source_id = normalize_string(source_id)
    if clean_source_id:
        seed = f"{clean_source}:id:{clean_source_id}"
    else:
        clean_url = canonicalize_url(source_url)
        if clean_url:
            seed = f"{clean_source}:url:{clean_url}"
        else:
            clean_company = normalize_string(company).lower()
            clean_title = normalize_string(title).lower()
            if not clean_company or not clean_title:
                raise ValueError(
                    "Cannot generate canonical ID: must provide source_id, source_url, or both company and title"
                )
            seed = f"{clean_source}:composite:{clean_company}:{clean_title}"

    digest = hashlib.sha256(seed.encode("utf-8")).hexdigest()
    return f"{clean_source}_{digest[:16]}"
