from __future__ import annotations

import logging
import time

import requests
from tenacity import retry, stop_after_attempt, wait_exponential

log = logging.getLogger(__name__)

GRAPH = "https://graph.facebook.com/v20.0"


class InstagramPublisher:
    """Three-step carousel publish via the Instagram Graph API.

    Flow (per Meta's docs):
      1. For each slide image URL, create a child container with `is_carousel_item=true`.
      2. Create the carousel container with `media_type=CAROUSEL`, `children=<comma-ids>`,
         and the caption.
      3. POST /media_publish with the carousel container's id.

    Requires an Instagram Business or Creator account linked to a Facebook Page,
    plus a long-lived user access token with `instagram_content_publish` and
    `instagram_basic` scopes.
    """

    # IG carousels accept 2-10 items.
    MIN_ITEMS = 2
    MAX_ITEMS = 10

    def __init__(self, ig_user_id: str, access_token: str):
        self.ig_user_id = ig_user_id
        self.access_token = access_token

    @retry(stop=stop_after_attempt(3), wait=wait_exponential(min=2, max=20))
    def _post(self, path: str, **params) -> dict:
        params["access_token"] = self.access_token
        r = requests.post(f"{GRAPH}/{path}", data=params, timeout=30)
        if not r.ok:
            log.error("graph %s failed: %s", path, r.text)
            r.raise_for_status()
        return r.json()

    @retry(stop=stop_after_attempt(3), wait=wait_exponential(min=2, max=20))
    def _get(self, path: str, **params) -> dict:
        params["access_token"] = self.access_token
        r = requests.get(f"{GRAPH}/{path}", params=params, timeout=30)
        if not r.ok:
            log.error("graph GET %s failed: %s", path, r.text)
            r.raise_for_status()
        return r.json()

    def _wait_finished(self, container_id: str, *, attempts: int = 30, delay: float = 3.0) -> None:
        for _ in range(attempts):
            status = self._get(container_id, fields="status_code")
            code = status.get("status_code")
            if code == "FINISHED":
                return
            if code == "ERROR":
                raise RuntimeError(f"container {container_id} processing failed: {status}")
            time.sleep(delay)
        raise TimeoutError(f"container {container_id} never reached FINISHED")

    def publish_carousel(self, image_urls: list[str], caption: str) -> str:
        """Publish a carousel of image URLs with the given caption.

        Returns the published media ID.
        """
        n = len(image_urls)
        if n < self.MIN_ITEMS or n > self.MAX_ITEMS:
            raise ValueError(
                f"carousel needs {self.MIN_ITEMS}-{self.MAX_ITEMS} items, got {n}"
            )

        # 1. Create child containers (one per slide).
        log.info("creating %d carousel child containers", n)
        child_ids: list[str] = []
        for idx, url in enumerate(image_urls, start=1):
            res = self._post(
                f"{self.ig_user_id}/media",
                image_url=url,
                is_carousel_item="true",
            )
            cid = res["id"]
            child_ids.append(cid)
            log.info("child %d/%d created: %s", idx, n, cid)

        # 2. Wait for each child to finish processing — IG fetches the URL,
        #    and the host (eg gh-pages) sometimes lags behind.
        for idx, cid in enumerate(child_ids, start=1):
            log.info("waiting for child %d/%d (%s) to finish", idx, n, cid)
            self._wait_finished(cid)

        # 3. Create the carousel container with all children + the caption.
        log.info("creating carousel container with %d children", n)
        carousel = self._post(
            f"{self.ig_user_id}/media",
            media_type="CAROUSEL",
            children=",".join(child_ids),
            caption=caption,
        )
        carousel_id = carousel["id"]
        self._wait_finished(carousel_id)

        # 4. Publish.
        log.info("publishing carousel %s", carousel_id)
        published = self._post(
            f"{self.ig_user_id}/media_publish", creation_id=carousel_id
        )
        media_id = published["id"]
        log.info("published media_id=%s", media_id)
        return media_id
