import json
from types import SimpleNamespace

import pytest

from spec_kit_atlassian.common import git
from spec_kit_atlassian.common.models import Conflict


def test_publication_is_focused_draft_and_does_not_touch_checkout(tmp_path, monkeypatch):
    calls = []
    replies = [
        [],
        {"object": {"sha": "a" * 40}},
        {"tree": {"sha": "tree"}},
        {"sha": "new-tree"},
        {"sha": "new-commit"},
        {},
        {"html_url": "https://github.com/o/r/pull/1"},
    ]

    def fake_run(args, **kwargs):
        calls.append((args, json.loads(kwargs["input"]) if kwargs.get("input") else None))
        return SimpleNamespace(returncode=0, stdout=json.dumps(replies.pop(0)))

    monkeypatch.setattr(git.subprocess, "run", fake_run)
    monkeypatch.setattr(git, "revision", lambda *a, **k: "a" * 40)
    result = git.draft_change(
        tmp_path,
        "o/r",
        "feature/example",
        "codex/reconcile",
        {"specs/f/tasks.md": "- [x] T001 task"},
        "Update tasks",
        "Review",
    )
    assert result.endswith("/pull/1")
    assert calls[-1][1]["draft"] is True
    assert calls[-2][1]["ref"] == "refs/heads/codex/reconcile"
    assert calls[3][1]["tree"] == [
        {"path": "specs/f/tasks.md", "mode": "100644", "type": "blob", "content": "- [x] T001 task"}
    ]
    assert list(tmp_path.iterdir()) == []


def test_publication_stops_when_feature_branch_moved(tmp_path, monkeypatch):
    replies = [[], {"object": {"sha": "b" * 40}}]
    monkeypatch.setattr(
        git.subprocess,
        "run",
        lambda *a, **k: SimpleNamespace(returncode=0, stdout=json.dumps(replies.pop(0))),
    )
    monkeypatch.setattr(git, "revision", lambda *a, **k: "a" * 40)
    with pytest.raises(Conflict, match="branch moved"):
        git.draft_change(tmp_path, "o/r", "feature", "codex/change", {"file": "value"}, "T", "B")


def test_existing_pr_is_reused(tmp_path, monkeypatch):
    monkeypatch.setattr(
        git.subprocess,
        "run",
        lambda *a, **k: SimpleNamespace(
            returncode=0,
            stdout=json.dumps(
                [{"base": {"ref": "feature"}, "html_url": "https://github.com/o/r/pull/1"}]
            ),
        ),
    )
    monkeypatch.setattr(git, "revision", lambda *a, **k: pytest.fail("duplicate publication"))
    assert git.draft_change(
        tmp_path, "o/r", "feature", "codex/change", {"file": "value"}, "T", "B"
    ).endswith("/pull/1")
