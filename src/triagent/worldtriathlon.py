"""World Triathlon news, which is published through a JSON API rather than RSS.

`triathlon.org/news` serves an HTML page that advertises no feed, so the
governing body — the source the post is meant to lead with whenever it has news
— never reached a single post despite `prioritize()` ranking it first. This
adapter is the missing half.

**It normalises into feedparser's shape rather than producing NewsItems.**
That is the whole design. `fetch_recent` already applies the age window, the
posted-story ledger, the per-source contribution cap, source diversity and
governing-body priority, and every one of those has been the site of a real bug
when applied in the wrong place. A second ingestion path would have to
re-implement all of them and would drift; returning `{"feed": ..., "entries":
[...]}` means the API enters through the same door as every RSS source and
inherits the lot.

**The response schema could not be observed while this was written** — the
development environment's proxy denies triathlon.org — so the mapper accepts
the field names the plausible shapes use and reports what it could not read.
`python -m triagent --mode apicheck` prints the actual structure from a network
that can reach the host, which is how the mapping gets pinned down.
"""

from __future__ import annotations

import datetime as dt
import logging
from typing import Any
from urllib.parse import urlparse

import requests
from feedparser.util import FeedParserDict

log = logging.getLogger(__name__)

SOURCE_NAME = "World Triathlon"

# Hosts that serve the API. Matched exactly or as a subdomain suffix so a
# lookalike ("notapi.triathlon.org.evil.com") cannot claim the adapter.
API_HOSTS = ("api.triathlon.org", "api.worldtriathlon.org")

# Where an article lives when the payload gives a slug but no absolute URL.
ARTICLE_BASE = "https://triathlon.org/news/"

DEFAULT_ENDPOINT = "https://api.triathlon.org/v1/news"

# The payload nests its list under one of these, or is a bare list.
LIST_KEYS = ("data", "results", "items", "news", "articles")

TITLE_KEYS = ("title", "headline", "name")
URL_KEYS = ("url", "link", "permalink", "web_url", "canonical_url")
SLUG_KEYS = ("slug", "url_slug", "permalink_slug")
SUMMARY_KEYS = ("summary", "excerpt", "teaser", "description", "abstract", "content", "body")
DATE_KEYS = (
    "published_at", "date_published", "published", "publication_date",
    "date", "created_at", "updated_at", "modified_at",
)

# Formats seen across the plausible shapes: "2026-08-20 09:30:00" (the style
# this API family tends to emit) and ISO 8601 with or without a zone.
_DATE_FORMATS = (
    "%Y-%m-%d %H:%M:%S",
    "%Y-%m-%dT%H:%M:%S",
    "%Y-%m-%d %H:%M",
    "%Y-%m-%d",
)


def is_api_url(url: str) -> bool:
    """True when this URL should be fetched as JSON rather than parsed as RSS."""
    host = urlparse(url).netloc.lower().split(":")[0]
    return any(host == h for h in API_HOSTS)


def _first(item: dict, keys: tuple[str, ...]) -> Any:
    for key in keys:
        value = item.get(key)
        if value not in (None, "", [], {}):
            return value
    return None


def _parse_date(raw: Any) -> dt.datetime | None:
    """Read a timestamp in any of the formats this API family emits."""
    if isinstance(raw, (int, float)):  # epoch seconds
        try:
            return dt.datetime.fromtimestamp(float(raw), dt.timezone.utc)
        except (OverflowError, OSError, ValueError):
            return None
    if not isinstance(raw, str):
        return None

    text = raw.strip()
    if not text:
        return None
    # fromisoformat handles offsets and fractional seconds; "Z" it does not.
    try:
        parsed = dt.datetime.fromisoformat(text.replace("Z", "+00:00"))
        return (
            parsed.replace(tzinfo=dt.timezone.utc)
            if parsed.tzinfo is None
            else parsed.astimezone(dt.timezone.utc)
        )
    except ValueError:
        pass
    for fmt in _DATE_FORMATS:
        try:
            return dt.datetime.strptime(text, fmt).replace(tzinfo=dt.timezone.utc)
        except ValueError:
            continue
    return None


def _extract_list(payload: Any) -> list[dict]:
    """Find the article list inside whatever envelope the API used.

    Unwrapping is recursive by one level because a `data` key holding another
    envelope (`{"data": {"results": [...]}}`) is a common pagination shape.
    """
    if isinstance(payload, list):
        return [i for i in payload if isinstance(i, dict)]
    if not isinstance(payload, dict):
        return []
    for key in LIST_KEYS:
        value = payload.get(key)
        if isinstance(value, list):
            return [i for i in value if isinstance(i, dict)]
        if isinstance(value, dict):
            nested = _extract_list(value)
            if nested:
                return nested
    return []


def _article_url(item: dict) -> str | None:
    """Resolve an article's URL, or None if it cannot be known.

    An explicit URL field wins. Failing that, a slug is a real identifier and
    composes deterministically. A bare numeric id does not — building a URL
    from one would be a guess, and a dead link in the post is a worse outcome
    than one fewer headline, so such items are dropped instead.
    """
    explicit = _first(item, URL_KEYS)
    if isinstance(explicit, str) and explicit.startswith(("http://", "https://")):
        return explicit
    slug = _first(item, SLUG_KEYS)
    if isinstance(slug, str) and slug.strip():
        return ARTICLE_BASE + slug.strip().strip("/")
    return None


