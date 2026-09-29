from __future__ import annotations

from typing import Any

from .http import AmbiguousWrite, APIError, Cloud
from .models import Conflict, Settings, digest, label
from .render import adf

PROPERTY = "atlassian.integration.v1"


class Jira:
    def __init__(self, cloud: Cloud, settings: Settings):
        self.cloud, self.cfg = cloud, settings

    def get(self, key: str) -> dict:
        return self.cloud.request("jira", "GET", f"/rest/api/3/issue/{key}")

    def metadata(self, key: str) -> dict:
        try:
            return self.cloud.request(
                "jira", "GET", f"/rest/api/3/issue/{key}/properties/{PROPERTY}"
            )["value"]
        except APIError as exc:
            if exc.status == 404:
                return {}
            raise

    def find(self, identity: str) -> dict | None:
        data = self.cloud.request(
            "jira",
            "GET",
            "/rest/api/3/search/jql",
            params={
                "jql": f'project = "{self.cfg.jira_project}" AND labels = "{label(identity)}"',
                "maxResults": 2,
                "fields": "summary,status",
            },
        )
        results = data["issues"]
        if len(results) > 1 or data.get("nextPageToken"):
            raise Conflict("multiple Jira records claim the same identity")
        if not results:
            return None
        issue = self.get(results[0]["key"])
        if self.metadata(issue["key"]).get("identity") != identity:
            raise Conflict("Jira identity label exists without matching ownership metadata")
        return issue

    def owned(self, feature_identity: str, owner: str) -> list[dict]:
        issues, token = [], None
        while True:
            params: dict[str, Any] = {
                "jql": (
                    f'project = "{self.cfg.jira_project}" AND labels = '
                    f'"{label(feature_identity)}" AND labels = "{owner}"'
                ),
                "maxResults": 100,
                "fields": "summary,status",
            }
            if token:
                params["nextPageToken"] = token
            data = self.cloud.request("jira", "GET", "/rest/api/3/search/jql", params=params)
            issues.extend(data["issues"])
            token = data.get("nextPageToken")
            if not token:
                return issues

    def upsert(
        self,
        identity: str,
        owner: str,
        kind: str,
        summary: str,
        description: str,
        feature_identity: str,
        parent: str | None = None,
        extra: dict | None = None,
        metadata: dict | None = None,
        adopt_key: str | None = None,
    ) -> tuple[str, str]:
        fields: dict[str, Any] = {"summary": summary[:255], "description": adf(description)}
        fields.update(extra or {})
        existing = self.find(identity)
        if adopt_key and (not existing or existing["key"] != adopt_key):
            # Adoption must be deliberate; never overwrite an unowned existing Epic.
            candidate = self.get(adopt_key)
            if candidate["fields"]["project"]["key"] != self.cfg.jira_project:
                raise Conflict("bound Jira issue belongs to another project")
            candidate_meta = self.metadata(adopt_key)
            if candidate_meta.get("identity") != identity:
                raise Conflict("bound issue lacks matching identity; use explicit binding adoption")
            existing = candidate
        meta = {
            "schema_version": "1.0",
            "identity": identity,
            "owner": owner,
            "kind": kind,
            "feature_identity": feature_identity,
            "generated_fields": fields,
            **(metadata or {}),
        }
        if existing:
            key = existing["key"]
            old = self.metadata(key)
            if old.get("owner") != owner:
                if kind == "feature" and old.get("identity") == identity:
                    return key, "shared"
                raise Conflict("refusing to overwrite another integration's Jira record")
            for name, before in old.get("generated_fields", {}).items():
                if existing["fields"].get(name) != before:
                    raise Conflict(f"human edit to managed Jira field {name} on {key}")
            if old == meta:
                return key, "unchanged"
            patch = {
                name: value
                for name, value in fields.items()
                if existing["fields"].get(name) != value
            }
            self.cloud.request(
                "jira",
                "PUT",
                f"/rest/api/3/issue/{key}",
                json={
                    "fields": patch,
                    "properties": [{"key": PROPERTY, "value": meta}],
                },
            )
            return key, "updated"
        create_fields = {
            **fields,
            "project": {"key": self.cfg.jira_project},
            "issuetype": {"name": self.cfg.issue_types[kind]},
            "labels": sorted({owner, label(identity), label(feature_identity)}),
        }
        if parent:
            create_fields["parent"] = {"key": parent}
        payload = {"fields": create_fields, "properties": [{"key": PROPERTY, "value": meta}]}
        try:
            created = self.cloud.request("jira", "POST", "/rest/api/3/issue", json=payload)
            return created["key"], "created"
        except AmbiguousWrite:
            recovered = self.find(identity)
            if recovered:
                return recovered["key"], "recovered"
            raise Conflict(
                "Jira create outcome unknown; inspect identity before retrying"
            ) from None

    def transition(self, key: str, status: str) -> str:
        issue = self.get(key)
        if issue["fields"]["status"]["name"] == status:
            return "unchanged"
        data = self.cloud.request(
            "jira",
            "GET",
            f"/rest/api/3/issue/{key}/transitions",
            params={"expand": "transitions.fields"},
        )
        matches = [t for t in data["transitions"] if t["to"]["name"] == status]
        if len(matches) != 1 or any(
            v.get("required") and not v.get("hasDefaultValue")
            for v in matches[0].get("fields", {}).values()
        ):
            raise Conflict(f"no unambiguous supported transition to {status} for {key}")
        self.cloud.request(
            "jira",
            "POST",
            f"/rest/api/3/issue/{key}/transitions",
            json={"transition": {"id": matches[0]["id"]}},
        )
        return "transitioned"

    def remote_link(self, key: str, url: str, title: str) -> None:
        global_id = "atlassian-integration:" + digest(url)
        links = self.cloud.request("jira", "GET", f"/rest/api/3/issue/{key}/remotelink")
        if any(
            link.get("globalId") == global_id
            and link["object"].get("url") == url
            and link["object"].get("title") == title
            for link in links
        ):
            return
        self.cloud.request(
            "jira",
            "POST",
            f"/rest/api/3/issue/{key}/remotelink",
            json={
                "globalId": global_id,
                "object": {"url": url, "title": title},
            },
        )

    def link(self, left: str, right: str, kind: str = "Relates") -> None:
        issue = self.get(left)
        if any(
            link["type"]["name"] == kind and (link.get("outwardIssue", {}).get("key") == right)
            for link in issue["fields"].get("issuelinks", [])
        ):
            return
        self.cloud.request(
            "jira",
            "POST",
            "/rest/api/3/issueLink",
            json={
                "type": {"name": kind},
                "inwardIssue": {"key": left},
                "outwardIssue": {"key": right},
            },
        )

    def changelog(self, key: str) -> list[dict]:
        result, start = [], 0
        while True:
            page = self.cloud.request(
                "jira",
                "GET",
                f"/rest/api/3/issue/{key}/changelog",
                params={"startAt": start, "maxResults": 100},
            )
            result.extend(page["values"])
            start += len(page["values"])
            if page.get("isLast", start >= page.get("total", start)):
                return result
            if not page["values"]:
                raise Conflict("incomplete Jira changelog")

    def shared_page(self, epic: str, identity: str, page_id: str | None = None) -> str | None:
        key = "atlassian.integration.feature-binding.v1"
        try:
            current = self.cloud.request(
                "jira", "GET", f"/rest/api/3/issue/{epic}/properties/{key}"
            )["value"]
        except APIError as exc:
            if exc.status != 404:
                raise
            current = None
        if current:
            if current["identity"] != identity:
                raise Conflict("Epic page binding belongs to another feature")
            if page_id and current["page_id"] != page_id:
                raise Conflict("two pages claim the same feature binding")
            return current["page_id"]
        if page_id:
            self.cloud.request(
                "jira",
                "PUT",
                f"/rest/api/3/issue/{epic}/properties/{key}",
                json={"identity": identity, "page_id": page_id},
            )
        return page_id
