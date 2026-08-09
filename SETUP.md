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

## 6. Token auto-refresh

Without this, publishing dies ~60 days after setup.

1. https://github.com/settings/personal-access-tokens → **Fine-grained token**.
2. Scope it to **this repository only**.
3. Permissions: **Secrets → Read and write**.
4. Store as secret **`GH_PAT`**.

The weekly `refresh-instagram-token` workflow then rotates the token unattended.
`GITHUB_TOKEN` cannot write secrets, which is why a PAT is required; without it
the job fails loudly rather than appearing to work.

---

## 7. Optional — Reels with music

Only Reels can carry audio. Image posts cannot, at all.

1. Add a **licensed** audio file to the repo, e.g. `assets/audio/theme.mp3`.

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
| `REEL_AUDIO` | `assets/audio/theme.mp3` |
| `REEL_SECONDS` | `8` (optional) |

Leave `POST_FORMAT` unset or `image` for the static card.

---

## 8. Optional — background photos

Drop `.jpg` / `.png` / `.webp` files into `assets/backgrounds/`. One is chosen
per day, deterministically from the date. Empty directory uses the gradient.

> ⚠️ Use images you have rights to. Photos taken from publishers' articles are a
> legal problem on a monetized account.

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
| 6 | `GH_PAT` | Secret | ✅ beyond 60 days |
| 7 | `POST_FORMAT`, `REEL_AUDIO` | Variables | only for Reels |
| 8 | `assets/backgrounds/*` | Repo | optional |
| 9 | `AFFILIATE_URLS` | Secret | optional |

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
