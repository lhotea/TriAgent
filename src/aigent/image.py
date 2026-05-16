from __future__ import annotations

import datetime as dt
import logging
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

from .config import SLIDE_COUNT
from .summarizer import CarouselSlide, DailyBrief

log = logging.getLogger(__name__)

W, H = 1080, 1350  # Instagram portrait 4:5
MARGIN = 72

# Colors — dark "tech editorial" palette. High contrast for the feed thumbnail.
BG_TOP = (10, 14, 26)        # near-black indigo
BG_BOTTOM = (24, 28, 60)     # deep indigo
ACCENT = (124, 237, 162)     # mint — reads "AI / tech" without the cliché electric blue
ACCENT_SOFT = (88, 200, 130)
TEXT = (245, 247, 252)
MUTED = (148, 163, 184)
CHIP_BG = (30, 41, 70)
CODE_BG = (15, 21, 36)


def _vertical_gradient(size: tuple[int, int], top: tuple, bottom: tuple) -> Image.Image:
    w, h = size
    base = Image.new("RGB", (1, h), top)
    for y in range(h):
        t = y / max(h - 1, 1)
        r = int(top[0] + (bottom[0] - top[0]) * t)
        g = int(top[1] + (bottom[1] - top[1]) * t)
        b = int(top[2] + (bottom[2] - top[2]) * t)
        base.putpixel((0, y), (r, g, b))
    return base.resize((w, h))


def _font(size: int, *, bold: bool = False, mono: bool = False) -> ImageFont.FreeTypeFont:
    if mono:
        candidates = [
            "/usr/share/fonts/truetype/dejavu/DejaVuSansMono-Bold.ttf" if bold
            else "/usr/share/fonts/truetype/dejavu/DejaVuSansMono.ttf",
            "/Library/Fonts/Menlo.ttc",
            "/System/Library/Fonts/Menlo.ttc",
        ]
    elif bold:
        candidates = [
            "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
            "/Library/Fonts/Arial Bold.ttf",
            "/System/Library/Fonts/Supplemental/Arial Bold.ttf",
        ]
    else:
        candidates = [
            "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
            "/Library/Fonts/Arial.ttf",
            "/System/Library/Fonts/Supplemental/Arial.ttf",
        ]
    for path in candidates:
        if Path(path).exists():
            return ImageFont.truetype(path, size)
    return ImageFont.load_default()


def _wrap(draw: ImageDraw.ImageDraw, text: str, font, max_width: int) -> list[str]:
    if not text:
        return []
    words = text.split()
    lines: list[str] = []
    current = ""
    for word in words:
        trial = f"{current} {word}".strip()
        width = draw.textlength(trial, font=font)
        if width <= max_width or not current:
            current = trial
        else:
            lines.append(current)
            current = word
    if current:
        lines.append(current)
    return lines


def _line_height(font) -> int:
    # Empirical: ~1.25× font size reads cleanly on IG portraits.
    ascent, descent = font.getmetrics()
    return int((ascent + descent) * 1.05)


def _draw_header(draw: ImageDraw.ImageDraw, brand_name: str, slide_idx: int) -> int:
    """Brand row + slide indicator dots. Returns y-cursor below the header."""
    # Accent bar
    draw.rectangle([MARGIN, MARGIN, MARGIN + 96, MARGIN + 6], fill=ACCENT)

    header_font = _font(28, bold=True)
    chip_font = _font(20, bold=True)

    draw.text((MARGIN, MARGIN + 22), brand_name.upper(), fill=TEXT, font=header_font)

    # Slide indicator dots, top-right.
    dot_r = 8
    gap = 18
    total_w = SLIDE_COUNT * (dot_r * 2) + (SLIDE_COUNT - 1) * (gap - dot_r * 2)
    x_start = W - MARGIN - total_w
    y_dot = MARGIN + 28
    for i in range(SLIDE_COUNT):
        cx = x_start + i * gap
        if i == slide_idx:
            draw.ellipse([cx, y_dot - dot_r, cx + dot_r * 2, y_dot + dot_r], fill=ACCENT)
        else:
            draw.ellipse([cx, y_dot - dot_r, cx + dot_r * 2, y_dot + dot_r], fill=CHIP_BG)

    # Category chip on slides past the cover (set by caller via _category_chip below).
    return MARGIN + 76


def _category_chip(draw: ImageDraw.ImageDraw, label: str, y: int) -> int:
    if not label:
        return y
    chip_font = _font(20, bold=True)
    text = label.upper()
    tw = draw.textlength(text, font=chip_font)
    pad_x, pad_y = 16, 8
    chip_h = 36
    rect = [MARGIN, y, MARGIN + int(tw) + 2 * pad_x, y + chip_h]
    draw.rounded_rectangle(rect, radius=10, fill=CHIP_BG, outline=ACCENT_SOFT, width=2)
    draw.text((MARGIN + pad_x, y + pad_y - 2), text, fill=ACCENT, font=chip_font)
    return y + chip_h + 24


