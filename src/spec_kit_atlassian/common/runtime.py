from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Literal, cast
from uuid import uuid4

import yaml

from .confluence import Confluence
from .git import run
from .http import Cloud
from .jira import Jira
from .models import Binding, Conflict, Settings, digest, inside, read_yaml, write_json

CONFIG = ".specify/integrations/atlassian/config.yml"


def load(root: Path, binding_path: str) -> tuple[Settings, Binding]:
    cfg = Settings.model_validate(read_yaml(inside(root, CONFIG)))
    binding = Binding.model_validate(read_yaml(inside(root, binding_path)))
    return cfg, binding


def cache_path(binding: Binding, owner: str) -> Path:
    return (
        Path.home()
        / ".cache"
        / "atlassian-integrations"
        / binding.repository_id
        / (f"{binding.feature_id}-{owner}.json")
    )


def restore_binding(binding: Binding, owner: str) -> Binding:
    path = cache_path(binding, owner)
    if path.exists():
        previous = json.loads(path.read_text()).get("binding", {})
        for key in ("epic_key", "page_id"):
            value = previous.get(key)
            if value and getattr(binding, key) is None:
                setattr(binding, key, value)
    return binding


def save_status(binding: Binding, owner: str, result: dict) -> None:
    write_json(cache_path(binding, owner), {"binding": binding.model_dump(mode="json"), **result})


def initialize(
    root: Path, feature: str, repository: str, site: str, project: str, space_id: str, owner: str
) -> dict:
    inside(root, feature + "/spec.md").read_text()
    repo = json.loads(run(["gh", "api", f"repos/{repository}"]))
    cfg_path = inside(root, CONFIG)
    cfg = Settings(
        site=site,
        github_repository=repository,
        jira_project=project,
        confluence_space_id=space_id,
        runtime_ref=repo["default_branch"],
    )
    if cfg_path.exists():
        existing = Settings.model_validate(read_yaml(cfg_path))
        if any(
            getattr(existing, key) != getattr(cfg, key)
            for key in ("site", "github_repository", "jira_project", "confluence_space_id")
        ):
            raise Conflict("existing integration configuration targets another workspace")
        cfg = existing
    if owner not in cfg.enabled_integrations:
        if owner not in ("spec-kit-atlassian", "agentstandards-atlassian"):
            raise Conflict("unknown integration owner")
        cfg.enabled_integrations.append(
            cast(Literal["spec-kit-atlassian", "agentstandards-atlassian"], owner)
        )
    for relative in cfg.binding_files:
        existing_binding = Binding.model_validate(read_yaml(inside(root, relative)))
        if existing_binding.feature_path == feature:
            cfg_path.write_text(yaml.safe_dump(cfg.model_dump(mode="json"), sort_keys=False))
            return {"config": CONFIG, "binding": relative, "action": "already initialized"}
    binding = Binding(repository_id=str(repo["id"]), feature_id=uuid4(), feature_path=feature)
    relative = f".specify/integrations/atlassian/features/{binding.feature_id}.json"
    cfg.binding_files.append(relative)
    branch = run(["git", "branch", "--show-current"], root)
    if not branch:
        raise Conflict("initialize from a named feature branch")
    cfg.feature_refs[relative] = branch
    cfg_path.parent.mkdir(parents=True, exist_ok=True)
    cfg_path.write_text(yaml.safe_dump(cfg.model_dump(mode="json"), sort_keys=False))
    write_json(inside(root, relative), binding.model_dump(mode="json"))
    return {"config": CONFIG, "binding": relative, "action": "initialized; review and commit"}


