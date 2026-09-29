# Validation status

This is a development preview, not a production or live-tenant sign-off.

Automated verification covers the deterministic runtime with stateful REST fakes, malformed
inputs, ownership conflicts, concurrency, retry behavior, and package installation. CI runs
Python 3.11/3.14 and Spec Kit 1.0.8/1.0.10 installation checks.

Local evidence (2026-09-26):

- 33 standalone tests passed on Python 3.14; lint, formatting and mypy passed.
- Two additional cross-package scenarios passed with both installation orders and no-op resync.
- Wheel, source distribution and allowlisted extension archive built successfully.
- Clean Spec Kit 1.0.8 and 1.0.10 installation and command bootstrap passed.
- Shared protocol runtime source matches between both integration packages.
- The optional cross-package test is skipped in standalone CI unless the peer is on PYTHONPATH.

Pending external acceptance:

- Connect a sandbox Jira project and Confluence space and provision API credentials.
- Run authenticated discovery and verify actual field/transition compatibility.
- Publish and visually inspect the feature page, Expand sections, Jira macro, and links.
- Verify the live status/decision round trip, repeated no-op sync, and simultaneous publishers.
- Exercise the installed consumer workflow and permissions in the target repository.

Until those checks pass, do not describe the integration as deployed, production-ready,
or eligible for community release. Draft PRs are for review of the tested local implementation.
