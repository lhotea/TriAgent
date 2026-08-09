"""Resolve a background picture for the day's post.

Claude decides *what* the picture should show (`DailyBrief.image_query`) — it's
good at reading a story and naming a scene. It cannot draw it: the Anthropic API
has no image-generation endpoint. So the query is handed to whichever renderer
is configured, tried in order:

1. **Image generation** (`IMAGE_GEN_API_KEY`) — an OpenAI-compatible images
   endpoint. Truly generated art, costs per image.
2. **Stock search** (`PEXELS_API_KEY`) — a real licensed photograph. Free, and
   closer to the reference account's look, which uses real imagery.
3. **Local directory** — whatever is in `assets/backgrounds/`.
4. **Nothing** — the card falls back to its headline-led layout.

Every step is optional and failure at any level falls through to the next, so a
missing key or a provider outage degrades the picture rather than the run.
"""

from __future__ import annotations

import base64
import logging
import os
from pathlib import Path

import requests

log = logging.getLogger(__name__)

GEN_ENDPOINT = os.environ.get(
    "IMAGE_GEN_ENDPOINT", "https://api.openai.com/v1/images/generations"
)
GEN_MODEL = os.environ.get("IMAGE_GEN_MODEL", "gpt-image-1")
PEXELS_ENDPOINT = "https://api.pexels.com/v1/search"

# Appended to the model's scene description. The reference imagery is dark and
# cinematic, and the card lays white text over the lower part of the photo.
STYLE_SUFFIX = (
    "cinematic photograph, dramatic low-key lighting, dark moody background, "
    "high contrast, shallow depth of field, no text, no watermark, no logos"
)


def _generate(query: str, out_path: Path, api_key: str, timeout: int = 120) -> Path | None:
    """Generate an image from the query via an OpenAI-compatible endpoint."""
    try:
        r = requests.post(
            GEN_ENDPOINT,
            headers={"Authorization": f"Bearer {api_key}"},
            json={
                "model": GEN_MODEL,
                "prompt": f"{query}. {STYLE_SUFFIX}",
                "size": "1024x1536",  # portrait, matching the card's photo band
                "n": 1,
            },
            timeout=timeout,
        )
        if not r.ok:
            log.warning("image generation failed (%s): %s", r.status_code, r.text[:300])
            return None
        payload = r.json()["data"][0]
        if "b64_json" in payload:
            out_path.write_bytes(base64.b64decode(payload["b64_json"]))
        elif "url" in payload:
            img = requests.get(payload["url"], timeout=timeout)
            img.raise_for_status()
            out_path.write_bytes(img.content)
        else:
            log.warning("image generation returned neither b64_json nor url")
            return None
        log.info("generated image for %r", query)
        return out_path
    except Exception as exc:
        log.warning("image generation error: %s", exc)
        return None


def _stock(query: str, out_path: Path, api_key: str, timeout: int = 30) -> Path | None:
    """Fetch a licensed stock photograph matching the query."""
    try:
        r = requests.get(
            PEXELS_ENDPOINT,
            headers={"Authorization": api_key},
            params={"query": query, "orientation": "portrait", "per_page": 1},
            timeout=timeout,
        )
        if not r.ok:
            log.warning("stock search failed (%s): %s", r.status_code, r.text[:300])
            return None
        photos = r.json().get("photos") or []
        if not photos:
            log.warning("stock search returned no photos for %r", query)
            return None
        src = photos[0]["src"].get("large2x") or photos[0]["src"]["large"]
        img = requests.get(src, timeout=timeout)
        img.raise_for_status()
        out_path.write_bytes(img.content)
        log.info(
            "stock photo for %r by %s", query, photos[0].get("photographer", "unknown")
        )
        return out_path
    except Exception as exc:
        log.warning("stock search error: %s", exc)
        return None


def resolve_background(
    query: str,
    out_path: Path,
    *,
    gen_key: str | None = None,
    stock_key: str | None = None,
    fallback: Path | None = None,
) -> Path | None:
    """Return a usable background image path, or None.

    Tries generation, then stock, then the supplied local fallback. Never
    raises — a picture is a nice-to-have, and losing it should not take down a
    post that is otherwise ready.
    """
    if not query.strip():
        return fallback

    out_path.parent.mkdir(parents=True, exist_ok=True)

    if gen_key:
        if got := _generate(query, out_path, gen_key):
            return got
        log.info("generation unavailable, trying stock")

    if stock_key:
        if got := _stock(query, out_path, stock_key):
            return got
        log.info("stock unavailable, trying local backgrounds")

    if fallback is not None:
        log.info("using local background %s", fallback.name)
    else:
        log.info("no background available — card will use headline-led layout")
    return fallback
