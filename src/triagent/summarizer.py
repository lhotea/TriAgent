from __future__ import annotations

import logging
from typing import List

import anthropic
from pydantic import BaseModel, Field

from .news import NewsItem

log = logging.getLogger(__name__)


class Headline(BaseModel):
    """One ranked, rewritten headline for the image card and caption."""

    title: str = Field(
        ...,
        description="Punchy 8-12 word headline rewritten for Instagram. No clickbait, no ALL CAPS.",
    )
    one_liner: str = Field(
        ...,
        description="Single sentence (~20 words) explaining why this matters to triathletes.",
    )
    source: str = Field(..., description="Source publication name.")


class DailyBrief(BaseModel):
    """The structured output Claude produces for a day's post."""

    hook: str = Field(
        ...,
        description=(
            "First line of the Instagram caption. 6-10 words. Must stop the scroll — "
            "provocative, curious, or emotionally charged. No emojis in this line."
        ),
    )
    headlines: List[Headline] = Field(
        ...,
        min_length=3,
        max_length=5,
        description="Ranked by audience value. Most engaging first.",
    )
    caption_body: str = Field(
        ...,
        description=(
            "Main caption body, 80-150 words, casual coaching tone. Reference the "
            "headlines inline. Include 1-2 tasteful emojis max. No hashtags here."
        ),
    )
    engagement_prompt: str = Field(
        ...,
        description=(
            "One question that invites comments (algorithm gold). Keep under 15 words. "
            "Example: 'Who do you think wins Kona this year?'"
        ),
    )
    hashtags: List[str] = Field(
        ...,
        min_length=15,
        max_length=25,
        description=(
            "Mix of broad (#triathlon, #ironman) and niche (#70point3, #ageGroupAthlete) "
            "tags without the # symbol. Lowercase. No spaces."
        ),
    )


SYSTEM_PROMPT = """You are the editorial brain behind a triathlon Instagram account that makes \
money through affiliate links, sponsorships, and driving traffic to a monetized link-in-bio.

Your job: turn today's triathlon news into one Instagram post that maximizes engagement, \
because engagement is the only lever that moves revenue on this platform.

How to think about this:
- The hook is the single most important line. If it doesn't stop the scroll, nothing \
  downstream matters.
- Rewrite headlines for an Instagram audience, not for SEO. Shorter, more emotional, \
  more specific. Names of athletes beat generic descriptors.
- The caption should feel like a knowledgeable friend texting, not a press release.
- Pick headlines that trigger identification (age-group drama, gear debates, pro rivalries, \
  controversial rule changes) over neutral reporting (sponsorship announcements, minor \
  product releases). Ranked order matters — put the juiciest first.
- The engagement prompt is a conversion event in disguise: every comment lifts the \
  post in the feed and compounds reach.
- Hashtags should blend high-volume reach tags (#triathlon, #ironman, #swimbikerun) \
  with mid-tier niche tags (#70point3, #ageGroupAthlete, #bikefit) and a few \
  topical tags tied to today's specific stories (athlete names, race names)."""


# Use the alias, not a dated snapshot. "claude-sonnet-4-5-20251001" was invalid:
# 20251001 is the Haiku 4.5 snapshot date, so that ID would 404 — and only on
# the fallback path, i.e. exactly when the primary model is already failing.
FALLBACK_MODEL = "claude-sonnet-4-5"


class Summarizer:
    def __init__(self, api_key: str, model: str = "claude-opus-4-7", fallback_model: str = FALLBACK_MODEL):
        self.client = anthropic.Anthropic(api_key=api_key)
        self.model = model
        self.fallback_model = fallback_model

    def build_brief(self, items: list[NewsItem], brand_name: str) -> DailyBrief:
        if not items:
            raise ValueError("no news items to summarize")

        bullets = "\n".join(
            f"- [{i.source}] {i.title}\n  {i.summary[:400]}\n  {i.url}" for i in items
        )
        user_prompt = (
            f"Brand: {brand_name}\n\n"
            f"Raw triathlon headlines from the last 36 hours (already deduped, newest first):\n\n"
            f"{bullets}\n\n"
            "Produce the structured brief. Pick the 3-5 stories with the highest "
            "engagement potential for an Instagram audience of triathletes and aspirants."
        )

        # Common kwargs shared between primary and fallback calls.
        parse_kwargs = dict(
            max_tokens=4096,
            thinking={"type": "adaptive"},
            output_config={"effort": "high"},
            system=[
                {
                    "type": "text",
                    "text": SYSTEM_PROMPT,
                    "cache_control": {"type": "ephemeral"},
                }
            ],
            messages=[{"role": "user", "content": user_prompt}],
            output_format=DailyBrief,
        )

        log.info("summarizing %d items with %s", len(items), self.model)
        try:
            response = self.client.messages.parse(
                model=self.model,
                **parse_kwargs
            )
        except (
            anthropic.APIConnectionError,
            anthropic.APITimeoutError,
            anthropic.RateLimitError,
            anthropic.APIStatusError,
        ) as exc:
            log.warning("primary model %s failed (%s), trying fallback %s", self.model, exc, self.fallback_model)
            try:
                response = self.client.messages.parse(
                    model=self.fallback_model,
                    **parse_kwargs
                )
                log.info("fallback model %s succeeded", self.fallback_model)
            except (
                anthropic.APIConnectionError,
                anthropic.APITimeoutError,
                anthropic.RateLimitError,
                anthropic.APIStatusError,
            ) as fallback_exc:
                raise RuntimeError(
                    f"both primary ({self.model}) and fallback ({self.fallback_model}) models failed"
                ) from fallback_exc

        brief = response.parsed_output
        if brief is None:
            raise RuntimeError(
                f"structured parse failed; stop_reason={response.stop_reason}"
            )
        log.info(
            "brief built: %d headlines, %d hashtags",
            len(brief.headlines),
            len(brief.hashtags),
        )
        return brief
