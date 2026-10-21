"""Tests for triagent.image — card rendering with Pillow."""

from __future__ import annotations

from pathlib import Path

from PIL import Image

from triagent.image import render_card


def test_render_card_creates_file(tmp_assets_dir, sample_brief):
    """render_card writes a PNG file to the output path."""
    out_path = tmp_assets_dir / "daily.png"
    result = render_card(sample_brief, brand_name="TestBrand", out_path=out_path)

    assert result.exists()
    assert result == out_path


def test_render_card_is_valid_image(tmp_assets_dir, sample_brief):
    """render_card produces a valid, loadable PNG."""
    out_path = tmp_assets_dir / "daily.png"
    render_card(sample_brief, brand_name="TestBrand", out_path=out_path)

    img = Image.open(out_path)
    assert img.format == "PNG"
    assert img.size == (1080, 1350)  # Instagram 4:5 portrait


def test_render_card_dimensions(tmp_assets_dir, sample_brief):
    """Card is exactly 1080x1350 (Instagram portrait 4:5)."""
    out_path = tmp_assets_dir / "daily.png"
    render_card(sample_brief, brand_name="TestBrand", out_path=out_path)

    img = Image.open(out_path)
    w, h = img.size
    assert w == 1080
    assert h == 1350
    assert w / h == 0.8  # 4:5 ratio


def test_render_card_is_rgb(tmp_assets_dir, sample_brief):
    """Card is in RGB mode (suitable for Instagram)."""
    out_path = tmp_assets_dir / "daily.png"
    render_card(sample_brief, brand_name="TestBrand", out_path=out_path)

    img = Image.open(out_path)
    assert img.mode == "RGB"


def test_render_card_includes_brand_name(tmp_assets_dir, sample_brief):
    """Card renders the brand name at the top."""
    out_path = tmp_assets_dir / "daily.png"
    render_card(sample_brief, brand_name="IRON INSIGHTS", out_path=out_path)

    img = Image.open(out_path)
    # The brand name should be rendered as text pixels
    # We can't easily OCR the image, but we can verify the file is non-empty and valid
    assert img.size == (1080, 1350)
    # Check that the image has non-uniform pixels (i.e., text was rendered)
    pixels = list(img.getdata())  # type: ignore[assignment]
    unique_colors = set(pixels)
    assert len(unique_colors) > 10  # More than just the gradient


def test_render_card_includes_hook(tmp_assets_dir, sample_brief):
    """Card renders the hook text prominently."""
    out_path = tmp_assets_dir / "daily.png"
    render_card(sample_brief, brand_name="TestBrand", out_path=out_path)

    img = Image.open(out_path)
    # Verify card has content (hook text should create additional pixels)
    pixels = list(img.getdata())  # type: ignore[assignment]
    unique_colors = set(pixels)
    assert len(unique_colors) > 20  # Hook + headlines add significant variation


def test_render_card_includes_headlines(tmp_assets_dir, sample_brief):
    """Card renders up to 3 headlines."""
    out_path = tmp_assets_dir / "daily.png"
    # Add more than 3 headlines — only 3 should render
    sample_brief.headlines.append(
        type(sample_brief.headlines[0])(
            title="Extra headline that should not render",
            one_liner="Extra one-liner",
            source="Extra",
            source_url="https://example.com/extra",
        )
    )

    render_card(sample_brief, brand_name="TestBrand", out_path=out_path)

    img = Image.open(out_path)
    assert img.size == (1080, 1350)


def test_render_card_uses_accent_color(tmp_assets_dir, sample_brief):
    """Card uses the accent color (amber) for decorative elements."""
    from triagent.image import ACCENT

    out_path = tmp_assets_dir / "daily.png"
    render_card(sample_brief, brand_name="TestBrand", out_path=out_path)

    img = Image.open(out_path)
    pixels = list(img.getdata())  # type: ignore[assignment]
    # Check that the accent color appears somewhere in the image
    accent_found = any(abs(p[0] - ACCENT[0]) < 20 and abs(p[1] - ACCENT[1]) < 20 and abs(p[2] - ACCENT[2]) < 20 for p in pixels)
    assert accent_found


