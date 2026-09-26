# Implementation and acceptance plan — Spec Kit Atlassian

## Deliverable

An independent community extension for one existing Jira board and one accordion-style
Confluence page per feature. No Agentstandards dependency, gate engine, Forge app, or hosted service.

## Milestones

1. Stable feature binding, safe file loading, task parsing, configuration, and read-only previews.
2. Epic and per-task publication, complete document rendering, native task lists, shared-page
   updates, source links, dependencies, and selected attachments.
3. Jira-owned completion reconciliation into draft PRs, removed-task history, renumbering mappings,
   ownership conflicts, and retry recovery.
4. Installable Spec Kit archive, Python distributions, trusted Actions worker, setup/runbook,
   shared contract fixtures, and clean-install verification.
5. Live sandbox acceptance and community submission after the verified implementation is merged.

## Acceptance evidence

Automated tests must cover standalone operation, before-task visibility, one card per task,
no-op repeat synchronization, input validation, task dependencies, removed tasks, completion
conflicts, human-edit preservation, ownership, and Confluence optimistic concurrency.
Both integrations must also operate in either installation order against the same test tenant.

Before release, record a live sandbox feature's Jira and Confluence URLs, verify rendering,
exercise a second no-op run, change Jira delivery status, review the resulting Git PR, and repeat
with a deliberate human edit and interrupted sync. Do not replace this acceptance with mocks.

## Publication

Verify lint, types, tests, package builds, and clean Spec Kit installs. Review diff and archives
for credentials, private data, and generated/unrelated files. Use codex/* branches and draft PRs.
Do not tag a release or submit the community extension until live acceptance is recorded.
