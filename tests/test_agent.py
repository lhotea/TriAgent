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
            pytest.raises(RuntimeError, match="no unused triathlon news"),
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


@pytest.fixture()
def real_settings(tmp_path, monkeypatch):
    """Real Settings and real files in a temp dir, so naming is tested end to end."""
    from triagent import agent
    from triagent.config import Settings

    monkeypatch.setattr(agent, "HISTORY_PATH", tmp_path / "posted.json")
    monkeypatch.setattr(agent, "PENDING_PATH", tmp_path / "pending.json")
    return Settings(
        anthropic_api_key="k", ig_user_id="1", ig_access_token="t",
        public_image_base_url="https://example.com/site",
        brand_handle="@x", brand_name="X", image_path=tmp_path / "daily.png",
    )


def _build(settings, items, brief, tag):
    """build() with the network and the model replaced, everything else real."""
    with (
        patch("triagent.agent.fetch_recent_widening", return_value=items),
        patch("triagent.agent.Summarizer") as summ,
        patch("triagent.agent.resolve_background", return_value=None),
        patch("triagent.agent.new_tag", return_value=tag),
    ):
        summ.return_value.build_brief.return_value = brief
        return build(settings)


def _publish(settings):
    """publish_from_build() with Instagram replaced; returns the slide URLs it was given."""
    with patch("triagent.agent.InstagramPublisher") as pub:
        pub.return_value.publish_carousel.return_value = "media-1"
        publish_from_build(settings)
    return pub.return_value.publish_carousel.call_args.kwargs["image_urls"]


class TestAssetTags:
    """Two publishes on one UTC date must not share URLs, and a run may hold past midnight."""

    def test_tag_has_a_checkable_format(self):
        import re
        from triagent.agent import new_tag

        assert re.fullmatch(r"\d{4}-\d{2}-\d{2}-\d{6}", new_tag())

    def test_every_published_file_carries_the_tag(
        self, real_settings, sample_news_items, sample_brief, tmp_path
    ):
        result = _build(real_settings, sample_news_items, sample_brief, "2026-10-23-110000")
        names = {p.name for p in tmp_path.iterdir()}
        assert {
            "daily-2026-10-23-110000.png",
            "daily-2026-10-23-110000-1.png",
            "daily-2026-10-23-110000-2.png",
            "daily-2026-10-23-110000-3.png",
            "post-tag.txt",
        } <= names
        assert result.tag == "2026-10-23-110000"
        assert result.image_url == "https://example.com/site/daily-2026-10-23-110000.png"
        assert "daily-2026-10-23-110000.png" in (tmp_path / "index.html").read_text()

    def test_two_builds_on_one_date_publish_at_different_urls(
        self, real_settings, sample_news_items, sample_brief
    ):
        """A manual run beside a scheduled one must not put new slides at old URLs —
        wait_for_image only asks whether the URL answers 200, and the old file does."""
        _build(real_settings, sample_news_items, sample_brief, "2026-10-23-110000")
        first = _publish(real_settings)
        _build(real_settings, sample_news_items, sample_brief, "2026-10-23-190000")
        second = _publish(real_settings)
        assert first and second
        assert not set(first) & set(second)
        assert all("2026-10-23-110000" in u for u in first)
        assert all("2026-10-23-190000" in u for u in second)

    def test_publish_uses_the_names_the_build_wrote_not_the_clock(
        self, real_settings, sample_news_items, sample_brief, monkeypatch
    ):
        """The run holds for hours between build and publish. If it crosses 00:00 UTC the
        clock names different files — and publish used to fail with 'no rendered slide'."""
        from triagent import agent

        _build(real_settings, sample_news_items, sample_brief, "2026-10-22-235800")
        monkeypatch.setattr(agent, "_today", lambda: "2026-10-23")  # midnight has passed
        urls = _publish(real_settings)
        assert all("2026-10-22-235800" in u for u in urls)

    def test_a_tag_file_that_is_not_a_tag_is_ignored(self, real_settings, tmp_path):
        from triagent.agent import read_tag, tag_path

        for junk in ("", "2026-10-23", "../../etc/passwd", "Test caption", "2026-10-23-11000x"):
            tag_path(real_settings).write_text(junk)
            assert read_tag(real_settings) is None, junk

    def test_no_tag_file_means_none(self, real_settings):
        from triagent.agent import read_tag

        assert read_tag(real_settings) is None

    def test_without_a_tag_names_fall_back_to_the_date(
        self, real_settings, sample_news_items, sample_brief, tmp_path, monkeypatch
    ):
        """A build from before tags existed named its files by date; publish still finds them."""
        from triagent import agent

        _build(real_settings, sample_news_items, sample_brief, "2026-10-23-110000")
        (tmp_path / "post-tag.txt").unlink()
        for n in (1, 2, 3):  # what the older build would have written
            (tmp_path / f"daily-2026-10-23-110000-{n}.png").rename(tmp_path / f"daily-2026-10-23-{n}.png")
        monkeypatch.setattr(agent, "_today", lambda: "2026-10-23")
        assert all(u.endswith(f"daily-2026-10-23-{n}.png") for n, u in enumerate(_publish(real_settings), 1))

    def test_a_missing_slide_still_fails_loudly(
        self, real_settings, sample_news_items, sample_brief, tmp_path
    ):
        _build(real_settings, sample_news_items, sample_brief, "2026-10-23-110000")
        (tmp_path / "daily-2026-10-23-110000-2.png").unlink()
        with pytest.raises(RuntimeError, match="no rendered slide"):
            _publish(real_settings)

    def test_run_publishes_the_names_it_built(
        self, real_settings, sample_news_items, sample_brief
    ):
        with (
            patch("triagent.agent.fetch_recent_widening", return_value=sample_news_items),
            patch("triagent.agent.Summarizer") as summ,
            patch("triagent.agent.resolve_background", return_value=None),
            patch("triagent.agent.new_tag", return_value="2026-10-23-110000"),
            patch("triagent.agent.InstagramPublisher") as pub,
        ):
            summ.return_value.build_brief.return_value = sample_brief
            pub.return_value.publish_carousel.return_value = "media-1"
            result = run(real_settings)
        urls = pub.return_value.publish_carousel.call_args.kwargs["image_urls"]
        assert all("2026-10-23-110000" in u for u in urls)
        assert result.tag == "2026-10-23-110000" and result.media_id == "media-1"
