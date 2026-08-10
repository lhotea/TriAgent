# TriAgent — Design Specification

A daily agent that turns triathlon news into an Instagram post. This document
describes how it works and why it's built this way. For the manual steps needed
to run it, see [SETUP.md](SETUP.md).

---

## 1. Goal and constraints

Publish one Instagram post per day, automatically, optimised for engagement.

Engagement is the target because it's the only lever that moves revenue on this
platform. Instagram does not pay per post. Money comes from affiliate clicks via
the link in bio, from sponsorships priced on engagement, and from Reels bonuses.
Every design decision below serves reach, or serves not breaking.

**Hard constraints discovered in production:**

| Constraint | Consequence |
|---|---|
| Instagram image posts cannot carry audio | Music requires publishing a Reel |
| The Graph API cannot use Instagram's music library | Audio must be baked into the file, and you must own the rights |
| The Graph API fetches media by URL; base64 is rejected | Rendered assets must be publicly hosted before publishing |
| RSS feed URLs rot constantly | Feeds must be overridable without a release, and a dead feed must not kill the run |
| Long-lived tokens expire after 60 days | Refresh must be automated or it will silently break |
| Claude cannot generate images | It chooses the *scene*; a separate provider renders or finds it |
| The card style depends on a condensed typeface | CI installs one; elsewhere it degrades to regular bold |

---

## 2. Pipeline

```
RSS feeds ──► fetch_recent_widening ──► Claude Opus ──► DailyBrief
                                                            │
                    ┌───────────────────────────────────────┤
                    ▼                                       │
          resolve_background (image_query)                  │
           gen ▸ stock ▸ local ▸ none                       │
                    ▼                                       ▼
             render_card (4:5)                      assemble_caption
             render_reel_frame (9:16)                       │
                    │                                       │
                    ▼                                       │
             build_reel (ffmpeg)                            │
                    │                                       │
                    └──────────► gh-pages ◄─────────────────┘
                                    │        (+ review page)
                                    ▼
                            GitHub Pages (public URL)
                                    │
                                    ▼
                     Instagram Graph API (2-step publish)
```

### Stage 1 — News ingestion (`news.py`)

Fetches RSS feeds and returns recent items, newest first, deduplicated by URL.

- **Browser user-agent.** Several publishers return 403 to `python-requests`.
- **Widening window.** Tries 36h, then 96h, then 240h, taking the first that
  yields anything. A quiet news day or a couple of dead feeds degrades to a
  slightly older story rather than a failed run. Normal days stop at the first
  window, so same-day news is unaffected.
- **Governing-body priority.** World Triathlon items are floated to the front
  of the list and marked in the prompt, so the governing body leads whenever it
  has news. Ordering carries the preference — a prompt instruction alone loses
  to a livelier aggregator story sitting higher in the list.
- **Per-feed isolation.** A feed that 404s, times out, or fails DNS is logged
  and skipped. The run continues on whatever is left.
- **Entity decoding.** Feeds deliver typographic punctuation as numeric
  entities ("I&#8217;m"). Decoding happens before tag stripping, so
  double-encoded markup is revealed and removed rather than surviving as
  literal text. Leaving entities encoded leaked into everything downstream —
  the public page, the rendered card, and the model's own prompt.
- **Control-character stripping.** Feed content is untrusted input that is about
  to be placed in a model prompt.

`check_feeds()` backs `--mode feedcheck`, reporting reachability, entry count and
newest-item age per feed, so feed health is diagnosable on demand rather than
discovered through a failed production run.

### Stage 2 — Editorial (`summarizer.py`)

One call to Claude Opus 4.7 with adaptive thinking, structured output enforced
by a Pydantic model (`DailyBrief`).

The system prompt is cached (`cache_control: ephemeral`) and the volatile
headline list goes in the user turn, so repeated same-day runs hit the cache.

The prompt ranks stories by engagement potential rather than newsworthiness —
identification and debate over neutral reporting — and produces:

| Field | Purpose |
|---|---|
| `hook` | The headline set large on the card *and* the first caption line |
| `hook_emphasis` | Substring of `hook` rendered in accent colour — the story's subject |
| `image_query` | The scene the picture should show, as a concrete 3–8 word description |
| `headlines[]` | 3–5 rewritten for Instagram, ranked, juiciest first, each with its `source_url` |
| `caption_body` | 4–7 paragraphs, one sentence each |
| `engagement_prompt` | A question answerable from experience, not knowledge |
| `hashtags[]` | 6–10 only. A wall of 25 reads as spam. |

The prompt bans hype adjectives, marketing cadence and emoji in the body, and
demands concrete detail — numbers, names, times, money — because that is what
distinguishes the reference accounts from generic brand output.

Falls back to Sonnet 4.5 if Opus is unavailable.

