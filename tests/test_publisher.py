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


class TestInsightsFetch:
    """Metric availability varies by media type and account age; an
    unsupported metric fails the whole call, so the request must degrade."""

    def _resp(self, names):
        return {"data": [{"name": n, "values": [{"value": 7}]} for n in names]}

    def test_returns_metrics_on_first_attempt(self, publisher):
        with patch.object(publisher, "_get", return_value=self._resp(["reach"])):
            assert publisher.media_insights("m1")["reach"] == 7

    def test_falls_back_to_core_metrics(self, publisher):
        get = MagicMock(side_effect=[Exception("unsupported"), self._resp(["reach"])])
        with patch.object(publisher, "_get", get):
            assert publisher.media_insights("m1") == {"reach": 7}
        assert get.call_count == 2

    def test_returns_empty_rather_than_raising(self, publisher):
        """Losing a day of numbers must not break anything else."""
        with patch.object(publisher, "_get", side_effect=Exception("nope")):
            assert publisher.media_insights("m1") == {}

    def test_list_media_returns_data_array(self, publisher):
        with patch.object(publisher, "_get", return_value={"data": [{"id": "1"}]}):
            assert publisher.list_media() == [{"id": "1"}]

    def test_list_media_handles_empty_account(self, publisher):
        with patch.object(publisher, "_get", return_value={}):
            assert publisher.list_media() == []


class TestAccountProfile:
    def test_reads_the_plain_profile_fields(self, publisher):
        with patch.object(
            publisher, "_get",
            return_value={"followers_count": 151, "follows_count": 327, "media_count": 109},
        ) as get:
            assert publisher.account_profile()["followers_count"] == 151
        # A profile read, not an Insights call: no audience-size minimum applies.
        get.assert_called_once_with("123", fields="followers_count,follows_count,media_count")

    def test_returns_empty_rather_than_raising(self, publisher):
        """The follower count failing must not be able to fail the insights run."""
        with patch.object(publisher, "_get", side_effect=Exception("nope")):
            assert publisher.account_profile() == {}


class TestAccountMetric:
    """One metric per request, because an unsupported name fails the whole call."""

    SINCE, UNTIL = 1_000, 2_000

    def _plain(self, metric, value):
        return {"data": [{"name": metric, "total_value": {"value": value}}]}

    def _split(self, metric, total, **parts):
        results = [{"dimension_values": [k], "value": v} for k, v in parts.items()]
        return {"data": [{"name": metric, "total_value": {
            "value": total, "breakdowns": [{"dimension_keys": ["follow_type"], "results": results}],
        }}]}

    def _call(self, publisher, metric="reach", **kw):
        return publisher.account_metric(metric, since=self.SINCE, until=self.UNTIL, **kw)

    def test_plain_total(self, publisher):
        with patch.object(publisher, "_get", return_value=self._plain("profile_views", 12)) as get:
            assert self._call(publisher, "profile_views") == {"total": 12, "split": {}}
        path = get.call_args.args[0]
        params = get.call_args.kwargs
        assert path == "123/insights"
        assert params == {
            "metric": "profile_views", "period": "day", "metric_type": "total_value",
            "since": 1_000, "until": 2_000,
        }

    def test_follower_split_is_read_with_upper_cased_keys(self, publisher):
        resp = self._split("reach", 100, FOLLOWER=80, NON_FOLLOWER=20)
        with patch.object(publisher, "_get", return_value=resp) as get:
            out = self._call(publisher, split=True)
        assert out == {"total": 100, "split": {"FOLLOWER": 80, "NON_FOLLOWER": 20}}
        assert get.call_args.kwargs["breakdown"] == "follow_type"

    def test_tries_the_other_breakdown_spelling_when_the_first_is_refused(self, publisher):
        resp = self._split("views", 50, FOLLOWER=40, NON_FOLLOWER=10)
        get = MagicMock(side_effect=[Exception("unsupported breakdown"), resp])
        with patch.object(publisher, "_get", get):
            out = self._call(publisher, "views", split=True)
        assert out["split"] == {"FOLLOWER": 40, "NON_FOLLOWER": 10}
        assert [c.kwargs["breakdown"] for c in get.call_args_list] == ["follow_type", "follower_type"]

    def test_falls_back_to_the_plain_total_when_no_breakdown_is_accepted(self, publisher):
        """A total without the split is still worth recording."""
        get = MagicMock(side_effect=[Exception("a"), Exception("b"), self._plain("reach", 90)])
        with patch.object(publisher, "_get", get):
            out = self._call(publisher, split=True)
        assert out == {"total": 90, "split": {}}
        assert "breakdown" not in get.call_args_list[-1].kwargs

    def test_total_is_summed_from_the_split_when_the_api_omits_it(self, publisher):
        resp = {"data": [{"name": "reach", "total_value": {"breakdowns": [{"results": [
            {"dimension_values": ["FOLLOWER"], "value": 3},
            {"dimension_values": ["NON_FOLLOWER"], "value": 1},
        ]}]}}]}
        with patch.object(publisher, "_get", return_value=resp):
            assert self._call(publisher, split=True)["total"] == 4

    def test_empty_data_is_unavailable_not_zero(self, publisher):
        """Meta answers an uncomputable metric with an empty array. Reading that as 0 would
        record 'nobody saw it' for a figure that simply was not available."""
        with patch.object(publisher, "_get", return_value={"data": []}):
            assert self._call(publisher) is None

    def test_a_row_for_a_different_metric_is_ignored(self, publisher):
        with patch.object(publisher, "_get", return_value=self._plain("views", 5)):
            assert self._call(publisher, "reach") is None

    def test_returns_none_rather_than_raising(self, publisher, caplog):
        with patch.object(publisher, "_get", side_effect=Exception("(#100) unsupported")):
            assert self._call(publisher, split=True) is None
        assert any("unsupported" in r.message for r in caplog.records)

    def test_zero_is_a_real_value(self, publisher):
        with patch.object(publisher, "_get", return_value=self._plain("accounts_engaged", 0)):
            assert self._call(publisher, "accounts_engaged") == {"total": 0, "split": {}}
