# Constitution Approval Gate

Install the `atlassian` extension first, then this preset with `specify preset add --dev PATH`.
It wraps specification, clarification, planning, tasks and implementation commands before core
execution. Every wrapper preserves `{CORE_TEMPLATE}`, including other wrappers and hooks.

The guard reads `enforcement_enabled` from trusted project registration. Staging is deliberately
not approval. Enable only after the sandbox acceptance in the extension governance guide.
Agentstandards' task wrapper remains in the chain and enforces its own READY gate independently.
