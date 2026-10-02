import copy
import json

import pytest

from spec_kit_atlassian.common.models import Conflict
from spec_kit_atlassian.governance.project import (
    CONSTITUTION,
    PROJECT,
    Candidate,
    Project,
    fingerprint,
    initialize,
)
from spec_kit_atlassian.governance.service import Service
from spec_kit_atlassian.governance.worker import classification, refresh_checks


class Source:
    def __init__(self):
        self.current = Candidate(
            commit="a" * 40,
            content="# Constitution\n\nBe precise.\n",
            content_digest=fingerprint("# Constitution\n\nBe precise.\n"),
        )
        self.contents = {self.current.commit: self.current.content}

    def candidate(self, project):
        return self.current

    def text(self, path, ref):
        assert path == CONSTITUTION
        return self.contents[ref]

    def amend(self, content="# Constitution\n\nBe kind.\n"):
        self.current = Candidate(
            commit="b" * 40, content=content, content_digest=fingerprint(content)
        )
        self.contents[self.current.commit] = content


@pytest.fixture
def governance(cfg, cloud, tenant):
    project = Project(
        repository_id="123",
        review_branch="codex/constitution",
        approver_account_ids=["human-1"],
        enforcement_enabled=True,
    )
    github = Source()
    return Service(cfg, project, github, cloud)


def decide(tenant, key, status="Approved", actor="human-1"):
    history = tenant.histories.setdefault(key, [])
    number = str(len(history) + 1)
    event = {
        "id": number,
        "created": "2026-10-02T12:00:00+00:00",
        "author": {"accountId": actor, "accountType": "atlassian"},
        "items": [{"field": "status", "fieldId": "status", "toString": status}],
    }
    history.append(event)
    tenant.issues[key]["fields"]["status"]["name"] = status
    tenant.issues[key]["fields"]["updated"] = number
    return event


def test_constitution_only_init(tmp_path, monkeypatch):
    monkeypatch.setattr(
        "spec_kit_atlassian.governance.project.GitHub.info",
        lambda self: {"id": 123, "default_branch": "main"},
    )
    result = initialize(
        tmp_path,
        "example/project",
        "https://example.atlassian.net",
        "TEST",
        "10",
        "codex/constitution",
        ["human-1"],
    )
    assert result["enforcement_enabled"] is False
    assert not (tmp_path / "specs").exists()
    assert json.loads((tmp_path / PROJECT).read_text())["review_branch"] == "codex/constitution"


def test_publish_approve_without_feature_or_merge(governance, tenant, tmp_path):
    result = governance.publish()
    assert result["gate"] is False
    assert len(tenant.issues) == len(tenant.pages) == 1
    issue = tenant.issues[result["review_issue"]]
    assert "parent" not in issue["fields"]
    assert issue["fields"]["status"]["name"] == "Awaiting approval"
    path = tmp_path / CONSTITUTION
    path.parent.mkdir(parents=True)
    path.write_text(governance.github.current.content)
    with pytest.raises(Conflict, match="awaiting_approval"):
        governance.gate(tmp_path)
    decide(tenant, result["review_issue"])
    # No synchronization, Git audit, feature initialization or Git merge needed.
    assert governance.gate(tmp_path)["gate"] is True
    path.write_text("old constitution")
    with pytest.raises(Conflict, match="local_mismatch"):
        governance.gate(tmp_path)


def test_noop_and_human_notes(governance, tenant):
    result = governance.publish()
    page = tenant.pages[result["page"]]
    page["body"]["storage"]["value"] += "<p>Keep my human note</p>"
    before = len(tenant.writes)
    governance.publish()
    assert len(tenant.writes) == before
    assert "Keep my human note" in page["body"]["storage"]["value"]


