"""Trusted project worker: repository content is API data, never executable code."""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path

from ..common.git import draft_change
from ..common.http import Cloud
from ..common.models import Conflict, digest, inside
from ..common.runtime import CONFIG
from .project import CONSTITUTION, PROJECT, trusted
from .service import Service

CHECK = "constitution/approved"
BOOTSTRAP = {
    CONFIG,
    PROJECT,
    ".github/workflows/atlassian-sync.yml",
    ".github/workflows/constitution-governance.yml",
    ".github/workflows/constitution-signal.yml",
}


def classification(files: list[dict], count: int) -> str:
    if len(files) != count or not files:
        raise Conflict("PR changed-file listing is incomplete")
    paths = {f["filename"] for f in files}
    # Renames or deletions must not turn a mixed change into an exempt PR.
    if any(f["status"] in ("removed", "renamed") for f in files):
        return "feature"
    if paths == {CONSTITUTION}:
        return "constitution"
    if paths.issubset(BOOTSTRAP):
        return "bootstrap"
    if all(
        p.startswith(".specify/integrations/atlassian/constitution/decisions/")
        and p.endswith(".json")
        for p in paths
    ):
        return "audit"
    return "feature"


def refresh_checks(service: Service):
    github = service.github
    for listing in github.pages("pulls?state=open"):
        pr = github.api(f"pulls/{listing['number']}")
        sha = pr["head"]["sha"]
        try:
            files = list(github.pages(f"pulls/{pr['number']}/files"))
            kind = classification(files, pr["changed_files"])
            if kind == "audit":
                page = service.page()
                journal, _ = service.journal(page)
                for file in files:
                    payload = json.loads(github.text(file["filename"], sha))
                    review = next(
                        (r for r in journal.reviews if r.id == payload.get("review_id")), None
                    )
                    if review is None or service.decision(review, page, journal) != payload:
                        raise Conflict("audit PR does not match live Jira evidence")
                    expected = (
                        ".specify/integrations/atlassian/constitution/decisions/"
                        f"{review.id}-{payload['event_id']}.json"
                    )
                    if file["filename"] != expected:
                        raise Conflict("audit path does not match approval evidence")
            if kind in ("constitution", "bootstrap", "audit"):
                state, message = "success", f"{kind} change only; feature work is not exempt"
            elif not service.project.enforcement_enabled:
                state, message = "success", "Governance staging: enforcement not enabled"
            else:
                result = service.status(github.text(CONSTITUTION, sha))
                state = "success" if result["gate"] else "failure"
                message = "Constitution: " + result["state"]
        except (Conflict, ValueError, OSError, RuntimeError):
            state, message = (
                "failure",
                "Cannot verify current constitution approval; inspect governance worker",
            )
        github.api(
            f"statuses/{sha}", {"state": state, "context": CHECK, "description": message[:140]}
        )


def audit(root: Path, service: Service, result: dict) -> str | None:
    evidence = result.get("review_decision", result.get("decision", {}))
    if "event_id" not in evidence:
        return None
    relative = (
        ".specify/integrations/atlassian/constitution/decisions/"
        + evidence["review_id"]
        + "-"
        + evidence["event_id"]
        + ".json"
    )
    text = json.dumps(evidence, indent=2, sort_keys=True) + "\n"
    path = inside(root, relative)
    if path.exists() and path.read_text() == text:
        return None
    info = service.github.info()
    return draft_change(
        root,
        service.cfg.github_repository,
        info["default_branch"],
        "codex/constitution-audit-" + digest(evidence)[:16],
        {relative: text},
        "Record Jira constitution decision",
        "Records live Jira evidence. Approval activation does not depend on merging this audit PR.",
    )


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--project", type=Path, default=Path.cwd())
    args = parser.parse_args()
    os.environ["ATLASSIAN_INTEGRATION_WORKER"] = "1"
    try:
        cfg, project, github = trusted(args.project)
        service = Service(cfg, project, github, Cloud(cfg))
        refresh_checks(service)
        try:
            result = service.publish()
            result["audit_pull_request"] = audit(args.project, service, result)
        finally:
            refresh_checks(service)
        print(json.dumps(result, indent=2))
        return 0
    except (Conflict, ValueError, OSError, RuntimeError) as exc:
        print(json.dumps({"error": str(exc)}))
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
