"""Tests for triagent.schedule — which weekdays to post, and when to hold until."""

from __future__ import annotations

import datetime as dt
import logging
import sys
from zoneinfo import ZoneInfo

import pytest

from triagent import schedule as sch
from triagent.__main__ import main

ZURICH = ZoneInfo("Europe/Zurich")
UTC = dt.timezone.utc


def utc(*args) -> dt.datetime:
    return dt.datetime(*args, tzinfo=UTC)


class TestParsePostDays:
    def test_blank_and_none_use_the_default(self):
        assert sch.parse_post_days(None) == ("Wed", "Fri", "Sat")
        assert sch.parse_post_days("") == ("Wed", "Fri", "Sat")
        assert sch.parse_post_days("   ") == ("Wed", "Fri", "Sat")

    def test_unset_github_variable_is_an_empty_string(self):
        """GitHub substitutes '' for an unset vars.X, so '' must mean 'default'."""
        assert sch.parse_post_days("") == sch.parse_post_days(sch.DEFAULT_POST_DAYS)

    def test_case_names_and_separators(self):
        assert sch.parse_post_days("mon, WEDNESDAY  fri") == ("Mon", "Wed", "Fri")

    def test_order_is_kept_and_repeats_collapse(self):
        assert sch.parse_post_days("Sat,Wed,Sat") == ("Sat", "Wed")

    @pytest.mark.parametrize("word", ["daily", "ALL", "*", "every"])
    def test_every_day_spellings(self, word):
        assert sch.parse_post_days(word) == sch.WEEKDAYS

    def test_unknown_word_raises_instead_of_being_skipped(self):
        """A typo must not quietly become 'never post' or 'post every day'."""
        with pytest.raises(ValueError, match="Wensday"):
            sch.parse_post_days("Mon,Wensday")

    def test_every_day_word_cannot_be_mixed_with_names(self):
        with pytest.raises(ValueError):
            sch.parse_post_days("Wed,daily")


class TestParsePublishAt:
    def test_default_when_blank(self):
        assert sch.parse_publish_at("") == dt.time(19, 0)
        assert sch.parse_publish_at(None) == dt.time(19, 0)

    def test_valid_times(self):
        assert sch.parse_publish_at("7:05") == dt.time(7, 5)
        assert sch.parse_publish_at(" 18:30 ") == dt.time(18, 30)

    @pytest.mark.parametrize("bad", ["24:00", "19:60", "19", "7pm", "19:0", "noon"])
    def test_rejects_anything_else(self, bad):
        with pytest.raises(ValueError, match="PUBLISH_AT"):
            sch.parse_publish_at(bad)


class TestLoadZone:
    def test_default_is_zurich(self):
        assert sch.load_zone("").key == "Europe/Zurich"
        assert sch.load_zone(None).key == "Europe/Zurich"

    def test_unknown_zone_is_a_config_error(self):
        with pytest.raises(ValueError, match="POST_TZ"):
            sch.load_zone("Mars/Olympus_Mons")


class TestPostDay:
    def test_weekday_follows_the_local_calendar_not_utc(self):
        """23:30 UTC on a Tuesday is already Wednesday 01:30 in Zurich (CEST)."""
        moment = utc(2026, 10, 20, 23, 30)
        assert moment.weekday() == 1  # Tuesday in UTC
        assert sch.weekday_name(moment, ZURICH) == "Wed"
        assert sch.is_post_day(moment, ("Wed",), ZURICH)
        assert not sch.is_post_day(moment, ("Tue",), ZURICH)

    def test_naive_datetime_is_read_as_utc(self):
        assert sch.weekday_name(dt.datetime(2026, 10, 21, 12, 0), ZURICH) == "Wed"

    def test_not_a_posting_day(self):
        assert not sch.is_post_day(utc(2026, 10, 19, 12, 0), ("Wed", "Fri"), ZURICH)


