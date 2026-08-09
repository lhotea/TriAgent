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
        choices=["full", "build", "publish", "whoami", "feedcheck"],
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

    if args.mode == "feedcheck":
        from .news import check_feeds

        rows = check_feeds(settings.feeds)
        for row in rows:
            mark = "OK  " if row["ok"] else "FAIL"
            detail = (
                f"{row['entries']} entries, newest {row.get('newest_age_hours')}h old"
                if row["ok"]
                else row.get("error", "")
            )
            print(f"{mark} {row['url']}\n     {detail}")
        working = sum(1 for r in rows if r["ok"])
        print(f"\n{working}/{len(rows)} feeds usable")
        return 0 if working else 1

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
