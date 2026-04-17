from __future__ import annotations

import datetime as dt
import logging
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

from .summarizer import DailyBrief

log = logging.getLogger(__name__)

W, H = 1080, 1350  # Instagram portrait 4:5
MARGIN = 72
BG_TOP = (15, 23, 42)       # slate-900
BG_BOTTOM = (6, 78, 130)    # deep ocean
ACCENT = (250, 204, 21)     # amber
TEXT = (248, 250, 252)
MUTED = (148, 163, 184)


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


def _load_font(size: int, bold: bool = False) -> ImageFont.FreeTypeFont:
    candidates = (
        [
            "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
            "/Library/Fonts/Arial Bold.ttf",
            "/System/Library/Fonts/Supplemental/Arial Bold.ttf",
        ]
        if bold
        else [
            "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
            "/Library/Fonts/Arial.ttf",
            "/System/Library/Fonts/Supplemental/Arial.ttf",
        ]
    )
    for path in candidates:
        if Path(path).exists():
            return ImageFont.truetype(path, size)
    return ImageFont.load_default()


def _wrap(draw: ImageDraw.ImageDraw, text: str, font, max_width: int) -> list[str]:
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


def render_card(brief: DailyBrief, brand_name: str, out_path: Path) -> Path:
    img = _vertical_gradient((W, H), BG_TOP, BG_BOTTOM)
    draw = ImageDraw.Draw(img)

    # Accent bar
    draw.rectangle([MARGIN, MARGIN, MARGIN + 120, MARGIN + 8], fill=ACCENT)

    # Brand + date row
    header_font = _load_font(30, bold=True)
    date_font = _load_font(24)
    today = dt.datetime.now(dt.timezone.utc).strftime("%a · %b %d, %Y").upper()
    draw.text((MARGIN, MARGIN + 24), brand_name.upper(), fill=TEXT, font=header_font)
    date_w = draw.textlength(today, font=date_font)
    draw.text((W - MARGIN - date_w, MARGIN + 30), today, fill=MUTED, font=date_font)

    # Hook — the star of the card
    hook_font = _load_font(68, bold=True)
    hook_lines = _wrap(draw, brief.hook, hook_font, W - 2 * MARGIN)
    y = MARGIN + 140
    for line in hook_lines[:3]:
        draw.text((MARGIN, y), line, fill=TEXT, font=hook_font)
        y += 82

    # Divider
    y += 20
    draw.line([(MARGIN, y), (W - MARGIN, y)], fill=ACCENT, width=3)
    y += 40

    # Headlines stack
    num_font = _load_font(44, bold=True)
    title_font = _load_font(34, bold=True)
    body_font = _load_font(26)

    for idx, h in enumerate(brief.headlines[:3], start=1):
        draw.text((MARGIN, y), f"{idx:02}", fill=ACCENT, font=num_font)
        title_lines = _wrap(draw, h.title, title_font, W - 2 * MARGIN - 90)
        ty = y
        for line in title_lines[:2]:
            draw.text((MARGIN + 90, ty), line, fill=TEXT, font=title_font)
            ty += 42
        src = h.source[:40]
        draw.text((MARGIN + 90, ty + 4), src.upper(), fill=MUTED, font=body_font)
        y = ty + 60

    # Footer CTA
    cta_font = _load_font(28, bold=True)
    cta = "SWIPE LINK IN BIO FOR FULL STORIES →"
    draw.text((MARGIN, H - MARGIN - 40), cta, fill=ACCENT, font=cta_font)

    out_path.parent.mkdir(parents=True, exist_ok=True)
    img.save(out_path, format="PNG", optimize=True)
    log.info("rendered card to %s", out_path)
    return out_path
