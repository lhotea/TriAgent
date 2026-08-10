from __future__ import annotations

import datetime as dt
import hashlib
import logging
from pathlib import Path

from PIL import Image, ImageDraw, ImageEnhance, ImageFilter, ImageFont

from .summarizer import DailyBrief

log = logging.getLogger(__name__)

W, H = 1080, 1350  # Instagram portrait 4:5
MARGIN = 64
BG_TOP = (10, 10, 12)
BG_BOTTOM = (0, 0, 0)
BLACK = (0, 0, 0)
# Sampled from the brand logo's accent dot so card and mark agree.
ACCENT = (110, 223, 171)
TEXT = (255, 255, 255)
MUTED = (150, 155, 160)

# Fraction of the canvas given to the photo. The headline occupies the rest and
# is allowed to run to the bottom edge.
IMAGE_FRACTION = 0.58

# "LINK IN BIO" alone says nothing about what's there. Naming the payoff is
# what earns the tap.
CTA_TEXT = "MORE TRIATHLON NEWS \u2192 LINK IN BIO"

# Height kept clear at the bottom of every slide for the mark and CTA pill.
FOOTER_BAND = 150


LOGO_PATH = Path(__file__).resolve().parents[2] / "assets" / "images" / "logo.png"
_LOGO_CACHE: dict[str, Image.Image | None] = {}


def _main_mark_bbox(alpha: Image.Image) -> tuple[int, int, int, int] | None:
    """Bounding box of the dominant shape in an alpha mask.

    The source artwork carries a small decorative sparkle in one corner. A
    plain getbbox() includes it, which at watermark size pushes the actual mark
    off-centre and shrinks it. Projecting the mask onto the x-axis splits the
    image into runs of occupied columns; the run holding the most alpha is the
    mark, and anything detached from it is ignored.
    """
    w, h = alpha.size
    cols = [0] * w
    px = alpha.load()
    for x in range(w):
        total = 0
        for y in range(h):
            total += px[x, y]
        cols[x] = total

    runs, start = [], None
    for x, weight in enumerate(cols):
        if weight and start is None:
            start = x
        elif not weight and start is not None:
            runs.append((start, x))
            start = None
    if start is not None:
        runs.append((start, w))
    if not runs:
        return None

    x0, x1 = max(runs, key=lambda r: sum(cols[r[0] : r[1]]))
    ys = [y for y in range(h) if any(px[x, y] for x in range(x0, x1))]
    if not ys:
        return None
    return (x0, min(ys), x1, max(ys) + 1)


def load_logo(path: Path = LOGO_PATH) -> Image.Image | None:
    """Load the brand mark as RGBA with its dark backing plate removed.

    The supplied file is RGBA but fully opaque — the mark sits on a dark green
    plate rather than on transparency. Pasting it straight onto the black card
    would show that plate as a rectangle. Since the artwork is light-on-dark,
    luminance makes a serviceable alpha channel: bright strokes stay, the plate
    falls away.

    Cached because it is composited onto every slide.
    """
    key = str(path)
    if key in _LOGO_CACHE:
        return _LOGO_CACHE[key]

    logo: Image.Image | None = None
    try:
        with Image.open(path) as raw:
            src = raw.convert("RGBA")
        lum = src.convert("L")
        # The histogram separates cleanly: ~95% of pixels are the plate at
        # luminance <=41, the mark sits above 100. Keying in that gap removes
        # the plate while the ramp keeps anti-aliased stroke edges smooth.
        alpha = lum.point(lambda v: 0 if v < 60 else min(255, int((v - 60) * 5.1)))
        src.putalpha(alpha)
        logo = src.crop(_main_mark_bbox(alpha) or alpha.getbbox() or (0, 0, *src.size))
    except Exception as exc:
        log.warning("could not load logo %s (%s) — using drawn mark", path, exc)

    _LOGO_CACHE[key] = logo
    return logo


def paste_logo(
    img: Image.Image, logo: Image.Image | None, *, cx: int, cy: int, height: int
) -> bool:
    """Composite the logo centred on (cx, cy) at the given height.

    Returns False when there is no logo, so callers can fall back to the drawn
    mark rather than silently rendering nothing.
    """
    if logo is None:
        return False
    w = max(1, int(logo.width * (height / logo.height)))
    resized = logo.resize((w, height), Image.LANCZOS)
    img.paste(resized, (int(cx - w / 2), int(cy - height / 2)), resized)
    return True


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