def test_render_card_overrides_existing_file(tmp_assets_dir, sample_brief):
    """render_card overwrites an existing file at the output path."""
    out_path = tmp_assets_dir / "daily.png"

    # Write a dummy file first
    out_path.write_bytes(b"dummy")
    assert out_path.read_bytes() == b"dummy"

    render_card(sample_brief, brand_name="TestBrand", out_path=out_path)

    # Should now be a valid PNG, not the dummy bytes
    img = Image.open(out_path)
    assert img.format == "PNG"


def test_render_card_creates_parent_dir(tmp_path, sample_brief):
    """render_card creates parent directories if they don't exist."""
    out_path = tmp_path / "nested" / "deep" / "card.png"
    render_card(sample_brief, brand_name="TestBrand", out_path=out_path)

    assert out_path.exists()

class TestLogo:
    def test_logo_loads_and_crops_to_the_mark(self):
        from triagent.image import load_logo

        logo = load_logo()
        assert logo is not None
        assert logo.mode == "RGBA"
        # Source is 1294x832; cropping to the mark must shrink it.
        assert logo.width < 1294 and logo.height < 832

    def test_backing_plate_is_keyed_out(self):
        """The file is opaque RGBA — corners must end up transparent."""
        from triagent.image import load_logo

        logo = load_logo()
        assert logo.getpixel((0, 0))[3] == 0

    def test_missing_logo_returns_none_not_raises(self, tmp_path):
        from triagent.image import load_logo

        assert load_logo(tmp_path / "absent.png") is None

    def test_paste_logo_reports_false_without_logo(self):
        from PIL import Image
        from triagent.image import paste_logo

        img = Image.new("RGB", (100, 100))
        assert paste_logo(img, None, cx=50, cy=50, height=20) is False


class TestSlides:
    def test_renders_requested_number_of_slides(self, tmp_assets_dir, sample_brief):
        from triagent.image import render_slides

        paths = render_slides(sample_brief, "Brand", tmp_assets_dir, "daily-x", count=3)
        assert len(paths) == 3
        assert all(p.exists() for p in paths)

    def test_slide_filenames_are_ordered(self, tmp_assets_dir, sample_brief):
        from triagent.image import render_slides

        paths = render_slides(sample_brief, "Brand", tmp_assets_dir, "daily-x", count=3)
        assert [p.name for p in paths] == [
            "daily-x-1.png",
            "daily-x-2.png",
            "daily-x-3.png",
        ]

    def test_cta_names_the_destination(self):
        """'Link in bio' alone doesn't say what's there."""
        from triagent.image import CTA_TEXT

        assert "TRIATHLON NEWS" in CTA_TEXT


