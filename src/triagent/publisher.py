from __future__ import annotations

import logging
import time

import requests
from tenacity import (
    retry,
    retry_if_exception_type,
    stop_after_delay,
    stop_after_attempt,
    wait_exponential,
    wait_fixed,
)

log = logging.getLogger(__name__)

GRAPH = "https://graph.facebook.com/v20.0"


class ImageNotReady(Exception):
    """Raised when an image URL has not yet returned HTTP 200."""


class InstagramPublisher:
    """Two-step publish via the Instagram Graph API.

    Requires an Instagram Business or Creator account linked to a Facebook Page,
    plus a long-lived user access token with `instagram_content_publish` and
    `instagram_basic` scopes.
    """

    def __init__(self, ig_user_id: str, access_token: str):
        self.ig_user_id = ig_user_id
        self.access_token = access_token

    def _check_image(self, image_url: str, *, head_unsupported: list[bool]) -> None:
        """Check whether the image URL is reachable; raise ImageNotReady if not."""
        # Try HEAD first (cheap), fall back to GET if the server doesn't support HEAD.
        if head_unsupported[0]:
            r = requests.get(image_url, timeout=5)
        else:
            r = requests.head(image_url, timeout=5)
            if r.status_code == 405:
                head_unsupported[0] = True
                log.debug("HEAD not supported, falling back to GET")
                r = requests.get(image_url, timeout=5)

        if r.ok:
            return
        raise ImageNotReady(f"image URL returned {r.status_code}")

    def wait_for_image(self, image_url: str, *, timeout_secs: int = 120) -> None:
        """Poll the image URL until it returns HTTP 200 or the timeout elapses.

        This replaces the raw ``sleep 45`` in the GitHub Actions workflow with
        a proper health check so we only proceed once the image is actually
        servable.
        """
        # Use a mutable container so the inner function can update it.
        head_unsupported: list[bool] = [False]

        retrying = retry(
            retry=retry_if_exception_type(
                (ImageNotReady, requests.ConnectionError, requests.Timeout)
            ),
            stop=stop_after_delay(timeout_secs),
            wait=wait_fixed(min(5, max(1, timeout_secs // 4))),
            reraise=True,
        )

        try:
            retrying(self._check_image)(image_url, head_unsupported=head_unsupported)
        except (ImageNotReady, requests.ConnectionError, requests.Timeout):
            raise TimeoutError(
                f"image URL {image_url} did not become reachable within {timeout_secs}s"
            )
        log.info("image URL is live: %s", image_url)

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

    def publish(self, image_url: str, caption: str) -> str:
        """Create a media container, wait for processing, then publish.

        Returns the published media ID.
        """
        log.info("creating media container")
        create = self._post(
            f"{self.ig_user_id}/media",
            image_url=image_url,
            caption=caption,
        )
        creation_id = create["id"]

        # Poll status up to ~60s — image hosts sometimes take a moment to fetch.
        for attempt in range(20):
            status = self._get(creation_id, fields="status_code")
            code = status.get("status_code")
            if code == "FINISHED":
                break
            if code == "ERROR":
                raise RuntimeError(f"container processing failed: {status}")
            time.sleep(3)
        else:
            raise TimeoutError("media container never reached FINISHED state")

        log.info("publishing container %s", creation_id)
        publish = self._post(
            f"{self.ig_user_id}/media_publish", creation_id=creation_id
        )
        media_id = publish["id"]
        log.info("published media_id=%s", media_id)
        return media_id
