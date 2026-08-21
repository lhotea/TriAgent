"""Tests for the World Triathlon API adapter.

triathlon.org serves an HTML page that advertises no feed, so the governing
body — the source the post is meant to lead with — never reached a single
post. Its news lives behind a JSON API instead.

The adapter normalises that JSON into the shape feedparser produces, so it
enters the pipeline through the same door as every RSS source and inherits the
age filter, the posted-story ledger, the per-source cap, source diversity and
governing-body priority. Bolting on a second, parallel ingestion path is how
those filters get applied inconsistently, which is the bug class that produced
the duplicate posts.

The exact response schema could not be observed from the development
environment (its proxy denies triathlon.org), so the mapper accepts the field
names the plausible shapes use and `--mode apicheck` reports what actually
came back.
"""

from __future__ import annotations

import datetime as dt
from unittest.mock import MagicMock, patch

import pytest

from triagent.worldtriathlon import (
    ARTICLE_BASE,
    is_api_url,
    fetch_api,
    to_feed,
)


ENVELOPE = {
    "code": 200,
    "status": "OK",
    "data": [
        {
            "news_id": 4821,
            "title": "Paris 2028 qualification criteria confirmed",
            "slug": "paris-2028-qualification-criteria-confirmed",
            "summary": "World Triathlon has ratified the qualification pathway.",
            "published_at": "2026-08-20 09:30:00",
        },
        {
            "news_id": 4822,
            "title": "World Cup Karlovy Vary preview",
            "slug": "world-cup-karlovy-vary-preview",
            "content": "<p>The field is <b>stacked</b>.</p>",
            "published_at": "2026-08-19T14:00:00Z",
        },
    ],
}


def _resp(payload):
    m = MagicMock()
    m.json.return_value = payload
    m.raise_for_status = MagicMock()
    m.headers = {"content-type": "application/json"}
    m.status_code = 200
    return m


class TestUrlRecognition:
    """The adapter has to claim its own URLs without a second config knob."""

    @pytest.mark.parametrize(
        "url",
        [
            "https://api.triathlon.org/v1/news",
            "https://api.triathlon.org/v1/news?per_page=20",
            "https://api.worldtriathlon.org/v1/news",
        ],
    )
    def test_recognises_api_hosts(self, url):
        assert is_api_url(url)

    @pytest.mark.parametrize(
        "url",
        [
            "https://triathlon.org/news",
            "https://220triathlon.com/feed/atom",
            "https://notapi.triathlon.org.evil.com/v1/news",
        ],
    )
    def test_ignores_everything_else(self, url):
        assert not is_api_url(url)


class TestToFeed:
    """The output must be indistinguishable from a parsed RSS feed."""

    def test_produces_feedparser_shape(self):
        parsed = to_feed(ENVELOPE)
        assert parsed["feed"]["title"] == "World Triathlon"
        assert len(parsed["entries"]) == 2
        assert set(parsed["entries"][0]) >= {"title", "link", "summary"}

    def test_entries_carry_a_parsed_timestamp(self):
        """fetch_recent reads published_parsed first — it must be present."""
        entry = to_feed(ENVELOPE)["entries"][0]
        assert entry["published_parsed"][:6] == (2026, 8, 20, 9, 30, 0)

    def test_parses_iso_timestamps_too(self):
        entry = to_feed(ENVELOPE)["entries"][1]
        assert entry["published_parsed"][:6] == (2026, 8, 19, 14, 0, 0)

    def test_builds_article_url_from_slug(self):
        entry = to_feed(ENVELOPE)["entries"][0]
        assert entry["link"] == (
            ARTICLE_BASE + "paris-2028-qualification-criteria-confirmed"
        )

    def test_prefers_an_explicit_url_field(self):
        payload = {"data": [{
            "title": "t", "url": "https://triathlon.org/news/real-one",
            "slug": "ignored", "published_at": "2026-08-20 09:00:00",
        }]}
        assert to_feed(payload)["entries"][0]["link"] == (
            "https://triathlon.org/news/real-one"
        )

    def test_skips_items_with_no_usable_url(self):
        """A fabricated link is worse than a missing story.

        Constructing a URL from a numeric id would be a guess, and a dead link
        in the post is a worse outcome than one fewer headline.
        """
        payload = {"data": [
            {"title": "no way to link this", "news_id": 99,
             "published_at": "2026-08-20 09:00:00"},
            {"title": "fine", "slug": "fine", "published_at": "2026-08-20 09:00:00"},
        ]}
        entries = to_feed(payload)["entries"]
        assert [e["title"] for e in entries] == ["fine"]

    def test_skips_items_with_no_title(self):
        payload = {"data": [{"slug": "x", "published_at": "2026-08-20 09:00:00"}]}
        assert to_feed(payload)["entries"] == []

    def test_summary_falls_back_to_content(self):
        entry = to_feed(ENVELOPE)["entries"][1]
        assert "stacked" in entry["summary"]

    def test_undated_item_is_still_usable(self):
        """Missing dates must not drop a story; fetch_recent treats it as now."""
        payload = {"data": [{"title": "t", "slug": "s"}]}
        assert len(to_feed(payload)["entries"]) == 1


