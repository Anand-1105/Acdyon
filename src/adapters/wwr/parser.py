"""WWR RSS Parser: converts raw RSS XML bytes into canonical domain records.

Responsibility:
- Parse RSS 2.0 XML safely using the stdlib defusedxml-compatible interface.
- Extract and normalize item fields from the WWR feed format.
- Produce a canonical JobRecord per valid item.
- Produce a structured IngestionError (scope=RECORD) per invalid item.
- Treat the full document as a feed-level failure only for XML/document errors.

This module knows about:
- WWR-specific XML element names (<region>, <type>, <skills>, <expires_at>).
- The WWR title convention "Company: Job Title".
- The WWR employment type vocabulary (Full-Time, Part-Time, Contract, etc.).
- RFC 2822 date format used by the WWR pubDate field.

This module does NOT know about:
- HTTP transport, retry policies, or rate limiting.
- Database writes or repository interfaces.
- FastAPI or HTTP response objects.
- Any other source (LinkedIn, RemoteOK, etc.).

Security:
- Uses xml.etree.ElementTree via defusedxml's monkey-patch substitute.
  Since Python 3.8+ the stdlib ET is NOT vulnerable to billion-laughs or
  quadratic-blowup attacks by default (no SYSTEM entity processing), but
  we still use safe parsing to be explicit about intent and forward-compatible.
- XML external entity expansion is NOT enabled.
- Response body size is bounded upstream by ResponseLimitConfig (10 MB default).
- HTML-encoded descriptions are decoded from entity form; no HTML execution occurs.

Live feed structure (verified 2026-08-18):
  Root:       <rss version="2.0" xmlns:dc="..." xmlns:media="...">
  Channel:    <channel>
                <title>...</title>
                <link>...</link>
                <description>...</description>
                <language>en-US</language>
                <ttl>60</ttl>
  Item:       <item>
                <title>Company: Job Title</title>
                <region>Anywhere in the World</region>
                <country></country>
                <state></state>
                <skills></skills>
                <category>All Other Remote</category>
                <type>Full-Time</type>
                <description>HTML-ENTITY-ENCODED content</description>
                <pubDate>Tue, 18 Aug 2026 07:30:41 +0000</pubDate>
                <expires_at>Thu, 17 Sep 2026 07:30:35 +0000</expires_at>
                <guid>https://weworkremotely.com/remote-jobs/...</guid>
                <link>https://weworkremotely.com/remote-jobs/...</link>
              </item>

Key observations:
- guid == link (both are stable WWR job URLs, suitable as identity seed)
- description is HTML-entity-encoded (not CDATA), e.g. &lt;p&gt;
- <type> provides a reliable employment-type signal
- <region> provides location (e.g. "Anywhere in the World", "USA Only")
- Title format is consistently "Company: Job Title" (colon-space separator)
- <country>/<state>/<skills> are often empty in the observed feed
"""

from __future__ import annotations

import html
import logging
import re
from datetime import datetime, timezone
from typing import List, Optional, Tuple
from xml.etree import ElementTree as ET

import defusedxml.ElementTree as defused_ET

from src.domain.enums import EmploymentType, IngestionErrorType, ErrorScope, SourceHealthStatus, SourceType
from src.domain.errors import IngestionError
from src.domain.identity import generate_canonical_id
from src.domain.job import JobRecord
from src.domain.source import SourceInfo

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

SOURCE_NAME = "weworkremotely"
SOURCE_ATTRIBUTION = (
    "Job listings provided by We Work Remotely (https://weworkremotely.com). "
    "Please see https://weworkremotely.com for the original postings."
)

# WWR title separator: "Company: Job Title"
# The regex expects one colon-space after the company name.
# Groups: (1) company, (2) job_title
_TITLE_SEPARATOR_RE = re.compile(r"^(.+?):\s+(.+)$")

