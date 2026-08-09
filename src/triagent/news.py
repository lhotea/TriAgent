from __future__ import annotations

import datetime as dt
import logging
import re
from dataclasses import dataclass
from email.utils import parsedate_to_datetime

import feedparser
import requests
from tenacity import retry, retry_if_exception_type, stop_after_attempt, wait_exponential

log = logging.getLogger(__name__)

FEED_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/125.0 Safari/537.36"
    ),
    "Accept": "application/rss+xml, application/xml, text/xml, */*",
}

_TAG_RE = re.compile(r"<[^>]+>")
_WS_RE = re.compile(r"\s+")
_CTRL_RE = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")


def _clean(html: str) -> str:
    text = _WS_RE.sub(" ", _TAG_RE.sub(" ", html or ""))
    # Strip control characters that could carry prompt-injection payloads.
    return _CTRL_RE.sub("", text).strip()


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


@retry(
    retry=retry_if_exception_type((requests.Timeout, requests.ConnectionError)),
    stop=stop_after_attempt(3),
    wait=wait_exponential(min=2, max=15),
    reraise=True,
)
def _fetch_feed(feed_url: str) -> feedparser.Feed:
    # Many publishers reject the default "python-requests/x.y" agent outright —
    # tri247 returns 403 for it. Identify as a normal browser instead.
    resp = requests.get(feed_url, timeout=10, headers=FEED_HEADERS)
    resp.raise_for_status()
    return feedparser.parse(resp.content)


def check_feeds(feed_urls: list[str]) -> list[dict]:
    """Probe each feed and report status, without applying any age filter.

    Feed URLs rot — paths move, domains lapse, publishers start blocking bots.
    This gives a straight answer about which sources are actually usable
    instead of requiring a failed production run to find out.
    """
    report: list[dict] = []
    for feed_url in feed_urls:
        row: dict = {"url": feed_url}
        try:
            parsed = _fetch_feed(feed_url)
            entries = parsed.entries or []
            row["ok"] = bool(entries)
            row["entries"] = len(entries)
            row["title"] = parsed.feed.get("title", "")
            if entries:
                newest = max(_parse_dt(e) for e in entries)
                row["newest_age_hours"] = round(
                    (dt.datetime.now(dt.timezone.utc) - newest).total_seconds() / 3600, 1
                )
            else:
                row["error"] = "parsed but contained no entries"
        except Exception as exc:
            row["ok"] = False
            row["entries"] = 0
            row["error"] = f"{type(exc).__name__}: {exc}"
        report.append(row)
    return report


def fetch_recent(
    feed_urls: list[str],
    *,
    max_age_hours: float = 36,
    per_feed_limit: int = 10,
) -> list[NewsItem]:
    now = dt.datetime.now(dt.timezone.utc)
    items: list[NewsItem] = []
    seen_urls: set[str] = set()
    live_feeds = 0

    for feed_url in feed_urls:
        try:
            parsed = _fetch_feed(feed_url)
            live_feeds += 1
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
    log.info(
        "fetched %d items; %d/%d feeds reachable (window %.0fh)",
        len(items),
        live_feeds,
        len(feed_urls),
        max_age_hours,
    )
    if live_feeds == 0:
        log.error("no feed was reachable — check the feed list with --mode feedcheck")
    return items


def fetch_recent_widening(
    feed_urls: list[str],
    *,
    windows: tuple[float, ...] = (36, 96, 240),
    per_feed_limit: int = 10,
) -> list[NewsItem]:
    """Fetch with progressively wider time windows until something turns up.

    A quiet news day, a publisher pausing, or a couple of dead feeds shouldn't
    take the whole run down — a slightly older story beats no post at all. The
    windows are tried in order and the first non-empty result wins, so normal
    days still get same-day news.
    """
    for window in windows:
        items = fetch_recent(
            feed_urls, max_age_hours=window, per_feed_limit=per_feed_limit
        )
        if items:
            if window != windows[0]:
                log.warning(
                    "no items within %.0fh; widened to %.0fh and found %d",
                    windows[0],
                    window,
                    len(items),
                )
            return items
    return []
