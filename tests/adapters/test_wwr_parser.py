"""Unit tests for WWRRSSParser (src/adapters/wwr/parser.py).

All tests are synchronous — no network calls.
Static XML fixtures provide deterministic, repeatable test conditions.

Coverage:
- Valid feed: multiple complete items parse into JobRecord instances.
- Identity: canonical_id is deterministic and stable across repeated parses.
- Title splitting: "Company: Title" convention correctly separated.
- Title fallback: no colon separator → rejected with INVALID_RECORD_ERROR.
- Empty title → rejected with INVALID_RECORD_ERROR.
- Missing URL (no link, no guid) → rejected.
- Employment type mapping: Full-Time, Part-Time, Contract → canonical values.
- Unknown employment type → EmploymentType.UNKNOWN.
- Location: <region> field used; missing region → "Remote".
- pubDate parsing: RFC 2822 format correctly parsed and normalized to UTC.
- pubDate malformed → rejected (not silently substituted).
- pubDate missing → rejected.
- Description: HTML-entity-encoded content is decoded.
- Description: missing description → placeholder text used (not rejected).
- Feed-level failure: malformed XML → is_feed_error=True, single feed error.
- Empty feed: valid XML with zero items → raw_count=0, zero records, zero errors.
- Per-item isolation: one broken item does not prevent valid siblings.
- Duplicate guids: same guid produces same canonical_id (idempotency).
- Metadata: wwr_category, wwr_type stored in record.metadata.
- build_source_info: returns correct SourceInfo with attribution.
"""

from __future__ import annotations

import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

import pytest

from src.adapters.wwr.parser import (
    SOURCE_ATTRIBUTION,
    SOURCE_NAME,
    FeedParseResult,
    build_source_info,
    parse_feed,
)
from src.domain.enums import (
    EmploymentType,
    IngestionErrorType,
    ErrorScope,
    SourceHealthStatus,
    SourceType,
)
from src.domain.job import JobRecord

# ---------------------------------------------------------------------------
# Fixture helpers
# ---------------------------------------------------------------------------

FIXTURE_DIR = Path(__file__).parent.parent / "fixtures"


def _load(filename: str) -> bytes:
    return (FIXTURE_DIR / filename).read_bytes()


def _valid_xml() -> bytes:
    return _load("wwr_valid.xml")


def _edge_cases_xml() -> bytes:
    return _load("wwr_edge_cases.xml")


def _malformed_xml() -> bytes:
    return _load("wwr_malformed.xml")


def _empty_xml() -> bytes:
    return _load("wwr_empty.xml")


def _make_minimal_item_xml(
    title: str = "TestCo: Test Job",
    link: str = "https://weworkremotely.com/remote-jobs/testco-test-job",
    guid: Optional[str] = "https://weworkremotely.com/remote-jobs/testco-test-job",
    pub_date: str = "Mon, 17 Aug 2026 09:00:00 +0000",
    description: Optional[str] = "&lt;p&gt;Description text.&lt;/p&gt;",
    employment_type: Optional[str] = "Full-Time",
    category: Optional[str] = "Programming",
    region: Optional[str] = None,
) -> bytes:
    """Build a minimal but complete RSS feed XML with a single item."""
    link_el = f"<link>{link}</link>" if link else ""
    guid_el = f"<guid>{guid}</guid>" if guid else ""
    desc_el = f"<description>{description}</description>" if description is not None else ""
    type_el = f"<type>{employment_type}</type>" if employment_type else ""
    cat_el = f"<category>{category}</category>" if category else ""
    region_el = f"<region>{region}</region>" if region else ""
    return f"""<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0">
  <channel>
    <title>WWR Test</title>
    <link>https://weworkremotely.com/remote-jobs.rss</link>
    <description>Test</description>
    <item>
      <title>{title}</title>
      {guid_el}
      {link_el}
      {desc_el}
      <pubDate>{pub_date}</pubDate>
      {type_el}
      {cat_el}
      {region_el}
    </item>
  </channel>
</rss>""".encode()