def _shape(entries: list[dict]) -> FeedParserDict:
    """Wrap entries the way feedparser does.

    A plain dict is not enough: the pipeline reads `parsed.entries` and
    `parsed.feed` by attribute, which only FeedParserDict supports. Matching
    the real type is what lets the adapter enter through the same door instead
    of needing a special case downstream.
    """
    return FeedParserDict(
        feed=FeedParserDict(title=SOURCE_NAME, links=[]),
        entries=[FeedParserDict(**e) for e in entries],
        bozo=False,
    )


def to_feed(payload: Any) -> dict:
    """Normalise an API payload into the dict shape feedparser produces.

    Entries carry `published_parsed` because `news._parse_dt` reads that first;
    supplying only a formatted string would route through the RFC 822 fallback
    and stamp everything as "now" — the exact bug that made Atom feeds
    ageless.
    """
    articles = _extract_list(payload)
    if not articles:
        log.warning(
            "World Triathlon API returned no recognisable article list "
            "(top-level keys: %s) — run --mode apicheck to see the shape",
            ", ".join(sorted(payload)) if isinstance(payload, dict) else type(payload).__name__,
        )
        return _shape([])

    entries: list[dict] = []
    skipped_no_url = 0
    skipped_no_title = 0
    for item in articles:
        title = _first(item, TITLE_KEYS)
        if not isinstance(title, str) or not title.strip():
            skipped_no_title += 1
            continue
        url = _article_url(item)
        if not url:
            skipped_no_url += 1
            continue

        entry: dict = {
            "title": title.strip(),
            "link": url,
            # Left as raw markup: news._clean strips tags and decodes entities
            # for every source, and doing it twice here would diverge.
            "summary": str(_first(item, SUMMARY_KEYS) or ""),
        }
        published = _parse_date(_first(item, DATE_KEYS))
        if published:
            entry["published_parsed"] = published.timetuple()
        entries.append(entry)

    if skipped_no_url or skipped_no_title:
        log.warning(
            "World Triathlon: skipped %d article(s) with no resolvable URL and "
            "%d with no title",
            skipped_no_url,
            skipped_no_title,
        )
    log.info("World Triathlon API supplied %d article(s)", len(entries))
    return _shape(entries)


def fetch_api(url: str, *, api_key: str | None = None, timeout: int = 10) -> dict:
    """Fetch the API and return it in feedparser's shape.

    A non-JSON body degrades to zero entries rather than raising, matching how
    an unparseable RSS document behaves — one source going strange must not
    take the daily post down. Transport failures still raise, so `fetch_recent`
    logs and skips the source exactly as it does for a dead feed.
    """
    headers = {
        "Accept": "application/json",
        "User-Agent": "TriAgent/1.0 (+https://github.com/lhotea/TriAgent)",
    }
    if api_key:
        headers["apikey"] = api_key

    resp = requests.get(url, timeout=timeout, headers=headers)
    resp.raise_for_status()
    try:
        payload = resp.json()
    except ValueError:
        content_type = (getattr(resp, "headers", None) or {}).get("content-type", "")
        log.warning(
            "World Triathlon API returned %s, not JSON — check the endpoint "
            "and whether WORLD_TRIATHLON_API_KEY is required",
            content_type or "an unreadable body",
        )
        return _shape([])
    return to_feed(payload)


def describe(url: str, *, api_key: str | None = None) -> dict:
    """Report the payload's actual structure, for `--mode apicheck`.

    The mapping above was written without ever seeing a real response. This is
    how it gets confirmed or corrected from a network that can reach the host,
    rather than by another round of guessing.
    """
    report: dict = {"url": url, "authenticated": bool(api_key)}
    try:
        headers = {"Accept": "application/json"}
        if api_key:
            headers["apikey"] = api_key
        resp = requests.get(url, timeout=15, headers=headers)
        report["status"] = resp.status_code
        report["content_type"] = (resp.headers or {}).get("content-type", "")
        resp.raise_for_status()
        payload = resp.json()
    except Exception as exc:
        report["error"] = f"{type(exc).__name__}: {exc}"
        return report

    report["top_level"] = (
        sorted(payload) if isinstance(payload, dict) else f"<{type(payload).__name__}>"
    )
    articles = _extract_list(payload)
    report["articles_found"] = len(articles)
    if articles:
        sample = articles[0]
        report["article_keys"] = sorted(sample)
        report["mapped"] = {
            "title": _first(sample, TITLE_KEYS),
            "url": _article_url(sample),
            "date_raw": _first(sample, DATE_KEYS),
            "date_parsed": str(_parse_date(_first(sample, DATE_KEYS))),
            "summary_present": bool(_first(sample, SUMMARY_KEYS)),
        }
        report["unmapped_keys"] = sorted(
            set(sample)
            - set(TITLE_KEYS) - set(URL_KEYS) - set(SLUG_KEYS)
            - set(SUMMARY_KEYS) - set(DATE_KEYS)
        )
    return report