class TestEnvelopeShapes:
    """The response envelope was not observable during development."""

    @pytest.mark.parametrize("key", ["data", "results", "items", "news"])
    def test_accepts_common_envelope_keys(self, key):
        payload = {key: [{"title": "t", "slug": "s"}]}
        assert len(to_feed(payload)["entries"]) == 1

    def test_accepts_a_bare_list(self):
        assert len(to_feed([{"title": "t", "slug": "s"}])["entries"]) == 1

    def test_unrecognised_shape_yields_no_entries_without_raising(self):
        """A schema change must degrade like a dead feed, not kill the run."""
        parsed = to_feed({"unexpected": {"nested": "thing"}})
        assert parsed["entries"] == []

    def test_nested_data_object_is_unwrapped(self):
        payload = {"data": {"results": [{"title": "t", "slug": "s"}]}}
        assert len(to_feed(payload)["entries"]) == 1


class TestFetchApi:
    """Auth and transport."""

    def test_sends_the_api_key_header(self):
        with patch("triagent.worldtriathlon.requests.get", return_value=_resp(ENVELOPE)) as g:
            fetch_api("https://api.triathlon.org/v1/news", api_key="secret")
        assert g.call_args.kwargs["headers"]["apikey"] == "secret"

    def test_works_without_a_key(self):
        """Some deployments leave the news endpoint open; try it anyway."""
        with patch("triagent.worldtriathlon.requests.get", return_value=_resp(ENVELOPE)) as g:
            fetch_api("https://api.triathlon.org/v1/news")
        assert "apikey" not in g.call_args.kwargs["headers"]

    def test_returns_feedparser_shape(self):
        with patch("triagent.worldtriathlon.requests.get", return_value=_resp(ENVELOPE)):
            parsed = fetch_api("https://api.triathlon.org/v1/news")
        assert len(parsed["entries"]) == 2

    def test_non_json_response_degrades_to_empty(self):
        bad = MagicMock()
        bad.raise_for_status = MagicMock()
        bad.json.side_effect = ValueError("not json")
        bad.headers = {"content-type": "text/html"}
        with patch("triagent.worldtriathlon.requests.get", return_value=bad):
            parsed = fetch_api("https://api.triathlon.org/v1/news")
        assert parsed["entries"] == []