def test_pushed_amendment_pauses_before_publisher(governance, tenant):
    first = governance.publish()
    decide(tenant, first["review_issue"])
    assert governance.status()["gate"]
    governance.github.amend()
    assert governance.status()["state"] == "awaiting_publication"
    assert not governance.status()["gate"]
    second = governance.publish()
    assert not second["gate"] and second["review_issue"] != first["review_issue"]
    # Late/repeated approval of the superseded ticket does not approve the amendment.
    decide(tenant, first["review_issue"])
    assert not governance.status()["gate"]


def test_withdrawal_restores_previous_but_rejection_does_not(governance, tenant):
    first = governance.publish()
    decide(tenant, first["review_issue"])
    original = governance.github.current.content
    governance.github.amend()
    second = governance.publish()
    decide(tenant, second["review_issue"], "Changes requested")
    assert not governance.status()["gate"]
    decide(tenant, second["review_issue"], "Withdrawn")
    restored = governance.status(original)
    assert restored["gate"] and restored["active_source"] == "a" * 40
    assert restored["review_decision"]["state"] == "withdrawn"
    assert not governance.status(governance.github.current.content)["gate"]
    decide(tenant, first["review_issue"], "Changes requested")
    with pytest.raises(Conflict, match="revoked"):
        governance.status()


def test_initial_withdrawal_never_unlocks(governance, tenant):
    result = governance.publish()
    decide(tenant, result["review_issue"], "Withdrawn")
    assert not governance.status()["gate"]


@pytest.mark.parametrize("tamper", ["actor", "bot", "description", "after", "confluence", "digest"])
def test_invalid_approvals_fail_closed(governance, tenant, tamper):
    result = governance.publish()
    key = result["review_issue"]
    event = decide(tenant, key)
    if tamper == "actor":
        event["author"]["accountId"] = "other"
    elif tamper == "bot":
        event["author"]["accountType"] = "app"
    elif tamper == "description":
        tenant.issues[key]["fields"]["description"] = {}
    elif tamper == "after":
        tenant.histories[key].append(
            {**copy.deepcopy(event), "id": "2", "items": [{"fieldId": "description"}]}
        )
    elif tamper == "confluence":
        body = tenant.pages[result["page"]]["body"]["storage"]
        body["value"] = body["value"].replace("Be precise.", "Someone edited this.")
    elif tamper == "digest":
        tenant.issue_props[key]["atlassian.integration.v1"]["constitution_digest"] = "wrong"
    with pytest.raises(Conflict):
        governance.status()


def test_generation_identity_survives_revert(governance, tenant):
    first = governance.publish()
    decide(tenant, first["review_issue"])
    initial = governance.github.current
    governance.github.amend()
    second = governance.publish()
    governance.github.current = initial  # even a force rewind must not reuse the old approval
    assert not governance.status()["gate"]
    third = governance.publish()
    assert third["review_issue"] not in (first["review_issue"], second["review_issue"])
    assert not third["gate"]


def test_approval_source_race_blocks(governance, tenant, monkeypatch):
    result = governance.publish()
    decide(tenant, result["review_issue"])
    original = governance.github.candidate
    calls = 0

    def changing(project):
        nonlocal calls
        calls += 1
        if calls == 2:
            governance.github.amend()
        return original(project)

    monkeypatch.setattr(governance.github, "candidate", changing)
    with pytest.raises(Conflict, match="changed during"):
        governance.status()


@pytest.mark.parametrize(
    "names,kind",
    [
        ([CONSTITUTION], "constitution"),
        ([PROJECT], "bootstrap"),
        ([CONSTITUTION, "app.py"], "feature"),
        ([PROJECT, "app.py"], "feature"),
    ],
)
def test_pr_exemptions_are_narrow(names, kind):
    assert (
        classification([{"filename": n, "status": "modified"} for n in names], len(names)) == kind
    )
    assert (
        classification([{"filename": n, "status": "renamed"} for n in names], len(names))
        == "feature"
    )


def test_incomplete_pr_files_never_exempt():
    with pytest.raises(Conflict):
        classification([{"filename": CONSTITUTION, "status": "modified"}], 2)


