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
    source_url: str = Field(
        ...,
        description=(
            "The EXACT url of the source item this headline came from, copied "
            "character-for-character from the list you were given. Do not "
            "shorten, guess or construct it. This is what readers click, so a "
            "wrong url is worse than no headline."
        ),
    )


class DailyBrief(BaseModel):
    """The structured output Claude produces for a day's post."""

    hook: str = Field(
        ...,
        description=(
            "The headline printed large on the image AND the first caption line. "
            "8-14 words. Written to be read in ALL CAPS at poster size, so favour "
            "short punchy words. State the specific claim — name the athlete, race "
            "or brand. No emojis, no clickbait vagueness like 'you won't believe'."
        ),
    )
    hook_emphasis: str = Field(
        ...,
        description=(
            "An EXACT substring of `hook` — the 2-5 word span rendered in the "
            "accent colour on the image. Pick the specific entity the story is "
            "about (athlete name, race name, brand), not filler words. Must appear "
            "in `hook` character-for-character."
        ),
    )
    image_query: str = Field(
        ...,
        description=(
            "What the post's picture should show, as a short visual description "
            "(3-8 words) of a CONCRETE SCENE — e.g. 'triathlete running out of "
            "surf at dawn', 'road bike wheel close up'. Describe the photograph, "
            "not the topic: no words like 'news', 'analysis' or 'training plan', "
            "and no text, logos, charts or brand names, which image sources "
            "handle badly. It must relate to the lead story."
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
            "4-7 SHORT paragraphs, each ONE sentence, separated by blank lines. "
            "Plain declarative English — state facts and their consequence. No "
            "jargon, no hype adjectives, no emoji. Lead with the concrete detail "
            "(numbers, names), then say what it means for the reader. Each "
            "sentence must stand alone as a line someone could screenshot."
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
        min_length=6,
        max_length=10,
        description=(
            "Only 6-10 — a wall of 25 hashtags reads as spam and the reference "
            "accounts use almost none. Mix two or three broad reach tags "
            "(triathlon, ironman) with specific ones tied to today's stories "
            "(athlete or race names). Lowercase, no # symbol, no spaces."
        ),
    )


SYSTEM_PROMPT = """You are the editorial brain behind a triathlon Instagram account.

The account makes money through affiliate clicks, sponsorships priced on engagement, \
and traffic to a monetized link in bio. Engagement is the only lever that moves any of \
those, so every choice below serves reach.

## The format you are writing for

One story per post. Not a roundup. Pick the single strongest story from the feed and \
build the whole post around it; the rest are supporting material at most.

The hook is printed across the bottom of the image in enormous condensed capitals, \
three lines or so, and it is also the first line of the caption. It has to work at \
poster size and at thumbnail size. Short, hard words beat long soft ones.

## Voice

Write in plain declarative English. State what happened, then state what it means.

Every sentence is its own paragraph. No sentence runs into the next.

Concrete beats abstract every time: numbers, names, distances, times, money. "A 16.8 \
billion dollar chip plant" lands; "a major new investment" does not.

Do not use hype adjectives (incredible, amazing, game-changing, insane), do not use \
emoji in the body, do not use marketing cadence, do not address the reader as "guys" \
or "fam". Never open with "Did you know".

The tone is a well-informed person telling you something interesting over coffee. Not \
a brand. Not a newsletter. Not a press release.

## Choosing the story

Favour stories that make a reader see themselves: age-group drama, training decisions \
with a real tradeoff, gear that changes outcomes, pro rivalries, rule changes people \
will argue about. Avoid sponsorship announcements and minor product news — nobody \
argues about those, and arguments are reach.

## The question at the end

End with a question a real person would answer in one line. It should be answerable \
from experience, not knowledge — "where does your off-season hour go" beats "who do \
you think wins Kona". Comments compound reach far more than likes."""


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