# Mapping from WWR <type> values to canonical EmploymentType.
# Observed values in the live feed: "Full-Time", "Part-Time", "Contract".
_EMPLOYMENT_TYPE_MAP: dict[str, EmploymentType] = {
    "full-time": EmploymentType.FULL_TIME,
    "part-time": EmploymentType.PART_TIME,
    "contract": EmploymentType.CONTRACT,
    "freelance": EmploymentType.FREELANCE,
    "temporary": EmploymentType.TEMPORARY,
    "internship": EmploymentType.INTERNSHIP,
}

# RSS pubDate format (RFC 2822): "Tue, 18 Aug 2026 07:30:41 +0000"
_RFC2822_FORMAT = "%a, %d %b %Y %H:%M:%S %z"

# Maximum description length to store (domain ceiling: 100,000 chars).
# We apply a conservative truncation inside the parser to avoid hitting the
# Pydantic validation boundary with a confusing error.
_MAX_DESCRIPTION_CHARS = 50_000

# Minimum characters required in a description before we consider it usable.
# An empty or single-character description is not useful.
_MIN_DESCRIPTION_CHARS = 1


# ---------------------------------------------------------------------------
# ParseResult — result of parsing one <item>
# ---------------------------------------------------------------------------

class ParseResult:
    """Result of attempting to parse one RSS item.

    Either record is set (success) or error is set (failure).
    """

    __slots__ = ("record", "error")

    def __init__(
        self,
        record: Optional[JobRecord] = None,
        error: Optional[IngestionError] = None,
    ) -> None:
        self.record = record
        self.error = error

    @property
    def is_success(self) -> bool:
        return self.record is not None


# ---------------------------------------------------------------------------
# Feed-level parse result
# ---------------------------------------------------------------------------

class FeedParseResult:
    """Result of parsing an entire RSS feed document.

    Attributes:
        records:       Successfully normalized canonical records.
        errors:        Structured per-record or feed-level errors.
        raw_count:     Total <item> elements encountered.
        channel_title: The RSS channel <title> value.
        is_feed_error: True when the document itself could not be parsed.
    """

    def __init__(self) -> None:
        self.records: List[JobRecord] = []
        self.errors: List[IngestionError] = []
        self.raw_count: int = 0
        self.channel_title: str = ""
        self.is_feed_error: bool = False
        self.feed_error: Optional[IngestionError] = None


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def parse_feed(
    xml_bytes: bytes,
    retrieved_at: Optional[datetime] = None,
) -> FeedParseResult:
    """Parse a WWR RSS feed from raw bytes into canonical domain records.

    Args:
        xml_bytes:    Raw bytes of the RSS response body.
        retrieved_at: UTC timestamp of when the feed was fetched.
                      Defaults to now() if not provided.

    Returns:
        FeedParseResult with records, errors, counts, and channel metadata.

    This function never raises for per-item failures — those become IngestionErrors.
    It DOES return a FeedParseResult with is_feed_error=True for XML document failures.
    """
    result = FeedParseResult()
    if retrieved_at is None:
        retrieved_at = datetime.now(timezone.utc)

    # -----------------------------------------------------------------------
    # Step 1: Parse the XML document
    # -----------------------------------------------------------------------
    try:
        root = _safe_parse_xml(xml_bytes)
    except ET.ParseError as exc:
        result.is_feed_error = True
        result.feed_error = _make_feed_error(
            message=f"RSS XML document is malformed: {exc}",
            error_type=IngestionErrorType.MALFORMED_RESPONSE_ERROR,
        )
        return result
    except Exception as exc:
        result.is_feed_error = True
        result.feed_error = _make_feed_error(
            message=f"Unexpected error parsing RSS document: {exc}",
            error_type=IngestionErrorType.INTERNAL_ERROR,
        )
        return result

    # -----------------------------------------------------------------------
    # Step 2: Locate the RSS channel element
    # -----------------------------------------------------------------------
    channel = _find_channel(root)
    if channel is None:
        result.is_feed_error = True
        result.feed_error = _make_feed_error(
            message="RSS document has no <channel> element; not a valid RSS 2.0 feed.",
            error_type=IngestionErrorType.MALFORMED_RESPONSE_ERROR,
        )
        return result

    result.channel_title = _text(channel, "title") or ""

    # -----------------------------------------------------------------------
    # Step 3: Iterate over <item> elements
    # -----------------------------------------------------------------------
    items = channel.findall("item")
    result.raw_count = len(items)

    for idx, item_el in enumerate(items):
        parse_result = _parse_item(item_el, item_index=idx)
        if parse_result.is_success:
            assert parse_result.record is not None
            result.records.append(parse_result.record)
        else:
            assert parse_result.error is not None
            result.errors.append(parse_result.error)

    return result