# ---------------------------------------------------------------------------
# Valid feed tests
# ---------------------------------------------------------------------------

class TestValidFeed:
    def test_parse_returns_correct_record_count(self):
        result = parse_feed(_valid_xml())
        assert not result.is_feed_error
        assert result.raw_count == 3
        assert len(result.records) == 3
        assert len(result.errors) == 0

    def test_channel_title_extracted(self):
        result = parse_feed(_valid_xml())
        assert "We Work Remotely" in result.channel_title

    def test_first_record_has_correct_fields(self):
        result = parse_feed(_valid_xml())
        job: JobRecord = result.records[0]
        assert job.source_name == SOURCE_NAME
        assert job.company == "Acme Corp"
        assert job.title == "Senior Backend Engineer"
        assert job.source_url == "https://weworkremotely.com/remote-jobs/acme-corp-senior-backend-engineer"
        assert job.employment_type == EmploymentType.FULL_TIME
        assert job.location == "Anywhere in the World"

    def test_second_record_contract_type(self):
        result = parse_feed(_valid_xml())
        job: JobRecord = result.records[1]
        assert job.company == "Beta Inc"
        assert job.title == "Product Designer"
        assert job.employment_type == EmploymentType.CONTRACT
        assert job.location == "USA Only"

    def test_third_record_full_time(self):
        result = parse_feed(_valid_xml())
        job: JobRecord = result.records[2]
        assert job.company == "Gamma LLC"
        assert job.employment_type == EmploymentType.FULL_TIME
        assert job.location == "Europe Only"

    def test_published_at_is_utc_aware(self):
        result = parse_feed(_valid_xml())
        for job in result.records:
            assert job.published_at.tzinfo is not None
            assert job.published_at.utcoffset().total_seconds() == 0  # type: ignore[union-attr]

    def test_first_published_at_value(self):
        result = parse_feed(_valid_xml())
        job = result.records[0]
        expected = datetime(2026, 8, 17, 9, 0, 0, tzinfo=timezone.utc)
        assert job.published_at == expected

    def test_description_html_decoded(self):
        result = parse_feed(_valid_xml())
        job = result.records[0]
        # After HTML entity decoding, the raw HTML tags should be present
        assert "<p>" in job.description
        assert "<strong>" in job.description

    def test_wwr_category_in_metadata(self):
        result = parse_feed(_valid_xml())
        job = result.records[0]
        assert job.metadata.get("wwr_category") == "Programming"

    def test_wwr_type_in_metadata(self):
        result = parse_feed(_valid_xml())
        job = result.records[0]
        assert job.metadata.get("wwr_type") == "Full-Time"

    def test_source_id_is_guid(self):
        result = parse_feed(_valid_xml())
        job = result.records[0]
        assert job.source_id == "https://weworkremotely.com/remote-jobs/acme-corp-senior-backend-engineer"

    def test_canonical_id_format(self):
        result = parse_feed(_valid_xml())
        job = result.records[0]
        assert job.canonical_id.startswith(f"{SOURCE_NAME}_")
        assert len(job.canonical_id) > len(SOURCE_NAME) + 1


# ---------------------------------------------------------------------------
# Identity / determinism
# ---------------------------------------------------------------------------