### Stage 2b — Picture (`imagery.py`)

Claude names the scene; it cannot draw it. `resolve_background` takes
`image_query` and tries, in order: an OpenAI-compatible generation endpoint
(`IMAGE_GEN_API_KEY`), licensed stock search (`PEXELS_API_KEY`), the local
`assets/backgrounds/` directory, then nothing.

A house style suffix (cinematic, low-key, high contrast, no text) is appended to
generation prompts so output suits white type over a dark card, and portrait
sizes are requested to match the photo band.

Nothing in this module raises. A missing key, an outage or a query with no
matches costs the post its picture, not the post.

Stock is worth preferring even where generation is available: the reference
accounts use real photography, and generated images still betray themselves on
human anatomy — which is most of triathlon.

### Stage 3 — Rendering (`image.py`, `video.py`)

The card follows the reference account's system: pure black; the photo is a
full-bleed band across the top ~58%, faded rather than hard-edged into the black
below; a hairline rule with a centred brand mark divides image from text; the
headline then fills the lower half in condensed capitals at the largest size
that fits, with roughly single leading so lines nearly touch. The story's
subject is set in teal against white.

Because the accent span can begin mid-line, the headline is drawn word by word
with per-word colour rather than line by line. Font size is searched *downward*
from 118pt — this look depends on the type being as large as it can be, so the
right size is always the largest that still fits the line budget.

With no photo the layout collapses (a 58% black band above the headline just
reads as empty), so the card switches to a headline-led composition using the
full canvas.

Posts are **carousels of 3 slides** by default. Slide 1 is the lead — photo
band plus the two-tone hook. Slides 2 and 3 carry one supporting story each as
type on black: large index, headline, one-liner, source. Keeping the photo to
the first slide is deliberate; repeating one image behind every slide reads as
padding, and supporting stories scan faster as type.

The brand mark is the real logo from `assets/images/logo.png`. That file is
RGBA but fully opaque — the mark sits on a dark plate — so pasting it straight
onto the black card would show a rectangle. Luminance supplies the alpha
instead (the histogram separates cleanly: ~95% of pixels are plate at ≤41, the
mark sits above 100), and the crop takes only the dominant shape so the
artwork's decorative corner sparkle doesn't push the mark off-centre. The card's
accent colour is sampled from the logo's own mint.

- `render_slides` → N × 1080×1350 PNG (the carousel)
- `render_card` → 1080×1350 PNG (slide 1, also used for single-image posts)
- `render_reel_frame` → 1080×1920 PNG, composed at Reel dimensions rather than
  letterboxed, and keeping the top/bottom 15% clear of Instagram's UI overlay
- `build_slideshow` → ffmpeg sequences the slides at 1080×1920 with a gentle
  per-slide zoom, H.264+AAC, `+faststart`

Music lives in `assets/music/`, one track chosen per day from the date so
retries don't swap it. Empty directory means a silent Reel. This is the only
route to a post with both several stories and sound: Instagram permits audio on
video alone, so the carousel's slides are sequenced into a Reel rather than
posted as separate images.

Local photos in `assets/backgrounds/` act as the last fallback before "no
picture", selected deterministically by date. Whatever the source, images used
behind body text are darkened, blurred and scrimmed — they are a backdrop, not
the subject. Local photos are user-supplied deliberately: putting a publisher's
press image into a monetized post is a rights problem, and that isn't a decision
this code should make on someone's behalf.

### Stage 4 — Caption assembly (`monetization.py`)

Hook → body → engagement prompt → CTA → optional affiliate line → follow
prompt → hashtags, with blank lines throughout. The body arrives already
split into one-sentence paragraphs, matching the reference's airy rhythm.

The CTA never promises what isn't configured: no "gear picks" without affiliate
URLs, no empty `()` without a brand handle. Captions are capped at Instagram's
2200 characters, trimming hashtags first.

### Stage 5 — Hosting (`review.py` + workflow)

The Graph API fetches media by URL, so assets are pushed to a `gh-pages` branch
and served by GitHub Pages.

The same branch carries a **review page** — the card, links to the actual source
articles, and the caption behind a collapsed section. This exists for two
reasons: the post's CTA sends people somewhere for "full stories", so that
destination must actually contain them; and if API publishing is ever
unavailable, the day's work is still usable by hand.

Assets are published under **dated filenames** (`daily-2026-08-09.png`). A
stable filename is a correctness bug rather than an untidiness: the URL handed
to Meta never changes, so the CDN in front of Pages can answer with the previous
day's bytes and Instagram posts the wrong picture. Distinct URLs remove the
possibility, and leave a dated archive behind.

Story links on the review page follow the model's ranking, not recency. That
distinction is load-bearing: the page used to list the most recent items, while
the model picks the lead story by engagement — so the story the headline was
about could be missing from its own list. Each headline now carries the
`source_url` it came from, validated against the fetched items.

