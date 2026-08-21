from __future__ import annotations

import argparse
import json
import logging
import sys

from .agent import build, publish_from_build, run
from .config import Settings


def main() -> int:
    parser = argparse.ArgumentParser(prog="triagent")
    parser.add_argument(
        "--mode",
        choices=[
            "full", "build", "publish", "whoami",
            "feedcheck", "apicheck", "refresh", "insights",
        ],
        default="full",
        help=(
            "full: build + publish in one process. "
            "build: fetch news, render card, write caption to disk, do not post. "
            "publish: read prebuilt card + caption from disk and post to Instagram."
        ),
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="(full mode only) build the post but skip publishing to Instagram.",
    )
    parser.add_argument(
        "--insights-limit",
        type=int,
        default=25,
        help=(
            "(insights mode) how many recent posts to re-poll. Metrics keep "
            "moving for days, so recent posts are refreshed, not just added."
        ),
    )
    parser.add_argument(
        "--log-level",
        default="INFO",
        choices=["DEBUG", "INFO", "WARNING", "ERROR"],
    )
    args = parser.parse_args()

    logging.basicConfig(
        level=getattr(logging, args.log_level),
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )

    try:
        settings = Settings.from_env()
    except RuntimeError as exc:
        print(f"config error: {exc}", file=sys.stderr)
        return 2

    if args.mode == "insights":
        # Needs only the account credentials — no Claude, no image host.
        if not (settings.ig_user_id and settings.ig_access_token):
            print(
                "config error: IG_USER_ID and IG_ACCESS_TOKEN are required",
                file=sys.stderr,
            )
            return 2
        from .insights import collect_rows, merge_rows, summarise, write_csv
        from .publisher import InstagramPublisher

        client = InstagramPublisher(
            ig_user_id=settings.ig_user_id, access_token=settings.ig_access_token
        )
        csv_path = settings.image_path.with_name("insights.csv")
        merged = merge_rows(csv_path, collect_rows(client, limit=args.insights_limit))
        write_csv(csv_path, merged)
        print(summarise(merged))
        return 0

    if args.mode == "refresh":
        # stdout carries ONLY the new token so a workflow can capture it with
        # $(...); everything human-readable goes to stderr. The caller is
        # responsible for masking it before it can reach a log.
        if not settings.ig_access_token:
            print("config error: IG_ACCESS_TOKEN is not set", file=sys.stderr)
            return 2
        from .publisher import InstagramPublisher

        client = InstagramPublisher(
            ig_user_id=settings.ig_user_id or "me",
            access_token=settings.ig_access_token,
        )
        data = client.refresh_long_lived_token()
        days = int(data.get("expires_in", 0)) // 86400
        print(f"token refreshed; valid for a further {days} days", file=sys.stderr)
        print(data["access_token"])
        return 0

    if args.mode == "feedcheck":
        import os

        from .config import dedupe_feeds, parse_feed_list
        from .news import check_feeds

        # Candidates are probed alongside the live list but are not part of it.
        # Nine of fourteen entries in the production FEEDS variable turned out
        # to be stale or dead — one abandoned five years ago — because URLs
        # were added on the strength of looking plausible. This is how a
        # candidate earns its place without the daily job finding out first.
        candidates = parse_feed_list(os.environ.get("EXTRA_FEEDS") or "")
        live = list(settings.feeds)
        rows = check_feeds(dedupe_feeds(live, candidates))
        live_set = set(live)
        for row in rows:
            mark = "OK  " if row["ok"] else "FAIL"
            detail = (
                f"{row['entries']} entries, newest {row.get('newest_age_hours')}h old"
                if row["ok"]
                else row.get("error", "")
            )
            tag = "" if row["url"] in live_set else "  [candidate]"
            print(f"{mark} {row['url']}{tag}\n     {detail}")
            # A URL that served a page rather than a feed still works, but the
            # operator should know which URL actually supplied the entries.
            if row.get("resolved_url"):
                print(f"     via feed link: {row['resolved_url']}")
            # For an empty result, say what actually came back — "no entries"
            # alone cannot separate a rendered page from a redirect or a
            # consent wall, and that is where diagnosis used to stop.
            if row.get("content_type"):
                print(f"     served {row['content_type']} ({row.get('bytes')} bytes)")
            if row.get("final_url"):
                print(f"     redirected to: {row['final_url']}")
            if "alternate_links" in row:
                links = row["alternate_links"]
                print(
                    f"     page advertises: {'; '.join(links)}"
                    if links
                    else "     page advertises no alternate links — needs a direct feed URL"
                )
            if row.get("parse_warning"):
                print(f"     parser warning: {row['parse_warning']}")
        live_rows = [r for r in rows if r["url"] in live_set]
        working = sum(1 for r in live_rows if r["ok"])
        print(f"\n{working}/{len(live_rows)} live feeds usable")
        good_candidates = [
            r for r in rows
            if r["url"] not in live_set
            and r["ok"]
            and (r.get("newest_age_hours") or 0) <= 240
        ]
        if good_candidates:
            print("\ncandidates worth adding to FEEDS:")
            for r in good_candidates:
                print(
                    f"  {r.get('resolved_url') or r['url']} "
                    f"({r['entries']} entries, newest {r['newest_age_hours']}h old)"
                )
        # A feed that yields nothing recent is invisible in a daily run until
        # the posts start repeating, so call it out here.
        stale = [
            r for r in rows
            if r["ok"] and (r.get("newest_age_hours") or 0) > 240
        ]
        if stale:
            print(
                "\nusable but stale (nothing within 10 days) — these will never "
                "reach a post:"
            )
            for r in stale:
                print(f"  {r['url']} (newest {r['newest_age_hours']}h old)")
        return 0 if working else 1

    if args.mode == "apicheck":
        # The World Triathlon mapping was written without ever seeing a real
        # response — the development environment cannot reach triathlon.org.
        # This reports the actual structure so the field mapping is confirmed
        # or corrected from evidence rather than by another round of guessing.
        import os

        from .worldtriathlon import DEFAULT_ENDPOINT, describe

        endpoint = os.environ.get("WORLD_TRIATHLON_ENDPOINT") or DEFAULT_ENDPOINT
        report = describe(endpoint, api_key=os.environ.get("WORLD_TRIATHLON_API_KEY"))
        print(json.dumps(report, indent=2, ensure_ascii=False, default=str))
        if report.get("error"):
            print(f"\nendpoint unreachable: {report['error']}", file=sys.stderr)
            return 1
        if report.get("needs_auth"):
            # Not configured yet is a state, not a failure. Exiting non-zero
            # here painted the whole feedcheck run red and read as "the
            # adapter is broken", when in fact the 401 is the endpoint
            # confirming it exists and wants a key — the most useful result
            # the probe can return short of success.
            print("\n" + report.get("message", "authentication required"))
            if report.get("authenticated"):
                return 1  # a key was sent and refused: that IS a failure
            return 0
        if not report.get("articles_found"):
            print(
                "\nreached the endpoint but found no article list. The keys "
                "above show what came back; add the right one to LIST_KEYS in "
                "worldtriathlon.py.",
                file=sys.stderr,
            )
            return 1
        mapped = report.get("mapped", {})
        missing = [k for k in ("title", "url") if not mapped.get(k)]
        if missing:
            print(
                f"\narticles found, but {', '.join(missing)} could not be mapped. "
                "Add the real field name(s) to TITLE_KEYS / URL_KEYS / SLUG_KEYS.",
                file=sys.stderr,
            )
            return 1
        print(
            f"\nmapping works: {report['articles_found']} article(s), "
            f"first one resolves to {mapped['url']}"
        )
        return 0

    if args.mode == "whoami":
        # Setup helper: verify the token and print the account ID for IG_USER_ID.
        # Only the token is needed, so don't demand the full publish config.
        if not settings.ig_access_token:
            print("config error: IG_ACCESS_TOKEN is not set", file=sys.stderr)
            return 2
        from .publisher import InstagramPublisher

        probe = InstagramPublisher(
            ig_user_id=settings.ig_user_id or "me",
            access_token=settings.ig_access_token,
        )
        print(json.dumps(probe.whoami(), indent=2))
        return 0

    if args.mode == "build":
        build(settings)
        print("build complete. card + caption ready in assets/.")
        return 0

    if args.mode == "publish":
        # "Not configured yet" and "configured but broken" are different states.
        # Skip cleanly for the former so an unconfigured account doesn't produce
        # a failing run every day; still fail loudly for the latter.
        if not (settings.ig_user_id and settings.ig_access_token):
            print(
                "Instagram credentials not set — skipping publish.\n"
                "The card and caption are still built; see the review page.",
                file=sys.stderr,
            )
            return 0
        result = publish_from_build(settings)
        print(f"published: https://www.instagram.com/p/{result.media_id}/")
        return 0

    result = run(settings, dry_run=args.dry_run)
    if result.media_id:
        print(f"published: https://www.instagram.com/p/{result.media_id}/")
    else:
        print("dry-run complete. caption and image ready in assets/.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
