from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from pathlib import Path

from .config import Settings
from .image import render_carousel
from .monetization import assemble_caption
from .news import fetch_recent
from .publisher import InstagramPublisher
from .summarizer import DailyBrief, Summarizer

log = logging.getLogger(__name__)


@dataclass
class RunResult:
    brief: DailyBrief | None
    caption: str
    image_urls: list[str]
    media_id: str | None  # None in dry-run / build-only mode


def _image_urls(settings: Settings) -> list[str]:
    paths = settings.slide_paths()
    if not settings.public_image_base_url:
        # Build mode: still useful as a placeholder; resolved at publish time.
        return [f"<unset>/{p.name}" for p in paths]
    return [f"{settings.public_image_base_url}/{p.name}" for p in paths]


def _caption_path(image_path: Path) -> Path:
    return image_path.with_name("caption.txt")


def build(settings: Settings) -> RunResult:
    """Fetch AI tool launches, build carousel brief, render slides, save caption.

    The expensive side — Claude + RSS happen here. Caption is persisted to disk
    so a later publish step can post exactly what was built (the renderer bakes
    the cover hook into pixels, so regenerating would diverge from the image).
    """
    items = fetch_recent(settings.feeds, max_age_hours=36, per_feed_limit=15)
    if not items:
        raise RuntimeError("no fresh AI tool launches found in the last 36 hours")

    # Cap input to keep token cost predictable.
    top_items = items[:24]

    summarizer = Summarizer(api_key=settings.anthropic_api_key, model=settings.model)
    brief = summarizer.build_brief(top_items, brand_name=settings.brand_name)

    slide_paths = settings.slide_paths()
    render_carousel(
        brief,
        brand_name=settings.brand_name,
        brand_handle=settings.brand_handle,
        out_paths=slide_paths,
    )

    caption = assemble_caption(
        brief,
        affiliate_map=settings.affiliate_map,
        newsletter_url=settings.newsletter_url,
        gumroad_url=settings.gumroad_url,
        brand_handle=settings.brand_handle,
    )

    caption_path = _caption_path(settings.image_path)
    caption_path.write_text(caption, encoding="utf-8")
    log.info("caption written to %s", caption_path)

    return RunResult(
        brief=brief, caption=caption, image_urls=_image_urls(settings), media_id=None
    )


def publish_from_build(settings: Settings) -> RunResult:
    """Publish the already-built carousel + caption to Instagram."""
    caption_path = _caption_path(settings.image_path)
    if not caption_path.exists():
        raise RuntimeError(
            f"no prebuilt caption at {caption_path}; run build step first"
        )
    slide_paths = settings.slide_paths()
    missing = [p for p in slide_paths if not p.exists()]
    if missing:
        raise RuntimeError(
            "slides missing on disk; run build step first. missing: "
            + ", ".join(str(p) for p in missing)
        )

    settings.require_publish_config()

    caption = caption_path.read_text(encoding="utf-8")
    image_urls = _image_urls(settings)

    assert settings.ig_user_id and settings.ig_access_token  # checked above
    publisher = InstagramPublisher(
        ig_user_id=settings.ig_user_id, access_token=settings.ig_access_token
    )
    media_id = publisher.publish_carousel(image_urls=image_urls, caption=caption)
    return RunResult(brief=None, caption=caption, image_urls=image_urls, media_id=media_id)


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
    assert settings.ig_user_id and settings.ig_access_token  # checked above
    publisher = InstagramPublisher(
        ig_user_id=settings.ig_user_id, access_token=settings.ig_access_token
    )
    media_id = publisher.publish_carousel(
        image_urls=result.image_urls, caption=result.caption
    )
    return RunResult(
        brief=result.brief,
        caption=result.caption,
        image_urls=result.image_urls,
        media_id=media_id,
    )
