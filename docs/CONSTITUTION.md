# Constitution-first governance

A constitution can be published before a feature, Epic or `specs/` directory exists. Git authors
the document; Confluence displays it; an authorized Jira transition activates the exact reviewed
revision. A Git audit PR records the decision afterward and is not an approval prerequisite.

## Bootstrap and staged rollout

1. Install the extension and the `constitution-gate` preset. Build their archives with
   `uv run python scripts/build_release.py`. Install the unpacked extension with
   `specify extension add --dev /path/to/unpacked-extension` and the preset with
   `specify preset add --dev /path/to/unpacked-preset`. Do not install development caches.
2. Initialize project registration (no feature or constitution file is required yet):

   ```sh
   spec-kit-atlassian --project . init-project \
     --repository OWNER/REPO --site https://TENANT.atlassian.net \
     --jira-project PROJECT --space-id SPACE_ID \
     --review-branch codex/constitution --approver ATLASSIAN_ACCOUNT_ID
   ```

   Repeat `--approver` for additional authorized humans. One of them can approve. Supply
   `--page-id` to reuse a previously integration-owned project overview. An unrelated page
   without matching project identity is refused.
3. Review and commit the shared `config.yml` and separate `project.json` under
   `.specify/integrations/atlassian/` on the trusted default branch. Existing feature bindings
   and the Agentstandards shared settings schema are unchanged. `enforcement_enabled` starts
   **false**: upgrading does not silently halt an existing project.
4. Copy `constitution-governance.yml` and `constitution-signal.yml` from
   `examples/consumer/.github/workflows/` into the consumer. The governance workflow belongs
   on the default branch; the signal workflow must also be present on the registered review
   branch. Set `SPECKIT_ATLASSIAN_REF` to a reviewed full integration commit SHA. Configure
   `ATLASSIAN_EMAIL` and `ATLASSIAN_API_TOKEN` in the `atlassian-sandbox` GitHub environment.
   Restrict environment deployment to the default branch. Enable Actions to create PRs.
5. Map the project sidecar's four distinct workflow statuses to actual Jira transitions:
   `awaiting_status`, `approved_status`, `changes_status`, `withdrawn_status`. Defaults are
   **Awaiting approval**, **Approved**, **Changes requested**, **Withdrawn**. Review tasks use
   the existing shared `issue_types.decision` (Task by default), not Jira Service Management.
   The integration account must be able to transition a new/recovered review into the awaiting
   state. Restrict decision transitions to the configured humans in Jira where possible.
6. Create `.specify/memory/constitution.md`, preview it, and push its registered review branch.
   The unprivileged push signal wakes a default-branch worker. The worker reads the registered
   Git revision via API and never checks out or executes branch/fork code. Missing signals or
   bot-generated pushes are recovered by the scheduled worker; manual dispatch is available.
7. Complete the sandbox checklist below. Install both the hooks and the command-wrapping preset
   in every supported working environment. Then set `enforcement_enabled: true` in the trusted
   project registration and require the GitHub status **`constitution/approved`** in the default
   branch's protection/ruleset. Do not restrict that required status to an App until the emitted
   status's actor has been verified in your tenant. Policy/bootstrap changes still require review.

## Daily commands

Use `spec-kit-atlassian --project . constitution <operation>`:

| Operation | Behavior |
|---|---|
| `preview --offline` | Scan local source and report its digest; no approval claim or network writes. |
| `preview` | Inspect the registered candidate and current review; report intended sections without writes. |
| `publish` | Dispatch the trusted project worker; no feature binding is needed. |
| `status` | Read live publication/approval state. |
| `status --after-edit` | Report local changes, awaiting push, awaiting publication or approval. |
| `gate` | Strict live gate; exits nonzero unless local source matches an active approved constitution. |
| `gate --if-enabled` | Command-hook/feature-sync guard that respects staged rollout from trusted registration. |
| `reconcile` | Dispatch publication, decision reconciliation, audit PR and PR-status refresh. |
| `pull-approved` | Copy the active approved source into a clean local constitution file for review/commit. |

