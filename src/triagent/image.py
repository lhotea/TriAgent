from __future__ import annotations

import datetime as dt
import hashlib
import logging
from pathlib import Path

from PIL import Image, ImageDraw, ImageEnhance, ImageFilter, ImageFont

from .summarizer import DailyBrief

log = logging.getLogger(__name__)

W, H = 1080, 1350  # Instagram portrait 4:5
MARGIN = 72
BG_TOP = (15, 23, 42)       # slate-900
BG_BOTTOM = (6, 78, 130)    # deep ocean
ACCENT = (250, 204, 21)     # amber
TEXT = (248, 250, 252)
MUTED = (148, 163, 184)


def pick_background(backgrounds_dir: Path, seed: str) -> Path | None:
    """Choose a background photo deterministically from a directory.

    Seeded by date so retries within a day reuse the same image, and so the
    rotation is predictable rather than random. Returns None when the directory
    is absent or empty, in which case callers fall back to the gradient.

    Photos are user-supplied on purpose: dropping a publisher's press image or
    an unlicensed stock photo into a commercial post is a rights problem, and
    that's not a call this code should make on someone's behalf.
    """
    if not backgrounds_dir.is_dir():
        return None
    files = sorted(
        p
        for p in backgrounds_dir.iterdir()
        if p.suffix.lower() in {".jpg", ".jpeg", ".png", ".webp"}
    )
    if not files:
        return None
    idx = int(hashlib.sha256(seed.encode()).hexdigest(), 16) % len(files)
    return files[idx]


def _cover(img: Image.Image, size: tuple[int, int]) -> Image.Image:
    """Scale-and-crop to fill `size` without distorting aspect ratio."""
    tw, th = size
    scale = max(tw / img.width, th / img.height)
    resized = img.resize(
        (max(1, int(img.width * scale)), max(1, int(img.height * scale))),
        Image.LANCZOS,
    )
    left = (resized.width - tw) // 2
    top = (resized.height - th) // 2
    return resized.crop((left, top, left + tw, top + th))


def _background(size: tuple[int, int], photo: Path | None) -> Image.Image:
    """Photo background with a legibility scrim, or the gradient fallback.

    Text over an unmodified photo is unreadable at feed size, so the photo is
    darkened and blurred slightly and a vertical scrim is laid over it. The
    point is a backdrop, not a subject.
    """
    if photo is None:
        return _vertical_gradient(size, BG_TOP, BG_BOTTOM)
    try:
        with Image.open(photo) as src:
            base = _cover(src.convert("RGB"), size)
    except Exception as exc:
        log.warning("could not use background %s (%s) — using gradient", photo, exc)
        return _vertical_gradient(size, BG_TOP, BG_BOTTOM)

    base = base.filter(ImageFilter.GaussianBlur(radius=3))
    base = ImageEnhance.Brightness(base).enhance(0.55)

    # Vertical scrim: darkest at top and bottom where the text sits.
    scrim = Image.new("L", size)
    sd = ImageDraw.Draw(scrim)
    h = size[1]
    for y in range(h):
        t = y / max(h - 1, 1)
        # Strong at the top (hook), strong at the bottom (CTA), lighter mid.
        alpha = int(215 * (1 - t) ** 1.4 + 165 * t**2.2)
        sd.line([(0, y), (size[0], y)], fill=min(alpha, 235))
    tint = Image.new("RGB", size, BG_TOP)
    return Image.composite(tint, base, scrim)


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


def render_card(
    brief: DailyBrief,
    brand_name: str,
    out_path: Path,
    background: Path | None = None,
) -> Path:
    img = _background((W, H), background)
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
    # Not "SWIPE" — this is a single static image, so there is nothing to swipe.
    # A CTA that describes an interaction the post doesn't support reads as
    # careless and costs credibility.
    cta = "FULL STORIES → LINK IN BIO"
    draw.text((MARGIN, H - MARGIN - 40), cta, fill=ACCENT, font=cta_font)

    out_path.parent.mkdir(parents=True, exist_ok=True)
    img.save(out_path, format="PNG", optimize=True)
    log.info("rendered card to %s", out_path)
    return out_path


# Reel canvas: 9:16 for full-screen playback.
REEL_W, REEL_H = 1080, 1920


def render_reel_frame(
    brief: DailyBrief,
    brand_name: str,
    out_path: Path,
    background: Path | None = None,
) -> Path:
    """Render the 9:16 still that the Reel animates.

    Composed at Reel dimensions rather than letterboxing the 4:5 card: bars
    down the sides of a Reel look like a reposted image and read as low effort,
    which is the opposite of the point.
    """
    img = _background((REEL_W, REEL_H), background)
    draw = ImageDraw.Draw(img)

    # Keep the first and last ~15% clear — Instagram overlays its own UI there.
    safe_top = int(REEL_H * 0.16)
    safe_bottom = int(REEL_H * 0.86)

    draw.rectangle([MARGIN, safe_top - 40, MARGIN + 120, safe_top - 32], fill=ACCENT)

    header_font = _load_font(34, bold=True)
    date_font = _load_font(26)
    today = dt.datetime.now(dt.timezone.utc).strftime("%a · %b %d").upper()
    draw.text((MARGIN, safe_top), brand_name.upper(), fill=TEXT, font=header_font)
    dw = draw.textlength(today, font=date_font)
    draw.text((REEL_W - MARGIN - dw, safe_top + 6), today, fill=MUTED, font=date_font)

    # Hook — the reason someone stops scrolling.
    hook_font = _load_font(82, bold=True)
    y = safe_top + 120
    for line in _wrap(draw, brief.hook, hook_font, REEL_W - 2 * MARGIN)[:4]:
        draw.text((MARGIN, y), line, fill=TEXT, font=hook_font)
        y += 96

    y += 30
    draw.line([(MARGIN, y), (REEL_W - MARGIN, y)], fill=ACCENT, width=4)
    y += 48

    num_font = _load_font(46, bold=True)
    title_font = _load_font(38, bold=True)
    src_font = _load_font(26)

    for idx, h in enumerate(brief.headlines[:3], start=1):
        if y > safe_bottom - 160:
            break
        draw.text((MARGIN, y), f"{idx:02}", fill=ACCENT, font=num_font)
        ty = y
        for line in _wrap(draw, h.title, title_font, REEL_W - 2 * MARGIN - 100)[:2]:
            draw.text((MARGIN + 100, ty), line, fill=TEXT, font=title_font)
            ty += 46
        draw.text((MARGIN + 100, ty + 6), h.source[:40].upper(), fill=MUTED, font=src_font)
        y = ty + 72

    cta_font = _load_font(30, bold=True)
    draw.text(
        (MARGIN, safe_bottom),
        "FULL STORIES → LINK IN BIO",
        fill=ACCENT,
        font=cta_font,
    )

    out_path.parent.mkdir(parents=True, exist_ok=True)
    img.save(out_path, format="PNG", optimize=True)
    log.info("rendered reel frame to %s", out_path)
    return out_path
