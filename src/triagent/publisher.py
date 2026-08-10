from __future__ import annotations

import logging
import os
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

# Meta supports each Graph API version for ~2 years. v20.0 (May 2024) is past
# end-of-life, so requests against it fail or get silently rerouted. Override
# with GRAPH_API_VERSION when Meta ships a newer version.
DEFAULT_GRAPH_VERSION = "v25.0"

# Two ways to reach a publishable Instagram account:
#
#   instagram_login — Instagram API with Instagram Login. Authenticates directly
#       against Instagram; needs NO Facebook Page. Requires only a Business or
#       Creator IG account. This is the simpler setup and the default.
#
#   facebook_login  — the classic Instagram Graph API. Reaches the IG account
#       through a linked Facebook Page, which must exist and be linked, and
#       whose access must be granted to the app during Business Login.
#
# The publish endpoints (/media, /media_publish, status polling) are identical
# across both; only the host differs.
API_HOSTS = {
    "instagram_login": "https://graph.instagram.com",
    "facebook_login": "https://graph.facebook.com",
}
DEFAULT_API_MODE = "instagram_login"

# Kept for backwards compatibility with anything importing the old constant.
GRAPH_VERSION = os.environ.get("GRAPH_API_VERSION", DEFAULT_GRAPH_VERSION)
GRAPH = f"{API_HOSTS['facebook_login']}/{GRAPH_VERSION}"


class ImageNotReady(Exception):
    """Raised when an image URL has not yet returned HTTP 200."""


