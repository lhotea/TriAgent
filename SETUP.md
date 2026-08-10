# TriAgent — Setup

Every manual step needed to get this running, in order. For how the agent works,
see [SPEC.md](SPEC.md).

Budget about an hour, most of it waiting on Meta. Steps 1–4 are quick; step 5 is
the one that fights back.

---

## 1. Anthropic API key

1. https://console.anthropic.com → **API Keys** → create one.
2. Repo → **Settings → Secrets and variables → Actions → Secrets** →
   `ANTHROPIC_API_KEY`.

This is the only secret the build step needs. Everything else is publish-side.

---

## 2. GitHub Pages

The Graph API fetches media by URL and rejects base64, so the rendered card has
to be publicly hosted before it can be posted.

1. Repo → **Settings → Pages**.
2. **Source**: *Deploy from a branch* → branch **`gh-pages`** → folder **`/ (root)`** → Save.
3. The branch is created automatically on the first run, so it may not exist yet.
   Run the workflow once, then come back and set this.

Then set the secret:

```
PUBLIC_IMAGE_BASE_URL = https://<your-username>.github.io/<RepoName>
```

> ⚠️ **Case-sensitive.** A repo named `TriAgent` is served at `/TriAgent/`, not
> `/triagent/`. Getting this wrong produces a 404 that looks exactly like a slow
> CDN. Confirm by opening `<that URL>/daily.png` in a browser before continuing.

No trailing slash, no filename — the agent appends `/daily.png` itself.

---

## 3. Brand identity

Repo → **Settings → Secrets and variables → Actions → Variables**:

| Variable | Example |
|---|---|
| `BRAND_NAME` | `TriPulse Daily` |
| `BRAND_HANDLE` | `@yourhandle` |

> ⚠️ **Set these explicitly.** GitHub substitutes an *empty string* for an unset
> `vars.X`, so the code's defaults never fire — which is how the brand name once
> vanished from the card and the caption ended with a literal `link in bio ()`.

---

## 4. RSS feeds

Optional but recommended — the built-in list is small and feed URLs rot.

Set a **variable** named `FEEDS` to a comma- or newline-separated list. Bare
domains are fine; the scheme is added automatically.

```
triathlete.com/feed/, dcrainmaker.com/feed, 220triathlon.com/feed/atom
```

Directories like `rss.feedspot.com/triathlon_rss_feeds/` are a good source.

Use the full feed path, not the bare domain: `tri247.com` returns 403, while
`www.tri247.com/feed` works. Run #123 reached 13 of 14 feeds, the single failure
being exactly that mistake.

Verify before relying on them:

```bash
python -m triagent --mode feedcheck
```

The build log also prints `N/M feeds reachable` on every run.

---

## 5. Instagram credentials

The long one. You need an **Instagram Business or Creator** account.

We use **Instagram API with Instagram Login**, which needs no Facebook Page.
The older Facebook-Login route requires a Page linked to the IG account, with
that Page's access granted to the app — a chain with several silent failure
points. Avoid it unless you have a reason.

### 5.1 Create a Meta app

1. https://developers.facebook.com/apps → **Create app**.
2. When asked about a Business Portfolio, choose **"I don't want to connect a
   business portfolio yet."**

   > ⚠️ Business-portfolio-owned apps enforce extra role checks that produce
   > `Insufficient developer role` errors which are painful to unpick. A
   > standalone app makes you sole admin by construction.
3. Use case: **Other** → type: **Business**.

### 5.2 Add the Instagram use case

Newer consoles are use-case-based, with no *Products* section:

1. **Use cases** in the sidebar → find **Manage messaging & content on
   Instagram** → **Customize**.
2. Choose **Instagram API setup with Instagram login** (not Facebook login).

### 5.3 Add your account as an Instagram Tester

This is the step that blocks token generation, and it is easy to miss.

1. **App roles → Roles** (`/apps/<APP_ID>/roles/roles/`).
2. Scroll past Administrators / Developers / Testers to the separate
   **Instagram Testers** section.

   > ⚠️ This is a *different list* from the app's Testers role. Adding your
   > Instagram account here does **not** change your admin role. Using the wrong
   > list produces an error about demoting all admins.
3. **Add Instagram Testers** → enter your **Instagram username** (not your
   Facebook name, not an email).
4. Accept the invite from inside Instagram — it stays pending until you do:
   - Web: https://www.instagram.com/accounts/manage_access/ → **Tester invites**
   - App: Settings → *Apps and websites* → **Tester invites**

### 5.4 Generate the token

1. Back in the Instagram use case → **Generate access tokens** → **Add account**.
2. Authorize, then **Generate token**. Copy it immediately — it's shown once.

This token is already long-lived (~60 days). **Do not run it through
`ig_exchange_token`** — that endpoint only accepts short-lived tokens and will
return `Session key invalid` / "access token type is not valid".

Verify it:

```
https://graph.instagram.com/v25.0/me?fields=id,user_id,username&access_token=YOUR_TOKEN
```

