from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path
from typing import Any, Literal
from urllib.parse import urlparse
from uuid import UUID

import yaml
from pydantic import BaseModel, ConfigDict, Field, field_validator


class Conflict(RuntimeError):
    """State is ambiguous; stop without overwriting human work."""


class Model(BaseModel):
    model_config = ConfigDict(extra="forbid")


def digest(value: Any) -> str:
    data = json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(data.encode()).hexdigest()


class Binding(Model):
    schema_version: Literal["1.0"] = "1.0"
    repository_id: str = Field(pattern=r"^[0-9]+$")
    feature_id: UUID
    feature_path: str
    epic_key: str | None = Field(default=None, pattern=r"^[A-Z][A-Z0-9_]*-[0-9]+$")
    page_id: str | None = Field(default=None, pattern=r"^[0-9]+$")

    @field_validator("feature_path")
    @classmethod
    def safe_feature(cls, value: str) -> str:
        path = Path(value)
        if path.is_absolute() or ".." in path.parts or not value.startswith("specs/"):
            raise ValueError("feature_path must be repository-relative under specs/")
        return value

    @property
    def identity(self) -> str:
        return f"{self.repository_id}:{self.feature_id}"


class Settings(Model):
    schema_version: Literal["1.0"] = "1.0"
    site: str
    cloud_id: UUID | None = None
    auth_mode: Literal["api-token", "scoped-token"] = "api-token"
    email_env: str = "ATLASSIAN_EMAIL"
    token_env: str = "ATLASSIAN_API_TOKEN"
    github_repository: str = Field(pattern=r"^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$")
    jira_project: str = Field(pattern=r"^[A-Z][A-Z0-9_]*$")
    confluence_space_id: str = Field(pattern=r"^[0-9]+$")
    confluence_parent_id: str | None = Field(default=None, pattern=r"^[0-9]+$")
    project_page_id: str | None = Field(default=None, pattern=r"^[0-9]+$")
    issue_types: dict[str, str] = Field(
        default_factory=lambda: {
            "feature": "Epic",
            "task": "Task",
            "council": "Task",
            "decision": "Task",
        }
    )
    statuses: dict[str, str] = Field(
        default_factory=lambda: {
            "todo": "To Do",
            "active": "In Progress",
            "review": "In Review",
            "blocked": "Blocked",
            "waiting": "Waiting for input",
            "done": "Done",
        }
    )
    fields: dict[str, str] = Field(default_factory=dict)
    approver_account_ids: list[str] = Field(default_factory=list)
    decision_status: str = "Approved"
    enabled_integrations: list[Literal["spec-kit-atlassian", "agentstandards-atlassian"]] = Field(
        default_factory=list
    )
    binding_files: list[str] = Field(default_factory=list)
    feature_refs: dict[str, str] = Field(default_factory=dict)
    attachments: list[str] = Field(default_factory=list)
    task_id_migrations: dict[str, str] = Field(default_factory=dict)
    max_page_bytes: int = Field(default=1_000_000, ge=1024, le=5_000_000)
    workflow: str = "atlassian-sync.yml"
    runtime_ref: str = Field(default="main", min_length=1)

    @field_validator("site")
    @classmethod
    def cloud_site(cls, value: str) -> str:
        u = urlparse(value)
        if (
            u.scheme != "https"
            or not u.hostname
            or not u.hostname.endswith(".atlassian.net")
            or u.username
            or u.password
            or u.port
            or u.path not in ("", "/")
            or u.query
            or u.fragment
        ):
            raise ValueError("site must be an https://<tenant>.atlassian.net origin")
        return value.rstrip("/")

    @field_validator("email_env", "token_env")
    @classmethod
    def environment_name(cls, value: str) -> str:
        if not re.fullmatch(r"[A-Z_][A-Z0-9_]*", value):
            raise ValueError("credential settings must be environment variable names")
        return value

    @field_validator("task_id_migrations")
    @classmethod
    def migrations(cls, value: dict[str, str]) -> dict[str, str]:
        if any(not re.fullmatch(r"T[0-9]+", item) for pair in value.items() for item in pair):
            raise ValueError("task migrations must use T-number identifiers")
        if len(set(value.values())) != len(value) or set(value) & set(value.values()):
            raise ValueError("task migrations must be one-to-one and cannot chain or cycle")
        return value

    @field_validator("fields")
    @classmethod
    def custom_fields(cls, value: dict[str, str]) -> dict[str, str]:
        if any(not re.fullmatch(r"customfield_[0-9]+", v) for v in value.values()):
            raise ValueError("field mappings must be customfield_N identifiers")
        return value


def inside(root: Path, relative: str) -> Path:
    root = root.resolve()
    path = root / relative
    if Path(relative).is_absolute() or ".." in Path(relative).parts:
        raise Conflict("path must be relative and cannot traverse parents")
    if not path.resolve().is_relative_to(root):
        raise Conflict("path escapes project root")
    for parent in [path, *path.parents]:
        if parent == root:
            break
        if parent.is_symlink():
            raise Conflict("symlinked inputs are not supported")
    return path


def read_yaml(path: Path) -> Any:
    return yaml.safe_load(path.read_text(encoding="utf-8"))


def write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_suffix(path.suffix + ".tmp")
    temp.write_text(json.dumps(value, indent=2, default=str) + "\n", encoding="utf-8")
    temp.replace(path)


def label(identity: str) -> str:
    return "atl-" + digest(identity)[:32]
