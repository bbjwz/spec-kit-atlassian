## Mandatory constitution gate

Before creating branches or modifying any feature artifacts, run:

```text
uv run --script .specify/extensions/atlassian/scripts/run.py --project . constitution gate --if-enabled
```

On a nonzero exit, stop and report the blocker. Do not execute the command below. Preserve all other extension hooks and wrappers, including Agentstandards. On success continue:

{CORE_TEMPLATE}
