"""Tests for triagent.imagery — background resolution.

Every provider is optional and every failure must fall through, because a
missing picture should degrade the post, not fail the run.
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

from triagent.imagery import resolve_background


def _ok_gen(b64="aGk="):
    r = MagicMock()
    r.ok = True
    r.json.return_value = {"data": [{"b64_json": b64}]}
    return r


def _ok_stock():
    r = MagicMock()
    r.ok = True
    r.json.return_value = {
        "photos": [{"src": {"large2x": "https://img/x.jpg"}, "photographer": "A"}]
    }
    return r


class TestProviderOrder:
    def test_generation_preferred_when_key_present(self, tmp_path):
        out = tmp_path / "bg.jpg"
        with patch("triagent.imagery.requests.post", return_value=_ok_gen()) as post:
            got = resolve_background("swimmer at dawn", out, gen_key="k", stock_key="s")
        assert got == out
        assert post.called

    def test_falls_back_to_stock_when_generation_fails(self, tmp_path):
        out = tmp_path / "bg.jpg"
        bad = MagicMock(ok=False, text="nope", status_code=500)
        img = MagicMock(content=b"jpeg")
        img.raise_for_status.return_value = None
        with (
            patch("triagent.imagery.requests.post", return_value=bad),
            patch("triagent.imagery.requests.get", side_effect=[_ok_stock(), img]),
        ):
            got = resolve_background("x", out, gen_key="k", stock_key="s")
        assert got == out
        assert out.read_bytes() == b"jpeg"

    def test_falls_back_to_local_when_all_remote_fail(self, tmp_path):
        out = tmp_path / "bg.jpg"
        local = tmp_path / "local.jpg"
        local.write_bytes(b"local")
        bad = MagicMock(ok=False, text="no", status_code=500)
        with (
            patch("triagent.imagery.requests.post", return_value=bad),
            patch("triagent.imagery.requests.get", return_value=bad),
        ):
            got = resolve_background("x", out, gen_key="k", stock_key="s", fallback=local)
        assert got == local

    def test_no_keys_returns_fallback_without_network(self, tmp_path):
        local = tmp_path / "l.jpg"
        local.write_bytes(b"l")
        with (
            patch("triagent.imagery.requests.post") as post,
            patch("triagent.imagery.requests.get") as get,
        ):
            got = resolve_background("x", tmp_path / "bg.jpg", fallback=local)
        assert got == local
        assert not post.called and not get.called

    def test_empty_query_short_circuits(self, tmp_path):
        with patch("triagent.imagery.requests.post") as post:
            got = resolve_background("   ", tmp_path / "bg.jpg", gen_key="k")
        assert got is None
        assert not post.called

    def test_returns_none_when_nothing_available(self, tmp_path):
        bad = MagicMock(ok=False, text="no", status_code=500)
        with patch("triagent.imagery.requests.post", return_value=bad):
            assert resolve_background("x", tmp_path / "bg.jpg", gen_key="k") is None


class TestResilience:
    def test_network_exception_does_not_propagate(self, tmp_path):
        with patch("triagent.imagery.requests.post", side_effect=OSError("down")):
            assert resolve_background("x", tmp_path / "bg.jpg", gen_key="k") is None

    def test_stock_with_no_results_falls_through(self, tmp_path):
        empty = MagicMock(ok=True)
        empty.json.return_value = {"photos": []}
        local = tmp_path / "l.jpg"
        local.write_bytes(b"l")
        with patch("triagent.imagery.requests.get", return_value=empty):
            got = resolve_background("x", tmp_path / "b.jpg", stock_key="s", fallback=local)
        assert got == local


class TestPromptShaping:
    def test_style_suffix_appended_to_prompt(self, tmp_path):
        with patch("triagent.imagery.requests.post", return_value=_ok_gen()) as post:
            resolve_background("swimmer at dawn", tmp_path / "b.jpg", gen_key="k")
        prompt = post.call_args[1]["json"]["prompt"]
        assert prompt.startswith("swimmer at dawn")
        assert "no text" in prompt

    def test_portrait_size_requested(self, tmp_path):
        with patch("triagent.imagery.requests.post", return_value=_ok_gen()) as post:
            resolve_background("x", tmp_path / "b.jpg", gen_key="k")
        w, h = post.call_args[1]["json"]["size"].split("x")
        assert int(h) > int(w)

    def test_stock_requests_portrait_orientation(self, tmp_path):
        img = MagicMock(content=b"j")
        img.raise_for_status.return_value = None
        with patch("triagent.imagery.requests.get", side_effect=[_ok_stock(), img]) as get:
            resolve_background("x", tmp_path / "b.jpg", stock_key="s")
        assert get.call_args_list[0][1]["params"]["orientation"] == "portrait"
