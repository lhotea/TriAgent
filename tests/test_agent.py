"""Tests for triagent.agent — build, publish, and run pipeline functions."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from triagent.agent import build, publish_from_build, run, _image_url


@pytest.fixture()
def mock_settings():
    """A mutable mock Settings object for agent tests."""
    s = MagicMock()
    s.anthropic_api_key = "test-key"
    s.ig_user_id = "123"
    s.ig_access_token = "tok_123"
    s.public_image_base_url = "https://example.com"
    s.brand_handle = "@testbrand"
    s.brand_name = "TestBrand"
    s.affiliate_urls = []
    s.feeds = ["http://test/feed"]
    s.model = "claude-opus-4-7"
    s.max_headlines = 6
    # image_path is a MagicMock so we can patch .with_name / .name / .exists
    s.image_path = MagicMock()
    s.image_path.name = "daily.png"
    s.require_publish_config = MagicMock()
    return s


class TestBuild:
    """Tests for the build() function."""

    def test_build_raises_on_no_news(self, mock_settings):
        """build() raises RuntimeError when no news items are found."""
        with (
            patch("triagent.agent.fetch_recent_widening", return_value=[]),
            pytest.raises(RuntimeError, match="no triathlon news found"),
        ):
            build(mock_settings)

    def test_build_calls_all_steps(self, mock_settings, sample_news_items, sample_brief):
        """build() calls fetch → summarize → render → assemble_caption."""
        mock_caption_path = MagicMock()
        mock_caption_path.write_text = MagicMock()
        mock_settings.image_path.with_name.return_value = mock_caption_path

        with (
            patch("triagent.agent.fetch_recent_widening", return_value=sample_news_items),
            patch("triagent.agent.Summarizer") as mock_summ_cls,
            patch("triagent.agent.render_card") as mock_render,
            patch("triagent.agent.assemble_caption", return_value="Test caption") as mock_caption,
        ):
            mock_summ = MagicMock()
            mock_summ.build_brief.return_value = sample_brief
            mock_summ_cls.return_value = mock_summ

            result = build(mock_settings)

        assert result.caption == "Test caption"
        assert result.media_id is None
        assert result.brief is sample_brief
        mock_summ.build_brief.assert_called_once()
        mock_render.assert_called_once()
        mock_caption.assert_called_once()

    def test_build_limits_items_for_summarizer(self, mock_settings, sample_brief):
        """build() passes at most max_headlines*2 items to the summarizer."""
        mock_settings.max_headlines = 3  # max(6, 12) = 12
        many_items = [
            type("NewsItem", (), {
                "title": f"Story {i}",
                "summary": "Summary text",
                "url": f"https://example.com/{i}",
                "source": "Test",
                "published": None,
                "age_hours": lambda now=None: 1.0,
            })()
            for i in range(20)
        ]

        mock_caption_path = MagicMock()
        mock_caption_path.write_text = MagicMock()
        mock_settings.image_path.with_name.return_value = mock_caption_path

        with (
            patch("triagent.agent.fetch_recent_widening", return_value=many_items),
            patch("triagent.agent.Summarizer") as mock_summ_cls,
            patch("triagent.agent.render_card"),
            patch("triagent.agent.assemble_caption", return_value="Caption"),
        ):
            mock_summ_cls.return_value.build_brief.return_value = sample_brief
            build(mock_settings)

        # Should pass at most 12 items (max(3*2, 12)), not all 20
        call_items = mock_summ_cls.return_value.build_brief.call_args[0][0]
        assert len(call_items) <= 12


class TestPublishFromBuild:
    """Tests for publish_from_build()."""

    def test_publish_requires_prebuilt_caption(self, mock_settings):
        """publish_from_build() raises if caption.txt doesn't exist."""
        mock_caption_path = MagicMock()
        mock_caption_path.exists.return_value = False
        mock_settings.image_path.with_name.return_value = mock_caption_path

        with pytest.raises(RuntimeError, match="no prebuilt caption"):
            publish_from_build(mock_settings)

    def test_publish_requires_rendered_card(self, mock_settings):
        """publish_from_build() raises if daily.png doesn't exist."""
        mock_caption_path = MagicMock()
        mock_caption_path.exists.return_value = True
        mock_caption_path.read_text.return_value = "Test caption"
        mock_settings.image_path.with_name.return_value = mock_caption_path

        with patch.object(mock_settings.image_path, "exists", return_value=False):
            with pytest.raises(RuntimeError, match="no rendered card"):
                publish_from_build(mock_settings)

    def test_publish_sends_to_instagram(self, mock_settings):
        """publish_from_build() calls InstagramPublisher.publish()."""
        mock_caption_path = MagicMock()
        mock_caption_path.exists.return_value = True
        mock_caption_path.read_text.return_value = "Test caption"
        mock_settings.image_path.with_name.return_value = mock_caption_path

        mock_publisher = MagicMock()
        mock_publisher.publish.return_value = "media_123"

        with (
            patch.object(mock_settings.image_path, "exists", return_value=True),
            patch("triagent.agent.InstagramPublisher", return_value=mock_publisher),
        ):
            result = publish_from_build(mock_settings)

        assert result.media_id == "media_123"
        mock_publisher.publish.assert_called_once()
        mock_publisher.wait_for_image.assert_called_once()


