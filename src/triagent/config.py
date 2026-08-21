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

# Only URLs a feedcheck run has actually confirmed. Every entry here was
# probed on 2026-08-21 and returned dated entries; the age shown is how stale
# the newest story was at that moment.
#
#   220triathlon.com/feed/atom       5.2h   the one high-volume news source
#   atriathletesdiary.com/blog/feed  8.8h
#   dcrainmaker.com/feed           215.5h   gear/reviews, publishes in bursts
#   marathonsandmotivation.com/feed 189.5h
#   t3-triathlon.com/feed          157.6h
#
# Deliberately absent, and worth re-reading before adding anything:
# the previous list carried nine entries that looked plausible and were dead —
# nutri-tri had not published in five years, ironmanhacks in two, joefriel in
# one. They cost a request each and contributed nothing, while making the feed
# list look four times healthier than it was. That is why the rule is now
# "probe it first": run feedcheck with EXTRA_FEEDS (or the workflow's
# extra_feeds input) and add only what comes back live.
#
# Also absent: triathlon.org/news, which serves HTML advertising no feed, and
# tri247.com/feed, which returns 403 without a www host.
DEFAULT_FEEDS = [
    "https://220triathlon.com/feed/atom",
    "https://atriathletesdiary.com/blog/feed",
    "https://dcrainmaker.com/feed",
    "https://marathonsandmotivation.com/feed",
    "https://t3-triathlon.com/feed",
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


def dedupe_feeds(*lists: list[str]) -> list[str]:
    """Merge feed lists, keeping first-seen order and dropping repeats.

    Candidate feeds are pasted by hand and routinely overlap the production
    list; probing the same URL twice wastes a request and reports it twice.
    """
    seen: set[str] = set()
    merged: list[str] = []
    for feeds in lists:
        for url in feeds:
            if url and url not in seen:
                seen.add(url)
                merged.append(url)
    return merged


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
    # Optional for the same reason the publish-side keys are: modes that don't
    # call Claude (refresh, whoami, feedcheck) must not be blocked by a key they
    # never use. Validated at point of use via require_build_config().
    anthropic_api_key: str | None
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
    # "carousel" (default), "image" or "reel". Reels are the only format
    # Instagram lets carry audio, so music requires post_format="reel".
    post_format: str = "carousel"
    carousel_slides: int = 3
    reel_audio: str | None = None
    reel_seconds: int = 8
    # Optional picture sources, tried in this order. Both absent means the card
    # uses whatever is in assets/backgrounds/, then its headline-led layout.
    image_gen_api_key: str | None = None
    stock_api_key: str | None = None
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
            anthropic_api_key=_optional("ANTHROPIC_API_KEY"),
            ig_user_id=_optional("IG_USER_ID"),
            ig_access_token=_optional("IG_ACCESS_TOKEN"),
            public_image_base_url=base.rstrip("/") if base else None,
            # `or` rather than a get() default: GitHub Actions substitutes an
            # empty string for an unset `vars.X`, so the env var exists but is
            # blank and get()'s default never fires. That is how the brand name
            # vanished from the card and the caption ended with "link in bio ()".
            post_format=(os.environ.get("POST_FORMAT") or "carousel").lower(),
            carousel_slides=max(2, min(10, int(os.environ.get("CAROUSEL_SLIDES") or 3))),
            reel_audio=_optional("REEL_AUDIO"),
            reel_seconds=int(os.environ.get("REEL_SECONDS") or 8),
            image_gen_api_key=_optional("IMAGE_GEN_API_KEY"),
            stock_api_key=_optional("PEXELS_API_KEY"),
            brand_handle=os.environ.get("BRAND_HANDLE") or "@tripulsedaily",
            brand_name=os.environ.get("BRAND_NAME") or "TriPulse Daily",
            affiliate_urls=affiliate,
        )

    def require_build_config(self) -> None:
        """Assert what building a post needs. Only build paths call this."""
        if not self.anthropic_api_key:
            raise RuntimeError("Missing required env var: ANTHROPIC_API_KEY")

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