class TestPipelineIntegration:
    """The whole point is that it enters through the same door as RSS.

    These patch `requests.get` exactly once. `news` and `worldtriathlon` both
    do `import requests`, so they share one module object — patching
    `triagent.news.requests.get` and `triagent.worldtriathlon.requests.get`
    separately means the second silently overrides the first, and the test
    passes while exercising the wrong path.
    """

    RSS = (
        b'<?xml version="1.0"?><rss version="2.0"><channel>'
        b"<title>Other</title><item><title>Story</title>"
        b"<link>https://other.example/1</link>"
        b"<pubDate>Thu, 20 Aug 2026 09:00:00 +0000</pubDate>"
        b"</item></channel></rss>"
    )

    def _dispatch(self, *, api_error: Exception | None = None):
        """One fake for both transports, routed by URL."""

        def fake_get(url, **kw):
            if "api.triathlon.org" in url:
                if api_error:
                    raise api_error
                return _resp(ENVELOPE)
            m = MagicMock()
            m.content = self.RSS
            m.raise_for_status = MagicMock()
            m.headers = {"content-type": "application/rss+xml"}
            m.url = url
            return m

        return fake_get

    def test_api_urls_go_to_the_adapter_not_feedparser(self):
        from triagent.news import fetch_recent

        with patch("triagent.news.feedparser.parse") as fp:
            with patch("requests.get", side_effect=self._dispatch()):
                items = fetch_recent(
                    ["https://api.triathlon.org/v1/news"], max_age_hours=24 * 365
                )

        fp.assert_not_called()
        assert len(items) == 2
        assert items[0].source == "World Triathlon"

    def test_api_key_is_read_from_the_environment(self, monkeypatch):
        monkeypatch.setenv("WORLD_TRIATHLON_API_KEY", "from-env")
        from triagent.news import fetch_recent

        seen = {}

        def fake_get(url, **kw):
            seen.update(kw.get("headers") or {})
            return _resp(ENVELOPE)

        with patch("requests.get", side_effect=fake_get):
            fetch_recent(["https://api.triathlon.org/v1/news"], max_age_hours=24 * 365)

        assert seen.get("apikey") == "from-env"

    def test_api_items_count_as_governing_body(self):
        from triagent.news import fetch_recent, is_governing_body

        with patch("requests.get", side_effect=self._dispatch()):
            items = fetch_recent(
                ["https://api.triathlon.org/v1/news"], max_age_hours=24 * 365
            )
        assert items and all(is_governing_body(i) for i in items)

    def test_api_items_lead_the_prioritized_list(self):
        """Governing-body priority is the reason this adapter exists."""
        from triagent.news import fetch_recent, prioritize

        with patch("requests.get", side_effect=self._dispatch()):
            items = fetch_recent(
                ["https://other.example/feed", "https://api.triathlon.org/v1/news"],
                max_age_hours=24 * 365,
            )
        assert prioritize(items)[0].source == "World Triathlon"

    def test_api_items_obey_the_age_filter(self):
        from triagent.news import fetch_recent

        with patch("requests.get", side_effect=self._dispatch()):
            items = fetch_recent(
                ["https://api.triathlon.org/v1/news"], max_age_hours=1
            )
        assert items == [], "a real timestamp means the window applies"

    def test_api_items_obey_the_posted_ledger(self):
        from triagent.news import fetch_recent

        used = {ARTICLE_BASE + "paris-2028-qualification-criteria-confirmed"}
        with patch("requests.get", side_effect=self._dispatch()):
            items = fetch_recent(
                ["https://api.triathlon.org/v1/news"],
                max_age_hours=24 * 365,
                exclude=used,
            )
        assert [i.title for i in items] == ["World Cup Karlovy Vary preview"]

    def test_api_items_obey_the_per_source_cap(self):
        from triagent.news import fetch_recent

        with patch("requests.get", side_effect=self._dispatch()):
            items = fetch_recent(
                ["https://api.triathlon.org/v1/news"],
                max_age_hours=24 * 365,
                per_feed_limit=1,
            )
        assert len(items) == 1

    def test_api_failure_does_not_stop_other_feeds(self):
        from triagent.news import fetch_recent

        with patch(
            "requests.get", side_effect=self._dispatch(api_error=RuntimeError("down"))
        ):
            items = fetch_recent(
                ["https://api.triathlon.org/v1/news", "https://other.example/feed"],
                max_age_hours=24 * 365,
            )

        assert [i.source for i in items] == ["Other"]

    def test_html_is_stripped_from_api_summaries(self):
        from triagent.news import fetch_recent

        with patch("requests.get", side_effect=self._dispatch()):
            items = fetch_recent(
                ["https://api.triathlon.org/v1/news"], max_age_hours=24 * 365
            )
        combined = " ".join(i.summary for i in items)
        assert "<b>" not in combined and "stacked" in combined


def _http_error(status: int):
    """A requests.HTTPError carrying a response, as raise_for_status raises."""
    import requests as req

    resp = MagicMock()
    resp.status_code = status
    resp.headers = {"content-type": "application/json"}
    err = req.HTTPError(f"{status} Client Error", response=resp)
    m = MagicMock()
    m.status_code = status
    m.headers = {"content-type": "application/json"}
    m.raise_for_status = MagicMock(side_effect=err)
    return m


