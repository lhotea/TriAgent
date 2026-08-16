from __future__ import annotations

import datetime as dt
import html as html_mod
import logging
import re
from dataclasses import dataclass
from email.utils import parsedate_to_datetime
from urllib.parse import urlparse

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


def _clean(raw: str) -> str:
    """Turn a feed's title/summary HTML into plain text.

    Entities must be decoded, not just tags stripped. Feeds routinely deliver
    typographic punctuation as numeric entities — "I&#8217;m" — and leaving
    them encoded leaks into everything downstream: the review page shows the
    raw entity (its own escaping turns the & into &amp;), the rendered card
    prints it, and the model sees it in its prompt.

    Unescaping happens first so double-encoded markup (&lt;b&gt;) is revealed
    and then stripped by the tag pass, rather than surviving as literal text.
    The parameter is `raw` rather than `html` so it cannot shadow the module.
    """
    text = html_mod.unescape(raw or "")
    text = _WS_RE.sub(" ", _TAG_RE.sub(" ", text))
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


# World Triathlon is the sport's governing body, so its news outranks
# aggregator coverage. Matched on host and on feed title, since the same
# organisation publishes under several domains.
GOVERNING_BODY_HOSTS = ("triathlon.org", "worldtriathlon.org")
GOVERNING_BODY_NAMES = ("world triathlon", "worldtriathlon")


def is_governing_body(item: "NewsItem") -> bool:
    host = urlparse(item.url).netloc.lower()
    if any(host == h or host.endswith("." + h) for h in GOVERNING_BODY_HOSTS):
        return True
    return any(name in item.source.lower() for name in GOVERNING_BODY_NAMES)


def prioritize(items: list["NewsItem"]) -> list["NewsItem"]:
    """Float World Triathlon items to the front, preserving recency within groups.

    The model reads this list in order and is told the first entries are the
    governing body, so ordering is how the preference is actually expressed —
    a prompt instruction alone would be ignored whenever a livelier aggregator
    story appeared higher up.
    """
    governing = [i for i in items if is_governing_body(i)]
    rest = [i for i in items if not is_governing_body(i)]
    if governing:
        log.info("%d governing-body item(s) prioritized", len(governing))
    return governing + rest


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

        # Feed titles carry entities too ("220 Triathlon &amp; Multisport").
        source = _clean(parsed.feed.get("title", "")) or feed_url
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
    exclude: set[str] | None = None,
    min_items: int = 1,
) -> list[NewsItem]:
    """Fetch with progressively wider windows until enough usable items appear.

    A quiet news day, a publisher pausing, or a couple of dead feeds shouldn't
    take the whole run down — a slightly older story beats no post at all. The
    windows are tried in order and the first sufficient result wins, so normal
    days still get same-day news.

    `exclude` holds URLs already posted. Filtering happens inside the loop, not
    after it: the fetch window overlaps by design, so on most days the 36h
    result is mostly yesterday's stories. Widening is exactly the right
    response to "everything recent has been used", and only the loop can do it.
    """
    exclude = exclude or set()
    for window in windows:
        items = fetch_recent(
            feed_urls, max_age_hours=window, per_feed_limit=per_feed_limit
        )
        fresh = [i for i in items if i.url not in exclude]
        if len(fresh) < len(items):
            log.info(
                "%d of %d items already posted (window %.0fh)",
                len(items) - len(fresh),
                len(items),
                window,
            )
        if len(fresh) >= min_items:
            if window != windows[0]:
                log.warning(
                    "widened to %.0fh to find %d unused item(s)", window, len(fresh)
                )
            return fresh
    log.error(
        "only found fewer than %d unused item(s) even at %.0fh",
        min_items,
        windows[-1],
    )
    return []
