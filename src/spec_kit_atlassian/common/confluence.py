from __future__ import annotations

import re
from dataclasses import dataclass
from html import escape
from typing import Any
from uuid import NAMESPACE_URL, uuid5

from lxml import etree

from .http import AmbiguousWrite, APIError, Cloud
from .models import Conflict, Settings, digest

AC = "http://atlassian.com/content"
RI = "http://atlassian.com/resource/identifier"
NS = {"ac": AC, "ri": RI}


@dataclass(frozen=True)
class Section:
    key: str
    title: str
    body: str
    expanded: bool = False


def document(body: str) -> Any:
    if re.search(r"<!DOCTYPE|<!ENTITY", body, re.I):
        raise Conflict("unsafe XML declaration in Confluence body")
    parser = etree.XMLParser(resolve_entities=False, no_network=True, remove_blank_text=False)
    try:
        return etree.fromstring(
            (f'<root xmlns:ac="{AC}" xmlns:ri="{RI}">' + body + "</root>").encode(), parser
        )
    except etree.XMLSyntaxError:
        raise Conflict(
            "unsupported Confluence storage markup; no content was overwritten"
        ) from None


def serialized(node: Any) -> str:
    return etree.tostring(node, encoding="unicode", with_tail=False)


def node_hash(node: Any) -> str:
    return digest(etree.tostring(node, method="c14n").decode())


def section_id(identity: str, owner: str, key: str) -> str:
    return str(uuid5(NAMESPACE_URL, f"atlassian:v1:{identity}:{owner}:{key}"))


def merge_sections(
    body: str, identity: str, owner: str, sections: list[Section], previous: dict[str, str]
) -> tuple[str, dict[str, str]]:
    root = document(body)
    hashes = {}
    for section in sections:
        sid = section_id(identity, owner, section.key)
        new = etree.Element(f"{{{AC}}}structured-macro", nsmap=NS)
        new.set(f"{{{AC}}}name", "panel" if section.expanded else "expand")
        new.set(f"{{{AC}}}schema-version", "1")
        new.set(f"{{{AC}}}macro-id", sid)
        title = etree.SubElement(new, f"{{{AC}}}parameter")
        title.set(f"{{{AC}}}name", "title")
        title.text = section.title
        rich = etree.SubElement(new, f"{{{AC}}}rich-text-body")
        fragment = document(section.body)
        rich.text = fragment.text
        for child in list(fragment):
            rich.append(child)
        desired_hash = node_hash(new)
        found = root.xpath(".//ac:structured-macro[@ac:macro-id=$id]", namespaces=NS, id=sid)
        if len(found) > 1:
            raise Conflict("duplicate managed Confluence section")
        if found:
            current_hash = node_hash(found[0])
            if current_hash != desired_hash and current_hash != previous.get(section.key):
                raise Conflict(f"human edits or missing ownership hash in section {section.key}")
            if current_hash != desired_hash:
                new.tail = found[0].tail
                found[0].getparent().replace(found[0], new)
        else:
            if section.key in previous:
                raise Conflict(f"managed section {section.key} was removed manually")
            root.append(new)
        hashes[section.key] = desired_hash
    # Keep retired generated sections rather than silently deleting historical content.
    hashes = {**previous, **hashes}
    merged = (root.text or "") + "".join(
        etree.tostring(node, encoding="unicode", with_tail=True) for node in root
    )
    return merged, hashes