class TestAuthentication:
    """401 without a key is a configuration state, not a breakage.

    The endpoint was confirmed real by a 401 with a JSON content type — it
    exists and wants a key. Reporting that as a failed run is wrong twice
    over: it reads as "the adapter is broken" when nothing is broken, and it
    buries the one thing the operator actually has to do.
    """

    def test_unauthenticated_401_names_the_secret_to_set(self):
        from triagent.worldtriathlon import WorldTriathlonAuthError, fetch_api

        with patch("triagent.worldtriathlon.requests.get", return_value=_http_error(401)):
            with pytest.raises(WorldTriathlonAuthError) as exc:
                fetch_api("https://api.triathlon.org/v1/news")

        assert "WORLD_TRIATHLON_API_KEY" in str(exc.value)

    def test_403_is_treated_the_same_way(self):
        from triagent.worldtriathlon import WorldTriathlonAuthError, fetch_api

        with patch("triagent.worldtriathlon.requests.get", return_value=_http_error(403)):
            with pytest.raises(WorldTriathlonAuthError):
                fetch_api("https://api.triathlon.org/v1/news")

    def test_a_rejected_key_says_so_rather_than_asking_for_one(self):
        """Telling someone to set a key they already set is a dead end."""
        from triagent.worldtriathlon import WorldTriathlonAuthError, fetch_api

        with patch("triagent.worldtriathlon.requests.get", return_value=_http_error(401)):
            with pytest.raises(WorldTriathlonAuthError) as exc:
                fetch_api("https://api.triathlon.org/v1/news", api_key="wrong")

        assert "rejected" in str(exc.value).lower()

    def test_other_http_errors_are_not_disguised_as_auth(self):
        import requests as req
        from triagent.worldtriathlon import WorldTriathlonAuthError, fetch_api

        with patch("triagent.worldtriathlon.requests.get", return_value=_http_error(500)):
            with pytest.raises(req.HTTPError) as exc:
                fetch_api("https://api.triathlon.org/v1/news")
        assert not isinstance(exc.value, WorldTriathlonAuthError)

    def test_a_daily_run_survives_the_missing_key(self):
        """Until a key exists the source must degrade like any dead feed."""
        from triagent.news import fetch_recent

        def dispatch(url, **kw):
            if "api.triathlon.org" in url:
                return _http_error(401)
            m = MagicMock()
            m.content = TestPipelineIntegration.RSS
            m.raise_for_status = MagicMock()
            m.headers = {"content-type": "application/rss+xml"}
            m.url = url
            return m

        with patch("requests.get", side_effect=dispatch):
            items = fetch_recent(
                ["https://api.triathlon.org/v1/news", "https://other.example/feed"],
                max_age_hours=24 * 365,
            )
        assert [i.source for i in items] == ["Other"]


class TestDescribeAuthReporting:
    """`--mode apicheck` has to separate 'not set up yet' from 'broken'."""

    def test_unauthenticated_401_is_reported_as_needing_auth(self):
        from triagent.worldtriathlon import describe

        with patch("triagent.worldtriathlon.requests.get", return_value=_http_error(401)):
            report = describe("https://api.triathlon.org/v1/news")

        assert report["needs_auth"] is True
        assert report["authenticated"] is False

    def test_401_with_a_key_is_reported_as_a_rejected_key(self):
        from triagent.worldtriathlon import describe

        with patch("triagent.worldtriathlon.requests.get", return_value=_http_error(401)):
            report = describe("https://api.triathlon.org/v1/news", api_key="wrong")

        assert report["needs_auth"] is True
        assert report["authenticated"] is True

    def test_a_401_still_confirms_the_endpoint_exists(self):
        """The status and content type are the evidence the URL is right."""
        from triagent.worldtriathlon import describe

        with patch("triagent.worldtriathlon.requests.get", return_value=_http_error(401)):
            report = describe("https://api.triathlon.org/v1/news")

        assert report["status"] == 401
        assert "json" in report["content_type"]


class TestApicheckExitCodes:
    """The exit code drives whether CI paints the run red."""

    def _run(self, report):
        import triagent.__main__ as m

        with patch("triagent.worldtriathlon.describe", return_value=report):
            with patch.object(m.sys, "argv", ["triagent", "--mode", "apicheck"]):
                return m.main()

    def test_unconfigured_is_not_a_failure(self):
        assert self._run({
            "url": "u", "authenticated": False, "needs_auth": True, "status": 401,
            "message": "set the WORLD_TRIATHLON_API_KEY secret",
        }) == 0

    def test_a_rejected_key_is_a_failure(self):
        assert self._run({
            "url": "u", "authenticated": True, "needs_auth": True, "status": 401,
            "message": "the key was rejected",
        }) == 1

    def test_a_report_without_a_message_does_not_crash(self):
        """describe() always sets one, but a partial report must not KeyError."""
        assert self._run({
            "url": "u", "authenticated": False, "needs_auth": True, "status": 401,
        }) == 0

    def test_a_working_mapping_passes(self):
        assert self._run({
            "url": "u", "authenticated": True, "articles_found": 3,
            "mapped": {"title": "t", "url": "https://triathlon.org/news/x"},
        }) == 0

    def test_an_unreachable_endpoint_is_a_failure(self):
        assert self._run({"url": "u", "authenticated": False, "error": "boom"}) == 1


