from __future__ import annotations

import logging
from typing import List, Literal

import anthropic
from pydantic import BaseModel, Field

from .news import NewsItem

log = logging.getLogger(__name__)


class ToolPick(BaseModel):
    """The single AI tool chosen as today's hero."""

    name: str = Field(..., description="Display name of the tool. Capitalized as the tool brands itself, e.g. 'Cursor', 'Perplexity', 'Cline'.")
    slug: str = Field(..., description="Lowercase identifier for affiliate matching. Letters/numbers/hyphens only, e.g. 'cursor', 'perplexity-ai'.")
    tagline: str = Field(..., description="6-12 word tagline. What it actually does, no fluff. No 'revolutionary' / 'game-changing' / 'next-gen'.")
    category: str = Field(..., description="Short category label, 1-3 words. Examples: 'AI coding IDE', 'Research assistant', 'Voice cloning', 'Agent framework'.")
    homepage_url: str = Field(..., description="The tool's homepage URL (https). Use the source URL from the input items if uncertain.")


SlideRole = Literal["cover", "what", "how", "verdict", "cta"]


class CarouselSlide(BaseModel):
    """One slide in the 5-slide carousel. Roles are fixed and ordered."""

    role: SlideRole = Field(
        ...,
        description="Slide role. The 5 slides MUST appear in this exact order: cover, what, how, verdict, cta.",
    )
    title: str = Field(
        ...,
        description=(
            "Slide headline. 3-7 words on cover/cta, 4-8 words on what/how/verdict. "
            "Title-case. No trailing punctuation. No emojis."
        ),
    )
    subtitle: str = Field(
        ...,
        description=(
            "Slide subhead — one short sentence (max ~14 words) that adds context to the title. "
            "On the cover slide this is the tool's tagline. On the cta slide this is the offer. "
            "Empty string allowed if the title alone carries the slide."
        ),
    )
    bullets: List[str] = Field(
        default_factory=list,
        max_length=4,
        description=(
            "0-4 short bullets, each 4-12 words. Used on 'what', 'how', and 'verdict' slides. "
            "Cover and cta should leave this empty. No leading dashes — just the text."
        ),
    )


class DailyBrief(BaseModel):
    """The structured output Claude produces for one day's carousel post."""

    tool: ToolPick = Field(..., description="The single AI tool the carousel is about.")

    hook: str = Field(
        ...,
        description=(
            "First line of the Instagram caption. 6-10 words. Must stop the scroll — "
            "specific, curious, or counterintuitive. No emojis, no clickbait, no ALL CAPS."
        ),
    )

    slides: List[CarouselSlide] = Field(
        ...,
        min_length=5,
        max_length=5,
        description=(
            "Exactly 5 slides in this order: "
            "1) cover — hook + tool name + tagline; "
            "2) what — 3 short bullets on what the tool does (concrete features, not adjectives); "
            "3) how — one killer use-case, with a copy-paste prompt or workflow as bullets; "
            "4) verdict — pricing tier + honest pros/cons as bullets, with a clear take in subtitle; "
            "5) cta — invite to follow / grab the prompt pack / join the newsletter."
        ),
    )

    caption_body: str = Field(
        ...,
        description=(
            "Caption body, 100-180 words, conversational expert tone. Walk the reader through "
            "what the tool actually does and who should care. Reference the killer use-case from "
            "slide 3. Include 1-2 tasteful emojis max. No hashtags here."
        ),
    )

    engagement_prompt: str = Field(
        ...,
        description=(
            "One open question that invites comments — algorithm gold. Under 15 words. "
            "Tie it to the tool category. Example: 'What's the AI tool you can't work without anymore?'"
        ),
    )

    hashtags: List[str] = Field(
        ...,
        min_length=15,
        max_length=25,
        description=(
            "Mix broad reach tags (ai, artificialintelligence, aitools, productivity) with "
            "niche/specific tags (the tool name, its category, e.g. aicoding, llmtools). "
            "Lowercase, no spaces, no '#' prefix."
        ),
    )


