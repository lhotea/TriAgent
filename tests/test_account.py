"""Tests for triagent.account — the daily follower and audience snapshot."""

from __future__ import annotations

import csv
import datetime as dt
import sys
from unittest.mock import MagicMock

import pytest

from triagent import account as acc
from triagent.config import Settings

NOW = dt.datetime(2026, 10, 22, 9, 0, tzinfo=dt.timezone.utc)


def client(profile=None, metrics=None):
    """A publisher stand-in. `metrics` maps metric name -> result dict (None = unavailable)."""
    c = MagicMock()
    c.account_profile.return_value = (
        {"followers_count": 151, "follows_count": 327, "media_count": 109}
        if profile is None else profile
    )
    metrics = metrics or {}
    c.account_metric.side_effect = lambda metric, **kw: metrics.get(metric)
    return c


FULL = {
    "reach": {"total": 400, "split": {"FOLLOWER": 300, "NON_FOLLOWER": 100}},
    "views": {"total": 1000, "split": {"FOLLOWER": 900, "NON_FOLLOWER": 100}},
    "profile_views": {"total": 12, "split": {}},
    "accounts_engaged": {"total": 20, "split": {}},
    "total_interactions": {"total": 31, "split": {}},
}


class TestCollectSnapshot:
    def test_builds_a_full_row(self):
        row = acc.collect_snapshot(client(metrics=FULL), now=NOW)
        assert row["date"] == "2026-10-22"
        assert row["collected"] == "2026-10-22T09:00:00Z"
        assert (row["followers"], row["following"], row["posts"]) == (151, 327, 109)
        assert row["reach_7d"] == 400
        assert row["reach_nonfollower_pct_7d"] == "25.0"
        assert row["views_nonfollower_pct_7d"] == "10.0"
        assert (row["profile_views_7d"], row["accounts_engaged_7d"], row["interactions_7d"]) == (12, 20, 31)

    def test_every_column_is_present(self):
        assert set(acc.collect_snapshot(client(), now=NOW)) == set(acc.FIELDS)

    def test_window_is_the_trailing_seven_days(self):
        c = client(metrics=FULL)
        acc.collect_snapshot(c, now=NOW)
        kwargs = c.account_metric.call_args.kwargs
        assert kwargs["until"] == int(NOW.timestamp())
        assert kwargs["until"] - kwargs["since"] == 7 * 86400

    def test_only_reach_and_views_ask_for_the_split(self):
        c = client(metrics=FULL)
        acc.collect_snapshot(c, now=NOW)
        asked = {call.args[0]: call.kwargs["split"] for call in c.account_metric.call_args_list}
        assert asked == {
            "reach": True, "views": True, "profile_views": False,
            "accounts_engaged": False, "total_interactions": False,
        }

    def test_unavailable_figures_are_blank_not_zero(self):
        row = acc.collect_snapshot(client(metrics={"reach": FULL["reach"]}), now=NOW)
        assert row["reach_7d"] == 400
        assert row["views_7d"] == "" and row["views_nonfollower_pct_7d"] == ""
        assert row["profile_views_7d"] == ""

    def test_a_real_zero_is_kept(self):
        row = acc.collect_snapshot(
            client(metrics={"accounts_engaged": {"total": 0, "split": {}}}), now=NOW
        )
        assert row["accounts_engaged_7d"] == 0

    def test_total_without_a_split_leaves_the_share_blank(self):
        row = acc.collect_snapshot(
            client(metrics={"reach": {"total": 90, "split": {}}}), now=NOW
        )
        assert row["reach_7d"] == 90 and row["reach_nonfollower_pct_7d"] == ""

    def test_unreadable_profile_does_not_stop_the_metrics(self):
        row = acc.collect_snapshot(client(profile={}, metrics=FULL), now=NOW)
        assert row["followers"] == ""
        assert row["reach_7d"] == 400


