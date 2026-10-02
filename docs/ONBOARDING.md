# Sandbox onboarding and operating guide

1. Select an existing Jira Cloud project and its board plus a Confluence Cloud space. Obtain
   the numeric space ID from the Confluence API. Start in a sandbox with representative workflows.
2. First complete [constitution governance setup](CONSTITUTION.md), including its staged rollout.
   Then initialize features from a named feature branch. Review configuration/binding changes and establish them
   on the trusted default branch. Keep `feature_refs` pointing at each active feature branch.
3. Map Jira issue types to existing non-subtask types. Default: Epic for features and Task for
   implementation, council, and decisions. No new board or issue hierarchy is created.
4. Map status names to actual available transitions. Add Waiting for input and Blocked if needed,
   or map them to your existing equivalents. `doctor` reports readable metadata and missing
   permissions; a real write test is still needed to verify Confluence write access.
5. Map optional custom text fields for `phase`, `story`, `parallel`, `council_phase`, and
   `architecture_outcome`. Fields in this release are text fields, not select-list objects.
   Add those fields to the relevant create/edit screens. The board filter must include the project
   and these issue types. Add native quick filters such as:

   - All implementation: `labels = "spec-kit-atlassian"`
   - Architecture: `labels = "agentstandards-atlassian"`
   - Needs my input: `status = "Waiting for input"`
   - Blocked: `status = "Blocked"`

   Board administration remains an explicit tenant setup step, not a guessed API mutation.
6. Configure `ATLASSIAN_EMAIL` and `ATLASSIAN_API_TOKEN` in your local environment and the
   consumer GitHub environment `atlassian-sandbox`. Do not paste values into prompts, configuration,
   command arguments, or Git. Standard API tokens use the tenant origin. Scoped API tokens require
   `auth_mode: scoped-token` and the tenant `cloud_id`, routing through `api.atlassian.com/ex/...`.
7. Copy `examples/consumer/.github/workflows/atlassian-sync.yml` into the consuming repository.
   Set each enabled integration's Actions variable (`SPECKIT_ATLASSIAN_REF` or
   `AGENTSTANDARDS_ATLASSIAN_REF`) to a reviewed full commit SHA. Enable Actions to create PRs.
   A fine-grained token or GitHub App may be substituted for `GITHUB_TOKEN` if the organization's
   policy requires it. Ordinary GITHUB_TOKEN-created PRs may need explicit CI dispatch because
   token-generated events do not automatically trigger other workflows.
8. Run offline preview, authenticated preview, then doctor. Dispatch one feature's first sync.
   Read the result, follow every created link, and merge the generated binding PR after review.
9. Run the same sync again: verify no duplicate items or unnecessary page versions. Test a human
   note, task completion, task removal, and a concurrent page edit in the sandbox.
10. Enable scheduled reconciliation only after the sandbox evidence is recorded. Scheduled runs
    poll registered feature tips every 15 minutes; GitHub may delay scheduled execution. Manual
    CLI dispatch handles publication immediately after pushing a feature revision.

## Existing feature pages and Epics

Use `adopt --binding PATH --epic PROJECT-123 --page PAGE_ID` to preview an existing-container
binding. The same command with `--apply` must run in the serialized worker environment; this
adds identity metadata and preserves existing descriptions/page content. Review its binding PR.
Only containers in the configured Jira project and Confluence space can be adopted.

## Confluence layout

One feature page contains visible summaries and native Expand sections. The source documents
remain authoritative; discussion belongs in comments or the Human notes section. The integration
requires Confluence's native Jira macro and a configured Jira/Confluence connection for live task
lists. If the macro cannot resolve the Jira site, configure that native connection first.

Constitution content appears once on the project governance page, published by its own worker.
Feature synchronization only links to that page and shows its current gate state. Explicit `attachments` must be files
under the selected feature directory; council/transcript folders are excluded by Spec Kit's
publisher. There is a 10 MB attachment budget and configurable page-size budget. Review binary
attachments separately; automated text secret detection is not a confidentiality classifier.

## Recovery and removal

Do not manually remove identity labels, page ownership properties, or managed section IDs.
For manual edits inside generated sections, move the edits into Git or Human notes before retrying.
Do not clear hashes to force an overwrite. An unknown create result requires identity inspection.
Reinstallation leaves `.specify/integrations/atlassian/` unchanged. Disabling the workflow stops
remote writes; uninstalling an extension does not delete remote records or historical evidence.
