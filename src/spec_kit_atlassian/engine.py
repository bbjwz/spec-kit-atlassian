from __future__ import annotations

from pathlib import Path

from .common.confluence import Confluence
from .common.git import revision
from .common.http import Cloud
from .common.jira import Jira
from .common.models import Binding, Conflict, Settings, inside
from .common.security import scan
from .specs import TASK, build_sections, load_feature, source_url

OWNER = "spec-kit-atlassian"


def synchronize(root: Path, cfg: Settings, binding: Binding, cloud: Cloud, save_binding) -> dict:
    from .governance.cli import feature_gate

    governance = feature_gate(root)
    sha = revision(root, require_clean=True)
    feature = load_feature(root, binding)
    for text in feature["documents"].values():
        scan(text)
    jira, confluence = Jira(cloud, cfg), Confluence(cloud, cfg)
    epic, action = jira.upsert(
        binding.identity + ":feature",
        OWNER,
        "feature",
        feature["title"],
        f"{feature['stage']}\n{source_url(cfg, sha, binding.feature_path + '/spec.md')}",
        binding.identity,
        adopt_key=binding.epic_key,
    )
    binding.epic_key = epic
    save_binding(binding)
    binding.page_id = jira.shared_page(epic, binding.identity, binding.page_id)
    page = confluence.ensure_page(
        binding.identity, binding.feature_path.rsplit("/", 1)[-1], binding.page_id
    )
    jira.shared_page(epic, binding.identity, page)
    binding.page_id = page
    save_binding(binding)
    existing = [
        (issue, jira.metadata(issue["key"])) for issue in jira.owned(binding.identity, OWNER)
    ]
    prior_tasks = {
        str(meta["task_id"]): (issue, meta)
        for issue, meta in existing
        if meta.get("kind") == "task"
    }
    ids = {task.id for task in feature["tasks"]}
    removed = {key: pair for key, pair in prior_tasks.items() if key not in ids}
    new = [task for task in feature["tasks"] if task.id not in prior_tasks]
    for task in new:
        if any(
            meta.get("task_fingerprint") == task.fingerprint
            and cfg.task_id_migrations.get(old_id) != task.id
            for old_id, (_, meta) in removed.items()
        ):
            raise Conflict("task appears renumbered; configure task_id_migrations explicitly")
    task_keys: dict[str, str] = {}
    operations: list[dict] = [{"key": epic, "action": action}]
    for task in feature["tasks"]:
        original_id = next(
            (old for old, new_id in cfg.task_id_migrations.items() if new_id == task.id), task.id
        )
        identity = binding.identity + ":task:" + original_id
        previous = jira.find(identity)
        current_status = previous["fields"]["status"]["name"] if previous else cfg.statuses["todo"]
        before = jira.metadata(previous["key"]) if previous else {}
        local_changed = before and before.get("source_done") != task.done
        remote_changed = before and before.get("jira_status_at_sync") != current_status
        if (
            local_changed
            and remote_changed
            and task.done != (current_status == cfg.statuses["done"])
        ):
            raise Conflict(f"both Git and Jira changed completion for {task.id}")
        completion_proposal = task.done and current_status != cfg.statuses["done"]
        extra = {}
        for name, value in {
            "phase": task.phase,
            "story": task.story or "",
            "parallel": str(task.parallel).lower(),
        }.items():
            if name in cfg.fields:
                extra[cfg.fields[name]] = value
        key, action = jira.upsert(
            identity,
            OWNER,
            "task",
            f"{task.id}: {task.text}",
            f"{task.text}\nPhase: {task.phase}\nStory: {task.story or 'none'}\n"
            f"Parallel marker: {task.parallel}\n"
            f"Dependencies: {', '.join(task.dependencies) or 'none'}\n"
            f"Source: {source_url(cfg, sha, binding.feature_path + '/tasks.md')}\n"
            f"Local completion proposed: {completion_proposal}",
            binding.identity,
            parent=epic,
            extra=extra,
            metadata={
                "task_id": task.id,
                "task_fingerprint": task.fingerprint,
                "source_done": task.done,
                "jira_status_at_sync": current_status,
                "removed": False,
            },
        )
        task_keys[task.id] = key
        operations.append(
            {"key": key, "action": action, "completion_proposal": completion_proposal}
        )
        jira.remote_link(
            key,
            f"{cfg.site}/wiki/spaces/{cfg.confluence_space_id}/pages/{page}",
            "Feature specification and plan",
        )
    for old_id, (_issue, meta) in removed.items():
        if cfg.task_id_migrations.get(old_id) in ids:
            continue
        if not meta.get("removed"):
            fields = meta["generated_fields"]
            key, action = jira.upsert(
                meta["identity"],
                OWNER,
                "task",
                "[Removed from plan] " + fields["summary"],
                "This task was removed from the current plan. Its Jira history is retained.",
                binding.identity,
                parent=epic,
                metadata={
                    k: v
                    for k, v in {**meta, "removed": True}.items()
                    if k
                    not in {
                        "generated_fields",
                        "owner",
                        "identity",
                        "kind",
                        "schema_version",
                        "feature_identity",
                    }
                },
            )
            operations.append({"key": key, "action": action})
    for task in feature["tasks"]:
        for dependency in task.dependencies:
            jira.link(task_keys[dependency], task_keys[task.id], "Blocks")
    import mimetypes

    for relative in cfg.attachments:
        if not relative.startswith(binding.feature_path + "/") or "/architecture/" in relative:
            raise Conflict("attachments must be selected feature documents, not council evidence")
        path = inside(root, relative)
        content = path.read_bytes()
        if len(content) > 10_000_000:
            raise Conflict("attachment exceeds the 10 MB integration budget")
        if path.suffix.lower() not in (".png", ".jpg", ".jpeg", ".pdf"):
            scan(content.decode("utf-8"))
        filename = "speckit-" + relative[len(binding.feature_path) + 1 :].replace("/", "__")
        confluence.attachment(
            page,
            filename,
            content,
            mimetypes.guess_type(path.name)[0] or "application/octet-stream",
        )
    sections = build_sections(feature, binding, cfg, sha, epic)
    if governance:
        from html import escape

        from .common.confluence import Section

        body = "<p>Constitution: " + escape(governance["state"]) + "</p>"
        if governance.get("page_url"):
            body += '<p><a href="' + escape(governance["page_url"]) + '">Project governance</a></p>'
        sections.append(Section("governance", "Constitution approval", body, True))
    page_action = confluence.update(page, binding.identity, OWNER, sections)
    jira.remote_link(
        epic,
        f"{cfg.site}/wiki/spaces/{cfg.confluence_space_id}/pages/{page}",
        "Feature specification and plan",
    )
    return {
        "source_revision": sha,
        "epic": epic,
        "page": page,
        "page_action": page_action,
        "issues": operations,
    }