# --- Event-discovery flow -------------------------------------------------
#
# `api.triathlon.org/v1/news` (the first guess) turned out to 404, and
# research into World Triathlon's own docs explains why: there is no flat
# "all news" endpoint. News is only exposed scoped to an event
# (`/v1/events/{id}/news`) or a federation (whose `latest_news` reportedly
# returns null since a 2020s CMS migration). The confirmed field names from
# World Triathlon's own examples — entry_id, title, slug, entry_date, excerpt
# — match what to_feed() already expects, which is why these tests reuse them
# rather than inventing a new shape.
#
# Rather than hardcode one event_id (which goes stale the moment that event's
# news cycle ends), fetch_api asks the confirmed `/v1/events` listing for
# what is happening in a rolling window around today, then merges each
# event's own news. That is the "event-scoped, but not manually rotated"
# design.

EVENTS_PAGE = {
    "data": [
        {"event_id": 8001, "event_title": "2026 WTCS Hamburg"},
        {"event_id": 8002, "event_title": "2026 World Cup Karlovy Vary"},
    ]
}

EVENT_NEWS_8001 = [
    {
        "entry_id": 100552, "title": "Hamburg preview: the contenders",
        "slug": "hamburg-preview-the-contenders", "entry_date": "2026-08-19 09:00:00",
        "excerpt": "A look at who lines up.",
    },
]

EVENT_NEWS_8002 = [
    {
        "entry_id": 100553, "title": "Karlovy Vary wraps up",
        "slug": "karlovy-vary-wraps-up", "entry_date": "2026-08-20 18:00:00",
        "excerpt": "Results from the World Cup.",
    },
]


class TestEventsListUrlRecognition:
    """Distinguishing the bare listing endpoint from a specific event's sub-resource."""

    @pytest.mark.parametrize("url", [
        "https://api.triathlon.org/v1/events",
        "https://api.triathlon.org/v1/events/",
        "https://api.triathlon.org/v1/events?category_id=351",
    ])
    def test_recognises_the_listing_endpoint(self, url):
        from triagent.worldtriathlon import _is_events_list_url

        assert _is_events_list_url(url)

    @pytest.mark.parametrize("url", [
        "https://api.triathlon.org/v1/events/8001/news",
        "https://api.triathlon.org/v1/news",
        "https://api.triathlon.org/v1/federations/1",
    ])
    def test_does_not_match_a_specific_event_or_other_resource(self, url):
        from triagent.worldtriathlon import _is_events_list_url

        assert not _is_events_list_url(url)


class TestDateWindow:
    def test_window_brackets_today(self):
        from triagent.worldtriathlon import _date_window

        start, end = _date_window()
        today = dt.datetime.now(dt.timezone.utc).date().isoformat()
        assert start < today < end

    def test_window_respects_configured_lookback_and_lookahead(self):
        from triagent.worldtriathlon import (
            EVENT_LOOKAHEAD_DAYS,
            EVENT_LOOKBACK_DAYS,
            _date_window,
        )

        start, end = _date_window()
        today = dt.datetime.now(dt.timezone.utc).date()
        assert dt.date.fromisoformat(start) == today - dt.timedelta(days=EVENT_LOOKBACK_DAYS)
        assert dt.date.fromisoformat(end) == today + dt.timedelta(days=EVENT_LOOKAHEAD_DAYS)