class TestIdentityDeterminism:
    def test_canonical_id_is_stable_across_repeated_parses(self):
        result1 = parse_feed(_valid_xml())
        result2 = parse_feed(_valid_xml())
        ids1 = [r.canonical_id for r in result1.records]
        ids2 = [r.canonical_id for r in result2.records]
        assert ids1 == ids2

    def test_duplicate_guids_produce_same_canonical_id(self):
        """Same guid in two separate parse runs → same canonical_id."""
        xml = _make_minimal_item_xml(
            guid="https://weworkremotely.com/remote-jobs/testco-test-job"
        )
        r1 = parse_feed(xml)
        r2 = parse_feed(xml)
        assert r1.records[0].canonical_id == r2.records[0].canonical_id

    def test_different_guids_produce_different_canonical_ids(self):
        xml_a = _make_minimal_item_xml(
            guid="https://weworkremotely.com/remote-jobs/job-a",
            link="https://weworkremotely.com/remote-jobs/job-a",
        )
        xml_b = _make_minimal_item_xml(
            guid="https://weworkremotely.com/remote-jobs/job-b",
            link="https://weworkremotely.com/remote-jobs/job-b",
        )
        r1 = parse_feed(xml_a)
        r2 = parse_feed(xml_b)
        assert r1.records[0].canonical_id != r2.records[0].canonical_id


# ---------------------------------------------------------------------------
# Title splitting
# ---------------------------------------------------------------------------

class TestTitleSplitting:
    def test_standard_company_colon_title(self):
        xml = _make_minimal_item_xml(title="Acme Corp: Senior Engineer")
        result = parse_feed(xml)
        assert len(result.records) == 1
        assert result.records[0].company == "Acme Corp"
        assert result.records[0].title == "Senior Engineer"

    def test_company_with_colon_in_name(self):
        """Company names containing colons should be handled by first colon split."""
        xml = _make_minimal_item_xml(title="Example: Corp: Backend Engineer")
        result = parse_feed(xml)
        assert len(result.records) == 1
        assert result.records[0].company == "Example"
        assert result.records[0].title == "Corp: Backend Engineer"

    def test_no_separator_rejects_item(self):
        xml = _make_minimal_item_xml(title="Senior Software Engineer at SomeCompany")
        result = parse_feed(xml)
        assert len(result.records) == 0
        assert len(result.errors) == 1
        assert result.errors[0].error_type == IngestionErrorType.INVALID_RECORD_ERROR
        assert result.errors[0].scope == ErrorScope.RECORD

    def test_empty_title_rejects_item(self):
        xml = _make_minimal_item_xml(title="")
        result = parse_feed(xml)
        assert len(result.records) == 0
        assert len(result.errors) == 1


# ---------------------------------------------------------------------------
# Missing required fields
# ---------------------------------------------------------------------------

class TestMissingRequiredFields:
    def test_missing_link_and_guid_rejects_item(self):
        xml = _make_minimal_item_xml(link="", guid=None)
        result = parse_feed(xml)
        assert len(result.records) == 0
        assert len(result.errors) == 1
        assert result.errors[0].error_type == IngestionErrorType.INVALID_RECORD_ERROR

    def test_missing_guid_uses_link_as_identity_seed(self):
        xml = _make_minimal_item_xml(
            guid=None,
            link="https://weworkremotely.com/remote-jobs/no-guid-job",
        )
        result = parse_feed(xml)
        assert len(result.records) == 1
        job = result.records[0]
        assert job.source_url == "https://weworkremotely.com/remote-jobs/no-guid-job"
        assert job.source_id is None

    def test_malformed_pubdate_rejects_item(self):
        xml = _make_minimal_item_xml(pub_date="NOT A DATE")
        result = parse_feed(xml)
        assert len(result.records) == 0
        assert len(result.errors) == 1
        assert result.errors[0].error_type == IngestionErrorType.INVALID_RECORD_ERROR

    def test_missing_pubdate_rejects_item(self):
        xml = _make_minimal_item_xml(pub_date="")
        result = parse_feed(xml)
        assert len(result.records) == 0
        assert len(result.errors) == 1

    def test_missing_description_yields_placeholder(self):
        """A missing description is allowed — parser inserts a safe placeholder."""
        xml = _make_minimal_item_xml(description=None)
        result = parse_feed(xml)
        assert len(result.records) == 1
        assert "[No description provided]" in result.records[0].description


# ---------------------------------------------------------------------------
# Employment type
# ---------------------------------------------------------------------------

