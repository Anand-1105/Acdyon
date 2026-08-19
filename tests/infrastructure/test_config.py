"""Unit tests for infrastructure configuration dataclasses.

Tests cover:
- Default values are safe and sensible.
- Validation rejects impossible or dangerous values.
- Computed properties return expected results.
"""

import pytest

from src.infrastructure.config import (
    HttpTransportConfig,
    RateLimitConfig,
    ResponseLimitConfig,
    RetryConfig,
    TimeoutConfig,
)


class TestTimeoutConfig:
    def test_defaults_are_safe(self):
        cfg = TimeoutConfig()
        assert cfg.connect_seconds == 5.0
        assert cfg.read_seconds == 10.0
        assert cfg.pool_seconds is None
        assert cfg.effective_pool_seconds == 5.0

    def test_explicit_pool_seconds(self):
        cfg = TimeoutConfig(connect_seconds=3.0, read_seconds=8.0, pool_seconds=2.0)
        assert cfg.effective_pool_seconds == 2.0

    def test_zero_connect_raises(self):
        with pytest.raises(ValueError, match="connect_seconds must be > 0"):
            TimeoutConfig(connect_seconds=0.0)

    def test_negative_read_raises(self):
        with pytest.raises(ValueError, match="read_seconds must be > 0"):
            TimeoutConfig(read_seconds=-1.0)

    def test_zero_pool_seconds_raises(self):
        with pytest.raises(ValueError, match="pool_seconds must be > 0"):
            TimeoutConfig(pool_seconds=0.0)


class TestResponseLimitConfig:
    def test_default_is_10mb(self):
        cfg = ResponseLimitConfig()
        assert cfg.max_bytes == 10 * 1024 * 1024

    def test_zero_bytes_raises(self):
        with pytest.raises(ValueError, match="max_bytes must be > 0"):
            ResponseLimitConfig(max_bytes=0)

    def test_over_ceiling_raises(self):
        with pytest.raises(ValueError, match="exceeds safety ceiling"):
            ResponseLimitConfig(max_bytes=101 * 1024 * 1024)

    def test_custom_valid_limit(self):
        cfg = ResponseLimitConfig(max_bytes=1024)
        assert cfg.max_bytes == 1024


class TestRetryConfig:
    def test_defaults(self):
        cfg = RetryConfig()
        assert cfg.max_attempts == 3
        assert cfg.base_backoff_seconds == 1.0
        assert cfg.max_backoff_seconds == 30.0
        assert cfg.jitter_factor == 0.5
        assert cfg.max_retries == 2

    def test_max_attempts_1_means_no_retries(self):
        cfg = RetryConfig(max_attempts=1)
        assert cfg.max_retries == 0

    def test_invalid_max_attempts(self):
        with pytest.raises(ValueError):
            RetryConfig(max_attempts=0)

    def test_max_backoff_below_base_raises(self):
        with pytest.raises(ValueError, match="max_backoff_seconds"):
            RetryConfig(base_backoff_seconds=5.0, max_backoff_seconds=2.0)

    def test_invalid_jitter_over_1(self):
        with pytest.raises(ValueError, match="jitter_factor"):
            RetryConfig(jitter_factor=1.5)

    def test_invalid_jitter_negative(self):
        with pytest.raises(ValueError, match="jitter_factor"):
            RetryConfig(jitter_factor=-0.1)


class TestRateLimitConfig:
    def test_defaults(self):
        cfg = RateLimitConfig()
        assert cfg.min_interval_seconds == 1.0
        assert cfg.max_concurrent == 1

    def test_zero_interval_allowed(self):
        # Zero interval = no pacing; still valid.
        cfg = RateLimitConfig(min_interval_seconds=0.0)
        assert cfg.min_interval_seconds == 0.0

    def test_negative_interval_raises(self):
        with pytest.raises(ValueError):
            RateLimitConfig(min_interval_seconds=-1.0)

    def test_zero_concurrent_raises(self):
        with pytest.raises(ValueError):
            RateLimitConfig(max_concurrent=0)


class TestHttpTransportConfig:
    def test_defaults(self):
        cfg = HttpTransportConfig()
        assert "Acdyon" in cfg.user_agent
        assert cfg.follow_redirects is True
        assert cfg.max_redirects == 5
        assert isinstance(cfg.timeout, TimeoutConfig)
        assert isinstance(cfg.response_limit, ResponseLimitConfig)

    def test_blank_user_agent_raises(self):
        with pytest.raises(ValueError, match="user_agent"):
            HttpTransportConfig(user_agent="   ")

    def test_negative_max_redirects_raises(self):
        with pytest.raises(ValueError, match="max_redirects"):
            HttpTransportConfig(max_redirects=-1)