class TestSecondsUntil:
    def test_summer_slot(self):
        """12:00 CEST to 19:00 CEST is seven hours."""
        assert sch.seconds_until(utc(2026, 8, 5, 10, 0), dt.time(19, 0), ZURICH) == 7 * 3600

    def test_winter_slot(self):
        """11:00 CET to 19:00 CET is eight hours — the UTC offset changed, the slot did not."""
        assert sch.seconds_until(utc(2026, 12, 2, 10, 0), dt.time(19, 0), ZURICH) == 8 * 3600

    def test_slot_already_passed(self):
        assert sch.seconds_until(utc(2026, 8, 5, 20, 30), dt.time(19, 0), ZURICH) == 0

    def test_exactly_at_the_slot(self):
        assert sch.seconds_until(utc(2026, 8, 5, 17, 0), dt.time(19, 0), ZURICH) == 0

    def test_clocks_go_back_and_the_day_is_an_hour_longer(self):
        """25 Oct 2026: 01:00 CEST is 23:00 UTC the day before. 19:00 CET is 18:00 UTC.

        That is 19 real hours. Subtracting the two wall-clock readings (01:00 and
        19:00) would give 18, which is the bug converting to UTC first avoids.
        """
        now = utc(2026, 10, 24, 23, 0)
        assert sch.seconds_until(now, dt.time(19, 0), ZURICH) == 19 * 3600

    def test_clocks_go_forward_and_the_day_is_an_hour_shorter(self):
        """28 Mar 2027: 01:00 CET is 00:00 UTC. 19:00 CEST is 17:00 UTC — 17 real hours."""
        now = utc(2027, 3, 28, 0, 0)
        assert sch.seconds_until(now, dt.time(19, 0), ZURICH) == 17 * 3600