class Confluence:
    def __init__(self, cloud: Cloud, settings: Settings):
        self.cloud, self.cfg = cloud, settings

    def get(self, page_id: str) -> dict:
        return self.cloud.request(
            "confluence", "GET", f"/wiki/api/v2/pages/{page_id}", params={"body-format": "storage"}
        )

    def properties(self, page_id: str) -> dict:
        data = self.cloud.request(
            "confluence", "GET", f"/wiki/api/v2/pages/{page_id}/properties", params={"limit": 250}
        )
        if data.get("_links", {}).get("next"):
            raise Conflict("too many page properties to establish ownership safely")
        return {item["key"]: item for item in data["results"]}

    def set_property(self, page_id: str, key: str, value: Any, current: dict | None) -> None:
        if current and current["value"] == value:
            return
        path = f"/wiki/api/v2/pages/{page_id}/properties"
        if current:
            self.cloud.request(
                "confluence",
                "PUT",
                path + "/" + str(current["id"]),
                json={
                    "key": key,
                    "value": value,
                    "version": {"number": current["version"]["number"] + 1},
                },
            )
        else:
            self.cloud.request("confluence", "POST", path, json={"key": key, "value": value})

    def ensure_page(self, identity: str, title: str, page_id: str | None) -> str:
        identity_key = "atlassian.integration.identity.v1"
        marker = section_id(identity, "shared", "identity")
        if page_id:
            page = self.get(page_id)
            props = self.properties(page_id)
            if str(page["spaceId"]) != self.cfg.confluence_space_id:
                raise Conflict("bound Confluence page belongs to another space")
            if props.get(identity_key, {}).get("value") != identity:
                raise Conflict("bound page lacks identity metadata; explicitly adopt it first")
            return page_id
        stable_title = f"{title} [{digest(identity)[:10]}]"
        matches = self.cloud.request(
            "confluence",
            "GET",
            "/wiki/api/v2/pages",
            params={
                "space-id": self.cfg.confluence_space_id,
                "title": stable_title,
                "status": "current",
                "limit": 2,
                "body-format": "storage",
            },
        )
        if len(matches["results"]) > 1 or matches.get("_links", {}).get("next"):
            raise Conflict("multiple Confluence pages match the feature")
        if matches["results"]:
            found = matches["results"][0]
            page_id = str(found["id"])
            props = self.properties(page_id)
            prop = props.get(identity_key)
            if prop and prop["value"] != identity:
                raise Conflict("Confluence title collision")
            if not prop and marker not in self.get(page_id)["body"]["storage"]["value"]:
                raise Conflict("page exists without a matching identity marker")
        else:
            seed = (
                f'<ac:structured-macro ac:name="anchor" ac:macro-id="{marker}">'
                f'<ac:parameter ac:name="">feature-{escape(identity)}</ac:parameter>'
                "</ac:structured-macro><h2>Human notes</h2><p></p>"
            )
            payload = {
                "spaceId": self.cfg.confluence_space_id,
                "status": "current",
                "title": stable_title,
                "body": {"representation": "storage", "value": seed},
            }
            if self.cfg.confluence_parent_id:
                payload["parentId"] = self.cfg.confluence_parent_id
            try:
                result = self.cloud.request(
                    "confluence", "POST", "/wiki/api/v2/pages", json=payload
                )
                page_id = str(result["id"])
                prop = None
            except AmbiguousWrite:
                raise Conflict(
                    "page create outcome unknown; rerun preview to recover by identity"
                ) from None
        self.set_property(page_id, identity_key, identity, prop)
        return page_id

    def update(self, page_id: str, identity: str, owner: str, sections: list[Section]) -> str:
        key = f"atlassian.integration.sections.v1.{owner}"
        for _ in range(3):
            page = self.get(page_id)
            props = self.properties(page_id)
            if props.get("atlassian.integration.identity.v1", {}).get("value") != identity:
                raise Conflict("page identity changed")
            prop = props.get(key)
            body = page["body"]["storage"]["value"]
            merged, hashes = merge_sections(
                body, identity, owner, sections, prop["value"] if prop else {}
            )
            # Compare canonical XML so equivalent serialization does not churn versions.
            changed = node_hash(document(merged)) != node_hash(document(body))
            if len(merged.encode()) > self.cfg.max_page_bytes:
                raise Conflict(
                    "page size budget exceeded; explicitly archive history before retrying"
                )
            try:
                if changed:
                    self.cloud.request(
                        "confluence",
                        "PUT",
                        f"/wiki/api/v2/pages/{page_id}",
                        json={
                            "id": page_id,
                            "status": "current",
                            "title": page["title"],
                            "body": {"representation": "storage", "value": merged},
                            "version": {
                                "number": page["version"]["number"] + 1,
                                "message": f"{owner}: update managed sections",
                            },
                        },
                    )
                self.set_property(page_id, key, hashes, prop)
                return "updated" if changed else "unchanged"
            except APIError as exc:
                if exc.status != 409:
                    raise
        raise Conflict("Confluence changed concurrently; retry after other updates finish")

    def attachment(self, page_id: str, filename: str, content: bytes, mime: str) -> str:
        import hashlib

        if not re.fullmatch(r"[A-Za-z0-9_.-]+", filename):
            raise Conflict("attachment filename must be a simple safe name")
        key = "atlassian.integration.attachment." + digest(filename)[:20]
        props = self.properties(page_id)
        fingerprint = hashlib.sha256(content).hexdigest()
        if props.get(key, {}).get("value", {}).get("sha256") == fingerprint:
            return "unchanged"
        path = f"/wiki/rest/api/content/{page_id}/child/attachment"
        data = self.cloud.request("confluence", "GET", path, params={"filename": filename})
        results = data["results"]
        if len(results) > 1:
            raise Conflict("ambiguous Confluence attachment")
        if results and key not in props:
            raise Conflict("attachment exists without integration ownership metadata")
        endpoint = path + "/" + results[0]["id"] + "/data" if results else path
        self.cloud.request(
            "confluence",
            "POST",
            endpoint,
            headers={"X-Atlassian-Token": "nocheck"},
            files={"file": (filename, content, mime)},
            data={"comment": "sha256:" + fingerprint, "minorEdit": "true"},
        )
        self.set_property(page_id, key, {"sha256": fingerprint}, props.get(key))
        return "uploaded"
