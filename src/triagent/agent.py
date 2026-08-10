from __future__ import annotations

import datetime as dt
import json
import logging
from dataclasses import dataclass
from pathlib import Path

from .config import ASSETS_DIR, Settings
from .image import (
    REEL_H,
    REEL_W,
    pick_background,
    render_card,
    render_slides,
)
from .imagery import resolve_background
from .monetization import assemble_caption
from .news import fetch_recent_widening, prioritize
from .publisher import InstagramPublisher
from .review import render_review_page
from .video import build_slideshow, pick_music
from .summarizer import DailyBrief, Summarizer

log = logging.getLogger(__name__)

# Drop your own licensed photos here; empty means the gradient is used.
BACKGROUNDS_DIR = ASSETS_DIR / "backgrounds"
# Drop licensed audio here; empty means a silent Reel.
MUSIC_DIR = ASSETS_DIR / "music"


@dataclass
class RunResult:
    brief: DailyBrief | None
    caption: str
    image_url: str
    media_id: str | None  # None in dry-run / build-only mode


def _today() -> str:
    return dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%d")


def dated_name(stem: str, suffix: str, when: str | None = None) -> str:
    """Return e.g. 'daily-2026-08-09.png'.

    Publishing under a stable filename is a real correctness bug, not just
    untidiness: the URL handed to Meta never changes, so the CDN in front of
    GitHub Pages can answer with yesterday's bytes and Instagram will post the
    wrong picture. A distinct URL per day removes any chance of that, and
    leaves a dated archive as a side effect.
    """
    return f"{stem}-{when or _today()}{suffix}"


def _asset_url(settings: Settings, filename: str) -> str:
    if not settings.public_image_base_url:
        # Build mode can run without this — it's only informational until publish.
        return f"<unset>/{filename}"
    return f"{settings.public_image_base_url}/{filename}"


def _image_url(settings: Settings) -> str:
    return _asset_url(settings, dated_name("daily", ".png"))


def _stories_for_page(brief, items) -> list:
    """Story links for the review page, in the model's ranked order.

    Previously the page listed the most *recent* items, but the model ranks by
    engagement, not recency — so the lead story (the one the headline is about)
    could be absent from its own list. Asking the model for each headline's
    source url fixes that directly.

    Model-supplied urls are validated against the items actually fetched. A url
    that wasn't in the input is dropped rather than published, since a
    fabricated link is worse than a missing one. Anything dropped is backfilled
    from the fetched items so the page is never short.
    """
    by_url = {i.url: i for i in items}
    stories, seen = [], set()

    for h in getattr(brief, "headlines", []):
        url = (getattr(h, "source_url", "") or "").strip()
        if url in by_url and url not in seen:
            seen.add(url)
            item = by_url[url]
            # Prefer the model's rewritten title; keep the real publication.
            stories.append(
                type("Story", (), {"title": h.title, "url": url, "source": item.source})()
            )
        elif url:
            log.warning("dropping headline with unrecognised source_url: %s", url)

    for item in items:
        if len(stories) >= 6:
            break
        if item.url not in seen:
            seen.add(item.url)
            stories.append(item)

    return stories[:6]


def _do_publish(settings: Settings, image_url: str, caption: str) -> str:
    """Shared helper: create publisher, wait for the asset, publish."""
    assert settings.ig_user_id and settings.ig_access_token  # checked by caller
    publisher = InstagramPublisher(
        ig_user_id=settings.ig_user_id, access_token=settings.ig_access_token
    )

    if settings.post_format == "carousel":
        urls = [
            _asset_url(settings, f"{dated_name('daily', '')}-{n}.png")
            for n in range(1, settings.carousel_slides + 1)
        ]
        for url in urls:
            log.info("waiting for slide URL: %s", url)
            publisher.wait_for_image(url)
        return publisher.publish_carousel(image_urls=urls, caption=caption)

    if settings.post_format == "reel":
        video_url = _asset_url(settings, dated_name("daily", ".mp4"))
        log.info("waiting for video URL to be reachable: %s", video_url)
        publisher.wait_for_image(video_url)
        # Cover frame is the still card, so the feed thumbnail matches the post.
        return publisher.publish_reel(
            video_url=video_url, caption=caption, cover_url=image_url
        )

    log.info("waiting for image URL to be reachable: %s", image_url)
    publisher.wait_for_image(image_url)
    return publisher.publish(image_url=image_url, caption=caption)