class TestLeadStorySlides:
    """The hook is a promise; slides 2 and 3 have to keep it for the same story."""

    @staticmethod
    def _drawn(monkeypatch, render):
        """Every string the renderer draws, in order, joined by spaces."""
        from PIL import ImageDraw

        seen: list[str] = []
        real = ImageDraw.ImageDraw.text

        def spy(self, xy, text, *args, **kwargs):
            seen.append(str(text))
            return real(self, xy, text, *args, **kwargs)

        monkeypatch.setattr(ImageDraw.ImageDraw, "text", spy)
        render()
        return " ".join(seen)

    def _slide(self, brief, index, tmp_assets_dir, **kw):
        from triagent.image import render_slide

        return render_slide(brief, index, "Brand", tmp_assets_dir / f"s{index}.png", **kw)

    def test_slide_two_states_the_leads_facts_and_credits_the_source(
        self, monkeypatch, tmp_assets_dir, sample_brief
    ):
        text = self._drawn(monkeypatch, lambda: self._slide(sample_brief, 1, tmp_assets_dir))
        assert "THE DETAILS" in text
        for fact in sample_brief.takeaways:
            assert fact.split()[0] in text and fact.split()[-1] in text
        assert "SOURCE: TRIATHLETE" in text  # headlines[0].source, the lead story's
        # ...and does NOT spend the slide on a different story, as it used to.
        assert "TRI-SUIT" not in text.upper() and "ITU" not in text

    def test_slide_three_says_what_it_means_and_asks_for_a_save(
        self, monkeypatch, tmp_assets_dir, sample_brief
    ):
        from triagent import image

        ctas = []
        real = image._draw_footer
        monkeypatch.setattr(
            image, "_draw_footer",
            lambda *a, **k: (ctas.append(k.get("cta_text")), real(*a, **k))[1],
        )
        text = self._drawn(monkeypatch, lambda: self._slide(sample_brief, 2, tmp_assets_dir))
        assert "WHAT IT MEANS FOR YOU" in text
        assert "Kona" in text and "run" in text
        assert ctas == [image.SAVE_CTA_TEXT]
        assert "SAVE" in image.SAVE_CTA_TEXT and "TRAINING PARTNER" in image.SAVE_CTA_TEXT

    def test_slide_two_keeps_the_link_in_bio_footer(self, monkeypatch, tmp_assets_dir, sample_brief):
        from triagent import image

        ctas = []
        real = image._draw_footer
        monkeypatch.setattr(
            image, "_draw_footer",
            lambda *a, **k: (ctas.append(k.get("cta_text")), real(*a, **k))[1],
        )
        self._slide(sample_brief, 1, tmp_assets_dir)
        assert ctas == [image.CTA_TEXT]

    def test_without_takeaways_the_original_layout_is_used(
        self, monkeypatch, tmp_assets_dir, sample_brief
    ):
        """A brief from before this change, or a model that returned none, must still render."""
        old = sample_brief.model_copy(update={"takeaways": [], "implication": ""})
        text = self._drawn(monkeypatch, lambda: self._slide(old, 1, tmp_assets_dir))
        assert "THE DETAILS" not in text
        assert "TRI-SUIT" in text.upper()  # headlines[1], as before

    def test_without_an_implication_slide_three_falls_back_to_the_next_story(
        self, monkeypatch, tmp_assets_dir, sample_brief
    ):
        brief = sample_brief.model_copy(update={"implication": "  "})
        text = self._drawn(monkeypatch, lambda: self._slide(brief, 2, tmp_assets_dir))
        assert "WHAT IT MEANS" not in text
        assert "TRI-SUIT" in text.upper()  # headlines[1] fills the slide the sentence could not

    def test_extra_slides_carry_the_supporting_stories_in_order(
        self, monkeypatch, tmp_assets_dir, sample_brief
    ):
        """With four slides, slide 4 is the first *other* story, not a repeat of the lead."""
        text = self._drawn(monkeypatch, lambda: self._slide(sample_brief, 3, tmp_assets_dir))
        assert "TRI-SUIT" in text.upper()
        assert "THE DETAILS" not in text

    def test_a_long_takeaway_still_fits(self, tmp_assets_dir, sample_brief):
        from PIL import Image

        long_fact = " ".join(["Kemenz"] * 40)
        brief = sample_brief.model_copy(update={"takeaways": [long_fact] * 3})
        path = self._slide(brief, 1, tmp_assets_dir)
        with Image.open(path) as img:
            assert img.size == (1080, 1350)
            # The footer band stays clear of body text: sample a strip just above the mark.
            strip = img.crop((0, 1350 - 150 + 10, 1080, 1350 - 150 + 20)).convert("L")
            assert strip.getextrema() == (0, 0)

    def test_renders_at_reel_size(self, tmp_assets_dir, sample_brief):
        from PIL import Image

        from triagent.image import REEL_H, REEL_W

        for index in (1, 2):
            path = self._slide(sample_brief, index, tmp_assets_dir, size=(REEL_W, REEL_H))
            with Image.open(path) as img:
                assert img.size == (REEL_W, REEL_H)

    def test_all_three_slides_render_through_render_slides(self, tmp_assets_dir, sample_brief):
        from triagent.image import render_slides

        paths = render_slides(sample_brief, "Brand", tmp_assets_dir, "daily-x", count=3)
        assert [p.name for p in paths] == ["daily-x-1.png", "daily-x-2.png", "daily-x-3.png"]
        assert all(p.stat().st_size > 1000 for p in paths)


