"""Tests for triagent.config — Settings parsing and validation."""

from __future__ import annotations

import os

import pytest

from triagent.config import Settings


class TestSettingsFromEnv:
    """Tests for Settings.from_env()."""

    def test_anthropic_key_required_at_build_not_at_load(self, env_no_env_file):
        """from_env() must load without it; only build paths demand it.

        Changed deliberately: the token-refresh workflow calls from_env() but
        never touches Claude, and a global requirement broke it.
        """
        s = Settings.from_env()
        assert s.anthropic_api_key is None
        with pytest.raises(RuntimeError, match="ANTHROPIC_API_KEY"):
            s.require_build_config()

    def test_accepts_anthropic_key(self, env_no_env_file, monkeypatch):
        """from_env() succeeds with only ANTHROPIC_API_KEY."""
        monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test-123")
        s = Settings.from_env()
        assert s.anthropic_api_key == "sk-test-123"

    def test_ig_credentials_optional(self, env_no_env_file, monkeypatch):
        """IG_USER_ID and IG_ACCESS_TOKEN default to None."""
        monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test")
        s = Settings.from_env()
        assert s.ig_user_id is None
        assert s.ig_access_token is None

    def test_stores_ig_credentials_when_present(self, env_no_env_file, monkeypatch):
        monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test")
        monkeypatch.setenv("IG_USER_ID", "12345")
        monkeypatch.setenv("IG_ACCESS_TOKEN", "tok_abc")
        s = Settings.from_env()
        assert s.ig_user_id == "12345"
        assert s.ig_access_token == "tok_abc"

    def test_trims_public_image_base_url(self, env_no_env_file, monkeypatch):
        monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test")
        monkeypatch.setenv("PUBLIC_IMAGE_BASE_URL", "https://example.com/")
        s = Settings.from_env()
        assert s.public_image_base_url == "https://example.com"

    def test_public_image_base_url_none_when_missing(self, env_no_env_file, monkeypatch):
        monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test")
        s = Settings.from_env()
        assert s.public_image_base_url is None

    def test_default_brand_handle(self, env_no_env_file, monkeypatch):
        monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test")
        s = Settings.from_env()
        assert s.brand_handle == "@tripulsedaily"

    def test_custom_brand_handle(self, env_no_env_file, monkeypatch):
        monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test")
        monkeypatch.setenv("BRAND_HANDLE", "@mybrand")
        s = Settings.from_env()
        assert s.brand_handle == "@mybrand"

    def test_default_brand_name(self, env_no_env_file, monkeypatch):
        monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test")
        s = Settings.from_env()
        assert s.brand_name == "TriPulse Daily"

    def test_custom_brand_name(self, env_no_env_file, monkeypatch):
        monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test")
        monkeypatch.setenv("BRAND_NAME", "IronInsights")
        s = Settings.from_env()
        assert s.brand_name == "IronInsights"

    def test_parses_affiliate_urls(self, env_no_env_file, monkeypatch):
        monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test")
        monkeypatch.setenv(
            "AFFILIATE_URLS",
            "https://aff.example/gear,https://aff.example/shoes",
        )
        s = Settings.from_env()
        assert len(s.affiliate_urls) == 2
        assert "https://aff.example/gear" in s.affiliate_urls
        assert "https://aff.example/shoes" in s.affiliate_urls

    def test_affiliate_urls_empty_when_missing(self, env_no_env_file, monkeypatch):
        monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test")
        s = Settings.from_env()
        assert s.affiliate_urls == []

    def test_affiliate_urls_skips_empty_entries(self, env_no_env_file, monkeypatch):
        monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test")
        monkeypatch.setenv("AFFILIATE_URLS", "https://aff.example,,  ,https://aff.example/2")
        s = Settings.from_env()
        assert len(s.affiliate_urls) == 2

    def test_default_feeds(self, env_no_env_file, monkeypatch):
        """Defaults must be usable, not any particular URL.

        This previously pinned triathlete.com as feeds[0]. Naming a specific
        host makes the test fail whenever the list is corrected, which is the
        opposite of what it should protect: the list is *expected* to change as
        feeds rot, and nine of the fourteen entries in production turned out to
        be dead. What must hold is that there are some and they are fetchable.
        """
        monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test")
        s = Settings.from_env()
        assert len(s.feeds) > 0
        assert all(u.startswith("https://") for u in s.feeds)
        assert len(set(s.feeds)) == len(s.feeds), "no duplicate default feeds"

    def test_default_model(self, env_no_env_file, monkeypatch):
        monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test")
        s = Settings.from_env()
        assert s.model == "claude-opus-4-7"


