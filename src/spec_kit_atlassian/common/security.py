from __future__ import annotations

import re

from .models import Conflict

PATTERNS = [
    r"-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----",
    r"\b(?:gh[pousr]_[A-Za-z0-9]{20,}|github_pat_[A-Za-z0-9_]{30,})\b",
    r"\bAKIA[A-Z0-9]{16}\b",
    r"\bsk-(?:proj-|ant-)?[A-Za-z0-9_-]{24,}\b",
    r"(?i)(?:api[_-]?token|api[_-]?key|password|secret)\s*[:=]\s*[\"']?[^\s\"']{16,}",
]


def scan(text: str) -> None:
    if any(re.search(pattern, text) for pattern in PATTERNS):
        raise Conflict("possible credential detected in publishable content; publication stopped")
