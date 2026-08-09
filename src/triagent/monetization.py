from __future__ import annotations

import datetime as dt
import hashlib

from .summarizer import DailyBrief


def _cta(brand_handle: str, *, has_affiliate: bool) -> str:
    """Build the link-in-bio call to action.

    Two things it must not do: promise "gear picks" when no affiliate links are
    configured, and render an empty pair of brackets when BRAND_HANDLE is unset.
    """
    what = "Full stories + today's gear picks" if has_affiliate else "Full stories"
    handle = brand_handle.strip()
    tail = f" ({handle})" if handle else ""
    return f"🔗 {what} → link in bio{tail}"


def _rotate(urls: list[str], seed: str) -> str | None:
    if not urls:
        return None
    idx = int(hashlib.sha256(seed.encode()).hexdigest(), 16) % len(urls)
    return urls[idx]


def assemble_caption(
    brief: DailyBrief,
    *,
    affiliate_urls: list[str],
    brand_handle: str,
) -> str:
    """Compose the final Instagram caption.

    Structure (tested against IG's algorithm preferences):
      1. Hook on its own line — scroll-stopper.
      2. Body with inline headline references.
      3. Engagement prompt — invites comments.
      4. Link-in-bio CTA (Instagram strips URLs from captions, so this points to bio).
      5. Optional affiliate line (rotated daily for A/B signal).
      6. Handle + hashtags on final line.
    """
    # Seed rotation by ISO date so the same day picks the same URL across retries.
    # IG strips URLs from captions, so the rotated link lives in the bio landing
    # page; we vary the CTA copy to A/B which framing drives bio clicks.
    today_seed = dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%d")
    has_affiliate = bool(_rotate(affiliate_urls, today_seed))

    parts: list[str] = [
        brief.hook.strip(),
        "",
        brief.caption_body.strip(),
        "",
        brief.engagement_prompt.strip(),
        "",
        _cta(brand_handle, has_affiliate=has_affiliate),
    ]
    if has_affiliate:
        suffix = f" through {brand_handle}" if brand_handle.strip() else ""
        parts.append(
            "⚡ Today's featured deal is pinned at the top of our bio link. "
            f"Every purchase{suffix} keeps this feed running."
        )
    parts.append("")

    tags = " ".join(f"#{t.lstrip('#').lower()}" for t in brief.hashtags)
    parts.append(tags)

    # IG caption cap is 2200 chars. Trim hashtags first if we overshoot.
    caption = "\n".join(parts)
    if len(caption) > 2200:
        trimmed_tags = tags[: 2200 - len(caption) + len(tags) - 10].rsplit(" ", 1)[0]
        parts[-1] = trimmed_tags
        caption = "\n".join(parts)
    return caption
