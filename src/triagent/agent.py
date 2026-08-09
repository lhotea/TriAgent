from __future__ import annotations

import datetime as dt
import json
import logging
from dataclasses import dataclass
from pathlib import Path

from .config import ASSETS_DIR, Settings
from .image import pick_background, render_card, render_reel_frame
from .monetization import assemble_caption
from .news import fetch_recent_widening
from .publisher import InstagramPublisher
from .review import render_review_page
from .video import build_reel
from .summarizer import DailyBrief, Summarizer

log = logging.getLogger(__name__)

# Drop your own licensed photos here; empty means the gradient is used.
BACKGROUNDS_DIR = ASSETS_DIR / "backgrounds"


@dataclass
class RunResult:
    brief: DailyBrief | None
    caption: str
    image_url: str
    media_id: str | None  # None in dry-run / build-only mode


def _asset_url(settings: Settings, filename: str) -> str:
    if not settings.public_image_base_url:
        # Build mode can run without this — it's only informational until publish.
        return f"<unset>/{filename}"
    return f"{settings.public_image_base_url}/{filename}"


def _image_url(settings: Settings) -> str:
    return _asset_url(settings, settings.image_path.name)


def _do_publish(settings: Settings, image_url: str, caption: str) -> str:
    """Shared helper: create publisher, wait for the asset, publish."""
    assert settings.ig_user_id and settings.ig_access_token  # checked by caller
    publisher = InstagramPublisher(
        ig_user_id=settings.ig_user_id, access_token=settings.ig_access_token
    )

    if settings.post_format == "reel":
        video_url = _asset_url(settings, "daily.mp4")
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
    items = fetch_recent_widening(settings.feeds, per_feed_limit=10)
    if not items:
        raise RuntimeError(
            "no triathlon news found in any time window — every feed is likely "
            "unreachable. Run `python -m triagent --mode feedcheck` to see which."
        )

    top_items = items[: max(settings.max_headlines * 2, 12)]

    summarizer = Summarizer(api_key=settings.anthropic_api_key, model=settings.model)
    brief = summarizer.build_brief(top_items, brand_name=settings.brand_name)

    seed = dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%d")
    background = pick_background(BACKGROUNDS_DIR, seed)
    if background:
        log.info("using background photo %s", background.name)

    render_card(
        brief,
        brand_name=settings.brand_name,
        out_path=settings.image_path,
        background=background,
    )

    if settings.post_format == "reel":
        frame = settings.image_path.with_name("reel_frame.png")
        render_reel_frame(
            brief,
            brand_name=settings.brand_name,
            out_path=frame,
            background=background,
        )
        audio = Path(settings.reel_audio) if settings.reel_audio else None
        build_reel(
            frame,
            settings.image_path.with_name("daily.mp4"),
            audio_path=audio,
            duration=settings.reel_seconds,
        )

    caption = assemble_caption(
        brief,
        affiliate_urls=settings.affiliate_urls,
        brand_handle=settings.brand_handle,
    )

    caption_path = settings.image_path.with_name("caption.txt")
    caption_path.write_text(caption, encoding="utf-8")

    # Human-postable fallback: card + caption on one page, published alongside
    # the image. Keeps the pipeline useful when API publishing is unavailable.
    # Link the actual sources: the post's CTA points here for "full stories",
    # so this page has to be able to deliver them. Use the items the brief was
    # built from — Claude rewrites headlines, so its output can't be mapped
    # back to URLs reliably, but these are exactly what it read.
    render_review_page(
        caption,
        brand_name=settings.brand_name,
        out_path=settings.image_path.with_name("index.html"),
        stories=top_items[: settings.max_headlines],
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
        mp4 = settings.image_path.with_name("daily.mp4")
        if not mp4.exists():
            raise RuntimeError(f"no rendered reel at {mp4}; run build step first")

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