class TestEmploymentTypeMapping:
    @pytest.mark.parametrize("raw,expected", [
        ("Full-Time", EmploymentType.FULL_TIME),
        ("full-time", EmploymentType.FULL_TIME),
        ("Part-Time", EmploymentType.PART_TIME),
        ("Contract", EmploymentType.CONTRACT),
        ("Freelance", EmploymentType.FREELANCE),
        ("Internship", EmploymentType.INTERNSHIP),
        ("Temporary", EmploymentType.TEMPORARY),
        ("Unknown Type XYZ", EmploymentType.UNKNOWN),
        (None, EmploymentType.UNKNOWN),
    ])
    def test_employment_type_mapping(self, raw, expected):
        xml = _make_minimal_item_xml(employment_type=raw)
        result = parse_feed(xml)
        assert len(result.records) == 1
        assert result.records[0].employment_type == expected


# ---------------------------------------------------------------------------
# Location
# ---------------------------------------------------------------------------

class TestLocation:
    def test_region_used_as_location(self):
        xml = _make_minimal_item_xml(region="Europe Only")
        result = parse_feed(xml)
        assert result.records[0].location == "Europe Only"

    def test_missing_region_defaults_to_remote(self):
        xml = _make_minimal_item_xml(region=None)
        result = parse_feed(xml)
        assert result.records[0].location == "Remote"


# ---------------------------------------------------------------------------
# Description HTML entity handling
# ---------------------------------------------------------------------------

class TestDescriptionHandling:
    def test_html_entities_decoded_to_html(self):
        xml = _make_minimal_item_xml(
            description="&lt;p&gt;&lt;strong&gt;Bold&lt;/strong&gt; text.&lt;/p&gt;"
        )
        result = parse_feed(xml)
        assert result.records[0].description == "<p><strong>Bold</strong> text.</p>"

    def test_amp_entity_decoded(self):
        xml = _make_minimal_item_xml(description="Salary: $100k &amp; benefits")
        result = parse_feed(xml)
        assert "& benefits" in result.records[0].description

    def test_description_stored_as_html(self):
        xml = _make_minimal_item_xml(
            description="&lt;h2&gt;About the Role&lt;/h2&gt;&lt;ul&gt;&lt;li&gt;Item&lt;/li&gt;&lt;/ul&gt;"
        )
        result = parse_feed(xml)
        desc = result.records[0].description
        assert "<h2>About the Role</h2>" in desc
        assert "<li>Item</li>" in desc


# ---------------------------------------------------------------------------
# Timestamp handling
# ---------------------------------------------------------------------------

class TestTimestampHandling:
    def test_utc_offset_normalized(self):
        xml = _make_minimal_item_xml(pub_date="Tue, 18 Aug 2026 07:30:41 +0000")
        result = parse_feed(xml)
        assert result.records[0].published_at == datetime(2026, 8, 18, 7, 30, 41, tzinfo=timezone.utc)

    def test_non_utc_offset_converted_to_utc(self):
        # +0530 (IST) = UTC - 5.5 hours
        xml = _make_minimal_item_xml(pub_date="Mon, 17 Aug 2026 14:30:00 +0530")
        result = parse_feed(xml)
        job = result.records[0]
        # 14:30 IST = 09:00 UTC
        assert job.published_at == datetime(2026, 8, 17, 9, 0, 0, tzinfo=timezone.utc)

    def test_truly_malformed_date_rejected(self):
        xml = _make_minimal_item_xml(pub_date="2026/08/18 07:30")
        result = parse_feed(xml)
        # This format is not RFC 2822; should be rejected
        assert len(result.records) == 0
        assert len(result.errors) == 1


# ---------------------------------------------------------------------------
# Feed-level failures
# ---------------------------------------------------------------------------

