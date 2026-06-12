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
            patch("triagent.agent.fetch_recent", return_value=[]),
            pytest.raises(RuntimeError, match="no fresh triathlon news"),
        ):
            build(mock_settings)

    def test_build_calls_all_steps(self, mock_settings, sample_news_items, sample_brief):
        """build() calls fetch → summarize → render → assemble_caption."""
        mock_caption_path = MagicMock()
        mock_caption_path.write_text = MagicMock()
        mock_settings.image_path.with_name.return_value = mock_caption_path

        with (
            patch("triagent.agent.fetch_recent", return_value=sample_news_items),
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
            patch("triagent.agent.fetch_recent", return_value=many_items),
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
            patch("triagent.agent.fetch_recent", return_value=sample_news_items),
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
            patch("triagent.agent.fetch_recent", return_value=sample_news_items),
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
        assert url == "https://cdn.example.com/daily.png"

    def test_image_url_without_base_url(self, mock_settings):
        mock_settings.public_image_base_url = None
        url = _image_url(mock_settings)
        assert "<unset>" in url
        assert "daily.png" in url