The review page is the **link-in-bio destination**, so it is public and shows
only the card and the day's stories. It previously also displayed the caption,
which made sense while it doubled as an operator page for posting by hand; a
reader arriving from the post has no use for that post's raw caption. The
caption is still written to `caption.txt` and uploaded with the artifacts. Instagram strips URLs
from captions, so the bio link is the only clickable path off a post — it has to
be set manually on the profile, which the API cannot do.

### Stage 6 — Publishing (`publisher.py`)

Two-step container flow: `POST /media` → poll `status_code` → `POST /media_publish`.
Carousels take three stages: a child container per image (`is_carousel_item`),
a parent collecting them (`media_type=CAROUSEL`), then publish. The caption goes
on the parent only.

- **Image**: ~60s polling. **Reel**: ~300s, because Meta must download and
  transcode the file.
- `wait_for_image` polls the public URL before handing it to Meta, and reports
  the last HTTP status on timeout. A 404 and a slow CDN need opposite fixes.
- Two auth paths via `IG_API_MODE`:
  - `instagram_login` (default) → `graph.instagram.com`, no Facebook Page
  - `facebook_login` → `graph.facebook.com`, requires a linked Page

### Stage 7 — Token refresh (`refresh-token.yml`)

Weekly `GET /refresh_access_token` resets the 60-day expiry, then writes the new
value back via `gh secret set`. Masked with `::add-mask::` on capture. Weekly is
deliberate overkill — the token would need to miss ~8 runs to lapse.

---

## 3. Execution modes

Build and publish are separate so the two-step workflow (render → host → post)
doesn't regenerate content between steps. Running the full pipeline twice would
produce a different caption than the one already baked into the rendered image.

| Mode | Does |
|---|---|
| `build` | Fetch, summarise, render, write caption + review page. No posting. |
| `publish` | Read prebuilt assets from disk and post. Skips cleanly if unconfigured. |
| `full` | Both in one process (`--dry-run` to skip posting) |
| `whoami` | Print account id + username. Verifies the token. |
| `feedcheck` | Per-feed reachability report |
| `refresh` | Refresh the token; new token on stdout, status on stderr |

---

## 4. Failure design

Every external dependency here has failed at least once in production. The
design assumes that continues.

| Failure | Behaviour |
|---|---|
| One feed dead | Logged, skipped, run continues |
| All feeds dead | Widening window, then a clear error naming `feedcheck` |
| Claude unavailable | Falls back to Sonnet |
| Instagram not configured | Publish skips with exit 0 — an unconfigured account should not produce a daily failure email |
| Instagram configured but failing | Fails loudly |
| Image URL unreachable | Polls, then reports the actual HTTP status |
| ffmpeg missing | Clear error naming `POST_FORMAT=image` as the way out |
| Audio file missing | Warns, encodes a silent Reel |

The distinction between "not configured yet" and "configured but broken" is
deliberate. A run that fails daily for a known reason trains you to ignore
failure emails, which then hides the real one.

---

## 4b. Verified in production

Run #123 (2026-08-09), end to end, 82 seconds:

```
fetched 11 items; 13/14 feeds reachable (window 36h)
summarizing 11 items with claude-opus-4-7
brief built: 5 headlines, 8 hashtags
stock photo for 'woman deadlifting barbell in gym' by Willie Reese
rendered card to assets/daily.png (headline 102pt)
```

The headline landing at 102pt against a 118pt ceiling confirms both the
condensed face and the downward size search behaved as designed.

Run #121 published successfully to Instagram (`media_id=18082672631692674`).

---

## 5. Costs

- **Claude**: ~4k input + ~1k output per run, roughly $0.05/day at Opus pricing.
  System prompt caching reduces repeated same-day runs.
- **GitHub Actions / Pages**: free at this volume.
- **Instagram API**: free.

---

## 6. Known limitations

- **Reels published via API carry no trending-audio signal.** Picking a sound in
  the app gets a reach boost this cannot replicate. This is a platform
  limitation, not something the code can work around.
- **Generated images are not Claude's work.** Claude picks the scene; an
  external provider renders it, at that provider's cost and quality.
- **The condensed typeface is environment-dependent.** CI installs
  `fonts-dejavu-extra`; on a machine without a condensed face the headline
  falls back to regular bold and reads noticeably wider.
- **One story per card, but the caption still references several.** The card is
  now single-story like the reference; the reference achieves depth with
  carousels, which this does not yet produce.
- **The slowtwitch feed path is unverified** — see the comment in `config.py`.
- **Model-supplied source urls are validated, not trusted.** Each headline
  carries the url it came from; any url not present in the fetched items is
  dropped rather than published, because a fabricated link is worse than a
  missing one. Dropped entries are backfilled from the fetched items.
