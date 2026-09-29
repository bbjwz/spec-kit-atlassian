import json
from pathlib import Path

import pytest

from spec_kit_atlassian.common.confluence import (
    Confluence,
    Section,
    document,
    merge_sections,
    node_hash,
)
from spec_kit_atlassian.common.models import Binding, Conflict, inside
from spec_kit_atlassian.common.render import markdown


def test_shared_page_both_orders():
    for first, second in (
        ("spec-kit-atlassian", "agentstandards-atlassian"),
        ("agentstandards-atlassian", "spec-kit-atlassian"),
    ):
        body = "<h2>Human notes</h2><p>Do not lose this.</p>"
        body, hashes = merge_sections(
            body, "123:feature", first, [Section("plan", "Plan", "<p>First</p>")], {}
        )
        body, _ = merge_sections(
            body, "123:feature", second, [Section("review", "Review", "<p>Second</p>")], {}
        )
        changed, _ = merge_sections(
            body, "123:feature", first, [Section("plan", "Plan", "<p>Updated</p>")], hashes
        )
        assert "Do not lose this." in changed and "Second" in changed and "Updated" in changed


def test_human_edit_is_conflict():
    body, hashes = merge_sections(
        "", "f", "owner", [Section("plan", "Plan", "<p>Original</p>")], {}
    )
    with pytest.raises(Conflict, match="human edits"):
        merge_sections(
            body.replace("Original", "Human"),
            "f",
            "owner",
            [Section("plan", "Plan", "<p>New</p>")],
            hashes,
        )


def test_identical_update_no_churn_and_crash_recovery():
    sections = [Section("plan", "Plan", "<p>same</p>")]
    body, hashes = merge_sections("", "f", "owner", sections, {})
    same, _ = merge_sections(body, "f", "owner", sections, hashes)
    recovered, _ = merge_sections(body, "f", "owner", sections, {})
    assert node_hash(document(body)) == node_hash(document(same)) == node_hash(document(recovered))


def test_version_conflict_rereads_human_changes(cloud, cfg, tenant):
    confluence = Confluence(cloud, cfg)
    page = confluence.ensure_page("f", "Feature", None)
    confluence.update(page, "f", "owner", [Section("a", "A", "<p>before</p>")])
    tenant.race = lambda page: page["body"]["storage"].update(
        value=page["body"]["storage"]["value"] + "<p>Concurrent human note</p>"
    )
    confluence.update(page, "f", "owner", [Section("a", "A", "<p>after</p>")])
    assert "Concurrent human note" in tenant.pages[page]["body"]["storage"]["value"]
    count = len(tenant.writes)
    assert confluence.update(page, "f", "owner", [Section("a", "A", "<p>after</p>")]) == "unchanged"
    assert len(tenant.writes) == count


def test_markup_safety_and_links():
    body = markdown(
        "<script>alert(1)</script>\n\n[plan](plan.md)\n\n![img](https://example.org/x.png)",
        "https://github.com/org/repo/blob/sha/specs/f/spec.md",
    )
    assert "<script>" not in body and "<img" not in body
    assert "https://github.com/org/repo/blob/sha/specs/f/plan.md" in body
    with pytest.raises(Conflict):
        document('<!DOCTYPE root [<!ENTITY x SYSTEM "file:///etc/passwd">]><p>&x;</p>')


def test_symlink_and_traversal_rejected(tmp_path):
    with pytest.raises(Conflict):
        inside(tmp_path, "../secret")
    (tmp_path / "link").symlink_to("/tmp")
    with pytest.raises(Conflict):
        inside(tmp_path, "link/secret")


def test_contract_fixture():
    data = json.loads((Path(__file__).parent / "fixtures/binding-v1.json").read_text())
    assert Binding.model_validate(data).model_dump(mode="json") == data


def test_expand_title_uses_named_parameter():
    body, _ = merge_sections("", "f", "owner", [Section("a", "Architecture", "<p>x</p>")], {})
    from spec_kit_atlassian.common.confluence import NS

    assert (
        document(body).xpath("string(.//ac:parameter[@ac:name='title'])", namespaces=NS)
        == "Architecture"
    )


@pytest.mark.parametrize(
    "migrations",
    [{"T001": "T002", "T003": "T002"}, {"T001": "T002", "T002": "T003"}, {"bad": "T001"}],
)
def test_ambiguous_task_migrations_rejected(cfg, migrations):
    from pydantic import ValidationError

    from spec_kit_atlassian.common.models import Settings

    with pytest.raises(ValidationError):
        Settings.model_validate({**cfg.model_dump(), "task_id_migrations": migrations})
