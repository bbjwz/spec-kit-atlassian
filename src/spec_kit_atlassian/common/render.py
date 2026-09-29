from __future__ import annotations

from html import escape
from urllib.parse import urljoin

from markdown_it import MarkdownIt


def markdown(text: str, base_url: str | None = None) -> str:
    renderer = MarkdownIt("commonmark", {"html": False}).enable("table")

    def link_open(self, tokens, idx, options, env):
        token = tokens[idx]
        href = token.attrGet("href") or ""
        if base_url and not href.startswith(("https://", "http://", "mailto:")):
            token.attrSet("href", urljoin(base_url, href))
        return self.renderToken(tokens, idx, options, env)

    def image(self, tokens, idx, options, env):
        token = tokens[idx]
        src = token.attrGet("src") or ""
        if base_url:
            src = urljoin(base_url, src)
        if not src.startswith(("https://", "http://")):
            return escape(f"[Image: {token.content}] ({src})")
        return f'<a href="{escape(src, quote=True)}">{escape(token.content or "Image")}</a>'

    renderer.add_render_rule("link_open", link_open)
    renderer.add_render_rule("image", image)
    return renderer.render(text)


def adf(text: str) -> dict:
    return {
        "version": 1,
        "type": "doc",
        "content": [
            {"type": "paragraph", "content": [{"type": "text", "text": line}]}
            for line in text.splitlines()
            if line.strip()
        ]
        or [{"type": "paragraph", "content": []}],
    }
