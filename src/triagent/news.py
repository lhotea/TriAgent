from __future__ import annotations

import datetime as dt
import logging
import re
from dataclasses import dataclass
from email.utils import parsedate_to_datetime

import feedparser

log = logging.getLogger(__name__)

_TAG_RE = re.compile(r"<[^>]+>")
_WS_RE = re.compile(r"\s+")


def _clean(html: str) -> str:
    return _WS_RE.sub(" ", _TAG_RE.sub(" ", html or "")).strip()


def _parse_dt(entry) -> dt.datetime:
    for key in ("published", "updated", "created"):
        raw = entry.get(key)
        if not raw:
            continue
        try:
            parsed = parsedate_to_datetime(raw)
            if parsed.tzinfo is None:
                parsed = parsed.replace(tzinfo=dt.timezone.utc)
            return parsed.astimezone(dt.timezone.utc)
        except (TypeError, ValueError):
            continue
    return dt.datetime.now(dt.timezone.utc)


@dataclass
class NewsItem:
    title: str
    summary: str
    url: str
    source: str
    published: dt.datetime

    def age_hours(self, now: dt.datetime | None = None) -> float:
        now = now or dt.datetime.now(dt.timezone.utc)
        return (now - self.published).total_seconds() / 3600


def fetch_recent(
    feed_urls: list[str],
    *,
    max_age_hours: float = 36,
    per_feed_limit: int = 10,
) -> list[NewsItem]:
    now = dt.datetime.now(dt.timezone.utc)
    items: list[NewsItem] = []
    seen_urls: set[str] = set()

    for feed_url in feed_urls:
        try:
            parsed = feedparser.parse(feed_url)
        except Exception as exc:
            log.warning("feed fetch failed for %s: %s", feed_url, exc)
            continue

        source = parsed.feed.get("title") or feed_url
        for entry in parsed.entries[:per_feed_limit]:
            url = entry.get("link")
            if not url or url in seen_urls:
                continue
            published = _parse_dt(entry)
            if (now - published).total_seconds() / 3600 > max_age_hours:
                continue

            title = _clean(entry.get("title", ""))
            summary = _clean(entry.get("summary", entry.get("description", "")))
            if not title:
                continue
            seen_urls.add(url)
            items.append(
                NewsItem(
                    title=title,
                    summary=summary[:800],
                    url=url,
                    source=source,
                    published=published,
                )
            )

    items.sort(key=lambda i: i.published, reverse=True)
    log.info("fetched %d items from %d feeds", len(items), len(feed_urls))
    return items
