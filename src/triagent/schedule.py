"""When the bot posts: which weekdays, and at what hour.

Two facts from this account's own data drive this module.

**The audience has a schedule.** Instagram Insights puts the followers' peak at
18:00-21:00 Zurich time on Sundays and Mondays and 21:00-00:00 on Tuesdays.

**GitHub does not keep the cron's schedule.** The workflow asked for 17:00 UTC,
but scheduled runs start late: over 47 posts the median start was 3.0h after the
cron time, the 90th percentile 4.8h and the worst 8.3h. Half of all posts went
out after 22:00 local time — outside every one of those windows — and because
every post sat in the same late slot, the data could never say whether the hour
mattered.

So the cron is only a *start* signal now. The run starts early, builds, and then
holds until a wall-clock slot before it publishes. A late start can make the
post late, but an early start no longer makes it early or arbitrary.

This logic lives here rather than in workflow YAML on purpose. The seam between
Python and YAML has broken three times (see daily-post.yml), and the test suite
cannot see across it; time-zone and daylight-saving arithmetic in shell is where
a bug would hide longest.
"""

from __future__ import annotations

import datetime as dt
import logging
import re
import time
from dataclasses import dataclass
from typing import Callable, Mapping
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

log = logging.getLogger(__name__)

DEFAULT_TZ = "Europe/Zurich"
DEFAULT_PUBLISH_AT = "19:00"

# Wednesday, Friday, Saturday. Sunday, Monday and Tuesday are when this
# account's followers are most active, and the posts that have earned reactions
# were the ones made by hand — so those evenings are left for them rather than
# spent on automated cards.
DEFAULT_POST_DAYS = "Wed,Fri,Sat"

# The longest a run will sleep. A GitHub-hosted job is killed at 6h, and the run
# still has setup, a build and a publish around the wait (each capped by its own
# step timeout in daily-post.yml). If the slot is further away than this — a run
# that happened to start almost on time — it waits the cap and then publishes:
# somewhat early, but never at an arbitrary hour.
MAX_WAIT_SECONDS = 5 * 3600

WEEKDAYS = ("Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun")

_DAY_NAMES = {
    "mon": "Mon", "monday": "Mon",
    "tue": "Tue", "tues": "Tue", "tuesday": "Tue",
    "wed": "Wed", "weds": "Wed", "wednesday": "Wed",
    "thu": "Thu", "thur": "Thu", "thurs": "Thu", "thursday": "Thu",
    "fri": "Fri", "friday": "Fri",
    "sat": "Sat", "saturday": "Sat",
    "sun": "Sun", "sunday": "Sun",
}
_EVERY_DAY = {"daily", "all", "every", "everyday", "*"}
_TIME_RE = re.compile(r"^\s*(\d{1,2}):(\d{2})\s*$")


@dataclass(frozen=True)
class Decision:
    post: bool
    reason: str


def parse_post_days(raw: str | None) -> tuple[str, ...]:
    """Turn ``"Wed, Fri, Sat"`` (or ``"daily"``) into canonical weekday names.

    An unknown word raises rather than being skipped. A typo in a GitHub
    variable that silently became "never post" or "post every day" would be
    discovered only by its effect on the account; failing the run is visible.
    """
    text = (raw or "").strip() or DEFAULT_POST_DAYS
    tokens = [t for t in re.split(r"[,\s]+", text) if t]
    if len(tokens) == 1 and tokens[0].lower() in _EVERY_DAY:
        return WEEKDAYS

    days: list[str] = []
    for token in tokens:
        day = _DAY_NAMES.get(token.lower())
        if day is None:
            raise ValueError(
                f"unknown weekday {token!r} in POST_DAYS={raw!r}; "
                "use names like 'Wed,Fri,Sat', or 'daily' for every day"
            )
        if day not in days:
            days.append(day)
    return tuple(days)


def parse_publish_at(raw: str | None) -> dt.time:
    """Parse ``"19:00"``. Blank means the default, as an unset variable does."""
    text = (raw or "").strip() or DEFAULT_PUBLISH_AT
    match = _TIME_RE.match(text)
    if not match or int(match.group(1)) > 23 or int(match.group(2)) > 59:
        raise ValueError(f"PUBLISH_AT={raw!r} is not a 24-hour HH:MM time")
    return dt.time(int(match.group(1)), int(match.group(2)))


def load_zone(name: str | None) -> ZoneInfo:
    try:
        return ZoneInfo((name or "").strip() or DEFAULT_TZ)
    except (ZoneInfoNotFoundError, ValueError) as exc:
        raise ValueError(
            f"POST_TZ={name!r} is not a known time zone (try 'Europe/Zurich')"
        ) from exc


