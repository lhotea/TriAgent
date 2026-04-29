from __future__ import annotations

import argparse
import logging
import sys

from .agent import build, publish_from_build, run
from .config import Settings


def main() -> int:
    parser = argparse.ArgumentParser(prog="aigent")
    parser.add_argument(
        "--mode",
        choices=["full", "build", "publish"],
        default="full",
        help=(
            "full: build + publish in one process. "
            "build: fetch launches, render the 5-slide carousel, write caption to disk. "
            "publish: read prebuilt slides + caption from disk and post the carousel."
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

    if args.mode == "build":
        build(settings)
        print("build complete. carousel slides + caption ready in assets/.")
        return 0

    if args.mode == "publish":
        result = publish_from_build(settings)
        print(f"published: https://www.instagram.com/p/{result.media_id}/")
        return 0

    result = run(settings, dry_run=args.dry_run)
    if result.media_id:
        print(f"published: https://www.instagram.com/p/{result.media_id}/")
    else:
        print("dry-run complete. carousel slides and caption ready in assets/.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