def _load_font(
    size: int, bold: bool = False, condensed: bool = False
) -> ImageFont.FreeTypeFont:
    """Load a font, preferring a condensed face for headlines.

    The reference style depends on a heavy condensed face — it's what lets a
    14-word headline fill three lines at poster size. Condensed faces aren't
    guaranteed on every machine, so this degrades to regular bold, which still
    works because the all-caps setting and tight leading do much of the work.
    """
    if condensed:
        for path in (
            "/usr/share/fonts/truetype/dejavu/DejaVuSansCondensed-Bold.ttf",
            "/usr/share/fonts/truetype/liberation/LiberationSansNarrow-Bold.ttf",
            "/usr/share/fonts/truetype/liberation2/LiberationSansNarrow-Bold.ttf",
            "/System/Library/Fonts/Supplemental/Impact.ttf",
        ):
            if Path(path).exists():
                return ImageFont.truetype(path, size)
        # Fall through to regular bold.

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



def _emphasis_words(hook: str, emphasis: str) -> set[int]:
    """Indices of the words in `hook` covered by the `emphasis` substring.

    The model is asked for an exact substring, but models paraphrase. Matching
    case-insensitively on word sequence rather than raw character offsets means
    a near-miss degrades to no emphasis instead of raising.
    """
    words = hook.split()
    emph = emphasis.strip().split()
    if not emph:
        return set()
    lowered = [w.lower().strip(".,!?:;\u2019'\"") for w in words]
    target = [w.lower().strip(".,!?:;\u2019'\"") for w in emph]
    for start in range(len(lowered) - len(target) + 1):
        if lowered[start : start + len(target)] == target:
            return set(range(start, start + len(target)))
    return set()


def _draw_two_tone(
    draw: ImageDraw.ImageDraw,
    words: list[str],
    highlight: set[int],
    font: ImageFont.FreeTypeFont,
    *,
    x: int,
    y: int,
    max_width: int,
    line_height: int,
    max_lines: int,
) -> int:
    """Draw words with per-word colour, wrapping. Returns the final y.

    Wrapping has to happen at word level rather than per line because the
    accent span can start mid-line — the reference style colours the entity,
    not whole lines.
    """
    space = draw.textlength(" ", font=font)
    cx, cy, line = x, y, 0
    for i, word in enumerate(words):
        w = draw.textlength(word, font=font)
        if cx > x and cx + w > x + max_width:
            line += 1
            if line >= max_lines:
                break
            cx, cy = x, cy + line_height
        draw.text((cx, cy), word, fill=ACCENT if i in highlight else TEXT, font=font)
        cx += w + space
    return cy + line_height


def _fit_headline_font(
    draw: ImageDraw.ImageDraw,
    words: list[str],
    max_width: int,
    max_lines: int,
    max_height: int | None = None,
) -> tuple[ImageFont.FreeTypeFont, int]:
    """Pick the largest headline size that fits in max_lines.

    Sized down from very large rather than up from small: the reference look
    depends on the headline being as big as it can possibly be, so the right
    answer is always the largest size that still fits.
    """
    for size in range(118, 52, -4):
        font = _load_font(size, bold=True, condensed=True)
        space = draw.textlength(" ", font=font)
        cx, lines = 0.0, 1
        for word in words:
            w = draw.textlength(word, font=font)
            if cx > 0 and cx + w > max_width:
                lines += 1
                cx = 0.0
            cx += w + space
        if lines > max_lines:
            continue
        # Height matters as much as line count: four lines that fit the budget
        # can still run into the footer, which is where the CTA and mark live.
        if max_height is not None and lines * int(size * 0.92) > max_height:
            continue
        return font, size
    return _load_font(52, bold=True, condensed=True), 52


def _logo_mark(draw: ImageDraw.ImageDraw, cx: int, cy: int, r: int, label: str) -> None:
    """Small circular brand mark, matching the reference's corner watermark."""
    draw.ellipse([cx - r, cy - r, cx + r, cy + r], outline=ACCENT, width=3)
    f = _load_font(int(r * 0.9), bold=True)
    tw = draw.textlength(label, font=f)
    draw.text((cx - tw / 2, cy - r * 0.62), label, fill=TEXT, font=f)


