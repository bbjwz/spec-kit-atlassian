"""Project governance registration and trusted GitHub source reads."""

from __future__ import annotations

import base64
import json
import re
import subprocess
from pathlib import Path
from typing import Literal
from urllib.parse import quote, urlencode

import yaml
from pydantic import Field, field_validator, model_validator

from ..common.git import run
from ..common.models import Conflict, Model, Settings, digest, inside, write_json
from ..common.runtime import CONFIG

PROJECT = ".specify/integrations/atlassian/project.json"
CONSTITUTION = ".specify/memory/constitution.md"


def fingerprint(text: str) -> str:
    return digest(text.replace("\r\n", "\n"))


class Project(Model):
    schema_version: Literal["1.0"] = "1.0"
    repository_id: str = Field(pattern=r"^[0-9]+$")
    review_branch: str
    approver_account_ids: list[str] = Field(min_length=1)
    page_id: str | None = Field(default=None, pattern=r"^[0-9]+$")
    enforcement_enabled: bool = False
    awaiting_status: str = "Awaiting approval"
    approved_status: str = "Approved"
    changes_status: str = "Changes requested"
    withdrawn_status: str = "Withdrawn"

    @field_validator("review_branch")
    @classmethod
    def branch(cls, value: str) -> str:
        if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_./-]*", value) or ".." in value:
            raise ValueError("invalid review branch")
        return value

    @model_validator(mode="after")
    def distinct_statuses(self):
        if (
            len(
                {
                    self.awaiting_status,
                    self.approved_status,
                    self.changes_status,
                    self.withdrawn_status,
                }
            )
            != 4
        ):
            raise ValueError("governance statuses must be distinct")
        if any(not actor.strip() for actor in self.approver_account_ids):
            raise ValueError("approver IDs must not be blank")
        return self

    @property
    def identity(self) -> str:
        return "project:" + self.repository_id


class Candidate(Model):
    commit: str = Field(pattern=r"^[a-f0-9]{40}$")
    content: str
    content_digest: str = Field(pattern=r"^[a-f0-9]{64}$")

    @property
    def token(self) -> str:
        return digest([self.commit, self.content_digest])


class GitHub:
    """Use gh's authenticated API; never import or execute candidate repository code."""

    def __init__(self, repository: str):
        self.repository = repository

    def api(self, path: str, payload=None):
        args = ["gh", "api", f"repos/{self.repository}/{path}"]
        if payload is not None:
            args += ["--method", "POST", "--input", "-"]
        response = subprocess.run(
            args,
            input=json.dumps(payload) if payload is not None else None,
            capture_output=True,
            text=True,
        )
        if response.returncode:
            raise Conflict("GitHub governance request failed; verify access and registration")
        return json.loads(response.stdout or "{}")

    def info(self):
        # Empty path is accepted by GitHub's repository endpoint.
        return self.api("")

    def text(self, path: str, ref: str) -> str:
        data = self.api("contents/" + quote(path, safe="/") + "?" + urlencode({"ref": ref}))
        if (
            data.get("type") != "file"
            or data.get("encoding") != "base64"
            or data.get("size", 0) > 1_000_000
        ):
            raise Conflict("governance input must be a regular UTF-8 file under 1 MB")
        return base64.b64decode(data["content"]).decode("utf-8")

    def tip(self, branch: str) -> str:
        return self.api("commits/" + quote(branch, safe=""))["sha"]

    def candidate(self, project: Project) -> Candidate:
        tip = self.tip(project.review_branch)
        content = self.text(CONSTITUTION, tip)
        commits = self.api(
            "commits?" + urlencode({"sha": tip, "path": CONSTITUTION, "per_page": 1})
        )
        if not commits:
            raise Conflict("registered branch has no committed constitution")
        source = commits[0]["sha"]
        if fingerprint(self.text(CONSTITUTION, source)) != fingerprint(content):
            raise Conflict("constitution source history disagrees with branch contents")
        return Candidate(commit=source, content=content, content_digest=fingerprint(content))

    def pages(self, path: str):
        separator = "&" if "?" in path else "?"
        for page in range(1, 101):
            values = self.api(f"{path}{separator}per_page=100&page={page}")
            yield from values
            if len(values) < 100:
                return
        raise Conflict("GitHub pagination budget exceeded")


def trusted(root: Path) -> tuple[Settings, Project, GitHub]:
    local = Settings.model_validate(yaml.safe_load(inside(root, CONFIG).read_text()))
    actual_repository = run(
        ["gh", "repo", "view", "--json", "nameWithOwner", "--jq", ".nameWithOwner"], root
    )
    if actual_repository.lower() != local.github_repository.lower():
        raise Conflict("local governance target does not match this Git repository")
    github = GitHub(local.github_repository)
    info = github.info()
    ref = github.tip(info["default_branch"])
    cfg = Settings.model_validate(yaml.safe_load(github.text(CONFIG, ref)))
    project = Project.model_validate_json(github.text(PROJECT, ref))
    if cfg.github_repository != local.github_repository or str(info["id"]) != project.repository_id:
        raise Conflict("trusted governance registration belongs to another repository")
    return cfg, project, github


def initialize(
    root: Path,
    repository: str,
    site: str,
    jira_project: str,
    space_id: str,
    branch: str,
    approvers: list[str],
    page_id: str | None = None,
) -> dict:
    github = GitHub(repository)
    info = github.info()
    path = inside(root, CONFIG)
    cfg = Settings(
        site=site,
        github_repository=repository,
        jira_project=jira_project,
        confluence_space_id=space_id,
        runtime_ref=info["default_branch"],
    )
    if path.exists():
        existing = Settings.model_validate(yaml.safe_load(path.read_text()))
        for name in ("site", "github_repository", "jira_project", "confluence_space_id"):
            if getattr(existing, name) != getattr(cfg, name):
                raise Conflict("existing configuration targets another workspace")
        cfg = existing
    if "spec-kit-atlassian" not in cfg.enabled_integrations:
        cfg.enabled_integrations.append("spec-kit-atlassian")
    project = Project(
        repository_id=str(info["id"]),
        review_branch=branch,
        approver_account_ids=approvers,
        page_id=page_id or cfg.project_page_id,
    )
    project_path = inside(root, PROJECT)
    if project_path.exists():
        old = Project.model_validate_json(project_path.read_text())
        if old.model_copy(update={"enforcement_enabled": False}) != project:
            raise Conflict("project already registered differently; review configuration manually")
        return {"state": "already initialized", "project": PROJECT}
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(yaml.safe_dump(cfg.model_dump(mode="json"), sort_keys=False))
    write_json(project_path, project.model_dump(mode="json"))
    return {
        "state": "initialized",
        "project": PROJECT,
        "enforcement_enabled": False,
        "next": "Commit registration/workflows to the default branch; push the review branch.",
    }
