"""Unit tests for Canonical Identity and Deduplication Rules."""

import pytest

from src.domain.identity import canonicalize_url, generate_canonical_id, normalize_string


class TestIdentityGeneration:
    """Test suite for deterministic canonical ID generation and URL canonicalization."""

    def test_normalize_string(self):
        assert normalize_string("  Hello   World  \n\t") == "Hello World"
        assert normalize_string("") == ""
        assert normalize_string(None) == ""

    def test_canonicalize_url_strips_tracking_params(self):
        url = "https://weworkremotely.com/jobs/12345?utm_source=twitter&utm_medium=social&ref=feed&page=1"
        clean = canonicalize_url(url)
        assert clean == "https://weworkremotely.com/jobs/12345?page=1"

    def test_canonicalize_url_lowercases_host_and_scheme(self):
        url = "HTTPS://WeWorkRemotely.COM/Jobs/Backend-Dev/"
        clean = canonicalize_url(url)
        assert clean == "https://weworkremotely.com/Jobs/Backend-Dev"

    def test_generate_canonical_id_precedence_source_id(self):
        # 1. Source ID has highest precedence
        id1 = generate_canonical_id(
            source_name="weworkremotely",
            source_id="guid-101",
            source_url="https://weworkremotely.com/jobs/101",
            company="Acme Corp",
            title="Senior Engineer",
        )
        id2 = generate_canonical_id(
            source_name="weworkremotely",
            source_id="guid-101",
            source_url="https://weworkremotely.com/jobs/different-url",
            company="Different Corp",
            title="Different Title",
        )
        assert id1 == id2
        assert id1.startswith("weworkremotely_")

    def test_generate_canonical_id_precedence_source_url(self):
        # 2. Source URL is used when source_id is absent
        id1 = generate_canonical_id(
            source_name="weworkremotely",
            source_url="https://weworkremotely.com/jobs/101?utm_campaign=spring",
            company="Acme Corp",
            title="Senior Engineer",
        )
        id2 = generate_canonical_id(
            source_name="weworkremotely",
            source_url="https://weworkremotely.com/jobs/101",
            company="Changed Name",
            title="Changed Title",
        )
        assert id1 == id2
        assert id1.startswith("weworkremotely_")

    def test_generate_canonical_id_fallback_composite(self):
        # 3. Composite of company and title when no id or url provided
        id1 = generate_canonical_id(
            source_name="weworkremotely",
            company="  Acme   Corp  ",
            title="Senior  Engineer ",
        )
        id2 = generate_canonical_id(
            source_name="weworkremotely",
            company="acme corp",
            title="senior engineer",
        )
        assert id1 == id2

    def test_generate_canonical_id_missing_source_name_raises(self):
        with pytest.raises(ValueError) as exc:
            generate_canonical_id(source_name="", source_id="123")
        assert "source_name is required" in str(exc.value)

    def test_generate_canonical_id_insufficient_data_raises(self):
        with pytest.raises(ValueError) as exc:
            generate_canonical_id(source_name="wwr")
        assert "must provide source_id, source_url, or both company and title" in str(exc.value)
