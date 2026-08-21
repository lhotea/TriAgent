from __future__ import annotations

import datetime as dt
import html as html_mod
import logging
import re
from dataclasses import dataclass
from email.utils import parsedate_to_datetime
from urllib.parse import urljoin, urlparse

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
    """Read an entry's publication time, in whatever format the feed uses.

    feedparser's `*_parsed` fields are already normalised to a struct_time
    whatever the source dialect, so they are tried first. Only RFC 822 strings
    were parsed before, which Atom does not use — it carries ISO 8601
    ("2026-07-01T10:00:00Z"). Every key therefore fell through to the `now()`
    fallback, and a seven-week-old story reported an age of zero hours. That
    disabled the age filter and flattened the recency sort for
    220triathlon.com/feed/atom, the feed that supplies most of the pool, and
    made the widening fallback inert: every item already passed every window.

    The `now()` fallback survives for genuinely undated entries, where
    "assume it is current" is the only useful guess.
    """
    for key in ("published_parsed", "updated_parsed", "created_parsed"):
        struct = entry.get(key)
        if not struct:
            continue
        try:
            # struct_time from feedparser is always UTC.
            return dt.datetime(*struct[:6], tzinfo=dt.timezone.utc)
        except (TypeError, ValueError):
            continue

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


# Types a <link rel="alternate"> must advertise for us to treat it as a feed.
FEED_LINK_TYPES = (
    "application/rss+xml",
    "application/atom+xml",
    "application/rdf+xml",
    "application/xml",
    "text/xml",
)


def _discover_feed_url(parsed: feedparser.Feed, page_url: str) -> str | None:
    """Find the feed a fetched HTML page advertises, if any.

    A section URL such as triathlon.org/news serves a page, not a feed. The
    page points at its feed with <link rel="alternate" type="application/
    rss+xml">, which is what makes it readable without naming a specific feed
    URL — but feedparser parses that tag without following it, so the fetch
    returned zero entries and World Triathlon was silently absent from every
    post despite being the governing body and ranked first when present.
    """
    for link in parsed.feed.get("links", []) or []:
        if link.get("rel") != "alternate":
            continue
        if (link.get("type") or "").lower() not in FEED_LINK_TYPES:
            continue
        href = link.get("href")
        if href:
            # hrefs are routinely relative ("/rss/news").
            return urljoin(page_url, href)
    return None


def _fetch_feed_resolved(feed_url: str) -> tuple[feedparser.Feed, str]:
    """Fetch a feed, following one autodiscovery hop if the URL served a page.

    Returns the parsed feed and the URL that actually supplied the entries, so
    diagnostics can name it. Exactly one hop is followed: a page whose
    advertised feed is another page is a broken source, not an invitation to
    recurse.
    """
    parsed = _fetch_feed(feed_url)
    if parsed.entries:
        return parsed, feed_url

    discovered = _discover_feed_url(parsed, feed_url)
    if not discovered or discovered == feed_url:
        return parsed, feed_url

    log.info("%s served a page; following its feed link to %s", feed_url, discovered)
    return _fetch_feed(discovered), discovered


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
            parsed, resolved = _fetch_feed_resolved(feed_url)
            entries = parsed.entries or []
            row["ok"] = bool(entries)
            row["entries"] = len(entries)
            row["title"] = parsed.feed.get("title", "")
            if resolved != feed_url:
                row["resolved_url"] = resolved
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


def diversify(items: list["NewsItem"]) -> list["NewsItem"]:
    """Round-robin across sources: one item each before any source repeats.

    Straight recency ordering hands the whole list to whichever publisher posts
    most often. 220 Triathlon files several stories a day while most sources
    file one, so a recency sort put nine 220 items above everything else and
    the model — which only ever sees the first dozen — had nothing else to
    choose from. Every feed was working; the selection was the problem.

    Recency is preserved *within* each source, so each publisher still leads
    with its newest story.
    """
    by_source: dict[str, list] = {}
    for item in items:
        by_source.setdefault(item.source, []).append(item)

    ordered: list = []
    while by_source:
        for source in list(by_source):
            ordered.append(by_source[source].pop(0))
            if not by_source[source]:
                del by_source[source]
    return ordered