class TestEventDiscovery:
    def test_extracts_id_and_title_pairs(self):
        from triagent.worldtriathlon import _discover_event_ids

        with patch("triagent.worldtriathlon.requests.get", return_value=_resp(EVENTS_PAGE)):
            pairs = _discover_event_ids(
                "https://api.triathlon.org/v1/events", api_key=None, timeout=10
            )
        assert pairs == [("8001", "2026 WTCS Hamburg"), ("8002", "2026 World Cup Karlovy Vary")]

    def test_start_and_end_date_are_sent(self):
        from triagent.worldtriathlon import _date_window, _discover_event_ids

        seen = {}

        def fake_get(url, **kw):
            seen["url"] = url
            return _resp(EVENTS_PAGE)

        with patch("triagent.worldtriathlon.requests.get", side_effect=fake_get):
            _discover_event_ids(
                "https://api.triathlon.org/v1/events", api_key=None, timeout=10
            )
        start, end = _date_window()
        assert f"start_date={start}" in seen["url"]
        assert f"end_date={end}" in seen["url"]

    def test_preserves_query_params_already_on_the_url(self):
        from triagent.worldtriathlon import _discover_event_ids

        seen = {}

        def fake_get(url, **kw):
            seen["url"] = url
            return _resp(EVENTS_PAGE)

        with patch("triagent.worldtriathlon.requests.get", side_effect=fake_get):
            _discover_event_ids(
                "https://api.triathlon.org/v1/events?category_id=351",
                api_key=None, timeout=10,
            )
        assert "category_id=351" in seen["url"]

    def test_skips_events_with_no_id(self):
        from triagent.worldtriathlon import _discover_event_ids

        payload = {"data": [{"event_title": "no id"}, {"event_id": 1, "event_title": "ok"}]}
        with patch("triagent.worldtriathlon.requests.get", return_value=_resp(payload)):
            pairs = _discover_event_ids(
                "https://api.triathlon.org/v1/events", api_key=None, timeout=10
            )
        assert pairs == [("1", "ok")]

    def test_caps_at_max_events_per_run(self):
        from triagent.worldtriathlon import MAX_EVENTS_PER_RUN, _discover_event_ids

        many = {"data": [
            {"event_id": n, "event_title": f"event {n}"} for n in range(MAX_EVENTS_PER_RUN + 10)
        ]}
        with patch("triagent.worldtriathlon.requests.get", return_value=_resp(many)):
            pairs = _discover_event_ids(
                "https://api.triathlon.org/v1/events", api_key=None, timeout=10
            )
        assert len(pairs) == MAX_EVENTS_PER_RUN


class TestFetchApiViaEventDiscovery:
    """fetch_api() on the bare /v1/events URL merges each event's own news."""

    def _dispatch(self, *, news_8002_error: Exception | None = None):
        def fake_get(url, **kw):
            if url.startswith("https://api.triathlon.org/v1/events/8001/news"):
                return _resp(EVENT_NEWS_8001)
            if url.startswith("https://api.triathlon.org/v1/events/8002/news"):
                if news_8002_error:
                    raise news_8002_error
                return _resp(EVENT_NEWS_8002)
            if "v1/events" in url:
                return _resp(EVENTS_PAGE)
            raise AssertionError(f"unexpected URL: {url}")

        return fake_get

    def test_merges_news_across_discovered_events(self):
        from triagent.worldtriathlon import fetch_api

        with patch("triagent.worldtriathlon.requests.get", side_effect=self._dispatch()):
            parsed = fetch_api("https://api.triathlon.org/v1/events")

        titles = {e["title"] for e in parsed["entries"]}
        assert titles == {"Hamburg preview: the contenders", "Karlovy Vary wraps up"}

    def test_entries_use_the_confirmed_field_mapping(self):
        from triagent.worldtriathlon import ARTICLE_BASE, fetch_api

        with patch("triagent.worldtriathlon.requests.get", side_effect=self._dispatch()):
            parsed = fetch_api("https://api.triathlon.org/v1/events")

        by_title = {e["title"]: e for e in parsed["entries"]}
        hamburg = by_title["Hamburg preview: the contenders"]
        assert hamburg["link"] == ARTICLE_BASE + "hamburg-preview-the-contenders"
        assert hamburg["published_parsed"][:6] == (2026, 8, 19, 9, 0, 0)

    def test_one_events_news_failing_does_not_drop_the_others(self):
        import requests as req
        from triagent.worldtriathlon import fetch_api

        with patch(
            "triagent.worldtriathlon.requests.get",
            side_effect=self._dispatch(news_8002_error=req.ConnectionError("down")),
        ):
            parsed = fetch_api("https://api.triathlon.org/v1/events")

        assert [e["title"] for e in parsed["entries"]] == ["Hamburg preview: the contenders"]

    def test_no_events_in_window_yields_no_entries_not_an_error(self):
        from triagent.worldtriathlon import fetch_api

        with patch("triagent.worldtriathlon.requests.get", return_value=_resp({"data": []})):
            parsed = fetch_api("https://api.triathlon.org/v1/events")
        assert parsed["entries"] == []

    def test_auth_failure_on_the_events_list_propagates(self):
        from triagent.worldtriathlon import WorldTriathlonAuthError, fetch_api

        with patch("triagent.worldtriathlon.requests.get", return_value=_http_error(401)):
            with pytest.raises(WorldTriathlonAuthError):
                fetch_api("https://api.triathlon.org/v1/events")

    def test_auth_failure_on_a_per_event_news_call_also_propagates(self):
        """Same key, same problem on every call — no point limping through the rest."""
        from triagent.worldtriathlon import WorldTriathlonAuthError, fetch_api

        def fake_get(url, **kw):
            if "v1/events" in url and "/news" not in url:
                return _resp(EVENTS_PAGE)
            return _http_error(401)

        with patch("triagent.worldtriathlon.requests.get", side_effect=fake_get):
            with pytest.raises(WorldTriathlonAuthError):
                fetch_api("https://api.triathlon.org/v1/events")


