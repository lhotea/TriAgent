# aigent — daily AI tool drops, on autopilot

A faceless Instagram channel that posts one AI tool per day as a 5-slide
carousel. Built with Claude Opus 4.7 (structured output + adaptive thinking),
PIL for the slides, and the Instagram Graph API for publishing.

The whole pipeline is monetization-shaped: every post pushes affiliate links
to the featured tool, a newsletter signup, and a paid prompt pack on Gumroad —
the three highest-RPM levers a faceless AI channel actually has.

## What it does

Every day:

1. Pulls the last 36 hours of AI launches from Product Hunt's AI/Dev Tools
   RSS feeds and Show-HN (filtered for AI keywords).
2. Sends the candidate launches to Claude, which **picks one tool** and
   returns a structured 5-slide carousel brief: cover hook, what it does,
   killer use-case (with a copy-paste prompt or 3-step workflow), pricing
   verdict, and a CTA slide.
3. Renders five 1080×1350 branded slides with PIL — different layout per
   role, fixed brand mark, slide indicator dots.
4. Assembles a caption with the hook, body, engagement question, and a
   monetization CTA stack (newsletter + prompt pack + tool affiliate).
5. Uploads all five slides to a public image host, then publishes a
   carousel via the Graph API's child-container flow.

## Why this niche

Faceless AI-tools content is the highest-RPM category a new IG channel can
realistically enter right now:

- **Affiliate payouts** for AI SaaS are 3–5× lifestyle/fitness affiliates —
  20–50% recurring or $30–$100 per signup is normal.
- **Content supply is endless** — Product Hunt + HN drop new launches daily,
  exactly the cadence Instagram rewards.
- **Format fits faceless** — screen-recordings, prompt screenshots, and text
  cards do better than face cams in this niche.
- **No FTC/regulatory minefield** like personal finance or supplements.

## Revenue plumbing

The caption is built around three monetization slots, all optional. Anything
not configured is silently dropped:

| Slot           | Env var          | What it does                                                      |
|----------------|------------------|-------------------------------------------------------------------|
| Affiliate link | `AFFILIATE_LINKS`| `slug=url` map. Only attaches when the chosen tool's slug matches.|
| Newsletter     | `NEWSLETTER_URL` | Adds a "daily AI tools in your inbox" CTA pointing to link-in-bio.|
| Prompt pack    | `GUMROAD_URL`    | Adds a "prompt pack pinned in our bio" CTA.                       |

IG strips URLs from captions, so all clickable links live on your
link-in-bio landing page (Linktree / Beacons / a static page on the same
GitHub Pages site that hosts the slides). Update that page when the
featured tool changes — the channel mentions it by name in the caption.

The `AFFILIATE_LINKS` map is intentionally strict: it only attaches an
affiliate URL when the chosen tool's slug matches an entry. We do **not**
fall through to a "featured deal" on an unrelated tool — that misaligned
promotion is the fastest way to tank CTR and trust on a new account.

## Setup

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
# Fill in the secrets
python -m aigent --dry-run   # builds slides + caption, skips publishing
python -m aigent             # publishes
```

After a dry-run, inspect `assets/daily_1.png` … `assets/daily_5.png` and
`assets/caption.txt`.

### Instagram Graph API prerequisites

1. Instagram **Business** or **Creator** account, linked to a Facebook Page.
2. A Meta App with the **Instagram Graph API** product enabled.
3. A **long-lived** page access token with `instagram_content_publish` and
   `instagram_basic` scopes. Short-lived tokens expire in 1 hour; swap them
   via `GET /oauth/access_token?grant_type=fb_exchange_token`.
4. Find your IG Business Account ID:
   `GET https://graph.facebook.com/v20.0/{page-id}?fields=instagram_business_account`.

`scripts/bootstrap_ig_credentials.py` automates steps 3 and 4. Grab a
short-lived user token from the Graph API Explorer (with the
`pages_show_list`, `pages_read_engagement`, `instagram_basic`,
`instagram_content_publish` scopes checked), then:

```bash
python scripts/bootstrap_ig_credentials.py \
    --app-id $FB_APP_ID \
    --app-secret $FB_APP_SECRET \
    --short-token $FB_SHORT_TOKEN
```

It prints `.env`-ready `IG_USER_ID=` and `IG_ACCESS_TOKEN=` lines for every
Page you admin that has an IG Business/Creator account linked.

### Public image host (for the carousel)

The Graph API fetches each slide by URL — base64 is not supported. The
bundled GitHub Actions workflow pushes `assets/daily_1.png` …
`daily_5.png` to a `gh-pages` branch on every run. Enable GitHub Pages on
this repo (Settings → Pages → Source: `gh-pages` branch, root), then set:

```
PUBLIC_IMAGE_BASE_URL=https://<user>.github.io/<repo>
```

Running outside Actions? Host the slide PNGs anywhere public (S3,
Cloudflare R2, a VPS) and point `PUBLIC_IMAGE_BASE_URL` at the directory.

### Affiliate program signups (do this once)

The channel earns nothing until you've registered for affiliate programs
and added the codes to `AFFILIATE_LINKS`. Realistic starter set, all
genuinely run affiliate programs as of writing:

- Cursor, Perplexity Pro, ElevenLabs, Notion, Jasper, Descript, Murf,
  Runway, Pictory, Otter, Synthesia, Krea, HeyGen.
- For broader catalog access: Impact, PartnerStack, Rewardful, Reflio.

Map example for `.env`:

```
AFFILIATE_LINKS=cursor=https://cursor.com/?ref=YOU,perplexity=https://perplexity.ai/pro?referral=YOU,elevenlabs=https://elevenlabs.io/?from=YOU
```

The slug is matched case-insensitively against either Claude's chosen
`tool.slug` or a slugified version of `tool.name`. So `perplexity` will
match a tool named `Perplexity AI` whose slug is `perplexity-ai`.

## Scheduling

The GitHub Actions workflow (`.github/workflows/daily-post.yml`) runs the
agent daily at 13:00 UTC. Add these repo **secrets**:

- `ANTHROPIC_API_KEY` (required — build fails without it)
- `IG_USER_ID` (required for publish)
- `IG_ACCESS_TOKEN` (required for publish)
- `PUBLIC_IMAGE_BASE_URL` (required for publish)
- `AFFILIATE_LINKS` (optional)
- `NEWSLETTER_URL` (optional)
- `GUMROAD_URL` (optional)

And these repo **variables**:

- `BRAND_HANDLE`
- `BRAND_NAME`

## Layout

```
src/aigent/
├── agent.py         # orchestrator
├── config.py        # Settings, RSS feed list, AI keyword filter
├── news.py          # feedparser-based fetcher (PH + HN)
├── summarizer.py    # Claude Opus 4.7 with the 5-slide Pydantic schema
├── image.py         # PIL multi-slide carousel renderer
├── monetization.py  # caption + affiliate / newsletter / Gumroad CTAs
├── publisher.py     # Instagram Graph API carousel publish
└── __main__.py      # CLI entry
```

## Cost

- **Claude**: ~5k input + ~1.5k output tokens per run → roughly $0.07/day at
  current Opus 4.7 pricing. The system prompt is cached (5-minute TTL).
- **Instagram + image hosting**: free at any reasonable volume.

## Extending

- Swap a slide for an AI-generated screenshot of the tool (DALL·E, Imagen,
  Replicate). The renderer accepts any PIL `Image` per-slide.
- Post Reels too: convert the carousel to a Ken-Burns video with ffmpeg
  and call `/media` with `media_type=REELS` + a `video_url`.
- A/B test hooks: generate two briefs, publish one to a shadow account,
  pick the winner on 4-hour engagement, swap the cover slide on the main
  account.
- Track click-through: route every bio CTA through a short.io/short URL so
  per-CTA click counts are visible in the dashboard.
