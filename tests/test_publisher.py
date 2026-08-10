"""Tests for triagent.publisher — InstagramPublisher class."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest
import requests
import tenacity

from triagent.publisher import InstagramPublisher


@pytest.fixture()
def publisher():
    return InstagramPublisher(ig_user_id="123", access_token="tok_123")


class TestWaitForImage:
    """Tests for InstagramPublisher.wait_for_image()."""

    def test_returns_on_first_success(self, publisher):
        """wait_for_image() returns immediately on HTTP 200."""
        mock_response = MagicMock()
        mock_response.ok = True

        with patch("triagent.publisher.requests.head", return_value=mock_response):
            publisher.wait_for_image("https://example.com/image.png")

    def test_raises_on_timeout(self, publisher):
        """wait_for_image() raises TimeoutError if URL never becomes reachable."""
        mock_response = MagicMock()
        mock_response.ok = False
        mock_response.status_code = 503

        with (
            patch("triagent.publisher.requests.head", return_value=mock_response),
            patch("triagent.publisher.requests.get", return_value=mock_response),
        ):
            with pytest.raises(TimeoutError, match="did not become reachable"):
                publisher.wait_for_image("https://example.com/image.png", timeout_secs=1)

    def test_falls_back_to_get_on_head_405(self, publisher):
        """wait_for_image() falls back to GET when HEAD returns 405, then remembers."""
        head_response = MagicMock()
        head_response.ok = False
        head_response.status_code = 405

        get_response = MagicMock()
        get_response.ok = True

        head_call_count = [0]

        def head_side_effect(*args, **kwargs):
            head_call_count[0] += 1
            return head_response

        with (
            patch("triagent.publisher.requests.head", side_effect=head_side_effect),
            patch("triagent.publisher.requests.get", return_value=get_response),
        ):
            publisher.wait_for_image("https://example.com/image.png", timeout_secs=10)

        # HEAD was called once, then 405 set the flag, then GET succeeded
        assert head_call_count[0] == 1

    def test_retries_on_connection_error(self, publisher):
        """wait_for_image() retries when HEAD raises ConnectionError."""
        from triagent.publisher import ImageNotReady

        call_count = [0]

        def failing_then_succeeding(*args, **kwargs):
            call_count[0] += 1
            if call_count[0] < 3:
                raise ImageNotReady("not ready yet")
            resp = MagicMock()
            resp.ok = True
            return resp

        with patch("triagent.publisher.requests.head", side_effect=failing_then_succeeding):
            publisher.wait_for_image("https://example.com/image.png", timeout_secs=60)

        assert call_count[0] == 3

    def test_uses_custom_timeout(self, publisher):
        """wait_for_image() respects the timeout_secs parameter."""
        mock_response = MagicMock()
        mock_response.ok = False
        mock_response.status_code = 503

        with (
            patch("triagent.publisher.requests.head", return_value=mock_response),
            patch("triagent.publisher.requests.get", return_value=mock_response),
        ):
            with pytest.raises(TimeoutError):
                publisher.wait_for_image("https://example.com/image.png", timeout_secs=1)

    def test_logs_on_success(self, publisher):
        """wait_for_image() logs on success."""
        mock_response = MagicMock()
        mock_response.ok = True

        with (
            patch("triagent.publisher.requests.head", return_value=mock_response),
            patch("triagent.publisher.log") as mock_log,
        ):
            publisher.wait_for_image("https://example.com/image.png")

            mock_log.info.assert_called()


class TestPublish:
    """Tests for InstagramPublisher.publish()."""

    def test_publish_creates_media_and_publishes(self, publisher):
        """publish() creates a media container, waits for FINISHED, then publishes."""
        mock_post = MagicMock(side_effect=[
            {"id": "creation_123"},
            {"id": "published_456"},
        ])

        mock_get = MagicMock(return_value={"status_code": "FINISHED"})

        with (
            patch.object(publisher, "_post", side_effect=mock_post) as mock_post_method,
            patch.object(publisher, "_get", side_effect=mock_get) as mock_get_method,
        ):
            media_id = publisher.publish(
                image_url="https://example.com/image.png",
                caption="Test caption",
            )

        assert media_id == "published_456"
        mock_post_method.assert_any_call("123/media", image_url="https://example.com/image.png", caption="Test caption")
        mock_post_method.assert_any_call("123/media_publish", creation_id="creation_123")

    def test_publish_raises_on_error_status(self, publisher):
        """publish() raises RuntimeError when status_code is ERROR."""
        mock_post = MagicMock(return_value={"id": "creation_123"})
        mock_get = MagicMock(return_value={"status_code": "ERROR", "error_msg": "Invalid image"})

        with (
            patch.object(publisher, "_post", side_effect=mock_post),
            patch.object(publisher, "_get", side_effect=mock_get),
        ):
            with pytest.raises(RuntimeError, match="container processing failed"):
                publisher.publish(
                    image_url="https://example.com/image.png",
                    caption="Test caption",
                )

    def test_publish_raises_on_timeout_waiting_for_finish(self, publisher):
        """publish() raises TimeoutError if status never reaches FINISHED."""
        mock_post = MagicMock(return_value={"id": "creation_123"})
        mock_get = MagicMock(return_value={"status_code": "PROCESSING"})

        with (
            patch.object(publisher, "_post", side_effect=mock_post),
            patch.object(publisher, "_get", side_effect=mock_get),
        ):
            with pytest.raises(TimeoutError, match="never reached FINISHED"):
                publisher.publish(
                    image_url="https://example.com/image.png",
                    caption="Test caption",
                )

    def test_publish_passes_correct_params(self, publisher):
        """publish() passes image_url and caption to the media endpoint."""
        mock_post = MagicMock(return_value={"id": "creation_123"})
        mock_get = MagicMock(return_value={"status_code": "FINISHED"})
        mock_post_publish = MagicMock(return_value={"id": "published_456"})

        with (
            patch.object(publisher, "_post", side_effect=[mock_post.return_value, mock_post_publish.return_value]) as mock_post_method,
            patch.object(publisher, "_get", return_value={"status_code": "FINISHED"}),
        ):
            publisher.publish(
                image_url="https://example.com/image.png",
                caption="My caption with emojis 🏊‍♂️🚴‍♀️🏃‍♂️",
            )

        first_call = mock_post_method.call_args_list[0]
        assert first_call[1]["image_url"] == "https://example.com/image.png"
        assert first_call[1]["caption"] == "My caption with emojis 🏊‍♂️🚴‍♀️🏃‍♂️"


class TestPostAndGet:
    """Tests for the internal _post and _get helpers."""

    def test_post_includes_access_token(self, publisher):
        """_post includes the access token in the request."""
        mock_response = MagicMock()
        mock_response.ok = True
        mock_response.json.return_value = {"result": "ok"}

        with patch("triagent.publisher.requests.post", return_value=mock_response) as mock_post:
            publisher._post("123/media", image_url="https://example.com/img.png")

        call_kwargs = mock_post.call_args[1]
        assert "access_token" in call_kwargs["data"]
        assert call_kwargs["data"]["access_token"] == "tok_123"

    def test_get_includes_access_token(self, publisher):
        """_get includes the access token in the request."""
        mock_response = MagicMock()
        mock_response.ok = True
        mock_response.json.return_value = {"result": "ok"}

        with patch("triagent.publisher.requests.get", return_value=mock_response) as mock_get:
            publisher._get("123/fields", fields="status_code")

        call_kwargs = mock_get.call_args[1]
        assert "access_token" in call_kwargs["params"]
        assert call_kwargs["params"]["access_token"] == "tok_123"

    def test_post_retries_on_failure(self, publisher):
        """_post retries up to 3 times on ConnectionError."""
        with patch("triagent.publisher.requests.post") as mock_post:
            mock_post.side_effect = requests.ConnectionError("Fail")

            with pytest.raises(tenacity.RetryError):
                publisher._post("123/test")

            assert mock_post.call_count == 3  # 3 attempts total

    def test_get_retries_on_failure(self, publisher):
        """_get retries up to 3 times on ConnectionError."""
        with patch("triagent.publisher.requests.get") as mock_get:
            mock_get.side_effect = requests.ConnectionError("Fail")

            with pytest.raises(tenacity.RetryError):
                publisher._get("123/test")

            assert mock_get.call_count == 3  # 3 attempts total

    def test_post_raises_on_http_error(self, publisher):
        """_post raises when the API returns an error response (after retries)."""
        mock_response = MagicMock()
        mock_response.ok = False
        mock_response.text = '{"error": "Invalid token"}'
        mock_response.raise_for_status.side_effect = requests.HTTPError(response=mock_response)

        with patch("triagent.publisher.requests.post", return_value=mock_response):
            with pytest.raises(tenacity.RetryError):
                publisher._post("123/test")

    def test_get_raises_on_http_error(self, publisher):
        """_get raises when the API returns an error response (after retries)."""
        mock_response = MagicMock()
        mock_response.ok = False
        mock_response.text = '{"error": "Not found"}'
        mock_response.raise_for_status.side_effect = requests.HTTPError(response=mock_response)

        with patch("triagent.publisher.requests.get", return_value=mock_response):
            with pytest.raises(tenacity.RetryError):
                publisher._get("123/test")


class TestApiMode:
    """Tests for the instagram_login / facebook_login host selection."""

    def test_defaults_to_instagram_login(self, monkeypatch):
        monkeypatch.delenv("IG_API_MODE", raising=False)
        p = InstagramPublisher(ig_user_id="1", access_token="t")
        assert p.api_mode == "instagram_login"
        assert p.base.startswith("https://graph.instagram.com/")

    def test_facebook_login_uses_graph_facebook_host(self):
        p = InstagramPublisher(
            ig_user_id="1", access_token="t", api_mode="facebook_login"
        )
        assert p.base.startswith("https://graph.facebook.com/")

    def test_mode_read_from_env(self, monkeypatch):
        monkeypatch.setenv("IG_API_MODE", "facebook_login")
        p = InstagramPublisher(ig_user_id="1", access_token="t")
        assert p.api_mode == "facebook_login"

    def test_explicit_arg_beats_env(self, monkeypatch):
        monkeypatch.setenv("IG_API_MODE", "facebook_login")
        p = InstagramPublisher(
            ig_user_id="1", access_token="t", api_mode="instagram_login"
        )
        assert p.api_mode == "instagram_login"

    def test_unknown_mode_raises(self):
        with pytest.raises(ValueError, match="unknown IG_API_MODE"):
            InstagramPublisher(ig_user_id="1", access_token="t", api_mode="nope")

    def test_graph_version_override(self, monkeypatch):
        monkeypatch.setenv("GRAPH_API_VERSION", "v99.0")
        p = InstagramPublisher(ig_user_id="1", access_token="t")
        assert p.base.endswith("/v99.0")

    def test_post_targets_selected_host(self):
        p = InstagramPublisher(
            ig_user_id="1", access_token="t", api_mode="instagram_login"
        )
        mock_response = MagicMock()
        mock_response.ok = True
        mock_response.json.return_value = {"id": "x"}
        with patch(
            "triagent.publisher.requests.post", return_value=mock_response
        ) as mock_post:
            p._post("1/media", image_url="https://e.com/i.png")
        assert mock_post.call_args[0][0].startswith("https://graph.instagram.com/")


class TestWhoami:
    """Tests for the whoami() setup helper."""

    def test_instagram_login_requests_user_id_field(self):
        p = InstagramPublisher(
            ig_user_id="me", access_token="t", api_mode="instagram_login"
        )
        mock_response = MagicMock()
        mock_response.ok = True
        mock_response.json.return_value = {"user_id": "178", "username": "tri"}
        with patch(
            "triagent.publisher.requests.get", return_value=mock_response
        ) as mock_get:
            out = p.whoami()
        assert out["user_id"] == "178"
        assert mock_get.call_args[1]["params"]["fields"] == "user_id,username"

    def test_facebook_login_requests_id_field(self):
        p = InstagramPublisher(
            ig_user_id="me", access_token="t", api_mode="facebook_login"
        )
        mock_response = MagicMock()
        mock_response.ok = True
        mock_response.json.return_value = {"id": "178", "username": "tri"}
        with patch("triagent.publisher.requests.get", return_value=mock_response) as mg:
            p.whoami()
        assert mg.call_args[1]["params"]["fields"] == "id,username"


class TestImageTimeoutDiagnostics:
    """The timeout must say *why*, since 404 and 5xx need opposite fixes."""

    def _resp(self, status):
        r = MagicMock()
        r.ok = False
        r.status_code = status
        return r

    def test_404_reports_case_sensitivity_hint(self, publisher):
        with patch("triagent.publisher.requests.head", return_value=self._resp(404)):
            with pytest.raises(TimeoutError, match="case-sensitive"):
                publisher.wait_for_image("https://x.example/daily.png", timeout_secs=1)

    def test_503_reports_last_status(self, publisher):
        with patch("triagent.publisher.requests.head", return_value=self._resp(503)):
            with pytest.raises(TimeoutError, match="HTTP 503"):
                publisher.wait_for_image("https://x.example/daily.png", timeout_secs=1)

    def test_connection_error_reports_no_answer(self, publisher):
        with patch(
            "triagent.publisher.requests.head",
            side_effect=requests.ConnectionError("dns"),
        ):
            with pytest.raises(TimeoutError, match="never answered"):
                publisher.wait_for_image("https://x.example/daily.png", timeout_secs=1)

    def test_success_does_not_raise(self, publisher):
        ok = MagicMock()
        ok.ok = True
        with patch("triagent.publisher.requests.head", return_value=ok):
            publisher.wait_for_image("https://x.example/daily.png", timeout_secs=5)


class TestRefreshLongLivedToken:
    """Tests for the 60-day token refresh."""

    def test_returns_payload_on_success(self, publisher):
        resp = MagicMock()
        resp.ok = True
        resp.json.return_value = {
            "access_token": "IGAA_new",
            "token_type": "bearer",
            "expires_in": 5184000,
        }
        with patch("triagent.publisher.requests.get", return_value=resp):
            out = publisher.refresh_long_lived_token()
        assert out["access_token"] == "IGAA_new"
        assert out["expires_in"] == 5184000

    def test_hits_unversioned_instagram_host(self, publisher):
        """The refresh endpoint is documented without a version segment."""
        resp = MagicMock()
        resp.ok = True
        resp.json.return_value = {"access_token": "x", "expires_in": 1}
        with patch("triagent.publisher.requests.get", return_value=resp) as mock_get:
            publisher.refresh_long_lived_token()
        url = mock_get.call_args[0][0]
        assert url == "https://graph.instagram.com/refresh_access_token"
        assert "/v" not in url.replace("https://", "")

    def test_sends_correct_grant_type(self, publisher):
        resp = MagicMock()
        resp.ok = True
        resp.json.return_value = {"access_token": "x", "expires_in": 1}
        with patch("triagent.publisher.requests.get", return_value=resp) as mock_get:
            publisher.refresh_long_lived_token()
        params = mock_get.call_args[1]["params"]
        assert params["grant_type"] == "ig_refresh_token"
        assert params["access_token"] == "tok_123"

    def test_rejects_facebook_login_mode(self):
        """Facebook tokens refresh differently and need the app secret."""
        p = InstagramPublisher(
            ig_user_id="1", access_token="t", api_mode="facebook_login"
        )
        with pytest.raises(RuntimeError, match="instagram_login"):
            p.refresh_long_lived_token()

    def test_raises_when_response_lacks_token(self, publisher):
        resp = MagicMock()
        resp.ok = True
        resp.json.return_value = {"error": {"message": "token too new"}}
        with patch("triagent.publisher.requests.get", return_value=resp):
            with pytest.raises(RuntimeError, match="no access_token"):
                publisher.refresh_long_lived_token()

    def test_raises_on_http_error(self, publisher):
        resp = MagicMock()
        resp.ok = False
        resp.text = "expired"
        resp.raise_for_status.side_effect = requests.HTTPError("400")
        with patch("triagent.publisher.requests.get", return_value=resp):
            with pytest.raises(requests.HTTPError):
                publisher.refresh_long_lived_token()


class TestPublishReel:
    """Reels are the only Instagram format that can carry audio."""

    def test_sends_reels_media_type(self, publisher):
        post = MagicMock(side_effect=[{"id": "c1"}, {"id": "m1"}])
        get = MagicMock(return_value={"status_code": "FINISHED"})
        with (
            patch.object(publisher, "_post", post),
            patch.object(publisher, "_get", get),
        ):
            out = publisher.publish_reel("https://x/v.mp4", "cap")
        assert out == "m1"
        assert post.call_args_list[0][1]["media_type"] == "REELS"
        assert post.call_args_list[0][1]["video_url"] == "https://x/v.mp4"

    def test_passes_cover_url_when_given(self, publisher):
        post = MagicMock(side_effect=[{"id": "c1"}, {"id": "m1"}])
        get = MagicMock(return_value={"status_code": "FINISHED"})
        with (
            patch.object(publisher, "_post", post),
            patch.object(publisher, "_get", get),
        ):
            publisher.publish_reel("https://x/v.mp4", "cap", cover_url="https://x/c.png")
        assert post.call_args_list[0][1]["cover_url"] == "https://x/c.png"

    def test_omits_cover_url_when_absent(self, publisher):
        post = MagicMock(side_effect=[{"id": "c1"}, {"id": "m1"}])
        get = MagicMock(return_value={"status_code": "FINISHED"})
        with (
            patch.object(publisher, "_post", post),
            patch.object(publisher, "_get", get),
        ):
            publisher.publish_reel("https://x/v.mp4", "cap")
        assert "cover_url" not in post.call_args_list[0][1]

    def test_raises_on_container_error(self, publisher):
        post = MagicMock(return_value={"id": "c1"})
        get = MagicMock(return_value={"status_code": "ERROR", "status": "bad codec"})
        with (
            patch.object(publisher, "_post", post),
            patch.object(publisher, "_get", get),
        ):
            with pytest.raises(RuntimeError, match="bad codec"):
                publisher.publish_reel("https://x/v.mp4", "cap")


class TestPublishCarousel:
    def _ok(self, publisher, ids):
        post = MagicMock(side_effect=[{"id": i} for i in ids])
        get = MagicMock(return_value={"status_code": "FINISHED"})
        return post, get

    def test_creates_child_then_parent_then_publishes(self, publisher):
        post, get = self._ok(publisher, ["c1", "c2", "c3", "parent", "media"])
        with patch.object(publisher, "_post", post), patch.object(publisher, "_get", get):
            out = publisher.publish_carousel(["u1", "u2", "u3"], "cap")
        assert out == "media"
        # three children, each flagged, none carrying the caption
        for i in range(3):
            assert post.call_args_list[i][1]["is_carousel_item"] == "true"
            assert "caption" not in post.call_args_list[i][1]
        parent = post.call_args_list[3][1]
        assert parent["media_type"] == "CAROUSEL"
        assert parent["children"] == "c1,c2,c3"
        assert parent["caption"] == "cap"

    def test_rejects_single_image(self, publisher):
        with pytest.raises(ValueError, match="2-10"):
            publisher.publish_carousel(["only"], "cap")

    def test_rejects_more_than_ten(self, publisher):
        with pytest.raises(ValueError, match="2-10"):
            publisher.publish_carousel([f"u{i}" for i in range(11)], "cap")

    def test_propagates_child_processing_error(self, publisher):
        post = MagicMock(return_value={"id": "c1"})
        get = MagicMock(return_value={"status_code": "ERROR", "status": "bad image"})
        with patch.object(publisher, "_post", post), patch.object(publisher, "_get", get):
            with pytest.raises(RuntimeError, match="bad image"):
                publisher.publish_carousel(["u1", "u2"], "cap")