def prioritize(items: list["NewsItem"]) -> list["NewsItem"]:
    """Order items for selection: governing body first, then source-balanced.

    The model reads this list in order and only sees the first dozen, so
    ordering *is* the selection policy — a prompt instruction alone loses to
    whatever happens to sit higher in the list.
    """
    governing = [i for i in items if is_governing_body(i)]
    rest = [i for i in items if not is_governing_body(i)]
    if governing:
        log.info("%d governing-body item(s) prioritized", len(governing))

    ordered = diversify(governing) + diversify(rest)
    if ordered:
        seen: dict[str, int] = {}
        for i in ordered[:12]:
            seen[i.source] = seen.get(i.source, 0) + 1
        log.info(
            "top 12 by source: %s",
            ", ".join(f"{k} x{v}" for k, v in sorted(seen.items())),
        )
    return ordered


def fetch_recent(
    feed_urls: list[str],
    *,
    max_age_hours: float = 36,
    per_feed_limit: int = 10,
    exclude: set[str] | None = None,
) -> list[NewsItem]:
    """Collect usable items from every feed, newest first.

    `per_feed_limit` caps what each feed *contributes*, counted after the age
    and `exclude` filters — not how far down the feed we look. Slicing the raw
    entry list first made the widening fallback inert: entry 11 was unreachable
    at any window, so once a busy feed's ten newest stories were all in the
    posted ledger, that feed was permanently dry and the run failed with "no
    unused triathlon news found in any time window". 220 Triathlon supplies
    most of the real pool, so that was days away rather than hypothetical.
    """
    exclude = exclude or set()
    now = dt.datetime.now(dt.timezone.utc)
    items: list[NewsItem] = []
    seen_urls: set[str] = set()
    live_feeds = 0
    silent_feeds: list[str] = []

    for feed_url in feed_urls:
        try:
            parsed, _resolved = _fetch_feed_resolved(feed_url)
            live_feeds += 1
        except Exception as exc:
            log.warning("feed fetch failed for %s: %s", feed_url, exc)
            continue

        # Feed titles carry entities too ("220 Triathlon &amp; Multisport").
        source = _clean(parsed.feed.get("title", "")) or feed_url
        kept = 0
        for entry in parsed.entries:
            if kept >= per_feed_limit:
                break
            url = entry.get("link")
            if not url or url in seen_urls or url in exclude:
                continue
            published = _parse_dt(entry)
            if (now - published).total_seconds() / 3600 > max_age_hours:
                continue

            title = _clean(entry.get("title", ""))
            summary = _clean(entry.get("summary", entry.get("description", "")))
            if not title:
                continue
            seen_urls.add(url)
            kept += 1
            items.append(
                NewsItem(
                    title=title,
                    summary=summary[:800],
                    url=url,
                    source=source,
                    published=published,
                )
            )
        if kept == 0:
            silent_feeds.append(feed_url)

    items.sort(key=lambda i: i.published, reverse=True)
    # A feed that parses but yields nothing is otherwise invisible: production
    # logged "13/14 feeds reachable" while two sources supplied every story.
    # Naming the silent ones is what makes a stale FEEDS entry fixable.
    if silent_feeds:
        log.info(
            "%d feed(s) contributed no items (window %.0fh): %s",
            len(silent_feeds),
            max_age_hours,
            ", ".join(silent_feeds),
        )
    # Per-source counts make an imbalance visible in the log rather than only
    # in the finished post.
    if items:
        counts: dict[str, int] = {}
        for i in items:
            counts[i.source] = counts.get(i.source, 0) + 1
        log.info(
            "items per source: %s",
            ", ".join(f"{k} x{v}" for k, v in sorted(counts.items())),
        )
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

    `exclude` holds URLs already posted. It is handed down to `fetch_recent`
    rather than applied to its result, so posted stories never consume a feed's
    contribution cap — filtering afterwards would leave a busy feed returning
    the same ten already-used items at every window, and widening would find
    nothing to widen into. The window overlaps by design, so on most days the
    36h result is mostly yesterday's stories; widening is exactly the right
    response to "everything recent has been used", and only the loop can do it.
    """
    exclude = exclude or set()
    for window in windows:
        fresh = fetch_recent(
            feed_urls,
            max_age_hours=window,
            per_feed_limit=per_feed_limit,
            exclude=exclude,
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