class InstagramPublisher:
    """Two-step publish via the Instagram content publishing API.

    Works against either auth path (see API_HOSTS above):

    * ``instagram_login`` (default) — Instagram Business/Creator account, a
      token from Instagram OAuth with the ``instagram_business_basic`` and
      ``instagram_business_content_publish`` scopes. No Facebook Page needed.
    * ``facebook_login`` — Instagram Business/Creator account linked to a
      Facebook Page, with a token carrying ``instagram_basic`` and
      ``instagram_content_publish``.
    """

    def __init__(
        self,
        ig_user_id: str,
        access_token: str,
        *,
        api_mode: str | None = None,
        graph_version: str | None = None,
    ):
        self.ig_user_id = ig_user_id
        self.access_token = access_token

        # Resolve from env at construction rather than import, so .env loading
        # order doesn't silently pin the wrong host.
        mode = api_mode or os.environ.get("IG_API_MODE") or DEFAULT_API_MODE
        if mode not in API_HOSTS:
            raise ValueError(
                f"unknown IG_API_MODE {mode!r}; expected one of {sorted(API_HOSTS)}"
            )
        self.api_mode = mode
        self.graph_version = (
            graph_version
            or os.environ.get("GRAPH_API_VERSION")
            or DEFAULT_GRAPH_VERSION
        )
        self.base = f"{API_HOSTS[mode]}/{self.graph_version}"
        log.info("publisher using %s (%s)", self.base, mode)

    def whoami(self) -> dict:
        """Return the authenticated Instagram account's id and username.

        Handy during setup: confirms the token works and surfaces the account
        ID to put in IG_USER_ID, without hunting through Meta's UI.
        """
        fields = (
            "user_id,username"
            if self.api_mode == "instagram_login"
            else "id,username"
        )
        return self._get("me", fields=fields)

    def refresh_long_lived_token(self) -> dict:
        """Exchange the current long-lived token for one with a fresh 60 days.

        Instagram Login tokens expire 60 days after issue. Refreshing needs no
        app secret and no re-authorization — just an unauthenticated GET with
        the current token — which is what makes it automatable.

        Meta requires the token to be long-lived, still valid, and at least 24
        hours old. Refreshing an already-expired token is not possible; that
        needs a fresh trip through the console.

        Returns the API payload: access_token, token_type, expires_in.
        """
        if self.api_mode != "instagram_login":
            raise RuntimeError(
                "refresh_long_lived_token only applies to IG_API_MODE="
                "'instagram_login'. Facebook-login tokens use a different "
                "endpoint and require the app secret."
            )
        # Documented unversioned, so bypass the versioned base used elsewhere.
        r = requests.get(
            f"{API_HOSTS['instagram_login']}/refresh_access_token",
            params={
                "grant_type": "ig_refresh_token",
                "access_token": self.access_token,
            },
            timeout=30,
        )
        if not r.ok:
            log.error("token refresh failed: %s", r.text)
            r.raise_for_status()
        data = r.json()
        if "access_token" not in data:
            raise RuntimeError(f"refresh returned no access_token: {data}")
        return data

    # Metric availability varies by media type, by account, and by when the
    # media was created — Meta retired `impressions` and `plays` for newer
    # posts. Asking for an unsupported metric fails the whole call, so the
    # request degrades: preferred set first, then a core set that every media
    # type supports.
    CORE_METRICS = ("reach", "likes", "comments", "saved", "shares")
    EXTRA_METRICS = ("views", "total_interactions", "profile_visits", "follows")

    def list_media(self, limit: int = 25) -> list[dict]:
        """Return recently published media, newest first."""
        data = self._get(
            f"{self.ig_user_id}/media",
            fields="id,media_type,permalink,timestamp,caption",
            limit=limit,
        )
        return data.get("data", [])

    def media_insights(self, media_id: str) -> dict:
        """Return metrics for one post, or {} if none are available.

        Never raises. Insight collection is observability, and losing a day of
        numbers must not be able to take down anything else.
        """
        for metrics in (
            self.CORE_METRICS + self.EXTRA_METRICS,
            self.CORE_METRICS,
        ):
            try:
                data = self._get(f"{media_id}/insights", metric=",".join(metrics))
            except Exception as exc:
                log.debug("insights for %s failed with %d metrics: %s",
                          media_id, len(metrics), exc)
                continue
            out: dict = {}
            for row in data.get("data", []):
                values = row.get("values") or [{}]
                out[row["name"]] = values[0].get("value")
            if out:
                return out
        log.warning("no insights available for %s (too new, or unsupported)", media_id)
        return {}

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
        # Record the status so the timeout can say *why* it never appeared.
        # A steady 404 means the URL is wrong; a 5xx or connection error means
        # the host is still warming up. Those need opposite fixes.
        self._last_image_status = r.status_code
        log.warning("image URL not ready yet: HTTP %s for %s", r.status_code, image_url)
        raise ImageNotReady(f"image URL returned {r.status_code}")

    def wait_for_image(self, image_url: str, *, timeout_secs: int = 300) -> None:
        """Poll the image URL until it returns HTTP 200 or the timeout elapses.

        This replaces the raw ``sleep 45`` in the GitHub Actions workflow with
        a proper health check so we only proceed once the image is actually
        servable. The default allows for GitHub Pages CDN propagation, which
        can exceed two minutes when a path is published for the first time.
        """
        # Use a mutable container so the inner function can update it.
        head_unsupported: list[bool] = [False]
        self._last_image_status: int | None = None

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
        except (ImageNotReady, requests.ConnectionError, requests.Timeout) as exc:
            status = getattr(self, "_last_image_status", None)
            if status == 404:
                hint = (
                    "Consistent 404 — PUBLIC_IMAGE_BASE_URL almost certainly points "
                    "somewhere the file isn't. GitHub Pages paths are case-sensitive: "
                    "a repo named 'TriAgent' is served at /TriAgent/, not /triagent/. "
                    "Open the URL in a browser to confirm."
                )
            elif status is not None:
                hint = f"last response was HTTP {status}"
            else:
                hint = f"host never answered ({type(exc).__name__}) — check the domain"
            raise TimeoutError(
                f"image URL {image_url} did not become reachable within "
                f"{timeout_secs}s. {hint}"
            )
        log.info("image URL is live: %s", image_url)

    @retry(stop=stop_after_attempt(3), wait=wait_exponential(min=2, max=20))
    def _post(self, path: str, **params) -> dict:
        params["access_token"] = self.access_token
        r = requests.post(f"{self.base}/{path}", data=params, timeout=30)
        if not r.ok:
            log.error("graph %s failed: %s", path, r.text)
            r.raise_for_status()
        return r.json()

    @retry(stop=stop_after_attempt(3), wait=wait_exponential(min=2, max=20))
    def _get(self, path: str, **params) -> dict:
        params["access_token"] = self.access_token
        r = requests.get(f"{self.base}/{path}", params=params, timeout=30)
        if not r.ok:
            log.error("graph GET %s failed: %s", path, r.text)
            r.raise_for_status()
        return r.json()

    def _await_container(self, creation_id: str, *, attempts: int, delay: int) -> None:
        """Block until the container finishes processing, or fail loudly."""
        for _ in range(attempts):
            status = self._get(creation_id, fields="status_code,status")
            code = status.get("status_code")
            if code == "FINISHED":
                return
            if code == "ERROR":
                # `status` carries the human-readable reason; status_code alone
                # just says ERROR, which isn't enough to act on.
                raise RuntimeError(f"container processing failed: {status}")
            time.sleep(delay)
        raise TimeoutError(
            f"container {creation_id} never reached FINISHED after "
            f"{attempts * delay}s"
        )

    def publish(self, image_url: str, caption: str) -> str:
        """Publish a single image post. Returns the published media ID."""
        log.info("creating image container")
        create = self._post(
            f"{self.ig_user_id}/media",
            image_url=image_url,
            caption=caption,
        )
        creation_id = create["id"]
        self._await_container(creation_id, attempts=20, delay=3)

        log.info("publishing container %s", creation_id)
        publish = self._post(
            f"{self.ig_user_id}/media_publish", creation_id=creation_id
        )
        media_id = publish["id"]
        log.info("published media_id=%s", media_id)
        return media_id

    def publish_carousel(self, image_urls: list[str], caption: str) -> str:
        """Publish a multi-image carousel. Returns the published media ID.

        Three API stages rather than two: each image becomes a child container
        flagged is_carousel_item, then a parent container collects the children,
        then the parent is published. Instagram accepts 2-10 children.

        The caption belongs on the parent only — children carry none.
        """
        if not 2 <= len(image_urls) <= 10:
            raise ValueError(
                f"carousel needs 2-10 images, got {len(image_urls)}"
            )

        children: list[str] = []
        for i, url in enumerate(image_urls, start=1):
            log.info("creating carousel child %d/%d", i, len(image_urls))
            child = self._post(
                f"{self.ig_user_id}/media",
                image_url=url,
                is_carousel_item="true",
            )
            self._await_container(child["id"], attempts=20, delay=3)
            children.append(child["id"])

        log.info("creating carousel parent with %d children", len(children))
        parent = self._post(
            f"{self.ig_user_id}/media",
            media_type="CAROUSEL",
            children=",".join(children),
            caption=caption,
        )
        self._await_container(parent["id"], attempts=30, delay=4)

        published = self._post(
            f"{self.ig_user_id}/media_publish", creation_id=parent["id"]
        )
        media_id = published["id"]
        log.info("published carousel media_id=%s", media_id)
        return media_id

    def publish_reel(
        self,
        video_url: str,
        caption: str,
        *,
        cover_url: str | None = None,
        share_to_feed: bool = True,
    ) -> str:
        """Publish a Reel. Returns the published media ID.

        Video transcoding takes substantially longer than an image fetch — Meta
        downloads the file, transcodes it, and only then reports FINISHED — so
        this polls far longer than publish() does. Giving up early here would
        abandon a container that was going to succeed.
        """
        log.info("creating reel container")
        params = {
            "media_type": "REELS",
            "video_url": video_url,
            "caption": caption,
            "share_to_feed": "true" if share_to_feed else "false",
        }
        if cover_url:
            params["cover_url"] = cover_url

        create = self._post(f"{self.ig_user_id}/media", **params)
        creation_id = create["id"]
        # Up to ~5 minutes: Meta must fetch and transcode the file.
        self._await_container(creation_id, attempts=60, delay=5)

        log.info("publishing reel container %s", creation_id)
        publish = self._post(
            f"{self.ig_user_id}/media_publish", creation_id=creation_id
        )
        media_id = publish["id"]
        log.info("published reel media_id=%s", media_id)
        return media_id