class TestRequirePublishConfig:
    """Tests for Settings.require_publish_config()."""

    def test_succeeds_with_all_publish_fields(self, settings):
        # No exception should be raised
        settings.require_publish_config()

    def test_raises_without_ig_user_id(self, env_no_env_file, monkeypatch):
        monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test")
        monkeypatch.setenv("PUBLIC_IMAGE_BASE_URL", "https://example.com")
        s = Settings.from_env()
        with pytest.raises(RuntimeError, match="IG_USER_ID"):
            s.require_publish_config()

    def test_raises_without_ig_access_token(self, env_no_env_file, monkeypatch):
        monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test")
        monkeypatch.setenv("IG_USER_ID", "123")
        monkeypatch.setenv("PUBLIC_IMAGE_BASE_URL", "https://example.com")
        s = Settings.from_env()
        with pytest.raises(RuntimeError, match="IG_ACCESS_TOKEN"):
            s.require_publish_config()

    def test_raises_without_public_image_base_url(self, env_no_env_file, monkeypatch):
        monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test")
        monkeypatch.setenv("IG_USER_ID", "123")
        monkeypatch.setenv("IG_ACCESS_TOKEN", "tok_123")
        s = Settings.from_env()
        with pytest.raises(RuntimeError, match="PUBLIC_IMAGE_BASE_URL"):
            s.require_publish_config()

    def test_raises_all_missing_fields(self, env_no_env_file, monkeypatch):
        monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test")
        s = Settings.from_env()
        with pytest.raises(RuntimeError) as exc_info:
            s.require_publish_config()
        err = str(exc_info.value)
        assert "IG_USER_ID" in err
        assert "IG_ACCESS_TOKEN" in err
        assert "PUBLIC_IMAGE_BASE_URL" in err

    def test_build_mode_works_without_publish_fields(self, env_no_env_file, monkeypatch):
        """Build mode should work without IG credentials or image URL."""
        monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test")
        s = Settings.from_env()
        # Should not raise — build mode doesn't need publish config
        # (require_publish_config is only called in publish_from_build and run)
        assert s.ig_user_id is None  # i.e. not available for publish


class TestSettingsFrozen:
    """Tests confirming Settings is immutable."""

    def test_settings_is_frozen(self):
        s = Settings(
            anthropic_api_key="key",
            ig_user_id="123",
            ig_access_token="tok",
            public_image_base_url="https://example.com",
            brand_handle="@test",
            brand_name="Test",
        )
        with pytest.raises(Exception):  # type: ignore[misc]  # FrozenInstanceError
            s.ig_user_id = "456"  # pyright: ignore[reportAttributeAccessIssue]

class TestFeedUrlNormalization:
    """Feed lists get pasted without schemes; requests rejects those outright."""

    def test_adds_https_to_bare_domain(self):
        from triagent.config import normalize_feed_url

        assert normalize_feed_url("dcrainmaker.com/feed") == "https://dcrainmaker.com/feed"

    def test_preserves_existing_https(self):
        from triagent.config import normalize_feed_url

        url = "https://www.triathlete.com/feed/"
        assert normalize_feed_url(url) == url

    def test_preserves_existing_http(self):
        from triagent.config import normalize_feed_url

        assert normalize_feed_url("http://example.com/feed") == "http://example.com/feed"

    def test_strips_whitespace_and_stray_commas(self):
        from triagent.config import normalize_feed_url

        assert normalize_feed_url("  example.com/feed , ") == "https://example.com/feed"

    def test_empty_string_stays_empty(self):
        from triagent.config import normalize_feed_url

        assert normalize_feed_url("   ") == ""

    def test_parse_list_splits_on_commas(self):
        from triagent.config import parse_feed_list

        out = parse_feed_list("a.com/feed,b.com/feed")
        assert out == ["https://a.com/feed", "https://b.com/feed"]

    def test_parse_list_splits_on_newlines(self):
        """Pasting a multi-line list from a feed directory must work too."""
        from triagent.config import parse_feed_list

        out = parse_feed_list("a.com/feed\nb.com/feed\n")
        assert out == ["https://a.com/feed", "https://b.com/feed"]

    def test_parse_list_drops_empties(self):
        from triagent.config import parse_feed_list

        assert parse_feed_list("a.com/feed,,  ,b.com/feed") == [
            "https://a.com/feed",
            "https://b.com/feed",
        ]

    def test_parse_list_empty_input(self):
        from triagent.config import parse_feed_list

        assert parse_feed_list("") == []

    def test_settings_normalizes_feeds_env(self, monkeypatch):
        from triagent.config import Settings

        monkeypatch.setenv("ANTHROPIC_API_KEY", "k")
        monkeypatch.setenv("FEEDS", "dcrainmaker.com/feed, 220triathlon.com/feed/atom")
        s = Settings.from_env()
        assert s.feeds == [
            "https://dcrainmaker.com/feed",
            "https://220triathlon.com/feed/atom",
        ]


class TestPerModeConfigRequirements:
    """Config must be validated where it's used, not globally.

    The scheduled token refresh failed with "Missing required env var:
    ANTHROPIC_API_KEY" — a key that path never uses. Settings.from_env() must
    load for every mode; each mode asserts only what it needs.
    """

    def test_from_env_works_without_anthropic_key(self, monkeypatch):
        from triagent.config import Settings

        monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
        monkeypatch.setenv("IG_ACCESS_TOKEN", "tok")
        s = Settings.from_env()
        assert s.anthropic_api_key is None
        assert s.ig_access_token == "tok"

    def test_require_build_config_raises_without_key(self, monkeypatch):
        from triagent.config import Settings

        monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
        with pytest.raises(RuntimeError, match="ANTHROPIC_API_KEY"):
            Settings.from_env().require_build_config()

    def test_require_build_config_passes_with_key(self, monkeypatch):
        from triagent.config import Settings

        monkeypatch.setenv("ANTHROPIC_API_KEY", "k")
        Settings.from_env().require_build_config()  # must not raise

    def test_publish_config_independent_of_anthropic_key(self, monkeypatch):
        """Publishing needs no Claude key; refreshing needs neither."""
        from triagent.config import Settings

        monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
        monkeypatch.setenv("IG_USER_ID", "1")
        monkeypatch.setenv("IG_ACCESS_TOKEN", "t")
        monkeypatch.setenv("PUBLIC_IMAGE_BASE_URL", "https://e.com")
        Settings.from_env().require_publish_config()  # must not raise
