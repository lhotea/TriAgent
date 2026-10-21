"""Track the account itself — followers above all — one row a day.

insights.py records how each *post* did. It cannot answer the question the posts
exist to serve: is the audience growing? Over the 90 days to 20 Oct, Instagram's
own dashboard shows +0 net followers (6 follows, 6 unfollows) across 58 automated
posts — and nothing in this repo could have shown that, so no change to the
content or the schedule could be judged by what it did for followers.

Choices that follow from that:

* **Raw snapshots, derived changes.** The file stores what was read each day; a
  change over a week or a month is computed when it is read. The scheduler
  skips and delays runs, and a missed day should cost a gap, not a wrong delta
  baked into the file.
* **A trailing week for the flow metrics, not a single day.** At this account's
  size a day's reach is a few dozen people, which is noise — and a trailing
  window sidesteps whichever day boundary or time zone the API uses.
* **Blank, never zero, when a figure is unavailable.** A zero would read as
  "nobody saw it". The dependable core is the follower count (a plain profile
  field); the Insights metrics are best-effort and say so when they fail.
* **The non-follower share of reach and views is the growth number.** In the
  app's content-type breakdown, feed posts were seen almost only by people who
  already follow; Reels were the format that reached strangers. Growth has to
  come from people who do not follow yet, so that share is tracked directly.
"""

from __future__ import annotations

import csv
import datetime as dt
import logging
from pathlib import Path

log = logging.getLogger(__name__)

WINDOW_DAYS = 7

FIELDS = [
    "date",
    "collected",
    "followers",
    "following",
    "posts",
    "reach_7d",
    "reach_nonfollower_pct_7d",
    "views_7d",
    "views_nonfollower_pct_7d",
    "profile_views_7d",
    "accounts_engaged_7d",
    "interactions_7d",
]

# (column, API metric, whether to ask for the follower / non-follower split)
_METRICS = (
    ("reach_7d", "reach", True),
    ("views_7d", "views", True),
    ("profile_views_7d", "profile_views", False),
    ("accounts_engaged_7d", "accounts_engaged", False),
    ("interactions_7d", "total_interactions", False),
)
_SPLIT_COLUMN = {
    "reach_7d": "reach_nonfollower_pct_7d",
    "views_7d": "views_nonfollower_pct_7d",
}


def _nonfollower_pct(split: dict) -> str:
    """Share of the audience that did not already follow, to one decimal."""
    whole = sum(split.values())
    if not whole or "NON_FOLLOWER" not in split:
        return ""
    return f"{100 * split['NON_FOLLOWER'] / whole:.1f}"


def collect_snapshot(client, now: dt.datetime | None = None) -> dict:
    """Read the account as it stands now into one CSV-shaped row."""
    now = now or dt.datetime.now(dt.timezone.utc)
    until = int(now.timestamp())
    since = int((now - dt.timedelta(days=WINDOW_DAYS)).timestamp())

    profile = client.account_profile() or {}
    row = {key: "" for key in FIELDS}
    row["date"] = now.strftime("%Y-%m-%d")
    row["collected"] = now.strftime("%Y-%m-%dT%H:%M:%SZ")
    row["followers"] = profile.get("followers_count", "")
    row["following"] = profile.get("follows_count", "")
    row["posts"] = profile.get("media_count", "")

    for column, metric, wants_split in _METRICS:
        result = client.account_metric(metric, since=since, until=until, split=wants_split)
        if not result:
            continue
        row[column] = result["total"]
        if wants_split:
            row[_SPLIT_COLUMN[column]] = _nonfollower_pct(result["split"])
    return row


def merge_snapshot(csv_path: Path, row: dict) -> list[dict]:
    """Upsert today's row by date, newest first.

    Field by field: a figure that came back blank this time must not erase one
    that was read earlier the same day (a manual re-run during an API hiccup),
    but a real new value always wins.
    """
    existing: dict[str, dict] = {}
    if csv_path.exists():
        with csv_path.open(newline="", encoding="utf-8") as fh:
            for old in csv.DictReader(fh):
                if old.get("date"):
                    existing[old["date"]] = old

    kept = {k: v for k, v in row.items() if v != ""}
    existing[row["date"]] = {**existing.get(row["date"], {}), **kept}

    merged = sorted(existing.values(), key=lambda r: r["date"], reverse=True)
    log.info("%d day(s) of account history", len(merged))
    return merged