class TestDecide:
    SCHEDULED = {"GITHUB_EVENT_NAME": "schedule"}

    def test_posting_day(self):
        d = sch.decide({**self.SCHEDULED, "POST_DAYS": "Wed"}, now=utc(2026, 10, 21, 11, 0))
        assert d.post and "Wed" in d.reason

    def test_other_day_is_skipped_with_a_reason(self):
        d = sch.decide({**self.SCHEDULED, "POST_DAYS": "Wed"}, now=utc(2026, 10, 19, 11, 0))
        assert not d.post
        assert "Mon" in d.reason and "skipping" in d.reason

    def test_default_days_leave_sunday_monday_tuesday_free(self):
        for day, expected in ((18, False), (19, False), (20, False), (21, True), (23, True), (24, True)):
            assert sch.decide(self.SCHEDULED, now=utc(2026, 10, day, 11, 0)).post is expected

    def test_blank_vars_fall_back_to_defaults(self):
        env = {**self.SCHEDULED, "POST_DAYS": "", "POST_TZ": "", "PUBLISH_AT": ""}
        assert sch.decide(env, now=utc(2026, 10, 21, 11, 0)).post

    def test_manual_runs_always_post(self):
        """Dispatching by hand on a Monday must not be refused as 'not a posting day'."""
        env = {"GITHUB_EVENT_NAME": "workflow_dispatch", "POST_DAYS": "Wed"}
        d = sch.decide(env, now=utc(2026, 10, 19, 11, 0))
        assert d.post and "workflow_dispatch" in d.reason

    def test_no_event_name_counts_as_manual(self):
        assert sch.decide({}, now=utc(2026, 10, 19, 11, 0)).post

    def test_a_typo_fails_a_scheduled_run_loudly(self):
        with pytest.raises(ValueError, match="POST_DAYS"):
            sch.decide({**self.SCHEDULED, "POST_DAYS": "Wed,Frid"})

    def test_a_run_delayed_past_midnight_is_skipped_not_posted_at_dawn(self):
        """Thursday's cron started 00:30 Friday (22:30 UTC Thursday). It reads 'Friday', passes
        a Friday gate, and would publish at the 5h cap — 05:40 — before Friday's own run
        posts again at 19:00."""
        env = {**self.SCHEDULED, "POST_DAYS": "Fri"}
        d = sch.decide(env, now=utc(2026, 10, 22, 22, 30))
        assert not d.post
        assert "previous day's run" in d.reason and "18h30m" in d.reason

    def test_an_on_time_start_is_not_mistaken_for_a_stale_one(self):
        """The cron's own start (11:17 UTC) is 5h43m before a 19:00 CEST slot in summer and
        6h43m in winter — well inside the limit."""
        env = {**self.SCHEDULED, "POST_DAYS": "Wed"}
        assert sch.decide(env, now=utc(2026, 8, 5, 11, 17)).post      # summer, a Wednesday
        assert sch.decide(env, now=utc(2026, 12, 2, 11, 17)).post     # winter, a Wednesday

    def test_exactly_twelve_hours_before_the_slot_is_still_todays_run(self):
        env = {**self.SCHEDULED, "POST_DAYS": "Wed"}
        assert sch.decide(env, now=utc(2026, 10, 21, 5, 0)).post      # 07:00 CEST, slot 19:00
        assert not sch.decide(env, now=utc(2026, 10, 21, 4, 59)).post  # a minute earlier

    def test_a_manual_run_at_any_hour_is_never_stale(self):
        env = {"GITHUB_EVENT_NAME": "workflow_dispatch", "POST_DAYS": "Fri"}
        assert sch.decide(env, now=utc(2026, 10, 22, 22, 30)).post

    def test_a_later_slot_does_not_make_every_run_look_stale(self):
        """Someone moves PUBLISH_AT to 21:00 without moving the cron: an on-time start is 8h43m
        before the slot, which must still count as today's run."""
        env = {**self.SCHEDULED, "POST_DAYS": "Wed", "PUBLISH_AT": "21:00"}
        assert sch.decide(env, now=utc(2026, 8, 5, 11, 17)).post

    def test_a_bad_publish_time_is_caught_before_anything_is_built(self):
        """Otherwise it would surface after the build, hours before the post."""
        with pytest.raises(ValueError, match="PUBLISH_AT"):
            sch.decide({**self.SCHEDULED, "PUBLISH_AT": "7pm"}, now=utc(2026, 10, 21, 11, 0))


class FakeClock:
    """Stands in for both the clock and sleep, so waits take no real time."""

    def __init__(self, start: dt.datetime):
        self.now = start
        self.sleeps: list[float] = []

    def __call__(self) -> dt.datetime:
        return self.now

    def sleep(self, seconds: float) -> None:
        self.sleeps.append(seconds)
        self.now += dt.timedelta(seconds=seconds)


class TestWaitForPublishTime:
    def _wait(self, clock, **kw):
        return sch.wait_for_publish_time(
            dt.time(19, 0), ZURICH, clock=clock, sleep=clock.sleep, **kw
        )

    def test_returns_immediately_when_the_slot_has_passed(self):
        """A run that started late publishes late; it does not wait for tomorrow."""
        clock = FakeClock(utc(2026, 8, 5, 20, 30))
        assert self._wait(clock) == 0
        assert clock.sleeps == []

    def test_waits_exactly_until_the_slot(self):
        clock = FakeClock(utc(2026, 8, 5, 14, 0))  # 16:00 CEST
        waited = self._wait(clock)
        assert waited == 3 * 3600
        assert clock.now == utc(2026, 8, 5, 17, 0)  # 19:00 CEST

    def test_sleeps_in_chunks_with_a_short_final_one(self):
        clock = FakeClock(utc(2026, 8, 5, 16, 53))  # 7 minutes before the slot
        self._wait(clock, chunk=300)
        assert clock.sleeps == [300, 120]

    def test_cap_limits_the_wait_and_says_how_early_it_will_publish(self, caplog):
        clock = FakeClock(utc(2026, 8, 5, 8, 0))  # 10:00 CEST: 9h from the slot
        with caplog.at_level(logging.WARNING, logger="triagent.schedule"):
            waited = self._wait(clock, max_wait=5 * 3600)
        assert waited == 5 * 3600
        assert clock.now == utc(2026, 8, 5, 13, 0)
        assert any("early" in r.message for r in caplog.records)

    def test_survives_a_sleep_that_overshoots(self):
        """If the machine slept longer than asked the loop must stop, not go negative."""
        clock = FakeClock(utc(2026, 8, 5, 16, 50))
        real_sleep = clock.sleep
        clock.sleep = lambda s: real_sleep(s + 3600)  # wakes an hour late
        self._wait(clock)
        assert clock.now > utc(2026, 8, 5, 17, 0)
        assert len(clock.sleeps) == 1