class TestMergeSnapshot:
    def _row(self, date, followers, **extra):
        row = {k: "" for k in acc.FIELDS}
        row.update(date=date, followers=followers, **extra)
        return row

    def test_creates_the_history(self, tmp_path):
        merged = acc.merge_snapshot(tmp_path / "a.csv", self._row("2026-10-22", 151))
        assert [r["date"] for r in merged] == ["2026-10-22"]

    def test_newest_first_and_one_row_per_day(self, tmp_path):
        path = tmp_path / "a.csv"
        acc.write_csv(path, acc.merge_snapshot(path, self._row("2026-10-20", 150)))
        acc.write_csv(path, acc.merge_snapshot(path, self._row("2026-10-22", 152)))
        merged = acc.merge_snapshot(path, self._row("2026-10-21", 151))
        assert [r["date"] for r in merged] == ["2026-10-22", "2026-10-21", "2026-10-20"]

    def test_a_rerun_the_same_day_replaces_rather_than_duplicates(self, tmp_path):
        path = tmp_path / "a.csv"
        acc.write_csv(path, acc.merge_snapshot(path, self._row("2026-10-22", 151)))
        merged = acc.merge_snapshot(path, self._row("2026-10-22", 153))
        assert len(merged) == 1 and merged[0]["followers"] == 153

    def test_a_blank_reading_never_erases_an_earlier_one(self, tmp_path):
        """A re-run during an API hiccup must not wipe a good morning reading."""
        path = tmp_path / "a.csv"
        good = self._row("2026-10-22", 151, reach_7d=400)
        acc.write_csv(path, acc.merge_snapshot(path, good))
        merged = acc.merge_snapshot(path, self._row("2026-10-22", "", reach_7d=""))
        assert merged[0]["followers"] == "151"
        assert merged[0]["reach_7d"] == "400"

    def test_a_real_zero_does_overwrite(self, tmp_path):
        path = tmp_path / "a.csv"
        acc.write_csv(path, acc.merge_snapshot(path, self._row("2026-10-22", 151, reach_7d=400)))
        assert acc.merge_snapshot(path, self._row("2026-10-22", 151, reach_7d=0))[0]["reach_7d"] == 0


class TestWriteCsv:
    def test_header_is_the_declared_columns(self, tmp_path):
        path = acc.write_csv(tmp_path / "x" / "a.csv", [])
        assert next(csv.reader(path.open())) == acc.FIELDS

    def test_extra_keys_are_ignored(self, tmp_path):
        row = {k: "" for k in acc.FIELDS}
        row["unexpected"] = "x"
        acc.write_csv(tmp_path / "a.csv", [row])  # must not raise


def _history(*pairs):
    """[(date, followers), ...] newest first, as merge_snapshot returns them."""
    rows = []
    for date, followers in pairs:
        row = {k: "" for k in acc.FIELDS}
        row.update(date=date, followers=followers)
        rows.append(row)
    return rows


class TestFollowersChange:
    def test_uses_the_newest_row_at_least_a_week_old(self):
        rows = _history(("2026-10-22", 155), ("2026-10-20", 152), ("2026-10-15", 150), ("2026-10-10", 140))
        assert acc._followers_change(rows, 7) == (5, 7)

    def test_short_history_reports_the_span_it_actually_has(self):
        rows = _history(("2026-10-22", 155), ("2026-10-20", 152))
        assert acc._followers_change(rows, 7) == (3, 2)

    def test_one_row_is_no_trend(self):
        assert acc._followers_change(_history(("2026-10-22", 155)), 7) is None

    def test_rows_with_a_blank_follower_count_are_skipped(self):
        rows = _history(("2026-10-22", 155), ("2026-10-21", ""), ("2026-10-14", 150))
        assert acc._followers_change(rows, 7) == (5, 8)

    def test_a_loss_is_negative(self):
        assert acc._followers_change(_history(("2026-10-22", 148), ("2026-10-15", 151)), 7) == (-3, 7)


