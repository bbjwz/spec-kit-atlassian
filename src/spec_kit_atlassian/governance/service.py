from __future__ import annotations

import difflib
from datetime import datetime
from html import escape
from pathlib import Path
from typing import Literal

from pydantic import Field

from ..common.confluence import (
    NS,
    Confluence,
    Section,
    document,
    merge_sections,
    node_hash,
    section_id,
)
from ..common.http import Cloud
from ..common.jira import Jira
from ..common.models import Conflict, Model, Settings, digest, inside
from ..common.render import markdown
from ..common.security import scan
from .project import CONSTITUTION, Candidate, GitHub, Project, fingerprint

OWNER = "spec-kit-atlassian"
LEDGER = "atlassian.constitution.reviews.v1"


class NotPublished(Conflict):
    pass


class Review(Model):
    id: str = Field(pattern=r"^[a-f0-9]{64}$")
    source: Candidate
    issue_key: str = Field(pattern=r"^[A-Z][A-Z0-9_]*-[0-9]+$")
    previous_id: str | None = None
    previous_event: str | None = None


class Journal(Model):
    schema_version: Literal["1.0"] = "1.0"
    reviews: list[Review] = Field(default_factory=list)


class Service:
    def __init__(self, cfg: Settings, project: Project, github: GitHub, cloud: Cloud):
        self.cfg, self.project, self.github = cfg, project, github
        self.jira, self.confluence = Jira(cloud, cfg), Confluence(cloud, cfg)

    def page(self, create=False) -> str:
        if create:
            return self.confluence.ensure_page(
                self.project.identity,
                self.cfg.github_repository,
                self.project.page_id or self.cfg.project_page_id,
            )
        if self.project.page_id or self.cfg.project_page_id:
            page = self.project.page_id or self.cfg.project_page_id
            assert page is not None
            # ensure_page with an explicit binding only reads and validates identity.
            return self.confluence.ensure_page(
                self.project.identity, self.cfg.github_repository, page
            )
        stable = f"{self.cfg.github_repository} [{digest(self.project.identity)[:10]}]"
        data = self.confluence.cloud.request(
            "confluence",
            "GET",
            "/wiki/api/v2/pages",
            params={
                "space-id": self.cfg.confluence_space_id,
                "title": stable,
                "status": "current",
                "limit": 2,
            },
        )
        if not data["results"]:
            raise NotPublished("constitution has not been published")
        if len(data["results"]) != 1 or data.get("_links", {}).get("next"):
            raise Conflict("constitution has not been published or its page is ambiguous")
        return self.confluence.ensure_page(
            self.project.identity, self.cfg.github_repository, str(data["results"][0]["id"])
        )

    def journal(self, page: str):
        prop = self.confluence.properties(page).get(LEDGER)
        return Journal.model_validate(prop["value"]) if prop else Journal(), prop

    def source(self, review: Review) -> Candidate:
        text = self.github.text(CONSTITUTION, review.source.commit)
        if fingerprint(text) != review.source.content_digest:
            raise Conflict("reviewed source does not match immutable Git evidence")
        return review.source.model_copy(update={"content": text})

    def rendered(self, source: Candidate) -> str:
        base = (
            f"https://github.com/{self.cfg.github_repository}/blob/{source.commit}/{CONSTITUTION}"
        )
        return markdown(source.content, base)

    def section(self, review: Review) -> Section:
        candidate = self.source(review)
        return Section(
            "constitution-review-" + review.id,
            "Constitution review " + review.id[:8],
            f"<p>Source: <code>{candidate.commit}</code> · "
            f"Digest: <code>{candidate.content_digest}</code></p>" + self.rendered(candidate),
        )

    def verify_section(self, page: str, review: Review):
        section = self.section(review)
        _, wanted = merge_sections("", self.project.identity, OWNER, [section], {})
        root = document(self.confluence.get(page)["body"]["storage"]["value"])
        nodes = root.xpath(
            ".//ac:structured-macro[@ac:macro-id=$id]",
            namespaces=NS,
            id=section_id(self.project.identity, OWNER, section.key),
        )
        if len(nodes) != 1 or node_hash(nodes[0]) != wanted[section.key]:
            raise Conflict("reviewed Confluence constitution was removed or altered")

    def fields(self, candidate: Candidate, page: str, review_id: str, previous: Review | None):
        comparison = (
            f"https://github.com/{self.cfg.github_repository}/compare/"
            f"{previous.source.commit}...{candidate.commit}"
            if previous
            else "Initial constitution"
        )
        return (
            f"Constitution Review: {self.cfg.github_repository} ({review_id[:8]})",
            "Review the published constitution, then use an authorized "
            "approval, change-request, or withdrawal transition.\n"
            f"Content digest: {candidate.content_digest}\nSource: https://github.com/{self.cfg.github_repository}/blob/{candidate.commit}/{CONSTITUTION}\n"
            f"Confluence: {self.cfg.site}/wiki/spaces/{self.cfg.confluence_space_id}/pages/{page}\n"
            f"Comparison: {comparison}\nReview identity: {review_id}",
        )

    def decision(self, review: Review, page: str, journal: Journal) -> dict:
        issue = self.jira.get(review.issue_key)
        meta = self.jira.metadata(review.issue_key)
        expected_identity = self.project.identity + ":constitution:" + review.id
        if (
            meta.get("identity") != expected_identity
            or meta.get("owner") != OWNER
            or meta.get("constitution_digest") != review.source.content_digest
        ):
            raise Conflict("constitution Jira ownership or digest changed")
        previous = next((r for r in journal.reviews if r.id == review.previous_id), None)
        summary, description = self.fields(review.source, page, review.id, previous)
        from ..common.render import adf

        fields = issue["fields"]
        if fields["summary"] != summary or fields["description"] != adf(description):
            raise Conflict("constitution Jira review content was edited")
        status = fields["status"]["name"]
        statuses = {
            self.project.approved_status: "approved",
            self.project.withdrawn_status: "withdrawn",
            self.project.changes_status: "changes_requested",
        }
        if status not in statuses:
            return {"state": "awaiting_approval"}
        history = self.jira.changelog(review.issue_key)

        def order(entry):
            return datetime.fromisoformat(entry["created"]), int(entry["id"])

        events = [
            e
            for e in history
            if any(i.get("field") == "status" and i.get("toString") == status for i in e["items"])
        ]
        if not events:
            raise Conflict("no auditable constitution decision transition")
        event = max(events, key=order)
        if (
            event["author"]["accountId"] not in self.project.approver_account_ids
            or event["author"].get("accountType") == "app"
        ):
            raise Conflict("constitution decision requires an authorized human")
        controlled = {"summary", "description", "status"}
        for entry in history:
            if order(entry) > order(event) and any(
                i.get("fieldId", i.get("field")) in controlled for i in entry["items"]
            ):
                raise Conflict("review changed after the decision; decide again")
        self.verify_section(page, review)
        if self.jira.get(review.issue_key)["fields"]["updated"] != fields["updated"]:
            raise Conflict("constitution decision changed during verification")
        return {
            "state": statuses[status],
            "account_id": event["author"]["accountId"],
            "event_id": event["id"],
            "decided_at": event["created"],
            "issue_key": review.issue_key,
            "review_id": review.id,
            "source_revision": review.source.commit,
            "content_digest": review.source.content_digest,
        }

    def effective(self, review: Review, page: str, journal: Journal):
        decision = self.decision(review, page, journal)
        if decision["state"] == "approved":
            return review, decision
        if decision["state"] == "withdrawn" and review.previous_id:
            return self.previous_approval(review, page, journal)
        return None, decision

    def previous_approval(self, review: Review, page: str, journal: Journal):
        previous = next((r for r in journal.reviews if r.id == review.previous_id), None)
        if previous is None:
            raise Conflict("previous constitution approval is missing")
        approved = self.decision(previous, page, journal)
        if approved["state"] != "approved" or approved["event_id"] != review.previous_event:
            raise Conflict("previous approval was changed or revoked")
        return previous, approved

    def status(self, local: str | None = None) -> dict:
        candidate = self.github.candidate(self.project)
        try:
            page = self.page()
        except NotPublished:
            return {"state": "awaiting_publication", "gate": False}
        journal, _ = self.journal(page)
        if not journal.reviews or journal.reviews[-1].source.token != candidate.token:
            return {"state": "awaiting_publication", "gate": False, "page": page}
        current = journal.reviews[-1]
        review_decision = self.decision(current, page, journal)
        active, decision = self.effective(current, page, journal)
        if self.github.candidate(self.project).token != candidate.token:
            raise Conflict("constitution changed during gate verification")
        matches = active is not None and (
            local is None or fingerprint(local) == active.source.content_digest
        )
        return {
            "state": decision["state"] if matches or active is None else "local_mismatch",
            "gate": matches,
            "page": page,
            "review_issue": current.issue_key,
            "active_review": active.id if active else None,
            "decision": decision,
            "review_decision": review_decision,
            "active_source": active.source.commit if active else None,
            "page_url": f"{self.cfg.site}/wiki/spaces/{self.cfg.confluence_space_id}/pages/{page}",
        }

    def preview(self) -> dict:
        candidate = self.github.candidate(self.project)
        scan(candidate.content)
        return {
            **self.status(),
            "source_revision": candidate.commit,
            "content_digest": candidate.content_digest,
            "sections": [
                "Project governance",
                "Active approved constitution",
                "Proposed constitution",
                "Changes from the approved version",
                "Review and approval history",
            ],
            "writes": False,
        }

    def label_reviews(self, journal: Journal):
        current = journal.reviews[-1].id
        for review in journal.reviews:
            issue = self.jira.get(review.issue_key)
            before = set(issue["fields"].get("labels", []))
            after = before - {"constitution-current", "constitution-superseded"}
            after |= {
                "constitution-review",
                "constitution-current" if review.id == current else "constitution-superseded",
            }
            if before != after:
                self.confluence.cloud.request(
                    "jira",
                    "PUT",
                    f"/rest/api/3/issue/{review.issue_key}",
                    json={"fields": {"labels": sorted(after)}},
                )

    def gate(self, root: Path) -> dict:
        result = self.status(inside(root, CONSTITUTION).read_text())
        if not result["gate"]:
            raise Conflict("constitution gate blocked: " + result["state"])
        return result

    def publish(self) -> dict:
        candidate = self.github.candidate(self.project)
        scan(candidate.content)
        page = self.page(create=True)
        journal, prop = self.journal(page)
        current = journal.reviews[-1] if journal.reviews else None
        previous = None
        previous_event = None
        if current and candidate.token != current.source.token:
            # Capture the last active approval BEFORE superseding the current review.
            previous, decision = self.effective(current, page, journal)
            if previous is None and current.previous_id:
                previous, decision = self.previous_approval(current, page, journal)
            previous_event = decision.get("event_id") if previous else None
        if not current or current.source.token != candidate.token:
            review_id = digest(
                [self.project.identity, candidate.token, current.id if current else None]
            )
            intent_key = "atlassian.constitution.creation.v1"
            intent = self.confluence.properties(page).get(intent_key)
            known_key = None
            if intent and intent["value"].get("review_id") == review_id:
                known_key = intent["value"].get("issue_key")
                if not known_key:
                    recovered = self.jira.find(self.project.identity + ":constitution:" + review_id)
                    if not recovered:
                        raise Conflict(
                            "constitution issue create is unresolved; inspect Jira before retry"
                        )
                    known_key = recovered["key"]
            else:
                if intent and not intent["value"].get("issue_key"):
                    raise Conflict(
                        "a previous constitution issue create is unresolved; inspect Jira"
                    )
                self.confluence.set_property(page, intent_key, {"review_id": review_id}, intent)
                intent = self.confluence.properties(page).get(intent_key)
            summary, description = self.fields(candidate, page, review_id, previous)
            key, _ = self.jira.upsert(
                self.project.identity + ":constitution:" + review_id,
                OWNER,
                "decision",
                summary,
                description,
                self.project.identity,
                metadata={"constitution_digest": candidate.content_digest, "review_id": review_id},
                adopt_key=known_key,
            )
            self.confluence.set_property(
                page, intent_key, {"review_id": review_id, "issue_key": key}, intent
            )
            current = Review(
                id=review_id,
                source=candidate,
                issue_key=key,
                previous_id=previous.id if previous else None,
                previous_event=previous_event,
            )
            # The published review section is immutable. Retrying cannot erase human edits.
            self.confluence.update(page, self.project.identity, OWNER, [self.section(current)])
            # An unjournaled review must enter the review state after publication.
            # This also invalidates an approval made during an interrupted create.
            self.jira.transition(key, self.project.awaiting_status)
            journal.reviews.append(current)
            if len(journal.reviews) > 100:
                raise Conflict(
                    "constitution history budget exceeded; explicitly archive before retry"
                )
            # Store references, never constitution bodies, in a Confluence property.
            stored = journal.model_dump(mode="json")
            for item in stored["reviews"]:
                item["source"]["content"] = ""
            if len(str(stored).encode()) > 30000:
                raise Conflict("constitution history property budget exceeded")
            self.confluence.set_property(page, LEDGER, stored, prop)
        result = self.status()
        if "active_review" not in result:
            raise Conflict(
                "constitution source moved during publication; retry the latest revision"
            )
        self.label_reviews(journal)
        self.render(page, journal, result)
        self.jira.remote_link(
            current.issue_key, result["page_url"], "Project constitution and approval"
        )
        return result

    def render(self, page: str, journal: Journal, result: dict):
        current = journal.reviews[-1]
        active = next((r for r in journal.reviews if r.id == result["active_review"]), None)
        previous = next((r for r in journal.reviews if r.id == current.previous_id), None)
        before = self.source(previous).content if previous else ""
        proposed = self.source(current).content
        delta = "".join(
            difflib.unified_diff(
                before.splitlines(True),
                proposed.splitlines(True),
                fromfile="approved",
                tofile="proposed",
            )
        )
        summary = f"<p>State: <strong>{escape(result['state'])}</strong></p>"
        summary += (
            "<p>Current review decision: " + escape(result["review_decision"]["state"]) + "</p>"
        )
        summary += (
            f'<p><a href="{self.cfg.site}/browse/{current.issue_key}">'
            "Review and decide in Jira</a></p>"
        )
        summary += (
            f"<p>Current review: {current.id[:8]} · "
            f"Source: <code>{current.source.commit}</code></p>"
        )
        summary += (
            "<p>Feature workflows are paused.</p>"
            if not result["gate"]
            else "<p>Approved constitution is active. Feature branches must match it.</p>"
        )
        if active:
            summary += (
                f"<p>Active version: <code>{active.source.content_digest[:12]}</code> · "
                f"Source: <code>{active.source.commit}</code></p>"
            )
        history = (
            "<table><tbody><tr><th>Review</th><th>Source</th><th>Jira</th>"
            "<th>Decision</th><th>Who / when</th></tr>"
        )
        for r in journal.reviews:
            try:
                event = self.decision(r, page, journal)
            except Conflict:
                event = {"state": "unverified"}
            state = event["state"]
            if r.id != current.id:
                state += " (historical)"
            actor = event.get("account_id", "") + " " + event.get("decided_at", "")
            history += (
                f"<tr><td>{r.id[:8]}</td><td>{r.source.commit[:12]}</td>"
                f'<td><a href="{self.cfg.site}/browse/{r.issue_key}">{r.issue_key}</a></td>'
                f"<td>{escape(state)}</td><td>{escape(actor)}</td></tr>"
            )
        history += "</tbody></table>"
        self.confluence.update(
            page,
            self.project.identity,
            OWNER,
            [
                Section("constitution-summary", "Project governance", summary, True),
                Section(
                    "constitution",
                    "Active approved constitution",
                    self.rendered(self.source(active))
                    if active
                    else "<p>No active constitution while review is pending.</p>",
                ),
                Section(
                    "constitution-proposal",
                    "Proposed constitution",
                    self.rendered(self.source(current)),
                ),
                Section(
                    "constitution-diff",
                    "Changes from the approved version",
                    "<pre>" + escape(delta) + "</pre>",
                ),
                Section("constitution-history", "Review and approval history", history),
            ],
        )
