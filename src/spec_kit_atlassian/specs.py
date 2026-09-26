from __future__ import annotations

import re
from dataclasses import asdict, dataclass
from html import escape
from pathlib import Path
from urllib.parse import quote

from .common.confluence import Section
from .common.models import Binding, Conflict, Settings, digest, inside
from .common.render import markdown

TASK = re.compile(r"^\s*-\s+\[([ xX])\]\s+(T\d+)\s+(.+)$")


@dataclass(frozen=True)
class Task:
    id: str
    text: str
    phase: str
    story: str | None
    parallel: bool
    done: bool
    dependencies: tuple[str, ...]

    @property
    def fingerprint(self) -> str:
        return digest({"text": self.text, "phase": self.phase, "story": self.story})


def parse_tasks(text: str) -> list[Task]:
    tasks, phase, fence = [], "", None
    for line in text.splitlines():
        stripped = line.lstrip()
        if stripped.startswith(("```", "~~~")):
            marker = stripped[:3]
            fence = None if fence == marker else marker if fence is None else fence
            continue
        if fence:
            continue
        if re.match(r"^#{1,6}\s", line):
            phase = line.lstrip("# ").strip()
        match = TASK.match(line)
        if not match:
            if re.match(r"^\s*-\s+\[[^]]*\]", line):
                raise Conflict("task checklist entry lacks a valid stable T-number ID")
            continue
        checked, task_id, description = match.groups()
        story = re.search(r"\[(US\d+)\]", description)
        dep = re.search(r"(?:depends on|dependencies:)\s*([^.;]+)", description, re.I)
        dependencies = tuple(re.findall(r"\bT\d+\b", dep.group(1))) if dep else ()
        tasks.append(
            Task(
                task_id,
                description,
                phase,
                story.group(1) if story else None,
                "[P]" in description,
                checked.lower() == "x",
                dependencies,
            )
        )
    ids = [task.id for task in tasks]
    if len(set(ids)) != len(ids):
        raise Conflict("duplicate task IDs in tasks.md")
    for task in tasks:
        if task.id in task.dependencies or not set(task.dependencies).issubset(ids):
            raise Conflict(f"invalid dependency for {task.id}")
    graph = {task.id: task.dependencies for task in tasks}

    def visit(node: str, path: set[str]) -> None:
        if node in path:
            raise Conflict("cyclic task dependencies")
        for dependency in graph[node]:
            visit(dependency, path | {node})

    for node in graph:
        visit(node, set())
    return tasks


def source_url(cfg: Settings, sha: str, path: str) -> str:
    return f"https://github.com/{cfg.github_repository}/blob/{sha}/{quote(path, safe='/')}"


def load_feature(root: Path, binding: Binding) -> dict:
    feature = inside(root, binding.feature_path)
    spec_path = inside(root, f"{binding.feature_path}/spec.md")
    spec = spec_path.read_text(encoding="utf-8")
    title_match = re.search(r"^#\s+(.+)$", spec, re.M)
    title = title_match.group(1) if title_match else feature.name
    title = re.sub(r"^Feature Specification:\s*", "", title)
    docs = {"spec.md": spec}
    for name in ("plan.md", "research.md", "data-model.md", "quickstart.md", "tasks.md"):
        path = inside(root, f"{binding.feature_path}/{name}")
        if path.exists():
            docs[name] = path.read_text(encoding="utf-8")
    contracts = inside(root, f"{binding.feature_path}/contracts")
    if contracts.is_dir():
        for path in sorted(contracts.rglob("*.md")):
            relative = path.relative_to(root).as_posix()
            docs[path.relative_to(feature).as_posix()] = inside(root, relative).read_text()
    tasks = parse_tasks(docs.get("tasks.md", ""))
    stage = (
        "Tasks prepared"
        if "tasks.md" in docs
        else ("Plan available" if "plan.md" in docs else "Specification available")
    )
    return {"title": title, "documents": docs, "tasks": tasks, "stage": stage}


def build_sections(
    feature: dict, binding: Binding, cfg: Settings, sha: str, epic_key: str | None
) -> list[Section]:
    url = escape(source_url(cfg, sha, binding.feature_path + "/spec.md"))
    links = f'<a href="{url}">Git source</a>'
    if epic_key:
        links += f' · <a href="{cfg.site}/browse/{epic_key}">Jira feature</a>'
    summary = (
        f"<p>{escape(feature['stage'])} · {len(feature['tasks'])} defined tasks</p>"
        f"<p>Published source revision: <code>{sha}</code></p><p>{links}</p>"
    )
    sections = [Section("delivery-summary", "Feature and delivery", summary, True)]
    titles = {
        "spec.md": "Specification and acceptance criteria",
        "plan.md": "Implementation plan",
        "research.md": "Research",
        "data-model.md": "Data model",
        "quickstart.md": "Quickstart",
    }
    for filename, title in titles.items():
        # Always include missing sections, preventing old plans being mistaken for current plans.
        text = feature["documents"].get(filename)
        sections.append(
            Section(
                filename,
                title,
                markdown(text, source_url(cfg, sha, binding.feature_path + "/" + filename))
                if text is not None
                else "<p>Not present in this source revision.</p>",
            )
        )
    contracts = "".join(
        f"<h3>{escape(name)}</h3>{markdown(text)}"
        for name, text in feature["documents"].items()
        if name.startswith("contracts/")
    )
    sections.append(Section("contracts", "Contracts", contracts or "<p>No Markdown contracts.</p>"))
    if epic_key:
        jql = f'parent = "{epic_key}" AND labels = "spec-kit-atlassian"'
        tasks = (
            '<ac:structured-macro ac:name="jira" ac:schema-version="1">'
            f'<ac:parameter ac:name="jqlQuery">{escape(jql)}</ac:parameter>'
            '<ac:parameter ac:name="columns">key,summary,status,assignee</ac:parameter>'
            "</ac:structured-macro>"
        )
    else:
        tasks = "<p>Jira feature has not been published yet.</p>"
    sections.append(Section("delivery-tasks", "Tasks and delivery progress", tasks))
    return sections


def preview_feature(feature: dict) -> dict:
    return {
        "title": feature["title"],
        "stage": feature["stage"],
        "documents": list(feature["documents"]),
        "tasks": [asdict(t) for t in feature["tasks"]],
    }
