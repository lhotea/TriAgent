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
