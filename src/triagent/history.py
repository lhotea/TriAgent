"""Remember which stories have been posted, so none is ever used twice.

The fetch window overlaps by design — 36 hours, widening to 240 on a quiet day
— so yesterday's strongest story is still in scope this morning and would be
picked again. Nothing in the pipeline remembered anything, so repeats were
inevitable rather than unlucky.

Two properties matter:

**Filtering happens before the model sees the list.** Removing a repeat
afterwards would mean discarding a brief the model had already built around it,
and the next-best story would never get written up properly.

**Stories are marked used only after a post actually publishes.** Marking at
build time would burn stories whenever publishing failed later in the run —
and publishing has failed for image hosting, credentials and API quirks over
this project's life. Build writes a pending list; a successful publish commits
it.
"""

from __future__ import annotations

import datetime as dt
import json
import logging
from pathlib import Path

log = logging.getLogger(__name__)


def load_used(path: Path) -> dict[str, dict]:
    """Load the ledger of already-posted URLs, or {} if there is none yet."""
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError) as exc:
        # A corrupt ledger must not stop the run. The cost of losing it is a
        # possible repeat; the cost of raising is no post at all.
        log.warning("could not read history %s (%s) — treating as empty", path, exc)
        return {}
    if not isinstance(data, dict):
        log.warning("history %s is not an object — treating as empty", path)
        return {}
    return data


def filter_unused(items: list, used: dict[str, dict]) -> list:
    """Drop items whose URL has already been posted."""
    if not used:
        return items
    fresh = [i for i in items if i.url not in used]
    dropped = len(items) - len(fresh)
    if dropped:
        log.info("skipped %d already-posted item(s)", dropped)
    return fresh


def write_pending(path: Path, items: list) -> Path:
    """Record the URLs this build used, awaiting a successful publish."""
    payload = {i.url: {"title": i.title, "source": i.source} for i in items}
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
    log.info("recorded %d pending story url(s)", len(payload))
    return path


def commit_pending(history_path: Path, pending_path: Path) -> int:
    """Fold the pending list into the ledger. Returns how many were added.

    Called only after a post is live. Existing entries keep their original
    date, so the ledger records when a story was first used rather than when it
    was last seen.
    """
    if not pending_path.exists():
        log.info("no pending stories to commit")
        return 0

    try:
        pending = json.loads(pending_path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError) as exc:
        log.warning("could not read pending file (%s) — nothing committed", exc)
        return 0

    used = load_used(history_path)
    today = dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%d")
    added = 0
    for url, meta in pending.items():
        if url in used:
            continue
        used[url] = {**meta, "first_used": today}
        added += 1

    history_path.parent.mkdir(parents=True, exist_ok=True)
    history_path.write_text(
        json.dumps(used, indent=2, ensure_ascii=False), encoding="utf-8"
    )
    log.info("committed %d story url(s); %d in history", added, len(used))
    return added
