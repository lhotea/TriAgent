"""Tests for triagent.insights — per-post performance tracking."""

from __future__ import annotations

import csv
from unittest.mock import MagicMock

from triagent.insights import (
    FIELDS,
    _engagement_rate,
    _hook_of,
    collect_rows,
    merge_rows,
    summarise,
    write_csv,
)


def _publisher(media, insights):
    p = MagicMock()
    p.list_media.return_value = media
    p.media_insights.side_effect = lambda mid: insights.get(mid, {})
    return p


class TestHook:
    def test_takes_first_non_empty_line(self):
        assert _hook_of("\n\nReal hook here\n\nbody") == "Real hook here"

    def test_empty_caption(self):
        assert _hook_of(None) == "" and _hook_of("") == ""


class TestEngagementRate:
    def test_uses_total_interactions_over_reach(self):
        assert _engagement_rate({"reach": 200, "total_interactions": 20}) == "10.0"

    def test_sums_components_when_total_absent(self):
        row = {"reach": 100, "likes": 5, "comments": 2, "saved": 2, "shares": 1}
        assert _engagement_rate(row) == "10.0"

    def test_blank_without_reach(self):
        """Rate is meaningless before reach exists — don't invent a zero."""
        assert _engagement_rate({"reach": 0, "likes": 5}) == ""

    def test_blank_when_reach_missing(self):
        assert _engagement_rate({"likes": 5}) == ""


class TestCollectRows:
    def test_builds_a_row_per_post(self):
        pub = _publisher(
            [
                {"id": "1", "media_type": "CAROUSEL_ALBUM", "timestamp": "2026-08-10",
                 "permalink": "https://x/1", "caption": "Hook one\n\nbody"},
                {"id": "2", "media_type": "REELS", "timestamp": "2026-08-09",
                 "permalink": "https://x/2", "caption": "Hook two"},
            ],
            {"1": {"reach": 100, "likes": 10, "saved": 3}},
        )
        rows = collect_rows(pub)
        assert [r["media_id"] for r in rows] == ["1", "2"]
        assert rows[0]["hook"] == "Hook one"
        assert rows[0]["saved"] == 3

    def test_post_without_insights_still_yields_a_row(self):
        """A post too new for metrics must still be tracked, not dropped."""
        pub = _publisher(
            [{"id": "1", "media_type": "IMAGE", "timestamp": "t",
              "permalink": "p", "caption": "c"}],
            {},
        )
        rows = collect_rows(pub)
        assert len(rows) == 1
        assert rows[0]["reach"] == ""
        assert rows[0]["engagement_rate"] == ""


class TestMergeRows:
    def _row(self, mid, published, reach):
        return {"media_id": mid, "published": published, "reach": reach, "hook": "h"}

    def test_adds_new_rows(self, tmp_path):
        out = tmp_path / "i.csv"
        merged = merge_rows(out, [self._row("1", "2026-08-10", 10)])
        assert len(merged) == 1

    def test_refreshes_rather_than_duplicates(self, tmp_path):
        """Insights mature for days; re-polling must correct the row."""
        out = tmp_path / "i.csv"
        write_csv(out, merge_rows(out, [self._row("1", "2026-08-10", 10)]))
        merged = merge_rows(out, [self._row("1", "2026-08-10", 250)])
        assert len(merged) == 1
        assert merged[0]["reach"] == 250

    def test_keeps_history_not_in_this_batch(self, tmp_path):
        out = tmp_path / "i.csv"
        write_csv(out, merge_rows(out, [self._row("old", "2026-01-01", 5)]))
        merged = merge_rows(out, [self._row("new", "2026-08-10", 9)])
        assert {r["media_id"] for r in merged} == {"old", "new"}

    def test_sorted_newest_first(self, tmp_path):
        out = tmp_path / "i.csv"
        merged = merge_rows(
            out,
            [self._row("a", "2026-01-01", 1), self._row("b", "2026-08-10", 2)],
        )
        assert [r["media_id"] for r in merged] == ["b", "a"]


class TestWriteCsv:
    def test_writes_declared_header(self, tmp_path):
        out = tmp_path / "i.csv"
        write_csv(out, [{"media_id": "1", "reach": 5}])
        with out.open(newline="", encoding="utf-8") as fh:
            reader = csv.DictReader(fh)
            assert reader.fieldnames == FIELDS
            assert next(reader)["media_id"] == "1"

    def test_ignores_unknown_keys(self, tmp_path):
        out = tmp_path / "i.csv"
        write_csv(out, [{"media_id": "1", "surprise": "x"}])
        assert "surprise" not in out.read_text()

    def test_roundtrips_through_merge(self, tmp_path):
        out = tmp_path / "i.csv"
        write_csv(out, [{"media_id": "1", "published": "2026-08-10", "reach": 7}])
        merged = merge_rows(out, [])
        assert merged[0]["reach"] == "7"


class TestSummarise:
    def test_says_so_when_no_data_yet(self):
        assert "enough data" in summarise([{"media_id": "1", "engagement_rate": ""}])

    def test_ranks_by_rate_not_raw_counts(self):
        """A later post with more followers must not outrank a better one."""
        rows = [
            {"hook": "big reach", "engagement_rate": "1.0", "reach": 5000, "saved": 1},
            {"hook": "punchy", "engagement_rate": "9.0", "reach": 100, "saved": 20},
        ]
        assert summarise(rows).index("punchy") < summarise(rows).index("big reach")


class TestPublishedParts:
    """Hour and weekday are recorded so posting time is analysable at all —
    a fixed schedule cannot be evaluated from its own data."""

    def test_parses_api_timestamp_format(self):
        from triagent.insights import _published_parts

        assert _published_parts("2026-08-10T17:04:12+0000") == ("17", "Mon")

    def test_parses_z_suffix(self):
        from triagent.insights import _published_parts

        assert _published_parts("2026-08-10T17:04:12Z") == ("17", "Mon")

    def test_normalises_to_utc(self):
        """A +02:00 timestamp at 19:00 local is hour 17 UTC."""
        from triagent.insights import _published_parts

        assert _published_parts("2026-08-10T19:04:12+02:00")[0] == "17"

    def test_blank_on_missing_or_bad_input(self):
        from triagent.insights import _published_parts

        assert _published_parts(None) == ("", "")
        assert _published_parts("not a date") == ("", "")

    def test_collect_rows_records_the_hour(self):
        pub = MagicMock()
        pub.list_media.return_value = [
            {"id": "1", "media_type": "IMAGE", "timestamp": "2026-08-10T17:00:00+0000",
             "permalink": "p", "caption": "c"}
        ]
        pub.media_insights.return_value = {}
        row = collect_rows(pub)[0]
        assert row["published_hour_utc"] == "17"
        assert row["weekday"] == "Mon"


def test_hour_and_weekday_are_csv_columns():
    assert "published_hour_utc" in FIELDS and "weekday" in FIELDS
