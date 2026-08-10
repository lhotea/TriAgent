"""Collect per-post performance into a CSV so content decisions have evidence.

Without this, every change to the hook, the format or the question is a hunch,
and with one post a day it would take months to notice which hunches were
right. The columns are chosen for that job specifically:

* **saves and shares** carry far more distribution weight than likes, and are
  what separates a post people keep from one they scroll past.
* **engagement rate** (interactions ÷ reach) is the comparable number. Raw
  counts only track how many followers you had that week.
* **follows** attributes new followers to the post that earned them.
* **hook** is the first caption line, so a row can be read back as "this
  sentence produced these numbers".

Rows are upserted by media_id rather than appended, because insights mature for
days after publishing — a post polled an hour after going out is not final, and
re-polling must correct the row rather than duplicate it.
"""

from __future__ import annotations

import csv
import datetime as dt
import logging
from pathlib import Path

log = logging.getLogger(__name__)

FIELDS = [
    "media_id",
    "published",
    "collected",
    "media_type",
    "published_hour_utc",
    "weekday",
    "permalink",
    "hook",
    "reach",
    "views",
    "likes",
    "comments",
    "saved",
    "shares",
    "total_interactions",
    "follows",
    "profile_visits",
    "engagement_rate",
]


def _published_parts(timestamp: str | None) -> tuple[str, str]:
    """Hour (UTC) and weekday from an API timestamp, or blanks.

    Recorded because a fixed posting time cannot be evaluated from its own
    data: every post shares the hour, so nothing distinguishes a good slot from
    a bad one. Capturing it now means that if the schedule is ever rotated
    across candidate hours, the history is already there to compare.
    """
    if not timestamp:
        return "", ""
    try:
        when = dt.datetime.fromisoformat(timestamp.replace("Z", "+00:00"))
    except ValueError:
        return "", ""
    when = when.astimezone(dt.timezone.utc)
    return f"{when.hour:02d}", when.strftime("%a")


def _hook_of(caption: str | None) -> str:
    """First non-empty caption line — the sentence being tested."""
    for line in (caption or "").splitlines():
        if line.strip():
            return line.strip()
    return ""


def _engagement_rate(row: dict) -> str:
    """Interactions per person reached, as a percentage to one decimal.

    Reach rather than followers: it measures whether the people who saw the
    post did anything, which is the signal distribution actually responds to.
    """
    reach = row.get("reach") or 0
    if not reach:
        return ""
    interactions = row.get("total_interactions")
    if interactions in (None, ""):
        interactions = sum(
            int(row.get(k) or 0) for k in ("likes", "comments", "saved", "shares")
        )
    return f"{100 * float(interactions) / float(reach):.1f}"


def collect_rows(publisher, limit: int = 25) -> list[dict]:
    """Fetch recent media and their insights as CSV-shaped rows."""
    now = dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    rows: list[dict] = []

    for media in publisher.list_media(limit=limit):
        metrics = publisher.media_insights(media["id"])
        hour, weekday = _published_parts(media.get("timestamp"))
        row = {
            "media_id": media["id"],
            "published": media.get("timestamp", ""),
            "published_hour_utc": hour,
            "weekday": weekday,
            "collected": now,
            "media_type": media.get("media_type", ""),
            "permalink": media.get("permalink", ""),
            "hook": _hook_of(media.get("caption")),
        }
        for key in (
            "reach", "views", "likes", "comments", "saved",
            "shares", "total_interactions", "follows", "profile_visits",
        ):
            row[key] = metrics.get(key, "")
        row["engagement_rate"] = _engagement_rate(row)
        rows.append(row)

    log.info("collected insights for %d post(s)", len(rows))
    return rows


def merge_rows(csv_path: Path, rows: list[dict]) -> list[dict]:
    """Upsert rows into the CSV by media_id, newest post first.

    Upsert rather than append: insights keep moving for days after publishing,
    so re-polling a post has to replace its row, not add a second one.
    """
    existing: dict[str, dict] = {}
    if csv_path.exists():
        with csv_path.open(newline="", encoding="utf-8") as fh:
            for row in csv.DictReader(fh):
                if row.get("media_id"):
                    existing[row["media_id"]] = row

    updated = 0
    for row in rows:
        if row["media_id"] in existing:
            updated += 1
        existing[row["media_id"]] = row

    merged = sorted(
        existing.values(), key=lambda r: r.get("published", ""), reverse=True
    )
    log.info(
        "%d new, %d refreshed, %d total", len(rows) - updated, updated, len(merged)
    )
    return merged


def write_csv(csv_path: Path, rows: list[dict]) -> Path:
    csv_path.parent.mkdir(parents=True, exist_ok=True)
    with csv_path.open("w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=FIELDS, extrasaction="ignore")
        writer.writeheader()
        for row in rows:
            writer.writerow({k: row.get(k, "") for k in FIELDS})
    log.info("wrote %d row(s) to %s", len(rows), csv_path)
    return csv_path


def summarise(rows: list[dict], top: int = 5) -> str:
    """Human-readable ranking by engagement rate, for the run log.

    Sorted by rate rather than raw counts so an early post isn't buried under a
    later one that simply had more followers to reach.
    """
    scored = [r for r in rows if r.get("engagement_rate")]
    if not scored:
        return "No post has enough data yet — insights need ~24-48h to settle."

    scored.sort(key=lambda r: float(r["engagement_rate"]), reverse=True)
    lines = [f"{len(scored)} post(s) with data, best engagement first:", ""]
    for r in scored[:top]:
        lines.append(
            f"  {r['engagement_rate']:>5}%  reach {r.get('reach') or '?':>6}  "
            f"saves {r.get('saved') or 0:>3}  shares {r.get('shares') or 0:>3}  "
            f"{r['hook'][:60]}"
        )
    return "\n".join(lines)
