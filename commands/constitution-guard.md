---
description: Require current Jira constitution approval before feature work
---

Run before any feature files or branches are created or changed:

```text
uv run --script .specify/extensions/atlassian/scripts/run.py --project . constitution gate --if-enabled
```

If this exits nonzero, stop immediately and report the reason. Never bypass it using cached approval or a Git audit PR. Staging/not-configured output is not approval; explain that enforcement has not been enabled. Continue only after a successful command.
