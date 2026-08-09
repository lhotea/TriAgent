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

---

## 2. Pipeline

```
RSS feeds ──► fetch_recent_widening ──► Claude Opus ──► DailyBrief
                                                            │
                    ┌───────────────────────────────────────┤
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
- **Per-feed isolation.** A feed that 404s, times out, or fails DNS is logged
  and skipped. The run continues on whatever is left.
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
| `hook` | First caption line. The scroll-stopper. Everything else is downstream of it. |
| `headlines[]` | 3–5 rewritten for Instagram, ranked, juiciest first |
| `caption_body` | 80–150 words, conversational |
| `engagement_prompt` | A question. Comments lift the post in the feed. |
| `hashtags[]` | 15–25, mixing broad reach tags with niche ones |

Falls back to Sonnet 4.5 if Opus is unavailable.

### Stage 3 — Rendering (`image.py`, `video.py`)

- `render_card` → 1080×1350 PNG (4:5 feed post)
- `render_reel_frame` → 1080×1920 PNG, composed at Reel dimensions rather than
  letterboxed, and keeping the top/bottom 15% clear of Instagram's UI overlay
- `build_reel` → ffmpeg encodes a slow 1.0→1.08 zoom over the still, H.264+AAC,
  `+faststart`

Backgrounds come from `assets/backgrounds/`, selected deterministically by date.
Photos are darkened, blurred and scrimmed so text stays legible. Empty directory
falls back to a gradient. Photos are user-supplied deliberately — dropping a
publisher's press image into a monetized post is a rights problem, and that
isn't a decision this code should make on someone's behalf.

### Stage 4 — Caption assembly (`monetization.py`)

Hook → body → engagement prompt → CTA → optional affiliate line → hashtags.

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

### Stage 6 — Publishing (`publisher.py`)

Two-step container flow: `POST /media` → poll `status_code` → `POST /media_publish`.

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
- **Backgrounds must be supplied manually.** No stock-photo API is wired in.
- **The slowtwitch feed path is unverified** — see the comment in `config.py`.
- **Headline-to-URL mapping is approximate.** Claude rewrites titles, so the
  review page lists the source items the brief was built from rather than
  claiming an exact per-headline mapping.
