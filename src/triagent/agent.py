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
    brief: DailyBrief
    caption: str
    image_url: str
    media_id: str | None  # None in dry-run mode


def run(settings: Settings, *, dry_run: bool = False) -> RunResult:
    items = fetch_recent(settings.feeds, max_age_hours=36, per_feed_limit=10)
    if not items:
        raise RuntimeError("no fresh triathlon news found in the last 36 hours")

    # Cap the set we send to Claude — more than ~12 items dilutes the ranking.
    top_items = items[: max(settings.max_headlines * 2, 12)]

    summarizer = Summarizer(api_key=settings.anthropic_api_key, model=settings.model)
    brief = summarizer.build_brief(top_items, brand_name=settings.brand_name)

    render_card(brief, brand_name=settings.brand_name, out_path=settings.image_path)

    caption = assemble_caption(
        brief,
        affiliate_urls=settings.affiliate_urls,
        brand_handle=settings.brand_handle,
    )

    image_url = f"{settings.public_image_base_url}/{settings.image_path.name}"

    if dry_run:
        log.info("dry-run — skipping Instagram publish")
        log.info("caption:\n%s", caption)
        log.info("brief:\n%s", json.dumps(brief.model_dump(), indent=2))
        return RunResult(brief=brief, caption=caption, image_url=image_url, media_id=None)

    publisher = InstagramPublisher(
        ig_user_id=settings.ig_user_id, access_token=settings.ig_access_token
    )
    media_id = publisher.publish(image_url=image_url, caption=caption)
    return RunResult(brief=brief, caption=caption, image_url=image_url, media_id=media_id)
