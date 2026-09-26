from __future__ import annotations

import argparse
import json
from pathlib import Path

from .common.confluence import Confluence, merge_sections
from .common.git import dispatch, draft_change, revision, run
from .common.http import Cloud
from .common.jira import Jira
from .common.models import Conflict
from .common.runtime import (
    adopt,
    cache_path,
    change_branch,
    doctor,
    initialize,
    load,
    require_worker,
    restore_binding,
    save_status,
)
from .engine import OWNER, reconcile_tasks, synchronize
from .specs import build_sections, load_feature, preview_feature


def parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description="Spec Kit → Jira/Confluence Cloud")
    p.add_argument("--project", type=Path, default=Path.cwd())
    sub = p.add_subparsers(dest="command", required=True)
    init = sub.add_parser("init")
    for flag in ("feature", "repository", "site", "jira-project", "space-id"):
        init.add_argument("--" + flag, required=True)
    for name in ("doctor", "preview", "sync", "reconcile", "status", "adopt"):
        cmd = sub.add_parser(name)
        cmd.add_argument("--binding", required=True)
        if name == "preview":
            cmd.add_argument("--offline", action="store_true")
        if name in ("sync", "reconcile", "adopt"):
            cmd.add_argument("--apply", action="store_true", help="worker-only remote write")
            cmd.add_argument("--base", default=None, help="feature branch for a draft PR")
        if name == "adopt":
            cmd.add_argument("--epic", required=True)
            cmd.add_argument("--page", required=True)
    return p


def execute(args) -> dict:
    root = args.project.resolve()
    if args.command == "init":
        return initialize(
            root, args.feature, args.repository, args.site, args.jira_project, args.space_id, OWNER
        )
    cfg, binding = load(root, args.binding)
    original_binding = binding.model_dump(mode="json")
    binding = restore_binding(binding, OWNER)
    if args.command == "status":
        path = cache_path(binding, OWNER)
        return json.loads(path.read_text()) if path.exists() else {"state": "never synchronized"}
    if args.command == "doctor":
        return doctor(cfg, binding, Cloud(cfg))
    sha = revision(root, require_clean=args.command in ("sync", "reconcile", "adopt"))
    if args.command in ("sync", "reconcile") and not args.apply:
        dispatch(
            cfg.github_repository,
            cfg.workflow,
            cfg.runtime_ref,
            args.binding,
            OWNER,
            args.command,
            sha,
        )
        return {"state": "dispatched", "source_revision": sha}
    feature = load_feature(root, binding)
    if args.command == "preview" and args.offline:
        return {"offline": True, "remote_conflicts_checked": False, **preview_feature(feature)}
    cloud = Cloud(cfg)
    jira = Jira(cloud, cfg)
    if args.command == "preview":
        existing = jira.find(binding.identity + ":feature")
        page = binding.page_id
        actions = [{"kind": "feature", "action": "update/adopt" if existing else "create"}]
        for task in feature["tasks"]:
            found = jira.find(binding.identity + ":task:" + task.id)
            actions.append({"task_id": task.id, "action": "reconcile" if found else "create"})
        if page:
            confluence = Confluence(cloud, cfg)
            data, props = confluence.get(page), confluence.properties(page)
            merge_sections(
                data["body"]["storage"]["value"],
                binding.identity,
                OWNER,
                build_sections(feature, binding, cfg, sha, binding.epic_key),
                props.get(f"atlassian.integration.sections.v1.{OWNER}", {}).get("value", {}),
            )
        return {"source_revision": sha, "actions": actions, **preview_feature(feature)}
    if args.command == "adopt" and not args.apply:
        return {
            "proposed_binding": {"epic": args.epic, "page": args.page},
            "note": "run in serialized worker with --apply to adopt existing containers",
        }
    require_worker()
    try:
        if args.command == "sync":
            result = synchronize(
                root, cfg, binding, cloud, lambda b: save_status(b, OWNER, {"state": "partial"})
            )
            save_status(binding, OWNER, {"state": "synchronized", **result})
            changes = {}
            if binding.model_dump(mode="json") != original_binding:
                changes[args.binding] = json.dumps(binding.model_dump(mode="json"), indent=2) + "\n"
        elif args.command == "adopt":
            result = adopt(cfg, binding, cloud, args.epic, args.page)
            changes = {args.binding: json.dumps(result, indent=2) + "\n"}
        else:
            changes = reconcile_tasks(root, cfg, binding, jira)
            result = {"changed_files": list(changes)}
        if changes:
            base = args.base or run(["git", "branch", "--show-current"], root)
            if not base:
                raise Conflict("--base is required for detached feature checkouts")
            result["pull_request"] = draft_change(
                root,
                cfg.github_repository,
                base,
                change_branch(args.command, binding, changes),
                changes,
                "Record Atlassian integration updates",
                "Updates feature bindings or Jira-owned completion. Review before merging.",
            )
        return result
    except Exception:
        save_status(
            binding,
            OWNER,
            {
                "state": "failed",
                "source_revision": sha,
                "note": "inspect the failed worker; remote writes may be partial",
            },
        )
        raise


def main() -> int:
    args = parser().parse_args()
    try:
        result = execute(args)
        print(json.dumps(result, indent=2, default=str))
        return 1 if result.get("ok") is False else 0
    except (Conflict, ValueError, OSError, RuntimeError) as exc:
        print(json.dumps({"error": str(exc)}))
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
