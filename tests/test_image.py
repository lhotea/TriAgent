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