"""Tests for triagent.review — the human-postable fallback page."""

from __future__ import annotations

from triagent.review import render_review_page


def test_writes_file(tmp_path):
    out = render_review_page("TriPulse", tmp_path / "index.html")
    assert out.exists()
    assert out.read_text(encoding="utf-8").startswith("<!doctype html>")


def test_references_card_relatively(tmp_path):
    """The page and image sit side by side on gh-pages, so the src must be relative."""
    page = render_review_page("B", tmp_path / "index.html").read_text("utf-8")
    assert 'src="daily.png"' in page


def test_escapes_html_in_brand(tmp_path):
    page = render_review_page("<b>Brand</b>", tmp_path / "index.html").read_text(
        "utf-8"
    )
    assert "<b>Brand</b>" not in page


def test_creates_parent_directory(tmp_path):
    out = render_review_page("B", tmp_path / "nested" / "deep" / "index.html")
    assert out.exists()


class _S:
    def __init__(self, title, url, source):
        self.title, self.url, self.source = title, url, source


class TestStoryLinks:
    """The post's CTA sends people here for stories, so they must be here."""

    def test_renders_story_links(self, tmp_path):
        page = render_review_page("Brand",
            tmp_path / "i.html",
            stories=[_S("Big race news", "https://ex.com/a", "Triathlete")],
        ).read_text("utf-8")
        assert 'href="https://ex.com/a"' in page
        assert "Big race news" in page
        assert "Triathlete" in page

    def test_links_open_in_new_tab_safely(self, tmp_path):
        page = render_review_page("B", tmp_path / "i.html",
            stories=[_S("t", "https://ex.com/a", "s")],
        ).read_text("utf-8")
        assert 'rel="noopener"' in page

    def test_escapes_story_fields(self, tmp_path):
        page = render_review_page("B", tmp_path / "i.html",
            stories=[_S("<script>x</script>", "https://ex.com/a", "s")],
        ).read_text("utf-8")
        assert "<script>x</script>" not in page

    def test_handles_no_stories(self, tmp_path):
        page = render_review_page("B", tmp_path / "i.html").read_text("utf-8")
        assert "No sources recorded" in page


def test_brand_name_still_shown(tmp_path):
    page = render_review_page("TriPulse Daily", tmp_path / "i.html").read_text("utf-8")
    assert "TriPulse Daily" in page


def test_caption_block_removed(tmp_path):
    """The bio link makes this page public; readers have no use for the raw
    caption of the post they just came from."""
    page = render_review_page("B", tmp_path / "i.html").read_text("utf-8")
    assert "Caption (for posting)" not in page
    assert "<details" not in page
    assert "Copy caption" not in page