class TestRun:
    """Tests for the run() function (build + publish in one step)."""

    def test_run_dry_run_skips_publish(self, mock_settings, sample_news_items, sample_brief):
        """run(dry_run=True) builds but doesn't publish to Instagram."""
        mock_caption_path = MagicMock()
        mock_caption_path.write_text = MagicMock()
        mock_settings.image_path.with_name.return_value = mock_caption_path

        with (
            patch("triagent.agent.fetch_recent_widening", return_value=sample_news_items),
            patch("triagent.agent.Summarizer") as mock_summ_cls,
            patch("triagent.agent.render_card"),
            patch("triagent.agent.assemble_caption", return_value="Caption"),
        ):
            mock_summ_cls.return_value.build_brief.return_value = sample_brief
            result = run(mock_settings, dry_run=True)

        assert result.media_id is None
        assert result.brief is sample_brief
        assert result.caption == "Caption"

    def test_run_full_mode_publishes(self, mock_settings, sample_news_items, sample_brief):
        """run(dry_run=False) builds and publishes."""
        mock_publisher = MagicMock()
        mock_publisher.publish.return_value = "media_456"

        mock_caption_path = MagicMock()
        mock_caption_path.write_text = MagicMock()
        mock_settings.image_path.with_name.return_value = mock_caption_path

        with (
            patch("triagent.agent.fetch_recent_widening", return_value=sample_news_items),
            patch("triagent.agent.Summarizer") as mock_summ_cls,
            patch("triagent.agent.render_card"),
            patch("triagent.agent.assemble_caption", return_value="Caption"),
            patch("triagent.agent.InstagramPublisher", return_value=mock_publisher),
        ):
            mock_summ_cls.return_value.build_brief.return_value = sample_brief
            result = run(mock_settings)

        assert result.media_id == "media_456"
        mock_publisher.publish.assert_called_once()
        mock_publisher.wait_for_image.assert_called_once()


class TestImageURL:
    """Tests for the _image_url helper."""

    def test_image_url_with_base_url(self, mock_settings):
        mock_settings.public_image_base_url = "https://cdn.example.com"
        url = _image_url(mock_settings)
        assert url.startswith("https://cdn.example.com/daily-")
        assert url.endswith(".png")

    def test_image_url_without_base_url(self, mock_settings):
        mock_settings.public_image_base_url = None
        url = _image_url(mock_settings)
        assert "<unset>" in url
        assert url.endswith(".png")