def reconcile_tasks(root: Path, cfg: Settings, binding: Binding, jira: Jira) -> dict[str, str]:
    feature = load_feature(root, binding)
    text = feature["documents"].get("tasks.md", "")
    wanted = {}
    for task in feature["tasks"]:
        original = next(
            (old for old, new in cfg.task_id_migrations.items() if new == task.id), task.id
        )
        issue = jira.find(binding.identity + ":task:" + original)
        if not issue:
            raise Conflict(f"no Jira binding for {task.id}")
        meta = jira.metadata(issue["key"])
        status = issue["fields"]["status"]["name"]
        done = status == cfg.statuses["done"]
        if (
            task.done != meta.get("source_done")
            and status != meta.get("jira_status_at_sync")
            and task.done != done
        ):
            raise Conflict(f"completion conflict for {task.id}")
        wanted[task.id] = done
    lines, fence = [], None
    for line in text.splitlines(keepends=True):
        stripped = line.lstrip()
        if stripped.startswith(("```", "~~~")):
            marker = stripped[:3]
            fence = None if fence == marker else marker if fence is None else fence
        match = TASK.match(line) if not fence else None
        if match:
            start, end = match.span(1)
            line = line[:start] + ("x" if wanted[match.group(2)] else " ") + line[end:]
        lines.append(line)
    updated = "".join(lines)
    return {binding.feature_path + "/tasks.md": updated} if updated != text else {}