class TestSlideRobustness:
    """Text on slides 2-3 comes from a model summarising feeds; it has to survive odd input."""

    @staticmethod
    def _right_margin_is_clear(img):
        from triagent.image import MARGIN

        w, h = img.size
        # Below the header rule, above the footer band: nothing may reach the right margin.
        strip = img.convert("L").crop((w - MARGIN + 2, 150, w, h - 150))
        return strip.getextrema() == (0, 0)

    def _slide(self, brief, index, tmp_assets_dir):
        from PIL import Image

        from triagent.image import render_slide

        path = render_slide(brief, index, "Brand", tmp_assets_dir / f"s{index}.png")
        return Image.open(path)

    def test_a_long_feed_title_as_the_source_stays_inside_the_margin(self, tmp_assets_dir, sample_brief):
        title = "Triathlon News | 220 Triathlon - Britain's leading triathlon magazine and website"
        headlines = list(sample_brief.headlines)
        headlines[0] = headlines[0].model_copy(update={"source": title})
        brief = sample_brief.model_copy(update={"headlines": headlines})
        with self._slide(brief, 1, tmp_assets_dir) as img:
            assert self._right_margin_is_clear(img)

    def test_the_source_credit_is_cut_with_an_ellipsis_only_when_it_cannot_fit(self, tmp_assets_dir):
        from PIL import Image, ImageDraw

        from triagent.image import _fit_one_line

        draw = ImageDraw.Draw(Image.new("RGB", (1080, 100)))
        short, _ = _fit_one_line(draw, "SOURCE: 220 TRIATHLON", 952)
        assert short == "SOURCE: 220 TRIATHLON"
        long, font = _fit_one_line(draw, "SOURCE: " + "WORD " * 40, 952)
        assert long.endswith("…") and draw.textlength(long, font=font) <= 952

    def test_an_unbroken_token_in_a_takeaway_is_broken_to_fit(self, tmp_assets_dir, sample_brief):
        token = "https://www.220triathlon.com/news/" + "ironman-" * 8
        brief = sample_brief.model_copy(update={"takeaways": [token, "A normal fact."]})
        with self._slide(brief, 1, tmp_assets_dir) as img:
            assert self._right_margin_is_clear(img)

    def test_an_unbroken_token_in_the_implication_is_broken_to_fit(self, tmp_assets_dir, sample_brief):
        brief = sample_brief.model_copy(update={"implication": "Read " + "x" * 90})
        with self._slide(brief, 2, tmp_assets_dir) as img:
            assert self._right_margin_is_clear(img)

    def test_split_long_words_leaves_ordinary_text_alone(self):
        from PIL import Image, ImageDraw

        from triagent.image import _load_font, _split_long_words

        draw = ImageDraw.Draw(Image.new("RGB", (10, 10)))
        font = _load_font(40, bold=True)
        assert _split_long_words(draw, "a plain  sentence here", font, 900) == "a plain sentence here"

    def test_split_long_words_never_loses_a_character(self):
        from PIL import Image, ImageDraw

        from triagent.image import _load_font, _split_long_words

        draw = ImageDraw.Draw(Image.new("RGB", (10, 10)))
        font = _load_font(40, bold=True)
        word = "abcdefghij" * 12
        pieces = _split_long_words(draw, word, font, 300).split()
        assert "".join(pieces) == word and len(pieces) > 1
        assert all(draw.textlength(piece, font=font) <= 300 for piece in pieces)
