# Validation status

This is a development preview, not a production or live-tenant sign-off.

Automated verification covers the deterministic runtime with stateful REST fakes, malformed
inputs, ownership conflicts, concurrency, retry behavior, and package installation. CI runs
Python 3.11/3.14 and Spec Kit 1.0.8/1.0.10 installation checks.

Local evidence (2026-10-02, constitution governance 0.2.0):

- 65 standalone tests cover publication, live approval gates, amendment pauses, withdrawal,
  source races, altered/unauthorized evidence, partial-write recovery and narrow PR exemptions.
- Two independent companion-publisher scenarios check both creation orders and no-op resync.
- Lint, formatting, mypy and wheel/source/extension/preset builds passed locally.
- Clean Spec Kit 1.0.8 and 1.0.10 installation registers the extension and all five guards.
- The generated tasks command contains both the constitution and Agentstandards core gate.
- CI now runs the companion test against its immutable merged commit on Python 3.11/3.14.
- Shared feature protocol runtime source remains unchanged.

Pending external acceptance:

- Connect a sandbox Jira project and Confluence space and provision API credentials.
- Run authenticated discovery and verify actual field/transition compatibility.
- Publish and visually inspect the feature page, Expand sections, Jira macro, and links.
- Verify the live status/decision round trip, repeated no-op sync, and simultaneous publishers.
- Exercise the installed consumer workflow and permissions in the target repository.
- Verify constitution-only publication, approval before merge, project-wide amendment pause,
  authorized withdrawal, the Git audit PR, and the required constitution/approved check.
- Only then enable governance enforcement in an existing consumer project.

Until those checks pass, do not describe the integration as deployed, production-ready,
or eligible for community release. Draft PRs are for review of the tested local implementation.