def _draw_footer(
    img: Image.Image,
    draw: ImageDraw.ImageDraw,
    brand_name: str,
    logo: Image.Image | None,
    *,
    show_cta: bool = True,
    size: tuple[int, int] | None = None,
) -> None:
    """Brand mark bottom-left, CTA pill centred. Shared by every slide."""
    w, h = size or (W, H)
    if not paste_logo(img, logo, cx=MARGIN + 34, cy=h - MARGIN - 20, height=64):
        _logo_mark(draw, MARGIN + 30, h - MARGIN - 20, 30, brand_name[:1].upper() or "T")

    if not show_cta:
        return

    # Shrink to fit rather than overflow — the wording matters more than the size.
    size = 26
    font = _load_font(size, bold=True)
    while size > 16 and draw.textlength(CTA_TEXT, font=font) > w - 2 * MARGIN - 150:
        size -= 2
        font = _load_font(size, bold=True)

    tw = draw.textlength(CTA_TEXT, font=font)
    pill_w, pill_h = tw + 52, 52
    px = (w - pill_w) / 2
    py = h - MARGIN - 46
    draw.rounded_rectangle(
        [px, py, px + pill_w, py + pill_h], radius=26, outline=TEXT, width=2
    )
    draw.text((px + 26, py + (pill_h - size) / 2 - 3), CTA_TEXT, fill=TEXT, font=font)


def render_slide(
    brief: DailyBrief,
    index: int,
    brand_name: str,
    out_path: Path,
    background: Path | None = None,
    size: tuple[int, int] | None = None,
) -> Path:
    """Render one carousel slide.

    Slide 0 is the lead: photo band plus the oversized two-tone hook. Later
    slides carry one story each — a large index, the headline, and the
    one-liner — on black. Keeping the photo to the first slide is deliberate:
    reusing the same image behind every slide reads as padding, and the
    supporting stories are quicker to scan as type.
    """
    w, h = size or (W, H)

    if index == 0:
        if (w, h) == (REEL_W, REEL_H):
            return render_reel_frame(brief, brand_name, out_path, background)
        return render_card(brief, brand_name, out_path, background)

    logo = load_logo()
    img = Image.new("RGB", (w, h), BLACK)
    draw = ImageDraw.Draw(img)

    headlines = brief.headlines[1:]
    story = headlines[index - 1] if index - 1 < len(headlines) else None

    brand_font = _load_font(30, bold=True)
    draw.text((MARGIN, MARGIN + 8), brand_name.upper(), fill=TEXT, font=brand_font)
    draw.line([(MARGIN, MARGIN + 62), (w - MARGIN, MARGIN + 62)], fill=ACCENT, width=3)

    if story is not None:
        num_font = _load_font(150, bold=True, condensed=True)
        draw.text((MARGIN, MARGIN + 110), f"{index + 1:02}", fill=ACCENT, font=num_font)

        _, fitted = _fit_headline_font(
            draw, story.title.upper().split(), w - 2 * MARGIN, 5
        )
        pt = min(fitted, 82)
        title_font = _load_font(pt, bold=True, condensed=True)
        y = MARGIN + 300
        for line in _wrap(draw, story.title.upper(), title_font, w - 2 * MARGIN)[:5]:
            draw.text((MARGIN, y), line, fill=TEXT, font=title_font)
            y += int(pt * 0.92)

        body_font = _load_font(34)
        y += 34
        for line in _wrap(draw, story.one_liner, body_font, w - 2 * MARGIN)[:4]:
            draw.text((MARGIN, y), line, fill=MUTED, font=body_font)
            y += 46

        src_font = _load_font(26, bold=True)
        draw.text((MARGIN, y + 20), story.source.upper(), fill=ACCENT, font=src_font)

    _draw_footer(img, draw, brand_name, logo, show_cta=True, size=(w, h))

    out_path.parent.mkdir(parents=True, exist_ok=True)
    img.save(out_path, format="PNG", optimize=True)
    log.info("rendered slide %d (%dx%d) to %s", index + 1, w, h, out_path)
    return out_path