def run_main(monkeypatch, capsys, mode, env):
    monkeypatch.setattr(sys, "argv", ["triagent", "--mode", mode])
    for key in ("GITHUB_EVENT_NAME", "POST_DAYS", "POST_TZ", "PUBLISH_AT"):
        monkeypatch.delenv(key, raising=False)
    for key, value in env.items():
        monkeypatch.setenv(key, value)
    code = main()
    out = capsys.readouterr()
    return code, out.out, out.err


class TestGateMode:
    def test_stdout_is_only_the_output_line(self, monkeypatch, capsys):
        """The workflow appends stdout to $GITHUB_OUTPUT, so nothing else may be on it."""
        monkeypatch.setattr(sch, "decide", lambda env, now=None: sch.Decision(True, "because"))
        code, out, err = run_main(monkeypatch, capsys, "gate", {})
        assert (code, out) == (0, "post=true\n")
        assert "because" in err

    def test_false_is_reported_as_an_output_not_a_failure(self, monkeypatch, capsys):
        monkeypatch.setattr(sch, "decide", lambda env, now=None: sch.Decision(False, "no"))
        code, out, _ = run_main(monkeypatch, capsys, "gate", {})
        assert (code, out) == (0, "post=false\n")

    def test_bad_config_fails_the_job_and_leaves_stdout_empty(self, monkeypatch, capsys):
        code, out, err = run_main(
            monkeypatch, capsys, "gate",
            {"GITHUB_EVENT_NAME": "schedule", "POST_DAYS": "Funday"},
        )
        assert code == 2
        assert out == ""
        assert "config error" in err and "Funday" in err


class TestWaitMode:
    def test_manual_runs_do_not_wait(self, monkeypatch, capsys):
        monkeypatch.setattr(
            sch, "wait_for_publish_time",
            lambda *a, **k: pytest.fail("a manual run must publish immediately"),
        )
        code, _, err = run_main(monkeypatch, capsys, "wait", {"GITHUB_EVENT_NAME": "workflow_dispatch"})
        assert code == 0
        assert "immediately" in err

    def test_scheduled_runs_wait_for_the_configured_slot(self, monkeypatch, capsys):
        seen = {}

        def fake_wait(publish_at, zone, **kwargs):
            seen["slot"], seen["zone"] = publish_at, zone.key
            return 600.0

        monkeypatch.setattr(sch, "wait_for_publish_time", fake_wait)
        code, _, err = run_main(
            monkeypatch, capsys, "wait",
            {"GITHUB_EVENT_NAME": "schedule", "PUBLISH_AT": "18:30", "POST_TZ": "Europe/Paris"},
        )
        assert code == 0
        assert seen == {"slot": dt.time(18, 30), "zone": "Europe/Paris"}
        assert "10 min" in err

    def test_bad_slot_is_a_config_error(self, monkeypatch, capsys):
        code, _, err = run_main(
            monkeypatch, capsys, "wait",
            {"GITHUB_EVENT_NAME": "schedule", "PUBLISH_AT": "late"},
        )
        assert code == 2 and "PUBLISH_AT" in err
