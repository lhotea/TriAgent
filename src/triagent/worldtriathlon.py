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

**There is no flat "all news" endpoint.** The first version of this adapter
guessed `/v1/news`, confirmed live but wrong: it 404s. World Triathlon's own
documentation (found via search once it became clear the docs domain itself is
unreachable from here, same as triathlon.org) shows news is only exposed
scoped to a specific event (`/v1/events/{id}/news`) or a federation — and a
federation's `latest_news` reportedly returns null since a CMS migration. So
`fetch_api` treats the confirmed `/v1/events` listing endpoint as a discovery
step: it asks what is happening in a rolling window around today, then merges
each event's own news. That also means the source never goes stale the way a
single hardcoded event_id would — nothing here needs manually rotating as the
season moves on.

The field names in `TITLE_KEYS`/`SLUG_KEYS`/`DATE_KEYS`/`SUMMARY_KEYS` are
confirmed from an actual response, not the docs: the first deploy of this
discovery flow found real events but mapped title/url/date to null, because
the live `/v1/events/{id}/news` endpoint prefixes its fields
(`news_title`, `news_slug`, `news_entry_date`, `news_excerpt`) — a different
convention than the generic "Content API" docs example this was first built
from. `python -m triagent --mode apicheck` still exists to catch the day that
changes again: it reports the actual events found and the actual fields on a
sample article, including raw values for anything that looks like a URL but
wasn't used (see `news_url` vs `news_api_url` below), so a mismatch is
diagnosed from evidence rather than another guess.
"""

from __future__ import annotations

import datetime as dt
import logging
from typing import Any
from urllib.parse import parse_qsl, urlencode, urlparse

import requests
from feedparser.util import FeedParserDict

log = logging.getLogger(__name__)

SOURCE_NAME = "World Triathlon"

API_KEY_ENV = "WORLD_TRIATHLON_API_KEY"


class WorldTriathlonAuthError(requests.HTTPError):
    """The API answered, but refused the request for lack of a valid key.

    Distinct from a transport failure because it is not a breakage: a 401 with
    a JSON content type is the endpoint confirming it exists and wants
    credentials. It carries the one instruction that resolves it, so the daily
    log line says what to do rather than just naming a status code.
    """


# Hosts that serve the API. Matched exactly or as a subdomain suffix so a
# lookalike ("notapi.triathlon.org.evil.com") cannot claim the adapter.
API_HOSTS = ("api.triathlon.org", "api.worldtriathlon.org")

# Where an article lives when the payload gives a slug but no absolute URL.
ARTICLE_BASE = "https://triathlon.org/news/"

# Confirmed by World Triathlon's own docs (the "Event Listings" reference
# example): filterable by start_date/end_date, returns event_id + event_title.
EVENTS_LIST_URL = "https://api.triathlon.org/v1/events"
# Confirmed by the docs' Event News example: an array of news objects keyed
# by entry_id/title/slug/entry_date/excerpt for one event.
EVENT_NEWS_URL = "https://api.triathlon.org/v1/events/{event_id}/news"

DEFAULT_ENDPOINT = EVENTS_LIST_URL

# How far around today to look for events with news. Triathlon coverage
# clusters around race day: recent results and near-term previews are the
# stories worth posting, so the window is asymmetric rather than a flat +/-N.
EVENT_LOOKBACK_DAYS = 14
EVENT_LOOKAHEAD_DAYS = 45

# Cap on how many discovered events get their own /news request per run. The
# calendar can hold far more than this in a 59-day window; querying all of
# them would turn one feedcheck or one daily build into dozens of HTTP calls
# for a source that is meant to be one contributor among several.
MAX_EVENTS_PER_RUN = 5

EVENT_ID_KEYS = ("event_id", "id")
EVENT_TITLE_KEYS = ("event_title", "title", "name")

# The payload nests its list under one of these, or is a bare list.
LIST_KEYS = ("data", "results", "items", "news", "articles")

# The live /v1/events/{id}/news response uses its own prefixed convention —
# news_title, news_slug, news_entry_date, news_excerpt, news_id — confirmed by
# a real feedcheck run, not the generic "Content API" docs example this
# adapter was first built from (that example turned out to describe a
# different resource). The prefixed names are tried first for that reason.
#
# news_url is deliberately NOT in URL_KEYS. The same response also carries a
# distinct news_api_url, and nothing here can yet tell which one is a
# browsable page — a wrong guess produces a dead link, which is worse than
# composing one from news_slug (confirmed correct: triathlon.org/news/{slug}
# matched a real article URL seen during earlier research). `describe()`
# prints raw values for exactly this kind of ambiguous field so it gets
# resolved from evidence rather than another guess.
TITLE_KEYS = ("news_title", "title", "headline", "name")
URL_KEYS = ("url", "link", "permalink", "web_url", "canonical_url")
SLUG_KEYS = ("news_slug", "slug", "url_slug", "permalink_slug")
SUMMARY_KEYS = (
    "news_excerpt", "summary", "excerpt", "teaser", "description",
    "abstract", "content", "body",
)
DATE_KEYS = (
    "news_entry_date", "entry_date", "published_at", "date_published",
    "published", "publication_date", "date", "created_at", "updated_at",
    "modified_at",
)

# Fields that look like they might be a URL but are not in URL_KEYS — either
# because they are unconfirmed or, like news_api_url, confirmed to point
# somewhere other than a browsable page. describe() surfaces their raw
# values so a real one can be promoted into URL_KEYS with evidence.
_URL_LIKE_HINT_KEYS = ("news_url", "news_api_url", "api_url", "web_url", "permalink")

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


def _is_events_list_url(url: str) -> bool:
    """True for the bare events-listing endpoint, false for one event's
    sub-resource (`/v1/events/{id}/news`) or an unrelated path.

    This is what routes `fetch_api` into the discovery flow: the listing
    endpoint has nothing to show on its own (an event, not an article), while
    a URL already pointing at a specific event's `/news` — or any other
    resource — should be fetched and mapped directly.
    """
    return urlparse(url).path.rstrip("/").endswith("/events")


def _date_window() -> tuple[str, str]:
    """The rolling date range passed to the events listing.

    Recomputed on every call rather than cached, so a long-running process
    does not fetch yesterday's window forever.
    """
    today = dt.datetime.now(dt.timezone.utc).date()
    start = today - dt.timedelta(days=EVENT_LOOKBACK_DAYS)
    end = today + dt.timedelta(days=EVENT_LOOKAHEAD_DAYS)
    return start.isoformat(), end.isoformat()


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


def _auth_message(status: int, api_key: str | None) -> str:
    """Say which of the two auth problems this is.

    Telling someone to set a key they have already set is a dead end, so the
    message turns on whether one was sent.
    """
    if api_key:
        return (
            f"World Triathlon API rejected the key ({status}). The "
            f"{API_KEY_ENV} secret is set but not accepted — check it has not "
            "expired and is valid for the news endpoint."
        )
    return (
        f"World Triathlon API requires authentication ({status}). The endpoint "
        f"is reachable and returns JSON; set the {API_KEY_ENV} secret to use "
        "it. Until then this source contributes nothing and the rest of the "
        "run is unaffected."
    )


def _get_json(url: str, *, api_key: str | None, timeout: int) -> Any:
    """GET a JSON endpoint, or None if the body isn't JSON.

    Shared by every call site — events discovery, a specific event's news,
    and a directly configured URL — so a 401/403 becomes the same
    `WorldTriathlonAuthError` everywhere rather than only where this was
    first written. A non-JSON body degrades to None rather than raising,
    matching how an unparseable RSS document behaves: one source going
    strange must not take the daily post down. A transport failure or a
    non-auth HTTP error still raises, so `fetch_recent` logs and skips the
    source exactly as it does for a dead feed.
    """
    headers = {
        "Accept": "application/json",
        "User-Agent": "TriAgent/1.0 (+https://github.com/lhotea/TriAgent)",
    }
    if api_key:
        headers["apikey"] = api_key

    resp = requests.get(url, timeout=timeout, headers=headers)
    try:
        resp.raise_for_status()
    except requests.HTTPError as exc:
        status = getattr(getattr(exc, "response", None), "status_code", None)
        if status in (401, 403):
            raise WorldTriathlonAuthError(_auth_message(status, api_key)) from exc
        raise
    try:
        return resp.json()
    except ValueError:
        content_type = (getattr(resp, "headers", None) or {}).get("content-type", "")
        log.warning(
            "World Triathlon API returned %s, not JSON (%s)",
            content_type or "an unreadable body",
            url,
        )
        return None


def _with_date_window(url: str) -> str:
    """Add the rolling start_date/end_date window to a listing URL.

    Existing query parameters are preserved with `setdefault` semantics — a
    URL someone has already parameterised (e.g. with `category_id`) is
    extended, not overwritten.
    """
    parsed = urlparse(url)
    query = dict(parse_qsl(parsed.query))
    start, end = _date_window()
    query.setdefault("start_date", start)
    query.setdefault("end_date", end)
    return parsed._replace(query=urlencode(query)).geturl()


def _discover_event_ids(
    url: str, *, api_key: str | None, timeout: int
) -> list[tuple[str, str]]:
    """Fetch nearby events and return (event_id, event_title) pairs, capped.

    Order is whatever the API returns — nothing here is confirmed about how
    results are sorted, so no re-sorting is attempted on a guessed field
    name. Capping simply takes the first `MAX_EVENTS_PER_RUN`.
    """
    payload = _get_json(_with_date_window(url), api_key=api_key, timeout=timeout)
    events = _extract_list(payload) if payload is not None else []

    pairs: list[tuple[str, str]] = []
    for item in events:
        event_id = _first(item, EVENT_ID_KEYS)
        if event_id in (None, ""):
            continue
        title = _first(item, EVENT_TITLE_KEYS) or ""
        pairs.append((str(event_id), str(title)))
        if len(pairs) >= MAX_EVENTS_PER_RUN:
            break
    return pairs


def _fetch_via_event_discovery(
    url: str, *, api_key: str | None, timeout: int
) -> FeedParserDict:
    """Merge news from every event found in the rolling date window.

    An auth failure on the events list itself propagates immediately — no
    key is going to work better on the next call. An auth failure on one
    event's `/news` lookup (which should not happen with the same key, but a
    per-resource permission model is not impossible) propagates too, for the
    same reason. A non-auth failure on one event's lookup is logged and
    skipped, so one bad event_id cannot take the rest of the batch down —
    the equivalent of per-feed isolation for RSS sources.
    """
    pairs = _discover_event_ids(url, api_key=api_key, timeout=timeout)
    if not pairs:
        log.info("World Triathlon: no events found in the discovery window")
        return _shape([])

    merged: list[dict] = []
    for event_id, title in pairs:
        try:
            payload = _get_json(
                EVENT_NEWS_URL.format(event_id=event_id), api_key=api_key, timeout=timeout
            )
        except WorldTriathlonAuthError:
            raise
        except requests.RequestException as exc:
            # Broader than HTTPError on purpose: a connection failure or
            # timeout on one event's lookup is exactly as isolable as a bad
            # status code, and both must leave the other events' news intact.
            log.warning(
                "World Triathlon: news lookup failed for event %s (%s): %s",
                event_id, title, exc,
            )
            continue
        if payload is not None:
            merged.extend(_extract_list(payload))

    log.info(
        "World Triathlon: checked %d event(s), found %d article(s)",
        len(pairs), len(merged),
    )
    return to_feed(merged)


def fetch_api(url: str, *, api_key: str | None = None, timeout: int = 10) -> FeedParserDict:
    """Fetch World Triathlon news and return it in feedparser's shape.

    The bare events-listing URL routes into the discovery flow, since it has
    no articles of its own to map. Any other URL — a specific event's
    `/news`, or a future flat endpoint if World Triathlon ever adds one — is
    fetched and mapped directly.
    """
    if _is_events_list_url(url):
        return _fetch_via_event_discovery(url, api_key=api_key, timeout=timeout)
    payload = _get_json(url, api_key=api_key, timeout=timeout)
    return to_feed(payload) if payload is not None else _shape([])


def _mapped_sample(articles: list[dict]) -> dict:
    """The mapping report for one article — shared by both describe() paths."""
    sample = articles[0]
    mapped_url = _article_url(sample)
    # Which key actually supplied mapped_url, so it can be excluded below —
    # an explicit URL field and a slug both look "url-like" and one of them
    # is exactly what got used.
    used_url_key = next(
        (k for k in URL_KEYS + SLUG_KEYS if sample.get(k) == mapped_url or
         (k in SLUG_KEYS and isinstance(sample.get(k), str) and
          mapped_url == ARTICLE_BASE + sample[k].strip().strip("/"))),
        None,
    )
    report = {
        "article_keys": sorted(sample),
        "mapped": {
            "title": _first(sample, TITLE_KEYS),
            "url": mapped_url,
            "date_raw": _first(sample, DATE_KEYS),
            "date_parsed": str(_parse_date(_first(sample, DATE_KEYS))),
            "summary_present": bool(_first(sample, SUMMARY_KEYS)),
        },
        "unmapped_keys": sorted(
            set(sample)
            - set(TITLE_KEYS) - set(URL_KEYS) - set(SLUG_KEYS)
            - set(SUMMARY_KEYS) - set(DATE_KEYS)
        ),
    }
    # Surface raw values for fields that look like a URL but were not the one
    # used, so an ambiguity like news_url vs news_api_url gets resolved from
    # evidence in this run rather than needing another guess-and-redeploy.
    url_like = {
        key: sample[key]
        for key in _URL_LIKE_HINT_KEYS
        if key in sample and key != used_url_key and isinstance(sample[key], str)
    }
    if url_like:
        report["unmapped_url_like_fields"] = url_like
    return report


def _describe_direct(url: str, *, api_key: str | None) -> dict:
    """describe() for a URL that is expected to hold articles itself."""
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
        status = report.get("status")
        if status in (401, 403):
            report["needs_auth"] = True
            report["message"] = _auth_message(status, api_key)
            return report
        report["error"] = f"{type(exc).__name__}: {exc}"
        return report

    report["top_level"] = (
        sorted(payload) if isinstance(payload, dict) else f"<{type(payload).__name__}>"
    )
    articles = _extract_list(payload)
    report["articles_found"] = len(articles)
    if articles:
        report.update(_mapped_sample(articles))
    return report


def _describe_discovery(url: str, *, api_key: str | None) -> dict:
    """describe() for the bare events-listing URL: report what was
    discovered, then map a sample article from the first event's news.

    Kept separate from `_describe_direct` because a discovery report has a
    genuinely different shape — no direct-fetch status/content-type to show,
    an events list to summarise, and a sample drawn from a second request
    rather than the one that was probed.
    """
    start, end = _date_window()
    report: dict = {
        "url": url,
        "authenticated": bool(api_key),
        "mode": "event-discovery",
        "date_window": {"start": start, "end": end},
    }
    try:
        pairs = _discover_event_ids(url, api_key=api_key, timeout=15)
    except WorldTriathlonAuthError as exc:
        report["needs_auth"] = True
        report["message"] = str(exc)
        return report
    except Exception as exc:
        report["error"] = f"{type(exc).__name__}: {exc}"
        return report

    report["events_found"] = len(pairs)
    report["events"] = [{"event_id": eid, "event_title": title} for eid, title in pairs]
    if not pairs:
        report["message"] = (
            "no events found in the date window "
            f"({start} to {end}) — the events list endpoint itself works, but "
            "nothing is scheduled nearby. This is not a mapping problem."
        )
        return report

    event_id, event_title = pairs[0]
    try:
        payload = _get_json(
            EVENT_NEWS_URL.format(event_id=event_id), api_key=api_key, timeout=15
        )
    except WorldTriathlonAuthError as exc:
        report["needs_auth"] = True
        report["message"] = str(exc)
        return report
    except Exception as exc:
        report["sample_event_error"] = f"{type(exc).__name__}: {exc}"
        return report

    articles = _extract_list(payload) if payload is not None else []
    report["sample_event"] = {
        "event_id": event_id, "event_title": event_title, "articles_found": len(articles),
    }
    # Duplicated at the top level under the same keys _describe_direct uses,
    # so the one apicheck exit-code branch below works for either mode.
    report["articles_found"] = len(articles)
    if articles:
        report.update(_mapped_sample(articles))
    return report


def describe(url: str, *, api_key: str | None = None) -> dict:
    """Report the API's actual structure, for `--mode apicheck`.

    The mapping was written from World Triathlon's own documentation
    examples rather than a live response — this domain has been unreachable
    from every environment this project has run in. This is how a mismatch
    gets confirmed or corrected from evidence instead of another guess.
    """
    if _is_events_list_url(url):
        return _describe_discovery(url, api_key=api_key)
    return _describe_direct(url, api_key=api_key)