class TestDiscoveryPipelineIntegration:
    def test_default_endpoint_reaches_news_pipeline_end_to_end(self):
        from triagent.news import fetch_recent
        from triagent.worldtriathlon import DEFAULT_ENDPOINT

        def fake_get(url, **kw):
            if "/news" in url:
                return _resp(EVENT_NEWS_8001 if "8001" in url else EVENT_NEWS_8002)
            return _resp(EVENTS_PAGE)

        with patch("requests.get", side_effect=fake_get):
            items = fetch_recent([DEFAULT_ENDPOINT], max_age_hours=24 * 365)

        assert len(items) == 2
        assert all(i.source == "World Triathlon" for i in items)


class TestDescribeEventDiscovery:
    def test_reports_discovered_events(self):
        from triagent.worldtriathlon import describe

        def fake_get(url, **kw):
            if "/news" in url:
                return _resp(EVENT_NEWS_8001)
            return _resp(EVENTS_PAGE)

        with patch("triagent.worldtriathlon.requests.get", side_effect=fake_get):
            report = describe("https://api.triathlon.org/v1/events")

        assert report["events_found"] == 2
        assert {e["event_id"] for e in report["events"]} == {"8001", "8002"}
        assert report["mapped"]["title"] == "Hamburg preview: the contenders"
        assert report["articles_found"] == 1

    def test_zero_events_is_reported_distinctly_from_a_mapping_problem(self):
        """Empty calendar window must not suggest fixing LIST_KEYS."""
        from triagent.worldtriathlon import describe

        with patch("triagent.worldtriathlon.requests.get", return_value=_resp({"data": []})):
            report = describe("https://api.triathlon.org/v1/events")

        assert report["events_found"] == 0
        assert "date window" in report.get("message", "").lower()

    def test_auth_failure_is_still_reported_as_needs_auth(self):
        from triagent.worldtriathlon import describe

        with patch("triagent.worldtriathlon.requests.get", return_value=_http_error(401)):
            report = describe("https://api.triathlon.org/v1/events")

        assert report["needs_auth"] is True


class TestApicheckEventDiscoveryExitCodes:
    def _run(self, report):
        import triagent.__main__ as m

        with patch("triagent.worldtriathlon.describe", return_value=report):
            with patch.object(m.sys, "argv", ["triagent", "--mode", "apicheck"]):
                return m.main()

    def test_zero_events_passes_rather_than_suggesting_list_keys(self, capsys):
        assert self._run({
            "url": "u", "authenticated": True, "events_found": 0,
            "message": "no events found in the date window",
        }) == 0
        assert "LIST_KEYS" not in capsys.readouterr().out

    def test_events_with_a_working_sample_passes(self):
        assert self._run({
            "url": "u", "authenticated": True, "events_found": 2,
            "events": [{"event_id": "1", "event_title": "x"}],
            "articles_found": 1,
            "mapped": {"title": "t", "url": "https://triathlon.org/news/x"},
        }) == 0


