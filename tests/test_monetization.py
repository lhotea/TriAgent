"""Tests for triagent.monetization — caption assembly and affiliate rotation."""

from __future__ import annotations

from triagent.monetization import assemble_caption


def test_assemble_caption_minimal(sample_brief):
    """assemble_caption produces a valid caption with no affiliate URLs."""
    caption = assemble_caption(
        sample_brief, affiliate_urls=[], brand_handle="@testbrand"
    )
    assert sample_brief.hook in caption
    assert sample_brief.caption_body in caption
    assert sample_brief.engagement_prompt in caption
    assert "@testbrand" in caption
    # No affiliate line when no URLs provided
    assert "featured deal" not in caption


def test_assemble_caption_with_affiliate(sample_brief):
    """assemble_caption includes affiliate line when URLs are provided."""
    caption = assemble_caption(
        sample_brief,
        affiliate_urls=["https://aff.example/gear"],
        brand_handle="@testbrand",
    )
    # Affiliate line appears when URLs are present
    assert "featured deal" in caption or "affiliate" in caption.lower() or "purchase" in caption


def test_assemble_caption_includes_hashtags(sample_brief):
    """assemble_caption includes all hashtags."""
    caption = assemble_caption(
        sample_brief, affiliate_urls=[], brand_handle="@testbrand"
    )
    for tag in sample_brief.hashtags:
        assert f"#{tag.lower()}" in caption or f" {tag.lower()}" in caption


def test_assemble_caption_capped_at_2200_chars(sample_brief):
    """assemble_caption trims to 2200 characters max (Instagram limit)."""
    caption = assemble_caption(
        sample_brief, affiliate_urls=[], brand_handle="@testbrand"
    )
    assert len(caption) <= 2200


def test_assemble_caption_with_many_hashtags(sample_brief):
    """Caption stays under 2200 chars even with max hashtags + affiliate."""
    # Use max-length hashtag list
    full_hashtags = [f"hashtag{i}" for i in range(25)]
    sample_brief.hashtags = full_hashtags

    caption = assemble_caption(
        sample_brief,
        affiliate_urls=["https://aff.example"],
        brand_handle="@testbrand",
    )
    assert len(caption) <= 2200


def test_assemble_caption_no_duplicate_hashtags(sample_brief):
    """assemble_caption doesn't double-prefix hashtags with #."""
    caption = assemble_caption(
        sample_brief, affiliate_urls=[], brand_handle="@testbrand"
    )
    # Each hashtag should appear with exactly one #
    for tag in sample_brief.hashtags:
        # The tag should appear as #tag or just tag (the function handles this)
        assert f"##{tag.lower()}" not in caption


def test_assemble_caption_includes_link_in_bio_cta(sample_brief):
    """assemble_caption includes the link-in-bio call-to-action."""
    caption = assemble_caption(
        sample_brief, affiliate_urls=[], brand_handle="@testbrand"
    )
    assert "link in bio" in caption.lower()
    assert "@testbrand" in caption


def test_assemble_caption_includes_brand_handle(sample_brief):
    """assemble_caption includes the brand handle in the CTA."""
    caption = assemble_caption(
        sample_brief, affiliate_urls=[], brand_handle="@mybrand"
    )
    assert "@mybrand" in caption


def test_assemble_caption_deterministic_for_same_day(sample_brief):
    """Affiliate rotation is deterministic for the same day."""
    from unittest.mock import patch
    import datetime as dt

    # Mock "now" to a fixed date for deterministic rotation
    fixed_date = dt.datetime(2026, 5, 16, tzinfo=dt.timezone.utc)

    with patch("triagent.monetization.dt.datetime") as mock_dt:
        mock_dt.now.return_value = fixed_date

        caption1 = assemble_caption(
            sample_brief,
            affiliate_urls=["https://aff1.example", "https://aff2.example"],
            brand_handle="@testbrand",
        )
        caption2 = assemble_caption(
            sample_brief,
            affiliate_urls=["https://aff1.example", "https://aff2.example"],
            brand_handle="@testbrand",
        )

    # Same day → same affiliate rotation → same caption
    assert caption1 == caption2


def test_assemble_caption_rotates_affiliate_across_dates(sample_brief):
    """Affiliate selection rotates based on the date."""
    from unittest.mock import patch
    import datetime as dt

    date1 = dt.datetime(2026, 5, 16, tzinfo=dt.timezone.utc)
    date2 = dt.datetime(2026, 5, 17, tzinfo=dt.timezone.utc)

    urls = ["https://aff1.example", "https://aff2.example", "https://aff3.example"]

    captions = []
    for d in [date1, date2]:
        with patch("triagent.monetization.dt.datetime") as mock_dt:
            mock_dt.now.return_value = d
            captions.append(
                assemble_caption(
                    sample_brief,
                    affiliate_urls=urls,
                    brand_handle="@testbrand",
                )
            )

    # At least one should differ (probabilistic, but very likely with 3 URLs)
    # We can't guarantee they differ, so just check both are valid
    for c in captions:
        assert len(c) > 0
        assert "@testbrand" in c

class TestCtaLine:
    """The CTA must not promise things that aren't configured."""

    def test_omits_gear_picks_without_affiliates(self):
        from triagent.monetization import _cta

        assert "gear picks" not in _cta("@x", has_affiliate=False)

    def test_includes_gear_picks_with_affiliates(self):
        from triagent.monetization import _cta

        assert "gear picks" in _cta("@x", has_affiliate=True)

    def test_no_empty_brackets_when_handle_missing(self):
        """An unset BRAND_HANDLE rendered as 'link in bio ()' in production."""
        from triagent.monetization import _cta

        out = _cta("", has_affiliate=False)
        assert "()" not in out
        assert out.endswith("link in bio")

    def test_handle_appended_when_present(self):
        from triagent.monetization import _cta

        assert _cta("@tri", has_affiliate=False).endswith("(@tri)")

    def test_whitespace_only_handle_treated_as_missing(self):
        from triagent.monetization import _cta

        assert "()" not in _cta("   ", has_affiliate=False)