def _draw_title_block(
    draw: ImageDraw.ImageDraw,
    title: str,
    subtitle: str,
    y: int,
    *,
    title_size: int = 64,
    subtitle_size: int = 32,
    max_title_lines: int = 4,
) -> int:
    title_font = _font(title_size, bold=True)
    sub_font = _font(subtitle_size)
    title_lines = _wrap(draw, title, title_font, W - 2 * MARGIN)[:max_title_lines]
    lh = _line_height(title_font)
    for line in title_lines:
        draw.text((MARGIN, y), line, fill=TEXT, font=title_font)
        y += lh
    if subtitle:
        y += 16
        sub_lines = _wrap(draw, subtitle, sub_font, W - 2 * MARGIN)[:3]
        sub_lh = _line_height(sub_font)
        for line in sub_lines:
            draw.text((MARGIN, y), line, fill=MUTED, font=sub_font)
            y += sub_lh
    return y


def _draw_bullets(
    draw: ImageDraw.ImageDraw,
    bullets: list[str],
    y: int,
    *,
    style: str = "check",
    size: int = 30,
) -> int:
    """Draw a stack of bullets. style: 'check' (mint dot) | 'num' (numbered)."""
    body_font = _font(size)
    body_bold = _font(size, bold=True)
    indent = 60
    lh = _line_height(body_font)
    for idx, b in enumerate(bullets, start=1):
        marker_y = y + (lh - 22) // 2
        if style == "num":
            label = f"{idx:02}"
            draw.text((MARGIN, y), label, fill=ACCENT, font=body_bold)
        else:
            # Mint pill marker
            draw.rounded_rectangle(
                [MARGIN, marker_y, MARGIN + 22, marker_y + 22], radius=6, fill=ACCENT
            )
        lines = _wrap(draw, b, body_font, W - 2 * MARGIN - indent)
        ly = y
        for line in lines[:3]:
            draw.text((MARGIN + indent, ly), line, fill=TEXT, font=body_font)
            ly += lh
        y = ly + 12
    return y


def _draw_code_box(
    draw: ImageDraw.ImageDraw,
    bullets: list[str],
    y: int,
) -> int:
    """Render bullets inside a code-style panel. Used on the 'how' slide for prompts/workflows."""
    pad = 28
    body_font = _font(24, mono=True)
    lh = _line_height(body_font)
    box_x1 = MARGIN
    box_x2 = W - MARGIN
    inner_w = box_x2 - box_x1 - 2 * pad

    # Pre-wrap to compute box height.
    wrapped: list[str] = []
    for b in bullets:
        wrapped.extend(_wrap(draw, b, body_font, inner_w))
        wrapped.append("")  # blank between items
    if wrapped and wrapped[-1] == "":
        wrapped.pop()
    box_h = pad * 2 + max(lh, lh * len(wrapped))

    draw.rounded_rectangle(
        [box_x1, y, box_x2, y + box_h], radius=20, fill=CODE_BG, outline=ACCENT_SOFT, width=2
    )
    # Row of fake terminal dots to sell the "code" affordance.
    cx = box_x1 + pad
    for color in ((255, 95, 86), (255, 189, 46), (39, 201, 63)):
        draw.ellipse([cx, y + pad, cx + 14, y + pad + 14], fill=color)
        cx += 22

    ty = y + pad + 30
    for line in wrapped:
        draw.text((box_x1 + pad, ty), line, fill=TEXT, font=body_font)
        ty += lh
    return y + box_h + 24


def _draw_footer(draw: ImageDraw.ImageDraw, text: str) -> None:
    cta_font = _font(26, bold=True)
    draw.text((MARGIN, H - MARGIN - 32), text, fill=ACCENT, font=cta_font)


def _render_cover(slide: CarouselSlide, brief: DailyBrief, brand_name: str) -> Image.Image:
    img = _vertical_gradient((W, H), BG_TOP, BG_BOTTOM)
    draw = ImageDraw.Draw(img)
    _draw_header(draw, brand_name, slide_idx=0)

    y = _category_chip(draw, brief.tool.category, y=MARGIN + 110)

    # Big hook (the title on the cover).
    title_font = _font(82, bold=True)
    hook_lines = _wrap(draw, slide.title, title_font, W - 2 * MARGIN)[:4]
    lh = _line_height(title_font)
    for line in hook_lines:
        draw.text((MARGIN, y), line, fill=TEXT, font=title_font)
        y += lh

    y += 32
    draw.line([(MARGIN, y), (MARGIN + 200, y)], fill=ACCENT, width=4)
    y += 36

    # Tool name + tagline
    name_font = _font(54, bold=True)
    tag_font = _font(30)
    draw.text((MARGIN, y), brief.tool.name, fill=ACCENT, font=name_font)
    y += _line_height(name_font) + 4
    tag_lines = _wrap(draw, slide.subtitle or brief.tool.tagline, tag_font, W - 2 * MARGIN)[:3]
    for line in tag_lines:
        draw.text((MARGIN, y), line, fill=MUTED, font=tag_font)
        y += _line_height(tag_font)

    today = dt.datetime.now(dt.timezone.utc).strftime("%b %d, %Y").upper()
    date_font = _font(20)
    draw.text((MARGIN, H - MARGIN - 70), today, fill=MUTED, font=date_font)
    _draw_footer(draw, "SWIPE FOR THE BREAKDOWN →")
    return img


