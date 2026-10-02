"""Stateful REST fake: exercises the actual HTTP adapters, never a live tenant."""

from __future__ import annotations

import copy
import json

import httpx


class Tenant:
    def __init__(self):
        self.issues, self.issue_props, self.pages, self.page_props = {}, {}, {}, {}
        self.attachments, self.links, self.histories = {}, {}, {}
        self.writes = []
        self.race = None

    def response(self, status=200, payload=None):
        return (
            httpx.Response(status, json=payload) if payload is not None else httpx.Response(status)
        )

    def __call__(self, request):
        path, method = request.url.path, request.method
        payload = (
            json.loads(request.content)
            if request.content and "application/json" in request.headers.get("content-type", "")
            else {}
        )
        parts = path.split("/")
        if method != "GET":
            self.writes.append((method, path, copy.deepcopy(payload)))
        if path == "/rest/api/3/search/jql":
            import re

            labels = re.findall(r'labels = "([^"]+)"', request.url.params["jql"])
            return self.response(
                payload={
                    "issues": [
                        copy.deepcopy(i)
                        for i in self.issues.values()
                        if all(label in i["fields"].get("labels", []) for label in labels)
                    ],
                    "isLast": True,
                }
            )
        if path == "/rest/api/3/issue" and method == "POST":
            key = f"TEST-{len(self.issues) + 1}"
            fields = {
                "status": {"name": "To Do"},
                "updated": "2026-09-26T10:00:00+00:00",
                "issuelinks": [],
                **payload["fields"],
            }
            self.issues[key] = {"key": key, "fields": fields}
            self.issue_props[key] = {p["key"]: p["value"] for p in payload.get("properties", [])}
            return self.response(201, {"key": key})
        if path == "/rest/api/3/issueLink":
            left, right = payload["inwardIssue"]["key"], payload["outwardIssue"]["key"]
            self.issues[left]["fields"]["issuelinks"].append(
                {"type": payload["type"], "outwardIssue": {"key": right}}
            )
            return self.response(201)
        if path.startswith("/rest/api/3/issue/"):
            key = parts[5]
            if key not in self.issues:
                return self.response(404)
            if len(parts) == 6:
                if method == "GET":
                    return self.response(payload=copy.deepcopy(self.issues[key]))
                self.issues[key]["fields"].update(payload.get("fields", {}))
                for p in payload.get("properties", []):
                    self.issue_props[key][p["key"]] = p["value"]
                return self.response(204)
            if parts[6] == "properties":
                name = parts[7]
                if method == "GET":
                    if name not in self.issue_props[key]:
                        return self.response(404)
                    return self.response(
                        payload={"value": copy.deepcopy(self.issue_props[key][name])}
                    )
                self.issue_props[key][name] = payload
                return self.response(204)
            if parts[6] == "transitions":
                if method == "GET":
                    return self.response(
                        payload={
                            "transitions": [
                                {"id": name, "to": {"name": name}, "fields": {}}
                                for name in (
                                    "To Do",
                                    "In Progress",
                                    "In Review",
                                    "Blocked",
                                    "Done",
                                    "Waiting for input",
                                    "Awaiting approval",
                                    "Approved",
                                    "Changes requested",
                                    "Withdrawn",
                                )
                            ]
                        }
                    )
                self.issues[key]["fields"]["status"]["name"] = payload["transition"]["id"]
                return self.response(204)
            if parts[6] == "remotelink":
                if method == "GET":
                    return self.response(payload=copy.deepcopy(self.links.get(key, [])))
                self.links.setdefault(key, []).append(payload)
                return self.response(201, {})
            if parts[6] == "changelog":
                return self.response(
                    payload={"values": copy.deepcopy(self.histories.get(key, [])), "isLast": True}
                )
        if path == "/wiki/api/v2/pages":
            if method == "GET":
                return self.response(
                    payload={
                        "results": [
                            copy.deepcopy(p)
                            for p in self.pages.values()
                            if p["title"] == request.url.params.get("title")
                        ]
                    }
                )
            page_id = str(len(self.pages) + 1)
            self.pages[page_id] = {
                "id": page_id,
                "title": payload["title"],
                "spaceId": payload["spaceId"],
                "version": {"number": 1},
                "body": {"storage": {"value": payload["body"]["value"]}},
            }
            self.page_props[page_id] = {}
            return self.response(201, {"id": page_id})
        if path.startswith("/wiki/api/v2/pages/"):
            page_id = parts[5]
            if len(parts) == 6:
                if method == "GET":
                    return self.response(payload=copy.deepcopy(self.pages[page_id]))
                if self.race:
                    race, self.race = self.race, None
                    race(self.pages[page_id])
                    self.pages[page_id]["version"]["number"] += 1
                    return self.response(409)
                if payload["version"]["number"] != self.pages[page_id]["version"]["number"] + 1:
                    return self.response(409)
                self.pages[page_id].update(
                    version=payload["version"],
                    body={"storage": {"value": payload["body"]["value"]}},
                )
                return self.response(payload={"id": page_id})
            if parts[6] == "properties":
                if method == "GET":
                    return self.response(
                        payload={"results": copy.deepcopy(list(self.page_props[page_id].values()))}
                    )
                prop = {
                    "id": parts[7] if len(parts) > 7 else str(len(self.page_props[page_id]) + 1),
                    "key": payload["key"],
                    "value": payload["value"],
                    "version": payload.get("version", {"number": 1}),
                }
                self.page_props[page_id][payload["key"]] = prop
                return self.response(payload=prop)
        if "/child/attachment" in path:
            page_id = parts[5]
            if method == "GET":
                return self.response(
                    payload={
                        "results": self.attachments.get(
                            (page_id, request.url.params["filename"]), []
                        )
                    }
                )
            return self.response(payload={"results": [{"id": "att1"}]})
        raise AssertionError(f"unhandled request: {method} {path}")
