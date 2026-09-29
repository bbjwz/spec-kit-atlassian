from __future__ import annotations

import json
import re
import subprocess
from pathlib import Path

from .models import Conflict


def run(args: list[str], root: Path | None = None) -> str:
    result = subprocess.run(args, cwd=root, capture_output=True, text=True, check=False)
    if result.returncode:
        raise Conflict(f"{args[0]} command failed (exit {result.returncode}); inspect locally")
    return result.stdout.strip()


def revision(root: Path, require_clean: bool = False) -> str:
    if require_clean and run(["git", "status", "--porcelain", "--untracked-files=normal"], root):
        raise Conflict("publish only a clean committed project revision")
    return run(["git", "rev-parse", "HEAD"], root)


def tracked(root: Path, path: Path) -> None:
    run(["git", "ls-files", "--error-unmatch", "--", path.relative_to(root).as_posix()], root)


def dispatch(
    repository: str,
    workflow: str,
    ref: str,
    binding: str,
    owner: str,
    operation: str,
    source_sha: str,
    decision_key: str = "",
) -> None:
    payload = json.dumps(
        {
            "ref": ref,
            "inputs": {
                "binding": binding,
                "owner": owner,
                "operation": operation,
                "source_sha": source_sha,
                "decision_key": decision_key,
            },
        }
    )
    result = subprocess.run(
        [
            "gh",
            "api",
            "--method",
            "POST",
            f"repos/{repository}/actions/workflows/{workflow}/dispatches",
            "--input",
            "-",
        ],
        input=payload,
        capture_output=True,
        text=True,
    )
    if result.returncode:
        raise Conflict("GitHub workflow dispatch failed; verify workflow and credentials")


def draft_change(
    root: Path,
    repository: str,
    base: str,
    branch: str,
    changes: dict[str, str],
    title: str,
    body: str,
) -> str:
    """Create a focused branch/commit/PR via GitHub Git Data API, leaving the checkout alone."""

    def gh(method: str, path: str, payload: dict | None = None) -> dict:
        args = ["gh", "api", "--method", method, f"repos/{repository}/{path}"]
        if payload is not None:
            args += ["--input", "-"]
        result = subprocess.run(
            args,
            input=json.dumps(payload) if payload is not None else None,
            capture_output=True,
            text=True,
        )
        if result.returncode:
            raise Conflict("GitHub change publication failed; inspect branch before retrying")
        return json.loads(result.stdout or "{}")

    if not re.fullmatch(r"[A-Za-z0-9_./-]+", base) or ".." in base:
        raise Conflict("invalid base branch")
    if not branch.startswith("codex/"):
        raise Conflict("integration branches must start with codex/")
    # The content-derived branch name makes reconciliation PR creation idempotent.
    owner = repository.split("/")[0]
    result = subprocess.run(
        ["gh", "api", f"repos/{repository}/pulls?head={owner}:{branch}&state=open"],
        capture_output=True,
        text=True,
    )
    if result.returncode:
        raise Conflict("could not check existing reconciliation pull requests")
    existing = json.loads(result.stdout)
    if len(existing) > 1:
        raise Conflict("multiple reconciliation pull requests claim the same change")
    if existing:
        if existing[0]["base"]["ref"] != base:
            raise Conflict("existing reconciliation pull request targets another branch")
        return existing[0]["html_url"]
    revision(root, require_clean=True)
    base_sha = gh("GET", f"git/ref/heads/{base}")["object"]["sha"]
    if base_sha != revision(root):
        raise Conflict("target branch moved; refresh the feature checkout before reconciling")
    commit = gh("GET", f"git/commits/{base_sha}")
    tree = gh(
        "POST",
        "git/trees",
        {
            "base_tree": commit["tree"]["sha"],
            "tree": [
                {"path": path, "mode": "100644", "type": "blob", "content": content}
                for path, content in changes.items()
            ],
        },
    )
    new_commit = gh(
        "POST", "git/commits", {"message": title, "tree": tree["sha"], "parents": [base_sha]}
    )
    gh("POST", "git/refs", {"ref": f"refs/heads/{branch}", "sha": new_commit["sha"]})
    pr = gh(
        "POST", "pulls", {"head": branch, "base": base, "title": title, "body": body, "draft": True}
    )
    return pr["html_url"]