def build_source_info(
    endpoint: str,
    retrieved_at: Optional[datetime] = None,
    channel_title: str = "",
    health_status: SourceHealthStatus = SourceHealthStatus.HEALTHY,
) -> SourceInfo:
    """Construct a SourceInfo for the WWR RSS adapter.

    Args:
        endpoint:      The feed URL that was actually fetched.
        retrieved_at:  UTC timestamp of the fetch.
        channel_title: The <title> element from the RSS <channel>.
        health_status: Current assessed health of the source.

    Returns:
        Fully constructed SourceInfo domain object.
    """
    return SourceInfo(
        source_name=SOURCE_NAME,
        source_type=SourceType.RSS,
        endpoint=endpoint,
        retrieval_timestamp=retrieved_at or datetime.now(timezone.utc),
        attribution=SOURCE_ATTRIBUTION,
        health_status=health_status,
        metadata={"channel_title": channel_title} if channel_title else {},
    )


# ---------------------------------------------------------------------------
# XML helpers
# ---------------------------------------------------------------------------

def _safe_parse_xml(xml_bytes: bytes) -> ET.Element:
    """Parse XML bytes safely using mandatory defusedxml entity-expansion protection."""
    return defused_ET.fromstring(xml_bytes)


def _find_channel(root: ET.Element) -> Optional[ET.Element]:
    """Locate the <channel> element regardless of whether root IS the channel."""
    if root.tag == "channel":
        return root
    return root.find("channel")


def _text(element: ET.Element, tag: str) -> Optional[str]:
    """Return stripped text content of a child element, or None if absent/empty."""
    child = element.find(tag)
    if child is None:
        return None
    text = (child.text or "").strip()
    return text if text else None


# ---------------------------------------------------------------------------
# Item parsing
# ---------------------------------------------------------------------------

def _parse_item(item_el: ET.Element, item_index: int) -> ParseResult:
    """Parse a single RSS <item> into a canonical JobRecord.

    Never raises — all failures are wrapped in IngestionError (scope=RECORD).
    """
    try:
        return _parse_item_inner(item_el, item_index)
    except Exception as exc:
        logger.debug("Unexpected error parsing item %d: %s", item_index, exc)
        return ParseResult(
            error=_make_record_error(
                message=f"Unexpected error parsing item at index {item_index}: {exc}",
                error_type=IngestionErrorType.INVALID_RECORD_ERROR,
            )
        )