def render_slides(
    brief: DailyBrief,
    brand_name: str,
    out_dir: Path,
    stem: str,
    background: Path | None = None,
    count: int = 3,
    size: tuple[int, int] | None = None,
) -> list[Path]:
    """Render `count` slides, returning their paths in order.

    `size` lets the same slides be produced at Reel dimensions, which is how a
    Reel can carry the carousel's stories *and* audio — Instagram allows sound
    on video only, never on a carousel.
    """
    return [
        render_slide(
            brief, i, brand_name, out_dir / f"{stem}-{i + 1}.png", background, size
        )
        for i in range(count)
    ]


def render_card(
    brief: DailyBrief,
    brand_name: str,
    out_path: Path,
    background: Path | None = None,
) -> Path:
    """Photo over black, with an oversized two-tone headline beneath it.

    Layout follows the reference account: the image is a full-bleed band across
    the top, a hairline rule with a centred mark divides it, and the headline
    fills the remaining space in condensed capitals — white, with the story's
    subject in accent colour.
    """
    logo = load_logo()
    img = Image.new("RGB", (W, H), BLACK)
    draw = ImageDraw.Draw(img)

    # --- photo band -------------------------------------------------------
    # With no photo the reference layout collapses — a 58% black band above the
    # headline just reads as empty. Fall back to a headline-led composition that
    # uses the whole canvas instead of reserving space for an image that isn't
    # there.
    has_photo = background is not None and background.exists()
    band_h = int(H * IMAGE_FRACTION) if has_photo else int(H * 0.20)

    if has_photo:
        try:
            with Image.open(background) as src:
                photo = _cover(src.convert("RGB"), (W, band_h))
            img.paste(photo, (0, 0))
        except Exception as exc:
            log.warning("could not use background %s (%s)", background, exc)
            has_photo = False
            band_h = int(H * 0.20)

    if has_photo:
        # Fade the photo into the black block so the seam isn't a hard edge.
        fade_h = 140
        fade = Image.new("L", (W, fade_h))
        fd = ImageDraw.Draw(fade)
        for i in range(fade_h):
            fd.line([(0, i), (W, i)], fill=int(255 * (i / fade_h) ** 1.5))
        img.paste(Image.new("RGB", (W, fade_h), BLACK), (0, band_h - fade_h), fade)
    else:
        # Headline-led: brand wordmark where the photo would have been.
        brand_font = _load_font(30, bold=True)
        draw.text((MARGIN, MARGIN + 8), brand_name.upper(), fill=TEXT, font=brand_font)
        today = dt.datetime.now(dt.timezone.utc).strftime("%d %b %Y").upper()
        dw = draw.textlength(today, font=_load_font(24))
        draw.text(
            (W - MARGIN - dw, MARGIN + 12), today, fill=MUTED, font=_load_font(24)
        )

    # --- divider rule with centred mark -----------------------------------
    rule_y = band_h
    mark_r = 26
    gap = 60
    draw.line([(MARGIN, rule_y), (W // 2 - gap, rule_y)], fill=TEXT, width=2)
    draw.line([(W // 2 + gap, rule_y), (W - MARGIN, rule_y)], fill=TEXT, width=2)
    if not paste_logo(img, logo, cx=W // 2, cy=rule_y, height=76):
        _logo_mark(draw, W // 2, rule_y, mark_r, brand_name[:1].upper() or "T")

    # --- headline ---------------------------------------------------------
    words = brief.hook.upper().split()
    highlight = _emphasis_words(brief.hook, getattr(brief, "hook_emphasis", "") or "")
    max_width = W - 2 * MARGIN
    # Without a photo there's room for a bigger headline over more lines.
    max_lines = 4 if has_photo else 6
    text_top = rule_y + 54
    # Reserve the footer band so the headline can never collide with the CTA
    # pill or the brand mark.
    headline_space = (H - FOOTER_BAND) - text_top
    font, size = _fit_headline_font(
        draw, words, max_width, max_lines, max_height=headline_space
    )
    line_height = int(size * 0.92)  # tight leading — lines almost touch
    _draw_two_tone(
        draw,
        words,
        highlight,
        font,
        x=MARGIN,
        y=text_top,
        max_width=max_width,
        line_height=line_height,
        max_lines=max_lines,
    )

    _draw_footer(img, draw, brand_name, logo)

    out_path.parent.mkdir(parents=True, exist_ok=True)
    img.save(out_path, format="PNG", optimize=True)
    log.info("rendered card to %s (headline %dpt)", out_path, size)
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