def test_check_invalidated_when_publisher_has_not_run(governance, tenant, monkeypatch):
    result = governance.publish()
    decide(tenant, result["review_issue"])
    governance.github.amend()
    requests = []
    monkeypatch.setattr(
        governance.github,
        "pages",
        lambda path: (
            [{"number": 1}]
            if path.startswith("pulls?")
            else [{"filename": "app.py", "status": "modified"}]
        ),
        raising=False,
    )

    def api(path, payload=None):
        if payload:
            requests.append(payload)
            return {}
        return {"head": {"sha": "a" * 40}, "changed_files": 1, "number": 1}

    monkeypatch.setattr(governance.github, "api", api, raising=False)
    refresh_checks(governance)
    assert requests[-1]["state"] == "failure"


def test_repeated_unapproved_amendments_keep_last_approved_baseline(governance, tenant):
    first = governance.publish()
    decide(tenant, first["review_issue"])
    original = governance.github.current.content
    governance.github.amend()
    governance.publish()
    governance.github.amend("# Constitution\n\nThird proposal.\n")
    third = governance.publish()
    decide(tenant, third["review_issue"], "Withdrawn")
    assert governance.status(original)["gate"]


def test_feature_sync_stops_before_any_writes(project, cfg, binding, cloud, tenant, monkeypatch):
    from spec_kit_atlassian.engine import synchronize

    def blocked(root):
        raise Conflict("constitution gate blocked")

    monkeypatch.setattr("spec_kit_atlassian.governance.cli.feature_gate", blocked)
    with pytest.raises(Conflict, match="constitution gate"):
        synchronize(project, cfg, binding, cloud, lambda b: pytest.fail("binding write"))
    assert tenant.writes == []


def test_constitution_publication_recovers_partial_create(governance, tenant, monkeypatch):
    original = governance.confluence.set_property

    def fail(page, key, value, current):
        if key == "atlassian.constitution.reviews.v1":
            raise Conflict("simulated property-write failure")
        return original(page, key, value, current)

    monkeypatch.setattr(governance.confluence, "set_property", fail)
    with pytest.raises(Conflict, match="simulated"):
        governance.publish()
    decide(tenant, "TEST-1")  # Approval made before durable publication completion is invalidated.
    monkeypatch.setattr(governance.confluence, "set_property", original)
    result = governance.publish()
    assert len(tenant.issues) == len(tenant.pages) == 1
    assert not result["gate"]


def test_hook_and_preset_cover_all_feature_commands():
    from pathlib import Path

    import yaml

    root = Path(__file__).parents[1]
    manifest = yaml.safe_load((root / "extension.yml").read_text())
    preset = yaml.safe_load((root / "presets/constitution-gate/preset.yml").read_text())
    for name in ("specify", "clarify", "plan", "tasks", "implement"):
        hook = manifest["hooks"]["before_" + name]
        assert hook["optional"] is False
        assert hook["command"] == "speckit.atlassian.constitution-guard"
        item = next(t for t in preset["provides"]["templates"] if t["name"] == "speckit." + name)
        assert item["strategy"] == "wrap"
        text = (root / "presets/constitution-gate" / item["file"]).read_text()
        assert text.index("constitution gate --if-enabled") < text.index("{CORE_TEMPLATE}")
        assert "Agentstandards" in text


def test_legacy_project_page_is_reused_not_approved(governance, tenant):
    from spec_kit_atlassian.common.confluence import Section

    page = governance.confluence.ensure_page(
        governance.project.identity, governance.cfg.github_repository, None
    )
    governance.confluence.update(
        page,
        governance.project.identity,
        "spec-kit-atlassian",
        [Section("constitution", "Project constitution", "<p>Old unreviewed text</p>")],
    )
    result = governance.publish()
    assert result["page"] == page and len(tenant.pages) == 1
    assert not result["gate"]