def _parse_item_inner(item_el: ET.Element, item_index: int) -> ParseResult:
    """Inner parser — may raise; caller catches and wraps in IngestionError."""
    # -----------------------------------------------------------------------
    # Extract raw fields
    # -----------------------------------------------------------------------
    raw_title = _text(item_el, "title")
    raw_link = _text(item_el, "link")
    raw_guid = _text(item_el, "guid")
    raw_pub_date = _text(item_el, "pubDate")
    raw_description = _text(item_el, "description")
    raw_category = _text(item_el, "category")
    raw_type = _text(item_el, "type")
    raw_region = _text(item_el, "region")

    # -----------------------------------------------------------------------
    # Validate required fields (title, URL)
    # -----------------------------------------------------------------------
    if not raw_title:
        return ParseResult(
            error=_make_record_error(
                message=f"Item at index {item_index} has no <title> element.",
                error_type=IngestionErrorType.INVALID_RECORD_ERROR,
                record_id=raw_guid or raw_link,
            )
        )

    # Primary URL: prefer <link>, fall back to <guid> (they are identical in WWR)
    source_url = raw_link or raw_guid
    if not source_url:
        return ParseResult(
            error=_make_record_error(
                message=f"Item '{raw_title}' has no usable URL (<link> or <guid>).",
                error_type=IngestionErrorType.INVALID_RECORD_ERROR,
                record_id=raw_title,
            )
        )

    # -----------------------------------------------------------------------
    # Parse description (required by JobRecord)
    # Description in WWR feed is HTML-entity-encoded (not CDATA).
    # We decode entities to recover the HTML, then store as-is.
    # We do NOT strip HTML tags here — downstream renderers can do that.
    # We DO truncate at the domain-safe ceiling.
    # -----------------------------------------------------------------------
    if raw_description:
        description = _decode_html_entities(raw_description)[:_MAX_DESCRIPTION_CHARS]
    else:
        # A missing description is technically valid per RSS spec; use a placeholder
        # so that the required field passes Pydantic validation.
        description = f"[No description provided] See full posting at {source_url}"

    # -----------------------------------------------------------------------
    # Split WWR title into company and job title
    # Format: "CompanyName: Job Title"
    # -----------------------------------------------------------------------
    company, title = _split_wwr_title(raw_title)

    if not company:
        # Cannot reliably determine company from title — reject the record
        return ParseResult(
            error=_make_record_error(
                message=(
                    f"Could not extract company from WWR title {raw_title!r}. "
                    "Expected format 'Company: Job Title'."
                ),
                error_type=IngestionErrorType.INVALID_RECORD_ERROR,
                record_id=raw_guid or source_url,
            )
        )

    # -----------------------------------------------------------------------
    # Parse publication timestamp
    # -----------------------------------------------------------------------
    published_at, date_error = _parse_pub_date(raw_pub_date)
    if published_at is None:
        # A missing/malformed date is a record-level error — we cannot invent
        # a timestamp and silently substitute now().
        return ParseResult(
            error=_make_record_error(
                message=(
                    f"Item '{raw_title}' has an unparseable <pubDate>: "
                    f"{raw_pub_date!r}. {date_error}"
                ),
                error_type=IngestionErrorType.INVALID_RECORD_ERROR,
                record_id=raw_guid or source_url,
            )
        )

    # -----------------------------------------------------------------------
    # Map employment type
    # -----------------------------------------------------------------------
    employment_type = _map_employment_type(raw_type)

    # -----------------------------------------------------------------------
    # Location: prefer <region>, fall back to "Remote"
    # -----------------------------------------------------------------------
    location = raw_region or "Remote"

    # -----------------------------------------------------------------------
    # Generate canonical ID
    # Precedence: guid (= stable WWR URL) > link > composite
    # -----------------------------------------------------------------------
    canonical_id = generate_canonical_id(
        source_name=SOURCE_NAME,
        source_id=raw_guid,  # Use guid as source_id (identity tier 1)
        source_url=source_url,
        company=company,
        title=title,
    )

    # -----------------------------------------------------------------------
    # Collect WWR-specific metadata (non-canonical fields preserved without loss)
    # -----------------------------------------------------------------------
    metadata: dict = {}
    if raw_category:
        metadata["wwr_category"] = raw_category
    if raw_type:
        metadata["wwr_type"] = raw_type
    region_val = _text(item_el, "region")
    if region_val:
        metadata["wwr_region"] = region_val

    # -----------------------------------------------------------------------
    # Build canonical JobRecord
    # -----------------------------------------------------------------------
    try:
        record = JobRecord(
            canonical_id=canonical_id,
            source_name=SOURCE_NAME,
            source_id=raw_guid,
            source_url=source_url,
            title=title,
            company=company,
            location=location,
            description=description,
            employment_type=employment_type,
            published_at=published_at,
            metadata=metadata,
        )
    except Exception as exc:
        return ParseResult(
            error=_make_record_error(
                message=f"JobRecord validation failed for '{raw_title}': {exc}",
                error_type=IngestionErrorType.INVALID_RECORD_ERROR,
                record_id=raw_guid or source_url,
            )
        )

    return ParseResult(record=record)