def doctor(cfg: Settings, binding: Binding, cloud: Cloud) -> dict:
    actual = json.loads(run(["gh", "api", f"repos/{cfg.github_repository}"]))
    if str(actual["id"]) != binding.repository_id:
        raise Conflict("binding repository ID does not match GitHub")
    user = cloud.request("jira", "GET", "/rest/api/3/myself")
    project = cloud.request("jira", "GET", f"/rest/api/3/project/{cfg.jira_project}")
    space = cloud.request("confluence", "GET", f"/wiki/api/v2/spaces/{cfg.confluence_space_id}")
    permissions = cloud.request(
        "jira",
        "GET",
        "/rest/api/3/mypermissions",
        params={
            "projectKey": cfg.jira_project,
            "permissions": (
                "BROWSE_PROJECTS,CREATE_ISSUES,EDIT_ISSUES,TRANSITION_ISSUES,LINK_ISSUES"
            ),
        },
    )
    missing = [
        key for key, value in permissions["permissions"].items() if not value.get("havePermission")
    ]
    issue_types = project.get("issueTypes", [])
    available = {item["name"] for item in issue_types if not item.get("subtask")}
    missing_types = sorted(set(cfg.issue_types.values()) - available)
    fields = cloud.request("jira", "GET", "/rest/api/3/field")
    field_ids = {field["id"] for field in fields}
    missing_fields = sorted(set(cfg.fields.values()) - field_ids)
    result = {
        "jira_account_id": user["accountId"],
        "project": project["key"],
        "space_id": space["id"],
        "issue_types": issue_types,
        "missing_permissions": missing,
        "missing_issue_types": missing_types,
        "missing_fields": missing_fields,
        "confluence_write_verified": False,
        "note": "Read access checked. Write permissions require the sandbox round trip.",
    }
    if binding.epic_key:
        result["available_transitions"] = cloud.request(
            "jira",
            "GET",
            f"/rest/api/3/issue/{binding.epic_key}/transitions",
            params={"expand": "transitions.fields"},
        )
    if binding.page_id:
        Confluence(cloud, cfg).get(binding.page_id)
    result["ok"] = not (missing or missing_types or missing_fields)
    return result


def adopt(cfg: Settings, binding: Binding, cloud: Cloud, epic_key: str, page_id: str) -> dict:
    """Explicitly bind existing containers, preserving all their existing content."""
    from .confluence import Section
    from .jira import PROPERTY
    from .models import label

    jira, confluence = Jira(cloud, cfg), Confluence(cloud, cfg)
    issue, page = jira.get(epic_key), confluence.get(page_id)
    if issue["fields"]["project"]["key"] != cfg.jira_project:
        raise Conflict("Epic belongs to another project")
    if str(page["spaceId"]) != cfg.confluence_space_id:
        raise Conflict("page belongs to another space")
    meta = jira.metadata(epic_key)
    identity = binding.identity + ":feature"
    if meta and meta.get("identity") != identity:
        raise Conflict("Epic is already bound to another feature")
    props = confluence.properties(page_id)
    prop = props.get("atlassian.integration.identity.v1")
    if prop and prop["value"] != binding.identity:
        raise Conflict("page is already bound to another feature")
    cloud.request(
        "jira",
        "PUT",
        f"/rest/api/3/issue/{epic_key}",
        json={
            "update": {"labels": [{"add": label(identity)}, {"add": label(binding.identity)}]},
            "properties": [
                {
                    "key": PROPERTY,
                    "value": {
                        "identity": identity,
                        "owner": "shared",
                        "schema_version": "1.0",
                        "kind": "feature",
                        "generated_fields": {},
                    },
                }
            ],
        },
    )
    confluence.set_property(page_id, "atlassian.integration.identity.v1", binding.identity, prop)
    confluence.update(
        page_id,
        binding.identity,
        "shared",
        [Section("identity", "Integration binding", f"<p>Feature {binding.feature_id}</p>")],
    )
    binding.epic_key, binding.page_id = epic_key, page_id
    return binding.model_dump(mode="json")


def require_worker() -> None:
    if os.environ.get("ATLASSIAN_INTEGRATION_WORKER") != "1":
        raise Conflict("remote writes run only in the serialized integration worker")


def change_branch(prefix: str, binding: Binding, changes: dict) -> str:
    return f"codex/{prefix}-{str(binding.feature_id)[:8]}-{digest(changes)[:10]}"