Should return your account. Take the **`user_id`** value (starts `17841…`).

### 5.5 Store them

Repo → **Secrets**:

| Secret | Value |
|---|---|
| `IG_USER_ID` | the `user_id` from above |
| `IG_ACCESS_TOKEN` | the token |

`IG_API_MODE` defaults to `instagram_login`; leave it unset.

---

## 5b. Put the link in your Instagram bio ⚠️

**Nothing works without this and the agent cannot do it for you.** Instagram
strips URLs from captions — they are never clickable — so every post says "link
in bio". If the bio has no link, that instruction dead-ends and the whole
call-to-action is wasted.

1. Instagram → **Edit profile** → **Links** → **Add external link**.
2. URL: the same value as `PUBLIC_IMAGE_BASE_URL`, e.g.
   `https://lhotea.github.io/TriAgent`
3. Title: something like "Today's stories".

That page is the review page the agent republishes every run: today's card, the
day's stories as real links to the source articles, and the caption.

> Two things to know:
> - The page only updates on a **non-dry-run**. Dry runs deliberately skip the
>   gh-pages step, so the live page stays on the previous run's content.
> - The card is published under a **dated filename** (`daily-2026-08-09.png`)
>   because a stable URL let the CDN serve a stale image to Instagram. The bio
>   link points at the directory, not the image, so it always shows the latest.

If you'd rather send traffic to Linktree or your own landing page, put that URL
in the bio instead — just make sure whatever you link actually contains the
stories the post promises.

---

## 6. Token auto-refresh

Without this, publishing dies ~60 days after setup.

1. https://github.com/settings/personal-access-tokens → **Fine-grained token**.
2. Scope it to **this repository only**.
3. Permissions: **Secrets → Read and write**.
4. Store as secret **`GH_PAT`**.

The weekly `refresh-instagram-token` workflow then rotates the token unattended.

> ⚠️ Meta will not refresh a token less than **24 hours old**. A refresh run
> shortly after first setup fails for that reason alone — it is not a `GH_PAT`
> problem. Tell the two apart by where the job stops: a failure in the refresh
> call is Meta's age limit; a failure at `gh secret set` is the PAT.

`GITHUB_TOKEN` cannot write secrets, which is why a PAT is required; without it
the job fails loudly rather than appearing to work.

---

## 7. Optional — Reels with music

**Only Reels can carry audio.** Carousels and single images are silent — an
Instagram rule, not a limit of this agent. So a post cannot be both a carousel
and have music. What it *can* be is a Reel that steps through the same three
slides with music over them, which gives you multiple stories and sound in one
post. That is what `POST_FORMAT=reel` now produces.

1. Drop **licensed** audio into `assets/music/`. One track is chosen per day
   from the date, so a given day always gets the same one. An empty directory
   means a silent Reel, which still publishes. `REEL_AUDIO` still works as an
   explicit single-file override.

   > ⚠️ The API cannot use Instagram's in-app music library — that catalogue is
   > licensed for use inside the app only. Audio published this way is baked
   > into the file, so you need rights to the track. Commercial music will get
   > the post muted or removed.
   >
   > Also note: a Reel published via API carries no trending-audio signal, so it
   > won't get the reach boost that picking a sound in the app gives you.

2. Set **variables**:

| Variable | Value |
|---|---|
| `POST_FORMAT` | `reel` |
| `REEL_AUDIO` | optional — pins one file instead of rotating `assets/music/` |
| `REEL_SECONDS` | seconds **per slide** (default 4) |

`POST_FORMAT` defaults to `carousel` (3 slides). Set `image` for a single card,
or `reel` for video. `CAROUSEL_SLIDES` overrides the slide count (2-10).

The brand mark comes from `assets/images/logo.png`. Replace that file to change
it — the dark backing plate is keyed out automatically, and only the dominant
shape is used, so surrounding artwork is ignored.

---

## 7b. Performance tracking

Runs automatically — no setup beyond the Instagram credentials you already
have. The `collect-insights` workflow runs daily at 18:00 UTC (five hours after
the post), pulls per-post metrics, and publishes them to:

```
https://<your-username>.github.io/<RepoName>/insights.csv
```

Recent posts are re-polled every run because insights keep moving for a few
days; a post checked an hour after publishing is not final.

Read the **engagement_rate** column rather than raw likes — it is interactions
divided by reach, so it stays comparable as the account grows. The **hook**
column is the first caption line, so you can see which openings earned saves
and which died. Each run also prints a ranking to the workflow summary.

Give it two weeks before drawing conclusions. Below roughly ten posts the
numbers are noise.

> The CSV lands on a public branch of a public repo. Post performance is not
> especially sensitive, but if you'd rather it weren't public, drop the
> "Publish CSV to gh-pages" step and read the run artifact instead.

---

## 8. Pictures — strongly recommended

The card design puts a photo across the top ~58%. Without one it falls back to a
headline-only layout, which works but looks plainer than the reference.