def test_unrelated_commits_do_not_change_candidate_identity():
    from spec_kit_atlassian.governance.project import GitHub

    github = GitHub("example/project")
    project = Project(
        repository_id="123", review_branch="codex/constitution", approver_account_ids=["human-1"]
    )
    calls = []
    github.tip = lambda branch: "b" * 40
    github.text = lambda path, ref: "same content"

    def api(path):
        calls.append(path)
        return [{"sha": "a" * 40}]

    github.api = api
    before = github.candidate(project)
    github.tip = lambda branch: "c" * 40
    after = github.candidate(project)
    assert before.token == after.token
    assert any("path=" in call for call in calls)


def test_unpublished_preview_is_read_only(governance, tenant):
    result = governance.preview()
    assert result["state"] == "awaiting_publication"
    assert not result["writes"] and not result["gate"]
    assert tenant.writes == []


def test_live_outage_never_uses_previous_approval(governance, tenant, monkeypatch):
    result = governance.publish()
    decide(tenant, result["review_issue"])
    assert governance.status()["gate"]

    def unavailable(project):
        raise Conflict("GitHub unavailable")

    monkeypatch.setattr(governance.github, "candidate", unavailable)
    with pytest.raises(Conflict, match="unavailable"):
        governance.status()


def test_missing_local_registration_cannot_bypass_trusted_gate(tmp_path, cfg, monkeypatch):
    import yaml

    from spec_kit_atlassian.common.runtime import CONFIG
    from spec_kit_atlassian.governance import cli

    path = tmp_path / CONFIG
    path.parent.mkdir(parents=True)
    path.write_text(yaml.safe_dump(cfg.model_dump(mode="json")))
    monkeypatch.setattr(cli, "run", lambda *a: "example/project")
    from spec_kit_atlassian.governance.project import GitHub

    monkeypatch.setattr(GitHub, "info", lambda self: {"default_branch": "main"})
    monkeypatch.setattr(GitHub, "tip", lambda *a: "a" * 40)
    monkeypatch.setattr(GitHub, "api", lambda *a: [{"name": "project.json"}])

    def trusted_guard(root):
        raise Conflict("trusted gate still required")

    monkeypatch.setattr(cli, "trusted", trusted_guard)
    with pytest.raises(Conflict, match="trusted gate"):
        cli.feature_gate(tmp_path)


def test_guard_does_not_trust_a_different_repository(tmp_path, cfg, monkeypatch):
    import yaml

    from spec_kit_atlassian.common.runtime import CONFIG
    from spec_kit_atlassian.governance import cli

    path = tmp_path / CONFIG
    path.parent.mkdir(parents=True)
    path.write_text(yaml.safe_dump(cfg.model_dump(mode="json")))
    monkeypatch.setattr(cli, "run", lambda *a: "somewhere/else")
    with pytest.raises(Conflict, match="does not match"):
        cli.feature_gate(tmp_path)


def test_unknown_issue_create_is_not_blindly_repeated(governance, monkeypatch):
    attempts = []

    def unknown(*args, **kwargs):
        attempts.append(True)
        raise Conflict("create outcome unknown")

    monkeypatch.setattr(governance.jira, "upsert", unknown)
    with pytest.raises(Conflict, match="unknown"):
        governance.publish()
    with pytest.raises(Conflict, match="unresolved"):
        governance.publish()
    assert len(attempts) == 1


def test_recorded_issue_key_recovers_even_when_search_index_lags(governance, tenant, monkeypatch):
    original = governance.confluence.set_property

    def fail(page, key, value, current):
        if key == "atlassian.constitution.reviews.v1":
            raise Conflict("journal failed")
        return original(page, key, value, current)

    monkeypatch.setattr(governance.confluence, "set_property", fail)
    with pytest.raises(Conflict, match="journal failed"):
        governance.publish()
    monkeypatch.setattr(governance.confluence, "set_property", original)
    monkeypatch.setattr(governance.jira, "find", lambda identity: None)
    governance.publish()
    assert len(tenant.issues) == 1
