"""Tests for triagent.history — never post the same story twice."""

from __future__ import annotations

import json

import pytest

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


class TestMergeLedger:
    """Recording must add to the published ledger, never replace it with the run's copy."""

    def _write(self, path, data):
        path.write_text(json.dumps(data), encoding="utf-8")

    def test_adds_what_the_run_posted(self, tmp_path):
        from triagent.history import merge_ledger

        published, built = tmp_path / "pub.json", tmp_path / "built.json"
        self._write(published, {"https://a": {"first_used": "2026-09-01"}})
        self._write(built, {"https://a": {"first_used": "2026-09-01"}, "https://b": {"first_used": "2026-10-23"}})
        assert merge_ledger(published, built) == 1
        assert set(json.loads(published.read_text())) == {"https://a", "https://b"}

    def test_a_run_whose_copy_is_stale_cannot_shrink_the_published_ledger(self, tmp_path):
        """The reviewer's scenario: the start-of-run fetch failed, the run held the tracked
        placeholder, and copying it over the remote ledger turned 40 entries into 4."""
        from triagent.history import merge_ledger

        published, built = tmp_path / "pub.json", tmp_path / "built.json"
        self._write(published, {f"https://s/{i}": {"first_used": "2026-09-01"} for i in range(40)})
        self._write(built, {"https://example.com/1": {}, "https://s/new": {"first_used": "2026-10-23"}})
        merge_ledger(published, built)
        merged = json.loads(published.read_text())
        assert len(merged) == 42
        assert all(f"https://s/{i}" in merged for i in range(40))
        assert "https://s/new" in merged

    def test_the_published_entry_wins_so_first_used_is_kept(self, tmp_path):
        from triagent.history import merge_ledger

        published, built = tmp_path / "pub.json", tmp_path / "built.json"
        self._write(published, {"https://a": {"first_used": "2026-08-01", "title": "orig"}})
        self._write(built, {"https://a": {"first_used": "2026-10-23", "title": "later"}})
        assert merge_ledger(published, built) == 0
        assert json.loads(published.read_text())["https://a"]["first_used"] == "2026-08-01"

    def test_no_published_ledger_yet_is_a_first_run(self, tmp_path):
        from triagent.history import merge_ledger

        published, built = tmp_path / "new" / "pub.json", tmp_path / "built.json"
        self._write(built, {"https://a": {}})
        assert merge_ledger(published, built) == 1
        assert list(json.loads(published.read_text())) == ["https://a"]

    def test_a_corrupt_published_ledger_is_never_overwritten(self, tmp_path):
        """load_used would call it empty; here the alternative is destroying it."""
        from triagent.history import merge_ledger

        published, built = tmp_path / "pub.json", tmp_path / "built.json"
        published.write_text("{not json", encoding="utf-8")
        self._write(built, {"https://a": {}})
        with pytest.raises(json.JSONDecodeError):
            merge_ledger(published, built)
        assert published.read_text() == "{not json"

    def test_a_published_ledger_that_is_not_an_object_is_refused(self, tmp_path):
        from triagent.history import merge_ledger

        published, built = tmp_path / "pub.json", tmp_path / "built.json"
        published.write_text("[1, 2]", encoding="utf-8")
        self._write(built, {})
        with pytest.raises(ValueError, match="not a JSON object"):
            merge_ledger(published, built)
        assert published.read_text() == "[1, 2]"

    def test_nothing_to_add_leaves_the_published_file_untouched(self, tmp_path):
        """Not even a re-format: a formatting-only change would become a commit."""
        from triagent.history import merge_ledger

        published, built = tmp_path / "pub.json", tmp_path / "built.json"
        original = '{"https://a":{"first_used":"2026-09-01"}}'  # deliberately not indent=2
        published.write_text(original, encoding="utf-8")
        self._write(built, {"https://a": {"first_used": "2026-10-23"}})
        assert merge_ledger(published, built) == 0
        assert published.read_text() == original
