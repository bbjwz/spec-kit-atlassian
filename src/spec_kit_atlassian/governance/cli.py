from __future__ import annotations

from pathlib import Path

from ..common.git import run
from ..common.http import Cloud
from ..common.models import Conflict, inside
from ..common.runtime import require_worker
from .project import CONSTITUTION, PROJECT, fingerprint, initialize, trusted
from .service import Service


def arguments(sub):
    init = sub.add_parser("init-project", help="Register governance before any feature exists")
    for flag in ("repository", "site", "jira-project", "space-id", "review-branch"):
        init.add_argument("--" + flag, required=True)
    init.add_argument("--approver", action="append", required=True)
    init.add_argument("--page-id")
    constitution = sub.add_parser("constitution")
    commands = constitution.add_subparsers(dest="operation", required=True)
    for name in ("preview", "publish", "status", "gate", "reconcile", "pull-approved"):
        command = commands.add_parser(name)
        if name in ("publish", "reconcile"):
            command.add_argument("--apply", action="store_true")
        if name == "gate":
            command.add_argument(
                "--if-enabled", action="store_true", help="Respect staged rollout configuration"
            )
        if name == "status":
            command.add_argument("--after-edit", action="store_true")
        if name == "preview":
            command.add_argument("--offline", action="store_true")


def feature_gate(root: Path) -> dict | None:
    # Legacy installations opt in through init-project. Once configured, enablement
    # is read from the trusted default branch, never from a feature's editable JSON.
    if not inside(root, PROJECT).exists():
        # A feature cannot bypass registered enforcement by deleting project.json:
        # query trusted registration through the shared config when present.
        import yaml

        from ..common.models import Settings
        from ..common.runtime import CONFIG
        from .project import GitHub

        cfg = Settings.model_validate(yaml.safe_load(inside(root, CONFIG).read_text()))
        actual = run(
            ["gh", "repo", "view", "--json", "nameWithOwner", "--jq", ".nameWithOwner"], root
        )
        if actual.lower() != cfg.github_repository.lower():
            raise Conflict("local governance target does not match this Git repository")
        gh = GitHub(cfg.github_repository)
        # Listing names distinguishes an absent registration from a failed read.
        info = gh.info()
        files = gh.api(
            "contents/.specify/integrations/atlassian?ref=" + gh.tip(info["default_branch"])
        )
        if not any(f["name"] == "project.json" for f in files):
            return None
    cfg, project, github = trusted(root)
    if not project.enforcement_enabled:
        return {"state": "staging", "enforced": False}
    return Service(cfg, project, github, Cloud(cfg)).gate(root)


def execute(args):
    root = args.project.resolve()
    if args.command == "init-project":
        return initialize(
            root,
            args.repository,
            args.site,
            args.jira_project,
            args.space_id,
            args.review_branch,
            args.approver,
            args.page_id,
        )
    if args.operation == "preview" and args.offline:
        path = inside(root, CONSTITUTION)
        from ..common.security import scan

        text = path.read_text()
        scan(text)
        return {
            "state": "local_preview",
            "content_digest": fingerprint(text),
            "remote_conflicts_checked": False,
            "approval_verified": False,
        }
    if args.operation == "gate" and args.if_enabled:
        return feature_gate(root) or {"state": "not_configured", "enforced": False}
    cfg, project, github = trusted(root)
    if args.operation in ("publish", "reconcile") and not args.apply:
        workflow = "constitution-governance.yml"
        default = github.info()["default_branch"]
        github.api(f"actions/workflows/{workflow}/dispatches", {"ref": default})
        return {"state": "dispatched", "branch": project.review_branch, "workflow": workflow}
    if args.operation == "status" and args.after_edit:
        local = inside(root, CONSTITUTION).read_text()
        if run(["git", "status", "--porcelain", "--", CONSTITUTION], root):
            return {
                "state": "local_changes",
                "next": "Commit and push the constitution review branch.",
            }
        candidate = github.candidate(project)
        if fingerprint(local) != candidate.content_digest:
            return {"state": "awaiting_push", "next": "Push the registered review branch."}
    service = Service(cfg, project, github, Cloud(cfg))
    if args.operation == "preview":
        return service.preview()
    if args.operation == "gate":
        return service.gate(root)
    if args.operation in ("publish", "reconcile"):
        require_worker()
        result = service.publish()
        if args.operation == "reconcile":
            from .worker import audit

            result["audit_pull_request"] = audit(root, service, result)
        return result
    if args.operation == "pull-approved":
        result = service.status()
        if not result["gate"]:
            raise Conflict("no active approved constitution: " + result["state"])
        path = inside(root, CONSTITUTION)
        # Explicit command permits updating a tracked clean file, never losing local edits.
        if path.exists():
            run(["git", "ls-files", "--error-unmatch", "--", CONSTITUTION], root)
        if run(["git", "status", "--porcelain", "--", CONSTITUTION], root):
            raise Conflict("constitution has local changes; commit or preserve them first")
        text = github.text(CONSTITUTION, result["active_source"])
        after = service.status()
        if not after["gate"] or after["active_review"] != result["active_review"]:
            raise Conflict("approval changed while copying constitution")
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text)
        return {
            "state": "copied_approved",
            "path": CONSTITUTION,
            "source_revision": result["active_source"],
            "next": "Review and commit the file.",
        }
    return service.status()