def _aware(moment: dt.datetime) -> dt.datetime:
    """Treat a naive datetime as UTC, matching how the rest of the pipeline reads time."""
    return moment if moment.tzinfo else moment.replace(tzinfo=dt.timezone.utc)


def weekday_name(now: dt.datetime, zone: ZoneInfo) -> str:
    """Local weekday as 'Mon'..'Sun'.

    Derived from ``weekday()`` rather than ``strftime('%a')``, which follows the
    machine's locale and would not match the names in POST_DAYS on a runner set
    to another language.
    """
    return WEEKDAYS[_aware(now).astimezone(zone).weekday()]


def is_post_day(now: dt.datetime, days: tuple[str, ...], zone: ZoneInfo) -> bool:
    return weekday_name(now, zone) in days


def seconds_until(now: dt.datetime, publish_at: dt.time, zone: ZoneInfo) -> float:
    """Seconds from ``now`` to today's slot in ``zone``; 0 if it has passed.

    Both ends are converted to UTC before subtracting. Subtracting two aware
    datetimes that share a ``ZoneInfo`` compares wall-clock readings and ignores
    the offset change, which would be an hour wrong on the two days a year the
    clocks move.
    """
    moment = _aware(now)
    local = moment.astimezone(zone)
    target = local.replace(
        hour=publish_at.hour, minute=publish_at.minute, second=0, microsecond=0
    )
    delta = (target.astimezone(dt.timezone.utc) - moment.astimezone(dt.timezone.utc))
    return max(0.0, delta.total_seconds())


def decide(env: Mapping[str, str], now: dt.datetime | None = None) -> Decision:
    """Should this run post today?

    Only *scheduled* runs are gated. A manual run is somebody asking for a post
    (or a dry run) right now, and refusing because it happens to be a Monday
    would read as the workflow being broken.
    """
    event = env.get("GITHUB_EVENT_NAME", "")
    if event != "schedule":
        return Decision(True, f"{event or 'manual'} run: posting regardless of weekday")

    zone = load_zone(env.get("POST_TZ"))
    days = parse_post_days(env.get("POST_DAYS"))
    # Validated here although only the wait step uses it. Left to that step, a
    # typo in PUBLISH_AT would fail the run after the build — hours before the
    # post — instead of immediately, while there is still time to fix it.
    parse_publish_at(env.get("PUBLISH_AT"))
    today = weekday_name(now or dt.datetime.now(dt.timezone.utc), zone)
    if today in days:
        return Decision(True, f"{today} is a posting day ({','.join(days)})")
    return Decision(
        False,
        f"{today} is not a posting day (POST_DAYS={','.join(days)}); skipping this run",
    )


def is_scheduled_run(env: Mapping[str, str]) -> bool:
    return env.get("GITHUB_EVENT_NAME", "") == "schedule"


def _fmt(seconds: float) -> str:
    minutes = int(round(seconds / 60))
    hours, minutes = divmod(minutes, 60)
    return f"{hours}h{minutes:02d}m" if hours else f"{minutes}m"


def wait_for_publish_time(
    publish_at: dt.time,
    zone: ZoneInfo,
    *,
    max_wait: float = MAX_WAIT_SECONDS,
    clock: Callable[[], dt.datetime] = lambda: dt.datetime.now(dt.timezone.utc),
    sleep: Callable[[float], None] = time.sleep,
    chunk: float = 300.0,
) -> float:
    """Block until today's publish slot. Returns the seconds actually waited.

    Sleeps in short chunks against a deadline rather than once for the whole
    span: the log shows the run is alive, and each wake-up re-reads the clock so
    a long sleep cannot drift.

    A slot that has already passed returns immediately — a run that started late
    publishes late rather than waiting a further day.
    """
    start = clock()
    remaining = seconds_until(start, publish_at, zone)
    if remaining <= 0:
        log.info(
            "the %s %s slot has already passed; publishing now",
            publish_at.strftime("%H:%M"), zone.key,
        )
        return 0.0

    budget = min(remaining, max_wait)
    if remaining > max_wait:
        log.warning(
            "the %s %s slot is %s away, more than the %s cap; waiting the cap and "
            "publishing about %s early",
            publish_at.strftime("%H:%M"), zone.key,
            _fmt(remaining), _fmt(max_wait), _fmt(remaining - max_wait),
        )

    deadline = start + dt.timedelta(seconds=budget)
    while True:
        left = (deadline - clock()).total_seconds()
        if left <= 0:
            break
        log.info(
            "holding for the %s %s slot: %s to go",
            publish_at.strftime("%H:%M"), zone.key, _fmt(left),
        )
        sleep(min(chunk, left))
    return (clock() - start).total_seconds()
