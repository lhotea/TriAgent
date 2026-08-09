from __future__ import annotations

import os
import re
from dataclasses import dataclass, field
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()

REPO_ROOT = Path(__file__).resolve().parents[2]
ASSETS_DIR = REPO_ROOT / "assets"
ASSETS_DIR.mkdir(exist_ok=True)

# Verify changes to this list with `python -m triagent --mode feedcheck` before
# relying on them. Run #117 showed several of the original entries had never
# worked: two domains no longer resolve, one path 404s, one blocks bots.
#
# Status as of the last observed production run:
#   triathlete.com  — reachable, parses
#   tri247.com      — returned 403 for the default requests user-agent; a
#                     browser UA is now sent, which usually clears it
#   slowtwitch.com  — /rss/news.rss returned 404; path below is a guess and
#                     needs feedcheck to confirm
# Removed: trinewsnetwork.com and worldtriathlon.org (DNS did not resolve).
DEFAULT_FEEDS = [
    "https://www.triathlete.com/feed/",
    "https://www.tri247.com/feed",
    "https://www.slowtwitch.com/feed",
]


_SCHEME_RE = re.compile(r"^[a-zA-Z][a-zA-Z0-9+.\-]*://")


def normalize_feed_url(url: str) -> str:
    """Add https:// when a feed is given as a bare domain.

    Feed lists get pasted from directories and browser address bars, which
    routinely drop the scheme. requests rejects those outright ("No scheme
    supplied"), so a whole list can fail for a purely cosmetic reason.
    """
    url = url.strip(" \t\r\n,")
    if not url:
        return ""
    if _SCHEME_RE.match(url):
        return url
    return f"https://{url.lstrip('/')}"


def parse_feed_list(raw: str) -> list[str]:
    """Split a comma- or whitespace-separated feed list and normalize each entry."""
    parts = re.split(r"[,\s]+", raw.strip())
    return [u for u in (normalize_feed_url(p) for p in parts) if u]


def _required(name: str) -> str:
    value = os.environ.get(name)
    if not value:
        raise RuntimeError(f"Missing required env var: {name}")
    return value


def _optional(name: str) -> str | None:
    value = os.environ.get(name)
    return value or None


@dataclass(frozen=True)
class Settings:
    anthropic_api_key: str
    # Publish-side — only needed when actually posting to Instagram. Build mode
    # must work without these so a scheduled run doesn't hard-fail during rendering.
    ig_user_id: str | None
    ig_access_token: str | None
    public_image_base_url: str | None
    brand_handle: str
    brand_name: str
    affiliate_urls: list[str] = field(default_factory=list)
    feeds: list[str] = field(default_factory=lambda: list(DEFAULT_FEEDS))
    model: str = "claude-opus-4-7"
    max_headlines: int = 6
    image_path: Path = ASSETS_DIR / "daily.png"

    @classmethod
    def from_env(cls) -> "Settings":
        affiliate_raw = os.environ.get("AFFILIATE_URLS", "").strip()
        affiliate = [u.strip() for u in affiliate_raw.split(",") if u.strip()]
        base = _optional("PUBLIC_IMAGE_BASE_URL")
        # Feeds rot faster than code ships — allow overriding without a release.
        feeds = parse_feed_list(os.environ.get("FEEDS", "")) or list(DEFAULT_FEEDS)
        return cls(
            feeds=feeds,
            anthropic_api_key=_required("ANTHROPIC_API_KEY"),
            ig_user_id=_optional("IG_USER_ID"),
            ig_access_token=_optional("IG_ACCESS_TOKEN"),
            public_image_base_url=base.rstrip("/") if base else None,
            # `or` rather than a get() default: GitHub Actions substitutes an
            # empty string for an unset `vars.X`, so the env var exists but is
            # blank and get()'s default never fires. That is how the brand name
            # vanished from the card and the caption ended with "link in bio ()".
            brand_handle=os.environ.get("BRAND_HANDLE") or "@tripulsedaily",
            brand_name=os.environ.get("BRAND_NAME") or "TriPulse Daily",
            affiliate_urls=affiliate,
        )

    def require_publish_config(self) -> None:
        missing = [
            name
            for name, val in (
                ("IG_USER_ID", self.ig_user_id),
                ("IG_ACCESS_TOKEN", self.ig_access_token),
                ("PUBLIC_IMAGE_BASE_URL", self.public_image_base_url),
            )
            if not val
        ]
        if missing:
            raise RuntimeError(
                "Cannot publish to Instagram. Missing env vars: "
                + ", ".join(missing)
            )
