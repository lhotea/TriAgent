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
            with pytest.raises(TimeoutError, match="media container never reached"):
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
