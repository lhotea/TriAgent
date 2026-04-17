# TriAgent

Daily triathlon news agent that publishes an engagement-optimized post to Instagram.
Built with Claude Opus 4.7 (structured output + adaptive thinking), PIL for the image
card, and the Instagram Graph API for publishing.

## What it does

Every day:

1. Pulls the last 36 hours of triathlon news from a curated set of RSS feeds
   (Triathlete, Slowtwitch, Tri247, World Triathlon, …).
2. Sends the raw headlines to Claude, which ranks them for Instagram engagement
   value and returns a structured brief: hook, rewritten headlines, caption body,
   engagement prompt, and hashtags.
3. Renders a 1080×1350 branded image card (PIL) with the hook + top 3 headlines.
4. Uploads the card to a public image host, then publishes via the Graph API's
   two-step container flow.
5. Rotates affiliate signals through the caption + link-in-bio.

## Ad revenue — how the money actually flows

Instagram does not pay per post. Direct monetization on this account comes from:

- **Affiliate links** in the link-in-bio landing page (primary).
- **Sponsored posts / brand deals** — priced on engagement, so every reply,
  save, and share compounds future rate-card value.
- **Reels Play bonuses** (invitation-based, secondary).

Everything in the agent points at the same lever: **engagement**. The Claude
prompt is tuned to pick headlines that trigger identification and debate over
neutral reporting; the caption always ends with an engagement prompt to pull
comments, which the algorithm rewards with reach.

## Setup

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
# Fill in the secrets
python -m triagent --dry-run   # builds the post, skips publishing
python -m triagent             # publishes
```

### Instagram Graph API prerequisites

1. Instagram **Business** or **Creator** account, linked to a Facebook Page.
2. A Meta App with the **Instagram Graph API** product enabled.
3. A **long-lived** page access token with `instagram_content_publish` and
   `instagram_basic` scopes. Short-lived tokens expire in 1 hour; swap them
   via `GET /oauth/access_token?grant_type=fb_exchange_token`.
4. Find your IG Business Account ID:
   `GET https://graph.facebook.com/v20.0/{page-id}?fields=instagram_business_account`.

### Public image host

The Graph API fetches the image by URL — it does **not** accept base64. Host
`assets/daily.png` somewhere public (S3, Cloudflare R2, GitHub Pages on this
repo, a VPS). Point `PUBLIC_IMAGE_BASE_URL` at the directory that serves it.
The agent builds the URL as `{PUBLIC_IMAGE_BASE_URL}/daily.png`.

If you run on GitHub Actions, one common pattern: commit the rendered card
back to a `gh-pages` branch in a separate step, then set
`PUBLIC_IMAGE_BASE_URL=https://<user>.github.io/triagent`.

## Scheduling

A GitHub Actions workflow (`.github/workflows/daily-post.yml`) runs the agent
daily at 13:00 UTC. Add these repo secrets:

- `ANTHROPIC_API_KEY`
- `IG_USER_ID`
- `IG_ACCESS_TOKEN`
- `PUBLIC_IMAGE_BASE_URL`
- `LINK_IN_BIO_URL`
- `AFFILIATE_URLS` (optional)

And these repo variables:

- `BRAND_HANDLE`
- `BRAND_NAME`

## Layout

```
src/triagent/
├── agent.py         # orchestrator
├── config.py        # Settings, RSS feed list
├── news.py          # feedparser-based RSS fetcher
├── summarizer.py    # Claude Opus 4.7 with Pydantic-typed output
├── image.py         # PIL branded card renderer
├── monetization.py  # caption assembly, affiliate rotation
├── publisher.py     # Instagram Graph API two-step publish
└── __main__.py      # CLI entry
```

## Cost

- **Claude**: ~4k input + ~1k output tokens per run → roughly $0.05/day at
  current Opus 4.7 pricing. Adaptive thinking adds variability; the system
  prompt is cached (5-minute TTL), so repeated same-day runs hit the cache.
- **Instagram + image hosting**: free at any reasonable volume.

## Extending

- Swap the PIL card for an AI-generated image (DALL·E, Imagen) — replace
  `image.py::render_card`.
- Post as Reels: add a Ken Burns pan + caption overlay via ffmpeg, then hit
  `/media` with `media_type=REELS` and a `video_url`.
- A/B test hooks: generate two briefs, publish one to a shadow account,
  pick the winner on 4-hour engagement.