# --- Confirmed field names from the real /v1/events/{id}/news response ----
#
# The feedcheck run reported real data: 5 events discovered, 10 articles on
# the sample event, but title/url/date all mapped to null. The actual keys
# are prefixed — news_title, news_slug, news_entry_date, news_excerpt,
# news_id — a different convention than the generic "Content API" docs
# example this adapter was originally built from. That earlier mapping was
# evidence-based for a different endpoint; this is evidence for the one
# actually in use.
#
# news_url is deliberately NOT added to URL_KEYS. The same response also
# carries a distinct news_api_url field, and without seeing raw values there
# is no way to tell which one is a browsable page — guessing wrong there
# would produce a dead link, which is worse than composing one from
# news_slug (already confirmed safe: triathlon.org/news/{slug} matches a
# real URL seen during earlier research). describe() now prints raw values
# for exactly this kind of ambiguous field so the next run resolves it from
# evidence instead of another guess.

REAL_NEWS_ITEM = {
    "news_id": 448210,
    "news_title": "Nyon set to crown FISU university champions",
    "news_slug": "nyon-set-to-crown-fisu-university-champions",
    "news_entry_date": "2026-08-20 07:15:00",
    "news_excerpt": "The best university triathletes gather in Switzerland.",
    "news_url": "https://triathlon.org/news/nyon-set-to-crown-fisu-university-champions",
    "news_api_url": "https://api.triathlon.org/v1/events/194998/news/448210",
    "author": "World Triathlon",
    "news_categories": ["university"],
    "tags": [],
}


class TestConfirmedEventNewsFieldNames:
    def test_maps_the_prefixed_title(self):
        from triagent.worldtriathlon import TITLE_KEYS, _first

        assert _first(REAL_NEWS_ITEM, TITLE_KEYS) == (
            "Nyon set to crown FISU university champions"
        )

    def test_maps_the_prefixed_date(self):
        from triagent.worldtriathlon import DATE_KEYS, _first, _parse_date

        raw = _first(REAL_NEWS_ITEM, DATE_KEYS)
        assert raw == "2026-08-20 07:15:00"
        assert _parse_date(raw) == dt.datetime(2026, 8, 20, 7, 15, tzinfo=dt.timezone.utc)

    def test_maps_the_prefixed_excerpt(self):
        from triagent.worldtriathlon import SUMMARY_KEYS, _first

        assert "university triathletes" in _first(REAL_NEWS_ITEM, SUMMARY_KEYS)

    def test_composes_the_url_from_the_prefixed_slug(self):
        """Not from news_url — see module note on the news_api_url ambiguity."""
        from triagent.worldtriathlon import ARTICLE_BASE, _article_url

        assert _article_url(REAL_NEWS_ITEM) == (
            ARTICLE_BASE + "nyon-set-to-crown-fisu-university-champions"
        )

    def test_end_to_end_through_to_feed(self):
        from triagent.worldtriathlon import ARTICLE_BASE, to_feed

        parsed = to_feed({"data": [REAL_NEWS_ITEM]})
        entry = parsed["entries"][0]
        assert entry["title"] == "Nyon set to crown FISU university champions"
        assert entry["link"] == ARTICLE_BASE + "nyon-set-to-crown-fisu-university-champions"
        assert entry["published_parsed"][:6] == (2026, 8, 20, 7, 15, 0)
        assert "university triathletes" in entry["summary"]


class TestDescribeSurfacesAmbiguousUrlFields:
    """A field that looks like a URL but isn't the chosen one must show its
    raw value, not just its name — that's what would have resolved the
    news_url vs news_api_url question in one run instead of two."""

    def test_reports_raw_values_for_unmapped_url_like_keys(self):
        from triagent.worldtriathlon import describe

        def fake_get(url, **kw):
            if "/news" in url:
                return _resp([REAL_NEWS_ITEM])
            return _resp(EVENTS_PAGE)

        with patch("triagent.worldtriathlon.requests.get", side_effect=fake_get):
            report = describe("https://api.triathlon.org/v1/events")

        assert report["unmapped_url_like_fields"] == {
            "news_url": "https://triathlon.org/news/nyon-set-to-crown-fisu-university-champions",
            "news_api_url": "https://api.triathlon.org/v1/events/194998/news/448210",
        }

    def test_does_not_repeat_a_field_already_used_for_the_mapped_url(self):
        from triagent.worldtriathlon import describe

        item = {"title": "t", "url": "https://triathlon.org/news/t", "slug": "t"}
        with patch("triagent.worldtriathlon.requests.get", return_value=_resp([item])):
            report = describe("https://api.triathlon.org/v1/events/1/news")

        assert "url" not in report.get("unmapped_url_like_fields", {})