# ---------------------------------------------------------------------------
# Field-level helpers
# ---------------------------------------------------------------------------

def _split_wwr_title(raw_title: str) -> Tuple[str, str]:
    """Split a WWR title string into (company, job_title).

    Expected format: "CompanyName: Job Title"

    Returns:
        (company, job_title) where either may be empty on parse failure.

    Examples:
        "Vonage: ServiceNow Alliance Executive"  -> ("Vonage", "ServiceNow Alliance Executive")
        "ACME Corp: Senior Backend Engineer"      -> ("ACME Corp", "Senior Backend Engineer")
        "No separator here"                       -> ("", "No separator here")
    """
    if not raw_title:
        return "", ""

    title = raw_title.strip()
    match = _TITLE_SEPARATOR_RE.match(title)
    if match:
        company = match.group(1).strip()
        job_title = match.group(2).strip()
        if company and job_title:
            return company, job_title

    # No separator found — cannot determine company reliably
    return "", title


def _parse_pub_date(raw: Optional[str]) -> Tuple[Optional[datetime], str]:
    """Parse an RFC 2822 pubDate string into a UTC-aware datetime.

    Args:
        raw: The raw pubDate string, e.g. "Tue, 18 Aug 2026 07:30:41 +0000".

    Returns:
        (datetime, "") on success, (None, error_reason) on failure.
    """
    if not raw:
        return None, "pubDate element is absent or empty"

    stripped = raw.strip()

    # Primary: RFC 2822 with explicit timezone offset
    try:
        dt = datetime.strptime(stripped, _RFC2822_FORMAT)
        # datetime.strptime with %z parses the tz offset; normalize to UTC.
        return dt.astimezone(timezone.utc), ""
    except ValueError:
        pass

    # Secondary: try email.utils which handles more RFC 2822 variants
    try:
        import email.utils
        ts = email.utils.parsedate_to_datetime(stripped)
        return ts.astimezone(timezone.utc), ""
    except Exception:
        pass

    return None, f"Could not parse date string: {stripped!r}"


def _map_employment_type(raw_type: Optional[str]) -> EmploymentType:
    """Map a WWR <type> value to a canonical EmploymentType.

    Args:
        raw_type: Raw string from the <type> element, e.g. "Full-Time".

    Returns:
        The matching EmploymentType or UNKNOWN if unrecognized/absent.
    """
    if not raw_type:
        return EmploymentType.UNKNOWN
    key = raw_type.strip().lower()
    return _EMPLOYMENT_TYPE_MAP.get(key, EmploymentType.UNKNOWN)


def _decode_html_entities(text: str) -> str:
    """Decode HTML-entity-encoded content.

    WWR descriptions are stored as entity-encoded HTML in the feed, e.g.:
        &lt;p&gt;Hello&lt;/p&gt;  →  <p>Hello</p>

    This recovers the original HTML markup. The caller can then choose to
    further strip tags for plain-text storage or keep the HTML for rich display.

    We use stdlib html.unescape which handles all standard entities
    and numeric character references without executing any code.
    """
    return html.unescape(text)


# ---------------------------------------------------------------------------
# Error constructors
# ---------------------------------------------------------------------------

def _make_record_error(
    message: str,
    error_type: IngestionErrorType = IngestionErrorType.INVALID_RECORD_ERROR,
    record_id: Optional[str] = None,
) -> IngestionError:
    return IngestionError(
        error_type=error_type,
        scope=ErrorScope.RECORD,
        message=message[:1024],
        retryable=False,
        record_id=record_id[:256] if record_id else None,
        timestamp=datetime.now(timezone.utc),
    )


def _make_feed_error(
    message: str,
    error_type: IngestionErrorType = IngestionErrorType.MALFORMED_RESPONSE_ERROR,
) -> IngestionError:
    return IngestionError(
        error_type=error_type,
        scope=ErrorScope.RUN,
        message=message[:1024],
        retryable=False,
        details={"source_name": SOURCE_NAME},
        timestamp=datetime.now(timezone.utc),
    )
