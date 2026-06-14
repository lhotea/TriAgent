"""Bootstrap Instagram Graph API credentials in one shot.

Given a short-lived Facebook user access token + your Meta App ID/Secret,
this script:

  1. Exchanges the short-lived user token for a long-lived (~60 day) one.
  2. Lists the Facebook Pages you admin and their long-lived page tokens
     (page tokens derived from a long-lived user token don't expire).
  3. For each page, fetches the linked Instagram Business Account ID.
  4. Prints `.env`-ready lines for IG_USER_ID and IG_ACCESS_TOKEN.

Get the inputs:
  - App ID / App Secret: https://developers.facebook.com/apps/ → your app → Settings → Basic.
  - Short-lived user token: https://developers.facebook.com/tools/explorer/ →
    pick your app → "Get User Access Token" → check `pages_show_list`,
    `pages_read_engagement`, `instagram_basic`, `instagram_content_publish`,
    `business_management` → Generate.

Run:
  python scripts/bootstrap_ig_credentials.py \\
      --app-id 1234567890 \\
      --app-secret abc... \\
      --short-token EAAB...

Or via env vars: FB_APP_ID, FB_APP_SECRET, FB_SHORT_TOKEN.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from typing import Any

import requests

GRAPH = "https://graph.facebook.com/v20.0"


def _get(path: str, **params: Any) -> dict:
    r = requests.get(f"{GRAPH}/{path}", params=params, timeout=30)
    if not r.ok:
        sys.exit(f"\nGraph API error on {path}:\n  {r.status_code} {r.text}\n")
    return r.json()


def exchange_long_lived_user_token(app_id: str, app_secret: str, short_token: str) -> str:
    """Short-lived user token (1h) → long-lived user token (~60d)."""
    data = _get(
        "oauth/access_token",
        grant_type="fb_exchange_token",
        client_id=app_id,
        client_secret=app_secret,
        fb_exchange_token=short_token,
    )
    token = data.get("access_token")
    if not token:
        sys.exit(f"no access_token in exchange response: {data}")
    return token


def list_pages(long_user_token: str) -> list[dict]:
    """Pages the user admins, with page-scoped tokens. Page tokens derived from
    a long-lived user token are themselves long-lived (effectively no expiry
    for content publishing as long as the user doesn't revoke perms)."""
    data = _get("me/accounts", access_token=long_user_token, fields="id,name,access_token")
    return data.get("data", [])


def ig_business_account(page_id: str, page_token: str) -> dict | None:
    data = _get(
        page_id,
        access_token=page_token,
        fields="instagram_business_account{id,username,name}",
    )
    return data.get("instagram_business_account")


def main() -> int:
    parser = argparse.ArgumentParser(description="Bootstrap IG Graph API creds.")
    parser.add_argument("--app-id", default=os.environ.get("FB_APP_ID"))
    parser.add_argument("--app-secret", default=os.environ.get("FB_APP_SECRET"))
    parser.add_argument("--short-token", default=os.environ.get("FB_SHORT_TOKEN"))
    parser.add_argument(
        "--json", action="store_true", help="Emit a JSON blob instead of human-readable output."
    )
    args = parser.parse_args()

    missing = [n for n, v in (
        ("--app-id / FB_APP_ID", args.app_id),
        ("--app-secret / FB_APP_SECRET", args.app_secret),
        ("--short-token / FB_SHORT_TOKEN", args.short_token),
    ) if not v]
    if missing:
        sys.exit("missing inputs: " + ", ".join(missing))

    print("→ exchanging short-lived user token for a long-lived one…", file=sys.stderr)
    long_user_token = exchange_long_lived_user_token(
        args.app_id, args.app_secret, args.short_token
    )

    print("→ listing Facebook Pages you admin…", file=sys.stderr)
    pages = list_pages(long_user_token)
    if not pages:
        sys.exit(
            "no Pages found. You must be an admin of at least one Page, and the\n"
            "token must include `pages_show_list` + `pages_read_engagement`."
        )

    results: list[dict] = []
    for p in pages:
        page_id = p["id"]
        page_name = p.get("name", "<unnamed>")
        page_token = p["access_token"]
        print(f"→ checking IG link on '{page_name}' ({page_id})…", file=sys.stderr)
        ig = ig_business_account(page_id, page_token)
        results.append(
            {
                "page_id": page_id,
                "page_name": page_name,
                "page_access_token": page_token,
                "instagram_business_account_id": ig.get("id") if ig else None,
                "instagram_username": ig.get("username") if ig else None,
            }
        )

    if args.json:
        print(json.dumps(results, indent=2))
        return 0

    print()
    linked = [r for r in results if r["instagram_business_account_id"]]
    if not linked:
        print("No Pages have a linked Instagram Business/Creator account.")
        print(
            "Fix in the IG mobile app: Settings → Account Center → Accounts →\n"
            "link a Facebook Page (the Page must be one of the ones above).\n"
        )
        for r in results:
            print(f"  · {r['page_name']} ({r['page_id']}) — no IG account linked")
        return 1

    print("✓ Found Instagram-linked Page(s). Use the matching pair in .env:\n")
    for r in linked:
        print(f"# Page: {r['page_name']} (IG: @{r['instagram_username']})")
        print(f"IG_USER_ID={r['instagram_business_account_id']}")
        print(f"IG_ACCESS_TOKEN={r['page_access_token']}")
        print()

    unlinked = [r for r in results if not r["instagram_business_account_id"]]
    if unlinked:
        print("Pages without an IG link (skipped):", file=sys.stderr)
        for r in unlinked:
            print(f"  · {r['page_name']} ({r['page_id']})", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
