from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()

REPO_ROOT = Path(__file__).resolve().parents[2]
ASSETS_DIR = REPO_ROOT / "assets"
ASSETS_DIR.mkdir(exist_ok=True)

DEFAULT_FEEDS = [
    "https://www.triathlete.com/feed/",
    "https://www.slowtwitch.com/rss/news.rss",
    "https://www.tri247.com/feed",
    "https://www.trinewsnetwork.com/feed/",
    "https://www.triradar.com/feed/",
    "https://www.worldtriathlon.org/rss/news",
]


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
        return cls(
            anthropic_api_key=_required("ANTHROPIC_API_KEY"),
            ig_user_id=_optional("IG_USER_ID"),
            ig_access_token=_optional("IG_ACCESS_TOKEN"),
            public_image_base_url=base.rstrip("/") if base else None,
            brand_handle=os.environ.get("BRAND_HANDLE", "@tripulsedaily"),
            brand_name=os.environ.get("BRAND_NAME", "TriPulse Daily"),
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
