from __future__ import annotations

import json
import logging
from dataclasses import dataclass

from .config import Settings
from .image import render_card
from .monetization import assemble_caption
from .news import fetch_recent
from .publisher import InstagramPublisher
from .summarizer import DailyBrief, Summarizer

log = logging.getLogger(__name__)


@dataclass
class RunResult:
    brief: DailyBrief | None
    caption: str
    image_url: str
    media_id: str | None  # None in dry-run / build-only mode


def _image_url(settings: Settings) -> str:
    return f"{settings.public_image_base_url}/{settings.image_path.name}"


def build(settings: Settings) -> RunResult:
    """Fetch news, build brief, render card, write caption next to card.

    This is the expensive side — Claude + RSS happen here. We persist the
    caption to disk so a later publish step can post exactly what was built,
    without regenerating (which would produce a different caption than the
    one already baked into the rendered image).
    """
    items = fetch_recent(settings.feeds, max_age_hours=36, per_feed_limit=10)
    if not items:
        raise RuntimeError("no fresh triathlon news found in the last 36 hours")

    top_items = items[: max(settings.max_headlines * 2, 12)]

    summarizer = Summarizer(api_key=settings.anthropic_api_key, model=settings.model)
    brief = summarizer.build_brief(top_items, brand_name=settings.brand_name)

    render_card(brief, brand_name=settings.brand_name, out_path=settings.image_path)

    caption = assemble_caption(
        brief,
        affiliate_urls=settings.affiliate_urls,
        brand_handle=settings.brand_handle,
    )

    caption_path = settings.image_path.with_name("caption.txt")
    caption_path.write_text(caption, encoding="utf-8")
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

    caption = caption_path.read_text(encoding="utf-8")
    image_url = _image_url(settings)

    publisher = InstagramPublisher(
        ig_user_id=settings.ig_user_id, access_token=settings.ig_access_token
    )
    media_id = publisher.publish(image_url=image_url, caption=caption)
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

    publisher = InstagramPublisher(
        ig_user_id=settings.ig_user_id, access_token=settings.ig_access_token
    )
    media_id = publisher.publish(image_url=result.image_url, caption=result.caption)
    return RunResult(
        brief=result.brief,
        caption=result.caption,
        image_url=result.image_url,
        media_id=media_id,
    )