def build(settings: Settings) -> RunResult:
    """Fetch news, build brief, render card, write caption next to card.

    This is the expensive side — Claude + RSS happen here. We persist the
    caption to disk so a later publish step can post exactly what was built,
    without regenerating (which would produce a different caption than the
    one already baked into the rendered image).
    """
    settings.require_build_config()
    assert settings.anthropic_api_key  # checked immediately above

    items = fetch_recent_widening(settings.feeds, per_feed_limit=10)
    if not items:
        raise RuntimeError(
            "no triathlon news found in any time window — every feed is likely "
            "unreachable. Run `python -m triagent --mode feedcheck` to see which."
        )

    top_items = prioritize(items)[: max(settings.max_headlines * 2, 12)]

    summarizer = Summarizer(api_key=settings.anthropic_api_key, model=settings.model)
    brief = summarizer.build_brief(top_items, brand_name=settings.brand_name)

    seed = dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%d")
    # Claude chose the scene; resolve_background renders or finds it, falling
    # back through stock search to the local directory.
    background = resolve_background(
        getattr(brief, "image_query", "") or "",
        settings.image_path.with_name("background.jpg"),
        gen_key=settings.image_gen_api_key,
        stock_key=settings.stock_api_key,
        fallback=pick_background(BACKGROUNDS_DIR, seed),
    )

    render_card(
        brief,
        brand_name=settings.brand_name,
        out_path=settings.image_path,
        background=background,
    )

    if settings.post_format == "carousel":
        render_slides(
            brief,
            brand_name=settings.brand_name,
            out_dir=settings.image_path.parent,
            stem=dated_name("daily", "").rstrip("-") or "daily",
            background=background,
            count=settings.carousel_slides,
        )

    if settings.post_format == "reel":
        # Sequence the same slides into video. Instagram allows audio on video
        # only, so this is the only way a post carries several stories AND
        # music — a carousel of images is always silent.
        frames = render_slides(
            brief,
            brand_name=settings.brand_name,
            out_dir=settings.image_path.parent,
            stem="reel_frame",
            background=background,
            count=settings.carousel_slides,
            size=(REEL_W, REEL_H),
        )
        audio = (
            Path(settings.reel_audio)
            if settings.reel_audio
            else pick_music(MUSIC_DIR, seed)
        )
        build_slideshow(
            frames,
            settings.image_path.with_name("daily.mp4"),
            audio_path=audio,
            seconds_per_slide=settings.reel_seconds,
        )

    caption = assemble_caption(
        brief,
        affiliate_urls=settings.affiliate_urls,
        brand_handle=settings.brand_handle,
    )

    caption_path = settings.image_path.with_name("caption.txt")
    caption_path.write_text(caption, encoding="utf-8")

    # Dated copies are what actually get published; the undated names stay for
    # artifacts and local inspection.
    dated_png = settings.image_path.with_name(dated_name("daily", ".png"))
    dated_png.write_bytes(settings.image_path.read_bytes())
    mp4 = settings.image_path.with_name("daily.mp4")
    if mp4.exists():
        mp4.with_name(dated_name("daily", ".mp4")).write_bytes(mp4.read_bytes())

    # Human-postable fallback, and the link-in-bio destination: card, caption
    # and the day's stories as real links. Ordered by the model's ranking so
    # the lead story — the one the headline is about — is always first.
    render_review_page(
        brand_name=settings.brand_name,
        out_path=settings.image_path.with_name("index.html"),
        stories=_stories_for_page(brief, top_items),
        image_name=dated_png.name,
    )
    log.info("caption written to %s", caption_path)

    return RunResult(
        brief=brief, caption=caption, image_url=_image_url(settings), media_id=None
    )


def publish_from_build(settings: Settings) -> RunResult:
    """Publish the already-built card and caption to Instagram."""
    caption_path = settings.image_path.with_name("caption.txt")
    if not caption_path.exists():
        raise RuntimeError(
            f"no prebuilt caption at {caption_path}; run build step first"
        )
    if not settings.image_path.exists():
        raise RuntimeError(
            f"no rendered card at {settings.image_path}; run build step first"
        )

    if settings.post_format == "reel":
        mp4 = settings.image_path.with_name(dated_name("daily", ".mp4"))
        if not mp4.exists():
            raise RuntimeError(f"no rendered reel at {mp4}; run build step first")

    if settings.post_format == "carousel":
        for n in range(1, settings.carousel_slides + 1):
            slide = settings.image_path.with_name(f"{dated_name('daily', '')}-{n}.png")
            if not slide.exists():
                raise RuntimeError(f"no rendered slide at {slide}; run build step first")

    settings.require_publish_config()

    caption = caption_path.read_text(encoding="utf-8")
    image_url = _image_url(settings)

    media_id = _do_publish(settings, image_url, caption)
    return RunResult(brief=None, caption=caption, image_url=image_url, media_id=media_id)


def run(settings: Settings, *, dry_run: bool = False) -> RunResult:
    """Full single-process pipeline (build + publish)."""
    result = build(settings)

    if dry_run:
        log.info("dry-run — skipping Instagram publish")
        log.info("caption:\n%s", result.caption)
        if result.brief:
            log.info("brief:\n%s", json.dumps(result.brief.model_dump(), indent=2))
        return result

    settings.require_publish_config()

    media_id = _do_publish(settings, result.image_url, result.caption)
    return RunResult(
        brief=result.brief,
        caption=result.caption,
        image_url=result.image_url,
        media_id=media_id,
    )
