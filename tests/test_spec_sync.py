import pytest

from spec_kit_atlassian.common.jira import Jira
from spec_kit_atlassian.common.models import Conflict
from spec_kit_atlassian.engine import reconcile_tasks, synchronize
from spec_kit_atlassian.specs import load_feature, parse_tasks


def test_feature_without_tasks(project, binding):
    (project / binding.feature_path / "tasks.md").unlink()
    (project / binding.feature_path / "plan.md").unlink()
    feature = load_feature(project, binding)
    assert feature["stage"] == "Specification available" and feature["tasks"] == []


def test_parse_dependencies_and_fences():
    tasks = parse_tasks(
        "## Phase 1\n- [x] T001 [P] [US1] Build schema\n"
        "```md\n- [ ] T999 ignored\n```\n"
        "- [ ] T002 Call T001 API; depends on T001\n"
    )
    assert len(tasks) == 2 and tasks[0].parallel and tasks[0].done
    assert tasks[1].dependencies == ("T001",)
    assert parse_tasks("- [ ] T003 Discuss T004")[0].dependencies == ()


@pytest.mark.parametrize(
    "text",
    [
        "- [ ] T001 a\n- [ ] T001 b",
        "- [ ] no identity",
        "- [ ] T001 depends on T002",
        "- [ ] T001 depends on T002\n- [ ] T002 depends on T001",
    ],
)
def test_bad_task_lists(text):
    with pytest.raises(Conflict):
        parse_tasks(text)


def test_roundtrip_idempotent_and_preserves_assignment(
    project, cfg, binding, cloud, tenant, monkeypatch
):
    monkeypatch.setattr("spec_kit_atlassian.engine.revision", lambda *a, **kw: "a" * 40)
    result = synchronize(project, cfg, binding, cloud, lambda b: None)
    assert len(tenant.issues) == 3 and len(tenant.pages) == 1
    task_key = result["issues"][1]["key"]
    tenant.issues[task_key]["fields"]["assignee"] = {"accountId": "human"}
    writes = len(tenant.writes)
    synchronize(project, cfg, binding, cloud, lambda b: None)
    assert len(tenant.writes) == writes
    tenant.issues[task_key]["fields"]["status"]["name"] = "Done"
    changes = reconcile_tasks(project, cfg, binding, Jira(cloud, cfg))
    assert "- [x] T001" in changes[binding.feature_path + "/tasks.md"]
    assert tenant.issues[task_key]["fields"]["assignee"]["accountId"] == "human"


def test_removal_retains_issue(project, cfg, binding, cloud, tenant, monkeypatch):
    monkeypatch.setattr("spec_kit_atlassian.engine.revision", lambda *a, **kw: "a" * 40)
    synchronize(project, cfg, binding, cloud, lambda b: None)
    (project / binding.feature_path / "tasks.md").write_text("- [ ] T001 [P] [US1] Build schema\n")
    synchronize(project, cfg, binding, cloud, lambda b: None)
    assert len(tenant.issues) == 3
    assert any("Removed from plan" in i["fields"]["summary"] for i in tenant.issues.values())


def test_human_description_edit_stops_sync(project, cfg, binding, cloud, tenant, monkeypatch):
    monkeypatch.setattr("spec_kit_atlassian.engine.revision", lambda *a, **kw: "a" * 40)
    synchronize(project, cfg, binding, cloud, lambda b: None)
    tenant.issues["TEST-2"]["fields"]["description"] = {"human": "edit"}
    with pytest.raises(Conflict, match="human edit"):
        synchronize(project, cfg, binding, cloud, lambda b: None)