class TestDatedAssetNames:
    """Published assets must not reuse a filename across days.

    A stable URL lets the CDN in front of GitHub Pages serve yesterday's bytes
    to Meta, which posts the wrong picture — the exact symptom reported after
    run #123.
    """

    def test_dated_name_embeds_the_date(self):
        from triagent.agent import dated_name

        assert dated_name("daily", ".png", "2026-08-09") == "daily-2026-08-09.png"

    def test_dated_name_differs_across_days(self):
        from triagent.agent import dated_name

        a = dated_name("daily", ".png", "2026-08-09")
        b = dated_name("daily", ".png", "2026-08-10")
        assert a != b

    def test_image_url_is_dated(self, mock_settings):
        from triagent.agent import dated_name

        mock_settings.public_image_base_url = "https://cdn.example.com"
        assert _image_url(mock_settings).endswith(dated_name("daily", ".png"))


class _Item:
    def __init__(self, title, url, source="Src"):
        self.title, self.url, self.source = title, url, source


class _H:
    def __init__(self, title, source_url):
        self.title, self.source_url = title, source_url
        self.source = "Model"


class _Brief:
    def __init__(self, headlines):
        self.headlines = headlines


class TestStoriesForPage:
    """The lead story must appear in its own list.

    It previously could not: the page listed the most recent items, but the
    model ranks by engagement, so the headline's story could fall outside the
    recency window entirely.
    """

    def test_lead_story_is_first(self):
        from triagent.agent import _stories_for_page

        items = [_Item(f"t{i}", f"https://e.com/{i}") for i in range(10)]
        brief = _Brief([_H("She quit lifting for T100", "https://e.com/9")])
        out = _stories_for_page(brief, items)
        assert out[0].url == "https://e.com/9"

    def test_uses_model_rewritten_title(self):
        from triagent.agent import _stories_for_page

        items = [_Item("Original wire headline", "https://e.com/1")]
        brief = _Brief([_H("Punchier rewrite", "https://e.com/1")])
        assert _stories_for_page(brief, items)[0].title == "Punchier rewrite"

    def test_keeps_real_publication_name(self):
        from triagent.agent import _stories_for_page

        items = [_Item("t", "https://e.com/1", source="Triathlete")]
        brief = _Brief([_H("rewrite", "https://e.com/1")])
        assert _stories_for_page(brief, items)[0].source == "Triathlete"

    def test_drops_hallucinated_url(self):
        """A fabricated link is worse than a missing one."""
        from triagent.agent import _stories_for_page

        items = [_Item("t", "https://e.com/1")]
        brief = _Brief([_H("rewrite", "https://made-up.example/x")])
        out = _stories_for_page(brief, items)
        assert all(s.url != "https://made-up.example/x" for s in out)

    def test_backfills_when_headlines_drop_out(self):
        from triagent.agent import _stories_for_page

        items = [_Item(f"t{i}", f"https://e.com/{i}") for i in range(4)]
        brief = _Brief([_H("bad", "https://nope.example/x")])
        assert len(_stories_for_page(brief, items)) == 4

    def test_no_duplicates(self):
        from triagent.agent import _stories_for_page

        items = [_Item("t1", "https://e.com/1"), _Item("t2", "https://e.com/2")]
        brief = _Brief([_H("a", "https://e.com/1"), _H("b", "https://e.com/1")])
        urls = [s.url for s in _stories_for_page(brief, items)]
        assert len(urls) == len(set(urls))

    def test_preserves_model_ranking_order(self):
        from triagent.agent import _stories_for_page

        items = [_Item(f"t{i}", f"https://e.com/{i}") for i in range(5)]
        brief = _Brief([_H("a", "https://e.com/3"), _H("b", "https://e.com/0")])
        out = _stories_for_page(brief, items)
        assert [s.url for s in out[:2]] == ["https://e.com/3", "https://e.com/0"]

    def test_caps_at_six(self):
        from triagent.agent import _stories_for_page

        items = [_Item(f"t{i}", f"https://e.com/{i}") for i in range(20)]
        assert len(_stories_for_page(_Brief([]), items)) == 6