def _render_what(slide: CarouselSlide, brief: DailyBrief, brand_name: str) -> Image.Image:
    img = _vertical_gradient((W, H), BG_TOP, BG_BOTTOM)
    draw = ImageDraw.Draw(img)
    _draw_header(draw, brand_name, slide_idx=1)
    y = _category_chip(draw, brief.tool.name, y=MARGIN + 110)
    y = _draw_title_block(draw, slide.title, slide.subtitle, y, title_size=58, subtitle_size=28)
    y += 36
    _draw_bullets(draw, slide.bullets, y, style="check", size=30)
    _draw_footer(draw, "→ KEEP SWIPING")
    return img


def _render_how(slide: CarouselSlide, brief: DailyBrief, brand_name: str) -> Image.Image:
    img = _vertical_gradient((W, H), BG_TOP, BG_BOTTOM)
    draw = ImageDraw.Draw(img)
    _draw_header(draw, brand_name, slide_idx=2)
    y = _category_chip(draw, "TRY THIS", y=MARGIN + 110)
    y = _draw_title_block(draw, slide.title, slide.subtitle, y, title_size=52, subtitle_size=26)
    y += 32
    if slide.bullets:
        _draw_code_box(draw, slide.bullets, y)
    _draw_footer(draw, "SAVE THIS PROMPT  ⌘")
    return img


def _render_verdict(slide: CarouselSlide, brief: DailyBrief, brand_name: str) -> Image.Image:
    img = _vertical_gradient((W, H), BG_TOP, BG_BOTTOM)
    draw = ImageDraw.Draw(img)
    _draw_header(draw, brand_name, slide_idx=3)
    y = _category_chip(draw, "VERDICT", y=MARGIN + 110)
    y = _draw_title_block(draw, slide.title, slide.subtitle, y, title_size=58, subtitle_size=30)
    y += 36
    _draw_bullets(draw, slide.bullets, y, style="num", size=28)
    _draw_footer(draw, "→ ONE MORE SLIDE")
    return img


def _render_cta(slide: CarouselSlide, brief: DailyBrief, brand_name: str, brand_handle: str) -> Image.Image:
    img = _vertical_gradient((W, H), BG_TOP, BG_BOTTOM)
    draw = ImageDraw.Draw(img)
    _draw_header(draw, brand_name, slide_idx=4)

    # Centered-ish vertical block.
    y = MARGIN + 220
    title_font = _font(74, bold=True)
    sub_font = _font(32)
    title_lines = _wrap(draw, slide.title, title_font, W - 2 * MARGIN)[:3]
    for line in title_lines:
        draw.text((MARGIN, y), line, fill=TEXT, font=title_font)
        y += _line_height(title_font)
    y += 24
    sub_lines = _wrap(draw, slide.subtitle, sub_font, W - 2 * MARGIN)[:4]
    for line in sub_lines:
        draw.text((MARGIN, y), line, fill=MUTED, font=sub_font)
        y += _line_height(sub_font)

    # Action chip row
    y += 40
    chip_font = _font(26, bold=True)
    chips = ["SAVE", "SHARE", "FOLLOW"]
    cx = MARGIN
    for label in chips:
        tw = draw.textlength(label, font=chip_font)
        pad_x = 24
        chip_w = int(tw) + 2 * pad_x
        draw.rounded_rectangle(
            [cx, y, cx + chip_w, y + 60], radius=14, fill=ACCENT, outline=None
        )
        draw.text((cx + pad_x, y + 14), label, fill=BG_TOP, font=chip_font)
        cx += chip_w + 18

    # Handle anchor at the bottom
    handle_font = _font(36, bold=True)
    hw = draw.textlength(brand_handle, font=handle_font)
    draw.text(((W - hw) // 2, H - MARGIN - 80), brand_handle, fill=ACCENT, font=handle_font)
    return img


_RENDERERS = {
    "cover": _render_cover,
    "what": _render_what,
    "how": _render_how,
    "verdict": _render_verdict,
}


def render_carousel(
    brief: DailyBrief,
    *,
    brand_name: str,
    brand_handle: str,
    out_paths: list[Path],
) -> list[Path]:
    """Render the 5-slide carousel to disk. out_paths must have len == SLIDE_COUNT."""
    if len(out_paths) != SLIDE_COUNT:
        raise ValueError(f"expected {SLIDE_COUNT} paths, got {len(out_paths)}")
    if len(brief.slides) != SLIDE_COUNT:
        raise ValueError(f"expected {SLIDE_COUNT} slides in brief, got {len(brief.slides)}")

    written: list[Path] = []
    for slide, path in zip(brief.slides, out_paths):
        if slide.role == "cta":
            img = _render_cta(slide, brief, brand_name, brand_handle)
        else:
            renderer = _RENDERERS[slide.role]
            img = renderer(slide, brief, brand_name)
        path.parent.mkdir(parents=True, exist_ok=True)
        img.save(path, format="PNG", optimize=True)
        log.info("rendered %s slide to %s", slide.role, path)
        written.append(path)
    return written