def write_csv(csv_path: Path, rows: list[dict]) -> Path:
    csv_path.parent.mkdir(parents=True, exist_ok=True)
    with csv_path.open("w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=FIELDS, extrasaction="ignore")
        writer.writeheader()
        for row in rows:
            writer.writerow({k: row.get(k, "") for k in FIELDS})
    log.info("wrote %d account row(s) to %s", len(rows), csv_path)
    return csv_path


def record_snapshot(client, csv_path: Path, now: dt.datetime | None = None) -> str:
    """Collect today's snapshot, fold it into the CSV and return the readout.

    The one call the CLI makes, so the collect-merge-write sequence is tested as
    a unit rather than only through its parts.
    """
    rows = merge_snapshot(csv_path, collect_snapshot(client, now))
    write_csv(csv_path, rows)
    return summarise_account(rows)


def _num(value) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _followers_change(rows: list[dict], days: int) -> tuple[int, int] | None:
    """(net change, days it actually spans) over roughly the last ``days``.

    Uses the newest row at least ``days`` old. When the history is shorter than
    that, it uses the oldest row there is and reports the shorter span instead
    of pretending to a full window.
    """
    points = sorted(
        (
            (dt.date.fromisoformat(r["date"]), int(n))
            for r in rows
            if (n := _num(r.get("followers"))) is not None
        ),
        reverse=True,
    )
    if len(points) < 2:
        return None
    latest_date, latest = points[0]
    cutoff = latest_date - dt.timedelta(days=days)
    baseline = next((p for p in points[1:] if p[0] <= cutoff), points[-1])
    return latest - baseline[1], (latest_date - baseline[0]).days


def _signed(n: int) -> str:
    return f"{n:+d}"


def summarise_account(rows: list[dict]) -> str:
    """A short readout for the run log: where the audience stands and which way it moved."""
    if not rows:
        return "No account data yet."

    today = rows[0]
    lines = [f"Account on {today['date']}:"]

    followers = _num(today.get("followers"))
    if followers is None:
        lines.append("  followers: not readable today")
    else:
        moves = []
        for days in (7, 30):
            change = _followers_change(rows, days)
            if change is None:
                continue
            delta, span = change
            moves.append(
                f"{_signed(delta)} over {days} days"
                if span >= days
                else f"{_signed(delta)} over the {span} day(s) recorded so far"
            )
        trend = f" ({'; '.join(moves)})" if moves else " (first snapshot — no trend yet)"
        lines.append(f"  followers {int(followers)}{trend}")

    following, posts = _num(today.get("following")), _num(today.get("posts"))
    if following is not None and posts is not None:
        lines.append(f"  following {int(following)} · posts {int(posts)}")

    def figure(column: str, label: str, share: str | None = None) -> str | None:
        value = _num(today.get(column))
        if value is None:
            return None
        text = f"{label} {int(value)}"
        pct = _num(today.get(share)) if share else None
        return f"{text} ({pct:.1f}% from non-followers)" if pct is not None else text

    flow = [
        figure("reach_7d", "reach", "reach_nonfollower_pct_7d"),
        figure("views_7d", "views", "views_nonfollower_pct_7d"),
        figure("profile_views_7d", "profile views"),
        figure("accounts_engaged_7d", "accounts engaged"),
        figure("interactions_7d", "interactions"),
    ]
    shown = [f for f in flow if f]
    if shown:
        lines.append(f"  last {WINDOW_DAYS} days: " + " · ".join(shown))

    missing = [
        metric
        for column, metric, _ in _METRICS
        if _num(today.get(column)) is None
    ]
    if missing:
        lines.append(
            f"  not available today: {', '.join(missing)} — the warnings above "
            "give Meta's reason; the follower count is unaffected"
        )
    return "\n".join(lines)