class TestFeedLevelFailures:
    def test_malformed_xml_returns_feed_error(self):
        result = parse_feed(_malformed_xml())
        assert result.is_feed_error is True
        assert result.feed_error is not None
        assert result.feed_error.scope == ErrorScope.RUN
        assert result.feed_error.error_type == IngestionErrorType.MALFORMED_RESPONSE_ERROR
        assert len(result.records) == 0

    def test_malformed_xml_has_no_records(self):
        result = parse_feed(_malformed_xml())
        assert result.records == []

    def test_no_channel_element(self):
        """A root element that is not an RSS structure → feed error."""
        xml = b"<?xml version='1.0'?><notRSS><item/></notRSS>"
        result = parse_feed(xml)
        assert result.is_feed_error is True
        assert result.feed_error.error_type == IngestionErrorType.MALFORMED_RESPONSE_ERROR


# ---------------------------------------------------------------------------
# Empty feed
# ---------------------------------------------------------------------------

class TestEmptyFeed:
    def test_empty_feed_zero_records_zero_errors(self):
        result = parse_feed(_empty_xml())
        assert not result.is_feed_error
        assert result.raw_count == 0
        assert len(result.records) == 0
        assert len(result.errors) == 0

    def test_empty_feed_channel_title_extracted(self):
        result = parse_feed(_empty_xml())
        assert "We Work Remotely" in result.channel_title


# ---------------------------------------------------------------------------
# Per-item isolation (mixed valid + invalid)
# ---------------------------------------------------------------------------

class TestPerItemIsolation:
    def test_valid_sibling_survives_broken_items(self):
        """Edge cases feed has 7 items: 4 invalid, 1 minimal-valid, 2 debatable.
        The valid sibling (ValidCompany: Valid Job Title) must survive."""
        result = parse_feed(_edge_cases_xml())
        assert not result.is_feed_error
        # Exactly 1 valid record (ValidCompany item)
        # Plus 1 minimal-field item (MissingFields Co - has no description, should get placeholder)
        # Plus 1 no-guid item (NoGuid Company - uses link for identity)
        valid_companies = {r.company for r in result.records}
        assert "ValidCompany" in valid_companies

    def test_errors_generated_for_broken_items(self):
        result = parse_feed(_edge_cases_xml())
        assert len(result.errors) > 0
        # Every error must be RECORD-scoped
        for err in result.errors:
            assert err.scope == ErrorScope.RECORD

    def test_raw_count_matches_item_elements(self):
        result = parse_feed(_edge_cases_xml())
        assert result.raw_count == 7  # 7 <item> elements in edge_cases.xml

    def test_total_raw_count_equals_records_plus_errors(self):
        result = parse_feed(_edge_cases_xml())
        assert result.raw_count == len(result.records) + len(result.errors)


# ---------------------------------------------------------------------------
# Source info
# ---------------------------------------------------------------------------

class TestBuildSourceInfo:
    def test_source_info_fields(self):
        ts = datetime(2026, 8, 18, 12, 0, 0, tzinfo=timezone.utc)
        info = build_source_info(
            endpoint="https://weworkremotely.com/remote-jobs.rss",
            retrieved_at=ts,
            channel_title="WWR Test Feed",
            health_status=SourceHealthStatus.HEALTHY,
        )
        assert info.source_name == SOURCE_NAME
        assert info.source_type == SourceType.RSS
        assert info.endpoint == "https://weworkremotely.com/remote-jobs.rss"
        assert info.retrieval_timestamp == ts
        assert info.attribution == SOURCE_ATTRIBUTION
        assert info.health_status == SourceHealthStatus.HEALTHY
        assert info.metadata.get("channel_title") == "WWR Test Feed"

    def test_source_info_without_channel_title(self):
        info = build_source_info(
            endpoint="https://weworkremotely.com/remote-jobs.rss",
            channel_title="",
        )
        assert info.metadata == {}

    def test_attribution_mentions_weworkremotely(self):
        info = build_source_info(endpoint="https://weworkremotely.com/remote-jobs.rss")
        assert "weworkremotely.com" in info.attribution.lower()
