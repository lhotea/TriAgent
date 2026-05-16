from __future__ import annotations

import re

from .summarizer import DailyBrief


def _slug(text: str) -> str:
    """Normalize a tool name into a slug for affiliate-map lookups."""
    return re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")


def affiliate_link_for(brief: DailyBrief, affiliate_map: dict[str, str]) -> str | None:
    """Return an affiliate URL for today's tool, or None if we don't have one.

    The match is intentionally strict: exact slug or a slug that matches the
    declared `tool.slug`. We do NOT fall through to a 'featured deal' on an
    unrelated tool — that's the kind of misaligned promotion that tanks trust
    and CTR. If we don't have an affiliate for today's pick, the post just
    points at the tool's own homepage instead.
    """
    if not affiliate_map:
        return None
    candidates = {brief.tool.slug.lower(), _slug(brief.tool.name)}
    for slug in candidates:
        if slug in affiliate_map:
            return affiliate_map[slug]
    return None


def assemble_caption(
    brief: DailyBrief,
    *,
    affiliate_map: dict[str, str],
    newsletter_url: str | None,
    gumroad_url: str | None,
    brand_handle: str,
) -> str:
    """Compose the final Instagram caption.

    Structure (algorithm-tuned):
      1. Hook on its own line — scroll-stopper.
      2. Body — the actual breakdown of the tool.
      3. Engagement prompt — invites comments.
      4. Bio CTA stack — newsletter / prompt pack / tool link via link-in-bio
         (Instagram strips URLs from captions, so all clickable links live in bio).
      5. Disclosure if there's an affiliate link in play.
      6. Hashtags on the final line, single chunk for a clean preview.
    """
    has_affiliate = affiliate_link_for(brief, affiliate_map) is not None

    # Build the bio CTA stack from whatever monetization slots are configured.
    cta_lines: list[str] = []
    if newsletter_url:
        cta_lines.append(f"📩 Daily AI tools in your inbox → link in bio ({brand_handle})")
    if gumroad_url:
        cta_lines.append("🧰 The full prompt pack is pinned in our bio")
    cta_lines.append(f"🔗 Try {brief.tool.name} → link in bio")

    parts: list[str] = [
        brief.hook.strip(),
        "",
        brief.caption_body.strip(),
        "",
        brief.engagement_prompt.strip(),
        "",
        *cta_lines,
    ]

    if has_affiliate:
        parts.append("")
        parts.append(
            "Some bio links are affiliate — buying through them costs you nothing extra "
            "and keeps this feed running. Thanks 🙏"
        )

    parts.append("")
    tags = " ".join(f"#{t.lstrip('#').lower()}" for t in brief.hashtags)
    parts.append(tags)

    caption = "\n".join(parts)

    # IG caption cap is 2200 chars. Trim hashtags first if we overshoot.
    if len(caption) > 2200:
        overflow = len(caption) - 2200
        # Drop full hashtags from the end until we fit, never mid-tag.
        tag_list = tags.split()
        while overflow > 0 and tag_list:
            dropped = tag_list.pop()
            overflow -= len(dropped) + 1
        parts[-1] = " ".join(tag_list)
        caption = "\n".join(parts)
    return caption
