from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()

REPO_ROOT = Path(__file__).resolve().parents[2]
ASSETS_DIR = REPO_ROOT / "assets"
ASSETS_DIR.mkdir(exist_ok=True)

# Sources for daily AI tool drops.
# Product Hunt's per-category RSS gives us the day's launches in the AI category
# in exactly the shape we want (name, tagline, link). Hacker News Show-HN with
# an `q=ai` query catches earlier (often pre-PH) launches; the summarizer's
# system prompt does a second-pass relevance filter on top.
DEFAULT_FEEDS = [
    "https://www.producthunt.com/feed?category=artificial-intelligence",
    "https://www.producthunt.com/feed?category=developer-tools",
    "https://hnrss.org/show?q=AI",
    "https://hnrss.org/newest?q=LLM&points=50",
]

# Carousel is fixed at 5 logical slides — schema + renderer + caption are
# coupled to: 1 cover / 2 what-it-does / 3 killer-use-case / 4 verdict / 5 cta.
SLIDE_COUNT = 5

# Coarse pre-filter on noisy feeds (HN). The summarizer does the real judgment;
# this just trims tokens before the model sees them.
AI_KEYWORDS = (
    "ai", "ml", "llm", "gpt", "claude", "gemini", "mistral", "llama",
    "agent", "rag", "embedding", "vector", "prompt", "diffusion", "stable",
    "midjourney", "openai", "anthropic", "perplexity", "fine-tune", "finetune",
    "transformer", "model", "chatbot", "copilot",
)


def _required(name: str) -> str:
    value = os.environ.get(name)
    if not value:
        raise RuntimeError(f"Missing required env var: {name}")
    return value


def _optional(name: str) -> str | None:
    value = os.environ.get(name)
    return value or None


def _parse_affiliate_map(raw: str) -> dict[str, str]:
    """Parse `slug=url,slug=url,...` into a dict.

    Slugs are matched case-insensitively against the chosen tool's name so an
    affiliate link only attaches when we actually have one — never a junk link
    to an unrelated product.
    """
    result: dict[str, str] = {}
    for chunk in raw.split(","):
        chunk = chunk.strip()
        if not chunk or "=" not in chunk:
            continue
        slug, url = chunk.split("=", 1)
        slug = slug.strip().lower()
        url = url.strip()
        if slug and url:
            result[slug] = url
    return result


@dataclass(frozen=True)
class Settings:
    anthropic_api_key: str
    # Publish-side — only required when actually posting. `--mode build` runs without these.
    ig_user_id: str | None
    ig_access_token: str | None
    public_image_base_url: str | None
    brand_handle: str
    brand_name: str
    # Monetization. All optional — every slot the agent can't fill is dropped.
    affiliate_map: dict[str, str] = field(default_factory=dict)
    newsletter_url: str | None = None
    gumroad_url: str | None = None
    feeds: list[str] = field(default_factory=lambda: list(DEFAULT_FEEDS))
    model: str = "claude-opus-4-7"
    # Base path; carousel slides are written as daily_1.png ... daily_N.png next to it.
    image_path: Path = ASSETS_DIR / "daily.png"

    @classmethod
    def from_env(cls) -> "Settings":
        affiliate_raw = os.environ.get("AFFILIATE_LINKS", "").strip()
        base = _optional("PUBLIC_IMAGE_BASE_URL")
        return cls(
            anthropic_api_key=_required("ANTHROPIC_API_KEY"),
            ig_user_id=_optional("IG_USER_ID"),
            ig_access_token=_optional("IG_ACCESS_TOKEN"),
            public_image_base_url=base.rstrip("/") if base else None,
            brand_handle=os.environ.get("BRAND_HANDLE", "@dailyaitools"),
            brand_name=os.environ.get("BRAND_NAME", "Daily AI Tools"),
            affiliate_map=_parse_affiliate_map(affiliate_raw),
            newsletter_url=_optional("NEWSLETTER_URL"),
            gumroad_url=_optional("GUMROAD_URL"),
        )

    def slide_paths(self) -> list[Path]:
        stem = self.image_path.stem
        suffix = self.image_path.suffix or ".png"
        parent = self.image_path.parent
        return [parent / f"{stem}_{i + 1}{suffix}" for i in range(SLIDE_COUNT)]

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
