# Spec Kit Atlassian

Publish Spec Kit specifications, plans, and every implementation task into **the existing
Jira project board and one Confluence feature page**. Git owns definitions; Jira owns
assignment, scheduling, and delivery state. Accordion sections keep the complete plan
readable without creating a tree of document pages.

This is an independent MIT-licensed Spec Kit community extension. It does not require
Agentstandards or the Agentstandards Atlassian companion. When both are installed they
reuse feature identities, Epics, and pages without overwriting each other's sections.

**Status: development preview.** Local automated verification is described in
[VALIDATION.md](docs/VALIDATION.md). A real Atlassian sandbox round trip is required
before production use or community release. No live tenant has been configured by this repository.

## Behavior

- One Epic per feature, available before tasks exist; one standard Task per task ID.
- Existing project workflow and board, with story, phase, parallel marker, and dependency context.
- Complete Markdown documents rendered into native Confluence expandable sections.
- Source-revision links, project constitution publishing, and explicitly selected attachments.
- Durable external identity, shared page binding, no-op detection, and conflict detection.
- Jira completion reconciled into a draft GitHub PR; local completion is a proposal, never an
  unconditional instruction to mark a Jira task Done.
- Removed tasks retain their issues and history. Explicit task-ID migration maps handle renumbering.
- Remote writes run in one serialized GitHub Actions worker; CLI previews are read-only.

## Install and configure

Review a release/archive before installing. During development:

```sh
specify extension add --dev /path/to/spec-kit-atlassian
uv run --script .specify/extensions/atlassian/scripts/run.py --help
```

From the consuming project's feature branch:

```sh
uv run --script .specify/extensions/atlassian/scripts/run.py --project . init \
  --feature specs/001-feature --repository OWNER/REPOSITORY \
  --site https://YOUR-SITE.atlassian.net --jira-project PROJECT --space-id SPACE_ID
```

The command writes configuration and a stable feature binding under
`.specify/integrations/atlassian/`. Review and commit these files. Publish the registration
and workflow on the trusted default branch, and carry the same configuration into the feature
branch before synchronization. The initialization output reports the binding path used below.

```sh
uv run --script .specify/extensions/atlassian/scripts/run.py --project . preview \
  --binding .specify/integrations/atlassian/features/FEATURE_ID.json --offline
uv run --script .specify/extensions/atlassian/scripts/run.py --project . doctor \
  --binding .specify/integrations/atlassian/features/FEATURE_ID.json
uv run --script .specify/extensions/atlassian/scripts/run.py --project . sync \
  --binding .specify/integrations/atlassian/features/FEATURE_ID.json
```

`sync` dispatches the configured workflow; it does not claim that publication has completed.
Use the workflow result and generated binding PR to confirm publication. `status` reads the
local last-run cache; GitHub Actions logs are authoritative for dispatched runs.

See [onboarding](docs/ONBOARDING.md), [shared contract](docs/CONTRACT.md), and
[implementation and acceptance plan](docs/IMPLEMENTATION.md).

## Development

```sh
uv sync --extra test --locked
uv run ruff check .
uv run ruff format --check .
uv run mypy src
uv run python -m pytest
uv build
uv run python scripts/build_release.py
bash scripts/verify_install.sh
```

Python 3.11 and 3.14 are the CI targets. Spec Kit installation checks target 1.0.8 and 1.0.10.
Development imports can use `PYTHONPATH=src`; wheels and extension archives are tested independently.

Upgrade/reinstall replaces extension code only. Uninstall removes commands, not the configuration,
Jira work items, Confluence content, or Git evidence. Disable the consumer workflow to stop writes.