The extension exposes equivalent `speckit.atlassian.constitution-*` commands plus
`speckit.atlassian.init-project`. Saving a file alone never publishes it. `--apply` is reserved for
serialized worker execution. Read-only commands use local Atlassian/GitHub authentication;
credentials are not written to project files. Ordinary synchronization makes no paid model calls.

## Review behavior

- One governance page per GitHub repository, with visible status and accordion sections for
  active/proposed text, the comparison, review history and immutable review snapshots. Human notes
  are outside managed sections. The existing legacy constitution section is migrated in place.
- One Jira task per proposal revision on the existing board, without a parent feature Epic.
  Suggested filters: `labels = constitution-current` and `labels = constitution-review`.
  Older cards retain their decisions and receive `constitution-superseded`; their stored approval
  can only be restored by the explicit withdrawal rules below.
- Approval verifies the authorized human, Jira transition event, immutable Git source, exact
  published section and unchanged controlled Jira fields. Human notes may change without invalidating
  approval. Editing managed source/evidence or losing live access makes the gate fail closed.
- Jira approval unlocks matching branches immediately, even before the constitution or audit PR
  is merged. An older branch must use `pull-approved`, review and commit the updated constitution.
- A pushed amendment pauses **all** feature workflows. The gate compares the live registered
  branch with the last published review, so failed/delayed publication cannot reuse an old approval.
  Unrelated commits do not create new reviews. A force-rewind/reintroduced proposal is reviewed anew.
- Changes requested keeps the project paused. An authorized withdrawal restores the exact last
  approved revision/event, including after multiple unapproved amendments. A revoked/changed prior
  approval cannot be restored. With no previous approval, withdrawal never unlocks work.
- Local edits block that branch. Project-wide pause begins when the registered proposal is pushed.
  Other draft branches are not registered review proposals; forks must be imported into the trusted
  registered branch before publication. Keep that branch available even after its PR is merged.

The supported commands gate specification, clarification, planning, task generation and
implementation. Feature publication is also gated. The preset preserves other wrappers/hooks,
including Agentstandards' independent architecture gate. These controls cannot prevent manual
file editing or an operation already in progress; they check at entrypoints.

The GitHub worker refreshes all open PR heads. Constitution-only and narrowly allowlisted bootstrap
PRs can proceed; mixed feature changes cannot use those exemptions. Audit-only PRs are exempt only
when their content matches live Jira evidence. API failures or incomplete changed-file lists fail
closed. Required checks must be configured by a repository administrator; the template emits them
but does not change branch protection on installation.

Jira approval is immediately effective for a live gate invocation. Confluence status banners,
GitHub checks and audit PRs update on the next worker execution (scheduled every 15 minutes,
subject to GitHub scheduling delays). Use `reconcile` to expedite them. GitHub status checks are
point-in-time evidence, not continuous enforcement between runs.

## Recovery and acceptance

Repeated identical publication should not create records or Confluence versions. A failed create
is retried against the same review identity. A durable create intent prevents blind retry when
the Jira create outcome is unknown: inspect Jira and recover its recorded key before continuing. If publication was interrupted before its journal
was durable, retry returns the card to awaiting approval; any interim decision must be repeated.
Do not clear ownership hashes to force updates. Put edits into Git or Human notes instead.
History is bounded by the page budget and a 30 KB/100-review journal; reaching the budget stops
publication and requires explicit archival planning. Shared publishers use one repository lock.

Before enabling enforcement, record evidence for: constitution-only initialization; actual page
rendering and workflow transitions; approval before merge; old/local/new branch gates; failed
publication while an amendment is pending; changes requested and withdrawal; unauthorized/stale
approvals; manual notes; no-op sync; required PR check exemptions; and a generated audit PR.
Also verify the constitution and Agentstandards gates in the same generated tasks command.

Live acceptance is pending. Do not treat local tests, installation or CI as tenant sign-off.