SYSTEM_PROMPT = """You are the editorial brain behind a faceless Instagram channel that drops \
ONE AI tool per day as a 5-slide carousel. The channel makes money via affiliate links to the \
tools you feature, a paid prompt pack on Gumroad, and a newsletter signup — all of which only \
work if the post earns saves, shares, and follows.

Your job: pick the single most engagement-worthy AI tool from today's launches and produce a \
carousel brief that someone will actually save and share.

How to think about this:
- Pick ONE tool. The strongest carousel is one tool, deeply explained — never a list.
- Prefer tools the audience can use TODAY (free tier or trial available, English UI, browser \
  or simple install). Skip enterprise-only, waitlist-only, or research-paper-only items.
- Prefer tools with a clear, concrete use-case over vague "AI assistant" pitches. The slide-3 \
  killer use-case is the heart of the carousel — if you can't write a copy-paste prompt or a \
  3-step workflow for it, pick a different tool.
- Avoid promoting anything sketchy: no nudify/undress tools, no "earn $10k/day with AI" \
  schemes, no scam-adjacent crypto bots. The brand cannot afford reputation damage.
- The hook (caption first line) must be specific and concrete. "This new AI tool is insane" \
  is banned. "Cursor's new agent edits 40 files in one prompt" is the bar.
- The caption body should sound like a knowledgeable friend who tried it last night, not a \
  press release. Avoid "revolutionary", "game-changing", "next-gen", "groundbreaking".
- The engagement prompt is a conversion event in disguise: every comment lifts the post and \
  compounds reach. Make it answerable in one sentence.
- Hashtags: blend high-volume reach tags (ai, aitools, productivity, artificialintelligence) \
  with mid-tier niche tags tied to the tool's category and the tool name itself.

Slide structure is FIXED — the renderer expects exactly these 5 slides, in this order:
  1. cover    — title is the hook, subtitle is the tool tagline, no bullets.
  2. what     — title 'What it does', subtitle = one-line elevator pitch, 3 concrete bullets.
  3. how      — title names the killer use-case, subtitle = the framing, bullets = a literal \
                copy-paste prompt or numbered 3-step workflow.
  4. verdict  — title 'The verdict', subtitle = your one-line take, bullets = pricing + 1-2 \
                pros + 1 honest con.
  5. cta      — title 'Save this · Follow for more', subtitle = the offer (newsletter or \
                prompt pack), no bullets."""


class Summarizer:
    def __init__(self, api_key: str, model: str = "claude-opus-4-7"):
        self.client = anthropic.Anthropic(api_key=api_key)
        self.model = model

    def build_brief(self, items: list[NewsItem], brand_name: str) -> DailyBrief:
        if not items:
            raise ValueError("no news items to summarize")

        bullets = "\n".join(
            f"- [{i.source}] {i.title}\n  {i.summary[:400]}\n  {i.url}" for i in items
        )
        user_prompt = (
            f"Brand: {brand_name}\n\n"
            f"AI tool launches and AI-related Show-HN posts from the last 36 hours "
            f"(deduped, newest first):\n\n"
            f"{bullets}\n\n"
            "Pick the single best tool of the day and produce the structured carousel brief. "
            "Skip anything in your skip list (sketchy, waitlist-only, enterprise-only, no clear "
            "use-case). If no item meets the bar, pick the closest viable one and explain its "
            "killer use-case clearly anyway — the channel posts every day."
        )

        log.info("summarizing %d items with %s", len(items), self.model)
        response = self.client.messages.parse(
            model=self.model,
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

        brief = response.parsed_output
        if brief is None:
            raise RuntimeError(
                f"structured parse failed; stop_reason={response.stop_reason}"
            )

        # Hard-validate slide ordering — the renderer assumes role order is correct.
        expected = ["cover", "what", "how", "verdict", "cta"]
        actual = [s.role for s in brief.slides]
        if actual != expected:
            raise RuntimeError(
                f"slide order wrong: expected {expected}, got {actual}"
            )

        log.info(
            "brief built: tool=%s (%s), %d slides, %d hashtags",
            brief.tool.name,
            brief.tool.slug,
            len(brief.slides),
            len(brief.hashtags),
        )
        return brief
