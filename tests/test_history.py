"""Tests for triagent.history — never post the same story twice."""

from __future__ import annotations

import json

from triagent.history import commit_pending, filter_unused, load_used, write_pending


class _Item:
    def __init__(self, url, title="t", source="s"):
        self.url, self.title, self.source = url, title, source


class TestLoadUsed:
    def test_missing_file_is_empty(self, tmp_path):
        assert load_used(tmp_path / "none.json") == {}

    def test_corrupt_file_is_empty_not_fatal(self, tmp_path):
        """A broken ledger risks a repeat; raising means no post at all."""
        p = tmp_path / "posted.json"
        p.write_text("{not json")
        assert load_used(p) == {}

    def test_non_object_json_is_empty(self, tmp_path):
        p = tmp_path / "posted.json"
        p.write_text('["a", "b"]')
        assert load_used(p) == {}

    def test_reads_existing(self, tmp_path):
        p = tmp_path / "posted.json"
        p.write_text(json.dumps({"https://a": {"first_used": "2026-08-01"}}))
        assert "https://a" in load_used(p)


class TestFilterUnused:
    def test_removes_already_posted(self):
        items = [_Item("https://a"), _Item("https://b")]
        out = filter_unused(items, {"https://a": {}})
        assert [i.url for i in out] == ["https://b"]

    def test_empty_history_keeps_everything(self):
        items = [_Item("https://a")]
        assert filter_unused(items, {}) == items

    def test_all_posted_yields_empty(self):
        items = [_Item("https://a")]
        assert filter_unused(items, {"https://a": {}}) == []


class TestPendingCommit:
    def test_pending_written_but_history_untouched(self, tmp_path):
        """Build records candidates; only a live post spends them."""
        pending = tmp_path / "pending.json"
        history = tmp_path / "posted.json"
        write_pending(pending, [_Item("https://a")])
        assert pending.exists()
        assert load_used(history) == {}

    def test_commit_moves_pending_into_history(self, tmp_path):
        pending, history = tmp_path / "p.json", tmp_path / "h.json"
        write_pending(pending, [_Item("https://a"), _Item("https://b")])
        assert commit_pending(history, pending) == 2
        assert set(load_used(history)) == {"https://a", "https://b"}

    def test_commit_is_idempotent(self, tmp_path):
        pending, history = tmp_path / "p.json", tmp_path / "h.json"
        write_pending(pending, [_Item("https://a")])
        commit_pending(history, pending)
        assert commit_pending(history, pending) == 0
        assert len(load_used(history)) == 1

    def test_commit_preserves_first_used_date(self, tmp_path):
        pending, history = tmp_path / "p.json", tmp_path / "h.json"
        history.write_text(json.dumps({"https://a": {"first_used": "2020-01-01"}}))
        write_pending(pending, [_Item("https://a")])
        commit_pending(history, pending)
        assert load_used(history)["https://a"]["first_used"] == "2020-01-01"

    def test_commit_keeps_earlier_history(self, tmp_path):
        pending, history = tmp_path / "p.json", tmp_path / "h.json"
        history.write_text(json.dumps({"https://old": {"first_used": "2026-01-01"}}))
        write_pending(pending, [_Item("https://new")])
        commit_pending(history, pending)
        assert set(load_used(history)) == {"https://old", "https://new"}

    def test_commit_without_pending_is_a_noop(self, tmp_path):
        assert commit_pending(tmp_path / "h.json", tmp_path / "absent.json") == 0

    def test_commit_survives_corrupt_pending(self, tmp_path):
        pending, history = tmp_path / "p.json", tmp_path / "h.json"
        pending.write_text("{broken")
        assert commit_pending(history, pending) == 0
