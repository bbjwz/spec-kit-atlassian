# Atlassian feature contract, version 1.0

This contract is mirrored in both independent integration repositories. It does not require
a third service or package. The `common/` source and `tests/fixtures/binding-v1.json` must match;
cross-installation tests verify that either publisher can operate first.

## Binding

A committed binding records `schema_version`, numeric GitHub `repository_id`, immutable
`feature_id` UUID, repository-relative `feature_path`, and optional `epic_key` and `page_id`.
Feature identity is `repository_id:feature_id`. Reuse it after a folder/title rename.
Initial remote IDs are saved to a draft binding PR and an ignored local cache. Merge the binding
PR before moving execution to another workstation. The Epic's shared page property also lets
both integrations recover the same page before that PR is merged.

Jira identities use deterministic hashed labels plus an ownership property. Labels alone never
establish ownership. Ambiguous identity matches fail. Jira issues and Confluence pages can be
explicitly adopted through `adopt`; ordinary sync never claims unrelated existing content.
The feature Epic is a shared container. Its first publisher owns its generated summary; the
other publisher links its records without overwriting that summary.

## Field and page ownership

Git defines specification/plan/task content. Jira owns assignment, priority, scheduling, and
execution status. Managed task descriptions are regenerated; human edits to those descriptions
cause a conflict instead of being discarded. Use Jira comments for human discussion.

Confluence uses deterministic macro IDs per feature, owner, and section. A namespaced page
property stores canonical section hashes. Writers read the current page and version, replace
only their owned sections, and retry version conflicts by rereading. Missing or edited managed
sections stop publication. If the page update succeeded but hash persistence failed, an identical
retry can recover. Nonmanaged content and the other publisher's sections are retained.

Spec Kit owns specification, implementation plan, research, data model, contracts, quickstart,
and delivery sections. Agentstandards owns council progress, review evidence, decisions,
master plan, validation, and run history. Human notes belong to neither integration.

No owner deletes historical sections implicitly. Page-size overflow requires explicit archival.
The integrations never rewrite source documents from human Confluence edits.

## Execution and trust

Consumer workflows serialize both publishers under the same repository concurrency group.
They install immutable integration commits and read registered feature revisions as data.
Configuration and allowed feature branches come from the trusted default branch. A dispatch
for an outdated feature tip is refused. Fork PRs do not receive Atlassian credentials.

Use Jira Cloud REST v3 enhanced search and Confluence Cloud REST v2 pages/properties; attachment
uploads use Confluence's supported v1 attachment endpoint. Credentials are environment references.
Redirects are not followed. Read retries and rate-limit retries are bounded; ambiguous creates
are reconciled by identity, never blindly repeated within a request.

A failed run can have partial remote writes. Inspect its result and preview again before retrying.
If a create result is unknown and its identity cannot be found, inspect Jira/Confluence before
retrying from another machine. Cached state is not a distributed transaction or an API idempotency key.