Claude decides **what** the picture should show by reading the lead story. It
cannot draw it — the Anthropic API has no image-generation endpoint — so a
provider renders or finds it. Sources are tried in order:

### 8a. Stock photography (recommended, free)

1. https://www.pexels.com/api/ → sign up → copy your key.
2. Repo secret **`PEXELS_API_KEY`**.

Real licensed photography, free, and closest to the reference look. Verified
working: run #123 resolved *"woman deadlifting barbell in gym"* from the day's
strength-training story.

### 8b. Image generation (optional, paid)

Repo secret **`IMAGE_GEN_API_KEY`** — any OpenAI-compatible images endpoint.
Override the endpoint and model with `IMAGE_GEN_ENDPOINT` / `IMAGE_GEN_MODEL`.

Costs per image. Worth knowing that generated images still tend to give
themselves away on human anatomy, which is most of triathlon — so stock is often
the better-looking option, not merely the cheaper one.

### 8c. Local files

Drop `.jpg` / `.png` / `.webp` into `assets/backgrounds/`. One is chosen per day
from the date.

> ⚠️ Use images you have rights to. Photos taken from publishers' articles are a
> legal problem on a monetized account.

If none of the three is configured, the card uses its headline-led layout — no
failure, just a plainer post.

---

## 9. Optional — affiliate links

Secret `AFFILIATE_URLS`, comma-separated. When set, the caption adds a featured-
deal line and the CTA mentions gear picks. When unset, neither appears — the
caption never promises what isn't configured.

---

## 10. Run it

Actions → **daily-post** → **Run workflow**. Leave `dry_run` unchecked to post.

The schedule is 13:00 UTC daily.

### Local

```bash
pip install -e ".[test]"
cp .env.example .env    # fill in
python -m triagent --mode build     # render only
python -m triagent --mode full --dry-run
```

---

## Checklist

| # | Item | Where | Required |
|---|---|---|---|
| 1 | `ANTHROPIC_API_KEY` | Secret | ✅ |
| 2 | Pages enabled on `gh-pages` | Settings → Pages | ✅ to publish |
| 2 | `PUBLIC_IMAGE_BASE_URL` | Secret | ✅ to publish |
| 3 | `BRAND_NAME`, `BRAND_HANDLE` | Variables | strongly recommended |
| 4 | `FEEDS` | Variable | recommended |
| 5 | IG Business/Creator account | Instagram | ✅ to publish |
| 5 | Meta app, no business portfolio | developers.facebook.com | ✅ to publish |
| 5 | Instagram Tester role accepted | App roles + Instagram | ✅ to publish |
| 5 | `IG_USER_ID`, `IG_ACCESS_TOKEN` | Secrets | ✅ to publish |
| 5b | Link added to Instagram bio | Instagram app | ✅ or the CTA dead-ends |
| 6 | `GH_PAT` | Secret | ✅ beyond 60 days |
| 7 | `POST_FORMAT`, `REEL_AUDIO` | Variables | only for Reels |
| 8 | `PEXELS_API_KEY` | Secret | strongly recommended |
| 8 | `IMAGE_GEN_API_KEY` | Secret | optional, paid |
| 8 | `assets/backgrounds/*` | Repo | optional fallback |
| 9 | `AFFILIATE_URLS` | Secret | optional |
| 7b | Performance tracking | automatic | no setup needed |

---

## Troubleshooting

| Symptom | Cause |
|---|---|
| `ModuleNotFoundError: triagent` | Workflow didn't `pip install -e .` |
| `Missing required env var` | A secret isn't set |
| `no triathlon news found` | All feeds unreachable — run `--mode feedcheck` |
| `No scheme supplied` | Feed URL without `https://` (auto-fixed now) |
| `image URL did not become reachable` + 404 | `PUBLIC_IMAGE_BASE_URL` wrong, usually capitalisation |
| `Session key invalid` on exchange | Token is already long-lived; skip the exchange |
| `Insufficient developer role` | App owned by a business portfolio, or 2FA off, or Instagram Tester role not accepted |
| `me/accounts` returns `data: []` | No Facebook Page — irrelevant on the Instagram Login path |
| Caption ends with `link in bio ()` | `BRAND_HANDLE` unset |
| `ffmpeg is required` | `POST_FORMAT=reel` without ffmpeg — CI has it, local may not |
| Refresh fails: token "too new" / `OAuthException` | Meta requires the token to be **24h old** before it can be refreshed. Not a `GH_PAT` problem — wait and re-run. |
| Card has no photo, just a headline | No picture source configured — see §8 |
| Posted image is not the newest one | Fixed by dated filenames; older posts used a cached stable URL |
| "Link in bio" goes nowhere | No link set on the Instagram profile — see §5b |
| Live page shows an old post | Last run was a dry run; gh-pages only updates on a real run |
| Headline looks wide, not condensed | No condensed font installed; CI adds `fonts-dejavu-extra` |
| `403 Forbidden` on a feed | Often a bare domain — use the full feed path, e.g. `www.tri247.com/feed` |
