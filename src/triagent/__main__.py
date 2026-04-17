from __future__ import annotations

import argparse
import logging
import sys

from .agent import run
from .config import Settings


def main() -> int:
    parser = argparse.ArgumentParser(prog="triagent")
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Build the post but skip publishing to Instagram.",
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

    result = run(settings, dry_run=args.dry_run)
    if result.media_id:
        print(f"published: https://www.instagram.com/p/{result.media_id}/")
    else:
        print("dry-run complete. caption and image ready in assets/.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