class TestSummariseAccount:
    def test_empty(self):
        assert acc.summarise_account([]) == "No account data yet."

    def test_first_snapshot_has_no_trend_yet(self):
        text = acc.summarise_account(_history(("2026-10-22", 151)))
        assert "followers 151 (first snapshot" in text

    def test_full_readout(self):
        rows = _history(("2026-10-22", 155), ("2026-10-15", 151), ("2026-09-22", 150))
        rows[0].update(
            following=327, posts=109, reach_7d=400, reach_nonfollower_pct_7d="25.0",
            views_7d=1000, views_nonfollower_pct_7d="10.0",
            profile_views_7d=12, accounts_engaged_7d=20, interactions_7d=31,
        )
        text = acc.summarise_account(rows)
        assert "followers 155 (+4 over 7 days; +5 over 30 days)" in text
        assert "following 327 · posts 109" in text
        assert "reach 400 (25.0% from non-followers)" in text
        assert "views 1000 (10.0% from non-followers)" in text
        assert "profile views 12" in text and "interactions 31" in text
        assert "not available" not in text

    def test_short_history_is_labelled_not_passed_off_as_a_week(self):
        text = acc.summarise_account(_history(("2026-10-22", 155), ("2026-10-20", 152)))
        assert "+3 over the 2 day(s) recorded so far" in text

    def test_names_what_was_unavailable_and_keeps_the_follower_count(self):
        rows = _history(("2026-10-22", 151))
        rows[0].update(reach_7d=400)
        text = acc.summarise_account(rows)
        assert "followers 151" in text
        assert "not available today: views, profile_views, accounts_engaged, total_interactions" in text

    def test_unreadable_follower_count_is_said_plainly(self):
        assert "followers: not readable today" in acc.summarise_account(_history(("2026-10-22", "")))


class TestRecordSnapshot:
    def test_collects_merges_writes_and_reports(self, tmp_path):
        path = tmp_path / "account.csv"
        text = acc.record_snapshot(client(metrics=FULL), path, now=NOW)
        assert "followers 151" in text
        assert [r["date"] for r in csv.DictReader(path.open())] == ["2026-10-22"]

    def test_running_twice_in_a_day_keeps_one_row(self, tmp_path):
        path = tmp_path / "account.csv"
        acc.record_snapshot(client(metrics=FULL), path, now=NOW)
        acc.record_snapshot(client(metrics=FULL), path, now=NOW + dt.timedelta(hours=3))
        assert len(list(csv.DictReader(path.open()))) == 1


class FakeInstagram:
    """Stands in for InstagramPublisher in the insights CLI mode."""

    fail_account = False

    def __init__(self, ig_user_id, access_token, **kwargs):
        pass

    def list_media(self, limit=25):
        return []

    def media_insights(self, media_id):
        return {}

    def account_profile(self):
        if self.fail_account:
            raise RuntimeError("boom")
        return {"followers_count": 151, "follows_count": 327, "media_count": 109}

    def account_metric(self, metric, **kwargs):
        return None


class TestInsightsMode:
    """The CLI path: per-post numbers first, the account snapshot after and on its own terms."""

    def _run(self, monkeypatch, capsys, tmp_path, fail_account=False):
        from triagent import publisher
        from triagent.__main__ import main

        FakeInstagram.fail_account = fail_account
        monkeypatch.setattr(publisher, "InstagramPublisher", FakeInstagram)
        settings = Settings(
            anthropic_api_key=None, ig_user_id="1", ig_access_token="t",
            public_image_base_url=None, brand_handle="@x", brand_name="X",
            image_path=tmp_path / "daily.png",
        )
        monkeypatch.setattr(Settings, "from_env", classmethod(lambda cls: settings))
        monkeypatch.setattr(sys, "argv", ["triagent", "--mode", "insights"])
        code = main()
        out = capsys.readouterr()
        return code, out.out, out.err

    def test_writes_both_files_and_prints_the_account_readout(self, monkeypatch, capsys, tmp_path):
        code, out, _ = self._run(monkeypatch, capsys, tmp_path)
        assert code == 0
        assert (tmp_path / "insights.csv").exists()
        assert (tmp_path / "account.csv").exists()
        assert "followers 151" in out

    def test_an_account_failure_costs_neither_the_posts_nor_the_run(self, monkeypatch, capsys, tmp_path):
        """account_profile is allowed to raise here: the CLI must still finish and keep insights.csv."""
        code, out, err = self._run(monkeypatch, capsys, tmp_path, fail_account=True)
        assert code == 0
        assert (tmp_path / "insights.csv").exists()
        assert "account snapshot failed" in err
